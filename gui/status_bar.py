"""
gui/status_bar.py — Application status bar.

Provides a thin wrapper around QStatusBar that exposes
convenience methods used throughout the application.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QProgressBar, QStatusBar

log = logging.getLogger(__name__)


class AppStatusBar(QStatusBar):
    """Custom status bar with a message area and an inline progress bar.

    Typical use::

        status_bar.show_message("Ready")
        status_bar.show_progress(50, "Merging…")
        status_bar.hide_progress()
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setSizeGripEnabled(False)

        self._message_label = QLabel("Ready")
        self._message_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self._progress_bar = QProgressBar()
        self._progress_bar.setFixedWidth(180)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setVisible(False)
        self._progress_bar.setTextVisible(False)

        self._progress_label = QLabel()
        self._progress_label.setVisible(False)

        self.addWidget(self._message_label, 1)
        self.addPermanentWidget(self._progress_label)
        self.addPermanentWidget(self._progress_bar)

    # ── Public API ────────────────────────────────────────────────────────

    def show_message(self, message: str, timeout_ms: int = 0) -> None:
        """Update the left-side status message.

        Args:
            message: Text to display.
            timeout_ms: If > 0, revert to "Ready" after this many milliseconds.
        """
        self._message_label.setText(message)
        log.debug("Status: %s", message)
        if timeout_ms > 0:
            self.showMessage(message, timeout_ms)

    def show_progress(self, value: int, label: str = "") -> None:
        """Show the progress bar and set its value.

        Args:
            value: Progress value in [0, 100].
            label: Optional label shown next to the bar.
        """
        self._progress_bar.setValue(value)
        self._progress_bar.setVisible(True)
        if label:
            self._progress_label.setText(label)
            self._progress_label.setVisible(True)

    def hide_progress(self) -> None:
        """Hide the progress bar and its label."""
        self._progress_bar.setVisible(False)
        self._progress_label.setVisible(False)

    def set_progress_range(self, minimum: int, maximum: int) -> None:
        """Set the progress bar range (use 0,0 for indeterminate)."""
        self._progress_bar.setRange(minimum, maximum)
