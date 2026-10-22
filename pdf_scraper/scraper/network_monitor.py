"""Watch Playwright network traffic for PDF and JSON API responses."""

from __future__ import annotations

import json
from urllib.parse import urlparse

from playwright.sync_api import Page, Response

from config import Settings
from models.pdf_item import DiscoveryMethod, PdfStatus
from scraper.dedup import PdfCandidate
from scraper.json_pdf_parser import is_json_content_type, parse_json_pdf_links
from scraper.pagination import ApiPageHint, extract_api_page_hint
from scraper.response_assessment import ResponseVerdict, assess_pdf_response
from utils.logger import get_logger
from utils.url_guard import destination_is_blocked
from utils.url_utils import extract_pdf_filename, is_pdf_url, redact_url

_SKIPPED_RESOURCE_TYPES = {"image", "stylesheet", "font", "media"}


class NetworkMonitor:
    """Collect PDF candidates from responses that the browser already receives."""

    def __init__(self, settings: Settings, source_page_url: str) -> None:
        self._settings = settings
        self._source_page_url = source_page_url
        self._logger = get_logger(__name__)
        self.candidates: list[PdfCandidate] = []
        self.failed_inspections = 0
        self.rate_limited = False
        self._seen_responses: set[int] = set()
        self._seen_downloads: set[int] = set()
        self._json_responses: list[Response] = []
        self._pdf_responses: list[Response] = []
        self._api_hints: list[ApiPageHint] = []

    def attach(self, page: Page) -> None:
        """Register listeners. Call this before navigation."""
        page.on("response", self._on_response)
        page.on("requestfailed", self._on_request_failed)
        page.on("download", self._on_download)

    def set_source_page(self, url: str) -> None:
        """Update the page URL after redirects."""
        self._source_page_url = url

    def drain(self) -> None:
        """Read queued JSON and small PDF bodies. Safe to call after every page."""
        pending_pdfs = self._pdf_responses
        self._pdf_responses = []
        for response in pending_pdfs:
            self._finalize_pdf_response(response)
        pending_json = self._json_responses
        self._json_responses = []
        for response in pending_json:
            self._read_json_response(response)

    def consume_api_hints(self) -> list[ApiPageHint]:
        """Return pagination hints observed since the last call."""
        hints = self._api_hints
        self._api_hints = []
        return hints

    def ingest_fetched_body(
        self,
        url: str,
        http_status: int,
        content_type: str | None,
        body: bytes | None,
    ) -> None:
        """Record a response fetched through the browser session for API pagination."""
        if http_status == 429:
            self.rate_limited = True
            self.failed_inspections += 1
            return
        if http_status >= 400:
            self.failed_inspections += 1
            return
        if is_json_content_type(content_type) and body is not None:
            self._consume_json_text(url, http_status, content_type, body)
            return
        verdict = assess_pdf_response(
            url=url,
            http_status=http_status,
            content_type=content_type,
            body_prefix=body[:8] if body else None,
        )
        if not verdict.accept:
            return
        self.candidates.append(
            PdfCandidate(
                url=url,
                file_name=verdict.file_name or extract_pdf_filename(url),
                discovery_method=DiscoveryMethod.PDF_RESPONSE,
                source_page_url=self._source_page_url,
                content_type=verdict.content_type or content_type,
                http_status=http_status,
                status=verdict.status,
            )
        )

    def _on_response(self, response: Response) -> None:
        response_id = id(response)
        if response_id in self._seen_responses:
            return
        self._seen_responses.add(response_id)
        try:
            self._inspect_response(response)
        except Exception:
            self.failed_inspections += 1
            self._logger.info("Skipped a network response that could not be inspected")

    def _on_request_failed(self, request: object) -> None:
        try:
            url = getattr(request, "url", "")
            if isinstance(url, str) and is_pdf_url(url):
                self.failed_inspections += 1
                self._logger.info("A PDF request failed for %s", redact_url(url))
        except Exception:
            self.failed_inspections += 1

    def _on_download(self, download: object) -> None:
        download_id = id(download)
        if download_id in self._seen_downloads:
            return
        self._seen_downloads.add(download_id)
        try:
            url = str(getattr(download, "url", "") or "")
            file_name = str(getattr(download, "suggested_filename", "") or "")
            if self._url_is_blocked(url):
                self.failed_inspections += 1
            elif url and _download_looks_like_pdf(file_name, url):
                self.candidates.append(
                    PdfCandidate(
                        url=url,
                        file_name=file_name or extract_pdf_filename(url),
                        discovery_method=DiscoveryMethod.BROWSER_DOWNLOAD,
                        source_page_url=self._source_page_url,
                        status=PdfStatus.DISCOVERED,
                    )
                )
            cancel = getattr(download, "cancel", None)
            if callable(cancel):
                cancel()
        except Exception:
            self.failed_inspections += 1
            self._logger.info("A browser download event could not be recorded")

    def _inspect_response(self, response: Response) -> None:
        url = response.url
        if not url.lower().startswith(("http://", "https://")):
            return
        if self._url_is_blocked(url):
            if is_pdf_url(url) or is_json_content_type(response.headers.get("content-type")):
                self.failed_inspections += 1
            return
        resource_type = ""
        try:
            resource_type = response.request.resource_type
        except Exception:
            resource_type = ""
        content_type = response.headers.get("content-type")
        if resource_type in _SKIPPED_RESOURCE_TYPES and not _header_is_pdf(content_type):
            return

        status = response.status
        disposition = response.headers.get("content-disposition")
        content_length = _content_length(response.headers.get("content-length"))
        verdict = assess_pdf_response(
            url=url,
            http_status=status,
            content_type=content_type,
            content_disposition=disposition,
            body_prefix=None,
        )
        if verdict.failed and not verdict.accept:
            self.failed_inspections += 1
            self._logger.info("PDF response was not usable (%s) for %s", status, redact_url(url))
            return
        if verdict.accept:
            if content_length is not None and content_length <= self._settings.max_pdf_validation_bytes:
                self._pdf_responses.append(response)
            else:
                self._add_pdf_candidate(response, verdict, content_length)
            return

        if _should_queue_json(content_type, url) and len(self._json_responses) < self._settings.max_json_responses:
            if content_length is not None and content_length > self._settings.max_json_bytes:
                self.failed_inspections += 1
                self._logger.info("Skipped a JSON response larger than the configured limit")
                return
            self._json_responses.append(response)

    def _read_json_response(self, response: Response) -> None:
        logged = redact_url(response.url)
        try:
            body = response.body()
        except Exception:
            self.failed_inspections += 1
            self._logger.info("Could not read a JSON response from %s", logged)
            return
        if len(body) > self._settings.max_json_bytes:
            self.failed_inspections += 1
            self._logger.info("Skipped a JSON response larger than the configured limit from %s", logged)
            return
        request_url = response.url
        try:
            request_url = response.request.url or response.url
        except Exception:
            request_url = response.url
        self._consume_json_text(
            request_url,
            response.status,
            response.headers.get("content-type"),
            body,
        )

    def _consume_json_text(
        self,
        request_url: str,
        http_status: int,
        content_type: str | None,
        body: bytes,
    ) -> None:
        logged = redact_url(request_url)
        if http_status == 429:
            self.rate_limited = True
            self.failed_inspections += 1
            self._logger.info("API pagination stopped after HTTP 429 from %s", logged)
            return
        try:
            text = body.decode("utf-8", errors="replace")
            payload = json.loads(text)
            links = parse_json_pdf_links(
                payload,
                request_url,
                max_depth=self._settings.max_json_depth,
                max_nodes=self._settings.max_json_nodes,
            )
            hint = extract_api_page_hint(payload, request_url, http_status)
        except Exception:
            self.failed_inspections += 1
            self._logger.info("A response from %s was not usable JSON", logged)
            return
        if hint is not None:
            self._api_hints.append(hint)
        for link in links:
            if self._url_is_blocked(link.url):
                self.failed_inspections += 1
                continue
            self.candidates.append(
                PdfCandidate(
                    url=link.url,
                    file_name=link.file_name,
                    discovery_method=DiscoveryMethod.API_JSON,
                    source_page_url=self._source_page_url,
                    source_endpoint=request_url,
                    content_type=content_type,
                    http_status=http_status,
                    status=PdfStatus.DISCOVERED,
                )
            )

    def _finalize_pdf_response(self, response: Response) -> None:
        content_type = response.headers.get("content-type")
        disposition = response.headers.get("content-disposition")
        content_length = _content_length(response.headers.get("content-length"))
        prefix: bytes | None = None
        try:
            body = response.body()
        except Exception:
            body = None
        if body is not None and len(body) <= self._settings.max_pdf_validation_bytes:
            prefix = body[:8]
        verdict = assess_pdf_response(
            url=response.url,
            http_status=response.status,
            content_type=content_type,
            content_disposition=disposition,
            body_prefix=prefix,
        )
        if not verdict.accept:
            self.failed_inspections += 1
            self._logger.info("A response was not a PDF file: %s", redact_url(response.url))
            return
        self._add_pdf_candidate(response, verdict, content_length)

    def _add_pdf_candidate(
        self,
        response: Response,
        verdict: ResponseVerdict,
        content_length: int | None,
    ) -> None:
        file_name = verdict.file_name or extract_pdf_filename(response.url)
        content_type = verdict.content_type or response.headers.get("content-type")
        status = verdict.status
        self.candidates.append(
            PdfCandidate(
                url=response.url,
                file_name=file_name,
                discovery_method=DiscoveryMethod.PDF_RESPONSE,
                source_page_url=self._source_page_url,
                content_type=content_type,
                http_status=response.status,
                file_size=content_length,
                status=status,
            )
        )

    def _url_is_blocked(self, url: str) -> bool:
        if self._settings.allow_private_networks:
            return False
        if not url:
            return True
        return destination_is_blocked(url)


def _download_looks_like_pdf(file_name: str, url: str) -> bool:
    if file_name.lower().endswith(".pdf"):
        return True
    return bool(url) and is_pdf_url(url)


def _header_is_pdf(content_type: str | None) -> bool:
    if not content_type:
        return False
    media = content_type.split(";", 1)[0].strip().lower()
    return media in {"application/pdf", "application/x-pdf"}


def _should_queue_json(content_type: str | None, url: str) -> bool:
    if is_json_content_type(content_type):
        return True
    path = urlparse(url).path.lower()
    media = (content_type or "").split(";", 1)[0].strip().lower()
    return path.endswith(".json") and media in {"", "text/plain"}


def _content_length(value: str | None) -> int | None:
    if not value:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    if parsed < 0:
        return None
    return parsed
