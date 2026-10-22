"""Discovered PDF records shown in the scanner results."""

from dataclasses import dataclass, field
from enum import StrEnum
from uuid import uuid4


class PdfStatus(StrEnum):
    """Lifecycle state stored for a PDF link."""

    DISCOVERED = "Discovered"


@dataclass(frozen=True)
class PdfItem:
    """A PDF link found on a single scanned page."""

    file_name: str
    url: str
    status: PdfStatus = PdfStatus.DISCOVERED
    id: str = field(default_factory=lambda: str(uuid4()))
