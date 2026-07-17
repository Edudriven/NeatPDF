"""
engines/toc_engine.py — TOC generation and PDF bookmark embedding.

Responsibilities:
  - Convert a flat list of TOCEntry objects into a PyMuPDF table-of-contents
    structure and embed it into an open fitz.Document.
  - Validate TOC entry page numbers against a total page count.

The engine is stateless: it takes data in, produces data or side-effects out.
It has no knowledge of Qt or any GUI concept.

Note: ``render_toc_page`` has been removed.  The TOC panel no longer inserts
a TOC page into the PDF; only the PDF sidebar outline (bookmarks) is managed.
"""

from __future__ import annotations

import logging

import fitz  # PyMuPDF

from models.toc_entry import TOCEntry

log = logging.getLogger(__name__)


# ── Bookmark embedding ────────────────────────────────────────────────────────

def embed_bookmarks(doc: fitz.Document, entries: list[TOCEntry]) -> None:
    """Write TOCEntry objects as PDF bookmarks (outline) into *doc*.

    Only enabled entries are embedded.  Page numbers are clamped to the
    valid range [1, doc.page_count] so out-of-range entries don't crash.

    Args:
        doc: An open, writable fitz.Document.
        entries: Flat ordered list of TOCEntry objects.
    """
    enabled = [e for e in entries if e.enabled]
    if not enabled:
        doc.set_toc([])
        log.debug("embed_bookmarks: no enabled entries — TOC cleared")
        return

    toc: list[list] = []
    for entry in enabled:
        page = max(1, min(entry.page_number, doc.page_count))
        # PyMuPDF toc format: [level, title, page_number (1-based)]
        toc.append([entry.level, entry.title, page])

    doc.set_toc(toc)
    log.info("embed_bookmarks: embedded %d bookmark(s)", len(toc))


# ── Validation ────────────────────────────────────────────────────────────────

def validate_entries(
    entries: list[TOCEntry], total_pages: int
) -> list[tuple[TOCEntry, str]]:
    """Check TOC entries for common problems.

    Args:
        entries: List of TOCEntry objects to validate.
        total_pages: Total page count of the merged PDF.

    Returns:
        List of (entry, problem_description) for any invalid entries.
        Empty list if all entries are valid.
    """
    problems: list[tuple[TOCEntry, str]] = []

    for entry in entries:
        if not entry.title.strip():
            problems.append((entry, "Title is empty"))
        if entry.page_number < 1:
            problems.append((entry, f"Page number {entry.page_number} < 1"))
        elif total_pages > 0 and entry.page_number > total_pages:
            problems.append(
                (entry, f"Page number {entry.page_number} exceeds total {total_pages}")
            )
        if entry.level < 1:
            problems.append((entry, f"Level {entry.level} < 1"))

    return problems


# ── Flat ↔ tree conversion helpers ───────────────────────────────────────────

def entries_to_fitz_toc(entries: list[TOCEntry]) -> list[list]:
    """Convert enabled TOCEntry objects to PyMuPDF toc format.

    Args:
        entries: Flat ordered list of TOCEntry objects.

    Returns:
        List of ``[level, title, page_number]`` triples (enabled only).
    """
    return [
        [e.level, e.title, e.page_number]
        for e in entries
        if e.enabled
    ]


def fitz_toc_to_entries(toc: list[list]) -> list[TOCEntry]:
    """Convert a PyMuPDF toc structure to TOCEntry objects.

    Args:
        toc: List of ``[level, title, page]`` triples from fitz.

    Returns:
        Flat list of TOCEntry objects with parent_id set according to hierarchy.
    """
    entries: list[TOCEntry] = []
    # Stack of (level, entry_id) to track parent chain
    stack: list[tuple[int, str]] = []

    for level, title, page in toc:
        # Pop stack until we find a parent at a lower level
        while stack and stack[-1][0] >= level:
            stack.pop()

        parent_id = stack[-1][1] if stack else None
        entry = TOCEntry(
            title=str(title),
            page_number=int(page),
            level=int(level),
            parent_id=parent_id,
        )
        entries.append(entry)
        stack.append((level, entry.entry_id))

    return entries
