from __future__ import annotations

from pathlib import Path
from threading import Event

from PySide6.QtCore import QThread, Signal

from video_agent.contracts import MotionScanProfile
from video_agent.video.motion import FFmpegMotionScanner
from video_agent.video.probe import FFprobeVideoProbe, VideoMetadata
from video_agent.video.process import OperationCanceled


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


class MotionScanWorker(QThread):
    result_ready = Signal(object)
    error = Signal(str)
    canceled = Signal()

    def __init__(
        self,
        scan_id: str,
        path: Path,
        metadata: VideoMetadata,
        range_start_sec: float,
        range_end_sec: float,
        scanner: FFmpegMotionScanner | None = None,
        *,
        profile: MotionScanProfile = MotionScanProfile.GENERAL_PEAK,
    ) -> None:
        super().__init__()
        self._scan_id = scan_id
        self._path = path
        self._metadata = metadata
        self._range_start_sec = range_start_sec
        self._range_end_sec = range_end_sec
        self._profile = profile
        self._scanner = scanner or FFmpegMotionScanner()
        self._cancel_event = Event()

    def request_cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        try:
            result = self._scanner.scan(
                self._scan_id,
                self._path,
                self._metadata,
                self._range_start_sec,
                self._range_end_sec,
                self._cancel_event,
                self._profile,
            )
        except OperationCanceled:
            self.canceled.emit()
            return
        except Exception as exc:  # Boundary between background media work and the UI.
            if self._cancel_event.is_set():
                self.canceled.emit()
            else:
                self.error.emit(str(exc) or exc.__class__.__name__)
            return
        if self._cancel_event.is_set():
            self.canceled.emit()
        else:
            self.result_ready.emit(result)
