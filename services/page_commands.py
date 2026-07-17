"""
services/page_commands.py — Undoable commands for page-level operations.

Each command holds a reference to the ProjectService so it can
swap ``project.pages`` in and out on execute/undo.

All commands follow the snapshot pattern:
  - ``_before``: snapshot of ``project.pages`` before the operation.
  - ``_after``:  snapshot after the operation (computed on first execute).

This is simple and correct for lists of lightweight PageItem objects.
"""

from __future__ import annotations

import copy
import logging
from typing import TYPE_CHECKING

from engines.page_engine import (
    copy_pages,
    delete_pages,
    insert_blank_page,
    move_pages,
    rotate_pages,
)
from services.undo_stack import Command

if TYPE_CHECKING:
    from services.project_service import ProjectService

log = logging.getLogger(__name__)


class _PageCommand(Command):
    """Base for all page commands using the snapshot approach."""

    def __init__(self, project: "ProjectService") -> None:
        self._project = project
        self._before = copy.deepcopy(project.pages)
        self._after: list | None = None

    def execute(self) -> None:
        if self._after is not None:
            # Redo path — restore computed result
            self._project.pages = copy.deepcopy(self._after)
            self._project._reindex_display_indices()
        else:
            # First execution — compute and store result
            self._after = copy.deepcopy(self._run())
            self._project.pages = self._after

    def undo(self) -> None:
        self._project.pages = copy.deepcopy(self._before)
        self._project._reindex_display_indices()

    def _run(self) -> list:
        raise NotImplementedError


class DeletePagesCommand(_PageCommand):
    """Delete the pages at the given display indices."""

    description = "Delete pages"

    def __init__(self, project: "ProjectService", indices: list[int]) -> None:
        super().__init__(project)
        self._indices = sorted(indices)

    def _run(self) -> list:
        return delete_pages(self._project.pages, self._indices)


class RotatePagesCommand(_PageCommand):
    """Rotate the pages at the given display indices."""

    def __init__(
        self, project: "ProjectService", indices: list[int], direction: str
    ) -> None:
        super().__init__(project)
        self._indices = indices
        self._direction = direction
        self.description = f"Rotate pages {'CW' if direction == 'cw' else 'CCW'}"

    def _run(self) -> list:
        return rotate_pages(self._project.pages, self._indices, self._direction)


class MovePagesCommand(_PageCommand):
    """Drag-reorder: move pages to a new position."""

    description = "Move pages"

    def __init__(
        self, project: "ProjectService", indices: list[int], target_index: int
    ) -> None:
        super().__init__(project)
        self._indices = indices
        self._target = target_index

    def _run(self) -> list:
        return move_pages(self._project.pages, self._indices, self._target)


class CopyPagesCommand(_PageCommand):
    """Duplicate pages after a given position."""

    description = "Copy pages"

    def __init__(
        self, project: "ProjectService", indices: list[int], after_index: int
    ) -> None:
        super().__init__(project)
        self._indices = indices
        self._after_index = after_index

    def _run(self) -> list:
        return copy_pages(self._project.pages, self._indices, self._after_index)


class InsertBlankPageCommand(_PageCommand):
    """Insert a blank page after a given position."""

    description = "Insert blank page"

    def __init__(
        self,
        project: "ProjectService",
        after_index: int,
        document_id: str,
    ) -> None:
        super().__init__(project)
        self._after_index = after_index
        self._document_id = document_id

    def _run(self) -> list:
        return insert_blank_page(
            self._project.pages,
            self._after_index,
            self._document_id,
        )
