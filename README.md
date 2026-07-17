# NeatPDF

A professional open-source desktop PDF toolkit built with Python and PySide6.

---

## Features

| Feature | Status |
|---------|--------|
| Import PDFs (single, multi, drag-and-drop) | ✅ |
| Page organizer (move, delete, rotate, copy, extract) | ✅ |
| Merge PDFs with document reordering | ✅ |
| Editable Table of Contents with live preview | ✅ |
| Auto TOC detection (bookmarks + heading heuristics) | ✅ |
| Watermark detection and removal | ✅ |
| PDF bookmark generation | ✅ |
| Dark / Light theme | ✅ |
| Persistent window layout | ✅ |
| Keyboard shortcuts | ✅ |

---

## Requirements

- Python 3.11+
- See `requirements.txt`

---

## Installation

```bash
# Clone the repository
git clone <repo-url>
cd NeatPDF

# Create a virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

---

## Running

```bash
python main.py
```

---

## Running Tests

```bash
# All tests (headless)
QT_QPA_PLATFORM=offscreen pytest tests/

# With coverage
QT_QPA_PLATFORM=offscreen pytest tests/ --cov=. --cov-report=term-missing
```

---

## Project Structure

```
NeatPDF/
├── main.py                      # Entry point
├── app.py                       # QApplication + theme
├── config.py                    # Constants
├── logger.py                    # Logging setup
├── gui/
│   ├── main_window.py           # Central signal hub
│   ├── toolbar.py               # Toolbar actions + shortcuts
│   ├── menu_bar.py              # Menu bar
│   ├── status_bar.py            # Status bar + progress
│   ├── panels/
│   │   ├── file_panel.py        # Imported documents list
│   │   ├── page_panel.py        # Thumbnail grid + drag-reorder
│   │   ├── toc_panel.py         # TOC editor panel
│   │   └── preview_panel.py     # Zoomable page preview
│   └── dialogs/
│       ├── export_dialog.py          # Merge + save with progress bar
│       ├── toc_detection_dialog.py   # Review auto-detected TOC
│       ├── toc_quick_edit_dialog.py  # Inline TOC entry editor
│       ├── watermark_dialog.py       # Watermark findings + removal
│       └── watermark_multi_dialog.py # Multi-document watermark removal
├── widgets/
│   ├── thumbnail_widget.py      # Drag-source page card
│   ├── toc_tree_widget.py       # Editable TOC tree
│   └── drop_area.py             # PDF drag-and-drop target
├── engines/
│   ├── merge_engine.py          # PDF merge via PyMuPDF
│   ├── page_engine.py           # Stateless page operations
│   ├── toc_engine.py            # Bookmark embed + TOC page render
│   ├── toc_detection_engine.py  # Bookmark + heading heuristic detection
│   └── watermark_engine.py      # Watermark detect + redaction removal
├── models/
│   ├── pdf_document.py          # PDFDocument dataclass
│   ├── page_item.py             # PageItem dataclass
│   ├── toc_entry.py             # TOCEntry dataclass
│   ├── toc_section.py           # TOCSection dataclass
│   └── watermark_result.py      # WatermarkResult dataclass
├── services/
│   ├── project_service.py       # Session state + undo stack
│   ├── export_service.py        # Async merge + bookmark pipeline
│   ├── preview_service.py       # Background thumbnail rendering
│   ├── toc_service.py           # TOC CRUD with signals
│   ├── watermark_service.py     # Async detect + remove
│   ├── undo_stack.py            # Generic command/undo/redo stack
│   └── page_commands.py         # Undoable page operation commands
├── resources/
│   └── themes/
│       ├── dark.qss
│       └── light.qss
└── tests/                       # 318 pytest tests
    ├── test_models.py
    ├── test_page_engine.py
    ├── test_undo_stack.py
    ├── test_merge_engine.py
    ├── test_toc_engine.py
    ├── test_toc_detection_engine.py
    ├── test_toc_service.py
    ├── test_watermark_engine.py
    ├── test_preview_service.py
    ├── test_project_service.py
    └── test_integration.py
```

---

## Architecture

```
GUI Layer  →  Service Layer  →  Engine Layer  →  Models
                                              ↓
                                    PyMuPDF / pypdf / OpenCV
```

See `PLAN.md` for the full design document.

---

## License

MIT
