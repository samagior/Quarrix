# PyInstaller: builds Quarrix.exe (bundled PyMuPDF engine).
# Usage: pyinstaller packaging/quarrix.spec
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

root = Path(SPECPATH).parent

a = Analysis(
    [str(root / "packaging" / "launch.py")],
    pathex=[str(root)],
    datas=collect_data_files("quarrix"),  # default_types.toml
    hiddenimports=["quarrix.types_dialog", "quarrix.engines.pymupdf_engine"],
    excludes=["docling", "docling_core", "torch", "transformers", "tkinter", "matplotlib"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name="Quarrix",
    console=False,
    upx=False,
)
