"""
tests/test_toc_service.py — Unit tests for services/toc_service.py (section model).

Run with:
    QT_QPA_PLATFORM=offscreen pytest tests/test_toc_service.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from models.toc_entry import TOCEntry
from models.toc_section import TOCSection
from services.toc_service import TOCService


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def svc(qapp):
    return TOCService()


def _fake_doc(doc_id: str, page_count: int = 5, title: str = "") -> MagicMock:
    """Build a minimal PDFDocument-like mock."""
    doc = MagicMock()
    doc.doc_id = doc_id
    doc.page_count = page_count
    doc.title = title or f"{doc_id}.pdf"
    doc.path = Path(f"/fake/{doc_id}.pdf")
    doc.password = ""
    return doc


def _section(doc_id: str, offset: int = 0, entries=None) -> TOCSection:
    """Build a TOCSection directly (no file I/O)."""
    if entries is None:
        entries = [TOCEntry(title="Entry", page_number=1, level=1)]
    return TOCSection(doc_id=doc_id, doc_title=f"{doc_id}.pdf", page_offset=offset, entries=entries)


# ── load_document ─────────────────────────────────────────────────────────────

class TestLoadDocument:
    def test_creates_section_with_bookmarks(self, svc, tmp_path):
        """If the PDF has bookmarks, they are loaded into the section."""
        import fitz
        pdf_path = tmp_path / "book.pdf"
        fitz_doc = fitz.open()
        for _ in range(5):
            fitz_doc.new_page()
        fitz_doc.set_toc([[1, "Chapter 1", 1], [2, "Section 1.1", 2]])
        fitz_doc.save(str(pdf_path))
        fitz_doc.close()

        doc = _fake_doc("a", 5, "book")
        doc.path = pdf_path

        svc.load_document(doc)

        assert len(svc._sections) == 1
        assert svc._sections[0].doc_id == "a"
        assert len(svc._sections[0].entries) == 2
        assert svc._sections[0].entries[0].title == "Chapter 1"

    def test_creates_auto_entry_when_no_bookmarks(self, svc, tmp_path):
        """PDF without bookmarks gets a single auto-generated entry."""
        import fitz
        pdf_path = tmp_path / "plain.pdf"
        fitz_doc = fitz.open()
        for _ in range(3):
            fitz_doc.new_page()
        fitz_doc.save(str(pdf_path))
        fitz_doc.close()

        doc = _fake_doc("b", 3, "plain")
        doc.path = pdf_path

        svc.load_document(doc)

        assert len(svc._sections) == 1
        entries = svc._sections[0].entries
        assert len(entries) == 1
        assert entries[0].page_number == 1
        assert entries[0].level == 1

    def test_no_duplicate_on_reload(self, svc, tmp_path):
        """Calling load_document twice for the same doc_id is a no-op."""
        import fitz
        pdf_path = tmp_path / "dup.pdf"
        fitz_doc = fitz.open()
        fitz_doc.new_page()
        fitz_doc.save(str(pdf_path))
        fitz_doc.close()

        doc = _fake_doc("c", 1)
        doc.path = pdf_path

        svc.load_document(doc)
        svc.load_document(doc)

        assert len(svc._sections) == 1

    def test_sections_changed_emitted(self, svc, tmp_path, qtbot):
        import fitz
        pdf_path = tmp_path / "sig.pdf"
        fitz_doc = fitz.open()
        fitz_doc.new_page()
        fitz_doc.save(str(pdf_path))
        fitz_doc.close()

        doc = _fake_doc("d", 1)
        doc.path = pdf_path

        with qtbot.waitSignal(svc.sections_changed, timeout=500):
            svc.load_document(doc)


# ── remove_document ───────────────────────────────────────────────────────────

class TestRemoveDocument:
    def test_removes_section(self, svc):
        svc._sections = [_section("a"), _section("b")]
        svc.remove_document("a")
        assert len(svc._sections) == 1
        assert svc._sections[0].doc_id == "b"

    def test_unknown_doc_id_no_error(self, svc):
        svc._sections = [_section("a")]
        svc.remove_document("nonexistent")
        assert len(svc._sections) == 1

    def test_active_doc_id_updated(self, svc):
        svc._sections = [_section("a"), _section("b")]
        svc._active_doc_id = "a"
        svc.remove_document("a")
        assert svc._active_doc_id == "b"

    def test_active_doc_id_none_when_empty(self, svc):
        svc._sections = [_section("a")]
        svc._active_doc_id = "a"
        svc.remove_document("a")
        assert svc._active_doc_id is None

    def test_sections_changed_emitted(self, svc, qtbot):
        svc._sections = [_section("a")]
        with qtbot.waitSignal(svc.sections_changed, timeout=500):
            svc.remove_document("a")


# ── reorder_documents ─────────────────────────────────────────────────────────

class TestReorderDocuments:
    def test_reorders_sections(self, svc):
        svc._sections = [_section("a"), _section("b"), _section("c")]
        svc.reorder_documents(["c", "a", "b"])
        ids = [s.doc_id for s in svc._sections]
        assert ids == ["c", "a", "b"]

    def test_missing_ids_ignored(self, svc):
        svc._sections = [_section("a"), _section("b")]
        svc.reorder_documents(["b", "a", "nonexistent"])
        ids = [s.doc_id for s in svc._sections]
        assert ids == ["b", "a"]

    def test_sections_changed_emitted(self, svc, qtbot):
        svc._sections = [_section("a"), _section("b")]
        with qtbot.waitSignal(svc.sections_changed, timeout=500):
            svc.reorder_documents(["b", "a"])


# ── recalculate_offsets ───────────────────────────────────────────────────────

class TestRecalculateOffsets:
    def test_offsets_correct(self, svc):
        docs = [_fake_doc("a", 5), _fake_doc("b", 10), _fake_doc("c", 3)]
        svc._sections = [_section(d.doc_id) for d in docs]
        svc.recalculate_offsets(docs)
        assert svc._sections[0].page_offset == 0
        assert svc._sections[1].page_offset == 5
        assert svc._sections[2].page_offset == 15

    def test_single_doc_offset_zero(self, svc):
        docs = [_fake_doc("a", 7)]
        svc._sections = [_section("a")]
        svc.recalculate_offsets(docs)
        assert svc._sections[0].page_offset == 0

    def test_sections_changed_emitted(self, svc, qtbot):
        docs = [_fake_doc("a", 3)]
        svc._sections = [_section("a")]
        with qtbot.waitSignal(svc.sections_changed, timeout=500):
            svc.recalculate_offsets(docs)


# ── active section ────────────────────────────────────────────────────────────

class TestActiveSection:
    def test_none_when_no_active(self, svc):
        svc._sections = [_section("a")]
        assert svc.active_section is None

    def test_returns_correct_section(self, svc):
        svc._sections = [_section("a"), _section("b")]
        svc.set_active_doc("b")
        assert svc.active_section is svc._sections[1]

    def test_returns_none_for_unknown_id(self, svc):
        svc._sections = [_section("a")]
        svc.set_active_doc("nope")
        assert svc.active_section is None


# ── merged_toc ────────────────────────────────────────────────────────────────

class TestMergedToc:
    def test_empty_service(self, svc):
        assert svc.merged_toc() == []

    def test_single_section(self, svc):
        svc._sections = [
            TOCSection(
                doc_id="a", doc_title="A", page_offset=0,
                entries=[
                    TOCEntry(title="Ch1", page_number=1, level=1),
                    TOCEntry(title="Ch2", page_number=3, level=1),
                ],
            )
        ]
        result = svc.merged_toc()
        assert result == [[1, "Ch1", 1], [1, "Ch2", 3]]

    def test_two_sections_with_offset(self, svc):
        svc._sections = [
            TOCSection(
                doc_id="a", doc_title="A", page_offset=0,
                entries=[TOCEntry(title="A1", page_number=1, level=1)],
            ),
            TOCSection(
                doc_id="b", doc_title="B", page_offset=8,
                # User entered absolute page 10 directly
                entries=[TOCEntry(title="B1", page_number=10, level=1)],
            ),
        ]
        result = svc.merged_toc()
        assert result[0] == [1, "A1", 1]
        assert result[1] == [1, "B1", 10]  # stored as-is, no offset added

    def test_disabled_entries_excluded(self, svc):
        svc._sections = [
            TOCSection(
                doc_id="a", doc_title="A", page_offset=0,
                entries=[
                    TOCEntry(title="On", page_number=1, level=1, enabled=True),
                    TOCEntry(title="Off", page_number=2, level=1, enabled=False),
                ],
            )
        ]
        result = svc.merged_toc()
        assert len(result) == 1
        assert result[0][1] == "On"

    def test_nesting_levels_preserved(self, svc):
        svc._sections = [
            TOCSection(
                doc_id="a", doc_title="A", page_offset=5,
                entries=[
                    # User entered absolute pages directly
                    TOCEntry(title="Part", page_number=6, level=1),
                    TOCEntry(title="Chapter", page_number=7, level=2),
                    TOCEntry(title="Section", page_number=8, level=3),
                ],
            )
        ]
        result = svc.merged_toc()
        levels = [r[0] for r in result]
        assert levels == [1, 2, 3]
        pages = [r[2] for r in result]
        assert pages == [6, 7, 8]  # stored values, no offset added


# ── entry operations ──────────────────────────────────────────────────────────

class TestEntryOperations:
    """Entry-level mutations scoped to the active section."""

    @pytest.fixture()
    def svc_with_sections(self, svc):
        svc._sections = [_section("a"), _section("b")]
        svc.set_active_doc("a")
        return svc

    def test_add_entry_to_active_section(self, svc_with_sections):
        svc = svc_with_sections
        e = svc.add_entry("New", 5)
        assert any(x.entry_id == e.entry_id for x in svc._sections[0].entries)

    def test_add_entry_after_id(self, svc_with_sections):
        svc = svc_with_sections
        existing = svc._sections[0].entries[0]
        new = svc.add_entry("After", 2, after_id=existing.entry_id)
        idx_existing = next(
            i for i, e in enumerate(svc._sections[0].entries)
            if e.entry_id == existing.entry_id
        )
        idx_new = next(
            i for i, e in enumerate(svc._sections[0].entries)
            if e.entry_id == new.entry_id
        )
        assert idx_new == idx_existing + 1

    def test_delete_entry(self, svc_with_sections):
        svc = svc_with_sections
        e = svc._sections[0].entries[0]
        svc.delete_entries([e.entry_id])
        ids = [x.entry_id for x in svc._sections[0].entries]
        assert e.entry_id not in ids

    def test_rename_entry(self, svc_with_sections):
        svc = svc_with_sections
        e = svc._sections[0].entries[0]
        svc.rename_entry(e.entry_id, "Renamed")
        assert svc._sections[0].entries[0].title == "Renamed"

    def test_rename_unknown_returns_false(self, svc):
        assert svc.rename_entry("bogus", "X") is False

    def test_set_page(self, svc_with_sections):
        svc = svc_with_sections
        e = svc._sections[0].entries[0]
        svc.set_page(e.entry_id, 7)
        assert svc._sections[0].entries[0].page_number == 7

    def test_set_page_minimum_one(self, svc_with_sections):
        svc = svc_with_sections
        e = svc._sections[0].entries[0]
        svc.set_page(e.entry_id, -3)
        assert svc._sections[0].entries[0].page_number == 1

    def test_set_enabled_false(self, svc_with_sections):
        svc = svc_with_sections
        e = svc._sections[0].entries[0]
        svc.set_enabled(e.entry_id, False)
        assert svc._sections[0].entries[0].enabled is False

    def test_move_up(self, svc):
        svc._sections = [
            TOCSection(
                doc_id="a", doc_title="A", page_offset=0,
                entries=[
                    TOCEntry(title="First", page_number=1, level=1),
                    TOCEntry(title="Second", page_number=2, level=1),
                ],
            )
        ]
        e2 = svc._sections[0].entries[1]
        svc.move_up(e2.entry_id)
        assert svc._sections[0].entries[0].title == "Second"

    def test_move_up_first_returns_false(self, svc):
        svc._sections = [_section("a")]
        e = svc._sections[0].entries[0]
        assert svc.move_up(e.entry_id) is False

    def test_move_down(self, svc):
        svc._sections = [
            TOCSection(
                doc_id="a", doc_title="A", page_offset=0,
                entries=[
                    TOCEntry(title="First", page_number=1, level=1),
                    TOCEntry(title="Second", page_number=2, level=1),
                ],
            )
        ]
        e1 = svc._sections[0].entries[0]
        svc.move_down(e1.entry_id)
        assert svc._sections[0].entries[0].title == "Second"

    def test_move_down_last_returns_false(self, svc):
        svc._sections = [_section("a")]
        e = svc._sections[0].entries[-1]
        assert svc.move_down(e.entry_id) is False

    def test_indent(self, svc):
        svc._sections = [
            TOCSection(
                doc_id="a", doc_title="A", page_offset=0,
                entries=[TOCEntry(title="A", page_number=1, level=1)],
            )
        ]
        e = svc._sections[0].entries[0]
        svc.indent(e.entry_id)
        assert svc._sections[0].entries[0].level == 2

    def test_indent_at_max_returns_false(self, svc):
        from config import TOC_MAX_LEVELS
        svc._sections = [
            TOCSection(
                doc_id="a", doc_title="A", page_offset=0,
                entries=[TOCEntry(title="A", page_number=1, level=TOC_MAX_LEVELS)],
            )
        ]
        e = svc._sections[0].entries[0]
        assert svc.indent(e.entry_id) is False

    def test_outdent(self, svc):
        svc._sections = [
            TOCSection(
                doc_id="a", doc_title="A", page_offset=0,
                entries=[TOCEntry(title="A", page_number=1, level=3)],
            )
        ]
        e = svc._sections[0].entries[0]
        svc.outdent(e.entry_id)
        assert svc._sections[0].entries[0].level == 2

    def test_outdent_at_level_1_returns_false(self, svc):
        svc._sections = [_section("a")]
        e = svc._sections[0].entries[0]
        e.level = 1
        assert svc.outdent(e.entry_id) is False

    def test_toc_changed_signal_emitted_on_rename(self, svc, qtbot):
        svc._sections = [_section("a")]
        e = svc._sections[0].entries[0]
        with qtbot.waitSignal(svc.toc_changed, timeout=500):
            svc.rename_entry(e.entry_id, "New")


# ── replace_section_entries ───────────────────────────────────────────────────

class TestReplaceSectionEntries:
    def test_replaces_entries(self, svc):
        svc._sections = [_section("a")]
        new_entries = [
            TOCEntry(title="X", page_number=1, level=1),
            TOCEntry(title="Y", page_number=2, level=2),
        ]
        result = svc.replace_section_entries("a", new_entries)
        assert result is True
        assert len(svc._sections[0].entries) == 2
        assert svc._sections[0].entries[0].title == "X"

    def test_returns_false_for_unknown(self, svc):
        svc._sections = [_section("a")]
        assert svc.replace_section_entries("nope", []) is False

    def test_toc_changed_emitted(self, svc, qtbot):
        svc._sections = [_section("a")]
        with qtbot.waitSignal(svc.toc_changed, timeout=500):
            svc.replace_section_entries("a", [])


# ── clear ──────────────────────────────────────────────────────────────────────

class TestClear:
    def test_clears_sections(self, svc):
        svc._sections = [_section("a"), _section("b")]
        svc.clear()
        assert svc._sections == []
        assert svc.is_empty

    def test_sections_changed_emitted(self, svc, qtbot):
        svc._sections = [_section("a")]
        with qtbot.waitSignal(svc.sections_changed, timeout=500):
            svc.clear()


# ── backward-compat entries property ─────────────────────────────────────────

class TestEntriesProperty:
    def test_flat_entries_across_sections(self, svc):
        svc._sections = [
            TOCSection(
                doc_id="a", doc_title="A", page_offset=0,
                entries=[
                    TOCEntry(title="A1", page_number=1, level=1),
                    TOCEntry(title="A2", page_number=2, level=2),
                ],
            ),
            TOCSection(
                doc_id="b", doc_title="B", page_offset=10,
                entries=[TOCEntry(title="B1", page_number=1, level=1)],
            ),
        ]
        flat = svc.entries
        assert len(flat) == 3
        titles = [e.title for e in flat]
        assert titles == ["A1", "A2", "B1"]

    def test_is_empty_with_no_sections(self, svc):
        assert svc.is_empty

    def test_is_not_empty_with_sections(self, svc):
        svc._sections = [_section("a")]
        assert not svc.is_empty


# ── get_entry / get_section_for_entry ─────────────────────────────────────────

class TestGetEntry:
    def test_finds_entry_across_sections(self, svc):
        e = TOCEntry(title="Target", page_number=5, level=1)
        svc._sections = [
            TOCSection(doc_id="a", doc_title="A", page_offset=0,
                       entries=[TOCEntry(title="Other", page_number=1, level=1)]),
            TOCSection(doc_id="b", doc_title="B", page_offset=0, entries=[e]),
        ]
        assert svc.get_entry(e.entry_id) is e

    def test_returns_none_for_unknown(self, svc):
        svc._sections = [_section("a")]
        assert svc.get_entry("nope") is None

    def test_get_section_for_entry(self, svc):
        e = TOCEntry(title="X", page_number=1, level=1)
        s = TOCSection(doc_id="a", doc_title="A", page_offset=0, entries=[e])
        svc._sections = [s]
        assert svc.get_section_for_entry(e.entry_id) is s

    def test_get_section_for_entry_none(self, svc):
        assert svc.get_section_for_entry("ghost") is None
