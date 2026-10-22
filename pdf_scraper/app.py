"""Local Streamlit interface for discovering PDF links."""

from __future__ import annotations

import sys

import pandas as pd
import streamlit as st

from config import Settings, ensure_runtime_directories, load_settings
from database.database import Database
from models.pdf_item import PdfItem, ScanMode
from scraper.pagination import PaginationOptions
from scraper.website_scanner import InteractiveSession, ScanError
from services.scanner_service import PaginationScan, ScanResult, ScannerService
from utils.logger import configure_logging, get_logger
from utils.url_guard import UrlBlockedError

_ERROR_KEY = "scan_error"
_RESULT_KEY = "scan_result"
_EDITOR_KEY = "pdf_editor"
_DOWNLOAD_NOTE_KEY = "download_note"
_DOWNLOAD_VERSION_KEY = "download_version"
_INTERACTIVE_KEY = "interactive_session"
_PAGINATION_KEY = "pagination_scan"
_MODE_KEY = "discovery_mode"

_COLUMN_SELECT = "Select"
_COLUMN_NAME = "PDF Name"
_COLUMN_URL = "PDF URL"
_COLUMN_SOURCE = "Discovery Source"
_COLUMN_ORIGIN = "Source"
_COLUMN_TYPE = "Content Type"
_COLUMN_HTTP = "HTTP Status"
_COLUMN_STATUS = "Discovery Status"
_COLUMN_DOWNLOAD = "Download Status"

_MODE_LABELS = {
    "HTML Links": ScanMode.HTML,
    "HTML + API Discovery": ScanMode.HTML_API,
    "Interactive Discovery": ScanMode.INTERACTIVE,
}


def main() -> None:
    """Start the local interface."""
    settings = load_settings()
    st.set_page_config(page_title=settings.app_name, page_icon=":page_facing_up:", layout="wide")
    _apply_theme()

    if sys.version_info < (3, 12):
        st.error("PDF Scraper requires Python 3.12 or newer.")
        st.stop()

    configure_logging(settings)
    logger = get_logger(__name__)
    try:
        ensure_runtime_directories(settings)
        database = Database(settings.database_path)
        database.initialize()
    except Exception:
        logger.exception("Database initialization failed")
        st.error("The local database could not be created. Check that this folder is writable.")
        st.stop()

    service = ScannerService(settings, database)
    _render(service, settings)


def _render(service: ScannerService, settings: Settings) -> None:
    st.title(settings.app_name)
    st.caption(
        "Find PDF links in the page, in API responses, and in files the browser loads. "
        "Downloading file contents comes in a later step."
    )

    mode = _mode_selector()
    _render_interactive(service, settings)
    _consume_finished_pagination()
    with st.form("scan_form"):
        url = st.text_input(
            "Website URL",
            placeholder="https://example.com",
            help="The address of the page to open.",
        )
        options = _pagination_controls()
        submitted = st.form_submit_button("Scan Website", type="primary")

    if submitted:
        _start_scan(service, settings, url, mode, options)

    _render_pagination_progress()

    error = st.session_state.get(_ERROR_KEY)
    if error:
        st.error(error)

    st.divider()
    st.subheader("PDFs Found")
    result = st.session_state.get(_RESULT_KEY)
    if result is None:
        st.caption("Results will appear here after a scan.")
        return
    _render_results(service, settings, result)


def _mode_selector() -> ScanMode:
    label = st.radio(
        "Discovery mode",
        options=list(_MODE_LABELS),
        horizontal=True,
        key=_MODE_KEY,
    )
    descriptions = {
        ScanMode.HTML: "Read PDF links from the rendered page.",
        ScanMode.HTML_API: "Also watch network responses and JSON returned while the page loads.",
        ScanMode.INTERACTIVE: (
            "Open a visible browser window. Paginate, filter, or scroll yourself. "
            "The app records PDFs from that session and does not click the page for you."
        ),
    }
    selected = _MODE_LABELS[label]
    st.caption(descriptions[selected])
    return selected


def _render_interactive(service: ScannerService, settings: Settings) -> None:
    session = st.session_state.get(_INTERACTIVE_KEY)
    if not isinstance(session, InteractiveSession):
        return
    if session.completed and session.outcome is None and session.error is not None:
        st.session_state[_INTERACTIVE_KEY] = None
        st.error(_public_error(session.error))
        return
    minutes = max(1, settings.interactive_timeout_seconds // 60)
    st.info(
        "A Chromium window is open. Use it to paginate, filter, scroll, or open a document list. "
        "When you are finished, click Finish interactive scan. "
        f"The window closes on its own after about {minutes} minutes."
    )
    if st.button("Finish interactive scan", type="primary"):
        _finish_interactive(service, session)


def _pagination_controls() -> PaginationOptions:
    with st.expander("Pagination settings"):
        enabled = st.checkbox(
            "Enable pagination scanning",
            value=True,
            help="Open later pages and API responses, up to the limits below.",
        )
        max_pages = st.number_input(
            "Maximum pages to scan",
            min_value=1,
            max_value=100,
            value=20,
            step=1,
            help="HTML pages and followed API responses both count toward this limit.",
        )
        max_duration = st.number_input(
            "Maximum total scan duration (seconds)",
            min_value=15,
            max_value=900,
            value=180,
            step=15,
        )
        max_empty = st.number_input(
            "Maximum consecutive attempts without new results",
            min_value=1,
            max_value=5,
            value=2,
            step=1,
        )
        follow_next = st.checkbox(
            "Enable Load More / Next button interaction",
            value=True,
            help="Click one clear Next or Load More control when the page does not expose a next URL.",
        )
        infinite = st.checkbox("Enable infinite scrolling", value=False)
        max_scroll = st.number_input(
            "Maximum scroll iterations",
            min_value=1,
            max_value=30,
            value=8,
            step=1,
        )
        follow_api = st.checkbox(
            "Enable API pagination following when sufficient evidence exists",
            value=True,
        )
        st.caption(
            "Interactive Discovery stays manual. The app does not click that window for you."
        )
    return PaginationOptions(
        enabled=bool(enabled),
        max_pages=int(max_pages),
        max_duration_seconds=int(max_duration),
        max_empty_attempts=int(max_empty),
        follow_next_controls=bool(follow_next),
        enable_infinite_scroll=bool(infinite),
        max_scroll_iterations=int(max_scroll),
        follow_api_pagination=bool(follow_api),
    )


def _start_scan(
    service: ScannerService,
    settings: Settings,
    url: str,
    mode: ScanMode,
    options: PaginationOptions,
) -> None:
    logger = get_logger(__name__)
    active = st.session_state.get(_INTERACTIVE_KEY)
    if isinstance(active, InteractiveSession) and not active.completed:
        st.session_state[_ERROR_KEY] = "Finish the interactive scan before starting another one."
        return
    running = st.session_state.get(_PAGINATION_KEY)
    if isinstance(running, PaginationScan) and not running.done:
        st.session_state[_ERROR_KEY] = "Stop the current scan before starting another one."
        return
    st.session_state.pop(_EDITOR_KEY, None)
    try:
        if mode is ScanMode.INTERACTIVE:
            with st.spinner("Opening a browser window..."):
                session = service.begin_interactive(url)
            st.session_state[_INTERACTIVE_KEY] = session
            st.session_state[_ERROR_KEY] = None
            return
        if options.enabled:
            scan = service.begin_pagination(url, mode, options)
            st.session_state[_PAGINATION_KEY] = scan
            st.session_state[_ERROR_KEY] = None
            return
        timeout_seconds = max(1, settings.playwright_timeout_ms // 1000)
        message = (
            f"Scanning website. Waiting for the page to load (up to {timeout_seconds} seconds)..."
        )
        if mode is ScanMode.HTML_API:
            message = (
                "Scanning the page and watching network responses "
                f"(up to {timeout_seconds} seconds)..."
            )
        with st.spinner(message):
            result = service.scan_website(url, mode, options)
    except (ValueError, UrlBlockedError) as exc:
        st.session_state[_ERROR_KEY] = str(exc)
        st.session_state.pop(_RESULT_KEY, None)
        return
    except ScanError as exc:
        st.session_state[_ERROR_KEY] = str(exc)
        st.session_state.pop(_RESULT_KEY, None)
        return
    except Exception:
        logger.exception("Unhandled interface failure during scan")
        st.session_state[_ERROR_KEY] = (
            "Something went wrong while scanning. Details were written to the log."
        )
        st.session_state.pop(_RESULT_KEY, None)
        return

    st.session_state[_ERROR_KEY] = None
    st.session_state[_RESULT_KEY] = result


def _finish_interactive(service: ScannerService, session: InteractiveSession) -> None:
    logger = get_logger(__name__)
    try:
        with st.spinner("Collecting PDFs from the browser session..."):
            result = service.complete_interactive(session)
    except (ValueError, UrlBlockedError) as exc:
        st.session_state[_ERROR_KEY] = str(exc)
        st.session_state[_INTERACTIVE_KEY] = None
        return
    except ScanError as exc:
        st.session_state[_ERROR_KEY] = str(exc)
        st.session_state[_INTERACTIVE_KEY] = None
        return
    except Exception:
        logger.exception("Unhandled interface failure during interactive scan")
        st.session_state[_ERROR_KEY] = (
            "Something went wrong while scanning. Details were written to the log."
        )
        st.session_state[_INTERACTIVE_KEY] = None
        return
    st.session_state[_INTERACTIVE_KEY] = None
    st.session_state[_ERROR_KEY] = None
    st.session_state[_RESULT_KEY] = result
    st.rerun()


def _consume_finished_pagination() -> None:
    scan = st.session_state.get(_PAGINATION_KEY)
    if isinstance(scan, PaginationScan) and scan.done:
        _store_pagination_result(scan)


def _render_pagination_progress() -> None:
    scan = st.session_state.get(_PAGINATION_KEY)
    if not isinstance(scan, PaginationScan) or scan.done:
        return
    _pagination_panel()


@st.fragment(run_every=1)
def _pagination_panel() -> None:
    """Refresh pagination status about once a second without rerunning the whole app."""
    scan = st.session_state.get(_PAGINATION_KEY)
    if not isinstance(scan, PaginationScan):
        return
    if scan.done:
        _store_pagination_result(scan)
        st.rerun(scope="app")
        return
    progress = scan.progress
    visited = progress.pages_visited
    remaining = progress.pages_remaining
    if remaining is None:
        fraction = min(1.0, visited / max(scan.max_pages, 1))
    else:
        fraction = min(1.0, visited / max(visited + remaining, 1))
    st.progress(fraction, text=progress.status or "Scanning")
    st.caption(f"Initial page: {progress.initial_url}")
    st.caption(f"Current page: {progress.current_url or progress.initial_url}")
    columns = st.columns(4)
    columns[0].metric("Pages visited", visited)
    columns[1].metric("Pages remaining", "Unknown" if remaining is None else remaining)
    columns[2].metric("API requests", progress.api_requests)
    columns[3].metric("PDFs discovered", progress.pdf_count)
    more = st.columns(4)
    more[0].metric("New on this page", progress.new_on_page)
    more[1].metric("Duplicates ignored", progress.duplicates_ignored)
    more[2].metric("Duration (seconds)", f"{progress.elapsed_seconds:.0f}")
    more[3].metric("Method", progress.method or "First page")
    if st.button("Stop scanning"):
        scan.request_stop()


def _store_pagination_result(scan: PaginationScan) -> None:
    if st.session_state.get(_PAGINATION_KEY) is not scan:
        return
    st.session_state[_PAGINATION_KEY] = None
    if scan.error is not None:
        st.session_state[_ERROR_KEY] = _public_error(scan.error)
        return
    st.session_state[_ERROR_KEY] = None
    st.session_state[_RESULT_KEY] = scan.result


def _render_results(service: ScannerService, settings: Settings, result: ScanResult) -> None:
    st.caption(f"Source page: {result.scanned_url}")
    if result.save_warning:
        st.warning(result.save_warning)
    note = st.session_state.pop(_DOWNLOAD_NOTE_KEY, None)
    if note:
        st.success(note)
    _render_summary(result)
    _render_pagination_summary(result)
    if not result.pdfs:
        st.warning("No PDF links were found.")
        return

    filtered, filter_key = _filter_pdfs(result.pdfs)
    if not filtered:
        st.info("No PDFs match the selected filters.")
        return
    version = int(st.session_state.get(_DOWNLOAD_VERSION_KEY, 0))
    frame = _results_frame(filtered)
    disabled = [column for column in frame.columns if column != _COLUMN_SELECT]
    edited = st.data_editor(
        frame,
        hide_index=True,
        width="stretch",
        num_rows="fixed",
        disabled=disabled,
        column_config={
            _COLUMN_SELECT: st.column_config.CheckboxColumn(_COLUMN_SELECT, default=False),
            _COLUMN_NAME: st.column_config.TextColumn(_COLUMN_NAME, width="medium"),
            _COLUMN_URL: st.column_config.LinkColumn(_COLUMN_URL),
            _COLUMN_SOURCE: st.column_config.TextColumn(_COLUMN_SOURCE, width="medium"),
            _COLUMN_ORIGIN: st.column_config.TextColumn(_COLUMN_ORIGIN, width="medium"),
            _COLUMN_TYPE: st.column_config.TextColumn(_COLUMN_TYPE, width="small"),
            _COLUMN_HTTP: st.column_config.TextColumn(_COLUMN_HTTP, width="small"),
            _COLUMN_STATUS: st.column_config.TextColumn(_COLUMN_STATUS, width="small"),
            _COLUMN_DOWNLOAD: st.column_config.TextColumn(_COLUMN_DOWNLOAD, width="small"),
        },
        key=f"{filter_key}-{version}",
        alt="PDF candidates from this scan",
    )
    st.caption(f"PDF files are saved in {settings.download_dir}")
    with st.container(horizontal=True):
        download_selected = st.button("Download selected", icon=":material/download:")
        download_shown = st.button("Download shown files", icon=":material/folder:")
    if download_selected or download_shown:
        chosen = _chosen_items(edited, filtered, selected_only=download_selected)
        _download_chosen(service, result, chosen, selected_only=download_selected)


def _render_summary(result: ScanResult) -> None:
    counts = _source_counts(result.pdfs)
    columns = st.columns(7)
    values = (
        ("Total", len(result.pdfs)),
        ("HTML", counts["HTML"]),
        ("API", counts["API_JSON"]),
        ("PDF response", counts["PDF_RESPONSE"]),
        ("Browser download", counts["BROWSER_DOWNLOAD"]),
        ("Duplicates removed", result.duplicates_removed),
        ("Failed inspections", result.failed_inspections),
    )
    for column, (label, value) in zip(columns, values, strict=True):
        column.metric(label, value)


def _render_pagination_summary(result: ScanResult) -> None:
    summary = result.pagination
    if summary is None:
        return
    st.caption(
        f"Pages scanned: {summary.pages_scanned}. "
        f"API pagination requests: {summary.api_requests}. "
        f"Duration: {summary.duration_seconds:.0f}s. "
        f"{summary.stopped_reason}"
    )
    if summary.methods:
        st.caption("Pagination methods: " + ", ".join(summary.methods))
    if summary.errors:
        st.warning("Pagination errors: " + " ".join(summary.errors))
    if summary.skipped:
        st.info("Skipped pagination paths: " + " ".join(summary.skipped))


def _chosen_items(
    edited: pd.DataFrame,
    pdfs: list[PdfItem],
    *,
    selected_only: bool,
) -> list[PdfItem]:
    by_url = {item.url: item for item in pdfs}
    if _COLUMN_URL not in edited.columns:
        return []
    rows = edited
    if selected_only:
        if _COLUMN_SELECT not in edited.columns:
            return []
        rows = edited[edited[_COLUMN_SELECT].fillna(False).astype(bool)]
    return [by_url[url] for url in rows[_COLUMN_URL].tolist() if url in by_url]


def _download_chosen(
    service: ScannerService,
    result: ScanResult,
    chosen: list[PdfItem],
    *,
    selected_only: bool,
) -> None:
    if not chosen:
        st.info("Select at least one PDF, or use Download shown files.")
        return
    with st.spinner(f"Saving {len(chosen)} PDF(s) to the downloads folder..."):
        batch = service.save_downloads(chosen)
    by_url = {item.url: item for item in batch.items}
    updated = tuple(by_url.get(item.url, item) for item in result.pdfs)
    st.session_state[_RESULT_KEY] = _replace_result(result, updated)
    st.session_state[_DOWNLOAD_VERSION_KEY] = int(st.session_state.get(_DOWNLOAD_VERSION_KEY, 0)) + 1
    label = "selected" if selected_only else "shown"
    st.session_state[_DOWNLOAD_NOTE_KEY] = (
        f"Saved {batch.saved} {label} PDF(s) to the downloads folder. "
        f"Already there: {batch.skipped}. Failed: {batch.failed}."
    )
    st.rerun()


def _replace_result(result: ScanResult, pdfs: tuple[PdfItem, ...]) -> ScanResult:
    """Return the same scan result with updated PDF rows."""
    return ScanResult(
        pdfs=pdfs,
        scanned_url=result.scanned_url,
        save_warning=result.save_warning,
        duplicates_removed=result.duplicates_removed,
        failed_inspections=result.failed_inspections,
        mode=result.mode,
        pagination=result.pagination,
    )


def _filter_pdfs(pdfs: tuple[PdfItem, ...]) -> tuple[list[PdfItem], str]:
    sources = sorted({item.discovery_method or "Not recorded" for item in pdfs})
    statuses = sorted({_status_label(item.status.value) for item in pdfs})
    filter_columns = st.columns(2)
    source_choice = filter_columns[0].selectbox("Discovery source", ["All", *sources])
    status_choice = filter_columns[1].selectbox("Discovery status", ["All", *statuses])
    selected: list[PdfItem] = []
    for item in pdfs:
        source = item.discovery_method or "Not recorded"
        status = _status_label(item.status.value)
        if source_choice != "All" and source != source_choice:
            continue
        if status_choice != "All" and status != status_choice:
            continue
        selected.append(item)
    return selected, f"{_EDITOR_KEY}-{source_choice}-{status_choice}"


def _results_frame(pdfs: list[PdfItem]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            _COLUMN_SELECT: [False] * len(pdfs),
            _COLUMN_NAME: [item.file_name for item in pdfs],
            _COLUMN_URL: [item.url for item in pdfs],
            _COLUMN_SOURCE: [item.discovery_method or "Not recorded" for item in pdfs],
            _COLUMN_ORIGIN: [_origin(item) for item in pdfs],
            _COLUMN_TYPE: [item.content_type or "" for item in pdfs],
            _COLUMN_HTTP: [
                "" if item.http_status is None else str(item.http_status) for item in pdfs
            ],
            _COLUMN_STATUS: [_status_label(item.status.value) for item in pdfs],
            _COLUMN_DOWNLOAD: [_download_label(item) for item in pdfs],
        }
    )


def _source_counts(pdfs: tuple[PdfItem, ...]) -> dict[str, int]:
    counts = {"HTML": 0, "API_JSON": 0, "PDF_RESPONSE": 0, "BROWSER_DOWNLOAD": 0}
    for item in pdfs:
        label = item.discovery_method or ""
        for name in counts:
            if name in label:
                counts[name] += 1
    return counts


def _origin(item: PdfItem) -> str:
    return item.source_endpoint or item.source_page_url or ""


def _download_label(item: PdfItem) -> str:
    if item.download_status is None:
        return "Not downloaded"
    return _status_label(item.download_status.value)


def _status_label(status: str) -> str:
    return status.replace("_", " ").title()


def _public_error(exc: Exception) -> str:
    if isinstance(exc, (ScanError, ValueError, UrlBlockedError)):
        return str(exc)
    return "Something went wrong while scanning. Details were written to the log."


def _apply_theme() -> None:
    st.markdown(
        """
        <style>
        .block-container { padding-top: 2rem; max-width: 1280px; }
        h1 { font-weight: 600; letter-spacing: -0.02em; }
        </style>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
