"""
main.py — Application entry point for NeatPDF.

Usage::

    python main.py
"""

import sys
import logging

# Bootstrap logging before importing anything else
from logger import setup_logging
setup_logging()

log = logging.getLogger(__name__)

from app import create_app  # noqa: E402
from gui.main_window import MainWindow  # noqa: E402


def main() -> int:
    """Create the application and run the event loop.

    Returns:
        Exit code (0 = success).
    """
    log.info("Starting NeatPDF")

    app, theme = create_app()

    window = MainWindow(theme=theme)
    window.show()

    exit_code = app.exec()
    log.info("NeatPDF exited with code %d", exit_code)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
