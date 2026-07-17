"""
models/page_item.py — Represents a single page within the working session.

A PageItem does NOT hold rendered pixel data — it holds only the
metadata needed to identify, display, and operate on a page.
Rendered thumbnails are managed separately by PreviewService.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class PageItem:
    """One page in the current working document set.

    Attributes:
        source_path: Absolute path of the PDF this page came from.
        source_page_index: 0-based page index within the source PDF.
        display_index: 0-based position in the merged/working page list.
        rotation: Cumulative rotation applied by the user (0, 90, 180, 270).
        is_blank: True if this is a user-inserted blank page (no source file).
        document_id: UUID-string of the parent PDFDocument.
    """

    source_path: Path
    source_page_index: int
    display_index: int
    document_id: str
    rotation: int = 0
    is_blank: bool = False

    def rotate_cw(self) -> None:
        """Rotate 90° clockwise in place."""
        self.rotation = (self.rotation + 90) % 360

    def rotate_ccw(self) -> None:
        """Rotate 90° counter-clockwise in place."""
        self.rotation = (self.rotation - 90) % 360

    def __repr__(self) -> str:
        return (
            f"PageItem(doc={self.document_id[:8]}, "
            f"src={self.source_page_index}, "
            f"disp={self.display_index}, rot={self.rotation})"
        )
