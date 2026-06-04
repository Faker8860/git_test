"""
自主策略机器人 v2 — 多币种 + 百分比仓位 + 强平价
==================================================
多币种同时监控，各自独立下单。
保证金 = min(账户余额 × 百分比, MAX_ORDER_USDT)，仓位价值 = 保证金 × 杠杆。
"""

import os
import sys
import json
import logging
import time
import signal as os_signal
from datetime import datetime, timezone, timedelta
from pathlib import Path

import ccxt
import pandas as pd
import numpy as np
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

logging.Formatter.converter = lambda *args: datetime.now(timezone(timedelta(hours=8))).timetuple()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(BASE_DIR / "strategy.log", encoding="utf-8"),
        logging.FileHandler(BASE_DIR / "trade_history.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("strategy-bot")

# 交易专用日志：记录每笔开平仓
TRADE_LOG = BASE_DIR / "trade_history.log"
def log_trade(msg: str):
    """专门记录交易事件"""
    log.info(f"[交易] {msg}")

# ── 策略参数 ──────────────────────────────────────
STRATEGY_TYPE = os.getenv("STRATEGY_TYPE", "TEMA")  # TEMA 或 VOLTY
SYMBOLS_RAW = os.getenv("SYMBOLS", "ETHUSDT")
TIMEFRAME_MINUTES = int(os.getenv("TIMEFRAME_MINUTES", "1"))
MA_TYPE = os.getenv("MA_TYPE", "TEMA")
MA_LEN = int(os.getenv("MA_LEN", "8"))
CROSS_MULT = int(os.getenv("CROSS_MULT", "3"))
DELAY_MINUTES = int(os.getenv("DELAY_MINUTES", "5"))
DELAY_OFFSET = int(os.getenv("DELAY_OFFSET", "0"))
POSITION_PCT = float(os.getenv("POSITION_PCT", "20"))   # 账户余额百分比
LEVERAGE = int(os.getenv("LEVERAGE", "3"))
# Volty 策略参数
VOLTY_LENGTH = int(os.getenv("VOLTY_LENGTH", "5"))
VOLTY_ATR_MULT = float(os.getenv("VOLTY_ATR_MULT", "0.75"))
VOLTY_TREND_FILTER = os.getenv("VOLTY_TREND_FILTER", "true").lower() == "true"  # SMA50 trend filter
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "5"))
MIN_ORDER_USDT = float(os.getenv("MIN_ORDER_USDT", "11"))
MAX_ORDER_USDT = float(os.getenv("MAX_ORDER_USDT", "0"))
TRADE_TYPE = os.getenv("TRADE_TYPE", "BOTH")
ACTIVATION_DELAY_MINUTES = int(os.getenv("ACTIVATION_DELAY_MINUTES", "0"))
MARGIN_MODE = os.getenv("MARGIN_MODE", "isolated")  # isolated=逐仓, cross=全仓

# 解析多币种
SYMBOLS = [s.strip() for s in SYMBOLS_RAW.split(",") if s.strip()]

# ── 策略覆盖配置 ──────────────────────────────────
STRATEGIES_FILE = BASE_DIR / "strategies.json"
STRATEGY_OVERRIDES = {}  # { "ETHUSDT": {...}, ... }

def load_strategy_overrides():
    global STRATEGY_OVERRIDES, SYMBOLS
    try:
        with open(STRATEGIES_FILE, "r") as f:
            configs = json.load(f)
        STRATEGY_OVERRIDES = {}
        for cfg in configs:
            sym = cfg.get("symbol", "")
            if sym:
                STRATEGY_OVERRIDES[sym] = cfg
        # 自动将策略配置中的币种加入交易列表
        for sym in STRATEGY_OVERRIDES:
            if sym not in SYMBOLS:
                SYMBOLS.append(sym)
                log.info(f"从策略配置添加币种: {sym}")
        log.info(f"加载策略覆盖配置: {len(STRATEGY_OVERRIDES)} 个")
    except Exception:
        STRATEGY_OVERRIDES = {}

def get_symbol_param(symbol: str, key: str, default):
    """获取币种参数，优先使用策略配置覆盖值."""
    override = STRATEGY_OVERRIDES.get(symbol, {})
    # 策略配置字段名映射
    key_map = {
        "TIMEFRAME_MINUTES": "timeframe",
        "MA_TYPE": "maType",
        "MA_LEN": "maLen",
        "CROSS_MULT": "crossMult",
        "DELAY_MINUTES": "delayMin",
        "DELAY_OFFSET": "delayOffset",
        "POSITION_PCT": "positionPct",
        "LEVERAGE": "leverage",
        "STOP_LOSS_PCT": "stopLoss",
        "TRADE_TYPE": "tradeType",
        "MAX_ORDER_USDT": "maxOrder",
        "MIN_ORDER_USDT": "minOrder",
        "STRATEGY_TYPE": "strategyType",
        "VOLTY_LENGTH": "voltyLength",
        "VOLTY_ATR_MULT": "voltyAtrMult",
        "VOLTY_TREND_FILTER": "voltyTrendFilter",
        "MARGIN_MODE": "marginMode",
    }
    mapped = key_map.get(key, key)
    if mapped in override:
        val = override[mapped]
        # 类型转换
        if key in ("TIMEFRAME_MINUTES", "MA_LEN", "CROSS_MULT", "DELAY_MINUTES", "LEVERAGE", "VOLTY_LENGTH"):
            return int(val)
        if key in ("POSITION_PCT", "STOP_LOSS_PCT", "VOLTY_ATR_MULT", "MAX_ORDER_USDT", "MIN_ORDER_USDT"):
            return float(val)
        return val
    return default

# ── 币安连接 ──────────────────────────────────────
binance = ccxt.binance({
    "apiKey": os.getenv("BINANCE_API_KEY"),
    "secret": os.getenv("BINANCE_SECRET_KEY"),
    "enableRateLimit": True,
    "options": {"defaultType": "swap"},
})

markets_loaded = False
hedge_mode = None  # True=双向持仓模式, False=单向模式, None=未检测

STATE_FILE = BASE_DIR / "strategy_state.json"
TRADE_FILE = BASE_DIR / "trades.json"

# 持仓追踪：{ "ETHUSDT": {"side":"LONG","entry_price":2000,"stop_order_id":"xxx"}, ... }
positions = {}
# 每币种的 last_bar_time：{ "ETHUSDT": Timestamp(...), ... }
last_bar_times = {}
# 初始化就绪标记：重启后等新K线才开单
symbols_ready = set()

# Volty stop 订单追踪：{symbol: {"long_order_id": "xxx", "short_order_id": "yyy", "bar_time": ts, "long_level": float, "short_level": float}}
volty_stop_orders = {}

running = True


# ============================================================
#   均线
# ============================================================

def calc_ma(series: pd.Series, ma_type: str, length: int) -> pd.Series:
    if ma_type == "SMA":
        return series.rolling(window=length).mean()
    if ma_type == "EMA":
        return series.ewm(span=length, adjust=False).mean()
    if ma_type == "TEMA":
        e1 = series.ewm(span=length, adjust=False).mean()
        e2 = e1.ewm(span=length, adjust=False).mean()
        e3 = e2.ewm(span=length, adjust=False).mean()
        return 3.0 * e1 - 3.0 * e2 + e3
    if ma_type == "DEMA":
        e1 = series.ewm(span=length, adjust=False).mean()
        return 2.0 * e1 - e1.ewm(span=length, adjust=False).mean()
    if ma_type == "SMMA":
        alpha = 1.0 / length
        result = series.copy()
        result.iloc[:length] = series.iloc[:length].mean()
        for i in range(length, len(result)):
            result.iloc[i] = alpha * series.iloc[i] + (1 - alpha) * result.iloc[i - 1]
        return result
    if ma_type == "HullMA":
        half_len = max(1, length // 2)
        sqrt_len = max(1, int(round(np.sqrt(length))))
        wma_half = series.rolling(window=half_len).apply(
            lambda x: np.dot(x, np.arange(1, half_len + 1)) / np.sum(np.arange(1, half_len + 1)), raw=True
        )
        wma_full = series.rolling(window=length).apply(
            lambda x: np.dot(x, np.arange(1, length + 1)) / np.sum(np.arange(1, length + 1)), raw=True
        )
        raw_hull = 2.0 * wma_half - wma_full
        hull = raw_hull.rolling(window=sqrt_len).apply(
            lambda x: np.dot(x, np.arange(1, sqrt_len + 1)) / np.sum(np.arange(1, sqrt_len + 1)), raw=True
        )
        return hull
    if ma_type == "WMA":
        return series.rolling(window=length).apply(
            lambda x: np.dot(x, np.arange(1, length + 1)) / np.sum(np.arange(1, length + 1)), raw=True
        )
    if ma_type == "VWMA":
        # VWMA = 累积(close * volume) / 累积(volume)，需要 volume 数据
        # 简化：使用 SMA 作为近似（实际需要 volume series）
        return series.rolling(window=length).mean()
    if ma_type == "LSMA":
        # Least Squares Moving Average (线性回归)
        def lsma(arr):
            x = np.arange(len(arr))
            slope, intercept = np.polyfit(x, arr, 1)
            return intercept + slope * (len(arr) - 1)
        return series.rolling(window=length).apply(lsma, raw=True)
    if ma_type == "ALMA":
        # Arnaud Legoux Moving Average
        sigma = 6.0
        m_ = 0.85
        s = length / sigma
        w = np.exp(-0.5 * ((np.arange(length) - (length - 1) * m_) / s) ** 2)
        w /= w.sum()
        return series.rolling(window=length).apply(lambda x: np.dot(x, w), raw=True)
    if ma_type == "TMA":
        # Triangular Moving Average = SMA(SMA(src, half_len), half_len)
        half_len = max(1, (length + 1) // 2)
        sma_half = series.rolling(window=half_len).mean()
        return sma_half.rolling(window=half_len).mean()
    if ma_type == "SSMA":
        # SuperSmoother filter (John Ehlers)
        a1 = np.exp(-1.414 * np.pi / length)
        b1 = 2.0 * a1 * np.cos(1.414 * np.pi / length)
        c3 = -a1 * a1
        c1 = 1.0 - b1 - c3
        result = series.copy().astype(float)
        for i in range(2, len(result)):
            result.iloc[i] = c1 * (series.iloc[i] + series.iloc[i - 1]) / 2.0 + b1 * result.iloc[i - 1] + c3 * result.iloc[i - 2]
        return result
    return series.rolling(window=length).mean()


# ============================================================
#   市场 & 账户
# ============================================================

def ensure_markets():
    global markets_loaded
    if not markets_loaded:
        try:
            binance.load_markets()
            markets_loaded = True
            log.info("市场数据加载完成")
        except Exception as e:
            log.warning(f"加载市场数据失败: {e}")


def detect_hedge_mode():
    """检测账户是否为双向持仓模式（Hedge Mode）."""
    global hedge_mode
    if hedge_mode is not None:
        return hedge_mode
    try:
        # ccxt 隐式方法：fapiPrivateGetPositionSideDual（camelCase）
        resp = binance.fapiPrivateGetPositionSideDual()
        hedge_mode = resp.get("dualSidePosition", False)
    except Exception:
        try:
            # 备选：snake_case 方法名
            resp = binance.fapiPrivate_get_positionSide_dual()
            hedge_mode = resp.get("dualSidePosition", False)
        except Exception:
            try:
                # 最后备选：直接请求
                resp = binance.fapiPrivateGetPositionSideDual()
                hedge_mode = resp.get("dualSidePosition", False)
            except Exception as e:
                log.warning(f"检测持仓模式失败，默认使用双向持仓(Hedge)模式: {e}")
                # Hedge Mode 是币安新账户默认，设 True 更安全
                hedge_mode = True
                return hedge_mode
    mode_name = "双向持仓(Hedge)" if hedge_mode else "单向持仓(One-way)"
    log.info(f"账户持仓模式: {mode_name}")
    return hedge_mode


def order_params(pos_side: str, reduce_only: bool = False):
    """构建订单 extraParams，自动适配 Hedge/One-way 模式.

    在 Hedge Mode 下，positionSide 已足够让币安正确路由订单方向，
    无需 reduceOnly —— 该参数在某些场景会导致 -1106 错误.
    """
    params = {}
    if detect_hedge_mode():
        params["positionSide"] = pos_side
    return params


def minutes_to_interval(minutes: int) -> str:
    """将分钟数转为币安 K 线周期字符串."""
    if minutes >= 60 and minutes % 60 == 0:
        return f"{minutes // 60}h"
    if minutes >= 1440 and minutes % 1440 == 0:
        return f"{minutes // 1440}d"
    return f"{minutes}m"


def fetch_ohlcv(symbol: str, timeframe: str = "1m", limit: int = 500) -> pd.DataFrame:
    raw = binance.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    return df.astype(float)


def get_swap_symbol(raw: str) -> str:
    for m_id, m in binance.markets.items():
        if m.get("id") == raw and m.get("swap"):
            return m_id
    return raw


def fetch_account_balance() -> float:
    """获取合约账户 USDT 可用余额."""
    try:
        bal = binance.fetch_balance()
        usdt = bal.get("USDT", {})
        return float(usdt.get("free", 0) or 0)
    except Exception as e:
        log.warning(f"获取余额失败: {e}")
        return 0.0


def fetch_positions_info():
    """从币安获取所有持仓的强平价格，更新 state 文件."""
    global positions
    try:
        raw = binance.fetch_positions()
        for p in raw:
            symbol = p.get("symbol", "")
            # 找到对应的原始币种名
            for key in list(positions.keys()):
                if get_swap_symbol(key) == symbol:
                    contracts = abs(float(p.get("contracts", 0) or 0))
                    if contracts > 0:
                        positions[key]["liquidation_price"] = float(p.get("liquidationPrice", 0) or 0)
                        positions[key]["unrealized_pnl"] = float(p.get("unrealizedPnl", 0) or 0)
                        positions[key]["mark_price"] = float(p.get("markPrice", 0) or 0)
                    break
        save_state()
    except Exception as e:
        log.warning(f"获取持仓信息失败: {e}")


# ============================================================
#   下单工具
# ============================================================

def usdt_to_contracts(symbol: str, usdt_amount: float, price: float) -> float:
    market = binance.market(symbol)
    cs = market.get("contractSize", 1.0)
    if cs <= 0:
        cs = 1.0
    contracts = usdt_amount / (price * cs)
    return float(binance.amount_to_precision(symbol, contracts))


def fix_qty(symbol: str, qty: float) -> float:
    return float(binance.amount_to_precision(symbol, qty))


def fix_price(symbol: str, price: float) -> float:
    return float(binance.price_to_precision(symbol, price))


def save_trade(data: dict):
    trades = []
    try:
        with open(TRADE_FILE, "r") as f:
            trades = json.load(f)
    except Exception:
        pass
    trades.append(data)
    with open(TRADE_FILE, "w") as f:
        json.dump(trades, f, ensure_ascii=False, indent=2)


def save_state():
    with open(STATE_FILE, "w") as f:
        json.dump(positions, f, ensure_ascii=False, indent=2)


def load_state():
    global positions
    try:
        with open(STATE_FILE, "r") as f:
            positions = json.load(f)
        log.info(f"加载持仓状态: {len(positions)} 个")
    except Exception:
        positions = {}


def sync_exchange_positions():
    """启动时将交易所现有持仓同步到本地状态，防止重启后状态丢失."""
    global positions
    try:
        raw = binance.fetch_positions()
        synced = 0
        for p in raw:
            contracts_val = abs(float(p.get("contracts", 0) or 0))
            if contracts_val <= 0:
                continue
            symbol = p.get("symbol", "")
            clean_sym = symbol.replace("/", "").replace(":USDT", "")
            info = p.get("info", {}) or {}
            pos_side = info.get("positionSide", "LONG")
            entry_price = float(p.get("entryPrice", 0) or 0)
            if entry_price <= 0:
                entry_price = float(info.get("entryPrice", 0) or 0)
            leverage_val = int(info.get("leverage", LEVERAGE) or LEVERAGE)
            margin_mode = info.get("marginType", "isolated")  # cross / isolated
            # 从持仓名义价值推算 usdt_value
            usdt_val = contracts_val * entry_price
            if clean_sym not in positions:
                positions[clean_sym] = {
                    "side": pos_side,
                    "entry_price": round(entry_price, 4),
                    "contracts": contracts_val,
                    "leverage": leverage_val,
                    "margin_mode": margin_mode,
                    "usdt_value": round(usdt_val, 2),
                    "margin": round(usdt_val / leverage_val, 2) if leverage_val > 0 else 0,
                    "liquidation_price": float(info.get("liquidationPrice", 0) or 0),
                }
                synced += 1
                log.info(f"[同步] 从交易所恢复持仓: {clean_sym} {pos_side} "
                         f"{contracts_val}张 @{entry_price:.2f}")
        if synced > 0:
            save_state()
            log.info(f"已同步 {synced} 个交易所持仓到本地状态")
    except Exception as e:
        log.warning(f"同步交易所持仓失败: {e}")


def set_leverage(symbol: str, margin_mode: str = "isolated"):
    try:
        binance.set_margin_mode(symbol, margin_mode)
    except Exception:
        pass
    try:
        binance.set_leverage(LEVERAGE, symbol)
    except Exception:
        pass


def estimate_liquidation(entry: float, leverage: int, side: str) -> float:
    """估算强平价格（隔离保证金 USDT-M，假设维持保证金率 0.5%）."""
    mmr = 0.005  # 维持保证金率
    if side == "LONG":
        return round(entry * (1.0 - 1.0 / leverage + mmr), 4)
    else:
        return round(entry * (1.0 + 1.0 / leverage - mmr), 4)


def place_stop_loss(symbol: str, entry_price: float, quantity: float, pos_side: str, sl_pct: float):
    if sl_pct <= 0:
        return
    sym = get_swap_symbol(symbol)
    sl_qty = fix_qty(sym, quantity)
    if sl_qty <= 0:
        return
    if pos_side == "LONG":
        stop_price = fix_price(sym, entry_price * (1 - sl_pct / 100))
        sp = {"stopPrice": stop_price}
        sp.update(order_params("LONG", reduce_only=True))
        order = binance.create_order(
            symbol=sym, type="stop_market",
            side="sell", amount=sl_qty,
            params=sp,
        )
    else:
        stop_price = fix_price(sym, entry_price * (1 + sl_pct / 100))
        sp = {"stopPrice": stop_price}
        sp.update(order_params("SHORT", reduce_only=True))
        order = binance.create_order(
            symbol=sym, type="stop_market",
            side="buy", amount=sl_qty,
            params=sp,
        )
    if symbol in positions:
        positions[symbol]["stop_order_id"] = order["id"]
    save_state()
    log.info(f"[止损] {sym} {pos_side} 入场 {entry_price}，止损 @ {stop_price} ({sl_pct}%)")


def cancel_stop_loss(symbol: str):
    pos = positions.get(symbol, {})
    if pos.get("stop_order_id"):
        try:
            binance.cancel_order(pos["stop_order_id"], sym := get_swap_symbol(symbol))
            log.info(f"[止损] 已取消 {symbol}")
        except Exception:
            pass
        pos.pop("stop_order_id", None)
        positions[symbol] = pos
        save_state()


# ============================================================
#   跨周期信号
# ============================================================

def compute_signals(df: pd.DataFrame, ma_type: str, ma_len: int,
                    cross_mult: int, tf_minutes: int, delay_offset: int,
                    delay_minutes: int = 0) -> pd.DataFrame:
    """OCC 策略信号 — 精确匹配 TradingView Pine Script v8.13 (lookahead_off).

    Pine Script 的 request.security(lookahead_off) 行为：
    - 高周期K线闭合的那根低周期K线上，HTF 值立即更新为该K线的 MA 值
    - 高周期K线未闭合时，HTF 值保持上一个已完成高周期K线的值
    - pandas resample() 的标签位置与 Pine Script 不同，会导致 1 根K线延迟

    此函数完全复刻 Pine Script 的时间对齐逻辑。
    """
    result = pd.DataFrame(index=df.index)

    close_src = df["close"].shift(delay_offset)
    open_src = df["open"].shift(delay_offset)

    close_ma = calc_ma(close_src, ma_type, ma_len)
    open_ma = calc_ma(open_src, ma_type, ma_len)

    # ── 跨周期 HTF：精确匹配 Pine Script request.security(lookahead_off) ──
    ht_freq = tf_minutes * cross_mult  # 高周期 = 基础周期 × 倍数

    # 将每根K线映射到其所属的高周期（HTF）区间（从 Unix epoch 0 开始对齐）
    epoch_minutes = np.array([int(t.timestamp() // 60) for t in df.index])
    ht_periods = epoch_minutes // ht_freq   # 每根K线所属的 HTF 区间编号

    # 标记 HTF 区间闭合 bar：当前 bar 的区间 ≠ 下一根 bar 的区间
    is_htf_close = np.zeros(len(df), dtype=bool)
    is_htf_close[:-1] = ht_periods[:-1] != ht_periods[1:]
    is_htf_close[-1] = True  # 最后一根 bar 总是其区间的闭合 bar

    # 只在 HTF 闭合 bar 上取 MA 值，其余置 NaN
    close_at_close = np.where(is_htf_close, close_ma.values, np.nan)
    open_at_close = np.where(is_htf_close, open_ma.values, np.nan)

    # forward-fill：每根 bar 获得最近一次 HTF 闭合时的 MA 值
    close_alt = pd.Series(close_at_close, index=df.index).ffill()
    open_alt = pd.Series(open_at_close, index=df.index).ffill()

    # ── 趋势方向 ──
    result["close_ma_alt"] = close_alt
    result["open_ma_alt"] = open_alt
    result["is_long_dir"] = close_alt > open_alt
    result["is_short_dir"] = close_alt < open_alt

    # ── 交叉信号：匹配 ta.crossover() / ta.crossunder() ──
    close_prev = close_alt.shift(1)
    open_prev = open_alt.shift(1)
    result["xlong"] = (close_alt > open_alt) & (close_prev <= open_prev)
    result["xshort"] = (close_alt < open_alt) & (close_prev >= open_prev)

    # ── 信号延迟逻辑（与 Pine Script 完全一致）──
    delay_bars = max(1, int(np.ceil(delay_minutes / tf_minutes))) if delay_minutes > 0 else 0

    long_sig_bar = None
    short_sig_bar = None
    long_cond = [False] * len(result)
    short_cond = [False] * len(result)

    for i in range(len(result)):
        if result["xlong"].iloc[i]:
            long_sig_bar = i
            short_sig_bar = None
        if result["xshort"].iloc[i]:
            short_sig_bar = i
            long_sig_bar = None
        if result["xshort"].iloc[i] and long_sig_bar is not None:
            long_sig_bar = None
        if result["xlong"].iloc[i] and short_sig_bar is not None:
            short_sig_bar = None

        if delay_bars == 0:
            long_cond[i] = bool(result["xlong"].iloc[i])
            short_cond[i] = bool(result["xshort"].iloc[i])
        else:
            long_cond[i] = (long_sig_bar is not None
                            and (i - long_sig_bar) == delay_bars
                            and result["is_long_dir"].iloc[i])
            short_cond[i] = (short_sig_bar is not None
                             and (i - short_sig_bar) == delay_bars
                             and result["is_short_dir"].iloc[i])

    result["long_cond"] = long_cond
    result["short_cond"] = short_cond
    return result


def compute_signals_volty(df: pd.DataFrame, length: int, atr_mult: float) -> pd.DataFrame:
    """
    Volty Expan Close 策略信号 — 精确匹配 TV stop 订单逻辑。

    Pine Script:
        atrs = ta.sma(ta.tr, length) * numATRs
        if (not na(close[length]))
            strategy.entry("VltClsLE", strategy.long, stop=close+atrs)
            strategy.entry("VltClsSE", strategy.short, stop=close-atrs)

    行为：
    - 每根K线收盘后挂 stop 单，下一根K线盘中触及（high/low）即成交
    - 前 length 根K线不挂单（close[length] 为 na）
    """
    result = pd.DataFrame(index=df.index)

    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift(1)).abs(),
        (df["low"] - df["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)

    atrs = tr.rolling(window=length).mean() * atr_mult

    # Pine Script: not na(close[length]) → 前 length 根K线不挂stop单
    valid_stop = pd.Series(False, index=df.index)
    if len(df) > length:
        valid_stop.iloc[length:] = True

    # 每根K线收盘后挂的 stop 单触发价（只有 valid_stop 为 True 的K线才挂单）
    long_stop = df["close"] + atrs
    short_stop = df["close"] - atrs
    long_stop[~valid_stop] = np.nan
    short_stop[~valid_stop] = np.nan

    # 上一根K线的 stop 单，在下一根K线盘中检查是否触发
    long_level = long_stop.shift(1)
    short_level = short_stop.shift(1)

    # high/low 判断：匹配 stop 订单盘中触发
    result["long_cond"] = df["high"] >= long_level
    result["short_cond"] = df["low"] <= short_level

    # 同根K线两个方向都触发，取 open 更靠近的那侧（先触发）
    both = result["long_cond"] & result["short_cond"]
    dist_to_long = abs(df["open"] - long_level)
    dist_to_short = abs(df["open"] - short_level)
    result.loc[both & (dist_to_long > dist_to_short), "long_cond"] = False
    result.loc[both & (dist_to_short >= dist_to_long), "short_cond"] = False

    # 方向判断
    result["is_long_dir"] = df["close"] > df["open"]
    result["is_short_dir"] = df["close"] < df["open"]

    return result


# ============================================================
#   Volty Stop 订单管理（匹配 TradingView 盘中触发）
# ============================================================

def cancel_volty_stop_orders(symbol: str):
    """取消币种所有未成交的 Volty stop 挂单."""
    orders = volty_stop_orders.pop(symbol, None)
    if not orders:
        return
    sym = get_swap_symbol(symbol)
    for key in ("long_order_id", "short_order_id"):
        oid = orders.get(key)
        if oid:
            try:
                binance.cancel_order(oid, sym)
                log.info(f"[Volty] 取消 {symbol} stop挂单 {oid}")
            except Exception:
                pass


def check_volty_order_fills(symbol: str) -> dict:
    """检查 Volty stop 订单是否已成交.

    Returns: {"long_filled": bool, "short_filled": bool, "fill_price": float or None}
    """
    orders = volty_stop_orders.get(symbol)
    if not orders:
        return {"long_filled": False, "short_filled": False, "fill_price": None}

    sym = get_swap_symbol(symbol)
    result = {"long_filled": False, "short_filled": False, "fill_price": None}

    for direction, key in [("long", "long_order_id"), ("short", "short_order_id")]:
        oid = orders.get(key)
        if not oid:
            continue
        try:
            order = binance.fetch_order(oid, sym)
            status = order.get("status", "")
            if status == "closed":
                result[f"{direction}_filled"] = True
                avg_price = order.get("average", 0) or order.get("price", 0)
                if avg_price and avg_price > 0:
                    result["fill_price"] = float(avg_price)
                log.info(f"[Volty] {symbol} {direction} stop单已成交 @ {result['fill_price']}")
        except Exception as e:
            log.warning(f"[Volty] 查询订单 {oid} 失败: {e}")

    return result


def place_volty_stop_orders(symbol: str, df: pd.DataFrame):
    """基于已完成K线，在币安挂 stop 订单（匹配 TV strategy.entry stop= 行为）.

    Pine Script:
        strategy.entry("VltClsLE", strategy.long, stop=close+atrs)
        strategy.entry("VltClsSE", strategy.short, stop=close-atrs)

    stop 单在下一根K线有效，触及即成交。
    """
    global volty_stop_orders

    sym = get_swap_symbol(symbol)
    vlen = get_symbol_param(symbol, "VOLTY_LENGTH", VOLTY_LENGTH)
    vmult = get_symbol_param(symbol, "VOLTY_ATR_MULT", VOLTY_ATR_MULT)

    if df.empty or len(df) < vlen + 3:
        return

    # 用上一根已完成K线（iloc[-2]）的 close 和 ATR 计算 stop 触发价
    prev_close = df["close"].iloc[-2]

    # 计算 ATR（基于截至上一根K线的数据）
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift(1)).abs(),
        (df["low"] - df["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)

    atrs = tr.rolling(window=vlen).mean() * vmult
    prev_atrs = atrs.iloc[-2]

    if np.isnan(prev_atrs) or prev_atrs <= 0:
        return

    long_level = prev_close + prev_atrs
    short_level = prev_close - prev_atrs

    pct = get_symbol_param(symbol, "POSITION_PCT", POSITION_PCT)
    lev = get_symbol_param(symbol, "LEVERAGE", LEVERAGE)
    min_order = get_symbol_param(symbol, "MIN_ORDER_USDT", MIN_ORDER_USDT)
    max_order = get_symbol_param(symbol, "MAX_ORDER_USDT", MAX_ORDER_USDT)

    balance = fetch_account_balance()
    if balance <= 0:
        return
    capital = balance * (pct / 100.0)
    if max_order > 0 and capital > max_order:
        capital = max_order
    usdt_val = capital * lev

    if usdt_val < min_order:
        log.warning(f"[Volty] {symbol} 仓位 {usdt_val:.0f}U < 最小 {min_order}U，不挂单")
        return

    contracts = usdt_to_contracts(sym, usdt_val, prev_close)

    # 设置杠杆和保证金模式
    margin_mode = get_symbol_param(symbol, "MARGIN_MODE", MARGIN_MODE)
    try:
        binance.set_margin_mode(sym, margin_mode)
    except Exception:
        pass
    try:
        binance.set_leverage(lev, sym)
    except Exception:
        pass

    trade_type = get_symbol_param(symbol, "TRADE_TYPE", TRADE_TYPE)
    current_side = positions.get(symbol, {}).get("side")

    # ── 趋势过滤：SMA50 ──
    use_trend = get_symbol_param(symbol, "VOLTY_TREND_FILTER", VOLTY_TREND_FILTER)
    trend_bull = True   # default: allow both directions
    trend_bear = True
    if use_trend and len(df) >= 52:
        sma50 = df["close"].rolling(50).mean().iloc[-2]  # SMA50 of previous completed bar
        prev_close_val = df["close"].iloc[-2]
        trend_bull = prev_close_val > sma50   # only long if above SMA50
        trend_bear = prev_close_val < sma50   # only short if below SMA50

    order_ids = {"bar_time": df.index[-1]}

    # 做多 stop：趋势向上 + trade_type 允许做多 + 当前不是多头
    if trade_type in ("LONG", "BOTH") and current_side != "LONG" and trend_bull:
        long_stop_price = fix_price(sym, long_level)
        contracts_qty = fix_qty(sym, contracts)
        try:
            sp = {"stopPrice": long_stop_price}
            sp.update(order_params("LONG", reduce_only=False))
            order = binance.create_order(
                symbol=sym, type="stop_market",
                side="buy", amount=contracts_qty,
                params=sp,
            )
            order_ids["long_order_id"] = order.get("id", "")
            order_ids["long_level"] = long_level
            log.info(f"[Volty] {symbol} 挂做多stop单 @ {long_stop_price} (close={prev_close:.2f}+{prev_atrs:.2f}) ID={order['id']}")
        except Exception as e:
            log.error(f"[Volty] {symbol} 挂多单失败: {e}")

    # 做空 stop：趋势向下 + trade_type 允许做空 + 当前不是空头
    if trade_type in ("SHORT", "BOTH") and current_side != "SHORT" and trend_bear:
        short_stop_price = fix_price(sym, short_level)
        contracts_qty = fix_qty(sym, contracts)
        try:
            sp = {"stopPrice": short_stop_price}
            sp.update(order_params("SHORT", reduce_only=False))
            order = binance.create_order(
                symbol=sym, type="stop_market",
                side="sell", amount=contracts_qty,
                params=sp,
            )
            order_ids["short_order_id"] = order.get("id", "")
            order_ids["short_level"] = short_level
            log.info(f"[Volty] {symbol} 挂做空stop单 @ {short_stop_price} (close={prev_close:.2f}-{prev_atrs:.2f}) ID={order['id']}")
        except Exception as e:
            log.error(f"[Volty] {symbol} 挂空单失败: {e}")

    if "long_order_id" in order_ids or "short_order_id" in order_ids:
        volty_stop_orders[symbol] = order_ids


# ============================================================
#   下单执行
# ============================================================

def execute_order(action: str, symbol: str, price: float):
    global positions
    ensure_markets()
    if not markets_loaded:
        log.error("市场数据未加载")
        return

    # ── 百分比仓位计算（支持策略覆盖） ──
    pct = get_symbol_param(symbol, "POSITION_PCT", POSITION_PCT)
    lev = get_symbol_param(symbol, "LEVERAGE", LEVERAGE)
    sl_pct = get_symbol_param(symbol, "STOP_LOSS_PCT", STOP_LOSS_PCT)
    min_order = get_symbol_param(symbol, "MIN_ORDER_USDT", MIN_ORDER_USDT)
    max_order = get_symbol_param(symbol, "MAX_ORDER_USDT", MAX_ORDER_USDT)

    balance = fetch_account_balance()
    if balance <= 0:
        log.error("账户余额为 0，跳过")
        return
    capital = balance * (pct / 100.0)
    # MAX_ORDER_USDT 限制的是保证金（本金），不是仓位价值
    if max_order > 0 and capital > max_order:
        capital = max_order
        log.info(f"保证金超过上限，调整为 {max_order}U")
    usdt_val = capital * lev

    if usdt_val < min_order:
        log.warning(f"仓位 {usdt_val:.0f}U < 最小 {min_order}U，跳过")
        return

    sym = get_swap_symbol(symbol)
    if price <= 0:
        ticker = binance.fetch_ticker(sym)
        price = ticker.get("last", 0) or ticker.get("close", 0)

    contracts = usdt_to_contracts(sym, usdt_val, price)
    # 设置保证金模式和杠杆（使用策略覆盖值）
    margin_mode = get_symbol_param(symbol, "MARGIN_MODE", MARGIN_MODE)
    try:
        binance.set_margin_mode(sym, margin_mode)
    except Exception:
        pass
    try:
        binance.set_leverage(lev, sym)
    except Exception:
        pass

    current_side = positions.get(symbol, {}).get("side")
    orders_placed = []

    # ── 翻转：先平旧仓位 ──
    if action == "long_entry" and current_side == "SHORT":
        cancel_stop_loss(symbol)
        prev = positions.get(symbol, {})
        old_contracts = prev.get("contracts", contracts)
        if old_contracts <= 0:
            old_contracts = contracts
        o = binance.create_order(symbol=sym, type="market", side="buy", amount=old_contracts,
                                 params=order_params("SHORT", reduce_only=True))
        orders_placed.append(o)
        flip_pnl = 0.0
        if prev:
            ep = prev.get("entry_price", 0); uv = prev.get("usdt_value", 0)
            flip_pnl = (ep - price) / ep * uv if ep > 0 else 0
        positions.pop(symbol, None)
        save_state()
        save_trade({
            "time": datetime.now(timezone.utc).isoformat(),
            "action": "flip_close_short", "symbol": symbol,
            "side": "buy", "contracts": old_contracts,
            "usdt_value": round(prev.get("usdt_value", 0), 2) if prev else 0,
            "price": round(price, 2),
            "order_id": o.get("id", ""), "realizedPnl": round(flip_pnl, 2),
        })
        log.info(f"[翻转] {symbol} 平空 → buy {old_contracts}张 收益={flip_pnl:.2f}U")
        time.sleep(0.5)

    if action == "short_entry" and current_side == "LONG":
        cancel_stop_loss(symbol)
        prev = positions.get(symbol, {})
        old_contracts = prev.get("contracts", contracts)
        if old_contracts <= 0:
            old_contracts = contracts
        o = binance.create_order(symbol=sym, type="market", side="sell", amount=old_contracts,
                                 params=order_params("LONG", reduce_only=True))
        orders_placed.append(o)
        flip_pnl = 0.0
        if prev:
            ep = prev.get("entry_price", 0); uv = prev.get("usdt_value", 0)
            flip_pnl = (price - ep) / ep * uv if ep > 0 else 0
        positions.pop(symbol, None)
        save_state()
        save_trade({
            "time": datetime.now(timezone.utc).isoformat(),
            "action": "flip_close_long", "symbol": symbol,
            "side": "sell", "contracts": old_contracts,
            "usdt_value": round(prev.get("usdt_value", 0), 2) if prev else 0,
            "price": round(price, 2),
            "order_id": o.get("id", ""), "realizedPnl": round(flip_pnl, 2),
        })
        log.info(f"[翻转] {symbol} 平多 → sell {old_contracts}张 收益={flip_pnl:.2f}U")
        time.sleep(0.5)

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

    order = binance.create_order(symbol=sym, type="market", side=side, amount=contracts,
                                 params=order_params(pos_side, reduce_only=not is_entry))
    orders_placed.append(order)

    # ── 成交均价 ──
    fill_price = price
    try:
        fills = order.get("fills", [])
        if fills:
            total_cost = sum(float(f["price"]) * float(f["contracts"]) for f in fills)
            total_cts = sum(float(f["contracts"]) for f in fills)
            if total_cts > 0:
                fill_price = total_cost / total_cts
    except Exception:
        pass

    log.info(f"[下单] {symbol} {action} {side} {contracts}张 ≈{usdt_val:.0f}U "
             f"保证金≈{capital:.0f}U 强平≈{estimate_liquidation(fill_price, lev, pos_side)}")

    if is_entry:
        est_liq = estimate_liquidation(fill_price, lev, pos_side)
        positions[symbol] = {
            "side": pos_side,
            "entry_price": round(fill_price, 4),
            "contracts": contracts,
            "leverage": lev,
            "margin_mode": margin_mode,
            "usdt_value": round(usdt_val, 2),
            "margin": round(capital, 2),
            "liquidation_price": est_liq,
        }
    else:
        # 计算已实现盈亏
        prev_pos = positions.pop(symbol, None)
        realized_pnl = 0.0
        if prev_pos:
            ep = prev_pos.get("entry_price", 0)
            uv = prev_pos.get("usdt_value", 0)
            if prev_pos.get("side") == "LONG":
                realized_pnl = (fill_price - ep) / ep * uv if ep > 0 else 0
            else:
                realized_pnl = (ep - fill_price) / ep * uv if ep > 0 else 0
    save_state()

    trade_record = {
        "time": datetime.now(timezone.utc).isoformat(),
        "action": action, "symbol": symbol,
        "side": side, "contracts": contracts,
        "usdt_value": round(usdt_val, 2), "price": round(fill_price, 2),
        "order_id": order.get("id", ""),
    }
    if not is_entry:
        trade_record["realizedPnl"] = round(realized_pnl, 2)
    save_trade(trade_record)

    # 交易摘要
    action_names = {"long_entry": "开多", "short_entry": "开空", "exit_long": "平多", "exit_short": "平空"}
    pnl_str = f" 已实现盈亏={realized_pnl:.2f}U" if not is_entry else ""
    log_trade(f"{symbol} {action_names.get(action, action)} | "
              f"方向={pos_side} | 张数={contracts} | 价格={fill_price:.4f} | "
              f"保证金≈{capital:.0f}U | 仓位≈{usdt_val:.0f}U{pnl_str}")

    # ── 止损（多空都设，使用策略配置的止损比例）──
    if is_entry and sl_pct > 0:
        place_stop_loss(symbol, fill_price, contracts, pos_side, sl_pct)

    # ── 更新真实强平价 ──
    time.sleep(0.5)
    fetch_positions_info()


# ============================================================
#   主循环
# ============================================================

def handle_shutdown(sig, frame):
    global running
    log.info("收到退出信号...")
    running = False


os_signal.signal(os_signal.SIGINT, handle_shutdown)
os_signal.signal(os_signal.SIGTERM, handle_shutdown)


def process_symbol(symbol: str) -> None:
    """检查单个币种的信号并执行（支持策略覆盖）.

    - OCC/TEMA: 新K线到来时基于上一根已完成K线检测信号，匹配 TradingView
      process_orders_on_close=true.
    - VOLTY: 新K线到来时取消旧 stop 单，基于上一根K线挂新的 stop 单，
      匹配 TradingView strategy.entry(..., stop=price) 盘中触发行为.
    """
    global last_bar_times

    sym = get_swap_symbol(symbol)
    tf_min = get_symbol_param(symbol, "TIMEFRAME_MINUTES", TIMEFRAME_MINUTES)
    trade_type = get_symbol_param(symbol, "TRADE_TYPE", TRADE_TYPE)
    stype = get_symbol_param(symbol, "STRATEGY_TYPE", STRATEGY_TYPE)

    try:
        df = fetch_ohlcv(sym, minutes_to_interval(tf_min), limit=500)
    except Exception as e:
        log.warning(f"[{symbol}] 获取数据失败: {e}")
        return

    if df.empty or len(df) < 2:
        return

    current_bar = df.index[-1]

    last_time = last_bar_times.get(symbol)
    if last_time is None:
        log.info(f"[{symbol}] 首个 bar: {current_bar}")
        last_bar_times[symbol] = current_bar
        return

    # 同一根K线，跳过（OCC 不检查盘中信号；Volty 由 check_all_volty_fills 处理）
    if current_bar <= last_time:
        return

    # ── 新K线来了 ──
    last_bar_times[symbol] = current_bar

    if stype == "VOLTY":
        # ── Volty: 取消旧 stop 单，挂新单 ──
        cancel_volty_stop_orders(symbol)

        if symbol not in symbols_ready:
            symbols_ready.add(symbol)
            log.info(f"[{symbol}] Volty 就绪，开始挂 stop 单")

        # 检查旧 stop 单是否有成交（在取消之前先检查）
        # cancel_volty_stop_orders 已经 pop 了，这里用不到了

        place_volty_stop_orders(symbol, df)

    else:
        # ── OCC/TEMA: 检查上一根已完成K线的信号 ──
        ma_type = get_symbol_param(symbol, "MA_TYPE", MA_TYPE)
        ma_len = get_symbol_param(symbol, "MA_LEN", MA_LEN)
        cross_mult = get_symbol_param(symbol, "CROSS_MULT", CROSS_MULT)
        delay_min = get_symbol_param(symbol, "DELAY_MINUTES", DELAY_MINUTES)
        delay_off = get_symbol_param(symbol, "DELAY_OFFSET", DELAY_OFFSET)

        sig = compute_signals(df, ma_type, ma_len, cross_mult, tf_min, delay_off, delay_min)

        if len(sig) < 2:
            return

        # 用上一根已完成K线的信号
        prev_sig = sig.iloc[-2]
        long_trig = prev_sig["long_cond"]
        short_trig = prev_sig["short_cond"]
        is_long = prev_sig["is_long_dir"]
        is_short = prev_sig["is_short_dir"]

        # 执行价 = 上一根K线的收盘价（匹配 TradingView process_orders_on_close）
        prev_close = df["close"].iloc[-2]
        prev_bar = df.index[-2]

        if symbol not in symbols_ready:
            symbols_ready.add(symbol)
            log.info(f"[{symbol}] 就绪，开始信号检测 bar={prev_bar}")

        direction = "多" if is_long else ("空" if is_short else "无")
        log.info(f"[{symbol}] bar={prev_bar} close={prev_close:.2f} "
                 f"long={long_trig} short={short_trig} 趋势={direction}")

        current_side = positions.get(symbol, {}).get("side")

        if long_trig:
            if trade_type in ("LONG", "BOTH") and current_side != "LONG":
                try:
                    execute_order("long_entry", symbol, prev_close)
                except Exception as e:
                    log.error(f"[{symbol}] 做多失败: {e}")
            elif trade_type == "SHORT" and current_side == "SHORT":
                try:
                    execute_order("exit_short", symbol, prev_close)
                except Exception as e:
                    log.error(f"[{symbol}] 平空失败: {e}")

        elif short_trig:
            if trade_type in ("SHORT", "BOTH") and current_side != "SHORT":
                try:
                    execute_order("short_entry", symbol, prev_close)
                except Exception as e:
                    log.error(f"[{symbol}] 做空失败: {e}")
            elif trade_type == "LONG" and current_side == "LONG":
                try:
                    execute_order("exit_long", symbol, prev_close)
                except Exception as e:
                    log.error(f"[{symbol}] 平多失败: {e}")


def check_all_volty_fills():
    """主循环每轮调用：检查所有 Volty stop 订单是否成交。

    匹配 TradingView stop 订单盘中触发行为：
    - 如果做多 stop 被触发 → 开多仓 + 设止损
    - 如果做空 stop 被触发 → 开空仓 + 设止损
    - 两个方向同时触发 → 取更靠近 open 的那侧（由订单触发时间自然决定）
    """
    for symbol in list(volty_stop_orders.keys()):
        orders = volty_stop_orders.get(symbol)
        if not orders:
            continue

        sym = get_swap_symbol(symbol)
        current_side = positions.get(symbol, {}).get("side")

        # 如果已有持仓，不再检查同方向的新成交
        long_filled = False
        short_filled = False

        # 检查做多 stop
        long_oid = orders.get("long_order_id")
        if long_oid and current_side != "LONG":
            try:
                order = binance.fetch_order(long_oid, sym)
                if order.get("status") == "closed":
                    long_filled = True
                    avg_price = float(order.get("average", 0) or order.get("price", 0))
                    log.info(f"[Volty] ✅ {symbol} 做多stop单成交 @ {avg_price}")
            except Exception:
                pass

        # 检查做空 stop
        short_oid = orders.get("short_order_id")
        if short_oid and current_side != "SHORT":
            try:
                order = binance.fetch_order(short_oid, sym)
                if order.get("status") == "closed":
                    short_filled = True
                    avg_price = float(order.get("average", 0) or order.get("price", 0))
                    log.info(f"[Volty] ✅ {symbol} 做空stop单成交 @ {avg_price}")
            except Exception:
                pass

        # 同根K线两个方向都成交：取先成交的那侧（由币安订单成交时间自然决定）
        if long_filled and short_filled:
            # 查询两个订单的成交时间，取更早的
            long_time = None
            short_time = None
            try:
                lo = binance.fetch_order(long_oid, sym)
                long_time = lo.get("lastTradeTimestamp", 0) or lo.get("timestamp", 0)
            except Exception:
                pass
            try:
                so = binance.fetch_order(short_oid, sym)
                short_time = so.get("lastTradeTimestamp", 0) or so.get("timestamp", 0)
            except Exception:
                pass
            if long_time and short_time and short_time < long_time:
                long_filled = False  # 空单先成交，忽略多单
            else:
                short_filled = False  # 多单先成交（或无法判断），忽略空单

        if long_filled:
            # 取消另一方向的未成交单
            cancel_volty_stop_orders(symbol)
            sl_pct = get_symbol_param(symbol, "STOP_LOSS_PCT", STOP_LOSS_PCT)
            pct = get_symbol_param(symbol, "POSITION_PCT", POSITION_PCT)
            lev = get_symbol_param(symbol, "LEVERAGE", LEVERAGE)
            max_order = get_symbol_param(symbol, "MAX_ORDER_USDT", MAX_ORDER_USDT)

            balance = fetch_account_balance()
            capital = min(balance * (pct / 100.0), max_order) if max_order > 0 else balance * (pct / 100.0)
            usdt_val = capital * lev
            contracts = usdt_to_contracts(sym, usdt_val, avg_price)

            # 记录持仓
            positions[symbol] = {
                "side": "LONG",
                "entry_price": round(avg_price, 4),
                "contracts": contracts,
                "leverage": lev,
                "margin_mode": get_symbol_param(symbol, "MARGIN_MODE", MARGIN_MODE),
                "usdt_value": round(usdt_val, 2),
                "margin": round(capital, 2),
                "liquidation_price": estimate_liquidation(avg_price, lev, "LONG"),
            }
            save_state()

            save_trade({
                "time": datetime.now(timezone.utc).isoformat(),
                "action": "long_entry", "symbol": symbol,
                "side": "buy", "contracts": contracts,
                "usdt_value": round(usdt_val, 2), "price": round(avg_price, 2),
            })
            log_trade(f"[Volty] {symbol} 开多 | 张数={contracts} | 价格={avg_price:.4f} | 仓位≈{usdt_val:.0f}U")

            if sl_pct > 0:
                place_stop_loss(symbol, avg_price, contracts, "LONG", sl_pct)
            fetch_positions_info()

        elif short_filled:
            cancel_volty_stop_orders(symbol)
            sl_pct = get_symbol_param(symbol, "STOP_LOSS_PCT", STOP_LOSS_PCT)
            pct = get_symbol_param(symbol, "POSITION_PCT", POSITION_PCT)
            lev = get_symbol_param(symbol, "LEVERAGE", LEVERAGE)
            max_order = get_symbol_param(symbol, "MAX_ORDER_USDT", MAX_ORDER_USDT)

            balance = fetch_account_balance()
            capital = min(balance * (pct / 100.0), max_order) if max_order > 0 else balance * (pct / 100.0)
            usdt_val = capital * lev
            contracts = usdt_to_contracts(sym, usdt_val, avg_price)

            positions[symbol] = {
                "side": "SHORT",
                "entry_price": round(avg_price, 4),
                "contracts": contracts,
                "leverage": lev,
                "margin_mode": get_symbol_param(symbol, "MARGIN_MODE", MARGIN_MODE),
                "usdt_value": round(usdt_val, 2),
                "margin": round(capital, 2),
                "liquidation_price": estimate_liquidation(avg_price, lev, "SHORT"),
            }
            save_state()

            save_trade({
                "time": datetime.now(timezone.utc).isoformat(),
                "action": "short_entry", "symbol": symbol,
                "side": "sell", "contracts": contracts,
                "usdt_value": round(usdt_val, 2), "price": round(avg_price, 2),
            })
            log_trade(f"[Volty] {symbol} 开空 | 张数={contracts} | 价格={avg_price:.4f} | 仓位≈{usdt_val:.0f}U")

            if sl_pct > 0:
                place_stop_loss(symbol, avg_price, contracts, "SHORT", sl_pct)
            fetch_positions_info()


def run():
    global running, SYMBOLS

    load_strategy_overrides()

    # 以「策略配置」为准，只交易已配置策略的币种
    if STRATEGY_OVERRIDES:
        SYMBOLS = list(STRATEGY_OVERRIDES.keys())
    if not SYMBOLS:
        log.warning("没有配置任何交易币种，机器人不会下单")

    log.info("=" * 60)
    log.info(f"策略机器人 v2 启动")
    log.info(f"币种: {SYMBOLS}  |  K线: {TIMEFRAME_MINUTES}分钟(默认)")
    log.info(f"均线: {MA_TYPE}({MA_LEN})  |  跨周期: {CROSS_MULT}x  |  延迟: {DELAY_MINUTES}分钟")
    log.info(f"仓位: {POSITION_PCT}%×{LEVERAGE}x  |  止损: {STOP_LOSS_PCT}%  |  方向: {TRADE_TYPE}  |  逐仓模式")
    if STRATEGY_OVERRIDES:
        log.info(f"策略覆盖: {list(STRATEGY_OVERRIDES.keys())}")
    if ACTIVATION_DELAY_MINUTES > 0:
        log.info(f"激活延迟: {ACTIVATION_DELAY_MINUTES} 分钟后开始开单")
    log.info("=" * 60)

    load_state()
    ensure_markets()
    detect_hedge_mode()
    sync_exchange_positions()

    # ── 激活延迟：等待指定分钟后再开始交易 ──
    if ACTIVATION_DELAY_MINUTES > 0:
        trading_start_time = datetime.now(timezone.utc) + timedelta(minutes=ACTIVATION_DELAY_MINUTES)
        log.info(f"策略将在 {trading_start_time.strftime('%H:%M:%S')} 开始交易（{ACTIVATION_DELAY_MINUTES}分钟后）")
        # 在等待期间只读取数据不交易，确保 K 线历史数据预热
        wait_end = time.time() + ACTIVATION_DELAY_MINUTES * 60
        warmup_cycle = 0
        while running and time.time() < wait_end:
            warmup_cycle += 1
            remaining = int(wait_end - time.time())
            if warmup_cycle % 5 == 1:
                log.info(f"[激活等待] 剩余 {remaining} 秒...")
            for symbol in SYMBOLS:
                try:
                    sym = get_swap_symbol(symbol)
                    tf = get_symbol_param(symbol, "TIMEFRAME_MINUTES", TIMEFRAME_MINUTES)
                    df = fetch_ohlcv(sym, minutes_to_interval(tf), limit=5)
                    if not df.empty:
                        last_bar_times[symbol] = df.index[-1]
                except Exception:
                    pass
            time.sleep(10)
        if running:
            log.info("激活等待结束，开始交易！")
        else:
            log.info("策略在等待期间被停止")
            return

    # 初始化 last_bar_times（跳过当前未完成的 bar）
    for symbol in SYMBOLS:
        try:
            sym = get_swap_symbol(symbol)
            tf = get_symbol_param(symbol, "TIMEFRAME_MINUTES", TIMEFRAME_MINUTES)
            df = fetch_ohlcv(sym, minutes_to_interval(tf), limit=5)
            if not df.empty:
                last_bar_times[symbol] = df.index[-1]
                log.info(f"[{symbol}] 初始化，当前 bar: {df.index[-1]}")
        except Exception as e:
            log.warning(f"[{symbol}] 初始化失败: {e}")

    cycle = 0
    while running:
        try:
            cycle += 1

            # 每 5 个循环更新一次真实强平价
            if cycle % 5 == 0:
                fetch_positions_info()

            # 每 10 个循环输出心跳
            if cycle % 10 == 1:
                log.info(f"[心跳] 第 {cycle} 轮，等待K线信号中...")

            for symbol in SYMBOLS:
                process_symbol(symbol)

            # Volty: 每轮检查 stop 订单是否成交（匹配 TV 盘中触发）
            check_all_volty_fills()

            time.sleep(12)

        except Exception as e:
            log.error(f"主循环异常: {e}")
            time.sleep(30)

    log.info("策略机器人已停止")


if __name__ == "__main__":
    run()
