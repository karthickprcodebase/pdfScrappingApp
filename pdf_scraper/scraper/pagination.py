"""Find the next page from HTML controls or an observed API response.

These helpers do not fetch anything. The scanner decides which URL is safe to open.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import ParseResult, parse_qsl, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup

from utils.url_utils import is_pdf_url, resolve_url

_PAGINATION_HINT = re.compile(r"paginat|pager|\bpages\b", re.IGNORECASE)
_NEXT_LABEL = re.compile(
    r"^(?:go to )?(?:next|next page|load more|more results|show more|›|»|>>)$",
    re.IGNORECASE,
)
_PAGE_LABEL = re.compile(r"^(?:page\s+)?(\d{1,4})$", re.IGNORECASE)
_PAGE_PARAMS = ("page", "pagenumber", "page_number", "p")
_OFFSET_PARAMS = ("offset", "start")
_LIMIT_PARAMS = ("limit", "pagesize", "page_size", "per_page", "perpage")
_CURSOR_PARAMS = ("cursor", "nextcursor", "next_cursor", "continuationtoken", "continuation_token")
_NEXT_URL_FIELDS = {"next", "nexturl", "next_url", "nextpageurl", "next_page_url"}
_CURSOR_FIELDS = {"nextcursor", "next_cursor", "continuationtoken", "continuation_token"}
_HAS_MORE_FIELDS = {"hasmore", "has_more"}
_TOTAL_PAGES_FIELDS = {"totalpages", "total_pages", "pagecount", "page_count"}
_CURRENT_PAGE_FIELDS = {"currentpage", "current_page", "pagenumber", "page_number"}
_NEXT_PAGE_FIELDS = {"nextpage", "next_page"}
_TOTAL_COUNT_FIELDS = {"totalrecords", "totalcount", "total_count", "totalresults", "total_results"}
_META_KEYS = {"pagination", "paging", "meta", "pageinfo", "page_info", "page"}


@dataclass
class PaginationProgress:
    """Live status for the Streamlit page. Fields are replaced as a scan advances."""

    initial_url: str = ""
    current_url: str = ""
    pages_visited: int = 0
    pages_remaining: int | None = None
    api_requests: int = 0
    pdf_count: int = 0
    new_on_page: int = 0
    duplicates_ignored: int = 0
    method: str = ""
    elapsed_seconds: float = 0.0
    status: str = "Starting"


@dataclass(frozen=True)
class PaginationOptions:
    """Limits for one scan. The scanner will not keep going without these bounds."""

    enabled: bool = False
    max_pages: int = 20
    max_duration_seconds: int = 180
    max_empty_attempts: int = 2
    follow_next_controls: bool = True
    enable_infinite_scroll: bool = False
    max_scroll_iterations: int = 8
    follow_api_pagination: bool = True
    request_delay_seconds: float = 0.4

    def clamped(self) -> PaginationOptions:
        """Return options inside the supported ranges."""
        return PaginationOptions(
            enabled=self.enabled,
            max_pages=_clamp(self.max_pages, 1, 100),
            max_duration_seconds=_clamp(self.max_duration_seconds, 0, 900),
            max_empty_attempts=_clamp(self.max_empty_attempts, 1, 5),
            follow_next_controls=self.follow_next_controls,
            enable_infinite_scroll=self.enable_infinite_scroll,
            max_scroll_iterations=_clamp(self.max_scroll_iterations, 1, 30),
            follow_api_pagination=self.follow_api_pagination,
            request_delay_seconds=min(max(self.request_delay_seconds, 0.0), 5.0),
        )


@dataclass(frozen=True)
class PageTarget:
    """One forward pagination URL found in HTML."""

    url: str
    kind: str
    page_number: int | None = None


@dataclass(frozen=True)
class HtmlPagination:
    """Forward links, plus a note when a path was too ambiguous to follow."""

    targets: tuple[PageTarget, ...]
    skipped: tuple[str, ...] = ()


@dataclass(frozen=True)
class ApiPageHint:
    """Pagination evidence taken from one observed API response."""

    request_url: str
    next_url: str | None = None
    next_cursor: str | None = None
    current_page: int | None = None
    total_pages: int | None = None
    next_page: int | None = None
    offset: int | None = None
    limit: int | None = None
    total_count: int | None = None
    has_more: bool | None = None
    http_status: int = 200


def find_html_pagination(html: str, page_url: str) -> HtmlPagination:
    """Return forward pagination links from one rendered page."""
    soup = BeautifulSoup(html, "html.parser")
    current_number = page_number_from_url(page_url)
    targets: list[PageTarget] = []
    seen: set[str] = set()
    bare_next: list[PageTarget] = []

    for tag in soup.find_all(["a", "link"]):
        raw_href = tag.get("href")
        if not isinstance(raw_href, str):
            continue
        absolute = resolve_url(page_url, raw_href)
        if absolute is None or _same_resource(absolute, page_url) or is_pdf_url(absolute):
            continue
        label = _control_label(tag)
        rel = " ".join(tag.get("rel", [])).lower() if isinstance(tag.get("rel"), list) else str(tag.get("rel") or "").lower()
        in_pager = _inside_pagination(tag)
        number = _page_number(label)
        kind: str | None = None
        if "next" in rel.split():
            kind = "rel_next"
        elif label and _NEXT_LABEL.match(label) and (in_pager or tag.name == "a"):
            kind = "next_control" if in_pager else "next_text"
        elif number is not None and (in_pager or _same_path_page_query(page_url, absolute)):
            if current_number is not None and number <= current_number:
                continue
            kind = "numbered"
        if kind is None:
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        target = PageTarget(url=absolute, kind=kind, page_number=number)
        if kind == "next_text":
            bare_next.append(target)
        else:
            targets.append(target)

    skipped: list[str] = []
    if len(bare_next) == 1:
        targets.extend(bare_next)
    elif len(bare_next) > 1:
        skipped.append("Several Next links were found outside a pagination control, so none of them were followed.")

    targets.sort(key=_target_order)
    return HtmlPagination(targets=tuple(targets), skipped=tuple(skipped))


def extract_api_page_hint(payload: object, request_url: str, http_status: int = 200) -> ApiPageHint | None:
    """Read pagination metadata from the top of a JSON document."""
    if not isinstance(payload, dict):
        return None
    sections = _meta_sections(payload)
    next_url = _first_url(sections, request_url)
    cursor = _first_text(sections, _CURSOR_FIELDS)
    current = _first_int(sections, _CURRENT_PAGE_FIELDS)
    total_pages = _first_int(sections, _TOTAL_PAGES_FIELDS)
    next_page = _first_int(sections, _NEXT_PAGE_FIELDS)
    total_count = _first_int(sections, _TOTAL_COUNT_FIELDS)
    has_more = _first_bool(sections, _HAS_MORE_FIELDS)
    offset, limit = _offset_and_limit(request_url)
    if current is None:
        current = page_number_from_url(request_url)
    hint = ApiPageHint(
        request_url=request_url,
        next_url=next_url,
        next_cursor=cursor,
        current_page=current,
        total_pages=total_pages,
        next_page=next_page,
        offset=offset,
        limit=limit,
        total_count=total_count,
        has_more=has_more,
        http_status=http_status,
    )
    if not _hint_has_evidence(hint):
        return None
    return hint


def next_api_url(hint: ApiPageHint, seen_cursors: set[str], seen_urls: set[str]) -> str | None:
    """Build the next API URL when the response provides enough evidence."""
    if hint.http_status == 429 or hint.http_status >= 400:
        return None
    if hint.has_more is False and not hint.next_url:
        return None
    if (
        hint.next_url
        and not _same_resource(hint.next_url, hint.request_url)
        and not _already_seen(hint.next_url, seen_urls)
    ):
        return hint.next_url
    cursor_url = _cursor_url(hint, seen_cursors)
    if cursor_url and not _already_seen(cursor_url, seen_urls):
        return cursor_url
    page_url = _page_url(hint)
    if page_url and not _already_seen(page_url, seen_urls):
        return page_url
    offset_url = _offset_url(hint)
    if offset_url and not _already_seen(offset_url, seen_urls):
        return offset_url
    return None


def page_number_from_url(url: str) -> int | None:
    """Return a page index from a query parameter or a /page/N path."""
    parsed = urlparse(url)
    for name, value in parse_qsl(parsed.query, keep_blank_values=False):
        if name.lower() in _PAGE_PARAMS and value.isdigit():
            return int(value)
    match = re.search(r"/page/(\d{1,4})/?$", parsed.path, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def same_host(left: str, right: str) -> bool:
    """Return True when two URLs name the same host."""
    return (urlparse(left).hostname or "").lower() == (urlparse(right).hostname or "").lower()


def stop_reason(
    *,
    pages_visited: int,
    max_pages: int,
    elapsed_seconds: float,
    max_duration_seconds: int,
    empty_streak: int,
    max_empty_attempts: int,
    cancelled: bool,
    rate_limited: bool,
    queue_empty: bool,
) -> str | None:
    """Return why a scan should stop, or None when another page is allowed."""
    if cancelled:
        return "Stopped by user."
    if pages_visited >= max_pages:
        return f"Stopped after the maximum of {max_pages} pages."
    if elapsed_seconds >= max_duration_seconds:
        return "Stopped because the scan reached its time limit."
    if empty_streak >= max_empty_attempts:
        return "Stopped after pages with no new PDFs."
    if rate_limited and queue_empty:
        return "Stopped API pagination after the site returned HTTP 429."
    if queue_empty:
        return "No further pagination pages were found."
    return None


def _hint_has_evidence(hint: ApiPageHint) -> bool:
    return any(
        (
            hint.next_url,
            hint.next_cursor,
            hint.next_page is not None,
            hint.total_pages is not None,
            hint.has_more is not None,
            hint.total_count is not None,
        )
    )


def _cursor_url(hint: ApiPageHint, seen_cursors: set[str]) -> str | None:
    if not hint.next_cursor or hint.has_more is False:
        return None
    if hint.next_cursor in seen_cursors:
        return None
    parsed = urlparse(hint.request_url)
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    replaced = False
    updated: list[tuple[str, str]] = []
    for name, value in pairs:
        if name.lower() in _CURSOR_PARAMS:
            updated.append((name, hint.next_cursor))
            replaced = True
        else:
            updated.append((name, value))
    if not replaced:
        return None
    return _with_query(parsed, updated)


def _page_url(hint: ApiPageHint) -> str | None:
    if hint.has_more is False:
        return None
    target = hint.next_page
    if target is None and hint.current_page is not None and hint.total_pages is not None:
        if hint.current_page < hint.total_pages:
            target = hint.current_page + 1
    if target is None:
        return None
    if hint.total_pages is not None and target > hint.total_pages:
        return None
    return _replace_known_param(hint.request_url, _PAGE_PARAMS, str(target))


def _offset_url(hint: ApiPageHint) -> str | None:
    if hint.offset is None or hint.limit is None or hint.limit <= 0:
        return None
    if hint.has_more is False:
        return None
    nxt = hint.offset + hint.limit
    if hint.total_count is not None and nxt >= hint.total_count:
        return None
    if hint.has_more is None and hint.total_count is None:
        return None
    return _replace_known_param(hint.request_url, _OFFSET_PARAMS, str(nxt))


def _replace_known_param(url: str, names: tuple[str, ...], value: str) -> str | None:
    parsed = urlparse(url)
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    replaced = False
    updated: list[tuple[str, str]] = []
    for name, current in pairs:
        if not replaced and name.lower() in names:
            updated.append((name, value))
            replaced = True
        else:
            updated.append((name, current))
    if not replaced:
        return None
    return _with_query(parsed, updated)


def _with_query(parsed: ParseResult, pairs: list[tuple[str, str]]) -> str:
    query = urlencode(pairs, doseq=True)
    return urlunparse(parsed._replace(query=query, fragment=""))


def _meta_sections(payload: dict[str, object]) -> list[dict[str, object]]:
    sections = [payload]
    for key, value in payload.items():
        if key.lower() in _META_KEYS and isinstance(value, dict):
            sections.append(value)
    return sections


def _first_url(sections: list[dict[str, object]], base_url: str) -> str | None:
    for section in sections:
        for key, value in section.items():
            if key.lower() not in _NEXT_URL_FIELDS or not isinstance(value, str):
                continue
            resolved = resolve_url(base_url, value)
            if resolved and not is_pdf_url(resolved):
                return resolved
    return None


def _first_text(sections: list[dict[str, object]], names: set[str]) -> str | None:
    for section in sections:
        for key, value in section.items():
            if key.lower() in names and isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _first_int(sections: list[dict[str, object]], names: set[str]) -> int | None:
    for section in sections:
        for key, value in section.items():
            if key.lower() not in names:
                continue
            if isinstance(value, bool):
                continue
            if isinstance(value, int):
                return value
            if isinstance(value, str) and value.isdigit():
                return int(value)
    return None


def _first_bool(sections: list[dict[str, object]], names: set[str]) -> bool | None:
    for section in sections:
        for key, value in section.items():
            if key.lower() in names and isinstance(value, bool):
                return value
    return None


def _offset_and_limit(url: str) -> tuple[int | None, int | None]:
    offset = None
    limit = None
    for name, value in parse_qsl(urlparse(url).query, keep_blank_values=False):
        if not value.isdigit():
            continue
        lowered = name.lower()
        if lowered in _OFFSET_PARAMS and offset is None:
            offset = int(value)
        if lowered in _LIMIT_PARAMS and limit is None:
            limit = int(value)
    return offset, limit


def _control_label(tag: object) -> str:
    getter = getattr(tag, "get", None)
    text_getter = getattr(tag, "get_text", None)
    aria = getter("aria-label") if getter else None
    if isinstance(aria, str) and aria.strip():
        return aria.strip()
    if text_getter:
        return str(text_getter(" ", strip=True))
    return ""


def _page_number(label: str) -> int | None:
    match = _PAGE_LABEL.match(label.strip())
    if not match:
        return None
    return int(match.group(1))


def _inside_pagination(tag: object) -> bool:
    current = tag
    for _ in range(8):
        parent = getattr(current, "parent", None)
        if parent is None:
            return False
        current = parent
        getter = getattr(current, "get", None)
        if getter is None:
            continue
        pieces = [
            str(getter("aria-label") or ""),
            str(getter("id") or ""),
            str(getter("role") or ""),
        ]
        classes = getter("class")
        if isinstance(classes, list):
            pieces.extend(str(item) for item in classes)
        elif classes:
            pieces.append(str(classes))
        if _PAGINATION_HINT.search(" ".join(pieces)):
            return True
    return False


def _same_path_page_query(page_url: str, candidate: str) -> bool:
    current = urlparse(page_url)
    other = urlparse(candidate)
    if current.path != other.path:
        return False
    names = {name.lower() for name, _value in parse_qsl(other.query)}
    return bool(names.intersection(_PAGE_PARAMS))


def _same_resource(left: str, right: str) -> bool:
    return urlparse(left)._replace(fragment="") == urlparse(right)._replace(fragment="")


def _already_seen(url: str, seen_urls: set[str]) -> bool:
    return url in seen_urls or urlparse(url)._replace(fragment="").geturl() in seen_urls


def _target_order(target: PageTarget) -> tuple[int, int]:
    rank = {"rel_next": 0, "next_control": 1, "next_text": 2, "numbered": 3}.get(target.kind, 4)
    return rank, target.page_number or 0


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))
