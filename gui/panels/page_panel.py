"""
gui/panels/page_panel.py — Page organizer panel.

Features:
  - Checkbox on each thumbnail; select-all checkbox in header
  - Single click = select; Ctrl+click = toggle; Shift+click = range
  - Rubber-band drag on empty space = select rectangle
  - Drag on thumbnail = reorder (existing behaviour)
  - Right-click context menu (rotate, delete, copy, insert blank, extract)
  - Lazy thumbnail loading on scroll
  - Deletion-warning banner (shown after delete, dismissed manually)
  - scroll_to_page(index) public method for TOC "go to" button
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import QByteArray, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QDragEnterEvent, QDropEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from models.page_item import PageItem
from widgets.thumbnail_widget import MIME_PAGE_INDEX, ThumbnailWidget

log = logging.getLogger(__name__)

_GRID_COLUMNS = 3
_GRID_SPACING = 8


# ── Thumbnail grid ────────────────────────────────────────────────────────────

class _ThumbnailGrid(QWidget):
    """Flow grid of ThumbnailWidget cards.

    Supports:
      - Drop-to-reorder (from thumbnail drags)
      - Rubber-band selection (from empty-space drags)
    """

    page_clicked = Signal(int, bool, bool)   # index, ctrl, shift
    page_double_clicked = Signal(int)
    drop_at = Signal(int, int)               # from_index, to_index
    rubber_band_select = Signal(QRect, bool) # rect in widget coords, ctrl_held

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._cards: list[ThumbnailWidget] = []
        self._selected: set[int] = set()
        self.setAcceptDrops(True)

        # Rubber band state
        self._rb_start: Optional[QPoint] = None
        self._rb_rect: Optional[QRect] = None
        self._rb_active = False

        self._build_grid_layout()

    def _build_grid_layout(self) -> None:
        self._layout = QGridLayout(self)
        self._layout.setContentsMargins(
            _GRID_SPACING, _GRID_SPACING, _GRID_SPACING, _GRID_SPACING
        )
        self._layout.setSpacing(_GRID_SPACING)
        self._layout.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )

    def add_card(self, card: ThumbnailWidget) -> None:
        idx = len(self._cards)
        row, col = divmod(idx, _GRID_COLUMNS)
        self._layout.addWidget(card, row, col)
        self._cards.append(card)
        card.clicked.connect(self._on_card_clicked)
        card.double_clicked.connect(self._on_card_double_clicked)

    def clear(self) -> None:
        for card in self._cards:
            self._layout.removeWidget(card)
            card.deleteLater()
        self._cards.clear()
        self._selected.clear()

    def set_pixmap(self, display_index: int, pixmap) -> None:
        if 0 <= display_index < len(self._cards):
            self._cards[display_index].set_pixmap(pixmap)

    def update_selection(self, indices: set[int]) -> None:
        for i, card in enumerate(self._cards):
            card.set_selected(i in indices)
        self._selected = set(indices)

    def card_count(self) -> int:
        return len(self._cards)

    def card_at(self, index: int) -> Optional[ThumbnailWidget]:
        if 0 <= index < len(self._cards):
            return self._cards[index]
        return None

    # ── Card events ───────────────────────────────────────────────────────

    def _on_card_clicked(self, index: int, ctrl: bool, shift: bool) -> None:
        self.page_clicked.emit(index, ctrl, shift)

    def _on_card_double_clicked(self, index: int) -> None:
        self.page_double_clicked.emit(index)

    # ── Rubber-band mouse events ──────────────────────────────────────────

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            # Only start rubber band if not pressing on a card
            if self._card_at_pos(event.pos()) is None:
                self._rb_start = event.pos()
                self._rb_rect = QRect(self._rb_start, self._rb_start)
                self._rb_active = True
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._rb_active and self._rb_start is not None:
            self._rb_rect = QRect(self._rb_start, event.pos()).normalized()
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if self._rb_active and self._rb_rect is not None:
            mods = QApplication.keyboardModifiers()
            ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
            self.rubber_band_select.emit(self._rb_rect, ctrl)
        self._rb_active = False
        self._rb_start = None
        self._rb_rect = None
        self.update()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        super().paintEvent(event)
        if self._rb_active and self._rb_rect and not self._rb_rect.isEmpty():
            painter = QPainter(self)
            pen = QPen(QColor("#89B4FA"), 1, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(QColor(137, 180, 250, 40))
            painter.drawRect(self._rb_rect)
            painter.end()

    def _card_at_pos(self, pos: QPoint) -> Optional[ThumbnailWidget]:
        for card in self._cards:
            if card.geometry().contains(pos):
                return card
        return None

    # ── Drop onto the grid ────────────────────────────────────────────────

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # type: ignore[override]
        if event.mimeData().hasFormat(MIME_PAGE_INDEX):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasFormat(MIME_PAGE_INDEX):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:  # type: ignore[override]
        if not event.mimeData().hasFormat(MIME_PAGE_INDEX):
            event.ignore()
            return

        raw: QByteArray = event.mimeData().data(MIME_PAGE_INDEX)
        try:
            from_index = int(bytes(raw).decode())
        except ValueError:
            event.ignore()
            return

        drop_pos = event.position().toPoint()
        to_index = self._index_at(drop_pos)
        if to_index < 0:
            to_index = len(self._cards)

        event.acceptProposedAction()
        if from_index != to_index:
            self.drop_at.emit(from_index, to_index)

    def _index_at(self, pos: QPoint) -> int:
        for i, card in enumerate(self._cards):
            if card.geometry().contains(pos):
                return i
        return -1


# ── Page panel ────────────────────────────────────────────────────────────────

class PagePanel(QWidget):
    """Centre panel: thumbnail grid for all pages.

    Signals:
        pages_selected:        list[int] of selected display indices
        page_double_clicked:   int
        thumbnail_visible:     (doc_id, path, page_index, rotation)
        pages_move_requested:  (from_indices: list[int], to_index: int)
        delete_requested:      list[int]
        rotate_cw_requested:   list[int]
        rotate_ccw_requested:  list[int]
        copy_requested:        list[int]
        insert_blank_requested: int (after_index)
        extract_requested:     list[int]
    """

    pages_selected = Signal(list)
    page_double_clicked = Signal(int)
    thumbnail_visible = Signal(str, object, int, int)
    pages_move_requested = Signal(list, int)
    delete_requested = Signal(list)
    rotate_cw_requested = Signal(list)
    rotate_ccw_requested = Signal(list)
    copy_requested = Signal(list)
    insert_blank_requested = Signal(int)
    extract_requested = Signal(list)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._pages: list[PageItem] = []
        self._selected: set[int] = set()
        self._last_clicked: int = -1
        self._build_ui()

        self._scroll_timer = QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.setInterval(120)
        self._scroll_timer.timeout.connect(self._request_visible_thumbnails)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Header row ────────────────────────────────────────────────────
        header_row = QHBoxLayout()
        header_row.setContentsMargins(8, 6, 8, 4)
        header_row.setSpacing(6)

        self._select_all_cb = QCheckBox()
        self._select_all_cb.setToolTip("Select / deselect all pages")
        self._select_all_cb.setTristate(True)
        self._select_all_cb.setFixedSize(16, 16)
        self._select_all_cb.stateChanged.connect(self._on_select_all_changed)
        header_row.addWidget(self._select_all_cb)

        self._header = QLabel("Pages  (0)")
        self._header.setStyleSheet(
            "font-weight: 600; font-size: 13px;"
        )
        header_row.addWidget(self._header, 1)
        layout.addLayout(header_row)

        # ── Deletion warning banner ───────────────────────────────────────
        self._warn_bar = QWidget()
        warn_layout = QHBoxLayout(self._warn_bar)
        warn_layout.setContentsMargins(8, 4, 8, 4)
        warn_layout.setSpacing(8)
        warn_lbl = QLabel(
            "⚠  Pages deleted — TOC bookmark page numbers may need review."
        )
        warn_lbl.setStyleSheet("color: #FFD700; font-size: 11px;")
        warn_layout.addWidget(warn_lbl, 1)
        dismiss_btn = QPushButton("Dismiss")
        dismiss_btn.setFixedHeight(20)
        dismiss_btn.setFixedWidth(64)
        dismiss_btn.clicked.connect(self._dismiss_warning)
        warn_layout.addWidget(dismiss_btn)
        self._warn_bar.setStyleSheet("background: #3d2f00; border-bottom: 1px solid #5a4400;")
        self._warn_bar.setVisible(False)
        layout.addWidget(self._warn_bar)

        # ── Scroll area ───────────────────────────────────────────────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self._scroll.verticalScrollBar().valueChanged.connect(
            lambda _: self._scroll_timer.start()
        )

        self._grid: _ThumbnailGrid | None = None

        self._scroll.setWidget(self._make_placeholder())
        layout.addWidget(self._scroll, 1)

        # Context menu
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

    def _make_placeholder(self) -> QLabel:
        placeholder = QLabel("Drop PDF files here or use File → Open")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        placeholder.setStyleSheet("color: #585B70; font-size: 14px;")
        return placeholder

    def _make_grid(self) -> _ThumbnailGrid:
        grid = _ThumbnailGrid()
        grid.page_clicked.connect(self._on_page_clicked)
        grid.page_double_clicked.connect(self.page_double_clicked)
        grid.drop_at.connect(self._on_drop_at)
        grid.rubber_band_select.connect(self._on_rubber_band_select)
        return grid

    # ── Public API ────────────────────────────────────────────────────────

    def load_pages(self, pages: list[PageItem], doc_colors: dict[str, QColor]) -> None:
        self._pages = list(pages)
        self._selected.clear()
        self._last_clicked = -1

        # Always create a fresh grid — the old one may have been deleted by Qt
        # when setWidget() replaced it in the scroll area.
        self._grid = self._make_grid()

        for page in pages:
            color = doc_colors.get(page.document_id, QColor("#89B4FA"))
            card = ThumbnailWidget(display_index=page.display_index, doc_color=color)
            card.check_toggled.connect(self._on_check_toggled)
            self._grid.add_card(card)

        self._scroll.setWidget(self._grid)
        self._header.setText(f"Pages  ({len(pages)})")
        self._update_select_all_state()

        QTimer.singleShot(50, self._request_visible_thumbnails)

    def show_placeholder(self) -> None:
        self._grid = None
        self._scroll.setWidget(self._make_placeholder())
        self._header.setText("Pages  (0)")
        self._pages.clear()
        self._selected.clear()
        self._warn_bar.setVisible(False)
        self._update_select_all_state()

    def update_thumbnail(self, doc_id: str, page_index: int, pixmap) -> None:
        if self._grid is None:
            return
        for i, page in enumerate(self._pages):
            if page.document_id == doc_id and page.source_page_index == page_index:
                self._grid.set_pixmap(i, pixmap)

    def selected_indices(self) -> list[int]:
        return sorted(self._selected)

    def select_all(self) -> None:
        if self._grid is None:
            return
        self._selected = set(range(len(self._pages)))
        self._grid.update_selection(self._selected)
        self._update_select_all_state()
        self.pages_selected.emit(sorted(self._selected))

    def deselect_all(self) -> None:
        if self._grid is None:
            return
        self._selected.clear()
        self._grid.update_selection(self._selected)
        self._update_select_all_state()
        self.pages_selected.emit([])

    def show_deletion_warning(self) -> None:
        """Show the deletion warning banner."""
        self._warn_bar.setVisible(True)

    def scroll_to_page(self, index: int) -> None:
        """Scroll so the thumbnail at *index* is visible and select it."""
        if not (0 <= index < len(self._pages)) or self._grid is None:
            return
        card = self._grid.card_at(index)
        if card:
            # Scroll to make card visible
            self._scroll.ensureWidgetVisible(card)
        # Select only this page
        self._selected = {index}
        self._last_clicked = index
        self._grid.update_selection(self._selected)
        self._update_select_all_state()
        self.pages_selected.emit([index])

    # ── Selection ─────────────────────────────────────────────────────────

    def _on_page_clicked(self, index: int, ctrl: bool, shift: bool) -> None:
        if self._grid is None:
            return
        if shift and self._last_clicked >= 0:
            lo, hi = sorted([self._last_clicked, index])
            if ctrl:
                self._selected.update(range(lo, hi + 1))
            else:
                self._selected = set(range(lo, hi + 1))
        elif ctrl:
            self._selected.symmetric_difference_update({index})
        else:
            self._selected = {index}

        self._last_clicked = index
        self._grid.update_selection(self._selected)
        self._update_select_all_state()
        self.pages_selected.emit(sorted(self._selected))

    def _on_check_toggled(self, index: int, checked: bool) -> None:
        """Checkbox on a thumbnail was clicked directly."""
        if self._grid is None:
            return
        if checked:
            self._selected.add(index)
        else:
            self._selected.discard(index)
        self._grid.update_selection(self._selected)
        self._update_select_all_state()
        self.pages_selected.emit(sorted(self._selected))

    def _on_select_all_changed(self, state: int) -> None:
        """Select-all checkbox in header toggled."""
        # Ignore partial state programmatic changes
        if self._select_all_cb.signalsBlocked():
            return
        if state == Qt.CheckState.Checked.value:
            self.select_all()
        else:
            self.deselect_all()

    def _update_select_all_state(self) -> None:
        """Sync the tristate select-all checkbox without emitting signals."""
        total = len(self._pages)
        n_sel = len(self._selected)
        self._select_all_cb.blockSignals(True)
        if total == 0 or n_sel == 0:
            self._select_all_cb.setCheckState(Qt.CheckState.Unchecked)
        elif n_sel == total:
            self._select_all_cb.setCheckState(Qt.CheckState.Checked)
        else:
            self._select_all_cb.setCheckState(Qt.CheckState.PartiallyChecked)
        self._select_all_cb.blockSignals(False)

    def _on_rubber_band_select(self, rect: QRect, ctrl: bool) -> None:
        """Select all cards whose geometry intersects the rubber-band rect."""
        if self._grid is None:
            return
        hit: set[int] = set()
        for i, card in enumerate(self._grid._cards):
            if rect.intersects(card.geometry()):
                hit.add(i)
        if ctrl:
            self._selected.update(hit)
        else:
            self._selected = hit
        if hit:
            self._last_clicked = max(hit)
        self._grid.update_selection(self._selected)
        self._update_select_all_state()
        self.pages_selected.emit(sorted(self._selected))

    # ── Drag reorder ──────────────────────────────────────────────────────

    def _on_drop_at(self, from_index: int, to_index: int) -> None:
        if self._grid is None:
            return
        if from_index in self._selected and len(self._selected) > 1:
            indices = sorted(self._selected)
        else:
            indices = [from_index]
            self._selected = {from_index}
            self._grid.update_selection(self._selected)

        self.pages_move_requested.emit(indices, to_index)

    # ── Warning banner ────────────────────────────────────────────────────

    def _dismiss_warning(self) -> None:
        self._warn_bar.setVisible(False)

    # ── Context menu ──────────────────────────────────────────────────────

    def _show_context_menu(self, pos) -> None:
        indices = sorted(self._selected)
        if not indices:
            return

        menu = QMenu(self)
        n = len(indices)
        label = f"page{'s' if n > 1 else ''}"

        act_rotate_cw = QAction(f"Rotate {label} 90° CW", self)
        act_rotate_ccw = QAction(f"Rotate {label} 90° CCW", self)
        act_copy = QAction(f"Copy {label}", self)
        act_insert_blank = QAction("Insert blank page after", self)
        act_extract = QAction(f"Extract {label}…", self)
        act_delete = QAction(f"Delete {label}", self)
        act_delete.setShortcut("Del")

        menu.addAction(act_rotate_cw)
        menu.addAction(act_rotate_ccw)
        menu.addSeparator()
        menu.addAction(act_copy)
        menu.addAction(act_insert_blank)
        menu.addAction(act_extract)
        menu.addSeparator()
        menu.addAction(act_delete)

        act_rotate_cw.triggered.connect(lambda: self.rotate_cw_requested.emit(indices))
        act_rotate_ccw.triggered.connect(lambda: self.rotate_ccw_requested.emit(indices))
        act_copy.triggered.connect(lambda: self.copy_requested.emit(indices))
        act_insert_blank.triggered.connect(
            lambda: self.insert_blank_requested.emit(indices[-1])
        )
        act_extract.triggered.connect(lambda: self.extract_requested.emit(indices))
        act_delete.triggered.connect(lambda: self.delete_requested.emit(indices))

        menu.exec(self.mapToGlobal(pos))

    # ── Lazy thumbnail loading ─────────────────────────────────────────────

    def _request_visible_thumbnails(self) -> None:
        if not self._pages or self._scroll.widget() is not self._grid:
            return

        viewport = self._scroll.viewport()
        vp_rect = viewport.rect()

        for i, page in enumerate(self._pages):
            if i >= self._grid.card_count():
                break
            item = self._grid.layout().itemAt(i)
            if item is None:
                continue
            widget = item.widget()
            if widget is None:
                continue
            card_rect = widget.geometry()
            card_in_vp = card_rect.translated(-self._grid.pos())
            if vp_rect.intersects(card_in_vp):
                self.thumbnail_visible.emit(
                    page.document_id,
                    page.source_path,
                    page.source_page_index,
                    page.rotation,
                )
