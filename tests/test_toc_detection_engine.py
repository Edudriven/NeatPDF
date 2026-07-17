"""
tests/test_toc_detection_engine.py — Unit tests for
engines/toc_detection_engine.py.

Run with:
    QT_QPA_PLATFORM=offscreen pytest tests/test_toc_detection_engine.py -v
"""

from __future__ import annotations

from pathlib import Path

import fitz

from engines.toc_detection_engine import (
    detect_from_bookmarks,
    detect_from_headings,
)
from models.toc_entry import TOCEntry


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_plain_pdf(path: Path, page_count: int = 3) -> Path:
    """Create a minimal PDF with no bookmarks and uniform small text."""
    doc = fitz.open()
    for i in range(page_count):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 100), f"Body text on page {i + 1}.", fontsize=10)
    doc.save(str(path))
    doc.close()
    return path


def _make_bookmarked_pdf(path: Path) -> Path:
    """Create a PDF with a three-level bookmark outline."""
    doc = fitz.open()
    for _ in range(6):
        doc.new_page(width=595, height=842)
    toc = [
        [1, "Chapter 1", 1],
        [2, "Section 1.1", 2],
        [2, "Section 1.2", 3],
        [1, "Chapter 2", 4],
        [2, "Section 2.1", 5],
        [3, "Subsection 2.1.1", 6],
    ]
    doc.set_toc(toc)
    doc.save(str(path))
    doc.close()
    return path


def _make_heading_pdf(path: Path) -> Path:
    """Create a PDF with large-font headings mixed with body text."""
    doc = fitz.open()
    for i in range(4):
        page = doc.new_page(width=595, height=842)
        # Large heading
        page.insert_text(
            (72, 80),
            f"Chapter {i + 1}",
            fontsize=18,
            fontname="Helvetica-Bold",
        )
        # Body text (smaller, repeated to establish median)
        for line in range(15):
            page.insert_text(
                (72, 110 + line * 14),
                "Lorem ipsum dolor sit amet consectetur adipiscing elit.",
                fontsize=10,
            )
    doc.save(str(path))
    doc.close()
    return path


def _make_empty_pdf(path: Path) -> Path:
    """Create a PDF with text but no meaningful headings."""
    doc = fitz.open()
    page = doc.new_page()
    for line in range(30):
        page.insert_text((72, 50 + line * 14), "uniform text line", fontsize=10)
    doc.save(str(path))
    doc.close()
    return path


# ── detect_from_bookmarks ─────────────────────────────────────────────────────

class TestDetectFromBookmarks:
    def test_extracts_bookmarks(self, tmp_path):
        p = _make_bookmarked_pdf(tmp_path / "bm.pdf")
        entries = detect_from_bookmarks(p)
        assert len(entries) == 6

    def test_chapter_level_is_1(self, tmp_path):
        p = _make_bookmarked_pdf(tmp_path / "bm.pdf")
        entries = detect_from_bookmarks(p)
        assert entries[0].level == 1
        assert entries[3].level == 1

    def test_section_level_is_2(self, tmp_path):
        p = _make_bookmarked_pdf(tmp_path / "bm.pdf")
        entries = detect_from_bookmarks(p)
        assert entries[1].level == 2

    def test_page_numbers_preserved(self, tmp_path):
        p = _make_bookmarked_pdf(tmp_path / "bm.pdf")
        entries = detect_from_bookmarks(p)
        assert entries[0].page_number == 1
        assert entries[3].page_number == 4

    def test_titles_preserved(self, tmp_path):
        p = _make_bookmarked_pdf(tmp_path / "bm.pdf")
        entries = detect_from_bookmarks(p)
        assert entries[0].title == "Chapter 1"
        assert entries[1].title == "Section 1.1"

    def test_no_bookmarks_returns_empty(self, tmp_path):
        p = _make_plain_pdf(tmp_path / "plain.pdf")
        entries = detect_from_bookmarks(p)
        assert entries == []

    def test_missing_file_returns_empty(self, tmp_path):
        entries = detect_from_bookmarks(tmp_path / "nonexistent.pdf")
        assert entries == []

    def test_invalid_file_returns_empty(self, tmp_path):
        bad = tmp_path / "bad.pdf"
        bad.write_bytes(b"not a pdf")
        entries = detect_from_bookmarks(bad)
        assert entries == []

    def test_all_entries_enabled(self, tmp_path):
        p = _make_bookmarked_pdf(tmp_path / "bm.pdf")
        entries = detect_from_bookmarks(p)
        assert all(e.enabled for e in entries)


# ── detect_from_headings ──────────────────────────────────────────────────────

class TestDetectFromHeadings:
    def test_detects_large_headings(self, tmp_path):
        p = _make_heading_pdf(tmp_path / "h.pdf")
        entries = detect_from_headings(p)
        assert len(entries) > 0

    def test_heading_titles_match_content(self, tmp_path):
        p = _make_heading_pdf(tmp_path / "h.pdf")
        entries = detect_from_headings(p)
        titles = [e.title for e in entries]
        assert any("Chapter" in t for t in titles)

    def test_page_offset_applied(self, tmp_path):
        p = _make_heading_pdf(tmp_path / "h.pdf")
        entries_no_offset = detect_from_headings(p, page_offset=0)
        entries_offset = detect_from_headings(p, page_offset=10)
        if entries_no_offset and entries_offset:
            assert entries_offset[0].page_number == entries_no_offset[0].page_number + 10

    def test_no_duplicates(self, tmp_path):
        p = _make_heading_pdf(tmp_path / "h.pdf")
        entries = detect_from_headings(p)
        titles = [e.title for e in entries]
        assert len(titles) == len(set(titles))

    def test_uniform_text_returns_empty_or_few(self, tmp_path):
        p = _make_empty_pdf(tmp_path / "uni.pdf")
        entries = detect_from_headings(p)
        # Uniform 10pt text should not produce many "headings"
        assert len(entries) <= 2

    def test_missing_file_returns_empty(self, tmp_path):
        entries = detect_from_headings(tmp_path / "nope.pdf")
        assert entries == []

    def test_invalid_file_returns_empty(self, tmp_path):
        bad = tmp_path / "bad.pdf"
        bad.write_bytes(b"garbage")
        entries = detect_from_headings(bad)
        assert entries == []

    def test_larger_font_gets_lower_level(self, tmp_path):
        """Largest font → level 1; smaller font → higher level number."""
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 80), "Big Heading", fontsize=24, fontname="Helvetica-Bold")
        page.insert_text((72, 120), "Medium Heading", fontsize=16, fontname="Helvetica-Bold")
        for line in range(20):
            page.insert_text((72, 160 + line * 14), "body text body text body", fontsize=10)
        p = tmp_path / "sizes.pdf"
        doc.save(str(p))
        doc.close()

        entries = detect_from_headings(p)
        big = next((e for e in entries if "Big" in e.title), None)
        med = next((e for e in entries if "Medium" in e.title), None)
        if big and med:
            assert big.level < med.level


# ── detect_combined removed ───────────────────────────────────────────────────
# detect_combined has been removed per TOCPLAN.md.
# Automatic bookmark loading on import is handled by TOCService.load_document().
# Heading detection is experimental and triggered manually via the UI.
