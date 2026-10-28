"""Unit tests for account groups (storage-free helpers + key binding)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from services.account_groups import account_in_group, filter_accounts, normalize_group, normalize_groups
from services.auth_service import AuthService
from services.storage.json_storage import JSONStorageBackend


class GroupHelpersTest(unittest.TestCase):
    def test_normalize_group_requires_id_and_name(self):
        self.assertIsNone(normalize_group({"id": "", "name": "x"}))
        self.assertIsNone(normalize_group({"id": "g1", "name": ""}))
        group = normalize_group({"id": "g1", "name": " 付费组 ", "description": "d"})
        self.assertEqual(group["name"], "付费组")
        self.assertTrue(group["enabled"])

    def test_normalize_groups_dedupes(self):
        groups = normalize_groups([{"id": "g1", "name": "A"}, {"id": "g1", "name": "B"}, {"id": "g2", "name": "C"}])
        self.assertEqual([group["id"] for group in groups], ["g1", "g2"])

    def test_membership_filter(self):
        accounts = [
            {"access_token": "a", "group_id": "g1"},
            {"access_token": "b", "group_id": "g2"},
            {"access_token": "c"},
        ]
        self.assertEqual([item["access_token"] for item in filter_accounts(accounts, "g1")], ["a"])
        self.assertEqual(len(filter_accounts(accounts, "")), 3)
        self.assertTrue(account_in_group({"group_id": "g1"}, "g1"))
        self.assertFalse(account_in_group({"group_id": "g2"}, "g1"))


class AuthKeyGroupTest(unittest.TestCase):
    def test_key_keeps_group_through_create_update_and_auth(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = JSONStorageBackend(Path(tmp) / "accounts.json", Path(tmp) / "auth_keys.json")
            service = AuthService(storage)

            item, raw_key = service.create_key(role="user", name="组密钥", group_id="g1")
            self.assertEqual(item["group_id"], "g1")

            identity = service.authenticate(raw_key)
            self.assertIsNotNone(identity)
            self.assertEqual(identity["group_id"], "g1")

            updated = service.update_key(item["id"], {"group_id": "g2"}, role="user")
            self.assertIsNotNone(updated)
            self.assertEqual(updated["group_id"], "g2")

            reloaded = AuthService(storage)
            listed = reloaded.list_keys(role="user")
            self.assertEqual(listed[0]["group_id"], "g2")
            self.assertNotIn("key_hash", json.dumps(listed))

    def test_default_group_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = JSONStorageBackend(Path(tmp) / "accounts.json", Path(tmp) / "auth_keys.json")
            service = AuthService(storage)
            item, _ = service.create_key(role="user", name="普通")
            self.assertEqual(item["group_id"], "")


if __name__ == "__main__":
    unittest.main()
