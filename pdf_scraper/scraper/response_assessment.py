"""Decide whether an observed network response is a PDF."""

from __future__ import annotations

from dataclasses import dataclass

from models.pdf_item import PdfStatus
from scraper.pdf_detector import is_pdf_content_type
from services.download_service import looks_like_pdf_bytes
from utils.url_utils import extract_pdf_filename, is_pdf_url

_HTML_TYPES = {"text/html", "application/xhtml+xml"}
_SKIP_TYPES = _HTML_TYPES | {"text/css", "text/javascript", "application/javascript", "image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml", "font/woff", "font/woff2"}


@dataclass(frozen=True)
class ResponseVerdict:
    """Result of inspecting one response without storing its body."""

    accept: bool
    status: PdfStatus = PdfStatus.DISCOVERED
    failed: bool = False
    file_name: str | None = None
    content_type: str | None = None


def assess_pdf_response(
    *,
    url: str,
    http_status: int,
    content_type: str | None,
    content_disposition: str | None = None,
    body_prefix: bytes | None = None,
) -> ResponseVerdict:
    """Classify a response. HTTP errors and HTML bodies are not PDF candidates."""
    media = _media_type(content_type)
    disposition_name = filename_from_content_disposition(content_disposition)
    url_is_pdf = is_pdf_url(url)
    header_is_pdf = is_pdf_content_type(content_type)
    disposition_is_pdf = bool(disposition_name and disposition_name.lower().endswith(".pdf"))
    looks_like_candidate = header_is_pdf or url_is_pdf or disposition_is_pdf

    if http_status >= 400:
        return ResponseVerdict(accept=False, failed=looks_like_candidate, content_type=media)

    if media in _SKIP_TYPES:
        return ResponseVerdict(accept=False, failed=False, content_type=media)

    if body_prefix is not None:
        if looks_like_pdf_bytes(body_prefix):
            return ResponseVerdict(
                accept=True,
                status=PdfStatus.VALIDATED,
                file_name=disposition_name or extract_pdf_filename(url),
                content_type=media or "application/pdf",
            )
        if header_is_pdf or url_is_pdf or disposition_is_pdf:
            return ResponseVerdict(accept=False, failed=True, content_type=media)

    if header_is_pdf:
        return ResponseVerdict(
            accept=True,
            status=PdfStatus.VALIDATED,
            file_name=disposition_name or extract_pdf_filename(url),
            content_type=media,
        )
    if disposition_is_pdf or url_is_pdf:
        return ResponseVerdict(
            accept=True,
            status=PdfStatus.DISCOVERED,
            file_name=disposition_name or extract_pdf_filename(url),
            content_type=media,
        )
    return ResponseVerdict(accept=False, content_type=media)


def filename_from_content_disposition(value: str | None) -> str | None:
    """Read a file name from a Content-Disposition header."""
    if not value:
        return None
    import re
    from urllib.parse import unquote

    extended = re.search(r"filename\*\s*=\s*([^']*)''([^;]+)", value, re.IGNORECASE)
    if extended:
        return unquote(extended.group(2).strip().strip('"'))
    plain = re.search(r'filename\s*=\s*"([^"]+)"', value, re.IGNORECASE)
    if plain:
        return plain.group(1).strip()
    plain = re.search(r"filename\s*=\s*([^;]+)", value, re.IGNORECASE)
    if plain:
        return plain.group(1).strip().strip('"')
    return None


def _media_type(content_type: str | None) -> str | None:
    if not content_type:
        return None
    return content_type.split(";", 1)[0].strip().lower() or None
