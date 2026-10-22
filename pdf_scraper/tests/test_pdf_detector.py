"""Tests for PDF link detection. These tests do not open a website."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scraper.pdf_detector import (  # noqa: E402
    LinkCandidate,
    detect_pdfs,
    extract_link_candidates,
    is_pdf_content_type,
    should_probe_content_type,
)

_SAMPLE_HTML = """
<!DOCTYPE html>
<html>
  <head>
    <base href="https://cdn.example.com/docs/">
  </head>
  <body>
    <a href="guide.pdf?download=1">Guide</a>
    <a href="guide.pdf?download=1">Duplicate</a>
    <a href="javascript:void(0)">Skip</a>
    <a href="mailto:person@example.com">Mail</a>
    <a href="https://example.com/page.html">Page</a>
    <a href="/files/report.pdf">Report</a>
    <iframe src="embedded.pdf"></iframe>
    <embed src="manual.pdf" type="application/pdf">
    <object data="spec.pdf"></object>
    <a href="/get-file" type="application/pdf">Typed</a>
  </body>
</html>
"""


class ContentTypeTests(unittest.TestCase):
    def test_accepts_pdf_media_types(self) -> None:
        self.assertTrue(is_pdf_content_type("application/pdf"))
        self.assertTrue(is_pdf_content_type("Application/PDF; charset=binary"))
        self.assertTrue(is_pdf_content_type("application/x-pdf"))

    def test_rejects_other_media_types(self) -> None:
        self.assertFalse(is_pdf_content_type(None))
        self.assertFalse(is_pdf_content_type("text/html; charset=utf-8"))
        self.assertFalse(is_pdf_content_type("application/octet-stream"))


class DetectPdfTests(unittest.TestCase):
    def test_keeps_original_url_and_removes_exact_duplicates(self) -> None:
        url = "https://example.com/files/report.pdf?download=1#page=2"
        found = detect_pdfs([LinkCandidate(url), LinkCandidate(url)])
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].url, url)
        self.assertEqual(found[0].file_name, "report.pdf")

    def test_keeps_distinct_query_strings(self) -> None:
        first = "https://example.com/report.pdf?id=1"
        second = "https://example.com/report.pdf?id=2"
        found = detect_pdfs([LinkCandidate(first), LinkCandidate(second)])
        self.assertEqual([item.url for item in found], [first, second])

    def test_uses_response_content_type_for_ambiguous_urls(self) -> None:
        url = "https://example.com/view?format=pdf"
        html_result = detect_pdfs(
            [LinkCandidate(url)],
            {url: "text/html; charset=utf-8"},
        )
        pdf_result = detect_pdfs(
            [LinkCandidate(url)],
            {url: "application/pdf; charset=binary"},
        )
        self.assertEqual(html_result, [])
        self.assertEqual(len(pdf_result), 1)
        self.assertEqual(pdf_result[0].url, url)
        self.assertEqual(pdf_result[0].file_name, "view.pdf")

    def test_uses_html_type_hint_without_a_network_header(self) -> None:
        url = "https://example.com/get-file"
        found = detect_pdfs([LinkCandidate(url, content_type_hint="application/pdf")])
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].file_name, "get-file.pdf")

    def test_probe_only_ambiguous_pdf_hints(self) -> None:
        self.assertFalse(should_probe_content_type(LinkCandidate("https://example.com/a.pdf")))
        self.assertFalse(
            should_probe_content_type(
                LinkCandidate("https://example.com/get", content_type_hint="application/pdf")
            )
        )
        self.assertFalse(should_probe_content_type(LinkCandidate("https://example.com/page.html")))
        self.assertTrue(
            should_probe_content_type(LinkCandidate("https://example.com/view?format=pdf"))
        )


class ExtractLinkTests(unittest.TestCase):
    def test_extracts_pdf_links_from_one_page(self) -> None:
        candidates = extract_link_candidates(_SAMPLE_HTML, "https://example.com/start")
        urls = [candidate.url for candidate in candidates]
        self.assertEqual(
            urls,
            [
                "https://cdn.example.com/docs/guide.pdf?download=1",
                "https://example.com/page.html",
                "https://cdn.example.com/files/report.pdf",
                "https://cdn.example.com/docs/embedded.pdf",
                "https://cdn.example.com/docs/manual.pdf",
                "https://cdn.example.com/docs/spec.pdf",
                "https://cdn.example.com/get-file",
            ],
        )
        found = detect_pdfs(candidates)
        self.assertEqual(
            [item.file_name for item in found],
            ["guide.pdf", "report.pdf", "embedded.pdf", "manual.pdf", "spec.pdf", "get-file.pdf"],
        )
        self.assertEqual(
            found[0].url,
            "https://cdn.example.com/docs/guide.pdf?download=1",
        )


if __name__ == "__main__":
    unittest.main()
