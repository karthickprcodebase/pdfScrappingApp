"""Extract PDF URLs from JSON API payloads observed in the browser."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from utils.url_guard import is_web_page_url
from utils.url_utils import extract_pdf_filename, is_pdf_url, resolve_url

_DOCUMENT_FIELDS = {
    "pdfurl",
    "pdf_url",
    "fileurl",
    "file_url",
    "downloadurl",
    "download_url",
    "documenturl",
    "document_url",
    "attachmenturl",
    "attachment_url",
    "documentpath",
    "document_path",
    "filepath",
    "file_path",
}


@dataclass(frozen=True)
class JsonPdfLink:
    """A PDF URL found inside one JSON document."""

    url: str
    file_name: str
    field_name: str | None


@dataclass
class _WalkState:
    links: list[JsonPdfLink] = field(default_factory=list)
    seen: set[str] = field(default_factory=set)
    nodes: int = 0


def parse_json_pdf_links(
    payload: Any,
    base_url: str,
    *,
    max_depth: int = 40,
    max_nodes: int = 10_000,
) -> list[JsonPdfLink]:
    """Walk a JSON value and return PDF links. The input must already be parsed."""
    state = _WalkState()
    _walk(payload, None, 0, base_url, max_depth, max_nodes, state)
    return state.links


def loads_json_pdf_links(
    raw_text: str,
    base_url: str,
    *,
    max_depth: int = 40,
    max_nodes: int = 10_000,
) -> list[JsonPdfLink]:
    """Parse JSON text and extract PDF links. Invalid JSON raises JSONDecodeError."""
    import json

    payload = json.loads(raw_text)
    return parse_json_pdf_links(payload, base_url, max_depth=max_depth, max_nodes=max_nodes)


def is_json_content_type(content_type: str | None) -> bool:
    """Return True for JSON media types, including vendor +json types."""
    if not content_type:
        return False
    media = content_type.split(";", 1)[0].strip().lower()
    return media == "application/json" or media == "text/json" or media.endswith("+json")


def _walk(
    node: Any,
    field_name: str | None,
    depth: int,
    base_url: str,
    max_depth: int,
    max_nodes: int,
    state: _WalkState,
) -> None:
    if depth > max_depth or state.nodes >= max_nodes:
        return
    state.nodes += 1
    if isinstance(node, dict):
        for key, value in node.items():
            key_text = str(key)
            if isinstance(value, str):
                _consider(key_text, value, base_url, state)
            else:
                _walk(value, key_text, depth + 1, base_url, max_depth, max_nodes, state)
        return
    if isinstance(node, list):
        for value in node:
            if isinstance(value, str):
                _consider(field_name, value, base_url, state)
            else:
                _walk(value, field_name, depth + 1, base_url, max_depth, max_nodes, state)
        return
    if isinstance(node, str):
        _consider(field_name, node, base_url, state)


def _consider(field_name: str | None, value: str, base_url: str, state: _WalkState) -> None:
    resolved = resolve_url(base_url, value)
    if resolved is None or resolved in state.seen:
        return
    if is_pdf_url(resolved) or _named_document_field(field_name, resolved):
        state.seen.add(resolved)
        state.links.append(
            JsonPdfLink(
                url=resolved,
                file_name=extract_pdf_filename(resolved),
                field_name=field_name,
            )
        )


def _named_document_field(field_name: str | None, url: str) -> bool:
    if not field_name or field_name.strip().lower() not in _DOCUMENT_FIELDS:
        return False
    if is_web_page_url(url):
        return False
    return True
