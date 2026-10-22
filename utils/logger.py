"""File and console logging for scan diagnostics."""

import logging
from logging.handlers import RotatingFileHandler

from config import get_settings

_APP_LOGGER_NAME = "pdf_scraper"
_LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"


def get_logger(name: str) -> logging.Logger:
    """Return a logger under the application hierarchy."""
    _configure()
    if name == "__main__":
        qualified_name = _APP_LOGGER_NAME
    elif name.startswith(f"{_APP_LOGGER_NAME}."):
        qualified_name = name
    else:
        qualified_name = f"{_APP_LOGGER_NAME}.{name}"
    return logging.getLogger(qualified_name)


def _configure() -> None:
    logger = logging.getLogger(_APP_LOGGER_NAME)
    if logger.handlers:
        return

    logger.setLevel(logging.INFO)
    logger.propagate = False
    formatter = logging.Formatter(_LOG_FORMAT)
    logger.addHandler(_build_file_handler(formatter))

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)


def _build_file_handler(formatter: logging.Formatter) -> logging.Handler:
    settings = get_settings()
    try:
        settings.log_dir.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = RotatingFileHandler(
            settings.log_dir / settings.log_file_name,
            maxBytes=1_000_000,
            backupCount=3,
            encoding="utf-8",
        )
    except OSError:
        handler = logging.StreamHandler()
    handler.setLevel(logging.INFO)
    handler.setFormatter(formatter)
    return handler
