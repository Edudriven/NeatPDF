"""
gui/panels/preview_panel.py — Single-page zoomable preview panel.

Requests a high-resolution render from PreviewService and displays it
in a scrollable, zoomable view.  On zoom changes the panel re-renders
at the appropriate DPI so the image is always sharp rather than
upscaling a fixed-size pixmap.
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

log = logging.getLogger(__name__)

_MIN_ZOOM = 0.1
_MAX_ZOOM = 5.0
_ZOOM_STEP = 1.25

# Base preview dimensions at zoom 1.0.  The render worker will scale the
# page to fit within these bounds at 1× zoom.
_BASE_PREVIEW_WIDTH = 800
_BASE_PREVIEW_HEIGHT = 1100


class PreviewPanel(QWidget):
    """Right-side panel showing a zoomable preview of the selected page.

    Signals:
        zoom_changed: Emitted when the zoom factor changes.
        preview_requested: Emitted when a render at specific pixel dimensions
            is needed.
            Payload: (doc_id, path, source_page_index, rotation, width, height).
    """

    zoom_changed = Signal(float)
    preview_requested = Signal(str, object, int, int, int, int)  # doc_id, path, page_idx, rotation, w, h

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._zoom: float = 1.0
        self._base_pixmap: Optional[QPixmap] = None   # render at zoom 1.0
        self._current_doc_id: Optional[str] = None
        self._current_page_index: int = -1
        self._current_rotation: int = 0
        self._current_path = None
        self._pending_zoom: float = 1.0   # zoom level the in-flight render was requested for
        self._fit_mode: str = "none"      # "none" | "window" | "width"
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Header row ────────────────────────────────────────────────────
        header_row = QHBoxLayout()
        header_row.setContentsMargins(10, 8, 10, 4)

        self._header_label = QLabel("Preview")
        self._header_label.setStyleSheet("font-weight: 600; font-size: 13px;")
        header_row.addWidget(self._header_label, 1)

        self._zoom_label = QLabel("100%")
        self._zoom_label.setStyleSheet("color: #A6ADC8; font-size: 11px;")
        header_row.addWidget(self._zoom_label)

        layout.addLayout(header_row)

        # ── Scroll area ───────────────────────────────────────────────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(False)
        self._scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self._page_label = QLabel("Select a page to preview")
        self._page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._page_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._page_label.setStyleSheet("color: #585B70; font-size: 14px;")
        self._scroll.setWidget(self._page_label)

        layout.addWidget(self._scroll, 1)

    # ── Public API ────────────────────────────────────────────────────────

    def show_page(
        self,
        doc_id: str,
        path,
        source_page_index: int,
        rotation: int,
        display_number: int,
    ) -> None:
        """Switch the preview to a different page."""
        self._current_doc_id = doc_id
        self._current_page_index = source_page_index
        self._current_rotation = rotation
        self._current_path = path
        self._base_pixmap = None
        self._fit_mode = "none"
        self._header_label.setText(f"Preview  — Page {display_number}")
        self._page_label.setText("Rendering…")
        self._page_label.setPixmap(QPixmap())  # clear old image
        self._request_render()

    def set_pixmap(self, doc_id: str, page_index: int, pixmap: QPixmap) -> None:
        """Called when a render is ready.

        Only updates the display if the delivered page matches the
        currently requested page.
        """
        if doc_id != self._current_doc_id or page_index != self._current_page_index:
            return   # stale render — discard

        self._base_pixmap = pixmap
        self._pending_zoom = self._zoom
        self._display_pixmap(pixmap)

    def set_zoom(self, factor: float) -> None:
        """Set zoom factor and re-render at the appropriate resolution."""
        self._zoom = max(_MIN_ZOOM, min(factor, _MAX_ZOOM))
        self._zoom_label.setText(f"{round(self._zoom * 100)}%")
        self.zoom_changed.emit(self._zoom)

        if self._current_doc_id is None:
            return

        # If we already have a pixmap rendered at a resolution that fully
        # covers the new zoom (i.e. the stored render is at least as large as
        # what we need), just downscale it — that is lossless.  Otherwise
        # request a fresh render at the exact required size.
        needed_w = int(_BASE_PREVIEW_WIDTH * self._zoom)
        needed_h = int(_BASE_PREVIEW_HEIGHT * self._zoom)

        if (
            self._base_pixmap is not None
            and not self._base_pixmap.isNull()
            and self._base_pixmap.width() >= needed_w
            and self._base_pixmap.height() >= needed_h
        ):
            # Downscale the existing high-res render — stays sharp.
            scaled = self._base_pixmap.scaled(
                needed_w,
                needed_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._display_pixmap(scaled)
        else:
            # Need a higher-resolution render.
            self._request_render()

    def zoom_in(self) -> None:
        self._fit_mode = "none"
        self.set_zoom(self._zoom * _ZOOM_STEP)

    def zoom_out(self) -> None:
        self._fit_mode = "none"
        self.set_zoom(self._zoom / _ZOOM_STEP)

    def zoom_fit(self) -> None:
        """Reset zoom to 100%."""
        self._fit_mode = "none"
        self.set_zoom(1.0)

    def zoom_fit_width(self) -> None:
        """Scale to fill the panel width."""
        self._fit_mode = "width"
        available_w = self._scroll.viewport().width() - 4
        factor = available_w / _BASE_PREVIEW_WIDTH
        self.set_zoom(max(_MIN_ZOOM, factor))

    def zoom_fit_window(self) -> None:
        """Scale so the full page fits within the viewport (both axes)."""
        self._fit_mode = "window"
        vp = self._scroll.viewport()
        available_w = vp.width() - 4
        available_h = vp.height() - 4
        factor = min(available_w / _BASE_PREVIEW_WIDTH, available_h / _BASE_PREVIEW_HEIGHT)
        self.set_zoom(max(_MIN_ZOOM, factor))

    def refit_if_needed(self) -> None:
        """Re-apply fit mode after a window resize / maximize."""
        if self._fit_mode == "window":
            self.zoom_fit_window()
        elif self._fit_mode == "width":
            self.zoom_fit_width()

    def clear(self) -> None:
        """Reset to the empty state."""
        self._base_pixmap = None
        self._current_doc_id = None
        self._current_page_index = -1
        self._current_rotation = 0
        self._current_path = None
        self._fit_mode = "none"
        self._header_label.setText("Preview")
        self._page_label.clear()
        self._page_label.setText("Select a page to preview")
        self._scroll.setWidget(self._page_label)

    # ── Helpers ───────────────────────────────────────────────────────────

    def _request_render(self) -> None:
        """Emit preview_requested with pixel dimensions for the current zoom."""
        w = max(1, int(_BASE_PREVIEW_WIDTH * self._zoom))
        h = max(1, int(_BASE_PREVIEW_HEIGHT * self._zoom))
        self._pending_zoom = self._zoom
        self.preview_requested.emit(
            self._current_doc_id,
            self._current_path,
            self._current_page_index,
            self._current_rotation,
            w,
            h,
        )

    def _display_pixmap(self, pixmap: QPixmap) -> None:
        self._page_label.setPixmap(pixmap)
        self._page_label.resize(pixmap.size())
        self._scroll.setWidget(self._page_label)
