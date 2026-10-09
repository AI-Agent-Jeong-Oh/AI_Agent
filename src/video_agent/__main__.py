from __future__ import annotations

import os
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from video_agent.ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("영상 중요 구간 분석 Agent")
    app.setOrganizationName("AI-Agent-Jeong-Oh")
    window = MainWindow()
    window.show()
    smoke_exit_ms = os.environ.get("VIDEO_AGENT_SMOKE_EXIT_MS")
    if smoke_exit_ms:
        try:
            delay = min(10_000, max(0, int(smoke_exit_ms)))
        except ValueError:
            delay = 0
        QTimer.singleShot(delay, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
