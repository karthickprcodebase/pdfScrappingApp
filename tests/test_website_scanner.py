"""Tests for anchor extraction that do not launch a browser."""

from scraper.website_scanner import WebsiteScanner, extract_anchor_links


def test_extracts_and_resolves_anchors() -> None:
    html = """
    <html>
      <head><base href="https://cdn.example.com/docs/"></head>
      <body>
        <a href="report.pdf">Annual report</a>
        <a href="/files/other.PDF?download=1">Other</a>
        <a href="javascript:void(0)">Skip</a>
        <a href="mailto:person@example.com">Email</a>
      </body>
    </html>
    """
    links = extract_anchor_links(html, "https://example.com/page")
    assert [(link.url, link.text) for link in links] == [
        ("https://cdn.example.com/docs/report.pdf", "Annual report"),
        ("https://cdn.example.com/files/other.PDF?download=1", "Other"),
    ]


def test_scanner_can_be_instantiated_without_opening_a_browser() -> None:
    scanner = WebsiteScanner()
    assert scanner is not None
