#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
woldvein_injector v0.4.7 — 主入口

    用法：
        python main.py            # 打开注入器状态面板（GUI）
        python main.py --cli      # 命令行：自动注入并常驻维持通道
        python main.py --cli --once   # 只做一次注入并打印结果后退出

    本产品不含任何玩法功能，玩法请见同版本的修改器 woldvein_trainer v0.4.8。
"""
import os
import sys
import time
import argparse

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from injector_api import InjectorService, VERSION, GAME_PROCESS_NAME  # noqa: E402


def _ensure_single_instance():
    """注入器互斥锁：两个注入器进程会互相抢通道文件。"""
    if os.name != "nt":
        return True
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW(None, False, f"woldvein_injector_mutex_v{VERSION.replace('.', '')}")
        return kernel32.GetLastError() != 183
    except Exception:
        return True


def run_cli(once=False):
    svc = InjectorService()
    print(f"[woldvein_injector v{VERSION}] 目标进程: {GAME_PROCESS_NAME}")

    ok, msg = svc.ensure_injected()
    print(f"[注入] {msg}")

    if not once:
        if ok:
            probe_ok, res = svc.execute_safe("return 1", timeout=4)
            print(f"[通道自检] {'通过' if probe_ok else '失败'} result={res}")
        print("[常驻] 按 Ctrl+C 退出")
        try:
            while True:
                time.sleep(2.0)
                if not svc.is_injected():
                    print("[告警] 通道丢失，尝试重新注入 ...")
                    svc.ensure_injected()
        except KeyboardInterrupt:
            print("\n[退出] 注入器已停止（DLL 仍驻留游戏进程）")
    else:
        if ok:
            probe_ok, res = svc.execute_safe("return 1", timeout=4)
            print(f"[通道自检] {'通过' if probe_ok else '失败'} result={res}")
    return 0 if ok else 1


def main():
    parser = argparse.ArgumentParser(description=f"woldvein_injector v{VERSION}")
    parser.add_argument("--cli", action="store_true", help="命令行模式（不弹面板）")
    parser.add_argument("--once", action="store_true", help="配合 --cli：注入一次即退出")
    args = parser.parse_args()

    if not _ensure_single_instance():
        print("[提示] 注入器已在运行，请勿重复启动。")
        return 0

    if args.cli:
        return run_cli(once=args.once)

    try:
        from injector_ui import main as ui_main
        ui_main()
        return 0
    except ImportError as e:
        print(f"[降级] 无法加载面板（{e}），改用命令行模式")
        return run_cli(once=True)


if __name__ == "__main__":
    sys.exit(main())
