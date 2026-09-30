# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 用ビルド設定。

comtypes は UIAutomationCore.dll から生成したラッパーモジュールを
実行時に import するので、hiddenimports に明示しておく。
"""

hiddenimports = [
    "comtypes",
    "comtypes.client",
    "comtypes.stream",
    "comtypes.gen",
    "comtypes.gen.UIAutomationClient",
    "comtypes.gen._944DE083_8FB8_45CF_BCB7_C477ACB2F897_0_1_0",
    "comtypes.gen.stdole",
    "comtypes.gen._00020430_0000_0000_C000_000000000046_0_2_0",
]

a = Analysis(
    ["KiroAutoAllow.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "numpy", "pandas", "matplotlib", "PIL", "scipy",
        "pytest", "setuptools", "pip",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="KiroAutoAllow",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,          # コンソール窓を出さない
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
