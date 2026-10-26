"""PyInstaller configuration for the Windows desktop build."""

from pathlib import Path

from PyInstaller.building.build_main import Analysis, PYZ, EXE

ROOT = Path(SPECPATH)

analysis = Analysis(
    [str(ROOT / "desktop_entry.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[(str(ROOT / "idle_butler" / "ui" / "index.html"), "idle_butler/ui")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
archive = PYZ(analysis.pure)
EXE(
    archive,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="IdleButler",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)

