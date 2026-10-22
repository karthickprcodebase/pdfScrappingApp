"""Open one web page with Playwright and collect its links."""

from dataclasses import dataclass

from bs4 import BeautifulSoup

from config import Settings, get_settings
from scraper.pdf_detector import (
    ContentTypeLookup,
    DetectedPdf,
    PdfDetector,
    filename_from_content_disposition,
    indicates_pdf_response,
)
from utils.logger import get_logger
from utils.url_utils import filename_from_url, redact_url, resolve_url


class ScanError(Exception):
    """A scan failure with a message that is safe to show in the UI."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class PageLink:
    """An anchor collected from the rendered page."""

    url: str
    text: str


@dataclass(frozen=True)
class PageScanResult:
    """PDFs found on the page that was opened."""

    final_url: str
    pdfs: list[DetectedPdf]


class WebsiteScanner:
    """Scan a single page. This does not crawl other pages or download files."""

    def __init__(
        self,
        settings: Settings | None = None,
        detector: PdfDetector | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._detector = detector or PdfDetector(self._settings.max_content_type_checks)
        self._logger = get_logger(__name__)

    def scan(self, url: str) -> PageScanResult:
        """Open the URL and return PDF links discovered on that page."""
        self._logger.info("Starting scan for %s", redact_url(url))
        try:
            return self._scan_with_browser(url)
        except ScanError:
            raise
        except Exception as exc:
            self._logger.exception("Unexpected scraping failure for %s", redact_url(url))
            raise ScanError("The page could not be scanned.") from exc

    def _scan_with_browser(self, url: str) -> PageScanResult:
        sync_playwright, playwright_error, playwright_timeout = _import_playwright()
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(headless=self._settings.browser_headless)
            except playwright_error as exc:
                self._logger.warning("Browser startup failed: %s", exc)
                raise _startup_error(exc) from exc

            try:
                downloads: list[object] = []
                context = browser.new_context(
                    viewport={
                        "width": self._settings.viewport_width,
                        "height": self._settings.viewport_height,
                    },
                    accept_downloads=True,
                )
                page = context.new_page()
                page.on("download", downloads.append)
                try:
                    response = self._goto(page, playwright_timeout, playwright_error, url)
                except playwright_error as exc:
                    if _is_download(exc):
                        pdfs = self._pdfs_from_download(url, downloads)
                        if pdfs:
                            self._logger.info("Navigation identified a PDF download for %s", redact_url(url))
                            return PageScanResult(final_url=url, pdfs=pdfs)
                        self._logger.info("Navigation started a non-PDF download for %s", redact_url(url))
                        raise ScanError("The page started a download instead of opening a web page.") from exc
                    raise

                final_url = page.url or url
                if final_url.startswith(("about:", "chrome-error:")):
                    raise ScanError("The page could not be loaded.")

                content_type = _header(response, "content-type")
                content_disposition = _header(response, "content-disposition")
                if indicates_pdf_response(content_type, content_disposition):
                    self._logger.info("Page itself is a PDF: %s", redact_url(final_url))
                    return PageScanResult(
                        final_url=final_url,
                        pdfs=self._pdfs_from_document(final_url, content_disposition),
                    )

                self._wait_for_network_idle(page, playwright_timeout, playwright_error)
                links = extract_anchor_links(page.content(), final_url)
                self._logger.info("Collected %s anchors from %s", len(links), redact_url(final_url))
                pdfs = self._detector.detect(
                    links,
                    _content_type_lookup(context.request, self._settings.content_type_timeout_ms, self._logger),
                )
                self._logger.info("Detected %s PDF links", len(pdfs))
                return PageScanResult(final_url=final_url, pdfs=pdfs)
            except ScanError:
                raise
            except playwright_error as exc:
                self._logger.warning("Navigation failed for %s: %s", redact_url(url), exc)
                raise _navigation_error(exc) from exc
            finally:
                _cancel_downloads(downloads)
                browser.close()

    def _goto(self, page: object, timeout_error: type[Exception], playwright_error: type[Exception], url: str) -> object:
        try:
            response = page.goto(  # type: ignore[attr-defined]
                url,
                wait_until="load",
                timeout=self._settings.playwright_timeout_ms,
            )
        except timeout_error as exc:
            self._logger.warning("Page load timed out for %s", redact_url(url))
            raise ScanError("The page took too long to load. Check the URL and try again.") from exc
        except playwright_error:
            raise

        if response is None:
            raise ScanError("The page could not be loaded.")
        status = int(response.status)
        if status >= 400:
            self._logger.warning("Page returned HTTP %s for %s", status, redact_url(url))
            raise ScanError(f"The website returned an error (HTTP {status}).")
        return response

    def _wait_for_network_idle(
        self,
        page: object,
        timeout_error: type[Exception],
        playwright_error: type[Exception],
    ) -> None:
        try:
            page.wait_for_load_state("networkidle", timeout=self._settings.network_idle_timeout_ms)  # type: ignore[attr-defined]
        except timeout_error:
            self._logger.info("Network idle timed out; continuing with the loaded page")
        except playwright_error as exc:
            self._logger.info("Stopped waiting for network idle: %s", exc)

    def _pdfs_from_url(self, url: str) -> list[DetectedPdf]:
        return self._detector.detect([PageLink(url=url, text="")])

    def _pdfs_from_download(self, url: str, downloads: list[object]) -> list[DetectedPdf]:
        suggested = _suggested_filename(downloads)
        if suggested.lower().endswith(".pdf"):
            safe_name = filename_from_url(f"https://download.local/{suggested}")
            return [DetectedPdf(url=url, file_name=safe_name or filename_from_url(url))]
        return self._pdfs_from_url(url)

    def _pdfs_from_document(self, url: str, content_disposition: str | None) -> list[DetectedPdf]:
        named = filename_from_content_disposition(content_disposition)
        detected = self._pdfs_from_url(url)
        file_name = named or (detected[0].file_name if detected else filename_from_url(url))
        return [DetectedPdf(url=url, file_name=file_name)]


def extract_anchor_links(html: str, page_url: str) -> list[PageLink]:
    """Parse rendered HTML and resolve anchor hrefs to absolute URLs."""
    soup = BeautifulSoup(html, "html.parser")
    base_tag = soup.find("base", href=True)
    base_url = page_url
    if base_tag is not None:
        resolved_base = resolve_url(page_url, str(base_tag.get("href", "")))
        if resolved_base:
            base_url = resolved_base

    links: list[PageLink] = []
    for anchor in soup.find_all("a", href=True):
        absolute = resolve_url(base_url, str(anchor.get("href", "")))
        if absolute is None:
            continue
        links.append(PageLink(url=absolute, text=anchor.get_text(" ", strip=True)))
    return links


def _import_playwright() -> tuple[object, type[Exception], type[Exception]]:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ScanError(
            "Playwright is not installed. Install dependencies, then run: python -m playwright install chromium"
        ) from exc
    return sync_playwright, PlaywrightError, PlaywrightTimeoutError


def _content_type_lookup(request_context: object, timeout_ms: int, logger: object) -> ContentTypeLookup:
    def lookup(target: str) -> tuple[str | None, str | None]:
        try:
            response = request_context.head(target, timeout=timeout_ms)  # type: ignore[attr-defined]
        except Exception:
            logger.info("Content-Type check failed for %s", redact_url(target))  # type: ignore[attr-defined]
            return None, None
        if int(response.status) >= 400:
            return None, None
        return _header(response, "content-type"), _header(response, "content-disposition")

    return lookup


def _header(response: object | None, name: str) -> str | None:
    if response is None:
        return None
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    try:
        value = headers.get(name)
    except Exception:
        return None
    if value is None:
        return None
    return str(value)


def _cancel_downloads(downloads: list[object]) -> None:
    for download in downloads:
        cancel = getattr(download, "cancel", None)
        if not callable(cancel):
            continue
        try:
            cancel()
        except Exception:
            continue


def _suggested_filename(downloads: list[object]) -> str:
    if not downloads:
        return ""
    return str(getattr(downloads[0], "suggested_filename", "") or "")


def _is_download(exc: Exception) -> bool:
    return "download is starting" in str(exc).lower()


def _startup_error(exc: Exception) -> ScanError:
    text = str(exc)
    if "Executable doesn't exist" in text or "playwright install" in text.lower():
        return ScanError(
            "The browser could not be started. Install Chromium with: python -m playwright install chromium"
        )
    return ScanError("The browser could not be started.")


_UNREACHABLE_MARKERS = (
    "ERR_NAME_NOT_RESOLVED",
    "ERR_CONNECTION_REFUSED",
    "ERR_CONNECTION_RESET",
    "ERR_CONNECTION_TIMED_OUT",
    "ERR_ADDRESS_UNREACHABLE",
    "ERR_INTERNET_DISCONNECTED",
    "ERR_NETWORK_CHANGED",
    "ERR_CONNECTION_CLOSED",
    "ERR_EMPTY_RESPONSE",
    "ERR_TIMED_OUT",
    "ERR_TUNNEL",
)


def _navigation_error(exc: Exception) -> ScanError:
    text = str(exc)
    if "ERR_CERT" in text or "ERR_SSL" in text:
        return ScanError("The page could not be loaded because the site certificate could not be verified.")
    if any(marker in text for marker in _UNREACHABLE_MARKERS):
        return ScanError("The website could not be reached. Check the URL and your network connection.")
    return ScanError("The page could not be loaded. It may be unavailable or taking too long to respond.")
