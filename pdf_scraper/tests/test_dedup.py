"""Tests for URL identity and duplicate merging."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from models.pdf_item import DiscoveryMethod, PdfStatus  # noqa: E402
from scraper.dedup import PdfCandidate, canonical_url, merge_candidates  # noqa: E402

_SIGNED = "https://cdn.example.com/a.pdf?X-Amz-Signature=abc%2Bdef&X-Amz-Credential=AKIA"


class CanonicalUrlTests(unittest.TestCase):
    def test_preserves_signed_query_and_removes_fragment(self) -> None:
        self.assertEqual(canonical_url(_SIGNED), _SIGNED)
        self.assertEqual(canonical_url(_SIGNED + "#page=2"), _SIGNED)

    def test_does_not_treat_reordered_query_as_the_same_url(self) -> None:
        first = "https://example.com/a.pdf?a=1&b=2"
        second = "https://example.com/a.pdf?b=2&a=1"
        self.assertNotEqual(canonical_url(first), canonical_url(second))


class MergeCandidateTests(unittest.TestCase):
    def test_merges_html_and_network_observations(self) -> None:
        url = "https://example.com/files/report.pdf?download=1"
        merged = merge_candidates(
            [
                PdfCandidate(url, "report.pdf", DiscoveryMethod.HTML, source_page_url="https://example.com"),
                PdfCandidate(
                    url + "#page=3",
                    "report.pdf",
                    DiscoveryMethod.PDF_RESPONSE,
                    content_type="application/pdf",
                    http_status=200,
                    status=PdfStatus.VALIDATED,
                ),
            ]
        )
        self.assertEqual(merged.duplicates_removed, 1)
        self.assertEqual(len(merged.candidates), 1)
        item = merged.candidates[0]
        self.assertEqual(item.url, url)
        self.assertEqual(item.methods_label, "HTML, PDF_RESPONSE")
        self.assertEqual(item.status, PdfStatus.VALIDATED)
        self.assertEqual(item.http_status, 200)

    def test_keeps_distinct_signed_urls(self) -> None:
        first = "https://cdn.example.com/a.pdf?X-Amz-Signature=one"
        second = "https://cdn.example.com/a.pdf?X-Amz-Signature=two"
        merged = merge_candidates(
            [
                PdfCandidate(first, "a.pdf", DiscoveryMethod.API_JSON),
                PdfCandidate(second, "a.pdf", DiscoveryMethod.API_JSON),
            ]
        )
        self.assertEqual(merged.duplicates_removed, 0)
        self.assertEqual([item.url for item in merged.candidates], [first, second])


if __name__ == "__main__":
    unittest.main()
