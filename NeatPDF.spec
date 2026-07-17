# NeatPDF.spec — PyInstaller build specification
#
# Usage:
#   Linux:   pyinstaller NeatPDF.spec
#   Windows: pyinstaller NeatPDF.spec
#
# The INSTALL_MODE variable is injected via the environment or
# overridden by the CI workflow before calling pyinstaller.

import os
import sys
from pathlib import Path

INSTALL_MODE = os.environ.get("NEATPDF_INSTALL_MODE", "portable")
ROOT = Path(SPEC).parent  # directory containing this .spec file

block_cipher = None

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        # Bundle all resources (themes, icons)
        (str(ROOT / "resources"), "resources"),
        # Inject install mode as a sentinel file read at runtime
        # (see config.py INSTALL_MODE logic)
    ],
    hiddenimports=[
        "PySide6.QtNetwork",
        "PySide6.QtPrintSupport",
        "fitz",          # PyMuPDF
        "pypdf",
        "cv2",
        "numpy",
        "PIL",
        "PIL.Image",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "scipy",
        "pandas",
        "IPython",
        "jupyter",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# ── Write install mode sentinel ───────────────────────────────────────────────
# We write a tiny file into the bundle so the app knows how it was packaged.
import tempfile, shutil

_sentinel = Path(tempfile.mkdtemp()) / "install_mode.txt"
_sentinel.write_text(INSTALL_MODE)
a.datas.append((str(_sentinel), "."))   # lands as install_mode.txt in root

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ── Platform-specific icon ────────────────────────────────────────────────────
if sys.platform == "win32":
    _icon = str(ROOT / "resources" / "icons" / "neatpdf.ico")
else:
    _icon = str(ROOT / "resources" / "icons" / "neatpdf_256.png")

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="NeatPDF",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # no terminal window on launch
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_icon,
    version_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="NeatPDF",
)
