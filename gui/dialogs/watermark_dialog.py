"""
gui/dialogs/watermark_dialog.py — Watermark detection results and removal dialog.

Workflow:
  1. Quick scan (OCG / Artifact only) first — fast, definitive.
  2. Quick finds → show results + "Detect More Types…" button.
  3. Quick finds nothing → full scan automatically.
  4. Each result row shows:
       - Checkbox, type chip, description
       - For ARTIFACT/TEXT: extracted watermark text shown inline as italic label
       - "Preview" button → opens / updates WatermarkPreviewWindow
  5. WatermarkPreviewWindow (non-modal, single reusable instance):
       - Opens at full screen height, width sized to page aspect ratio
       - Renders the page IN-MEMORY with watermark removed (no file writes)
       - Clicking Preview on another row reuses the same window (updates in place)
       - Async render with loading spinner; cleans up on close
  6. Filter chips, Select All, output path, Remove Selected.
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
    QFileDialog,
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
from services.watermark_service import WatermarkService

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


# ── Preview render worker ─────────────────────────────────────────────────────

class _PreviewSignals(QObject):
    ready = Signal(QPixmap)
    failed = Signal(str)


class _PreviewWorker(QRunnable):
    """Renders the page with the watermark removed (pure in-memory)."""

    def __init__(
        self,
        wm: WatermarkResult,
        password: str,
        signals: _PreviewSignals,
        width: int = _PREVIEW_W,
        height: int = _PREVIEW_H,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._wm = wm
        self._password = password
        self._signals = signals
        self._width = width
        self._height = height

    @Slot()
    def run(self) -> None:
        try:
            png = render_page_without_watermark(
                self._wm.source_path,
                self._wm,
                self._password,
                width=self._width,
                height=self._height,
            )
            if png:
                img = QImage.fromData(png)
                self._signals.ready.emit(QPixmap.fromImage(img))
            else:
                self._signals.failed.emit("Render returned no data.")
        except Exception as exc:
            self._signals.failed.emit(str(exc))


# ── Watermark text fetch worker ───────────────────────────────────────────────

class _TextSignals(QObject):
    ready = Signal(int, str)   # row_index, text


class _TextWorker(QRunnable):
    """Extracts watermark text for a result row (used for ARTIFACT / TEXT types)."""

    def __init__(
        self, row_idx: int, wm: WatermarkResult, password: str, signals: _TextSignals
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._idx = row_idx
        self._wm = wm
        self._password = password
        self._signals = signals

    @Slot()
    def run(self) -> None:
        try:
            text = extract_watermark_text(self._wm.source_path, self._wm, self._password)
            self._signals.ready.emit(self._idx, text)
        except Exception:
            self._signals.ready.emit(self._idx, "")


# ── Preview window ────────────────────────────────────────────────────────────

# ── Preview window ────────────────────────────────────────────────────────────

class WatermarkPreviewWindow(QWidget):
    """Full-screen-height non-modal window showing the page after watermark removal.

    A single instance is reused across all Preview button clicks — calling
    ``load(wm, password)`` replaces the current content with a new render.

    Sizing: opens at the full available screen height; width is derived from
    the page aspect ratio so the image fills the height exactly.
    """

    # Token incremented on each load() call; workers check it to discard stale renders.
    _token: int = 0

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setWindowTitle("After Removal — Preview")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)  # reused, not deleted
        self._current_signals: Optional[_PreviewSignals] = None

        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(6)
        root.setContentsMargins(12, 10, 12, 10)

        # Sub-label (updated per load)
        self._sub_label = QLabel("After Removal")
        self._sub_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sub_label.setStyleSheet(
            "color: #A6E3A1; font-size: 12px; font-weight: bold;"
        )
        root.addWidget(self._sub_label)

        self._hint_label = QLabel(
            "The page as it will appear after removing the watermark."
        )
        self._hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint_label.setStyleSheet("color: #A6ADC8; font-size: 11px;")
        root.addWidget(self._hint_label)

        # Spinner
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        root.addWidget(self._progress)

        # Page image — fills available height
        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._image_label.setStyleSheet(
            "background: #1E1E2E; border: 1px solid #313244; border-radius: 4px;"
        )
        self._image_label.setText("Click Preview on any finding.")
        root.addWidget(self._image_label, 1)

        # Error label
        self._error_label = QLabel()
        self._error_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._error_label.setStyleSheet("color: #F38BA8;")
        self._error_label.setVisible(False)
        root.addWidget(self._error_label)

    def load(self, wm: WatermarkResult, password: str) -> None:
        """Replace the current preview with a render of *wm* after removal."""
        self._token += 1
        token = self._token

        type_label = _TYPE_LABELS.get(wm.watermark_type, "Watermark")
        self.setWindowTitle(
            f"After Removal — Page {wm.page_index + 1}  [{type_label}]"
        )
        self._sub_label.setText(
            f"After Removal — Page {wm.page_index + 1}  ·  {type_label}"
        )
        self._error_label.setVisible(False)
        self._image_label.clear()
        self._image_label.setText("Rendering…")
        self._progress.setVisible(True)

        # Size window to full screen height before we know the page dims.
        # We'll resize width once the render arrives.
        screen = self.screen()
        if screen is None and self.parent():
            screen = self.parent().screen()
        if screen:
            available = screen.availableGeometry()
            self.resize(available.width() // 3, int(available.height() * 0.80))
            self.move(available.right() - self.width(), available.top())

        signals = _PreviewSignals()
        # Capture token in closure
        signals.ready.connect(
            lambda pix, t=token: self._on_ready(pix, t)
        )
        signals.failed.connect(
            lambda msg, t=token: self._on_failed(msg, t)
        )
        self._current_signals = signals  # keep alive

        # Determine target render height from 90% of available screen height
        screen_h = available.height() if screen else 900
        render_h = int(screen_h * 0.80) - 120  # leave room for labels/padding
        render_w = int(render_h * 0.71)  # approximate A4 aspect; worker uses actual

        worker = _PreviewWorker(wm, password, signals, render_w, render_h)
        QThreadPool.globalInstance().start(worker)

    @Slot(QPixmap, int)
    def _on_ready(self, pixmap: QPixmap, token: int) -> None:
        if token != self._token:
            return  # stale render
        self._progress.setVisible(False)
        self._image_label.setText("")
        self._image_label.setPixmap(pixmap)

        # Resize window width to match image aspect ratio at screen height
        if not pixmap.isNull():
            screen = self.screen()
            if screen is None and self.parent():
                screen = self.parent().screen()
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

class WatermarkDialog(QDialog):
    """Full watermark detection + removal workflow dialog."""

    def __init__(
        self,
        doc: PDFDocument,
        watermark_service: WatermarkService,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._doc = doc
        self._svc = watermark_service
        self._results: list[WatermarkResult] = []
        self._checkboxes: list[tuple[QCheckBox, WatermarkResult]] = []
        # Per-row text labels (for ARTIFACT / TEXT inline text display)
        self._wm_text_labels: list[Optional[QLabel]] = []
        self._active_filter: str = _FILTER_ALL
        self._quick_done = False
        self._full_done = False
        # Shared text-fetch signals (re-created on each repopulate)
        self._text_signals: Optional[_TextSignals] = None
        # Single reusable preview window (created lazily)
        self._preview_window: Optional[WatermarkPreviewWindow] = None

        self.setWindowTitle(f"Watermark Detection — {doc.title}")
        self.setMinimumSize(540, 420)
        self.setModal(True)

        self._build_ui()
        self._connect_signals()
        self._start_quick_detection()

    # ── UI construction ───────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(14, 14, 14, 14)

        self._status_label = QLabel("Scanning for watermarks…")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._status_label)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        root.addWidget(self._progress)

        # ── Filter + select-all row ───────────────────────────────────────
        self._controls_row = QWidget()
        ctrl_layout = QHBoxLayout(self._controls_row)
        ctrl_layout.setContentsMargins(0, 0, 0, 0)
        ctrl_layout.setSpacing(6)

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
            ctrl_layout.addWidget(btn)
            btn.clicked.connect(lambda _c, l=label: self._on_filter(l))

        ctrl_layout.addStretch(1)
        self._select_all_btn = QPushButton("Select All")
        self._select_all_btn.setFixedWidth(100)
        self._select_all_btn.clicked.connect(self._on_select_all)
        ctrl_layout.addWidget(self._select_all_btn)
        self._controls_row.setVisible(False)
        root.addWidget(self._controls_row)

        # ── Scroll area ───────────────────────────────────────────────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setVisible(False)
        root.addWidget(self._scroll, 1)

        # ── "Detect More Types" banner ────────────────────────────────────
        self._more_row = QWidget()
        more_layout = QHBoxLayout(self._more_row)
        more_layout.setContentsMargins(0, 0, 0, 0)
        _ml = QLabel("Only PDF-tagged watermarks shown.")
        _ml.setStyleSheet("color: #A6ADC8;")
        self._detect_more_btn = QPushButton("Detect More Types…")
        self._detect_more_btn.clicked.connect(self._on_detect_more)
        more_layout.addWidget(_ml)
        more_layout.addStretch(1)
        more_layout.addWidget(self._detect_more_btn)
        self._more_row.setVisible(False)
        root.addWidget(self._more_row)

        # ── Output path row ───────────────────────────────────────────────
        self._output_row = QWidget()
        out_layout = QHBoxLayout(self._output_row)
        out_layout.setContentsMargins(0, 0, 0, 0)
        out_layout.addWidget(QLabel("Save cleaned PDF to:"))
        self._output_edit = QLabel("")
        self._output_edit.setStyleSheet("color: #89B4FA;")
        self._output_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self._browse_btn = QPushButton("Browse…")
        self._browse_btn.setFixedWidth(76)
        self._browse_btn.clicked.connect(self._on_browse)
        out_layout.addWidget(self._output_edit, 1)
        out_layout.addWidget(self._browse_btn)
        self._output_row.setVisible(False)
        root.addWidget(self._output_row)

        self._output_path = self._doc.path.parent / (
            self._doc.path.stem + "_cleaned.pdf"
        )
        self._output_edit.setText(self._output_path.name)

        # ── Button box ────────────────────────────────────────────────────
        self._buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self._remove_btn = QPushButton("Remove Selected")
        self._remove_btn.setEnabled(False)
        self._buttons.addButton(
            self._remove_btn, QDialogButtonBox.ButtonRole.AcceptRole
        )
        self._buttons.rejected.connect(self.reject)
        self._remove_btn.clicked.connect(self._on_remove)
        root.addWidget(self._buttons)

    @staticmethod
    def _chip_style(active: bool) -> str:
        if active:
            return (
                "QPushButton{background:#313244;color:#CDD6F4;"
                "border:1px solid #89B4FA;border-radius:4px;"
                "padding:1px 8px;font-size:11px;}"
            )
        return (
            "QPushButton{background:transparent;color:#A6ADC8;"
            "border:1px solid #45475A;border-radius:4px;"
            "padding:1px 8px;font-size:11px;}"
            "QPushButton:hover{border-color:#89B4FA;color:#CDD6F4;}"
        )

    def _connect_signals(self) -> None:
        self._svc.detection_finished.connect(self._on_detection_done)
        self._svc.detection_failed.connect(self._on_detection_failed)
        self._svc.removal_finished.connect(self._on_removal_done)
        self._svc.removal_failed.connect(self._on_removal_failed)

    # ── Detection flow ────────────────────────────────────────────────────

    def _start_quick_detection(self) -> None:
        self._svc.detect(self._doc, quick=True)

    def _on_detection_done(self, doc_id: str, results: list) -> None:
        if doc_id != self._doc.doc_id:
            return
        self._progress.setVisible(False)

        if not self._quick_done:
            self._quick_done = True
            if results:
                self._merge_results(results)
                self._show_results(quick_only=True)
            else:
                self._status_label.setText(
                    "Quick scan found nothing, running full scan…"
                )
                self._progress.setVisible(True)
                self._svc.detect(self._doc, quick=False)
        else:
            self._full_done = True
            self._more_row.setVisible(False)
            self._detect_more_btn.setEnabled(True)
            if results:
                self._merge_results(results)
                self._show_results(quick_only=False)
            elif not self._results:
                self._status_label.setText(
                    f"✓ No watermarks detected in <b>{self._doc.title}</b>."
                )
                self._buttons.setStandardButtons(QDialogButtonBox.StandardButton.Close)
                self._buttons.rejected.connect(self.accept)
            else:
                self._show_results(quick_only=False)

    def _on_detection_failed(self, doc_id: str, message: str) -> None:
        if doc_id != self._doc.doc_id:
            return
        self._progress.setVisible(False)
        self._status_label.setText(f"Detection error: {message}")
        self._status_label.setStyleSheet("color: #F38BA8;")
        self._detect_more_btn.setEnabled(True)

    def _on_detect_more(self) -> None:
        self._detect_more_btn.setEnabled(False)
        self._progress.setRange(0, 0)
        self._progress.setVisible(True)
        self._status_label.setText("Running full scan for all watermark types…")
        self._svc.detect(self._doc, quick=False)

    def _merge_results(self, new_results: list[WatermarkResult]) -> None:
        existing = {(r.page_index, r.watermark_type) for r in self._results}
        for r in new_results:
            key = (r.page_index, r.watermark_type)
            if key not in existing:
                self._results.append(r)
                existing.add(key)

    def _show_results(self, quick_only: bool) -> None:
        count = len(self._results)
        if not count:
            return

        types_present = {r.watermark_type for r in self._results}
        if quick_only:
            self._status_label.setText(
                f"Found <b>{count}</b> PDF-tagged watermark(s) in "
                f"<b>{self._doc.title}</b>. Select findings to remove:"
            )
        else:
            self._status_label.setText(
                f"Found <b>{count}</b> watermark(s) in "
                f"<b>{self._doc.title}</b>. Select findings to remove:"
            )

        self._checkboxes.clear()
        self._wm_text_labels.clear()
        self._repopulate_scroll()

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
        self._output_row.setVisible(True)
        self._remove_btn.setEnabled(True)
        self._more_row.setVisible(quick_only and not self._full_done)
        self._sync_select_all_label()

    # ── List population ───────────────────────────────────────────────────

    def _repopulate_scroll(self) -> None:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        # Shared text signals for this batch
        self._text_signals = _TextSignals()
        self._text_signals.ready.connect(self._on_wm_text_ready)

        for idx, wm in enumerate(self._results):
            widget = self._make_result_widget(idx, wm)
            widget.setVisible(self._matches_filter(wm))
            layout.addWidget(widget)

            # Queue text extraction for ARTIFACT and TEXT types
            if wm.watermark_type in (WatermarkType.ARTIFACT, WatermarkType.TEXT):
                worker = _TextWorker(
                    idx, wm, self._doc.password, self._text_signals
                )
                QThreadPool.globalInstance().start(worker)
            else:
                self._wm_text_labels.append(None)

        layout.addStretch(1)
        self._scroll.setWidget(container)

    def _make_result_widget(self, idx: int, wm: WatermarkResult) -> QWidget:
        frame = QGroupBox()
        frame.setFlat(True)
        outer = QVBoxLayout(frame)
        outer.setContentsMargins(6, 4, 6, 4)
        outer.setSpacing(2)

        # ── Main row ──────────────────────────────────────────────────────
        main_row = QHBoxLayout()
        main_row.setSpacing(8)

        cb = QCheckBox()
        cb.setChecked(wm.removable)
        cb.stateChanged.connect(self._sync_select_all_label)
        main_row.addWidget(cb)
        self._checkboxes.append((cb, wm))

        color = _TYPE_COLORS.get(wm.watermark_type, "#585B70")
        type_lbl = QLabel(_TYPE_LABELS.get(wm.watermark_type, "?"))
        type_lbl.setStyleSheet(
            f"color:{color};font-weight:bold;min-width:54px;"
        )
        main_row.addWidget(type_lbl)

        desc_lbl = QLabel(wm.description)
        desc_lbl.setWordWrap(True)
        main_row.addWidget(desc_lbl, 1)

        conf_lbl = QLabel(wm.confidence_pct)
        conf_lbl.setStyleSheet("color:#A6ADC8;min-width:38px;")
        conf_lbl.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        main_row.addWidget(conf_lbl)

        preview_btn = QPushButton("Preview")
        preview_btn.setFixedWidth(66)
        preview_btn.setToolTip("Show page after watermark removal")
        preview_btn.clicked.connect(lambda _c, i=idx: self._on_preview(i))
        main_row.addWidget(preview_btn)

        outer.addLayout(main_row)

        # ── Watermark text sub-row (placeholder, filled async) ────────────
        if wm.watermark_type in (WatermarkType.ARTIFACT, WatermarkType.TEXT):
            text_lbl = QLabel("Extracting watermark text…")
            text_lbl.setStyleSheet(
                f"color:{color};font-style:italic;font-size:11px;"
                "padding-left:76px;"
            )
            text_lbl.setWordWrap(True)
            outer.addWidget(text_lbl)
            self._wm_text_labels.append(text_lbl)
        else:
            self._wm_text_labels.append(None)

        if not wm.removable:
            cb.setEnabled(False)
            cb.setToolTip("Automatic removal not supported for this finding")

        return frame

    @Slot(int, str)
    def _on_wm_text_ready(self, idx: int, text: str) -> None:
        if idx < len(self._wm_text_labels):
            lbl = self._wm_text_labels[idx]
            if lbl is not None:
                if text.strip():
                    lbl.setText(f'"{text.strip()}"')
                else:
                    lbl.setVisible(False)

    # ── Preview button ────────────────────────────────────────────────────

    def _on_preview(self, idx: int) -> None:
        if idx >= len(self._results):
            return
        wm = self._results[idx]
        # Create the window once; reuse it for every subsequent click
        if self._preview_window is None:
            self._preview_window = WatermarkPreviewWindow(self)
        self._preview_window.load(wm, self._doc.password)
        self._preview_window.show()
        self._preview_window.raise_()

    # ── Filter chips ──────────────────────────────────────────────────────

    def _matches_filter(self, wm: WatermarkResult) -> bool:
        if self._active_filter == _FILTER_ALL:
            return True
        return _TYPE_LABELS.get(wm.watermark_type, "") == self._active_filter

    def _on_filter(self, label: str) -> None:
        self._active_filter = label
        for lbl, btn in self._filter_btns.items():
            btn.setStyleSheet(self._chip_style(lbl == label))
        self._apply_filter_to_scroll()
        self._sync_select_all_label()

    def _apply_filter_to_scroll(self) -> None:
        container = self._scroll.widget()
        if container is None:
            return
        groups = container.findChildren(QGroupBox)
        for group, (_, wm) in zip(groups, self._checkboxes):
            group.setVisible(self._matches_filter(wm))

    # ── Select All toggle ─────────────────────────────────────────────────

    def _visible_enabled_boxes(self) -> list[QCheckBox]:
        container = self._scroll.widget()
        groups = container.findChildren(QGroupBox) if container else []
        result = []
        for group, (cb, _) in zip(groups, self._checkboxes):
            if cb.isEnabled() and group.isVisible():
                result.append(cb)
        return result

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

    # ── Output path ───────────────────────────────────────────────────────

    def _on_browse(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Cleaned PDF", str(self._output_path), "PDF Files (*.pdf)",
        )
        if path:
            if not path.lower().endswith(".pdf"):
                path += ".pdf"
            self._output_path = Path(path)
            self._output_edit.setText(self._output_path.name)

    # ── Removal ───────────────────────────────────────────────────────────

    def _on_remove(self) -> None:
        selected = [wm for cb, wm in self._checkboxes if cb.isChecked()]
        if not selected:
            return
        self._remove_btn.setEnabled(False)
        self._browse_btn.setEnabled(False)
        self._detect_more_btn.setEnabled(False)
        self._progress.setRange(0, 0)
        self._progress.setVisible(True)
        self._status_label.setText(
            f"Removing {len(selected)} watermark(s)… please wait."
        )
        self._svc.remove(
            source_path=self._doc.path,
            results=selected,
            output_path=self._output_path,
            password=self._doc.password,
        )

    def _on_removal_done(self, output_path: str, pages_cleaned: int) -> None:
        self._progress.setVisible(False)
        self._status_label.setText(
            f"✓ Done. Cleaned {pages_cleaned} page(s). "
            f"Saved to: <b>{Path(output_path).name}</b>"
        )
        self._status_label.setStyleSheet("color: #A6E3A1;")
        self._buttons.setStandardButtons(QDialogButtonBox.StandardButton.Close)

    def _on_removal_failed(self, message: str) -> None:
        self._progress.setVisible(False)
        self._status_label.setText(f"Removal failed: {message}")
        self._status_label.setStyleSheet("color: #F38BA8;")
        self._remove_btn.setEnabled(True)
        self._browse_btn.setEnabled(True)
        self._detect_more_btn.setEnabled(not self._full_done)
