"""
tests/test_models.py — Unit tests for the data model layer.

Run with:
    QT_QPA_PLATFORM=offscreen pytest tests/test_models.py -v
"""

from pathlib import Path


from models.pdf_document import PDFDocument
from models.page_item import PageItem
from models.toc_entry import TOCEntry
from models.watermark_result import WatermarkResult, WatermarkType


# ── PDFDocument ───────────────────────────────────────────────────────────────

class TestPDFDocument:
    def test_default_title_uses_stem(self, tmp_path):
        pdf = tmp_path / "my_report.pdf"
        pdf.write_bytes(b"")
        doc = PDFDocument(path=pdf, page_count=5, file_size_bytes=1024)
        assert doc.title == "my_report"

    def test_explicit_title(self, tmp_path):
        pdf = tmp_path / "file.pdf"
        pdf.write_bytes(b"")
        doc = PDFDocument(path=pdf, page_count=1, file_size_bytes=2048, title="Custom Title")
        assert doc.title == "Custom Title"

    def test_file_size_mb(self, tmp_path):
        pdf = tmp_path / "big.pdf"
        pdf.write_bytes(b"")
        doc = PDFDocument(path=pdf, page_count=10, file_size_bytes=5_242_880)
        assert doc.file_size_mb == 5.0

    def test_unique_doc_ids(self, tmp_path):
        pdf = tmp_path / "a.pdf"
        pdf.write_bytes(b"")
        doc1 = PDFDocument(path=pdf, page_count=1, file_size_bytes=100)
        doc2 = PDFDocument(path=pdf, page_count=1, file_size_bytes=100)
        assert doc1.doc_id != doc2.doc_id

    def test_repr_contains_title(self, tmp_path):
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"")
        doc = PDFDocument(path=pdf, page_count=3, file_size_bytes=512)
        assert "test" in repr(doc)


# ── PageItem ──────────────────────────────────────────────────────────────────

class TestPageItem:
    _path = Path("/fake/doc.pdf")

    def _make_page(self, rotation: int = 0) -> PageItem:
        return PageItem(
            source_path=self._path,
            source_page_index=0,
            display_index=0,
            document_id="doc-001",
            rotation=rotation,
        )

    def test_initial_rotation(self):
        page = self._make_page(rotation=0)
        assert page.rotation == 0

    def test_rotate_cw(self):
        page = self._make_page(rotation=0)
        page.rotate_cw()
        assert page.rotation == 90

    def test_rotate_cw_wraps(self):
        page = self._make_page(rotation=270)
        page.rotate_cw()
        assert page.rotation == 0

    def test_rotate_ccw(self):
        page = self._make_page(rotation=90)
        page.rotate_ccw()
        assert page.rotation == 0

    def test_rotate_ccw_wraps(self):
        page = self._make_page(rotation=0)
        page.rotate_ccw()
        assert page.rotation == 270

    def test_rotate_full_circle(self):
        page = self._make_page(rotation=0)
        for _ in range(4):
            page.rotate_cw()
        assert page.rotation == 0

    def test_blank_page(self):
        page = PageItem(
            source_path=self._path,
            source_page_index=-1,
            display_index=3,
            document_id="doc-001",
            is_blank=True,
        )
        assert page.is_blank is True


# ── TOCEntry ──────────────────────────────────────────────────────────────────

class TestTOCEntry:
    def test_default_enabled(self):
        entry = TOCEntry(title="Chapter 1", page_number=1)
        assert entry.enabled is True

    def test_unique_ids(self):
        e1 = TOCEntry(title="A", page_number=1)
        e2 = TOCEntry(title="B", page_number=2)
        assert e1.entry_id != e2.entry_id

    def test_repr_shows_title(self):
        entry = TOCEntry(title="Introduction", page_number=1, level=1)
        assert "Introduction" in repr(entry)

    def test_disabled_entry_repr(self):
        entry = TOCEntry(title="Appendix", page_number=50, enabled=False)
        assert "disabled" in repr(entry)

    def test_parent_id_none_by_default(self):
        entry = TOCEntry(title="Top", page_number=1)
        assert entry.parent_id is None

    def test_hierarchy_levels(self):
        root = TOCEntry(title="Part I", page_number=1, level=1)
        child = TOCEntry(title="Chapter 1", page_number=5, level=2, parent_id=root.entry_id)
        assert child.level == 2
        assert child.parent_id == root.entry_id


# ── WatermarkResult ───────────────────────────────────────────────────────────

class TestWatermarkResult:
    _path = Path("/fake/doc.pdf")

    def test_confidence_pct(self):
        result = WatermarkResult(
            source_path=self._path,
            page_index=0,
            watermark_type=WatermarkType.TEXT,
            confidence=0.87,
            description="Repeated 'DRAFT' text",
        )
        assert result.confidence_pct == "87%"

    def test_not_removable_by_default(self):
        result = WatermarkResult(
            source_path=self._path,
            page_index=0,
            watermark_type=WatermarkType.IMAGE,
            confidence=0.5,
            description="Logo watermark",
        )
        assert result.removable is False

    def test_all_watermark_types(self):
        for wtype in WatermarkType:
            result = WatermarkResult(
                source_path=self._path,
                page_index=0,
                watermark_type=wtype,
                confidence=0.9,
                description="test",
            )
            assert result.watermark_type == wtype
