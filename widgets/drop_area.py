"""
widgets/drop_area.py — Drag-and-drop file import overlay widget.

Can be used as a stand-alone drop target widget or as an overlay
on top of any QWidget.  Only accepts .pdf files.
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDropEvent
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

log = logging.getLogger(__name__)

_ACTIVE_STYLE = """
    QWidget#DropArea {
        border: 2px dashed #89B4FA;
        border-radius: 10px;
        background: rgba(137, 180, 250, 0.08);
    }
"""

_IDLE_STYLE = """
    QWidget#DropArea {
        border: 2px dashed #45475A;
        border-radius: 10px;
        background: transparent;
    }
"""


class DropArea(QWidget):
    """A drag-and-drop target that emits accepted PDF file paths.

    Signals:
        files_dropped: Emitted with a list of accepted file path strings
            when the user drops valid PDF files onto this widget.
    """

    files_dropped = Signal(list)   # list[str]

    def __init__(
        self,
        label_text: str = "Drop PDF files here",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("DropArea")
        self.setAcceptDrops(True)
        self.setMinimumHeight(80)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._label = QLabel(label_text)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setStyleSheet("color: #585B70; font-size: 13px;")
        layout.addWidget(self._label)

        self._apply_idle_style()

    # ── Drag-and-drop event handlers ──────────────────────────────────────

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # type: ignore[override]
        if self._has_pdf_urls(event):
            event.acceptProposedAction()
            self._apply_active_style()
        else:
            event.ignore()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:  # type: ignore[override]
        self._apply_idle_style()
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:  # type: ignore[override]
        self._apply_idle_style()
        paths = [
            url.toLocalFile()
            for url in event.mimeData().urls()
            if url.toLocalFile().lower().endswith(".pdf")
        ]
        if paths:
            log.info("DropArea: accepted %d PDF file(s)", len(paths))
            self.files_dropped.emit(paths)
            event.acceptProposedAction()
        else:
            event.ignore()

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _has_pdf_urls(event: QDragEnterEvent) -> bool:
        if not event.mimeData().hasUrls():
            return False
        return any(
            url.toLocalFile().lower().endswith(".pdf")
            for url in event.mimeData().urls()
        )

    def _apply_idle_style(self) -> None:
        self.setStyleSheet(_IDLE_STYLE)

    def _apply_active_style(self) -> None:
        self.setStyleSheet(_ACTIVE_STYLE)
