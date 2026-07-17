"""
gui/dialogs/export_dialog.py — Export / Save merged PDF dialog.

Lets the user choose an output path and shows a progress bar while the
merge runs.  The dialog is non-blocking: it launches the export via
ExportService and updates its own UI from the service's signals.

States:
  ┌──────────┐  user clicks Save  ┌──────────────┐
  │  READY   │ ─────────────────► │  IN_PROGRESS │
  └──────────┘                    └──────┬───────┘
                                         │ finished / failed
                                   ┌─────▼──────┐
                                   │   DONE /   │
                                   │   ERROR    │
                                   └────────────┘
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from services.export_service import ExportService

log = logging.getLogger(__name__)

_DIALOG_MIN_WIDTH = 480


class ExportDialog(QDialog):
    """Modal dialog for exporting the merged PDF.

    Args:
        export_service: Pre-constructed ExportService instance.
        default_path: Pre-filled output path suggestion.
        parent: Parent widget.
    """

    def __init__(
        self,
        export_service: ExportService,
        default_path: Path,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._svc = export_service
        self._output_path: Optional[Path] = None

        self.setWindowTitle("Export Merged PDF")
        self.setMinimumWidth(_DIALOG_MIN_WIDTH)
        self.setModal(True)

        self._build_ui(default_path)
        self._connect_signals()

    # ── UI construction ───────────────────────────────────────────────────

    def _build_ui(self, default_path: Path) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(12)
        root.setContentsMargins(16, 16, 16, 16)

        # ── Path row ──────────────────────────────────────────────────────
        path_label = QLabel("Output file:")
        root.addWidget(path_label)

        path_row = QHBoxLayout()
        path_row.setSpacing(8)

        self._path_edit = QLineEdit(str(default_path))
        self._path_edit.setPlaceholderText("Choose output path…")
        path_row.addWidget(self._path_edit, 1)

        self._browse_btn = QPushButton("Browse…")
        self._browse_btn.setFixedWidth(80)
        path_row.addWidget(self._browse_btn)

        root.addLayout(path_row)

        # ── Progress bar ──────────────────────────────────────────────────
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(True)
        self._progress.setVisible(False)
        root.addWidget(self._progress)

        # ── Status label ──────────────────────────────────────────────────
        self._status_label = QLabel("")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self._status_label.setVisible(False)
        root.addWidget(self._status_label)

        # ── Button box ────────────────────────────────────────────────────
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        self._save_btn = self._buttons.button(QDialogButtonBox.StandardButton.Save)
        self._save_btn.setDefault(True)

        root.addWidget(self._buttons)

    def _connect_signals(self) -> None:
        self._browse_btn.clicked.connect(self._on_browse)
        self._buttons.accepted.connect(self._on_save)
        self._buttons.rejected.connect(self.reject)

        self._svc.export_started.connect(self._on_export_started)
        self._svc.export_progress.connect(self._on_export_progress)
        self._svc.export_finished.connect(self._on_export_finished)
        self._svc.export_failed.connect(self._on_export_failed)

    # ── Slots ─────────────────────────────────────────────────────────────

    def _on_browse(self) -> None:
        current = self._path_edit.text().strip() or str(Path.home())
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Merged PDF",
            current,
            "PDF Files (*.pdf);;All Files (*)",
        )
        if path:
            if not path.lower().endswith(".pdf"):
                path += ".pdf"
            self._path_edit.setText(path)

    def _on_save(self) -> None:
        raw = self._path_edit.text().strip()
        if not raw:
            self._show_status("Please choose an output file.", error=True)
            return

        path = Path(raw)
        if not path.suffix:
            path = path.with_suffix(".pdf")

        # Confirm overwrite
        if path.exists():
            from PySide6.QtWidgets import QMessageBox
            reply = QMessageBox.question(
                self,
                "Overwrite?",
                f"'{path.name}' already exists.\nOverwrite it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self._output_path = path
        self._svc.export(path)

    def _on_export_started(self, total: int) -> None:
        self._save_btn.setEnabled(False)
        self._browse_btn.setEnabled(False)
        self._path_edit.setEnabled(False)
        self._progress.setRange(0, total)
        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._show_status(f"Merging {total} pages…")

    def _on_export_progress(self, current: int, total: int) -> None:
        self._progress.setValue(current)
        pct = int(current / total * 100) if total else 0
        self._show_status(f"Writing page {current} of {total}  ({pct}%)")

    def _on_export_finished(self, path: str) -> None:
        self._progress.setValue(self._progress.maximum())
        self._show_status(f"Saved: {Path(path).name} ✓", error=False)
        # Switch to a single Close button
        self._buttons.setStandardButtons(QDialogButtonBox.StandardButton.Close)
        self._buttons.rejected.disconnect()
        self._buttons.rejected.connect(self.accept)
        log.info("ExportDialog: finished → %s", path)

    def _on_export_failed(self, message: str) -> None:
        self._progress.setVisible(False)
        self._show_status(f"Error: {message}", error=True)
        self._save_btn.setEnabled(True)
        self._browse_btn.setEnabled(True)
        self._path_edit.setEnabled(True)
        log.error("ExportDialog: failed — %s", message)

    # ── Helpers ───────────────────────────────────────────────────────────

    def _show_status(self, text: str, error: bool = False) -> None:
        self._status_label.setText(text)
        color = "#F38BA8" if error else "#A6E3A1"
        self._status_label.setStyleSheet(f"color: {color}; font-size: 12px;")
        self._status_label.setVisible(True)

    # ── Result accessor ───────────────────────────────────────────────────

    @property
    def output_path(self) -> Optional[Path]:
        """The path the user exported to, or None if cancelled."""
        return self._output_path
