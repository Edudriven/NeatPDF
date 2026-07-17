"""
gui/main_window.py — Application main window.

New layout:
┌─────────────────────────────────────────────────────────────┐
│                       Toolbar                               │
├──────────────┬──────────────────────────┬───────────────────┤
│              │   PagePanel              │                   │
│  FilePanel   ├──────────────────────────┤  PreviewPanel     │
│ (full height)│   TOCPanel               │  (or TOCPanel     │
│              │                          │   when hidden)    │
├──────────────┴──────────────────────────┴───────────────────┤
│                       StatusBar                             │
└─────────────────────────────────────────────────────────────┘
When preview is hidden, TOCPanel moves to the right slot.
When preview is shown, TOCPanel returns to center bottom.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QSettings, QSize, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QWidget,
)

from app import apply_theme
from config import (
    APP_NAME,
    APP_ORG,
    APP_VERSION,
    INSTALL_MODE,
    WINDOW_DEFAULT_HEIGHT,
    WINDOW_DEFAULT_WIDTH,
    WINDOW_MIN_HEIGHT,
    WINDOW_MIN_WIDTH,
)
from gui.menu_bar import AppMenuBar
from gui.panels.file_panel import FilePanel
from gui.panels.page_panel import PagePanel
from gui.panels.preview_panel import PreviewPanel
from gui.panels.toc_panel import TOCPanel
from gui.status_bar import AppStatusBar
from gui.toolbar import AppToolBar
from models.page_item import PageItem
from services.export_service import ExportService
from services.preview_service import PreviewService
from services.project_service import ProjectService
from services.toc_service import TOCService
from services.update_service import UpdateChecker
from services.watermark_service import WatermarkService

log = logging.getLogger(__name__)

# Default splitter proportions
_H_SIZES = [220, 580, 360]      # file | center | right (preview)
_CENTER_V_SIZES = [560, 220]    # pages | toc (center column)
_TOC_RIGHT_WIDTH = 340          # right-slot width when TOC moves there


class MainWindow(QMainWindow):
    """Top-level application window."""

    def __init__(self, theme: str = "dark", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._settings = QSettings(APP_ORG, APP_NAME)

        self._project = ProjectService()
        self._preview_svc = PreviewService(self)
        self._toc_svc = TOCService(self)
        self._project.toc_service = self._toc_svc
        self._export_svc = ExportService(self._project, self, toc_service=self._toc_svc)
        self._watermark_svc = WatermarkService(self)

        # Track latest selected page index (0-based) for TOC integration
        self._last_selected_page: int = -1

        self._build_window()
        self._toolbar.update_theme(self._theme)   # set correct logo for saved theme
        self._restore_geometry()
        self._schedule_update_check()
        log.info("MainWindow initialised (theme=%s)", theme)

    # ── Window construction ───────────────────────────────────────────────

    def _build_window(self) -> None:
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        self.setMinimumSize(QSize(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT))
        self.setAcceptDrops(True)

        self._toolbar = AppToolBar(self)
        self.addToolBar(self._toolbar)

        self._menu_bar = AppMenuBar(self._toolbar, self)
        self.setMenuBar(self._menu_bar)

        self._status_bar = AppStatusBar(self)
        self.setStatusBar(self._status_bar)

        self._file_panel = FilePanel()
        self._file_panel.setMinimumWidth(120)
        self._page_panel = PagePanel()
        self._page_panel.setMinimumWidth(120)
        self._preview_panel = PreviewPanel()
        self._preview_panel.setMinimumWidth(120)
        self._toc_panel = TOCPanel(toc_service=self._toc_svc)
        self._toc_panel.setMinimumWidth(120)

        # Center column: pages (top) + toc (bottom)
        self._center_splitter = QSplitter(Qt.Orientation.Vertical)
        self._center_splitter.addWidget(self._page_panel)
        self._center_splitter.addWidget(self._toc_panel)
        self._center_splitter.setSizes(_CENTER_V_SIZES)
        self._center_splitter.setChildrenCollapsible(False)
        # Pages panel expands vertically; TOC gets remaining space
        self._center_splitter.setStretchFactor(0, 3)
        self._center_splitter.setStretchFactor(1, 1)

        # Main horizontal splitter: file | center | right(preview or toc)
        self._h_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._h_splitter.addWidget(self._file_panel)
        self._h_splitter.addWidget(self._center_splitter)
        self._h_splitter.addWidget(self._preview_panel)
        self._h_splitter.setSizes(_H_SIZES)
        # Center column absorbs all extra horizontal space on resize
        self._h_splitter.setStretchFactor(0, 0)   # file panel: fixed
        self._h_splitter.setStretchFactor(1, 1)   # center: grows
        self._h_splitter.setStretchFactor(2, 0)   # preview: fixed
        self._h_splitter.setChildrenCollapsible(False)

        self.setCentralWidget(self._h_splitter)
        self._connect_signals()

    def _connect_signals(self) -> None:
        tb = self._toolbar
        mb = self._menu_bar

        # ── File ──────────────────────────────────────────────────────────
        tb.action_open.triggered.connect(self._on_open_files)
        self._file_panel.btn_add.clicked.connect(self._on_open_files)
        mb.action_close_all.triggered.connect(self._on_close_all)
        tb.action_save.triggered.connect(self._on_save)
        tb.action_save_as.triggered.connect(self._on_save_as)
        mb.action_quit.triggered.connect(self.close)

        # ── Edit ──────────────────────────────────────────────────────────
        tb.action_undo.triggered.connect(self._on_undo)
        tb.action_redo.triggered.connect(self._on_redo)
        mb.action_select_all.triggered.connect(self._page_panel.select_all)
        mb.action_deselect_all.triggered.connect(self._page_panel.deselect_all)

        # ── Page ops — toolbar & menu ──────────────────────────────────────
        tb.action_rotate_cw.triggered.connect(self._on_rotate_cw)
        tb.action_rotate_ccw.triggered.connect(self._on_rotate_ccw)
        tb.action_delete_pages.triggered.connect(self._on_delete_pages)
        tb.action_copy_pages.triggered.connect(self._on_copy_pages)
        tb.action_extract_pages.triggered.connect(self._on_extract_pages)
        mb.action_insert_blank.triggered.connect(self._on_insert_blank)
        mb.action_extract_pages.triggered.connect(self._on_extract_pages)

        # ── Page ops — context menu signals from PagePanel ─────────────────
        self._page_panel.rotate_cw_requested.connect(self._on_rotate_cw_indices)
        self._page_panel.rotate_ccw_requested.connect(self._on_rotate_ccw_indices)
        self._page_panel.delete_requested.connect(self._on_delete_indices)
        self._page_panel.copy_requested.connect(self._on_copy_indices)
        self._page_panel.insert_blank_requested.connect(self._on_insert_blank_after)
        self._page_panel.extract_requested.connect(self._on_extract_indices)
        self._page_panel.pages_move_requested.connect(self._on_pages_move)

        # ── View ──────────────────────────────────────────────────────────
        tb.action_zoom_in.triggered.connect(self._preview_panel.zoom_in)
        tb.action_zoom_out.triggered.connect(self._preview_panel.zoom_out)
        tb.action_zoom_fit.triggered.connect(self._preview_panel.zoom_fit)
        tb.action_zoom_fit_window.triggered.connect(self._preview_panel.zoom_fit_window)
        tb.action_toggle_theme.triggered.connect(self._on_toggle_theme)
        mb.action_show_file_panel.toggled.connect(self._file_panel.setVisible)
        mb.action_show_page_panel.toggled.connect(self._page_panel.setVisible)
        mb.action_show_toc_panel.toggled.connect(self._toc_panel.setVisible)
        mb.action_show_preview_panel.toggled.connect(self._on_toggle_preview)

        # ── Tools ─────────────────────────────────────────────────────────
        mb.action_detect_watermark.triggered.connect(self._on_detect_watermark)
        mb.action_detect_toc.triggered.connect(self._on_detect_toc)
        mb.action_preferences.triggered.connect(self._on_preferences)

        # ── Help ──────────────────────────────────────────────────────────
        mb.action_about.triggered.connect(self._on_about)
        mb.action_shortcuts.triggered.connect(self._on_shortcuts)
        mb.action_check_updates.triggered.connect(self._on_check_updates_manual)

        # ── FilePanel ─────────────────────────────────────────────────────
        self._file_panel.files_dropped.connect(self._on_files_dropped)
        self._file_panel.document_selected.connect(self._on_document_selected)
        self._file_panel.document_removed.connect(self._on_document_removed)
        self._file_panel.order_changed.connect(self._on_document_order_changed)

        # ── PagePanel ─────────────────────────────────────────────────────
        self._page_panel.pages_selected.connect(self._on_pages_selected)
        self._page_panel.thumbnail_visible.connect(self._on_thumbnail_visible)
        self._preview_panel.preview_requested.connect(self._on_preview_requested)

        # ── PreviewService ────────────────────────────────────────────────
        self._preview_svc.thumbnail_ready.connect(self._on_thumbnail_ready)
        self._preview_svc.thumbnail_ready.connect(self._on_preview_ready)
        self._preview_svc.render_error.connect(self._on_render_error)

        # ── TOC panel ─────────────────────────────────────────────────────
        self._toc_panel.go_to_page_requested.connect(self._on_toc_goto_page)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        # Re-apply fit-window zoom if the user had used it, so the page
        # stays fitted after maximize / window snapping / manual resize.
        self._preview_panel.refit_if_needed()

    # ── Preview panel toggle → move TOC ──────────────────────────────────

    def _on_toggle_preview(self, visible: bool) -> None:
        """Show/hide preview panel and relocate TOC accordingly."""
        self._preview_panel.setVisible(visible)
        if visible:
            # Move TOC back to center column bottom (if it's in right slot)
            if self._toc_panel.parent() is self._h_splitter:
                self._toc_panel.setParent(None)  # type: ignore[call-overload]
                self._center_splitter.addWidget(self._toc_panel)
                self._center_splitter.setSizes(_CENTER_V_SIZES)
            # Restore preview to right slot with saved or default ratios
            self._h_splitter.addWidget(self._preview_panel)
            self._h_splitter.setStretchFactor(0, 0)
            self._h_splitter.setStretchFactor(1, 1)
            self._h_splitter.setStretchFactor(2, 0)
            h_ratios = self._settings.value("window/h_splitter_ratios")
            if h_ratios:
                try:
                    ratios = [float(r) for r in h_ratios]
                    total = self._h_splitter.width()
                    self._h_splitter.setSizes([max(1, int(r * total)) for r in ratios])
                except (TypeError, ValueError):
                    self._h_splitter.setSizes(_H_SIZES)
            else:
                self._h_splitter.setSizes(_H_SIZES)
        else:
            # Detach both; put TOC in the right slot at a sensible width
            self._preview_panel.setParent(None)  # type: ignore[call-overload]
            self._toc_panel.setParent(None)       # type: ignore[call-overload]
            self._h_splitter.addWidget(self._toc_panel)
            self._h_splitter.setStretchFactor(0, 0)
            self._h_splitter.setStretchFactor(1, 1)
            self._h_splitter.setStretchFactor(2, 0)
            # Give file | center | toc a reasonable split; center gets the rest
            total = sum(self._h_splitter.sizes()) or (WINDOW_DEFAULT_WIDTH - 20)
            file_w = self._h_splitter.sizes()[0] if self._h_splitter.sizes() else 220
            toc_w = _TOC_RIGHT_WIDTH
            center_w = max(300, total - file_w - toc_w)
            self._h_splitter.setSizes([file_w, center_w, toc_w])

    # ── TOC ↔ Pages integration ───────────────────────────────────────────

    def _on_toc_goto_page(self, page_number: int) -> None:
        """Scroll PagePanel to the page whose 1-based display number matches."""
        self._page_panel.scroll_to_page(page_number - 1)

    # ── Drag-and-drop onto the main window ───────────────────────────────

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasUrls() and any(
            u.toLocalFile().lower().endswith(".pdf")
            for u in event.mimeData().urls()
        ):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # type: ignore[override]
        paths = [
            u.toLocalFile()
            for u in event.mimeData().urls()
            if u.toLocalFile().lower().endswith(".pdf")
        ]
        if paths:
            self._import_files([Path(p) for p in paths])
            event.acceptProposedAction()

    # ── Geometry persistence ──────────────────────────────────────────────

    def _restore_geometry(self) -> None:
        geometry = self._settings.value("window/geometry")
        state = self._settings.value("window/state")
        h_ratios = self._settings.value("window/h_splitter_ratios")
        center_ratios = self._settings.value("window/center_splitter_ratios")

        if geometry:
            self.restoreGeometry(geometry)  # type: ignore[arg-type]
        else:
            self._fit_to_screen()

        # After restoring, make sure the window fits within the available
        # screen area (handles cases where saved geometry was from a larger
        # monitor or the window was dragged partially offscreen).
        self._constrain_to_screen()

        if state:
            self.restoreState(state)  # type: ignore[arg-type]

        # Restore splitter ratios after the window has its final size.
        # Defer via singleShot so widget sizes are valid when we apply them.
        if h_ratios or center_ratios:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, lambda: self._apply_splitter_ratios(h_ratios, center_ratios))

    def _fit_to_screen(self) -> None:
        """Size the window to fit the available screen, up to the defaults."""
        from PySide6.QtWidgets import QApplication
        screen = QApplication.primaryScreen()
        if screen is None:
            self.resize(WINDOW_DEFAULT_WIDTH, WINDOW_DEFAULT_HEIGHT)
            return
        avail = screen.availableGeometry()
        w = min(WINDOW_DEFAULT_WIDTH, avail.width())
        h = min(WINDOW_DEFAULT_HEIGHT, avail.height())
        self.resize(w, h)
        # Centre on screen
        self.move(
            avail.x() + (avail.width() - w) // 2,
            avail.y() + (avail.height() - h) // 2,
        )

    def _constrain_to_screen(self) -> None:
        """Shrink and reposition the window if it exceeds the available screen area."""
        from PySide6.QtWidgets import QApplication
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        geo = self.frameGeometry()

        # Clamp size
        new_w = min(geo.width(), avail.width())
        new_h = min(geo.height(), avail.height())
        if new_w != geo.width() or new_h != geo.height():
            self.resize(new_w, new_h)
            geo = self.frameGeometry()

        # Clamp position so the window doesn't go off-screen
        new_x = max(avail.x(), min(geo.x(), avail.right() - geo.width()))
        new_y = max(avail.y(), min(geo.y(), avail.bottom() - geo.height()))
        if new_x != geo.x() or new_y != geo.y():
            self.move(new_x, new_y)

    def _apply_splitter_ratios(self, h_ratios, center_ratios) -> None:
        """Convert saved ratios back to pixel sizes based on current splitter size."""
        if h_ratios:
            try:
                ratios = [float(r) for r in h_ratios]
                total = self._h_splitter.width()
                sizes = [max(1, int(r * total)) for r in ratios]
                self._h_splitter.setSizes(sizes)
            except (TypeError, ValueError):
                self._h_splitter.setSizes(_H_SIZES)

        if center_ratios:
            try:
                ratios = [float(r) for r in center_ratios]
                total = self._center_splitter.height()
                sizes = [max(1, int(r * total)) for r in ratios]
                self._center_splitter.setSizes(sizes)
            except (TypeError, ValueError):
                self._center_splitter.setSizes(_CENTER_V_SIZES)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._settings.setValue("window/geometry", self.saveGeometry())
        self._settings.setValue("window/state", self.saveState())

        # Save as ratios so they scale correctly to any future window size.
        h_total = self._h_splitter.width()
        if h_total > 0:
            h_ratios = [s / h_total for s in self._h_splitter.sizes()]
            self._settings.setValue("window/h_splitter_ratios", h_ratios)

        c_total = self._center_splitter.height()
        if c_total > 0:
            c_ratios = [s / c_total for s in self._center_splitter.sizes()]
            self._settings.setValue("window/center_splitter_ratios", c_ratios)

        log.info("Window geometry saved")
        super().closeEvent(event)

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        """Global key bindings that work regardless of widget focus."""
        from PySide6.QtCore import Qt as _Qt
        key = event.key()
        mods = event.modifiers()
        ctrl = bool(mods & _Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & _Qt.KeyboardModifier.ShiftModifier)

        if key == _Qt.Key.Key_Delete and not ctrl and not shift:
            # Del → delete selected pages (no confirmation when triggered by key)
            indices = self._selected_indices()
            if indices:
                self._project.delete_pages(indices)
                self._refresh_page_panel()
                self._update_undo_redo_actions()
                self._page_panel.show_deletion_warning()
                self._status_bar.show_message(
                    f"Deleted {len(indices)} page(s) — Ctrl+Z to undo", timeout_ms=3000
                )
            return

        if key == _Qt.Key.Key_A and ctrl and not shift:
            self._page_panel.select_all()
            return

        if key == _Qt.Key.Key_Escape:
            self._page_panel.deselect_all()
            return

        super().keyPressEvent(event)

    # ── Import helpers ────────────────────────────────────────────────────

    def _on_open_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Import PDF Files",
            self._settings.value("last_open_dir", str(Path.home())),
            "PDF Files (*.pdf);;All Files (*)",
        )
        if paths:
            self._settings.setValue("last_open_dir", str(Path(paths[0]).parent))
            self._import_files([Path(p) for p in paths])

    def _on_files_dropped(self, paths: list[str]) -> None:
        self._import_files([Path(p) for p in paths])

    def _import_files(self, paths: list[Path]) -> None:
        self._status_bar.show_message(f"Importing {len(paths)} file(s)…")
        self._status_bar.show_progress(0, "Importing…")

        succeeded, failed = self._project.import_documents(paths)

        if failed:
            msgs = "\n".join(f"• {p.name}: {err}" for p, err in failed)
            QMessageBox.warning(
                self,
                "Import Errors",
                f"The following files could not be imported:\n\n{msgs}",
            )

        for doc in succeeded:
            color = self._file_panel.get_doc_color(doc.doc_id)
            if color is None:
                self._file_panel.add_document_entry(
                    doc_id=doc.doc_id,
                    title=doc.title,
                    page_count=doc.page_count,
                    file_size_mb=doc.file_size_mb,
                )

        self._refresh_page_panel()
        self._update_undo_redo_actions()

        total = self._project.total_pages
        self._status_bar.hide_progress()
        self._status_bar.show_message(
            f"Imported {len(succeeded)} file(s) — {total} total pages",
            timeout_ms=4000,
        )

    def _refresh_page_panel(self) -> None:
        pages = self._project.pages
        doc_colors: dict[str, QColor] = {}
        for doc in self._project.documents:
            color = self._file_panel.get_doc_color(doc.doc_id)
            doc_colors[doc.doc_id] = color if color else QColor("#89B4FA")

        self._page_panel.load_pages(pages, doc_colors)
        self._toc_panel.set_total_pages(self._project.total_pages)

    def _update_undo_redo_actions(self) -> None:
        """Sync toolbar/menu undo-redo enabled state with the undo stack."""
        stack = self._project.undo_stack
        tb = self._toolbar

        tb.action_undo.setEnabled(stack.can_undo)
        tb.action_redo.setEnabled(stack.can_redo)

        undo_desc = stack.undo_description or "Undo"
        redo_desc = stack.redo_description or "Redo"
        tb.action_undo.setToolTip(f"Undo: {undo_desc}  (Ctrl+Z)")
        tb.action_redo.setToolTip(f"Redo: {redo_desc}  (Ctrl+Y)")

    # ── FilePanel signal handlers ──────────────────────────────────────────

    def _on_document_selected(self, doc_id: str) -> None:
        doc = self._project.get_document(doc_id)
        if doc is None:
            return
        for page in self._project.pages:
            if page.document_id == doc_id:
                self._preview_panel.show_page(
                    doc_id=page.document_id,
                    path=page.source_path,
                    source_page_index=page.source_page_index,
                    rotation=page.rotation,
                    display_number=page.display_index + 1,
                )
                break

    def _on_document_removed(self, doc_id: str) -> None:
        self._project.remove_document(doc_id)
        self._preview_svc.invalidate_document(doc_id)
        if self._project.is_empty:
            self._page_panel.show_placeholder()
            self._preview_panel.clear()
        else:
            self._refresh_page_panel()
        self._update_undo_redo_actions()
        self._status_bar.show_message(
            f"Removed document — {self._project.total_pages} pages remaining",
            timeout_ms=3000,
        )

    def _on_document_order_changed(self, ordered_ids: list[str]) -> None:
        self._project.reorder_documents(ordered_ids)
        self._refresh_page_panel()

    # ── PagePanel signal handlers ──────────────────────────────────────────

    def _on_pages_selected(self, indices: list[int]) -> None:
        # Update TOC panel with the last selected page (for pre-fill and sync)
        last = indices[-1] if indices else -1
        self._last_selected_page = last
        self._toc_panel.set_selected_page(last)

        if not indices:
            return
        idx = indices[-1]
        if idx >= len(self._project.pages):
            return
        page: PageItem = self._project.pages[idx]
        self._preview_panel.show_page(
            doc_id=page.document_id,
            path=page.source_path,
            source_page_index=page.source_page_index,
            rotation=page.rotation,
            display_number=page.display_index + 1,
        )

    def _on_thumbnail_visible(
        self, doc_id: str, path, page_index: int, rotation: int
    ) -> None:
        doc = self._project.get_document(doc_id)
        password = doc.password if doc else ""
        result = self._preview_svc.request_thumbnail(
            doc_id=doc_id,
            path=Path(str(path)),
            page_index=page_index,
            rotation=rotation,
            password=password,
        )
        if result is not None:
            self._page_panel.update_thumbnail(doc_id, page_index, result)

    def _on_preview_requested(
        self, doc_id: str, path, page_index: int, rotation: int, width: int, height: int
    ) -> None:
        doc = self._project.get_document(doc_id)
        password = doc.password if doc else ""
        result = self._preview_svc.request_preview(
            doc_id=doc_id,
            path=Path(str(path)),
            page_index=page_index,
            rotation=rotation,
            password=password,
            width=width,
            height=height,
        )
        if result is not None:
            self._preview_panel.set_pixmap(doc_id, page_index, result)

    # ── PreviewService signal handlers ────────────────────────────────────

    def _on_thumbnail_ready(self, doc_id: str, page_index: int, pixmap) -> None:
        self._page_panel.update_thumbnail(doc_id, page_index, pixmap)

    def _on_preview_ready(self, doc_id: str, page_index: int, pixmap) -> None:
        self._preview_panel.set_pixmap(doc_id, page_index, pixmap)

    def _on_render_error(self, doc_id: str, page_index: int, error: str) -> None:
        log.warning("Render error doc=%s page=%d: %s", doc_id[:8], page_index, error)

    # ── Page operation handlers (toolbar / keyboard) ───────────────────────

    def _selected_indices(self) -> list[int]:
        return self._page_panel.selected_indices()

    def _on_rotate_cw(self) -> None:
        self._on_rotate_cw_indices(self._selected_indices())

    def _on_rotate_ccw(self) -> None:
        self._on_rotate_ccw_indices(self._selected_indices())

    def _on_delete_pages(self) -> None:
        self._on_delete_indices(self._selected_indices())

    def _on_insert_blank(self) -> None:
        indices = self._selected_indices()
        after = indices[-1] if indices else (self._project.total_pages - 1)
        self._on_insert_blank_after(after)

    def _on_copy_pages(self) -> None:
        self._on_copy_indices(self._selected_indices())

    def _on_extract_pages(self) -> None:
        self._on_extract_indices(self._selected_indices())

    # ── Page operation handlers (from context menu / panel signals) ────────

    def _on_rotate_cw_indices(self, indices: list[int]) -> None:
        if not indices:
            return
        self._project.rotate_pages(indices, "cw")
        self._preview_svc.invalidate_document("")   # flush all — rotation changed
        self._refresh_page_panel()
        self._update_undo_redo_actions()
        self._status_bar.show_message(
            f"Rotated {len(indices)} page(s) CW", timeout_ms=2000
        )

    def _on_rotate_ccw_indices(self, indices: list[int]) -> None:
        if not indices:
            return
        self._project.rotate_pages(indices, "ccw")
        self._preview_svc.invalidate_document("")
        self._refresh_page_panel()
        self._update_undo_redo_actions()
        self._status_bar.show_message(
            f"Rotated {len(indices)} page(s) CCW", timeout_ms=2000
        )

    def _on_delete_indices(self, indices: list[int]) -> None:
        if not indices:
            return
        n = len(indices)
        reply = QMessageBox.question(
            self,
            "Delete Pages",
            f"Delete {n} selected page{'s' if n > 1 else ''}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._project.delete_pages(indices)
        self._refresh_page_panel()
        self._update_undo_redo_actions()
        self._page_panel.show_deletion_warning()
        self._status_bar.show_message(
            f"Deleted {n} page{'s' if n > 1 else ''} — Ctrl+Z to undo", timeout_ms=3000
        )

    def _on_copy_indices(self, indices: list[int]) -> None:
        if not indices:
            return
        after = indices[-1]
        self._project.copy_pages(indices, after)
        self._refresh_page_panel()
        self._update_undo_redo_actions()
        self._status_bar.show_message(
            f"Copied {len(indices)} page(s)", timeout_ms=2000
        )

    def _on_insert_blank_after(self, after_index: int) -> None:
        self._project.insert_blank_page(after_index)
        self._refresh_page_panel()
        self._update_undo_redo_actions()
        self._page_panel.show_deletion_warning()
        self._status_bar.show_message("Blank page inserted", timeout_ms=2000)

    def _on_extract_indices(self, indices: list[int]) -> None:
        if not indices:
            return

        from engines.page_engine import extract_pages
        from gui.dialogs.export_dialog import ExportDialog
        from services.export_service import ExportService

        extracted = extract_pages(self._project.pages, indices)
        if not extracted:
            return

        last_dir = self._settings.value("last_export_dir", str(Path.home()))
        default_path = Path(last_dir) / "extracted.pdf"

        # Build a temporary ExportService that works on the extracted subset
        class _TempProject:
            """Minimal duck-type of ProjectService for ExportDialog."""
            def __init__(self, pages, docs):
                self.pages = pages
                self.documents = docs

        tmp_project = _TempProject(extracted, self._project.documents)
        tmp_svc = ExportService(tmp_project, self)  # type: ignore[arg-type]

        dialog = ExportDialog(tmp_svc, default_path, self)
        dialog.exec()

        if dialog.output_path:
            self._settings.setValue("last_export_dir", str(dialog.output_path.parent))
            self._status_bar.show_message(
                f"Extracted {len(indices)} page(s) → {dialog.output_path.name}",
                timeout_ms=3000,
            )

    def _on_pages_move(self, indices: list[int], to_index: int) -> None:
        if not indices:
            return
        self._project.move_pages(indices, to_index)
        self._refresh_page_panel()
        self._update_undo_redo_actions()
        self._status_bar.show_message(
            f"Moved {len(indices)} page(s)", timeout_ms=1500
        )

    # ── Session ────────────────────────────────────────────────────────────

    def _on_close_all(self) -> None:
        if self._project.is_empty:
            return
        reply = QMessageBox.question(
            self,
            "Close All Documents",
            "Close all imported documents and clear the session?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._project.clear()  # also clears toc_service
            self._preview_svc.clear()
            self._file_panel.clear()
            self._page_panel.show_placeholder()
            self._preview_panel.clear()
            self._update_undo_redo_actions()
            self._status_bar.show_message("Session cleared", timeout_ms=2000)

    # ── Undo / Redo ───────────────────────────────────────────────────────

    def _on_undo(self) -> None:
        desc = self._project.undo()
        if desc:
            self._refresh_page_panel()
            self._update_undo_redo_actions()
            self._status_bar.show_message(f"Undid: {desc}", timeout_ms=2000)

    def _on_redo(self) -> None:
        desc = self._project.redo()
        if desc:
            self._refresh_page_panel()
            self._update_undo_redo_actions()
            self._status_bar.show_message(f"Redid: {desc}", timeout_ms=2000)

    # ── Save / Export ─────────────────────────────────────────────────────

    def _on_save(self) -> None:
        """Quick-save: re-use the last exported path if known, else Save As."""
        last = self._settings.value("last_export_path", "")
        if last:
            self._run_export(Path(last))
        else:
            self._on_save_as()

    def _on_save_as(self) -> None:
        if self._project.is_empty:
            self._status_bar.show_message("No documents imported yet.", timeout_ms=2000)
            return

        from gui.dialogs.export_dialog import ExportDialog

        last_dir = self._settings.value("last_export_dir", str(Path.home()))
        default_path = Path(last_dir) / "merged.pdf"

        dialog = ExportDialog(self._export_svc, default_path, self)
        dialog.exec()

        if dialog.output_path:
            self._settings.setValue("last_export_path", str(dialog.output_path))
            self._settings.setValue("last_export_dir", str(dialog.output_path.parent))

    def _run_export(self, output_path: Path) -> None:
        """Start an export to *output_path* with an inline progress message."""
        if self._project.is_empty:
            self._status_bar.show_message("No documents imported yet.", timeout_ms=2000)
            return

        if self._export_svc.is_busy:
            self._status_bar.show_message("Export already in progress…", timeout_ms=2000)
            return

        # Connect one-shot signals for status bar feedback
        def _on_done(path: str) -> None:
            self._status_bar.hide_progress()
            self._status_bar.show_message(
                f"Saved: {Path(path).name}", timeout_ms=4000
            )
            self._export_svc.export_finished.disconnect(_on_done)
            self._export_svc.export_failed.disconnect(_on_err)

        def _on_err(msg: str) -> None:
            self._status_bar.hide_progress()
            self._status_bar.show_message(f"Export failed: {msg}", timeout_ms=5000)
            self._export_svc.export_finished.disconnect(_on_done)
            self._export_svc.export_failed.disconnect(_on_err)

        self._export_svc.export_started.connect(
            lambda total: self._status_bar.show_progress(0, f"Exporting {total} pages…")
        )
        self._export_svc.export_progress.connect(
            lambda cur, tot: self._status_bar.show_progress(
                int(cur / tot * 100) if tot else 0, f"Page {cur}/{tot}"
            )
        )
        self._export_svc.export_finished.connect(_on_done)
        self._export_svc.export_failed.connect(_on_err)

        self._export_svc.export(output_path)

    # ── Theme ─────────────────────────────────────────────────────────────

    def _on_toggle_theme(self) -> None:
        self._theme = "light" if self._theme == "dark" else "dark"
        app = QApplication.instance()
        if app:
            apply_theme(app, self._theme)  # type: ignore[arg-type]
        self._toolbar.update_theme(self._theme)
        self._settings.setValue("ui/theme", self._theme)
        log.info("Theme toggled to: %s", self._theme)

    # ── Tools ─────────────────────────────────────────────────────────────

    def _on_detect_watermark(self) -> None:
        if self._project.is_empty:
            self._status_bar.show_message("Import documents first.", timeout_ms=2000)
            return

        if len(self._project.documents) == 1:
            # Single document — original immediate workflow
            from gui.dialogs.watermark_dialog import WatermarkDialog
            doc = self._project.documents[0]
            dialog = WatermarkDialog(doc, self._watermark_svc, self)
            dialog.exec()
        else:
            # Multiple documents — deferred merge-time removal
            from gui.dialogs.watermark_multi_dialog import WatermarkMultiDialog
            dialog = WatermarkMultiDialog(
                documents=self._project.documents,
                existing_removals=self._project.watermark_removals,
                parent=self,
            )
            if dialog.exec() == WatermarkMultiDialog.DialogCode.Accepted:
                total = sum(
                    len(v) for v in self._project.watermark_removals.values()
                )
                if total:
                    self._status_bar.show_message(
                        f"{total} watermark removal(s) queued — "
                        "will be applied on export.",
                        timeout_ms=4000,
                    )
                else:
                    self._status_bar.show_message(
                        "No watermark removals queued.", timeout_ms=2000
                    )

    def _on_detect_toc(self) -> None:
        """TOC detection is now per-document via right-click on section headers."""
        QMessageBox.information(
            self,
            "Detect Bookmarks",
            "Bookmarks are now loaded automatically when you import a document.\n\n"
            "To run experimental heading detection for a specific document, "
            "right-click its section header in the Bookmarks panel and choose\n"
            "\"Detect from content (experimental)\".",
        )

    def _on_preferences(self) -> None:
        self._status_bar.show_message("Preferences — coming soon", timeout_ms=1500)

    # ── Help ──────────────────────────────────────────────────────────────

    def _on_about(self) -> None:
        from gui.dialogs.about_dialog import AboutDialog
        AboutDialog(self, theme=self._theme).exec()

    def _on_check_updates_manual(self) -> None:
        """Triggered by Help → Check for Updates."""
        self._status_bar.show_message("Checking for updates…", timeout_ms=3000)
        checker = UpdateChecker(install_mode=INSTALL_MODE, parent=self)
        checker.update_available.connect(self._on_update_available)
        checker.up_to_date.connect(lambda: QMessageBox.information(
            self, "Up to date", f"You have the latest version ({APP_VERSION})."
        ))
        checker.check_failed.connect(lambda err: QMessageBox.warning(
            self, "Update check failed", f"Could not check for updates:\n{err}"
        ))
        checker.check()

    def _schedule_update_check(self) -> None:
        """Run a silent background update check shortly after startup."""
        from PySide6.QtCore import QTimer
        QTimer.singleShot(3000, self._run_background_update_check)

    def _run_background_update_check(self) -> None:
        self._update_checker = UpdateChecker(install_mode=INSTALL_MODE, parent=self)
        self._update_checker.update_available.connect(self._on_update_available)
        self._update_checker.check()

    def _on_update_available(self, info) -> None:
        from gui.dialogs.update_dialog import UpdateDialog
        UpdateDialog(info, self).exec()

    def _on_shortcuts(self) -> None:
        shortcuts = (
            "<table cellspacing='6'>"
            "<tr><td><b>Ctrl+O</b></td><td>Open / Import PDFs</td></tr>"
            "<tr><td><b>Ctrl+S</b></td><td>Save merged PDF</td></tr>"
            "<tr><td><b>Ctrl+Shift+S</b></td><td>Save As</td></tr>"
            "<tr><td><b>Ctrl+Shift+W</b></td><td>Close all documents</td></tr>"
            "<tr><td><b>Ctrl+Z / Ctrl+Y</b></td><td>Undo / Redo</td></tr>"
            "<tr><td><b>Del</b></td><td>Delete selected pages</td></tr>"
            "<tr><td><b>R</b></td><td>Rotate selected pages CW</td></tr>"
            "<tr><td><b>Shift+R</b></td><td>Rotate selected pages CCW</td></tr>"
            "<tr><td><b>Ctrl+D</b></td><td>Copy / duplicate selected pages</td></tr>"
            "<tr><td><b>Ctrl+E</b></td><td>Extract selected pages…</td></tr>"
            "<tr><td><b>Ctrl+B</b></td><td>Insert blank page</td></tr>"
            "<tr><td><b>Ctrl+A</b></td><td>Select all pages</td></tr>"
            "<tr><td><b>Escape</b></td><td>Deselect all</td></tr>"
            "<tr><td><b>Ctrl++ / Ctrl+-</b></td><td>Zoom in / out</td></tr>"
            "<tr><td><b>Ctrl+0</b></td><td>Fit page</td></tr>"
            "<tr><td><b>Ctrl+,</b></td><td>Preferences</td></tr>"
            "<tr><td><b>Ctrl+Q</b></td><td>Quit</td></tr>"
            "<tr><td><b>F1</b></td><td>Show shortcuts</td></tr>"
            "</table>"
        )
        QMessageBox.information(self, "Keyboard Shortcuts", shortcuts)
