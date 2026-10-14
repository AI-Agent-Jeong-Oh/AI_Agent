from __future__ import annotations

import os
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialog

from video_agent.storage.auth import LocalAuthStore
from video_agent.storage.json_store import SettingsStore
from video_agent.storage.paths import AppPaths
from video_agent.ui.local_ai_setup import LocalAiSetupDialog
from video_agent.ui.main_window import MainWindow
from video_agent.ui.startup import LocalLoginDialog, StartupScreen


class _ApplicationFlow:
    """Keep each startup window alive and open the main app only after local login."""

    def __init__(self, app: QApplication) -> None:
        self._app = app
        self._paths = AppPaths.default()
        self._startup = StartupScreen()
        self._login: LocalLoginDialog | None = None
        self._local_ai_setup: LocalAiSetupDialog | None = None
        self._window: MainWindow | None = None

    def start(self) -> None:
        self._startup.finished.connect(self._show_login)
        self._startup.show()
        self._startup.start()

    def _show_login(self) -> None:
        self._login = LocalLoginDialog(LocalAuthStore(self._paths.credentials_file))
        result = self._login.exec()
        username = self._login.authenticated_username
        if result != QDialog.DialogCode.Accepted or not username:
            self._app.quit()
            return
        settings_store = SettingsStore(self._paths.settings_file)
        try:
            needs_setup = not settings_store.load().local_setup_completed
        except (OSError, ValueError):
            needs_setup = True
        if needs_setup:
            self._local_ai_setup = LocalAiSetupDialog(settings_store)
            if self._local_ai_setup.exec() != QDialog.DialogCode.Accepted:
                self._app.quit()
                return
            self._local_ai_setup = None
        self._window = MainWindow(operator_name=username)
        self._window.closed.connect(self._window_closed)
        self._window.show()

    def _window_closed(self, logging_out: bool) -> None:
        self._window = None
        if logging_out:
            QTimer.singleShot(0, self._show_login)
        else:
            self._app.quit()


def main() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("숭재 수사대 다연캅")
    app.setOrganizationName("AI-Agent-Jeong-Oh")
    flow = _ApplicationFlow(app)
    flow.start()
    smoke_exit_ms = os.environ.get("VIDEO_AGENT_SMOKE_EXIT_MS")
    if smoke_exit_ms:
        try:
            delay = min(10_000, max(0, int(smoke_exit_ms)))
        except ValueError:
            delay = 0
        QTimer.singleShot(delay, app.quit)
    result = app.exec()
    del flow
    return result


if __name__ == "__main__":
    raise SystemExit(main())
