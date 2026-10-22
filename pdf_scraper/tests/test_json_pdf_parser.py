"""Tests for JSON API PDF extraction."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scraper.json_pdf_parser import loads_json_pdf_links  # noqa: E402

_SIGNED = (
    "https://cdn.example.com/files/report.pdf"
    "?X-Amz-Signature=abc%2Bdef&X-Amz-Credential=AKIAEXAMPLE"
)


class JsonPdfParserTests(unittest.TestCase):
    def test_extracts_nested_pdf_urls_and_keeps_signed_query(self) -> None:
        payload = {
            "meta": {"url": "https://example.com/about", "href": "https://example.com/help.html"},
            "groups": [
                {
                    "pdfUrl": _SIGNED,
                    "files": [
                        {"fileUrl": "/files/nested.pdf"},
                        {"downloadUrl": "folder/minutes.pdf?token=keep-me"},
                    ],
                }
            ],
        }
        found = loads_json_pdf_links(json.dumps(payload), "https://api.example.com/v1/docs")
        urls = [item.url for item in found]
        self.assertIn(_SIGNED, urls)
        self.assertIn("https://api.example.com/files/nested.pdf", urls)
        self.assertIn("https://api.example.com/v1/folder/minutes.pdf?token=keep-me", urls)
        self.assertNotIn("https://example.com/about", urls)
        self.assertNotIn("https://example.com/help.html", urls)

    def test_named_download_field_without_pdf_extension(self) -> None:
        payload = {"downloadUrl": "https://files.example.com/get?id=42&sig=abc"}
        found = loads_json_pdf_links(json.dumps(payload), "https://api.example.com/list")
        self.assertEqual(found[0].url, "https://files.example.com/get?id=42&sig=abc")

    def test_rejects_ordinary_page_in_a_document_field(self) -> None:
        payload = {"documentPath": "/pages/home.html"}
        found = loads_json_pdf_links(json.dumps(payload), "https://example.com/api")
        self.assertEqual(found, [])

    def test_invalid_json_raises(self) -> None:
        with self.assertRaises(json.JSONDecodeError):
            loads_json_pdf_links("{not json", "https://example.com/api")

    def test_respects_node_limit(self) -> None:
        payload = {"items": [{"pdfUrl": f"/files/{index}.pdf"} for index in range(10)]}
        found = loads_json_pdf_links(
            json.dumps(payload),
            "https://example.com/api",
            max_nodes=3,
        )
        self.assertLess(len(found), 10)


if __name__ == "__main__":
    unittest.main()
