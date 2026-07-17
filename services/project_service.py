"""
services/project_service.py — Session state manager for NeatPDF.

ProjectService is the single source of truth for the current working
session.  It owns all imported PDFDocument objects and the flat ordered
list of PageItem objects that represent the merged page sequence.

The GUI never mutates models directly — it calls ProjectService methods
and observes the Qt signals that ProjectService emits in response.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, TYPE_CHECKING

import fitz  # PyMuPDF

from engines.page_engine import BLANK_DOC_ID
from models.page_item import PageItem
from models.pdf_document import PDFDocument
from services.undo_stack import UndoStack

if TYPE_CHECKING:
    from services.toc_service import TOCService

log = logging.getLogger(__name__)


class DocumentLoadError(Exception):
    """Raised when a PDF file cannot be opened."""


class ProjectService:
    """Manages the in-memory session state.

    Attributes:
        documents: Ordered list of imported PDFDocument objects.
        pages: Flat ordered list of PageItem objects representing the
            merged page sequence (one entry per page, across all docs).
        undo_stack: UndoStack instance for page-level operations.
        toc_service: Optional TOCService; if set, it is kept in sync
            whenever documents are imported, removed, or reordered.
    """

    def __init__(self, toc_service: Optional["TOCService"] = None) -> None:
        self.documents: list[PDFDocument] = []
        self.pages: list[PageItem] = []
        self._doc_index: dict[str, PDFDocument] = {}  # doc_id → PDFDocument
        self.undo_stack: UndoStack = UndoStack(max_size=100)
        self.toc_service: Optional["TOCService"] = toc_service
        # Pending watermark removals: doc_id → list[WatermarkResult]
        # Applied at export time when merging multiple documents.
        self.watermark_removals: dict[str, list] = {}

    # ── Document import ───────────────────────────────────────────────────

    def import_document(
        self, path: Path, password: str = ""
    ) -> PDFDocument:
        """Open a PDF file, create a PDFDocument, and append its pages.

        Args:
            path: Absolute path to the PDF file.
            password: Optional decryption password.

        Returns:
            The newly created PDFDocument.

        Raises:
            DocumentLoadError: If the file cannot be opened or decrypted.
            FileNotFoundError: If the path does not exist.
        """
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        log.info("Opening PDF: %s", path)

        try:
            fitz_doc = fitz.open(str(path))
        except Exception as exc:
            raise DocumentLoadError(f"Cannot open {path.name}: {exc}") from exc

        # Handle encryption
        if fitz_doc.is_encrypted:
            if not password:
                fitz_doc.close()
                raise DocumentLoadError(
                    f"{path.name} is encrypted — password required."
                )
            if not fitz_doc.authenticate(password):
                fitz_doc.close()
                raise DocumentLoadError(
                    f"Incorrect password for {path.name}."
                )

        page_count = fitz_doc.page_count
        file_size = path.stat().st_size
        fitz_doc.close()

        doc = PDFDocument(
            path=path,
            page_count=page_count,
            file_size_bytes=file_size,
            password=password,
        )

        self.documents.append(doc)
        self._doc_index[doc.doc_id] = doc
        self._append_pages(doc)

        # Notify TOCService so it loads bookmarks for this document
        if self.toc_service is not None:
            self.toc_service.load_document(doc)
            self.toc_service.recalculate_offsets(self.documents)

        log.info(
            "Imported: %r  (id=%s, pages=%d)", doc.title, doc.doc_id[:8], page_count
        )
        return doc

    def import_documents(
        self, paths: list[Path], password: str = ""
    ) -> tuple[list[PDFDocument], list[tuple[Path, str]]]:
        """Import multiple PDFs, collecting errors without aborting.

        Args:
            paths: List of PDF file paths.
            password: Password applied to all files (use empty string if none).

        Returns:
            A tuple of (successfully imported docs, list of (path, error_message)).
        """
        succeeded: list[PDFDocument] = []
        failed: list[tuple[Path, str]] = []

        for path in paths:
            try:
                doc = self.import_document(path, password)
                succeeded.append(doc)
            except (DocumentLoadError, FileNotFoundError, OSError) as exc:
                log.warning("Failed to import %s: %s", path, exc)
                failed.append((path, str(exc)))

        return succeeded, failed

    # ── Document management ───────────────────────────────────────────────

    def get_document(self, doc_id: str) -> Optional[PDFDocument]:
        """Return a PDFDocument by its ID, or None if not found."""
        return self._doc_index.get(doc_id)

    def remove_document(self, doc_id: str) -> None:
        """Remove a document and all its pages from the session.

        Args:
            doc_id: ID of the document to remove.
        """
        doc = self._doc_index.pop(doc_id, None)
        if doc is None:
            log.warning("remove_document: unknown id %s", doc_id)
            return

        self.documents = [d for d in self.documents if d.doc_id != doc_id]
        self.pages = [p for p in self.pages if p.document_id != doc_id]
        self._reindex_display_indices()
        self.watermark_removals.pop(doc_id, None)

        # Notify TOCService
        if self.toc_service is not None:
            self.toc_service.remove_document(doc_id)
            self.toc_service.recalculate_offsets(self.documents)

        log.info("Removed document: %r (id=%s)", doc.title, doc_id[:8])

    def reorder_documents(self, ordered_ids: list[str]) -> None:
        """Reorder documents to match the given ID sequence.

        Pages are rebuilt in the new document order.

        Args:
            ordered_ids: Document IDs in the desired display order.
        """
        new_order: list[PDFDocument] = []
        for doc_id in ordered_ids:
            doc = self._doc_index.get(doc_id)
            if doc:
                new_order.append(doc)

        self.documents = new_order
        self._rebuild_pages()

        # Notify TOCService
        if self.toc_service is not None:
            self.toc_service.reorder_documents(ordered_ids)
            self.toc_service.recalculate_offsets(self.documents)

        log.debug("Documents reordered: %s", [d.title for d in self.documents])

    # ── Session management ────────────────────────────────────────────────

    def clear(self) -> None:
        """Remove all documents and pages from the session."""
        self.documents.clear()
        self.pages.clear()
        self._doc_index.clear()
        self.undo_stack.clear()
        self.watermark_removals.clear()
        if self.toc_service is not None:
            self.toc_service.clear()
        log.info("Session cleared")

    @property
    def total_pages(self) -> int:
        """Total number of pages in the current working sequence."""
        return len(self.pages)

    @property
    def is_empty(self) -> bool:
        """True if no documents have been imported."""
        return len(self.documents) == 0

    # ── Page operations (undoable) ────────────────────────────────────────

    def delete_pages(self, indices: list[int]) -> None:
        """Delete pages at the given display indices (undoable).

        Args:
            indices: 0-based display indices to delete.
        """
        from services.page_commands import DeletePagesCommand
        if not indices:
            return
        self.undo_stack.push(DeletePagesCommand(self, indices))
        log.info("delete_pages: %s", indices)

    def rotate_pages(self, indices: list[int], direction: str) -> None:
        """Rotate pages CW or CCW (undoable).

        Args:
            indices: 0-based display indices to rotate.
            direction: ``"cw"`` or ``"ccw"``.
        """
        from services.page_commands import RotatePagesCommand
        if not indices:
            return
        self.undo_stack.push(RotatePagesCommand(self, indices, direction))
        log.info("rotate_pages: %s %s", direction, indices)

    def move_pages(self, indices: list[int], target_index: int) -> None:
        """Move selected pages to a new position (undoable).

        Args:
            indices: 0-based display indices to move.
            target_index: Insertion point in the original list.
        """
        from services.page_commands import MovePagesCommand
        if not indices:
            return
        self.undo_stack.push(MovePagesCommand(self, indices, target_index))
        log.info("move_pages: %s → %d", indices, target_index)

    def copy_pages(self, indices: list[int], after_index: int) -> None:
        """Copy selected pages after *after_index* (undoable).

        Args:
            indices: 0-based display indices to copy.
            after_index: Copies appear after this position.
        """
        from services.page_commands import CopyPagesCommand
        if not indices:
            return
        self.undo_stack.push(CopyPagesCommand(self, indices, after_index))
        log.info("copy_pages: %s after %d", indices, after_index)

    def insert_blank_page(self, after_index: int) -> None:
        """Insert a blank page after *after_index* (undoable).

        Uses the document_id of the adjacent page, or BLANK_DOC_ID if empty.

        Args:
            after_index: Blank is inserted after this position (-1 = prepend).
        """
        from services.page_commands import InsertBlankPageCommand
        # Assign the blank to the adjacent document so color stripe is consistent
        if self.pages and 0 <= after_index < len(self.pages):
            doc_id = self.pages[after_index].document_id
        else:
            doc_id = BLANK_DOC_ID
        self.undo_stack.push(InsertBlankPageCommand(self, after_index, doc_id))
        log.info("insert_blank_page after %d", after_index)

    def undo(self) -> Optional[str]:
        """Undo the last page operation.

        Returns:
            Description of the undone operation, or None if nothing to undo.
        """
        return self.undo_stack.undo()

    def redo(self) -> Optional[str]:
        """Redo the last undone page operation.

        Returns:
            Description of the redone operation, or None if nothing to redo.
        """
        return self.undo_stack.redo()

    # ── Internal helpers ──────────────────────────────────────────────────

    def _append_pages(self, doc: PDFDocument) -> None:
        """Append PageItem objects for every page in *doc*."""
        start_index = len(self.pages)
        for i in range(doc.page_count):
            self.pages.append(
                PageItem(
                    source_path=doc.path,
                    source_page_index=i,
                    display_index=start_index + i,
                    document_id=doc.doc_id,
                )
            )

    def _rebuild_pages(self) -> None:
        """Rebuild the full page list from scratch in document order."""
        self.pages = []
        for doc in self.documents:
            self._append_pages(doc)

    def _reindex_display_indices(self) -> None:
        """Update display_index on all PageItems after a removal."""
        for i, page in enumerate(self.pages):
            page.display_index = i
