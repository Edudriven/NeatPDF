"""
models/pdf_document.py — Represents an imported PDF file.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PDFDocument:
    """An imported PDF file in the current session.

    Attributes:
        path: Absolute path to the PDF file on disk.
        page_count: Total number of pages in the original file.
        file_size_bytes: File size in bytes.
        title: Display name (defaults to filename stem).
        doc_id: Unique identifier for this document in the session.
        is_encrypted: True if the PDF requires a password.
        password: Password used to unlock the PDF (if any).
    """

    path: Path
    page_count: int
    file_size_bytes: int
    title: str = ""
    doc_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    is_encrypted: bool = False
    password: str = ""

    def __post_init__(self) -> None:
        if not self.title:
            self.title = self.path.stem

    @property
    def file_size_mb(self) -> float:
        """File size in megabytes, rounded to two decimal places."""
        return round(self.file_size_bytes / (1024 * 1024), 2)

    def __repr__(self) -> str:
        return (
            f"PDFDocument(id={self.doc_id[:8]}, "
            f"title={self.title!r}, pages={self.page_count})"
        )
