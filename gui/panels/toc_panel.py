"""
gui/panels/toc_panel.py — Table of Contents editor panel (section-aware).

Layout:
┌──────────────────────────────────────────────────────────┐
│  Bookmarks  (N entries across M documents)               │
├──────────────────────────────────────────────────────────┤
│  [＋] [✕] [↑] [↓] [←] [→] [○]  │ p: [___] │ [✎ Edit]  │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  ── document1.pdf ──────────── p.1 ──                   │  ← section header
│    Chapter 1                   1   (+0)                  │
│      1.1 Background            3                         │
│                                                          │
│  ── document2.pdf ──────────── p.10 ─                   │
│    Introduction                1  (+10)                  │
│                                                          │
└──────────────────────────────────────────────────────────┘

The panel talks to TOCService via signals/slots; it never mutates
TOCEntry objects directly.
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import QEvent, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from services.toc_service import TOCService
from widgets.toc_tree_widget import TOCTreeWidget

log = logging.getLogger(__name__)

_BTN_H = 26   # button height (unchanged)
_BTN_STYLE = (
    "QPushButton { padding: 0px 4px; min-width: 0px; }"
)
_TEXT_THRESHOLD = 341   # width above which row-2 shows text labels
_SINGLE_ROW_THRESHOLD = 500  # width above which both rows merge into one


class TOCPanel(QWidget):
    """Full TOC editor panel with section-aware display.

    Signals:
        toc_changed:          Re-emitted from TOCService whenever the TOC mutates.
        go_to_page_requested: (page_number: int) — user clicked Go To on an entry.
    """

    toc_changed = Signal()
    go_to_page_requested = Signal(int)   # page number (1-based absolute)

    def __init__(
        self,
        toc_service: Optional[TOCService] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._svc: Optional[TOCService] = toc_service
        self._total_pages: int = 0
        self._selected_page: int = -1   # currently selected page in PagePanel (0-based)
        self._build_ui()
        if self._svc:
            self._attach_service(self._svc)

    # ── Service wiring ────────────────────────────────────────────────────

    def set_service(self, svc: TOCService) -> None:
        """Attach (or replace) the TOCService after construction."""
        self._svc = svc
        self._attach_service(svc)
        self._refresh()

    def _attach_service(self, svc: TOCService) -> None:
        svc.toc_changed.connect(self._refresh)
        svc.toc_changed.connect(self.toc_changed)
        svc.sections_changed.connect(self._refresh)

    # ── Public API ────────────────────────────────────────────────────────

    def set_total_pages(self, total: int) -> None:
        """Inform the panel of the current total page count (for default page numbers)."""
        self._total_pages = total
        self._page_spin.setRange(1, max(1, total))

    def set_selected_page(self, page_index: int) -> None:
        """Called by MainWindow when the selected page in PagePanel changes.

        Args:
            page_index: 0-based display index of the selected page, or -1 if none.
        """
        self._selected_page = page_index
        # Enable sync button only when both an entry and a page are selected
        sync_enabled = page_index >= 0 and self._tree.selected_entry_id() is not None
        self._btn_sync.setEnabled(sync_enabled)
        self._s_btn_sync.setEnabled(sync_enabled)

    # ── UI construction ───────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header
        self._header = QLabel("Bookmarks  (0 entries)")
        self._header.setStyleSheet(
            "font-weight: 600; padding: 6px 10px 4px 10px; font-size: 13px;"
        )
        root.addWidget(self._header)

        # Toolbar
        root.addWidget(self._build_toolbar())

        # Tree
        self._tree = TOCTreeWidget()
        self._tree.entry_title_changed.connect(self._on_title_changed)
        self._tree.entry_page_changed.connect(self._on_page_changed)
        self._tree.entry_selected.connect(self._on_selection_changed)
        self._tree.selection_cleared.connect(lambda: self._on_selection_changed(None))
        self._tree.detect_requested.connect(self._on_detect_from_content)
        root.addWidget(self._tree, 1)

    def _build_toolbar(self) -> QWidget:
        bar = QWidget()
        bar.setStyleSheet("background-color: transparent;")
        self._toolbar_root = QVBoxLayout(bar)
        self._toolbar_root.setContentsMargins(6, 3, 6, 3)
        self._toolbar_root.setSpacing(2)

        def _btn(icon: str, tip: str) -> QPushButton:
            b = QPushButton(icon)
            b.setToolTip(tip)
            b.setFixedHeight(_BTN_H)
            b.setStyleSheet(_BTN_STYLE)
            return b

        def _btn_row1(icon: str, tip: str) -> QPushButton:
            """Structural button — one instance shared across layouts via same slot."""
            return _btn(icon, tip)

        # ── Two-row layout ────────────────────────────────────────────────
        self._two_row_widget = QWidget()
        two_root = QVBoxLayout(self._two_row_widget)
        two_root.setContentsMargins(0, 0, 0, 0)
        two_root.setSpacing(2)

        # Row 1 buttons (two-row mode)
        self._btn_add     = _btn_row1("＋", "Add entry after selection (or at end)")
        self._btn_delete  = _btn_row1("✕",  "Delete selected entry")
        self._btn_up      = _btn_row1("↑",  "Move entry up")
        self._btn_down    = _btn_row1("↓",  "Move entry down")
        self._btn_indent  = _btn_row1("→",  "Indent (increase level)")
        self._btn_outdent = _btn_row1("←",  "Outdent (decrease level)")
        self._btn_toggle  = _btn_row1("○",  "Enable / disable entry")

        self._page_label = QLabel("p:")
        self._page_label.setStyleSheet("color: #CDD6F4;")
        self._page_spin = QSpinBox()
        self._page_spin.setRange(1, 9999)
        self._page_spin.setFixedWidth(52)
        self._page_spin.setFixedHeight(_BTN_H)
        self._page_spin.setToolTip("Page number for selected entry")

        # Row 2 buttons (two-row mode)
        self._btn_goto = _btn("↗", "Go to page — scroll Pages panel to this entry's page")
        self._btn_sync = _btn("⊙", "Sync page — set entry page to the currently selected page")
        self._btn_edit = _btn("✎", "Quick text editor for all section titles")

        row1 = QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        row1.setSpacing(2)
        for b in (self._btn_add, self._btn_delete, self._btn_up, self._btn_down,
                  self._btn_indent, self._btn_outdent, self._btn_toggle):
            row1.addWidget(b)
        row1.addStretch(1)
        row1.addWidget(self._page_label)
        row1.addWidget(self._page_spin)

        row2 = QHBoxLayout()
        row2.setContentsMargins(0, 0, 0, 0)
        row2.setSpacing(2)
        row2.addWidget(self._btn_goto)
        row2.addWidget(self._btn_sync)
        row2.addWidget(self._btn_edit)
        row2.addStretch(1)

        two_root.addLayout(row1)
        two_root.addLayout(row2)

        # ── Single-row layout (separate button instances) ─────────────────
        self._one_row_widget = QWidget()
        one_row = QHBoxLayout(self._one_row_widget)
        one_row.setContentsMargins(0, 0, 0, 0)
        one_row.setSpacing(2)

        # Separate button instances for single-row mode
        self._s_btn_add     = _btn("＋", "Add entry after selection (or at end)")
        self._s_btn_delete  = _btn("✕",  "Delete selected entry")
        self._s_btn_up      = _btn("↑",  "Move entry up")
        self._s_btn_down    = _btn("↓",  "Move entry down")
        self._s_btn_indent  = _btn("→",  "Indent (increase level)")
        self._s_btn_outdent = _btn("←",  "Outdent (decrease level)")
        self._s_btn_toggle  = _btn("○",  "Enable / disable entry")
        self._s_btn_goto    = _btn("↗",  "Go to page — scroll Pages panel to this entry's page")
        self._s_btn_sync    = _btn("⊙",  "Sync page — set entry page to the currently selected page")
        self._s_btn_edit    = _btn("✎",  "Quick text editor for all section titles")

        self._s_page_label = QLabel("p:")
        self._s_page_label.setStyleSheet("color: #CDD6F4;")
        # Share the spin box — it holds state, must be the same instance.
        # We handle this by keeping spin in two-row and hiding/showing.
        self._s_page_spin_label_placeholder = QLabel("p:")
        self._s_page_spin_label_placeholder.setStyleSheet("color: #CDD6F4;")

        for b in (self._s_btn_add, self._s_btn_delete, self._s_btn_up,
                  self._s_btn_down, self._s_btn_indent, self._s_btn_outdent,
                  self._s_btn_toggle):
            one_row.addWidget(b)
        one_row.addStretch(1)
        one_row.addWidget(self._s_page_spin_label_placeholder)
        one_row.addWidget(self._page_spin)   # spin is shared — moves here in single-row mode
        one_row.addWidget(self._s_btn_goto)
        one_row.addWidget(self._s_btn_sync)
        one_row.addWidget(self._s_btn_edit)

        self._toolbar_root.addWidget(self._two_row_widget)
        self._toolbar_root.addWidget(self._one_row_widget)
        self._one_row_widget.setVisible(False)

        # ── Wire two-row signals ──────────────────────────────────────────
        self._btn_add.clicked.connect(self._on_add)
        self._btn_delete.clicked.connect(self._on_delete)
        self._btn_up.clicked.connect(self._on_move_up)
        self._btn_down.clicked.connect(self._on_move_down)
        self._btn_indent.clicked.connect(self._on_indent)
        self._btn_outdent.clicked.connect(self._on_outdent)
        self._btn_toggle.clicked.connect(self._on_toggle_enabled)
        self._btn_goto.clicked.connect(self._on_goto_page)
        self._btn_sync.clicked.connect(self._on_sync_page)
        self._btn_edit.clicked.connect(self._on_quick_edit)

        # ── Wire single-row signals ───────────────────────────────────────
        self._s_btn_add.clicked.connect(self._on_add)
        self._s_btn_delete.clicked.connect(self._on_delete)
        self._s_btn_up.clicked.connect(self._on_move_up)
        self._s_btn_down.clicked.connect(self._on_move_down)
        self._s_btn_indent.clicked.connect(self._on_indent)
        self._s_btn_outdent.clicked.connect(self._on_outdent)
        self._s_btn_toggle.clicked.connect(self._on_toggle_enabled)
        self._s_btn_goto.clicked.connect(self._on_goto_page)
        self._s_btn_sync.clicked.connect(self._on_sync_page)
        self._s_btn_edit.clicked.connect(self._on_quick_edit)

        self._page_spin.valueChanged.connect(self._on_spin_page_changed)

        self._set_toolbar_enabled(False)
        return bar

    # ── Responsive toolbar ────────────────────────────────────────────────

    def showEvent(self, event: QEvent) -> None:  # type: ignore[override]
        super().showEvent(event)  # type: ignore[arg-type]
        self._apply_toolbar_mode(self.width())

    def resizeEvent(self, event: QEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)  # type: ignore[arg-type]
        self._apply_toolbar_mode(self.width())

    def _apply_toolbar_mode(self, panel_width: int) -> None:
        """Three modes:
          < 341px  — two rows, row-2 icon only
          341-499px — two rows, row-2 text labels
          >= 500px  — single row, all buttons + text labels
        """
        single = panel_width >= _SINGLE_ROW_THRESHOLD
        text   = panel_width >= _TEXT_THRESHOLD

        self._one_row_widget.setVisible(single)
        self._two_row_widget.setVisible(not single)

        # Update row-2 / single-row button labels
        label_goto = "↗  Go to page" if text else "↗"
        label_sync = "⊙  Sync page"  if text else "⊙"
        label_edit = "✎  Edit all"   if text else "✎"
        for goto, sync, edit in (
            (self._btn_goto,   self._btn_sync,   self._btn_edit),
            (self._s_btn_goto, self._s_btn_sync, self._s_btn_edit),
        ):
            goto.setText(label_goto)
            sync.setText(label_sync)
            edit.setText(label_edit)

    def _all_action_buttons(self) -> list[QPushButton]:
        """Return both sets of structural buttons for enable/disable."""
        return [
            self._btn_add, self._btn_delete, self._btn_up, self._btn_down,
            self._btn_indent, self._btn_outdent, self._btn_toggle,
            self._btn_goto, self._btn_sync,
            self._s_btn_add, self._s_btn_delete, self._s_btn_up, self._s_btn_down,
            self._s_btn_indent, self._s_btn_outdent, self._s_btn_toggle,
            self._s_btn_goto, self._s_btn_sync,
        ]

    # ── Refresh ───────────────────────────────────────────────────────────

    def _refresh(self) -> None:
        if self._svc is None:
            return

        sections = self._svc.sections
        self._tree.load_sections(sections)

        total_entries = sum(len(s.entries) for s in sections)
        n_docs = len(sections)
        if n_docs == 0:
            self._header.setText("Bookmarks  (0 entries)")
        elif n_docs == 1:
            self._header.setText(
                f"Bookmarks  ({total_entries} {'entry' if total_entries == 1 else 'entries'})"
            )
        else:
            self._header.setText(
                f"Bookmarks  ({total_entries} "
                f"{'entry' if total_entries == 1 else 'entries'} "
                f"across {n_docs} documents)"
            )

        # Update spin range to cover the full merged page count
        if self._total_pages > 0:
            self._page_spin.setRange(1, self._total_pages)

        # Auto-populate spin with the active section's start page,
        # unless an entry is currently selected (in which case its page wins).
        if not self._tree.selected_entry_id() and sections:
            active = self._svc.active_section or sections[0]
            self._page_spin.blockSignals(True)
            self._page_spin.setValue(active.page_offset + 1)
            self._page_spin.blockSignals(False)

    # ── Selection tracking ────────────────────────────────────────────────

    def _on_selection_changed(self, entry_id: Optional[str]) -> None:
        has = entry_id is not None
        self._set_toolbar_enabled(has)
        if has and self._svc:
            entry = self._svc.get_entry(entry_id)  # type: ignore[arg-type]
            if entry:
                self._page_spin.blockSignals(True)
                self._page_spin.setValue(entry.page_number)
                self._page_spin.blockSignals(False)
            # Set active doc so entry operations land in the right section
            section = self._svc.get_section_for_entry(entry_id)  # type: ignore[arg-type]
            if section:
                self._svc.set_active_doc(section.doc_id)
        elif self._svc:
            # No entry selected — show active (or first) section's start page
            active = self._svc.active_section
            if active is None:
                sections = self._svc.sections
                active = sections[0] if sections else None
            if active:
                self._page_spin.blockSignals(True)
                self._page_spin.setValue(active.page_offset + 1)
                self._page_spin.blockSignals(False)

    def _set_toolbar_enabled(self, enabled: bool) -> None:
        for b in (
            self._btn_delete, self._btn_up, self._btn_down,
            self._btn_indent, self._btn_outdent, self._btn_toggle,
            self._btn_goto,
            self._s_btn_delete, self._s_btn_up, self._s_btn_down,
            self._s_btn_indent, self._s_btn_outdent, self._s_btn_toggle,
            self._s_btn_goto,
        ):
            b.setEnabled(enabled)
        self._page_spin.setEnabled(enabled)
        self._page_label.setEnabled(enabled)
        self._s_page_spin_label_placeholder.setEnabled(enabled)
        # sync requires both entry selected AND a page selected
        sync_enabled = enabled and self._selected_page >= 0
        self._btn_sync.setEnabled(sync_enabled)
        self._s_btn_sync.setEnabled(sync_enabled)

    # ── Toolbar handlers ──────────────────────────────────────────────────

    def _on_add(self) -> None:
        if self._svc is None:
            return

        title, ok = QInputDialog.getText(self, "Add TOC Entry", "Entry title:")
        if not ok or not title.strip():
            return

        after_id = self._tree.selected_entry_id()

        # Page number: use selected page in Pages panel if available,
        # otherwise use whatever is in the spin box
        if self._selected_page >= 0:
            page = self._selected_page + 1  # convert 0-based to 1-based
        else:
            page = self._page_spin.value()

        # Inherit level from selected entry if possible, else level 2
        level = 2
        if after_id and self._svc:
            sel = self._svc.get_entry(after_id)
            if sel:
                level = sel.level

        self._svc.add_entry(
            title=title.strip(),
            page_number=page,
            level=level,
            after_id=after_id,
        )

    def _on_delete(self) -> None:
        if self._svc is None:
            return
        entry_id = self._tree.selected_entry_id()
        if not entry_id:
            return
        entry = self._svc.get_entry(entry_id)
        title = entry.title if entry else "this entry"
        reply = QMessageBox.question(
            self,
            "Delete Entry",
            f"Delete \"{title}\"?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._svc.delete_entries([entry_id])

    def _on_move_up(self) -> None:
        if self._svc is None:
            return
        entry_id = self._tree.selected_entry_id()
        if entry_id:
            self._svc.move_up(entry_id)
            self._tree.scroll_to_entry(entry_id)

    def _on_move_down(self) -> None:
        if self._svc is None:
            return
        entry_id = self._tree.selected_entry_id()
        if entry_id:
            self._svc.move_down(entry_id)
            self._tree.scroll_to_entry(entry_id)

    def _on_indent(self) -> None:
        if self._svc is None:
            return
        entry_id = self._tree.selected_entry_id()
        if entry_id:
            self._svc.indent(entry_id)

    def _on_outdent(self) -> None:
        if self._svc is None:
            return
        entry_id = self._tree.selected_entry_id()
        if entry_id:
            self._svc.outdent(entry_id)

    def _on_toggle_enabled(self) -> None:
        if self._svc is None:
            return
        entry_id = self._tree.selected_entry_id()
        if not entry_id:
            return
        entry = self._svc.get_entry(entry_id)
        if entry:
            self._svc.set_enabled(entry_id, not entry.enabled)

    def _on_title_changed(self, entry_id: str, new_title: str) -> None:
        if self._svc:
            self._svc.rename_entry(entry_id, new_title)

    def _on_page_changed(self, entry_id: str, page: int) -> None:
        if self._svc:
            self._svc.set_page(entry_id, page)

    def _on_spin_page_changed(self, value: int) -> None:
        if self._svc is None:
            return
        entry_id = self._tree.selected_entry_id()
        if entry_id:
            self._svc.set_page(entry_id, value)

    # ── Go to page / Sync page ────────────────────────────────────────────

    def _on_goto_page(self) -> None:
        """Emit go_to_page_requested with the selected entry's page number."""
        if self._svc is None:
            return
        entry_id = self._tree.selected_entry_id()
        if not entry_id:
            return
        entry = self._svc.get_entry(entry_id)
        if entry:
            self.go_to_page_requested.emit(entry.page_number)

    def _on_sync_page(self) -> None:
        """Set selected entry's page to the currently selected page in Pages panel."""
        if self._svc is None or self._selected_page < 0:
            return
        entry_id = self._tree.selected_entry_id()
        if not entry_id:
            return
        page = self._selected_page + 1  # 0-based → 1-based
        self._svc.set_page(entry_id, page)
        self._page_spin.blockSignals(True)
        self._page_spin.setValue(page)
        self._page_spin.blockSignals(False)

    # ── Quick Edit ────────────────────────────────────────────────────────

    def _on_quick_edit(self) -> None:
        """Open the Quick Text Editor for all sections."""
        if self._svc is None:
            return

        sections = self._svc.sections
        if not sections:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(
                self,
                "Quick Edit",
                "Import a document first to use the TOC editor.",
            )
            return

        from gui.dialogs.toc_quick_edit_dialog import TOCQuickEditDialog

        dialog = TOCQuickEditDialog(sections=sections, parent=self)
        if dialog.exec() == TOCQuickEditDialog.DialogCode.Accepted:
            for doc_id, entries in dialog.result_sections:
                self._svc.replace_section_entries(doc_id, entries)

    # ── Experimental detection ────────────────────────────────────────────

    def _on_detect_from_content(self, doc_id: str) -> None:
        """Handle right-click → 'Detect from content (experimental)'."""
        if self._svc is None:
            return

        # Find the section
        section = None
        for s in self._svc.sections:
            if s.doc_id == doc_id:
                section = s
                break
        if section is None:
            return

        # Warn the user
        reply = QMessageBox.warning(
            self,
            "Detect from Content — Experimental",
            "This feature uses font-size heuristics and may produce inaccurate results.\n\n"
            f"Existing entries for «{section.doc_title}» will be replaced if you confirm.\n\n"
            "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # We need the PDFDocument to get path/password.  Fetch it via the main window.
        from PySide6.QtWidgets import QApplication
        main_win = None
        for widget in QApplication.topLevelWidgets():
            if hasattr(widget, "_project"):
                main_win = widget
                break

        if main_win is None:
            log.warning("TOCPanel: could not find main window for document lookup")
            return

        doc = main_win._project.get_document(doc_id)
        if doc is None:
            QMessageBox.warning(self, "Error", "Could not find the document.")
            return

        from engines.toc_detection_engine import detect_from_headings
        entries = detect_from_headings(doc.path, doc.password, page_offset=0)

        if not entries:
            QMessageBox.information(
                self,
                "Detect from Content",
                f"No headings were detected in «{section.doc_title}».",
            )
            return

        from gui.dialogs.toc_detection_dialog import TOCDetectionDialog
        dialog = TOCDetectionDialog(
            entries=entries,
            source_name=section.doc_title,
            parent=self,
        )
        if dialog.exec() != TOCDetectionDialog.DialogCode.Accepted:
            return

        accepted = dialog.accepted_entries
        if not accepted:
            return

        self._svc.replace_section_entries(doc_id, accepted)
        log.info(
            "TOCPanel: replaced section entries for %r with %d heading-detected entries",
            section.doc_title,
            len(accepted),
        )
