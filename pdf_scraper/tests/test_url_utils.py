"""Tests for URL validation, resolution, and filename extraction."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from utils.url_utils import (  # noqa: E402
    extract_pdf_filename,
    is_pdf_url,
    redact_url,
    resolve_url,
    validate_http_url,
)


class ValidateHttpUrlTests(unittest.TestCase):
    def test_accepts_http_and_https(self) -> None:
        self.assertEqual(validate_http_url("https://example.com"), "https://example.com")
        self.assertEqual(
            validate_http_url("  http://localhost:8080/reports  "),
            "http://localhost:8080/reports",
        )

    def test_rejects_empty_and_whitespace(self) -> None:
        with self.assertRaisesRegex(ValueError, "Enter a website URL."):
            validate_http_url("   ")

    def test_rejects_missing_scheme(self) -> None:
        with self.assertRaisesRegex(ValueError, "including http:// or https://"):
            validate_http_url("example.com/file.pdf")

    def test_rejects_non_http_scheme(self) -> None:
        with self.assertRaisesRegex(ValueError, "Only http and https"):
            validate_http_url("ftp://example.com/file.pdf")

    def test_rejects_spaces_and_missing_host(self) -> None:
        with self.assertRaisesRegex(ValueError, "including http:// or https://"):
            validate_http_url("https://example.com/a file.pdf")
        with self.assertRaisesRegex(ValueError, "including http:// or https://"):
            validate_http_url("https://")


class ResolveUrlTests(unittest.TestCase):
    def test_resolves_relative_and_root_links(self) -> None:
        base = "https://example.com/docs/index.html"
        self.assertEqual(resolve_url(base, "guide.pdf"), "https://example.com/docs/guide.pdf")
        self.assertEqual(resolve_url(base, "/files/a.pdf"), "https://example.com/files/a.pdf")
        self.assertEqual(
            resolve_url(base, "//cdn.example.com/a.pdf"),
            "https://cdn.example.com/a.pdf",
        )

    def test_drops_non_http_links(self) -> None:
        base = "https://example.com/index.html"
        self.assertIsNone(resolve_url(base, "#section"))
        self.assertIsNone(resolve_url(base, "javascript:void(0)"))
        self.assertIsNone(resolve_url(base, "mailto:person@example.com"))
        self.assertIsNone(resolve_url(base, "data:text/plain,hello"))


class PdfUrlAndFilenameTests(unittest.TestCase):
    def test_detects_pdf_extension_with_query_and_fragment(self) -> None:
        url = "https://example.com/files/Annual%20Report.PDF?download=1#page=2"
        self.assertTrue(is_pdf_url(url))
        self.assertEqual(extract_pdf_filename(url), "Annual Report.PDF")

    def test_detects_pdf_filename_in_query(self) -> None:
        url = "https://example.com/download?file=Quarterly%20Report.pdf&token=1"
        self.assertTrue(is_pdf_url(url))
        self.assertEqual(extract_pdf_filename(url), "Quarterly Report.pdf")

    def test_format_flag_alone_is_not_a_pdf_url(self) -> None:
        self.assertFalse(is_pdf_url("https://example.com/view?format=pdf"))

    def test_rejects_html_pages(self) -> None:
        self.assertFalse(is_pdf_url("https://example.com/reports/index.html"))

    def test_sanitizes_windows_filenames(self) -> None:
        self.assertEqual(
            extract_pdf_filename("https://example.com/files/a:b*c.pdf"),
            "a_b_c.pdf",
        )
        self.assertEqual(extract_pdf_filename("https://example.com/CON.pdf"), "_CON.pdf")
        self.assertEqual(
            extract_pdf_filename(r"https://example.com/get?file=folder\notes.pdf"),
            "notes.pdf",
        )

    def test_redacts_userinfo(self) -> None:
        self.assertEqual(
            redact_url("https://user:secret@example.com/a.pdf?x=1"),
            "https://example.com/a.pdf?x=1",
        )
        self.assertEqual(
            redact_url("https://user:secret@[::1]/a.pdf"),
            "https://[::1]/a.pdf",
        )


if __name__ == "__main__":
    unittest.main()
