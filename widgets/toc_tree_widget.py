"""
widgets/toc_tree_widget.py — Editable TOC tree widget with section headers.

A QTreeWidget subclass that:
  - Shows one non-editable, non-selectable section header row per document.
  - Shows one row per TOCEntry with columns: Title | Page | Lvl.
  - Supports inline editing of Title and Page via double-click on entry rows.
  - Emits structured signals for all user actions so the panel can
    delegate to TOCService without any business logic here.
  - Visually dims disabled entries.
  - Shows local page number + "(+offset)" on entry rows.
  - Provides a right-click context menu on section headers for
    "Detect from content (experimental)".

Section header rows store the doc_id in UserRole of column 0 and the
sentinel ``_SECTION_ROLE`` flag in UserRole of column 1 so that callers
can distinguish them from regular entry rows.
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QMenu,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

from models.toc_entry import TOCEntry
from models.toc_section import TOCSection

log = logging.getLogger(__name__)

# Column indices
_COL_TITLE = 0
_COL_PAGE = 1
_COL_LEVEL = 2

# Sentinel stored in column-1 UserRole to mark section header rows
_SECTION_MARKER = "__section__"

_DISABLED_COLOR = QColor("#585B70")
_ENABLED_COLOR = QColor("#CDD6F4")
_PAGE_COLOR = QColor("#89B4FA")
_OFFSET_COLOR = QColor("#6C7086")
_LEVEL_COLOR = QColor("#6C7086")
_HEADER_BG = QColor("#2A2A3A")
_HEADER_FG = QColor("#A0A0B0")


class TOCTreeWidget(QTreeWidget):
    """Editable tree widget for TOCSections + TOCEntry objects.

    Signals:
        entry_title_changed:    (entry_id, new_title)
        entry_page_changed:     (entry_id, new_page_number)
        entry_selected:         (entry_id) — single selection changed
        selection_cleared:      emitted when nothing is selected
        detect_requested:       (doc_id) — user wants experimental detection
    """

    entry_title_changed = Signal(str, str)   # entry_id, new_title
    entry_page_changed = Signal(str, int)    # entry_id, new_page
    entry_selected = Signal(str)             # entry_id
    selection_cleared = Signal()
    detect_requested = Signal(str)           # doc_id

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._loading = False  # suppress signals during bulk load

        self.setColumnCount(3)
        self.setHeaderLabels(["Title", "Page", "Lvl"])
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(False)  # we handle backgrounds manually
        self.setRootIsDecorated(False)
        self.setUniformRowHeights(True)
        self.setAnimated(False)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)

        hdr = self.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.setColumnWidth(1, 80)  # wider to fit "(+N)" offset annotation
        self.setColumnWidth(2, 36)

        self.itemDoubleClicked.connect(self._on_double_click)
        self.itemSelectionChanged.connect(self._on_selection_changed)

    # ── Public API ────────────────────────────────────────────────────────

    def load_sections(self, sections: list[TOCSection]) -> None:
        """Repopulate the widget from a list of TOCSections."""
        self._loading = True
        self.clear()
        for section in sections:
            self._add_section_header(section)
            for entry in section.entries:
                self._add_entry_item(entry, section.page_offset)
        self._loading = False

    def load_entries(self, entries: list[TOCEntry]) -> None:
        """Backward-compat: repopulate with a flat entry list (no section headers).

        Used by tests and code paths that haven't been migrated to sections yet.
        """
        self._loading = True
        self.clear()
        for entry in entries:
            self._add_entry_item(entry, page_offset=0)
        self._loading = False

    def selected_entry_id(self) -> Optional[str]:
        """Return the entry_id of the currently selected entry row, or None.

        Section header rows are not selectable and never returned.
        """
        items = self.selectedItems()
        if not items:
            return None
        item = items[0]
        # Reject section header rows
        if item.data(_COL_PAGE, Qt.ItemDataRole.UserRole) == _SECTION_MARKER:
            return None
        return item.data(_COL_TITLE, Qt.ItemDataRole.UserRole)

    def scroll_to_entry(self, entry_id: str) -> None:
        """Scroll to and select the row for *entry_id*."""
        item = self._find_entry_item(entry_id)
        if item:
            self.setCurrentItem(item)
            self.scrollToItem(item)

    # ── Internal row builders ─────────────────────────────────────────────

    def _add_section_header(self, section: TOCSection) -> QTreeWidgetItem:
        item = QTreeWidgetItem(self)

        # Title column: "── document.pdf ─── p.N ──"
        start_page = section.page_offset + 1  # display as 1-based
        label = f"── {section.doc_title} ─── p.{start_page} ──"
        item.setText(_COL_TITLE, label)
        item.setText(_COL_PAGE, "")
        item.setText(_COL_LEVEL, "")

        # Store doc_id for context menu; sentinel to flag as header
        item.setData(_COL_TITLE, Qt.ItemDataRole.UserRole, section.doc_id)
        item.setData(_COL_PAGE, Qt.ItemDataRole.UserRole, _SECTION_MARKER)

        # Visual styling
        for col in range(self.columnCount()):
            item.setBackground(col, QBrush(_HEADER_BG))
            item.setForeground(col, QBrush(_HEADER_FG))

        font = item.font(_COL_TITLE)
        font.setWeight(QFont.Weight.DemiBold)
        font.setItalic(True)
        item.setFont(_COL_TITLE, font)

        # Non-selectable
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)  # no ItemIsSelectable

        return item

    def _add_entry_item(
        self, entry: TOCEntry, page_offset: int
    ) -> QTreeWidgetItem:
        item = QTreeWidgetItem(self)
        self._populate_entry_item(item, entry, page_offset)
        return item

    def _populate_entry_item(
        self, item: QTreeWidgetItem, entry: TOCEntry, page_offset: int
    ) -> None:
        indent = "  " * (entry.level - 1)
        item.setText(_COL_TITLE, indent + entry.title)

        # Show page number as-is — user enters absolute merged-sequence pages
        page_str = str(entry.page_number)
        item.setText(_COL_PAGE, page_str)
        item.setToolTip(_COL_PAGE, "")
        item.setText(_COL_LEVEL, str(entry.level))

        # Store entry_id; no section marker
        item.setData(_COL_TITLE, Qt.ItemDataRole.UserRole, entry.entry_id)
        item.setData(_COL_PAGE, Qt.ItemDataRole.UserRole, None)  # not a header
        # Store offset for commitData
        item.setData(_COL_LEVEL, Qt.ItemDataRole.UserRole, page_offset)

        item.setTextAlignment(
            _COL_PAGE, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        item.setTextAlignment(_COL_LEVEL, Qt.AlignmentFlag.AlignCenter)

        if entry.enabled:
            item.setForeground(_COL_TITLE, QBrush(_ENABLED_COLOR))
            item.setForeground(_COL_PAGE, QBrush(_PAGE_COLOR))
            item.setForeground(_COL_LEVEL, QBrush(_LEVEL_COLOR))
        else:
            for col in (_COL_TITLE, _COL_PAGE, _COL_LEVEL):
                item.setForeground(col, QBrush(_DISABLED_COLOR))
            font = item.font(_COL_TITLE)
            font.setItalic(True)
            item.setFont(_COL_TITLE, font)

        if entry.level == 1:
            font = item.font(_COL_TITLE)
            font.setWeight(QFont.Weight.Medium)
            item.setFont(_COL_TITLE, font)

        item.setFlags(
            Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        )

    def _find_entry_item(self, entry_id: str) -> Optional[QTreeWidgetItem]:
        for i in range(self.topLevelItemCount()):
            item = self.topLevelItem(i)
            if (
                item
                and item.data(_COL_PAGE, Qt.ItemDataRole.UserRole) != _SECTION_MARKER
                and item.data(_COL_TITLE, Qt.ItemDataRole.UserRole) == entry_id
            ):
                return item
        return None

    # ── Context menu ──────────────────────────────────────────────────────

    def _on_context_menu(self, pos) -> None:
        item = self.itemAt(pos)
        if item is None:
            return

        # Only show context menu on section header rows
        if item.data(_COL_PAGE, Qt.ItemDataRole.UserRole) != _SECTION_MARKER:
            return

        doc_id = item.data(_COL_TITLE, Qt.ItemDataRole.UserRole)
        if not doc_id:
            return

        menu = QMenu(self)
        action = menu.addAction("Detect from content (experimental)…")
        chosen = menu.exec(self.viewport().mapToGlobal(pos))
        if chosen is action:
            self.detect_requested.emit(doc_id)

    # ── Event handlers ────────────────────────────────────────────────────

    def _on_double_click(self, item: QTreeWidgetItem, column: int) -> None:
        # Section headers are not editable
        if item.data(_COL_PAGE, Qt.ItemDataRole.UserRole) == _SECTION_MARKER:
            return

        entry_id = item.data(_COL_TITLE, Qt.ItemDataRole.UserRole)
        if not entry_id:
            return

        if column == _COL_TITLE:
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
            self.editItem(item, _COL_TITLE)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)

        elif column == _COL_PAGE:
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
            self.editItem(item, _COL_PAGE)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)

    def commitData(self, editor) -> None:  # type: ignore[override]
        """Called when the delegate commits an edit."""
        super().commitData(editor)
        item = self.currentItem()
        if item is None:
            return

        # Section headers must never commit
        if item.data(_COL_PAGE, Qt.ItemDataRole.UserRole) == _SECTION_MARKER:
            return

        entry_id = item.data(_COL_TITLE, Qt.ItemDataRole.UserRole)
        if not entry_id:
            return

        col = self.currentColumn()
        if col == _COL_TITLE:
            raw = item.text(_COL_TITLE).strip()
            if raw:
                self.entry_title_changed.emit(entry_id, raw)

        elif col == _COL_PAGE:
            try:
                page = int(item.text(_COL_PAGE))
                self.entry_page_changed.emit(entry_id, page)
            except ValueError:
                pass

    def _on_selection_changed(self) -> None:
        if self._loading:
            return
        entry_id = self.selected_entry_id()
        if entry_id:
            self.entry_selected.emit(entry_id)
        else:
            self.selection_cleared.emit()
