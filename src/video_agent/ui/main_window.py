from __future__ import annotations

import os
import time
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QCloseEvent, QFont, QKeySequence
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
    QScrollArea,
    QSlider,
    QSplitter,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from video_agent.agent.planning import AgentSession, AnalysisIntent, PlanningError, WorkflowState
from video_agent.analysis.evidence import (
    duration_requirement,
    fine_detail_requirement,
    observation_span_seconds,
    temporal_motion_requirement,
)
from video_agent.analysis.factory import create_analyzer
from video_agent.analysis.worker import AnalysisWorker
from video_agent.contracts import (
    AnalysisJob,
    AnalysisLimits,
    AnalysisMode,
    JobStatus,
    MotionProposal,
    MotionScanProfile,
    MotionScanResult,
    ResultOrigin,
    SamplingStrategy,
    Segment,
)
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
from video_agent.video.motion import (
    build_motion_sample_timestamps,
    build_temporal_sample_timestamps,
)
from video_agent.video.worker import MetadataWorker, MotionScanWorker


class MainWindow(QMainWindow):
    UI_ZOOM_MIN = 75
    UI_ZOOM_MAX = 150
    UI_ZOOM_STEP = 10

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("영상 중요 구간 분석 Agent")
        self.resize(1220, 850)

        self._base_font = self.font()
        self._ui_zoom_percent = 100

        self._video_path: Path | None = None
        self._video_id: str | None = None
        self._metadata: VideoMetadata | None = None
        self._duration_ms = 0
        self._seeking = False
        self._worker: AnalysisWorker | None = None
        self._metadata_worker: MetadataWorker | None = None
        self._motion_worker: MotionScanWorker | None = None
        self._active_motion_scan_id: str | None = None
        self._sampling_interval_override: float | None = None
        self._sampling_timestamps_override: tuple[float, ...] = ()
        self._sampling_strategy_override: SamplingStrategy | None = None
        self._applied_motion_proposals: tuple[MotionProposal, ...] = ()
        self._motion_scan_profile = MotionScanProfile.GENERAL_PEAK
        self._motion_scan_purpose: str | None = None
        self._clarification_id: str | None = None
        self._analysis_started_at: float | None = None
        self._last_progress_message = "대기 중"
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(1000)
        self._elapsed_timer.timeout.connect(self._refresh_elapsed_status)
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
        self._install_zoom_shortcuts()
        self._apply_ui_zoom()
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
        zoom_controls = QHBoxLayout()
        zoom_controls.setSpacing(4)
        zoom_label = QLabel("화면 크기")
        self._zoom_out_button = QPushButton("−")
        self._zoom_out_button.setToolTip("화면 축소 (⌘-)")
        self._zoom_reset_button = QPushButton("100%")
        self._zoom_reset_button.setToolTip("기본 화면 크기 (⌘0)")
        self._zoom_in_button = QPushButton("+")
        self._zoom_in_button.setToolTip("화면 확대 (⌘+)")
        zoom_controls.addWidget(zoom_label)
        zoom_controls.addWidget(self._zoom_out_button)
        zoom_controls.addWidget(self._zoom_reset_button)
        zoom_controls.addWidget(self._zoom_in_button)
        title_row.addLayout(zoom_controls)
        title_row.addWidget(mascot)
        root_layout.addLayout(title_row)

        self._mode_banner = QLabel()
        self._mode_banner.setObjectName("modeBanner")
        self._mode_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root_layout.addWidget(self._mode_banner)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_video_panel())
        analysis_panel = self._build_analysis_panel()
        self._analysis_scroll = QScrollArea()
        self._analysis_scroll.setWidgetResizable(True)
        self._analysis_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._analysis_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._analysis_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._analysis_scroll.setWidget(analysis_panel)
        splitter.addWidget(self._analysis_scroll)
        splitter.setSizes([660, 540])
        root_layout.addWidget(splitter, 1)

        self._root_layout = root_layout

        self.setCentralWidget(root)
        self.statusBar().showMessage("영상 파일을 선택해 주세요.")

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

        self._plan_group = QGroupBox("3. 추가 질문과 분석 계획")
        plan_layout = QVBoxLayout(self._plan_group)
        self._motion_help = QLabel(
            "로컬 픽셀 변화가 큰 짧은 구간을 먼저 제안합니다. 상위 3개가 기본 선택되며 "
            "각 항목의 체크박스로 최대 3개를 조정할 수 있습니다. 행동 판정이나 AI 결과가 아닙니다."
        )
        self._motion_help.setWordWrap(True)
        motion_buttons = QHBoxLayout()
        self._motion_scan_button = QPushButton("현재 범위 움직임 후보 찾기")
        self._motion_apply_button = QPushButton("선택 후보(최대 3개)를 분석 계획에 적용")
        motion_buttons.addWidget(self._motion_scan_button)
        motion_buttons.addWidget(self._motion_apply_button)
        self._motion_list = QListWidget()
        self._motion_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._motion_list.setToolTip(
            "변화 점수는 저해상도 프레임 차이이며 행동 확률이 아닙니다. "
            "각 항목의 체크박스로 최대 3개를 선택할 수 있습니다."
        )
        self._motion_selection_label = QLabel("선택한 분석 구간: 0/3")
        self._motion_selection_label.setWordWrap(True)
        self._selected_motion_list = QListWidget()
        self._selected_motion_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        selected_motion_buttons = QHBoxLayout()
        self._selected_motion_uncheck_button = QPushButton("선택 목록의 항목 해제")
        self._selected_motion_clear_button = QPushButton("전체 선택 해제")
        selected_motion_buttons.addWidget(self._selected_motion_uncheck_button)
        selected_motion_buttons.addWidget(self._selected_motion_clear_button)
        self._question_label = QLabel("추가 질문 없음")
        self._question_label.setWordWrap(True)
        self._clarification_edit = QLineEdit()
        self._clarification_edit.setPlaceholderText("질문이 표시된 경우 답변")
        self._plan_view = QPlainTextEdit()
        self._plan_view.setReadOnly(True)
        self._plan_view.setPlaceholderText("영상 메타데이터와 목적을 확인한 뒤 계획을 만드세요.")
        self._make_plan_button = QPushButton("추가 질문 확인 / 계획 만들기")
        plan_layout.addWidget(self._motion_help)
        plan_layout.addLayout(motion_buttons)
        plan_layout.addWidget(self._motion_list)
        plan_layout.addWidget(self._motion_selection_label)
        plan_layout.addWidget(self._selected_motion_list)
        plan_layout.addLayout(selected_motion_buttons)
        plan_layout.addWidget(self._question_label)
        plan_layout.addWidget(self._clarification_edit)
        plan_layout.addWidget(self._plan_view)
        plan_layout.addWidget(self._make_plan_button)
        layout.addWidget(self._plan_group)

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
        self._cancel_button = QPushButton("작업 취소")
        progress_layout.addRow("진행", self._progress_bar)
        progress_layout.addRow("상태", self._progress_label)
        progress_layout.addRow("", self._cancel_button)
        layout.addWidget(progress_group)

        result_group = QGroupBox("6. 중요 구간 후보")
        result_layout = QVBoxLayout(result_group)
        help_label = QLabel(
            "후보를 선택하면 원본 영상이 해당 시작 시점으로 이동합니다. "
            "입력한 분석 구간 수와 결과 후보 수는 같지 않을 수 있으며, 결과는 0개·1개·여러 개이거나 중복 정리로 합쳐질 수 있습니다."
        )
        help_label.setWordWrap(True)
        self._result_list = QListWidget()
        self._result_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        result_layout.addWidget(help_label)
        result_layout.addWidget(self._result_list, 1)
        layout.addWidget(result_group, 1)
        self._analysis_layout = layout
        self._purpose_group = purpose_group
        self._approval_group = approval_group
        self._progress_group = progress_group
        self._result_group = result_group
        return panel

    def _connect_signals(self) -> None:
        self._choose_button.clicked.connect(self._choose_video)
        self._play_button.clicked.connect(self._toggle_playback)
        self._timeline.sliderPressed.connect(self._slider_pressed)
        self._timeline.sliderReleased.connect(self._slider_released)
        self._purpose_edit.textChanged.connect(self._purpose_changed)
        self._mode_combo.currentIndexChanged.connect(self._mode_changed)
        self._model_edit.textChanged.connect(self._inputs_changed)
        self._range_start.valueChanged.connect(self._range_changed)
        self._range_end.valueChanged.connect(self._range_changed)
        self._motion_scan_button.clicked.connect(self._start_motion_scan)
        self._motion_apply_button.clicked.connect(self._apply_motion_proposal)
        self._motion_list.itemClicked.connect(self._preview_motion_proposal)
        self._motion_list.itemChanged.connect(self._motion_check_changed)
        self._selected_motion_list.itemClicked.connect(self._preview_motion_proposal)
        self._selected_motion_list.currentItemChanged.connect(
            lambda *_args: self._update_motion_selection_buttons()
        )
        self._selected_motion_uncheck_button.clicked.connect(
            self._uncheck_selected_motion_proposal
        )
        self._selected_motion_clear_button.clicked.connect(self._clear_motion_checks)
        self._make_plan_button.clicked.connect(self._make_plan)
        self._approve_button.clicked.connect(self._approve_plan)
        self._transfer_button.clicked.connect(self._approve_transfer)
        self._run_button.clicked.connect(self._start_analysis)
        self._cancel_button.clicked.connect(self._cancel_analysis)
        self._result_list.itemActivated.connect(self._seek_to_segment)
        self._result_list.itemClicked.connect(self._seek_to_segment)
        self._zoom_out_button.clicked.connect(
            lambda: self._change_ui_zoom(-self.UI_ZOOM_STEP)
        )
        self._zoom_reset_button.clicked.connect(lambda: self._set_ui_zoom(100))
        self._zoom_in_button.clicked.connect(
            lambda: self._change_ui_zoom(self.UI_ZOOM_STEP)
        )
        self._player.durationChanged.connect(self._on_duration_changed)
        self._player.positionChanged.connect(self._on_position_changed)
        self._player.playbackStateChanged.connect(self._on_playback_state_changed)
        self._player.errorOccurred.connect(self._on_media_error)

    def _install_zoom_shortcuts(self) -> None:
        self._zoom_in_action = QAction("화면 확대", self)
        self._zoom_in_action.setShortcut(QKeySequence.StandardKey.ZoomIn)
        self._zoom_in_action.triggered.connect(
            lambda: self._change_ui_zoom(self.UI_ZOOM_STEP)
        )
        self.addAction(self._zoom_in_action)

        self._zoom_out_action = QAction("화면 축소", self)
        self._zoom_out_action.setShortcut(QKeySequence.StandardKey.ZoomOut)
        self._zoom_out_action.triggered.connect(
            lambda: self._change_ui_zoom(-self.UI_ZOOM_STEP)
        )
        self.addAction(self._zoom_out_action)

        self._zoom_reset_action = QAction("화면 크기 초기화", self)
        self._zoom_reset_action.setShortcut(QKeySequence("Ctrl+0"))
        self._zoom_reset_action.triggered.connect(lambda: self._set_ui_zoom(100))
        self.addAction(self._zoom_reset_action)

    def _change_ui_zoom(self, delta: int) -> None:
        self._set_ui_zoom(self._ui_zoom_percent + delta)

    def _set_ui_zoom(self, percent: int) -> None:
        bounded = max(self.UI_ZOOM_MIN, min(self.UI_ZOOM_MAX, int(percent)))
        if bounded == self._ui_zoom_percent:
            return
        self._ui_zoom_percent = bounded
        self._apply_ui_zoom()
        self.statusBar().showMessage(
            f"화면 크기 {self._ui_zoom_percent}% · ⌘+/⌘-/⌘0으로 조정",
            4000,
        )

    def _apply_ui_zoom(self) -> None:
        scale = self._ui_zoom_percent / 100.0
        font = QFont(self._base_font)
        font.setPointSizeF(max(8.0, self._base_font.pointSizeF() * scale))
        self.setFont(font)

        def scaled(value: int) -> int:
            return max(1, round(value * scale))

        self._root_layout.setContentsMargins(
            scaled(18), scaled(16), scaled(18), scaled(18)
        )
        self._root_layout.setSpacing(scaled(12))
        self._analysis_layout.setSpacing(scaled(8))
        self._purpose_edit.setMinimumHeight(scaled(72))
        self._purpose_edit.setMaximumHeight(scaled(120))
        self._motion_list.setMinimumHeight(scaled(128))
        self._motion_list.setMaximumHeight(scaled(210))
        self._selected_motion_list.setMinimumHeight(scaled(84))
        self._selected_motion_list.setMaximumHeight(scaled(140))
        self._plan_view.setMinimumHeight(scaled(145))
        self._plan_view.setMaximumHeight(scaled(260))
        self._plan_group.setMinimumHeight(scaled(635))
        self._purpose_group.setMinimumHeight(scaled(205))
        self._approval_group.setMinimumHeight(scaled(150))
        self._progress_group.setMinimumHeight(scaled(145))
        self._result_group.setMinimumHeight(scaled(230))

        self._zoom_reset_button.setText(f"{self._ui_zoom_percent}%")
        self._zoom_out_button.setEnabled(self._ui_zoom_percent > self.UI_ZOOM_MIN)
        self._zoom_in_button.setEnabled(self._ui_zoom_percent < self.UI_ZOOM_MAX)
        self.setStyleSheet(
            f"""
            QMainWindow {{ background: #f3f6fa; }}
            QLabel#title {{ font-size: {scaled(22)}px; font-weight: 700; color: #18324a; }}
            QLabel#placeholder {{ color: #61758a; border: 1px dashed #9aacbc;
                                  padding: {scaled(7)}px; }}
            QLabel#modeBanner {{ border-radius: {scaled(6)}px; padding: {scaled(9)}px;
                                 font-weight: 700; }}
            QGroupBox {{ font-weight: 700; border: 1px solid #ccd7e2;
                         border-radius: {scaled(7)}px; margin-top: {scaled(9)}px;
                         padding-top: {scaled(10)}px; background: white; }}
            QGroupBox::title {{ subcontrol-origin: margin; left: {scaled(10)}px;
                                padding: 0 {scaled(4)}px; }}
            QPushButton {{ padding: {scaled(7)}px {scaled(12)}px; }}
            QListWidget, QPlainTextEdit, QLineEdit {{ background: white; }}
            """
        )

    def _restore_settings(self) -> None:
        index = self._mode_combo.findData(self._settings.mode)
        self._mode_combo.blockSignals(True)
        self._model_edit.blockSignals(True)
        self._mode_combo.setCurrentIndex(max(0, index))
        if self._settings.mode is AnalysisMode.LOCAL:
            self._model_edit.setText(self._preferred_local_model())
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
        self._motion_scan_button.setEnabled(False)
        self._motion_apply_button.setEnabled(False)
        self._selected_motion_uncheck_button.setEnabled(False)
        self._selected_motion_clear_button.setEnabled(False)
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
        if self._background_work_running():
            self._show_error("작업 중에는 영상을 바꿀 수 없습니다. 먼저 작업을 취소해 주세요.")
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
        self._clear_motion_proposals()
        self._sampling_interval_override = None
        self._sampling_timestamps_override = ()
        self._sampling_strategy_override = None
        self._applied_motion_proposals = ()
        self._motion_scan_profile = MotionScanProfile.GENERAL_PEAK
        self._motion_scan_purpose = None
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
        self._motion_scan_button.setEnabled(True)
        self._make_plan_button.setEnabled(True)
        self._inputs_changed()
        self.statusBar().showMessage("영상 메타데이터 확인 완료", 5000)

    def _on_metadata_error(self, message: str) -> None:
        self._metadata = None
        self._metadata_label.setText("메타데이터 확인 실패")
        self._motion_scan_button.setEnabled(False)
        self._make_plan_button.setEnabled(False)
        self._show_error(message)

    def _on_metadata_finished(self) -> None:
        self._choose_button.setEnabled(not self._background_work_running())

    def _start_motion_scan(self) -> None:
        if self._video_path is None or self._metadata is None:
            self._show_error("먼저 영상 메타데이터 확인을 완료해 주세요.")
            return
        if self._background_work_running():
            self._show_error("이미 백그라운드 작업이 실행 중입니다.")
            return
        start = self._range_start.value()
        end = self._range_end.value() or self._metadata.duration_seconds
        if not 0 <= start < end <= self._metadata.duration_seconds + 1e-6:
            self._show_error("움직임 탐색 범위가 영상 길이를 벗어났습니다.")
            return

        scan_id = f"motion-{uuid4().hex[:12]}"
        purpose = self._purpose_edit.toPlainText().strip()
        temporal = temporal_motion_requirement(purpose)
        fine_detail = fine_detail_requirement(purpose)
        profile = (
            MotionScanProfile.REPEATED_SUBTLE
            if temporal.required and not fine_detail.required
            else MotionScanProfile.GENERAL_PEAK
        )
        worker = MotionScanWorker(
            scan_id,
            self._video_path,
            self._metadata,
            start,
            end,
            profile=profile,
        )
        worker.result_ready.connect(self._on_motion_scan_result)
        worker.error.connect(self._on_motion_scan_error)
        worker.canceled.connect(self._on_motion_scan_canceled)
        worker.finished.connect(self._on_motion_scan_finished)
        self._motion_worker = worker
        self._active_motion_scan_id = scan_id
        self._motion_scan_profile = profile
        self._motion_scan_purpose = purpose
        self._clear_motion_proposals()
        self._progress_bar.setRange(0, 0)
        self._analysis_started_at = time.monotonic()
        if fine_detail.required:
            self._motion_help.setText(
                "작은 손·손가락·버튼 접촉 목적이므로 변화 정점 중심의 짧은 구간을 제안합니다. "
                "적용 후 각 원본 시각을 전체 화면 보기와 중앙 60% 확대 보기로 함께 분석합니다. "
                "움직임 점수는 접촉 확률이나 행동 판정이 아닙니다."
            )
            self._last_progress_message = "작은 접촉 변화 탐색 중 · 진행률 알 수 없음"
        elif profile is MotionScanProfile.REPEATED_SUBTLE:
            self._motion_help.setText(
                "입력 목적이 미세·반복 시간 변화로 분류되어 작은 영역의 변화를 시간 동안 비교합니다. "
                "가장 큰 움직임은 감점해 덜 두드러진 반복 변화도 제안합니다. 행동 판정이나 AI 결과가 아닙니다."
            )
            self._last_progress_message = "국소 반복 변화 탐색 중 · 진행률 알 수 없음"
        else:
            self._motion_help.setText(
                "큰 픽셀 변화가 있는 짧은 구간을 먼저 제안합니다. 상위 3개가 기본 선택되며 "
                "각 항목의 체크박스로 최대 3개를 조정할 수 있습니다. 행동 판정이나 AI 결과가 아닙니다."
            )
            self._last_progress_message = "로컬 움직임 변화 탐색 중 · 진행률 알 수 없음"
        self._refresh_elapsed_status()
        self._elapsed_timer.start()
        self._set_analysis_running(True)
        worker.start()

    def _on_motion_scan_result(self, result: MotionScanResult) -> None:
        if result.scan_id != self._active_motion_scan_id or result.video_id != self._video_id:
            return
        self._active_motion_scan_id = None
        self._motion_scan_profile = result.profile
        self._motion_list.blockSignals(True)
        self._motion_list.clear()
        for rank, proposal in enumerate(result.proposals, start=1):
            start = format_milliseconds(seconds_to_milliseconds(proposal.start_sec))
            end = format_milliseconds(seconds_to_milliseconds(proposal.end_sec))
            peak = format_milliseconds(seconds_to_milliseconds(proposal.peak_timestamp_sec))
            score_label = (
                "반복 변화 점수"
                if result.profile is MotionScanProfile.REPEATED_SUBTLE
                else "변화 점수"
            )
            item = QListWidgetItem(
                f"{rank}. {start}–{end} · 최대 변화 {peak} · {score_label} {proposal.motion_score:.2f}"
            )
            item.setData(Qt.ItemDataRole.UserRole, proposal)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if rank <= 3 else Qt.CheckState.Unchecked
            )
            self._motion_list.addItem(item)
        if result.proposals:
            self._motion_list.setCurrentRow(0)
            profile_text = (
                "국소 반복 변화 후보"
                if result.profile is MotionScanProfile.REPEATED_SUBTLE
                else "큰 움직임 후보"
            )
            message = (
                f"{profile_text} {len(result.proposals)}개 · {result.sampled_frame_count}개 저해상도 표본 · "
                f"상위 {min(3, len(result.proposals))}개 체크됨 · 점수는 확률이나 행동 판정이 아님"
            )
        else:
            self._motion_list.addItem("현재 범위에서 비교 가능한 움직임 후보를 만들지 못했습니다.")
            message = "움직임 후보 없음 · 행동 부재를 의미하지 않음"
        self._motion_list.blockSignals(False)
        self._refresh_motion_selection_display()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(100)
        self._progress_label.setText(message)

    def _on_motion_scan_error(self, message: str) -> None:
        if self._active_motion_scan_id is None:
            return
        self._active_motion_scan_id = None
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_label.setText("움직임 탐색 오류")
        self._show_error(message)

    def _on_motion_scan_canceled(self) -> None:
        self._active_motion_scan_id = None
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_label.setText("움직임 탐색 취소됨")

    def _on_motion_scan_finished(self) -> None:
        self._elapsed_timer.stop()
        self._analysis_started_at = None
        self._motion_worker = None
        self._set_analysis_running(False)
        self._model_edit.setEnabled(self._current_mode() is not AnalysisMode.SAMPLE)
        self._cancel_button.setEnabled(False)
        self._update_post_run_buttons()

    def _preview_motion_proposal(self, item: QListWidgetItem) -> None:
        proposal = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(proposal, MotionProposal):
            return
        position_ms = seconds_to_milliseconds(proposal.peak_timestamp_sec)
        self._player.setPosition(min(position_ms, self._duration_ms))
        self.statusBar().showMessage(
            f"움직임 최대 변화 시각 {format_milliseconds(position_ms)}로 이동했습니다.",
            4000,
        )

    def _motion_check_changed(self, changed_item: QListWidgetItem) -> None:
        proposal = changed_item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(proposal, MotionProposal):
            return
        if (
            changed_item.checkState() == Qt.CheckState.Checked
            and len(self._checked_motion_proposals()) > 3
        ):
            self._motion_list.blockSignals(True)
            changed_item.setCheckState(Qt.CheckState.Unchecked)
            self._motion_list.blockSignals(False)
            self.statusBar().showMessage(
                "한 계획에는 움직임 후보를 최대 3개까지 선택할 수 있습니다.",
                5000,
            )
        self._refresh_motion_selection_display()

    def _checked_motion_proposals(self) -> tuple[MotionProposal, ...]:
        checked: list[MotionProposal] = []
        for row in range(self._motion_list.count()):
            item = self._motion_list.item(row)
            proposal = item.data(Qt.ItemDataRole.UserRole)
            if (
                isinstance(proposal, MotionProposal)
                and item.checkState() == Qt.CheckState.Checked
            ):
                checked.append(proposal)
        return tuple(checked)

    def _refresh_motion_selection_display(self) -> None:
        proposals = self._checked_motion_proposals()
        self._motion_selection_label.setText(
            f"선택한 분석 구간: {len(proposals)}/3"
            + (" · '분석 계획에 적용' 버튼을 눌러 등록" if proposals else "")
        )
        self._selected_motion_list.clear()
        for proposal in proposals:
            start = format_milliseconds(seconds_to_milliseconds(proposal.start_sec))
            end = format_milliseconds(seconds_to_milliseconds(proposal.end_sec))
            peak = format_milliseconds(seconds_to_milliseconds(proposal.peak_timestamp_sec))
            item = QListWidgetItem(f"{start}–{end} · 최대 변화 {peak}")
            item.setData(Qt.ItemDataRole.UserRole, proposal)
            self._selected_motion_list.addItem(item)
        running = self._background_work_running()
        self._motion_apply_button.setEnabled(bool(proposals) and not running)
        self._selected_motion_clear_button.setEnabled(bool(proposals) and not running)
        self._update_motion_selection_buttons()

    def _update_motion_selection_buttons(self) -> None:
        self._selected_motion_uncheck_button.setEnabled(
            self._selected_motion_list.currentItem() is not None
            and not self._background_work_running()
        )

    def _uncheck_selected_motion_proposal(self) -> None:
        selected = self._selected_motion_list.currentItem()
        if selected is None:
            return
        proposal = selected.data(Qt.ItemDataRole.UserRole)
        if not isinstance(proposal, MotionProposal):
            return
        for row in range(self._motion_list.count()):
            item = self._motion_list.item(row)
            candidate = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(candidate, MotionProposal) and candidate.proposal_id == proposal.proposal_id:
                item.setCheckState(Qt.CheckState.Unchecked)
                return

    def _clear_motion_checks(self) -> None:
        self._motion_list.blockSignals(True)
        for row in range(self._motion_list.count()):
            item = self._motion_list.item(row)
            if isinstance(item.data(Qt.ItemDataRole.UserRole), MotionProposal):
                item.setCheckState(Qt.CheckState.Unchecked)
        self._motion_list.blockSignals(False)
        self._refresh_motion_selection_display()

    def _apply_motion_proposal(self) -> None:
        proposals = self._checked_motion_proposals()
        if not proposals:
            self._show_error("먼저 움직임 후보를 선택해 주세요.")
            return
        if len(proposals) > 3:
            self._show_error("한 계획에는 움직임 후보를 최대 3개까지 선택할 수 있습니다.")
            return
        if any(proposal.video_id != self._video_id for proposal in proposals):
            self._show_error("현재 영상과 다른 움직임 후보입니다. 다시 탐색해 주세요.")
            return
        proposals = tuple(sorted(proposals, key=lambda item: (item.start_sec, item.end_sec)))
        try:
            if self._motion_scan_profile is MotionScanProfile.REPEATED_SUBTLE:
                timestamps = build_temporal_sample_timestamps(proposals)
                sampling_strategy = SamplingStrategy.TEMPORAL_SEQUENCE
            else:
                timestamps = build_motion_sample_timestamps(proposals)
                sampling_strategy = SamplingStrategy.MOTION_PEAK
        except ValueError as exc:
            self._show_error(str(exc))
            return

        self._range_start.blockSignals(True)
        self._range_end.blockSignals(True)
        self._range_start.setValue(min(proposal.start_sec for proposal in proposals))
        self._range_end.setValue(max(proposal.end_sec for proposal in proposals))
        self._range_start.blockSignals(False)
        self._range_end.blockSignals(False)
        gaps = tuple(later - earlier for earlier, later in zip(timestamps, timestamps[1:]))
        self._sampling_interval_override = min(gaps, default=0.25)
        self._sampling_timestamps_override = timestamps
        self._sampling_strategy_override = sampling_strategy
        self._applied_motion_proposals = proposals
        self._inputs_changed()
        position_ms = seconds_to_milliseconds(proposals[0].start_sec)
        self._player.setPosition(min(position_ms, self._duration_ms))
        self.statusBar().showMessage(
            f"선택한 움직임 후보 {len(proposals)}개와 총 {len(timestamps)}개 원본 시각을 "
            f"{sampling_strategy.value} 전략으로 적용했습니다. "
            "새 계획을 만들고 다시 승인해 주세요.",
            7000,
        )

    def _clear_motion_proposals(self) -> None:
        self._motion_list.blockSignals(True)
        self._motion_list.clear()
        self._motion_list.blockSignals(False)
        self._refresh_motion_selection_display()

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
            self._model_edit.setText(self._preferred_local_model())
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

    def _preferred_local_model(self) -> str:
        configured = (os.environ.get("VIDEO_AGENT_LOCAL_MODEL") or "").strip()
        if configured:
            return configured
        saved = (self._settings.local_model_id or "").strip()
        confirmed = self._runtime_inventory.vision_ollama_models
        if saved in confirmed and "instruct" in saved.lower():
            return saved
        instruct_models = tuple(item for item in confirmed if "instruct" in item.lower())
        if instruct_models:
            return min(instruct_models, key=str.lower)
        if saved in confirmed:
            return saved
        if confirmed:
            return min(
                confirmed,
                key=str.lower,
            )
        return saved

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
            sampling_interval_sec=self._sampling_interval_override,
            sample_timestamps_sec=self._sampling_timestamps_override,
            provider_id=provider_id,
            provider_endpoint=endpoint,
            sampling_strategy=self._sampling_strategy_override,
        )

    def _purpose_changed(self) -> None:
        if self._background_work_running():
            return
        current_purpose = self._purpose_edit.toPlainText().strip()
        if self._motion_scan_purpose is not None and current_purpose != self._motion_scan_purpose:
            self._sampling_interval_override = None
            self._sampling_timestamps_override = ()
            self._sampling_strategy_override = None
            self._applied_motion_proposals = ()
            self._motion_scan_purpose = None
            self._motion_scan_profile = MotionScanProfile.GENERAL_PEAK
            self._clear_motion_proposals()
        self._inputs_changed()

    def _range_changed(self) -> None:
        self._sampling_interval_override = None
        self._sampling_timestamps_override = ()
        self._sampling_strategy_override = None
        self._applied_motion_proposals = ()
        self._motion_scan_purpose = None
        self._motion_scan_profile = MotionScanProfile.GENERAL_PEAK
        self._clear_motion_proposals()
        self._inputs_changed()

    def _inputs_changed(self) -> None:
        if self._background_work_running():
            return
        try:
            self._session.update_context(self._metadata, self._current_intent())
        except PlanningError as exc:
            self.statusBar().showMessage(str(exc), 5000)
            return
        self._invalidate_plan_display()
        self._update_mode_banner()
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
        mode = self._current_mode()
        model_id = self._model_edit.text().strip() or None
        if mode is AnalysisMode.LOCAL and not self._runtime_inventory.confirms_vision(model_id):
            if model_id:
                self._show_error(
                    f"설치된 Ollama가 '{model_id}'의 영상 입력 기능을 확인하지 못했습니다. "
                    "상단에 표시된 비전 모델을 사용해 주세요."
                )
            else:
                self._show_error("설치되어 영상 입력 기능이 확인된 로컬 모델이 없습니다.")
            return
        self._session.update_context(self._metadata, self._current_intent())
        questions = self._session.questions()
        if questions:
            question = questions[0]
            if self._clarification_id == question.question_id and self._clarification_edit.text().strip():
                self._session.answer(question.question_id, self._clarification_edit.text())
                self._clarification_edit.clear()
                questions = self._session.questions()
            if questions:
                question = questions[0]
                self._clarification_id = question.question_id
                self._question_label.setText(f"추가 질문: {question.prompt}\n이유: {question.reason}")
                self._clarification_edit.setFocus()
                self.statusBar().showMessage("추가 질문에 답한 뒤 계획 버튼을 다시 눌러 주세요.")
                return
        try:
            limits = (
                AnalysisLimits(
                    max_runtime_seconds=300.0,
                    max_provider_requests=3,
                    max_frames_per_request=4,
                )
                if mode is AnalysisMode.LOCAL
                else AnalysisLimits()
            )
            plan = self._session.prepare_plan(limits)
        except PlanningError as exc:
            self._show_error(str(exc))
            return

        if plan.mode is AnalysisMode.SAMPLE:
            sampling = "프레임 미추출(길이 비율 기반 샘플 결과)"
        elif self._sampling_timestamps_override:
            fine_detail = fine_detail_requirement(plan.purpose)
            proposal_ranges = "\n".join(
                f"  {index}. {item.start_sec:.3f}~{item.end_sec:.3f}초 "
                f"(최대 변화 {item.peak_timestamp_sec:.3f}초)"
                for index, item in enumerate(self._applied_motion_proposals, start=1)
            )
            strategy_text = (
                "각 구간 전체에 분산한 시간순 비교"
                if plan.sampling_strategy is SamplingStrategy.TEMPORAL_SEQUENCE
                else "최대 변화 시각 중심의 촘촘한 비교"
            )
            sampling = (
                f"계획에 등록된 움직임 후보 {len(self._applied_motion_proposals)}개에서 "
                f"{strategy_text} 원본 타임스탬프 {len(plan.sample_timestamps_sec)}개, "
                f"최소 약 {plan.sampling_interval_sec:.3f}초 간격\n"
                f"등록된 분석 구간:\n{proposal_ranges}\n"
                "입력 구간 수와 최종 결과 후보 수는 서로 다를 수 있음"
            )
            if (
                plan.mode is AnalysisMode.LOCAL
                and plan.sampling_strategy is SamplingStrategy.TEMPORAL_SEQUENCE
                and not fine_detail.required
            ):
                sampling += (
                    "\n원본 프레임은 최대 4개씩 최대 2×2 시간순 비교 보드로 묶어 "
                    "로컬 모델에 입력"
                )
        elif plan.sampling_strategy is SamplingStrategy.TEMPORAL_SEQUENCE:
            fine_detail = fine_detail_requirement(plan.purpose)
            sampling = (
                f"미세·반복 변화 시간순 비교 표본 {len(plan.sample_timestamps_sec)}개, "
                f"약 {plan.sampling_interval_sec:.3f}초 간격"
            )
            if plan.mode is AnalysisMode.LOCAL and not fine_detail.required:
                sampling += (
                    "\n원본 프레임은 최대 4개씩 최대 2×2 시간순 비교 보드로 묶어 "
                    "로컬 모델에 입력"
                )
        else:
            sampling = (
                f"원본 타임스탬프 균등 샘플 {len(plan.sample_timestamps_sec)}개, "
                f"약 {plan.sampling_interval_sec:.3f}초 간격"
            )
        if (
            plan.mode is AnalysisMode.LOCAL
            and fine_detail_requirement(plan.purpose).required
        ):
            sampling += (
                "\n작은 접촉 정밀 입력: 최대 1920px 원본에서 각 시각별 "
                "전체 화면 보기 + 중앙 60% 확대 보기(1280×1080)를 모델에 전달"
            )
        lines = [
            f"모드: {plan.mode.value} · 모델: {plan.model_id or '없음(샘플)'}",
            f"분석 대상: {plan.video_id} · 원본 {plan.range_start_sec:.3f}~{plan.range_end_sec:.3f}초",
            f"분석 전략: {plan.sampling_strategy.value}",
            f"샘플링: {sampling}",
            f"한도: {plan.limits.max_runtime_seconds:g}초 · 프레임 {plan.limits.max_frames}개 · 자동 재분석 {plan.limits.max_reanalysis_attempts}회",
            f"모델 요청: 최대 {plan.limits.max_provider_requests}회 · 요청당 최대 {plan.limits.max_frames_per_request}프레임 · 자동 재시도 없음",
            "범위 확대가 필요하면 자동 실행하지 않고 제안 상태로 남깁니다.",
        ]
        if plan.observation_guidance:
            lines.append("관찰 기준: " + " / ".join(plan.observation_guidance))
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
        if self._background_work_running():
            self._show_error("이미 백그라운드 작업이 실행 중입니다. 중복 실행할 수 없습니다.")
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
                vision_capability_confirmed=(
                    self._runtime_inventory.confirms_vision(self._session.plan.model_id)
                    if self._session.plan.mode is AnalysisMode.LOCAL
                    else None
                ),
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
        self._set_running_banner(request.mode, request.model_id)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._analysis_started_at = time.monotonic()
        self._last_progress_message = "분석 시작"
        self._progress_label.setText("분석 시작 · 경과 00:00")
        self._elapsed_timer.start()
        self._set_analysis_running(True)
        worker.start()

    def _set_analysis_running(self, running: bool) -> None:
        self._choose_button.setEnabled(not running and not self._metadata_probe_running())
        self._motion_scan_button.setEnabled(not running and self._metadata is not None)
        self._motion_list.setEnabled(not running)
        self._selected_motion_list.setEnabled(not running)
        checked_motion = self._checked_motion_proposals()
        self._motion_apply_button.setEnabled(not running and bool(checked_motion))
        self._selected_motion_clear_button.setEnabled(not running and bool(checked_motion))
        self._update_motion_selection_buttons()
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
            self._last_progress_message = message
            self._refresh_elapsed_status()
        else:
            self._progress_bar.setRange(0, 100)
            self._progress_bar.setValue(progress)
            self._last_progress_message = f"{message} · {progress}%"
            self._refresh_elapsed_status()

    def _refresh_elapsed_status(self) -> None:
        if self._analysis_started_at is None:
            return
        elapsed = max(0, round(time.monotonic() - self._analysis_started_at))
        minutes, seconds = divmod(elapsed, 60)
        self._progress_label.setText(
            f"{self._last_progress_message} · 경과 {minutes:02d}:{seconds:02d}"
        )

    def _cancel_analysis(self) -> None:
        if self._motion_worker is not None and self._motion_worker.isRunning():
            self._active_motion_scan_id = None
            self._motion_worker.request_cancel()
            self._cancel_button.setEnabled(False)
            self._last_progress_message = "움직임 탐색 취소 중 · FFmpeg 정리 중"
            self._refresh_elapsed_status()
            self.statusBar().showMessage("움직임 탐색을 취소하고 있습니다.", 5000)
            return
        if self._worker is None or not self._worker.isRunning():
            self.statusBar().showMessage("취소할 작업이 없습니다.", 4000)
            return
        self._session.cancel_active()
        self._worker.request_cancel()
        self._cancel_button.setEnabled(False)
        self._last_progress_message = "취소됨 · 서버 응답/시간 제한까지 백그라운드 정리 중"
        self._refresh_elapsed_status()
        self.statusBar().showMessage(
            "취소 결과는 저장하지 않으며 정리가 끝날 때까지 새 분석을 시작할 수 없습니다.",
            7000,
        )

    def _on_analysis_result(self, job: AnalysisJob) -> None:
        accepted = self._session.accept_result(
            job.job_id,
            failed=job.status is JobStatus.FAILED,
            canceled=job.status is JobStatus.CANCELED,
            partial=job.status is JobStatus.PARTIAL,
        )
        if not accepted:
            return
        if job.status is not JobStatus.CANCELED:
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
            elapsed = f" · {job.elapsed_seconds:.1f}초" if job.elapsed_seconds is not None else ""
            outcome = f"후보 {len(job.segments)}개" if job.segments else "후보 없음"
            self._progress_label.setText(f"완료 · {outcome} · {origin_text}{elapsed}")
            self._set_result_banner(job.origin, job.model_id)
            self._display_segments(job)
        elif job.status is JobStatus.PARTIAL:
            elapsed = f" · {job.elapsed_seconds:.1f}초" if job.elapsed_seconds is not None else ""
            self._progress_label.setText(
                f"일부 분석만 완료 · {job.analyzed_frame_count}/{job.requested_frame_count}프레임{elapsed}"
            )
            self._set_result_banner(job.origin, job.model_id)
            self._display_segments(job)
            self.statusBar().showMessage(job.error or "일부 배치만 완료되었습니다.", 10000)
        elif job.status is JobStatus.CANCELED:
            self._progress_label.setText("사용자 요청으로 취소됨")
            self._set_terminal_banner("분석 취소됨", job.model_id, failed=False)
        else:
            self._progress_bar.setValue(0)
            self._progress_label.setText("오류로 종료됨")
            self._set_terminal_banner("모델 응답 실패", job.model_id, failed=True)
            self._result_list.addItem(
                f"분석 실패 — {job.model_id or '모델 없음'} · "
                f"{job.error or '알 수 없는 분석 오류'}"
            )
            self._show_error(job.error or "알 수 없는 분석 오류가 발생했습니다.")

    def _on_worker_finished(self) -> None:
        self._elapsed_timer.stop()
        self._analysis_started_at = None
        self._set_analysis_running(False)
        self._model_edit.setEnabled(self._current_mode() is not AnalysisMode.SAMPLE)
        self._cancel_button.setEnabled(False)
        self._worker = None
        self._update_post_run_buttons()

    def _display_segments(self, job: AnalysisJob) -> None:
        if not job.segments:
            self._result_list.addItem(
                "분석된 프레임에서 목적에 맞는 후보를 찾지 못했습니다. "
                "이는 해당 행동이 없다는 확정이 아닙니다."
            )
            return
        for segment in job.segments:
            item = QListWidgetItem(self._segment_text(segment, job.purpose))
            item.setData(Qt.ItemDataRole.UserRole, segment)
            self._result_list.addItem(item)

    def _update_post_run_buttons(self) -> None:
        self._motion_scan_button.setEnabled(
            self._metadata is not None and not self._background_work_running()
        )
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

    def _set_running_banner(self, mode: AnalysisMode, model_id: str | None) -> None:
        if mode is AnalysisMode.SAMPLE:
            text = "샘플 분석 실행 중 — 실제 AI 분석 아님"
        else:
            text = f"분석 실행 중 — {model_id or '모델 미지정'}"
        self._mode_banner.setText(text)
        self._mode_banner.setStyleSheet(
            "background:#fff6d8;color:#6e5200;border:1px solid #d6b858;"
        )

    def _set_terminal_banner(
        self,
        label: str,
        model_id: str | None,
        *,
        failed: bool,
    ) -> None:
        self._mode_banner.setText(f"{label} — {model_id or '모델 없음'}")
        self._mode_banner.setStyleSheet(
            "background:#fdeaea;color:#7a2020;border:1px solid #d89a9a;"
            if failed
            else "background:#eef1f4;color:#46515a;border:1px solid #adb7c0;"
        )

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
                confirmed = self._runtime_inventory.vision_ollama_models
                status = (
                    "확인된 비전 모델: " + ", ".join(confirmed)
                    if confirmed
                    else "설치 모델은 있으나 vision 기능 확인 안 됨"
                )
            self._mode_banner.setText(f"로컬 VLM 모드 — {status} · 자동 외부 전환 없음")
            self._mode_banner.setStyleSheet("background:#e8f1fb;color:#174d78;border:1px solid #8bb6d9;")
        else:
            self._mode_banner.setText("외부 API 모드 — 분석 승인과 전송 승인이 각각 필요")
            self._mode_banner.setStyleSheet("background:#fdeaea;color:#7a2020;border:1px solid #d89a9a;")

    @staticmethod
    def _segment_text(segment: Segment, purpose: str | None = None) -> str:
        start = format_milliseconds(seconds_to_milliseconds(segment.start_sec))
        end = format_milliseconds(seconds_to_milliseconds(segment.end_sec))
        evidence = " / ".join(segment.evidence)
        frames = f"\n원본 프레임: {', '.join(segment.source_frame_ids)}" if segment.source_frame_ids else ""
        observed = (
            "\n관찰 프레임 원본 시각: "
            + ", ".join(f"{timestamp:.3f}초" for timestamp in segment.source_timestamps_sec)
            if segment.source_timestamps_sec
            else ""
        )
        duration_evidence = ""
        requirement = duration_requirement(purpose or "")
        if requirement.required:
            timestamps = tuple(sorted(set(segment.source_timestamps_sec)))
            span = observation_span_seconds(timestamps)
            if not timestamps:
                duration_evidence = (
                    "\n지속 시간 근거: 관찰 프레임 시각이 없어 '오래 머무름'을 "
                    "판단할 수 없음"
                )
            elif span is None:
                duration_evidence = (
                    "\n지속 시간 근거: 단일 관찰 시각만 있어 '오래 머무름'을 "
                    "판단할 수 없음"
                )
            else:
                duration_evidence = (
                    f"\n지속 시간 표본 근거: {timestamps[0]:.3f}~{timestamps[-1]:.3f}초의 "
                    f"{len(timestamps)}개 관찰 시각 · 첫·마지막 표본 사이 {span:.3f}초"
                )
                if requirement.threshold_seconds is not None:
                    if span + 1e-6 >= requirement.threshold_seconds:
                        duration_evidence += (
                            f" · 시간 간격은 기준 {requirement.threshold_seconds:g}초 이상이지만 "
                            "중간의 연속성은 원본 확인 필요"
                        )
                    else:
                        duration_evidence += (
                            f" · 기준 {requirement.threshold_seconds:g}초 충족 근거 부족"
                        )
        temporal_evidence = ""
        temporal = temporal_motion_requirement(purpose or "")
        if temporal.required:
            timestamps = tuple(sorted(set(segment.source_timestamps_sec)))
            span = observation_span_seconds(timestamps)
            if not timestamps:
                temporal_evidence = "\n시간 변화 근거: 관찰 프레임 시각이 없어 변화 순서를 확인할 수 없음"
            elif span is None:
                temporal_evidence = (
                    "\n시간 변화 근거: 단일 관찰 시각만 있어 반복·방향 변화를 확인할 수 없음"
                )
            else:
                temporal_evidence = (
                    f"\n시간 변화 표본 근거: {timestamps[0]:.3f}~{timestamps[-1]:.3f}초의 "
                    f"{len(timestamps)}개 관찰 시각 · 표본 사이 {span:.3f}초 · "
                    "중간 동작과 실제 반복 횟수는 원본 확인 필요"
                )
        return (
            f"확인용 재생 범위 {start}–{end}  {segment.summary}\n"
            f"근거: {evidence}{frames}{observed}{duration_evidence}{temporal_evidence}\n"
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

    def _background_work_running(self) -> bool:
        return any(
            worker is not None and worker.isRunning()
            for worker in (self._worker, self._motion_worker)
        )

    def _show_error(self, message: str) -> None:
        self.statusBar().showMessage(message, 8000)
        QMessageBox.critical(self, "오류", message)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt override name
        workers = [
            item
            for item in (self._worker, self._motion_worker, self._metadata_worker)
            if item and item.isRunning()
        ]
        for worker in workers:
            worker.request_cancel()
        if any(not worker.wait(2000) for worker in workers):
            event.ignore()
            QMessageBox.warning(self, "종료 대기", "작업을 정리하고 있습니다. 잠시 후 다시 종료해 주세요.")
            return
        self._player.stop()
        event.accept()
