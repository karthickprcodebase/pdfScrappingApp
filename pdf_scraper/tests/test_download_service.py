"""Tests for PDF byte checks, content hashes, and saving files locally."""

from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from database.database import Database  # noqa: E402
from models.pdf_item import DownloadStatus, PdfItem, PdfStatus, new_pdf_id  # noqa: E402
from services.download_service import (  # noqa: E402
    download_pdfs,
    duplicate_content_groups,
    looks_like_pdf_bytes,
    sha256_file,
)

_PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"


class DownloadServiceTests(unittest.TestCase):
    def test_recognizes_pdf_magic_bytes(self) -> None:
        self.assertTrue(looks_like_pdf_bytes(b"%PDF-1.7\n"))
        self.assertFalse(looks_like_pdf_bytes(b"<html></html>"))
        self.assertFalse(looks_like_pdf_bytes(b""))

    def test_groups_different_urls_with_the_same_file_hash(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "report.pdf"
            path.write_bytes(b"%PDF-1.4\nsame-bytes\n")
            digest = sha256_file(path)
            first = PdfItem(
                id="1",
                file_name="report.pdf",
                url="https://example.com/a.pdf",
                status=PdfStatus.DISCOVERED,
                content_hash=digest,
                source_page_url="https://example.com/one",
            )
            second = PdfItem(
                id="2",
                file_name="copy.pdf",
                url="https://cdn.example.com/b.pdf?sig=1",
                status=PdfStatus.DISCOVERED,
                content_hash=digest,
                source_page_url="https://example.com/two",
            )
            other = PdfItem(
                id="3",
                file_name="other.pdf",
                url="https://example.com/c.pdf",
                status=PdfStatus.DISCOVERED,
            )
            groups = duplicate_content_groups([first, second, other])
        self.assertEqual(len(groups), 1)
        self.assertEqual({item.url for item in groups[0]}, {first.url, second.url})


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/files/report.pdf":
            self._send(200, "application/pdf", _PDF)
            return
        if path == "/files/page.html":
            self._send(200, "text/html; charset=utf-8", b"<html>not a pdf</html>")
            return
        if path == "/files/huge.pdf":
            self._send(200, "application/pdf", _PDF, content_length=50_000_000)
            return
        self._send(404, "text/plain; charset=utf-8", b"missing")

    def log_message(self, format: str, *args: object) -> None:
        return

    def _send(self, status: int, content_type: str, body: bytes, content_length: int | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body) if content_length is None else content_length))
        self.end_headers()
        if content_length is None:
            self.wfile.write(body)


class LocalDownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        host, port = cls.server.server_address[:2]
        cls.base = f"http://{host}:{port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def _item(self, path: str) -> PdfItem:
        url = f"{self.base}{path}"
        return PdfItem(
            id=new_pdf_id(url),
            file_name="report.pdf",
            url=url,
            status=PdfStatus.DISCOVERED,
            source_page_url=f"{self.base}/page",
        )

    def test_saves_a_pdf_and_skips_the_same_file(self) -> None:
        item = self._item("/files/report.pdf")
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            first = download_pdfs(
                [item],
                directory,
                allow_private_networks=True,
                timeout_seconds=5,
                max_bytes=1_000_000,
            )
            self.assertEqual(first.saved, 1)
            saved = first.items[0]
            self.assertEqual(saved.download_status, DownloadStatus.DOWNLOADED)
            self.assertTrue(saved.local_file_path)
            assert saved.local_file_path is not None
            path = Path(saved.local_file_path)
            self.assertTrue(path.is_file())
            self.assertTrue(path.read_bytes().startswith(b"%PDF-"))
            self.assertEqual(saved.content_hash, sha256_file(path))
            second = download_pdfs(
                [saved],
                directory,
                allow_private_networks=True,
                timeout_seconds=5,
                max_bytes=1_000_000,
            )
            self.assertEqual(second.skipped, 1)
            self.assertEqual(second.saved, 0)

    def test_rejects_a_page_that_is_not_a_pdf(self) -> None:
        item = self._item("/files/page.html")
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            batch = download_pdfs(
                [item],
                directory,
                allow_private_networks=True,
                timeout_seconds=5,
                max_bytes=1_000_000,
            )
            self.assertEqual(batch.failed, 1)
            self.assertEqual(batch.items[0].download_status, DownloadStatus.FAILED)
            self.assertIn("did not return a PDF", batch.items[0].error_message or "")
            self.assertEqual(list(directory.glob("*")), [])

    def test_rejects_an_oversized_file_and_a_missing_file(self) -> None:
        huge = self._item("/files/huge.pdf")
        missing = self._item("/files/missing.pdf")
        with tempfile.TemporaryDirectory() as folder:
            batch = download_pdfs(
                [huge, missing],
                Path(folder),
                allow_private_networks=True,
                timeout_seconds=5,
                max_bytes=1_000,
            )
        self.assertEqual(batch.failed, 2)
        self.assertIn("50 MB", batch.items[0].error_message or "")
        self.assertIn("404", batch.items[1].error_message or "")

    def test_blocks_a_private_address_unless_allowed(self) -> None:
        item = self._item("/files/report.pdf")
        with tempfile.TemporaryDirectory() as folder:
            batch = download_pdfs(
                [item],
                Path(folder),
                allow_private_networks=False,
                timeout_seconds=5,
                max_bytes=1_000_000,
            )
        self.assertEqual(batch.failed, 1)
        self.assertIn("blocked", (batch.items[0].error_message or "").lower())

    def test_database_keeps_the_download_after_another_scan(self) -> None:
        item = self._item("/files/report.pdf")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            batch = download_pdfs(
                [item],
                root / "downloads",
                allow_private_networks=True,
                timeout_seconds=5,
                max_bytes=1_000_000,
            )
            database = Database(root / "pdfs.db")
            database.initialize()
            database.save_discovered([item], source_url=item.source_page_url or item.url)
            database.record_download(batch.items[0])
            database.save_discovered([item], source_url=item.source_page_url or item.url)
            stored = database.list_discovered()
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0].download_status, DownloadStatus.DOWNLOADED)
        self.assertEqual(stored[0].content_hash, batch.items[0].content_hash)
        self.assertEqual(stored[0].local_file_path, batch.items[0].local_file_path)


if __name__ == "__main__":
    unittest.main()
