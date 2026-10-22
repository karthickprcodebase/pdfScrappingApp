"""Validate a URL, scan that page, and store the PDF links."""

from __future__ import annotations

import threading
from dataclasses import dataclass

from config import Settings
from database.database import Database
from models.pdf_item import PdfItem, ScanMode
from services.download_service import DownloadBatch, download_pdfs
from scraper.pagination import PaginationOptions, PaginationProgress
from scraper.website_scanner import (
    InteractiveSession,
    PaginationSummary,
    ScanError,
    WebsiteScanner,
)
from utils.logger import get_logger
from utils.url_guard import UrlBlockedError, ensure_scan_allowed
from utils.url_utils import redact_url, safe_for_log, validate_http_url


@dataclass(frozen=True)
class ScanResult:
    """PDF links from one scan, plus a warning if local storage failed."""

    pdfs: tuple[PdfItem, ...]
    scanned_url: str
    save_warning: str | None = None
    duplicates_removed: int = 0
    failed_inspections: int = 0
    mode: ScanMode = ScanMode.HTML
    pagination: PaginationSummary | None = None


class ScannerService:
    """Coordinate validation, scanning, and persistence for one page scan."""

    def __init__(
        self,
        settings: Settings,
        database: Database,
        scanner: WebsiteScanner | None = None,
    ) -> None:
        self._settings = settings
        self._database = database
        self._scanner = scanner if scanner is not None else WebsiteScanner(settings)
        self._logger = get_logger(__name__)

    def scan_website(
        self,
        raw_url: str,
        mode: ScanMode = ScanMode.HTML,
        pagination: PaginationOptions | None = None,
        progress: PaginationProgress | None = None,
    ) -> ScanResult:
        """Scan one website URL and persist the PDF links found for that mode."""
        if mode is ScanMode.INTERACTIVE:
            raise ScanError("Open the interactive browser, then finish the scan from the page.")
        url = self._prepare(raw_url)
        logged_url = redact_url(url)
        self._logger.info("Starting %s scan of %s", mode.value, logged_url)
        try:
            outcome = self._scanner.scan(url, mode, pagination, progress)
        except ScanError:
            self._logger.info("Scan did not complete for %s", logged_url)
            raise
        except Exception as exc:
            self._logger.exception("Unexpected scan error for %s", logged_url)
            raise ScanError("The website could not be scanned.") from exc
        return self._persist(
            url,
            mode,
            outcome.pdfs,
            outcome.duplicates_removed,
            outcome.failed_inspections,
            outcome.pagination,
        )

    def save_downloads(self, items: list[PdfItem]) -> DownloadBatch:
        """Save the chosen PDFs into the local downloads folder and record the result."""
        batch = download_pdfs(
            items,
            self._settings.download_dir,
            allow_private_networks=self._settings.allow_private_networks,
            timeout_seconds=max(1, self._settings.playwright_timeout_ms / 1000),
            max_bytes=self._settings.max_download_bytes,
        )
        for item in batch.items:
            try:
                self._database.record_download(item)
            except Exception:
                self._logger.exception("Could not record a download for %s", redact_url(item.url))
        return batch

    def begin_pagination(
        self,
        raw_url: str,
        mode: ScanMode,
        options: PaginationOptions,
    ) -> PaginationScan:
        """Start a paginated scan on a background thread so the page can show Stop."""
        if mode is ScanMode.INTERACTIVE:
            raise ScanError("Open the interactive browser, then finish the scan from the page.")
        url = self._prepare(raw_url)
        scan = PaginationScan(self, url, mode, options.clamped())
        scan.start()
        return scan

    def begin_interactive(self, raw_url: str) -> InteractiveSession:
        """Validate the URL and open a visible browser window."""
        url = self._prepare(raw_url)
        session = self._scanner.open_interactive(url)
        timeout = self._settings.playwright_timeout_ms / 1000 + 15
        if not session.wait_until_ready(timeout):
            try:
                session.finish()
            except ScanError:
                raise
            except Exception as exc:
                raise ScanError("The browser window did not open.") from exc
            raise ScanError("The browser window did not open in time.")
        if session.error is not None:
            if isinstance(session.error, ScanError):
                raise session.error
            raise ScanError("The browser window could not be opened.") from session.error
        return session

    def complete_interactive(self, session: InteractiveSession) -> ScanResult:
        """Close the interactive browser and store the PDFs it observed."""
        try:
            outcome = session.finish()
        except ScanError:
            raise
        except Exception as exc:
            self._logger.exception("Interactive scan failed")
            raise ScanError("The interactive scan could not be completed.") from exc
        return self._persist(
            session.page_url,
            ScanMode.INTERACTIVE,
            outcome.pdfs,
            outcome.duplicates_removed,
            outcome.failed_inspections,
            outcome.pagination,
        )

    def _prepare(self, raw_url: str) -> str:
        try:
            url = validate_http_url(raw_url)
        except ValueError:
            self._logger.info("URL validation failed for input: %s", safe_for_log(raw_url))
            raise
        try:
            ensure_scan_allowed(url, allow_private_networks=self._settings.allow_private_networks)
        except UrlBlockedError:
            self._logger.info("Blocked scan target: %s", safe_for_log(url))
            raise
        return url

    def _persist(
        self,
        url: str,
        mode: ScanMode,
        pdfs: list[PdfItem],
        duplicates_removed: int,
        failed_inspections: int,
        pagination: PaginationSummary | None = None,
    ) -> ScanResult:
        logged_url = redact_url(url)
        save_warning: str | None = None
        try:
            self._database.save_discovered(pdfs, source_url=url)
        except Exception:
            self._logger.exception("Could not save scan results for %s", logged_url)
            save_warning = (
                "The PDFs were found, but they could not be saved to the local database."
            )
        self._logger.info("Scan finished for %s with %s PDF link(s)", logged_url, len(pdfs))
        return ScanResult(
            pdfs=tuple(pdfs),
            scanned_url=url,
            save_warning=save_warning,
            duplicates_removed=duplicates_removed,
            failed_inspections=failed_inspections,
            mode=mode,
            pagination=pagination,
        )


class PaginationScan:
    """One paginated scan running outside the Streamlit script thread."""

    def __init__(
        self,
        service: ScannerService,
        url: str,
        mode: ScanMode,
        options: PaginationOptions,
    ) -> None:
        self.progress = PaginationProgress(initial_url=url, current_url=url, status="Starting")
        self.result: ScanResult | None = None
        self.error: BaseException | None = None
        self.max_pages = options.max_pages
        self._service = service
        self._url = url
        self._mode = mode
        self._options = options
        self._stop = threading.Event()
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._run, name="pdf-pagination-scan", daemon=True)

    def start(self) -> None:
        """Start the scan thread."""
        self._thread.start()

    def request_stop(self) -> None:
        """Ask the scan to finish after the page it is on."""
        self._stop.set()
        self.progress.status = "Stopping"

    @property
    def done(self) -> bool:
        """Return True once the thread has stored a result or an error."""
        return self._done.is_set()

    def _run(self) -> None:
        try:
            outcome = self._service._scanner.collect(
                self._url,
                self._mode,
                self._stop,
                None,
                self._options,
                self.progress,
            )
            self.result = self._service._persist(
                self._url,
                self._mode,
                outcome.pdfs,
                outcome.duplicates_removed,
                outcome.failed_inspections,
                outcome.pagination,
            )
        except Exception as exc:
            self.error = exc
        finally:
            self._done.set()
