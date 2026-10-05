# woldvein_injector v0.4.7

独立的**游戏进程注入与通道服务**。它不含任何玩法内容。

> 从 woldvein_trainer v0.4.6 拆分而来。注入/通信部分留在 v0.4.7，
> 玩法部分独立为 woldvein_trainer v0.4.8。

## 它做什么 / 不做什么

**做：**
- 找到目标进程（白名单校验）
- 把通道 DLL 注入到游戏进程（CreateRemoteThread + LoadLibraryW，幂等）
- 维持一条双向通道（命名管道优先，`lua_cmd.txt` 文件轮询自动回退）
- 提供统一的 `execute()` 接口给调用方投递脚本

**不做（一律归 v0.4.8 修改器）：**
- 加资源、改幸福度、创造模式、世界工具、热键、UI 主题
- 认识 `g_camp` / `g_CityManager` 等任何游戏对象

## 运行

```
python main.py                # 打开注入器状态面板
python main.py --cli          # 命令行：自动注入并常驻维持通道
python main.py --cli --once   # 注入一次，自检后退出
```

## 目录

| 路径 | 说明 |
|---|---|
| `injector_api.py` | **对外契约**，唯一被外部依赖的入口 |
| `injector_ui.py` | 注入器状态面板（tkinter） |
| `main.py` | 入口（GUI / CLI） |
| `injector_core/` | 内核实现，包名为 `injector_core`，不叫 `src` |
| `injector_core/injector/` | 注入器 + `trainer.c` 源码 + 通道 DLL |
| `injector_core/transport.py` | 双通道传输（管道 + 文件轮询）、请求 ID、双重锁 |
| `dist/woldvein_trainer.dll` | 编译好的通道 DLL |
| `woldvein_injector.spec` | PyInstaller 打包配置 |

## 契约（给修改器用）

```python
import sys
sys.path.insert(0, r"E:/.../woldvein_injector0.4.7")
from injector_api import InjectorService

svc = InjectorService()
svc.ensure_injected()          # -> (success, message)
ok, res = svc.execute("return 1")
```

`execute()` 返回约定：

| 返回 | 含义 |
|---|---|
| `(True, 1)` | Lua 返回真值 |
| `(False, 0)` | Lua 返回假值 |
| `(True, "字符串")` | Lua 返回字符串（如 JSON） |
| `(False, -1)` | 失败或超时 |

## 为什么内部包叫 `injector_core` 而不是 `src`

两个拆分产品原本顶层包都叫 `src`。同进程加载时（修改器 import 注入器），
Python 只认一个 `src`，注入器内部的 `from src.injector import get_dll_path`
会被解析到**修改器**的同名 shim，立刻无限递归。

改名后两者彻底隔离 —— 这是本次拆分踩到的最大一个坑，投产前别改回去。

## 依赖

零第三方依赖。`psutil` 可用时优先用；不可用时自动降级到 Toolhelp32 原生枚举
（见 `injector_core/injector/__init__.py` 的 `_toolhelp_processes`）。
