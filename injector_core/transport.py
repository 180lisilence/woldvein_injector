"""
woldvein Trainer v0.3 - Lua 执行引擎

功能说明：
    通过命令文件机制与注入游戏进程的DLL通信，在游戏主线程执行Lua代码。
    Python端写入命令文件(lua_cmd.txt)，DLL轮询检测并执行，结果写入
    lua_result.txt，Python端读取结果。

通信机制：
    1. Python端清理旧的命令/结果文件
    2. Python端写入Lua代码到lua_cmd.txt
    3. DLL在游戏主线程轮询检测lua_cmd.txt，读取并执行Lua代码
    4. DLL将执行结果写入lua_result.txt
    5. Python端轮询检测lua_result.txt，读取结果
    6. Python端清理结果文件

核心函数：
    execute_lua(code, timeout)      执行Lua代码，返回(success, result)
    execute_lua_safe(code, timeout) 安全执行（捕获所有异常）

Lua脚本模板：
    LUA_ADD_RESOURCE       增加资源
    LUA_SET_RESOURCE       设置资源
    LUA_MAX_HAPPINESS      幸福度最大
    LUA_ADD_FAME           增加知名度
    LUA_CREATIVE_ENABLE    开启创造模式
    LUA_CREATIVE_DISABLE   关闭创造模式
    LUA_GET_STATUS         获取游戏状态（JSON字符串）

技术要点：
    - 线程锁：_lock防止多线程并发写命令文件
    - 超时处理：默认5秒，创造模式10秒
    - 结果解析：优先解析为整数，失败则返回原始字符串（支持JSON）
    - 超时清理：同时清理命令文件和结果文件，防止残留干扰
    - 通信文件：使用%LOCALAPPDATA%\\woldvein_trainer\\固定路径（避免_MEIPASS每次启动不同）
"""
import os
import time
import uuid
import threading

from .logger import log, log_success, log_error, log_warning
from .constants import LUA_TIMEOUT

# ========== 命名管道通信（v0.4.6 新增，优先于文件轮询） ==========
# 使用 ctypes 直接调用 Windows API，不依赖 pywin32，PyInstaller 打包更干净
import ctypes
from ctypes import wintypes

PIPE_NAME = r"\\.\pipe\woldvein_trainer"
_pipe_handle = None
_pipe_lock = threading.Lock()
_pipe_available = False  # 管道是否已连接成功（缓存状态，避免每次重连）

# Windows API 常量
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
PIPE_READMODE_MESSAGE = 0x00000002
ERROR_PIPE_BUSY = 231
ERROR_PIPE_LISTENING = 536
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateFileW.restype = wintypes.HANDLE
_kernel32.CreateFileW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
    wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE
]
_kernel32.WriteFile.restype = wintypes.BOOL
_kernel32.WriteFile.argtypes = [
    wintypes.HANDLE, wintypes.LPCVOID, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID
]
_kernel32.ReadFile.restype = wintypes.BOOL
_kernel32.ReadFile.argtypes = [
    wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID
]
_kernel32.SetNamedPipeHandleState.restype = wintypes.BOOL
_kernel32.SetNamedPipeHandleState.argtypes = [
    wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD)
]
_kernel32.CloseHandle.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


def _connect_pipe(timeout=3.0):
    """连接命名管道。成功返回True，失败返回False（调用方回退文件轮询）。"""
    global _pipe_handle, _pipe_available
    if _pipe_handle is not None and _pipe_available:
        return True

    deadline = time.time() + timeout
    while time.time() < deadline:
        handle = _kernel32.CreateFileW(
            PIPE_NAME,
            GENERIC_READ | GENERIC_WRITE,
            0,  # 不共享
            None,
            OPEN_EXISTING,
            0,
            None
        )
        if handle != INVALID_HANDLE_VALUE and handle is not None:
            # 设置为消息读取模式
            mode = wintypes.DWORD(PIPE_READMODE_MESSAGE)
            if _kernel32.SetNamedPipeHandleState(handle, ctypes.byref(mode), None, None):
                _pipe_handle = handle
                _pipe_available = True
                log_success(f"[Pipe] 命名管道连接成功: {PIPE_NAME}")
                return True
            else:
                _kernel32.CloseHandle(handle)
        # 管道忙或不存在，等一下重试
        err = ctypes.get_last_error()
        if err == ERROR_PIPE_BUSY:
            time.sleep(0.1)
        else:
            time.sleep(0.2)
    log_warning("[Pipe] 连接超时，回退文件轮询")
    return False


def _close_pipe():
    """关闭管道连接（出错时调用，下次重连）"""
    global _pipe_handle, _pipe_available
    if _pipe_handle is not None:
        try:
            _kernel32.CloseHandle(_pipe_handle)
        except Exception:
            pass
    _pipe_handle = None
    _pipe_available = False


def _pipe_execute(code, timeout=LUA_TIMEOUT):
    """通过命名管道执行Lua代码。返回(success, result)，失败返回None（调用方回退）。

    协议与文件轮询完全一致：
    - 发送：REQ_ID:xxxxxxxx\n + Lua代码
    - 接收：REQ_ID:xxxxxxxx\n + 结果
    """
    global _pipe_handle, _pipe_available
    if _pipe_handle is None or not _pipe_available:
        return None

    req_id = uuid.uuid4().hex[:8]
    payload = f"REQ_ID:{req_id}\n{code}".encode("utf-8")

    try:
        # 发送命令
        written = wintypes.DWORD(0)
        ok = _kernel32.WriteFile(
            _pipe_handle, payload, len(payload), ctypes.byref(written), None
        )
        if not ok:
            raise OSError(f"WriteFile failed: {ctypes.get_last_error()}")

        # 读取结果（消息模式，一次ReadFile读完整条消息）
        buf = ctypes.create_string_buffer(65536)
        start = time.time()
        while time.time() - start < timeout:
            read = wintypes.DWORD(0)
            ok = _kernel32.ReadFile(
                _pipe_handle, buf, 65536, ctypes.byref(read), None
            )
            if ok and read.value > 0:
                data = buf.raw[:read.value]
                break
            err = ctypes.get_last_error()
            if err == ERROR_PIPE_LISTENING:
                time.sleep(0.02)
                continue
            # 其他错误 = 管道断开
            raise OSError(f"ReadFile failed: {err}")
        else:
            log_warning("[Pipe] 读取超时")
            _close_pipe()
            return None

        # 逐行解码（UTF-8/GBK混合，与文件轮询逻辑一致）
        _decoded_lines = []
        for _bl in data.split(b"\n"):
            try:
                _decoded_lines.append(_bl.decode("utf-8"))
            except UnicodeDecodeError:
                _decoded_lines.append(_bl.decode("gbk", errors="replace"))
        raw = "\n".join(_decoded_lines)

        lines = raw.split("\n", 1)
        result_id_line = lines[0].strip()
        result_str = lines[1].strip() if len(lines) > 1 else ""

        # ID不匹配 = 串号，返回None让调用方重试/回退
        if not result_id_line.startswith("REQ_ID:") or result_id_line[7:] != req_id:
            log_warning(f"[Pipe] 请求ID不匹配，期望:{req_id} 实际:{result_id_line[:20]}")
            return None

        if result_str == "":
            return False, -1

        try:
            result = int(result_str)
            return (result > 0), result
        except ValueError:
            return True, result_str

    except Exception as e:
        log_warning(f"[Pipe] 通信异常: {e}，断开管道回退文件轮询")
        _close_pipe()
        return None

# 通信文件路径：使用固定的 %LOCALAPPDATA%\woldvein_trainer\ 目录
# 原因：PyInstaller onefile模式下sys._MEIPASS每次启动都不同，
# DLL注入后驻留游戏进程，重启修改器后旧DLL仍轮询旧_MEIPASS路径，
# 导致第二次之后所有Lua执行超时。改用固定路径解决此问题。
import tempfile
def _get_comm_dir():
    """获取通信文件目录（固定路径，重启修改器后不变）"""
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    d = os.path.join(base, "woldvein_trainer")
    os.makedirs(d, exist_ok=True)
    return d
COMM_DIR = _get_comm_dir()
CMD_FILE = os.path.join(COMM_DIR, "lua_cmd.txt")
RESULT_FILE = os.path.join(COMM_DIR, "lua_result.txt")
# 跨进程通道锁文件（见 _ipc_lock_acquire）：防止多个修改器实例同时写同一份命令文件
LOCK_FILE = os.path.join(COMM_DIR, "lua_channel.lock")

_lock = threading.RLock()


def _execute_lua_inner(code, timeout=LUA_TIMEOUT):
    """
    执行Lua代码，通过命令文件机制与DLL通信。

    参数：
        code: 要执行的Lua代码字符串
        timeout: 超时时间（秒），默认5秒

    返回：
        (success, result) 元组
        - success: True表示执行成功，False表示失败或超时
        - result: 执行结果
            * 整数：Lua返回的布尔值（true→1, false→0）或数字
            * 字符串：Lua返回的字符串（如JSON状态）
            * -1：执行失败

    处理流程：
        1. 生成唯一请求ID（防竞态）
        2. 加锁，清理旧的命令/结果文件
        3. 写入"请求ID + \n + Lua代码"到lua_cmd.txt
        4. 轮询等待lua_result.txt出现，且第一行ID匹配
        5. 读取结果，解析为整数或字符串
        6. 清理结果文件
        7. 超时则清理两个文件，返回失败

    线程安全：
        使用_lock全局锁，防止多线程并发写命令文件导致冲突。
        同时使用请求ID机制，防止超时后新旧结果串号。

    竞态防护（请求ID机制）：
        问题场景：A请求超时释放锁 → B请求写入新命令 → DLL写完A的结果 → B读到A的结果
        解决方案：每次请求带唯一ID，DLL回写时带上相同ID，
                  读取方校验ID，不匹配则跳过继续等（说明是上一个请求的残留）。
    """
    # 生成唯一请求ID（短UUID前8位，足够区分并发请求）
    req_id = uuid.uuid4().hex[:8]
    req_prefix = f"REQ_ID:{req_id}\n"

    # v0.4.6：优先尝试命名管道通信（延迟更低，无文件IO）
    # 管道失败时自动回退到文件轮询，保证兼容性
    with _pipe_lock:
        if _connect_pipe(timeout=2.0):
            pipe_result = _pipe_execute(code, timeout)
            if pipe_result is not None:
                return pipe_result
            # 管道返回None = 通信失败，继续回退文件轮询
            log_warning("[Pipe] 管道执行失败，回退文件轮询")

    with _lock:
        # 清理旧文件
        for f in [CMD_FILE, RESULT_FILE]:
            try:
                if os.path.exists(f):
                    os.remove(f)
            except Exception:
                pass  # 清理失败不影响执行，静默容错

        # 写入命令文件（格式：REQ_ID:xxxxxxxx\n + Lua代码）
        # 原子写入（.tmp + os.replace）：open("w") 会先截断再分片写入，
        # 若同时有第二个实例在写同一份文件，DLL 可能读到「A的请求ID + B的代码」的拼接内容，
        # 表现就是「命令返回了别的命令的结果」（实机事故 2026-09-14：面板注入/品阶升级假失败）。
        try:
            _write_cmd_atomic(req_prefix + code)
        except Exception as e:
            log_error(f"写入命令文件失败: {e}")
            return False, -1

        # 等待结果（校验请求ID，防止串号）
        start = time.time()
        mismatch_skips = 0  # 统计ID不匹配跳过次数（用于诊断）
        while time.time() - start < timeout:
            if os.path.exists(RESULT_FILE):
                try:
                    with open(RESULT_FILE, "rb") as f:
                        _raw_bytes = f.read()
                    # 逐行解码：
                    # 同一份结果里既有我们的 UTF-8 字面量，也有游戏返回的 GBK 字符串。
                    # 若按整段回退（先试 utf-8，失败则整段 gbk），
                    # 会把正常的字面量也变成乱码（如“城市管理器”变成“鍩庡競绠”）。
                    # 逐行判断可两者兼顾。
                    _decoded_lines = []
                    for _bl in _raw_bytes.split(b"\n"):
                        try:
                            _decoded_lines.append(_bl.decode("utf-8"))
                        except UnicodeDecodeError:
                            # 该行是游戏内的 GBK 字符串，单独回退解码
                            _decoded_lines.append(_bl.decode("gbk", errors="replace"))
                    raw = "\n".join(_decoded_lines)
                    # 解析：第一行是请求ID，剩余是结果
                    lines = raw.split("\n", 1)
                    result_id_line = lines[0].strip()
                    result_str = lines[1].strip() if len(lines) > 1 else ""

                    # ID不匹配 = 读到了上一个请求的残留结果，跳过继续等
                    if not result_id_line.startswith("REQ_ID:") or result_id_line[7:] != req_id:
                        mismatch_skips += 1
                        if mismatch_skips <= 3:
                            log_warning(
                                f"[竞态防护] 请求ID不匹配，跳过。"
                                f"期望:{req_id} 实际:{result_id_line[:20]} "
                                f"跳过次数:{mismatch_skips}"
                            )
                        time.sleep(0.05)
                        continue

                    # ID匹配成功，清理结果文件
                    try:
                        os.remove(RESULT_FILE)
                    except Exception:
                        pass  # 清理失败不影响结果返回

                    # 空结果诊断
                    if result_str == "":
                        cmd_exists = os.path.exists(CMD_FILE)
                        cmd_size = 0
                        if cmd_exists:
                            try:
                                cmd_size = os.path.getsize(CMD_FILE)
                            except Exception:
                                pass
                        log_warning(f"[Lua调试] 返回空结果。命令文件是否存在: {cmd_exists}, 大小: {cmd_size}B")
                        return False, -1
                    # 尝试解析为整数，失败则返回原始字符串
                    # 约定：Lua 脚本返回 1=成功, 0=失败（非负数判断会把 0 误判为成功）
                    try:
                        result = int(result_str)
                        return (result > 0), result
                    except ValueError:
                        # 字符串结果（如JSON）
                        return True, result_str
                except Exception as e:
                    log_error(f"读取结果失败: {e}")
                    return False, -1
            time.sleep(0.05)

        log_warning(f"Lua执行超时 ({timeout}s)")
        # 超时后清理命令文件和结果文件，防止残留干扰
        for f in [CMD_FILE, RESULT_FILE]:
            try:
                if os.path.exists(f):
                    os.remove(f)
            except Exception:
                pass  # 超时清理失败不影响返回结果
        return False, -1


def _write_cmd_atomic(payload):
    """原子写入命令文件：先写 .tmp 再 os.replace（同分卷原子）。

    为什么必须原子：直接 open(CMD_FILE,"w") 会先截断再分片写入（Python 文本层默认 8KB 缓冲），
    如果同时存在第二个修改器实例在写同一份文件，DLL 可能读到「A 的请求ID + B 的代码」这种
    拼接内容，于是「某个命令返回了另一个命令的结果」→ 面板注入 / 品阶升级全部假失败。
    """
    tmp = CMD_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(payload)
        f.flush()
        try:
            os.fsync(f.fileno())
        except Exception:
            pass
    os.replace(tmp, CMD_FILE)


_ipc_handle = None


def _ipc_lock_acquire(timeout=20.0):
    """获取跨进程通道锁（Windows: msvcrt 文件锁）。失败返回 False（仍会继续，仅告警）。

    进程内的 _lock 管不住另一个修改器进程，必须用系统级文件锁，
    否则两个实例的后台状态轮询会互相覆盖 lua_cmd.txt。
    """
    global _ipc_handle
    try:
        import msvcrt
    except ImportError:
        return True  # 非 Windows：退化为仅进程内锁
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            h = open(LOCK_FILE, "a+b")
        except Exception:
            return True
        try:
            msvcrt.locking(h.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            try:
                h.close()
            except Exception:
                pass
            time.sleep(0.02)
            continue
        _ipc_handle = h
        return True
    return False


def _ipc_lock_release():
    """释放跨进程通道锁"""
    global _ipc_handle
    h = _ipc_handle
    _ipc_handle = None
    if h is None:
        return
    try:
        import msvcrt
        h.seek(0)
        msvcrt.locking(h.fileno(), msvcrt.LK_UNLCK, 1)
    except Exception:
        pass
    try:
        h.close()
    except Exception:
        pass


def execute_lua(code, timeout=LUA_TIMEOUT):
    """执行Lua代码（进程内锁 + 跨进程通道锁）。

    具体协议、返回值约定见 _execute_lua_inner 的文档字符串。
    """
    with _lock:
        got = _ipc_lock_acquire()
        try:
            return _execute_lua_inner(code, timeout)
        finally:
            if got:
                _ipc_lock_release()


def execute_lua_retry(code, timeout=LUA_TIMEOUT, attempts=3, tag="", expect=None):
    """关键操作重试包装：拿不到有效结果时自动重发（每次都是全新请求ID）。

    expect: 期望的结果前缀元组；若结果是字符串但不以这些前缀开头，
            说明拿到的可能是别的命令的残留结果（串号），同样重试。
    """
    if expect is None:
        expect = ("[成功]", "[失败]", "[错误]", "[提示]", "[警告]")
    expect = tuple(expect)
    last = (False, -1)
    for i in range(1, max(1, int(attempts)) + 1):
        success, result = execute_lua_safe(code, timeout)
        last = (success, result)
        ok = bool(success)
        if ok and isinstance(result, str):
            ok = result.strip().startswith(expect)
        if ok:
            return success, result
        if i < attempts:
            log_warning(f"[重试] {tag or '关键操作'} 第 {i} 次未拿到有效结果，重发请求 ...")
            time.sleep(0.3)
    return last

def execute_lua_safe(code, timeout=LUA_TIMEOUT):
    """
    安全执行Lua代码，捕获所有异常。

    参数：
        code: 要执行的Lua代码字符串
        timeout: 超时时间（秒），默认5秒

    返回：
        (success, result) 元组，异常时返回(False, -1)

    说明：
        对execute_lua的包装，捕获所有异常并记录日志，
        确保调用方不会因为Lua执行异常而崩溃。
    """
    try:
        return execute_lua(code, timeout)
    except Exception as e:
        log_error(f"Lua执行异常: {e}")
        return False, -1

