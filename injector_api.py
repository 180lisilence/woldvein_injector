"""
woldvein_injector v0.4.7 — 对外契约（唯一被外部依赖的入口）

职责边界（严格）：
    本产品只做三件事：找到游戏进程、把通道 DLL 注入进去、提供一条可靠的双向通道。
    它不知道任何玩法内容：没有"加资源"" creative 模式""世界工具"，
    也不认识 g_camp / g_CityManager 这些游戏对象。这些一律归修改器 v0.4.8。

外部使用方式（二选一）：
    1) 库调用（默认，零部署成本）：
         injector_home = r"E:/.../woldvein_injector0.4.7"
         sys.path.insert(0, injector_home)
         from injector_api import InjectorService
         svc = InjectorService()
         svc.ensure_injected()
         ok, res = svc.execute("return 1")

    2) 独立运行：
         python main.py            # 带托盘式状态面板
         python main.py --cli      # 纯命令行，打印状态后常驻

版本契约：
    execute() 的返回值约定与原 lua_engine 完全一致：(success, result)
    - result 为整数：Lua 返回布尔/数字，success = (result > 0)
    - result 为字符串：Lua 返回字符串（如 JSON）
    - -1：执行失败或超时
"""
import os
import sys

PRODUCT_NAME = "woldvein_injector"
VERSION = "0.4.7"
PROTOCOL_VERSION = "1.0"

# 通道 DLL 名称（修改器侧若自带旧 dll 会被拒绝，必须以本产品 payload 为准）
DLL_NAME = "woldvein_trainer.dll"


def _bootstrap():
    """把本产品根目录塞进 sys.path，保证 `src.xxx` 可被导入。"""
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)


_bootstrap()

from injector_core.injector import (  # noqa: E402
    find_game_process,
    inject_dll,
    is_dll_injected,
    launch_game,
    get_dll_path,
    get_process_modules,
    GAME_PROCESS_NAME,
    PROCESS_WHITELIST,
)
from injector_core.transport import execute_lua, execute_lua_safe, PIPE_NAME, COMM_DIR  # noqa: E402
from injector_core.logger import log, log_success, log_error, log_warning  # noqa: E402


class InjectorService:
    """注入器对外服务。所有方法都不含玩法语义。"""

    def __init__(self, dll_path=None, auto_inject=True):
        self.dll_path = dll_path or get_dll_path()
        self.auto_inject = auto_inject
        self.game_pid = None
        self._injected = False

    # ---------- 进程 ----------
    def find_game_process(self):
        """返回 (pid, process) 或 (None, None)。"""
        pid, proc = find_game_process()
        if pid:
            self.game_pid = pid
        return pid, proc

    def is_injected(self, pid=None):
        pid = pid or self.game_pid
        if not pid:
            return False
        ok = is_dll_injected(pid, DLL_NAME)
        self._injected = bool(ok)
        return self._injected

    # ---------- 注入 ----------
    def ensure_injected(self, pid=None, dll_path=None):
        """一次性完成"找进程 → 注入 → 校验"。返回 (success, message)。

        幂等：已注入则直接返回成功，不会重复注入。
        """
        if pid is None:
            pid, _ = self.find_game_process()
        if not pid:
            return False, f"未找到游戏进程 {GAME_PROCESS_NAME}"

        self.game_pid = pid
        dll = dll_path or self.dll_path
        if not os.path.exists(dll):
            return False, f"通道 DLL 不存在: {dll}"

        if is_dll_injected(pid, os.path.basename(dll)):
            self._injected = True
            return True, f"通道已就绪: {os.path.basename(dll)}"

        ok, msg = inject_dll(pid, dll)
        self._injected = bool(ok)
        return ok, msg

    # ---------- 通道 ----------
    def execute(self, code, timeout=None):
        """向游戏主线程投递一段脚本并取回结果。返回 (success, result)。

        注意：本产品不校验 code 的内容 —— 传什么是调用方的事。
        """
        from injector_core.constants import LUA_TIMEOUT
        return execute_lua(code, timeout or LUA_TIMEOUT)

    def execute_safe(self, code, timeout=None):
        from injector_core.constants import LUA_TIMEOUT
        return execute_lua_safe(code, timeout or LUA_TIMEOUT)

    # ---------- 生命周期 ----------
    def close(self):
        """释放资源。DLL 本身保留在游戏进程内（卸载由 emergency_stop 负责）。"""
        self._injected = False

    # ---------- 元信息 ----------
    @staticmethod
    def version():
        return VERSION

    @staticmethod
    def protocol():
        return PROTOCOL_VERSION

    def status(self):
        """返回可序列化的状态字典，供修改器或 CLI 展示。"""
        return {
            "product": PRODUCT_NAME,
            "version": VERSION,
            "protocol": PROTOCOL_VERSION,
            "dll_path": self.dll_path,
            "dll_exists": os.path.exists(self.dll_path),
            "game_pid": self.game_pid,
            "injected": self._injected,
            "pipe": PIPE_NAME,
            "comm_dir": COMM_DIR,
        }


__all__ = [
    "InjectorService", "VERSION", "PROTOCOL_VERSION", "PRODUCT_NAME", "DLL_NAME",
    "find_game_process", "inject_dll", "is_dll_injected", "launch_game",
    "get_dll_path", "get_process_modules", "GAME_PROCESS_NAME", "PROCESS_WHITELIST",
]
