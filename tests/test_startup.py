from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QApplication, QDialog, QLabel

from video_agent.storage.auth import LocalAuthStore
from video_agent.__main__ import _ApplicationFlow
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

    def test_every_successful_login_opens_local_ai_setup_before_main_window(self) -> None:
        fake_login = MagicMock()
        fake_login.exec.return_value = QDialog.DialogCode.Accepted
        fake_login.authenticated_username = "김수사관"
        fake_setup = MagicMock()
        fake_setup.exec.return_value = QDialog.DialogCode.Accepted
        fake_window = MagicMock()
        environment = {
            "VIDEO_AGENT_DATA_DIR": str(Path(self.temporary.name) / "data"),
            "VIDEO_AGENT_CACHE_DIR": str(Path(self.temporary.name) / "cache"),
        }
        with (
            patch.dict(os.environ, environment, clear=False),
            patch("video_agent.__main__.LocalLoginDialog", return_value=fake_login),
            patch("video_agent.__main__.LocalAiSetupDialog", return_value=fake_setup) as setup,
            patch("video_agent.__main__.MainWindow", return_value=fake_window) as main_window,
        ):
            flow = _ApplicationFlow(self.app)
            flow._show_login()
            flow._show_login()

        self.assertEqual(setup.call_count, 2)
        self.assertEqual(fake_setup.exec.call_count, 2)
        self.assertEqual(main_window.call_count, 2)
        main_window.assert_called_with(operator_name="김수사관")
        self.assertEqual(fake_window.show.call_count, 2)


if __name__ == "__main__":
    unittest.main()
