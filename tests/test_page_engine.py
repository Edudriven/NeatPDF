"""
tests/test_page_engine.py — Unit tests for engines/page_engine.py.

Run with:
    QT_QPA_PLATFORM=offscreen pytest tests/test_page_engine.py -v
"""

from __future__ import annotations

from pathlib import Path


from engines.page_engine import (
    BLANK_DOC_ID,
    BLANK_PATH,
    copy_pages,
    delete_pages,
    extract_pages,
    insert_blank_page,
    move_pages,
    rotate_pages,
)
from models.page_item import PageItem


# ── Helpers ───────────────────────────────────────────────────────────────────

_FAKE_PATH = Path("/fake/doc.pdf")


def _make_pages(count: int, doc_id: str = "doc-A") -> list[PageItem]:
    """Return a list of *count* PageItems belonging to *doc_id*."""
    return [
        PageItem(
            source_path=_FAKE_PATH,
            source_page_index=i,
            display_index=i,
            document_id=doc_id,
        )
        for i in range(count)
    ]


def _display_indices(pages: list[PageItem]) -> list[int]:
    return [p.display_index for p in pages]


def _source_indices(pages: list[PageItem]) -> list[int]:
    return [p.source_page_index for p in pages]


# ── delete_pages ──────────────────────────────────────────────────────────────

class TestDeletePages:
    def test_delete_single(self):
        pages = _make_pages(5)
        result = delete_pages(pages, [2])
        assert len(result) == 4
        assert _source_indices(result) == [0, 1, 3, 4]

    def test_delete_multiple(self):
        pages = _make_pages(5)
        result = delete_pages(pages, [0, 2, 4])
        assert len(result) == 2
        assert _source_indices(result) == [1, 3]

    def test_delete_all(self):
        pages = _make_pages(3)
        result = delete_pages(pages, [0, 1, 2])
        assert result == []

    def test_delete_first(self):
        pages = _make_pages(4)
        result = delete_pages(pages, [0])
        assert _source_indices(result) == [1, 2, 3]

    def test_delete_last(self):
        pages = _make_pages(4)
        result = delete_pages(pages, [3])
        assert _source_indices(result) == [0, 1, 2]

    def test_display_indices_resequenced(self):
        pages = _make_pages(5)
        result = delete_pages(pages, [1, 3])
        assert _display_indices(result) == [0, 1, 2]

    def test_empty_indices_no_change(self):
        pages = _make_pages(3)
        result = delete_pages(pages, [])
        assert _source_indices(result) == [0, 1, 2]

    def test_original_not_mutated(self):
        pages = _make_pages(4)
        original_len = len(pages)
        delete_pages(pages, [1])
        assert len(pages) == original_len


# ── rotate_pages ──────────────────────────────────────────────────────────────

class TestRotatePages:
    def test_rotate_cw_single(self):
        pages = _make_pages(3)
        result = rotate_pages(pages, [1], "cw")
        assert result[0].rotation == 0
        assert result[1].rotation == 90
        assert result[2].rotation == 0

    def test_rotate_ccw_single(self):
        pages = _make_pages(3)
        result = rotate_pages(pages, [0], "ccw")
        assert result[0].rotation == 270

    def test_rotate_cw_multiple(self):
        pages = _make_pages(4)
        result = rotate_pages(pages, [0, 2], "cw")
        assert result[0].rotation == 90
        assert result[1].rotation == 0
        assert result[2].rotation == 90
        assert result[3].rotation == 0

    def test_rotate_cw_full_circle(self):
        pages = _make_pages(1)
        result = pages
        for _ in range(4):
            result = rotate_pages(result, [0], "cw")
        assert result[0].rotation == 0

    def test_rotate_ccw_full_circle(self):
        pages = _make_pages(1)
        result = pages
        for _ in range(4):
            result = rotate_pages(result, [0], "ccw")
        assert result[0].rotation == 0

    def test_cw_then_ccw_cancels(self):
        pages = _make_pages(1)
        after_cw = rotate_pages(pages, [0], "cw")
        after_ccw = rotate_pages(after_cw, [0], "ccw")
        assert after_ccw[0].rotation == 0

    def test_rotate_out_of_range_ignored(self):
        pages = _make_pages(2)
        result = rotate_pages(pages, [5], "cw")
        assert all(p.rotation == 0 for p in result)

    def test_original_not_mutated(self):
        pages = _make_pages(3)
        rotate_pages(pages, [0, 1, 2], "cw")
        assert all(p.rotation == 0 for p in pages)


# ── move_pages ────────────────────────────────────────────────────────────────

class TestMovePages:
    def test_move_single_forward(self):
        # target_index=3 means insert before position 3 of the original list.
        # Original: [0,1,2,3,4]. Remove 0 → [1,2,3,4]. Insert before original-pos 3
        # → adjusted insert_at = 3-1 = 2 → [1,2,0,3,4].
        pages = _make_pages(5)
        result = move_pages(pages, [0], 3)
        assert _source_indices(result) == [1, 2, 0, 3, 4]

    def test_move_single_backward(self):
        pages = _make_pages(5)
        result = move_pages(pages, [4], 1)
        assert _source_indices(result) == [0, 4, 1, 2, 3]

    def test_move_to_beginning(self):
        pages = _make_pages(4)
        result = move_pages(pages, [3], 0)
        assert _source_indices(result) == [3, 0, 1, 2]

    def test_move_to_end(self):
        pages = _make_pages(4)
        result = move_pages(pages, [0], 4)
        assert _source_indices(result) == [1, 2, 3, 0]

    def test_move_multiple_contiguous(self):
        # target_index=4 means insert before original position 4.
        # Original: [0,1,2,3,4]. Remove [1,2] → [0,3,4]. Insert before original-pos 4
        # → adjusted insert_at = 4-2 = 2 → [0,3,1,2,4].
        pages = _make_pages(5)
        result = move_pages(pages, [1, 2], 4)
        assert _source_indices(result) == [0, 3, 1, 2, 4]

    def test_move_multiple_non_contiguous(self):
        pages = _make_pages(5)
        result = move_pages(pages, [0, 4], 2)
        assert len(result) == 5
        # Pages 0 and 4 should appear together at the insertion point
        moved_srcs = [p.source_page_index for p in result if p.source_page_index in {0, 4}]
        assert moved_srcs == [0, 4]

    def test_move_same_position_no_change(self):
        pages = _make_pages(4)
        result = move_pages(pages, [2], 2)
        assert _source_indices(result) == [0, 1, 2, 3]

    def test_display_indices_resequenced(self):
        pages = _make_pages(5)
        result = move_pages(pages, [0], 3)
        assert _display_indices(result) == [0, 1, 2, 3, 4]

    def test_length_preserved(self):
        pages = _make_pages(6)
        result = move_pages(pages, [1, 3, 5], 2)
        assert len(result) == 6

    def test_original_not_mutated(self):
        pages = _make_pages(4)
        original_src = _source_indices(pages)
        move_pages(pages, [0], 3)
        assert _source_indices(pages) == original_src


# ── copy_pages ────────────────────────────────────────────────────────────────

class TestCopyPages:
    def test_copy_single_middle(self):
        pages = _make_pages(4)
        result = copy_pages(pages, [1], 1)
        # Original pages + one copy of page 1 inserted after index 1
        assert len(result) == 5
        assert result[2].source_page_index == 1

    def test_copy_first_after_last(self):
        pages = _make_pages(3)
        result = copy_pages(pages, [0], 2)
        assert len(result) == 4
        assert result[3].source_page_index == 0

    def test_copy_multiple(self):
        pages = _make_pages(4)
        result = copy_pages(pages, [0, 2], 1)
        assert len(result) == 6

    def test_copy_after_minus_one_prepends(self):
        pages = _make_pages(3)
        result = copy_pages(pages, [2], -1)
        assert len(result) == 4
        assert result[0].source_page_index == 2

    def test_copies_are_independent(self):
        pages = _make_pages(2)
        result = copy_pages(pages, [0], 0)
        result[0].rotation = 90
        # Copy at index 1 should not be affected
        assert result[1].rotation == 0

    def test_display_indices_resequenced(self):
        pages = _make_pages(3)
        result = copy_pages(pages, [1], 1)
        assert _display_indices(result) == [0, 1, 2, 3]

    def test_original_not_mutated(self):
        pages = _make_pages(4)
        original_len = len(pages)
        copy_pages(pages, [0], 0)
        assert len(pages) == original_len


# ── insert_blank_page ─────────────────────────────────────────────────────────

class TestInsertBlankPage:
    def test_insert_after_middle(self):
        pages = _make_pages(4)
        result = insert_blank_page(pages, 1, BLANK_DOC_ID)
        assert len(result) == 5
        assert result[2].is_blank is True

    def test_insert_after_last(self):
        pages = _make_pages(3)
        result = insert_blank_page(pages, 2, BLANK_DOC_ID)
        assert len(result) == 4
        assert result[3].is_blank is True

    def test_insert_prepend(self):
        pages = _make_pages(3)
        result = insert_blank_page(pages, -1, BLANK_DOC_ID)
        assert len(result) == 4
        assert result[0].is_blank is True

    def test_blank_has_correct_doc_id(self):
        pages = _make_pages(2)
        result = insert_blank_page(pages, 0, "custom-doc-id")
        blank = next(p for p in result if p.is_blank)
        assert blank.document_id == "custom-doc-id"

    def test_blank_has_blank_path(self):
        pages = _make_pages(2)
        result = insert_blank_page(pages, 0, BLANK_DOC_ID)
        blank = next(p for p in result if p.is_blank)
        assert blank.source_path == BLANK_PATH

    def test_blank_source_page_index_is_minus_one(self):
        pages = _make_pages(2)
        result = insert_blank_page(pages, 0, BLANK_DOC_ID)
        blank = next(p for p in result if p.is_blank)
        assert blank.source_page_index == -1

    def test_display_indices_resequenced(self):
        pages = _make_pages(3)
        result = insert_blank_page(pages, 1, BLANK_DOC_ID)
        assert _display_indices(result) == [0, 1, 2, 3]

    def test_original_not_mutated(self):
        pages = _make_pages(3)
        insert_blank_page(pages, 0, BLANK_DOC_ID)
        assert len(pages) == 3


# ── extract_pages ─────────────────────────────────────────────────────────────

class TestExtractPages:
    def test_extract_single(self):
        pages = _make_pages(5)
        result = extract_pages(pages, [2])
        assert len(result) == 1
        assert result[0].source_page_index == 2

    def test_extract_multiple(self):
        pages = _make_pages(5)
        result = extract_pages(pages, [0, 2, 4])
        assert len(result) == 3
        assert _source_indices(result) == [0, 2, 4]

    def test_extract_does_not_remove_from_original(self):
        pages = _make_pages(5)
        extract_pages(pages, [1, 3])
        assert len(pages) == 5

    def test_extract_display_indices_start_at_zero(self):
        pages = _make_pages(5)
        result = extract_pages(pages, [2, 4])
        assert _display_indices(result) == [0, 1]

    def test_extract_all(self):
        pages = _make_pages(4)
        result = extract_pages(pages, [0, 1, 2, 3])
        assert len(result) == 4

    def test_extract_empty_indices(self):
        pages = _make_pages(4)
        result = extract_pages(pages, [])
        assert result == []

    def test_extract_out_of_range_skipped(self):
        pages = _make_pages(3)
        result = extract_pages(pages, [1, 10])
        assert len(result) == 1
        assert result[0].source_page_index == 1

    def test_extract_copies_are_independent(self):
        pages = _make_pages(3)
        result = extract_pages(pages, [0])
        result[0].rotation = 90
        assert pages[0].rotation == 0
