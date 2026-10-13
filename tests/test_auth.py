from __future__ import annotations

import json
import stat
import tempfile
import unittest
from pathlib import Path

from video_agent.storage.auth import LocalAuthStore


class LocalAuthStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "data" / "local_account.json"
        self.store = LocalAuthStore(self.path)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_password_is_hashed_and_authenticates_only_the_registered_user(self) -> None:
        account = self.store.create("김수사관", "local-password-123")

        self.assertEqual(account.username, "김수사관")
        self.assertTrue(self.store.authenticate("김수사관", "local-password-123"))
        self.assertFalse(self.store.authenticate("다른 사용자", "local-password-123"))
        self.assertFalse(self.store.authenticate("김수사관", "wrong-password"))
        stored = self.path.read_text(encoding="utf-8")
        self.assertNotIn("local-password-123", stored)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_short_password_and_duplicate_username_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "8자 이상"):
            self.store.create("김수사관", "short")
        self.store.create("김수사관", "long-enough-password")

        with self.assertRaisesRegex(ValueError, "이미 등록"):
            self.store.create("김수사관", "another-password")

    def test_multiple_local_users_have_independent_passwords(self) -> None:
        self.store.create("김수사관", "first-password")
        self.store.create("박분석관", "second-password")

        self.assertEqual(self.store.list_usernames(), ("김수사관", "박분석관"))
        self.assertTrue(self.store.authenticate("김수사관", "first-password"))
        self.assertTrue(self.store.authenticate("박분석관", "second-password"))
        self.assertFalse(self.store.authenticate("김수사관", "second-password"))

    def test_legacy_single_account_is_read_and_migrated_when_a_user_joins(self) -> None:
        self.store.create("기존 사용자", "legacy-password")
        current = json.loads(self.path.read_text(encoding="utf-8"))
        first = current["accounts"][0]
        legacy = {
            "version": 1,
            "algorithm": current["algorithm"],
            "iterations": current["iterations"],
            **first,
        }
        self.path.write_text(json.dumps(legacy), encoding="utf-8")

        self.assertTrue(self.store.authenticate("기존 사용자", "legacy-password"))
        self.store.create("새 사용자", "new-user-password")

        migrated = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(migrated["version"], 2)
        self.assertEqual(self.store.list_usernames(), ("기존 사용자", "새 사용자"))

    def test_unknown_or_damaged_credential_fields_are_rejected(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text(
            json.dumps({"username": "김수사관", "secret": "plain"}), encoding="utf-8"
        )

        with self.assertRaises(ValueError):
            self.store.load()


if __name__ == "__main__":
    unittest.main()
