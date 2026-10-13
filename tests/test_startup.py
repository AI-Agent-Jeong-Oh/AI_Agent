from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PySide6.QtWidgets import QApplication, QDialog, QLabel

from video_agent.storage.auth import LocalAuthStore
from video_agent.ui.startup import LocalLoginDialog, StartupScreen


class StartupUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = LocalAuthStore(Path(self.temporary.name) / "local_account.json")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_loading_screen_uses_unknown_progress_and_mascot_art(self) -> None:
        screen = StartupScreen(duration_ms=0)

        self.assertEqual(screen.progress.minimum(), 0)
        self.assertEqual(screen.progress.maximum(), 0)
        self.assertEqual(screen.status_label.text(), "프로그램을 여는 중입니다")
        mascots = screen.findChild(QLabel, "startupMascots")
        self.assertIsNotNone(mascots)
        self.assertFalse(mascots.pixmap().isNull())
        screen.close()

    def test_first_run_registers_a_local_user_without_plaintext_password(self) -> None:
        dialog = LocalLoginDialog(self.store)
        dialog.username_edit.setText("김수사관")
        dialog.password_edit.setText("local-password-123")
        dialog.confirm_edit.setText("local-password-123")

        dialog.submit_button.click()

        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(dialog.authenticated_username, "김수사관")
        self.assertTrue(self.store.authenticate("김수사관", "local-password-123"))

    def test_existing_user_needs_the_correct_password(self) -> None:
        self.store.create("김수사관", "local-password-123")
        dialog = LocalLoginDialog(self.store)
        dialog.password_edit.setText("wrong-password")

        dialog.submit_button.click()

        self.assertNotEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertIn("확인해 주세요", dialog.error_label.text())
        self.assertEqual(dialog.password_edit.text(), "")

        dialog.password_edit.setText("local-password-123")
        dialog.submit_button.click()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(dialog.authenticated_username, "김수사관")

    def test_existing_user_can_switch_to_signup_and_add_an_account(self) -> None:
        self.store.create("김수사관", "local-password-123")
        dialog = LocalLoginDialog(self.store)

        self.assertEqual(dialog.submit_button.text(), "로그인")
        self.assertTrue(dialog.mode_button.isVisibleTo(dialog))
        dialog.mode_button.click()
        self.assertEqual(dialog.submit_button.text(), "가입하고 시작")
        self.assertFalse(dialog.confirm_edit.isHidden())

        dialog.username_edit.setText("박분석관")
        dialog.password_edit.setText("second-password")
        dialog.confirm_edit.setText("second-password")
        dialog.submit_button.click()

        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(dialog.authenticated_username, "박분석관")
        self.assertTrue(self.store.authenticate("김수사관", "local-password-123"))
        self.assertTrue(self.store.authenticate("박분석관", "second-password"))


if __name__ == "__main__":
    unittest.main()
