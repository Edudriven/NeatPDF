"""
engines/merge_engine.py — PDF merge engine using PyMuPDF.

Takes an ordered list of PageItem objects and writes a single merged
PDF to an output path.  Progress is reported via a callable so callers
(GUI or CLI) can drive a progress bar.

Blank pages (is_blank=True) are rendered as A4 white pages with no
content, since they have no source PDF to copy from.

Design decisions:
  - Uses fitz.Document.copy_page() for fast in-memory copy (no re-encoding).
  - Rotation from PageItem is applied via the page's /Rotate entry.
  - The engine is stateless — call merge() with all inputs; it closes
    all opened documents on completion or error.
  - Passwords are passed per-document via the PDFDocument mapping.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional

import fitz  # PyMuPDF

from models.page_item import PageItem

log = logging.getLogger(__name__)

# Standard A4 dimensions in points (72 pt/inch)
_A4_WIDTH = 595
_A4_HEIGHT = 842

ProgressCallback = Callable[[int, int], None]  # (current, total)


class MergeError(Exception):
    """Raised when the merge cannot be completed."""


def merge(
    pages: list[PageItem],
    output_path: Path,
    passwords: Optional[dict[str, str]] = None,
    progress: Optional[ProgressCallback] = None,
) -> None:
    """Merge *pages* into a single PDF at *output_path*.

    Args:
        pages: Ordered list of PageItem objects to include.
        output_path: Destination file path. Parent directory must exist.
        passwords: Mapping of document_id → password for encrypted sources.
            Pass None or an empty dict if no documents are encrypted.
        progress: Optional callable ``(current, total)`` called after each
            page is written.  ``current`` is 1-based.

    Raises:
        MergeError: If an input PDF cannot be opened or output cannot be saved.
        ValueError: If *pages* is empty.
    """
    if not pages:
        raise ValueError("Cannot merge: page list is empty.")

    passwords = passwords or {}
    total = len(pages)

    # ── Open all unique source PDFs ────────────────────────────────────────
    # Map source_path → fitz.Document so we open each file at most once.
    open_docs: dict[Path, fitz.Document] = {}

    try:
        source_paths = {p.source_path for p in pages if not p.is_blank}
        for path in source_paths:
            if not path.exists():
                raise MergeError(f"Source file not found: {path}")
            try:
                doc = fitz.open(str(path))
            except Exception as exc:
                raise MergeError(f"Cannot open {path.name}: {exc}") from exc

            if doc.is_encrypted:
                # Find the document_id for this path to look up the password
                pwd = ""
                for page in pages:
                    if page.source_path == path:
                        pwd = passwords.get(page.document_id, "")
                        break
                if not doc.authenticate(pwd):
                    doc.close()
                    raise MergeError(f"Wrong password for {path.name}.")

            open_docs[path] = doc
            log.debug("Opened source: %s (%d pages)", path.name, doc.page_count)

        # ── Build output document ──────────────────────────────────────────
        out = fitz.open()

        for i, page_item in enumerate(pages):
            if page_item.is_blank:
                _insert_blank(out, page_item.rotation)
            else:
                src_doc = open_docs[page_item.source_path]
                src_idx = page_item.source_page_index

                if src_idx < 0 or src_idx >= src_doc.page_count:
                    raise MergeError(
                        f"Page index {src_idx} out of range for "
                        f"{page_item.source_path.name} "
                        f"({src_doc.page_count} pages)."
                    )

                # Copy the page into the output document from the source
                out.insert_pdf(src_doc, from_page=src_idx, to_page=src_idx)

                # Apply user rotation on top of the page's existing /Rotate
                if page_item.rotation != 0:
                    out_page = out[-1]
                    existing = out_page.rotation
                    out_page.set_rotation((existing + page_item.rotation) % 360)

            if progress:
                progress(i + 1, total)

        # ── Save ───────────────────────────────────────────────────────────
        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            out.save(
                str(output_path),
                garbage=4,      # remove unused objects
                deflate=True,   # compress streams
                clean=True,     # sanitise content streams
            )
        except Exception as exc:
            raise MergeError(f"Cannot save to {output_path}: {exc}") from exc
        finally:
            out.close()

        log.info(
            "Merge complete: %d pages → %s (%.1f KB)",
            total,
            output_path.name,
            output_path.stat().st_size / 1024,
        )

    finally:
        for doc in open_docs.values():
            doc.close()


def _insert_blank(out: fitz.Document, rotation: int) -> None:
    """Insert a white A4 blank page into *out* with the given rotation."""
    if rotation in (90, 270):
        out.new_page(width=_A4_HEIGHT, height=_A4_WIDTH)
    else:
        out.new_page(width=_A4_WIDTH, height=_A4_HEIGHT)
    if rotation != 0:
        out[-1].set_rotation(rotation)
