"""Coordinate validation, scanning, and local persistence."""

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from config import Settings, get_settings
from database.database import Database
from models.pdf_item import PdfItem, PdfStatus
from scraper.website_scanner import PageScanResult, ScanError, WebsiteScanner
from utils.logger import get_logger
from utils.url_utils import redact_url, validate_website_url


@dataclass(frozen=True)
class ScanOutcome:
    """Result returned to the Streamlit UI. Errors are already user-facing."""

    pdfs: list[PdfItem]
    error: str | None = None
    info: str | None = None
    page_url: str | None = None


class ScannerService:
    """Application service used by the local UI."""

    def __init__(
        self,
        scanner: WebsiteScanner | None = None,
        database: Database | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._scanner = scanner or WebsiteScanner(self._settings)
        self._database = database or Database(self._settings.database_path)
        self._logger = get_logger(__name__)

    def prepare_storage(self) -> str | None:
        """Create the SQLite database. Returns a user-facing warning if setup fails."""
        try:
            self._database.initialize()
        except Exception:
            self._logger.exception("Database initialization failed")
            return "Local storage could not be initialized. Discovered PDFs may not be saved."
        return None

    def scan(self, raw_url: str) -> ScanOutcome:
        """Validate a URL, scan that page, and store any PDFs that were found."""
        url = (raw_url or "").strip()
        validation_error = validate_website_url(url)
        if validation_error:
            self._logger.warning("Rejected URL input: %s", validation_error)
            return ScanOutcome(pdfs=[], error=validation_error)

        try:
            result = _run_blocking(self._scanner.scan, url)
        except ScanError as exc:
            self._logger.warning("Scan failed for %s: %s", redact_url(url), exc.message)
            return ScanOutcome(pdfs=[], error=exc.message)
        except Exception:
            self._logger.exception("Unexpected error scanning %s", redact_url(url))
            return ScanOutcome(pdfs=[], error="Something went wrong while scanning this website.")

        items = [
            PdfItem(file_name=pdf.file_name, url=pdf.url, status=PdfStatus.DISCOVERED)
            for pdf in result.pdfs
        ]
        if not items:
            self._logger.info("No PDFs found on %s", redact_url(result.final_url))
            return ScanOutcome(
                pdfs=[],
                info="No PDFs were found on this page.",
                page_url=result.final_url,
            )

        self._persist(items, result.final_url)
        return ScanOutcome(pdfs=items, page_url=result.final_url)

    def _persist(self, items: list[PdfItem], page_url: str) -> None:
        try:
            self._database.initialize()
            self._database.save_discovered(items, source_page=page_url)
        except Exception:
            self._logger.exception("Could not save discovered PDFs for %s", redact_url(page_url))


def _run_blocking(func: Callable[[str], PageScanResult], url: str) -> PageScanResult:
    """Run Playwright outside a Streamlit asyncio loop when one is already active."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return func(url)

    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(func, url).result()
