"""
交易机器人 GUI 控制台
=====================
启动：python gui.py
"""

import os
import sys
import json
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import ttk, messagebox

BASE_DIR = Path(__file__).parent
ENV_FILE = BASE_DIR / ".env"
TRADE_FILE = BASE_DIR / "trades.json"


class TradingGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("量化交易控制台")
        self.root.geometry("900x600")
        self.root.resizable(True, True)

        self.server_process = None
        self.settings = {}

        self._load_env()
        self._build_ui()
        self._load_trades()
        self._start_polling()

    # ── 配置读写 ────────────────────────────────────

    def _load_env(self):
        """从 .env 读取配置."""
        self.settings = {
            "MAX_ORDER_USDT": "2000",
            "STOP_LOSS_PCT": "5",
            "LEVERAGE": "3",
            "MIN_ORDER_USDT": "11",
            "PORT": "8000",
        }
        try:
            with open(ENV_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if "=" in line and not line.startswith("#"):
                        key, val = line.split("=", 1)
                        key = key.strip()
                        val = val.strip()
                        if key in self.settings:
                            self.settings[key] = val
        except FileNotFoundError:
            pass

    def _save_env(self):
        """保存配置到 .env."""
        lines = []
        # 保留 API Key 部分
        api_section = True
        try:
            with open(ENV_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    s = line.strip()
                    if s.startswith("BINANCE_API_KEY="):
                        lines.append(line.rstrip())
                        continue
                    if s.startswith("BINANCE_SECRET_KEY="):
                        lines.append(line.rstrip())
                        continue
        except FileNotFoundError:
            pass

        # 写入新配置
        new_content = []
        new_content.append("# 量化交易配置文件")
        new_content.append("")
        # API Keys
        api_key = ""
        api_sec = ""
        try:
            with open(ENV_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("BINANCE_API_KEY="):
                        api_key = line.split("=", 1)[1].strip()
                    if line.startswith("BINANCE_SECRET_KEY="):
                        api_sec = line.split("=", 1)[1].strip()
        except FileNotFoundError:
            pass
        if api_key:
            new_content.append(f"BINANCE_API_KEY={api_key}")
            new_content.append(f"BINANCE_SECRET_KEY={api_sec}")
            new_content.append("")

        new_content.append(f"MIN_ORDER_USDT={self.settings['MIN_ORDER_USDT']}")
        new_content.append(f"MAX_ORDER_USDT={self.settings['MAX_ORDER_USDT']}")
        new_content.append(f"STOP_LOSS_PCT={self.settings['STOP_LOSS_PCT']}")
        new_content.append(f"LEVERAGE={self.settings['LEVERAGE']}")
        new_content.append(f"PORT={self.settings['PORT']}")
        new_content.append(f"WEBHOOK_SECRET=")

        with open(ENV_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(new_content) + "\n")

    # ── UI 构建 ─────────────────────────────────────

    def _build_ui(self):
        # 主分割：左设置 / 右记录
        main_pane = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main_pane.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # ─── 左侧：设置面板 ───
        left_frame = ttk.LabelFrame(main_pane, text=" 设置 ", padding=10)
        main_pane.add(left_frame, weight=1)

        row = 0
        self._add_setting(left_frame, "下单金额 (USDT)", "MAX_ORDER_USDT", row); row += 1
        self._add_setting(left_frame, "止损比例 (%)", "STOP_LOSS_PCT", row); row += 1
        self._add_setting(left_frame, "杠杆倍数", "LEVERAGE", row); row += 1
        self._add_setting(left_frame, "最小下单 (USDT)", "MIN_ORDER_USDT", row); row += 1

        ttk.Separator(left_frame, orient=tk.HORIZONTAL).grid(
            row=row, column=0, columnspan=2, sticky="ew", pady=10); row += 1

        ttk.Button(left_frame, text="保存设置", command=self._on_save).grid(
            row=row, column=0, columnspan=2, sticky="ew", pady=5); row += 1

        # 服务器控制
        ttk.Separator(left_frame, orient=tk.HORIZONTAL).grid(
            row=row, column=0, columnspan=2, sticky="ew", pady=10); row += 1

        self.status_label = ttk.Label(left_frame, text="服务器：未启动", foreground="red")
        self.status_label.grid(row=row, column=0, columnspan=2, sticky="w"); row += 1

        btn_frame = ttk.Frame(left_frame)
        btn_frame.grid(row=row, column=0, columnspan=2, sticky="ew", pady=5); row += 1
        self.start_btn = ttk.Button(btn_frame, text="启动服务器", command=self._toggle_server)
        self.start_btn.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.server_running = False

        # ─── 右侧：交易记录表格 ───
        right_frame = ttk.LabelFrame(main_pane, text=" 交易记录 ", padding=5)
        main_pane.add(right_frame, weight=3)

        columns = ("time", "action", "side", "amount", "price", "value")
        self.tree = ttk.Treeview(right_frame, columns=columns, show="headings", height=20)

        self.tree.heading("time", text="时间")
        self.tree.heading("action", text="操作")
        self.tree.heading("side", text="方向")
        self.tree.heading("amount", text="张数")
        self.tree.heading("price", text="价格")
        self.tree.heading("value", text="金额(U)")

        self.tree.column("time", width=160)
        self.tree.column("action", width=90)
        self.tree.column("side", width=60)
        self.tree.column("amount", width=70)
        self.tree.column("price", width=80)
        self.tree.column("value", width=80)

        scrollbar = ttk.Scrollbar(right_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 汇总标签
        self.summary_label = ttk.Label(right_frame, text="")
        self.summary_label.pack(side=tk.BOTTOM, fill=tk.X, pady=(5, 0))

        # 刷新按钮
        ttk.Button(right_frame, text="刷新记录", command=self._load_trades).pack(
            side=tk.BOTTOM, fill=tk.X, pady=(5, 0))

    def _add_setting(self, parent, label, key, row):
        ttk.Label(parent, text=label + "：").grid(row=row, column=0, sticky="w", pady=3)
        var = tk.StringVar(value=self.settings.get(key, ""))
        ttk.Entry(parent, textvariable=var, width=15).grid(row=row, column=1, sticky="e", pady=3)
        setattr(self, f"var_{key}", var)

    # ── 操作 ────────────────────────────────────────

    def _on_save(self):
        for key in self.settings:
            var = getattr(self, f"var_{key}", None)
            if var:
                self.settings[key] = var.get()
        self._save_env()
        messagebox.showinfo("保存成功", "配置已保存\n重启服务器后生效")

    def _toggle_server(self):
        if self.server_running:
            self._stop_server()
        else:
            self._start_server()

    def _start_server(self):
        try:
            self.server_process = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "bot_server:app",
                 "--host", "0.0.0.0", "--port", self.settings.get("PORT", "8000")],
                cwd=str(BASE_DIR),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.server_running = True
            self.status_label.config(text="服务器：运行中", foreground="green")
            self.start_btn.config(text="停止服务器")
        except Exception as e:
            messagebox.showerror("启动失败", str(e))

    def _stop_server(self):
        if self.server_process:
            self.server_process.terminate()
            try:
                self.server_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.server_process.kill()
            self.server_process = None
        self.server_running = False
        self.status_label.config(text="服务器：已停止", foreground="red")
        self.start_btn.config(text="启动服务器")

    # ── 交易记录 ────────────────────────────────────

    def _load_trades(self):
        """从 trades.json 加载交易记录."""
        for item in self.tree.get_children():
            self.tree.delete(item)

        try:
            with open(TRADE_FILE, "r", encoding="utf-8") as f:
                trades = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            trades = []

        action_names = {
            "long_entry": "做多开仓",
            "exit_long": "平多",
            "short_entry": "做空开仓",
            "exit_short": "平空",
        }
        side_names = {"buy": "买入", "sell": "卖出"}

        total_profit = 0
        for t in trades:
            try:
                dt = datetime.fromisoformat(t["time"]).strftime("%m-%d %H:%M:%S")
            except Exception:
                dt = t.get("time", "")[:19]
            self.tree.insert("", "end", values=(
                dt,
                action_names.get(t.get("action", ""), t.get("action", "")),
                side_names.get(t.get("side", ""), t.get("side", "")),
                t.get("contracts", ""),
                t.get("price", ""),
                t.get("usdt_value", ""),
            ))

        # 汇总
        total = sum(t.get("usdt_value", 0) for t in trades)
        buy_count = sum(1 for t in trades if t.get("action") in ("long_entry", "exit_short"))
        sell_count = sum(1 for t in trades if t.get("action") in ("exit_long", "short_entry"))
        self.summary_label.config(
            text=f"共 {len(trades)} 笔  |  买入 {buy_count} 笔  |  卖出 {sell_count} 笔  |  总交易额 {total:.0f} U"
        )

    def _start_polling(self):
        """定时刷新交易记录."""
        self._load_trades()
        self.root.after(5000, self._start_polling)

    def on_close(self):
        """关闭窗口."""
        self._stop_server()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = TradingGUI(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()
