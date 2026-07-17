"""
services/undo_stack.py — Generic command-pattern undo/redo stack.

Commands are simple objects with execute() / undo() methods.
The stack has a configurable maximum depth.

Usage::

    stack = UndoStack(max_size=100)
    stack.push(SomeCommand(...))   # executes and records the command
    stack.undo()
    stack.redo()
    stack.can_undo  # bool
    stack.can_redo  # bool
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections import deque
from typing import Optional

log = logging.getLogger(__name__)

_DEFAULT_MAX = 100


class Command(ABC):
    """Abstract base for all undoable commands.

    Subclasses must implement ``execute()`` and ``undo()``.
    The ``description`` attribute is shown in the Undo/Redo menu item text.
    """

    description: str = "Unknown operation"

    @abstractmethod
    def execute(self) -> None:
        """Apply the command."""

    @abstractmethod
    def undo(self) -> None:
        """Reverse the command."""


class UndoStack:
    """Fixed-depth undo/redo stack.

    Attributes:
        max_size: Maximum number of commands to keep in history.
    """

    def __init__(self, max_size: int = _DEFAULT_MAX) -> None:
        self.max_size = max_size
        self._undo_stack: deque[Command] = deque(maxlen=max_size)
        self._redo_stack: deque[Command] = deque()

    # ── Core API ──────────────────────────────────────────────────────────

    def push(self, command: Command) -> None:
        """Execute *command* and add it to the undo stack.

        Pushing a new command always clears the redo stack.

        Args:
            command: Command to execute.
        """
        command.execute()
        self._undo_stack.append(command)
        self._redo_stack.clear()
        log.debug("UndoStack push: %r  (depth=%d)", command.description, len(self._undo_stack))

    def undo(self) -> Optional[str]:
        """Undo the most recent command.

        Returns:
            Description of the undone command, or None if nothing to undo.
        """
        if not self._undo_stack:
            return None
        command = self._undo_stack.pop()
        command.undo()
        self._redo_stack.append(command)
        log.debug("UndoStack undo: %r", command.description)
        return command.description

    def redo(self) -> Optional[str]:
        """Redo the most recently undone command.

        Returns:
            Description of the redone command, or None if nothing to redo.
        """
        if not self._redo_stack:
            return None
        command = self._redo_stack.pop()
        command.execute()
        self._undo_stack.append(command)
        log.debug("UndoStack redo: %r", command.description)
        return command.description

    def clear(self) -> None:
        """Discard all undo and redo history."""
        self._undo_stack.clear()
        self._redo_stack.clear()
        log.debug("UndoStack cleared")

    # ── State queries ─────────────────────────────────────────────────────

    @property
    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo_stack)

    @property
    def undo_description(self) -> Optional[str]:
        """Description of the command that would be undone next."""
        return self._undo_stack[-1].description if self._undo_stack else None

    @property
    def redo_description(self) -> Optional[str]:
        """Description of the command that would be redone next."""
        return self._redo_stack[-1].description if self._redo_stack else None

    def __len__(self) -> int:
        return len(self._undo_stack)
