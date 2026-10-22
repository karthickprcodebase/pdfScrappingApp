"""Service tests with a fake scanner so no website is contacted."""

from pathlib import Path

from database.database import Database
from scraper.pdf_detector import DetectedPdf
from scraper.website_scanner import PageScanResult, ScanError
from services.scanner_service import ScannerService


class FakeScanner:
    def __init__(self, result: PageScanResult | None = None, error: Exception | None = None) -> None:
        self.result = result or PageScanResult(final_url="https://example.com", pdfs=[])
        self.error = error
        self.calls: list[str] = []

    def scan(self, url: str) -> PageScanResult:
        self.calls.append(url)
        if self.error:
            raise self.error
        return self.result


def _service(tmp_path: Path, scanner: FakeScanner) -> ScannerService:
    return ScannerService(scanner=scanner, database=Database(tmp_path / "pdf_scraper.db"))  # type: ignore[arg-type]


def test_empty_and_invalid_urls_do_not_scan(tmp_path: Path) -> None:
    scanner = FakeScanner()
    service = _service(tmp_path, scanner)

    empty = service.scan("   ")
    invalid = service.scan("example.com")

    assert empty.error == "Enter a website URL."
    assert invalid.error == "Enter a valid website URL that starts with http:// or https://."
    assert scanner.calls == []


def test_scan_error_is_returned_without_a_stack_trace(tmp_path: Path) -> None:
    scanner = FakeScanner(error=ScanError("The website could not be reached. Check the URL and your network connection."))
    outcome = _service(tmp_path, scanner).scan("https://missing.example")

    assert outcome.error == "The website could not be reached. Check the URL and your network connection."
    assert outcome.pdfs == []
    assert "Traceback" not in outcome.error


def test_unexpected_scanner_errors_stay_generic(tmp_path: Path) -> None:
    scanner = FakeScanner(error=RuntimeError("socket boom"))
    outcome = _service(tmp_path, scanner).scan("https://example.com")

    assert outcome.error == "Something went wrong while scanning this website."
    assert "socket boom" not in outcome.error


def test_no_pdfs_returns_an_info_message(tmp_path: Path) -> None:
    scanner = FakeScanner(PageScanResult(final_url="https://example.com/page", pdfs=[]))
    outcome = _service(tmp_path, scanner).scan("https://example.com/page")

    assert outcome.pdfs == []
    assert outcome.info == "No PDFs were found on this page."
    assert outcome.page_url == "https://example.com/page"


def test_discovered_pdfs_are_saved(tmp_path: Path) -> None:
    scanner = FakeScanner(
        PageScanResult(
            final_url="https://example.com/docs",
            pdfs=[DetectedPdf(url="https://example.com/docs/a.pdf", file_name="a.pdf")],
        )
    )
    service = _service(tmp_path, scanner)
    outcome = service.scan("https://example.com/docs")

    assert [item.url for item in outcome.pdfs] == ["https://example.com/docs/a.pdf"]
    assert outcome.pdfs[0].status == "Discovered"
    assert outcome.error is None
    stored = service._database.list_discovered()
    assert stored[0]["url"] == "https://example.com/docs/a.pdf"
    assert stored[0]["status"] == "Discovered"


def test_results_are_returned_when_storage_fails(tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("block", encoding="utf-8")
    scanner = FakeScanner(
        PageScanResult(
            final_url="https://example.com",
            pdfs=[DetectedPdf(url="https://example.com/a.pdf", file_name="a.pdf")],
        )
    )
    service = ScannerService(scanner=scanner, database=Database(blocker / "pdf.db"))  # type: ignore[arg-type]
    outcome = service.scan("https://example.com")

    assert [item.file_name for item in outcome.pdfs] == ["a.pdf"]
    assert outcome.error is None
