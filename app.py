"""Local Streamlit UI for scanning a web page for PDF links."""

import pandas as pd
import streamlit as st

from config import get_settings
from models.pdf_item import PdfItem
from services.scanner_service import ScanOutcome, ScannerService


def main() -> None:
    settings = get_settings()
    st.set_page_config(page_title=settings.app_name, layout="wide")
    _apply_theme()

    st.title(settings.app_name)
    st.caption("Scan one web page and list links to PDF files.")

    service = ScannerService()
    storage_warning = service.prepare_storage()
    if storage_warning:
        st.warning(storage_warning)

    st.session_state.setdefault("scan_outcome", None)
    st.session_state.setdefault("scan_seq", 0)
    st.session_state.setdefault("selected_urls", set())

    with st.form("scan_form", clear_on_submit=False):
        url = st.text_input(
            "Website URL",
            placeholder="https://example.com/reports",
        )
        submitted = st.form_submit_button("Scan Website", type="primary")

    if submitted:
        with st.spinner("Scanning website..."):
            st.session_state.scan_outcome = service.scan(url)
        st.session_state.scan_seq += 1
        st.session_state.selected_urls = set()

    st.subheader("PDFs Found")
    _render_results(st.session_state.scan_outcome, st.session_state.scan_seq)


def _render_results(outcome: ScanOutcome | None, scan_seq: int) -> None:
    if outcome is None:
        st.info("Enter a website URL and choose Scan Website to list PDF links on that page.")
        return
    if outcome.error:
        st.error(outcome.error)
        return
    if outcome.page_url:
        st.caption(f"Scanned page: {outcome.page_url}")
    if outcome.info:
        st.warning(outcome.info)
        return
    if not outcome.pdfs:
        st.warning("No PDFs were found on this page.")
        return

    count = len(outcome.pdfs)
    noun = "PDF" if count == 1 else "PDFs"
    st.caption(
        f"{count} {noun} found. Selected rows are kept for a later download step. "
        "This version does not download files."
    )
    edited = st.data_editor(
        _results_frame(outcome.pdfs, st.session_state.selected_urls),
        hide_index=True,
        use_container_width=True,
        disabled=["PDF Name", "PDF URL", "Status"],
        num_rows="fixed",
        column_config={
            "Select": st.column_config.CheckboxColumn("Select", width="small"),
            "PDF Name": st.column_config.TextColumn("PDF Name", width="medium"),
            "PDF URL": st.column_config.LinkColumn("PDF URL", width="large"),
            "Status": st.column_config.TextColumn("Status", width="small"),
        },
        key=f"pdf_editor_{scan_seq}",
    )
    st.session_state.selected_urls = set(edited.loc[edited["Select"], "PDF URL"].astype(str))


def _results_frame(pdfs: list[PdfItem], selected_urls: set[str]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Select": item.url in selected_urls,
                "PDF Name": item.file_name,
                "PDF URL": item.url,
                "Status": str(item.status),
            }
            for item in pdfs
        ]
    )


def _apply_theme() -> None:
    st.markdown(
        """
        <style>
        .block-container {
            padding-top: 2rem;
            max-width: 1100px;
        }
        h1 {
            font-weight: 650;
            letter-spacing: -0.02em;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
