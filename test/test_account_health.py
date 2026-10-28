"""Unit tests for account health classification and import validation."""

from __future__ import annotations

import base64
import json
import time
import unittest

from services.account_health import (
    STATUS_AT_EXPIRED_NO_RT,
    STATUS_AT_ONLY,
    STATUS_CODEX_MANAGED,
    STATUS_EXPIRING,
    STATUS_MISSING_AT,
    STATUS_OK,
    STATUS_REFRESH_INVALID,
    analyze_accounts,
    classify_account,
    filter_import_payloads,
    validate_import_payload,
)


def make_jwt(exp: int) -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode()).rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).rstrip(b"=").decode()
    return f"{header}.{payload}.sig"


NOW = 1_800_000_000.0


def account(**kwargs):
    base = {
        "access_token": make_jwt(int(NOW) + 7 * 86400),
        "refresh_token": "rt-1",
        "source_type": "web",
        "created_at": "2026-10-20T00:00:00Z",
    }
    base.update(kwargs)
    return base


class ClassifyTest(unittest.TestCase):
    def test_ok(self):
        record = classify_account(account(), now=NOW)
        self.assertEqual(record["status"], STATUS_OK)
        self.assertFalse(record["dead"])

    def test_expiring_when_at_expired_but_rt_present(self):
        record = classify_account(account(access_token=make_jwt(int(NOW) - 10)), now=NOW)
        self.assertEqual(record["status"], STATUS_EXPIRING)

    def test_at_only(self):
        record = classify_account(account(refresh_token=""), now=NOW)
        self.assertEqual(record["status"], STATUS_AT_ONLY)
        self.assertTrue(record["at_only"])
        self.assertFalse(record["dead"])

    def test_at_expired_without_rt_is_dead(self):
        record = classify_account(account(refresh_token="", access_token=make_jwt(int(NOW) - 10)), now=NOW)
        self.assertEqual(record["status"], STATUS_AT_EXPIRED_NO_RT)
        self.assertTrue(record["dead"])

    def test_refresh_error_invalidated(self):
        record = classify_account(
            account(last_token_refresh_error="oauth_refresh_http_401: refresh_token_invalidated"),
            now=NOW,
        )
        self.assertEqual(record["status"], STATUS_REFRESH_INVALID)
        self.assertTrue(record["dead"])

    def test_missing_access_token(self):
        record = classify_account({"email": "x"}, now=NOW)
        self.assertEqual(record["status"], STATUS_MISSING_AT)
        self.assertTrue(record["dead"])

    def test_codex_managed_exempt_from_rt(self):
        record = classify_account(account(refresh_token="", source_type="codex"), now=NOW)
        self.assertEqual(record["status"], STATUS_CODEX_MANAGED)
        self.assertFalse(record["dead"])


class AnalyzeTest(unittest.TestCase):
    def test_aggregate_counts_and_samples(self):
        accounts = [
            account(email="ok@example.com"),
            account(email="only@example.com", refresh_token=""),
            account(email="dead@example.com", refresh_token="", access_token=make_jwt(int(NOW) - 10)),
            account(email="bad@example.com", last_token_refresh_error="refresh_token_invalidated"),
            account(email="codex@example.com", refresh_token="", source_type="codex"),
            {"email": "no-at@example.com"},
        ]
        summary = analyze_accounts(accounts, now=NOW)
        self.assertEqual(summary["total"], 6)
        self.assertEqual(summary["healthy"], 2)  # ok + codex managed
        self.assertEqual(summary["dead"], 3)  # expired-no-rt + refresh-invalid + missing-at
        self.assertEqual(summary["at_only"], 1)
        self.assertEqual(summary["status_counts"][STATUS_OK], 1)
        self.assertEqual(summary["status_counts"][STATUS_CODEX_MANAGED], 1)
        self.assertEqual(len(summary["dead_samples"]), 3)
        self.assertEqual(len(summary["at_only_samples"]), 1)
        self.assertTrue(summary["age_buckets"])
        self.assertTrue(all("access_token" in row for row in summary["dead_samples"]))

    def test_samples_are_masked(self):
        summary = analyze_accounts([account(email="a@example.com", refresh_token="")], now=NOW)
        token = summary["at_only_samples"][0]["access_token"]
        self.assertIn("…", token)


class ImportValidationTest(unittest.TestCase):
    def test_requires_refresh_token(self):
        ok, reason = validate_import_payload(account(), now=NOW)
        self.assertTrue(ok, reason)

        ok, reason = validate_import_payload(account(refresh_token=""), now=NOW)
        self.assertFalse(ok)
        self.assertIn("refresh_token", reason)

    def test_rejects_expired_access_token(self):
        payload = account(access_token=make_jwt(int(NOW) - 60))
        ok, reason = validate_import_payload(payload, now=NOW)
        self.assertFalse(ok)
        self.assertIn("已过期", reason)

    def test_codex_accounts_exempt(self):
        ok, _ = validate_import_payload(account(refresh_token="", source_type="codex"), now=NOW)
        self.assertTrue(ok)

    def test_filter_reports_rejected(self):
        accepted, rejected = filter_import_payloads(
            [
                account(email="good@example.com"),
                account(email="bad@example.com", refresh_token=""),
            ],
            now=NOW,
        )
        self.assertEqual([item["email"] for item in accepted], ["good@example.com"])
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["email"], "bad@example.com")
        self.assertIn("refresh_token", rejected[0]["reason"])
        self.assertNotIn("rt-1", json.dumps(rejected))

    def test_validation_can_be_disabled(self):
        ok, _ = validate_import_payload(account(refresh_token=""), now=NOW, require_refresh_token=False)
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
