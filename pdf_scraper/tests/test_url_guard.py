"""Tests for blocked scan destinations."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from utils.url_guard import (  # noqa: E402
    UrlBlockedError,
    destination_is_blocked,
    ensure_scan_allowed,
)
from utils.url_utils import redact_url, validate_http_url  # noqa: E402


class UrlGuardTests(unittest.TestCase):
    def test_blocks_local_private_and_metadata_addresses(self) -> None:
        blocked = (
            "http://127.0.0.1/file.pdf",
            "http://localhost/file.pdf",
            "http://10.1.2.3/file.pdf",
            "http://192.168.1.20/file.pdf",
            "http://169.254.169.254/latest/meta-data",
            "http://2130706433/file.pdf",
            "http://metadata.google.internal/computeMetadata/v1/",
            "file:///C:/secret.pdf",
        )
        for url in blocked:
            self.assertTrue(destination_is_blocked(url), url)

    def test_allows_a_public_hostname_without_resolving_it(self) -> None:
        self.assertFalse(destination_is_blocked("https://example.com/files/a.pdf"))

    def test_explicit_opt_in_permits_a_local_test_server(self) -> None:
        ensure_scan_allowed("http://127.0.0.1:8765/page.html", allow_private_networks=True)

    def test_default_policy_rejects_a_local_address(self) -> None:
        with self.assertRaises(UrlBlockedError):
            ensure_scan_allowed("http://127.0.0.1/page.html", allow_private_networks=False)

    def test_invalid_scheme_is_still_rejected_before_a_scan(self) -> None:
        with self.assertRaisesRegex(ValueError, "Only http and https"):
            validate_http_url("ftp://example.com/file.pdf")

    def test_logs_redact_signed_query_values(self) -> None:
        redacted = redact_url(
            "https://cdn.example.com/a.pdf?X-Amz-Signature=abc%2Bdef&X-Amz-Credential=AKIA&download=1"
        )
        self.assertNotIn("abc", redacted)
        self.assertNotIn("AKIA", redacted)
        self.assertIn("download=1", redacted)
        self.assertIn("REDACTED", redacted)


if __name__ == "__main__":
    unittest.main()
