"""A PDF link discovered on a web page."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum


class PdfStatus(str, Enum):
    """Discovery state. Download progress is stored separately."""

    DISCOVERED = "discovered"
    VALIDATED = "validated"
    FAILED = "failed"


class DownloadStatus(str, Enum):
    """Whether a file has been saved locally. Discovery does not set this."""

    DOWNLOAD_PENDING = "download_pending"
    DOWNLOADED = "downloaded"
    FAILED = "failed"


class DiscoveryMethod(str, Enum):
    """How a PDF candidate was observed."""

    HTML = "HTML"
    API_JSON = "API_JSON"
    PDF_RESPONSE = "PDF_RESPONSE"
    BROWSER_DOWNLOAD = "BROWSER_DOWNLOAD"


class ScanMode(str, Enum):
    """Which discovery sources a scan uses."""

    HTML = "html"
    HTML_API = "html_api"
    INTERACTIVE = "interactive"


_METHOD_ORDER = (
    DiscoveryMethod.HTML,
    DiscoveryMethod.API_JSON,
    DiscoveryMethod.PDF_RESPONSE,
    DiscoveryMethod.BROWSER_DOWNLOAD,
)


@dataclass(frozen=True)
class PdfItem:
    """One PDF candidate.

    The URL keeps its original query string. A fragment is removed only when
    records are merged, because it does not identify a different file.
    """

    id: str
    file_name: str
    url: str
    status: PdfStatus
    source_page_url: str | None = None
    source_endpoint: str | None = None
    discovery_method: str | None = None
    content_type: str | None = None
    http_status: int | None = None
    file_size: int | None = None
    discovered_at: str | None = None
    local_file_path: str | None = None
    error_message: str | None = None
    download_status: DownloadStatus | None = None
    content_hash: str | None = None


def new_pdf_id(url: str) -> str:
    """Return a stable id for a PDF URL so repeat scans refer to the same row."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]


def format_discovery_methods(methods: set[str] | list[str]) -> str | None:
    """Return discovery methods in a stable order, without duplicates."""
    present = {method.strip() for method in methods if method and method.strip()}
    ordered = [method.value for method in _METHOD_ORDER if method.value in present]
    extras = sorted(present.difference(ordered))
    combined = ordered + extras
    if not combined:
        return None
    return ", ".join(combined)


def parse_status(value: str | None) -> PdfStatus:
    """Map a stored status to the discovery enum. Unknown values stay discovered."""
    if not value:
        return PdfStatus.DISCOVERED
    try:
        return PdfStatus(value)
    except ValueError:
        return PdfStatus.DISCOVERED


def parse_download_status(value: str | None) -> DownloadStatus | None:
    """Map a stored download status. Unknown values are left unset."""
    if not value:
        return None
    try:
        return DownloadStatus(value)
    except ValueError:
        return None
