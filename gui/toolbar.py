"""
gui/toolbar.py — Main application toolbar.

Actions are created here and exposed as public attributes so
MainWindow can connect them to slots without reaching into Qt internals.
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import QSize
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QToolBar, QWidget

log = logging.getLogger(__name__)


class AppToolBar(QToolBar):
    """Primary toolbar containing the most-used actions.

    All QAction objects are public attributes so they can be added
    to the menu bar as well (shared action, shared shortcut).
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__("Main Toolbar", parent)
        self.setObjectName("MainToolbar")  # needed for saveState()
        self.setMovable(False)
        self.setFloatable(False)
        self.setIconSize(QSize(20, 20))
        self.setToolButtonStyle(
            __import__("PySide6.QtCore", fromlist=["Qt"]).Qt.ToolButtonStyle.ToolButtonIconOnly
        )
        # Allow the toolbar to shrink — Qt will show a ">>" overflow button
        # for actions that don't fit rather than forcing the window wider.
        self.setMinimumWidth(1)

        self._build_actions()
        self._add_to_bar()

        # Style the overflow extension button after it's created
        self._style_extension_button()

    def _style_extension_button(self) -> None:
        """Find the overflow button and force plain text '>>'."""
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QIcon
        from PySide6.QtWidgets import QToolButton
        ext = self.findChild(QToolButton, "qt_toolbar_ext_button")
        if ext and ext.text() != ">>":
            ext.setIcon(QIcon())
            ext.setText(">>")
            ext.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            ext.setToolTip("More actions")

    def restyle_extension_button(self) -> None:
        """Re-apply extension button styling after a theme change."""
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QIcon
        from PySide6.QtWidgets import QToolButton
        ext = self.findChild(QToolButton, "qt_toolbar_ext_button")
        if ext:
            ext.setIcon(QIcon())
            ext.setText(">>")
            ext.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            ext.setToolTip("More actions")

    def childEvent(self, event) -> None:  # type: ignore[override]
        super().childEvent(event)
        if event.added():
            from PySide6.QtWidgets import QToolButton
            child = event.child()
            if isinstance(child, QToolButton) and child.objectName() == "qt_toolbar_ext_button":
                self._style_extension_button()

    # ── Action construction ───────────────────────────────────────────────

    def _make_action(
        self,
        text: str,
        tooltip: str,
        shortcut: Optional[str] = None,
        checkable: bool = False,
    ) -> QAction:
        action = QAction(text, self)
        action.setToolTip(tooltip)
        action.setCheckable(checkable)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        return action

    def _build_actions(self) -> None:
        """Instantiate all toolbar actions."""
        # File actions
        self.action_open = self._make_action(
            "Open", "Import PDF files  (Ctrl+O)", "Ctrl+O"
        )
        self.action_save = self._make_action(
            "Save", "Save merged PDF  (Ctrl+S)", "Ctrl+S"
        )
        self.action_save_as = self._make_action(
            "Save As", "Save merged PDF to a new file  (Ctrl+Shift+S)",
            "Ctrl+Shift+S",
        )

        # Edit actions
        self.action_undo = self._make_action(
            "Undo", "Undo last action  (Ctrl+Z)", "Ctrl+Z"
        )
        self.action_undo.setEnabled(False)

        self.action_redo = self._make_action(
            "Redo", "Redo last undone action  (Ctrl+Y)", "Ctrl+Y"
        )
        self.action_redo.setEnabled(False)

        # Page actions
        self.action_rotate_cw = self._make_action(
            "Rotate CW", "Rotate selected pages 90° clockwise  (R)",
            "R",
        )
        self.action_rotate_ccw = self._make_action(
            "Rotate CCW", "Rotate selected pages 90° counter-clockwise  (Shift+R)",
            "Shift+R",
        )
        self.action_delete_pages = self._make_action(
            "Delete", "Delete selected pages  (Del)", "Del"
        )
        self.action_copy_pages = self._make_action(
            "Copy Pages", "Duplicate selected pages  (Ctrl+D)", "Ctrl+D"
        )
        self.action_extract_pages = self._make_action(
            "Extract…", "Extract selected pages to a new PDF  (Ctrl+E)", "Ctrl+E"
        )

        # View actions
        self.action_zoom_in = self._make_action(
            "Zoom In", "Zoom in preview  (Ctrl++)", "Ctrl++"
        )
        self.action_zoom_out = self._make_action(
            "Zoom Out", "Zoom out preview  (Ctrl+-)", "Ctrl+-"
        )
        self.action_zoom_fit = self._make_action(
            "Fit Page", "Fit page to panel  (Ctrl+0)", "Ctrl+0"
        )
        self.action_zoom_fit_window = self._make_action(
            "Fit Window", "Fit page to window  (Ctrl+Shift+0)", "Ctrl+Shift+0"
        )

        # Theme toggle
        self.action_toggle_theme = self._make_action(
            "Toggle Theme",
            "Switch between dark and light mode",
            checkable=True,
        )

    def _add_to_bar(self) -> None:
        """Add actions (and separators) to the toolbar in display order."""
        self.addAction(self.action_open)
        self.addAction(self.action_save)
        self.addAction(self.action_save_as)
        self.addSeparator()
        self.addAction(self.action_undo)
        self.addAction(self.action_redo)
        self.addSeparator()
        self.addAction(self.action_rotate_cw)
        self.addAction(self.action_rotate_ccw)
        self.addAction(self.action_delete_pages)
        self.addAction(self.action_copy_pages)
        self.addAction(self.action_extract_pages)
        self.addSeparator()
        self.addAction(self.action_zoom_in)
        self.addAction(self.action_zoom_out)
        self.addAction(self.action_zoom_fit)
        self.addAction(self.action_zoom_fit_window)
        self.addSeparator()
        self.addAction(self.action_toggle_theme)
