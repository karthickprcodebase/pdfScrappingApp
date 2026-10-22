"""Local-server tests for HTML, JSON, and direct PDF responses."""

from __future__ import annotations

import json
import sys
import threading
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config import load_settings  # noqa: E402
from models.pdf_item import PdfStatus, ScanMode  # noqa: E402
from scraper.website_scanner import ScanError, WebsiteScanner  # noqa: E402

_PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
_SIGNED = (
    "https://cdn.example.com/files/report.pdf"
    "?X-Amz-Signature=abc%2Bdef&X-Amz-Credential=AKIAEXAMPLE"
)
_API_PAYLOAD = {
    "meta": {"url": "https://example.com/about"},
    "items": [
        {
            "pdfUrl": _SIGNED,
            "files": [{"fileUrl": "/files/nested.pdf"}],
        }
    ],
}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/page.html":
            self._send(200, "text/html; charset=utf-8", _PAGE.encode("utf-8"))
            return
        if path == "/files/report.pdf":
            self._send(200, "application/pdf", _PDF)
            return
        if path == "/files/generated.pdf":
            self._send(200, "application/pdf", _PDF)
            return
        if path == "/missing.pdf":
            self._send(404, "text/html; charset=utf-8", b"<html>missing</html>")
            return
        if path == "/api/docs":
            self._send(200, "application/json", json.dumps(_API_PAYLOAD).encode("utf-8"))
            return
        self._send(404, "text/plain; charset=utf-8", b"no")

    def log_message(self, format: str, *args: object) -> None:
        return

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


_PAGE = """
<!DOCTYPE html>
<html>
  <body>
    <a href="/files/report.pdf?download=1">Report</a>
    <iframe src="/missing.pdf"></iframe>
    <script>
      fetch("/api/docs");
      fetch("/files/report.pdf?download=1");
      fetch("/files/generated.pdf");
    </script>
  </body>
</html>
"""


def _chromium_ready() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            browser.close()
        return True
    except Exception:
        return False


@unittest.skipUnless(_chromium_ready(), "Playwright Chromium is not installed")
class LocalDiscoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        host, port = cls.server.server_address[:2]
        cls.page_url = f"http://{host}:{port}/page.html"
        cls.settings = replace(
            load_settings(),
            allow_private_networks=True,
            network_idle_timeout_ms=4_000,
            playwright_timeout_ms=20_000,
            headless=True,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def test_html_mode_reads_links_from_the_page(self) -> None:
        outcome = WebsiteScanner(self.settings).scan(self.page_url, ScanMode.HTML)
        urls = [item.url for item in outcome.pdfs]
        self.assertIn(self.page_url.replace("/page.html", "/files/report.pdf?download=1"), urls)
        self.assertIn(self.page_url.replace("/page.html", "/missing.pdf"), urls)
        self.assertNotIn(_SIGNED, urls)
        self.assertTrue(all(item.discovery_method == "HTML" for item in outcome.pdfs))

    def test_network_mode_adds_json_and_pdf_responses(self) -> None:
        outcome = WebsiteScanner(self.settings).scan(self.page_url, ScanMode.HTML_API)
        by_url = {item.url: item for item in outcome.pdfs}
        report_url = self.page_url.replace("/page.html", "/files/report.pdf?download=1")
        generated_url = self.page_url.replace("/page.html", "/files/generated.pdf")
        nested_url = self.page_url.replace("/page.html", "/files/nested.pdf")

        self.assertIn(_SIGNED, by_url)
        self.assertEqual(by_url[_SIGNED].discovery_method, "API_JSON")
        self.assertIn("/api/docs", by_url[_SIGNED].source_endpoint or "")
        self.assertEqual(by_url[_SIGNED].download_status, None)
        self.assertIn(nested_url, by_url)
        self.assertNotIn("https://example.com/about", by_url)

        report = by_url[report_url]
        self.assertIn("HTML", report.discovery_method or "")
        self.assertIn("PDF_RESPONSE", report.discovery_method or "")
        self.assertEqual(report.status, PdfStatus.VALIDATED)

        generated = by_url[generated_url]
        self.assertEqual(generated.discovery_method, "PDF_RESPONSE")
        self.assertEqual(generated.status, PdfStatus.VALIDATED)
        self.assertGreaterEqual(outcome.duplicates_removed, 1)
        self.assertGreaterEqual(outcome.failed_inspections, 1)

    def test_blocked_public_setting_rejects_the_local_server(self) -> None:
        blocked = replace(self.settings, allow_private_networks=False)
        with self.assertRaises(ScanError):
            WebsiteScanner(blocked).scan(self.page_url, ScanMode.HTML)


if __name__ == "__main__":
    unittest.main()
