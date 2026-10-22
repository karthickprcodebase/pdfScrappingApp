"""Application settings.

Paths are resolved from this file during development and from the executable
folder when the app is later packaged. Nothing here is a machine-specific
absolute path.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


def get_base_dir() -> Path:
    """Return the folder that should contain the local data directory."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


@dataclass(frozen=True)
class Settings:
    """Immutable runtime settings."""

    app_name: str
    base_dir: Path
    database_path: Path
    download_dir: Path
    log_dir: Path
    log_file_name: str
    playwright_timeout_ms: int
    network_idle_timeout_ms: int
    headless: bool
    max_content_type_checks: int
    content_type_timeout_ms: int
    max_json_bytes: int
    max_json_responses: int
    max_json_depth: int
    max_json_nodes: int
    max_pdf_validation_bytes: int
    interactive_timeout_seconds: int
    max_download_bytes: int
    allow_private_networks: bool


def load_settings() -> Settings:
    """Build settings from the application directory and the built-in defaults."""
    base_dir = get_base_dir()
    data_dir = base_dir / "data"
    return Settings(
        app_name="PDF Scraper",
        base_dir=base_dir,
        database_path=data_dir / "pdf_scraper.db",
        download_dir=data_dir / "downloads",
        log_dir=data_dir / "logs",
        log_file_name="pdf_scraper.log",
        playwright_timeout_ms=30_000,
        network_idle_timeout_ms=8_000,
        headless=True,
        max_content_type_checks=10,
        content_type_timeout_ms=5_000,
        max_json_bytes=2_000_000,
        max_json_responses=20,
        max_json_depth=40,
        max_json_nodes=10_000,
        max_pdf_validation_bytes=256_000,
        interactive_timeout_seconds=600,
        max_download_bytes=50 * 1024 * 1024,
        allow_private_networks=_env_flag("PDF_SCRAPER_ALLOW_PRIVATE_NETWORKS"),
    )


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def ensure_runtime_directories(settings: Settings) -> None:
    """Create local folders the application needs. Existing folders are left as they are."""
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    settings.download_dir.mkdir(parents=True, exist_ok=True)
    settings.log_dir.mkdir(parents=True, exist_ok=True)
