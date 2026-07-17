"""
app.py — QApplication factory and theme management for NeatPDF.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QFont, QPalette, QColor
from PySide6.QtWidgets import QApplication

from config import APP_NAME, APP_ORG, APP_VERSION, DEFAULT_THEME, ICONS_DIR, THEMES_DIR

log = logging.getLogger(__name__)


def _load_stylesheet(theme: str) -> str:
    """Read and return the QSS stylesheet for the given theme name."""
    qss_path: Path = THEMES_DIR / f"{theme}.qss"
    if not qss_path.exists():
        log.warning("Theme file not found: %s — falling back to dark", qss_path)
        qss_path = THEMES_DIR / "dark.qss"
    return qss_path.read_text(encoding="utf-8")


def apply_theme(app: QApplication, theme: str) -> None:
    """Apply a named QSS theme to the application.

    Args:
        app: The running QApplication instance.
        theme: Theme name, e.g. ``"dark"`` or ``"light"``.
    """
    stylesheet = _load_stylesheet(theme)
    app.setStyleSheet(stylesheet)

    # Force the palette to dark/light base so native widgets follow suit.
    palette = QPalette()
    if theme == "dark":
        palette.setColor(QPalette.ColorRole.Window, QColor("#1E1E2E"))
        palette.setColor(QPalette.ColorRole.WindowText, QColor("#CDD6F4"))
        palette.setColor(QPalette.ColorRole.Base, QColor("#181825"))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#1E1E2E"))
        palette.setColor(QPalette.ColorRole.Text, QColor("#CDD6F4"))
        palette.setColor(QPalette.ColorRole.Button, QColor("#313244"))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor("#CDD6F4"))
        palette.setColor(QPalette.ColorRole.Highlight, QColor("#89B4FA"))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#1E1E2E"))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#313244"))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#CDD6F4"))
        palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#585B70"))
    else:
        palette.setColor(QPalette.ColorRole.Window, QColor("#EFF1F5"))
        palette.setColor(QPalette.ColorRole.WindowText, QColor("#4C4F69"))
        palette.setColor(QPalette.ColorRole.Base, QColor("#FFFFFF"))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#EFF1F5"))
        palette.setColor(QPalette.ColorRole.Text, QColor("#4C4F69"))
        palette.setColor(QPalette.ColorRole.Button, QColor("#E6E9EF"))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor("#4C4F69"))
        palette.setColor(QPalette.ColorRole.Highlight, QColor("#1E66F5"))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#E6E9EF"))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#4C4F69"))
        palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#9CA0B0"))

    app.setPalette(palette)
    log.info("Applied theme: %s", theme)


def create_app() -> tuple[QApplication, str]:
    """Create and configure the QApplication.

    Returns:
        A tuple of ``(QApplication instance, active theme name)``.
    """
    # High-DPI scaling handled automatically by Qt6.
    app = QApplication.instance() or QApplication([])
    assert isinstance(app, QApplication)

    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_ORG)
    app.setApplicationVersion(APP_VERSION)
    app.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps)

    # App icon
    from PySide6.QtGui import QIcon
    icon_path = ICONS_DIR / "neatpdf_256.png"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    # Base font
    font = QFont("Segoe UI", 10)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    app.setFont(font)

    # Restore saved theme or use default
    settings = QSettings()
    theme: str = settings.value("ui/theme", DEFAULT_THEME)  # type: ignore[assignment]

    apply_theme(app, theme)
    return app, theme
