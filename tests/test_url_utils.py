"""Tests for URL validation, resolution, and filename handling."""

from utils.url_utils import (
    dedupe_key,
    filename_from_url,
    redact_url,
    resolve_url,
    validate_website_url,
)


def test_empty_url_is_rejected() -> None:
    assert validate_website_url("") == "Enter a website URL."
    assert validate_website_url("   ") == "Enter a website URL."


def test_invalid_urls_are_rejected() -> None:
    message = "Enter a valid website URL that starts with http:// or https://."
    assert validate_website_url("example.com") == message
    assert validate_website_url("ftp://example.com/file.pdf") == message
    assert validate_website_url("http://") == message
    assert validate_website_url("https://") == message
    assert validate_website_url("javascript:alert(1)") == message


def test_valid_urls_are_accepted() -> None:
    assert validate_website_url("https://example.com/reports") is None
    assert validate_website_url("  http://localhost:8501/docs  ") is None
    assert validate_website_url("https://127.0.0.1/files/a.pdf") is None


def test_resolve_relative_and_protocol_relative_links() -> None:
    base = "https://example.com/docs/index.html"
    assert resolve_url(base, "report.pdf") == "https://example.com/docs/report.pdf"
    assert resolve_url(base, "/files/a.pdf") == "https://example.com/files/a.pdf"
    assert resolve_url(base, "//cdn.example.com/a.pdf") == "https://cdn.example.com/a.pdf"


def test_resolve_ignores_non_http_links() -> None:
    base = "https://example.com/docs/"
    assert resolve_url(base, "#section") is None
    assert resolve_url(base, "javascript:void(0)") is None
    assert resolve_url(base, "mailto:person@example.com") is None
    assert resolve_url(base, "data:text/plain,hello") is None


def test_filename_from_pdf_path_and_query() -> None:
    assert filename_from_url("https://example.com/files/Annual%20Report.pdf") == "Annual Report.pdf"
    assert filename_from_url("https://example.com/files/report.pdf?download=1") == "report.pdf"
    assert filename_from_url("https://example.com/download?file=Q3%20Report.pdf") == "Q3 Report.pdf"
    assert filename_from_url("https://example.com/download/123") == "123.pdf"


def test_filename_strips_windows_invalid_characters_and_reserved_names() -> None:
    assert filename_from_url("https://example.com/files/Q3:Report.pdf") == "Q3_Report.pdf"
    assert filename_from_url("https://example.com/files/CON.pdf") == "_CON.pdf"


def test_filename_uses_path_when_trailing_slash_follows_pdf() -> None:
    assert filename_from_url("https://example.com/files/report.pdf/") == "report.pdf"


def test_dedupe_key_ignores_fragment_only() -> None:
    assert dedupe_key("https://example.com/a.pdf#page=2") == "https://example.com/a.pdf"
    assert dedupe_key("https://example.com/a.pdf?x=1") == "https://example.com/a.pdf?x=1"


def test_redact_url_removes_userinfo_and_keeps_normal_urls() -> None:
    assert redact_url("https://user:secret@example.com/a.pdf") == "https://example.com/a.pdf"
    assert redact_url("https://example.com/a.pdf?token=1") == "https://example.com/a.pdf?token=1"
