"""Open one web page with Playwright and collect PDF links from it."""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from playwright.sync_api import Page, sync_playwright

from config import Settings
from models.pdf_item import DiscoveryMethod, PdfItem, PdfStatus, ScanMode, new_pdf_id
from scraper.dedup import PdfCandidate, canonical_url, merge_candidates
from scraper.network_monitor import NetworkMonitor
from scraper.page_actions import click_next_control, document_height, scroll_once
from scraper.pagination import (
    PaginationOptions,
    PaginationProgress,
    find_html_pagination,
    next_api_url,
    same_host,
    stop_reason,
)
from scraper.pdf_detector import (
    LinkCandidate,
    detect_pdfs,
    extract_link_candidates,
    is_pdf_content_type,
    should_probe_content_type,
)
from utils.logger import get_logger
from utils.url_guard import UrlBlockedError, destination_is_blocked, ensure_scan_allowed
from utils.url_utils import redact_url

_BROWSER_MISSING_TEXT = (
    "The browser could not be started. From the project folder run: "
    "python -m playwright install chromium"
)

_T = TypeVar("_T")


class ScanError(Exception):
    """A scan failure whose message is safe to show in the interface."""


@dataclass(frozen=True)
class PaginationSummary:
    """What a multi-page scan did, including paths it skipped."""

    pages_scanned: int = 0
    methods: tuple[str, ...] = ()
    api_requests: int = 0
    errors: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    stopped_reason: str = ""
    duration_seconds: float = 0.0
    pages_remaining: int | None = None


@dataclass(frozen=True)
class ScanOutcome:
    """PDF candidates from one scan, plus inspection counters."""

    pdfs: list[PdfItem]
    duplicates_removed: int = 0
    failed_inspections: int = 0
    pagination: PaginationSummary | None = None


@dataclass
class _ThreadOutcome:
    value: _T | None = None
    error: Exception | None = None


class InteractiveSession:
    """A visible browser session that keeps listening until the user finishes."""

    def __init__(self, scanner: WebsiteScanner, url: str) -> None:
        self.page_url = url
        self._scanner = scanner
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self.outcome: ScanOutcome | None = None
        self.error: Exception | None = None
        self.completed = False

    def start(self) -> None:
        """Open the browser on a background thread."""
        self._thread = threading.Thread(
            target=self._run,
            name="pdf-scraper-interactive",
            daemon=True,
        )
        self._thread.start()

    def wait_until_ready(self, timeout_seconds: float) -> bool:
        """Return True once the window is open or the scan has failed."""
        return self._ready.wait(timeout_seconds)

    def finish(self) -> ScanOutcome:
        """Stop listening, close the browser, and return the merged results."""
        self._stop.set()
        if self._thread is not None:
            timeout = self._scanner.settings.interactive_timeout_seconds + 30
            self._thread.join(timeout)
        if self.error is not None:
            raise self.error
        if self.outcome is None:
            raise ScanError("The interactive scan did not finish.")
        return self.outcome

    def _run(self) -> None:
        try:
            self.outcome = self._scanner.collect(
                self.page_url,
                ScanMode.INTERACTIVE,
                self._stop,
                self._ready,
            )
        except Exception as exc:
            self.error = exc
            self._ready.set()
        finally:
            self.completed = True


class WebsiteScanner:
    """Scan the page the user entered. Network modes also watch that browser session."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._logger = get_logger(__name__)

    def scan(
        self,
        url: str,
        mode: ScanMode = ScanMode.HTML,
        pagination: PaginationOptions | None = None,
        progress: PaginationProgress | None = None,
    ) -> ScanOutcome:
        """Open the page and return the PDF links found for the selected mode."""
        return _run_on_worker_thread(
            lambda: self.collect(url, mode, None, None, pagination, progress)
        )

    def open_interactive(self, url: str) -> InteractiveSession:
        """Open a visible browser and return control to the caller."""
        session = InteractiveSession(self, url)
        session.start()
        return session

    def collect(
        self,
        url: str,
        mode: ScanMode,
        stop_event: threading.Event | None,
        ready_event: threading.Event | None,
        pagination: PaginationOptions | None = None,
        progress: PaginationProgress | None = None,
    ) -> ScanOutcome:
        """Run one browser session. Call this on the thread that owns Playwright."""
        settings = self.settings
        options = (pagination or PaginationOptions()).clamped()
        logged_url = redact_url(url)
        try:
            ensure_scan_allowed(url, allow_private_networks=settings.allow_private_networks)
        except UrlBlockedError as exc:
            raise ScanError(str(exc)) from exc

        network_enabled = mode is not ScanMode.HTML or (
            options.enabled and mode is not ScanMode.INTERACTIVE
        )
        headless = False if mode is ScanMode.INTERACTIVE else settings.headless
        monitor: NetworkMonitor | None = None
        try:
            with sync_playwright() as playwright:
                try:
                    # #region agent log
                    _dbg_path = Path(__file__).resolve().parents[2] / "debug-ce1b42.log"
                    _browsers = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
                    _expected = (
                        Path(_browsers)
                        / "chromium_headless_shell-1243"
                        / "chrome-headless-shell-win64"
                        / "chrome-headless-shell.exe"
                        if _browsers
                        else None
                    )
                    with _dbg_path.open("a", encoding="utf-8") as _dbg:
                        _dbg.write(
                            json.dumps(
                                {
                                    "sessionId": "ce1b42",
                                    "runId": "post-fix",
                                    "hypothesisId": "A,B,C,D",
                                    "location": "website_scanner.py:collect:before_launch",
                                    "message": "About to launch Chromium",
                                    "data": {
                                        "headless": headless,
                                        "playwrightBrowsersPath": _browsers or None,
                                        "expectedExeExists": (
                                            _expected.is_file() if _expected else None
                                        ),
                                        "expectedExe": str(_expected) if _expected else None,
                                        "pythonExecutable": __import__("sys").executable,
                                        "cwd": os.getcwd(),
                                    },
                                    "timestamp": int(time.time() * 1000),
                                }
                            )
                            + "\n"
                        )
                    # #endregion
                    browser, launch_channel = _launch_chromium(
                        playwright, headless=headless
                    )
                    # #region agent log
                    with _dbg_path.open("a", encoding="utf-8") as _dbg:
                        _dbg.write(
                            json.dumps(
                                {
                                    "sessionId": "ce1b42",
                                    "runId": "post-fix",
                                    "hypothesisId": "A,E,F",
                                    "location": "website_scanner.py:collect:after_launch",
                                    "message": "Chromium launch succeeded",
                                    "data": {
                                        "browserVersion": getattr(browser, "version", None),
                                        "launchChannel": launch_channel,
                                    },
                                    "timestamp": int(time.time() * 1000),
                                }
                            )
                            + "\n"
                        )
                    # #endregion
                except Exception as exc:
                    # #region agent log
                    _dbg_path = Path(__file__).resolve().parents[2] / "debug-ce1b42.log"
                    with _dbg_path.open("a", encoding="utf-8") as _dbg:
                        _dbg.write(
                            json.dumps(
                                {
                                    "sessionId": "ce1b42",
                                    "runId": "post-fix",
                                    "hypothesisId": "A,B,C,E,F",
                                    "location": "website_scanner.py:collect:launch_failed",
                                    "message": "Chromium launch failed",
                                    "data": {
                                        "errorType": type(exc).__name__,
                                        "errorText": str(exc)[:800],
                                        "playwrightBrowsersPath": os.environ.get(
                                            "PLAYWRIGHT_BROWSERS_PATH"
                                        ),
                                        "mappedMessage": _browser_start_message(exc),
                                    },
                                    "timestamp": int(time.time() * 1000),
                                }
                            )
                            + "\n"
                        )
                    # #endregion
                    self._logger.exception("Browser startup failed")
                    raise ScanError(_browser_start_message(exc)) from exc
                try:
                    context = browser.new_context(
                        ignore_https_errors=False,
                        accept_downloads=True,
                    )
                    page = context.new_page()
                    page.set_default_navigation_timeout(settings.playwright_timeout_ms)
                    page.set_default_timeout(settings.playwright_timeout_ms)
                    if network_enabled:
                        monitor = NetworkMonitor(settings, url)
                        monitor.attach(page)
                    self._logger.info("Opening %s in %s mode", logged_url, mode.value)
                    try:
                        page.goto(
                            url,
                            wait_until="domcontentloaded",
                            timeout=settings.playwright_timeout_ms,
                        )
                    except Exception as exc:
                        self._logger.exception("Navigation failed for %s", logged_url)
                        raise ScanError(_navigation_message(exc)) from exc
                    final_url = page.url or url
                    try:
                        ensure_scan_allowed(
                            final_url,
                            allow_private_networks=settings.allow_private_networks,
                        )
                    except UrlBlockedError as exc:
                        raise ScanError(str(exc)) from exc
                    if monitor is not None:
                        monitor.set_source_page(final_url)

                    if mode is ScanMode.INTERACTIVE:
                        if ready_event is not None:
                            ready_event.set()
                        html, final_url = self._wait_for_user(page, stop_event, monitor)
                    else:
                        self._wait_for_network_idle(page, redact_url(final_url))
                        html = page.content()

                    if options.enabled and mode is not ScanMode.INTERACTIVE:
                        return self._collect_pages(
                            page,
                            monitor,
                            html,
                            final_url,
                            options,
                            stop_event,
                            progress,
                        )
                    return self._outcome_from_page(page, monitor, html, final_url)
                finally:
                    browser.close()
        except ScanError:
            raise
        except Exception as exc:
            self._logger.exception("Unexpected browser failure for %s", logged_url)
            raise ScanError("The website could not be scanned.") from exc

    def _outcome_from_page(
        self,
        page: Page,
        monitor: NetworkMonitor | None,
        html: str,
        page_url: str,
    ) -> ScanOutcome:
        html_candidates, probe_failures = self._html_candidates(page, html, page_url)
        if monitor is not None:
            monitor.drain()
        observed = list(html_candidates)
        if monitor is not None:
            observed.extend(monitor.candidates)
        merged = merge_candidates(observed)
        items = [_to_pdf_item(candidate) for candidate in merged.candidates]
        failed = probe_failures + (monitor.failed_inspections if monitor else 0)
        self._logger.info(
            "Detected %s PDF link(s) on %s; removed %s duplicate(s)",
            len(items),
            redact_url(page_url),
            merged.duplicates_removed,
        )
        return ScanOutcome(
            pdfs=items,
            duplicates_removed=merged.duplicates_removed,
            failed_inspections=failed,
        )

    def _collect_pages(
        self,
        page: Page,
        monitor: NetworkMonitor | None,
        html: str,
        page_url: str,
        options: PaginationOptions,
        stop_event: threading.Event | None,
        progress: PaginationProgress | None,
    ) -> ScanOutcome:
        """Visit later pages and API responses, then merge every candidate."""
        started = time.monotonic()
        pending: list[tuple[str, str]] = [("loaded", page_url)]
        visited: set[str] = set()
        seen_cursors: set[str] = set()
        seen_api_urls: set[str] = set()
        observed: list[PdfCandidate] = []
        known: set[str] = set()
        methods: set[str] = set()
        errors: list[str] = []
        skipped: list[str] = []
        api_requests = 0
        pages = 0
        browser_pages = 0
        empty_streak = 0
        probe_failures = 0
        network_seen = 0
        stopped = ""
        pages_remaining: int | None = None
        click_blocked = False
        current_html = html
        current_url = page_url
        rate_note_added = False

        while pending:
            kind, target = pending.pop(0)
            cancelled = stop_event is not None and stop_event.is_set()
            elapsed = time.monotonic() - started
            if kind != "loaded":
                reason = stop_reason(
                    pages_visited=pages,
                    max_pages=options.max_pages,
                    elapsed_seconds=elapsed,
                    max_duration_seconds=options.max_duration_seconds,
                    empty_streak=empty_streak,
                    max_empty_attempts=options.max_empty_attempts,
                    cancelled=cancelled,
                    rate_limited=False,
                    queue_empty=False,
                )
                if reason:
                    stopped = reason
                    break
                if options.request_delay_seconds:
                    time.sleep(options.request_delay_seconds)
            if kind == "api":
                self._fetch_api_page(
                    page,
                    monitor,
                    target,
                    current_url,
                    errors,
                )
                api_requests += 1
                methods.add("API pagination")
                pages += 1
                if monitor is not None:
                    added, probe_failures, network_seen = self._absorb(
                        page,
                        monitor,
                        "",
                        current_url,
                        observed,
                        known,
                        probe_failures,
                        network_seen,
                        include_html=False,
                    )
                else:
                    added = 0
            else:
                if kind == "html":
                    opened = self._open_later_page(page, target, errors)
                    if not opened:
                        continue
                    current_url = page.url or target
                    try:
                        current_html = page.content()
                    except Exception:
                        errors.append("A later page could not be read.")
                        continue
                elif kind == "inplace":
                    self._wait_for_network_idle(page, redact_url(page.url or current_url))
                    current_url = page.url or current_url
                    try:
                        current_html = page.content()
                    except Exception:
                        errors.append("The page could not be read after the Next control.")
                        continue
                identity = canonical_url(current_url)
                if kind != "inplace" and identity in visited:
                    skipped.append("Skipped a repeated page address.")
                    continue
                visited.add(identity)
                if monitor is not None:
                    monitor.set_source_page(current_url)
                pages += 1
                browser_pages += 1
                added, probe_failures, network_seen = self._absorb(
                    page,
                    monitor,
                    current_html,
                    current_url,
                    observed,
                    known,
                    probe_failures,
                    network_seen,
                    include_html=True,
                )
            empty_streak = 0 if added else empty_streak + 1
            self._remember_pagination(
                page,
                monitor,
                current_html,
                current_url,
                options,
                pending,
                visited,
                seen_cursors,
                seen_api_urls,
                methods,
                skipped,
                click_blocked,
                pages < options.max_pages,
            )
            click_blocked = click_blocked or any(
                "none were clicked" in note for note in skipped
            )
            if monitor is not None and monitor.rate_limited and not rate_note_added:
                errors.append("The site returned HTTP 429. API pagination stopped.")
                rate_note_added = True
            pages_remaining = len(pending) if pending else None
            self._publish(
                progress,
                initial_url=page_url,
                current_url=current_url,
                pages=browser_pages,
                remaining=pages_remaining,
                api_requests=api_requests,
                pdf_count=len(known),
                new_on_page=added,
                duplicates=max(0, len(observed) - len(known)),
                method=", ".join(sorted(methods)) or "First page",
                elapsed=time.monotonic() - started,
                status="Scanning",
            )
            if empty_streak >= options.max_empty_attempts:
                stopped = "Stopped after pages with no new PDFs."
                break
            if not pending and options.enable_infinite_scroll:
                scrolled, probe_failures, network_seen, scrolled_html = self._scroll_for_content(
                    page,
                    monitor,
                    options,
                    stop_event,
                    started,
                    current_url,
                    observed,
                    known,
                    probe_failures,
                    network_seen,
                )
                if scrolled:
                    methods.add("Infinite scroll")
                    if scrolled_html:
                        current_html = scrolled_html
                        self._remember_pagination(
                            page,
                            monitor,
                            current_html,
                            page.url or current_url,
                            options,
                            pending,
                            visited,
                            seen_cursors,
                            seen_api_urls,
                            methods,
                            skipped,
                            click_blocked,
                            pages < options.max_pages,
                        )
            if not pending:
                stopped = stop_reason(
                    pages_visited=pages,
                    max_pages=options.max_pages,
                    elapsed_seconds=time.monotonic() - started,
                    max_duration_seconds=options.max_duration_seconds,
                    empty_streak=empty_streak,
                    max_empty_attempts=options.max_empty_attempts,
                    cancelled=stop_event is not None and stop_event.is_set(),
                    rate_limited=bool(monitor and monitor.rate_limited),
                    queue_empty=True,
                ) or "No further pagination pages were found."
                break

        if not stopped:
            stopped = stop_reason(
                pages_visited=pages,
                max_pages=options.max_pages,
                elapsed_seconds=time.monotonic() - started,
                max_duration_seconds=options.max_duration_seconds,
                empty_streak=empty_streak,
                max_empty_attempts=options.max_empty_attempts,
                cancelled=stop_event is not None and stop_event.is_set(),
                rate_limited=bool(monitor and monitor.rate_limited),
                queue_empty=not pending,
            ) or "No further pagination pages were found."
        merged = merge_candidates(observed)
        items = [_to_pdf_item(candidate) for candidate in merged.candidates]
        failed = probe_failures + (monitor.failed_inspections if monitor else 0)
        summary = PaginationSummary(
            pages_scanned=browser_pages,
            methods=tuple(sorted(methods)) or ("Single page",),
            api_requests=api_requests,
            errors=tuple(dict.fromkeys(errors)),
            skipped=tuple(dict.fromkeys(skipped)),
            stopped_reason=stopped,
            duration_seconds=round(time.monotonic() - started, 1),
            pages_remaining=pages_remaining,
        )
        self._publish(
            progress,
            initial_url=page_url,
            current_url=current_url,
            pages=browser_pages,
            remaining=pages_remaining,
            api_requests=api_requests,
            pdf_count=len(items),
            new_on_page=0,
            duplicates=merged.duplicates_removed,
            method=", ".join(summary.methods),
            elapsed=summary.duration_seconds,
            status=stopped,
        )
        self._logger.info(
            "Pagination scan finished with %s PDF link(s) across %s page(s): %s",
            len(items),
            pages,
            stopped,
        )
        return ScanOutcome(
            pdfs=items,
            duplicates_removed=merged.duplicates_removed,
            failed_inspections=failed,
            pagination=summary,
        )

    def _absorb(
        self,
        page: Page,
        monitor: NetworkMonitor | None,
        html: str,
        page_url: str,
        observed: list[PdfCandidate],
        known: set[str],
        probe_failures: int,
        network_seen: int,
        *,
        include_html: bool,
    ) -> tuple[int, int, int]:
        fresh = 0
        if include_html and html:
            html_candidates, failures = self._html_candidates(page, html, page_url)
            probe_failures += failures
            fresh += self._take_new(html_candidates, observed, known)
        if monitor is not None:
            before = network_seen
            monitor.drain()
            fresh += self._take_new(monitor.candidates[before:], observed, known)
            network_seen = len(monitor.candidates)
        return fresh, probe_failures, network_seen

    def _take_new(
        self,
        candidates: list[PdfCandidate],
        observed: list[PdfCandidate],
        known: set[str],
    ) -> int:
        fresh = 0
        for candidate in candidates:
            observed.append(candidate)
            key = canonical_url(candidate.url)
            if key in known:
                continue
            known.add(key)
            fresh += 1
        return fresh

    def _open_later_page(self, page: Page, url: str, errors: list[str]) -> bool:
        try:
            ensure_scan_allowed(url, allow_private_networks=self.settings.allow_private_networks)
        except UrlBlockedError:
            errors.append("Skipped a pagination page on a blocked address.")
            return False
        try:
            response = page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=self.settings.playwright_timeout_ms,
            )
        except Exception as exc:
            self._logger.info("Later page failed for %s", redact_url(url))
            errors.append(_navigation_message(exc))
            return False
        final_url = page.url or url
        try:
            ensure_scan_allowed(
                final_url,
                allow_private_networks=self.settings.allow_private_networks,
            )
        except UrlBlockedError:
            errors.append("A pagination page redirected to a blocked address.")
            return False
        if response is not None and response.status >= 400:
            errors.append(f"HTTP {response.status} while opening a later page.")
            if response.status == 429:
                return False
        self._wait_for_network_idle(page, redact_url(final_url))
        return True

    def _fetch_api_page(
        self,
        page: Page,
        monitor: NetworkMonitor | None,
        url: str,
        page_url: str,
        errors: list[str],
    ) -> None:
        if monitor is None:
            return
        try:
            ensure_scan_allowed(url, allow_private_networks=self.settings.allow_private_networks)
        except UrlBlockedError:
            errors.append("Skipped an API page on a blocked address.")
            return
        try:
            response = page.request.get(
                url,
                timeout=self.settings.playwright_timeout_ms,
                fail_on_status_code=False,
            )
        except Exception:
            self._logger.info("API pagination request failed for %s", redact_url(url))
            errors.append("An API pagination request failed.")
            monitor.failed_inspections += 1
            return
        if not self.settings.allow_private_networks and destination_is_blocked(response.url):
            errors.append("An API pagination request redirected to a blocked address.")
            return
        content_type = response.headers.get("content-type")
        body: bytes | None = None
        try:
            length_header = response.headers.get("content-length")
            length = int(length_header) if length_header and length_header.isdigit() else None
        except ValueError:
            length = None
        if length is None or length <= self.settings.max_json_bytes:
            try:
                body = response.body()
            except Exception:
                body = None
        if body is not None and len(body) > self.settings.max_json_bytes:
            errors.append("Skipped a pagination response larger than the size limit.")
            return
        monitor.set_source_page(page_url)
        monitor.ingest_fetched_body(response.url or url, response.status, content_type, body)

    def _remember_pagination(
        self,
        page: Page,
        monitor: NetworkMonitor | None,
        html: str,
        page_url: str,
        options: PaginationOptions,
        pending: list[tuple[str, str]],
        visited: set[str],
        seen_cursors: set[str],
        seen_api_urls: set[str],
        methods: set[str],
        skipped: list[str],
        click_blocked: bool,
        allow_controls: bool,
    ) -> None:
        html_found = False
        if html:
            found = find_html_pagination(html, page_url)
            skipped.extend(found.skipped)
            html_found = bool(found.targets or found.skipped)
            for target in found.targets:
                key = canonical_url(target.url)
                if key in visited or any(canonical_url(item[1]) == key for item in pending):
                    continue
                if not same_host(target.url, page_url):
                    skipped.append("Skipped a next link on a different website.")
                    continue
                pending.append(("html", target.url))
                methods.add("HTML pagination")
        hints = monitor.consume_api_hints() if monitor is not None else []
        clicked = False
        if (
            allow_controls
            and not pending
            and not html_found
            and not click_blocked
            and options.follow_next_controls
        ):
            result = click_next_control(page)
            if result.clicked:
                clicked = True
                methods.add("JavaScript pagination")
                pending.append(("inplace", page.url or page_url))
            elif result.message:
                skipped.append(result.message)
        if (
            clicked
            or monitor is None
            or not options.follow_api_pagination
            or monitor.rate_limited
        ):
            return
        for hint in hints:
            seen_api_urls.add(hint.request_url)
            if hint.next_cursor and hint.next_cursor in seen_cursors:
                skipped.append("Stopped on a repeated pagination cursor.")
                continue
            nxt = next_api_url(hint, seen_cursors, seen_api_urls)
            if hint.next_cursor and nxt:
                seen_cursors.add(hint.next_cursor)
            if not nxt:
                continue
            if not (same_host(nxt, page_url) or same_host(nxt, hint.request_url)):
                skipped.append("Skipped a next API link on a different website.")
                continue
            key = canonical_url(nxt)
            if key in seen_api_urls or any(
                item[0] == "api" and canonical_url(item[1]) == key for item in pending
            ):
                continue
            seen_api_urls.add(key)
            pending.append(("api", nxt))
            methods.add("API pagination")

    def _scroll_for_content(
        self,
        page: Page,
        monitor: NetworkMonitor | None,
        options: PaginationOptions,
        stop_event: threading.Event | None,
        started: float,
        page_url: str,
        observed: list[PdfCandidate],
        known: set[str],
        probe_failures: int,
        network_seen: int,
    ) -> tuple[bool, int, int, str]:
        stagnant = 0
        last_height = document_height(page)
        last_known = len(known)
        scrolled = False
        latest_html = ""
        for _ in range(options.max_scroll_iterations):
            if stop_event is not None and stop_event.is_set():
                break
            if time.monotonic() - started >= options.max_duration_seconds:
                break
            scroll_once(page)
            scrolled = True
            self._wait_for_network_idle(page, redact_url(page.url or page_url))
            try:
                html = page.content()
            except Exception:
                break
            latest_html = html
            added, probe_failures, network_seen = self._absorb(
                page,
                monitor,
                html,
                page.url or page_url,
                observed,
                known,
                probe_failures,
                network_seen,
                include_html=True,
            )
            height = document_height(page)
            if height <= last_height and added == 0 and len(known) == last_known:
                stagnant += 1
            else:
                stagnant = 0
                last_height = height
                last_known = len(known)
            if stagnant >= options.max_empty_attempts:
                break
        return scrolled, probe_failures, network_seen, latest_html

    def _publish(self, progress: PaginationProgress | None, **values: object) -> None:
        if progress is None:
            return
        progress.initial_url = str(values["initial_url"])
        progress.current_url = str(values["current_url"])
        progress.pages_visited = int(values["pages"])
        remaining = values["remaining"]
        progress.pages_remaining = remaining if isinstance(remaining, int) else None
        progress.api_requests = int(values["api_requests"])
        progress.pdf_count = int(values["pdf_count"])
        progress.new_on_page = int(values["new_on_page"])
        progress.duplicates_ignored = int(values["duplicates"])
        progress.method = str(values["method"])
        progress.elapsed_seconds = float(values["elapsed"])
        progress.status = str(values["status"])

    def _wait_for_user(
        self,
        page: Page,
        stop_event: threading.Event | None,
        monitor: NetworkMonitor | None,
    ) -> tuple[str, str]:
        """Keep the window open until the user finishes, closes it, or time runs out."""
        deadline = time.monotonic() + self.settings.interactive_timeout_seconds
        latest_html = ""
        latest_url = page.url
        while stop_event is None or not stop_event.is_set():
            if time.monotonic() >= deadline:
                self._logger.info("Interactive scan reached its time limit")
                break
            try:
                if page.is_closed():
                    break
                current_url = page.url
                if not self.settings.allow_private_networks and destination_is_blocked(current_url):
                    self._logger.info("Interactive scan left the public web and stopped")
                    break
                latest_html = page.content()
                latest_url = current_url
                if monitor is not None:
                    monitor.set_source_page(current_url)
            except Exception:
                break
            if stop_event is None:
                break
            if stop_event.wait(1):
                break
        return latest_html, latest_url

    def _html_candidates(
        self,
        page: Page,
        html: str,
        page_url: str,
    ) -> tuple[list[PdfCandidate], int]:
        extracted = extract_link_candidates(html, page_url)
        self._logger.info("Collected %s link(s) from %s", len(extracted), redact_url(page_url))
        content_types, statuses, failures = self._probe_content_types(page, extracted)
        detected = detect_pdfs(extracted, content_types)
        candidates: list[PdfCandidate] = []
        for pdf in detected:
            content_type = content_types.get(pdf.url)
            status = (
                PdfStatus.VALIDATED
                if is_pdf_content_type(content_type)
                else PdfStatus.DISCOVERED
            )
            candidates.append(
                PdfCandidate(
                    url=pdf.url,
                    file_name=pdf.file_name,
                    discovery_method=DiscoveryMethod.HTML,
                    source_page_url=page_url,
                    content_type=content_type,
                    http_status=statuses.get(pdf.url),
                    status=status,
                )
            )
        return candidates, failures

    def _wait_for_network_idle(self, page: Page, logged_url: str) -> None:
        try:
            page.wait_for_load_state(
                "networkidle",
                timeout=self.settings.network_idle_timeout_ms,
            )
        except Exception:
            self._logger.info(
                "Page %s did not become fully idle; continuing with the loaded content",
                logged_url,
            )

    def _probe_content_types(
        self,
        page: Page,
        candidates: list[LinkCandidate],
    ) -> tuple[dict[str, str], dict[str, int], int]:
        """HEAD a limited set of ambiguous links. This does not download file bodies."""
        to_probe = [candidate for candidate in candidates if should_probe_content_type(candidate)]
        limited = to_probe[: self.settings.max_content_type_checks]
        if len(to_probe) > len(limited):
            self._logger.info(
                "Checking Content-Type for %s of %s ambiguous link(s)",
                len(limited),
                len(to_probe),
            )
        content_types: dict[str, str] = {}
        statuses: dict[str, int] = {}
        failures = 0
        for candidate in limited:
            content_type, status, failed = self._read_content_type(page, candidate.url)
            if failed:
                failures += 1
            if content_type:
                content_types[candidate.url] = content_type
            if status is not None:
                statuses[candidate.url] = status
        return content_types, statuses, failures

    def _read_content_type(self, page: Page, url: str) -> tuple[str | None, int | None, bool]:
        logged_url = redact_url(url)
        if not self.settings.allow_private_networks and destination_is_blocked(url):
            self._logger.info("Skipped a blocked address during Content-Type check: %s", logged_url)
            return None, None, True
        try:
            response = page.request.head(
                url,
                timeout=self.settings.content_type_timeout_ms,
                fail_on_status_code=False,
            )
        except Exception:
            self._logger.info("Content-Type check failed for %s", logged_url)
            return None, None, True
        if not self.settings.allow_private_networks and destination_is_blocked(response.url):
            self._logger.info("Content-Type check redirected to a blocked address")
            return None, None, True
        if response.status >= 400:
            self._logger.info(
                "Content-Type check returned HTTP %s for %s",
                response.status,
                logged_url,
            )
            return None, response.status, True
        return response.headers.get("content-type"), response.status, False


def _to_pdf_item(candidate: PdfCandidate) -> PdfItem:
    return PdfItem(
        id=new_pdf_id(candidate.url),
        file_name=candidate.file_name,
        url=candidate.url,
        status=candidate.status,
        source_page_url=candidate.source_page_url,
        source_endpoint=candidate.source_endpoint,
        discovery_method=candidate.methods_label or candidate.discovery_method.value,
        content_type=candidate.content_type,
        http_status=candidate.http_status,
        file_size=candidate.file_size,
        error_message=candidate.error_message,
    )


def _run_on_worker_thread(func: Callable[[], _T]) -> _T:
    outcome: _ThreadOutcome[_T] = _ThreadOutcome()

    def _target() -> None:
        try:
            outcome.value = func()
        except Exception as exc:
            outcome.error = exc

    # Streamlit runs inside an asyncio event loop. Playwright's sync API
    # refuses to start there, so the browser work runs on a worker thread.
    thread = threading.Thread(target=_target, name="pdf-scraper-scan", daemon=True)
    thread.start()
    thread.join()
    if outcome.error is not None:
        raise outcome.error
    if outcome.value is None:
        raise ScanError("The website could not be scanned.")
    return outcome.value


def _launch_chromium(playwright, *, headless: bool):
    """Launch bundled Chromium, or fall back to system Chrome/Edge when missing."""
    try:
        browser = playwright.chromium.launch(headless=headless)
        return browser, "bundled"
    except Exception as first:
        text = str(first).lower()
        missing = (
            "doesn't exist" in text
            or "executable" in text
            or "playwright install" in text
        )
        if not missing:
            raise
        last = first
        for channel in ("chrome", "msedge"):
            try:
                browser = playwright.chromium.launch(
                    channel=channel, headless=headless
                )
                return browser, channel
            except Exception as channel_exc:  # noqa: PERF203 — try each channel
                last = channel_exc
        raise last from first


def _browser_start_message(exc: Exception) -> str:
    text = str(exc).lower()
    if "doesn't exist" in text or "executable" in text or "playwright install" in text:
        return _BROWSER_MISSING_TEXT
    return "The browser could not be started."


def _navigation_message(exc: Exception) -> str:
    text = str(exc).lower()
    unreachable_markers = (
        "err_name_not_resolved",
        "err_connection_refused",
        "err_connection_timed_out",
        "err_connection_reset",
        "err_internet_disconnected",
        "err_address_unreachable",
        "err_network_changed",
        "err_empty_response",
        "name not resolved",
        "getaddrinfo",
        "dns",
    )
    if any(marker in text for marker in unreachable_markers):
        return "The website could not be reached. Check the address and your connection."
    if "timeout" in text or "timed_out" in text:
        return "The page took too long to load. Try again or check whether the site is available."
    if "err_cert" in text or "ssl" in text or "certificate" in text:
        return "The website could not be opened because the secure connection failed."
    return "The page could not be loaded."
