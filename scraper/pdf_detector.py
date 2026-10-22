"""Identify PDF links without downloading the files."""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import parse_qs, unquote, urlparse

from utils.url_utils import dedupe_key, filename_from_url

_PDF_QUERY_KEYS = {"format", "type", "filetype", "contenttype", "content_type", "mime"}
_PDF_QUERY_VALUE = re.compile(r"(?:^|[/=])[^/?#&=]*\.pdf(?:$|[&])", re.IGNORECASE)
_DISPOSITION_FILENAME = re.compile(r"filename\*?=(?:UTF-8''|\"?)([^\";]+)", re.IGNORECASE)

ContentTypeLookup = Callable[[str], tuple[str | None, str | None]]


class LinkLike(Protocol):
    """Minimum link data needed for PDF detection."""

    url: str
    text: str


@dataclass(frozen=True)
class DetectedPdf:
    """A unique PDF URL and a display filename. The URL is kept unchanged."""

    url: str
    file_name: str


class PdfDetector:
    """Detect PDF links from anchors on one page."""

    def __init__(self, max_content_type_checks: int = 10) -> None:
        self._max_content_type_checks = max_content_type_checks

    def detect(
        self,
        links: Sequence[LinkLike],
        content_type_for: ContentTypeLookup | None = None,
    ) -> list[DetectedPdf]:
        """Return unique PDF links in the order they were found."""
        found: list[DetectedPdf] = []
        seen: set[str] = set()
        probes_remaining = self._max_content_type_checks

        for link in links:
            key = dedupe_key(link.url)
            if key in seen:
                continue
            if is_direct_pdf_url(link.url):
                _add(found, seen, link.url)
                continue
            if content_type_for is None or probes_remaining <= 0 or not _mentions_pdf(link):
                continue

            probes_remaining -= 1
            try:
                content_type, content_disposition = content_type_for(link.url)
            except Exception:
                continue
            if indicates_pdf_response(content_type, content_disposition):
                filename = filename_from_content_disposition(content_disposition) or filename_from_url(link.url)
                _add(found, seen, link.url, filename)

        return found


def is_direct_pdf_url(url: str) -> bool:
    """True when the URL path or query string already identifies a PDF."""
    parsed = urlparse(url)
    if unquote(parsed.path).rstrip("/").lower().endswith(".pdf"):
        return True

    query = parse_qs(parsed.query, keep_blank_values=False)
    for key, values in query.items():
        if key.lower() not in _PDF_QUERY_KEYS:
            continue
        for value in values:
            normalized = value.strip().lower()
            if normalized in {"pdf", "application/pdf", "application/x-pdf"}:
                return True

    return _PDF_QUERY_VALUE.search(parsed.query) is not None


def indicates_pdf_response(content_type: str | None, content_disposition: str | None = None) -> bool:
    """True for a PDF media type or a PDF filename in Content-Disposition."""
    if content_type:
        media_type = content_type.split(";", 1)[0].strip().lower()
        if media_type in {"application/pdf", "application/x-pdf"}:
            return True
    return filename_from_content_disposition(content_disposition) is not None


def _mentions_pdf(link: LinkLike) -> bool:
    return "pdf" in f"{link.url} {link.text}".lower()


def filename_from_content_disposition(content_disposition: str | None) -> str | None:
    if not content_disposition:
        return None
    match = _DISPOSITION_FILENAME.search(content_disposition)
    if match is None:
        return None
    filename = unquote(match.group(1)).strip().strip('"')
    if not filename.lower().endswith(".pdf"):
        return None
    return filename_from_url(f"https://download.local/{filename}")


def _add(
    found: list[DetectedPdf],
    seen: set[str],
    url: str,
    file_name: str | None = None,
) -> None:
    seen.add(dedupe_key(url))
    found.append(DetectedPdf(url=url, file_name=file_name or filename_from_url(url)))
