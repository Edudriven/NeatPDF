"""
engines/page_engine.py — Pure page-level operations on a PageItem list.

All functions are stateless: they take a list of PageItem objects and
return a new list (copy-on-write style).  This makes undo trivial —
the undo command just restores the previous list snapshot.

The engine has no knowledge of Qt or any GUI concept.
"""

from __future__ import annotations

import copy
import logging
from pathlib import Path

from models.page_item import PageItem

log = logging.getLogger(__name__)

# Sentinel document_id used for blank pages inserted by the user.
BLANK_DOC_ID = "blank"
# Sentinel path used for blank pages.
BLANK_PATH = Path("/dev/null")


def _reindex(pages: list[PageItem]) -> list[PageItem]:
    """Return *pages* with display_index set to sequential 0-based values."""
    for i, p in enumerate(pages):
        p.display_index = i
    return pages


# ── Operations ────────────────────────────────────────────────────────────────

def delete_pages(pages: list[PageItem], indices: list[int]) -> list[PageItem]:
    """Remove pages at the given display indices.

    Args:
        pages: Current page list.
        indices: 0-based display indices to remove.

    Returns:
        New page list with the selected pages removed.
    """
    index_set = set(indices)
    result = [copy.copy(p) for i, p in enumerate(pages) if i not in index_set]
    _reindex(result)
    log.debug("delete_pages: removed %d pages", len(indices))
    return result


def rotate_pages(
    pages: list[PageItem], indices: list[int], direction: str
) -> list[PageItem]:
    """Rotate pages at the given indices.

    Args:
        pages: Current page list.
        indices: 0-based display indices to rotate.
        direction: ``"cw"`` (90° clockwise) or ``"ccw"`` (90° counter-clockwise).

    Returns:
        New page list with updated rotations.
    """
    result = [copy.copy(p) for p in pages]
    for i in indices:
        if 0 <= i < len(result):
            if direction == "cw":
                result[i].rotate_cw()
            else:
                result[i].rotate_ccw()
    log.debug("rotate_pages: rotated %d pages %s", len(indices), direction)
    return result


def move_pages(
    pages: list[PageItem], indices: list[int], target_index: int
) -> list[PageItem]:
    """Move the selected pages so they appear before *target_index*.

    The target is interpreted as the position in the *original* list
    (before extraction), matching standard drag-and-drop semantics.

    Args:
        pages: Current page list.
        indices: Sorted 0-based display indices to move.
        target_index: Insertion point in the original list (0 = beginning).

    Returns:
        New page list with pages relocated.
    """
    index_set = set(indices)
    moving = [copy.copy(pages[i]) for i in sorted(indices)]
    staying = [copy.copy(p) for i, p in enumerate(pages) if i not in index_set]

    # Adjust target for pages removed before it
    removed_before = sum(1 for i in indices if i < target_index)
    insert_at = max(0, min(target_index - removed_before, len(staying)))

    result = staying[:insert_at] + moving + staying[insert_at:]
    _reindex(result)
    log.debug(
        "move_pages: moved %d pages to position %d", len(indices), insert_at
    )
    return result


def copy_pages(
    pages: list[PageItem], indices: list[int], after_index: int
) -> list[PageItem]:
    """Insert copies of the selected pages immediately after *after_index*.

    Args:
        pages: Current page list.
        indices: 0-based display indices to copy.
        after_index: Copies are inserted after this position (use -1 for start).

    Returns:
        New page list with the copies inserted.
    """
    copies = [copy.copy(pages[i]) for i in sorted(indices) if 0 <= i < len(pages)]
    insert_at = after_index + 1
    result = (
        [copy.copy(p) for p in pages[:insert_at]]
        + copies
        + [copy.copy(p) for p in pages[insert_at:]]
    )
    _reindex(result)
    log.debug(
        "copy_pages: copied %d pages after position %d", len(copies), after_index
    )
    return result


def insert_blank_page(
    pages: list[PageItem],
    after_index: int,
    document_id: str,
    source_path: Path = BLANK_PATH,
) -> list[PageItem]:
    """Insert a single blank page after *after_index*.

    Args:
        pages: Current page list.
        after_index: Blank page is inserted after this position (use -1 to prepend).
        document_id: Document ID to assign (use BLANK_DOC_ID for standalone blanks).
        source_path: Source PDF path (use BLANK_PATH for blanks).

    Returns:
        New page list with the blank page inserted.
    """
    blank = PageItem(
        source_path=source_path,
        source_page_index=-1,
        display_index=0,          # will be reindexed
        document_id=document_id,
        is_blank=True,
    )
    insert_at = after_index + 1
    result = (
        [copy.copy(p) for p in pages[:insert_at]]
        + [blank]
        + [copy.copy(p) for p in pages[insert_at:]]
    )
    _reindex(result)
    log.debug("insert_blank_page: inserted at position %d", insert_at)
    return result


def extract_pages(
    pages: list[PageItem], indices: list[int]
) -> list[PageItem]:
    """Return the subset of pages at the given indices (for export).

    This does NOT modify *pages*; it only returns the extracted subset.
    The display_index values on the returned list are resequenced from 0.

    Args:
        pages: Current page list.
        indices: 0-based display indices to extract.

    Returns:
        New list of PageItem copies for the extracted pages.
    """
    extracted = [copy.copy(pages[i]) for i in sorted(indices) if 0 <= i < len(pages)]
    _reindex(extracted)
    log.debug("extract_pages: extracted %d pages", len(extracted))
    return extracted
