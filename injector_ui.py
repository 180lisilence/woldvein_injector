#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
woldvein_injector v0.4.7 — 注入器独立状态面板

只包含与"注入 + 通道"有关的状态展示与操作，
没有任何资源修改、创造模式、世界工具等玩法入口。
"""
import os
import sys
import time
import threading

import tkinter as tk
from tkinter import ttk, scrolledtext

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from injector_api import (  # noqa: E402
    InjectorService, VERSION, GAME_PROCESS_NAME, DLL_NAME,
)

BG = "#FFFFFF"
FG = "#1F1F1F"
MUTED = "#6B6B6B"
OK_COLOR = "#0F6E56"
BAD_COLOR = "#A32D2D"
ACCENT = "#185FA5"


class _TextLog:
    """把 logger 的输出接到面板文本框。"""

    def __init__(self, widget):
        self.widget = widget

    def write(self, s):
        if not str(s).strip():
            return
        self.widget.after(0, self._append, str(s))

    def _append(self, s):
        try:
            self.widget.insert(tk.END, s if s.endswith("\n") else s + "\n")
            self.widget.see(tk.END)
        except Exception:
            pass

    def flush(self):
        pass


class InjectorUI:
    def __init__(self, root):
        self.root = root
        self.root.title(f"woldvein_injector v{VERSION}")
        self.root.geometry("760x520")
        self.root.configure(bg=BG)

        self.svc = InjectorService()
        self._build()

        self.refresh_status()
        self._auto_tick()

    # ---------- 界面 ----------
    def _build(self):
        top = tk.Frame(self.root, bg=BG)
        top.pack(fill=tk.X, padx=16, pady=(14, 8))

        tk.Label(top, text=f"woldvein_injector v{VERSION}",
                 font=("Microsoft YaHei UI", 15, "bold"), bg=BG, fg=FG).pack(anchor=tk.W)
        tk.Label(top, text="只做三件事：找进程 · 注入通道 · 维持双向通道",
                 font=("Microsoft YaHei UI", 9), bg=BG, fg=MUTED).pack(anchor=tk.W)

        card = tk.LabelFrame(self.root, text=" 状态 ", bg=BG, fg=FG,
                             font=("Microsoft YaHei UI", 10))
        card.pack(fill=tk.X, padx=16, pady=6)

        self.vars = {}
        rows = [
            ("target_proc", "目标进程", GAME_PROCESS_NAME),
            ("pid", "进程 PID", "-"),
            ("dll", "通道 DLL", "-"),
            ("injected", "注入状态", "未注入"),
            ("transport", "通道", "-"),
        ]
        for i, (key, label, default) in enumerate(rows):
            tk.Label(card, text=label, font=("Microsoft YaHei UI", 9),
                     bg=BG, fg=MUTED).grid(row=i, column=0, sticky=tk.W, padx=(12, 16), pady=3)
            v = tk.StringVar(value=default)
            self.vars[key] = v
            color = FG if key in ("target_proc",) else (BAD_COLOR if key == "injected" else FG)
            tk.Label(card, textvariable=v, font=("Consolas", 9),
                     bg=BG, fg=color).grid(row=i, column=1, sticky=tk.W, pady=3)

        bar = tk.Frame(self.root, bg=BG)
        bar.pack(fill=tk.X, padx=16, pady=10)

        btns = [
            ("扫描进程", self.on_scan),
            ("注入通道 DLL", self.on_inject),
            ("通道自检", self.on_probe),
            ("刷新状态", self.refresh_status),
        ]
        for text, cmd in btns:
            ttk.Button(bar, text=text, command=cmd).pack(side=tk.LEFT, padx=(0, 8))

        log_frame = tk.LabelFrame(self.root, text=" 日志 ", bg=BG, fg=FG,
                                  font=("Microsoft YaHei UI", 10))
        log_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 14))
        self.log = scrolledtext.ScrolledText(log_frame, height=12,
                                             font=("Consolas", 9), relief=tk.FLAT)
        self.log.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self._redirect_logger()

    def _redirect_logger(self):
        try:
            import injector_core.logger as _lg
            if hasattr(_lg, "LOG_FILE"):
                pass
            sys.stdout = self._tee(sys.stdout, self.log)
        except Exception:
            pass

    @staticmethod
    def _tee(orig, widget):
        class Tee:
            def write(self, s):
                try:
                    orig.write(s)
                except Exception:
                    pass
                if str(s).strip():
                    widget.after(0, lambda: (widget.insert(tk.END, str(s)), widget.see(tk.END)))

            def flush(self):
                try:
                    orig.flush()
                except Exception:
                    pass
        return Tee()

    # ---------- 行为 ----------
    def _log(self, msg):
        self.log.insert(tk.END, f"{time.strftime('%H:%M:%S')}  {msg}\n")
        self.log.see(tk.END)

    def refresh_status(self):
        st = self.svc.status()
        self.vars["dll"].set(os.path.basename(st["dll_path"]) + ("（就绪）" if st["dll_exists"] else "（缺失）"))
        pid = st["game_pid"]
        self.vars["pid"].set(str(pid) if pid else "未找到")
        self.vars["injected"].set("已注入" if st["injected"] else "未注入")
        self.vars["transport"].set(st["pipe"])

        if pid:
            self.vars["injected"].set("已注入" if self.svc.is_injected(pid) else "未注入")

    def on_scan(self):
        pid, _ = self.svc.find_game_process()
        if pid:
            self._log(f"找到 {GAME_PROCESS_NAME}，PID={pid}")
        else:
            self._log(f"未找到 {GAME_PROCESS_NAME}，请确认游戏已启动")
        self.refresh_status()

    def on_inject(self):
        def work():
            ok, msg = self.svc.ensure_injected()
            self.root.after(0, lambda: (self._log(msg), self.refresh_status()))

        threading.Thread(target=work, daemon=True).start()

    def on_probe(self):
        def work():
            ok, res = self.svc.execute_safe("return 1", timeout=4)
            self.root.after(0, lambda: self._log(f"通道自检: {'通过' if ok else '失败'} result={res}"))

        threading.Thread(target=work, daemon=True).start()

    def _auto_tick(self):
        self.root.after(3000, self._tick)

    def _tick(self):
        try:
            self.refresh_status()
        except Exception:
            pass
        self._auto_tick()


def main():
    root = tk.Tk()
    InjectorUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
