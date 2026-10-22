"""Tests for the discovered-PDF table."""

from pathlib import Path

from database.database import Database
from models.pdf_item import PdfItem, PdfStatus


def test_initialize_is_safe_to_repeat(tmp_path: Path) -> None:
    database = Database(tmp_path / "pdf_scraper.db")
    database.initialize()
    database.initialize()
    assert database.list_discovered() == []


def test_save_lists_rows_and_keeps_the_original_id(tmp_path: Path) -> None:
    database = Database(tmp_path / "pdf_scraper.db")
    database.initialize()
    first = PdfItem(
        file_name="report.pdf",
        url="https://example.com/report.pdf",
        status=PdfStatus.DISCOVERED,
        id="original-id",
    )
    database.save_discovered([first], source_page="https://example.com/docs")

    updated = PdfItem(
        file_name="report-final.pdf",
        url="https://example.com/report.pdf",
        status=PdfStatus.DISCOVERED,
        id="new-id",
    )
    database.save_discovered([updated], source_page="https://example.com/docs")

    rows = database.list_discovered()
    assert len(rows) == 1
    assert rows[0]["id"] == "original-id"
    assert rows[0]["file_name"] == "report-final.pdf"
    assert rows[0]["url"] == "https://example.com/report.pdf"
    assert rows[0]["status"] == "Discovered"
    assert rows[0]["source_page"] == "https://example.com/docs"
    assert rows[0]["discovered_at"]
