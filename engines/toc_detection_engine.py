"""
engines/toc_detection_engine.py — Automatic TOC detection for imported PDFs.

Two detection strategies:

1. **Bookmark extraction** — reads the PDF's built-in outline (if any)
   and converts it to TOCEntry objects.  Fast and accurate.  Used
   automatically on every import.

2. **Heading heuristics** — scans every page for text spans that look like
   headings based on font size and font flags (bold).  Produces candidate
   entries ranked by confidence.  Results are always presented as
   suggestions; the user reviews and edits before committing.  Triggered
   only via right-click → "Detect from content (experimental)".

Both strategies are stateless functions.  They take a file path (and
optional password) and return a list of TOCEntry objects.

``detect_combined`` has been removed.  Auto-fallback to heading heuristics
on import is no longer supported; heading detection is experimental and
always requires explicit user confirmation.
"""

from __future__ import annotations

import logging
from pathlib import Path

import fitz  # PyMuPDF

from engines.toc_engine import fitz_toc_to_entries
from models.toc_entry import TOCEntry

log = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────

# Minimum font size (points) to consider a span as a heading candidate
_MIN_HEADING_SIZE = 11.0
# Minimum ratio of span font size to median body font size
_SIZE_RATIO_THRESHOLD = 1.15
# PyMuPDF font flag bit for bold text
_FLAG_BOLD = 1 << 4   # bit 4 of span["flags"]
# Maximum characters for a heading (longer spans are likely body text)
_MAX_HEADING_CHARS = 120
# How many pages to scan in heuristic mode (None = all)
_MAX_SCAN_PAGES: int | None = None


# ── Public API ────────────────────────────────────────────────────────────────

def detect_from_bookmarks(
    path: Path, password: str = ""
) -> list[TOCEntry]:
    """Extract TOC entries from the PDF's built-in bookmark outline.

    Args:
        path: Absolute path to the PDF file.
        password: Password for encrypted PDFs.

    Returns:
        List of TOCEntry objects.  Empty list if the PDF has no bookmarks
        or cannot be opened.
    """
    try:
        doc = fitz.open(str(path))
    except Exception as exc:
        log.warning("detect_from_bookmarks: cannot open %s — %s", path.name, exc)
        return []

    try:
        if doc.is_encrypted and not doc.authenticate(password):
            log.warning("detect_from_bookmarks: wrong password for %s", path.name)
            return []

        toc = doc.get_toc(simple=True)   # [[level, title, page], ...]
        if not toc:
            log.debug("detect_from_bookmarks: no bookmarks in %s", path.name)
            return []

        entries = fitz_toc_to_entries(toc)
        log.info(
            "detect_from_bookmarks: %d entries from %s", len(entries), path.name
        )
        return entries
    finally:
        doc.close()


def detect_from_headings(
    path: Path,
    password: str = "",
    page_offset: int = 0,
) -> list[TOCEntry]:
    """Detect headings by font-size and bold heuristics.

    This is an *experimental* feature.  Results are always shown to the
    user for review before being committed.

    Scans text spans on each page.  Spans significantly larger or bolder
    than the median body text are treated as heading candidates.

    Args:
        path: Absolute path to the PDF file.
        password: Password for encrypted PDFs.
        page_offset: Added to every detected page number (use when the PDF
            is not the first document in the merged sequence).

    Returns:
        List of TOCEntry objects.  Each entry's ``level`` is assigned
        based on relative font size (larger → lower level number = higher).
        Empty list on error or if no headings are found.
    """
    try:
        doc = fitz.open(str(path))
    except Exception as exc:
        log.warning("detect_from_headings: cannot open %s — %s", path.name, exc)
        return []

    try:
        if doc.is_encrypted and not doc.authenticate(password):
            log.warning("detect_from_headings: wrong password for %s", path.name)
            return []

        page_count = doc.page_count
        scan_limit = (
            min(_MAX_SCAN_PAGES, page_count) if _MAX_SCAN_PAGES else page_count
        )

        # Pass 1: collect all font sizes to compute median body size
        all_sizes: list[float] = []
        for pno in range(scan_limit):
            page = doc[pno]
            for block in page.get_text("dict")["blocks"]:
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        size = span.get("size", 0.0)
                        if size > 0:
                            all_sizes.append(size)

        if not all_sizes:
            return []

        all_sizes.sort()
        median_size = all_sizes[len(all_sizes) // 2]
        log.debug(
            "detect_from_headings: median body size=%.1f in %s",
            median_size,
            path.name,
        )

        # Pass 2: collect candidate heading spans
        candidates: list[dict] = []
        seen_titles: set[str] = set()

        for pno in range(scan_limit):
            page = doc[pno]
            for block in page.get_text("dict")["blocks"]:
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        size = span.get("size", 0.0)
                        flags = span.get("flags", 0)
                        text = span.get("text", "").strip()

                        if not text or len(text) > _MAX_HEADING_CHARS:
                            continue
                        if text in seen_titles:
                            continue

                        is_large = size >= _MIN_HEADING_SIZE and (
                            size >= median_size * _SIZE_RATIO_THRESHOLD
                        )
                        is_bold = bool(flags & _FLAG_BOLD)

                        if is_large or (is_bold and size >= _MIN_HEADING_SIZE):
                            candidates.append(
                                {
                                    "text": text,
                                    "size": size,
                                    "bold": is_bold,
                                    "page": pno + 1,  # 1-based
                                }
                            )
                            seen_titles.add(text)

        if not candidates:
            log.debug("detect_from_headings: no candidates in %s", path.name)
            return []

        # Assign levels based on distinct font sizes (largest → level 1)
        unique_sizes = sorted({c["size"] for c in candidates}, reverse=True)
        size_to_level = {s: (i + 1) for i, s in enumerate(unique_sizes[:6])}

        entries: list[TOCEntry] = []
        for c in candidates:
            level = size_to_level.get(c["size"], 6)
            page_number = c["page"] + page_offset
            entries.append(
                TOCEntry(
                    title=c["text"],
                    page_number=page_number,
                    level=level,
                )
            )

        log.info(
            "detect_from_headings: %d candidates in %s", len(entries), path.name
        )
        return entries

    finally:
        doc.close()
