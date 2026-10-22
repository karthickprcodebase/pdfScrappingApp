"""Local-server tests for multi-page PDF discovery."""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config import load_settings  # noqa: E402
from database.database import Database  # noqa: E402
from models.pdf_item import ScanMode  # noqa: E402
from scraper.pagination import PaginationOptions  # noqa: E402
from scraper.website_scanner import WebsiteScanner  # noqa: E402

_PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"


def _html_page(*parts: str) -> bytes:
    body = "".join(parts)
    return f"<!DOCTYPE html><html><body>{body}</body></html>".encode("utf-8")


def _options(**overrides: object) -> PaginationOptions:
    values: dict[str, object] = {
        "enabled": True,
        "max_pages": 6,
        "max_duration_seconds": 45,
        "max_empty_attempts": 2,
        "follow_next_controls": False,
        "enable_infinite_scroll": False,
        "max_scroll_iterations": 4,
        "follow_api_pagination": False,
        "request_delay_seconds": 0,
    }
    values.update(overrides)
    return PaginationOptions(**values)  # type: ignore[arg-type]


class _Handler(BaseHTTPRequestHandler):
    hits: list[str] = []
    lock = threading.Lock()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        with self.lock:
            self.hits.append(self.path)
        status, content_type, body = _route(parsed.path, parse_qs(parsed.query))
        self._send(status, content_type, body)

    def do_HEAD(self) -> None:  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _route(path: str, query: dict[str, list[str]]) -> tuple[int, str, bytes]:
    if path.startswith("/files/") and path.endswith(".pdf"):
        return 200, "application/pdf", _PDF
    if path == "/num":
        page = query.get("page", ["1"])[0]
        if page == "3":
            return 200, "text/html; charset=utf-8", _html_page('<a href="/files/n3.pdf">Three</a>')
        nxt = ""
        if page == "1":
            nxt = (
                '<nav class="pagination" aria-label="Pagination">'
                '<a href="/num?page=2">2</a><a href="/num?page=3">3</a></nav>'
                '<footer><a href="/about">2</a></footer>'
            )
        return 200, "text/html; charset=utf-8", _html_page(
            nxt,
            f'<a href="/files/n{page}.pdf">Page {page}</a>',
        )
    if path == "/rel/1":
        return 200, "text/html; charset=utf-8", _html_page(
            '<a href="/files/rel-1.pdf">One</a>',
            '<a rel="next" href="/rel/2">Next</a>',
        )
    if path == "/rel/2":
        return 200, "text/html; charset=utf-8", _html_page('<a href="/files/rel-2.pdf">Two</a>')
    if path == "/js":
        return 200, "text/html; charset=utf-8", _html_page(
            '<div id="docs"><a href="/files/js-1.pdf">One</a></div>',
            '<button type="button">Next</button>',
            """
            <script>
              document.querySelector("button").addEventListener("click", (event) => {
                document.getElementById("docs").insertAdjacentHTML(
                  "beforeend",
                  '<a href="/files/js-2.pdf">Two</a>'
                );
                event.currentTarget.disabled = true;
              });
            </script>
            """,
        )
    if path == "/scroll":
        return 200, "text/html; charset=utf-8", _html_page(
            '<div style="min-height:4000px"><a href="/files/scroll-1.pdf">One</a></div>',
            """
            <script>
              let added = false;
              window.addEventListener("scroll", () => {
                if (added || (window.scrollY || 0) < 80) return;
                added = true;
                document.body.insertAdjacentHTML(
                  "beforeend",
                  '<a href="/files/scroll-2.pdf">Two</a>'
                );
              });
            </script>
            """,
        )
    if path == "/loop/1":
        return 200, "text/html; charset=utf-8", _html_page(
            '<a href="/files/loop-1.pdf">One</a><a rel="next" href="/loop/2">Next</a>'
        )
    if path == "/loop/2":
        return 200, "text/html; charset=utf-8", _html_page(
            '<a href="/files/loop-2.pdf">Two</a><a rel="next" href="/loop/1">Next</a>'
        )
    if path == "/dup/1":
        return 200, "text/html; charset=utf-8", _html_page(
            '<a href="/files/same.pdf?token=keep">Same</a><a rel="next" href="/dup/2">Next</a>'
        )
    if path == "/dup/2":
        return 200, "text/html; charset=utf-8", _html_page(
            '<a href="/files/same.pdf?token=keep">Same</a>'
        )
    if path == "/fail":
        page = query.get("page", ["1"])[0]
        if page == "2":
            return 500, "text/html; charset=utf-8", b"<html>unavailable</html>"
        pager = ""
        if page == "1":
            pager = (
                '<nav class="pagination"><a href="/fail?page=2">2</a>'
                '<a href="/fail?page=3">3</a></nav>'
            )
        return 200, "text/html; charset=utf-8", _html_page(
            pager,
            f'<a href="/files/fail-{page}.pdf">Page {page}</a>',
        )
    if path == "/api-page":
        return 200, "text/html; charset=utf-8", _html_page(
            '<script>fetch("/api/paged?page=1");</script>'
        )
    if path == "/api/paged":
        page = query.get("page", ["1"])[0]
        payload = {
            "items": [{"pdfUrl": f"/files/api-page-{page}.pdf"}],
            "pagination": {"currentPage": int(page), "totalPages": 2},
        }
        return 200, "application/json", json.dumps(payload).encode("utf-8")
    if path == "/api-offset":
        return 200, "text/html; charset=utf-8", _html_page(
            '<script>fetch("/api/offset?offset=0&limit=1");</script>'
        )
    if path == "/api/offset":
        offset = int(query.get("offset", ["0"])[0])
        payload = {
            "items": [{"pdfUrl": f"/files/api-offset-{offset}.pdf"}],
            "hasMore": offset == 0,
            "totalCount": 2,
        }
        return 200, "application/json", json.dumps(payload).encode("utf-8")
    if path == "/api-cursor":
        return 200, "text/html; charset=utf-8", _html_page(
            '<script>fetch("/api/cursor?cursor=start");</script>'
        )
    if path == "/api/cursor":
        cursor = query.get("cursor", ["start"])[0]
        item = "cur-1" if cursor == "start" else "cur-2"
        payload = {
            "items": [{"pdfUrl": f"/files/{item}.pdf"}],
            "nextCursor": "page-two",
        }
        return 200, "application/json", json.dumps(payload).encode("utf-8")
    if path == "/api-next":
        return 200, "text/html; charset=utf-8", _html_page(
            '<script>fetch("/api/explicit?page=1");</script>'
        )
    if path == "/api/explicit":
        page = query.get("page", ["1"])[0]
        payload: dict[str, object] = {"items": [{"pdfUrl": f"/files/explicit-{page}.pdf"}]}
        if page == "1":
            payload["next"] = "/api/explicit?page=2"
        return 200, "application/json", json.dumps(payload).encode("utf-8")
    return 404, "text/plain; charset=utf-8", b"no"


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
class PaginationBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        host, port = cls.server.server_address[:2]
        cls.base = f"http://{host}:{port}"
        cls.settings = replace(
            load_settings(),
            allow_private_networks=True,
            network_idle_timeout_ms=1_500,
            playwright_timeout_ms=15_000,
            headless=True,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self) -> None:
        with _Handler.lock:
            _Handler.hits.clear()

    def _scan(self, path: str, **overrides: object):
        return WebsiteScanner(self.settings).scan(
            f"{self.base}{path}",
            ScanMode.HTML,
            _options(**overrides),
        )

    def _urls(self, outcome) -> list[str]:
        return [item.url for item in outcome.pdfs]

    def test_numbered_pages_include_later_pdfs_and_skip_footer_links(self) -> None:
        outcome = self._scan("/num?page=1")
        urls = self._urls(outcome)
        self.assertIn(f"{self.base}/files/n1.pdf", urls)
        self.assertIn(f"{self.base}/files/n2.pdf", urls)
        self.assertIn(f"{self.base}/files/n3.pdf", urls)
        self.assertFalse(any(hit.startswith("/about") for hit in _Handler.hits))
        self.assertIn("HTML pagination", outcome.pagination.methods)

    def test_next_link_opens_the_following_page(self) -> None:
        outcome = self._scan("/rel/1")
        urls = self._urls(outcome)
        self.assertIn(f"{self.base}/files/rel-1.pdf", urls)
        self.assertIn(f"{self.base}/files/rel-2.pdf", urls)
        self.assertEqual(outcome.pagination.pages_scanned, 2)
        self.assertIn("No further", outcome.pagination.stopped_reason)

    def test_javascript_next_button_updates_the_page(self) -> None:
        outcome = self._scan("/js", follow_next_controls=True)
        urls = self._urls(outcome)
        self.assertIn(f"{self.base}/files/js-1.pdf", urls)
        self.assertIn(f"{self.base}/files/js-2.pdf", urls)
        self.assertIn("JavaScript pagination", outcome.pagination.methods)

    def test_api_page_numbers(self) -> None:
        outcome = self._scan("/api-page", follow_api_pagination=True)
        urls = self._urls(outcome)
        self.assertIn(f"{self.base}/files/api-page-1.pdf", urls)
        self.assertIn(f"{self.base}/files/api-page-2.pdf", urls)
        self.assertGreaterEqual(outcome.pagination.api_requests, 1)

    def test_api_offset(self) -> None:
        outcome = self._scan("/api-offset", follow_api_pagination=True)
        urls = self._urls(outcome)
        self.assertIn(f"{self.base}/files/api-offset-0.pdf", urls)
        self.assertIn(f"{self.base}/files/api-offset-1.pdf", urls)

    def test_api_cursor_stops_when_the_cursor_repeats(self) -> None:
        outcome = self._scan("/api-cursor", follow_api_pagination=True)
        urls = self._urls(outcome)
        self.assertIn(f"{self.base}/files/cur-1.pdf", urls)
        self.assertIn(f"{self.base}/files/cur-2.pdf", urls)
        followed = [hit for hit in _Handler.hits if "cursor=page-two" in hit]
        self.assertEqual(len(followed), 1)
        self.assertTrue(any("cursor" in note.lower() for note in outcome.pagination.skipped))

    def test_api_explicit_next_url(self) -> None:
        outcome = self._scan("/api-next", follow_api_pagination=True)
        urls = self._urls(outcome)
        self.assertIn(f"{self.base}/files/explicit-1.pdf", urls)
        self.assertIn(f"{self.base}/files/explicit-2.pdf", urls)
        self.assertEqual(
            len([hit for hit in _Handler.hits if hit.startswith("/api/explicit")]),
            2,
        )

    def test_infinite_scroll_collects_the_revealed_pdf(self) -> None:
        outcome = self._scan(
            "/scroll",
            enable_infinite_scroll=True,
            max_scroll_iterations=4,
        )
        urls = self._urls(outcome)
        self.assertIn(f"{self.base}/files/scroll-1.pdf", urls)
        self.assertIn(f"{self.base}/files/scroll-2.pdf", urls)
        self.assertIn("Infinite scroll", outcome.pagination.methods)

    def test_repeated_page_addresses_do_not_loop(self) -> None:
        outcome = self._scan("/loop/1", max_pages=8)
        self.assertEqual(outcome.pagination.pages_scanned, 2)
        self.assertEqual(len([hit for hit in _Handler.hits if hit.startswith("/loop/1")]), 1)
        self.assertIn(f"{self.base}/files/loop-2.pdf", self._urls(outcome))

    def test_duplicate_pdf_across_pages_is_stored_once(self) -> None:
        outcome = self._scan("/dup/1")
        self.assertEqual(len(outcome.pdfs), 1)
        self.assertGreaterEqual(outcome.duplicates_removed, 1)
        self.assertIn("token=keep", outcome.pdfs[0].url)
        with tempfile.TemporaryDirectory() as folder:
            database = Database(Path(folder) / "pdfs.db")
            database.initialize()
            database.save_discovered(list(outcome.pdfs), source_url=f"{self.base}/dup/1")
            database.save_discovered(list(outcome.pdfs), source_url=f"{self.base}/dup/2")
            self.assertEqual(len(database.list_discovered()), 1)

    def test_a_failed_page_does_not_stop_the_next_page(self) -> None:
        outcome = self._scan("/fail?page=1", max_empty_attempts=3)
        urls = self._urls(outcome)
        self.assertIn(f"{self.base}/files/fail-1.pdf", urls)
        self.assertIn(f"{self.base}/files/fail-3.pdf", urls)
        self.assertTrue(any("500" in error for error in outcome.pagination.errors))

    def test_max_pages_and_time_limit_stop_the_scan(self) -> None:
        limited = self._scan("/num?page=1", max_pages=2)
        limited_urls = self._urls(limited)
        self.assertIn(f"{self.base}/files/n1.pdf", limited_urls)
        self.assertIn(f"{self.base}/files/n2.pdf", limited_urls)
        self.assertNotIn(f"{self.base}/files/n3.pdf", limited_urls)
        self.assertIn("maximum of 2", limited.pagination.stopped_reason)

        timed = self._scan("/rel/1", max_duration_seconds=0)
        self.assertEqual(timed.pagination.pages_scanned, 1)
        self.assertNotIn(f"{self.base}/files/rel-2.pdf", self._urls(timed))
        self.assertIn("time limit", timed.pagination.stopped_reason)

    def test_pagination_disabled_stays_on_the_first_page(self) -> None:
        outcome = WebsiteScanner(self.settings).scan(f"{self.base}/rel/1", ScanMode.HTML)
        self.assertIsNone(outcome.pagination)
        self.assertIn(f"{self.base}/files/rel-1.pdf", self._urls(outcome))
        self.assertNotIn(f"{self.base}/files/rel-2.pdf", self._urls(outcome))

    def test_stop_event_ends_the_scan_after_the_current_page(self) -> None:
        stop = threading.Event()
        stop.set()
        outcome = WebsiteScanner(self.settings).collect(
            f"{self.base}/rel/1",
            ScanMode.HTML,
            stop,
            None,
            _options(),
        )
        self.assertEqual(outcome.pagination.pages_scanned, 1)
        self.assertEqual(outcome.pagination.stopped_reason, "Stopped by user.")


if __name__ == "__main__":
    unittest.main()
