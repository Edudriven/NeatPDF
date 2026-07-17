"""
tests/test_toc_engine.py — Unit tests for engines/toc_engine.py.

Run with:
    QT_QPA_PLATFORM=offscreen pytest tests/test_toc_engine.py -v
"""

from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from engines.toc_engine import (
    embed_bookmarks,
    entries_to_fitz_toc,
    fitz_toc_to_entries,
    validate_entries,
)
from models.toc_entry import TOCEntry


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_pdf(path: Path, page_count: int = 5) -> Path:
    doc = fitz.open()
    for _ in range(page_count):
        doc.new_page(width=595, height=842)
    doc.save(str(path))
    doc.close()
    return path


def _entry(title: str, page: int, level: int = 1, enabled: bool = True) -> TOCEntry:
    return TOCEntry(title=title, page_number=page, level=level, enabled=enabled)


# ── embed_bookmarks ───────────────────────────────────────────────────────────

class TestEmbedBookmarks:
    def test_embeds_enabled_entries(self, tmp_path):
        p = _make_pdf(tmp_path / "doc.pdf", 5)
        entries = [
            _entry("Chapter 1", 1),
            _entry("Chapter 2", 3),
        ]
        doc = fitz.open(str(p))
        embed_bookmarks(doc, entries)
        toc = doc.get_toc()
        doc.close()
        assert len(toc) == 2
        assert toc[0][1] == "Chapter 1"
        assert toc[1][1] == "Chapter 2"

    def test_skips_disabled_entries(self, tmp_path):
        p = _make_pdf(tmp_path / "doc.pdf", 5)
        entries = [
            _entry("Visible", 1, enabled=True),
            _entry("Hidden", 2, enabled=False),
        ]
        doc = fitz.open(str(p))
        embed_bookmarks(doc, entries)
        toc = doc.get_toc()
        doc.close()
        assert len(toc) == 1
        assert toc[0][1] == "Visible"

    def test_empty_entries_clears_toc(self, tmp_path):
        p = _make_pdf(tmp_path / "doc.pdf", 3)
        doc = fitz.open(str(p))
        embed_bookmarks(doc, [])
        toc = doc.get_toc()
        doc.close()
        assert toc == []

    def test_page_numbers_clamped_to_range(self, tmp_path):
        p = _make_pdf(tmp_path / "doc.pdf", 3)
        entries = [_entry("Too High", 999)]
        doc = fitz.open(str(p))
        embed_bookmarks(doc, entries)
        toc = doc.get_toc()
        doc.close()
        assert toc[0][2] <= 3

    def test_level_preserved(self, tmp_path):
        p = _make_pdf(tmp_path / "doc.pdf", 5)
        entries = [
            _entry("Part 1", 1, level=1),
            _entry("Chapter 1.1", 2, level=2),
            _entry("Section 1.1.1", 3, level=3),
        ]
        doc = fitz.open(str(p))
        embed_bookmarks(doc, entries)
        toc = doc.get_toc()
        doc.close()
        levels = [row[0] for row in toc]
        assert levels == [1, 2, 3]


# ── validate_entries ──────────────────────────────────────────────────────────

class TestValidateEntries:
    def test_valid_entries_no_problems(self):
        entries = [_entry("A", 1), _entry("B", 3, level=2)]
        problems = validate_entries(entries, total_pages=5)
        assert problems == []

    def test_empty_title_flagged(self):
        entries = [_entry("", 1)]
        problems = validate_entries(entries, total_pages=5)
        assert len(problems) == 1
        assert "empty" in problems[0][1].lower()

    def test_page_below_one_flagged(self):
        entries = [_entry("X", 0)]
        problems = validate_entries(entries, total_pages=5)
        assert any("< 1" in p for _, p in problems)

    def test_page_above_total_flagged(self):
        entries = [_entry("X", 10)]
        problems = validate_entries(entries, total_pages=5)
        assert any("exceeds" in p for _, p in problems)

    def test_page_equal_to_total_valid(self):
        entries = [_entry("X", 5)]
        assert validate_entries(entries, total_pages=5) == []

    def test_zero_total_pages_skips_upper_bound(self):
        entries = [_entry("X", 999)]
        # total_pages=0 means "unknown"; upper bound not enforced
        assert validate_entries(entries, total_pages=0) == []

    def test_bad_level_flagged(self):
        entries = [TOCEntry(title="X", page_number=1, level=0)]
        problems = validate_entries(entries, total_pages=5)
        assert any("Level" in p for _, p in problems)

    def test_multiple_problems(self):
        entries = [_entry("", 0), _entry("Good", 2)]
        problems = validate_entries(entries, total_pages=5)
        assert len(problems) >= 2


# ── fitz_toc_to_entries ───────────────────────────────────────────────────────

class TestFitzTocToEntries:
    def test_basic_conversion(self):
        toc = [[1, "Chapter 1", 1], [2, "Section 1.1", 3]]
        entries = fitz_toc_to_entries(toc)
        assert len(entries) == 2
        assert entries[0].title == "Chapter 1"
        assert entries[0].level == 1
        assert entries[1].title == "Section 1.1"
        assert entries[1].level == 2

    def test_parent_ids_set_correctly(self):
        toc = [[1, "Part", 1], [2, "Chapter", 2], [3, "Section", 3]]
        entries = fitz_toc_to_entries(toc)
        assert entries[1].parent_id == entries[0].entry_id
        assert entries[2].parent_id == entries[1].entry_id

    def test_root_entries_have_no_parent(self):
        toc = [[1, "A", 1], [1, "B", 5]]
        entries = fitz_toc_to_entries(toc)
        assert entries[0].parent_id is None
        assert entries[1].parent_id is None

    def test_empty_toc(self):
        assert fitz_toc_to_entries([]) == []

    def test_all_entries_enabled_by_default(self):
        toc = [[1, "X", 1]]
        entries = fitz_toc_to_entries(toc)
        assert entries[0].enabled is True


# ── entries_to_fitz_toc ───────────────────────────────────────────────────────

class TestEntriesToFitzToc:
    def test_basic_roundtrip(self):
        entries = [_entry("A", 1, 1), _entry("B", 3, 2)]
        toc = entries_to_fitz_toc(entries)
        assert toc == [[1, "A", 1], [2, "B", 3]]

    def test_disabled_entries_excluded(self):
        entries = [_entry("Visible", 1), _entry("Hidden", 2, enabled=False)]
        toc = entries_to_fitz_toc(entries)
        assert len(toc) == 1
        assert toc[0][1] == "Visible"

    def test_empty_entries(self):
        assert entries_to_fitz_toc([]) == []


# ── merged_toc passthrough (via TOCService) ───────────────────────────────────

class TestMergedTocOffsets:
    """Verify that page numbers are passed through as-is (no offset arithmetic)."""

    def _make_section_entries(self, titles_and_pages):
        """Helper: create TOCEntry list from [(title, page)] pairs."""
        return [TOCEntry(title=t, page_number=p, level=1) for t, p in titles_and_pages]

    def test_single_section_passthrough(self):
        from models.toc_section import TOCSection
        from services.toc_service import TOCService
        from PySide6.QtCore import QCoreApplication
        import sys
        app = QCoreApplication.instance() or QCoreApplication(sys.argv)

        svc = TOCService()
        section = TOCSection(
            doc_id="a", doc_title="A.pdf", page_offset=0,
            entries=self._make_section_entries([("Intro", 1), ("Ch 1", 3)]),
        )
        svc._sections = [section]

        result = svc.merged_toc()
        assert result == [[1, "Intro", 1], [1, "Ch 1", 3]]

    def test_two_sections_no_offset_added(self):
        from models.toc_section import TOCSection
        from services.toc_service import TOCService
        from PySide6.QtCore import QCoreApplication
        import sys
        app = QCoreApplication.instance() or QCoreApplication(sys.argv)

        svc = TOCService()
        # User already entered absolute page numbers — stored as-is
        s1 = TOCSection(
            doc_id="a", doc_title="A.pdf", page_offset=0,
            entries=self._make_section_entries([("A Intro", 1)]),
        )
        s2 = TOCSection(
            doc_id="b", doc_title="B.pdf", page_offset=10,
            entries=self._make_section_entries([("B Intro", 11), ("B Ch 2", 15)]),
        )
        svc._sections = [s1, s2]

        result = svc.merged_toc()
        # No offset added — page numbers stored by user are used directly
        assert result[0] == [1, "A Intro", 1]
        assert result[1] == [1, "B Intro", 11]
        assert result[2] == [1, "B Ch 2", 15]

    def test_disabled_entries_excluded_from_merged_toc(self):
        from models.toc_section import TOCSection
        from services.toc_service import TOCService
        from PySide6.QtCore import QCoreApplication
        import sys
        app = QCoreApplication.instance() or QCoreApplication(sys.argv)

        svc = TOCService()
        entries = [
            TOCEntry(title="Visible", page_number=6, level=1, enabled=True),
            TOCEntry(title="Hidden", page_number=7, level=1, enabled=False),
        ]
        section = TOCSection(doc_id="a", doc_title="A.pdf", page_offset=5, entries=entries)
        svc._sections = [section]

        result = svc.merged_toc()
        assert len(result) == 1
        assert result[0][1] == "Visible"
        assert result[0][2] == 6  # stored value, no offset added

    def test_three_sections_recalculate_auto_entries(self):
        from models.toc_section import TOCSection
        from services.toc_service import TOCService
        from models.pdf_document import PDFDocument
        from pathlib import Path
        from PySide6.QtCore import QCoreApplication
        import sys
        app = QCoreApplication.instance() or QCoreApplication(sys.argv)

        def _doc(doc_id, page_count):
            d = PDFDocument.__new__(PDFDocument)
            d.doc_id = doc_id
            d.page_count = page_count
            d.title = doc_id
            d.path = Path("/fake.pdf")
            d.file_size_bytes = 0
            d.password = ""
            d.is_encrypted = False
            return d

        docs = [_doc("a", 5), _doc("b", 8), _doc("c", 3)]
        svc = TOCService()
        for doc in docs:
            # Auto-entries: single level-1 entry with title = doc title
            svc._sections.append(
                TOCSection(
                    doc_id=doc.doc_id, doc_title=doc.title,
                    page_offset=0,
                    entries=[TOCEntry(title=doc.doc_id, page_number=1, level=1)],
                )
            )
        svc.recalculate_offsets(docs)

        # recalculate_offsets should fix auto-entry page numbers to offset+1
        assert svc._sections[0].entries[0].page_number == 1   # offset 0 → page 1
        assert svc._sections[1].entries[0].page_number == 6   # offset 5 → page 6
        assert svc._sections[2].entries[0].page_number == 14  # offset 13 → page 14

        # merged_toc passes through stored values unchanged
        result = svc.merged_toc()
        assert result[0][2] == 1
        assert result[1][2] == 6
        assert result[2][2] == 14
