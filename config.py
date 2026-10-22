"""Application settings for the local PDF scraper."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    """Runtime configuration. Paths are resolved from this file, not the shell cwd."""

    app_name: str
    base_dir: Path
    data_dir: Path
    database_path: Path
    download_dir: Path
    log_dir: Path
    log_file_name: str
    playwright_timeout_ms: int
    network_idle_timeout_ms: int
    content_type_timeout_ms: int
    max_content_type_checks: int
    browser_headless: bool
    viewport_width: int
    viewport_height: int


def get_settings() -> Settings:
    """Return the default settings for a local Windows or development run."""
    base_dir = Path(__file__).resolve().parent
    data_dir = base_dir / "data"
    return Settings(
        app_name="PDF Scraper",
        base_dir=base_dir,
        data_dir=data_dir,
        database_path=data_dir / "pdf_scraper.db",
        download_dir=data_dir / "downloads",
        log_dir=data_dir / "logs",
        log_file_name="pdf_scraper.log",
        playwright_timeout_ms=30_000,
        network_idle_timeout_ms=8_000,
        content_type_timeout_ms=5_000,
        max_content_type_checks=10,
        browser_headless=True,
        viewport_width=1280,
        viewport_height=720,
    )
