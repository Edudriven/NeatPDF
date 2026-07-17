"""
tests/test_undo_stack.py — Unit tests for UndoStack and page commands.

Run with:
    QT_QPA_PLATFORM=offscreen pytest tests/test_undo_stack.py -v
"""

from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from services.project_service import ProjectService
from services.undo_stack import Command, UndoStack


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_pdf(path: Path, page_count: int = 4) -> Path:
    doc = fitz.open()
    for _ in range(page_count):
        doc.new_page(width=595, height=842)
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture()
def pdf_factory(tmp_path):
    counter = {"n": 0}

    def factory(page_count: int = 4) -> Path:
        counter["n"] += 1
        return _make_pdf(tmp_path / f"doc_{counter['n']}.pdf", page_count)

    return factory


@pytest.fixture()
def svc():
    return ProjectService()


@pytest.fixture()
def svc_with_pdf(svc, pdf_factory):
    svc.import_document(pdf_factory(4))
    return svc


# ── UndoStack unit tests ──────────────────────────────────────────────────────

class _DummyCommand(Command):
    """Minimal command that records execution and undo calls."""

    description = "Dummy"

    def __init__(self):
        self.execute_count = 0
        self.undo_count = 0

    def execute(self) -> None:
        self.execute_count += 1

    def undo(self) -> None:
        self.undo_count += 1


class TestUndoStack:
    def test_push_executes_command(self):
        stack = UndoStack()
        cmd = _DummyCommand()
        stack.push(cmd)
        assert cmd.execute_count == 1

    def test_can_undo_after_push(self):
        stack = UndoStack()
        stack.push(_DummyCommand())
        assert stack.can_undo is True

    def test_cannot_undo_initially(self):
        stack = UndoStack()
        assert stack.can_undo is False

    def test_undo_calls_undo_on_command(self):
        stack = UndoStack()
        cmd = _DummyCommand()
        stack.push(cmd)
        stack.undo()
        assert cmd.undo_count == 1

    def test_undo_returns_description(self):
        stack = UndoStack()
        stack.push(_DummyCommand())
        desc = stack.undo()
        assert desc == "Dummy"

    def test_undo_empty_returns_none(self):
        stack = UndoStack()
        assert stack.undo() is None

    def test_can_redo_after_undo(self):
        stack = UndoStack()
        stack.push(_DummyCommand())
        stack.undo()
        assert stack.can_redo is True

    def test_cannot_redo_initially(self):
        stack = UndoStack()
        assert stack.can_redo is False

    def test_redo_re_executes_command(self):
        stack = UndoStack()
        cmd = _DummyCommand()
        stack.push(cmd)
        stack.undo()
        stack.redo()
        assert cmd.execute_count == 2

    def test_redo_returns_description(self):
        stack = UndoStack()
        stack.push(_DummyCommand())
        stack.undo()
        desc = stack.redo()
        assert desc == "Dummy"

    def test_redo_empty_returns_none(self):
        stack = UndoStack()
        assert stack.redo() is None

    def test_push_clears_redo_stack(self):
        stack = UndoStack()
        stack.push(_DummyCommand())
        stack.undo()
        assert stack.can_redo
        stack.push(_DummyCommand())
        assert not stack.can_redo

    def test_multiple_undo_redo_cycle(self):
        stack = UndoStack()
        cmds = [_DummyCommand() for _ in range(3)]
        for c in cmds:
            stack.push(c)
        stack.undo()
        stack.undo()
        stack.undo()
        assert not stack.can_undo
        assert stack.can_redo
        stack.redo()
        stack.redo()
        stack.redo()
        assert stack.can_undo
        assert not stack.can_redo

    def test_max_size_evicts_oldest(self):
        stack = UndoStack(max_size=3)
        for _ in range(5):
            stack.push(_DummyCommand())
        assert len(stack) == 3

    def test_clear_empties_both_stacks(self):
        stack = UndoStack()
        stack.push(_DummyCommand())
        stack.push(_DummyCommand())
        stack.undo()
        stack.clear()
        assert not stack.can_undo
        assert not stack.can_redo

    def test_undo_description_property(self):
        stack = UndoStack()
        cmd = _DummyCommand()
        cmd.description = "My Op"
        stack.push(cmd)
        assert stack.undo_description == "My Op"

    def test_redo_description_property(self):
        stack = UndoStack()
        cmd = _DummyCommand()
        cmd.description = "My Op"
        stack.push(cmd)
        stack.undo()
        assert stack.redo_description == "My Op"

    def test_undo_description_none_when_empty(self):
        assert UndoStack().undo_description is None

    def test_redo_description_none_when_empty(self):
        assert UndoStack().redo_description is None


# ── Page command integration tests via ProjectService ─────────────────────────

class TestDeleteCommand:
    def test_delete_removes_pages(self, svc_with_pdf):
        svc = svc_with_pdf
        svc.delete_pages([0, 1])
        assert len(svc.pages) == 2

    def test_delete_undo_restores_pages(self, svc_with_pdf):
        svc = svc_with_pdf
        original = len(svc.pages)
        svc.delete_pages([0])
        svc.undo()
        assert len(svc.pages) == original

    def test_delete_redo_reapplies(self, svc_with_pdf):
        svc = svc_with_pdf
        svc.delete_pages([1, 2])
        svc.undo()
        svc.redo()
        assert len(svc.pages) == 2

    def test_display_indices_correct_after_delete(self, svc_with_pdf):
        svc = svc_with_pdf
        svc.delete_pages([0])
        indices = [p.display_index for p in svc.pages]
        assert indices == list(range(len(svc.pages)))

    def test_display_indices_correct_after_undo(self, svc_with_pdf):
        svc = svc_with_pdf
        svc.delete_pages([1])
        svc.undo()
        indices = [p.display_index for p in svc.pages]
        assert indices == list(range(len(svc.pages)))


class TestRotateCommand:
    def test_rotate_cw(self, svc_with_pdf):
        svc = svc_with_pdf
        svc.rotate_pages([0], "cw")
        assert svc.pages[0].rotation == 90

    def test_rotate_ccw(self, svc_with_pdf):
        svc = svc_with_pdf
        svc.rotate_pages([0], "ccw")
        assert svc.pages[0].rotation == 270

    def test_rotate_undo_restores(self, svc_with_pdf):
        svc = svc_with_pdf
        svc.rotate_pages([0], "cw")
        svc.undo()
        assert svc.pages[0].rotation == 0

    def test_rotate_redo_reapplies(self, svc_with_pdf):
        svc = svc_with_pdf
        svc.rotate_pages([0], "cw")
        svc.undo()
        svc.redo()
        assert svc.pages[0].rotation == 90

    def test_rotate_description_cw(self, svc_with_pdf):
        svc = svc_with_pdf
        svc.rotate_pages([0], "cw")
        assert "CW" in svc.undo_stack.undo_description

    def test_rotate_description_ccw(self, svc_with_pdf):
        svc = svc_with_pdf
        svc.rotate_pages([0], "ccw")
        assert "CCW" in svc.undo_stack.undo_description


class TestMoveCommand:
    def test_move_changes_order(self, svc_with_pdf):
        svc = svc_with_pdf
        src_before = svc.pages[0].source_page_index
        svc.move_pages([0], 3)
        # Page 0 should no longer be at position 0
        assert svc.pages[0].source_page_index != src_before

    def test_move_undo_restores_order(self, svc_with_pdf):
        svc = svc_with_pdf
        original = [p.source_page_index for p in svc.pages]
        svc.move_pages([0], 3)
        svc.undo()
        assert [p.source_page_index for p in svc.pages] == original

    def test_move_redo_reapplies(self, svc_with_pdf):
        svc = svc_with_pdf
        svc.move_pages([0], 2)
        after_move = [p.source_page_index for p in svc.pages]
        svc.undo()
        svc.redo()
        assert [p.source_page_index for p in svc.pages] == after_move

    def test_move_preserves_page_count(self, svc_with_pdf):
        svc = svc_with_pdf
        before = len(svc.pages)
        svc.move_pages([1, 2], 3)
        assert len(svc.pages) == before


class TestCopyCommand:
    def test_copy_increases_page_count(self, svc_with_pdf):
        svc = svc_with_pdf
        before = len(svc.pages)
        svc.copy_pages([0, 1], 1)
        assert len(svc.pages) == before + 2

    def test_copy_undo_restores_count(self, svc_with_pdf):
        svc = svc_with_pdf
        before = len(svc.pages)
        svc.copy_pages([0], 0)
        svc.undo()
        assert len(svc.pages) == before

    def test_copy_redo_reapplies(self, svc_with_pdf):
        svc = svc_with_pdf
        svc.copy_pages([2], 2)
        after_copy = len(svc.pages)
        svc.undo()
        svc.redo()
        assert len(svc.pages) == after_copy


class TestInsertBlankCommand:
    def test_insert_blank_adds_page(self, svc_with_pdf):
        svc = svc_with_pdf
        before = len(svc.pages)
        svc.insert_blank_page(1)
        assert len(svc.pages) == before + 1

    def test_inserted_page_is_blank(self, svc_with_pdf):
        svc = svc_with_pdf
        svc.insert_blank_page(1)
        assert svc.pages[2].is_blank is True

    def test_insert_blank_undo(self, svc_with_pdf):
        svc = svc_with_pdf
        before = len(svc.pages)
        svc.insert_blank_page(0)
        svc.undo()
        assert len(svc.pages) == before

    def test_insert_blank_redo(self, svc_with_pdf):
        svc = svc_with_pdf
        svc.insert_blank_page(2)
        after = len(svc.pages)
        svc.undo()
        svc.redo()
        assert len(svc.pages) == after

    def test_blank_inherits_adjacent_doc_id(self, svc_with_pdf):
        svc = svc_with_pdf
        adjacent_doc_id = svc.pages[0].document_id
        svc.insert_blank_page(0)
        blank = svc.pages[1]
        assert blank.is_blank
        assert blank.document_id == adjacent_doc_id


# ── Chained undo/redo across multiple command types ───────────────────────────

class TestUndoRedoChaining:
    def test_multiple_operations_undo_in_order(self, svc_with_pdf):
        svc = svc_with_pdf
        original_count = len(svc.pages)

        svc.delete_pages([3])          # 4 → 3 pages
        svc.insert_blank_page(0)       # 3 → 4 pages
        svc.delete_pages([0, 1])       # 4 → 2 pages

        assert len(svc.pages) == 2

        svc.undo()   # undo second delete → 4 pages
        assert len(svc.pages) == 4

        svc.undo()   # undo insert blank → 3 pages
        assert len(svc.pages) == 3

        svc.undo()   # undo first delete → 4 pages (original)
        assert len(svc.pages) == original_count

    def test_undo_then_new_op_clears_redo(self, svc_with_pdf):
        svc = svc_with_pdf
        svc.rotate_pages([0], "cw")
        svc.undo()
        assert svc.undo_stack.can_redo

        svc.delete_pages([1])
        assert not svc.undo_stack.can_redo

    def test_undo_returns_correct_description(self, svc_with_pdf):
        svc = svc_with_pdf
        svc.delete_pages([0])
        svc.rotate_pages([0], "cw")
        desc = svc.undo()
        assert "Rotate" in desc

    def test_undo_nothing_returns_none(self, svc):
        assert svc.undo() is None

    def test_redo_nothing_returns_none(self, svc):
        assert svc.redo() is None

    def test_display_indices_always_sequential_after_undo_redo(self, svc_with_pdf):
        svc = svc_with_pdf
        svc.delete_pages([0, 2])
        svc.insert_blank_page(0)
        svc.undo()
        svc.redo()
        svc.undo()

        indices = [p.display_index for p in svc.pages]
        assert indices == list(range(len(svc.pages)))
