from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QPointF, Qt, QTimer, QUrl
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QColor,
    QFont,
    QGuiApplication,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
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
    QSizePolicy,
    QSlider,
    QSplitter,
    QStackedWidget,
    QStyle,
    QTabBar,
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
    AnalysisClue,
    AnalysisJob,
    AnalysisLimits,
    AnalysisMode,
    CaseRecord,
    CaseVideoLink,
    JobStatus,
    ReviewJudgement,
    ReviewSession,
    MotionProposal,
    MotionScanProfile,
    MotionScanResult,
    ResultOrigin,
    SamplingStrategy,
    Segment,
)
from video_agent.models.config import RuntimeInventory
from video_agent.storage.json_store import (
    AppSettings,
    CaseStore,
    ResultStore,
    ReviewStore,
    SettingsStore,
    StoredResult,
)
from video_agent.storage.paths import AppPaths, CacheManager
from video_agent.ui.case_panel import CaseContextPanel
from video_agent.ui import theme
from video_agent.ui.video_list import CaseVideoList, VideoEntry
from video_agent.knowledge.common_rules import CommonRules, load_common_rules
from video_agent.review.summary import (
    CameraSummaryInput,
    build_case_summary,
    build_summary,
)
from video_agent.ui.case_overview import CaseCandidateOverview, OverviewRow
from video_agent.ui.review_panel import JUDGEMENT_LABELS, CandidateReviewPanel
from video_agent.ui.summary_dialog import ResultSummaryDialog
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


class _StepIndicator(QWidget):
    """Dots on a thin line above the step names; reached steps are drawn in blue."""

    DOT_Y = 11

    def __init__(self, buttons: list[QPushButton]) -> None:
        super().__init__()
        self.setObjectName("stepBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._buttons = buttons
        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 2)
        layout.setSpacing(0)
        for button in buttons:
            layout.addWidget(button, 1)
            button.toggled.connect(self.update)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().paintEvent(event)
        if not self._buttons:
            return
        current = next((i for i, b in enumerate(self._buttons) if b.isChecked()), 0)
        centers = [b.geometry().center().x() for b in self._buttons]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        y = self.DOT_Y
        painter.setPen(QPen(QColor(theme.BORDER), 2))
        painter.drawLine(centers[0], y, centers[-1], y)
        painter.setPen(QPen(QColor(theme.PRIMARY), 2))
        painter.drawLine(centers[0], y, centers[current], y)
        painter.setPen(Qt.PenStyle.NoPen)
        for index, x in enumerate(centers):
            painter.setBrush(QColor(theme.PRIMARY if index <= current else theme.BORDER))
            painter.drawEllipse(QPointF(x, y), 4.5, 4.5)
        painter.end()


class MainWindow(QMainWindow):
    UI_ZOOM_MIN = 75
    UI_ZOOM_MAX = 150
    UI_ZOOM_STEP = 10
    RESULT_CONTEXT_PREROLL_SECONDS = 1.0
    APP_TITLE = "CCTV 영상수사 지원 Agent"
    ORGANIZATION = "대한민국 경찰청"
    STEP_TITLES = ("사건·영상", "분석 계획·실행", "후보 검토")
    OPENAI_PROVIDER_ID = "openai"
    OPENAI_CHAT_COMPLETIONS_ENDPOINT = "https://api.openai.com/v1/chat/completions"
    OPENAI_API_KEY_ENV = "OPENAI_API_KEY"

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(self.APP_TITLE)
        self.resize(1220, 850)

        self._base_font = self.font()
        self._ui_zoom_percent = 100

        self._video_path: Path | None = None
        self._video_id: str | None = None
        # Videos of the current case in list order: video ID -> path chosen in this run, or
        # None when a saved case links a video whose file has not been chosen again.
        self._case_videos: dict[str, Path | None] = {}
        # Unsaved camera details of videos that are not open right now.
        self._link_drafts: dict[str, CaseVideoLink] = {}
        self._video_durations: dict[str, float] = {}
        # Per-video analysis status shown in the CCTV list: (text, has stored result).
        self._video_statuses: dict[str, tuple[str, bool]] = {}
        # Failed or canceled runs of this app session, newer than any stored result.
        self._video_run_notes: dict[str, str] = {}
        # Video whose results the review step shows. Usually the open video; a camera tab
        # can show a linked video's saved results even before its file is chosen again.
        self._review_video_id: str | None = None
        # Newest stored result per case video and its saved review records, for the
        # case-wide candidate list (read on refresh, not on every keystroke).
        self._newest_jobs: dict[str, AnalysisJob] = {}
        self._overview_sessions: dict[str, ReviewSession | None] = {}
        self._metadata: VideoMetadata | None = None
        self._duration_ms = 0
        self._seeking = False
        self._playback_stop_ms: int | None = None
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
        self._review_store = ReviewStore(self._paths)
        self._saved_results: dict[str, StoredResult] = {}
        self._common_rules: CommonRules | None = None
        self._common_rules_error: str | None = None
        try:
            self._common_rules = load_common_rules()
        except (OSError, ValueError, TypeError) as exc:
            self._common_rules_error = str(exc)
        self._case_store = CaseStore(self._paths)
        self._cache_manager = CacheManager(self._paths.cache_dir)
        self._case_record = CaseRecord(
            case_id=f"case-{uuid4().hex[:12]}",
            revision=1,
            title="새 사건",
        )
        self._case_saved = False
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
        self._case_panel.set_record(self._case_record)
        self._case_panel.set_video_open(False)
        self._refresh_case_choices()
        self._set_idle_state()
        self._refresh_saved_results()
        self._show_step(0)

    def _build_ui(self) -> None:
        root = QWidget(self)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self._build_header())

        self._step_button_group = QButtonGroup(self)
        self._step_buttons: list[QPushButton] = []
        for index, title in enumerate(self.STEP_TITLES):
            button = QPushButton(title)
            button.setObjectName("stepButton")
            button.setCheckable(True)
            button.setAccessibleName(f"{index + 1}단계 {title}로 이동")
            self._step_button_group.addButton(button, index)
            self._step_buttons.append(button)
        self._step_indicator = _StepIndicator(self._step_buttons)
        root_layout.addWidget(self._step_indicator)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        self._mode_banner = QLabel()
        self._mode_banner.setObjectName("modeBanner")
        self._mode_banner.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        content_layout.addWidget(self._mode_banner)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_video_panel())
        splitter.addWidget(self._build_step_area(self._build_analysis_panel()))
        splitter.setSizes([660, 540])
        content_layout.addWidget(splitter, 1)
        root_layout.addWidget(content, 1)

        self._root_layout = content_layout

        self.setCentralWidget(root)
        self._build_step_navigation()
        self.statusBar().showMessage("영상 파일을 선택해 주세요.")

    def _build_header(self) -> QWidget:
        """Organization mark and app name on the left, zoom controls on the right."""
        header = QWidget()
        header.setObjectName("appHeader")
        header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        row = QHBoxLayout(header)
        row.setContentsMargins(24, 8, 24, 8)
        row.setSpacing(10)
        self._emblem_label = QLabel()
        self._emblem_label.setObjectName("emblem")
        self._emblem_pixmap = QPixmap(str(theme.ASSETS_DIR / "police_emblem.png"))
        self._emblem_label.setAccessibleName(f"{self.ORGANIZATION} 상징마크")
        organization = QLabel(self.ORGANIZATION)
        organization.setObjectName("organization")
        divider = QFrame()
        divider.setObjectName("headerDivider")
        divider.setFrameShape(QFrame.Shape.VLine)
        divider.setFixedWidth(1)
        title = QLabel(self.APP_TITLE)
        title.setObjectName("title")
        row.addWidget(self._emblem_label)
        row.addWidget(organization)
        row.addWidget(divider)
        row.addWidget(title)
        row.addStretch()
        zoom_label = QLabel("화면 크기")
        zoom_label.setObjectName("headerHint")
        self._zoom_out_button = QPushButton("−")
        self._zoom_out_button.setToolTip("화면 축소 (⌘-)")
        self._zoom_reset_button = QPushButton("100%")
        self._zoom_reset_button.setToolTip("기본 화면 크기 (⌘0)")
        self._zoom_in_button = QPushButton("+")
        self._zoom_in_button.setToolTip("화면 확대 (⌘+)")
        row.addWidget(zoom_label)
        row.addWidget(self._zoom_out_button)
        row.addWidget(self._zoom_reset_button)
        row.addWidget(self._zoom_in_button)
        return header

    def _build_step_navigation(self) -> None:
        """Previous/next buttons on the right of the bottom status bar."""
        navigation = QWidget()
        row = QHBoxLayout(navigation)
        row.setContentsMargins(0, 0, 12, 0)
        row.setSpacing(8)
        self._prev_step_button = QPushButton()
        self._next_step_button = QPushButton()
        theme.mark_primary(self._next_step_button)
        for button in (self._prev_step_button, self._next_step_button):
            # Keep each button at its text width when the other one is hidden.
            button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        row.addWidget(self._prev_step_button)
        row.addWidget(self._next_step_button)
        # QStatusBar paints messages 6px from its left edge; its own text is transparent in
        # the theme and this label repeats the message with the page margin instead.
        self._status_label = QLabel()
        self._status_label.setObjectName("statusMessage")
        self._status_label.setAccessibleName("상태 메시지")
        status_bar = self.statusBar()
        status_bar.messageChanged.connect(self._status_label.setText)
        status_bar.addPermanentWidget(self._status_label, 1)
        status_bar.addPermanentWidget(navigation)
        navigation.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._step_navigation = navigation

    def _build_step_area(self, pages: tuple[QWidget, ...]) -> QWidget:
        """Right side: one scrollable page per step."""
        area = QWidget()
        layout = QVBoxLayout(area)
        layout.setContentsMargins(6, 0, 0, 0)

        self._step_stack = QStackedWidget()
        self._step_scrolls: list[QScrollArea] = []
        for page in pages:
            if page.findChild(QScrollArea) is self._plan_scroll:
                self._step_stack.addWidget(page)
                self._step_scrolls.append(self._plan_scroll)
                continue
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            scroll.setWidget(page)
            self._step_stack.addWidget(scroll)
            self._step_scrolls.append(scroll)
        layout.addWidget(self._step_stack, 1)
        return area

    def _build_video_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 6, 0)

        self._video_list = CaseVideoList()
        self._choose_button = self._video_list.add_button
        self._metadata_label = self._video_list.metadata_label
        layout.addWidget(self._video_list)

        self._video_widget = QVideoWidget()
        self._video_widget.setMinimumHeight(350)
        self._video_widget.setStyleSheet(f"background: {theme.VIDEO_BACKGROUND};")
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

    def _build_analysis_panel(self) -> tuple[QWidget, QWidget, QWidget]:
        """Build the step pages: case/video, planning and execution, candidate review."""
        case_page = QWidget()
        case_layout = QVBoxLayout(case_page)
        case_layout.setContentsMargins(0, 0, 0, 0)
        self._case_panel = CaseContextPanel()
        case_layout.addWidget(self._case_panel)
        case_layout.addStretch(1)

        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)

        purpose_group = QGroupBox("분석 목적과 범위")
        purpose_layout = QFormLayout(purpose_group)
        self._mode_combo = QComboBox()
        self._mode_combo.addItem("샘플 — 실제 AI 분석 아님", AnalysisMode.SAMPLE)
        self._mode_combo.addItem("로컬 VLM — 명시적 연결", AnalysisMode.LOCAL)
        self._mode_combo.addItem("외부 API VLM — 별도 전송 승인", AnalysisMode.EXTERNAL)
        self._model_edit = QLineEdit()
        self._model_edit.setPlaceholderText("실제 모드에서 영상 입력 지원이 확인된 모델 ID")
        self._external_api_key_edit = QLineEdit()
        self._external_api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._external_api_key_edit.setClearButtonEnabled(True)
        self._external_api_key_edit.setPlaceholderText("OpenAI API 키 · 현재 실행 중에만 사용")
        self._external_api_key_edit.setToolTip(
            "키는 설정·결과·로그에 저장하지 않습니다. OPENAI_API_KEY 환경 변수도 사용할 수 있습니다."
        )
        self._external_vision_confirm = QCheckBox(
            "입력한 외부 모델이 이미지 입력을 지원함을 확인했습니다"
        )
        self._external_vision_confirm.setToolTip(
            "텍스트 전용 모델을 영상 분석 모델처럼 사용하지 않기 위한 명시적 확인입니다."
        )
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
        purpose_layout.addRow("OpenAI 키", self._external_api_key_edit)
        purpose_layout.addRow("영상 입력", self._external_vision_confirm)
        purpose_layout.addRow("목적", self._purpose_edit)
        purpose_layout.addRow("원본 범위", range_row)
        self._purpose_form_layout = purpose_layout
        layout.addWidget(purpose_group)

        self._plan_group = QGroupBox("추가 질문과 분석 계획")
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

        approval_group = QGroupBox("승인과 실행")
        approval_layout = QGridLayout(approval_group)
        self._approve_button = QPushButton("분석 계획 승인")
        self._transfer_button = QPushButton("표시된 외부 전송 내용·범위 별도 승인")
        self._run_button = QPushButton("승인된 계획 실행")
        theme.mark_primary(self._approve_button)
        theme.mark_primary(self._run_button)
        approval_layout.addWidget(self._approve_button, 0, 0)
        approval_layout.addWidget(self._run_button, 0, 1)
        approval_layout.addWidget(self._transfer_button, 1, 0, 1, 2)

        progress_group = QGroupBox("실행 상태")
        progress_layout = QGridLayout(progress_group)
        self._progress_bar = QProgressBar()
        self._progress_label = QLabel("대기 중")
        self._progress_label.setWordWrap(True)
        self._cancel_button = QPushButton("작업 취소")
        progress_layout.addWidget(self._progress_bar, 0, 0)
        progress_layout.addWidget(self._cancel_button, 0, 1)
        progress_layout.addWidget(self._progress_label, 1, 0, 1, 2)
        layout.addStretch(1)

        # Approval and run stay visible below the scrolling plan content.
        self._plan_scroll = QScrollArea()
        self._plan_scroll.setWidgetResizable(True)
        self._plan_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._plan_scroll.setWidget(panel)
        plan_step = QWidget()
        plan_step_layout = QVBoxLayout(plan_step)
        plan_step_layout.setContentsMargins(0, 0, 0, 0)
        plan_step_layout.addWidget(self._plan_scroll, 1)
        plan_step_layout.addWidget(approval_group)
        plan_step_layout.addWidget(progress_group)

        result_group = QGroupBox("중요 구간 후보")
        result_layout = QVBoxLayout(result_group)
        help_text = (
            "후보를 선택하면 맥락 확인을 위해 원본 영상이 확인용 범위 시작의 최대 1초 전으로 이동하고, "
            "표시된 근거 범위를 지나 계속 재생합니다. "
            "입력한 분석 구간 수와 결과 후보 수는 같지 않을 수 있으며, 결과는 0개·1개·여러 개이거나 중복 정리로 합쳐질 수 있습니다."
        )
        # Kept as a tooltip (like the demo's info marks) so the review step fits one screen.
        help_label = QLabel("ⓘ 재생 안내")
        help_label.setObjectName("infoMark")
        help_label.setCursor(Qt.CursorShape.WhatsThisCursor)
        help_label.setToolTip(help_text)
        help_label.setAccessibleName("후보 재생과 결과 수 안내")
        help_label.setAccessibleDescription(help_text)
        saved_row = QHBoxLayout()
        self._saved_results_combo = QComboBox()
        self._saved_results_combo.setAccessibleName("이 영상의 저장된 분석 결과")
        # Long result names are cut in the closed box (full text in the list) so the row
        # never forces a horizontal scroll bar.
        self._saved_results_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self._saved_results_combo.setMinimumContentsLength(8)
        self._open_result_button = QPushButton("열기")
        self._open_result_button.setToolTip("선택한 저장 결과와 검토 기록을 불러옵니다")
        saved_row.addWidget(QLabel("저장된 결과"))
        saved_row.addWidget(self._saved_results_combo, 1)
        saved_row.addWidget(self._open_result_button)
        saved_row.addWidget(help_label)
        self._camera_tabs = QTabBar()
        self._camera_tabs.setObjectName("cameraTabs")
        self._camera_tabs.setExpanding(False)
        self._camera_tabs.setDrawBase(False)
        self._camera_tabs.setElideMode(Qt.TextElideMode.ElideMiddle)
        self._camera_tabs.setAccessibleName("검토할 카메라")
        self._case_overview = CaseCandidateOverview()
        self._camera_row = QWidget()
        camera_row = QHBoxLayout(self._camera_row)
        camera_row.setContentsMargins(0, 0, 0, 0)
        camera_row.addWidget(self._camera_tabs, 1)
        camera_row.addWidget(self._case_overview.toggle_button)
        self._camera_row.setVisible(False)
        result_layout.addWidget(self._camera_row)
        result_layout.addWidget(self._case_overview)
        result_layout.addLayout(saved_row)
        self._review_panel = CandidateReviewPanel(self._segment_text)
        self._result_list = self._review_panel.candidate_list
        self._review_empty_label = QLabel(
            "아직 표시할 결과가 없습니다. 분석 계획·실행 단계에서 분석을 실행하거나, "
            "위의 저장된 결과를 열어 주세요."
        )
        self._review_empty_label.setWordWrap(True)
        self._review_empty_label.setObjectName("placeholder")
        self._result_help_label = help_label
        result_layout.addWidget(self._review_empty_label)
        result_layout.addWidget(self._review_panel, 1)
        result_layout.addStretch(1)
        self._result_layout = result_layout

        review_page = QWidget()
        review_layout = QVBoxLayout(review_page)
        review_layout.setContentsMargins(0, 0, 0, 0)
        review_layout.addWidget(result_group, 1)
        self._step_layouts = (case_layout, layout, review_layout)
        self._purpose_group = purpose_group
        self._approval_group = approval_group
        self._progress_group = progress_group
        self._result_group = result_group
        return case_page, plan_step, review_page

    def _connect_signals(self) -> None:
        self._case_panel.changed.connect(self._case_inputs_changed)
        self._case_panel.save_requested.connect(self._save_case)
        self._case_panel.load_requested.connect(self._load_case)
        self._case_panel.new_requested.connect(self._new_case)
        self._choose_button.clicked.connect(self._choose_video)
        self._video_list.video_activated.connect(self._activate_listed_video)
        self._video_list.relink_requested.connect(self._relink_video)
        self._video_list.remove_requested.connect(self._remove_video)
        self._camera_tabs.currentChanged.connect(self._camera_tab_changed)
        # A click on the already selected tab still opens its result when none is shown.
        self._camera_tabs.tabBarClicked.connect(self._camera_tab_changed)
        self._case_overview.candidate_activated.connect(self._activate_overview_candidate)
        self._review_panel.records_changed.connect(self._review_records_changed)
        self._play_button.clicked.connect(self._toggle_playback)
        self._timeline.sliderPressed.connect(self._slider_pressed)
        self._timeline.sliderReleased.connect(self._slider_released)
        self._purpose_edit.textChanged.connect(self._purpose_changed)
        self._mode_combo.currentIndexChanged.connect(self._mode_changed)
        self._model_edit.textChanged.connect(self._model_changed)
        self._external_api_key_edit.textChanged.connect(self._external_credential_changed)
        self._external_vision_confirm.checkStateChanged.connect(
            self._external_credential_changed
        )
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
        self._review_panel.playback_range_requested.connect(self._play_review_range)
        self._open_result_button.clicked.connect(self._open_saved_result)
        self._step_button_group.idClicked.connect(self._show_step)
        self._prev_step_button.clicked.connect(
            lambda: self._show_step(self._step_stack.currentIndex() - 1)
        )
        self._next_step_button.clicked.connect(
            lambda: self._show_step(self._step_stack.currentIndex() + 1)
        )
        self._review_panel.summary_requested.connect(self._open_summary)
        application = QGuiApplication.instance()
        if application is not None:
            application.applicationStateChanged.connect(self._on_application_state_changed)
            self._on_application_state_changed(application.applicationState())
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
            scaled(18), scaled(12), scaled(18), scaled(12)
        )
        self._root_layout.setSpacing(scaled(10))
        if not self._emblem_pixmap.isNull():
            self._emblem_label.setPixmap(
                self._emblem_pixmap.scaledToHeight(
                    scaled(34), Qt.TransformationMode.SmoothTransformation
                )
            )
        for step_layout in self._step_layouts:
            step_layout.setSpacing(scaled(8))
        self._purpose_edit.setMinimumHeight(scaled(60))
        self._purpose_edit.setMaximumHeight(scaled(96))
        self._motion_list.setMinimumHeight(scaled(128))
        self._motion_list.setMaximumHeight(scaled(170))
        self._selected_motion_list.setMinimumHeight(scaled(64))
        self._selected_motion_list.setMaximumHeight(scaled(110))
        self._plan_view.setMinimumHeight(scaled(145))
        self._plan_view.setMaximumHeight(scaled(220))
        self._case_panel.clue_list.setMinimumHeight(scaled(100))
        self._plan_group.setMinimumHeight(scaled(480))
        purpose_height = 265 if self._current_mode() is AnalysisMode.EXTERNAL else 185
        self._purpose_group.setMinimumHeight(scaled(purpose_height))
        self._review_panel.apply_scale(scaled)
        self._video_list.apply_scale(scaled)
        self._case_overview.apply_scale(scaled)

        self._zoom_reset_button.setText(f"{self._ui_zoom_percent}%")
        self._zoom_out_button.setEnabled(self._ui_zoom_percent > self.UI_ZOOM_MIN)
        self._zoom_in_button.setEnabled(self._ui_zoom_percent < self.UI_ZOOM_MAX)
        self.setStyleSheet(theme.build_stylesheet(scaled, font.pointSizeF()))

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
        self._update_external_controls()
        self._update_mode_banner()
        if self._settings_error:
            self.statusBar().showMessage(f"설정 파일을 읽지 못해 기본값을 사용합니다: {self._settings_error}")

    def _current_case_clues(self, *, for_analysis: bool = False) -> tuple[AnalysisClue, ...]:
        clues = self._case_panel.clues()
        if for_analysis:
            return tuple(item for item in clues if item.use_for_analysis)
        return clues

    def _case_from_panel(self, revision: int) -> CaseRecord:
        links = [self._link_for(video_id) for video_id in self._case_videos]
        return CaseRecord(
            case_id=self._case_record.case_id,
            revision=revision,
            title=self._case_panel.title_edit.text().strip() or "새 사건",
            case_type=self._case_panel.type_edit.text().strip(),
            occurrence_time_text=self._case_panel.occurrence_edit.text().strip(),
            occurrence_time_certainty=self._case_panel.occurrence_certainty.currentData(),
            management_notes=self._case_panel.notes_edit.toPlainText().strip(),
            clues=self._current_case_clues(),
            video_links=tuple(sorted(links, key=lambda item: item.video_id)),
        )

    @staticmethod
    def _case_content(record: CaseRecord) -> dict[str, object]:
        data = record.to_dict()
        data.pop("revision", None)
        return data

    def _case_inputs_changed(self) -> None:
        self._refresh_video_list()
        if self._background_work_running():
            return
        self._inputs_changed()

    def _save_case(self) -> None:
        if self._background_work_running():
            self._show_error("작업 중에는 사건 정보를 저장할 수 없습니다.")
            return
        try:
            candidate = self._case_from_panel(self._case_record.revision)
            if self._case_saved and self._case_content(candidate) != self._case_content(
                self._case_record
            ):
                candidate = self._case_from_panel(self._case_record.revision + 1)
            path = self._case_store.save(candidate)
        except (OSError, ValueError) as exc:
            self._show_error(f"사건 정보 저장 실패: {exc}")
            return
        self._case_record = candidate
        self._case_saved = True
        self._refresh_case_choices()
        self._inputs_changed()
        self.statusBar().showMessage(
            f"사건 정보 v{candidate.revision} 저장 완료: {path.name}",
            6000,
        )

    def _load_case(self) -> None:
        if self._background_work_running():
            self._show_error("작업 중에는 사건을 바꿀 수 없습니다.")
            return
        case_id = self._case_panel.selected_case_id()
        if case_id is None:
            self.statusBar().showMessage("저장된 사건이 없습니다.", 4000)
            return
        try:
            record = self._case_store.load(case_id)
        except (OSError, ValueError) as exc:
            self._show_error(f"사건 정보 불러오기 실패: {exc}")
            return
        self._case_record = record
        self._case_saved = True
        self._reset_case_videos(record)
        self._refresh_video_statuses()
        self._case_panel.set_record(record, self._video_id)
        self._case_panel.set_video_open(self._video_id is not None)
        self._refresh_video_list()
        self._inputs_changed()
        self.statusBar().showMessage(
            f"사건 '{record.title}' v{record.revision}을 불러왔습니다. 원본 영상 경로는 다시 선택해야 할 수 있습니다.",
            7000,
        )

    def _new_case(self) -> None:
        if self._background_work_running():
            self._show_error("작업 중에는 새 사건을 만들 수 없습니다.")
            return
        self._case_record = CaseRecord(
            case_id=f"case-{uuid4().hex[:12]}",
            revision=1,
            title="새 사건",
        )
        self._case_saved = False
        self._reset_case_videos(self._case_record)
        self._refresh_video_statuses()
        self._case_panel.set_record(self._case_record, self._video_id)
        self._case_panel.set_video_open(self._video_id is not None)
        self._refresh_video_list()
        self._inputs_changed()
        self.statusBar().showMessage("새 사건 입력을 시작합니다. 저장 전에도 계획에 단서를 사용할 수 있습니다.", 6000)

    def _reset_case_videos(self, record: CaseRecord) -> None:
        """List the case's linked videos, reusing files chosen in this run, plus the open one."""
        known = dict(self._case_videos)
        self._case_videos = {
            item.video_id: known.get(item.video_id) for item in record.video_links
        }
        if self._video_id is not None and self._video_path is not None:
            self._case_videos.setdefault(self._video_id, self._video_path)
        self._link_drafts = {}

    def _refresh_case_choices(self) -> None:
        try:
            records = self._case_store.list_records()
        except (OSError, ValueError) as exc:
            self.statusBar().showMessage(f"저장된 사건 목록을 읽지 못했습니다: {exc}", 7000)
            records = ()
        self._case_panel.set_saved_cases(records, self._case_record.case_id)

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
        """Add one or more videos to the case list and open the first new one."""
        filenames, _ = QFileDialog.getOpenFileNames(
            self,
            "사건 CCTV 영상 추가",
            "",
            "영상 파일 (*.mp4 *.mov *.m4v *.avi *.mkv *.webm);;모든 파일 (*)",
        )
        if not filenames:
            self.statusBar().showMessage("영상 선택을 취소했습니다.", 4000)
            return
        if self._background_work_running():
            self._show_error("작업 중에는 영상을 추가할 수 없습니다. 먼저 작업을 취소해 주세요.")
            return
        added: list[tuple[Path, str]] = []
        errors: list[str] = []
        for filename in filenames:
            path = Path(filename)
            try:
                validate_video_path(path)
                video_id = build_video_id(path)
            except (OSError, ValueError) as exc:
                errors.append(f"{path.name}: {exc}")
                continue
            if self._case_videos.get(video_id) is None:
                self._case_videos[video_id] = path
                added.append((path, video_id))
        self._refresh_video_statuses()
        self._refresh_video_list()
        if errors:
            self._show_error("추가하지 못한 영상이 있습니다.\n" + "\n".join(errors))
        if not added:
            if not errors:
                self.statusBar().showMessage("이미 목록에 있는 영상입니다.", 4000)
            return
        if len(added) > 1:
            self.statusBar().showMessage(f"영상 {len(added)}개를 사건 목록에 추가했습니다.", 5000)
        path, video_id = added[0]
        if video_id != self._video_id:
            self._switch_to_video(path, video_id)

    def _activate_listed_video(self, video_id: str) -> None:
        path = self._case_videos.get(video_id)
        if path is None or video_id == self._video_id:
            return
        if self._background_work_running():
            self._show_error("작업 중에는 영상을 바꿀 수 없습니다. 먼저 작업을 취소해 주세요.")
            self._refresh_video_list()
            return
        self._switch_to_video(path, video_id)

    def _switch_to_video(self, path: Path, video_id: str) -> None:
        if not self._confirm_discard_unsaved_reviews("다른 영상을 열까요?"):
            self._refresh_video_list()
            return
        self._open_video(path, video_id)

    def _relink_video(self, video_id: str) -> None:
        if self._background_work_running():
            self._show_error("작업 중에는 파일을 연결할 수 없습니다.")
            return
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "같은 영상 파일 다시 연결",
            "",
            "영상 파일 (*.mp4 *.mov *.m4v *.avi *.mkv *.webm);;모든 파일 (*)",
        )
        if not filename:
            return
        path = Path(filename)
        try:
            validate_video_path(path)
            chosen_id = build_video_id(path)
        except (OSError, ValueError) as exc:
            self._show_error(str(exc))
            return
        if chosen_id != video_id:
            self._show_error(
                "고른 파일은 사건에 연결된 영상과 다릅니다. 같은 위치·크기·수정 시각의 원본 파일을 골라 주세요."
            )
            return
        self._case_videos[video_id] = path
        self._refresh_video_list()
        self._switch_to_video(path, video_id)

    def _remove_video(self, video_id: str) -> None:
        if self._background_work_running():
            self._show_error("작업 중에는 영상을 뺄 수 없습니다.")
            return
        if video_id == self._video_id:
            self.statusBar().showMessage("지금 연 영상은 다른 영상을 고른 뒤 뺄 수 있습니다.", 5000)
            return
        self._case_videos.pop(video_id, None)
        self._link_drafts.pop(video_id, None)
        self._refresh_video_list()
        self._inputs_changed()
        self.statusBar().showMessage("사건 목록에서 영상을 뺐습니다. 사건을 저장하면 연결이 끊깁니다.", 6000)

    def _link_for(self, video_id: str, *, from_panel: bool = True) -> CaseVideoLink:
        """Camera details of a video: unsaved edits first, then the saved case."""
        if from_panel and video_id == self._video_id:
            current = self._case_panel.video_link(video_id)
            if current is not None:
                return current
        if video_id in self._link_drafts:
            return self._link_drafts[video_id]
        saved = next(
            (item for item in self._case_record.video_links if item.video_id == video_id), None
        )
        return saved or CaseVideoLink(video_id=video_id)

    _ORIGIN_SHORT = {
        ResultOrigin.SAMPLE: "샘플",
        ResultOrigin.TEST_DOUBLE: "테스트 응답",
        ResultOrigin.REAL_MODEL: "실제 모델",
    }

    def _refresh_video_statuses(self) -> None:
        """Read each listed video's newest stored result once; the list reuses it."""
        statuses: dict[str, tuple[str, bool]] = {}
        for video_id in self._case_videos:
            try:
                stored, _unreadable = self._result_store.list_for_video(video_id)
            except OSError:
                stored = ()
            if stored:
                job = stored[0].job
                self._newest_jobs[video_id] = job
                label = "분석 완료" if job.status is JobStatus.COMPLETED else "일부 완료"
                origin = self._ORIGIN_SHORT.get(job.origin, "출처 미확인")
                found = f"후보 {len(job.segments)}" if job.segments else "후보 없음"
                more = f" · 결과 {len(stored)}개" if len(stored) > 1 else ""
                statuses[video_id] = (f"{label}({origin}) · {found}{more}", True)
            else:
                self._newest_jobs.pop(video_id, None)
                statuses[video_id] = ("분석 전", False)
        self._video_statuses = statuses
        self._overview_sessions = {}
        for job in self._newest_jobs.values():
            try:
                self._overview_sessions[job.job_id] = self._review_store.load(job.job_id)
            except (OSError, ValueError, KeyError, TypeError):
                self._overview_sessions[job.job_id] = None

    def _record_run_outcome(self, job: AnalysisJob) -> None:
        if job.status is JobStatus.FAILED:
            self._video_run_notes[job.video_id] = "최근 실행 실패"
        elif job.status is JobStatus.CANCELED:
            self._video_run_notes[job.video_id] = "최근 실행 취소"
        else:
            self._video_run_notes.pop(job.video_id, None)
        self._refresh_video_statuses()
        self._refresh_video_list()

    def _video_status_text(self, video_id: str) -> tuple[str, bool]:
        text, analyzed = self._video_statuses.get(video_id, ("분석 전", False))
        # The analysis worker is set before it starts and cleared when it finishes.
        if video_id == self._video_id and self._worker is not None:
            return "분석 중", analyzed
        note = self._video_run_notes.get(video_id)
        if note is not None:
            text = f"{note} · 이전 {text}" if analyzed else note
        return text, analyzed

    def _refresh_video_list(self) -> None:
        entries = []
        for video_id, path in self._case_videos.items():
            link = self._link_for(video_id)
            status, analyzed = self._video_status_text(video_id)
            entries.append(
                VideoEntry(
                    video_id=video_id,
                    file_name=path.name if path is not None else None,
                    camera_location=link.camera_location,
                    duration_seconds=self._video_durations.get(video_id),
                    clock_certainty=link.clock_certainty,
                    analysis_status=status,
                    analyzed=analyzed,
                )
            )
        self._video_list.set_entries(tuple(entries), self._video_id)
        self._refresh_camera_tabs()
        self._refresh_case_overview()

    def _review_target(self) -> str | None:
        return self._review_video_id or self._video_id

    def _camera_name(self, video_id: str) -> str:
        location = self._link_for(video_id).camera_location
        if location:
            return location
        path = self._case_videos.get(video_id)
        return path.stem if path is not None else f"영상 {video_id[:6]}"

    def _refresh_camera_tabs(self) -> None:
        """One tab per case video; shown only when the case has two or more videos."""
        tabs = self._camera_tabs
        tabs.blockSignals(True)
        try:
            while tabs.count():
                tabs.removeTab(0)
            target = self._review_target()
            for video_id in self._case_videos:
                job = self._newest_jobs.get(video_id)
                found = f"({len(job.segments)})" if job is not None else "(결과 없음)"
                index = tabs.addTab(f"{self._camera_name(video_id)} {found}")
                tabs.setTabData(index, video_id)
                linked = self._case_videos[video_id] is not None
                tabs.setTabToolTip(
                    index,
                    "파일 연결됨" if linked else "파일 다시 연결 필요 · 저장된 결과와 기록만 볼 수 있음",
                )
                if video_id == target:
                    tabs.setCurrentIndex(index)
        finally:
            tabs.blockSignals(False)
        several = len(self._case_videos) > 1
        self._camera_row.setVisible(several)
        if not several and self._case_overview.toggle_button.isChecked():
            self._case_overview.toggle_button.setChecked(False)

    def _refresh_case_overview(self) -> None:
        rows: list[OverviewRow] = []
        current = self._review_panel.job
        for video_id in self._case_videos:
            job = self._newest_jobs.get(video_id)
            if job is None:
                continue
            if current is not None and current.job_id == job.job_id:
                session = self._review_panel.review_session
            else:
                session = self._overview_sessions.get(job.job_id)
            camera = self._camera_name(video_id)
            order = sorted(
                range(len(job.segments)),
                key=lambda i: (job.segments[i].start_sec, job.segments[i].end_sec, i),
            )
            for index in order:
                segment = job.segments[index]
                review = session.review_for(index) if session is not None else None
                judgement = review.judgement if review is not None else ReviewJudgement.UNREVIEWED
                rows.append(
                    OverviewRow(
                        video_id=video_id,
                        job_id=job.job_id,
                        segment_index=index,
                        camera=camera,
                        start_sec=segment.start_sec,
                        end_sec=segment.end_sec,
                        summary=segment.summary,
                        judgement=JUDGEMENT_LABELS[judgement],
                        bookmarked=bool(review is not None and review.bookmarked),
                    )
                )
        self._case_overview.set_rows(
            tuple(rows), tuple(JUDGEMENT_LABELS[item] for item in ReviewJudgement)
        )

    def _review_records_changed(self) -> None:
        job = self._review_panel.job
        if job is not None:
            self._overview_sessions[job.job_id] = self._review_panel.review_session
        self._refresh_case_overview()

    def _camera_tab_changed(self, index: int) -> None:
        video_id = self._camera_tabs.tabData(index)
        if isinstance(video_id, str):
            self._show_camera(video_id)

    def _show_camera(self, video_id: str) -> bool:
        """Show one case video's newest result in the review step; opens its file if linked."""
        if video_id == self._review_target() and self._review_panel.job is not None:
            return True
        if self._background_work_running():
            self._show_error("작업 중에는 카메라를 바꿀 수 없습니다. 먼저 작업을 취소해 주세요.")
            self._refresh_camera_tabs()
            return False
        if not self._confirm_discard_unsaved_reviews("다른 카메라를 볼까요?"):
            self._refresh_camera_tabs()
            return False
        path = self._case_videos.get(video_id)
        if path is not None and video_id != self._video_id:
            self._open_video(path, video_id)
        else:
            self._review_video_id = video_id
            self._review_panel.clear()
            self._refresh_review_empty_state()
            self._refresh_saved_results()
            self._refresh_camera_tabs()
        newest = next(iter(self._saved_results.values()), None)
        if newest is not None:
            self._set_result_banner(newest.job.origin, newest.job.model_id)
            self._display_segments(newest.job)
        if path is None:
            self.statusBar().showMessage(
                "이 영상은 파일이 연결되지 않아 재생할 수 없습니다. 저장된 결과와 검토 기록은 볼 수 있습니다.",
                7000,
            )
        return True

    def _activate_overview_candidate(self, video_id: str, job_id: str, index: int) -> None:
        if not self._show_camera(video_id):
            return
        job = self._review_panel.job
        if job is None or job.job_id != job_id:
            stored = self._saved_results.get(job_id)
            if stored is None:
                return
            self._display_segments(stored.job)
        self._review_panel.select_segment(index)

    def _open_video(self, path: Path, video_id: str) -> None:
        self._player.stop()
        if self._video_id is not None and self._video_id != video_id:
            self._link_drafts[self._video_id] = self._case_panel.video_link(self._video_id)
        self._video_path = path
        self._video_id = video_id
        self._review_video_id = video_id
        self._case_videos[video_id] = path
        self._case_panel.show_link(self._link_for(video_id, from_panel=False))
        self._case_panel.set_video_open(True)
        self._metadata = None
        self._duration_ms = 0
        self._metadata_label.setText("FFprobe 메타데이터 확인 중…")
        self._timeline.setRange(0, 0)
        self._time_label.setText("00:00 / 길이 확인 중")
        self._play_button.setEnabled(False)
        self._review_panel.clear()
        self._refresh_review_empty_state()
        self._clear_motion_proposals()
        self._sampling_interval_override = None
        self._sampling_timestamps_override = ()
        self._sampling_strategy_override = None
        self._applied_motion_proposals = ()
        self._motion_scan_profile = MotionScanProfile.GENERAL_PEAK
        self._motion_scan_purpose = None
        self._invalidate_plan_display()
        self._player.setSource(QUrl.fromLocalFile(str(path)))
        self._refresh_saved_results()
        self._refresh_video_list()
        self._start_metadata_probe(path)

    def _start_metadata_probe(self, path: Path) -> None:
        self._choose_button.setEnabled(False)
        self._video_list.set_locked(True)
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
        self._video_durations[metadata.video_id] = metadata.duration_seconds
        self._refresh_video_list()
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
        self._video_list.set_locked(self._background_work_running())

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
        self._playback_stop_ms = None

    def _slider_released(self) -> None:
        self._player.setPosition(self._timeline.value())
        self._seeking = False

    def _on_duration_changed(self, duration_ms: int) -> None:
        self._duration_ms = max(0, duration_ms)
        self._timeline.setRange(0, self._duration_ms)
        self._play_button.setEnabled(self._duration_ms > 0)
        self._time_label.setText(f"00:00 / {format_milliseconds(self._duration_ms)}")

    def _on_position_changed(self, position_ms: int) -> None:
        if self._playback_stop_ms is not None and position_ms >= self._playback_stop_ms:
            self._playback_stop_ms = None
            self._player.pause()
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
        self._external_vision_confirm.setChecked(False)
        self._update_external_controls()
        self._apply_ui_zoom()
        self._update_mode_banner()
        self._inputs_changed()
        self._persist_settings()

    def _current_mode(self) -> AnalysisMode:
        return AnalysisMode(self._mode_combo.currentData())

    def _model_changed(self) -> None:
        if self._current_mode() is AnalysisMode.EXTERNAL:
            self._external_vision_confirm.setChecked(False)
        self._inputs_changed()

    def _external_credential_changed(self, *_args: object) -> None:
        self._update_mode_banner()
        self._update_post_run_buttons()

    def _update_external_controls(self) -> None:
        external = self._current_mode() is AnalysisMode.EXTERNAL
        self._purpose_form_layout.setRowVisible(self._external_api_key_edit, external)
        self._purpose_form_layout.setRowVisible(self._external_vision_confirm, external)
        editable = external and not self._background_work_running()
        self._external_api_key_edit.setEnabled(editable)
        self._external_vision_confirm.setEnabled(editable)

    def _external_api_key_value(self) -> str | None:
        entered = self._external_api_key_edit.text().strip()
        if entered:
            return entered
        configured_name = os.environ.get("VIDEO_AGENT_EXTERNAL_API_KEY_ENV")
        names = tuple(
            dict.fromkeys(
                name
                for name in (
                    configured_name,
                    self._settings.api_key_env_name,
                    self.OPENAI_API_KEY_ENV,
                    "VIDEO_AGENT_EXTERNAL_API_KEY",
                )
                if name
            )
        )
        for name in names:
            value = (os.environ.get(name) or "").strip()
            if value:
                return value
        return None

    def _external_vision_confirmed(self) -> bool:
        return self._external_vision_confirm.isChecked() or (
            os.environ.get("VIDEO_AGENT_EXTERNAL_MODEL_SUPPORTS_VISION", "").strip().lower()
            in {"1", "true", "yes", "on"}
        )

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
            provider_id = (
                os.environ.get("VIDEO_AGENT_EXTERNAL_PROVIDER")
                or self._settings.external_provider_id
                or self.OPENAI_PROVIDER_ID
            )
            endpoint = (
                os.environ.get("VIDEO_AGENT_EXTERNAL_ENDPOINT")
                or self._settings.external_endpoint
                or self.OPENAI_CHAT_COMPLETIONS_ENDPOINT
            )
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
            case_id=self._case_record.case_id,
            case_revision=self._case_record.revision,
            analysis_clues=self._current_case_clues(for_analysis=True),
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
        if mode is AnalysisMode.EXTERNAL and not self._external_vision_confirmed():
            self._show_error(
                "입력한 외부 모델이 이미지 입력을 지원하는지 확인한 뒤 "
                "'영상 입력' 항목을 체크해 주세요."
            )
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
            f"사건: {self._case_panel.title_edit.text().strip() or self._case_record.title} · "
            f"ID {plan.case_id or '미연결'} · 정보 버전 {plan.case_revision or '없음'}",
            f"모드: {plan.mode.value} · 모델: {plan.model_id or '없음(샘플)'}",
            f"분석 대상: {plan.video_id} · 원본 {plan.range_start_sec:.3f}~{plan.range_end_sec:.3f}초",
            f"분석 전략: {plan.sampling_strategy.value}",
            f"샘플링: {sampling}",
            f"한도: {plan.limits.max_runtime_seconds:g}초 · 프레임 {plan.limits.max_frames}개 · 자동 재분석 {plan.limits.max_reanalysis_attempts}회",
            f"모델 요청: 최대 {plan.limits.max_provider_requests}회 · 요청당 최대 {plan.limits.max_frames_per_request}프레임 · 자동 재시도 없음",
            "범위 확대가 필요하면 자동 실행하지 않고 제안 상태로 남깁니다.",
        ]
        if plan.analysis_clues:
            lines.append("분석에 사용하는 사전 정보(영상 관찰 사실 아님):")
            lines.extend(
                f"  - [{item.source.value}/{item.certainty.value}] {item.text}"
                for item in plan.analysis_clues
            )
        else:
            lines.append("분석에 사용하는 사전 정보: 없음")
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
                    "외부로 전송할 사전 정보: "
                    + (
                        " / ".join(item.text for item in manifest.analysis_clues)
                        if manifest.analysis_clues
                        else "없음"
                    ),
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
        key_available = self._external_api_key_value() is not None
        self._run_button.setEnabled(key_available)
        self.statusBar().showMessage(
            "외부 전송 내용을 승인했습니다. 승인된 계획을 실행할 수 있습니다."
            if key_available
            else "외부 전송 내용을 승인했습니다. 실행하려면 OpenAI API 키를 입력해 주세요.",
            7000,
        )

    def _start_analysis(self) -> None:
        if self._background_work_running():
            self._show_error("이미 백그라운드 작업이 실행 중입니다. 중복 실행할 수 없습니다.")
            return
        if self._session.plan is None or self._video_path is None:
            self._show_error("승인된 분석 계획이 없습니다.")
            return
        if not self._confirm_discard_unsaved_reviews("새 분석을 시작할까요?"):
            return
        api_key = None
        if self._session.plan.mode is AnalysisMode.EXTERNAL:
            api_key = self._external_api_key_value()
            if api_key is None:
                self._show_error(
                    "OpenAI API 키를 입력하거나 OPENAI_API_KEY 환경 변수를 설정해 주세요."
                )
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
                    else self._external_vision_confirmed()
                ),
                api_key=api_key,
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
        self._review_panel.clear()
        self._refresh_review_empty_state()
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
        self._case_panel.set_editing_enabled(not running)
        self._choose_button.setEnabled(not running and not self._metadata_probe_running())
        self._video_list.set_locked(running or self._metadata_probe_running())
        self._refresh_video_list()
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
            self._external_api_key_edit,
            self._external_vision_confirm,
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
        self._update_saved_results_controls(running)

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
        self._record_run_outcome(job)

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
            self._refresh_saved_results(select_job_id=job.job_id)
            self.statusBar().showMessage(
                f"분석이 끝났습니다. [{self.STEP_TITLES[2]} →]에서 후보를 검토하세요.", 10000
            )
        elif job.status is JobStatus.PARTIAL:
            elapsed = f" · {job.elapsed_seconds:.1f}초" if job.elapsed_seconds is not None else ""
            self._progress_label.setText(
                f"일부 분석만 완료 · {job.analyzed_frame_count}/{job.requested_frame_count}프레임{elapsed}"
            )
            self._set_result_banner(job.origin, job.model_id)
            self._display_segments(job)
            self._refresh_saved_results(select_job_id=job.job_id)
            self.statusBar().showMessage(job.error or "일부 배치만 완료되었습니다.", 10000)
        elif job.status is JobStatus.CANCELED:
            self._progress_label.setText("사용자 요청으로 취소됨")
            self._set_terminal_banner("분석 취소됨", job.model_id, failed=False)
        else:
            self._progress_bar.setValue(0)
            self._progress_label.setText("오류로 종료됨")
            self._set_terminal_banner("모델 응답 실패", job.model_id, failed=True)
            self._review_panel.show_message(
                f"분석 실패 — {job.model_id or '모델 없음'} · "
                f"{job.error or '알 수 없는 분석 오류'}"
            )
            self._refresh_review_empty_state()
            self._show_error(job.error or "알 수 없는 분석 오류가 발생했습니다.")

    def _on_worker_finished(self) -> None:
        self._elapsed_timer.stop()
        self._analysis_started_at = None
        self._set_analysis_running(False)
        self._model_edit.setEnabled(self._current_mode() is not AnalysisMode.SAMPLE)
        self._cancel_button.setEnabled(False)
        self._worker = None
        self._update_post_run_buttons()
        self._refresh_video_list()

    def _show_step(self, index: int) -> None:
        index = max(0, min(index, len(self.STEP_TITLES) - 1))
        self._step_stack.setCurrentIndex(index)
        self._step_buttons[index].setChecked(True)
        previous_exists = index > 0
        next_exists = index < len(self.STEP_TITLES) - 1
        self._prev_step_button.setEnabled(previous_exists)
        self._prev_step_button.setVisible(previous_exists)
        self._prev_step_button.setText("← 이전")
        self._prev_step_button.setToolTip(
            f"{self.STEP_TITLES[index - 1]} 단계로" if previous_exists else ""
        )
        self._next_step_button.setEnabled(next_exists)
        self._next_step_button.setVisible(next_exists)
        self._next_step_button.setText(
            f"{self.STEP_TITLES[index + 1]} →" if next_exists else "다음 →"
        )
        self._step_navigation.adjustSize()
        self._refresh_review_empty_state()

    def _refresh_review_empty_state(self) -> None:
        has_job = self._review_panel.job is not None
        has_content = has_job or self._result_list.count() > 0
        self._review_empty_label.setVisible(not has_job)
        self._review_panel.setVisible(has_content)
        # The trailing stretch keeps the empty state at the top; with a result the
        # review panel takes the spare height instead.
        self._result_layout.setStretch(self._result_layout.count() - 1, 0 if has_content else 1)

    def _display_segments(self, job: AnalysisJob) -> None:
        session = None
        load_error = None
        if job.segments:
            try:
                session = self._review_store.load(job.job_id)
                if session is not None:
                    session.validate_against(job)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                session = None
                load_error = exc
        self._review_panel.set_job(
            job,
            session=session,
            persist=self._review_store.save,
            load_error=load_error,
        )
        self._refresh_review_empty_state()
        self._refresh_case_overview()

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
        can_run = self._session.state is WorkflowState.APPROVED
        if self._current_mode() is AnalysisMode.EXTERNAL:
            can_run = (
                can_run
                and self._external_api_key_value() is not None
                and self._external_vision_confirmed()
            )
        self._run_button.setEnabled(can_run)

    def _set_result_banner(self, origin: ResultOrigin, model_id: str | None) -> None:
        if origin is ResultOrigin.SAMPLE:
            self._mode_banner.setText("샘플 결과 — 실제 AI 분석 아님")
            self._mode_banner.setStyleSheet(theme.banner_style("sample"))
        elif origin is ResultOrigin.TEST_DOUBLE:
            self._mode_banner.setText("테스트용 가짜 모델 응답 — 실제 모델 검증 아님")
            self._mode_banner.setStyleSheet(theme.banner_style("test_double"))
        else:
            self._mode_banner.setText(f"실제 모델 응답 — {model_id}")
            self._mode_banner.setStyleSheet(theme.banner_style("real_model"))

    def _set_running_banner(self, mode: AnalysisMode, model_id: str | None) -> None:
        if mode is AnalysisMode.SAMPLE:
            text = "샘플 분석 실행 중 — 실제 AI 분석 아님"
        else:
            text = f"분석 실행 중 — {model_id or '모델 미지정'}"
        self._mode_banner.setText(text)
        self._mode_banner.setStyleSheet(theme.banner_style("running"))

    def _set_terminal_banner(
        self,
        label: str,
        model_id: str | None,
        *,
        failed: bool,
    ) -> None:
        self._mode_banner.setText(f"{label} — {model_id or '모델 없음'}")
        self._mode_banner.setStyleSheet(theme.banner_style("failed" if failed else "neutral"))

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
            self._mode_banner.setStyleSheet(theme.banner_style("local"))
        else:
            key_status = (
                "API 키 준비됨(저장 안 함)"
                if self._external_api_key_value() is not None
                else "API 키 필요"
            )
            self._mode_banner.setText(
                f"OpenAI API 모드 — {key_status} · 분석 승인과 외부 전송 승인 별도"
            )
            self._mode_banner.setStyleSheet(theme.banner_style("failed"))

    @classmethod
    def _segment_text(cls, segment: Segment, purpose: str | None = None) -> str:
        start = format_milliseconds(seconds_to_milliseconds(segment.start_sec))
        end = format_milliseconds(seconds_to_milliseconds(segment.end_sec))
        context_start = format_milliseconds(
            seconds_to_milliseconds(cls._segment_playback_start_sec(segment))
        )
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
            f"클릭 시 맥락 재생 시작 {context_start} "
            f"(확인용 범위 시작 최대 {cls.RESULT_CONTEXT_PREROLL_SECONDS:g}초 전, 이후 계속 재생)\n"
            f"근거: {evidence}{frames}{observed}{duration_evidence}{temporal_evidence}\n"
            f"불확실성: {segment.uncertainty}"
        )

    @classmethod
    def _segment_playback_start_sec(cls, segment: Segment) -> float:
        return max(0.0, segment.start_sec - cls.RESULT_CONTEXT_PREROLL_SECONDS)

    def _review_video_playable(self, video_id: str | None = None) -> bool:
        """Playback only for candidates of the video file that is open in the player."""
        if video_id is None:
            job = self._review_panel.job
            video_id = job.video_id if job is not None else None
        if video_id is not None and self._video_path is not None and video_id == self._video_id:
            return True
        self.statusBar().showMessage(
            "이 후보의 영상 파일이 열려 있지 않아 재생할 수 없습니다. CCTV 목록에서 파일을 연결해 주세요.",
            7000,
        )
        return False

    def _seek_to_segment(self, item: QListWidgetItem) -> None:
        segment = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(segment, Segment) or not self._review_video_playable(segment.video_id):
            return
        position_ms = seconds_to_milliseconds(self._segment_playback_start_sec(segment))
        self._playback_stop_ms = None
        self._player.setPosition(min(position_ms, self._duration_ms))
        evidence_start = format_milliseconds(
            seconds_to_milliseconds(segment.start_sec)
        )
        evidence_end = format_milliseconds(seconds_to_milliseconds(segment.end_sec))
        self.statusBar().showMessage(
            f"맥락 확인을 위해 원본 {format_milliseconds(position_ms)}부터 재생합니다. "
            f"확인용 근거 범위는 {evidence_start}–{evidence_end}입니다.",
            6000,
        )

    def _play_review_range(self, start_sec: float, end_sec: float) -> None:
        if not self._review_video_playable():
            return
        start_ms = seconds_to_milliseconds(max(0.0, start_sec))
        end_ms = seconds_to_milliseconds(end_sec)
        if self._duration_ms > 0:
            start_ms = min(start_ms, self._duration_ms)
            end_ms = min(end_ms, self._duration_ms)
        if end_ms <= start_ms:
            return
        self._playback_stop_ms = end_ms
        self._player.setPosition(start_ms)
        self._player.play()
        self.statusBar().showMessage(
            f"원본 {format_milliseconds(start_ms)}–{format_milliseconds(end_ms)}을 재생하고 "
            "끝에서 일시정지합니다.",
            6000,
        )

    def _on_application_state_changed(self, state: Qt.ApplicationState) -> None:
        self._review_panel.set_app_active(state == Qt.ApplicationState.ApplicationActive)

    def _refresh_saved_results(self, select_job_id: str | None = None) -> None:
        self._saved_results = {}
        self._saved_results_combo.clear()
        target = self._review_target()
        if target is None:
            self._saved_results_combo.addItem("영상을 먼저 선택하세요")
            self._update_saved_results_controls()
            return
        try:
            results, unreadable = self._result_store.list_for_video(target)
        except OSError as exc:
            results, unreadable = (), 0
            self.statusBar().showMessage(f"저장된 결과 목록을 읽지 못했습니다: {exc}", 8000)
        for stored in results:
            self._saved_results[stored.job.job_id] = stored
            self._saved_results_combo.addItem(self._saved_result_text(stored), stored.job.job_id)
        if not results:
            self._saved_results_combo.addItem("이 영상의 저장된 결과 없음")
        elif select_job_id is not None:
            index = self._saved_results_combo.findData(select_job_id)
            if index >= 0:
                self._saved_results_combo.setCurrentIndex(index)
        if unreadable:
            self.statusBar().showMessage(f"읽지 못한 결과 파일 {unreadable}개는 목록에서 제외했습니다.", 8000)
        self._update_saved_results_controls()

    @staticmethod
    def _saved_result_text(stored: StoredResult) -> str:
        job = stored.job
        saved_at = datetime.fromtimestamp(stored.saved_at).strftime("%m-%d %H:%M")
        if job.mode is AnalysisMode.SAMPLE:
            mode = "샘플"
        elif job.mode is AnalysisMode.LOCAL:
            mode = f"로컬 {job.model_id or '모델 미기록'}"
        else:
            mode = f"외부 {job.model_id or '모델 미기록'}"
        if job.origin is ResultOrigin.TEST_DOUBLE:
            mode += " (테스트 응답)"
        status = "일부 완료" if job.status is JobStatus.PARTIAL else "완료"
        candidates = f"후보 {len(job.segments)}개" if job.segments else "후보 없음"
        return f"{saved_at} · {mode} · {status} · {candidates}"

    def _update_saved_results_controls(self, running: bool | None = None) -> None:
        if running is None:
            running = self._worker is not None and self._worker.isRunning()
        has_results = bool(self._saved_results)
        self._saved_results_combo.setEnabled(not running and has_results)
        self._open_result_button.setEnabled(not running and has_results)

    def _open_saved_result(self) -> None:
        stored = self._saved_results.get(self._saved_results_combo.currentData())
        if stored is None or self._background_work_running():
            return
        if not self._confirm_discard_unsaved_reviews("다른 결과를 열까요?"):
            return
        job = stored.job
        self._set_result_banner(job.origin, job.model_id)
        self._display_segments(job)
        self.statusBar().showMessage(
            f"저장된 결과를 열었습니다: {self._saved_result_text(stored)}", 6000
        )

    def _open_summary(self) -> ResultSummaryDialog | None:
        job = self._review_panel.job
        if job is None:
            return None
        video_file_name = (
            self._video_path.name
            if self._video_path is not None and self._video_id == job.video_id
            else None
        )
        panel = self._review_panel

        def build():
            case = None
            case_error = None
            if job.case_id:
                try:
                    case = self._case_store.load(job.case_id)
                except FileNotFoundError:
                    case = None
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    case_error = str(exc)
            session = panel.review_session if panel.job is job else None
            return build_summary(
                job,
                session,
                case,
                video_file_name=video_file_name,
                unsaved_count=len(panel.unsaved_indexes()) if panel.job is job else 0,
                case_load_error=case_error,
                common_rules=self._common_rules,
                common_rules_error=self._common_rules_error,
            )

        cameras = self._case_summary_inputs()
        case_build = None
        if sum(camera.job is not None for camera in cameras) > 1:

            def case_build():
                case = None
                case_error = None
                try:
                    case = self._case_store.load(self._case_record.case_id)
                except FileNotFoundError:
                    case = None
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    case_error = str(exc)
                return build_case_summary(
                    case,
                    self._case_summary_inputs(),
                    case_id=self._case_record.case_id,
                    case_load_error=case_error,
                    common_rules=self._common_rules,
                    common_rules_error=self._common_rules_error,
                )

        dialog = ResultSummaryDialog(build, self, case_build=case_build)
        self._summary_dialog = dialog
        dialog.show()
        return dialog

    def _case_summary_inputs(self) -> list[CameraSummaryInput]:
        """Each case camera with the result the reviewer sees for it.

        The open camera uses the result shown in the review panel; the others use their
        newest saved result. Review files are read again so the draft uses saved records.
        """
        panel = self._review_panel
        cameras = []
        for video_id, path in self._case_videos.items():
            unsaved = 0
            load_error = None
            if panel.job is not None and panel.job.video_id == video_id:
                job = panel.job
                session = panel.review_session
                unsaved = len(panel.unsaved_indexes())
            else:
                job = self._newest_jobs.get(video_id)
                session = None
                if job is not None:
                    try:
                        session = self._review_store.load(job.job_id)
                    except (OSError, ValueError, KeyError, TypeError) as exc:
                        load_error = str(exc)
            cameras.append(
                CameraSummaryInput(
                    video_id=video_id,
                    camera=self._camera_name(video_id),
                    file_name=path.name if path is not None else None,
                    link=self._link_for(video_id),
                    job=job,
                    session=session,
                    unsaved_count=unsaved,
                    review_load_error=load_error,
                )
            )
        return cameras

    def _confirm_discard_unsaved_reviews(self, action: str = "닫을까요?") -> bool:
        unsaved = self._review_panel.unsaved_indexes()
        if not unsaved:
            return True
        names = ", ".join(f"후보 #{index + 1}" for index in unsaved)
        answer = QMessageBox.question(
            self,
            "저장하지 않은 검토 기록",
            f"{names}의 검토 기록을 저장하지 않았습니다. 저장하지 않고 {action}",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Discard

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
                    or self.OPENAI_PROVIDER_ID
                ),
                external_model_id=(
                    self._model_edit.text().strip() or None
                    if mode is AnalysisMode.EXTERNAL
                    else self._settings.external_model_id
                ),
                external_endpoint=(
                    os.environ.get("VIDEO_AGENT_EXTERNAL_ENDPOINT")
                    or self._settings.external_endpoint
                    or self.OPENAI_CHAT_COMPLETIONS_ENDPOINT
                ),
                api_key_env_name=os.environ.get(
                    "VIDEO_AGENT_EXTERNAL_API_KEY_ENV", self.OPENAI_API_KEY_ENV
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
        if not self._confirm_discard_unsaved_reviews():
            event.ignore()
            return
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
        self._external_api_key_edit.clear()
        event.accept()
