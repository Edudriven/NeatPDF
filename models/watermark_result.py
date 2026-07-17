"""
models/watermark_result.py — Result of watermark detection on a PDF page.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import Optional


class WatermarkType(Enum):
    """Category of detected watermark."""
    TEXT = auto()
    IMAGE = auto()
    VECTOR = auto()
    ARTIFACT = auto()   # PDF-tagged /Artifact Watermark or OCG layer
    UNKNOWN = auto()


@dataclass
class WatermarkResult:
    """Watermark detection result for a single page.

    Attributes:
        source_path: PDF file the detection was run on.
        page_index: 0-based page index where the watermark was found.
        watermark_type: Detected category.
        confidence: Detection confidence in [0.0, 1.0].
        description: Human-readable description for the confirmation dialog.
        highlight_rect: Optional (x0, y0, x1, y1) in PDF user-space coordinates
            of the watermark region, used to render a crop-stamp and highlight
            overlay in the dialog preview panel.
        preview_image_path: Optional path to a rendered preview with the
            watermark highlighted.
        removable: Whether the engine believes safe removal is possible.
    """

    source_path: Path
    page_index: int
    watermark_type: WatermarkType
    confidence: float
    description: str
    highlight_rect: Optional[tuple] = None   # (x0, y0, x1, y1) PDF coords
    preview_image_path: Optional[Path] = None
    removable: bool = False

    @property
    def confidence_pct(self) -> str:
        """Confidence as a percentage string, e.g. '87%'."""
        return f"{round(self.confidence * 100)}%"
