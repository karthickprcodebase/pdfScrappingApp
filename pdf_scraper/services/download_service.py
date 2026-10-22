"""Save discovered PDF URLs into the local downloads folder."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from models.pdf_item import DownloadStatus, PdfItem
from utils.logger import get_logger
from utils.url_guard import UrlBlockedError, ensure_scan_allowed
from utils.url_utils import extract_pdf_filename, redact_url, validate_http_url

_PDF_MAGIC = b"%PDF-"
_CHUNK_SIZE = 1024 * 1024
_USER_AGENT = "PDFScraper/1.0"


def looks_like_pdf_bytes(data: bytes | None) -> bool:
    """Return True when the bytes start with the PDF header."""
    return bool(data) and data.startswith(_PDF_MAGIC)


def sha256_file(path: Path) -> str:
    """Return the SHA-256 hex digest of a local file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class DownloadBatch:
    """How a download request finished, with one updated record per requested PDF."""

    items: tuple[PdfItem, ...]
    saved: int
    skipped: int
    failed: int


def download_pdfs(
    items: list[PdfItem],
    download_dir: Path,
    *,
    allow_private_networks: bool,
    timeout_seconds: float,
    max_bytes: int,
) -> DownloadBatch:
    """Save each PDF into download_dir. One failure does not stop the others."""
    download_dir.mkdir(parents=True, exist_ok=True)
    updated: list[PdfItem] = []
    saved = skipped = failed = 0
    for item in items:
        result, outcome = _download_one(
            item,
            download_dir,
            allow_private_networks=allow_private_networks,
            timeout_seconds=timeout_seconds,
            max_bytes=max_bytes,
        )
        updated.append(result)
        if outcome == "saved":
            saved += 1
        elif outcome == "skipped":
            skipped += 1
        else:
            failed += 1
    return DownloadBatch(items=tuple(updated), saved=saved, skipped=skipped, failed=failed)


def _download_one(
    item: PdfItem,
    download_dir: Path,
    *,
    allow_private_networks: bool,
    timeout_seconds: float,
    max_bytes: int,
) -> tuple[PdfItem, str]:
    logger = get_logger(__name__)
    destination = _destination_path(download_dir, item)
    if _existing_file_is_complete(item, destination):
        return (
            replace(
                item,
                local_file_path=str(destination),
                download_status=DownloadStatus.DOWNLOADED,
                error_message=None,
            ),
            "skipped",
        )
    try:
        url = validate_http_url(item.url)
        ensure_scan_allowed(url, allow_private_networks=allow_private_networks)
        saved_path, size, digest = _fetch_pdf(
            url,
            destination,
            allow_private_networks=allow_private_networks,
            timeout_seconds=timeout_seconds,
            max_bytes=max_bytes,
        )
    except (UrlBlockedError, ValueError) as exc:
        logger.info("Download blocked for %s", redact_url(item.url))
        return _failed(item, str(exc)), "failed"
    except HTTPError as exc:
        logger.info("Download returned HTTP %s for %s", exc.code, redact_url(item.url))
        return _failed(item, f"The file returned HTTP {exc.code}."), "failed"
    except URLError:
        logger.info("Download failed for %s", redact_url(item.url))
        return _failed(item, "The file could not be downloaded."), "failed"
    except OSError:
        logger.exception("Could not write a download for %s", redact_url(item.url))
        return _failed(item, "The file could not be saved."), "failed"
    return (
        replace(
            item,
            local_file_path=str(saved_path),
            file_size=size,
            content_hash=digest,
            download_status=DownloadStatus.DOWNLOADED,
            error_message=None,
        ),
        "saved",
    )


def _fetch_pdf(
    url: str,
    destination: Path,
    *,
    allow_private_networks: bool,
    timeout_seconds: float,
    max_bytes: int,
) -> tuple[Path, int, str]:
    opener = build_opener(_GuardedRedirectHandler(allow_private_networks))
    request = Request(url, headers={"User-Agent": _USER_AGENT})
    partial = destination.with_suffix(destination.suffix + ".part")
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            final_url = response.geturl()
            ensure_scan_allowed(final_url, allow_private_networks=allow_private_networks)
            length = response.headers.get("Content-Length")
            if length and length.isdigit() and int(length) > max_bytes:
                raise ValueError("The file is larger than the 50 MB download limit.")
            digest = hashlib.sha256()
            size = 0
            with partial.open("wb") as handle:
                prefix = response.read(len(_PDF_MAGIC))
                if not looks_like_pdf_bytes(prefix):
                    raise ValueError("The address did not return a PDF.")
                handle.write(prefix)
                digest.update(prefix)
                size += len(prefix)
                while True:
                    chunk = response.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError("The file is larger than the 50 MB download limit.")
                    handle.write(chunk)
                    digest.update(chunk)
        partial.replace(destination)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    return destination, size, digest.hexdigest()


class _GuardedRedirectHandler(HTTPRedirectHandler):
    """Reject a redirect that leaves the addresses this app is allowed to open."""

    def __init__(self, allow_private_networks: bool) -> None:
        super().__init__()
        self._allow_private_networks = allow_private_networks

    def redirect_request(
        self,
        req: Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> Request | None:
        ensure_scan_allowed(newurl, allow_private_networks=self._allow_private_networks)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _destination_path(download_dir: Path, item: PdfItem) -> Path:
    name = extract_pdf_filename(item.url)
    prefix = (item.id or "pdf")[:8]
    return download_dir / f"{prefix}_{name}"


def _existing_file_is_complete(item: PdfItem, destination: Path) -> bool:
    if not destination.is_file():
        return False
    with destination.open("rb") as handle:
        prefix = handle.read(len(_PDF_MAGIC))
    if not looks_like_pdf_bytes(prefix):
        return False
    if item.content_hash and sha256_file(destination) != item.content_hash:
        return False
    return item.download_status is DownloadStatus.DOWNLOADED or item.local_file_path == str(destination)


def _failed(item: PdfItem, message: str) -> PdfItem:
    return replace(
        item,
        download_status=DownloadStatus.FAILED,
        error_message=message,
    )


def duplicate_content_groups(items: list[PdfItem]) -> list[tuple[PdfItem, ...]]:
    """Group different URLs that already share a downloaded file hash.

    Records stay separate so each source URL is preserved. Callers can skip a
    second save when a group already has a local file.
    """
    grouped: dict[str, list[PdfItem]] = {}
    for item in items:
        if not item.content_hash:
            continue
        grouped.setdefault(item.content_hash, []).append(item)
    return [tuple(group) for group in grouped.values() if len(group) > 1]
