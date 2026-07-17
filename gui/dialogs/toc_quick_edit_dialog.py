"""
gui/dialogs/toc_quick_edit_dialog.py — Plain-text bulk TOC editor (all sections).

Shows all sections as one unified indented text.  Level-1 entries are
document root entries; their children are indented below them.  On Apply
the text is split back into per-section entry lists by tracking level-1
boundaries — each level-1 block maps positionally to the corresponding
section (first level-1 block → section 0, second → section 1, etc.).

Single-section behaviour is identical to before: one level-1 root plus
any indented children for that document.

Format
------
Document 1 Title
  Chapter 1
    1.1 Background
  Chapter 2
Document 2 Title
  Introduction
Document 3 Title

Rules:
- Each non-empty line = one TOC entry.
- Leading whitespace determines level: 0 spaces = level 1 (document root),
  2 spaces = level 2, 4 spaces = level 3, etc.  A tab = one level.
- Blank lines are ignored.
- Lines starting with ``#`` are comments (ignored).
- The number of level-1 lines must match the number of sections.
  Extra level-1 lines beyond the section count are treated as level-2.
- Page numbers are preserved automatically.
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from models.toc_entry import TOCEntry
from models.toc_section import TOCSection

log = logging.getLogger(__name__)

_DIALOG_MIN_WIDTH = 560
_DIALOG_MIN_HEIGHT = 520

_FORMAT_HINT = """\
Document 1 title         ← level 1 = document root (one per document)
  Chapter heading        ← level 2
    Sub-section          ← level 3
Document 2 title
  Introduction
# comment line (ignored)\
"""


# ── Parsing helpers ────────────────────────────────────────────────────────────

def _line_level(raw_line: str) -> int:
    """Determine the nesting level from leading whitespace (1-based)."""
    if not raw_line or raw_line[0] == "\t":
        tab_count = 0
        for ch in raw_line:
            if ch == "\t":
                tab_count += 1
            else:
                break
        return tab_count + 1
    leading = len(raw_line) - len(raw_line.lstrip(" "))
    return (leading // 2) + 1


def _parse_into_sections(
    text: str,
    sections: list[TOCSection],
) -> list[list[TOCEntry]]:
    """Parse editor text back into per-section entry lists.

    Level-1 lines mark section boundaries.  The N-th level-1 line maps to
    sections[N].  Lines indented below a level-1 line belong to the same
    section, with their level shifted down by 1 (so they start at level 1
    within that section's entry list).

    Page numbers are preserved from the existing entries by position within
    each section.  New lines inherit the page of the nearest preceding entry.

    Args:
        text: Raw editor content.
        sections: Current TOCSections (for page number preservation).

    Returns:
        A list of entry-lists, one per section, in section order.
    """
    # Collect (raw_level, title) pairs, ignoring blanks/comments
    pairs: list[tuple[int, str]] = []
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        level = _line_level(raw_line)
        pairs.append((max(1, level), stripped))

    n_sections = len(sections)
    # Split into blocks at each level-1 boundary
    blocks: list[list[tuple[int, str]]] = []  # one sub-list per section
    current_block: list[tuple[int, str]] = []

    for raw_level, title in pairs:
        if raw_level == 1:
            # Start of a new block
            if len(blocks) < n_sections:
                current_block = [(1, title)]
                blocks.append(current_block)
            else:
                # More level-1 lines than sections — treat as level-2 in last block
                if blocks:
                    blocks[-1].append((2, title))
        else:
            if not blocks:
                # Indented line before any level-1 — attach to first block
                current_block = []
                blocks.append(current_block)
            # Shift level: raw_level 2 → entry level 2, raw_level 3 → level 3, etc.
            blocks[-1].append((raw_level, title))

    # Pad missing blocks (if user deleted a level-1 line) with empty lists
    while len(blocks) < n_sections:
        blocks.append([])

    # For each section, build the TOCEntry list preserving page numbers
    result: list[list[TOCEntry]] = []
    for sec_idx, (section, block_pairs) in enumerate(zip(sections, blocks)):
        existing = section.entries
        entries: list[TOCEntry] = []

        for pos, (level, title) in enumerate(block_pairs):
            if level == 1:
                # Document root entry — always gets the absolute start page
                page = section.page_offset + 1
                if pos < len(existing) and existing[pos].level == 1:
                    # Reuse existing entry object (preserves entry_id, enabled)
                    e = existing[pos]
                    e.title = title
                    e.level = 1
                    e.page_number = page
                    entries.append(e)
                else:
                    entries.append(TOCEntry(title=title, page_number=page, level=1))
            else:
                # Child entry — use existing page if available, else 1
                if pos < len(existing):
                    e = existing[pos]
                    e.title = title
                    e.level = level
                    # Keep existing page — user sets it via spin box
                    entries.append(e)
                else:
                    entries.append(TOCEntry(title=title, page_number=1, level=level))

        result.append(entries)

    return result


def _sections_to_text(sections: list[TOCSection]) -> str:
    """Render all sections as one unified indented text block."""
    lines: list[str] = []
    for section in sections:
        for entry in section.entries:
            indent = "  " * (entry.level - 1)
            lines.append(f"{indent}{entry.title}")
    return "\n".join(lines)


# ── Dialog ─────────────────────────────────────────────────────────────────────

class TOCQuickEditDialog(QDialog):
    """Modal plain-text editor for all sections' TOC entries.

    For a single document the behaviour is identical to the old per-section
    editor.  For multiple documents all sections are shown as one unified
    text where level-1 lines are the document roots.

    After the dialog is accepted, ``result_sections`` is a list of
    ``(doc_id, entries)`` pairs ready to be passed back to TOCService.

    Args:
        sections: Current TOCSections in display order.
        parent: Parent widget.
    """

    def __init__(
        self,
        sections: list[TOCSection],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._sections = list(sections)
        self._result: list[tuple[str, list[TOCEntry]]] = []

        n = len(sections)
        title = (
            f"Quick Edit — {sections[0].doc_title}" if n == 1
            else f"Quick Edit — {n} documents"
        )
        self.setWindowTitle(title)
        self.setMinimumSize(_DIALOG_MIN_WIDTH, _DIALOG_MIN_HEIGHT)
        self.setModal(True)
        self.setSizeGripEnabled(True)

        self._build_ui(n)

    # ── Public result ─────────────────────────────────────────────────────

    @property
    def result_sections(self) -> list[tuple[str, list[TOCEntry]]]:
        """List of (doc_id, entries) after accept()."""
        return self._result

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self, n_docs: int) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(14, 14, 14, 14)

        # Format hint
        hint_label = QLabel("Format hint:")
        hint_label.setStyleSheet("color: #888; font-size: 12px;")
        root.addWidget(hint_label)

        hint_box = QPlainTextEdit()
        hint_box.setPlainText(_FORMAT_HINT)
        hint_box.setReadOnly(True)
        hint_box.setFixedHeight(90)
        hint_box.setStyleSheet(
            "color: #888; background: transparent; border: 1px solid #444;"
            "font-family: monospace; font-size: 11px;"
        )
        hint_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        root.addWidget(hint_box)

        # Main editor
        self._editor = QPlainTextEdit()
        self._editor.setPlaceholderText(
            "One entry per line.  Level-1 (no indent) = document root.  "
            "Indent children with 2 spaces per level."
        )
        self._editor.setFont(QFont("Monospace", 11))
        self._editor.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        root.addWidget(self._editor, 1)

        self._editor.setPlainText(_sections_to_text(self._sections))

        # Footer note
        if n_docs > 1:
            note_text = (
                f"There are <b>{n_docs} documents</b>.  "
                f"Keep exactly {n_docs} un-indented (level-1) lines — "
                "one per document, in order.  "
                "Page numbers are preserved automatically."
            )
        else:
            note_text = (
                "Page numbers are preserved automatically.  "
                "New lines inherit the page of the nearest existing entry above them."
            )
        note = QLabel(note_text)
        note.setWordWrap(True)
        note.setStyleSheet("color: #888; font-size: 11px;")
        root.addWidget(note)

        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Apply
        )
        apply_btn = buttons.button(QDialogButtonBox.StandardButton.Apply)
        apply_btn.setDefault(True)
        apply_btn.clicked.connect(self._on_apply)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # ── Apply ─────────────────────────────────────────────────────────────

    def _on_apply(self) -> None:
        text = self._editor.toPlainText()

        # Validate level-1 count matches section count when > 1 document
        n_sections = len(self._sections)
        if n_sections > 1:
            level1_count = sum(
                1 for raw in text.splitlines()
                if raw.strip() and not raw.strip().startswith("#")
                and _line_level(raw) == 1
            )
            if level1_count != n_sections:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.warning(
                    self,
                    "Wrong number of top-level entries",
                    f"You have {level1_count} un-indented (level-1) line(s) "
                    f"but there are {n_sections} documents.\n\n"
                    f"Please keep exactly {n_sections} un-indented lines — "
                    "one per document.",
                )
                return

        per_section = _parse_into_sections(text, self._sections)
        self._result = [
            (section.doc_id, entries)
            for section, entries in zip(self._sections, per_section)
        ]
        log.debug(
            "TOCQuickEditDialog: applied %d sections",
            len(self._result),
        )
        self.accept()
