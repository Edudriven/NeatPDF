"""
gui/dialogs/about_dialog.py — About NeatPDF dialog.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from config import APP_VERSION, ICONS_DIR, GITHUB_REPO


class AboutDialog(QDialog):
    """Simple About dialog showing logo, version, and links."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("About NeatPDF")
        self.setFixedSize(420, 280)
        self.setModal(True)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(24, 24, 24, 20)
        root.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Logo (text logo if available, else icon)
        logo_label = QLabel()
        logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_path = ICONS_DIR / "neatpdf_logo.png"
        icon_path = ICONS_DIR / "neatpdf_128.png"
        if logo_path.exists():
            pix = QPixmap(str(logo_path)).scaledToWidth(
                280, Qt.TransformationMode.SmoothTransformation
            )
            logo_label.setPixmap(pix)
        elif icon_path.exists():
            pix = QPixmap(str(icon_path)).scaled(
                80, 80,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            logo_label.setPixmap(pix)
        root.addWidget(logo_label)

        # Version
        ver_label = QLabel(f"Version {APP_VERSION}")
        ver_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ver_label.setStyleSheet("color: gray; font-size: 12px;")
        root.addWidget(ver_label)

        # Description
        desc = QLabel("A professional open-source desktop PDF toolkit.")
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc.setWordWrap(True)
        root.addWidget(desc)

        # Links row
        links = QHBoxLayout()
        links.setAlignment(Qt.AlignmentFlag.AlignCenter)
        links.setSpacing(16)

        gh_btn = QPushButton("GitHub")
        gh_btn.setFlat(True)
        gh_btn.setStyleSheet("color: #89B4FA; text-decoration: underline;")
        gh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        gh_btn.clicked.connect(lambda: self._open_url(
            f"https://github.com/{GITHUB_REPO}"
        ))
        links.addWidget(gh_btn)

        releases_btn = QPushButton("Releases")
        releases_btn.setFlat(True)
        releases_btn.setStyleSheet("color: #89B4FA; text-decoration: underline;")
        releases_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        releases_btn.clicked.connect(lambda: self._open_url(
            f"https://github.com/{GITHUB_REPO}/releases"
        ))
        links.addWidget(releases_btn)

        root.addLayout(links)

        # MIT license note
        mit_label = QLabel("Released under the MIT License")
        mit_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mit_label.setStyleSheet("color: gray; font-size: 10px;")
        root.addWidget(mit_label)

        root.addStretch()

        # Close button
        close_btn = QPushButton("Close")
        close_btn.setFixedWidth(90)
        close_btn.clicked.connect(self.accept)
        close_row = QHBoxLayout()
        close_row.addStretch()
        close_row.addWidget(close_btn)
        root.addLayout(close_row)

    @staticmethod
    def _open_url(url: str) -> None:
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        QDesktopServices.openUrl(QUrl(url))
