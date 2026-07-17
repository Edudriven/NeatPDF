"""
services/export_service.py — Orchestrates the merge-and-save pipeline.

ExportService sits between the GUI and MergeEngine.  It:
  - Collects page list and passwords from ProjectService.
  - Runs MergeEngine.merge() in a background QThread so the UI stays
    responsive on large documents.
  - Optionally embeds PDF bookmarks from TOCService after merging.
  - Reports progress and completion/error back to the GUI via Qt signals.

Usage::

    svc = ExportService(project_service, parent_widget)
    svc.export_started.connect(...)
    svc.export_progress.connect(...)   # (current_page, total_pages)
    svc.export_finished.connect(...)   # (output_path_str,)
    svc.export_failed.connect(...)     # (error_message,)
    svc.export(output_path)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from engines.merge_engine import MergeError, merge
from models.page_item import PageItem
from services.project_service import ProjectService

if TYPE_CHECKING:
    from services.toc_service import TOCService

log = logging.getLogger(__name__)


class _MergeSignals(QObject):
    """Signals emitted by the background merge worker."""

    progress = Signal(int, int)   # current, total
    finished = Signal(str)        # output_path as str
    failed = Signal(str)          # error message


class _MergeWorker(QRunnable):
    """QRunnable that runs MergeEngine.merge() on a thread-pool thread."""

    def __init__(
        self,
        pages: list[PageItem],
        output_path: Path,
        passwords: dict[str, str],
        toc_entries: Optional[list] = None,
        watermark_removals: Optional[dict] = None,
        doc_paths: Optional[dict] = None,
    ) -> None:
        super().__init__()
        self._pages = pages
        self._output_path = output_path
        self._passwords = passwords
        self._toc_entries = toc_entries  # list[TOCEntry] or None
        # dict[doc_id → list[WatermarkResult]]
        self._watermark_removals: dict = watermark_removals or {}
        # dict[doc_id → Path] — needed for per-doc removal before merge
        self._doc_paths: dict = doc_paths or {}
        self.signals = _MergeSignals()

    @Slot()
    def run(self) -> None:
        try:
            # If there are watermark removals, apply them to temp cleaned copies
            # before merging, then redirect page sources to cleaned copies.
            pages = self._pages
            if self._watermark_removals:
                pages = self._apply_watermark_removals()

            merge(
                pages=pages,
                output_path=self._output_path,
                passwords=self._passwords,
                progress=self.signals.progress.emit,
            )

            # Embed bookmarks if TOC entries were provided
            if self._toc_entries:
                self._embed_bookmarks()

            self.signals.finished.emit(str(self._output_path))
        except (MergeError, ValueError, OSError) as exc:
            log.error("Merge failed: %s", exc)
            self.signals.failed.emit(str(exc))

    def _apply_watermark_removals(self) -> list[PageItem]:
        """Apply deferred watermark removals to in-memory cleaned copies.

        For each doc_id that has pending removals, writes a cleaned temp file
        and returns a page list where those pages point to the cleaned copy.
        """
        import tempfile
        from engines.watermark_engine import remove_watermarks

        # Build cleaned copies for each affected doc
        cleaned_paths: dict[str, Path] = {}  # doc_id → temp path
        tmp_dir = Path(tempfile.mkdtemp(prefix="neatpdf_wm_"))

        for doc_id, results in self._watermark_removals.items():
            if not results:
                continue
            source = self._doc_paths.get(doc_id)
            if source is None:
                log.warning("_apply_watermark_removals: no path for doc_id %s", doc_id[:8])
                continue
            password = self._passwords.get(doc_id, "")
            tmp_out = tmp_dir / f"{doc_id}_clean.pdf"
            try:
                remove_watermarks(source, results, tmp_out, password)
                cleaned_paths[doc_id] = tmp_out
                log.info(
                    "Watermark removals applied for %s → %s",
                    source.name, tmp_out.name,
                )
            except Exception as exc:
                log.warning(
                    "Watermark removal failed for %s: %s — using original",
                    source.name, exc,
                )

        if not cleaned_paths:
            return self._pages

        # Rebuild page list, redirecting affected docs to cleaned copies
        new_pages: list[PageItem] = []
        for page in self._pages:
            if page.document_id in cleaned_paths:
                import copy
                p = copy.copy(page)
                p.source_path = cleaned_paths[page.document_id]
                new_pages.append(p)
            else:
                new_pages.append(page)
        return new_pages

    def _embed_bookmarks(self) -> None:
        import fitz
        from engines.toc_engine import embed_bookmarks
        try:
            doc = fitz.open(str(self._output_path))
            embed_bookmarks(doc, self._toc_entries)
            doc.save(str(self._output_path), incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
            doc.close()
            log.info("Bookmarks embedded: %d entries", len(self._toc_entries))
        except Exception as exc:
            # Non-fatal: merge succeeded, bookmarks are best-effort
            log.warning("Failed to embed bookmarks: %s", exc)


class ExportService(QObject):
    """Manages the async export pipeline.

    Signals:
        export_started: Emitted when the merge worker begins.
            Payload: total page count.
        export_progress: Emitted after each page is written.
            Payload: (current, total).
        export_finished: Emitted on success.
            Payload: output path as str.
        export_failed: Emitted on any error.
            Payload: human-readable error message.
    """

    export_started = Signal(int)       # total pages
    export_progress = Signal(int, int) # current, total
    export_finished = Signal(str)      # output path
    export_failed = Signal(str)        # error message

    def __init__(
        self,
        project: ProjectService,
        parent: Optional[QObject] = None,
        toc_service: Optional["TOCService"] = None,
    ) -> None:
        super().__init__(parent)
        self._project = project
        self._toc_svc = toc_service
        self._busy = False

    # ── Public API ────────────────────────────────────────────────────────

    def set_toc_service(self, toc_service: "TOCService") -> None:
        """Attach a TOCService so bookmarks are embedded on export."""
        self._toc_svc = toc_service

    @property
    def is_busy(self) -> bool:
        """True while an export is in progress."""
        return self._busy

    def export(self, output_path: Path) -> None:
        """Start an async merge-and-save operation.

        Does nothing if an export is already running.

        Args:
            output_path: Destination file path for the merged PDF.
        """
        if self._busy:
            log.warning("ExportService: export already in progress — ignored")
            return

        pages = list(self._project.pages)
        if not pages:
            self.export_failed.emit("No pages to export.")
            return

        passwords: dict[str, str] = {
            doc.doc_id: doc.password
            for doc in self._project.documents
            if doc.password
        }

        # Use merged_toc() which applies page offsets and filters disabled entries
        toc_entries: Optional[list] = None
        if self._toc_svc and not self._toc_svc.is_empty:
            raw = self._toc_svc.merged_toc()
            if raw:
                from models.toc_entry import TOCEntry as _TOCEntry
                # merged_toc() already returns [[level, title, abs_page], ...]
                # _embed_bookmarks expects list[TOCEntry]; convert here.
                toc_entries_converted = [
                    _TOCEntry(title=row[1], page_number=row[2], level=row[0])
                    for row in raw
                ]
                toc_entries = toc_entries_converted  # type: ignore[assignment]

        # Collect any pending watermark removals from ProjectService
        watermark_removals: dict = {}
        doc_paths: dict = {}
        if hasattr(self._project, "watermark_removals"):
            watermark_removals = dict(self._project.watermark_removals)
        for doc in self._project.documents:
            doc_paths[doc.doc_id] = doc.path

        worker = _MergeWorker(
            pages, output_path, passwords, toc_entries,
            watermark_removals=watermark_removals,
            doc_paths=doc_paths,
        )
        worker.signals.progress.connect(self._on_progress)
        worker.signals.finished.connect(self._on_finished)
        worker.signals.failed.connect(self._on_failed)

        self._busy = True
        self.export_started.emit(len(pages))
        log.info(
            "Export started: %d pages → %s", len(pages), output_path.name
        )
        QThreadPool.globalInstance().start(worker)

    # ── Worker callbacks ──────────────────────────────────────────────────

    def _on_progress(self, current: int, total: int) -> None:
        self.export_progress.emit(current, total)

    def _on_finished(self, path: str) -> None:
        self._busy = False
        self.export_finished.emit(path)
        log.info("Export finished: %s", path)

    def _on_failed(self, message: str) -> None:
        self._busy = False
        self.export_failed.emit(message)
        log.error("Export failed: %s", message)
