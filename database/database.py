"""SQLite storage for PDFs discovered by the scanner."""

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from models.pdf_item import PdfItem
from utils.logger import get_logger

_CREATE_DISCOVERED_PDFS = """
CREATE TABLE IF NOT EXISTS discovered_pdfs (
    id TEXT PRIMARY KEY,
    file_name TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    source_page TEXT,
    discovered_at TEXT NOT NULL
)
"""


class Database:
    """Local database initialization and discovered-PDF persistence."""

    def __init__(self, database_path: Path) -> None:
        self._path = database_path
        self._logger = get_logger(__name__)

    def initialize(self) -> None:
        """Create the data directory and the discovered PDF table."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(_CREATE_DISCOVERED_PDFS)
        self._logger.info("Database ready at %s", self._path)

    def save_discovered(self, items: Sequence[PdfItem], source_page: str) -> None:
        """Insert newly found PDFs. An existing URL keeps its original id."""
        discovered_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        rows = [
            (item.id, item.file_name, item.url, str(item.status), source_page, discovered_at)
            for item in items
        ]
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO discovered_pdfs (id, file_name, url, status, source_page, discovered_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    file_name = excluded.file_name,
                    status = excluded.status,
                    source_page = excluded.source_page,
                    discovered_at = excluded.discovered_at
                """,
                rows,
            )

    def list_discovered(self) -> list[dict[str, str | None]]:
        """Return saved PDF rows, newest first."""
        with self._connect() as connection:
            cursor = connection.execute(
                """
                SELECT id, file_name, url, status, source_page, discovered_at
                FROM discovered_pdfs
                ORDER BY discovered_at DESC, file_name ASC
                """
            )
            return [dict(row) for row in cursor.fetchall()]

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
