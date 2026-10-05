# -*- mode: python ; coding: utf-8 -*-
"""woldvein_injector v0.4.7 打包配置

注意：与本目录下的旧 woldvein_trainer.spec 无关 —— 那是修改器的。
这里只打包注入器：入口 main.py + UI + injector_core + 通道 DLL。
"""
import os

block_cipher = None

PROJECT_DIR = os.path.abspath(SPECPATH) if 'SPECPATH' in globals() else os.path.dirname(os.path.abspath(__file__))

a = Analysis(
    ['main.py'],
    pathex=[PROJECT_DIR],
    binaries=[],
    datas=[
        ('dist/woldvein_trainer.dll', 'dist'),
        ('config.json', '.'),
        # locales 必须随包：i18n 在 frozen 下从 sys._MEIPASS 读语言文件，
        # 少这一项界面文案会回退成 key
        ('locales', 'locales'),
    ],
    hiddenimports=[
        'injector_api',
        'injector_ui',
        'injector_core.constants',
        'injector_core.config',
        'injector_core.logger',
        'injector_core.transport',
        'injector_core.injector',
        'injector_core.process_handle_cache',
        'injector_core.aob_scanner',
        'injector_core.address_validator',
        'injector_core.input_validator',
        'injector_core.atomic_file',
        'injector_core.diagnostic',
        'injector_core.crash_report',
        'injector_core.emergency_stop',
        'injector_core.overlay_controller',
        'injector_core.log_enhancer',
        'tkinter',
        'tkinter.ttk',
        'tkinter.scrolledtext',
        'tkinter.messagebox',
        'tkinter.simpledialog',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='woldvein_injector_v0.4.7',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
