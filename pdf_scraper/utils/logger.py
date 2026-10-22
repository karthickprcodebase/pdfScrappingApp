"""Application logging.

User-facing screens receive short messages. Diagnostic detail, including
tracebacks from unexpected failures, stays in the log file.
"""

from __future__ import annotations

import logging

from config import Settings

_NAMESPACE = "pdf_scraper"


def configure_logging(settings: Settings) -> None:
    """Attach file and console handlers once. Later calls keep the same handlers."""
    logger = logging.getLogger(_NAMESPACE)
    if logger.handlers:
        return

    logger.setLevel(logging.INFO)
    logger.propagate = False
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    try:
        settings.log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(
            settings.log_dir / settings.log_file_name,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except OSError:
        logger.warning("File logging is unavailable. Console logging will continue.")


def get_logger(name: str) -> logging.Logger:
    """Return a logger under the application namespace."""
    if name == _NAMESPACE or name.startswith(f"{_NAMESPACE}."):
        return logging.getLogger(name)
    return logging.getLogger(f"{_NAMESPACE}.{name}")
