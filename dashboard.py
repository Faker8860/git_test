"""
量化交易 — Web 看板 + 管理面板 v2
=================================
/         公开看板（多币种持仓 + 强平价）
/admin    参数设置（多币种 + 百分比仓位）
"""

import json
import os
import sys
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

BASE_DIR = Path(__file__).parent
TRADE_FILE = BASE_DIR / "trades.json"
STATE_FILE = BASE_DIR / "strategy_state.json"
LOG_FILE = BASE_DIR / "strategy.log"
ENV_FILE = BASE_DIR / ".env"

PORT = int(os.getenv("DASHBOARD_PORT", "8080"))
AUTO_REFRESH = 10

SYMBOLS_OPTIONS = ["ETHUSDT", "BTCUSDT", "BNBUSDT", "SOLUSDT", "DOGEUSDT",
                   "ADAUSDT", "XRPUSDT", "AVAXUSDT", "LINKUSDT", "ARBUSDT"]
MA_TYPES = ["TEMA", "SMA", "EMA", "DEMA", "SMMA", "HullMA", "WMA"]
TF_OPTIONS = [1, 3, 5, 15, 30, 60]
TRADE_TYPES = ["BOTH", "LONG", "SHORT"]


def load_env():
    cfg = {
        "SYMBOLS": "ETHUSDT,BTCUSDT", "MA_TYPE": "TEMA", "MA_LEN": "8",
        "CROSS_MULT": "3", "TIMEFRAME_MINUTES": "1", "DELAY_MINUTES": "5",
        "POSITION_PCT": "20", "LEVERAGE": "3", "STOP_LOSS_PCT": "5",
        "TRADE_TYPE": "BOTH", "MAX_ORDER_USDT": "0", "MIN_ORDER_USDT": "11",
    }
    try:
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    if k.strip() in cfg:
                        cfg[k.strip()] = v.strip()
    except Exception:
        pass
    return cfg


def save_env(form_data: dict):
    api_key, api_sec = "", ""
    try:
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("BINANCE_API_KEY="):
                    api_key = line.split("=", 1)[1].strip()
                if line.startswith("BINANCE_SECRET_KEY="):
                    api_sec = line.split("=", 1)[1].strip()
    except FileNotFoundError:
        pass

    lines = [
        "# 量化交易配置文件",
        "",
        f"BINANCE_API_KEY={api_key}",
        f"BINANCE_SECRET_KEY={api_sec}",
        "",
        f"MIN_ORDER_USDT={form_data.get('MIN_ORDER_USDT', '11')}",
        f"MAX_ORDER_USDT={form_data.get('MAX_ORDER_USDT', '0')}",
        f"STOP_LOSS_PCT={form_data['STOP_LOSS_PCT']}",
        f"LEVERAGE={form_data['LEVERAGE']}",
        "PORT=8000",
        "WEBHOOK_SECRET=",
        "",
        f"SYMBOLS={form_data['SYMBOLS']}",
        f"TIMEFRAME_MINUTES={form_data['TIMEFRAME_MINUTES']}",
        f"MA_TYPE={form_data['MA_TYPE']}",
        f"MA_LEN={form_data['MA_LEN']}",
        f"CROSS_MULT={form_data['CROSS_MULT']}",
        f"DELAY_MINUTES={form_data['DELAY_MINUTES']}",
        "DELAY_OFFSET=0",
        f"POSITION_PCT={form_data['POSITION_PCT']}",
        f"TRADE_TYPE={form_data['TRADE_TYPE']}",
    ]
    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def restart_bot():
    subprocess.run(["pkill", "-f", "strategy_bot.py"], capture_output=True)
    time.sleep(1)
    subprocess.Popen(
        ["python3", str(BASE_DIR / "strategy_bot.py")],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        cwd=str(BASE_DIR),
    )


def load_trades():
    try:
        with open(TRADE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def load_logs(n=40):
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            return "".join(f.readlines()[-n:])
    except Exception:
        return "暂无日志"


# ============================================================
#   看板
# ============================================================

def dashboard_page():
    trades = load_trades()
    positions = load_state()
    logs = load_logs(40)
    cfg = load_env()

    action_names = {
        "long_entry": "做多", "exit_long": "平多",
        "short_entry": "做空", "exit_short": "平空",
        "flip_close_long": "翻转平多", "flip_close_short": "翻转平空",
    }
    side_names = {"buy": "买", "sell": "卖"}

    # ── 持仓卡片（含强平价） ──
    if positions:
        pos_cards = ""
        for sym, info in positions.items():
            side = info.get("side", "")
            side_label = {"LONG": "多头", "SHORT": "空头"}.get(side, side)
            color = "#00cc00" if side == "LONG" else "#cc0000"
            entry = info.get("entry_price", "—")
            liq = info.get("liquidation_price", "—")
            margin = info.get("margin", "—")
            usdt_val = info.get("usdt_value", "—")
            lev = info.get("leverage", "—")
            pnl = info.get("unrealized_pnl", None)
            mark = info.get("mark_price", None)

            extra = ""
            if pnl is not None:
                pnl_color = "#00cc00" if float(pnl) >= 0 else "#cc0000"
                extra += f'<p>未实现盈亏：<b style="color:{pnl_color}">{float(pnl):.2f}U</b></p>'
            if mark is not None:
                liq_pct = ""
                if float(mark) > 0 and liq != "—" and float(liq) > 0:
                    if side == "LONG":
                        dist = (float(mark) - float(liq)) / float(mark) * 100
                    else:
                        dist = (float(liq) - float(mark)) / float(mark) * 100
                    liq_pct = f"（距强平 {dist:.1f}%）"
                extra += f'<p>标记价：{float(mark):.2f} {liq_pct}</p>'

            pos_cards += f"""
            <div class="card">
                <h3>{sym} <span style="color:{color};font-size:14px">{side_label}</span></h3>
                <div class="pos-grid">
                    <div><span>入场价</span><b>{entry}</b></div>
                    <div><span>强平价</span><b style="color:#ff4444">{liq}</b></div>
                    <div><span>仓位</span><b>{usdt_val}U</b></div>
                    <div><span>保证金</span><b>{margin}U</b></div>
                    <div><span>杠杆</span><b>{lev}x</b></div>
                </div>
                {extra}
            </div>"""
    else:
        pos_cards = '<div class="card"><p>当前无持仓</p></div>'

    # ── 交易记录 ──
    trade_rows = ""
    for t in reversed(trades[-50:]):
        dt = t.get("time", "")[:19].replace("T", " ")
        sym = t.get("symbol", "")
        trade_rows += f"""<tr>
            <td>{dt}</td><td>{sym}</td>
            <td>{action_names.get(t.get('action',''), t.get('action',''))}</td>
            <td>{side_names.get(t.get('side',''), '')}</td>
            <td>{t.get('contracts','')}</td><td>{t.get('price','')}</td><td>{t.get('usdt_value','')}</td>
        </tr>"""

    # ── 币种列表 ──
    symbols_list = cfg.get("SYMBOLS", "ETHUSDT")
    symbols_tags = "".join(
        f'<span class="tag">{s.strip()}</span>' for s in symbols_list.split(",") if s.strip()
    )

    return f"""<!DOCTYPE html>
<html lang="zh"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>量化交易看板</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Segoe UI','Microsoft YaHei',sans-serif;background:#0a0e14;color:#bfbdb6;padding:16px}}
h1{{color:#e6e1cf;margin-bottom:4px}}
.topbar{{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}}
.topbar a{{color:#ffb454;text-decoration:none;font-size:14px;border:1px solid #ffb454;padding:4px 12px;border-radius:4px}}
.topbar a:hover{{background:#ffb454;color:#000}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
@media(max-width:768px){{.grid{{grid-template-columns:1fr}}}}
.card{{background:#131721;border:1px solid #1f2937;border-radius:8px;padding:14px;margin-bottom:12px}}
.card h3{{color:#ffb454;margin-bottom:8px}}
.card p{{line-height:1.6;color:#8a8678}}
.pos-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:6px}}
.pos-grid div{{text-align:center}}
.pos-grid span{{display:block;font-size:11px;color:#5a5548;margin-bottom:2px}}
.pos-grid b{{font-size:16px;color:#e6e1cf}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{background:#1b2133;color:#e6e1cf;padding:8px 6px;text-align:left;border-bottom:2px solid #2a3346}}
td{{padding:6px;border-bottom:1px solid #1a1f2e;color:#9b9689}}
tr:hover td{{background:#1a1f2e}}
.log-box{{background:#0d1017;border:1px solid #1f2937;border-radius:6px;padding:12px;font-family:'Cascadia Code',Consolas,monospace;font-size:12px;white-space:pre-wrap;max-height:450px;overflow-y:auto;color:#7a7568}}
.refresh{{color:#5a5548;font-size:12px;margin-top:8px;text-align:right}}
.status{{display:inline-block;width:8px;height:8px;border-radius:50%;background:#00cc00;margin-right:6px;animation:pulse 2s infinite}}
.tag{{display:inline-block;background:#1b2133;color:#ffb454;padding:2px 8px;border-radius:3px;margin:2px;font-size:13px}}
@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:0.3}}}}
</style>
<meta http-equiv="refresh" content="{AUTO_REFRESH}">
</head><body>
<div class="topbar">
<h1><span class="status"></span>量化交易看板</h1>
<a href="/admin">⚙ 策略设置</a>
</div>
<p style="color:#5a5548;margin-bottom:16px">每{AUTO_REFRESH}秒刷新 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
<div class="grid"><div>
<div class="card"><h3>当前持仓</h3>{pos_cards}</div>
<div class="card">
<h3>当前配置</h3>
<p>监控币种：{symbols_tags}</p>
<p>K线：{cfg['TIMEFRAME_MINUTES']}分钟 | 均线：{cfg['MA_TYPE']}({cfg['MA_LEN']}) | 跨周期：{cfg['CROSS_MULT']}x</p>
<p>延迟：{cfg['DELAY_MINUTES']}分钟 | 方向：{cfg['TRADE_TYPE']}</p>
<p>仓位：{cfg['POSITION_PCT']}%余额 × {cfg['LEVERAGE']}x | 止损：{cfg['STOP_LOSS_PCT']}%</p>
</div>
</div><div>
<div class="card"><h3>运行日志</h3><div class="log-box">{logs}</div></div>
</div></div>
<div class="card" style="margin-top:16px">
<h3>交易记录（最近50笔）</h3>
<table>
<tr><th>时间</th><th>币种</th><th>操作</th><th>方向</th><th>张数</th><th>价格</th><th>金额(U)</th></tr>
{trade_rows}
</table></div>
<p class="refresh">上次刷新：{datetime.now().strftime('%H:%M:%S')}</p>
</body></html>"""


# ============================================================
#   管理页面
# ============================================================

def option_tags(options, selected, multi_select=False):
    if multi_select:
        return "".join(
            f'<option value="{o}">{o}</option>' for o in options
        )
    return "".join(
        f'<option value="{o}" {"selected" if str(o)==str(selected) else ""}>{o}</option>'
        for o in options
    )


def admin_page(success_msg=""):
    cfg = load_env()
    msg_html = f'<p style="background:#0a2a0a;color:#00cc00;padding:10px;border-radius:6px;margin-bottom:12px">{success_msg}</p>' if success_msg else ""

    selected_symbols = set(s.strip() for s in cfg["SYMBOLS"].split(",") if s.strip())

    return f"""<!DOCTYPE html>
<html lang="zh"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>策略设置</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Segoe UI','Microsoft YaHei',sans-serif;background:#0a0e14;color:#bfbdb6;padding:24px;max-width:700px;margin:0 auto}}
h1{{color:#e6e1cf;margin-bottom:8px}}
a.back{{color:#ffb454;text-decoration:none;font-size:14px}}
.card{{background:#131721;border:1px solid #1f2937;border-radius:8px;padding:20px;margin-top:16px}}
label{{display:block;color:#8a8678;margin-bottom:4px;margin-top:14px;font-size:13px}}
input,select{{width:100%;padding:8px 10px;background:#0d1017;border:1px solid #1f2937;border-radius:4px;color:#bfbdb6;font-size:14px}}
input:focus,select:focus{{outline:none;border-color:#ffb454}}
.row{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
.chips{{display:flex;flex-wrap:wrap;gap:6px;margin-top:4px}}
.chip{{padding:4px 10px;border:1px solid #1f2937;border-radius:4px;cursor:pointer;font-size:13px;user-select:none;color:#8a8678}}
.chip.on{{background:#ffb454;color:#000;border-color:#ffb454}}
button{{width:100%;padding:12px;margin-top:20px;background:#ffb454;color:#000;border:none;border-radius:6px;font-size:15px;font-weight:bold;cursor:pointer}}
button:hover{{background:#ffd080}}
.note{{color:#5a5548;font-size:12px;margin-top:16px}}
.info{{color:#ffb454;font-size:12px;margin-top:4px}}
</style>
</head><body>
<a href="/" class="back">← 返回看板</a>
<h1>⚙ 策略设置</h1>
{msg_html}
<div class="card">
<form method="post" action="/admin/save">

<label>交易币种（多选）</label>
<div class="chips" id="symbolChips"></div>
<input type="hidden" name="SYMBOLS" id="symbolsInput" value="{cfg['SYMBOLS']}">
<p class="info">点击选中币种，支持多选</p>

<label>K线周期（分钟）</label>
<select name="TIMEFRAME_MINUTES">{option_tags(TF_OPTIONS, cfg['TIMEFRAME_MINUTES'])}</select>

<label>均线类型</label>
<select name="MA_TYPE">{option_tags(MA_TYPES, cfg['MA_TYPE'])}</select>

<div class="row">
<div><label>均线周期</label><input type="number" name="MA_LEN" value="{cfg['MA_LEN']}" min="1" max="200"></div>
<div><label>跨周期倍数</label><input type="number" name="CROSS_MULT" value="{cfg['CROSS_MULT']}" min="1" max="10"></div>
</div>

<div class="row">
<div><label>信号延迟（分钟）</label><input type="number" name="DELAY_MINUTES" value="{cfg['DELAY_MINUTES']}" min="0" max="60"></div>
<div><label>仓位占比（%余额）</label><input type="number" name="POSITION_PCT" value="{cfg['POSITION_PCT']}" min="1" max="100" step="1"></div>
</div>

<div class="row">
<div><label>杠杆倍数</label><input type="number" name="LEVERAGE" value="{cfg['LEVERAGE']}" min="1" max="125"></div>
<div><label>止损比例（%）</label><input type="number" name="STOP_LOSS_PCT" value="{cfg['STOP_LOSS_PCT']}" min="0" max="50" step="0.5"></div>
</div>

<div class="row">
<div><label>交易方向</label><select name="TRADE_TYPE">{option_tags(TRADE_TYPES, cfg['TRADE_TYPE'])}</select></div>
</div>

<button type="submit">保存并重启策略</button>
</form>
</div>
<p class="note">保存后自动重启策略机器人。<br>仓位公式：账户余额 × 占比% × 杠杆 = 实际仓位。<br>例：余额1000U，占比20%，3x杠杆 → 保证金200U，实际仓位600U。</p>

<script>
const allSymbols = {json.dumps(SYMBOLS_OPTIONS)};
const selected = new Set({json.dumps(list(selected_symbols))});
const chipsDiv = document.getElementById('symbolChips');
const hiddenInput = document.getElementById('symbolsInput');

function render() {{
    chipsDiv.innerHTML = allSymbols.map(s =>
        `<span class="chip ${{selected.has(s)?'on':''}}" onclick="toggle('${{s}}')">${{s}}</span>`
    ).join('');
    hiddenInput.value = [...selected].join(',') || 'ETHUSDT';
}}

window.toggle = function(s) {{
    if (selected.has(s)) selected.delete(s); else selected.add(s);
    render();
}};

render();
</script>
</body></html>"""


# ============================================================
#   HTTP Handler
# ============================================================

class Handler(BaseHTTPRequestHandler):

    def _send_html(self, html: str):
        data = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self._send_html(dashboard_page())
        elif self.path == "/admin":
            self._send_html(admin_page())
        elif self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")

    def do_POST(self):
        if self.path == "/admin/save":
            content_len = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_len).decode()
            params = parse_qs(body)
            form = {k: v[0] for k, v in params.items()}

            save_env(form)
            restart_bot()

            self._send_html(admin_page(success_msg="✅ 配置已保存，策略机器人已重启，约3秒后生效。"))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def run():
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"看板 → http://47.76.187.153:{PORT}")
    print(f"设置 → http://47.76.187.153:{PORT}/admin")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    run()
