"""
gui/logo_utils.py — Shared logo/icon rendering helpers.

Provides theme-aware pixmap loading so the NeatPDF logo is always
legible regardless of dark or light mode.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPixmap, QPainterPath

from config import ICONS_DIR

# Background pill colour — a neutral light tone that works on both themes
_LOGO_BG_LIGHT = QColor("#FFFFFF")   # white pill on dark bg
_LOGO_BG_DARK  = QColor("#1E1E2E")   # dark pill on light bg (matches dark theme base)
_PILL_RADIUS   = 5                   # rounded corner radius in px
_PADDING_H     = 8                   # horizontal padding inside pill
_PADDING_V     = 3                   # vertical padding inside pill


def _render_on_bg(pixmap: QPixmap, bg_color: QColor) -> QPixmap:
    """
    Render *pixmap* centred on a rounded-rectangle background of *bg_color*.
    Returns a new pixmap with the background included.
    """
    w = pixmap.width() + _PADDING_H * 2
    h = pixmap.height() + _PADDING_V * 2

    result = QPixmap(w, h)
    result.fill(Qt.GlobalColor.transparent)

    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

    # Draw rounded background
    path = QPainterPath()
    path.addRoundedRect(0, 0, w, h, _PILL_RADIUS, _PILL_RADIUS)
    painter.fillPath(path, bg_color)

    # Draw logo centred on the background
    painter.drawPixmap(_PADDING_H, _PADDING_V, pixmap)
    painter.end()

    return result


def get_logo_pixmap(height: int, theme: str = "dark") -> QPixmap:
    """
    Return a logo pixmap with a theme-appropriate background pill,
    scaled so the logo content is *height* pixels tall.

    - Dark theme: white pill background (logo is navy → legible)
    - Light theme: no pill needed (navy on white is already fine),
                   but we add a subtle white bg for consistency.
    """
    logo_path: Path = ICONS_DIR / "neatpdf_logo.png"
    icon_path: Path = ICONS_DIR / "neatpdf_32.png"

    src_path = logo_path if logo_path.exists() else icon_path
    pix = QPixmap(str(src_path)).scaledToHeight(
        height, Qt.TransformationMode.SmoothTransformation
    )

    if pix.isNull():
        return pix

    if theme == "dark":
        pix = _render_on_bg(pix, _LOGO_BG_LIGHT)
    # light theme: original colours on light bg — no pill needed

    return pix


def get_icon_pixmap(size: int, theme: str = "dark") -> QPixmap:
    """
    Return a square icon pixmap at *size*×*size*. No tinting needed.
    """
    icon_path: Path = ICONS_DIR / "neatpdf_256.png"
    if not icon_path.exists():
        icon_path = ICONS_DIR / "neatpdf_32.png"

    pix = QPixmap(str(icon_path)).scaled(
        size, size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    return pix
