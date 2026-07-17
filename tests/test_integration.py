"""
tests/test_integration.py — End-to-end pipeline integration tests.

These tests exercise the full stack from import → page operations →
merge → bookmark embedding, without any GUI.

Run with:
    QT_QPA_PLATFORM=offscreen pytest tests/test_integration.py -v
"""

from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from engines.merge_engine import merge
from engines.toc_detection_engine import detect_from_bookmarks
from engines.toc_engine import embed_bookmarks
from engines.watermark_engine import detect_watermarks, remove_watermarks
from models.toc_entry import TOCEntry
from services.project_service import ProjectService
from services.toc_service import TOCService


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_pdf(path: Path, pages: int = 4, add_bookmarks: bool = False) -> Path:
    doc = fitz.open()
    for i in range(pages):
        pg = doc.new_page(width=595, height=842)
        pg.insert_text((72, 80), f"Chapter {i + 1}", fontsize=18,
                       fontname="Helvetica-Bold")
        for line in range(10):
            pg.insert_text((72, 120 + line * 14),
                           f"Body text line {line + 1} on page {i + 1}.",
                           fontsize=10)
    if add_bookmarks:
        doc.set_toc([[1, f"Chapter {i + 1}", i + 1] for i in range(pages)])
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture()
def pdf_a(tmp_path):
    return _make_pdf(tmp_path / "doc_a.pdf", pages=3)


@pytest.fixture()
def pdf_b(tmp_path):
    return _make_pdf(tmp_path / "doc_b.pdf", pages=2)


@pytest.fixture()
def bookmarked_pdf(tmp_path):
    return _make_pdf(tmp_path / "bm.pdf", pages=4, add_bookmarks=True)


@pytest.fixture()
def svc():
    return ProjectService()


# ── Import → merge ────────────────────────────────────────────────────────────

class TestImportMergePipeline:
    def test_single_doc_correct_page_count(self, svc, pdf_a, tmp_path):
        svc.import_document(pdf_a)
        out = tmp_path / "out.pdf"
        merge(svc.pages, out)
        with fitz.open(str(out)) as doc:
            assert doc.page_count == 3

    def test_two_docs_correct_page_count(self, svc, pdf_a, pdf_b, tmp_path):
        svc.import_document(pdf_a)
        svc.import_document(pdf_b)
        out = tmp_path / "out.pdf"
        merge(svc.pages, out)
        with fitz.open(str(out)) as doc:
            assert doc.page_count == 5

    def test_document_reorder_reflected_in_merge(self, svc, pdf_a, pdf_b, tmp_path):
        doc_a = svc.import_document(pdf_a)   # 3 pages
        doc_b = svc.import_document(pdf_b)   # 2 pages
        svc.reorder_documents([doc_b.doc_id, doc_a.doc_id])
        assert svc.pages[0].document_id == doc_b.doc_id
        out = tmp_path / "out.pdf"
        merge(svc.pages, out)
        with fitz.open(str(out)) as doc:
            assert doc.page_count == 5

    def test_merged_pdf_is_not_encrypted(self, svc, pdf_a, tmp_path):
        svc.import_document(pdf_a)
        out = tmp_path / "out.pdf"
        merge(svc.pages, out)
        with fitz.open(str(out)) as doc:
            assert not doc.is_encrypted


# ── Page operations → merge ───────────────────────────────────────────────────

class TestPageOpsThenMerge:
    def test_delete_then_merge(self, svc, pdf_a, tmp_path):
        svc.import_document(pdf_a)   # 3 pages
        svc.delete_pages([1])
        assert svc.total_pages == 2
        out = tmp_path / "out.pdf"
        merge(svc.pages, out)
        with fitz.open(str(out)) as doc:
            assert doc.page_count == 2

    def test_rotate_then_merge(self, svc, pdf_a, tmp_path):
        svc.import_document(pdf_a)
        svc.rotate_pages([0], "cw")
        out = tmp_path / "out.pdf"
        merge(svc.pages, out)
        with fitz.open(str(out)) as doc:
            assert doc[0].rotation == 90

    def test_move_then_merge(self, svc, pdf_a, pdf_b, tmp_path):
        svc.import_document(pdf_a)   # pages 0,1,2
        svc.import_document(pdf_b)   # pages 3,4
        svc.move_pages([0], 4)
        out = tmp_path / "out.pdf"
        merge(svc.pages, out)
        with fitz.open(str(out)) as doc:
            assert doc.page_count == 5

    def test_copy_then_merge(self, svc, pdf_a, tmp_path):
        svc.import_document(pdf_a)   # 3 pages
        svc.copy_pages([0], 0)       # duplicate first
        assert svc.total_pages == 4
        out = tmp_path / "out.pdf"
        merge(svc.pages, out)
        with fitz.open(str(out)) as doc:
            assert doc.page_count == 4

    def test_insert_blank_then_merge(self, svc, pdf_a, tmp_path):
        svc.import_document(pdf_a)   # 3 pages
        svc.insert_blank_page(1)
        assert svc.total_pages == 4
        out = tmp_path / "out.pdf"
        merge(svc.pages, out)
        with fitz.open(str(out)) as doc:
            assert doc.page_count == 4

    def test_undo_then_merge_restores_original_count(self, svc, pdf_a, tmp_path):
        svc.import_document(pdf_a)   # 3 pages
        svc.delete_pages([0])        # 2 pages
        svc.undo()                   # back to 3
        assert svc.total_pages == 3
        out = tmp_path / "out.pdf"
        merge(svc.pages, out)
        with fitz.open(str(out)) as doc:
            assert doc.page_count == 3

    def test_chained_ops_then_merge(self, svc, pdf_a, pdf_b, tmp_path):
        svc.import_document(pdf_a)   # 0,1,2
        svc.import_document(pdf_b)   # 3,4
        svc.delete_pages([4])        # → 4 pages
        svc.rotate_pages([0], "cw")
        svc.move_pages([0], 3)
        out = tmp_path / "out.pdf"
        merge(svc.pages, out)
        with fitz.open(str(out)) as doc:
            assert doc.page_count == 4


# ── TOC pipeline ──────────────────────────────────────────────────────────────

class TestTOCPipeline:
    def test_detect_and_embed_bookmarks(self, svc, bookmarked_pdf, tmp_path):
        svc.import_document(bookmarked_pdf)
        entries = detect_from_bookmarks(bookmarked_pdf)
        assert len(entries) == 4

        out = tmp_path / "out.pdf"
        merge(svc.pages, out)
        with fitz.open(str(out)) as doc:
            embed_bookmarks(doc, entries)
            doc.save(str(out), incremental=True,
                     encryption=fitz.PDF_ENCRYPT_KEEP)

        with fitz.open(str(out)) as doc:
            assert len(doc.get_toc()) == 4

    def test_toc_service_entries_embedded(self, svc, pdf_a, tmp_path, qapp):
        svc.import_document(pdf_a)
        toc_svc = TOCService()
        # Create a synthetic section so add_entry has somewhere to land
        toc_svc.load_from_fitz([])
        toc_svc.add_entry("Introduction", 1, level=1)
        toc_svc.add_entry("Background", 2, level=2)
        toc_svc.add_entry("Conclusion", 3, level=1)

        out = tmp_path / "out.pdf"
        merge(svc.pages, out)
        with fitz.open(str(out)) as doc:
            embed_bookmarks(doc, toc_svc.entries)
            doc.save(str(out), incremental=True,
                     encryption=fitz.PDF_ENCRYPT_KEEP)

        with fitz.open(str(out)) as doc:
            toc = doc.get_toc()
            assert len(toc) == 3
            assert toc[0][1] == "Introduction"
            assert toc[1][0] == 2   # level 2

    def test_disabled_entries_not_embedded(self, svc, pdf_a, tmp_path, qapp):
        svc.import_document(pdf_a)
        toc_svc = TOCService()
        toc_svc.load_from_fitz([])
        toc_svc.add_entry("Visible", 1)
        e = toc_svc.add_entry("Hidden", 2)
        toc_svc.set_enabled(e.entry_id, False)

        out = tmp_path / "out.pdf"
        merge(svc.pages, out)
        with fitz.open(str(out)) as doc:
            embed_bookmarks(doc, toc_svc.entries)
            toc = doc.get_toc()

        assert len(toc) == 1
        assert toc[0][1] == "Visible"


# ── Watermark pipeline ────────────────────────────────────────────────────────

class TestWatermarkPipeline:
    def _make_watermarked(self, path: Path) -> Path:
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        page.insert_text(
            (150, 400), "CONFIDENTIAL",
            fontsize=36, color=(0.85, 0.85, 0.85),
            fontname="Helvetica-Bold",
        )
        for i in range(15):
            page.insert_text((72, 50 + i * 14), "Body content.", fontsize=10)
        doc.save(str(path))
        doc.close()
        return path

    def test_detect_remove_produces_valid_pdf(self, tmp_path):
        src = self._make_watermarked(tmp_path / "wm.pdf")
        results = detect_watermarks(src)
        out = tmp_path / "clean.pdf"
        remove_watermarks(src, results, out)
        with fitz.open(str(out)) as doc:
            assert doc.page_count == 1

    def test_source_unchanged_after_removal(self, tmp_path):
        src = self._make_watermarked(tmp_path / "wm.pdf")
        original_size = src.stat().st_size
        results = detect_watermarks(src)
        remove_watermarks(src, results, tmp_path / "clean.pdf")
        assert src.stat().st_size == original_size

    def test_page_count_preserved(self, tmp_path):
        src = self._make_watermarked(tmp_path / "wm.pdf")
        results = detect_watermarks(src)
        out = tmp_path / "clean.pdf"
        remove_watermarks(src, results, out)
        with fitz.open(str(src)) as s, fitz.open(str(out)) as c:
            assert s.page_count == c.page_count


# ── Full combined pipeline ────────────────────────────────────────────────────

class TestFullPipeline:
    def test_import_ops_toc_merge_bookmarks(self, tmp_path, qapp):
        """import → rotate → delete → blank → merge → embed bookmarks → verify."""
        pdf_1 = _make_pdf(tmp_path / "p1.pdf", pages=3)
        pdf_2 = _make_pdf(tmp_path / "p2.pdf", pages=2)

        svc = ProjectService()
        svc.import_document(pdf_1)
        svc.import_document(pdf_2)

        svc.rotate_pages([0], "cw")
        svc.delete_pages([4])        # 4 total
        svc.insert_blank_page(2)     # 5 total
        assert svc.total_pages == 5

        toc_svc = TOCService()
        toc_svc.load_from_fitz([])
        toc_svc.add_entry("Part 1", 1, level=1)
        toc_svc.add_entry("Part 2", 4, level=1)

        out = tmp_path / "final.pdf"
        merge(svc.pages, out)

        with fitz.open(str(out)) as doc:
            assert doc.page_count == 5
            assert doc[0].rotation == 90
            embed_bookmarks(doc, toc_svc.entries)
            doc.save(str(out), incremental=True,
                     encryption=fitz.PDF_ENCRYPT_KEEP)

        with fitz.open(str(out)) as doc:
            assert doc.page_count == 5
            assert len(doc.get_toc()) == 2

    def test_undo_redo_does_not_corrupt_merge(self, tmp_path, qapp):
        """Multiple undo/redo cycles before merge should yield consistent output."""
        svc = ProjectService()
        svc.import_document(_make_pdf(tmp_path / "doc.pdf", pages=4))

        svc.delete_pages([0, 1])  # single command → 2 pages
        svc.undo()                # restore → 4 pages
        svc.redo()                # re-apply delete → 2 pages
        assert svc.total_pages == 2

        out = tmp_path / "out.pdf"
        merge(svc.pages, out)
        with fitz.open(str(out)) as doc:
            assert doc.page_count == 2

    def test_remove_document_then_merge(self, tmp_path):
        svc = ProjectService()
        doc_a = svc.import_document(_make_pdf(tmp_path / "a.pdf", pages=3))
        svc.import_document(_make_pdf(tmp_path / "b.pdf", pages=2))
        svc.remove_document(doc_a.doc_id)
        assert svc.total_pages == 2

        out = tmp_path / "out.pdf"
        merge(svc.pages, out)
        with fitz.open(str(out)) as doc:
            assert doc.page_count == 2
