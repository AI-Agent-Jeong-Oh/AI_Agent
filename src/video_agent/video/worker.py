from __future__ import annotations

from pathlib import Path
from threading import Event

from PySide6.QtCore import QThread, Signal

from video_agent.video.probe import FFprobeVideoProbe


class MetadataWorker(QThread):
    metadata_ready = Signal(object)
    error = Signal(str)

    def __init__(self, path: Path, probe: FFprobeVideoProbe | None = None) -> None:
        super().__init__()
        self.path = path
        self._probe = probe or FFprobeVideoProbe()
        self._cancel_event = Event()

    def request_cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        try:
            metadata = self._probe.probe(self.path, self._cancel_event)
        except Exception as exc:
            if not self._cancel_event.is_set():
                self.error.emit(str(exc) or exc.__class__.__name__)
            return
        if not self._cancel_event.is_set():
            self.metadata_ready.emit(metadata)
