from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QCloseEvent
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSlider,
    QSplitter,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from video_agent.agent.planning import AgentSession, AnalysisIntent, PlanningError, WorkflowState
from video_agent.analysis.factory import create_analyzer
from video_agent.analysis.worker import AnalysisWorker
from video_agent.contracts import AnalysisJob, AnalysisMode, JobStatus, ResultOrigin, Segment
from video_agent.models.config import RuntimeInventory
from video_agent.storage.json_store import AppSettings, ResultStore, SettingsStore
from video_agent.storage.paths import AppPaths, CacheManager
from video_agent.video.files import (
    build_video_id,
    format_milliseconds,
    seconds_to_milliseconds,
    validate_video_path,
)
from video_agent.video.probe import VideoMetadata
from video_agent.video.worker import MetadataWorker


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("영상 중요 구간 분석 Agent")
        self.resize(1220, 850)

        self._video_path: Path | None = None
        self._video_id: str | None = None
        self._metadata: VideoMetadata | None = None
        self._duration_ms = 0
        self._seeking = False
        self._worker: AnalysisWorker | None = None
        self._metadata_worker: MetadataWorker | None = None
        self._clarification_id: str | None = None
        self._session = AgentSession()
        self._runtime_inventory = RuntimeInventory.detect()

        self._paths = AppPaths.default()
        self._settings_store = SettingsStore(self._paths.settings_file)
        self._result_store = ResultStore(self._paths)
        self._cache_manager = CacheManager(self._paths.cache_dir)
        try:
            self._settings = self._settings_store.load()
            self._settings_error: str | None = None
        except (OSError, ValueError) as exc:
            self._settings = AppSettings()
            self._settings_error = str(exc)

        self._player = QMediaPlayer(self)
        self._audio_output = QAudioOutput(self)
        self._player.setAudioOutput(self._audio_output)

        self._build_ui()
        self._connect_signals()
        self._restore_settings()
        self._set_idle_state()

    def _build_ui(self) -> None:
        root = QWidget(self)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(18, 16, 18, 18)
        root_layout.setSpacing(12)

        title_row = QHBoxLayout()
        title = QLabel("영상 데이터 중요 구간 선별 Agent")
        title.setObjectName("title")
        mascot = QLabel("[포돌이·포순이 이미지 자리]")
        mascot.setObjectName("placeholder")
        title_row.addWidget(title)
        title_row.addStretch()
        title_row.addWidget(mascot)
        root_layout.addLayout(title_row)

        self._mode_banner = QLabel()
        self._mode_banner.setObjectName("modeBanner")
        self._mode_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root_layout.addWidget(self._mode_banner)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_video_panel())
        splitter.addWidget(self._build_analysis_panel())
        splitter.setSizes([660, 540])
        root_layout.addWidget(splitter, 1)

        self.setCentralWidget(root)
        self.statusBar().showMessage("영상 파일을 선택해 주세요.")
        self.setStyleSheet(
            """
            QMainWindow { background: #f3f6fa; }
            QLabel#title { font-size: 22px; font-weight: 700; color: #18324a; }
            QLabel#placeholder { color: #61758a; border: 1px dashed #9aacbc; padding: 7px; }
            QLabel#modeBanner { border-radius: 6px; padding: 9px; font-weight: 700; }
            QGroupBox { font-weight: 700; border: 1px solid #ccd7e2; border-radius: 7px;
                        margin-top: 9px; padding-top: 10px; background: white; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
            QPushButton { padding: 7px 12px; }
            QListWidget, QPlainTextEdit, QLineEdit { background: white; }
            """
        )

    def _build_video_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 6, 0)

        file_group = QGroupBox("1. 로컬 영상")
        file_box = QVBoxLayout(file_group)
        file_row = QHBoxLayout()
        self._file_label = QLabel("선택된 파일 없음")
        self._file_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._choose_button = QPushButton("영상 선택…")
        file_row.addWidget(self._file_label, 1)
        file_row.addWidget(self._choose_button)
        self._metadata_label = QLabel("메타데이터 미확인")
        self._metadata_label.setWordWrap(True)
        self._metadata_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        file_box.addLayout(file_row)
        file_box.addWidget(self._metadata_label)
        layout.addWidget(file_group)

        self._video_widget = QVideoWidget()
        self._video_widget.setMinimumHeight(350)
        self._video_widget.setStyleSheet("background: #101820;")
        self._player.setVideoOutput(self._video_widget)
        layout.addWidget(self._video_widget, 1)

        controls = QHBoxLayout()
        self._play_button = QPushButton("재생")
        self._play_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self._timeline = QSlider(Qt.Orientation.Horizontal)
        self._timeline.setRange(0, 0)
        self._time_label = QLabel("00:00 / 00:00")
        controls.addWidget(self._play_button)
        controls.addWidget(self._timeline, 1)
        controls.addWidget(self._time_label)
        layout.addLayout(controls)
        return panel

    def _build_analysis_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(6, 0, 0, 0)

        purpose_group = QGroupBox("2. 분석 목적과 범위")
        purpose_layout = QFormLayout(purpose_group)
        self._mode_combo = QComboBox()
        self._mode_combo.addItem("샘플 — 실제 AI 분석 아님", AnalysisMode.SAMPLE)
        self._mode_combo.addItem("로컬 VLM — 명시적 연결", AnalysisMode.LOCAL)
        self._mode_combo.addItem("외부 API VLM — 별도 전송 승인", AnalysisMode.EXTERNAL)
        self._model_edit = QLineEdit()
        self._model_edit.setPlaceholderText("실제 모드에서 영상 입력 지원이 확인된 모델 ID")
        self._purpose_edit = QPlainTextEdit()
        self._purpose_edit.setPlaceholderText(
            "예: 출입문 앞에 사람이 오래 머무르는 것으로 보이는 장면 후보를 찾고 싶습니다."
        )
        self._purpose_edit.setMaximumHeight(75)
        range_row = QWidget()
        range_layout = QHBoxLayout(range_row)
        range_layout.setContentsMargins(0, 0, 0, 0)
        self._range_start = QDoubleSpinBox()
        self._range_end = QDoubleSpinBox()
        for spin in (self._range_start, self._range_end):
            spin.setDecimals(3)
            spin.setRange(0, 0)
            spin.setSuffix(" 초")
        self._range_end.setSpecialValueText("영상 끝")
        range_layout.addWidget(QLabel("시작"))
        range_layout.addWidget(self._range_start)
        range_layout.addWidget(QLabel("종료"))
        range_layout.addWidget(self._range_end)
        purpose_layout.addRow("모드", self._mode_combo)
        purpose_layout.addRow("모델", self._model_edit)
        purpose_layout.addRow("목적", self._purpose_edit)
        purpose_layout.addRow("원본 범위", range_row)
        layout.addWidget(purpose_group)

        plan_group = QGroupBox("3. 추가 질문과 분석 계획")
        plan_layout = QVBoxLayout(plan_group)
        self._question_label = QLabel("추가 질문 없음")
        self._question_label.setWordWrap(True)
        self._clarification_edit = QLineEdit()
        self._clarification_edit.setPlaceholderText("질문이 표시된 경우 답변")
        self._plan_view = QPlainTextEdit()
        self._plan_view.setReadOnly(True)
        self._plan_view.setPlaceholderText("영상 메타데이터와 목적을 확인한 뒤 계획을 만드세요.")
        self._plan_view.setMaximumHeight(145)
        self._make_plan_button = QPushButton("추가 질문 확인 / 계획 만들기")
        plan_layout.addWidget(self._question_label)
        plan_layout.addWidget(self._clarification_edit)
        plan_layout.addWidget(self._plan_view)
        plan_layout.addWidget(self._make_plan_button)
        layout.addWidget(plan_group)

        approval_group = QGroupBox("4. 승인과 실행")
        approval_layout = QVBoxLayout(approval_group)
        self._approve_button = QPushButton("분석 계획 승인")
        self._transfer_button = QPushButton("표시된 외부 전송 내용·범위 별도 승인")
        self._run_button = QPushButton("승인된 계획 실행")
        approval_layout.addWidget(self._approve_button)
        approval_layout.addWidget(self._transfer_button)
        approval_layout.addWidget(self._run_button)
        layout.addWidget(approval_group)

        progress_group = QGroupBox("5. 실행 상태")
        progress_layout = QFormLayout(progress_group)
        self._progress_bar = QProgressBar()
        self._progress_label = QLabel("대기 중")
        self._cancel_button = QPushButton("분석 취소")
        progress_layout.addRow("진행", self._progress_bar)
        progress_layout.addRow("상태", self._progress_label)
        progress_layout.addRow("", self._cancel_button)
        layout.addWidget(progress_group)

        result_group = QGroupBox("6. 중요 구간 후보")
        result_layout = QVBoxLayout(result_group)
        help_label = QLabel("후보를 선택하면 원본 영상이 해당 시작 시점으로 이동합니다.")
        help_label.setWordWrap(True)
        self._result_list = QListWidget()
        self._result_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        result_layout.addWidget(help_label)
        result_layout.addWidget(self._result_list, 1)
        layout.addWidget(result_group, 1)
        return panel

    def _connect_signals(self) -> None:
        self._choose_button.clicked.connect(self._choose_video)
        self._play_button.clicked.connect(self._toggle_playback)
        self._timeline.sliderPressed.connect(self._slider_pressed)
        self._timeline.sliderReleased.connect(self._slider_released)
        self._purpose_edit.textChanged.connect(self._inputs_changed)
        self._mode_combo.currentIndexChanged.connect(self._mode_changed)
        self._model_edit.textChanged.connect(self._inputs_changed)
        self._range_start.valueChanged.connect(self._inputs_changed)
        self._range_end.valueChanged.connect(self._inputs_changed)
        self._make_plan_button.clicked.connect(self._make_plan)
        self._approve_button.clicked.connect(self._approve_plan)
        self._transfer_button.clicked.connect(self._approve_transfer)
        self._run_button.clicked.connect(self._start_analysis)
        self._cancel_button.clicked.connect(self._cancel_analysis)
        self._result_list.itemActivated.connect(self._seek_to_segment)
        self._result_list.itemClicked.connect(self._seek_to_segment)
        self._player.durationChanged.connect(self._on_duration_changed)
        self._player.positionChanged.connect(self._on_position_changed)
        self._player.playbackStateChanged.connect(self._on_playback_state_changed)
        self._player.errorOccurred.connect(self._on_media_error)

    def _restore_settings(self) -> None:
        index = self._mode_combo.findData(self._settings.mode)
        self._mode_combo.blockSignals(True)
        self._model_edit.blockSignals(True)
        self._mode_combo.setCurrentIndex(max(0, index))
        if self._settings.mode is AnalysisMode.LOCAL:
            self._model_edit.setText(self._settings.local_model_id or "")
        elif self._settings.mode is AnalysisMode.EXTERNAL:
            self._model_edit.setText(self._settings.external_model_id or "")
        self._model_edit.setEnabled(self._settings.mode is not AnalysisMode.SAMPLE)
        self._model_edit.blockSignals(False)
        self._mode_combo.blockSignals(False)
        self._update_mode_banner()
        if self._settings_error:
            self.statusBar().showMessage(f"설정 파일을 읽지 못해 기본값을 사용합니다: {self._settings_error}")

    def _set_idle_state(self) -> None:
        self._play_button.setEnabled(False)
        self._make_plan_button.setEnabled(False)
        self._approve_button.setEnabled(False)
        self._transfer_button.setEnabled(False)
        self._run_button.setEnabled(False)
        self._cancel_button.setEnabled(False)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)

    def _choose_video(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "로컬 영상 선택",
            "",
            "영상 파일 (*.mp4 *.mov *.m4v *.avi *.mkv *.webm);;모든 파일 (*)",
        )
        if not filename:
            self.statusBar().showMessage("영상 선택을 취소했습니다.", 4000)
            return
        path = Path(filename)
        try:
            validate_video_path(path)
            video_id = build_video_id(path)
        except (OSError, ValueError) as exc:
            self._show_error(str(exc))
            return
        if self._worker is not None and self._worker.isRunning():
            self._show_error("분석 중에는 영상을 바꿀 수 없습니다. 먼저 분석을 취소해 주세요.")
            return

        self._player.stop()
        self._video_path = path
        self._video_id = video_id
        self._metadata = None
        self._duration_ms = 0
        self._file_label.setText(path.name)
        self._file_label.setToolTip(str(path))
        self._metadata_label.setText("FFprobe 메타데이터 확인 중…")
        self._timeline.setRange(0, 0)
        self._time_label.setText("00:00 / 길이 확인 중")
        self._play_button.setEnabled(False)
        self._result_list.clear()
        self._invalidate_plan_display()
        self._player.setSource(QUrl.fromLocalFile(str(path)))
        self._start_metadata_probe(path)

    def _start_metadata_probe(self, path: Path) -> None:
        self._choose_button.setEnabled(False)
        worker = MetadataWorker(path)
        worker.metadata_ready.connect(self._on_metadata_ready)
        worker.error.connect(self._on_metadata_error)
        worker.finished.connect(self._on_metadata_finished)
        self._metadata_worker = worker
        worker.start()

    def _on_metadata_ready(self, metadata: VideoMetadata) -> None:
        if self._video_path is None or metadata.video_id != build_video_id(self._video_path):
            return
        self._metadata = metadata
        self._video_id = metadata.video_id
        maximum = max(0.001, metadata.duration_seconds)
        self._range_start.blockSignals(True)
        self._range_end.blockSignals(True)
        self._range_start.setRange(0, max(0, maximum - 0.001))
        self._range_end.setRange(0, maximum)
        self._range_start.setValue(0)
        self._range_end.setValue(0)
        self._range_start.blockSignals(False)
        self._range_end.blockSignals(False)
        average_rate = "알 수 없음" if metadata.average_frame_rate is None else f"{metadata.average_frame_rate:.3f}"
        if metadata.variable_frame_rate_detected is True:
            vfr = "VFR 징후 감지"
        elif metadata.variable_frame_rate_detected is None:
            vfr = "VFR 여부 미확정"
        else:
            vfr = "VFR 징후 미감지(고정 FPS 단정 안 함)"
        self._metadata_label.setText(
            f"{metadata.display_width}×{metadata.display_height} · {metadata.duration_seconds:.3f}초 · "
            f"평균 FPS {average_rate} · 회전 {metadata.rotation_degrees}° · {vfr}"
        )
        self._make_plan_button.setEnabled(True)
        self._inputs_changed()
        self.statusBar().showMessage("영상 메타데이터 확인 완료", 5000)

    def _on_metadata_error(self, message: str) -> None:
        self._metadata = None
        self._metadata_label.setText("메타데이터 확인 실패")
        self._make_plan_button.setEnabled(False)
        self._show_error(message)

    def _on_metadata_finished(self) -> None:
        self._choose_button.setEnabled(self._worker is None or not self._worker.isRunning())

    def _toggle_playback(self) -> None:
        if self._player.playbackState() is QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _slider_pressed(self) -> None:
        self._seeking = True

    def _slider_released(self) -> None:
        self._player.setPosition(self._timeline.value())
        self._seeking = False

    def _on_duration_changed(self, duration_ms: int) -> None:
        self._duration_ms = max(0, duration_ms)
        self._timeline.setRange(0, self._duration_ms)
        self._play_button.setEnabled(self._duration_ms > 0)
        self._time_label.setText(f"00:00 / {format_milliseconds(self._duration_ms)}")

    def _on_position_changed(self, position_ms: int) -> None:
        if not self._seeking:
            self._timeline.setValue(position_ms)
        self._time_label.setText(
            f"{format_milliseconds(position_ms)} / {format_milliseconds(self._duration_ms)}"
        )

    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        playing = state is QMediaPlayer.PlaybackState.PlayingState
        self._play_button.setText("일시정지" if playing else "재생")
        icon = QStyle.StandardPixmap.SP_MediaPause if playing else QStyle.StandardPixmap.SP_MediaPlay
        self._play_button.setIcon(self.style().standardIcon(icon))

    def _on_media_error(self, error: QMediaPlayer.Error, error_string: str) -> None:
        if error is QMediaPlayer.Error.NoError:
            return
        self._duration_ms = 0
        self._play_button.setEnabled(False)
        self._invalidate_plan_display()
        detail = error_string or "지원되지 않거나 손상된 영상일 수 있습니다."
        self._show_error(f"영상을 재생할 수 없습니다: {detail}")

    def _mode_changed(self) -> None:
        mode = self._current_mode()
        self._model_edit.blockSignals(True)
        if mode is AnalysisMode.SAMPLE:
            self._model_edit.clear()
        elif mode is AnalysisMode.LOCAL:
            self._model_edit.setText(
                os.environ.get("VIDEO_AGENT_LOCAL_MODEL") or self._settings.local_model_id or ""
            )
        else:
            self._model_edit.setText(
                os.environ.get("VIDEO_AGENT_EXTERNAL_MODEL")
                or self._settings.external_model_id
                or ""
            )
        self._model_edit.setEnabled(mode is not AnalysisMode.SAMPLE)
        self._model_edit.blockSignals(False)
        self._update_mode_banner()
        self._inputs_changed()
        self._persist_settings()

    def _current_mode(self) -> AnalysisMode:
        return AnalysisMode(self._mode_combo.currentData())

    def _current_intent(self) -> AnalysisIntent:
        mode = self._current_mode()
        if mode is AnalysisMode.LOCAL:
            provider_id = os.environ.get("VIDEO_AGENT_LOCAL_PROVIDER", "ollama-local")
            endpoint = os.environ.get("VIDEO_AGENT_LOCAL_ENDPOINT", "http://127.0.0.1:11434/api/chat")
        elif mode is AnalysisMode.EXTERNAL:
            provider_id = os.environ.get("VIDEO_AGENT_EXTERNAL_PROVIDER") or self._settings.external_provider_id
            endpoint = os.environ.get("VIDEO_AGENT_EXTERNAL_ENDPOINT") or self._settings.external_endpoint
        else:
            provider_id = None
            endpoint = None
        return AnalysisIntent(
            purpose=self._purpose_edit.toPlainText().strip(),
            mode=mode,
            model_id=self._model_edit.text().strip() or None,
            range_start_sec=self._range_start.value(),
            range_end_sec=self._range_end.value() or None,
            provider_id=provider_id,
            provider_endpoint=endpoint,
        )

    def _inputs_changed(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        try:
            self._session.update_context(self._metadata, self._current_intent())
        except PlanningError as exc:
            self.statusBar().showMessage(str(exc), 5000)
            return
        self._invalidate_plan_display()
        self._persist_settings()

    def _invalidate_plan_display(self) -> None:
        self._plan_view.clear()
        self._question_label.setText("추가 질문 없음")
        self._clarification_edit.clear()
        self._clarification_edit.setEnabled(True)
        self._clarification_id = None
        self._approve_button.setEnabled(False)
        self._transfer_button.setEnabled(False)
        self._run_button.setEnabled(False)

    def _make_plan(self) -> None:
        if self._metadata is None or self._video_path is None:
            self._show_error("먼저 FFprobe로 메타데이터를 확인할 수 있는 영상을 선택해 주세요.")
            return
        if not self._purpose_edit.toPlainText().strip():
            self._show_error("분석 목적을 입력해 주세요.")
            return
        self._session.update_context(self._metadata, self._current_intent())
        questions = self._session.questions()
        if questions:
            question = questions[0]
            if self._clarification_id == question.question_id and self._clarification_edit.text().strip():
                self._session.answer(question.question_id, self._clarification_edit.text())
                questions = self._session.questions()
            if questions:
                question = questions[0]
                self._clarification_id = question.question_id
                self._question_label.setText(f"추가 질문: {question.prompt}\n이유: {question.reason}")
                self._clarification_edit.setFocus()
                self.statusBar().showMessage("추가 질문에 답한 뒤 계획 버튼을 다시 눌러 주세요.")
                return
        try:
            plan = self._session.prepare_plan()
        except PlanningError as exc:
            self._show_error(str(exc))
            return

        sampling = (
            "프레임 미추출(길이 비율 기반 샘플 결과)"
            if plan.mode is AnalysisMode.SAMPLE
            else f"원본 타임스탬프 균등 샘플 {len(plan.sample_timestamps_sec)}개, 약 {plan.sampling_interval_sec:.3f}초 간격"
        )
        lines = [
            f"모드: {plan.mode.value} · 모델: {plan.model_id or '없음(샘플)'}",
            f"분석 대상: {plan.video_id} · 원본 {plan.range_start_sec:.3f}~{plan.range_end_sec:.3f}초",
            f"샘플링: {sampling}",
            f"한도: {plan.limits.max_runtime_seconds:g}초 · 프레임 {plan.limits.max_frames}개 · 자동 재분석 {plan.limits.max_reanalysis_attempts}회",
            "범위 확대가 필요하면 자동 실행하지 않고 제안 상태로 남깁니다.",
        ]
        if plan.transfer_manifest:
            manifest = plan.transfer_manifest
            times = ", ".join(f"{item:.3f}s" for item in manifest.frame_timestamps_sec)
            lines.extend(
                [
                    "[외부 전송은 분석 승인과 별도 승인 필요]",
                    f"공급자/목적지: {manifest.provider_id} · {manifest.endpoint_origin}",
                    f"전송: 분석 목적 텍스트 + JPEG 프레임 {len(manifest.frame_timestamps_sec)}개 ({times})",
                    "PDF·원본 영상 전체·라벨 JSON은 전송하지 않습니다.",
                ]
            )
        self._plan_view.setPlainText("\n".join(lines))
        self._clarification_edit.setEnabled(False)
        self._approve_button.setEnabled(True)
        self._transfer_button.setEnabled(False)
        self._run_button.setEnabled(False)
        self.statusBar().showMessage("계획을 확인한 뒤 분석 계획을 승인해 주세요.", 5000)

    def _approve_plan(self) -> None:
        try:
            self._session.approve_analysis()
        except PlanningError as exc:
            self._show_error(str(exc))
            return
        external = self._session.plan is not None and self._session.plan.mode is AnalysisMode.EXTERNAL
        self._transfer_button.setEnabled(external)
        self._run_button.setEnabled(not external)
        self._approve_button.setEnabled(False)
        self.statusBar().showMessage(
            "분석 계획 승인 완료. 외부 전송은 아직 승인되지 않았습니다."
            if external
            else "분석 계획 승인 완료. 실행할 수 있습니다.",
            7000,
        )

    def _approve_transfer(self) -> None:
        try:
            self._session.approve_external_transfer()
        except PlanningError as exc:
            self._show_error(str(exc))
            return
        self._transfer_button.setEnabled(False)
        self._run_button.setEnabled(True)
        self.statusBar().showMessage("현재 계획의 외부 전송 내용과 범위를 별도로 승인했습니다.", 7000)

    def _start_analysis(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._show_error("이미 분석이 실행 중입니다. 중복 실행할 수 없습니다.")
            return
        if self._session.plan is None or self._video_path is None:
            self._show_error("승인된 분석 계획이 없습니다.")
            return
        try:
            analyzer = create_analyzer(
                self._session.plan.mode,
                self._session.plan.model_id,
                self._cache_manager.create_job_dir,
                provider_id=self._session.plan.provider_id,
                provider_endpoint=self._session.plan.provider_endpoint,
            )
            request = self._session.create_request(
                str(self._video_path), f"{self._session.plan.mode.value}-{uuid4().hex[:12]}"
            )
        except (PlanningError, OSError, ValueError) as exc:
            self._show_error(str(exc))
            return

        worker = AnalysisWorker(analyzer, request)
        worker.progress_changed.connect(self._on_analysis_progress)
        worker.result_ready.connect(self._on_analysis_result)
        worker.finished.connect(self._on_worker_finished)
        self._worker = worker
        self._result_list.clear()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_label.setText("분석 시작")
        self._set_analysis_running(True)
        worker.start()

    def _set_analysis_running(self, running: bool) -> None:
        self._choose_button.setEnabled(not running and not self._metadata_probe_running())
        for widget in (
            self._purpose_edit,
            self._mode_combo,
            self._model_edit,
            self._range_start,
            self._range_end,
            self._clarification_edit,
            self._make_plan_button,
            self._approve_button,
            self._transfer_button,
            self._run_button,
        ):
            widget.setEnabled(not running)
        self._cancel_button.setEnabled(running)

    def _on_analysis_progress(self, progress: int | None, message: str) -> None:
        if self._session.active_job_id is None:
            return
        if progress is None:
            self._progress_bar.setRange(0, 0)
            self._progress_label.setText(message)
        else:
            self._progress_bar.setRange(0, 100)
            self._progress_bar.setValue(progress)
            self._progress_label.setText(f"{message} · {progress}%")

    def _cancel_analysis(self) -> None:
        if self._worker is None or not self._worker.isRunning():
            self.statusBar().showMessage("취소할 분석 작업이 없습니다.", 4000)
            return
        self._session.cancel_active()
        self._worker.request_cancel()
        self._cancel_button.setEnabled(False)
        self._progress_label.setText("취소됨 · 백그라운드 작업 정리 중")
        self.statusBar().showMessage("취소 이후 도착하는 결과는 표시하지 않습니다.", 5000)

    def _on_analysis_result(self, job: AnalysisJob) -> None:
        accepted = self._session.accept_result(
            job.job_id,
            failed=job.status is JobStatus.FAILED,
            canceled=job.status is JobStatus.CANCELED,
        )
        if not accepted:
            return
        try:
            self._result_store.save(job)
        except (OSError, ValueError) as exc:
            self.statusBar().showMessage(f"결과 저장 실패: {exc}", 8000)

        self._progress_bar.setRange(0, 100)
        if job.status is JobStatus.COMPLETED:
            self._progress_bar.setValue(100)
            origin_text = {
                ResultOrigin.SAMPLE: "샘플 결과 — 실제 AI 분석 아님",
                ResultOrigin.TEST_DOUBLE: "테스트용 가짜 공급자 응답 — 실제 모델 아님",
                ResultOrigin.REAL_MODEL: f"실제 모델 응답 · {job.model_id}",
            }[job.origin]
            self._progress_label.setText(f"완료 · {origin_text}")
            self._set_result_banner(job.origin, job.model_id)
            for segment in job.segments:
                item = QListWidgetItem(self._segment_text(segment))
                item.setData(Qt.ItemDataRole.UserRole, segment)
                self._result_list.addItem(item)
        elif job.status is JobStatus.CANCELED:
            self._progress_label.setText("사용자 요청으로 취소됨")
        else:
            self._progress_bar.setValue(0)
            self._progress_label.setText("오류로 종료됨")
            self._show_error(job.error or "알 수 없는 분석 오류가 발생했습니다.")

    def _on_worker_finished(self) -> None:
        self._set_analysis_running(False)
        self._model_edit.setEnabled(self._current_mode() is not AnalysisMode.SAMPLE)
        self._cancel_button.setEnabled(False)
        self._worker = None
        self._update_post_run_buttons()

    def _update_post_run_buttons(self) -> None:
        self._make_plan_button.setEnabled(self._metadata is not None)
        self._clarification_edit.setEnabled(True)
        self._approve_button.setEnabled(self._session.state is WorkflowState.PLAN_READY)
        self._transfer_button.setEnabled(
            self._session.state is WorkflowState.ANALYSIS_APPROVED
            and self._current_mode() is AnalysisMode.EXTERNAL
        )
        self._run_button.setEnabled(self._session.state is WorkflowState.APPROVED)

    def _set_result_banner(self, origin: ResultOrigin, model_id: str | None) -> None:
        if origin is ResultOrigin.SAMPLE:
            self._mode_banner.setText("샘플 결과 — 실제 AI 분석 아님")
            self._mode_banner.setStyleSheet("background:#fff0d7;color:#813f00;border:1px solid #e6a654;")
        elif origin is ResultOrigin.TEST_DOUBLE:
            self._mode_banner.setText("테스트용 가짜 모델 응답 — 실제 모델 검증 아님")
            self._mode_banner.setStyleSheet("background:#f5e9ff;color:#5d2b7a;border:1px solid #b98bd0;")
        else:
            self._mode_banner.setText(f"실제 모델 응답 — {model_id}")
            self._mode_banner.setStyleSheet("background:#e4f4eb;color:#125d35;border:1px solid #75b991;")

    def _update_mode_banner(self) -> None:
        mode = self._current_mode()
        if mode is AnalysisMode.SAMPLE:
            self._set_result_banner(ResultOrigin.SAMPLE, None)
        elif mode is AnalysisMode.LOCAL:
            if self._runtime_inventory.ollama_executable is None:
                status = "설치된 Ollama 런타임 없음"
            elif not self._runtime_inventory.installed_ollama_models:
                status = "설치 모델 확인 안 됨"
            else:
                status = "설치 모델: " + ", ".join(self._runtime_inventory.installed_ollama_models)
            self._mode_banner.setText(f"로컬 VLM 모드 — {status} · 자동 외부 전환 없음")
            self._mode_banner.setStyleSheet("background:#e8f1fb;color:#174d78;border:1px solid #8bb6d9;")
        else:
            self._mode_banner.setText("외부 API 모드 — 분석 승인과 전송 승인이 각각 필요")
            self._mode_banner.setStyleSheet("background:#fdeaea;color:#7a2020;border:1px solid #d89a9a;")

    @staticmethod
    def _segment_text(segment: Segment) -> str:
        start = format_milliseconds(seconds_to_milliseconds(segment.start_sec))
        end = format_milliseconds(seconds_to_milliseconds(segment.end_sec))
        evidence = " / ".join(segment.evidence)
        frames = f"\n원본 프레임: {', '.join(segment.source_frame_ids)}" if segment.source_frame_ids else ""
        return (
            f"{start}–{end}  {segment.summary}\n"
            f"근거: {evidence}{frames}\n"
            f"불확실성: {segment.uncertainty}"
        )

    def _seek_to_segment(self, item: QListWidgetItem) -> None:
        segment = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(segment, Segment):
            return
        position_ms = seconds_to_milliseconds(segment.start_sec)
        self._player.setPosition(min(position_ms, self._duration_ms))
        self.statusBar().showMessage(
            f"원본 {format_milliseconds(position_ms)} 위치로 이동했습니다.", 4000
        )

    def _persist_settings(self) -> None:
        try:
            mode = self._current_mode()
            settings = AppSettings(
                mode=mode,
                local_model_id=(
                    self._model_edit.text().strip() or None
                    if mode is AnalysisMode.LOCAL
                    else self._settings.local_model_id
                ),
                external_provider_id=(
                    os.environ.get("VIDEO_AGENT_EXTERNAL_PROVIDER")
                    or self._settings.external_provider_id
                ),
                external_model_id=(
                    self._model_edit.text().strip() or None
                    if mode is AnalysisMode.EXTERNAL
                    else self._settings.external_model_id
                ),
                external_endpoint=(
                    os.environ.get("VIDEO_AGENT_EXTERNAL_ENDPOINT")
                    or self._settings.external_endpoint
                ),
                api_key_env_name=os.environ.get(
                    "VIDEO_AGENT_EXTERNAL_API_KEY_ENV", "VIDEO_AGENT_EXTERNAL_API_KEY"
                ),
            )
            self._settings_store.save(settings)
            self._settings = settings
        except (OSError, ValueError) as exc:
            self.statusBar().showMessage(f"설정 저장 실패: {exc}", 6000)

    def _metadata_probe_running(self) -> bool:
        return self._metadata_worker is not None and self._metadata_worker.isRunning()

    def _show_error(self, message: str) -> None:
        self.statusBar().showMessage(message, 8000)
        QMessageBox.critical(self, "오류", message)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt override name
        workers = [item for item in (self._worker, self._metadata_worker) if item and item.isRunning()]
        for worker in workers:
            worker.request_cancel()
        if any(not worker.wait(2000) for worker in workers):
            event.ignore()
            QMessageBox.warning(self, "종료 대기", "작업을 정리하고 있습니다. 잠시 후 다시 종료해 주세요.")
            return
        self._player.stop()
        event.accept()
