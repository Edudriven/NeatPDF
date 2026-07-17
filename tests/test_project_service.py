"""
tests/test_project_service.py — Unit tests for ProjectService.

Run with:
    QT_QPA_PLATFORM=offscreen pytest tests/test_project_service.py -v
"""

from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from services.project_service import DocumentLoadError, ProjectService


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_pdf(path: Path, page_count: int = 3) -> Path:
    """Create a minimal valid PDF with *page_count* blank pages."""
    doc = fitz.open()
    for _ in range(page_count):
        doc.new_page(width=595, height=842)
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture()
def pdf_factory(tmp_path):
    """Return a callable that creates a PDF at a temp path."""
    counter = {"n": 0}

    def factory(page_count: int = 3, name: str | None = None) -> Path:
        counter["n"] += 1
        fname = name or f"doc_{counter['n']}.pdf"
        return _make_pdf(tmp_path / fname, page_count)

    return factory


@pytest.fixture()
def svc():
    return ProjectService()


# ── Import ────────────────────────────────────────────────────────────────────

class TestImportDocument:
    def test_import_single(self, svc, pdf_factory):
        path = pdf_factory(page_count=5)
        doc = svc.import_document(path)
        assert doc.page_count == 5
        assert doc.path == path
        assert len(svc.documents) == 1

    def test_pages_created(self, svc, pdf_factory):
        path = pdf_factory(page_count=4)
        svc.import_document(path)
        assert len(svc.pages) == 4

    def test_display_indices_sequential(self, svc, pdf_factory):
        svc.import_document(pdf_factory(3))
        svc.import_document(pdf_factory(2))
        indices = [p.display_index for p in svc.pages]
        assert indices == list(range(5))

    def test_pages_reference_correct_source(self, svc, pdf_factory):
        path = pdf_factory(3)
        doc = svc.import_document(path)
        for i, page in enumerate(svc.pages):
            assert page.document_id == doc.doc_id
            assert page.source_page_index == i
            assert page.source_path == path

    def test_file_not_found(self, svc):
        with pytest.raises(FileNotFoundError):
            svc.import_document(Path("/nonexistent/file.pdf"))

    def test_invalid_file(self, svc, tmp_path):
        bad = tmp_path / "bad.pdf"
        bad.write_bytes(b"not a pdf")
        with pytest.raises(DocumentLoadError):
            svc.import_document(bad)

    def test_import_documents_bulk(self, svc, pdf_factory):
        paths = [pdf_factory(2), pdf_factory(3), pdf_factory(1)]
        succeeded, failed = svc.import_documents(paths)
        assert len(succeeded) == 3
        assert len(failed) == 0
        assert svc.total_pages == 6

    def test_import_documents_partial_failure(self, svc, pdf_factory, tmp_path):
        good = pdf_factory(2)
        bad = tmp_path / "bad.pdf"
        bad.write_bytes(b"garbage")
        succeeded, failed = svc.import_documents([good, bad])
        assert len(succeeded) == 1
        assert len(failed) == 1
        assert failed[0][0] == bad

    def test_file_size_set(self, svc, pdf_factory):
        path = pdf_factory(1)
        doc = svc.import_document(path)
        assert doc.file_size_bytes == path.stat().st_size
        assert doc.file_size_bytes > 0

    def test_title_defaults_to_stem(self, svc, pdf_factory):
        path = pdf_factory(1, name="annual_report.pdf")
        doc = svc.import_document(path)
        assert doc.title == "annual_report"


# ── Remove ────────────────────────────────────────────────────────────────────

class TestRemoveDocument:
    def test_remove_only_doc(self, svc, pdf_factory):
        doc = svc.import_document(pdf_factory(3))
        svc.remove_document(doc.doc_id)
        assert svc.is_empty
        assert svc.total_pages == 0

    def test_remove_first_of_two(self, svc, pdf_factory):
        doc1 = svc.import_document(pdf_factory(2))
        svc.import_document(pdf_factory(3))
        svc.remove_document(doc1.doc_id)
        assert len(svc.documents) == 1
        assert svc.total_pages == 3

    def test_display_indices_reindexed_after_remove(self, svc, pdf_factory):
        svc.import_document(pdf_factory(2))
        doc2 = svc.import_document(pdf_factory(2))
        svc.remove_document(doc2.doc_id)
        # Remaining 2 pages should have indices 0, 1
        indices = [p.display_index for p in svc.pages]
        assert indices == [0, 1]

    def test_remove_unknown_id_no_error(self, svc):
        svc.remove_document("nonexistent-id")  # should not raise

    def test_pages_of_removed_doc_gone(self, svc, pdf_factory):
        doc = svc.import_document(pdf_factory(3))
        svc.import_document(pdf_factory(2))
        svc.remove_document(doc.doc_id)
        remaining_docs = {p.document_id for p in svc.pages}
        assert doc.doc_id not in remaining_docs


# ── Reorder ───────────────────────────────────────────────────────────────────

class TestReorderDocuments:
    def test_reorder_reverses_docs(self, svc, pdf_factory):
        doc1 = svc.import_document(pdf_factory(2))
        doc2 = svc.import_document(pdf_factory(3))
        svc.reorder_documents([doc2.doc_id, doc1.doc_id])
        assert svc.documents[0].doc_id == doc2.doc_id
        assert svc.documents[1].doc_id == doc1.doc_id

    def test_reorder_rebuilds_pages(self, svc, pdf_factory):
        doc1 = svc.import_document(pdf_factory(2))
        doc2 = svc.import_document(pdf_factory(3))
        svc.reorder_documents([doc2.doc_id, doc1.doc_id])
        # First pages should belong to doc2 after reorder
        assert svc.pages[0].document_id == doc2.doc_id
        assert svc.pages[3].document_id == doc1.doc_id

    def test_reorder_preserves_page_count(self, svc, pdf_factory):
        doc1 = svc.import_document(pdf_factory(2))
        doc2 = svc.import_document(pdf_factory(3))
        before = svc.total_pages
        svc.reorder_documents([doc2.doc_id, doc1.doc_id])
        assert svc.total_pages == before


# ── Session ───────────────────────────────────────────────────────────────────

class TestSession:
    def test_clear_removes_everything(self, svc, pdf_factory):
        svc.import_document(pdf_factory(5))
        svc.import_document(pdf_factory(2))
        svc.clear()
        assert svc.is_empty
        assert svc.total_pages == 0
        assert len(svc.documents) == 0

    def test_is_empty_initial(self, svc):
        assert svc.is_empty

    def test_is_not_empty_after_import(self, svc, pdf_factory):
        svc.import_document(pdf_factory(1))
        assert not svc.is_empty

    def test_get_document(self, svc, pdf_factory):
        doc = svc.import_document(pdf_factory(1))
        found = svc.get_document(doc.doc_id)
        assert found is doc

    def test_get_document_unknown(self, svc):
        assert svc.get_document("nope") is None
