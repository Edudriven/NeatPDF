"""
gui/dialogs/toc_detection_dialog.py — Experimental heading-detection review dialog.

Shown when the user triggers "Detect from content (experimental)" via
right-click on a section header in the TOC panel.  Presents the detected
entries as a checkable list so the user can accept or discard individual
results before they replace the section's existing entries.

After the dialog is accepted, ``accepted_entries`` contains only the
entries the user checked.
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from models.toc_entry import TOCEntry

log = logging.getLogger(__name__)

_DIALOG_MIN_WIDTH = 520
_DIALOG_MIN_HEIGHT = 400


class TOCDetectionDialog(QDialog):
    """Review dialog for experimentally detected TOC entries.

    After the dialog is accepted, ``accepted_entries`` contains the
    entries the user chose to keep.

    Args:
        entries: Detected TOCEntry objects to present.
        source_name: Document title shown in the header.
        parent: Parent widget.
    """

    def __init__(
        self,
        entries: list[TOCEntry],
        source_name: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._entries = entries
        self._checkboxes: list[tuple[QCheckBox, TOCEntry]] = []

        self.setWindowTitle("Detect from Content — Experimental")
        self.setMinimumSize(_DIALOG_MIN_WIDTH, _DIALOG_MIN_HEIGHT)
        self.setModal(True)

        self._build_ui(source_name)

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self, source_name: str) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(14, 14, 14, 14)

        # Warning banner
        warning = QLabel(
            "⚠  <b>Experimental</b> — heading detection uses font-size heuristics "
            "and may produce inaccurate results.  Review carefully before applying."
        )
        warning.setWordWrap(True)
        warning.setStyleSheet(
            "background: #3d2f00; color: #FFD700; padding: 8px; border-radius: 4px;"
        )
        root.addWidget(warning)

        # Header
        src_str = f" in <b>{source_name}</b>" if source_name else ""
        header = QLabel(
            f"Found <b>{len(self._entries)}</b> candidate entries "
            f"using font-size heuristics{src_str}.<br>"
            "<small>Check the entries you want to keep. "
            "They will <b>replace</b> the current entries for this document.</small>"
        )
        header.setWordWrap(True)
        root.addWidget(header)

        # Select all / none row
        sel_row = QHBoxLayout()
        btn_all = QLabel('<a href="#">Select all</a>')
        btn_none = QLabel('<a href="#">Select none</a>')
        btn_all.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        btn_none.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        btn_all.linkActivated.connect(lambda _: self._set_all(True))
        btn_none.linkActivated.connect(lambda _: self._set_all(False))
        sel_row.addWidget(btn_all)
        sel_row.addWidget(btn_none)
        sel_row.addStretch(1)
        root.addLayout(sel_row)

        # Scroll area with entry checkboxes
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(4, 4, 4, 4)
        container_layout.setSpacing(2)

        if not self._entries:
            no_entries = QLabel("No entries were detected.")
            no_entries.setStyleSheet("color: #585B70;")
            container_layout.addWidget(no_entries)
        else:
            for entry in self._entries:
                cb = self._make_entry_checkbox(entry)
                container_layout.addWidget(cb)
                self._checkboxes.append((cb, entry))

        container_layout.addStretch(1)
        scroll.setWidget(container)
        root.addWidget(scroll, 1)

        # Button box
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        ok_btn = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok_btn.setText("Apply Selected")
        ok_btn.setEnabled(bool(self._entries))
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        root.addWidget(self._buttons)

    def _make_entry_checkbox(self, entry: TOCEntry) -> QCheckBox:
        indent = "  " * (entry.level - 1)
        level_badge = f"[L{entry.level}]"
        label = f"{indent}{level_badge}  {entry.title}  →  p{entry.page_number}"
        cb = QCheckBox(label)
        cb.setChecked(True)
        if entry.level == 1:
            font = cb.font()
            font.setBold(True)
            cb.setFont(font)
        cb.setStyleSheet("padding: 2px 0;")
        return cb

    # ── Actions ───────────────────────────────────────────────────────────

    def _set_all(self, checked: bool) -> None:
        for cb, _ in self._checkboxes:
            cb.setChecked(checked)

    # ── Result ────────────────────────────────────────────────────────────

    @property
    def accepted_entries(self) -> list[TOCEntry]:
        """Entries the user checked.  Only meaningful after ``accept()``."""
        return [entry for cb, entry in self._checkboxes if cb.isChecked()]
