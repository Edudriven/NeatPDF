"""
gui/dialogs/watermark_multi_dialog.py — Multi-document watermark scan and
deferred removal dialog.

Full feature parity with WatermarkDialog (single-file) but for all docs:
  1. Quick scan (OCG/Artifact) across all documents first — parallel.
  2. Results shown grouped by document, with type filter chips, Select All,
     and per-finding Preview buttons.
  3. "Detect More Types…" banner triggers full scan across all docs.
  4. "Queue for Export" stores selections in ProjectService.watermark_removals
     — no immediate disk write; removals applied during export/merge.

Single-document sessions use the original WatermarkDialog instead.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QObject, QRunnable, QThreadPool, Signal, Slot
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from engines.watermark_engine import extract_watermark_text, render_page_without_watermark
from models.pdf_document import PDFDocument
from models.watermark_result import WatermarkResult, WatermarkType

log = logging.getLogger(__name__)

_TYPE_LABELS = {
    WatermarkType.TEXT: "Text",
    WatermarkType.IMAGE: "Image",
    WatermarkType.VECTOR: "Vector",
    WatermarkType.ARTIFACT: "Artifact",
    WatermarkType.UNKNOWN: "Unknown",
}

_TYPE_COLORS = {
    WatermarkType.TEXT: "#F38BA8",
    WatermarkType.IMAGE: "#FAB387",
    WatermarkType.VECTOR: "#89B4FA",
    WatermarkType.ARTIFACT: "#A6E3A1",
    WatermarkType.UNKNOWN: "#585B70",
}

_FILTER_ALL = "All"
_PREVIEW_W, _PREVIEW_H = 500, 700


# ── Shared workers (identical to single-file dialog) ─────────────────────────

class _PreviewSignals(QObject):
    ready = Signal(QPixmap)
    failed = Signal(str)


class _PreviewWorker(QRunnable):
    def __init__(self, wm: WatermarkResult, password: str,
                 signals: "_PreviewSignals",
                 width: int = _PREVIEW_W, height: int = _PREVIEW_H) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._wm, self._password = wm, password
        self._signals = signals
        self._width, self._height = width, height

    @Slot()
    def run(self) -> None:
        try:
            png = render_page_without_watermark(
                self._wm.source_path, self._wm, self._password,
                width=self._width, height=self._height,
            )
            if png:
                self._signals.ready.emit(QPixmap.fromImage(QImage.fromData(png)))
            else:
                self._signals.failed.emit("Render returned no data.")
        except Exception as exc:
            self._signals.failed.emit(str(exc))


class _TextSignals(QObject):
    ready = Signal(int, str)   # global_row_index, text


class _TextWorker(QRunnable):
    def __init__(self, row_idx: int, wm: WatermarkResult,
                 password: str, signals: "_TextSignals") -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._idx, self._wm, self._password, self._signals = row_idx, wm, password, signals

    @Slot()
    def run(self) -> None:
        try:
            text = extract_watermark_text(self._wm.source_path, self._wm, self._password)
            self._signals.ready.emit(self._idx, text)
        except Exception:
            self._signals.ready.emit(self._idx, "")


class _ScanSignals(QObject):
    finished = Signal(str, list, bool)  # doc_id, results, is_quick
    failed = Signal(str, str)           # doc_id, error


class _ScanWorker(QRunnable):
    def __init__(self, doc: PDFDocument, quick: bool, signals: "_ScanSignals") -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._doc, self._quick, self._signals = doc, quick, signals

    @Slot()
    def run(self) -> None:
        from engines.watermark_engine import detect_watermarks, detect_watermarks_quick
        try:
            results = (detect_watermarks_quick if self._quick else detect_watermarks)(
                self._doc.path, self._doc.password
            )
            self._signals.finished.emit(self._doc.doc_id, results, self._quick)
        except Exception as exc:
            self._signals.failed.emit(self._doc.doc_id, str(exc))


# ── Preview window (identical to single-file version) ────────────────────────

class WatermarkPreviewWindow(QWidget):
    _token: int = 0

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setWindowTitle("After Removal — Preview")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self._current_signals: Optional[_PreviewSignals] = None
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(6)
        root.setContentsMargins(12, 10, 12, 10)
        self._sub_label = QLabel("After Removal")
        self._sub_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sub_label.setStyleSheet("color:#A6E3A1;font-size:12px;font-weight:bold;")
        root.addWidget(self._sub_label)
        hint = QLabel("The page as it will appear after removing the watermark.")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("color:#A6ADC8;font-size:11px;")
        root.addWidget(hint)
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        root.addWidget(self._progress)
        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._image_label.setStyleSheet(
            "background:#1E1E2E;border:1px solid #313244;border-radius:4px;")
        self._image_label.setText("Click Preview on any finding.")
        root.addWidget(self._image_label, 1)
        self._error_label = QLabel()
        self._error_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._error_label.setStyleSheet("color:#F38BA8;")
        self._error_label.setVisible(False)
        root.addWidget(self._error_label)

    def load(self, wm: WatermarkResult, password: str) -> None:
        self._token += 1
        token = self._token
        type_label = _TYPE_LABELS.get(wm.watermark_type, "Watermark")
        self.setWindowTitle(f"After Removal — Page {wm.page_index + 1}  [{type_label}]")
        self._sub_label.setText(f"After Removal — Page {wm.page_index + 1}  ·  {type_label}")
        self._error_label.setVisible(False)
        self._image_label.clear()
        self._image_label.setText("Rendering…")
        self._progress.setVisible(True)
        screen = self.screen() or (self.parent().screen() if self.parent() else None)
        available = screen.availableGeometry() if screen else None
        if available:
            self.resize(available.width() // 3, int(available.height() * 0.80))
            self.move(available.right() - self.width(), available.top())
        signals = _PreviewSignals()
        signals.ready.connect(lambda pix, t=token: self._on_ready(pix, t))
        signals.failed.connect(lambda msg, t=token: self._on_failed(msg, t))
        self._current_signals = signals
        screen_h = available.height() if available else 900
        render_h = int(screen_h * 0.80) - 120
        render_w = int(render_h * 0.71)
        QThreadPool.globalInstance().start(
            _PreviewWorker(wm, password, signals, render_w, render_h))

    @Slot(QPixmap, int)
    def _on_ready(self, pixmap: QPixmap, token: int) -> None:
        if token != self._token:
            return
        self._progress.setVisible(False)
        self._image_label.setText("")
        self._image_label.setPixmap(pixmap)
        if not pixmap.isNull():
            screen = self.screen() or (self.parent().screen() if self.parent() else None)
            if screen:
                avail = screen.availableGeometry()
                win_h = int(avail.height() * 0.80)
                img_h = win_h - 120
                img_w = int(pixmap.width() * img_h / pixmap.height()) if pixmap.height() else 400
                self.resize(img_w + 24, win_h)
                self.move(avail.right() - self.width(), avail.top())

    @Slot(str, int)
    def _on_failed(self, message: str, token: int) -> None:
        if token != self._token:
            return
        self._progress.setVisible(False)
        self._image_label.setText("")
        self._error_label.setText(f"Preview failed: {message}")
        self._error_label.setVisible(True)


# ── Main dialog ───────────────────────────────────────────────────────────────

class WatermarkMultiDialog(QDialog):
    """Multi-document watermark scan with deferred export-time removal.

    Mirrors WatermarkDialog feature-for-feature:
      - Quick scan first, Detect More button, filter chips, Select All, Preview
    Differences:
      - Results grouped by document
      - No output-path row; "Queue for Export" replaces "Remove Selected"
      - Selections stored in existing_removals dict (ProjectService.watermark_removals)
    """

    def __init__(
        self,
        documents: list[PDFDocument],
        existing_removals: dict,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._documents = documents
        self._existing_removals = existing_removals   # mutated on queue

        # Per-doc state
        self._doc_map: dict[str, PDFDocument] = {d.doc_id: d for d in documents}
        # All results flat list (for filter/select-all indexing)
        self._all_results: list[WatermarkResult] = []
        # Parallel lists; indices match _all_results
        self._checkboxes: list[tuple[QCheckBox, WatermarkResult]] = []
        self._text_labels: list[Optional[QLabel]] = []
        self._result_frames: list[QWidget] = []

        # Quick/full scan tracking per doc
        self._quick_done: set[str] = set()
        self._full_done: set[str] = set()
        self._pending_quick: int = len(documents)
        self._pending_full: int = 0   # set when full scan is triggered

        self._active_filter: str = _FILTER_ALL
        self._text_signals: Optional[_TextSignals] = None
        self._preview_window: Optional[WatermarkPreviewWindow] = None

        self.setWindowTitle(f"Watermark Detection — {len(documents)} Documents")
        self.setMinimumSize(600, 500)
        self.setModal(True)

        self._build_ui()
        self._start_quick_scans()

    # ─────────────────────────────────────────────────────────────────────
    # UI
    # ─────────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(14, 14, 14, 14)

        self._status_label = QLabel(
            f"Quick-scanning {len(self._documents)} document(s)…"
        )
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._status_label)

        self._progress = QProgressBar()
        self._progress.setRange(0, len(self._documents))
        self._progress.setValue(0)
        root.addWidget(self._progress)

        # ── Filter chips + Select All ─────────────────────────────────────
        self._controls_row = QWidget()
        ctrl = QHBoxLayout(self._controls_row)
        ctrl.setContentsMargins(0, 0, 0, 0)
        ctrl.setSpacing(6)

        self._filter_group = QButtonGroup(self)
        self._filter_group.setExclusive(True)
        self._filter_btns: dict[str, QPushButton] = {}
        for label in [_FILTER_ALL, "Artifact", "Image", "Text", "Vector"]:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setChecked(label == _FILTER_ALL)
            btn.setFixedHeight(24)
            btn.setStyleSheet(self._chip_style(label == _FILTER_ALL))
            self._filter_btns[label] = btn
            self._filter_group.addButton(btn)
            ctrl.addWidget(btn)
            btn.clicked.connect(lambda _c, l=label: self._on_filter(l))

        ctrl.addStretch(1)
        self._select_all_btn = QPushButton("Select All")
        self._select_all_btn.setFixedWidth(100)
        self._select_all_btn.clicked.connect(self._on_select_all)
        ctrl.addWidget(self._select_all_btn)
        self._controls_row.setVisible(False)
        root.addWidget(self._controls_row)

        # ── Scroll area ───────────────────────────────────────────────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll_container = QWidget()
        self._scroll_layout = QVBoxLayout(self._scroll_container)
        self._scroll_layout.setContentsMargins(4, 4, 4, 4)
        self._scroll_layout.setSpacing(10)
        self._scroll_layout.addStretch(1)
        self._scroll.setWidget(self._scroll_container)
        self._scroll.setVisible(False)
        root.addWidget(self._scroll, 1)

        # ── "Detect More Types" banner ────────────────────────────────────
        self._more_row = QWidget()
        more_layout = QHBoxLayout(self._more_row)
        more_layout.setContentsMargins(0, 0, 0, 0)
        _ml = QLabel("Only PDF-tagged watermarks shown.")
        _ml.setStyleSheet("color:#A6ADC8;")
        self._detect_more_btn = QPushButton("Detect More Types…")
        self._detect_more_btn.clicked.connect(self._on_detect_more)
        more_layout.addWidget(_ml)
        more_layout.addStretch(1)
        more_layout.addWidget(self._detect_more_btn)
        self._more_row.setVisible(False)
        root.addWidget(self._more_row)

        # ── Deferred note ─────────────────────────────────────────────────
        self._info_label = QLabel(
            "Checked findings will be removed during export — not saved immediately."
        )
        self._info_label.setWordWrap(True)
        self._info_label.setStyleSheet("color:#A6ADC8;font-size:11px;")
        self._info_label.setVisible(False)
        root.addWidget(self._info_label)

        # ── Buttons ───────────────────────────────────────────────────────
        self._buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self._queue_btn = QPushButton("Queue for Export")
        self._queue_btn.setToolTip(
            "Store selections; removals will be applied when you export."
        )
        self._queue_btn.setEnabled(False)
        self._buttons.addButton(self._queue_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        self._buttons.rejected.connect(self.reject)
        self._queue_btn.clicked.connect(self._on_queue)
        root.addWidget(self._buttons)

    @staticmethod
    def _chip_style(active: bool) -> str:
        if active:
            return ("QPushButton{background:#313244;color:#CDD6F4;"
                    "border:1px solid #89B4FA;border-radius:4px;"
                    "padding:1px 8px;font-size:11px;}")
        return ("QPushButton{background:transparent;color:#A6ADC8;"
                "border:1px solid #45475A;border-radius:4px;"
                "padding:1px 8px;font-size:11px;}"
                "QPushButton:hover{border-color:#89B4FA;color:#CDD6F4;}")

    # ─────────────────────────────────────────────────────────────────────
    # Scan management
    # ─────────────────────────────────────────────────────────────────────

    def _start_quick_scans(self) -> None:
        self._scan_signals = _ScanSignals()
        self._scan_signals.finished.connect(self._on_scan_done)
        self._scan_signals.failed.connect(self._on_scan_failed)
        for doc in self._documents:
            QThreadPool.globalInstance().start(
                _ScanWorker(doc, quick=True, signals=self._scan_signals)
            )

    def _start_full_scans(self) -> None:
        self._pending_full = len(self._documents)
        for doc in self._documents:
            QThreadPool.globalInstance().start(
                _ScanWorker(doc, quick=False, signals=self._scan_signals)
            )

    @Slot(str, list, bool)
    def _on_scan_done(self, doc_id: str, results: list, is_quick: bool) -> None:
        doc = self._doc_map.get(doc_id)
        if doc is None:
            return

        if is_quick:
            self._quick_done.add(doc_id)
            self._pending_quick -= 1
            self._progress.setValue(self._progress.value() + 1)

            if results:
                self._add_results_for_doc(doc, results)

            if self._pending_quick == 0:
                self._on_all_quick_done()
        else:
            self._full_done.add(doc_id)
            self._pending_full -= 1
            self._progress.setValue(self._progress.value() + 1)

            if results:
                self._add_results_for_doc(doc, results)

            if self._pending_full == 0:
                self._on_all_full_done()

    @Slot(str, str)
    def _on_scan_failed(self, doc_id: str, message: str) -> None:
        doc = self._doc_map.get(doc_id)
        title = doc.title if doc else doc_id
        log.warning("Multi-watermark scan failed for %s: %s", title, message)
        # Decrement whichever counter is active
        if doc_id not in self._quick_done:
            self._quick_done.add(doc_id)
            self._pending_quick -= 1
        else:
            self._pending_full = max(0, self._pending_full - 1)
        self._progress.setValue(self._progress.value() + 1)
        self._add_error_group(title, message)

        if self._pending_quick == 0 and not self._full_done:
            self._on_all_quick_done()
        elif self._pending_full == 0 and self._full_done:
            self._on_all_full_done()

    def _on_all_quick_done(self) -> None:
        self._progress.setVisible(False)
        total = len(self._all_results)
        if total == 0:
            # Nothing found by quick scan — kick off full scan automatically
            self._status_label.setText(
                "Quick scan found nothing, running full scan…"
            )
            self._progress.setRange(0, len(self._documents))
            self._progress.setValue(0)
            self._progress.setVisible(True)
            self._start_full_scans()
        else:
            self._finalize_display(quick_only=True)

    def _on_all_full_done(self) -> None:
        self._progress.setVisible(False)
        self._more_row.setVisible(False)
        total = len(self._all_results)
        if total == 0:
            self._status_label.setText(
                "✓ No watermarks detected in any of the imported documents."
            )
            self._buttons.setStandardButtons(QDialogButtonBox.StandardButton.Close)
            self._buttons.rejected.connect(self.accept)
        else:
            self._finalize_display(quick_only=False)

    def _finalize_display(self, quick_only: bool) -> None:
        total = len(self._all_results)
        n_docs_with_results = len({wm.source_path for wm in self._all_results})
        if quick_only:
            self._status_label.setText(
                f"Found <b>{total}</b> PDF-tagged watermark(s) across "
                f"<b>{n_docs_with_results}</b> document(s).  "
                "Select findings to queue for removal:"
            )
        else:
            self._status_label.setText(
                f"Found <b>{total}</b> watermark(s) across "
                f"<b>{n_docs_with_results}</b> document(s).  "
                "Select findings to queue for removal:"
            )

        # Show/hide type filter chips based on present types
        types_present = {wm.watermark_type for wm in self._all_results}
        for label, btn in self._filter_btns.items():
            if label == _FILTER_ALL:
                btn.setVisible(True)
                continue
            wm_type = {
                "Artifact": WatermarkType.ARTIFACT,
                "Image": WatermarkType.IMAGE,
                "Text": WatermarkType.TEXT,
                "Vector": WatermarkType.VECTOR,
            }.get(label)
            btn.setVisible(wm_type in types_present)

        self._scroll.setVisible(True)
        self._controls_row.setVisible(True)
        self._queue_btn.setEnabled(True)
        self._info_label.setVisible(True)
        self._more_row.setVisible(quick_only and not self._full_done)
        self._sync_select_all_label()

    def _on_detect_more(self) -> None:
        self._detect_more_btn.setEnabled(False)
        self._more_row.setVisible(False)
        self._progress.setRange(0, len(self._documents))
        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._status_label.setText(
            "Running full scan for all watermark types across all documents…"
        )
        self._start_full_scans()

    # ─────────────────────────────────────────────────────────────────────
    # Result row building
    # ─────────────────────────────────────────────────────────────────────

    # Track one QGroupBox per doc (header group) in the scroll area
    _doc_header_groups: dict  # doc_id → (QGroupBox, QVBoxLayout)

    def __init_subclass__(cls, **kwargs):  # noqa: PYI034
        super().__init_subclass__(**kwargs)

    def _get_or_create_doc_group(self, doc: PDFDocument):
        """Return the (group, inner_layout) for doc, creating it if needed."""
        if not hasattr(self, "_doc_header_groups"):
            self._doc_header_groups = {}
        if doc.doc_id in self._doc_header_groups:
            return self._doc_header_groups[doc.doc_id]

        group = QGroupBox(doc.title)
        group.setStyleSheet(
            "QGroupBox{font-weight:bold;border:1px solid #45475A;"
            "border-radius:4px;margin-top:8px;padding-top:6px;}"
            "QGroupBox::title{subcontrol-origin:margin;left:8px;padding:0 4px;}"
        )
        inner = QVBoxLayout(group)
        inner.setSpacing(4)
        inner.setContentsMargins(8, 4, 8, 8)

        # Insert before the trailing stretch
        stretch_idx = self._scroll_layout.count() - 1
        self._scroll_layout.insertWidget(stretch_idx, group)
        self._doc_header_groups[doc.doc_id] = (group, inner)
        return group, inner

    def _add_results_for_doc(self, doc: PDFDocument, new_results: list[WatermarkResult]) -> None:
        """Merge new results into the flat list and add rows to the doc's group."""
        _group, inner = self._get_or_create_doc_group(doc)

        # Fresh text signals batch (keyed by global index)
        if self._text_signals is None:
            self._text_signals = _TextSignals()
            self._text_signals.ready.connect(self._on_wm_text_ready)

        existing_keys = {
            (wm.page_index, wm.watermark_type) for wm in self._all_results
            if wm.source_path == doc.path
        }

        for wm in new_results:
            key = (wm.page_index, wm.watermark_type)
            if key in existing_keys:
                continue
            existing_keys.add(key)

            global_idx = len(self._all_results)
            self._all_results.append(wm)

            frame = self._make_result_frame(global_idx, wm, doc.password)
            frame.setVisible(self._matches_filter(wm))
            inner.addWidget(frame)
            self._result_frames.append(frame)

            if wm.watermark_type in (WatermarkType.ARTIFACT, WatermarkType.TEXT):
                QThreadPool.globalInstance().start(
                    _TextWorker(global_idx, wm, doc.password, self._text_signals)
                )

    def _make_result_frame(
        self, global_idx: int, wm: WatermarkResult, password: str
    ) -> QWidget:
        frame = QGroupBox()
        frame.setFlat(True)
        outer = QVBoxLayout(frame)
        outer.setContentsMargins(6, 4, 6, 4)
        outer.setSpacing(2)

        main_row = QHBoxLayout()
        main_row.setSpacing(8)

        cb = QCheckBox()
        cb.setChecked(wm.removable)
        cb.stateChanged.connect(self._sync_select_all_label)
        main_row.addWidget(cb)
        self._checkboxes.append((cb, wm))
        self._text_labels.append(None)  # placeholder; overwritten below for text types

        color = _TYPE_COLORS.get(wm.watermark_type, "#585B70")
        type_lbl = QLabel(_TYPE_LABELS.get(wm.watermark_type, "?"))
        type_lbl.setStyleSheet(f"color:{color};font-weight:bold;min-width:54px;")
        main_row.addWidget(type_lbl)

        pg_lbl = QLabel(f"p.{wm.page_index + 1}")
        pg_lbl.setStyleSheet("color:#A6ADC8;min-width:36px;")
        main_row.addWidget(pg_lbl)

        desc_lbl = QLabel(wm.description)
        desc_lbl.setWordWrap(True)
        main_row.addWidget(desc_lbl, 1)

        conf_lbl = QLabel(wm.confidence_pct)
        conf_lbl.setStyleSheet("color:#A6ADC8;min-width:38px;")
        conf_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        main_row.addWidget(conf_lbl)

        preview_btn = QPushButton("Preview")
        preview_btn.setFixedWidth(66)
        preview_btn.setToolTip("Show page after watermark removal")
        preview_btn.clicked.connect(
            lambda _c, i=global_idx, pw=password: self._on_preview(i, pw)
        )
        main_row.addWidget(preview_btn)
        outer.addLayout(main_row)

        if wm.watermark_type in (WatermarkType.ARTIFACT, WatermarkType.TEXT):
            text_lbl = QLabel("Extracting watermark text…")
            text_lbl.setStyleSheet(
                f"color:{color};font-style:italic;font-size:11px;padding-left:76px;"
            )
            text_lbl.setWordWrap(True)
            outer.addWidget(text_lbl)
            # Replace placeholder at global_idx
            self._text_labels[global_idx] = text_lbl

        if not wm.removable:
            cb.setEnabled(False)
            cb.setToolTip("Automatic removal not supported for this finding")

        return frame

    def _add_error_group(self, title: str, message: str) -> None:
        stretch_idx = self._scroll_layout.count() - 1
        group = QGroupBox(title)
        vbox = QVBoxLayout(group)
        lbl = QLabel(f"Scan error: {message}")
        lbl.setStyleSheet("color:#F38BA8;font-style:italic;")
        lbl.setWordWrap(True)
        vbox.addWidget(lbl)
        self._scroll_layout.insertWidget(stretch_idx, group)
        self._scroll.setVisible(True)

    @Slot(int, str)
    def _on_wm_text_ready(self, idx: int, text: str) -> None:
        if idx < len(self._text_labels):
            lbl = self._text_labels[idx]
            if lbl is not None:
                if text.strip():
                    lbl.setText(f'"{text.strip()}"')
                else:
                    lbl.setVisible(False)

    # ─────────────────────────────────────────────────────────────────────
    # Preview
    # ─────────────────────────────────────────────────────────────────────

    def _on_preview(self, global_idx: int, password: str) -> None:
        if global_idx >= len(self._all_results):
            return
        wm = self._all_results[global_idx]
        if self._preview_window is None:
            self._preview_window = WatermarkPreviewWindow(self)
        self._preview_window.load(wm, password)
        self._preview_window.show()
        self._preview_window.raise_()

    # ─────────────────────────────────────────────────────────────────────
    # Filter chips
    # ─────────────────────────────────────────────────────────────────────

    def _matches_filter(self, wm: WatermarkResult) -> bool:
        if self._active_filter == _FILTER_ALL:
            return True
        return _TYPE_LABELS.get(wm.watermark_type, "") == self._active_filter

    def _on_filter(self, label: str) -> None:
        self._active_filter = label
        for lbl, btn in self._filter_btns.items():
            btn.setStyleSheet(self._chip_style(lbl == label))
        for frame, (_, wm) in zip(self._result_frames, self._checkboxes):
            frame.setVisible(self._matches_filter(wm))
        self._sync_select_all_label()

    # ─────────────────────────────────────────────────────────────────────
    # Select All
    # ─────────────────────────────────────────────────────────────────────

    def _visible_enabled_boxes(self) -> list[QCheckBox]:
        return [
            cb for frame, (cb, _) in zip(self._result_frames, self._checkboxes)
            if cb.isEnabled() and frame.isVisible()
        ]

    def _sync_select_all_label(self) -> None:
        boxes = self._visible_enabled_boxes()
        if not boxes:
            self._select_all_btn.setText("Select All")
            return
        self._select_all_btn.setText(
            "Deselect All" if all(cb.isChecked() for cb in boxes) else "Select All"
        )

    def _on_select_all(self) -> None:
        boxes = self._visible_enabled_boxes()
        all_checked = bool(boxes) and all(cb.isChecked() for cb in boxes)
        target = not all_checked
        for cb in boxes:
            cb.blockSignals(True)
            cb.setChecked(target)
            cb.blockSignals(False)
        self._select_all_btn.setText("Deselect All" if target else "Select All")

    # ─────────────────────────────────────────────────────────────────────
    # Queue for Export
    # ─────────────────────────────────────────────────────────────────────

    def _on_queue(self) -> None:
        # Group selected removals by doc_id
        by_doc: dict[str, list[WatermarkResult]] = {}
        for cb, wm in self._checkboxes:
            if not cb.isChecked():
                continue
            # Find which doc owns this result
            for doc in self._documents:
                if doc.path == wm.source_path:
                    by_doc.setdefault(doc.doc_id, []).append(wm)
                    break

        # Write back into the shared dict
        self._existing_removals.clear()
        self._existing_removals.update(by_doc)

        total = sum(len(v) for v in by_doc.values())
        log.info(
            "WatermarkMultiDialog: queued %d removal(s) across %d doc(s)",
            total, len(by_doc),
        )
        self.accept()
