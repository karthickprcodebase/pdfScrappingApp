"""URL validation, resolution, and filename helpers."""

import re
from pathlib import PurePosixPath
from urllib.parse import parse_qs, unquote, urljoin, urlparse, urlunparse

_ALLOWED_SCHEMES = {"http", "https"}
_SKIPPED_HREF_PREFIXES = ("#", "javascript:", "mailto:", "tel:", "data:")
_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
_FILENAME_QUERY_KEYS = {"filename", "file", "name", "download"}
_MAX_FILENAME_LENGTH = 180


def validate_website_url(url: str) -> str | None:
    """Return a user-facing error message, or None when the URL can be scanned."""
    candidate = url.strip()
    if not candidate:
        return "Enter a website URL."

    parsed = urlparse(candidate)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES or not parsed.netloc:
        return "Enter a valid website URL that starts with http:// or https://."
    if parsed.hostname is None or any(character.isspace() for character in parsed.netloc):
        return "Enter a valid website URL that starts with http:// or https://."
    return None


def resolve_url(base_url: str, href: str) -> str | None:
    """Resolve a link against the page URL. Non-http links are ignored."""
    candidate = href.strip()
    if not candidate or candidate.lower().startswith(_SKIPPED_HREF_PREFIXES):
        return None

    absolute = urljoin(base_url, candidate)
    parsed = urlparse(absolute)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES or not parsed.netloc:
        return None
    return absolute


def dedupe_key(url: str) -> str:
    """Identity used to drop duplicate links that differ only by a fragment."""
    parsed = urlparse(url)
    return urlunparse(parsed._replace(fragment=""))


def redact_url(url: str) -> str:
    """Remove userinfo before writing a URL to the log."""
    parsed = urlparse(url)
    if parsed.username is None and parsed.password is None:
        return url
    hostname = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    return urlunparse(parsed._replace(netloc=f"{hostname}{port}"))


def filename_from_url(url: str) -> str:
    """Build a Windows-safe PDF filename from the URL path or a PDF name in the query."""
    parsed = urlparse(url)
    for key, values in parse_qs(parsed.query, keep_blank_values=False).items():
        if key.lower() not in _FILENAME_QUERY_KEYS:
            continue
        for value in values:
            cleaned = _filename_from_candidate(unquote(value), require_pdf_suffix=True)
            if cleaned:
                return cleaned

    path_name = PurePosixPath(unquote(parsed.path).rstrip("/")).name
    cleaned_path = _filename_from_candidate(path_name, require_pdf_suffix=False)
    if cleaned_path:
        return cleaned_path
    return "document.pdf"


def _filename_from_candidate(candidate: str, require_pdf_suffix: bool) -> str:
    name = candidate.strip()
    if not name:
        return ""
    name = name.split("?")[0].split("#")[0]
    if "/" in name or "\\" in name:
        name = PurePosixPath(name.replace("\\", "/")).name
    if not name or name in {".", ".."}:
        return ""
    if require_pdf_suffix and not name.lower().endswith(".pdf"):
        return ""
    if "." in name and not name.lower().endswith(".pdf"):
        return ""

    pdf_name = name if name.lower().endswith(".pdf") else f"{name}.pdf"
    return sanitize_filename(pdf_name)


def sanitize_filename(name: str) -> str:
    """Make a filename safe to display and later save on Windows."""
    cleaned = _INVALID_FILENAME_CHARS.sub("_", name).strip(" .")
    if not cleaned:
        return ""

    suffix = ".pdf" if cleaned.lower().endswith(".pdf") else ""
    stem = cleaned[: -len(suffix)] if suffix else cleaned
    if not stem:
        return "document.pdf" if suffix else ""
    if stem.upper() in _WINDOWS_RESERVED_NAMES:
        stem = f"_{stem}"
    cleaned = f"{stem}{suffix}"
    if len(cleaned) <= _MAX_FILENAME_LENGTH:
        return cleaned
    if suffix:
        return f"{cleaned[: _MAX_FILENAME_LENGTH - len(suffix)].rstrip(' .')}{suffix}"
    return cleaned[:_MAX_FILENAME_LENGTH]
