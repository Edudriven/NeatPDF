"""
services/update_service.py — GitHub release update checker for NeatPDF.

Checks the latest GitHub release against the bundled APP_VERSION,
downloads the appropriate artifact for the current platform/install mode,
and emits signals for the UI to react to.
"""

from __future__ import annotations

import logging
import platform
import re
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import (
    QObject,
    QSettings,
    QThread,
    Signal,
)
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtCore import QUrl

from config import APP_NAME, APP_ORG, APP_VERSION, GITHUB_REPO

log = logging.getLogger(__name__)

# QSettings key used to remember a skipped version
_SKIPPED_VERSION_KEY = "updates/skipped_version"


def _parse_version(v: str) -> tuple[int, ...]:
    """Parse a version string like 'v1.2.3' or '1.2.3' into a comparable tuple."""
    v = v.lstrip("v").strip()
    parts = re.split(r"[.\-]", v)
    result = []
    for p in parts:
        try:
            result.append(int(p))
        except ValueError:
            break
    return tuple(result)


def _is_newer(remote: str, local: str) -> bool:
    return _parse_version(remote) > _parse_version(local)


def _expected_asset_name(tag: str, install_mode: str) -> str:
    """Return the expected artifact filename for this platform and install mode."""
    system = platform.system()
    if system == "Linux":
        return f"NeatPDF-{tag}-linux.AppImage"
    elif system == "Windows":
        if install_mode == "installer":
            return f"NeatPDF-{tag}-windows-installer.exe"
        else:
            return f"NeatPDF-{tag}-windows-portable.zip"
    return ""


class ReleaseInfo:
    """Holds metadata about a GitHub release."""

    def __init__(
        self,
        tag: str,
        name: str,
        body: str,
        download_url: str,
        asset_name: str,
    ) -> None:
        self.tag = tag
        self.name = name
        self.body = body          # release notes (markdown)
        self.download_url = download_url
        self.asset_name = asset_name

    def __repr__(self) -> str:
        return f"<ReleaseInfo tag={self.tag!r}>"


class UpdateChecker(QObject):
    """
    Async worker that fetches the latest GitHub release info.

    Signals
    -------
    update_available(ReleaseInfo)
        Emitted when a newer version exists and is not skipped.
    up_to_date()
        Emitted when the current version is the latest.
    check_failed(str)
        Emitted on network error or unexpected response.
    """

    update_available = Signal(object)   # ReleaseInfo
    up_to_date = Signal()
    check_failed = Signal(str)

    def __init__(self, install_mode: str = "portable", parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._install_mode = install_mode
        self._nam = QNetworkAccessManager(self)
        self._nam.finished.connect(self._on_reply)

    # ── Public API ────────────────────────────────────────────────────────

    def check(self) -> None:
        """Start an async check against the GitHub releases API."""
        url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b"Accept", b"application/vnd.github+json")
        request.setRawHeader(b"User-Agent", f"NeatPDF/{APP_VERSION}".encode())
        self._nam.get(request)
        log.debug("Checking for updates: %s", url)

    # ── Internals ─────────────────────────────────────────────────────────

    def _on_reply(self, reply: QNetworkReply) -> None:
        reply.deleteLater()

        if reply.error() != QNetworkReply.NetworkError.NoError:
            msg = reply.errorString()
            log.warning("Update check failed: %s", msg)
            self.check_failed.emit(msg)
            return

        import json
        try:
            data = json.loads(bytes(reply.readAll()))
        except Exception as exc:
            log.warning("Update check: failed to parse response: %s", exc)
            self.check_failed.emit(str(exc))
            return

        tag: str = data.get("tag_name", "")
        name: str = data.get("name", tag)
        body: str = data.get("body", "")
        assets: list = data.get("assets", [])

        if not tag:
            self.check_failed.emit("No tag_name in response")
            return

        if not _is_newer(tag, APP_VERSION):
            log.info("Already up to date (%s)", APP_VERSION)
            self.up_to_date.emit()
            return

        # Check if user previously skipped this version
        settings = QSettings(APP_ORG, APP_NAME)
        skipped = settings.value(_SKIPPED_VERSION_KEY, "")
        if skipped and not _is_newer(tag, str(skipped)):
            log.info("Update %s was skipped by user", tag)
            return

        # Find the matching asset for this platform
        asset_name = _expected_asset_name(tag, self._install_mode)
        download_url = ""
        for asset in assets:
            if asset.get("name") == asset_name:
                download_url = asset.get("browser_download_url", "")
                break

        if not download_url:
            # Fall back to the HTML release page if no matching asset
            download_url = data.get("html_url", "")
            log.warning("No matching asset '%s' found; using release page URL", asset_name)

        info = ReleaseInfo(
            tag=tag,
            name=name,
            body=body,
            download_url=download_url,
            asset_name=asset_name,
        )
        log.info("Update available: %s", tag)
        self.update_available.emit(info)


class UpdateDownloader(QObject):
    """
    Downloads an update artifact in the background.

    Signals
    -------
    progress(int)
        Download progress 0-100.
    finished(Path)
        Path to the downloaded file.
    failed(str)
        Error message.
    """

    progress = Signal(int)
    finished = Signal(object)   # Path
    failed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._nam = QNetworkAccessManager(self)

    def download(self, url: str, dest: Path) -> None:
        """Start downloading *url* to *dest*."""
        self._dest = dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b"User-Agent", f"NeatPDF/{APP_VERSION}".encode())
        reply = self._nam.get(request)
        reply.downloadProgress.connect(self._on_progress)
        reply.finished.connect(lambda: self._on_finished(reply))
        self._reply = reply

    def _on_progress(self, received: int, total: int) -> None:
        if total > 0:
            self.progress.emit(int(received * 100 / total))

    def _on_finished(self, reply: QNetworkReply) -> None:
        reply.deleteLater()
        if reply.error() != QNetworkReply.NetworkError.NoError:
            self.failed.emit(reply.errorString())
            return
        data = bytes(reply.readAll())
        try:
            self._dest.write_bytes(data)
            self.finished.emit(self._dest)
        except OSError as exc:
            self.failed.emit(str(exc))


def skip_version(tag: str) -> None:
    """Persist the user's choice to skip *tag*."""
    settings = QSettings(APP_ORG, APP_NAME)
    settings.setValue(_SKIPPED_VERSION_KEY, tag)
    log.info("User skipped version %s", tag)
