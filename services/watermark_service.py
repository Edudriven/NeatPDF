"""
services/watermark_service.py — Orchestrates watermark detection and removal.

Runs detection on a background thread and reports results via Qt signals.
Removal is also async.  The service never modifies source files.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from engines.watermark_engine import RemovalError, detect_watermarks, detect_watermarks_quick, remove_watermarks
from models.pdf_document import PDFDocument
from models.watermark_result import WatermarkResult

log = logging.getLogger(__name__)


# ── Worker signals ────────────────────────────────────────────────────────────

class _DetectSignals(QObject):
    finished = Signal(str, list)  # doc_id, list[WatermarkResult]
    failed = Signal(str, str)     # doc_id, error_message


class _RemoveSignals(QObject):
    finished = Signal(str, int)   # output_path, pages_cleaned
    failed = Signal(str)          # error_message


# ── Workers ───────────────────────────────────────────────────────────────────

class _DetectWorker(QRunnable):
    def __init__(self, doc: PDFDocument, quick: bool = False) -> None:
        super().__init__()
        self._doc = doc
        self._quick = quick
        self.signals = _DetectSignals()

    @Slot()
    def run(self) -> None:
        try:
            if self._quick:
                results = detect_watermarks_quick(self._doc.path, self._doc.password)
            else:
                results = detect_watermarks(self._doc.path, self._doc.password)
            self.signals.finished.emit(self._doc.doc_id, results)
        except Exception as exc:
            self.signals.failed.emit(self._doc.doc_id, str(exc))


class _RemoveWorker(QRunnable):
    def __init__(
        self,
        source_path: Path,
        results: list[WatermarkResult],
        output_path: Path,
        password: str,
    ) -> None:
        super().__init__()
        self._source = source_path
        self._results = results
        self._output = output_path
        self._password = password
        self.signals = _RemoveSignals()

    @Slot()
    def run(self) -> None:
        try:
            n = remove_watermarks(
                self._source, self._results, self._output, self._password
            )
            self.signals.finished.emit(str(self._output), n)
        except RemovalError as exc:
            self.signals.failed.emit(str(exc))


# ── Service ───────────────────────────────────────────────────────────────────

class WatermarkService(QObject):
    """Async watermark detection and removal service.

    Signals:
        detection_finished: (doc_id, results) — detection completed.
        detection_failed:   (doc_id, message) — detection error.
        removal_finished:   (output_path, pages_cleaned) — removal done.
        removal_failed:     (message) — removal error.
    """

    detection_finished = Signal(str, list)   # doc_id, list[WatermarkResult]
    detection_failed = Signal(str, str)      # doc_id, error_message
    removal_finished = Signal(str, int)      # output_path, pages_cleaned
    removal_failed = Signal(str)             # error_message

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._busy = False

    @property
    def is_busy(self) -> bool:
        return self._busy

    def detect(self, doc: PDFDocument, quick: bool = False) -> None:
        """Start async watermark detection on *doc*.

        Args:
            doc: Document to scan.
            quick: If True, only run the fast OCG/Artifact pass.
        """
        worker = _DetectWorker(doc, quick=quick)
        worker.signals.finished.connect(self._on_detect_done)
        worker.signals.failed.connect(self._on_detect_failed)
        self._busy = True
        log.info(
            "WatermarkService: %s detection in %s",
            "quick" if quick else "full",
            doc.title,
        )
        QThreadPool.globalInstance().start(worker)

    def remove(
        self,
        source_path: Path,
        results: list[WatermarkResult],
        output_path: Path,
        password: str = "",
    ) -> None:
        """Start async watermark removal, writing result to *output_path*."""
        worker = _RemoveWorker(source_path, results, output_path, password)
        worker.signals.finished.connect(self._on_remove_done)
        worker.signals.failed.connect(self._on_remove_failed)
        self._busy = True
        log.info("WatermarkService: removing from %s", source_path.name)
        QThreadPool.globalInstance().start(worker)

    def _on_detect_done(self, doc_id: str, results: list) -> None:
        self._busy = False
        self.detection_finished.emit(doc_id, results)

    def _on_detect_failed(self, doc_id: str, msg: str) -> None:
        self._busy = False
        self.detection_failed.emit(doc_id, msg)

    def _on_remove_done(self, path: str, n: int) -> None:
        self._busy = False
        self.removal_finished.emit(path, n)

    def _on_remove_failed(self, msg: str) -> None:
        self._busy = False
        self.removal_failed.emit(msg)
