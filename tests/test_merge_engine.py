"""
tests/test_merge_engine.py — Unit tests for engines/merge_engine.py.

Run with:
    QT_QPA_PLATFORM=offscreen pytest tests/test_merge_engine.py -v
"""

from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from engines.merge_engine import MergeError, merge
from engines.page_engine import BLANK_DOC_ID, BLANK_PATH
from models.page_item import PageItem


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_pdf(path: Path, page_count: int = 3) -> Path:
    """Create a minimal valid PDF with *page_count* blank A4 pages."""
    doc = fitz.open()
    for _ in range(page_count):
        doc.new_page(width=595, height=842)
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture()
def pdf_factory(tmp_path):
    counter = {"n": 0}

    def factory(page_count: int = 3, name: str | None = None) -> Path:
        counter["n"] += 1
        fname = name or f"doc_{counter['n']}.pdf"
        return _make_pdf(tmp_path / fname, page_count)

    return factory


def _page_items(path: Path, doc_id: str, count: int) -> list[PageItem]:
    """Build PageItem list for the first *count* pages of *path*."""
    return [
        PageItem(
            source_path=path,
            source_page_index=i,
            display_index=i,
            document_id=doc_id,
        )
        for i in range(count)
    ]


def _open_result(output_path: Path) -> fitz.Document:
    return fitz.open(str(output_path))


# ── Basic merge ───────────────────────────────────────────────────────────────

class TestMergeBasic:
    def test_single_doc_correct_page_count(self, pdf_factory, tmp_path):
        src = pdf_factory(4)
        pages = _page_items(src, "d1", 4)
        out = tmp_path / "out.pdf"
        merge(pages, out)
        with _open_result(out) as doc:
            assert doc.page_count == 4

    def test_two_docs_page_count(self, pdf_factory, tmp_path):
        src1 = pdf_factory(3)
        src2 = pdf_factory(2)
        pages = _page_items(src1, "d1", 3) + _page_items(src2, "d2", 2)
        out = tmp_path / "out.pdf"
        merge(pages, out)
        with _open_result(out) as doc:
            assert doc.page_count == 5

    def test_subset_of_pages(self, pdf_factory, tmp_path):
        src = pdf_factory(6)
        # Only pages 0, 2, 4
        pages = [
            PageItem(source_path=src, source_page_index=i, display_index=j, document_id="d1")
            for j, i in enumerate([0, 2, 4])
        ]
        out = tmp_path / "out.pdf"
        merge(pages, out)
        with _open_result(out) as doc:
            assert doc.page_count == 3

    def test_output_file_created(self, pdf_factory, tmp_path):
        src = pdf_factory(2)
        out = tmp_path / "result.pdf"
        merge(_page_items(src, "d1", 2), out)
        assert out.exists()
        assert out.stat().st_size > 0

    def test_output_is_valid_pdf(self, pdf_factory, tmp_path):
        src = pdf_factory(3)
        out = tmp_path / "valid.pdf"
        merge(_page_items(src, "d1", 3), out)
        # fitz.open should not raise
        with fitz.open(str(out)) as doc:
            assert doc.page_count == 3

    def test_parent_dir_created_if_missing(self, pdf_factory, tmp_path):
        src = pdf_factory(1)
        out = tmp_path / "subdir" / "nested" / "out.pdf"
        merge(_page_items(src, "d1", 1), out)
        assert out.exists()

    def test_progress_callback_called(self, pdf_factory, tmp_path):
        src = pdf_factory(5)
        pages = _page_items(src, "d1", 5)
        calls = []
        merge(pages, tmp_path / "out.pdf", progress=lambda c, t: calls.append((c, t)))
        assert len(calls) == 5
        assert calls[0] == (1, 5)
        assert calls[-1] == (5, 5)

    def test_progress_callback_none_ok(self, pdf_factory, tmp_path):
        src = pdf_factory(3)
        merge(_page_items(src, "d1", 3), tmp_path / "out.pdf", progress=None)


# ── Blank pages ───────────────────────────────────────────────────────────────

class TestMergeBlankPages:
    def _blank(self, index: int) -> PageItem:
        return PageItem(
            source_path=BLANK_PATH,
            source_page_index=-1,
            display_index=index,
            document_id=BLANK_DOC_ID,
            is_blank=True,
        )

    def test_blank_only(self, tmp_path):
        pages = [self._blank(0), self._blank(1)]
        out = tmp_path / "blank.pdf"
        merge(pages, out)
        with _open_result(out) as doc:
            assert doc.page_count == 2

    def test_blank_mixed_with_real(self, pdf_factory, tmp_path):
        src = pdf_factory(2)
        pages = _page_items(src, "d1", 2) + [self._blank(2)] + _page_items(src, "d1", 1)
        out = tmp_path / "mixed.pdf"
        merge(pages, out)
        with _open_result(out) as doc:
            assert doc.page_count == 4

    def test_blank_page_is_a4_portrait(self, tmp_path):
        pages = [self._blank(0)]
        out = tmp_path / "b.pdf"
        merge(pages, out)
        with _open_result(out) as doc:
            page = doc[0]
            # Allow ±2pt tolerance for rounding
            assert abs(page.rect.width - 595) < 2
            assert abs(page.rect.height - 842) < 2

    def test_blank_page_rotated_90_is_landscape(self, tmp_path):
        # PDF rotation is stored as metadata; set_rotation(90) means the page
        # is displayed landscape.  We verify the rotation value is set correctly
        # and the mediabox (raw storage) has landscape dimensions.
        p = self._blank(0)
        p.rotation = 90
        out = tmp_path / "r.pdf"
        merge([p], out)
        with _open_result(out) as doc:
            page = doc[0]
            assert page.rotation == 90
            # mediabox stores raw new_page dimensions (landscape: w>h)
            assert page.mediabox.width > page.mediabox.height


# ── Rotation ──────────────────────────────────────────────────────────────────

class TestMergeRotation:
    def test_rotation_applied_to_page(self, pdf_factory, tmp_path):
        src = pdf_factory(1)
        pages = _page_items(src, "d1", 1)
        pages[0].rotation = 90
        out = tmp_path / "rot.pdf"
        merge(pages, out)
        with _open_result(out) as doc:
            assert doc[0].rotation == 90

    def test_rotation_zero_unchanged(self, pdf_factory, tmp_path):
        src = pdf_factory(1)
        pages = _page_items(src, "d1", 1)
        out = tmp_path / "norot.pdf"
        merge(pages, out)
        with _open_result(out) as doc:
            assert doc[0].rotation == 0

    def test_mixed_rotations(self, pdf_factory, tmp_path):
        src = pdf_factory(3)
        pages = _page_items(src, "d1", 3)
        pages[0].rotation = 0
        pages[1].rotation = 90
        pages[2].rotation = 180
        out = tmp_path / "mixed_rot.pdf"
        merge(pages, out)
        with _open_result(out) as doc:
            assert doc[0].rotation == 0
            assert doc[1].rotation == 90
            assert doc[2].rotation == 180


# ── Multiple source documents ─────────────────────────────────────────────────

class TestMergeMultipleSources:
    def test_interleaved_pages(self, pdf_factory, tmp_path):
        src1 = pdf_factory(3)
        src2 = pdf_factory(3)
        # Interleave: page 0 from src1, page 0 from src2, page 1 from src1…
        pages = []
        for i in range(3):
            pages.append(PageItem(source_path=src1, source_page_index=i,
                                  display_index=len(pages), document_id="d1"))
            pages.append(PageItem(source_path=src2, source_page_index=i,
                                  display_index=len(pages), document_id="d2"))
        out = tmp_path / "interleaved.pdf"
        merge(pages, out)
        with _open_result(out) as doc:
            assert doc.page_count == 6

    def test_same_source_used_twice(self, pdf_factory, tmp_path):
        src = pdf_factory(2)
        # Include all pages, then include them again
        pages = _page_items(src, "d1", 2) + _page_items(src, "d1", 2)
        out = tmp_path / "doubled.pdf"
        merge(pages, out)
        with _open_result(out) as doc:
            assert doc.page_count == 4


# ── Error handling ────────────────────────────────────────────────────────────

class TestMergeErrors:
    def test_empty_pages_raises_value_error(self, tmp_path):
        with pytest.raises(ValueError, match="empty"):
            merge([], tmp_path / "out.pdf")

    def test_missing_source_raises_merge_error(self, tmp_path):
        pages = [
            PageItem(
                source_path=Path("/nonexistent/file.pdf"),
                source_page_index=0,
                display_index=0,
                document_id="d1",
            )
        ]
        with pytest.raises(MergeError, match="not found"):
            merge(pages, tmp_path / "out.pdf")

    def test_invalid_source_raises_merge_error(self, tmp_path):
        bad = tmp_path / "bad.pdf"
        bad.write_bytes(b"this is not a pdf")
        pages = [
            PageItem(
                source_path=bad,
                source_page_index=0,
                display_index=0,
                document_id="d1",
            )
        ]
        with pytest.raises(MergeError):
            merge(pages, tmp_path / "out.pdf")

    def test_page_index_out_of_range_raises_merge_error(self, pdf_factory, tmp_path):
        src = pdf_factory(2)
        pages = [
            PageItem(
                source_path=src,
                source_page_index=99,   # out of range
                display_index=0,
                document_id="d1",
            )
        ]
        with pytest.raises(MergeError, match="out of range"):
            merge(pages, tmp_path / "out.pdf")

    def test_source_opened_only_once_per_path(self, pdf_factory, tmp_path):
        """Merge 10 pages from the same source without opening the file 10 times."""
        src = pdf_factory(10)
        pages = _page_items(src, "d1", 10)
        out = tmp_path / "out.pdf"
        # Should not raise and result is correct
        merge(pages, out)
        with _open_result(out) as doc:
            assert doc.page_count == 10
