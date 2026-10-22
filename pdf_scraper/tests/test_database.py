"""Tests for the local discovered-PDF table."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from database.database import Database  # noqa: E402
from models.pdf_item import PdfItem, PdfStatus, new_pdf_id  # noqa: E402


class DatabaseTests(unittest.TestCase):
    def test_saves_and_lists_discovered_pdfs(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            database = Database(Path(folder) / "pdf_scraper.db")
            database.initialize()
            database.initialize()
            first_url = "https://example.com/files/report.pdf?download=1"
            second_url = "https://example.com/files/other.pdf"
            first = PdfItem(
                id=new_pdf_id(first_url),
                file_name="report.pdf",
                url=first_url,
                status=PdfStatus.DISCOVERED,
            )
            second = PdfItem(
                id=new_pdf_id(second_url),
                file_name="other.pdf",
                url=second_url,
                status=PdfStatus.DISCOVERED,
            )
            database.save_discovered([first, second], source_url="https://example.com/reports")
            database.save_discovered([first, second], source_url="https://example.com/reports")

            stored = database.list_discovered()
            self.assertEqual([item.url for item in stored], [second_url, first_url])
            self.assertEqual(stored[0].status, PdfStatus.DISCOVERED)
            self.assertTrue(stored[0].id)

    def test_rescan_updates_filename_and_keeps_existing_status(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            database_path = Path(folder) / "pdf_scraper.db"
            database = Database(database_path)
            database.initialize()
            url = "https://example.com/files/report.pdf"
            original = PdfItem(
                id=new_pdf_id(url),
                file_name="report.pdf",
                url=url,
                status=PdfStatus.DISCOVERED,
            )
            database.save_discovered([original], source_url="https://example.com/a")

            connection = sqlite3.connect(database_path)
            connection.execute(
                "UPDATE discovered_pdfs SET status = ? WHERE url = ?",
                ("downloaded", url),
            )
            connection.commit()
            connection.close()

            renamed = PdfItem(
                id=new_pdf_id(url),
                file_name="renamed.pdf",
                url=url,
                status=PdfStatus.DISCOVERED,
            )
            database.save_discovered([renamed], source_url="https://example.com/b")

            connection = sqlite3.connect(database_path)
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT file_name, status, source_url FROM discovered_pdfs WHERE url = ?",
                (url,),
            ).fetchone()
            count = connection.execute("SELECT COUNT(*) AS count FROM discovered_pdfs").fetchone()
            connection.close()

            self.assertEqual(row["file_name"], "renamed.pdf")
            self.assertEqual(row["status"], "downloaded")
            self.assertEqual(row["source_url"], "https://example.com/b")
            self.assertEqual(count["count"], 1)

    def test_migration_keeps_existing_rows_and_merges_discovery_methods(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            database_path = Path(folder) / "pdf_scraper.db"
            connection = sqlite3.connect(database_path)
            connection.execute(
                """
                CREATE TABLE discovered_pdfs (
                    id TEXT PRIMARY KEY,
                    file_name TEXT NOT NULL,
                    url TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    discovered_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO discovered_pdfs
                (id, file_name, url, status, source_url, discovered_at)
                VALUES ('abc', 'old.pdf', 'https://example.com/old.pdf', 'discovered',
                        'https://example.com', '2026-01-01T00:00:00+00:00')
                """
            )
            connection.commit()
            connection.close()

            database = Database(database_path)
            database.initialize()
            updated = PdfItem(
                id="abc",
                file_name="old.pdf",
                url="https://example.com/old.pdf",
                status=PdfStatus.VALIDATED,
                discovery_method="API_JSON",
                source_page_url="https://example.com/api",
            )
            database.save_discovered([updated], source_url="https://example.com/api")

            connection = sqlite3.connect(database_path)
            connection.row_factory = sqlite3.Row
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(discovered_pdfs)")
            }
            row = connection.execute(
                "SELECT file_name, status, discovery_method FROM discovered_pdfs"
            ).fetchone()
            connection.close()

            self.assertIn("discovery_method", columns)
            self.assertIn("content_hash", columns)
            self.assertEqual(row["file_name"], "old.pdf")
            self.assertEqual(row["status"], "discovered")
            self.assertEqual(row["discovery_method"], "API_JSON")


if __name__ == "__main__":
    unittest.main()
