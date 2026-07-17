"""
services/toc_service.py — Section-aware TOC state management.

TOCService owns one ``TOCSection`` per imported document, in document order.
All entry-level mutations are scoped to the *active* section (set via
``set_active_doc``).  Cross-section operations (merged export, offset
recalculation) are provided at the service level.

Signals
-------
toc_changed
    Emitted after any entry-level mutation inside a section.
sections_changed
    Emitted when sections are added, removed, or reordered, and when
    page offsets are recalculated.

Operations (entry-level, scoped to active section)
--------------------------------------------------
add_entry, delete_entries, rename_entry, set_page,
move_up, move_down, indent, outdent, set_enabled — same public API as before.

Operations (section-level)
--------------------------
load_document(doc)           — import bookmarks; create auto-entry if none.
remove_document(doc_id)      — drop section.
reorder_documents(ids)       — reorder sections.
recalculate_offsets(docs)    — recompute page_offset for every section.
"""

from __future__ import annotations

import logging
from typing import Optional, TYPE_CHECKING

from PySide6.QtCore import QObject, Signal

from engines.toc_engine import fitz_toc_to_entries, validate_entries
from engines.toc_detection_engine import detect_from_bookmarks
from models.toc_entry import TOCEntry
from models.toc_section import TOCSection
from config import TOC_MAX_LEVELS

if TYPE_CHECKING:
    from models.pdf_document import PDFDocument

log = logging.getLogger(__name__)


class TOCService(QObject):
    """Section-aware TOC manager.

    Signals:
        toc_changed:     Any entry mutation inside a section.
        sections_changed: Section list added/removed/reordered or offsets changed.
    """

    toc_changed = Signal()
    sections_changed = Signal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._sections: list[TOCSection] = []
        self._active_doc_id: Optional[str] = None

    # ── Section management ────────────────────────────────────────────────

    def load_document(self, doc: "PDFDocument") -> None:
        """Called on import.  Reads bookmarks; creates an auto-entry if none.

        Args:
            doc: The newly imported PDFDocument.
        """
        # Avoid duplicates (e.g. re-import guard)
        if any(s.doc_id == doc.doc_id for s in self._sections):
            log.warning("TOCService.load_document: %s already loaded", doc.doc_id[:8])
            return

        entries = detect_from_bookmarks(doc.path, doc.password)
        if not entries:
            # Auto-generate a single top-level entry.  Page number is set to 1
            # as a placeholder; recalculate_offsets() (called right after this)
            # will update it to the correct absolute start page.
            entries = [TOCEntry(title=doc.title, page_number=1, level=1)]
            log.debug(
                "TOCService.load_document: no bookmarks in %r — auto-entry created",
                doc.title,
            )
        else:
            log.info(
                "TOCService.load_document: %d entries from %r",
                len(entries),
                doc.title,
            )

        section = TOCSection(
            doc_id=doc.doc_id,
            doc_title=doc.title,
            page_offset=0,  # recalculated after append
            entries=entries,
        )
        self._sections.append(section)
        log.debug("TOCService: section added for %r", doc.title)
        # Emit sections_changed; caller should call recalculate_offsets afterwards
        self.sections_changed.emit()

    def remove_document(self, doc_id: str) -> None:
        """Drop the section for *doc_id*.

        Args:
            doc_id: ID of the document whose section should be removed.
        """
        before = len(self._sections)
        self._sections = [s for s in self._sections if s.doc_id != doc_id]
        if len(self._sections) == before:
            log.warning("TOCService.remove_document: unknown doc_id %s", doc_id[:8])
            return

        if self._active_doc_id == doc_id:
            self._active_doc_id = (
                self._sections[0].doc_id if self._sections else None
            )

        log.debug("TOCService: section removed for %s", doc_id[:8])
        self.sections_changed.emit()

    def reorder_documents(self, ordered_doc_ids: list[str]) -> None:
        """Reorder sections to match the given document ID sequence.

        Args:
            ordered_doc_ids: Document IDs in the desired order.
        """
        index = {s.doc_id: s for s in self._sections}
        new_order: list[TOCSection] = []
        for doc_id in ordered_doc_ids:
            if doc_id in index:
                new_order.append(index[doc_id])
        # Keep any sections whose doc_id wasn't listed (shouldn't happen, but safe)
        listed = set(ordered_doc_ids)
        for s in self._sections:
            if s.doc_id not in listed:
                new_order.append(s)
        self._sections = new_order
        log.debug("TOCService: sections reordered")
        self.sections_changed.emit()

    def recalculate_offsets(self, docs: list["PDFDocument"]) -> None:
        """Recompute ``page_offset`` for every section from current page counts.

        Also updates the page number of auto-generated root entries (single
        level-1 entry whose title matches the document title) to reflect the
        correct absolute start page (``page_offset + 1``).

        Args:
            docs: Documents in their current display order.
        """
        offset = 0
        doc_map = {d.doc_id: d for d in docs}
        for section in self._sections:
            section.page_offset = offset
            doc = doc_map.get(section.doc_id)
            if doc:
                # Fix auto-entry page number: if the section has exactly one
                # entry at level 1 whose title matches the doc title, it was
                # auto-generated and should track the absolute start page.
                if (
                    len(section.entries) == 1
                    and section.entries[0].level == 1
                    and section.entries[0].title == doc.title
                ):
                    section.entries[0].page_number = offset + 1
                offset += doc.page_count
        log.debug("TOCService.recalculate_offsets: %d sections updated", len(self._sections))
        self.sections_changed.emit()

    # ── Active section ────────────────────────────────────────────────────

    def set_active_doc(self, doc_id: str) -> None:
        """Set which document's section is being edited.

        Args:
            doc_id: The document to make active.
        """
        self._active_doc_id = doc_id

    @property
    def active_section(self) -> Optional[TOCSection]:
        """The section currently being edited, or None."""
        if self._active_doc_id is None:
            return None
        for s in self._sections:
            if s.doc_id == self._active_doc_id:
                return s
        return None

    # ── Read ──────────────────────────────────────────────────────────────

    @property
    def sections(self) -> list[TOCSection]:
        """Ordered list of TOCSections (read-only copy)."""
        return list(self._sections)

    @property
    def is_empty(self) -> bool:
        """True if no sections (no documents imported)."""
        return len(self._sections) == 0

    def get_entry(self, entry_id: str) -> Optional[TOCEntry]:
        """Find a TOCEntry by ID across all sections.

        Args:
            entry_id: The entry_id to look up.

        Returns:
            The matching TOCEntry, or None.
        """
        for section in self._sections:
            for e in section.entries:
                if e.entry_id == entry_id:
                    return e
        return None

    def get_section_for_entry(self, entry_id: str) -> Optional[TOCSection]:
        """Return the section that owns the given entry_id."""
        for section in self._sections:
            for e in section.entries:
                if e.entry_id == entry_id:
                    return section
        return None

    def merged_toc(self) -> list[list]:
        """Flat ``[level, title, page_number]`` list for PDF export.

        Page numbers are stored as absolute merged-sequence page numbers
        (entered directly by the user).  No offset arithmetic is applied.
        Only enabled entries are included.

        Returns:
            List of ``[level, title, page_number]`` triples.
        """
        result: list[list] = []
        for section in self._sections:
            for entry in section.entries:
                if not entry.enabled:
                    continue
                result.append([entry.level, entry.title, entry.page_number])
        return result

    # ── Backward-compat shim for legacy callers ───────────────────────────

    @property
    def entries(self) -> list[TOCEntry]:
        """Flat ordered list of all entries across all sections.

        Kept for backward compatibility with existing callers.
        For export, prefer ``merged_toc()``.
        """
        result: list[TOCEntry] = []
        for section in self._sections:
            result.extend(section.entries)
        return result

    def validate(self, total_pages: int) -> list[tuple[TOCEntry, str]]:
        """Validate all entries against *total_pages*.

        Args:
            total_pages: Total merged page count.

        Returns:
            List of (entry, problem) tuples.
        """
        return validate_entries(self.entries, total_pages)

    # ── Entry operations (scoped to active section) ───────────────────────

    def add_entry(
        self,
        title: str,
        page_number: int,
        level: int = 1,
        after_id: Optional[str] = None,
        doc_id: Optional[str] = None,
    ) -> TOCEntry:
        """Add a new TOC entry to the active (or specified) section.

        Args:
            title: Display title.
            page_number: 1-based page number (local, within the document).
            level: Nesting depth (1–TOC_MAX_LEVELS).
            after_id: Insert after this entry_id. Appends if None.
            doc_id: Target section by doc_id; falls back to active section.

        Returns:
            The newly created TOCEntry.
        """
        section = self._resolve_section(doc_id, after_id)
        if section is None:
            log.warning("TOCService.add_entry: no active section — entry dropped")
            return TOCEntry(title=title, page_number=page_number, level=level)

        level = max(1, min(level, TOC_MAX_LEVELS))
        entry = TOCEntry(title=title, page_number=page_number, level=level)

        if after_id is None:
            section.entries.append(entry)
        else:
            idx = self._index_in_section(section, after_id)
            if idx < 0:
                section.entries.append(entry)
            else:
                section.entries.insert(idx + 1, entry)

        log.debug("TOCService.add_entry: %r p%d (section=%s)", title, page_number, section.doc_title)
        self.toc_changed.emit()
        return entry

    def delete_entries(self, entry_ids: list[str]) -> None:
        """Remove entries by ID (and their descendants) from whichever section owns them.

        Args:
            entry_ids: List of entry_id strings to remove.
        """
        id_set = set(entry_ids)
        for section in self._sections:
            to_delete: set[str] = set()
            for e in section.entries:
                if e.entry_id in id_set or e.parent_id in to_delete:
                    to_delete.add(e.entry_id)
            before = len(section.entries)
            section.entries = [e for e in section.entries if e.entry_id not in to_delete]
            removed = before - len(section.entries)
            if removed:
                log.debug(
                    "TOCService.delete_entries: removed %d from section %s",
                    removed,
                    section.doc_title,
                )
        self.toc_changed.emit()

    def rename_entry(self, entry_id: str, new_title: str) -> bool:
        """Rename an entry's title.

        Returns:
            True if found and renamed, False otherwise.
        """
        entry = self.get_entry(entry_id)
        if entry is None:
            return False
        entry.title = new_title
        log.debug("TOCService.rename_entry: %s → %r", entry_id[:8], new_title)
        self.toc_changed.emit()
        return True

    def set_page(self, entry_id: str, page_number: int) -> bool:
        """Set the (local) page number of an entry.

        Returns:
            True if found and updated, False otherwise.
        """
        entry = self.get_entry(entry_id)
        if entry is None:
            return False
        entry.page_number = max(1, page_number)
        self.toc_changed.emit()
        return True

    def set_enabled(self, entry_id: str, enabled: bool) -> bool:
        """Enable or disable an entry.

        Returns:
            True if found, False otherwise.
        """
        entry = self.get_entry(entry_id)
        if entry is None:
            return False
        entry.enabled = enabled
        self.toc_changed.emit()
        return True

    def move_up(self, entry_id: str) -> bool:
        """Swap the entry with the one above it within its section.

        Returns:
            True if moved, False if already first or not found.
        """
        section = self.get_section_for_entry(entry_id)
        if section is None:
            return False
        idx = self._index_in_section(section, entry_id)
        if idx <= 0:
            return False
        section.entries[idx - 1], section.entries[idx] = (
            section.entries[idx],
            section.entries[idx - 1],
        )
        self.toc_changed.emit()
        return True

    def move_down(self, entry_id: str) -> bool:
        """Swap the entry with the one below it within its section.

        Returns:
            True if moved, False if already last or not found.
        """
        section = self.get_section_for_entry(entry_id)
        if section is None:
            return False
        idx = self._index_in_section(section, entry_id)
        if idx < 0 or idx >= len(section.entries) - 1:
            return False
        section.entries[idx], section.entries[idx + 1] = (
            section.entries[idx + 1],
            section.entries[idx],
        )
        self.toc_changed.emit()
        return True

    def indent(self, entry_id: str) -> bool:
        """Increase the nesting level of an entry (up to TOC_MAX_LEVELS).

        Returns:
            True if indented, False if already at max or not found.
        """
        entry = self.get_entry(entry_id)
        if entry is None or entry.level >= TOC_MAX_LEVELS:
            return False
        entry.level += 1
        self.toc_changed.emit()
        return True

    def outdent(self, entry_id: str) -> bool:
        """Decrease the nesting level of an entry (minimum 1).

        Returns:
            True if outdented, False if already at level 1 or not found.
        """
        entry = self.get_entry(entry_id)
        if entry is None or entry.level <= 1:
            return False
        entry.level -= 1
        self.toc_changed.emit()
        return True

    def clear(self) -> None:
        """Remove all sections and entries."""
        self._sections.clear()
        self._active_doc_id = None
        log.debug("TOCService.clear")
        self.sections_changed.emit()
        self.toc_changed.emit()

    def replace_section_entries(
        self, doc_id: str, entries: list[TOCEntry]
    ) -> bool:
        """Replace all entries in a section (used by experimental detection).

        Args:
            doc_id: The section to update.
            entries: New entry list.

        Returns:
            True if the section was found, False otherwise.
        """
        for section in self._sections:
            if section.doc_id == doc_id:
                section.entries = list(entries)
                log.info(
                    "TOCService.replace_section_entries: %d entries for %s",
                    len(entries),
                    section.doc_title,
                )
                self.toc_changed.emit()
                return True
        return False

    # ── Legacy load_from_fitz (kept for tests / compat) ──────────────────

    def load_from_fitz(self, toc: list[list]) -> None:
        """Replace current entries with those from a PyMuPDF toc list.

        This bypasses the section model and only makes sense in a
        single-document context (e.g. tests).  Clears all existing sections
        and creates one synthetic section.

        Args:
            toc: List of ``[level, title, page]`` triples.
        """
        entries = fitz_toc_to_entries(toc)
        if self._sections:
            self._sections[0].entries = entries
        else:
            self._sections = [
                TOCSection(
                    doc_id="legacy",
                    doc_title="Document",
                    page_offset=0,
                    entries=entries,
                )
            ]
        log.info("TOCService.load_from_fitz: loaded %d entries", len(entries))
        self.toc_changed.emit()

    # ── Internal helpers ──────────────────────────────────────────────────

    def _resolve_section(
        self,
        doc_id: Optional[str],
        entry_id: Optional[str],
    ) -> Optional[TOCSection]:
        """Find the right section for a mutation.

        Priority:
        1. ``doc_id`` if given
        2. Section that owns ``entry_id`` (for after_id)
        3. Active section
        4. First section (fallback)
        """
        if doc_id:
            for s in self._sections:
                if s.doc_id == doc_id:
                    return s

        if entry_id:
            s = self.get_section_for_entry(entry_id)
            if s:
                return s

        if self._active_doc_id:
            for s in self._sections:
                if s.doc_id == self._active_doc_id:
                    return s

        return self._sections[0] if self._sections else None

    @staticmethod
    def _index_in_section(section: TOCSection, entry_id: str) -> int:
        """Return the list index of *entry_id* within *section*, or -1."""
        for i, e in enumerate(section.entries):
            if e.entry_id == entry_id:
                return i
        return -1
