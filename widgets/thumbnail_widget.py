"""
widgets/thumbnail_widget.py — A single page thumbnail card.

Displays a rendered page image, the page number, and a document-color
indicator stripe.  Supports:
  - Selection highlight + checkbox overlay (top-left)
  - Loading placeholder
  - Drag source for page reordering (drag on the card body)
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import QByteArray, QMimeData, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QColor, QDrag, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QCheckBox, QLabel, QSizePolicy, QVBoxLayout, QWidget

from config import THUMBNAIL_HEIGHT, THUMBNAIL_WIDTH

log = logging.getLogger(__name__)

_CARD_W = THUMBNAIL_WIDTH + 16
_CARD_H = THUMBNAIL_HEIGHT + 40   # extra room for checkbox + label

_SELECTED_BORDER = "#89B4FA"
_NORMAL_BORDER = "transparent"
_HOVER_BORDER = "#585B70"
_DROP_BORDER = "#F38BA8"

MIME_PAGE_INDEX = "application/x-neatpdf-page-index"


class ThumbnailWidget(QWidget):
    """Visual card for one page in the page panel.

    Signals:
        clicked:       (display_index, ctrl_held, shift_held)
        double_clicked:(display_index)
        drag_started:  (display_index)
        check_toggled: (display_index, checked) — checkbox state changed by user
    """

    clicked = Signal(int, bool, bool)   # display_index, ctrl, shift
    double_clicked = Signal(int)
    drag_started = Signal(int)
    check_toggled = Signal(int, bool)   # display_index, checked

    def __init__(
        self,
        display_index: int,
        doc_color: QColor,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._display_index = display_index
        self._doc_color = doc_color
        self._selected = False
        self._drag_start_pos: Optional[QPoint] = None

        self.setFixedSize(QSize(_CARD_W, _CARD_H))
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAcceptDrops(True)
        self._build_ui()
        self._apply_style(selected=False, hover=False, drop_target=False)

    # ── UI construction ───────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 4)
        layout.setSpacing(2)
        layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        # Top row: checkbox (left) + stripe (right)
        from PySide6.QtWidgets import QHBoxLayout
        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(4)

        self._checkbox = QCheckBox()
        self._checkbox.setFixedSize(16, 16)
        self._checkbox.setStyleSheet(
            "QCheckBox::indicator{width:14px;height:14px;}"
            "QCheckBox::indicator:unchecked{border:1px solid #585B70;"
            "border-radius:3px;background:#1E1E2E;}"
            "QCheckBox::indicator:checked{border:1px solid #89B4FA;"
            "border-radius:3px;background:#89B4FA;}"
        )
        self._checkbox.stateChanged.connect(self._on_checkbox_changed)
        top_row.addWidget(self._checkbox)

        self._stripe = QWidget()
        self._stripe.setFixedHeight(3)
        self._stripe.setStyleSheet(
            f"background-color: {self._doc_color.name()}; border-radius: 2px;"
        )
        top_row.addWidget(self._stripe, 1)
        layout.addLayout(top_row)

        self._image_label = QLabel()
        self._image_label.setFixedSize(THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT)
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setStyleSheet(
            "background-color: #313244; border-radius: 3px;"
        )
        self._show_placeholder()
        layout.addWidget(self._image_label, 0, Qt.AlignmentFlag.AlignHCenter)

        self._page_label = QLabel(str(self._display_index + 1))
        font = QFont()
        font.setPointSize(9)
        self._page_label.setFont(font)
        self._page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._page_label.setStyleSheet("color: #A6ADC8;")
        layout.addWidget(self._page_label)

    # ── Public API ────────────────────────────────────────────────────────

    @property
    def display_index(self) -> int:
        return self._display_index

    def set_display_index(self, index: int) -> None:
        self._display_index = index
        self._page_label.setText(str(index + 1))

    def set_pixmap(self, pixmap: QPixmap) -> None:
        scaled = pixmap.scaled(
            THUMBNAIL_WIDTH,
            THUMBNAIL_HEIGHT,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._image_label.setPixmap(scaled)

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        # Sync checkbox without firing the signal
        self._checkbox.blockSignals(True)
        self._checkbox.setChecked(selected)
        self._checkbox.blockSignals(False)
        self._apply_style(selected=selected, hover=False, drop_target=False)

    def set_loading(self, loading: bool) -> None:
        if loading:
            self._show_placeholder()

    # ── Checkbox handler ──────────────────────────────────────────────────

    def _on_checkbox_changed(self, state: int) -> None:
        checked = state == Qt.CheckState.Checked.value
        self.check_toggled.emit(self._display_index, checked)

    # ── Mouse events ──────────────────────────────────────────────────────

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.pos()
            mods = event.modifiers()
            ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
            shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
            self.clicked.emit(self._display_index, ctrl, shift)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit(self._display_index)
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if (
            self._drag_start_pos is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            delta = (event.pos() - self._drag_start_pos).manhattanLength()
            if delta >= 8:
                self._start_drag()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        self._drag_start_pos = None
        super().mouseReleaseEvent(event)

    def enterEvent(self, event) -> None:  # type: ignore[override]
        if not self._selected:
            self._apply_style(selected=False, hover=True, drop_target=False)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        if not self._selected:
            self._apply_style(selected=False, hover=False, drop_target=False)
        super().leaveEvent(event)

    # ── Drag source ───────────────────────────────────────────────────────

    def _start_drag(self) -> None:
        mime = QMimeData()
        mime.setData(
            MIME_PAGE_INDEX,
            QByteArray(str(self._display_index).encode()),
        )
        drag = QDrag(self)
        drag.setMimeData(mime)

        drag_pixmap = self.grab().scaled(
            _CARD_W // 2,
            _CARD_H // 2,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        drag.setPixmap(drag_pixmap)
        drag.setHotSpot(QPoint(_CARD_W // 4, _CARD_H // 4))

        self.drag_started.emit(self._display_index)
        drag.exec(Qt.DropAction.MoveAction)

    # ── Drop target ───────────────────────────────────────────────────────

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasFormat(MIME_PAGE_INDEX):
            event.acceptProposedAction()
            self._apply_style(selected=self._selected, hover=False, drop_target=True)
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:  # type: ignore[override]
        self._apply_style(selected=self._selected, hover=False, drop_target=False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # type: ignore[override]
        self._apply_style(selected=self._selected, hover=False, drop_target=False)
        if event.mimeData().hasFormat(MIME_PAGE_INDEX):
            event.acceptProposedAction()
        else:
            event.ignore()

    # ── Helpers ───────────────────────────────────────────────────────────

    def _show_placeholder(self) -> None:
        placeholder = QPixmap(THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT)
        placeholder.fill(QColor("#2A2A3E"))
        painter = QPainter(placeholder)
        painter.setPen(QColor("#45475A"))
        painter.drawText(placeholder.rect(), Qt.AlignmentFlag.AlignCenter, "⏳")
        painter.end()
        self._image_label.setPixmap(placeholder)

    def _apply_style(self, selected: bool, hover: bool, drop_target: bool) -> None:
        if drop_target:
            border = _DROP_BORDER
            bg = "#2D2020"
        elif selected:
            border = _SELECTED_BORDER
            bg = "#2A2D3E"
        elif hover:
            border = _HOVER_BORDER
            bg = "#25253A"
        else:
            border = _NORMAL_BORDER
            bg = "transparent"

        self.setStyleSheet(
            f"ThumbnailWidget{{"
            f"border:2px solid {border};"
            f"border-radius:6px;"
            f"background-color:{bg};"
            f"}}"
        )
