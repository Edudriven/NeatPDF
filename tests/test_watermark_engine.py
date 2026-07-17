"""
tests/test_watermark_engine.py — Unit tests for engines/watermark_engine.py.

Run with:
    QT_QPA_PLATFORM=offscreen pytest tests/test_watermark_engine.py -v
"""

from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from engines.watermark_engine import (
    RemovalError,
    detect_watermarks,
    remove_watermarks,
)
from models.watermark_result import WatermarkResult, WatermarkType


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_plain_pdf(path: Path, pages: int = 3) -> Path:
    doc = fitz.open()
    for i in range(pages):
        pg = doc.new_page(width=595, height=842)
        pg.insert_text((72, 100), f"Normal body text on page {i + 1}.", fontsize=10)
    doc.save(str(path))
    doc.close()
    return path


def _make_text_watermark_pdf(path: Path) -> Path:
    """PDF with grey uppercase short text that looks like a watermark."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    # Normal body
    for i in range(20):
        page.insert_text((72, 50 + i * 14), "Regular body text line.", fontsize=10)
    # Grey all-caps watermark-style text
    page.insert_text(
        (150, 400),
        "CONFIDENTIAL",
        fontsize=36,
        color=(0.85, 0.85, 0.85),  # light grey
        fontname="Helvetica-Bold",
    )
    doc.save(str(path))
    doc.close()
    return path


def _make_diagonal_text_watermark_pdf(path: Path) -> Path:
    """PDF with a rotated (diagonal) text watermark using morph transform."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    # Insert regular body text
    for i in range(10):
        page.insert_text((72, 50 + i * 14), "Body text content.", fontsize=10)
    # 45° rotation around the page centre using the morph parameter
    pivot = fitz.Point(297.5, 421.0)
    mat = fitz.Matrix(45)  # 45-degree rotation
    page.insert_text(
        (150, 421),
        "DRAFT",
        fontsize=60,
        color=(0.75, 0.75, 0.75),
        morph=(pivot, mat),
    )
    doc.save(str(path))
    doc.close()
    return path


def _make_artifact_watermark_pdf(path: Path) -> Path:
    """PDF with an /Artifact <</Subtype /Watermark>> marked-content sequence."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "Normal body content.", fontsize=12)

    # Build content stream that wraps an image Do call in an Artifact/Watermark BDC/EMC block.
    # We insert a placeholder Form XObject (/Fm0) via insert_text first so the font is registered,
    # then manually inject the marked-content wrapper.
    pivot = fitz.Point(297.5, 421.0)
    mat = fitz.Matrix(45)
    page.insert_text(
        (100, 400),
        "WATERMARK",
        fontsize=48,
        color=(0.8, 0.8, 0.8),
        morph=(pivot, mat),
    )

    # Wrap the page's existing content in an Artifact Watermark BDC/EMC block
    # by prepending/appending to the content stream.
    content_xref = page.get_contents()[0]
    existing = doc.xref_stream(content_xref).decode("latin-1", errors="replace")

    # Find the morph-rotated text insertion (last q...Q block) and wrap it
    # Split to isolate: first part = normal text, second = watermark text
    # For simplicity, wrap the entire second half with the marker.
    # This mimics real PDFs that wrap only the watermark layer.
    lines = existing.rsplit("q\n", 1)
    if len(lines) == 2:
        new_stream = (
            lines[0]
            + "/Artifact <</Subtype /Watermark /Type /Pagination >>BDC \n"
            + "q\n"
            + lines[1]
            + "EMC \n"
        )
        doc.update_stream(content_xref, new_stream.encode("latin-1"))

    doc.save(str(path))
    doc.close()
    return path


def _make_image_watermark_pdf(path: Path) -> Path:
    """PDF with a large semi-transparent image covering most of the page."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)

    # Use a pixmap as an image watermark — covers ~70% of page area
    pix = fitz.Pixmap(fitz.csGRAY, fitz.IRect(0, 0, 400, 500))
    pix.clear_with(220)  # light grey

    rect = fitz.Rect(50, 100, 545, 750)  # covers ~70% of page
    page.insert_image(rect, pixmap=pix)

    for i in range(10):
        page.insert_text((72, 50 + i * 14), "Body text content.", fontsize=10)

    doc.save(str(path))
    doc.close()
    return path


def _make_vector_watermark_pdf(path: Path) -> Path:
    """PDF with a large filled rectangle covering most of the page (vector watermark)."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    # Large grey rectangle spanning >30% of page
    page.draw_rect(
        fitz.Rect(50, 150, 545, 700),
        color=(0.8, 0.8, 0.8),
        fill=(0.8, 0.8, 0.8),
        width=0.5,
    )
    for i in range(10):
        page.insert_text((72, 50 + i * 14), "Body text content.", fontsize=10)
    doc.save(str(path))
    doc.close()
    return path


# ── detect_watermarks ─────────────────────────────────────────────────────────

class TestDetectWatermarks:
    def test_plain_pdf_returns_empty_or_few(self, tmp_path):
        p = _make_plain_pdf(tmp_path / "plain.pdf")
        results = detect_watermarks(p)
        # Plain body text should not trigger high-confidence watermark detection
        text_results = [r for r in results if r.watermark_type == WatermarkType.TEXT]
        # Allow some false positives but not many
        assert len(text_results) <= 2

    def test_text_watermark_detected(self, tmp_path):
        p = _make_text_watermark_pdf(tmp_path / "wm_text.pdf")
        results = detect_watermarks(p)
        assert any(r.watermark_type == WatermarkType.TEXT for r in results)

    def test_image_watermark_detected(self, tmp_path):
        p = _make_image_watermark_pdf(tmp_path / "wm_img.pdf")
        results = detect_watermarks(p)
        assert any(r.watermark_type == WatermarkType.IMAGE for r in results)

    def test_vector_watermark_detected(self, tmp_path):
        p = _make_vector_watermark_pdf(tmp_path / "wm_vec.pdf")
        results = detect_watermarks(p)
        assert any(r.watermark_type == WatermarkType.VECTOR for r in results)

    def test_diagonal_text_watermark_detected(self, tmp_path):
        p = _make_diagonal_text_watermark_pdf(tmp_path / "wm_diag.pdf")
        results = detect_watermarks(p)
        text_results = [r for r in results if r.watermark_type == WatermarkType.TEXT]
        assert text_results, "Diagonal text watermark should be detected"
        # Diagonal watermarks get higher confidence
        assert any(r.confidence >= 0.88 for r in text_results)

    def test_diagonal_watermark_description_notes_rotation(self, tmp_path):
        p = _make_diagonal_text_watermark_pdf(tmp_path / "wm_diag2.pdf")
        results = detect_watermarks(p)
        text_results = [r for r in results if r.watermark_type == WatermarkType.TEXT]
        assert any("diagonal" in r.description.lower() or "rotated" in r.description.lower()
                   for r in text_results)

    def test_artifact_watermark_detected(self, tmp_path):
        p = _make_artifact_watermark_pdf(tmp_path / "wm_artifact.pdf")
        results = detect_watermarks(p)
        assert any(r.watermark_type == WatermarkType.ARTIFACT for r in results)

    def test_artifact_watermark_high_confidence(self, tmp_path):
        p = _make_artifact_watermark_pdf(tmp_path / "wm_artifact2.pdf")
        results = detect_watermarks(p)
        artifact = [r for r in results if r.watermark_type == WatermarkType.ARTIFACT]
        assert artifact and artifact[0].confidence >= 0.95

    def test_result_has_source_path(self, tmp_path):
        p = _make_text_watermark_pdf(tmp_path / "wm.pdf")
        results = detect_watermarks(p)
        for r in results:
            assert r.source_path == p

    def test_result_has_valid_confidence(self, tmp_path):
        p = _make_text_watermark_pdf(tmp_path / "wm.pdf")
        results = detect_watermarks(p)
        for r in results:
            assert 0.0 <= r.confidence <= 1.0

    def test_result_has_description(self, tmp_path):
        p = _make_text_watermark_pdf(tmp_path / "wm.pdf")
        results = detect_watermarks(p)
        for r in results:
            assert r.description

    def test_missing_file_returns_empty(self, tmp_path):
        results = detect_watermarks(tmp_path / "ghost.pdf")
        assert results == []

    def test_invalid_file_returns_empty(self, tmp_path):
        bad = tmp_path / "bad.pdf"
        bad.write_bytes(b"not a pdf")
        results = detect_watermarks(bad)
        assert results == []

    def test_max_pages_limits_scan(self, tmp_path):
        p = _make_plain_pdf(tmp_path / "p.pdf", pages=10)
        # Should not crash and returns results only from scanned pages
        results = detect_watermarks(p, max_pages=2)
        for r in results:
            assert r.page_index < 2

    def test_results_are_watermark_result_objects(self, tmp_path):
        p = _make_text_watermark_pdf(tmp_path / "wm.pdf")
        results = detect_watermarks(p)
        assert all(isinstance(r, WatermarkResult) for r in results)


# ── remove_watermarks ─────────────────────────────────────────────────────────

class TestRemoveWatermarks:
    def test_output_file_created(self, tmp_path):
        p = _make_text_watermark_pdf(tmp_path / "wm.pdf")
        results = detect_watermarks(p)
        out = tmp_path / "clean.pdf"
        remove_watermarks(p, results, out)
        assert out.exists()
        assert out.stat().st_size > 0

    def test_output_is_valid_pdf(self, tmp_path):
        p = _make_text_watermark_pdf(tmp_path / "wm.pdf")
        results = detect_watermarks(p)
        out = tmp_path / "clean.pdf"
        remove_watermarks(p, results, out)
        with fitz.open(str(out)) as doc:
            assert doc.page_count >= 1

    def test_source_not_modified(self, tmp_path):
        p = _make_text_watermark_pdf(tmp_path / "wm.pdf")
        original_size = p.stat().st_size
        results = detect_watermarks(p)
        out = tmp_path / "clean.pdf"
        remove_watermarks(p, results, out)
        assert p.stat().st_size == original_size

    def test_remove_with_empty_results_still_saves(self, tmp_path):
        p = _make_plain_pdf(tmp_path / "plain.pdf")
        out = tmp_path / "clean.pdf"
        n = remove_watermarks(p, [], out)
        assert out.exists()
        assert n == 0

    def test_returns_pages_cleaned_count(self, tmp_path):
        p = _make_text_watermark_pdf(tmp_path / "wm.pdf")
        results = detect_watermarks(p)
        removable = [r for r in results if r.removable]
        out = tmp_path / "clean.pdf"
        n = remove_watermarks(p, removable, out)
        assert isinstance(n, int)
        assert n >= 0

    def test_parent_dir_created_if_missing(self, tmp_path):
        p = _make_text_watermark_pdf(tmp_path / "wm.pdf")
        results = detect_watermarks(p)
        out = tmp_path / "sub" / "deep" / "clean.pdf"
        remove_watermarks(p, results, out)
        assert out.exists()

    def test_missing_source_raises_removal_error(self, tmp_path):
        with pytest.raises(RemovalError, match="Cannot open"):
            remove_watermarks(
                Path("/nonexistent/file.pdf"),
                [],
                tmp_path / "out.pdf",
            )

    def test_invalid_source_raises_removal_error(self, tmp_path):
        bad = tmp_path / "bad.pdf"
        bad.write_bytes(b"not a pdf")
        with pytest.raises(RemovalError):
            remove_watermarks(bad, [], tmp_path / "out.pdf")

    def test_image_watermark_removal(self, tmp_path):
        p = _make_image_watermark_pdf(tmp_path / "wm_img.pdf")
        results = detect_watermarks(p)
        out = tmp_path / "clean.pdf"
        remove_watermarks(p, results, out)
        assert out.exists()

    def test_artifact_watermark_removal(self, tmp_path):
        p = _make_artifact_watermark_pdf(tmp_path / "wm_artifact.pdf")
        results = detect_watermarks(p)
        artifact_results = [r for r in results if r.watermark_type == WatermarkType.ARTIFACT]
        assert artifact_results, "Expected artifact watermark to be detected"
        out = tmp_path / "clean.pdf"
        n = remove_watermarks(p, artifact_results, out)
        assert out.exists()
        assert n >= 1

    def test_sample_pdf_watermark_detected(self):
        """Integration test against the real NCERT sample PDF."""
        sample = Path("file/sample.pdf")
        if not sample.exists():
            import pytest
            pytest.skip("file/sample.pdf not present")
        results = detect_watermarks(sample)
        assert results, "Expected at least one watermark in sample.pdf"
        types = {r.watermark_type for r in results}
        assert WatermarkType.ARTIFACT in types or WatermarkType.IMAGE in types, (
            f"Expected ARTIFACT or IMAGE watermark, got types: {types}"
        )

    def test_sample_pdf_watermark_removal(self, tmp_path):
        """Integration test: remove watermark from the real NCERT sample PDF."""
        sample = Path("file/sample.pdf")
        if not sample.exists():
            import pytest
            pytest.skip("file/sample.pdf not present")
        results = detect_watermarks(sample)
        removable = [r for r in results if r.removable]
        out = tmp_path / "sample_clean.pdf"
        n = remove_watermarks(sample, removable, out)
        assert out.exists()
        assert n >= 1

    def test_vector_watermark_removal(self, tmp_path):
        p = _make_vector_watermark_pdf(tmp_path / "wm_vec.pdf")
        results = detect_watermarks(p)
        out = tmp_path / "clean.pdf"
        remove_watermarks(p, results, out)
        assert out.exists()

    def test_page_count_preserved_after_removal(self, tmp_path):
        p = _make_text_watermark_pdf(tmp_path / "wm.pdf")
        results = detect_watermarks(p)
        out = tmp_path / "clean.pdf"
        remove_watermarks(p, results, out)
        with fitz.open(str(p)) as src, fitz.open(str(out)) as cleaned:
            assert src.page_count == cleaned.page_count


# ── WatermarkResult model ─────────────────────────────────────────────────────

class TestWatermarkResultModel:
    def test_confidence_pct_formatting(self):
        r = WatermarkResult(
            source_path=Path("/f.pdf"),
            page_index=0,
            watermark_type=WatermarkType.TEXT,
            confidence=0.873,
            description="test",
        )
        assert r.confidence_pct == "87%"

    def test_not_removable_by_default(self):
        r = WatermarkResult(
            source_path=Path("/f.pdf"),
            page_index=0,
            watermark_type=WatermarkType.IMAGE,
            confidence=0.5,
            description="test",
        )
        assert r.removable is False

    def test_removable_flag_settable(self):
        r = WatermarkResult(
            source_path=Path("/f.pdf"),
            page_index=0,
            watermark_type=WatermarkType.VECTOR,
            confidence=0.9,
            description="test",
            removable=True,
        )
        assert r.removable is True
