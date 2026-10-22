"""Tests for PDF URL detection that do not open a website."""

from dataclasses import dataclass

from scraper.pdf_detector import PdfDetector, indicates_pdf_response, is_direct_pdf_url


@dataclass(frozen=True)
class Link:
    url: str
    text: str = ""


def test_detects_pdf_extension_with_query_and_mixed_case() -> None:
    assert is_direct_pdf_url("https://example.com/files/report.pdf")
    assert is_direct_pdf_url("https://example.com/files/report.PDF?download=1&token=abc")
    assert is_direct_pdf_url("https://example.com/files/report.pdf/")


def test_detects_pdf_named_in_the_query_string() -> None:
    assert is_direct_pdf_url("https://example.com/download?file=report.pdf")
    assert is_direct_pdf_url("https://example.com/view?id=12&format=pdf")
    assert is_direct_pdf_url("https://example.com/export?mime=application/pdf")


def test_does_not_treat_unrelated_pdf_text_as_a_file() -> None:
    assert not is_direct_pdf_url("https://example.com/search?q=pdf")
    assert not is_direct_pdf_url("https://example.com/articles/how-to-read")


def test_content_type_and_disposition_headers() -> None:
    assert indicates_pdf_response("application/pdf; charset=binary")
    assert indicates_pdf_response("application/octet-stream", 'attachment; filename="report.pdf"')
    assert indicates_pdf_response(None, "attachment; filename*=UTF-8''Q3%20Report.pdf")
    assert not indicates_pdf_response("text/html")
    assert not indicates_pdf_response("application/octet-stream", 'attachment; filename="notes.txt"')


def test_removes_duplicate_urls_and_preserves_the_first_original_url() -> None:
    links = [
        Link("https://example.com/a.pdf#page=1"),
        Link("https://example.com/a.pdf"),
        Link("https://example.com/a.pdf?download=1"),
    ]
    found = PdfDetector().detect(links)
    assert [item.url for item in found] == [
        "https://example.com/a.pdf#page=1",
        "https://example.com/a.pdf?download=1",
    ]
    assert found[0].file_name == "a.pdf"


def test_content_type_probe_is_limited_to_likely_pdf_links() -> None:
    calls: list[str] = []

    def lookup(url: str) -> tuple[str | None, str | None]:
        calls.append(url)
        return ("application/pdf", None)

    links = [
        Link("https://example.com/about", "About"),
        Link("https://example.com/files/a.pdf", "Annual report"),
        Link("https://example.com/download/42", "Download PDF"),
    ]
    found = PdfDetector(max_content_type_checks=10).detect(links, lookup)

    assert calls == ["https://example.com/download/42"]
    assert [item.url for item in found] == [
        "https://example.com/files/a.pdf",
        "https://example.com/download/42",
    ]
    assert found[1].file_name == "42.pdf"


def test_probe_failure_does_not_drop_extension_matches() -> None:
    def lookup(url: str) -> tuple[str | None, str | None]:
        raise RuntimeError(f"failed {url}")

    found = PdfDetector().detect(
        [Link("https://example.com/a.pdf"), Link("https://example.com/maybe", "PDF")],
        lookup,
    )
    assert [item.url for item in found] == ["https://example.com/a.pdf"]


def test_stops_after_the_configured_number_of_probes() -> None:
    calls: list[str] = []

    def lookup(url: str) -> tuple[str | None, str | None]:
        calls.append(url)
        return ("text/html", None)

    links = [Link(f"https://example.com/item-{index}", "PDF") for index in range(4)]
    PdfDetector(max_content_type_checks=2).detect(links, lookup)
    assert len(calls) == 2


def test_uses_content_disposition_filename_when_the_url_has_none() -> None:
    found = PdfDetector().detect(
        [Link("https://example.com/download/42", "PDF")],
        lambda url: ("application/octet-stream", 'attachment; filename="Board-Minutes.pdf"'),
    )
    assert found[0].url == "https://example.com/download/42"
    assert found[0].file_name == "Board-Minutes.pdf"
