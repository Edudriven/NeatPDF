"""
services/preview_service.py — Thumbnail and page-preview rendering.

Rendering is done on a QThreadPool using QRunnable workers so the UI
thread is never blocked.  Completed thumbnails are delivered via a
Qt signal on the main thread.

Architecture
------------
* PreviewService owns a QThreadPool (bounded to RENDER_THREAD_COUNT).
* Callers request a thumbnail with request_thumbnail(doc_id, page_index).
* The service checks an LRU cache first; on miss it queues a RenderWorker.
* RenderWorker emits thumbnail_ready(doc_id, page_index, QPixmap) on finish.
* Callers connect to thumbnail_ready and update their widgets accordingly.

Thread safety
-------------
* fitz.Document is NOT thread-safe.  Each RenderWorker opens its own
  fitz.Document, renders, and closes it — no shared fitz state.
* The LRU cache is accessed only from the main thread (signal delivery
  ensures this because thumbnail_ready is connected with AutoConnection).
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from pathlib import Path
from typing import Optional

import fitz
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot
from PySide6.QtGui import QImage, QPixmap

from config import (
    MAX_THUMBNAIL_CACHE,
    RENDER_THREAD_COUNT,
    THUMBNAIL_DPI,
    THUMBNAIL_HEIGHT,
    THUMBNAIL_WIDTH,
)

log = logging.getLogger(__name__)


# ── Worker signals (must live on a QObject) ───────────────────────────────────

class _WorkerSignals(QObject):
    """Signals emitted by RenderWorker (QRunnable cannot emit directly)."""

    finished = Signal(str, int, QPixmap, int, int, int)  # doc_id, page_index, pixmap, rotation, width, height
    error = Signal(str, int, str)          # doc_id, page_index, error_message


# ── Render worker ─────────────────────────────────────────────────────────────

class _RenderWorker(QRunnable):
    """Opens a fitz.Document, renders one page as a QPixmap, and emits it."""

    def __init__(
        self,
        doc_id: str,
        path: Path,
        page_index: int,
        rotation: int,
        dpi: float,
        password: str,
        width: int,
        height: int,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._doc_id = doc_id
        self._path = path
        self._page_index = page_index
        self._rotation = rotation
        self._dpi = dpi
        self._password = password
        self._width = width
        self._height = height
        self.signals = _WorkerSignals()

    @Slot()
    def run(self) -> None:  # type: ignore[override]
        try:
            pixmap = self._render()
            self.signals.finished.emit(
                self._doc_id, self._page_index, pixmap,
                self._rotation, self._width, self._height,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "Render error doc=%s page=%d: %s",
                self._doc_id[:8], self._page_index, exc,
            )
            self.signals.error.emit(self._doc_id, self._page_index, str(exc))

    def _render(self) -> QPixmap:
        doc = fitz.open(str(self._path))
        try:
            if doc.is_encrypted and self._password:
                doc.authenticate(self._password)

            page = doc[self._page_index]

            # Scale so the rendered image fits within (width × height) while
            # preserving aspect ratio.
            page_rect = page.rect
            scale_x = self._width / page_rect.width if page_rect.width else 1.0
            scale_y = self._height / page_rect.height if page_rect.height else 1.0
            scale = min(scale_x, scale_y)

            mat = fitz.Matrix(scale, scale).prerotate(self._rotation)
            pix = page.get_pixmap(matrix=mat, alpha=False)

            img = QImage(
                pix.samples,
                pix.width,
                pix.height,
                pix.stride,
                QImage.Format.Format_RGB888,
            )
            return QPixmap.fromImage(img.copy())  # copy to detach from fitz memory
        finally:
            doc.close()


# ── LRU thumbnail cache ───────────────────────────────────────────────────────

class _ThumbnailCache:
    """Simple LRU cache keyed by (doc_id, page_index, rotation)."""

    def __init__(self, max_size: int) -> None:
        self._max = max_size
        self._store: OrderedDict[tuple, QPixmap] = OrderedDict()

    def get(self, key: tuple) -> Optional[QPixmap]:
        if key in self._store:
            self._store.move_to_end(key)
            return self._store[key]
        return None

    def put(self, key: tuple, pixmap: QPixmap) -> None:
        self._store[key] = pixmap
        self._store.move_to_end(key)
        if len(self._store) > self._max:
            self._store.popitem(last=False)

    def invalidate(self, doc_id: str) -> None:
        """Remove all entries for a given document."""
        keys = [k for k in self._store if k[0] == doc_id]
        for k in keys:
            del self._store[k]

    def clear(self) -> None:
        self._store.clear()


# ── PreviewService ────────────────────────────────────────────────────────────

class PreviewService(QObject):
    """Renders page thumbnails and full-resolution previews in the background.

    Signals:
        thumbnail_ready: Emitted on the main thread when a thumbnail has been
            rendered.  Payload: (doc_id: str, page_index: int, pixmap: QPixmap).
        render_error: Emitted if a render job fails.
            Payload: (doc_id: str, page_index: int, error: str).
    """

    thumbnail_ready = Signal(str, int, QPixmap)   # doc_id, page_index, pixmap
    render_error = Signal(str, int, str)           # doc_id, page_index, error

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool.globalInstance()
        self._pool.setMaxThreadCount(RENDER_THREAD_COUNT)
        self._cache = _ThumbnailCache(MAX_THUMBNAIL_CACHE)
        self._pending: set[tuple] = set()   # keys currently in flight

        log.debug(
            "PreviewService ready (threads=%d, cache=%d)",
            RENDER_THREAD_COUNT, MAX_THUMBNAIL_CACHE,
        )

    # ── Public API ────────────────────────────────────────────────────────

    def request_thumbnail(
        self,
        doc_id: str,
        path: Path,
        page_index: int,
        rotation: int = 0,
        password: str = "",
        width: int = THUMBNAIL_WIDTH,
        height: int = THUMBNAIL_HEIGHT,
    ) -> Optional[QPixmap]:
        """Return a cached thumbnail immediately, or queue a render job.

        If the thumbnail is already in cache it is returned synchronously.
        Otherwise None is returned and ``thumbnail_ready`` will fire when
        the render completes.

        Args:
            doc_id: Document identifier (used as cache key and signal payload).
            path: Path to the PDF file.
            page_index: 0-based page index.
            rotation: Cumulative user rotation (0, 90, 180, 270).
            password: Decryption password if needed.
            width: Maximum thumbnail width in pixels.
            height: Maximum thumbnail height in pixels.

        Returns:
            Cached QPixmap, or None if rendering is queued.
        """
        key = (doc_id, page_index, rotation, width, height)

        cached = self._cache.get(key)
        if cached is not None:
            return cached

        if key in self._pending:
            return None   # already queued

        self._pending.add(key)
        worker = _RenderWorker(
            doc_id=doc_id,
            path=path,
            page_index=page_index,
            rotation=rotation,
            dpi=THUMBNAIL_DPI,
            password=password,
            width=width,
            height=height,
        )
        worker.signals.finished.connect(self._on_render_finished)
        worker.signals.error.connect(self._on_render_error)
        self._pool.start(worker)
        return None

    def request_preview(
        self,
        doc_id: str,
        path: Path,
        page_index: int,
        rotation: int = 0,
        password: str = "",
        width: int = 800,
        height: int = 1100,
    ) -> Optional[QPixmap]:
        """Queue a higher-resolution render for the preview panel.

        Uses the same mechanism as request_thumbnail but with larger dims.
        """
        return self.request_thumbnail(
            doc_id=doc_id,
            path=path,
            page_index=page_index,
            rotation=rotation,
            password=password,
            width=width,
            height=height,
        )

    def invalidate_document(self, doc_id: str) -> None:
        """Remove all cached entries for a document (e.g. after rotation)."""
        self._cache.invalidate(doc_id)
        self._pending = {k for k in self._pending if k[0] != doc_id}

    def clear(self) -> None:
        """Wipe the entire cache."""
        self._cache.clear()
        self._pending.clear()

    # ── Internal slots ────────────────────────────────────────────────────

    @Slot(str, int, QPixmap, int, int, int)
    def _on_render_finished(
        self, doc_id: str, page_index: int, pixmap: QPixmap,
        rotation: int, width: int, height: int,
    ) -> None:
        key = (doc_id, page_index, rotation, width, height)
        self._pending.discard(key)
        self._cache.put(key, pixmap)
        self.thumbnail_ready.emit(doc_id, page_index, pixmap)
        log.debug("Thumbnail ready: doc=%s page=%d", doc_id[:8], page_index)

    @Slot(str, int, str)
    def _on_render_error(self, doc_id: str, page_index: int, error: str) -> None:
        for r in (0, 90, 180, 270):
            self._pending.discard((doc_id, page_index, r))
        self.render_error.emit(doc_id, page_index, error)
