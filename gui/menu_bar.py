"""
gui/menu_bar.py — Application menu bar.

Builds the full menu structure and wires it to the same QAction
objects created by AppToolBar so shortcuts are never duplicated.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QMenuBar, QWidget

if TYPE_CHECKING:
    from gui.toolbar import AppToolBar

log = logging.getLogger(__name__)


class AppMenuBar(QMenuBar):
    """Full application menu bar.

    Shared actions (Open, Save, Undo, …) are received from AppToolBar
    so a single QAction instance drives both the menu item and the
    toolbar button.

    Additional menu-only actions (About, Preferences, …) are created
    and exposed as public attributes.
    """

    def __init__(self, toolbar: "AppToolBar", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._toolbar = toolbar
        self._build_menus()

    # ── Menu construction ─────────────────────────────────────────────────

    def _build_menus(self) -> None:
        self._build_file_menu()
        self._build_edit_menu()
        self._build_view_menu()
        self._build_tools_menu()
        self._build_help_menu()

    def _build_file_menu(self) -> None:
        menu = self.addMenu("&File")
        menu.addAction(self._toolbar.action_open)

        # Recent files placeholder — populated by MainWindow at runtime
        self.menu_recent = menu.addMenu("Open &Recent")
        self.menu_recent.setEnabled(False)

        menu.addSeparator()
        menu.addAction(self._toolbar.action_save)
        menu.addAction(self._toolbar.action_save_as)
        menu.addSeparator()

        self.action_close_all = QAction("Close &All Documents", self)
        self.action_close_all.setShortcut(QKeySequence("Ctrl+Shift+W"))
        menu.addAction(self.action_close_all)

        menu.addSeparator()

        self.action_quit = QAction("&Quit", self)
        self.action_quit.setShortcut(QKeySequence("Ctrl+Q"))
        menu.addAction(self.action_quit)

    def _build_edit_menu(self) -> None:
        menu = self.addMenu("&Edit")
        menu.addAction(self._toolbar.action_undo)
        menu.addAction(self._toolbar.action_redo)
        menu.addSeparator()

        self.action_select_all = QAction("Select &All Pages", self)
        self.action_select_all.setShortcut(QKeySequence("Ctrl+A"))
        menu.addAction(self.action_select_all)

        self.action_deselect_all = QAction("&Deselect All", self)
        self.action_deselect_all.setShortcut(QKeySequence("Escape"))
        menu.addAction(self.action_deselect_all)

        menu.addSeparator()
        menu.addAction(self._toolbar.action_rotate_cw)
        menu.addAction(self._toolbar.action_rotate_ccw)
        menu.addAction(self._toolbar.action_delete_pages)

        menu.addSeparator()
        self.action_insert_blank = QAction("Insert &Blank Page", self)
        self.action_insert_blank.setShortcut(QKeySequence("Ctrl+B"))
        menu.addAction(self.action_insert_blank)

        self.action_extract_pages = QAction("&Extract Selected Pages…", self)
        menu.addAction(self.action_extract_pages)

    def _build_view_menu(self) -> None:
        menu = self.addMenu("&View")
        menu.addAction(self._toolbar.action_zoom_in)
        menu.addAction(self._toolbar.action_zoom_out)
        menu.addAction(self._toolbar.action_zoom_fit)
        menu.addAction(self._toolbar.action_zoom_fit_window)
        menu.addSeparator()
        menu.addAction(self._toolbar.action_toggle_theme)

        menu.addSeparator()
        self.action_show_file_panel = QAction("Show &File Panel", self, checkable=True)
        self.action_show_file_panel.setChecked(True)
        menu.addAction(self.action_show_file_panel)

        self.action_show_page_panel = QAction("Show &Page Panel", self, checkable=True)
        self.action_show_page_panel.setChecked(True)
        menu.addAction(self.action_show_page_panel)

        self.action_show_toc_panel = QAction("Show &TOC Panel", self, checkable=True)
        self.action_show_toc_panel.setChecked(True)
        menu.addAction(self.action_show_toc_panel)

        self.action_show_preview_panel = QAction(
            "Show P&review Panel", self, checkable=True
        )
        self.action_show_preview_panel.setChecked(True)
        menu.addAction(self.action_show_preview_panel)

    def _build_tools_menu(self) -> None:
        menu = self.addMenu("&Tools")

        self.action_detect_watermark = QAction("&Detect Watermarks…", self)
        menu.addAction(self.action_detect_watermark)

        self.action_detect_toc = QAction("Auto-Detect &TOC…", self)
        menu.addAction(self.action_detect_toc)

        menu.addSeparator()

        self.action_preferences = QAction("&Preferences…", self)
        self.action_preferences.setShortcut(QKeySequence("Ctrl+,"))
        menu.addAction(self.action_preferences)

    def _build_help_menu(self) -> None:
        menu = self.addMenu("&Help")

        self.action_check_updates = QAction("Check for &Updates…", self)
        menu.addAction(self.action_check_updates)

        menu.addSeparator()

        self.action_about = QAction("&About NeatPDF", self)
        menu.addAction(self.action_about)

        self.action_shortcuts = QAction("&Keyboard Shortcuts", self)
        self.action_shortcuts.setShortcut(QKeySequence("F1"))
        menu.addAction(self.action_shortcuts)
