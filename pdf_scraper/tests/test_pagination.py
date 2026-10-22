"""Pagination decisions that do not need a browser."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from models.pdf_item import DiscoveryMethod  # noqa: E402
from scraper.dedup import PdfCandidate, merge_candidates  # noqa: E402
from scraper.pagination import (  # noqa: E402
    PaginationOptions,
    extract_api_page_hint,
    find_html_pagination,
    next_api_url,
    stop_reason,
)

_PAGE = "https://docs.example.com/list?page=1"


class HtmlPaginationTests(unittest.TestCase):
    def test_numbered_links_inside_a_pager_skip_unrelated_numbers(self) -> None:
        html = """
        <nav class="pagination" aria-label="Pagination">
          <a href="/list?page=2">2</a>
          <a href="/list?page=3">3</a>
          <a href="/list?page=1">1</a>
        </nav>
        <footer><a href="/about">2</a></footer>
        <a href="/files/report.pdf">Report</a>
        """
        found = find_html_pagination(html, _PAGE)
        urls = [target.url for target in found.targets]
        self.assertEqual(
            urls,
            [
                "https://docs.example.com/list?page=2",
                "https://docs.example.com/list?page=3",
            ],
        )

    def test_rel_next_is_followed_and_previous_is_not(self) -> None:
        html = """
        <a rel="prev" href="/list?page=1">Previous</a>
        <a rel="next" href="/list?page=2">Next</a>
        """
        found = find_html_pagination(html, _PAGE)
        self.assertEqual(len(found.targets), 1)
        self.assertEqual(found.targets[0].url, "https://docs.example.com/list?page=2")
        self.assertEqual(found.targets[0].kind, "rel_next")

    def test_several_bare_next_links_are_not_followed(self) -> None:
        html = """
        <a href="/news">Next</a>
        <a href="/funds">Next</a>
        """
        found = find_html_pagination(html, "https://docs.example.com/list")
        self.assertEqual(found.targets, ())
        self.assertTrue(found.skipped)


class ApiPaginationTests(unittest.TestCase):
    def test_page_number_advances_until_the_last_page(self) -> None:
        first = "https://docs.example.com/api/items?page=1&sort=name"
        hint = extract_api_page_hint(
            {"pagination": {"currentPage": 1, "totalPages": 2}},
            first,
        )
        self.assertIsNotNone(hint)
        assert hint is not None
        nxt = next_api_url(hint, set(), set())
        self.assertEqual(nxt, "https://docs.example.com/api/items?page=2&sort=name")
        last = extract_api_page_hint(
            {"pagination": {"currentPage": 2, "totalPages": 2}},
            nxt or first,
        )
        self.assertIsNotNone(last)
        assert last is not None
        self.assertIsNone(next_api_url(last, set(), set()))

    def test_page_number_is_not_invented_without_a_page_parameter(self) -> None:
        hint = extract_api_page_hint(
            {"totalPages": 4, "currentPage": 1},
            "https://docs.example.com/api/items",
        )
        self.assertIsNotNone(hint)
        assert hint is not None
        self.assertIsNone(next_api_url(hint, set(), set()))

    def test_offset_advances_by_the_observed_limit(self) -> None:
        first = "https://docs.example.com/api/items?offset=0&limit=2"
        hint = extract_api_page_hint({"hasMore": True, "totalCount": 4}, first)
        self.assertIsNotNone(hint)
        assert hint is not None
        nxt = next_api_url(hint, set(), set())
        self.assertEqual(nxt, "https://docs.example.com/api/items?offset=2&limit=2")
        done = extract_api_page_hint({"hasMore": False, "totalCount": 4}, nxt or first)
        self.assertIsNotNone(done)
        assert done is not None
        self.assertIsNone(next_api_url(done, set(), set()))

    def test_cursor_replaces_the_existing_cursor_and_stops_when_repeated(self) -> None:
        first = "https://docs.example.com/api/items?cursor=start"
        hint = extract_api_page_hint({"nextCursor": "abc"}, first)
        self.assertIsNotNone(hint)
        assert hint is not None
        seen: set[str] = set()
        nxt = next_api_url(hint, seen, set())
        self.assertEqual(nxt, "https://docs.example.com/api/items?cursor=abc")
        seen.add("abc")
        repeated = extract_api_page_hint({"nextCursor": "abc"}, nxt or first)
        self.assertIsNotNone(repeated)
        assert repeated is not None
        self.assertIsNone(next_api_url(repeated, seen, set()))

    def test_cursor_is_not_added_when_the_request_had_none(self) -> None:
        hint = extract_api_page_hint(
            {"nextCursor": "abc"},
            "https://docs.example.com/api/items",
        )
        self.assertIsNotNone(hint)
        assert hint is not None
        self.assertIsNone(next_api_url(hint, set(), set()))

    def test_explicit_next_url_is_preferred(self) -> None:
        hint = extract_api_page_hint(
            {"next": "/api/items?page=2", "pagination": {"currentPage": 1, "totalPages": 5}},
            "https://docs.example.com/api/items?page=1",
        )
        self.assertIsNotNone(hint)
        assert hint is not None
        self.assertEqual(
            next_api_url(hint, set(), set()),
            "https://docs.example.com/api/items?page=2",
        )

    def test_seen_next_url_is_not_returned_again(self) -> None:
        hint = extract_api_page_hint(
            {"nextUrl": "https://docs.example.com/api/items?page=2"},
            "https://docs.example.com/api/items?page=1",
        )
        self.assertIsNotNone(hint)
        assert hint is not None
        seen = {"https://docs.example.com/api/items?page=2"}
        self.assertIsNone(next_api_url(hint, set(), seen))

    def test_http_429_does_not_continue(self) -> None:
        hint = extract_api_page_hint(
            {"next": "https://docs.example.com/api/items?page=2"},
            "https://docs.example.com/api/items?page=1",
            http_status=429,
        )
        self.assertIsNotNone(hint)
        assert hint is not None
        self.assertIsNone(next_api_url(hint, set(), set()))


class PaginationLimitTests(unittest.TestCase):
    def test_options_keep_a_conservative_page_limit(self) -> None:
        options = PaginationOptions(enabled=True, max_pages=500, max_duration_seconds=5_000).clamped()
        self.assertEqual(options.max_pages, 100)
        self.assertEqual(options.max_duration_seconds, 900)
        self.assertEqual(PaginationOptions(max_duration_seconds=0).clamped().max_duration_seconds, 0)

    def test_stop_reasons_cover_limits_and_cancellation(self) -> None:
        self.assertIn(
            "maximum of 2",
            stop_reason(
                pages_visited=2,
                max_pages=2,
                elapsed_seconds=1,
                max_duration_seconds=30,
                empty_streak=0,
                max_empty_attempts=2,
                cancelled=False,
                rate_limited=False,
                queue_empty=False,
            )
            or "",
        )
        self.assertIn(
            "time limit",
            stop_reason(
                pages_visited=1,
                max_pages=20,
                elapsed_seconds=0,
                max_duration_seconds=0,
                empty_streak=0,
                max_empty_attempts=2,
                cancelled=False,
                rate_limited=False,
                queue_empty=False,
            )
            or "",
        )
        self.assertEqual(
            stop_reason(
                pages_visited=1,
                max_pages=20,
                elapsed_seconds=1,
                max_duration_seconds=30,
                empty_streak=0,
                max_empty_attempts=2,
                cancelled=True,
                rate_limited=False,
                queue_empty=False,
            ),
            "Stopped by user.",
        )

    def test_the_same_pdf_on_two_pages_collapses_to_one_row(self) -> None:
        url = "https://cdn.example.com/files/same.pdf?token=keep"
        merged = merge_candidates(
            [
                PdfCandidate(url, "same.pdf", DiscoveryMethod.HTML, source_page_url="https://docs.example.com/1"),
                PdfCandidate(url, "same.pdf", DiscoveryMethod.API_JSON, source_page_url="https://docs.example.com/2"),
            ]
        )
        self.assertEqual(len(merged.candidates), 1)
        self.assertEqual(merged.duplicates_removed, 1)
        self.assertEqual(merged.candidates[0].url, url)
        self.assertIn("HTML", merged.candidates[0].methods_label)
        self.assertIn("API_JSON", merged.candidates[0].methods_label)


if __name__ == "__main__":
    unittest.main()
