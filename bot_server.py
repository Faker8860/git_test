"""
TradingView → 币安 量化桥接器
=============================
接收 TradingView 告警 Webhook，自动在币安现货下单。
支持：最大下单金额限制 + 自动止损

部署：uvicorn bot_server:app --host 0.0.0.0 --port 8000
"""

import os
import sys
import json
import hmac
import hashlib
import logging
from datetime import datetime, timezone

from pathlib import Path

import ccxt
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse

BASE_DIR = Path(__file__).parent

# ── 多用户支持 ──
_user = None
for i, arg in enumerate(sys.argv):
    if arg == "--user" and i + 1 < len(sys.argv):
        _user = sys.argv[i + 1]
        break
if _user:
    user_env = BASE_DIR / "user_data" / _user / ".env"
    if user_env.exists():
        load_dotenv(user_env, override=True)
load_dotenv(BASE_DIR / ".env")  # 根配置作为回退

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("bot.log", encoding="utf-8")],
)
log = logging.getLogger("trading-bot")

# ── 代理配置 ──
PROXY_URL = os.getenv("PROXY_URL", "")
_proxies = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None

# ── 币安配置（U本位合约, ccxt timeout 单位毫秒）──
binance = ccxt.binance({
    "apiKey": os.getenv("BINANCE_API_KEY"),
    "secret": os.getenv("BINANCE_SECRET_KEY"),
    "enableRateLimit": True,
    "timeout": 30000,
    "options": {"defaultType": "swap"},  # U本位永续合约
})
if _proxies:
    binance.session.proxies.update(_proxies)
if os.getenv("BINANCE_API_URL"):
    binance.urls["api"] = os.getenv("BINANCE_API_URL")

markets_loaded = False
hedge_mode = None  # True=双向持仓模式(Hedge), False=单向模式(One-way), None=未检测


def ensure_markets():
    """确保市场数据已加载。失败不崩溃，等下次重试."""
    global markets_loaded
    if not markets_loaded:
        try:
            binance.load_markets()
            markets_loaded = True
            log.info("市场数据加载完成")
        except Exception as e:
            log.warning(f"加载市场数据失败（VPN 开了吗？）: {e}")


def detect_hedge_mode():
    """检测账户是否为双向持仓模式（Hedge Mode）."""
    global hedge_mode
    if hedge_mode is not None:
        return hedge_mode
    try:
        resp = binance.fapiPrivateGetPositionSideDual()
        hedge_mode = resp.get("dualSidePosition", False)
    except Exception:
        try:
            resp = binance.fapiPrivate_get_positionSide_dual()
            hedge_mode = resp.get("dualSidePosition", False)
        except Exception:
            log.warning("检测持仓模式失败，默认使用双向持仓(Hedge)模式")
            hedge_mode = True
            return hedge_mode
    mode_name = "双向持仓(Hedge)" if hedge_mode else "单向持仓(One-way)"
    log.info(f"账户持仓模式: {mode_name}")
    return hedge_mode


def order_params(pos_side: str, reduce_only: bool = False):
    """构建订单 extraParams，自动适配 Hedge/One-way 模式."""
    params = {}
    if detect_hedge_mode():
        params["positionSide"] = pos_side
    return params


# 合约配置
LEVERAGE = int(os.getenv("LEVERAGE", 3))  # 杠杆倍数，默认 3x

# ── 风控参数 ─────────────────────────────────────────
MIN_ORDER_USDT = float(os.getenv("MIN_ORDER_USDT", 11))       # 最小下单金额
MAX_ORDER_USDT = float(os.getenv("MAX_ORDER_USDT", 0))        # 最大下单金额，0=不限制
STOP_LOSS_PCT  = float(os.getenv("STOP_LOSS_PCT", 0))         # 止损比例，0=不止损，如 5 表示 5%
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

# ── 记录每笔入场价，用于止损和取消止损 ──────────────
# {"ETHUSDT": {"side": "LONG", "entry_price": 2000.0, "stop_order_id": 12345}}
positions = {}

TRADE_FILE = BASE_DIR / "trades.json"


def save_trade(data: dict):
    """保存交易记录到 JSON 文件."""
    trades = []
    try:
        with open(TRADE_FILE, "r") as f:
            trades = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    trades.append(data)
    with open(TRADE_FILE, "w") as f:
        json.dump(trades, f, ensure_ascii=False, indent=2)

app = FastAPI(title="TV-Binance Bot", docs_url=None, redoc_url=None)


def verify_signature(payload: bytes, signature: str) -> bool:
    if not WEBHOOK_SECRET:
        return True
    expected = hmac.new(WEBHOOK_SECRET.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


def get_swap_symbol(raw: str) -> str:
    """把 TV 格式 'ETHUSDT' 转成 CCXT 合约格式 'ETH/USDT:USDT'."""
    # 先直接找 Binance id 匹配的 swap 市场
    for m_id, m in binance.markets.items():
        if m.get("id") == raw and m.get("swap"):
            return m_id
    # 回退：直接用 CCXT 命名规则
    return raw  # CCXT 对币安合约其实能直接用 raw


def usdt_to_contracts(symbol: str, usdt_amount: float, price: float) -> float:
    """把 USDT 金额转成合约张数."""
    market = binance.market(symbol)
    contract_size = market.get("contractSize", 1.0)
    if contract_size <= 0:
        contract_size = 1.0
    contracts = usdt_amount / (price * contract_size)
    return float(binance.amount_to_precision(symbol, contracts))


def fix_qty(symbol: str, quantity: float) -> float:
    """用 CCXT 内置方法修整数量到交易所精度."""
    return float(binance.amount_to_precision(symbol, quantity))


def fix_price(symbol: str, price: float) -> float:
    """用 CCXT 内置方法修整价格到交易所精度."""
    return float(binance.price_to_precision(symbol, price))


def set_leverage(symbol: str):
    """设置杠杆倍数."""
    try:
        binance.set_leverage(LEVERAGE, symbol)
        log.info(f"[杠杆] {symbol} 设置为 {LEVERAGE}x")
    except Exception as e:
        log.warning(f"[杠杆] 设置失败（可能已设置）: {e}")


def place_stop_loss(symbol: str, entry_price: float, quantity: float, pos_side: str):
    """入场后挂止损单（合约版），支持多空双方."""
    if STOP_LOSS_PCT <= 0:
        return

    sym = get_swap_symbol(symbol)
    contracts_qty = fix_qty(sym, quantity)
    if contracts_qty <= 0:
        return

    if pos_side == "LONG":
        stop_price = fix_price(sym, entry_price * (1 - STOP_LOSS_PCT / 100))
        sp = {"stopPrice": stop_price}
        sp.update(order_params("LONG", reduce_only=True))
        order = binance.create_order(
            symbol=sym, type="stop_market",
            side="sell", amount=contracts_qty,
            params=sp,
        )
    else:
        stop_price = fix_price(sym, entry_price * (1 + STOP_LOSS_PCT / 100))
        sp = {"stopPrice": stop_price}
        sp.update(order_params("SHORT", reduce_only=True))
        order = binance.create_order(
            symbol=sym, type="stop_market",
            side="buy", amount=contracts_qty,
            params=sp,
        )

    if symbol in positions:
        positions[symbol]["entry_price"] = entry_price
        positions[symbol]["stop_order_id"] = order["id"]
        positions[symbol]["side"] = pos_side
    else:
        positions[symbol] = {"entry_price": entry_price, "stop_order_id": order["id"], "side": pos_side}
    log.info(f"[止损] {sym} {pos_side} 入场 {entry_price}，止损 @ {stop_price} (ID: {order['id']})")


def cancel_stop_loss(symbol: str):
    """出场时取消止损单."""
    pos = positions.pop(symbol, None)
    if pos and pos.get("stop_order_id"):
        try:
            binance.cancel_order(pos["stop_order_id"], symbol)
            log.info(f"[止损] 已取消 {symbol} 止损单 {pos['stop_order_id']}")
        except Exception as e:
            log.warning(f"[止损] 取消止损单失败: {e}")


@app.get("/")
def root():
    return JSONResponse({
        "status": "running",
        "max_order_usdt": MAX_ORDER_USDT if MAX_ORDER_USDT > 0 else "无限制",
        "stop_loss_pct": f"{STOP_LOSS_PCT}%" if STOP_LOSS_PCT > 0 else "已关闭",
    })


@app.get("/health")
def health():
    return JSONResponse({"ok": True, "time": datetime.now(timezone.utc).isoformat()})


@app.post("/webhook")
async def webhook(request: Request):
    """接收 TradingView 告警，下单."""
    body = await request.body()

    sig = request.headers.get("x-tv-signature", "")
    if not verify_signature(body, sig):
        raise HTTPException(status_code=403, detail="签名验证失败")

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        log.warning(f"收到非 JSON 告警: {body[:200]}")
        raise HTTPException(status_code=400, detail="告警必须为 JSON 格式")

    log.info(f"收到告警: {json.dumps(data, ensure_ascii=False)}")

    action  = data.get("action", "")
    symbol  = data.get("symbol", "ETHUSDT")
    quantity = float(data.get("quantity", 0))
    price   = float(data.get("price", 0))

    # ── 风控①：最小金额 ──
    order_value = quantity * (price or 0)
    if 0 < order_value < MIN_ORDER_USDT:
        log.warning(f"金额 {order_value:.2f}U 低于最小 {MIN_ORDER_USDT}U，跳过")
        return JSONResponse({"status": "rejected", "reason": "order too small"})

    # ── 风控②：最大金额 ──
    if MAX_ORDER_USDT > 0 and order_value > MAX_ORDER_USDT:
        log.warning(f"金额 {order_value:.2f}U 超过最大 {MAX_ORDER_USDT}U，跳过")
        return JSONResponse({"status": "rejected", "reason": "order too large"})

    # ── 方向 & 翻转逻辑 ──
    if action not in ("long_entry", "short_entry", "exit_long", "exit_short"):
        raise HTTPException(status_code=400, detail=f"未知 action: {action}")

    # ── 下单 ──
    try:
        ensure_markets()
        detect_hedge_mode()
        if not markets_loaded:
            raise HTTPException(status_code=503, detail="服务器初始化中，请确保 VPN 已开启")

        # 如果 TV 没传价格，从交易所查
        if price <= 0:
            ticker = binance.fetch_ticker(symbol)
            price = ticker.get("last", 0) or ticker.get("close", 0)

        sym = get_swap_symbol(symbol)
        usdt_val = quantity * price
        contracts = usdt_to_contracts(sym, usdt_val, price)
        set_leverage(sym)

        orders_placed = []
        current_side = positions.get(symbol, {}).get("side")  # "LONG" / "SHORT" / None

        # ── 翻转：long_entry → 先平空单 ──
        if action == "long_entry" and current_side == "SHORT":
            cancel_stop_loss(symbol)
            o = binance.create_order(
                symbol=sym, type="market", side="buy", amount=contracts,
                params=order_params("SHORT", reduce_only=True),
            )
            log.info(f"[翻转] 平空单 → buy {contracts}张 {sym} 订单ID: {o['id']}")
            orders_placed.append(o)
            save_trade({
                "time": datetime.now(timezone.utc).isoformat(),
                "action": "flip_close_short", "symbol": symbol,
                "side": "buy", "contracts": contracts,
                "usdt_value": round(usdt_val, 2), "price": round(price, 2),
                "order_id": o.get("id", ""),
            })

        # ── 翻转：short_entry → 先平多单 ──
        if action == "short_entry" and current_side == "LONG":
            cancel_stop_loss(symbol)
            o = binance.create_order(
                symbol=sym, type="market", side="sell", amount=contracts,
                params=order_params("LONG", reduce_only=True),
            )
            log.info(f"[翻转] 平多单 → sell {contracts}张 {sym} 订单ID: {o['id']}")
            orders_placed.append(o)
            save_trade({
                "time": datetime.now(timezone.utc).isoformat(),
                "action": "flip_close_long", "symbol": symbol,
                "side": "sell", "contracts": contracts,
                "usdt_value": round(usdt_val, 2), "price": round(price, 2),
                "order_id": o.get("id", ""),
            })

        # ── 主订单 ──
        if action == "long_entry":
            side, pos_side, is_entry = "buy", "LONG", True
        elif action == "short_entry":
            side, pos_side, is_entry = "sell", "SHORT", True
        elif action == "exit_long":
            side, pos_side, is_entry = "sell", "LONG", False
            cancel_stop_loss(symbol)
        elif action == "exit_short":
            side, pos_side, is_entry = "buy", "SHORT", False
            cancel_stop_loss(symbol)

        order = binance.create_order(
            symbol=sym, type="market", side=side, amount=contracts,
            params=order_params(pos_side, reduce_only=not is_entry),
        )
        log.info(f"[币安合约] {side} {contracts}张 {sym} (≈{usdt_val:.0f}U) 订单ID: {order['id']}")
        orders_placed.append(order)

        # 更新持仓追踪
        if is_entry:
            positions[symbol] = {"side": pos_side}
        else:
            positions.pop(symbol, None)

        save_trade({
            "time": datetime.now(timezone.utc).isoformat(),
            "action": action, "symbol": symbol,
            "side": side, "contracts": contracts,
            "usdt_value": round(usdt_val, 2), "price": round(price, 2),
            "order_id": order.get("id", ""),
        })

        # ── 入场后挂止损 ──
        if is_entry and STOP_LOSS_PCT > 0:
            fill_price = price
            try:
                fills = order.get("fills", [])
                if fills:
                    total_cost = sum(float(f["price"]) * float(f["contracts"]) for f in fills)
                    total_cts  = sum(float(f["contracts"]) for f in fills)
                    if total_cts > 0:
                        fill_price = total_cost / total_cts
            except Exception:
                pass
            place_stop_loss(symbol, fill_price, contracts, pos_side)

        return JSONResponse({
            "status": "ok",
            "side": side,
            "quantity": contracts,
            "fill_price": round(fill_price if is_entry else price, 4),
            "order_id": order.get("id", ""),
            "flip": len(orders_placed) > 1,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        log.error(f"[币安] 下单失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    log.info(f"启动交易机器人 → 币安现货")
    log.info(f"最大下单: {'无限制' if MAX_ORDER_USDT <= 0 else f'{MAX_ORDER_USDT}U'}")
    log.info(f"止损比例: {'关闭' if STOP_LOSS_PCT <= 0 else f'{STOP_LOSS_PCT}%'}")
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
