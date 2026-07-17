"""
models/toc_entry.py — A single node in the Table of Contents tree.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TOCEntry:
    """One entry in the editable Table of Contents.

    The tree is represented as a flat list in TOCService but each entry
    carries a parent reference so the hierarchy can be reconstructed.

    Attributes:
        title: Display title shown in the TOC.
        page_number: 1-based page number in the final merged PDF.
        level: Nesting depth (1 = top-level chapter, 2 = section, …).
        entry_id: Unique identifier for this entry.
        parent_id: entry_id of the parent, or None for root entries.
        enabled: If False the entry is excluded from the generated TOC.
    """

    title: str
    page_number: int
    level: int = 1
    entry_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    parent_id: Optional[str] = None
    enabled: bool = True

    def __repr__(self) -> str:
        indent = "  " * (self.level - 1)
        status = "" if self.enabled else " [disabled]"
        return f"TOCEntry({indent}{self.title!r} → p{self.page_number}{status})"
