"""
models/toc_section.py — Groups TOC entries belonging to one imported document.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from models.toc_entry import TOCEntry


@dataclass
class TOCSection:
    """TOC entries belonging to one imported PDF document.

    Attributes:
        doc_id: Matches ``PDFDocument.doc_id`` for the owning document.
        doc_title: Display name (filename stem) of the owning document.
        page_offset: 0-based index of the first page of this document in
            the merged output sequence.  Recalculated whenever document
            order or page counts change.
        entries: Ordered list of TOCEntry objects for this document.
            If the source PDF had no bookmarks this is initialised to a
            single auto-generated top-level entry pointing to page 1.
    """

    doc_id: str
    doc_title: str
    page_offset: int = 0
    entries: list[TOCEntry] = field(default_factory=list)

    def __repr__(self) -> str:
        return (
            f"TOCSection(doc={self.doc_title!r}, "
            f"offset={self.page_offset}, entries={len(self.entries)})"
        )
