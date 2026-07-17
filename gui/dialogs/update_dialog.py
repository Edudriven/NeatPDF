"""
gui/dialogs/update_dialog.py — "Update available" dialog for NeatPDF.

Shows release notes pulled from GitHub and lets the user update now,
defer until next launch, or skip the version entirely.
"""

from __future__ import annotations

import logging
import platform
import subprocess
import sys
import zipfile
from pathlib import Path
from tempfile import gettempdir

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtGui import QPixmap

from config import APP_VERSION, ICONS_DIR
from services.update_service import ReleaseInfo, UpdateDownloader, skip_version

log = logging.getLogger(__name__)


class UpdateDialog(QDialog):
    """
    Shown when a new GitHub release is available.

    Displays the release tag, changelog body (as plain text), and
    a progress bar while downloading.
    """

    def __init__(self, info: ReleaseInfo, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._info = info
        self._downloader: UpdateDownloader | None = None
        self._downloaded_path: Path | None = None

        self.setWindowTitle("Update Available")
        self.setMinimumWidth(520)
        self.setMinimumHeight(380)
        self.setModal(True)

        self._build_ui()

    # ── UI construction ───────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(12)
        root.setContentsMargins(20, 20, 20, 16)

        # Header row: icon + title
        header = QHBoxLayout()
        icon_label = QLabel()
        icon_path = ICONS_DIR / "neatpdf_64.png"
        if icon_path.exists():
            pix = QPixmap(str(icon_path)).scaled(
                48, 48,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            icon_label.setPixmap(pix)
        header.addWidget(icon_label)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title_label = QLabel(f"<b>NeatPDF {self._info.tag} is available</b>")
        title_label.setStyleSheet("font-size: 15px;")
        current_label = QLabel(f"You have version {APP_VERSION}")
        current_label.setStyleSheet("color: gray; font-size: 11px;")
        title_col.addWidget(title_label)
        title_col.addWidget(current_label)
        header.addLayout(title_col)
        header.addStretch()
        root.addLayout(header)

        # Changelog
        changelog_label = QLabel("What's new:")
        changelog_label.setStyleSheet("font-weight: bold;")
        root.addWidget(changelog_label)

        self._changelog = QTextBrowser()
        self._changelog.setOpenExternalLinks(True)
        self._changelog.setMarkdown(self._info.body or "_No release notes provided._")
        self._changelog.setMinimumHeight(160)
        root.addWidget(self._changelog)

        # Progress bar (hidden until download starts)
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(True)
        self._progress.hide()
        root.addWidget(self._progress)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: gray; font-size: 11px;")
        self._status_label.hide()
        root.addWidget(self._status_label)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self._skip_btn = QPushButton("Skip This Version")
        self._skip_btn.setFlat(True)
        self._skip_btn.clicked.connect(self._on_skip)
        btn_layout.addWidget(self._skip_btn)

        self._later_btn = QPushButton("Later")
        self._later_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self._later_btn)

        self._update_btn = QPushButton("Update Now")
        self._update_btn.setDefault(True)
        self._update_btn.clicked.connect(self._on_update_now)
        btn_layout.addWidget(self._update_btn)

        root.addLayout(btn_layout)

    # ── Button handlers ───────────────────────────────────────────────────

    def _on_skip(self) -> None:
        skip_version(self._info.tag)
        self.reject()

    def _on_update_now(self) -> None:
        if not self._info.download_url:
            # No direct asset — open release page in browser
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl
            QDesktopServices.openUrl(QUrl(self._info.download_url or
                f"https://github.com/Edudriven/NeatPDF/releases/latest"))
            self.accept()
            return

        self._start_download()

    def _start_download(self) -> None:
        dest = Path(gettempdir()) / self._info.asset_name
        self._downloaded_path = dest

        self._update_btn.setEnabled(False)
        self._skip_btn.setEnabled(False)
        self._later_btn.setEnabled(False)
        self._progress.show()
        self._status_label.show()
        self._status_label.setText(f"Downloading {self._info.asset_name}…")

        self._downloader = UpdateDownloader(self)
        self._downloader.progress.connect(self._on_progress)
        self._downloader.finished.connect(self._on_download_finished)
        self._downloader.failed.connect(self._on_download_failed)
        self._downloader.download(self._info.download_url, dest)

    def _on_progress(self, pct: int) -> None:
        self._progress.setValue(pct)

    def _on_download_finished(self, path: Path) -> None:
        self._progress.setValue(100)
        self._status_label.setText("Download complete. Restarting…")
        log.info("Update downloaded to %s", path)
        self._apply_update(path)

    def _on_download_failed(self, error: str) -> None:
        self._status_label.setText(f"Download failed: {error}")
        self._status_label.setStyleSheet("color: red; font-size: 11px;")
        self._update_btn.setEnabled(True)
        self._later_btn.setEnabled(True)
        self._skip_btn.setEnabled(True)
        log.error("Update download failed: %s", error)

    # ── Apply update ──────────────────────────────────────────────────────

    def _apply_update(self, path: Path) -> None:
        system = platform.system()
        suffix = path.suffix.lower()

        try:
            if system == "Linux" and suffix == ".appimage":
                self._apply_appimage(path)
            elif system == "Windows" and suffix == ".exe":
                self._apply_windows_installer(path)
            elif system == "Windows" and suffix == ".zip":
                self._apply_windows_portable(path)
            else:
                # Unknown — open containing folder
                self._open_in_explorer(path)
        except Exception as exc:
            log.error("Failed to apply update: %s", exc)
            self._on_download_failed(str(exc))

    def _apply_appimage(self, path: Path) -> None:
        """Replace the current AppImage and relaunch."""
        current = Path(sys.executable)
        path.chmod(0o755)
        path.replace(current)
        subprocess.Popen([str(current)] + sys.argv[1:])
        sys.exit(0)

    def _apply_windows_installer(self, path: Path) -> None:
        """Run the NSIS installer silently and exit."""
        subprocess.Popen([str(path), "/S"])
        sys.exit(0)

    def _apply_windows_portable(self, path: Path) -> None:
        """Extract zip over the current install dir and relaunch."""
        install_dir = Path(sys.executable).parent
        with zipfile.ZipFile(path, "r") as zf:
            zf.extractall(install_dir)
        subprocess.Popen([sys.executable] + sys.argv[1:])
        sys.exit(0)

    def _open_in_explorer(self, path: Path) -> None:
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
        self.accept()
