"""
gui/panels/file_panel.py — Imported files list panel.

Displays the list of imported PDFs with their name, page count,
file size, and a color-coded indicator.  Supports drag-to-reorder
for changing the merge order and drag-and-drop file import.
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

log = logging.getLogger(__name__)

# Palette of distinct colors to distinguish documents visually.
_DOC_COLORS = [
    "#89B4FA",  # blue
    "#A6E3A1",  # green
    "#F38BA8",  # red/pink
    "#FAB387",  # peach
    "#F9E2AF",  # yellow
    "#CBA6F7",  # mauve
    "#94E2D5",  # teal
    "#89DCEB",  # sky
]


def _doc_color(index: int) -> str:
    """Return a color hex string for the nth document."""
    return _DOC_COLORS[index % len(_DOC_COLORS)]


class _DocumentItemWidget(QWidget):
    """Rich widget rendered inside each QListWidget row."""

    def __init__(
        self,
        title: str,
        page_count: int,
        file_size_mb: float,
        color: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._build_ui(title, page_count, file_size_mb, color)

    def _build_ui(
        self, title: str, page_count: int, file_size_mb: float, color: str
    ) -> None:
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 6, 6, 6)
        row.setSpacing(8)

        # Color stripe
        stripe = QFrame()
        stripe.setFixedWidth(4)
        stripe.setStyleSheet(
            f"background-color: {color}; border-radius: 2px;"
        )
        row.addWidget(stripe)

        # Text column
        text_col = QVBoxLayout()
        text_col.setSpacing(2)

        title_label = QLabel(title)
        title_label.setStyleSheet("font-weight: 600; font-size: 13px;")
        title_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        title_label.setToolTip(title)

        meta_label = QLabel(f"{page_count} pages  •  {file_size_mb:.2f} MB")
        meta_label.setStyleSheet("color: #A6ADC8; font-size: 11px;")

        text_col.addWidget(title_label)
        text_col.addWidget(meta_label)
        row.addLayout(text_col, 1)

    def update_page_count(self, count: int) -> None:
        """Refresh the meta label after page count is resolved."""
        meta: Optional[QLabel] = self.findChildren(QLabel)[1] if len(self.findChildren(QLabel)) > 1 else None  # type: ignore[assignment]
        if meta:
            current = meta.text()
            # Replace the page count portion
            parts = current.split("  •  ")
            if len(parts) == 2:
                meta.setText(f"{count} pages  •  {parts[1]}")


class FilePanel(QWidget):
    """Left-side panel listing all imported PDF documents.

    Signals:
        files_dropped: Emitted when PDF files are dropped onto this panel.
            Payload: list[str] of file paths.
        document_selected: Emitted when the user selects a document row.
            Payload: doc_id string.
        document_removed: Emitted when the user clicks Remove.
            Payload: doc_id string.
        order_changed: Emitted when rows are dragged to a new position.
            Payload: list[str] of doc_ids in new order.
    """

    files_dropped = Signal(list)       # list[str]
    document_selected = Signal(str)    # doc_id
    document_removed = Signal(str)     # doc_id
    order_changed = Signal(list)       # list[str] ordered doc_ids

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._color_counter = 0
        self._id_to_row: dict[str, int] = {}
        self.setAcceptDrops(True)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Header ─────────────────────────────────────────────────────────
        self._header = QLabel("Documents  (0)")
        self._header.setStyleSheet(
            "font-weight: 600; padding: 8px 10px 4px 10px; font-size: 13px;"
        )
        layout.addWidget(self._header)

        # ── Document list ──────────────────────────────────────────────────
        self._list = QListWidget()
        self._list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._list.setAlternatingRowColors(False)
        self._list.setSpacing(2)
        self._list.setStyleSheet(
            "QListWidget { border: none; background: transparent; }"
            "QListWidget::item { padding: 0; margin: 2px 4px; border-radius: 6px; }"
            "QListWidget::item:selected { background: #313244; }"
            "QListWidget::item:hover { background: #25253A; }"
        )
        layout.addWidget(self._list, 1)

        # ── Drop hint ──────────────────────────────────────────────────────
        self._drop_hint = QLabel("Drop PDFs here")
        self._drop_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._drop_hint.setStyleSheet(
            "color: #585B70; font-size: 12px; padding: 8px;"
        )
        layout.addWidget(self._drop_hint)

        # ── Button row ─────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(8, 4, 8, 8)
        btn_row.setSpacing(6)

        self.btn_add = QPushButton("Add Files…")
        self.btn_remove = QPushButton("Remove")
        self.btn_remove.setEnabled(False)

        btn_row.addWidget(self.btn_add)
        btn_row.addWidget(self.btn_remove)
        layout.addLayout(btn_row)

        # ── Connections ────────────────────────────────────────────────────
        self._list.currentRowChanged.connect(self._on_selection_changed)
        self._list.model().rowsMoved.connect(self._on_rows_moved)
        self.btn_remove.clicked.connect(self._on_remove_clicked)

    # ── Drag-and-drop for file import ──────────────────────────────────────

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # type: ignore[override]
        if event.mimeData().hasUrls() and any(
            u.toLocalFile().lower().endswith(".pdf")
            for u in event.mimeData().urls()
        ):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:  # type: ignore[override]
        paths = [
            u.toLocalFile()
            for u in event.mimeData().urls()
            if u.toLocalFile().lower().endswith(".pdf")
        ]
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()

    # ── Slots ──────────────────────────────────────────────────────────────

    def _on_selection_changed(self, row: int) -> None:
        has_sel = row >= 0
        self.btn_remove.setEnabled(has_sel)
        if has_sel:
            item = self._list.item(row)
            if item:
                doc_id = item.data(Qt.ItemDataRole.UserRole)
                self.document_selected.emit(doc_id)

    def _on_rows_moved(self) -> None:
        self.order_changed.emit(self.document_ids_in_order())

    def _on_remove_clicked(self) -> None:
        row = self._list.currentRow()
        if row < 0:
            return
        item = self._list.item(row)
        if item:
            doc_id = item.data(Qt.ItemDataRole.UserRole)
            self._list.takeItem(row)
            self._update_header()
            self.document_removed.emit(doc_id)
            log.debug("FilePanel: removed row %d (doc_id=%s)", row, doc_id[:8])

    # ── Public API ────────────────────────────────────────────────────────

    def add_document_entry(
        self,
        doc_id: str,
        title: str,
        page_count: int,
        file_size_mb: float,
    ) -> None:
        """Append a document entry to the list.

        Args:
            doc_id: Unique document identifier.
            title: Display name (usually the filename stem).
            page_count: Number of pages in the document.
            file_size_mb: File size in MB.
        """
        color = _doc_color(self._color_counter)
        self._color_counter += 1

        item_widget = _DocumentItemWidget(title, page_count, file_size_mb, color)

        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, doc_id)
        # Store color so page panel can use it for stripe indicators
        item.setData(Qt.ItemDataRole.UserRole + 1, color)
        item.setSizeHint(item_widget.sizeHint())

        self._list.addItem(item)
        self._list.setItemWidget(item, item_widget)
        self._update_header()
        self._drop_hint.setVisible(self._list.count() == 0)

        log.debug("FilePanel: added %r (id=%s)", title, doc_id[:8])

    def get_doc_color(self, doc_id: str) -> Optional[QColor]:
        """Return the display color for a document, or None if not found."""
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == doc_id:
                color_hex = item.data(Qt.ItemDataRole.UserRole + 1)
                if color_hex:
                    return QColor(color_hex)
        return None

    def update_page_count(self, doc_id: str, count: int) -> None:
        """Update the page count label for a specific document."""
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == doc_id:
                widget = self._list.itemWidget(item)
                if isinstance(widget, _DocumentItemWidget):
                    widget.update_page_count(count)
                break

    def clear(self) -> None:
        """Remove all document entries."""
        self._list.clear()
        self._color_counter = 0
        self._update_header()
        self._drop_hint.setVisible(True)

    def document_ids_in_order(self) -> list[str]:
        """Return document IDs in current display order."""
        return [
            self._list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self._list.count())
            if self._list.item(i)
        ]

    def select_document(self, doc_id: str) -> None:
        """Programmatically select a row by doc_id."""
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == doc_id:
                self._list.setCurrentRow(i)
                return

    # ── Helpers ───────────────────────────────────────────────────────────

    def _update_header(self) -> None:
        count = self._list.count()
        self._header.setText(f"Documents  ({count})")
