"""Merge PDF candidates that refer to the same URL."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse, urlunparse

from models.pdf_item import DiscoveryMethod, PdfStatus, format_discovery_methods


@dataclass
class PdfCandidate:
    """A PDF observed from one source before duplicates are merged."""

    url: str
    file_name: str
    discovery_method: DiscoveryMethod
    source_page_url: str | None = None
    source_endpoint: str | None = None
    content_type: str | None = None
    http_status: int | None = None
    file_size: int | None = None
    status: PdfStatus = PdfStatus.DISCOVERED
    error_message: str | None = None
    methods_label: str | None = None


@dataclass
class MergeResult:
    """Unique candidates and how many observations were collapsed."""

    candidates: list[PdfCandidate] = field(default_factory=list)
    duplicates_removed: int = 0


def canonical_url(url: str) -> str:
    """Drop the fragment and keep the query string exactly as it was observed."""
    parsed = urlparse(url)
    if not parsed.fragment:
        return url
    return urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path, parsed.params, parsed.query, "")
    )


def merge_candidates(candidates: list[PdfCandidate]) -> MergeResult:
    """Collapse exact URL matches after fragment removal."""
    groups: dict[str, list[PdfCandidate]] = {}
    order: list[str] = []
    for candidate in candidates:
        key = canonical_url(candidate.url)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(candidate)
    merged = [_merge_group(groups[key], key) for key in order]
    return MergeResult(
        candidates=merged,
        duplicates_removed=len(candidates) - len(merged),
    )


def merge_discovery_methods(existing: str | None, incoming: str | None) -> str | None:
    """Combine stored discovery labels."""
    parts: list[str] = []
    for value in (existing, incoming):
        if not value:
            continue
        parts.extend(part.strip() for part in value.split(","))
    return format_discovery_methods(parts)


def _merge_group(group: list[PdfCandidate], stored_url: str) -> PdfCandidate:
    methods: list[str] = []
    for item in group:
        if item.methods_label:
            methods.extend(part.strip() for part in item.methods_label.split(","))
        else:
            methods.append(item.discovery_method.value)
    status = (
        PdfStatus.VALIDATED
        if any(item.status == PdfStatus.VALIDATED for item in group)
        else group[0].status
    )
    return PdfCandidate(
        url=stored_url,
        file_name=_preferred_file_name(group),
        discovery_method=_primary_method(group),
        source_page_url=next((item.source_page_url for item in group if item.source_page_url), None),
        source_endpoint=next((item.source_endpoint for item in group if item.source_endpoint), None),
        content_type=_preferred_content_type(group),
        http_status=_preferred_status(group),
        file_size=next((item.file_size for item in group if item.file_size), None),
        status=status,
        error_message=next((item.error_message for item in group if item.error_message), None),
        methods_label=format_discovery_methods(methods),
    )


def _primary_method(group: list[PdfCandidate]) -> DiscoveryMethod:
    for method in (
        DiscoveryMethod.PDF_RESPONSE,
        DiscoveryMethod.BROWSER_DOWNLOAD,
        DiscoveryMethod.API_JSON,
        DiscoveryMethod.HTML,
    ):
        if any(item.discovery_method == method for item in group):
            return method
    return group[0].discovery_method


def _preferred_file_name(group: list[PdfCandidate]) -> str:
    for method in (DiscoveryMethod.BROWSER_DOWNLOAD, DiscoveryMethod.PDF_RESPONSE):
        for item in group:
            if item.discovery_method == method and item.file_name.lower().endswith(".pdf"):
                return item.file_name
    for item in group:
        if item.file_name.lower().endswith(".pdf"):
            return item.file_name
    return group[0].file_name


def _preferred_content_type(group: list[PdfCandidate]) -> str | None:
    for item in group:
        if item.content_type and _is_pdf_type(item.content_type):
            return item.content_type
    return next((item.content_type for item in group if item.content_type), None)


def _preferred_status(group: list[PdfCandidate]) -> int | None:
    for item in group:
        if item.http_status is not None and item.http_status < 400:
            return item.http_status
    return next((item.http_status for item in group if item.http_status is not None), None)


def _is_pdf_type(content_type: str) -> bool:
    media = content_type.split(";", 1)[0].strip().lower()
    return media in {"application/pdf", "application/x-pdf"}
