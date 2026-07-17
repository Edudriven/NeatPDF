"""
tests/test_preview_service.py — Unit tests for PreviewService.

Run with:
    QT_QPA_PLATFORM=offscreen pytest tests/test_preview_service.py -v
"""

from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication


def _ensure_app():
    """Ensure a QApplication exists for pixmap operations."""
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="session", autouse=True)
def qt_app():
    return _ensure_app()


def _make_pdf(path: Path, page_count: int = 2) -> Path:
    doc = fitz.open()
    for _ in range(page_count):
        doc.new_page(width=595, height=842)
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture()
def sample_pdf(tmp_path):
    return _make_pdf(tmp_path / "sample.pdf", page_count=3)


class TestThumbnailCache:
    """Tests for the internal _ThumbnailCache."""

    def test_cache_put_and_get(self):
        from services.preview_service import _ThumbnailCache
        cache = _ThumbnailCache(10)
        px = QPixmap(10, 10)
        cache.put(("doc1", 0, 0), px)
        result = cache.get(("doc1", 0, 0))
        assert result is px

    def test_cache_miss_returns_none(self):
        from services.preview_service import _ThumbnailCache
        cache = _ThumbnailCache(10)
        assert cache.get(("doc1", 99, 0)) is None

    def test_cache_evicts_lru(self):
        from services.preview_service import _ThumbnailCache
        cache = _ThumbnailCache(2)
        px = QPixmap(1, 1)
        cache.put(("a", 0, 0), px)
        cache.put(("b", 0, 0), px)
        cache.put(("c", 0, 0), px)   # evicts ("a", 0, 0)
        assert cache.get(("a", 0, 0)) is None
        assert cache.get(("b", 0, 0)) is not None

    def test_invalidate_removes_doc_entries(self):
        from services.preview_service import _ThumbnailCache
        cache = _ThumbnailCache(20)
        px = QPixmap(1, 1)
        cache.put(("doc1", 0, 0), px)
        cache.put(("doc1", 1, 0), px)
        cache.put(("doc2", 0, 0), px)
        cache.invalidate("doc1")
        assert cache.get(("doc1", 0, 0)) is None
        assert cache.get(("doc1", 1, 0)) is None
        assert cache.get(("doc2", 0, 0)) is not None

    def test_clear_empties_cache(self):
        from services.preview_service import _ThumbnailCache
        cache = _ThumbnailCache(10)
        px = QPixmap(1, 1)
        cache.put(("doc1", 0, 0), px)
        cache.clear()
        assert cache.get(("doc1", 0, 0)) is None


class TestRenderWorker:
    """Tests that _RenderWorker produces valid pixmaps."""

    def test_render_produces_pixmap(self, sample_pdf):
        from services.preview_service import _RenderWorker
        results = []

        worker = _RenderWorker(
            doc_id="test-doc",
            path=sample_pdf,
            page_index=0,
            rotation=0,
            dpi=72.0,
            password="",
            width=140,
            height=180,
        )
        worker.signals.finished.connect(
            lambda doc_id, idx, px: results.append(px)
        )
        worker.run()

        assert len(results) == 1
        assert not results[0].isNull()

    def test_render_respects_size_bounds(self, sample_pdf):
        from services.preview_service import _RenderWorker
        results = []

        worker = _RenderWorker(
            doc_id="test-doc",
            path=sample_pdf,
            page_index=0,
            rotation=0,
            dpi=72.0,
            password="",
            width=100,
            height=130,
        )
        worker.signals.finished.connect(
            lambda doc_id, idx, px: results.append(px)
        )
        worker.run()

        px = results[0]
        assert px.width() <= 100
        assert px.height() <= 130

    def test_render_invalid_page_emits_error(self, sample_pdf):
        from services.preview_service import _RenderWorker
        errors = []

        worker = _RenderWorker(
            doc_id="test-doc",
            path=sample_pdf,
            page_index=999,   # out of range
            rotation=0,
            dpi=72.0,
            password="",
            width=140,
            height=180,
        )
        worker.signals.error.connect(
            lambda doc_id, idx, err: errors.append(err)
        )
        worker.run()

        assert len(errors) == 1

    def test_render_all_pages(self, sample_pdf):
        from services.preview_service import _RenderWorker
        for i in range(3):
            results = []
            worker = _RenderWorker(
                doc_id="test-doc",
                path=sample_pdf,
                page_index=i,
                rotation=0,
                dpi=72.0,
                password="",
                width=140,
                height=180,
            )
            worker.signals.finished.connect(
                lambda doc_id, idx, px: results.append(px)
            )
            worker.run()
            assert not results[0].isNull()
