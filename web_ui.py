"""
量化交易系统 — Web 界面 v3（实时数据版）
==========================================
所有数据来自币安实时API + 本地交易记录。
"""

import json
import os
import sys
import subprocess
import time
import math
import base64
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
import ccxt
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, TimeoutError as ThreadTimeoutError
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, urlparse

from db import (
    register_user, login_user, get_user_by_token, logout_user,
    get_user_dir, ensure_user_dir, init_preset_accounts,
    reset_password, hash_password as db_hash_password,
)

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """多线程 HTTP Server，支持并发请求."""
    daemon_threads = True
    # 限制最大等待队列，防止请求堆积
    request_queue_size = 50

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

TRADE_FILE = BASE_DIR / "trades.json"
STATE_FILE = BASE_DIR / "strategy_state.json"
LOG_FILE = BASE_DIR / "strategy.log"
ENV_FILE = BASE_DIR / ".env"
WATCH_FILE = BASE_DIR / "watchlist.json"
STRATEGIES_FILE = BASE_DIR / "strategies.json"


def _user_file(username: str | None, filename: str) -> Path:
    """获取用户专属文件路径，用户为空时回退到根目录."""
    if username:
        d = ensure_user_dir(username)
        return d / filename
    return BASE_DIR / filename


PORT = int(os.getenv("UI_PORT", "8000"))

# ── 代理配置 ──
PROXY_URL = os.getenv("PROXY_URL", "")  # 本地代理 URL，如 http://127.0.0.1:7890
_proxies = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None

# ── 币安连接（ccxt timeout 单位是毫秒，30000=30秒）──
public_binance = ccxt.binance({"enableRateLimit": True, "timeout": 30000, "options": {"defaultType": "swap"}})
if _proxies:
    public_binance.session.proxies.update(_proxies)
if os.getenv("BINANCE_API_URL"):
    public_binance.urls["api"] = os.getenv("BINANCE_API_URL")

# 启动时预热：加载市场数据（通过代理首次请求较慢，提前加载避免后续超时）
try:
    public_binance.load_markets()
    print(f"[启动] 市场数据加载完成，共 {len(public_binance.markets)} 个市场")
except Exception as e:
    print(f"[启动] 市场数据加载失败（VPN/代理正常吗?）: {e}")

# ── 用户专属 Binance 客户端缓存 ──
_user_private_clients: dict = {}  # username -> ccxt.binance 或 None
_user_public_clients: dict = {}   # username -> ccxt.binance (公开行情，走用户代理)
_user_client_times: dict = {}     # username -> last_use_timestamp


def _get_user_api_keys(username: str) -> dict:
    """读取用户 .env 中的 API 密钥."""
    env_file = _user_file(username, ".env")
    result = {"apiKey": "", "secretKey": "", "proxyUrl": ""}
    try:
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("BINANCE_API_KEY="):
                    result["apiKey"] = line.split("=", 1)[1].strip()
                elif line.startswith("BINANCE_SECRET_KEY="):
                    result["secretKey"] = line.split("=", 1)[1].strip()
                elif line.startswith("PROXY_URL="):
                    result["proxyUrl"] = line.split("=", 1)[1].strip()
    except Exception:
        pass
    # 不回退到根目录，每个用户必须配置自己的密钥
    return result


def _get_private_binance(username: str):
    """获取或创建用户专属的 Binance 私有客户端（5 分钟缓存）."""
    now = time.time()
    if username in _user_private_clients:
        if now - _user_client_times.get(username, 0) < 300:
            _user_client_times[username] = now
            return _user_private_clients[username]

    keys = _get_user_api_keys(username)
    api_key = keys["apiKey"]
    api_secret = keys["secretKey"]
    proxy_url = keys["proxyUrl"]

    if not api_key or not api_secret:
        _user_client_times[username] = now
        _user_private_clients[username] = None
        return None

    try:
        client = ccxt.binance({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "timeout": 30000,
            "options": {"defaultType": "swap"},
        })
        if proxy_url:
            client.session.proxies.update({"http": proxy_url, "https": proxy_url})
        if os.getenv("BINANCE_API_URL"):
            client.urls["api"] = os.getenv("BINANCE_API_URL")
        _user_private_clients[username] = client
    except Exception:
        _user_private_clients[username] = None

    _user_client_times[username] = now
    return _user_private_clients[username]


def _get_public_binance(username: str):
    """获取用户专属的公网 Binance 客户端（走用户代理）."""
    if username in _user_public_clients:
        return _user_public_clients[username]
    keys = _get_user_api_keys(username)
    proxy_url = keys.get("proxyUrl", "")
    if not proxy_url:
        _user_public_clients[username] = public_binance  # 回退到共享客户端
        return public_binance
    try:
        client = ccxt.binance({"enableRateLimit": True, "timeout": 30000, "options": {"defaultType": "swap"}})
        client.session.proxies.update({"http": proxy_url, "https": proxy_url})
        client.load_markets()
        _user_public_clients[username] = client
    except Exception:
        _user_public_clients[username] = public_binance
    return _user_public_clients[username]


# ── 缓存 ──
_ticker_cache = {}
_ticker_cache_time = 0
_balance_cache = {}
_balance_cache_time = 0
_binance_ok = True  # Binance 是否可达
_binance_check_time = 0


def load_env(username=None):
    cfg = {
        "SYMBOLS": "ETHUSDT,BTCUSDT", "MA_TYPE": "TEMA", "MA_LEN": "8",
        "CROSS_MULT": "3", "TIMEFRAME_MINUTES": "1", "DELAY_MINUTES": "5",
        "POSITION_PCT": "60", "LEVERAGE": "3", "STOP_LOSS_PCT": "5", "TAKE_PROFIT_PCT": "1.5",
        "TRADE_TYPE": "BOTH", "MAX_ORDER_USDT": "0", "MIN_ORDER_USDT": "11",
        "ACTIVATION_DELAY_MINUTES": "0",
        "STRATEGY_TYPE": "TEMA", "VOLTY_LENGTH": "5", "VOLTY_ATR_MULT": "0.75",
        "MARGIN_MODE": "isolated",
    }
    env_file = _user_file(username, ".env")
    try:
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    if k.strip() in cfg:
                        cfg[k.strip()] = v.strip()
    except Exception:
        pass
    return cfg


def save_env(form_data: dict, username=None):
    env_file = _user_file(username, ".env")
    api_key, api_sec = "", ""
    try:
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("BINANCE_API_KEY="):
                    api_key = line.split("=", 1)[1].strip()
                if line.startswith("BINANCE_SECRET_KEY="):
                    api_sec = line.split("=", 1)[1].strip()
    except FileNotFoundError:
        pass
    lines = [
        "# 量化交易配置文件", "",
        f"BINANCE_API_KEY={api_key}", f"BINANCE_SECRET_KEY={api_sec}", "",
        f"MIN_ORDER_USDT={form_data.get('MIN_ORDER_USDT', '11')}",
        f"MAX_ORDER_USDT={form_data.get('MAX_ORDER_USDT', '0')}",
        f"STOP_LOSS_PCT={form_data['STOP_LOSS_PCT']}",
        f"TAKE_PROFIT_PCT={form_data.get('TAKE_PROFIT_PCT', '1.5')}",
        f"LEVERAGE={form_data['LEVERAGE']}",
        f"MARGIN_MODE={form_data.get('MARGIN_MODE', 'isolated')}",
        "PORT=8000", "WEBHOOK_SECRET=", "",
        f"SYMBOLS={form_data['SYMBOLS']}",
        f"TIMEFRAME_MINUTES={form_data['TIMEFRAME_MINUTES']}",
        f"STRATEGY_TYPE={form_data.get('STRATEGY_TYPE', 'TEMA')}",
        f"MA_TYPE={form_data['MA_TYPE']}",
        f"MA_LEN={form_data['MA_LEN']}",
        f"CROSS_MULT={form_data['CROSS_MULT']}",
        f"DELAY_MINUTES={form_data['DELAY_MINUTES']}",
        "DELAY_OFFSET=0",
        f"VOLTY_LENGTH={form_data.get('VOLTY_LENGTH', '5')}",
        f"VOLTY_ATR_MULT={form_data.get('VOLTY_ATR_MULT', '0.75')}",
        f"VOLTY_TREND_TYPE={form_data.get('VOLTY_TREND_TYPE', 'EMA')}",
        f"POSITION_PCT={form_data['POSITION_PCT']}",
        f"TRADE_TYPE={form_data['TRADE_TYPE']}",
        f"ACTIVATION_DELAY_MINUTES={form_data.get('ACTIVATION_DELAY_MINUTES', '0')}",
    ]
    with open(env_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def load_trades(username=None):
    trade_file = _user_file(username, "trades.json")
    try:
        with open(trade_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        # 去重：相同 symbol + contracts + price + time(HH:MM) 只保留一条
        seen = set()
        deduped = []
        for t in data:
            key = (t.get("symbol", ""), t.get("contracts", 0), t.get("price", 0), str(t.get("time", ""))[:16])
            if key not in seen:
                seen.add(key)
                deduped.append(t)
        if len(deduped) < len(data):
            save_trades(deduped)
        return deduped
    except Exception:
        return []


def save_trades(trades, username=None):
    trade_file = _user_file(username, "trades.json")
    with open(trade_file, "w", encoding="utf-8") as f:
        json.dump(trades, f, ensure_ascii=False, indent=2)


def load_positions(username=None):
    state_file = _user_file(username, "strategy_state.json")
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_positions(pos, username=None):
    state_file = _user_file(username, "strategy_state.json")
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(pos, f, ensure_ascii=False, indent=2)


# 防止重复记录手动平仓（每个仓位只记录一次）
_manual_exit_seen = set()


def sync_manual_exits(exchange_positions, local_positions, trades):
    """检测手动平仓：本地有但交易所没有的仓位，生成平仓记录（仅一次）."""
    # 安全阀：交易所没有返回任何持仓数据时，不进行比对（可能是API故障）
    if not exchange_positions:
        return False, []

    now_utc = datetime.now(timezone.utc).isoformat(timespec="seconds") + "Z"
    now_ts = int(time.time() * 1000)
    try:
        from urllib.request import Request, urlopen
        url = "https://fapi.binance.com/fapi/v1/premiumIndex"
        req = Request(url)
        with urlopen(req, timeout=10) as resp:
            premium_data = json.loads(resp.read().decode())
        mark_prices = {item["symbol"]: float(item["markPrice"]) for item in premium_data}
    except Exception:
        mark_prices = {}

    exchange_syms = set()
    for p in exchange_positions:
        contracts_val = abs(float(p.get("contracts", 0) or 0))
        if contracts_val > 0:  # 只计入有实际持仓的
            raw = clean_symbol(p.get("symbol", ""))
            exchange_syms.add(raw)

    modified = False
    closed_syms = []
    for sym, pos in list(local_positions.items()):
        if sym not in exchange_syms:
            contracts = abs(float(pos.get("contracts", 0) or 0))
            if contracts <= 0:
                del local_positions[sym]
                modified = True
                continue
            entry_price = float(pos.get("entry_price", 0) or 0)
            pos_key = f"{sym}_{contracts}_{entry_price}"
            if pos_key in _manual_exit_seen:
                del local_positions[sym]
                modified = True
                continue
            _manual_exit_seen.add(pos_key)
            closed_syms.append(sym)
            usdt_value = float(pos.get("usdt_value", 0) or 0)
            side = pos.get("side", "LONG")
            exit_price = mark_prices.get(sym, entry_price)
            if side == "LONG":
                realized_pnl = (exit_price - entry_price) / entry_price * usdt_value if entry_price > 0 else 0
            else:
                realized_pnl = (entry_price - exit_price) / entry_price * usdt_value if entry_price > 0 else 0
            trades.append({
                "time": now_utc,
                "timestamp": now_ts,
                "symbol": sym,
                "action": "exit_manual",
                "side": side,
                "price": round(exit_price, 4),
                "contracts": contracts,
                "usdt_value": round(usdt_value, 2),
                "realizedPnl": round(realized_pnl, 2),
                "note": "手动平仓（系统检测）",
            })
            del local_positions[sym]
            modified = True

    return modified, closed_syms
def load_logs(n=100, username=None):
    log_file = _user_file(username, "strategy.log")
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            lines = f.readlines()[-n:]
            lines.reverse()
            return "".join(lines)
    except Exception:
        return ""


def load_watchlist(username=None):
    watch_file = _user_file(username, "watchlist.json")
    try:
        with open(watch_file, "r") as f:
            return json.load(f)
    except Exception:
        return ["ETHUSDT", "BTCUSDT"]


def save_watchlist(data, username=None):
    watch_file = _user_file(username, "watchlist.json")
    with open(watch_file, "w") as f:
        json.dump(data, f)


def load_strategies(username=None):
    """加载策略配置列表."""
    strategies_file = _user_file(username, "strategies.json")
    try:
        with open(strategies_file, "r") as f:
            return json.load(f)
    except Exception:
        return []


def save_strategies(data, username=None):
    strategies_file = _user_file(username, "strategies.json")
    with open(strategies_file, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ═══════════════════════ 用户个人信息 ═══════════════════════

def load_profile(username: str) -> dict:
    pf = _user_file(username, "profile.json")
    try:
        with open(pf, "r") as f:
            return json.load(f)
    except Exception:
        return {"nickname": username, "avatar": ""}

def save_profile(username: str, data: dict):
    pf = _user_file(username, "profile.json")
    with open(pf, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def clean_symbol(sym: str) -> str:
    """ETH/USDT:USDT → ETHUSDT, BTC/USDT:USDT → BTCUSDT"""
    s = str(sym)
    if ":" in s:
        s = s.split(":")[0]
    return s.replace("/", "")


def fmt_time(val):
    """UTC时间 → 北京时间 YYYY-MM-DD HH:MM:SS，支持ISO字符串和毫秒时间戳"""
    if not val:
        return ""
    try:
        if isinstance(val, (int, float)):
            dt = datetime.fromtimestamp(val / 1000, tz=timezone.utc)
        else:
            dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
        local = dt.astimezone(timezone(timedelta(hours=8)))
        return local.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        s = str(val)
        return s[:19].replace("T", " ") if len(s) >= 19 else s


def kill_bot_windows():
    """在 Windows 上终止 strategy_bot.py 进程."""
    try:
        result = subprocess.run(
            ['wmic', 'process', 'where', 'name like "python%.exe"',
             'get', 'commandline,processid', '/format:csv'],
            capture_output=True, text=True, timeout=10)
        for line in result.stdout.split('\n'):
            if 'strategy_bot.py' in line:
                try:
                    pid = line.strip().split(',')[-1].strip()
                    if pid.isdigit():
                        subprocess.run(['taskkill', '/F', '/PID', pid], capture_output=True)
                except Exception:
                    pass
    except Exception:
        pass


def is_bot_running(username=None):
    """检测 strategy_bot.py 是否在运行（跨平台），可选按用户过滤."""
    try:
        if sys.platform == "win32":
            result = subprocess.run(
                ['wmic', 'process', 'where', 'name like "python%.exe"',
                 'get', 'commandline', '/format:csv'],
                capture_output=True, text=True, timeout=5)
            for line in result.stdout.split('\n'):
                if 'strategy_bot.py' in line:
                    if username and f'--user {username}' not in line:
                        continue
                    return True
            return False
        else:
            if username:
                result = subprocess.run(
                    ["pgrep", "-f", f"strategy_bot.py.*--user {username}"],
                    capture_output=True, text=True, timeout=5)
            else:
                result = subprocess.run(
                    ["pgrep", "-f", "strategy_bot.py"],
                    capture_output=True, text=True, timeout=5)
            return len(result.stdout.strip()) > 0
    except Exception:
        return False


def restart_bot(username=None):
    if sys.platform == "win32":
        kill_bot_windows()
    else:
        subprocess.run(["pkill", "-f", "strategy_bot.py"], capture_output=True)
    time.sleep(1)
    args = [sys.executable, str(BASE_DIR / "strategy_bot.py")]
    if username:
        args.extend(["--user", username])
    subprocess.Popen(
        args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(BASE_DIR),
    )


# ============================================================
#   实时数据获取
# ============================================================

def _run_with_timeout(fn, timeout_sec=3, *args, **kwargs):
    """在独立线程中执行函数，超时则返回 None."""
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(fn, *args, **kwargs)
        return future.result(timeout=timeout_sec)
    except (ThreadTimeoutError, Exception):
        return None
    finally:
        executor.shutdown(wait=False)


def get_tickers(client=None):
    """获取所有合约 Ticker（缓存5秒）."""
    global _ticker_cache, _ticker_cache_time
    cl = client or public_binance
    now = time.time()
    if now - _ticker_cache_time < 5 and _ticker_cache:
        return _ticker_cache
    try:
        tickers = _run_with_timeout(cl.fetch_tickers, 5)
        if tickers:
            _ticker_cache = {k: v for k, v in tickers.items() if k.endswith("USDT") and ":USDT" in k}
            _ticker_cache_time = now
    except Exception:
        pass
    return _ticker_cache or {}


def get_klines(symbol: str, timeframe: str = "1m", limit: int = 100):
    """获取 K 线."""
    try:
        sym = symbol if "/" in symbol else f"{symbol[:-4]}/{symbol[-4:]}:{symbol[-4:]}" if symbol.endswith("USDT") else symbol
        klines = _run_with_timeout(
            lambda: public_binance.fetch_ohlcv(sym, timeframe=timeframe, limit=limit), 5)
        return klines if klines else []
    except Exception:
        return []


def get_account_balance(username=None):
    """获取合约账户余额."""
    global _balance_cache, _balance_cache_time
    now = time.time()
    if now - _balance_cache_time < 10 and _balance_cache:
        return _balance_cache
    try:
        pb = _get_private_binance(username) if username else None
        if pb:
            bal = _run_with_timeout(
                lambda: pb.fetch_balance(params={"type": "swap"}), 5)
            if bal:
                _balance_cache = bal
                _balance_cache_time = now
                return bal
    except Exception:
        pass
    return _balance_cache or {}


# 资金费率 & 手续费缓存
_funding_cache = {}
_funding_cache_time = 0
_fee_info = None


def get_funding_rates():
    """获取所有合约资金费率（缓存30秒）— 用币安原生API."""
    global _funding_cache, _funding_cache_time
    now = time.time()
    if now - _funding_cache_time < 30 and _funding_cache:
        return _funding_cache

    def _fetch():
        import urllib.request
        url = "https://fapi.binance.com/fapi/v1/premiumIndex"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read().decode())

    try:
        data = _run_with_timeout(_fetch, 5)
        if data:
            _funding_cache = {}
            for item in data:
                _funding_cache[item["symbol"]] = {
                    "symbol": item["symbol"],
                    "fundingRate": float(item.get("lastFundingRate", 0) or 0),
                    "markPrice": float(item.get("markPrice", 0) or 0),
                    "nextFundingTime": int(item.get("nextFundingTime", 0) or 0),
                }
            _funding_cache_time = now
    except Exception:
        pass
    return _funding_cache or {}


def get_next_funding_time():
    """获取下一次资金费率结算时间（用已有缓存，不重复请求API）."""
    rates = _funding_cache  # 直接用缓存，不重复请求
    if rates:
        first = next(iter(rates.values()))
        ts = first.get("nextFundingTime", 0)
        if ts > 0:
            next_utc = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
            local_time = next_utc.astimezone(timezone(timedelta(hours=8)))
            now_utc = datetime.now(timezone.utc)
            seconds_left = int((next_utc - now_utc).total_seconds())
            return {
                "timestamp": ts,
                "display": local_time.strftime("%H:%M:%S"),
                "seconds_left": max(0, seconds_left),
            }
    # fallback: 推算
    now_utc = datetime.now(timezone.utc)
    hour = now_utc.hour
    settle_hours = [0, 8, 16]
    for h in settle_hours:
        if hour < h:
            next_time = now_utc.replace(hour=h, minute=0, second=0, microsecond=0)
            break
    else:
        next_time = now_utc.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    local_time = next_time.astimezone(timezone(timedelta(hours=8)))
    return {
        "timestamp": int(next_time.timestamp() * 1000),
        "display": local_time.strftime("%H:%M:%S"),
        "seconds_left": int((next_time - now_utc).total_seconds()),
    }


def get_fee_info(username=None):
    """获取用户手续费率."""
    global _fee_info
    if _fee_info:
        return _fee_info
    try:
        pbc = _get_private_binance(username) if username else None
        if pbc:
            fees = _run_with_timeout(pbc.fetch_trading_fees, 5)
            if fees:
                linear = fees.get("linear", {}) if isinstance(fees, dict) else {}
                _fee_info = {
                    "taker": float(linear.get("taker", 0.0004) or 0.0004),
                    "maker": float(linear.get("maker", 0.0002) or 0.0002),
                }
                return _fee_info
    except Exception:
        pass
    _fee_info = {"taker": 0.0004, "maker": 0.0002}
    return _fee_info


def get_positions_from_exchange(username=None):
    """从币安获取实时持仓."""
    try:
        pbc = _get_private_binance(username) if username else None
        if pbc:
            positions = _run_with_timeout(pbc.fetch_positions, 5)
            return positions if positions else []
    except Exception:
        pass
    return []


def calc_ma(data, length):
    """简单移动平均."""
    if len(data) < length:
        return [None] * len(data)
    result = [None] * (length - 1)
    for i in range(length - 1, len(data)):
        result.append(round(sum(data[i - length + 1:i + 1]) / length, 4))
    return result


# ============================================================
#   HTML 页面
# ============================================================

# ═══════════════════════════════════════════════════════════════
#  登录页
# ═══════════════════════════════════════════════════════════════

def _hash_password(password: str) -> str:
    """SHA-256 密码哈希."""
    import hashlib
    return hashlib.sha256(password.encode("utf-8")).hexdigest()

PAGE_LOGIN = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>量化交易系统 — 登录</title>
<style>
:root {
  --bg: #0c0f16; --card: #111620; --border: #1c2333; --text: #9599a3;
  --title: #c8ccd4; --accent: #3b82f6; --green: #22c55e; --red: #ef4444;
}
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family: 'Segoe UI','Microsoft YaHei',sans-serif; background:var(--bg); color:var(--text);
       display:flex; justify-content:center; align-items:center; height:100vh; }
.login-box { background:var(--card); border:1px solid var(--border); border-radius:12px;
             padding:40px; width:380px; max-width:90vw; }
.login-box h1 { font-size:20px; color:var(--title); text-align:center; margin-bottom:4px; }
.login-box .sub { font-size:12px; color:var(--text); text-align:center; margin-bottom:28px; }
.login-box label { display:block; font-size:12px; color:var(--text); margin-bottom:4px; margin-top:16px; }
.login-box input { width:100%; padding:10px 12px; background:#0d1117; border:1px solid var(--border);
                   border-radius:6px; color:var(--title); font-size:14px; }
.login-box input:focus { outline:none; border-color:var(--accent); }
.login-box .btn { width:100%; padding:10px; border:none; border-radius:6px; font-size:14px;
                  cursor:pointer; font-weight:500; margin-top:20px; }
.btn-primary { background:var(--accent); color:#fff; }
.btn-primary:hover { opacity:.85; }
.login-box .links { text-align:center; margin-top:16px; font-size:12px; }
.login-box .links a { color:var(--accent); text-decoration:none; }
.login-box .links a:hover { text-decoration:underline; }
.error-msg { color:var(--red); font-size:12px; text-align:center; margin-top:12px; display:none; }
</style>
</head>
<body>
<div class="login-box">
  <h1>📈 量化交易系统</h1>
  <div class="sub">交易员测试平台</div>
  <label>账户名</label>
  <input type="text" id="username" placeholder="请输入账户名" autocomplete="off">
  <label>密码</label>
  <input type="password" id="password" placeholder="请输入密码" onkeydown="if(event.key==='Enter')doLogin()">
  <button class="btn btn-primary" onclick="doLogin()">登 录</button>
  <div class="error-msg" id="errorMsg"></div>
  <div class="links">
    还没有账户？<a href="/register">立即注册</a>
  </div>
</div>
<script>
const apiUrl = '/api';
async function api(path, opts={}) {
  const init = {};
  if (opts.method === 'POST') {
    init.method = 'POST';
    init.headers = {'Content-Type': 'application/json'};
    init.body = opts.body;
  }
  const r = await fetch(apiUrl + path, init);
  return r.json();
}
async function doLogin() {
  const username = document.getElementById('username').value.trim();
  const password = document.getElementById('password').value;
  const errEl = document.getElementById('errorMsg');
  if (!username || !password) {
    errEl.textContent = '请输入账户名和密码';
    errEl.style.display = 'block';
    return;
  }
  errEl.style.display = 'none';
  const result = await api('/auth/login', {
    method: 'POST',
    body: JSON.stringify({username, password})
  });
  if (result.ok) {
    localStorage.setItem('token', result.token);
    localStorage.setItem('username', result.user.username);
    window.location.href = '/app';
  } else {
    errEl.textContent = result.error || '登录失败';
    errEl.style.display = 'block';
  }
}
</script>
</body>
</html>
"""

# ═══════════════════════════════════════════════════════════════
#  注册页
# ═══════════════════════════════════════════════════════════════
PAGE_REGISTER = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>量化交易系统 — 注册</title>
<style>
:root {
  --bg: #0c0f16; --card: #111620; --border: #1c2333; --text: #9599a3;
  --title: #c8ccd4; --accent: #3b82f6; --green: #22c55e; --red: #ef4444;
}
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family: 'Segoe UI','Microsoft YaHei',sans-serif; background:var(--bg); color:var(--text);
       display:flex; justify-content:center; align-items:center; height:100vh; }
.reg-box { background:var(--card); border:1px solid var(--border); border-radius:12px;
           padding:40px; width:380px; max-width:90vw; }
.reg-box h1 { font-size:20px; color:var(--title); text-align:center; margin-bottom:4px; }
.reg-box .sub { font-size:12px; color:var(--text); text-align:center; margin-bottom:28px; }
.reg-box label { display:block; font-size:12px; color:var(--text); margin-bottom:4px; margin-top:16px; }
.reg-box input { width:100%; padding:10px 12px; background:#0d1117; border:1px solid var(--border);
                 border-radius:6px; color:var(--title); font-size:14px; }
.reg-box input:focus { outline:none; border-color:var(--accent); }
.reg-box .btn { width:100%; padding:10px; border:none; border-radius:6px; font-size:14px;
                cursor:pointer; font-weight:500; margin-top:20px; }
.btn-primary { background:var(--accent); color:#fff; }
.btn-primary:hover { opacity:.85; }
.reg-box .links { text-align:center; margin-top:16px; font-size:12px; }
.reg-box .links a { color:var(--accent); text-decoration:none; }
.reg-box .links a:hover { text-decoration:underline; }
.error-msg { color:var(--red); font-size:12px; text-align:center; margin-top:12px; display:none; }
.success-msg { color:var(--green); font-size:12px; text-align:center; margin-top:12px; display:none; }
</style>
</head>
<body>
<div class="reg-box">
  <h1>📈 量化交易系统</h1>
  <div class="sub">注册新账户</div>
  <label>账户名</label>
  <input type="text" id="username" placeholder="请输入账户名（1-30字符）" autocomplete="off">
  <label>密码</label>
  <input type="password" id="password" placeholder="请输入密码（至少4位）">
  <label>确认密码</label>
  <input type="password" id="password2" placeholder="请再次输入密码" onkeydown="if(event.key==='Enter')doRegister()">
  <button class="btn btn-primary" onclick="doRegister()">注 册</button>
  <div class="error-msg" id="errorMsg"></div>
  <div class="success-msg" id="successMsg"></div>
  <div class="links">
    已有账户？<a href="/">立即登录</a>
  </div>
</div>
<script>
const apiUrl = '/api';
async function api(path, opts={}) {
  const init = {};
  if (opts.method === 'POST') {
    init.method = 'POST';
    init.headers = {'Content-Type': 'application/json'};
    init.body = opts.body;
  }
  const r = await fetch(apiUrl + path, init);
  return r.json();
}
async function doRegister() {
  const username = document.getElementById('username').value.trim();
  const password = document.getElementById('password').value;
  const password2 = document.getElementById('password2').value;
  const errEl = document.getElementById('errorMsg');
  const sucEl = document.getElementById('successMsg');
  errEl.style.display = 'none';
  sucEl.style.display = 'none';
  if (!username || !password) {
    errEl.textContent = '请输入账户名和密码';
    errEl.style.display = 'block';
    return;
  }
  if (password !== password2) {
    errEl.textContent = '两次密码输入不一致';
    errEl.style.display = 'block';
    return;
  }
  if (password.length < 4) {
    errEl.textContent = '密码至少4位';
    errEl.style.display = 'block';
    return;
  }
  const result = await api('/auth/register', {
    method: 'POST',
    body: JSON.stringify({username, password})
  });
  if (result.ok) {
    sucEl.textContent = '注册成功！即将跳转到登录页...';
    sucEl.style.display = 'block';
    setTimeout(() => { window.location.href = '/app'; }, 1500);
  } else {
    errEl.textContent = result.error || '注册失败';
    errEl.style.display = 'block';
  }
}
</script>
</body>
</html>
"""

# ═══════════════════════════════════════════════════════════════
#  账户搜索页
# ═══════════════════════════════════════════════════════════════
PAGE_SEARCH = r"""
<div style="max-width:600px;margin:0 auto;">
  <div class="card">
    <h3>🔍 账户搜索</h3>
    <div class="inline-input" style="margin-bottom:16px;">
      <input type="text" id="searchInput" placeholder="输入账户名搜索..." onkeydown="if(event.key==='Enter')doSearch()">
      <button class="btn btn-primary" onclick="doSearch()">搜索</button>
    </div>
    <div id="searchResults" style="margin-top:12px;"></div>
  </div>
</div>
<script>
async function doSearch() {
  const keyword = document.getElementById('searchInput').value.trim();
  const container = document.getElementById('searchResults');
  if (!keyword) {
    container.innerHTML = '<p style="color:var(--text);font-size:12px;">请输入搜索关键词</p>';
    return;
  }
  const token = localStorage.getItem('token') || '';
  const result = await fetch('/api/users/search?keyword=' + encodeURIComponent(keyword) + '&token=' + token).then(r=>r.json());
  if (!result.users || result.users.length === 0) {
    container.innerHTML = '<p style="color:var(--text);font-size:12px;">未找到匹配的用户</p>';
    return;
  }
  const myName = localStorage.getItem('username') || '';
  container.innerHTML = result.users.map(u => {
    const isMe = u.username === myName;
    return `<div style="display:flex;align-items:center;justify-content:space-between;
      padding:12px;border:1px solid var(--border);border-radius:6px;margin-bottom:8px;
      background:var(--bg);">
      <div>
        <div style="color:var(--title);font-weight:bold;font-size:14px;">${u.username} ${isMe ? '<span class="tag tag-success">我</span>' : ''}</div>
        <div style="font-size:11px;color:var(--text);">注册时间: ${u.createdAt || '--'}</div>
      </div>
      <button class="btn btn-outline btn-sm" onclick="viewUser('${u.username}')">查看仪表盘</button>
    </div>`;
  }).join('');
}
function viewUser(username) {
  window.open('/user/' + username + '/dashboard', '_blank');
}
</script>
"""

# ═══════════════════════════════════════════════════════════════
#  用户仪表盘查看页（只读）
# ═══════════════════════════════════════════════════════════════
PAGE_USER_DASHBOARD = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>用户仪表盘</title>
<script src="/chart.js"></script>
<style>
:root {
  --bg: #0c0f16; --card: #111620; --border: #1c2333; --text: #9599a3;
  --title: #c8ccd4; --accent: #3b82f6; --green: #22c55e; --red: #ef4444;
}
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family: 'Segoe UI','Microsoft YaHei',sans-serif; background:var(--bg); color:var(--text); }
.header { padding:12px 20px; border-bottom:1px solid var(--border); display:flex;
          justify-content:space-between; align-items:center; background:var(--card); }
.header h2 { font-size:15px; color:var(--title); }
.content { padding:20px; max-width:1200px; margin:0 auto; }
.stat-cards { display:grid; grid-template-columns:repeat(auto-fill,minmax(180px,1fr)); gap:10px; margin-bottom:16px; }
.stat-card { background:var(--card); border:1px solid var(--border); border-radius:8px; padding:12px 14px; }
.stat-card .label { font-size:11px; color:var(--text); margin-bottom:4px; }
.stat-card .value { font-size:20px; font-weight:bold; color:var(--title); }
.stat-card .sub { font-size:11px; margin-top:2px; }
.green { color:var(--green); } .red { color:var(--red); }
.card { background:var(--card); border:1px solid var(--border); border-radius:6px; padding:16px; margin-bottom:16px; box-shadow:0 0 12px rgba(0,255,65,0.05); }
.card h3 { font-size:14px; color:var(--title); margin-bottom:12px; display:flex; align-items:center; gap:8px; }
.card h3::before { content:''; width:3px; height:14px; background:var(--accent); border-radius:2px; }
table { width:100%; border-collapse:collapse; font-size:12px; }
th { background:#191e2a; color:var(--text); padding:8px 10px; text-align:left; font-weight:500; }
td { padding:7px 10px; border-bottom:1px solid var(--border); }
.grid2 { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
.chart-wrap { position:relative; height:280px; }
.chart-wrap canvas { width:100%!important; height:100%!important; }
::-webkit-scrollbar { width:5px; }
::-webkit-scrollbar-track { background:var(--bg); }
::-webkit-scrollbar-thumb { background:#2a3040; border-radius:3px; }
</style>
</head>
<body>
<div class="header">
  <h2>📋 <span id="viewUserName">--</span> 的交易仪表盘</h2>
  <div>
    <span style="font-size:12px;color:var(--text);margin-right:12px;">只读模式</span>
    <button class="btn btn-outline btn-sm" onclick="window.close()" style="padding:6px 12px;background:transparent;border:1px solid var(--border);color:var(--text);border-radius:4px;cursor:pointer;">关闭</button>
  </div>
</div>
<div class="content">
  <div class="stat-cards" id="statCards"></div>
  <div class="grid2">
    <div class="card"><h3>📈 收益走势</h3>
      <div class="chart-wrap"><canvas id="equityChart"></canvas></div>
    </div>
    <div class="card"><h3>📋 近期交易</h3>
      <div id="recentTrades" style="max-height:250px;overflow-y:auto;"></div>
    </div>
  </div>
</div>
<script>
const username = window.location.pathname.split('/')[2];
document.getElementById('viewUserName').textContent = username;

async function load() {
  const r = await fetch('/api/user/' + username + '/dashboard');
  const d = await r.json();
  if (d.error) {
    document.getElementById('statCards').innerHTML = '<p style="color:var(--red)">' + d.error + '</p>';
    return;
  }
  document.getElementById('statCards').innerHTML = [
    {label:'账户余额', value:'$'+d.balance.toFixed(2)},
    {label:'可用余额', value:'$'+d.available.toFixed(2)},
    {label:'未实现盈亏', value:'$'+d.unrealizedPnl.toFixed(2), cls:(d.unrealizedPnl>=0?'green':'red')},
    {label:'持仓数量', value:d.positionCount},
    {label:'今日盈亏', value:'$'+d.todayPnl.toFixed(2), cls:(d.todayPnl>=0?'green':'red')},
    {label:'今日交易', value:d.todayTrades+' 笔'},
  ].map(s=>`<div class="stat-card"><div class="label">${s.label}</div><div class="value ${s.cls||''}">${s.value}</div></div>`).join('');

  if (d.recentTrades && d.recentTrades.length > 0) {
    document.getElementById('recentTrades').innerHTML = '<table>' +
      '<tr><th>时间</th><th>币种</th><th>方向</th><th>价格</th><th>盈亏</th></tr>' +
      d.recentTrades.map(t=>`<tr><td>${t.time}</td><td>${t.symbol}</td><td>${t.action}</td><td>${t.price}</td><td class="${(t.realizedPnl||0)>=0?'green':'red'}">${(t.realizedPnl||0)>=0?'+':''}${(t.realizedPnl||0).toFixed(2)}</td></tr>`).join('') +
      '</table>';
  } else {
    document.getElementById('recentTrades').innerHTML = '<p style="color:var(--text);font-size:12px;">暂无交易记录</p>';
  }

  if (d.profitLabels && d.profitLabels.length > 0) {
    const ctx = document.getElementById('equityChart').getContext('2d');
    new Chart(ctx, {type:'line',data:{labels:d.profitLabels,datasets:[{label:'累计盈亏',data:d.profitData,borderColor:'#3b82f6',backgroundColor:'rgba(59,130,246,.1)',fill:true,tension:.3,pointRadius:0}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{x:{ticks:{color:'#9599a3',font:{size:10}}},y:{ticks:{color:'#9599a3',font:{size:10}}}}}});
  }
}
load();
</script>
</body>
</html>
"""

HTML_USER = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>量化交易系统</title>
<script src="/chart.js"></script>
<style>
:root {
  --bg: #060606; --card: #0d0d0d; --border: rgba(0,255,65,0.12);
  --text: #999; --title: #e0e0e0; --accent: #00ff41; --green: #00ff41;
  --red: #ff4466; --orange: #ff9500; --sidebar: #080808; --hover: rgba(0,255,65,0.05);
}
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family: 'Segoe UI','Microsoft YaHei',sans-serif; background:var(--bg); color:var(--text); display:flex; height:100vh; overflow:hidden; }
.sidebar { width:200px; min-width:200px; background:var(--sidebar); border-right:1px solid var(--border); display:flex; flex-direction:column; padding:16px 0; }
.sidebar .logo { padding:0 20px 20px; font-size:16px; font-weight:bold; color:var(--title); border-bottom:1px solid var(--border); margin-bottom:8px; }
.sidebar .logo span { font-size:11px; color:var(--green); display:block; margin-top:2px; }
.sidebar a { display:flex; align-items:center; gap:8px; padding:10px 20px; color:var(--text); text-decoration:none; font-size:13px; transition:all .2s; border-left:2px solid transparent; }
.sidebar a:hover, .sidebar a.active { background:var(--hover); color:var(--title); border-left-color:var(--accent); }
.sidebar a .icon { width:18px; text-align:center; font-size:15px; }
.main { flex:1; display:flex; flex-direction:column; overflow:hidden; }
.header { padding:12px 20px; border-bottom:1px solid var(--border); display:flex; justify-content:space-between; align-items:center; background:var(--card); }
.header h2 { font-size:15px; color:var(--title); }
.header .right { display:flex; align-items:center; gap:12px; font-size:12px; }
.badge { padding:3px 8px; border-radius:10px; font-size:11px; }
.badge.on { background:rgba(34,197,94,.15); color:var(--green); }
.badge.off { background:rgba(239,68,68,.15); color:var(--red); }
.content { flex:1; overflow-y:auto; padding:20px; }
.stat-cards { display:grid; grid-template-columns:repeat(auto-fill,minmax(180px,1fr)); gap:10px; margin-bottom:16px; }
.stat-card { background:var(--card); border:1px solid var(--border); border-radius:6px; padding:12px 14px; cursor:default; box-shadow:0 0 12px rgba(0,255,65,0.05); transition:all .2s; }
.stat-card:hover { border-color:var(--accent); box-shadow:0 0 20px rgba(0,255,65,0.12); }
.stat-card.watch { cursor:pointer; transition:all .2s; }
.stat-card.watch:hover { border-color:var(--accent); }
.stat-card .label { font-size:11px; color:var(--text); margin-bottom:4px; }
.stat-card .value { font-size:20px; font-weight:bold; color:var(--title); }
.stat-card .sub { font-size:11px; margin-top:2px; }
.green { color:var(--green); } .red { color:var(--red); }
.card { background:var(--card); border:1px solid var(--border); border-radius:6px; padding:16px; margin-bottom:16px; box-shadow:0 0 12px rgba(0,255,65,0.05); }
.card h3 { font-size:14px; color:var(--title); margin-bottom:12px; display:flex; align-items:center; gap:8px; }
.card h3::before { content:''; width:3px; height:14px; background:var(--accent); border-radius:2px; }
.grid2 { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
@media(max-width:900px){.grid2{grid-template-columns:1fr}}
table { width:100%; border-collapse:collapse; font-size:12px; }
th { background:#191e2a; color:var(--text); padding:8px 10px; text-align:left; font-weight:500; }
td { padding:7px 10px; border-bottom:1px solid var(--border); }
tr:hover td { background:rgba(255,255,255,.02); }
input,select { width:100%; padding:8px 10px; background:#0d1117; border:1px solid var(--border); border-radius:4px; color:var(--title); font-size:13px; }
input:focus,select:focus { outline:none; border-color:var(--accent); }
label { display:block; font-size:12px; color:var(--text); margin-bottom:4px; margin-top:12px; }
.btn { padding:8px 16px; border:none; border-radius:4px; font-size:13px; cursor:pointer; font-weight:500; }
.btn-primary { background:var(--accent); color:#fff; }
.btn-green { background:var(--green); color:#000; }
.btn-red { background:var(--red); color:#fff; }
.btn-outline { background:transparent; border:1px solid var(--border); color:var(--text); }
.btn-sm { padding:4px 10px; font-size:11px; }
.period-btn { padding:2px 8px; border:1px solid var(--border); background:transparent; color:var(--text); font-size:10px; cursor:pointer; border-radius:3px; margin:0 1px; }
.period-btn.active { background:var(--accent); color:#fff; border-color:var(--accent); }
.btn:hover { opacity:.85; }
.form-row { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
.tag { display:inline-block; padding:2px 8px; border-radius:3px; font-size:11px; margin:1px; }
.tag-info { background:rgba(59,130,246,.15); color:#60a5fa; }
.tag-success { background:rgba(34,197,94,.15); color:#4ade80; }
.tag-warn { background:rgba(245,158,11,.15); color:#fbbf24; }
.tag-error { background:rgba(239,68,68,.15); color:#f87171; }
.log-line { font-family:'Cascadia Code',Consolas,monospace; font-size:11px; padding:3px 0; border-bottom:1px solid rgba(255,255,255,.02); line-height:1.5; }
.chart-wrap { position:relative; height:280px; }
.chart-wrap canvas { width:100%!important; height:100%!important; }
.inline-input { display:flex; gap:6px; align-items:center; }
.inline-input input { flex:1; }
::-webkit-scrollbar { width:5px; }
::-webkit-scrollbar-track { background:var(--bg); }
::-webkit-scrollbar-thumb { background:#2a3040; border-radius:3px; }
.watch-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(160px,1fr)); gap:8px; margin-bottom:16px; }
.watch-card { background:var(--card); border:1px solid var(--border); border-radius:8px; padding:10px 12px; }
.watch-card .pair { font-size:13px; color:var(--title); font-weight:bold; }
.watch-card .price { font-size:18px; color:var(--title); font-weight:bold; margin:4px 0; }
.watch-card .change { font-size:11px; }
.watch-card .remove { float:right; color:var(--red); cursor:pointer; font-size:14px; opacity:.5; }
.watch-card .remove:hover { opacity:1; }
.modal-overlay { display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,.7); z-index:1000; align-items:center; justify-content:center; }
.modal-overlay.show { display:flex; }
.modal { background:var(--card); border:1px solid var(--border); border-radius:12px; padding:24px; width:360px; max-width:90vw; }
.modal h3 { font-size:15px; color:var(--title); margin-bottom:16px; }
.modal input { margin-bottom:12px; }
.modal .btn-row { display:flex; gap:8px; justify-content:flex-end; margin-top:8px; }
.modal .error-msg { color:var(--red); font-size:11px; margin-top:4px; display:none; }
.tab-nav { display:flex; gap:0; margin-bottom:12px; border-bottom:1px solid var(--border); }
.tab-nav button { padding:8px 16px; border:none; background:transparent; color:var(--text); font-size:13px; cursor:pointer; border-bottom:2px solid transparent; transition:all .2s; }
.tab-nav button.active { color:var(--accent); border-bottom-color:var(--accent); }
.tab-content { display:none; }
.tab-content.active { display:block; }
.table-wrap { overflow-x:auto; -webkit-overflow-scrolling:touch; }
.table-wrap table { min-width:600px; }
.hamburger { display:none; background:none; border:none; color:var(--title); font-size:22px; cursor:pointer; padding:4px; }
.sidebar-overlay { display:none; }
/* 手机适配 */
@media(max-width:768px){
  body { flex-direction:column; }
  .sidebar { position:fixed; left:-220px; top:0; height:100%; z-index:1001; transition:left .3s; width:200px; }
  .sidebar.open { left:0; box-shadow:4px 0 20px rgba(0,0,0,.8); }
  .sidebar-overlay { display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,.5); z-index:1000; }
  .sidebar-overlay.show { display:block; }
  .hamburger { display:inline-block; }
  .main { width:100%; flex:1; }
  .header { padding:8px 12px; }
  .header h2 { font-size:14px; }
  .content { padding:8px; }
  .stat-cards { grid-template-columns:repeat(2,1fr); gap:6px; }
  .stat-card { padding:8px 10px; }
  .stat-card .value { font-size:16px; }
  .stat-card .label { font-size:10px; }
  .grid2 { grid-template-columns:1fr; gap:8px; }
  .form-row { grid-template-columns:1fr; gap:6px; }
  .card { padding:10px; margin-bottom:8px; }
  .card h3 { font-size:13px; }
  .chart-wrap { height:220px; }
  .watch-grid { grid-template-columns:repeat(auto-fill,minmax(120px,1fr)); gap:6px; }
  .watch-card { padding:8px 10px; }
  .watch-card .price { font-size:15px; }
  table { font-size:10px; }
  th,td { padding:5px 6px; }
  .tab-nav button { padding:6px 10px; font-size:11px; }
  .btn { padding:6px 12px; font-size:12px; }
  .modal { width:90vw; padding:16px; }
  .logo { font-size:14px; }
  .badge { font-size:10px; }
  input,select { font-size:16px !important; padding:8px; }
}
</style>
</head>
<body>

<nav class="sidebar">
  <div class="logo" style="display:flex;align-items:center;gap:8px">
    <img id="avatarImg" src="" style="width:36px;height:36px;border-radius:50%;object-fit:cover;border:2px solid var(--accent)">
    <div>量化交易<span>交易员测试</span></div>
  </div>
  <a href="#dashboard" class="active" data-page="dashboard"><span class="icon">📋</span> 仪表盘</a>
  <a href="#strategy_op" data-page="strategy_op"><span class="icon">🎯</span> 策略配置</a>
  <a href="#monitor" data-page="monitor"><span class="icon">📈</span> 数据监控</a>
  <a href="#analysis" data-page="analysis"><span class="icon">📊</span> 资产分析</a>
  <a href="#logs" data-page="logs"><span class="icon">📝</span> 系统日志</a>
  <a href="#settings" data-page="settings"><span class="icon">🔧</span> 设置中心</a>
</nav>

<div class="main">
  <div class="sidebar-overlay" id="sidebarOverlay" onclick="toggleSidebar()"></div>
<div class="header">
    <button class="hamburger" onclick="toggleSidebar()" title="菜单">☰</button>
    <h2 id="pageTitle">仪表盘</h2>
    <div class="right">
      <div class="user-area" onclick="toggleUserMenu()" style="display:flex;align-items:center;gap:8px;cursor:pointer;margin-right:16px;">
        <img id="avatarTop" src="" style="width:28px;height:28px;border-radius:50%;border:1px solid var(--accent);object-fit:cover;display:none;" onerror="this.style.display='none'">
        <span id="usernameDisplay" style="color:var(--accent);font-size:12px;"></span>
        <div class="user-dropdown" id="userDropdown" style="display:none;position:absolute;top:40px;right:16px;background:var(--card);border:1px solid var(--accent);border-radius:6px;min-width:120px;z-index:200;">
          <a href="#" onclick="event.preventDefault();showProfileModal();" style="display:block;padding:8px 14px;font-size:11px;color:var(--text);text-decoration:none;">编辑资料</a>
          <a href="#" onclick="event.preventDefault();showPwdModal2();" style="display:block;padding:8px 14px;font-size:11px;color:var(--text);text-decoration:none;">修改密码</a>
          <a href="#" onclick="event.preventDefault();doLogout();" style="display:block;padding:8px 14px;font-size:11px;color:var(--red);text-decoration:none;">退出登录</a>
        </div>
      </div>
      <span id="liveTime">--</span>
      <span id="botStatus" class="badge off">● 离线</span>
    </div>
  </div>
  <div class="content" id="mainContent"></div>
</div>

<script>
// ── 登录检查 ──
if(!localStorage.getItem('token')){
  window.location.href='/';
}
const pages={dashboard:'仪表盘',strategy_op:'策略配置',monitor:'数据监控',analysis:'资产分析',logs:'系统日志',settings:'设置中心'};
let currentPage='dashboard', chartInstances={}, refreshTimer=null;

document.querySelectorAll('.sidebar a').forEach(a=>{
  a.addEventListener('click',e=>{e.preventDefault();navigate(a.dataset.page);});
});

function toggleSidebar(){
  const sb=document.querySelector('.sidebar');
  const ov=document.getElementById('sidebarOverlay');
  sb.classList.toggle('open');
  if(ov)ov.classList.toggle('show');
}
async function navigate(page){
  currentPage=page;
  document.querySelectorAll('.sidebar a').forEach(a=>a.classList.remove('active'));
  document.querySelector(`[data-page="${page}"]`).classList.add('active');
  document.getElementById('pageTitle').textContent=pages[page];
  // 手机端：切换页面后关闭侧边栏
  const sb=document.querySelector('.sidebar');
  if(sb.classList.contains('open'))toggleSidebar();
  if(refreshTimer){clearInterval(refreshTimer);refreshTimer=null;}
  await loadPage(page);
}

async function api(path,opts={}){
  const init={};
  if(opts.method==='POST'){init.method='POST';init.headers={'Content-Type':'application/json'};init.body=opts.body;}
  const token=localStorage.getItem('token')||'';
  let url='/api'+path;
  if(token){url+=(url.includes('?')?'&':'?')+'token='+encodeURIComponent(token);}
  const r=await fetch(url,init);
  if(r.status===401){localStorage.removeItem('token');localStorage.removeItem('username');window.location.href='/';return{error:'unauthorized'};}
  return r.json();
}

async function loadPage(page){
  const mc=document.getElementById('mainContent');
  const html=await(await fetch('/page/'+page)).text();
  mc.innerHTML=html;
  Object.values(chartInstances).forEach(c=>c.destroy?.());
  chartInstances={};
  if(page==='dashboard'){initDashboard();refreshTimer=setInterval(refreshDashboard,5000);}
  if(page==='strategy_op')initStrategyOp();
  if(page==='monitor'){initMonitor();refreshTimer=setInterval(refreshMonitor,8000);}
  if(page==='analysis')initAnalysis();
  if(page==='logs')initLogs();
  if(page==='settings')initSettings();
}

function updateClock(){
  document.getElementById('liveTime').textContent=new Date().toLocaleString('zh-CN');
}
setInterval(updateClock,1000);updateClock();

// ── 认证 ──
async function doLogout(){
  await api('/auth/logout',{method:'POST',body:'{}'});
  localStorage.removeItem('token');
  localStorage.removeItem('username');
  window.location.href='/';
}
(async function(){
  const me=await api('/auth/me');
  if(me.user){
    document.getElementById('usernameDisplay').textContent=me.user.username;
    localStorage.setItem('username',me.user.username);
  }
})();

async function updateBotStatus(){
  const d=await api('/status');
  const el=document.getElementById('botStatus');
  // 只在不刷新dashboard时更新bot状态
  if (!refreshTimer) {
    el.className='badge '+(d.running?'on':'off');
    el.textContent=d.running?'● 运行中':'● 策略停止';
  }
}
setInterval(updateBotStatus,5000);updateBotStatus();

// ========== 仪表盘 ==========
async function initDashboard(){
  // 检查是否已配置币安密钥
  const keys=await api('/apikeys');
  if(!keys.configured){
    document.getElementById('mainContent').innerHTML='<div class=\"card\" style=\"text-align:center;padding:60px 20px\"><h3 style=\"color:var(--orange);margin-bottom:16px\">⚙️ 请先配置币安 API 密钥</h3><p style=\"color:var(--text);margin-bottom:24px\">您还没有配置币安 API Key 和 Secret Key，<br>无法连接交易所查看行情和交易。</p><button class=\"btn btn-primary\" onclick=\"navigate(\'settings\')\">前往设置 →</button></div>';
    return;
  }
  refreshDashboard();loadHistoryPositions();
}

async function refreshDashboard(){
  const d=await api('/dashboard');
  // 更新连接状态
  const bs=document.getElementById('botStatus');
  if (d.offline) {
    bs.className='badge off'; bs.textContent='⚠ 币安离线(需VPN)';
  }
  const set=(id,v,cls)=>{const el=document.getElementById(id);if(el){el.textContent=v;if(cls)el.className=cls;}};
  set('dashBalance',(d.balance||0).toFixed(2)+' USDT');
  set('dashPnl',(d.unrealizedPnl||0).toFixed(2)+' USDT','value '+(d.unrealizedPnl>=0?'green':'red'));
  set('dashTodayPnl',(d.todayPnl||0).toFixed(2)+' USDT','value '+(d.todayPnl>=0?'green':'red'));
  set('dashPosCount',d.positionCount||0);
  set('dashTradeCount',d.todayTrades||0);

  // 下次资金费率结算倒计时
  if(d.nextFunding){
    const cd=document.getElementById('dashFundingCD');
    if(cd){
      const secs=d.nextFunding.seconds_left||0;
      const h=Math.floor(secs/3600), m=Math.floor((secs%3600)/60), s=secs%60;
      cd.textContent=h+'时'+m+'分'+s+'秒';
      cd.style.color=secs<1800?'var(--orange)':'var(--title)';
    }
    setText('dashFeeRate',(d.feeInfo?.takerPct||0.04).toFixed(3)+'% taker');
    setText('dashFeeRateVal',(d.feeInfo?.takerPct||0.04).toFixed(3)+'%');
  }

  // 当前持仓表格
  const ct=document.getElementById('currentPositions');
  if(ct && d.positions){
    if(d.positions.length===0){
      ct.innerHTML='<tr><td colspan="11" style="text-align:center;color:var(--text)">暂无持仓</td></tr>';
    }else{
      ct.innerHTML=d.positions.map(p=>{
        const side=p.side==='LONG'?'多头':'空头';
        const sideColor=p.side==='LONG'?'var(--green)':'var(--red)';
        const pnl=(p.unrealizedPnl||0);
        const pnlColor=pnl>=0?'var(--green)':'var(--red)';
        const fr=(p.fundingRate||0);
        const frColor=fr>0?'var(--green)':(fr<0?'var(--red)':'var(--text)');
        return `<tr>
          <td>${(p.symbol||'').replace(':USDT','')}</td>
          <td style="color:${sideColor};font-weight:bold">${side}</td>
          <td>${(p.entryPrice||0).toFixed(4)}</td>
          <td>${(p.markPrice||0).toFixed(4)}</td>
          <td style="color:var(--red)">${(p.liquidationPrice||0).toFixed(4)}</td>
          <td>${p.leverage||0}x</td>
          <td>${p.marginMode||'--'}</td>
          <td>${p.contracts||0}</td>
          <td style="color:${frColor};font-size:11px" title="${p.fundingLabel||''}">${fr>=0?'+':''}${fr.toFixed(4)}%</td>
          <td style="font-size:11px">${(p.openFee||0).toFixed(4)}+${(p.closeFee||0).toFixed(4)}=<b>${(p.totalFee||0).toFixed(4)}U</b></td>
          <td style="color:${pnlColor};font-weight:bold">${pnl>=0?'+':''}${pnl.toFixed(2)}U</td>
        </tr>`;
      }).join('');
    }
  }

  // 自选币种
  const wg=document.getElementById('watchGrid');
  if(wg && d.watchlist){
    wg.innerHTML=d.watchlist.map(w=>{
      const ch=w.change||0;
      const cc=ch>=0?'green':'red';
      return `<div class="watch-card">
        <span class="remove" onclick="removeWatch('${w.symbol}')" title="移除">×</span>
        <div class="pair">${(w.symbol||'').replace(':USDT','')}</div>
        <div class="price">${(w.price||0).toFixed(w.symbol?.startsWith('BTC')?2:4)}</div>
        <div class="change ${cc}">${ch>=0?'+':''}${ch.toFixed(2)}%</div>
      </div>`;
    }).join('');
  }

  // 收益图 - 支持时间周期切换
  async function loadEquity(period,btn){
    document.querySelectorAll('.period-btn').forEach(b=>b.classList.remove('active'));
    if(btn)btn.classList.add('active');
    try{
      const resp=await fetch('/api/equity?period='+period);
      const d=await resp.json();
      if(!d.labels||!d.labels.length)return;
      const ctx=document.getElementById('profitChart')?.getContext('2d');
      if(!ctx)return;
      if(chartInstances.profit)chartInstances.profit.destroy();
      const isUp=d.data[d.data.length-1]>=d.data[0];
      chartInstances.profit=new Chart(ctx,{
        type:'line',
        data:{
          labels:d.labels,
          datasets:[{label:'权益曲线',data:d.data,borderColor:isUp?'#22c55e':'#ef4444',backgroundColor:isUp?'rgba(34,197,94,.08)':'rgba(239,68,68,.08)',fill:true,tension:.3,pointRadius:0}]
        },
        options:{
          responsive:true,maintainAspectRatio:false,
          interaction:{intersect:false,mode:'index'},
          plugins:{legend:{display:false},tooltip:{callbacks:{label:ctx=>'USD '+ctx.raw.toFixed(2)}}},
          scales:{x:{ticks:{color:'#555',font:{size:9},maxTicksLimit:12},grid:{color:'#1c2333'}},y:{ticks:{color:'#555',font:{size:9},callback:v=>'$'+v.toFixed(0)},grid:{color:'#1c2333'}}}
        }
      });
    }catch(e){}
  }

  // 收益图（首次加载）
  if(!chartInstances.profit)loadEquity('1D');

  // 最近交易
  const rt=document.getElementById('recentTrades');
  if(rt && d.recentTrades){
    const names={'long_entry':'做多','exit_long':'平多','short_entry':'做空','exit_short':'平空',
      'flip_close_long':'翻转平多','flip_close_short':'翻转平空','exit_manual':'手动平仓',
      '买入':'买入','卖出':'卖出','手动买入':'手动买入','手动卖出':'手动卖出'};
    rt.innerHTML=d.recentTrades.map(t=>`<tr>
      <td>${t.time||''}</td><td>${t.symbol||''}</td><td>${names[t.action]||t.action||'--'}</td>
      <td>${t.contracts||''}</td><td>${t.price||''}</td><td>${t.usdt_value||''}U</td></tr>`).join('');
  }
}

async function loadHistoryPositions(){
  const d=await api('/history/positions');
  setText('dashTotalRealPnl',(d.totalPnl||0).toFixed(2)+' USDT');
  const el=document.getElementById('dashTotalRealPnl');
  if(el)el.className='value '+(d.totalPnl>=0?'green':'red');
  setText('histTotal',d.totalClosed||0);
  setText('histTotalPnl',(d.totalPnl||0).toFixed(2));
  setText('histWinRate',(d.winRate||0).toFixed(1)+'%');
  const ht=document.getElementById('historyPositions');
  if(ht && d.positions){
    if(d.positions.length===0){
      ht.innerHTML='<tr><td colspan="7" style="text-align:center;color:var(--text)">暂无交易记录</td></tr>';
    }else{
      ht.innerHTML=d.positions.map(p=>{
        const pnl=p.realizedPnl||0;
        const pnlColor=pnl>=0?'var(--green)':'var(--red)';
        const actionColor=p.action?.includes('平')||p.action?.includes('卖')?'var(--orange)':(p.action?.includes('买')?'var(--green)':'var(--text)');
        const isManual=p.isManual?' (手动)':'';
        return `<tr>
          <td>${p.entryTime||p.exitTime||''}</td>
          <td>${(p.symbol||'').replace(':USDT','')}</td>
          <td style="color:${actionColor}">${p.action||(p.side==='LONG'?'多头':'空头')}${isManual}</td>
          <td>${(p.price||p.entryPrice||0).toFixed(4)}</td>
          <td>${p.contracts||0}</td>
          <td>${(p.usdtValue||0).toFixed(2)}U</td>
          <td style="color:${pnlColor};font-weight:bold">${pnl>0?'+':''}${pnl.toFixed(2)}U</td>
        </tr>`;
      }).join('');
    }
  }
}

function switchPosTab(tab,btn){
  document.querySelectorAll('.tab-nav button').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  document.querySelectorAll('.tab-content').forEach(c=>c.classList.remove('active'));
  document.getElementById('tab'+(tab==='current'?'Current':'History')).classList.add('active');
}

async function addWatch(){
  const inp=document.getElementById('watchInput');
  const sym=(inp.value||'').toUpperCase().trim();
  if(!sym){alert('输入币种');return;}
  await api('/watchlist/add',{method:'POST',body:JSON.stringify({symbol:sym})});
  inp.value='';
  refreshDashboard();
}

async function removeWatch(sym){
  await api('/watchlist/remove',{method:'POST',body:JSON.stringify({symbol:sym})});
  refreshDashboard();
}

// ========== 交易策略 ==========
async function initStrategy(){
  const d=await api('/status');
  const cfg=await api('/config');
  setText('stratStatus',d.running?'运行中':'已停止');
  const sel=document.getElementById('stratStatus');
  if(sel){sel.className='badge '+(d.running?'on':'off');}
  setText('stratBtn',d.running?'停止策略':'启动策略');
  const btn=document.getElementById('stratBtn');
  if(btn)btn.onclick=()=>toggleBot(!d.running);
  const actDelay=parseInt(cfg.ACTIVATION_DELAY_MINUTES||'0');
  setText('stratActDelay',actDelay>0?actDelay+' 分钟':'立即启动');
  const stype=cfg.STRATEGY_TYPE||'TEMA';
  if(stype==='VOLTY'){
    setText('stratName','Volty Expan Close Strategy');
    setText('stratTypeLabel','波动性突破（ATR通道）');
    setText('stratLogic','ATR通道突破，Bar收盘确认，每根K线更新入场价');
  }else{
    setText('stratName','OCC Strategy v8.13');
    setText('stratTypeLabel','趋势跟随（TEMA 跨周期）');
    setText('stratLogic','跨周期均线交叉 + 延迟确认');
  }
  const fields={stratSymbols:cfg.SYMBOLS,stratTF:cfg.TIMEFRAME_MINUTES+'分钟',stratMA:cfg.MA_TYPE+'('+cfg.MA_LEN+')',stratCross:cfg.CROSS_MULT+'x',stratDelay:cfg.DELAY_MINUTES+'分钟',stratPct:cfg.POSITION_PCT+'%',stratLev:cfg.LEVERAGE+'x',stratSL:cfg.STOP_LOSS_PCT+'%',stratType:cfg.TRADE_TYPE,stratVoltyLen:cfg.VOLTY_LENGTH||'5',stratVoltyMult:(cfg.VOLTY_ATR_MULT||'0.75')+'x'};
  for(const[id,val]of Object.entries(fields))setText(id,val);
  // 根据策略类型显示/隐藏行
  const showTema=stype!=='VOLTY';
  const showVolty=stype==='VOLTY';
  document.getElementById('stratRowMA').style.display=showTema?'':'none';
  document.getElementById('stratRowCross').style.display=showTema?'':'none';
  document.getElementById('stratRowVoltyLen').style.display=showVolty?'':'none';
  document.getElementById('stratRowVoltyMult').style.display=showVolty?'':'none';
}

function setText(id,v){const el=document.getElementById(id);if(el)el.textContent=v;}

async function toggleBot(start){
  await api(start?'/bot/start':'/bot/stop',{method:'POST',body:'{}'});
  setTimeout(initStrategy,1500);
}

// ========== 策略配置 ==========
let strategyList=[];
async function initStrategyOp(){
  // ── 策略密码验证 ──
  const pwdCheck = await api('/strategy-password/status');
  if (pwdCheck.hasPassword) {
    const pwd = prompt('此策略页面已设置密码保护，请输入策略密码：');
    if (!pwd) {
      navigate('dashboard');
      alert('已取消，返回仪表盘');
      return;
    }
    const verify = await api('/strategy-password/verify', {
      method: 'POST',
      body: JSON.stringify({password: pwd})
    });
    if (!verify.ok) {
      alert('策略密码错误！');
      navigate('dashboard');
      return;
    }
  }
  // 加载策略状态
  const d=await api('/status');
  const cfg=await api('/config');
  setText('stratStatus',d.running?'运行中':'已停止');
  const sel=document.getElementById('stratStatus');
  if(sel){sel.className='badge '+(d.running?'on':'off');}
  setText('stratBtn',d.running?'停止策略':'启动策略');
  const btn=document.getElementById('stratBtn');
  if(btn)btn.onclick=()=>toggleBot(!d.running);
  const actDelay=parseInt(cfg.ACTIVATION_DELAY_MINUTES||'0');
  setText('stratActDelay',actDelay>0?actDelay+' 分钟':'立即启动');
  const stype=cfg.STRATEGY_TYPE||'TEMA';
  if(stype==='VOLTY'){
    setText('stratName','Volty Expan Close Strategy');
    setText('stratTypeLabel','波动性突破（ATR通道）');
    setText('stratLogic','ATR通道突破，Bar收盘确认，每根K线更新入场价');
  }else{
    setText('stratName','OCC Strategy v8.13');
    setText('stratTypeLabel','趋势跟随（TEMA 跨周期）');
    setText('stratLogic','跨周期均线交叉 + 延迟确认');
  }
  const fields={stratSymbols:cfg.SYMBOLS,stratTF:cfg.TIMEFRAME_MINUTES+'分钟',stratMA:cfg.MA_TYPE+'('+cfg.MA_LEN+')',stratCross:cfg.CROSS_MULT+'x',stratDelay:cfg.DELAY_MINUTES+'分钟',stratPct:cfg.POSITION_PCT+'%',stratLev:cfg.LEVERAGE+'x',stratSL:cfg.STOP_LOSS_PCT+'%',stratType:cfg.TRADE_TYPE,stratVoltyLen:cfg.VOLTY_LENGTH||'5',stratVoltyMult:(cfg.VOLTY_ATR_MULT||'0.75')+'x'};
  for(const[id,val]of Object.entries(fields))setText(id,val);
  const showTema=stype!=='VOLTY';
  const showVolty=stype==='VOLTY';
  document.getElementById('stratRowMA').style.display=showTema?'':'none';
  document.getElementById('stratRowCross').style.display=showTema?'':'none';
  document.getElementById('stratRowVoltyLen').style.display=showVolty?'':'none';
  document.getElementById('stratRowVoltyMult').style.display=showVolty?'':'none';
  // 加载策略配置列表
  loadStrategyList();
}
function toggleCfgStrategyFields(){
  const stype=document.getElementById('cfgStrategyType')?.value||'TEMA';
  const tema=document.getElementById('cfgTemaGroup');
  const volty=document.getElementById('cfgVoltyGroup');
  const delayRow=document.getElementById('cfgDelayMin')?.closest('.form-row');
  if(tema)tema.style.display=stype==='VOLTY'?'none':'';
  if(volty)volty.style.display=stype==='VOLTY'?'':'none';
}
function resetStrategyForm(){
  document.getElementById('cfgEditId').value='';
  document.getElementById('cfgSymbol').value='ETHUSDT';
  document.getElementById('cfgStrategyType').value='TEMA';
  document.getElementById('cfgTimeframe').value='1';
  document.getElementById('cfgMAType').value='TEMA';
  document.getElementById('cfgMALen').value='8';
  document.getElementById('cfgCrossMult').value='3';
  document.getElementById('cfgDelayMin').value='5';
  document.getElementById('cfgVoltyLength').value='5';
  document.getElementById('cfgVoltyAtrMult').value='0.75';
  document.getElementById('cfgPositionPct').value='20';
  document.getElementById('cfgLeverage').value='3';
  document.getElementById('cfgLevLabel').textContent='3x';
  document.getElementById('cfgStopLoss').value='5';
  document.getElementById('cfgTradeType').value='BOTH';
  document.getElementById('cfgActDelay').value='0';
  document.getElementById('cfgMaxOrder').value='0';
  document.getElementById('cfgMinOrder').value='11';
  toggleCfgStrategyFields();
}
async function loadStrategyList(){
  const d=await api('/strategies');
  strategyList=d.strategies||[];
  const def=d.defaults||{};
  document.getElementById('cfgTimeframe').value=def.TIMEFRAME_MINUTES||'1';
  document.getElementById('cfgStrategyType').value=def.STRATEGY_TYPE||'TEMA';
  document.getElementById('cfgMAType').value=def.MA_TYPE||'TEMA';
  document.getElementById('cfgMALen').value=def.MA_LEN||'8';
  document.getElementById('cfgCrossMult').value=def.CROSS_MULT||'3';
  document.getElementById('cfgDelayMin').value=def.DELAY_MINUTES||'5';
  document.getElementById('cfgVoltyLength').value=def.VOLTY_LENGTH||'5';
  document.getElementById('cfgVoltyAtrMult').value=def.VOLTY_ATR_MULT||'0.75';
  document.getElementById('cfgPositionPct').value=def.POSITION_PCT||'20';
  document.getElementById('cfgLeverage').value=def.LEVERAGE||'3';
  document.getElementById('cfgLevLabel').textContent=(def.LEVERAGE||'3')+'x';
  document.getElementById('cfgStopLoss').value=def.STOP_LOSS_PCT||'5';
  document.getElementById('cfgMarginMode').value=def.MARGIN_MODE||'isolated';
  document.getElementById('cfgTradeType').value=def.TRADE_TYPE||'BOTH';
  document.getElementById('cfgActDelay').value=def.ACTIVATION_DELAY_MINUTES||'0';
  toggleCfgStrategyFields();
  renderStrategyTable();
}
function renderStrategyTable(){
  const tb=document.getElementById('strategyTableBody');
  if(!tb)return;
  if(strategyList.length===0){
    tb.innerHTML='<tr><td colspan="10" style="text-align:center;color:var(--text)">暂无策略配置，请添加</td></tr>';
  }else{
    tb.innerHTML=strategyList.map((s,i)=>{
      const stype=s.strategyType||'TEMA';
      const stypeLabel=stype==='VOLTY'?'Volty':'TEMA';
      const params=stype==='VOLTY'?`ATR${s.voltyLength||5}x${s.voltyAtrMult||0.75}`:`${s.maType||'TEMA'}(${s.maLen||8}) ${s.crossMult||3}x`;
      return `<tr>
      <td style="color:var(--title);font-weight:bold">${s.symbol||''}</td>
      <td><span class="tag ${stype==='VOLTY'?'tag-warn':'tag-info'}">${stypeLabel}</span></td>
      <td>${s.timeframe||1}分钟</td>
      <td>${params}</td>
      <td>${s.positionPct||20}%</td>
      <td>${s.leverage||3}x</td>
      <td>${s.marginMode==='cross'?'全仓':'逐仓'}</td>
      <td>${s.maxOrder>0?s.maxOrder+'U':'默认'}</td>
      <td>${s.stopLoss||5}%</td>
      <td>
        <button class="btn btn-outline btn-sm" onclick="editStrategy(${i})">编辑</button>
        <button class="btn btn-red btn-sm" style="margin-left:4px" onclick="deleteStrategy(${i})">✕</button>
      </td>
    </tr>`}).join('');
  }
}
function editStrategy(idx){
  const s=strategyList[idx];
  if(!s)return;
  document.getElementById('cfgEditId').value=idx;
  document.getElementById('cfgSymbol').value=s.symbol||'ETHUSDT';
  document.getElementById('cfgStrategyType').value=s.strategyType||'TEMA';
  document.getElementById('cfgTimeframe').value=s.timeframe||'1';
  document.getElementById('cfgMAType').value=s.maType||'TEMA';
  document.getElementById('cfgMALen').value=s.maLen||'8';
  document.getElementById('cfgCrossMult').value=s.crossMult||'3';
  document.getElementById('cfgDelayMin').value=s.delayMin||'5';
  document.getElementById('cfgVoltyLength').value=s.voltyLength||'5';
  document.getElementById('cfgVoltyAtrMult').value=s.voltyAtrMult||'0.75';
  document.getElementById('cfgPositionPct').value=s.positionPct||'20';
  document.getElementById('cfgLeverage').value=s.leverage||'3';
  document.getElementById('cfgLevLabel').textContent=(s.leverage||'3')+'x';
  document.getElementById('cfgMarginMode').value=s.marginMode||'isolated';
  document.getElementById('cfgStopLoss').value=s.stopLoss||'5';
  document.getElementById('cfgTradeType').value=s.tradeType||'BOTH';
  document.getElementById('cfgActDelay').value=s.actDelay||'0';
  document.getElementById('cfgMaxOrder').value=s.maxOrder||'0';
  document.getElementById('cfgMinOrder').value=s.minOrder||'11';
  toggleCfgStrategyFields();
}
async function saveStrategyConfig(){
  const idx=document.getElementById('cfgEditId').value;
  const cfg={
    symbol:document.getElementById('cfgSymbol').value,
    strategyType:document.getElementById('cfgStrategyType').value,
    timeframe:parseInt(document.getElementById('cfgTimeframe').value)||1,
    maType:document.getElementById('cfgMAType').value,
    maLen:parseInt(document.getElementById('cfgMALen').value)||8,
    crossMult:parseInt(document.getElementById('cfgCrossMult').value)||3,
    delayMin:parseInt(document.getElementById('cfgDelayMin').value)||5,
    voltyLength:parseInt(document.getElementById('cfgVoltyLength').value)||5,
    voltyAtrMult:parseFloat(document.getElementById('cfgVoltyAtrMult').value)||0.75,
    positionPct:parseInt(document.getElementById('cfgPositionPct').value)||20,
    leverage:parseInt(document.getElementById('cfgLeverage').value)||3,
    marginMode:document.getElementById('cfgMarginMode').value,
    stopLoss:parseFloat(document.getElementById('cfgStopLoss').value)||5,
    tradeType:document.getElementById('cfgTradeType').value,
    actDelay:parseInt(document.getElementById('cfgActDelay').value)||0,
    maxOrder:parseFloat(document.getElementById('cfgMaxOrder').value)||0,
    minOrder:parseFloat(document.getElementById('cfgMinOrder').value)||11,
  };
  if(idx!==''){
    strategyList[parseInt(idx)]=cfg;
  }else{
    // 检查重复
    const dupIdx=strategyList.findIndex(s=>s.symbol===cfg.symbol);
    if(dupIdx>=0)strategyList[dupIdx]=cfg;
    else strategyList.push(cfg);
  }
  await api('/strategies/save',{method:'POST',body:JSON.stringify({strategies:strategyList})});
  resetStrategyForm();
  renderStrategyTable();
  try{await api('/bot/stop',{method:'POST',body:'{}'});}catch(e){}
  setTimeout(async()=>{try{await api('/bot/start',{method:'POST',body:'{}'});}catch(e){}},2000);
  setTimeout(initStrategyOp,4000);
  alert('策略配置已保存，正在重启机器人...');
}
async function deleteStrategy(idx){
  if(!confirm('删除策略 '+strategyList[idx]?.symbol+' 的配置？'))return;
  strategyList.splice(idx,1);
  await api('/strategies/save',{method:'POST',body:JSON.stringify({strategies:strategyList})});
  renderStrategyTable();
  try{await api('/bot/stop',{method:'POST',body:'{}'});}catch(e){}
  setTimeout(async()=>{try{await api('/bot/start',{method:'POST',body:'{}'});}catch(e){}},2000);
}

// ========== 数据监控 ==========
async function initMonitor(){await refreshMonitor();document.getElementById('monSymbol')?.addEventListener('change',refreshMonitor);}
async function refreshMonitor(){
  const symbol=document.getElementById('monSymbol')?.value||'ETHUSDT';
  const d=await api('/klines/'+symbol+'?limit=200');
  const ctx=document.getElementById('klineChart')?.getContext('2d');
  if(!ctx||!d.klines)return;
  if(chartInstances.kline)chartInstances.kline.destroy();
  const labels=d.klines.map(k=>new Date(k[0]).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'}));
  const closes=d.klines.map(k=>k[4]);
  chartInstances.kline=new Chart(ctx,{
    type:'line',
    data:{
      labels,
      datasets:[
        {label:'价格',data:closes,borderColor:'#3b82f6',pointRadius:0,tension:.1,borderWidth:1.5},
        {label:'MA5',data:d.ma5,borderColor:'#f59e0b',pointRadius:0,borderWidth:1,spanGaps:true},
        {label:'MA10',data:d.ma10,borderColor:'#ef4444',pointRadius:0,borderWidth:1,spanGaps:true},
        {label:'MA20',data:d.ma20,borderColor:'#8b5cf6',pointRadius:0,borderWidth:1,spanGaps:true},
      ]
    },
    options:{
      responsive:true,maintainAspectRatio:false,
      plugins:{legend:{labels:{color:'#9599a3',font:{size:10}}}},
      scales:{x:{ticks:{color:'#555',font:{size:9}},grid:{color:'#1c2333'}},y:{ticks:{color:'#555',font:{size:9}},grid:{color:'#1c2333'}}}
    }
  });
  const last=closes[closes.length-1]||0;
  setText('monPrice',last.toFixed(2));
  setText('monVolume',(d.klines[d.klines.length-1]?.[5]||0).toFixed(1));

  // 行情表
  const mkts=await api('/market/overview');
  const tb=document.getElementById('marketTable');
  if(tb){
    tb.innerHTML=mkts.slice(0,30).map((m,i)=>`<tr><td>${i+1}</td><td>${(m.symbol||'').replace(':USDT','')}</td><td>${(m.price||0).toFixed(4)}</td><td class="${(m.change||0)>=0?'green':'red'}">${(m.change||0)>=0?'+':''}${(m.change||0).toFixed(2)}%</td></tr>`).join('');
  }
}

// ========== 资产分析 ==========
async function initAnalysis(){
  const d=await api('/analysis');
  setText('anaWinRate',(d.winRate||0).toFixed(1)+'%');
  setText('anaPnLRatio',d.pnlRatio||'--');
  setText('anaTotalTrades',d.totalTrades||0);
  setText('anaBestTrade',(d.bestTrade||0).toFixed(2)+'U');
  const ctx1=document.getElementById('winRateChart')?.getContext('2d');
  if(ctx1){
    chartInstances.winRate=new Chart(ctx1,{type:'doughnut',data:{labels:['盈利','亏损'],datasets:[{data:[d.winRate||0,100-(d.winRate||0)],backgroundColor:['#22c55e','#ef4444']}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{labels:{color:'#9599a3',font:{size:10}}}}}});
  }
  const ctx2=document.getElementById('pairChart')?.getContext('2d');
  if(ctx2&&d.pairStats){
    chartInstances.pair=new Chart(ctx2,{type:'bar',data:{labels:d.pairStats.map(p=>p.name),datasets:[{data:d.pairStats.map(p=>p.count),backgroundColor:'#3b82f6',borderRadius:4}]},options:{responsive:true,maintainAspectRatio:false,indexAxis:'y',plugins:{legend:{display:false}},scales:{x:{ticks:{color:'#555'},grid:{color:'#1c2333'}},y:{ticks:{color:'#555'},grid:{display:false}}}}});
  }
}

// ========== 日志 ==========
async function initLogs(){await loadLogLines();document.getElementById('logLevel')?.addEventListener('change',loadLogLines);}
async function loadLogLines(){
  const level=document.getElementById('logLevel')?.value||'ALL';
  const d=await api('/logs?level='+level);
  const div=document.getElementById('logContent');
  if(div){
    div.innerHTML=(d.lines||[]).map(l=>{
      let cls='';if(l.includes('ERROR'))cls='tag-error';else if(l.includes('WARNING'))cls='tag-warn';else if(l.includes('INFO'))cls='tag-info';
      return `<div class="log-line"><span class="tag ${cls}">${cls.replace('tag-','')}</span> ${l}</div>`;
    }).join('');
  }
}

// ========== 设置 ==========
async function initSettings(){
  const apis=await api('/apikeys');
  const ak=document.getElementById('set_api_key');if(ak)ak.value=apis.apiKey||'';
  const sk=document.getElementById('set_secret_key');if(sk)sk.value=apis.secretKey||'';
  const px=document.getElementById('set_proxy_url');if(px)px.value=apis.proxyUrl||'';
}
let pwdCallback=null;
function showPwdModal(callback){
  pwdCallback=callback;
  document.getElementById('pwdModal').classList.add('show');
  document.getElementById('pwdInput').value='';
  document.getElementById('pwdError').style.display='none';
  setTimeout(()=>document.getElementById('pwdInput').focus(),100);
}
function cancelPwd(){
  document.getElementById('pwdModal').classList.remove('show');
  pwdCallback=null;
}
async function confirmPwd(){
  const pwd=document.getElementById('pwdInput').value;
  const r=await api('/verify-password',{method:'POST',body:JSON.stringify({password:pwd})});
  if(r.ok){
    document.getElementById('pwdModal').classList.remove('show');
    if(pwdCallback){pwdCallback();pwdCallback=null;}
  }else{
    document.getElementById('pwdError').style.display='block';
  }
}
async function saveApiKeys(){
  const ak=document.getElementById('set_api_key')?.value||'';
  const sk=document.getElementById('set_secret_key')?.value||'';
  const px=document.getElementById('set_proxy_url')?.value||'';
  const r=await api('/apikeys/save',{method:'POST',body:JSON.stringify({apiKey:ak,secretKey:sk,proxyUrl:px})});
  if(r.ok){alert('API Key 已保存，2秒后自动刷新');setTimeout(()=>location.reload(),2000);}else{alert('保存失败');}
}

(async function(){
  const a=await api('/avatar');
  if(a.data){document.getElementById('avatarImg').src=a.data;}
})();
loadPage('dashboard');
// ── Profile ──
async function loadProfileUI(){
  const p=await api('/profile');
  document.getElementById('usernameDisplay').textContent=p.nickname||'用户';
  const a=await api('/avatar');
  const img=document.getElementById('avatarTop');
  if(a.data){img.src=a.data;img.style.display='';}else{img.style.display='none';}
}
function toggleUserMenu(){
  const d=document.getElementById('userDropdown');
  d.style.display=d.style.display==='block'?'none':'block';
}
document.addEventListener('click',e=>{if(!e.target.closest('.user-area'))document.getElementById('userDropdown').style.display='none';});
function showProfileModal(){document.getElementById('profileModal').classList.add('show');api('/profile').then(p=>document.getElementById('profileNickname').value=p.nickname||'');}
function closeProfileModal(){document.getElementById('profileModal').classList.remove('show');}
async function uploadAvatar(){
  const file=document.getElementById('profileAvatar').files[0];
  if(!file)return alert('请选择图片');
  const reader=new FileReader();
  reader.onload=async e=>{const r=await api('/avatar/upload',{method:'POST',body:JSON.stringify({image:e.target.result})});alert(r.ok?'头像已上传':'上传失败');if(r.ok)loadProfileUI();};
  reader.readAsDataURL(file);
}
async function saveProfile(){
  const nick=document.getElementById('profileNickname').value.trim();
  await api('/profile/save',{method:'POST',body:JSON.stringify({nickname:nick})});
  closeProfileModal();loadProfileUI();
}
function showPwdModal2(){document.getElementById('pwdModal2').classList.add('show');}
function closePwdModal2(){document.getElementById('pwdModal2').classList.remove('show');}
async function changePassword(){
  const op=document.getElementById('oldPwd').value,np=document.getElementById('newPwd').value,er=document.getElementById('pwdError2');
  if(np.length<4){er.textContent='新密码至少4位';er.style.display='block';return;}
  const r=await api('/auth/reset-password',{method:'POST',body:JSON.stringify({oldPassword:op,newPassword:np})});
  if(r.ok){alert('密码已修改');closePwdModal2();}else{er.textContent=r.error||'修改失败';er.style.display='block';}
}
loadProfileUI();
</script>

<div class="modal-overlay" id="pwdModal">
  <div class="modal">
    <h3>请输入管理密码</h3>
    <input type="password" id="pwdInput" placeholder="输入密码后保存" onkeydown="if(event.key==='Enter')confirmPwd()">
    <div class="error-msg" id="pwdError">密码错误</div>
    <div class="btn-row">
      <button class="btn btn-outline btn-sm" onclick="cancelPwd()">取消</button>
      <button class="btn btn-primary btn-sm" onclick="confirmPwd()">确认</button>
    </div>
  </div>
</div>

<!-- Profile Edit Modal -->
<div class="modal-overlay" id="profileModal">
  <div class="modal">
    <h3>编辑个人资料</h3>
    <label>昵称</label>
    <input type="text" id="profileNickname" maxlength="20" placeholder="输入昵称">
    <label>头像</label>
    <input type="file" id="profileAvatar" accept="image/jpeg,image/png" style="padding:4px;">
    <button class="btn btn-outline btn-sm" onclick="uploadAvatar()">上传头像</button>
    <div class="btn-row">
      <button class="btn btn-outline" onclick="closeProfileModal()">取消</button>
      <button class="btn btn-primary" onclick="saveProfile()">保存</button>
    </div>
  </div>
</div>

<!-- Password Change Modal -->
<div class="modal-overlay" id="pwdModal2">
  <div class="modal">
    <h3>修改密码</h3>
    <label>旧密码</label>
    <input type="password" id="oldPwd" placeholder="当前密码">
    <label>新密码</label>
    <input type="password" id="newPwd" placeholder="新密码（至少4位）">
    <div class="error-msg" id="pwdError2"></div>
    <div class="btn-row">
      <button class="btn btn-outline" onclick="closePwdModal2()">取消</button>
      <button class="btn btn-primary" onclick="changePassword()">确认</button>
    </div>
  </div>
</div>

</body>
</html>"""

# ============================================================
#   页面片段
# ============================================================

PAGE_DASHBOARD = """
<div class="card" style="margin-bottom:12px">
  <h3>自选币种</h3>
  <div class="inline-input" style="margin-bottom:8px">
    <input id="watchInput" placeholder="添加币种（如 SOLUSDT）" style="width:200px">
    <button class="btn btn-primary btn-sm" onclick="addWatch()">添加</button>
  </div>
  <div class="watch-grid" id="watchGrid">加载中...</div>
</div>
<div class="stat-cards">
  <div class="stat-card"><div class="label">合约账户余额</div><div class="value" id="dashBalance">--</div></div>
  <div class="stat-card"><div class="label">持仓盈亏</div><div class="value" id="dashPnl">--</div></div>
  <div class="stat-card"><div class="label">今日盈亏</div><div class="value" id="dashTodayPnl">--</div></div>
  <div class="stat-card"><div class="label">当前持仓</div><div class="value" id="dashPosCount">--</div></div>
  <div class="stat-card"><div class="label">累计已实现盈亏</div><div class="value" id="dashTotalRealPnl">--</div></div>
  <div class="stat-card"><div class="label">下次资金费结算</div><div class="value" id="dashFundingCD" style="font-size:16px">--</div></div>
  <div class="stat-card"><div class="label">今日交易</div><div class="value" id="dashTradeCount">--</div></div>
  <div class="stat-card"><div class="label">Taker手续费率</div><div class="value" id="dashFeeRateVal" style="font-size:15px">--</div></div>
</div>

<div class="card">
  <div class="tab-nav">
    <button class="active" onclick="switchPosTab('current',this)">当前持仓</button>
    <button onclick="switchPosTab('history',this)">历史仓位</button>
  </div>
  <div class="tab-content active" id="tabCurrent">
    <div style="max-height:300px;overflow-y:auto" class="table-wrap">
    <table><thead><tr><th>币种</th><th>方向</th><th>入场价</th><th>标记价</th><th>强平价</th><th>杠杆</th><th>模式</th><th>张数</th><th>资金费率</th><th>手续费(开+平)</th><th>未实现盈亏</th></tr></thead>
    <tbody id="currentPositions"><tr><td colspan="11" style="text-align:center;color:var(--text)">加载中...</td></tr></tbody></table>
    </div>
  </div>
  <div class="tab-content" id="tabHistory">
    <div style="max-height:300px;overflow-y:auto" class="table-wrap">
    <table><thead><tr><th>时间</th><th>币种</th><th>操作</th><th>价格</th><th>张数</th><th>金额</th><th>已实现盈亏</th></tr></thead>
    <tbody id="historyPositions"><tr><td colspan="7" style="text-align:center;color:var(--text)">加载中...</td></tr></tbody></table>
    </div>
    <div style="margin-top:8px;font-size:12px;display:flex;gap:16px">
      <span>总计已平仓：<b id="histTotal" style="color:var(--title)">--</b> 笔</span>
      <span>累计收益：<b id="histTotalPnl" style="color:var(--title)">--</b> USDT</span>
      <span>胜率：<b id="histWinRate" style="color:var(--title)">--</b></span>
    </div>
  </div>
</div>

<div class="grid2">
  <div class="card"><h3>收益走势 <span style="font-size:12px;font-weight:400;margin-left:8px">|</span> <button class="period-btn active" onclick="loadEquity('1D',this)">1天</button><button class="period-btn" onclick="loadEquity('1W',this)">1周</button><button class="period-btn" onclick="loadEquity('1M',this)">1月</button><button class="period-btn" onclick="loadEquity('3M',this)">3月</button><button class="period-btn" onclick="loadEquity('1Y',this)">1年</button></h3><div class="chart-wrap" style="height:320px"><canvas id="profitChart"></canvas></div></div>
  <div class="card"><h3>最新交易</h3><div style="max-height:280px;overflow-y:auto">
    <table><thead><tr><th>时间</th><th>币种</th><th>操作</th><th>张数</th><th>价格</th><th>金额</th></tr></thead><tbody id="recentTrades"></tbody></table>
  </div></div>
</div>
"""

PAGE_STRATEGY_OP = """
<div class="grid2">
  <div class="card">
    <h3>策略状态</h3>
    <p>当前状态：<span id="stratStatus" class="badge off">--</span></p>
    <p style="margin-top:8px">策略名称：<span id="stratName">--</span></p>
    <p>策略类型：<span id="stratTypeLabel">--</span></p>
    <p>信号逻辑：<span id="stratLogic">--</span></p>
    <p>激活延迟：<span id="stratActDelay" style="color:var(--orange)">--</span></p>
    <button class="btn btn-primary" id="stratBtn" style="margin-top:12px" onclick="toggleBot(true)">--</button>
    <button class="btn btn-outline btn-sm" style="margin-left:8px" onclick="initStrategyOp()">刷新状态</button>
  </div>
  <div class="card">
    <h3>策略参数概览</h3>
    <table id="stratCfgTable">
      <tr><td>已配置币种</td><td id="stratSymbols">--</td></tr>
      <tr><td>K线周期</td><td id="stratTF">--</td></tr>
      <tr id="stratRowMA"><td>均线类型</td><td id="stratMA">--</td></tr>
      <tr id="stratRowCross"><td>跨周期倍数</td><td id="stratCross">--</td></tr>
      <tr id="stratRowVoltyLen" style="display:none"><td>ATR计算周期</td><td id="stratVoltyLen">--</td></tr>
      <tr id="stratRowVoltyMult" style="display:none"><td>ATR倍数</td><td id="stratVoltyMult">--</td></tr>
      <tr><td>信号延迟</td><td id="stratDelay">--</td></tr>
      <tr><td>仓位占比</td><td id="stratPct">--</td></tr>
      <tr><td>杠杆倍数</td><td id="stratLev">--</td></tr>
      <tr><td>止损比例</td><td id="stratSL">--</td></tr>
      <tr><td>交易方向</td><td id="stratType">--</td></tr>
    </table>
  </div>
</div>
<div class="grid2" style="margin-top:16px">
  <div class="card">
    <h3>添加/编辑策略配置</h3>
    <p style="font-size:11px;color:var(--text);margin-bottom:12px">为每个币种独立配置策略参数，保存后自动重启机器人应用新配置</p>
    <input type="hidden" id="cfgEditId" value="">
    <label>交易对</label>
    <select id="cfgSymbol">
      <option>ETHUSDT</option><option>BTCUSDT</option><option>BNBUSDT</option><option>SOLUSDT</option><option>ZECUSDT</option><option>DOGEUSDT</option><option>ADAUSDT</option><option>XRPUSDT</option><option>LTCUSDT</option><option>LINKUSDT</option><option>AVAXUSDT</option><option>DOTUSDT</option><option>MATICUSDT</option><option>ARBUSDT</option><option>OPUSDT</option>
    </select>
    <div class="form-row">
      <div><label>策略类型</label><select id="cfgStrategyType" onchange="toggleCfgStrategyFields()"><option value="TEMA">TEMA 趋势跟随</option><option value="VOLTY">Volty 波动突破</option></select></div>
      <div><label>K线周期(分钟)</label><select id="cfgTimeframe"><option value="1">1分钟</option><option value="3">3分钟</option><option value="5">5分钟</option><option value="15">15分钟</option><option value="30">30分钟</option><option value="60">1小时</option></select></div>
    </div>
    <div id="cfgTemaGroup">
    <div class="form-row">
      <div><label>均线类型</label><select id="cfgMAType"><option>TEMA</option><option>EMA</option><option>SMA</option><option>SMMA</option></select></div>
      <div><label>均线周期</label><input type="number" id="cfgMALen" value="8" min="1"></div>
    </div>
    <div class="form-row">
      <div><label>跨周期倍数</label><input type="number" id="cfgCrossMult" value="3" min="1"></div>
      <div><label>信号延迟(分钟)</label><input type="number" id="cfgDelayMin" value="5" min="0"></div>
    </div>
    </div>
    <div id="cfgVoltyGroup" style="display:none">
    <div class="form-row">
      <div><label>ATR计算周期</label><input type="number" id="cfgVoltyLength" value="5" min="1"></div>
      <div><label>ATR倍数</label><input type="number" id="cfgVoltyAtrMult" value="0.75" step="0.05" min="0.1"></div>
    </div>
    </div>
    <div class="form-row">
      <div><label>仓位占比(%)</label><input type="number" id="cfgPositionPct" value="20" min="1" max="100"></div>
      <div><label>杠杆倍数</label><input type="range" id="cfgLeverage" min="1" max="125" value="3" oninput="document.getElementById('cfgLevLabel').textContent=this.value+'x'"><span id="cfgLevLabel" style="font-size:14px;color:var(--title)">3x</span></div>
    </div>
    <div class="form-row">
      <div><label>止损比例(%)</label><input type="number" id="cfgStopLoss" value="5" min="0" step="0.5"></div>
      <div><label>交易方向</label><select id="cfgTradeType"><option>BOTH</option><option>LONG</option><option>SHORT</option></select></div>
    </div>
    <div class="form-row">
      <div><label>保证金模式</label><select id="cfgMarginMode"><option value="isolated">逐仓</option><option value="cross">全仓</option></select></div>
      <div></div>
    </div>
    <div class="form-row">
      <div><label>最大保证金上限(U)</label><input type="number" id="cfgMaxOrder" value="0" min="0"><span style="font-size:10px;color:var(--text)">限制本金投入，0=不限</span></div>
      <div><label>最小仓位下限(U)</label><input type="number" id="cfgMinOrder" value="11" min="11"></div>
    </div>
    <div class="form-row">
      <div><label>启动延迟(分钟)</label><input type="number" id="cfgActDelay" value="0" min="0"></div>
      <div></div>
    </div>
    <div style="display:flex;gap:8px;margin-top:16px">
      <button class="btn btn-primary" style="flex:1" onclick="saveStrategyConfig()">保存并应用</button>
      <button class="btn btn-outline btn-sm" onclick="resetStrategyForm()">清空</button>
    </div>
  </div>
  <div class="card">
    <h3>已配置策略</h3>
    <div style="max-height:450px;overflow-y:auto">
    <table><thead><tr><th>交易对</th><th>类型</th><th>周期</th><th>参数</th><th>仓位</th><th>杠杆</th><th>模式</th><th>最大</th><th>止损</th><th>操作</th></tr></thead>
    <tbody id="strategyTableBody"><tr><td colspan="10" style="text-align:center;color:var(--text)">加载中...</td></tr></tbody></table>
    </div>
    <p style="font-size:11px;color:var(--orange);margin-top:8px">提示：已配置策略的币种自动加入交易列表，不需在其他地方重复设置。</p>
  </div>
</div>
	<div class="card" style="margin-top:16px;">
	  <h3>🔒 策略密码保护</h3>
	  <p style="font-size:12px;color:var(--text);margin-bottom:12px;">设置策略页面访问密码，防止他人修改你的策略配置</p>
	  <div class="inline-input">
	    <input type="password" id="strategyPwdInput" placeholder="输入新密码（至少4位，留空则清除密码）" style="flex:1;">
	    <button class="btn btn-primary" onclick="setStrategyPassword()" style="white-space:nowrap;">设置密码</button>
	    <button class="btn btn-outline btn-sm" onclick="clearStrategyPassword()" style="white-space:nowrap;">清除密码</button>
	  </div>
	  <p id="strategyPwdStatus" style="font-size:11px;margin-top:8px;"></p>
	</div>
	<script>
	(async function checkStrategyPwd(){
	  const r = await api('/strategy-password/status');
	  const el = document.getElementById('strategyPwdStatus');
	  if (r.hasPassword) {
	    el.innerHTML = '<span style="color:var(--green);">✅ 已设置策略密码保护</span>';
	  } else {
	    el.innerHTML = '<span style="color:var(--text);">⚠️ 未设置策略密码（任何人都可修改策略）</span>';
	  }
	})();
	async function setStrategyPassword(){
	  const pwd = document.getElementById('strategyPwdInput').value;
	  if (!pwd) { alert('请输入密码'); return; }
	  if (pwd.length < 4) { alert('密码至少4位'); return; }
	  const r = await api('/strategy-password/set', {
	    method: 'POST',
	    body: JSON.stringify({password: pwd})
	  });
	  if (r.ok) {
	    alert('策略密码设置成功');
	    document.getElementById('strategyPwdStatus').innerHTML = '<span style="color:var(--green);">✅ 已设置策略密码保护</span>';
	    document.getElementById('strategyPwdInput').value = '';
	  } else {
	    alert(r.error || '设置失败');
	  }
	}
	async function clearStrategyPassword(){
	  if (!confirm('确定要清除策略密码？清除后策略页面将不受保护。')) return;
	  const r = await api('/strategy-password/set', {
	    method: 'POST',
	    body: JSON.stringify({password: ''})
	  });
	  if (r.ok) {
	    alert('策略密码已清除');
	    document.getElementById('strategyPwdStatus').innerHTML = '<span style="color:var(--text);">⚠️ 未设置策略密码（任何人都可修改策略）</span>';
	  }
	}
	</script>
"""
PAGE_MONITOR = """
<div class="stat-cards">
  <div class="stat-card"><div class="label">最新价</div><div class="value" id="monPrice">--</div></div>
  <div class="stat-card"><div class="label">24H成交量</div><div class="value" id="monVolume">--</div></div>
</div>
<div class="grid2">
  <div class="card"><h3>K线图 <select id="monSymbol" style="width:auto;display:inline;margin-left:8px"><option>ETHUSDT</option><option>BTCUSDT</option><option>BNBUSDT</option><option>SOLUSDT</option><option>DOGEUSDT</option><option>ADAUSDT</option><option>XRPUSDT</option></select> <select id="monTF" style="width:auto;display:inline;margin-left:4px" onchange="refreshMonitor()"><option value="1m">1分钟</option><option value="5m">5分钟</option><option value="15m">15分钟</option><option value="1h">1小时</option></select></h3><div class="chart-wrap"><canvas id="klineChart"></canvas></div></div>
  <div class="card"><h3>币种行情</h3><div style="max-height:400px;overflow-y:auto">
    <table><thead><tr><th>#</th><th>币种</th><th>最新价</th><th>24H涨跌</th></tr></thead><tbody id="marketTable"></tbody></table>
  </div></div>
</div>
"""

PAGE_ANALYSIS = """
<div class="stat-cards">
  <div class="stat-card"><div class="label">历史胜率</div><div class="value" id="anaWinRate">--</div></div>
  <div class="stat-card"><div class="label">盈亏比</div><div class="value" id="anaPnLRatio">--</div></div>
  <div class="stat-card"><div class="label">总交易笔数</div><div class="value" id="anaTotalTrades">--</div></div>
  <div class="stat-card"><div class="label">最佳单笔</div><div class="value green" id="anaBestTrade">--</div></div>
</div>
<div class="grid2">
  <div class="card"><h3>胜率分布</h3><div class="chart-wrap"><canvas id="winRateChart"></canvas></div></div>
  <div class="card"><h3>交易对偏好</h3><div class="chart-wrap"><canvas id="pairChart"></canvas></div></div>
</div>
"""

PAGE_LOGS = """
<div class="card">
  <div style="display:flex;gap:8px;align-items:center;margin-bottom:8px">
    <span style="font-size:12px">筛选：</span>
    <select id="logLevel" style="width:120px" onchange="loadLogLines()">
      <option value="ALL">全部</option><option value="INFO">INFO</option><option value="WARNING">WARNING</option><option value="ERROR">ERROR</option>
    </select>
    <button class="btn btn-outline btn-sm" onclick="loadLogLines()" style="margin-left:auto">刷新</button>
  </div>
  <div id="logContent" style="max-height:500px;overflow-y:auto">加载中...</div>
</div>
"""

PAGE_SETTINGS = """
<div class="card" style="max-width:500px">
  <h3>API 配置</h3>
  <label>API Key</label><input type="password" id="set_api_key" placeholder="币安 API Key">
  <label>Secret Key</label><input type="password" id="set_secret_key" placeholder="币安 Secret Key">
  <label>代理 URL（VPN/Clash 端口）</label><input type="text" id="set_proxy_url" placeholder="http://127.0.0.1:7897">
  <button class="btn btn-primary" style="margin-top:12px" onclick="saveApiKeys()">保存 API 密钥</button>
  <p style="font-size:11px;color:var(--text);margin-top:8px">密钥和代理保存到您的专属配置中，保存后刷新页面生效</p>
  <p style="font-size:12px;color:var(--orange);margin-top:16px">交易策略配置请前往「策略配置」页面</p>
</div>
"""

PAGE_MAP = {k: v for k, v in {
    "dashboard": PAGE_DASHBOARD, "strategy_op": PAGE_STRATEGY_OP,
    "monitor": PAGE_MONITOR, "analysis": PAGE_ANALYSIS, "logs": PAGE_LOGS, "settings": PAGE_SETTINGS,
}.items()}


# ============================================================
#   API 路由
# ============================================================

def handle_api(path, body=None, qs=""):
    try:
        return _handle_api(path, body, qs)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"error": str(e)}


def _parse_json(body):
    """安全解析 JSON，失败时返回空字典."""
    if not body:
        return {}
    try:
        return json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return {}


def _extract_token(qs: str = "", body: str = None) -> str:
    """从 query string 或 POST body 中提取 token."""
    if qs:
        params = parse_qs(qs)
        token = params.get("token", [""])[0]
        if token:
            return token
    if body:
        data = _parse_json(body)
        token = data.get("token", "")
        if token:
            return token
    return ""


# 不需要认证的公开路径
_PUBLIC_PATHS = [
    "/api/auth/login", "/api/auth/register", "/api/status",
    "/api/market/overview", "/api/ticker/", "/api/klines/",
    "/api/debug/", "/api/avatar",
]


def _is_public_path(path: str) -> bool:
    """检查路径是否为公开访问路径（无需登录）."""
    for pp in _PUBLIC_PATHS:
        if path.startswith(pp):
            return True
    return False


def _handle_api(path, body=None, qs=""):
    params = parse_qs(qs) if qs else {}

    # ── 认证端点（公开访问）──
    if path == "/api/auth/login" and body:
        data = _parse_json(body)
        result = login_user(data.get("username", ""), data.get("password", ""))
        return result

    if path == "/api/auth/register" and body:
        data = _parse_json(body)
        result = register_user(data.get("username", ""), data.get("password", ""))
        return result

    if path == "/api/auth/me":
        token = _extract_token(qs, body)
        user = get_user_by_token(token) if token else None
        if user:
            return {"ok": True, "user": user}
        return {"ok": False, "error": "未登录", "user": None}

    if path == "/api/auth/logout" and body:
        token = _extract_token(qs, body)
        logout_user(token)
        return {"ok": True}

    if path == "/api/auth/reset-password" and body:
        token = _extract_token(qs, body)
        user = get_user_by_token(token) if token else None
        if not user:
            return {"error": "unauthorized", "code": 401}
        data = _parse_json(body)
        result = reset_password(user["username"], data.get("oldPassword", ""), data.get("newPassword", ""))
        return result

    # ── 认证守卫：非公开路径需要有效 token ──
    if not _is_public_path(path):
        token = _extract_token(qs, body)
        user = get_user_by_token(token) if token else None
        if not user:
            return {"error": "unauthorized", "code": 401}
        username = user["username"]
        pb = _get_private_binance(username)
    else:
        username = None
        pb = None

    # ── GET ──

    if path == "/api/status":
        return {"running": is_bot_running(username) if username else is_bot_running()}

    if path == "/api/config":
        return load_env(username)

    if path == "/api/dashboard":

        # ── Binance 连通性快速检查（缓存60秒）──
        global _binance_ok, _binance_check_time
        if time.time() - _binance_check_time > 60:
            checker = _get_public_binance(username) if username else public_binance
            result = _run_with_timeout(lambda: checker.fetch_ticker('BTC/USDT'), 5)
            _binance_ok = result is not None
            _binance_check_time = time.time()

        trades = load_trades(username)
        local_positions = load_positions(username)
        watchlist = load_watchlist(username)

        # 如果 Binance 不可达，返回本地数据 + 离线标记
        if not _binance_ok:
            # 用上次缓存的余额
            cached_bal = _balance_cache.get("USDT", {}) if _balance_cache else {}
            balance = float(cached_bal.get("total", 0) or 0)
            available = float(cached_bal.get("free", 0) or 0)
            cumulative = 0; profit_data = []; profit_labels = []
            for t in trades[-500:]:
                cumulative += t.get("realizedPnl", 0) or 0
                profit_data.append(round(cumulative, 2))
                profit_labels.append(fmt_time(t.get("time", ""))[5:16])
            # 用缓存ticker数据
            wd = []
            for sym in watchlist:
                t_info = _ticker_cache.get(sym, {})
                wd.append({
                    "symbol": sym,
                    "price": float(t_info.get("last", 0) or 0),
                    "change": float(t_info.get("percentage", 0) or 0),
                })
            return {
                "balance": round(balance, 2),
                "available": round(available, 2),
                "positions": [], "unrealizedPnl": 0, "positionCount": 0,
                "todayPnl": 0, "todayTrades": 0,
                "watchlist": wd,
                "profitLabels": profit_labels, "profitData": profit_data,
                "recentTrades": [{"time": fmt_time(t.get("time","")), "symbol": t.get("symbol",""),
                    "action": t.get("action",""), "contracts": t.get("contracts",0),
                    "price": t.get("price",0), "usdt_value": t.get("usdt_value",0),
                    "realizedPnl": t.get("realizedPnl",0)} for t in trades[-50:][::-1] if (t.get("price") or 0) > 0][:30],
                "nextFunding": {}, "feeInfo": {"takerPct": 0.04, "makerPct": 0.02},
                "offline": True,  # 前端显示"币安离线"
            }

        # ── Binance 可达，并行获取数据 ──
        executor = ThreadPoolExecutor(max_workers=8)
        try:
            f_balance = executor.submit(lambda: get_account_balance(username))
            f_positions = executor.submit(lambda: get_positions_from_exchange(username))
            f_funding = executor.submit(get_funding_rates)
            f_fee = executor.submit(lambda: get_fee_info(username))
            f_tickers = executor.submit(lambda: get_tickers(_get_public_binance(username) if username else None))
            f_next_funding = executor.submit(get_next_funding_time)

            def _fetch_trades():
                fills = []
                if pb:
                    try:
                        syms = [s.strip() for s in os.getenv("SYMBOLS", "ETHUSDT,BTCUSDT").split(",") if s.strip()]
                        extra = set()
                        for cfg in load_strategies(username):
                            extra.add(cfg.get("symbol", ""))
                        for w in load_watchlist(username):
                            extra.add(w)
                        syms = list(dict.fromkeys(syms + list(extra)))
                        for raw_sym in syms:
                            try:
                                if "/" not in raw_sym and raw_sym.endswith("USDT"):
                                    csym = f"{raw_sym[:-4]}/{raw_sym[-4:]}:{raw_sym[-4:]}"
                                else:
                                    csym = raw_sym
                                result = _run_with_timeout(
                                    lambda s=csym: pb.fetch_my_trades(s, limit=500), 3)
                                if result:
                                    for f_item in result:
                                        info = f_item.get("info", {}) or {}
                                        side = f_item.get("side", "")
                                        pos_side = info.get("positionSide", "")
                                        amount = float(f_item.get("amount", 0) or 0)
                                        price = float(f_item.get("price", 0) or 0)
                                        if side == "buy":
                                            action = "买入"
                                        elif side == "sell":
                                            action = "卖出"
                                        else:
                                            action = ""
                                        fills.append({
                                            "time": datetime.fromtimestamp((f_item.get("timestamp", 0) or 0) / 1000, tz=timezone.utc).isoformat(),
                                            "symbol": clean_symbol(f_item.get("symbol", "") or ""),
                                            "realizedPnl": float(info.get("realizedPnl", 0) or 0),
                                            "price": price,
                                            "side": pos_side or side.upper(),
                                            "action": action,
                                            "contracts": round(amount, 4),
                                            "usdt_value": round(amount * price, 2),
                                        })
                            except Exception:
                                pass
                    except Exception:
                        pass
                return fills

            f_binance_fills = executor.submit(_fetch_trades)

            # 收集结果（每个最多等 3 秒，总计不超过 ~20 秒）
            def _get(future, default):
                try:
                    return future.result(timeout=3)
                except Exception:
                    return default

            bal = _get(f_balance, {})
            exchange_positions = _get(f_positions, [])
            funding_rates = _get(f_funding, {})
            fee_info = _get(f_fee, {"taker": 0.0004, "maker": 0.0002})
            tickers = _get(f_tickers, {})
            next_funding = _get(f_next_funding, {})
            binance_fills = _get(f_binance_fills, [])
        finally:
            executor.shutdown(wait=False)

        usdt = bal.get("USDT", {}) if bal else {}
        balance = float(usdt.get("total", 0) or 0)
        available = float(usdt.get("free", 0) or 0)

        # 检测手动平仓：本地有但交易所没有的仓位
        closed_syms = []
        if exchange_positions and local_positions:
            modified, closed_syms = sync_manual_exits(exchange_positions, local_positions, trades)
            if modified:
                save_trades(trades, username)
                save_positions(local_positions, username)
                trades = load_trades(username)
                if closed_syms:
                    logging.info(f"检测到手动平仓: {closed_syms}")

        positions_display = []
        unrealized_pnl = 0.0
        taker_fee = fee_info.get("taker", 0.0004)
        if exchange_positions:
            for p in exchange_positions:
                contracts = abs(float(p.get("contracts", 0) or 0))
                if contracts < 0.0001:
                    continue
                side = "LONG" if (p.get("side", "") == "long") else "SHORT"
                upnl = float(p.get("unrealizedPnl", 0) or 0)
                unrealized_pnl += upnl
                sym_raw = p.get("symbol", "")
                sym_clean = sym_raw.replace("/", "").replace(":", "")
                info = p.get("info", {}) or {}
                margin_type = info.get("marginType", "cross")  # cross=全仓, isolated=逐仓
                fdata = funding_rates.get(sym_clean, {}) or funding_rates.get(sym_raw, {})
                funding_rate = float(fdata.get("fundingRate", 0) or 0) if fdata else 0
                mark_price = float(p.get("markPrice", 0) or 0)
                entry_price = float(p.get("entryPrice", 0) or 0)
                notional = contracts * mark_price
                # 开仓+平仓手续费合计
                open_fee = notional * taker_fee
                close_fee = notional * taker_fee
                total_fee = open_fee + close_fee
                # 资金费率结算（每8小时），当前持仓需支付/收取
                funding_cost = notional * funding_rate  # 下一期预估
                funding_label = "每8h" if abs(funding_rate) > 0 else "暂无"
                if funding_rate > 0:
                    funding_label = f"+{(funding_rate*100):.4f}% (空付多)"
                elif funding_rate < 0:
                    funding_label = f"{(funding_rate*100):.4f}% (多付空)"
                positions_display.append({
                    "symbol": p.get("symbol", ""),
                    "side": side,
                    "entryPrice": round(entry_price, 4),
                    "markPrice": round(mark_price, 4),
                    "liquidationPrice": float(p.get("liquidationPrice", 0) or 0),
                    "leverage": int(os.getenv("LEVERAGE", "3")),
                    "marginMode": "逐仓" if margin_type == "isolated" else "全仓",
                    "unrealizedPnl": round(upnl, 2),
                    "contracts": contracts,
                    "notional": round(notional, 2),
                    "fundingRate": round(funding_rate * 100, 4),
                    "fundingCost": round(funding_cost, 4),
                    "fundingLabel": funding_label,
                    "openFee": round(open_fee, 4),
                    "closeFee": round(close_fee, 4),
                    "totalFee": round(total_fee, 4),
                })

        # 自选行情
        watch_data = []
        for sym in watchlist:
            key = sym if sym in tickers else (f"{sym[:-4]}/{sym[-4:]}:{sym[-4:]}" if sym.endswith("USDT") else sym)
            t = tickers.get(key, {})
            watch_data.append({
                "symbol": sym,
                "price": float(t.get("last", 0) or 0),
                "change": float(t.get("percentage", 0) or 0),
            })

        # 收益走势：合并币安真实成交（已并行获取）+ 本地记录
        profit_data, profit_labels = [], []
        # 合并并去重
        seen = set()
        merged = []
        for t in trades:
            key = (t.get("time", "")[:19], t.get("symbol", ""), str(t.get("price", 0)))
            if key not in seen:
                seen.add(key)
                merged.append(t)
        for bf in binance_fills:
            key = (bf["time"][:19], bf["symbol"], str(bf.get("price", 0)))
            if key not in seen:
                seen.add(key)
                # 币安成交已包含完整字段（action/side/contracts/usdt_value）
                merged.append(bf)
        merged.sort(key=lambda x: x.get("time", ""))
        # 计算累计盈亏（展示最近500条）
        cumulative = 0
        for t in merged[-500:]:
            cumulative += t.get("realizedPnl", 0) or 0
            profit_data.append(round(cumulative, 2))
            profit_labels.append(fmt_time(t.get("time", ""))[5:16])

        # 今日统计：使用去重后的合并数据，北京时间分界
        today_str_bj = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
        today_pnl = 0
        for t in merged:
            t_time = t.get("time", "")
            # 转为北京时间判断
            try:
                if isinstance(t_time, str) and "T" in t_time:
                    dt = datetime.fromisoformat(t_time.replace("Z", "+00:00"))
                    bj_str = dt.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
                else:
                    bj_str = ""
            except Exception:
                bj_str = ""
            if bj_str == today_str_bj:
                today_pnl += t.get("realizedPnl", 0) or 0
        today_trades_count = 0
        for t in merged:
            t_time = t.get("time", "")
            try:
                if isinstance(t_time, str) and "T" in t_time:
                    dt = datetime.fromisoformat(t_time.replace("Z", "+00:00"))
                    bj_str = dt.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
                else:
                    bj_str = ""
            except Exception:
                bj_str = ""
            if bj_str == today_str_bj and (t.get("realizedPnl") or 0) != 0:
                today_trades_count += 1

        return {
            "balance": round(balance, 2),
            "available": round(available, 2),
            "positions": positions_display,
            "unrealizedPnl": round(unrealized_pnl, 2),
            "positionCount": len(positions_display),
            "todayPnl": round(today_pnl, 2),
            "todayTrades": today_trades_count,
            "watchlist": watch_data,
            "profitLabels": profit_labels,
            "profitData": profit_data,
            "recentTrades": [{"time": fmt_time(t.get("time", "")), "symbol": t.get("symbol", ""), "action": t.get("action", ""), "contracts": t.get("contracts", 0), "price": t.get("price", 0), "usdt_value": t.get("usdt_value", 0), "realizedPnl": t.get("realizedPnl", 0)} for t in merged[-50:][::-1] if (t.get("price") or 0) > 0][:30],
            "nextFunding": next_funding,
            "feeInfo": {"takerPct": round(taker_fee * 100, 3), "makerPct": round(fee_info.get("maker", 0.0002) * 100, 3)},
        }

    if path == "/api/equity":
        period = params.get("period", ["1M"])[0]
        days_map = {"1D": 1, "1W": 7, "1M": 30, "3M": 90, "1Y": 365}
        days = days_map.get(period, 30)
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        trades = load_trades(username)
        # Get current balance
        try:
            if pb:
                bal = pb.fetch_balance()
                usdt = bal.get("USDT", {})
                current_bal = float(usdt.get("total", 0) or 0)
            else:
                current_bal = 680.0
        except Exception:
            current_bal = 680.0
        # Build daily equity from trades
        from collections import defaultdict
        daily_pnl = defaultdict(float)
        for t in trades:
            t_time = t.get("time", "")
            try:
                if isinstance(t_time, str) and "T" in t_time:
                    dt = datetime.fromisoformat(t_time.replace("Z", "+00:00"))
                else:
                    continue
            except Exception:
                continue
            if dt.replace(tzinfo=None) < cutoff.replace(tzinfo=None):
                continue
            day_key = dt.strftime("%m-%d")
            daily_pnl[day_key] += t.get("realizedPnl", 0) or 0
        # Build cumulative equity from oldest to newest
        labels = []
        data = []
        start_bal = current_bal - sum(daily_pnl.values())
        cum = start_bal
        for d in sorted(daily_pnl.keys()):
            cum += daily_pnl[d]
            labels.append(d)
            data.append(round(cum, 2))
        # Add current point
        labels.append("now")
        data.append(round(current_bal, 2))
        return {"labels": labels[-300:], "data": data[-300:], "period": period}

    if path == "/api/analysis":
        trades = load_trades(username)
        # 计算真实胜率：平仓记录中有 realizedPnl > 0 的比例
        exits = [t for t in trades if t.get("realizedPnl") is not None and t.get("realizedPnl") != 0]
        win_count = sum(1 for t in exits if t.get("realizedPnl", 0) > 0)
        total_closed = len(exits)
        win_rate = round(win_count / max(total_closed, 1) * 100, 1) if total_closed > 0 else 0
        # 盈亏比：平均盈利 / 平均亏损（绝对值）
        wins = [t.get("realizedPnl", 0) for t in exits if t.get("realizedPnl", 0) > 0]
        losses = [abs(t.get("realizedPnl", 0)) for t in exits if t.get("realizedPnl", 0) < 0]
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 0
        pnl_ratio = f"1:{round(avg_win / avg_loss, 1)}" if avg_loss > 0 and avg_win > 0 else "--"
        # 交易对分布
        pair_count = {}
        for t in trades:
            s = t.get("symbol", ""); pair_count[s] = pair_count.get(s, 0) + 1
        pair_stats = sorted([{"name": k, "count": v} for k, v in pair_count.items()], key=lambda x: -x["count"])[:5]
        best = max((t.get("realizedPnl", 0) for t in exits), default=0) if exits else 0
        # 总交易笔数（有盈亏的）
        total = total_closed
        return {
            "winRate": win_rate, "pnlRatio": pnl_ratio, "totalTrades": total,
            "bestTrade": round(best, 2), "pairStats": pair_stats,
        }

    if path.startswith("/api/logs"):
        level = params.get("level", ["ALL"])[0]
        raw = load_logs(500, username)
        lines = raw.strip().split("\n") if raw.strip() else []
        if level != "ALL":
            lines = [l for l in lines if level in l]
        return {"lines": lines[:100]}

    if path.startswith("/api/klines/"):
        symbol_raw = path.split("/api/klines/")[1]
        limit = int(params.get("limit", ["100"])[0])
        klines = get_klines(symbol_raw, "1m", limit)
        if not klines:
            return {"klines": [], "ma5": [], "ma10": [], "ma20": []}
        closes = [k[4] for k in klines]
        return {
            "klines": klines,
            "ma5": calc_ma(closes, 5),
            "ma10": calc_ma(closes, 10),
            "ma20": calc_ma(closes, 20),
        }

    if path.startswith("/api/ticker/"):
        symbol_raw = path.split("/api/ticker/")[1]
        tickers = get_tickers()
        t = tickers.get(symbol_raw, {})
        return {"symbol": symbol_raw, "price": float(t.get("last", 0) or 0)}

    if path == "/api/market/overview":
        tickers = get_tickers()
        result = []
        for sym, t in list(tickers.items())[:50]:
            result.append({
                "symbol": sym,
                "price": float(t.get("last", 0) or 0),
                "change": float(t.get("percentage", 0) or 0),
            })
        result.sort(key=lambda x: abs(x["change"]), reverse=True)
        return result

    if path == "/api/apikeys":
        if username:
            keys = _get_user_api_keys(username)
            keys["configured"] = bool(keys.get("apiKey") and keys.get("secretKey"))
            return keys
        return {"apiKey": "", "secretKey": "", "proxyUrl": "", "configured": False}

    if path == "/api/watchlist":
        return {"symbols": load_watchlist(username)}

    # ── POST ──

    if path == "/api/bot/start" and body is not None:
        restart_bot(username)
        return {"ok": True}

    if path == "/api/bot/stop" and body is not None:
        if sys.platform == "win32":
            kill_bot_windows()
        else:
            subprocess.run(["pkill", "-f", "strategy_bot.py"], capture_output=True)
        return {"ok": True}

    if path == "/api/config/save" and body:
        data = _parse_json(body)
        save_env(data, username)
        restart_bot(username)
        return {"ok": True}

    if path == "/api/apikeys/save" and body:
        data = _parse_json(body)
        user_env_file = _user_file(username, ".env") if username else ENV_FILE
        env_lines = []
        try:
            with open(user_env_file, "r", encoding="utf-8") as f:
                env_lines = f.readlines()
        except Exception:
            env_lines = []
        new_lines = []
        found_key, found_sec, found_proxy = False, False, False
        for line in env_lines:
            if line.startswith("BINANCE_API_KEY="):
                new_lines.append(f"BINANCE_API_KEY={data.get('apiKey','')}\n")
                found_key = True
            elif line.startswith("BINANCE_SECRET_KEY="):
                new_lines.append(f"BINANCE_SECRET_KEY={data.get('secretKey','')}\n")
                found_sec = True
            elif line.startswith("PROXY_URL="):
                new_lines.append(f"PROXY_URL={data.get('proxyUrl','')}\n")
                found_proxy = True
            else:
                new_lines.append(line)
        if not found_key:
            new_lines.append(f"BINANCE_API_KEY={data.get('apiKey','')}\n")
        if not found_sec:
            new_lines.append(f"BINANCE_SECRET_KEY={data.get('secretKey','')}\n")
        if not found_proxy:
            new_lines.append(f"PROXY_URL={data.get('proxyUrl','')}\n")
        with open(user_env_file, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
        # 清除缓存，让新代理和密钥立即生效
        _user_private_clients.pop(username, None) if username else None
        _user_public_clients.pop(username, None) if username else None
        return {"ok": True}

    if path == "/api/profile/save" and body:
        data = _parse_json(body)
        if username:
            profile = load_profile(username)
            if "nickname" in data:
                profile["nickname"] = data["nickname"][:20]
            save_profile(username, profile)
        return {"ok": True}

    if path == "/api/avatar/upload" and body:
        data = _parse_json(body)
        img_data = data.get("image", "")
        if img_data and username:
            import re as _re
            img_bytes = None
            if img_data.startswith("data:image/jpeg"):
                img_bytes = base64.b64decode(_re.sub(r"^data:image/jpeg;base64,", "", img_data))
                ext = "jpg"
            elif img_data.startswith("data:image/png"):
                img_bytes = base64.b64decode(_re.sub(r"^data:image/png;base64,", "", img_data))
                ext = "png"
            if img_bytes:
                ua = _user_file(username, f"avatar.{ext}")
                ua.write_bytes(img_bytes)
                return {"ok": True}
        return {"ok": False, "error": "无效图片"}

    if path == "/api/trade/manual" and body:
        data = _parse_json(body)
        if not pb:
            return {"ok": False, "error": "币安 API 未连接"}
        try:
            symbol = data.get("symbol", "ETHUSDT")
            side = data.get("side", "LONG")
            margin = float(data.get("margin", 0))
            leverage = int(data.get("leverage", 3))
            sl_pct = float(data.get("slPct", 5))

            sym = symbol
            if "/" not in sym and sym.endswith("USDT"):
                sym = f"{sym[:-4]}/{sym[-4:]}:{sym[-4:]}"

            # 获取价格
            ticker = pb.fetch_ticker(sym)
            price = ticker.get("last", 0) or ticker.get("close", 0)

            usdt_val = margin * leverage
            market = pb.market(sym)
            cs = market.get("contractSize", 1.0) or 1.0
            contracts = usdt_val / (price * cs)
            contracts = float(pb.amount_to_precision(sym, contracts))

            pb.set_leverage(leverage, sym)

            order_side = "buy" if side == "LONG" else "sell"

            order = pb.create_order(
                symbol=sym, type="market", side=order_side,
                amount=contracts,
            )
            return {"ok": True, "result": {"id": order.get("id", ""), "symbol": symbol, "side": side, "contracts": contracts}}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    if path == "/api/watchlist/add" and body:
        data = _parse_json(body)
        sym = data.get("symbol", "").strip().upper()
        if not sym:
            return {"ok": False, "error": "symbol required"}
        wl = load_watchlist(username)
        if sym not in wl:
            wl.append(sym)
            save_watchlist(wl, username)
        return {"ok": True}

    if path == "/api/watchlist/remove" and body:
        data = _parse_json(body)
        sym = data.get("symbol", "").strip().upper()
        wl = load_watchlist(username)
        if sym in wl:
            wl.remove(sym)
            save_watchlist(wl, username)
        return {"ok": True}

    if path == "/api/strategies":
        return {"strategies": load_strategies(username), "defaults": load_env(username)}

    if path == "/api/strategies/save" and body:
        data = _parse_json(body)
        save_strategies(data.get("strategies", []), username)
        return {"ok": True}

    if path == "/api/avatar":
        if username:
            for ext in ["jpg", "png"]:
                ua = _user_file(username, f"avatar.{ext}")
                if ua.exists():
                    return {"data": f"data:image/{'jpeg' if ext=='jpg' else ext};base64," + base64.b64encode(ua.read_bytes()).decode()}
        avatar_path = BASE_DIR / "交易头像.jpg"
        try:
            with open(avatar_path, "rb") as f:
                img = base64.b64encode(f.read()).decode()
            return {"data": f"data:image/jpeg;base64,{img}"}
        except Exception:
            return {"data": ""}

    if path == "/api/profile":
        if username:
            return load_profile(username)
        return {"nickname": "", "avatar": ""}

    if path == "/api/verify-password" and body:
        data = _parse_json(body)
        pwd = data.get("password", "")
        admin_pwd = os.getenv("ADMIN_PASSWORD", "")
        if admin_pwd and pwd == admin_pwd:
            return {"ok": True}
        return {"ok": False, "error": "密码错误"}

    # ── 策略密码管理 ──
    if path == "/api/strategy-password/status":
        sp_file = _user_file(username, "strategy_password.json") if username else BASE_DIR / "strategy_password.json"
        if sp_file.exists():
            return {"hasPassword": True}
        return {"hasPassword": False}

    if path == "/api/strategy-password/set" and body:
        data = _parse_json(body)
        new_pwd = data.get("password", "")
        if not new_pwd or len(new_pwd) < 4:
            return {"ok": False, "error": "密码至少4位"}
        sp_file = _user_file(username, "strategy_password.json") if username else BASE_DIR / "strategy_password.json"
        with open(sp_file, "w") as f:
            json.dump({"password_hash": _hash_password(new_pwd)}, f)
        return {"ok": True}

    if path == "/api/strategy-password/verify" and body:
        data = _parse_json(body)
        pwd = data.get("password", "")
        sp_file = _user_file(username, "strategy_password.json") if username else BASE_DIR / "strategy_password.json"
        if not sp_file.exists():
            return {"ok": True}  # 未设置密码，直接通过
        try:
            with open(sp_file, "r") as f:
                stored = json.load(f)
            if stored.get("password_hash") == _hash_password(pwd):
                return {"ok": True}
            return {"ok": False, "error": "策略密码错误"}
        except Exception:
            return {"ok": True}  # 文件损坏，直接通过

    if path == "/api/debug/trades":
        if not pb:
            return {"error": "无币安连接"}
        try:
            since = int((time.time() - 86400 * 7) * 1000)
            syms = [s.strip() for s in os.getenv("SYMBOLS", "ETHUSDT").split(",") if s.strip()]
            raw = syms[0]
            if "/" not in raw and raw.endswith("USDT"):
                csym = f"{raw[:-4]}/{raw[-4:]}:{raw[-4:]}"
            else:
                csym = raw
            fills = pb.fetch_my_trades(csym, since=since, limit=5)
            result = []
            for t in (fills or []):
                result.append({
                    "symbol": t.get("symbol"),
                    "side": t.get("side"),
                    "amount": t.get("amount"),
                    "price": t.get("price"),
                    "timestamp": t.get("timestamp"),
                    "info_keys": list(t.get("info", {}).keys()) if t.get("info") else [],
                    "info_posSide": t.get("info", {}).get("positionSide", "N/A"),
                })
            return {"count": len(fills or []), "sample": result}
        except Exception as e:
            return {"error": str(e)}

    if path == "/api/history/positions":
        # 1. 先从本地 trades.json 构建配对的入场/出场记录（策略交易权威来源）
        closed = []
        local_trades = load_trades(username)
        entry_map = {}  # {symbol: entry_info}
        local_time_set = set()  # {(symbol, unix_ts)} for matching

        for t in local_trades:
            action = t.get("action", "")
            sym = t.get("symbol", "")
            t_time = t.get("time", "")
            try:
                if isinstance(t_time, str) and "T" in t_time:
                    t_ts = int(datetime.fromisoformat(t_time.replace("Z", "+00:00")).timestamp())
                elif isinstance(t_time, (int, float)):
                    t_ts = int(t_time)
                else:
                    t_ts = 0
            except Exception:
                t_ts = 0

            if action in ("long_entry", "short_entry"):
                entry_map[sym] = {
                    "symbol": sym,
                    "side": "LONG" if action == "long_entry" else "SHORT",
                    "entryTime": t_time,
                    "entryPrice": float(t.get("price", 0) or 0),
                    "contracts": float(t.get("contracts", 0) or 0),
                    "usdtValue": float(t.get("usdt_value", 0) or 0),
                }
                if t_ts > 0:
                    local_time_set.add((sym, t_ts))
            elif "exit" in action or "flip" in action:
                if t_ts > 0:
                    local_time_set.add((sym, t_ts))
                prev = entry_map.pop(sym, None)
                exit_price = float(t.get("price", 0) or 0)
                contracts = float(t.get("contracts", 0) or 0)
                usdt_val = float(t.get("usdt_value", 0) or 0)
                if "realizedPnl" in t:
                    realized_pnl = float(t.get("realizedPnl", 0) or 0)
                elif prev:
                    ep = prev["entryPrice"]
                    realized_pnl = (exit_price - ep) / ep * prev["usdtValue"] if prev["side"] == "LONG" else (ep - exit_price) / ep * prev["usdtValue"]
                else:
                    realized_pnl = 0
                entry_price = prev.get("entryPrice", exit_price) if prev else exit_price
                action_map = {
                    "exit_long": "平多", "exit_short": "平空",
                    "exit_manual": "手动平仓",
                    "flip_close_long": "翻转平多", "flip_close_short": "翻转平空",
                }
                action_name = action_map.get(action, action)
                closed.append({
                    "symbol": sym,
                    "side": prev.get("side", "") if prev else "",
                    "action": action_name,
                    "entryTime": prev.get("entryTime", "") if prev else "",
                    "exitTime": t_time,
                    "entryPrice": round(entry_price, 4),
                    "exitPrice": round(exit_price, 4),
                    "price": round(exit_price, 4),
                    "contracts": contracts,
                    "usdtValue": round(usdt_val, 2),
                    "realizedPnl": round(realized_pnl, 2),
                    "isManual": action == "exit_manual",
                })

        # 未平仓的仓位也加入历史列表（标记为持有中）
        for sym, pos in load_positions(username).items():
            if pos.get("contracts", 0) > 0:
                closed.append({
                    "symbol": sym,
                    "side": pos.get("side", ""),
                    "action": "持有中",
                    "entryTime": pos.get("entry_time", ""),
                    "exitTime": "",
                    "entryPrice": round(float(pos.get("entry_price", 0) or 0), 4),
                    "exitPrice": 0,
                    "price": round(float(pos.get("entry_price", 0) or 0), 4),
                    "contracts": float(pos.get("contracts", 0) or 0),
                    "usdtValue": 0,
                    "realizedPnl": 0,
                    "isManual": False,
                })

        # 2. 从币安API补充手动交易（本地记录中没有的成交）
        if pb:
            try:
                symbols = [s.strip() for s in os.getenv("SYMBOLS", "ETHUSDT,BTCUSDT").split(",") if s.strip()]
                extra_syms = set()
                for cfg in load_strategies(username):
                    extra_syms.add(cfg.get("symbol", ""))
                for w in load_watchlist(username):
                    extra_syms.add(w)
                all_syms = list(dict.fromkeys(symbols + list(extra_syms)))

                for raw_sym in all_syms:
                    try:
                        if "/" not in raw_sym and raw_sym.endswith("USDT"):
                            csym = f"{raw_sym[:-4]}/{raw_sym[-4:]}:{raw_sym[-4:]}"
                        else:
                            csym = raw_sym
                        fills = _run_with_timeout(
                            lambda s=csym: pb.fetch_my_trades(s, limit=500), 8)
                        if fills:
                            for t in fills:
                                ts = t.get("timestamp", 0) or 0
                                ts_int = int(ts / 1000) if ts > 1e12 else int(ts)
                                price = float(t.get("price", 0) or 0)
                                amount = float(t.get("amount", 0) or 0)
                                side = t.get("side", "")
                                info = t.get("info", {}) or {}
                                sym = clean_symbol(t.get("symbol", "") or "")
                                pos_side = info.get("positionSide", side.upper())
                                rpnl = float(info.get("realizedPnl", 0) or 0)

                                # 只保留本地没有记录的手动交易
                                is_local = False
                                for lt_sym, lt_ts in local_time_set:
                                    if lt_sym == sym and abs(ts_int - lt_ts) <= 30:
                                        is_local = True
                                        break
                                if is_local:
                                    continue

                                action = "手动买入" if side == "buy" else "手动卖出"
                                closed.append({
                                    "symbol": sym,
                                    "side": pos_side or side.upper(),
                                    "action": action,
                                    "entryTime": fmt_time(ts),
                                    "exitTime": "",
                                    "entryPrice": round(price, 4),
                                    "exitPrice": round(price, 4),
                                    "price": round(price, 4),
                                    "contracts": round(amount, 4),
                                    "usdtValue": round(amount * price, 2),
                                    "realizedPnl": round(rpnl, 2),
                                    "isManual": True,
                                })
                    except Exception:
                        pass
            except Exception as e:
                logging.info(f"币安历史获取失败: {e}")

        # 按时间排序（优先用 exitTime，其次 entryTime），最新的在前
        def _sort_key(c):
            t = c.get("exitTime") or c.get("entryTime") or ""
            return t
        closed.sort(key=_sort_key, reverse=True)

        # 合并连续的翻转操作：如果同币种的翻转平多紧跟翻转平空(或反之)，合并显示
        # 过滤掉 open 持仓记录（重复的持有中记录）
        filtered = []
        for c in closed:
            if c.get("action") == "持有中":
                filtered.append(c)
            else:
                filtered.append(c)
        # 去重：相同 symbol + 相同 exitTime 的只保留一条
        seen = set()
        deduped = []
        for c in filtered:
            key = (c.get("symbol", ""), c.get("exitTime", ""), c.get("action", ""), c.get("realizedPnl", 0))
            if key not in seen:
                seen.add(key)
                deduped.append(c)
        closed = deduped

        total_pnl = sum(c.get("realizedPnl", 0) for c in closed)
        win_count = sum(1 for c in closed if c.get("realizedPnl", 0) > 0)
        return {
            "positions": closed[-100:],
            "totalPnl": round(total_pnl, 2),
            "winRate": round(win_count / max(len(closed), 1) * 100, 1),
            "totalClosed": len(closed),
        }

    return {"error": "not found"}


# ============================================================
#   HTTP Server
# ============================================================

class UIHandler(BaseHTTPRequestHandler):

    def _send(self, data, status=200, ct="application/json"):
        if isinstance(data, (dict, list, tuple)):
            data = json.dumps(data, ensure_ascii=False)
        if not isinstance(data, bytes):
            data = data.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ct + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_file(self, filepath: Path, ct: str):
        """发送静态文件."""
        try:
            data = filepath.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(data)
        except Exception:
            self._send({"error": "not found"}, status=404)

    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            qs = parsed.query

            # ── 静态文件 ──
            if path == "/chart.js":
                chart_js = BASE_DIR / "chart.js"
                if chart_js.exists():
                    self._send_file(chart_js, "application/javascript")
                    return
                self._send({"error": "not found"}, status=404)
                return

            # ── 注册页面 ──
            if path == "/register":
                self._send(PAGE_REGISTER, ct="text/html")
                return

            # ── 登录页/主应用 ──
            if path == "/":
                token = _extract_token(qs)
                user = get_user_by_token(token) if token else None
                if user:
                    self._send(HTML_USER, ct="text/html")
                else:
                    self._send(PAGE_LOGIN, ct="text/html")
                return

            # ── 主应用页面 ──
            if path == "/app":
                # JS 端用 localStorage 做鉴权，服务端无条件返回主页面
                self._send(HTML_USER, ct="text/html")
                return

            if path.startswith("/page/"):
                page_name = path.split("/page/")[1]
                self._send(PAGE_MAP.get(page_name, "<p>404</p>"), ct="text/html")
                return
            if path.startswith("/api/"):
                api_path = path if not qs else path
                try:
                    result = handle_api(api_path, qs=qs)
                except Exception as e:
                    result = {"error": str(e)}
                code = result.get("code", 0) if isinstance(result, dict) else 0
                if code == 401 or (isinstance(result, dict) and result.get("error") == "unauthorized"):
                    status = 401
                elif isinstance(result, dict) and "error" in result:
                    status = 404
                else:
                    status = 200
                self._send(result, status=status)
                return

            self._send({"error": "not found"}, status=404)
        except Exception as e:
            try:
                self._send({"error": str(e)}, status=500)
            except Exception:
                pass

    def do_POST(self):
        try:
            content_len = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_len).decode() if content_len else None

            parsed = urlparse(self.path)
            path = parsed.path
            qs = parsed.query

            if path.startswith("/api/"):
                try:
                    result = handle_api(path, body=body, qs=qs)
                except Exception as e:
                    result = {"error": str(e)}
                code = result.get("code", 0) if isinstance(result, dict) else 0
                if code == 401 or (isinstance(result, dict) and result.get("error") == "unauthorized"):
                    status = 401
                elif isinstance(result, dict) and "error" in result:
                    status = 404
                else:
                    status = 200
                self._send(result, status=status)
                return

            self._send({"error": "not found"}, status=404)
        except Exception as e:
            try:
                self._send({"error": str(e)}, status=500)
            except Exception:
                pass

    def log_message(self, format, *args):
        pass


def run():
    # 初始化数据库和预置账户
    init_preset_accounts()
    print(f"数据库已初始化: {BASE_DIR / 'accounts.db'}")

    server = ThreadingHTTPServer(("0.0.0.0", PORT), UIHandler)
    server_host = os.getenv("SERVER_HOST", "localhost")
    print(f"量化交易系统 v3 → http://{server_host}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    run()
