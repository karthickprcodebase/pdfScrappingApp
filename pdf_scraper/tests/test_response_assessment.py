"""Tests for PDF network response classification."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from models.pdf_item import PdfStatus  # noqa: E402
from scraper.response_assessment import assess_pdf_response  # noqa: E402


class ResponseAssessmentTests(unittest.TestCase):
    def test_accepts_pdf_content_type(self) -> None:
        verdict = assess_pdf_response(
            url="https://example.com/download",
            http_status=200,
            content_type="application/pdf; charset=binary",
        )
        self.assertTrue(verdict.accept)
        self.assertEqual(verdict.status, PdfStatus.VALIDATED)

    def test_validates_magic_bytes_when_a_body_is_available(self) -> None:
        verdict = assess_pdf_response(
            url="https://example.com/files/report.pdf?download=1",
            http_status=200,
            content_type="application/octet-stream",
            body_prefix=b"%PDF-1.4",
        )
        self.assertTrue(verdict.accept)
        self.assertEqual(verdict.status, PdfStatus.VALIDATED)
        self.assertEqual(verdict.file_name, "report.pdf")

    def test_rejects_http_errors(self) -> None:
        verdict = assess_pdf_response(
            url="https://example.com/missing.pdf",
            http_status=404,
            content_type="text/html",
        )
        self.assertFalse(verdict.accept)
        self.assertTrue(verdict.failed)

    def test_rejects_html_even_when_the_url_mentions_pdf(self) -> None:
        verdict = assess_pdf_response(
            url="https://example.com/search?q=pdf",
            http_status=200,
            content_type="text/html; charset=utf-8",
            body_prefix=b"<!DOCTYPE html>",
        )
        self.assertFalse(verdict.accept)
        self.assertFalse(verdict.failed)

    def test_rejects_a_body_that_is_not_a_pdf(self) -> None:
        verdict = assess_pdf_response(
            url="https://example.com/file.pdf",
            http_status=200,
            content_type="application/pdf",
            body_prefix=b"<html>",
        )
        self.assertFalse(verdict.accept)
        self.assertTrue(verdict.failed)

    def test_reads_filename_from_content_disposition(self) -> None:
        verdict = assess_pdf_response(
            url="https://example.com/download",
            http_status=200,
            content_type="application/pdf",
            content_disposition="attachment; filename=\"Quarterly Report.pdf\"",
        )
        self.assertEqual(verdict.file_name, "Quarterly Report.pdf")


if __name__ == "__main__":
    unittest.main()
