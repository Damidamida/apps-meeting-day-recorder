# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


ROOT = Path(SPECPATH).resolve().parents[1]
APP_NAME = "BK Scribe"
ICON_PATH = ROOT / "app" / "assets" / "bk_scribe.ico"
FFMPEG_DIR = ROOT / "packaging" / "ffmpeg" / "bin"

datas = [
    (str(ROOT / "app" / "assets"), "app/assets"),
    (str(ROOT / "config.yaml.example"), "."),
]

if FFMPEG_DIR.is_dir():
    datas.append(Tree(str(FFMPEG_DIR), prefix="resources/ffmpeg"))

a = Analysis(
    [str(ROOT / "app" / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='BK Scribe',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON_PATH),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name=APP_NAME,
)
