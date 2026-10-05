"""
woldvein_injector v0.4.7 — 单元测试

只测试注入器自己拥有的模块。玩法模块（operation_history / hotkey_* / transaction /
i18n 等）已随拆分移交给修改器 v0.4.8，相关测试也一并移除，不要再加回来。

运行方式：
    python tests/test_core.py            # 直接用 stdlib unittest，不需要 pytest
    python -m unittest discover tests    # 等价
"""
import os
import sys
import json
import shutil
import tempfile
import unittest

# 项目根目录入路径，保证 injector_core / injector_api 可导入
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


class TestInputValidator(unittest.TestCase):
    def setUp(self):
        from injector_core.input_validator import validate_int, clamp_int
        self.validate_int = validate_int
        self.clamp_int = clamp_int

    def test_normal_int(self):
        v, ok, msg = self.validate_int("42", 0, 100)
        self.assertTrue(ok, msg)
        self.assertEqual(v, 42)

    def test_out_of_range(self):
        _, ok, _ = self.validate_int("500", 0, 100)
        self.assertFalse(ok)

    def test_empty(self):
        _, ok, _ = self.validate_int("")
        self.assertFalse(ok)

    def test_none(self):
        _, ok, _ = self.validate_int(None)
        self.assertFalse(ok)

    def test_float_string_accepted(self):
        v, ok, _ = self.validate_int("100.0", 0, 1000)
        self.assertTrue(ok)
        self.assertEqual(v, 100)

    def test_clamp(self):
        self.assertEqual(self.clamp_int(500, 0, 100), 100)
        self.assertEqual(self.clamp_int(-5, 0, 100), 0)
        self.assertEqual(self.clamp_int(50, 0, 100), 50)


class TestAtomicFile(unittest.TestCase):
    def setUp(self):
        from injector_core.atomic_file import atomic_write_text, atomic_write_json
        self.atomic_write_text = atomic_write_text
        self.atomic_write_json = atomic_write_json
        self.tmp = tempfile.mkdtemp(prefix="wv_injector_test_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_write_text(self):
        p = os.path.join(self.tmp, "a.txt")
        self.atomic_write_text(p, "hello 注入器")
        self.assertEqual(open(p, encoding="utf-8").read(), "hello 注入器")

    def test_no_tmp_leftover(self):
        p = os.path.join(self.tmp, "b.txt")
        self.atomic_write_text(p, "x")
        leftovers = [f for f in os.listdir(self.tmp) if f.endswith(".tmp")]
        self.assertEqual(leftovers, [], "原子写不应留下 .tmp 残留")

    def test_write_json_roundtrip(self):
        p = os.path.join(self.tmp, "c.json")
        data = {"version": "0.4.7", "ok": True}
        self.atomic_write_json(p, data)
        self.assertEqual(json.load(open(p, encoding="utf-8")), data)


class TestEmergencyStop(unittest.TestCase):
    def setUp(self):
        from injector_core.emergency_stop import EmergencyStop
        self.EmergencyStop = EmergencyStop

    def test_trigger_and_reset(self):
        es = self.EmergencyStop()
        es.reset()
        self.assertFalse(es.is_triggered())
        es.trigger(reason="单元测试")
        self.assertTrue(es.is_triggered())
        es.reset()
        self.assertFalse(es.is_triggered())

    def test_status_has_triggered_flag(self):
        es = self.EmergencyStop()
        es.trigger(reason="单元测试")
        st = es.get_status()
        self.assertIsInstance(st, dict)
        self.assertTrue(st.get("triggered"))
        es.reset()


class TestInjectorProcessGuard(unittest.TestCase):
    """注入安全边界：只允许注入白名单进程，绝不许乱打其它进程。"""

    def setUp(self):
        from injector_core.injector import (
            validate_process_pid, PROCESS_WHITELIST, GAME_PROCESS_NAME
        )
        self.validate = validate_process_pid
        self.whitelist = PROCESS_WHITELIST
        self.game_name = GAME_PROCESS_NAME

    def test_whitelist_is_single_game(self):
        self.assertEqual(self.whitelist, ["balladsofhongye.exe"])
        self.assertEqual(self.game_name, "BalladsOfHongye.exe")

    def test_nonexistent_pid_rejected(self):
        ok, name, msg = self.validate(999999)
        self.assertFalse(ok)
        self.assertIn("不存在", msg)

    def test_current_python_process_rejected(self):
        """当前解释器进程绝不在白名单内 —— 这是防误注入的关键断言。"""
        ok, name, msg = self.validate(os.getpid())
        self.assertFalse(ok)
        self.assertIsNotNone(name)


class TestTransport(unittest.TestCase):
    """传输层的纯逻辑（不真正连游戏）。"""

    def setUp(self):
        self.transport = __import__("injector_core.transport", fromlist=["*"])

    def test_comm_dir_is_fixed(self):
        """通信目录必须是固定路径，否则 PyInstaller 重启后旧 DLL 找不到通道。"""
        d = self.transport._get_comm_dir()
        self.assertTrue(os.path.isabs(d))
        self.assertTrue(d.endswith("woldvein_trainer"), d)

    def test_cmd_file_under_comm_dir(self):
        self.assertEqual(
            os.path.dirname(self.transport.CMD_FILE),
            self.transport.COMM_DIR,
        )

    def test_atomic_cmd_write(self):
        orig = self.transport.CMD_FILE
        try:
            tmpdir = tempfile.mkdtemp(prefix="wv_cmd_")
            self.transport.CMD_FILE = os.path.join(tmpdir, "lua_cmd.txt")
            self.transport._write_cmd_atomic("REQ_ID:abcd1234\nreturn 1")
            with open(self.transport.CMD_FILE, "rb") as f:
                raw = f.read()
            self.assertTrue(raw.startswith(b"REQ_ID:abcd1234\n"))
            self.assertIn(b"return 1", raw)
            shutil.rmtree(tmpdir, ignore_errors=True)
        finally:
            self.transport.CMD_FILE = orig

    def test_pipe_name(self):
        self.assertEqual(self.transport.PIPE_NAME, r"\\.\pipe\woldvein_trainer")


class TestInjectorApiContract(unittest.TestCase):
    """对外契约必须稳定 —— 修改器 v0.4.8 依赖这里。"""

    def setUp(self):
        import injector_api
        self.api = injector_api

    def test_version(self):
        self.assertEqual(self.api.VERSION, "0.4.7")
        self.assertEqual(self.api.PROTOCOL_VERSION, "1.0")

    def test_service_status_shape(self):
        svc = self.api.InjectorService()
        st = svc.status()
        for key in ("product", "version", "dll_path", "dll_exists", "game_pid", "injected"):
            self.assertIn(key, st)
        self.assertEqual(st["version"], "0.4.7")

    def test_execute_returns_tuple(self):
        """未注入时 execute 必须返回 (bool, int|str) 而不是抛异常。"""
        svc = self.api.InjectorService()
        ok, res = svc.execute_safe("return 1", timeout=1)
        self.assertIsInstance(ok, bool)
        self.assertIsInstance(res, (int, str))

    def test_dll_exists(self):
        svc = self.api.InjectorService()
        self.assertTrue(os.path.exists(svc.dll_path), f"通道 DLL 缺失: {svc.dll_path}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
