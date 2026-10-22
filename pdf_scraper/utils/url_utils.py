"""URL validation, resolution, and PDF filename extraction."""

from __future__ import annotations

import posixpath
import re
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlparse, urlunparse

_ALLOWED_SCHEMES = {"http", "https"}
_SKIPPED_SCHEMES = ("javascript:", "mailto:", "tel:", "data:", "blob:")
_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_WINDOWS_NAMES = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$", re.IGNORECASE)
_MAX_FILENAME_LENGTH = 180
_SENSITIVE_QUERY_NAMES = re.compile(
    r"(signature|sig|token|secret|password|passwd|credential|auth|jwt|session|key)",
    re.IGNORECASE,
)


def validate_http_url(value: str) -> str:
    """Return a trimmed http(s) URL, or raise ValueError with a message for the UI."""
    if value is None or not str(value).strip():
        raise ValueError("Enter a website URL.")

    candidate = str(value).strip()
    if any(character in candidate for character in "\r\n\t ") or any(
        character.isspace() for character in candidate
    ):
        raise ValueError("Enter a valid website URL, including http:// or https://.")

    parsed = urlparse(candidate)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        if parsed.scheme:
            raise ValueError("Only http and https website URLs can be scanned.")
        raise ValueError("Enter a valid website URL, including http:// or https://.")
    if not parsed.netloc or not parsed.hostname:
        raise ValueError("Enter a valid website URL, including http:// or https://.")
    return candidate


def resolve_url(base_url: str, raw_href: str) -> str | None:
    """Resolve a link against the page URL. Non-http links are dropped."""
    href = raw_href.strip()
    if not href or href.startswith("#"):
        return None
    if href.lower().startswith(_SKIPPED_SCHEMES):
        return None

    absolute = urljoin(base_url, href)
    parsed = urlparse(absolute)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES or not parsed.netloc:
        return None
    return absolute


def redact_url(url: str) -> str:
    """Remove passwords and signed-query secrets before a URL is written to a log."""
    parsed = urlparse(url)
    has_userinfo = parsed.username is not None or parsed.password is not None
    redacted_query, query_changed = _redact_query(parsed.query)
    if not has_userinfo and not query_changed:
        return url
    hostname = parsed.hostname or ""
    if ":" in hostname:
        hostname = f"[{hostname}]"
    if has_userinfo:
        netloc = f"{hostname}:{parsed.port}" if parsed.port else hostname
    else:
        netloc = parsed.netloc
    return urlunparse(
        (parsed.scheme, netloc, parsed.path, parsed.params, redacted_query, parsed.fragment)
    )


def _redact_query(query: str) -> tuple[str, bool]:
    if not query:
        return query, False
    pairs = parse_qsl(query, keep_blank_values=True)
    changed = False
    redacted: list[tuple[str, str]] = []
    for name, value in pairs:
        if _SENSITIVE_QUERY_NAMES.search(name):
            redacted.append((name, "REDACTED"))
            changed = True
        else:
            redacted.append((name, value))
    if not changed:
        return query, False
    return urlencode(redacted, doseq=True), True


def safe_for_log(value: str, limit: int = 300) -> str:
    """Return a short redacted string suitable for a log line."""
    text = "" if value is None else str(value).strip()
    if not text:
        return "<empty>"
    redacted = redact_url(text)
    if len(redacted) > limit:
        return redacted[:limit] + "..."
    return redacted


def is_pdf_url(url: str) -> bool:
    """Return True when the URL path or a query filename ends in .pdf.

    Query strings and fragments do not hide a .pdf path. A query value of
    "pdf" by itself is not treated as a file, because that page may be HTML.
    """
    parsed = urlparse(url)
    if _looks_like_pdf_filename(_basename(unquote(parsed.path))):
        return True
    for _name, query_value in parse_qsl(parsed.query, keep_blank_values=False):
        if _looks_like_pdf_filename(_basename(unquote(query_value))):
            return True
    return False


def extract_pdf_filename(url: str) -> str:
    """Choose a Windows-safe file name from the URL path or query string."""
    parsed = urlparse(url)
    path_name = _basename(unquote(parsed.path)).strip()
    query_name = _filename_from_query(parsed.query)

    if _looks_like_pdf_filename(path_name):
        chosen = path_name
    elif query_name:
        chosen = query_name
    elif path_name:
        chosen = path_name
    else:
        chosen = "document.pdf"
    return _finalize_filename(chosen)


def _filename_from_query(query: str) -> str | None:
    for _name, query_value in parse_qsl(query, keep_blank_values=False):
        candidate = _basename(unquote(query_value)).strip()
        if _looks_like_pdf_filename(candidate):
            return candidate
    return None


def _basename(value: str) -> str:
    normalized = value.replace("\\", "/").split("?", 1)[0].split("#", 1)[0]
    return posixpath.basename(normalized)


def _looks_like_pdf_filename(name: str) -> bool:
    return name.lower().endswith(".pdf") and name.lower() != ".pdf"


def _finalize_filename(name: str) -> str:
    cleaned = _INVALID_FILENAME_CHARS.sub("_", name).strip(" .")
    if not cleaned or cleaned in {".", ".."}:
        cleaned = "document.pdf"
    if not cleaned.lower().endswith(".pdf"):
        cleaned = f"{cleaned}.pdf"
    stem = cleaned[:-4]
    if _RESERVED_WINDOWS_NAMES.fullmatch(stem):
        cleaned = f"_{cleaned}"
    if len(cleaned) > _MAX_FILENAME_LENGTH:
        cleaned = cleaned[: _MAX_FILENAME_LENGTH - 4].rstrip(" .") + ".pdf"
    if not _looks_like_pdf_filename(cleaned):
        return "document.pdf"
    return cleaned
