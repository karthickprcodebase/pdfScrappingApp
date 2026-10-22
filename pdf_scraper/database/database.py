"""SQLite storage for PDFs discovered on a scanned page."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from models.pdf_item import PdfItem, new_pdf_id, parse_download_status, parse_status
from scraper.dedup import merge_discovery_methods

_CREATE_DISCOVERED_PDFS = """
CREATE TABLE IF NOT EXISTS discovered_pdfs (
    id TEXT PRIMARY KEY,
    file_name TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    source_url TEXT NOT NULL,
    discovered_at TEXT NOT NULL,
    source_endpoint TEXT,
    discovery_method TEXT,
    content_type TEXT,
    http_status INTEGER,
    file_size INTEGER,
    local_file_path TEXT,
    error_message TEXT,
    download_status TEXT,
    content_hash TEXT
)
"""

_ADDED_COLUMNS = (
    ("source_endpoint", "TEXT"),
    ("discovery_method", "TEXT"),
    ("content_type", "TEXT"),
    ("http_status", "INTEGER"),
    ("file_size", "INTEGER"),
    ("local_file_path", "TEXT"),
    ("error_message", "TEXT"),
    ("download_status", "TEXT"),
    ("content_hash", "TEXT"),
)


class Database:
    """Small wrapper around the local SQLite file."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def initialize(self) -> None:
        """Create the database file and add any missing columns."""
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        try:
            with connection:
                connection.execute(_CREATE_DISCOVERED_PDFS)
                self._migrate(connection)
        finally:
            connection.close()

    def save_discovered(self, items: list[PdfItem], source_url: str) -> None:
        """Insert newly discovered PDFs. An existing URL keeps its current status."""
        if not items:
            return
        discovered_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        connection = self._connect()
        try:
            with connection:
                for item in items:
                    self._upsert(connection, item, source_url, discovered_at)
        finally:
            connection.close()

    def list_discovered(self) -> list[PdfItem]:
        """Return stored PDFs, newest first."""
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT id, file_name, url, status, source_url, discovered_at,
                       source_endpoint, discovery_method, content_type, http_status,
                       file_size, local_file_path, error_message, download_status,
                       content_hash
                FROM discovered_pdfs
                ORDER BY discovered_at DESC, file_name ASC
                """
            ).fetchall()
        finally:
            connection.close()
        return [_item_from_row(row) for row in rows]

    def record_download(self, item: PdfItem) -> None:
        """Store the local file result for one PDF URL. Discovery status stays as it is."""
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    """
                    UPDATE discovered_pdfs
                    SET download_status = ?,
                        local_file_path = ?,
                        file_size = ?,
                        content_hash = ?,
                        error_message = ?
                    WHERE url = ?
                    """,
                    (
                        item.download_status.value if item.download_status else None,
                        item.local_file_path,
                        item.file_size,
                        item.content_hash,
                        item.error_message,
                        item.url,
                    ),
                )
        finally:
            connection.close()

    def _migrate(self, connection: sqlite3.Connection) -> None:
        existing = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(discovered_pdfs)").fetchall()
        }
        for name, column_type in _ADDED_COLUMNS:
            if name not in existing:
                connection.execute(f"ALTER TABLE discovered_pdfs ADD COLUMN {name} {column_type}")

    def _upsert(
        self,
        connection: sqlite3.Connection,
        item: PdfItem,
        source_url: str,
        discovered_at: str,
    ) -> None:
        page_url = item.source_page_url or source_url
        seen_at = item.discovered_at or discovered_at
        existing = connection.execute(
            """
            SELECT status, discovery_method, download_status
            FROM discovered_pdfs
            WHERE url = ?
            """,
            (item.url,),
        ).fetchone()
        if existing is None:
            connection.execute(
                """
                INSERT INTO discovered_pdfs (
                    id, file_name, url, status, source_url, discovered_at,
                    source_endpoint, discovery_method, content_type, http_status,
                    file_size, local_file_path, error_message, download_status,
                    content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.id or new_pdf_id(item.url),
                    item.file_name,
                    item.url,
                    item.status.value,
                    page_url,
                    seen_at,
                    item.source_endpoint,
                    item.discovery_method,
                    item.content_type,
                    item.http_status,
                    item.file_size,
                    item.local_file_path,
                    item.error_message,
                    item.download_status.value if item.download_status else None,
                    item.content_hash,
                ),
            )
            return
        download_status = existing["download_status"]
        if not download_status and item.download_status:
            download_status = item.download_status.value
        connection.execute(
            """
            UPDATE discovered_pdfs
            SET file_name = ?,
                source_url = ?,
                source_endpoint = COALESCE(?, source_endpoint),
                discovery_method = ?,
                content_type = COALESCE(?, content_type),
                http_status = COALESCE(?, http_status),
                file_size = COALESCE(?, file_size),
                local_file_path = COALESCE(?, local_file_path),
                error_message = COALESCE(?, error_message),
                download_status = ?,
                content_hash = COALESCE(?, content_hash)
            WHERE url = ?
            """,
            (
                item.file_name,
                page_url,
                item.source_endpoint,
                merge_discovery_methods(existing["discovery_method"], item.discovery_method),
                item.content_type,
                item.http_status,
                item.file_size,
                item.local_file_path,
                item.error_message,
                download_status,
                item.content_hash,
                item.url,
            ),
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection


def _item_from_row(row: sqlite3.Row) -> PdfItem:
    return PdfItem(
        id=row["id"],
        file_name=row["file_name"],
        url=row["url"],
        status=parse_status(row["status"]),
        source_page_url=row["source_url"],
        source_endpoint=row["source_endpoint"],
        discovery_method=row["discovery_method"],
        content_type=row["content_type"],
        http_status=row["http_status"],
        file_size=row["file_size"],
        discovered_at=row["discovered_at"],
        local_file_path=row["local_file_path"],
        error_message=row["error_message"],
        download_status=parse_download_status(row["download_status"]),
        content_hash=row["content_hash"],
    )
