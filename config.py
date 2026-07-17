"""
config.py — Application-wide configuration constants for NeatPDF.

All hardcoded values live here. Import from this module; never hardcode
paths or magic numbers elsewhere.
"""

import sys as _sys
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────

ROOT_DIR: Path = Path(__file__).parent.resolve()
RESOURCES_DIR: Path = ROOT_DIR / "resources"
THEMES_DIR: Path = RESOURCES_DIR / "themes"
ICONS_DIR: Path = RESOURCES_DIR / "icons"

# When frozen (AppImage / installer / portable), write user data to
# ~/.local/share/NeatPDF on Linux or %APPDATA%\NeatPDF on Windows.
# When running from source, keep everything local to the project root.
def _user_data_dir() -> Path:
    if getattr(_sys, "frozen", False):
        import os as _os
        if _sys.platform == "win32":
            base = Path(_os.environ.get("APPDATA", Path.home()))
        else:
            base = Path(_os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        return base / "NeatPDF"
    return ROOT_DIR

_DATA_DIR: Path = _user_data_dir()
OUTPUT_DIR: Path = _DATA_DIR / "output"
LOG_DIR: Path = _DATA_DIR / "logs"

# ── Application metadata ──────────────────────────────────────────────────────

APP_NAME: str = "NeatPDF"
APP_VERSION: str = "0.1.0"
APP_ORG: str = "NeatPDF"
GITHUB_REPO: str = "Edudriven/NeatPDF"

# ── Install mode ──────────────────────────────────────────────────────────────
# Overridden at build time by PyInstaller via --add-data or spec file.
# Values: "portable" | "installer" | "appimage" | "source"

def _detect_install_mode() -> str:
    # When frozen by PyInstaller, read the sentinel file bundled at build time
    if getattr(_sys, "frozen", False):
        import os as _os
        sentinel = Path(getattr(_sys, "_MEIPASS", ".")) / "install_mode.txt"
        if sentinel.exists():
            return sentinel.read_text().strip()
        return "portable"
    return "source"

INSTALL_MODE: str = _detect_install_mode()

# ── UI defaults ───────────────────────────────────────────────────────────────

WINDOW_MIN_WIDTH: int = 480
WINDOW_MIN_HEIGHT: int = 400
WINDOW_DEFAULT_WIDTH: int = 1280
WINDOW_DEFAULT_HEIGHT: int = 720

THUMBNAIL_WIDTH: int = 140
THUMBNAIL_HEIGHT: int = 180
THUMBNAIL_DPI: float = 72.0

PREVIEW_DPI: float = 150.0

# ── Theme ─────────────────────────────────────────────────────────────────────

DEFAULT_THEME: str = "dark"   # "dark" | "light"

# ── PDF rendering ─────────────────────────────────────────────────────────────

MAX_THUMBNAIL_CACHE: int = 500   # pages kept in LRU thumbnail cache
RENDER_THREAD_COUNT: int = 4     # background threads for thumbnail rendering

# ── TOC ───────────────────────────────────────────────────────────────────────

TOC_MAX_LEVELS: int = 6          # maximum nesting depth
TOC_PAGE_FONT_SIZE: int = 11     # default font size for generated TOC pages

# ── Watermark detection ───────────────────────────────────────────────────────

WATERMARK_CONFIDENCE_THRESHOLD: float = 0.75   # auto-offer removal above this

# ── Logging ───────────────────────────────────────────────────────────────────

LOG_LEVEL: str = "DEBUG"
LOG_FORMAT: str = "%(asctime)s  %(levelname)-8s  %(name)s — %(message)s"
LOG_DATE_FORMAT: str = "%Y-%m-%d %H:%M:%S"
