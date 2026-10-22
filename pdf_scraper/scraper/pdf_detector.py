"""Find PDF links in HTML from a single loaded page."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from bs4 import BeautifulSoup

from utils.url_utils import extract_pdf_filename, is_pdf_url, resolve_url

_PDF_CONTENT_TYPES = {"application/pdf", "application/x-pdf"}
_LINK_TAGS = ("a", "iframe", "embed", "object")


@dataclass(frozen=True)
class LinkCandidate:
    """A link harvested from the page, before PDF classification."""

    url: str
    content_type_hint: str | None = None


@dataclass(frozen=True)
class DetectedPdf:
    """A PDF link and the file name derived from it."""

    url: str
    file_name: str


def is_pdf_content_type(content_type: str | None) -> bool:
    """Return True for a PDF media type, ignoring parameters such as charset."""
    if not content_type:
        return False
    media_type = content_type.split(";", 1)[0].strip().lower()
    return media_type in _PDF_CONTENT_TYPES


def should_probe_content_type(candidate: LinkCandidate) -> bool:
    """Return True when a URL is ambiguous and a single HEAD request is reasonable.

    Extension matches and explicit PDF type hints do not need a request.
    Other links are probed only when the URL or hint mentions "pdf", and the
    scanner caps how many of those requests it makes.
    """
    if is_pdf_url(candidate.url) or is_pdf_content_type(candidate.content_type_hint):
        return False
    if "pdf" in candidate.url.lower():
        return True
    hint = candidate.content_type_hint or ""
    return "pdf" in hint.lower()


def extract_link_candidates(html: str, page_url: str) -> list[LinkCandidate]:
    """Collect absolute http(s) links from anchors and embedded file tags."""
    soup = BeautifulSoup(html, "html.parser")
    base_url = page_url
    base_tag = soup.find("base", href=True)
    if base_tag is not None:
        base_href = base_tag.get("href")
        if isinstance(base_href, str):
            resolved_base = resolve_url(page_url, base_href)
            if resolved_base is not None:
                base_url = resolved_base

    candidates: list[LinkCandidate] = []
    seen: set[str] = set()
    for tag in soup.find_all(_LINK_TAGS):
        raw_href = _link_attribute(tag)
        if raw_href is None:
            continue
        absolute = resolve_url(base_url, raw_href)
        if absolute is None or absolute in seen:
            continue
        seen.add(absolute)
        type_hint = tag.get("type")
        hint = type_hint.strip() if isinstance(type_hint, str) and type_hint.strip() else None
        candidates.append(LinkCandidate(url=absolute, content_type_hint=hint))
    return candidates


def detect_pdfs(
    candidates: Sequence[LinkCandidate],
    content_types: Mapping[str, str] | None = None,
) -> list[DetectedPdf]:
    """Keep unique PDF links and preserve the first occurrence of each URL."""
    headers = content_types or {}
    detected: list[DetectedPdf] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate.url in seen:
            continue
        header = headers.get(candidate.url)
        if not _candidate_is_pdf(candidate, header):
            continue
        seen.add(candidate.url)
        detected.append(
            DetectedPdf(
                url=candidate.url,
                file_name=extract_pdf_filename(candidate.url),
            )
        )
    return detected


def _candidate_is_pdf(candidate: LinkCandidate, content_type: str | None) -> bool:
    if is_pdf_url(candidate.url):
        return True
    if is_pdf_content_type(candidate.content_type_hint):
        return True
    return is_pdf_content_type(content_type)


def _link_attribute(tag: object) -> str | None:
    getter = getattr(tag, "get", None)
    if getter is None:
        return None
    for attribute in ("href", "src", "data"):
        value = getter(attribute)
        if isinstance(value, str) and value.strip():
            return value
    return None
