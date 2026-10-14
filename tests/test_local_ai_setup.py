from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PySide6.QtWidgets import QApplication, QDialog

from video_agent.contracts import AnalysisMode
from video_agent.models.config import RuntimeInventory
from video_agent.storage.json_store import SettingsStore
from video_agent.ui.local_ai_setup import LocalAiSetupDialog


class LocalAiSetupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = SettingsStore(Path(self.temporary.name) / "settings.json")
        self.inventory = RuntimeInventory(
            "/opt/homebrew/bin/ollama",
            ("qwen3-vl:4b-instruct", "qwen3-vl:8b-instruct", "text-only:latest"),
            None,
            "http://127.0.0.1:11434/api/chat",
            ("qwen3-vl:4b-instruct", "qwen3-vl:8b-instruct"),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_confirmed_vision_models_are_listed_and_the_choice_is_saved(self) -> None:
        dialog = LocalAiSetupDialog(self.store, inventory=self.inventory)

        self.assertEqual(dialog.model_combo.count(), 2)
        self.assertEqual(dialog.model_combo.itemText(0), "qwen3-vl:4b-instruct")
        self.assertNotIn("text-only", dialog.model_combo.itemText(1))
        dialog.model_combo.setCurrentIndex(1)
        dialog.save_button.click()

        settings = self.store.load()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertIs(settings.mode, AnalysisMode.LOCAL)
        self.assertEqual(settings.local_model_id, "qwen3-vl:8b-instruct")
        self.assertEqual(settings.local_endpoint, "http://127.0.0.1:11434/api/chat")
        self.assertTrue(settings.local_setup_completed)

    def test_external_address_cannot_be_saved_as_a_local_model_endpoint(self) -> None:
        dialog = LocalAiSetupDialog(self.store, inventory=self.inventory)
        dialog.endpoint_edit.setText("https://provider.example/api/chat")

        dialog.save_button.click()

        self.assertNotEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertIn("localhost", dialog.error_label.text())

    def test_user_can_continue_and_reopen_setup_later(self) -> None:
        empty = RuntimeInventory(None, (), None, "http://127.0.0.1:11434/api/chat", ())
        dialog = LocalAiSetupDialog(self.store, inventory=empty)

        dialog.later_button.click()

        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertTrue(self.store.load().local_setup_completed)
        self.assertFalse(dialog.download_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
