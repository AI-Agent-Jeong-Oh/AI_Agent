from __future__ import annotations

import os
import re
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QEvent, QObject, QPointF, QSize, Qt, QTimer, QUrl
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
    QApplication,
    QAbstractSpinBox,
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
    QTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStyle,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from video_agent.agent.planning import AgentSession, AnalysisIntent, PlanningError, WorkflowState
from video_agent.agent.situations import DEFAULT_PURPOSES, SITUATION_LABELS
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
    InformationCertainty,
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
from video_agent.ui.analysis_choice import SAMPLE_LABEL, AnalysisChoice
from video_agent.ui.card import Card
from video_agent.ui.case_panel import SOURCE_LABELS, CaseContextPanel
from video_agent.ui.disclosure import DisclosureButton
from video_agent.ui.focus_panel import FocusSection
from video_agent.ui.motion_table import MotionHeader, MotionItemDelegate
from video_agent.ui.judgement_marks import JUDGEMENT_MARKS
from video_agent.ui.time_field import TimeSpinBox, format_time
from video_agent.ui.timeline import TimelineLegend, TimelineMark, TimelineSlider
from video_agent.ui.popover import InfoPopover, info_mark
from video_agent.ui.step_section import StepSection
from video_agent.ui import theme
from video_agent.ui.video_list import CaseVideoList, VideoEntry
from video_agent.knowledge.common_rules import CommonRules, load_common_rules
from video_agent.review.summary import (
    CameraSummaryInput,
    build_case_summary,
    build_summary,
)
from video_agent.ui.case_overview import CaseCandidateOverview, OverviewRow
from video_agent.ui.review_panel import JUDGEMENT_LABELS, SHORTCUT_HELP, CandidateReviewPanel
from video_agent.ui.summary_dialog import ResultSummaryDialog
from video_agent.ui.wrap_label import WrapLabel
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


# Playback speeds offered under the video (1× is the default).
PLAYBACK_RATES = (0.5, 1.0, 1.5, 2.0)

class _StepIndicator(QWidget):
    """Compact stepper like the demo page: a dot per step on a short line, name below.

    Steps before the current one are filled blue dots, the current one is a larger blue
    dot with bold blue text, and later steps are hollow grey dots, so the state does not
    rely on color alone. Steps are centered with a limited width so lines stay short.
    """

    DOT_Y = 8
    STEP_WIDTH = 200

    def __init__(self, buttons: list[QPushButton]) -> None:
        super().__init__()
        self.setObjectName("stepBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._buttons = buttons
        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 4)
        layout.setSpacing(0)
        layout.addStretch(1)
        for button in buttons:
            button.setMinimumWidth(self.STEP_WIDTH)
            button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            layout.addWidget(button)
            button.toggled.connect(self._state_changed)
        layout.addStretch(1)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        # Never squeezed: when the window is short the step pages give way, not the names.
        return self.sizeHint()

    def _state_changed(self, *_args: object) -> None:
        current = self._current()
        for index, button in enumerate(self._buttons):
            button.setAccessibleDescription(
                "현재 단계" if index == current else "지난 단계" if index < current else "다음 단계"
            )
        self.update()

    def _current(self) -> int:
        return next((i for i, b in enumerate(self._buttons) if b.isChecked()), 0)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().paintEvent(event)
        if not self._buttons:
            return
        current = self._current()
        centers = [b.geometry().center().x() for b in self._buttons]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        y = self.DOT_Y
        for index in range(len(centers) - 1):
            reached = index < current
            painter.setPen(QPen(QColor(theme.PRIMARY_FILL if reached else theme.BORDER), 2))
            painter.drawLine(centers[index] + 9, y, centers[index + 1] - 9, y)
        for index, x in enumerate(centers):
            if index == current:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(theme.PRIMARY_FILL))
                painter.drawEllipse(QPointF(x, y), 5.5, 5.5)
            elif index < current:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(theme.PRIMARY_FILL))
                painter.drawEllipse(QPointF(x, y), 4, 4)
            else:
                painter.setPen(QPen(QColor(theme.GRAY_40), 1.5))
                painter.setBrush(QColor(theme.SURFACE))
                painter.drawEllipse(QPointF(x, y), 4, 4)
        painter.end()


class MainWindow(QMainWindow):
    UI_ZOOM_MIN = 75
    UI_ZOOM_MAX = 150
    UI_ZOOM_STEP = 10
    RESULT_CONTEXT_PREROLL_SECONDS = 1.0
    APP_TITLE = "CCTV 영상수사 지원 Agent"
    ORGANIZATION = "대한민국 경찰청"
    STEP_TITLES = ("사건·영상", "분석 계획·실행", "후보 검토")
    # One short line per detection profile; the muted note says it is not an AI judgement.
    MOTION_HELP_GENERAL = "영상 내 움직임이 큰 구간을 후보로 제안합니다. 최대 3개까지 선택할 수 있습니다."
    MOTION_HELP_REPEATED = (
        "작은 영역에서 반복되는 변화가 있는 구간을 후보로 제안합니다. 최대 3개까지 선택할 수 있습니다."
    )
    MOTION_HELP_FINE_DETAIL = (
        "작은 손·손가락·버튼 접촉 변화가 있는 구간을 후보로 제안합니다. 최대 3개까지 선택할 수 "
        "있으며, 적용하면 전체 화면 보기와 중앙 60% 확대 보기로 함께 분석합니다."
    )
    MOTION_EMPTY_NO_VIDEO = "등록된 CCTV 영상이 없습니다."
    MOTION_EMPTY_NOT_SCANNED = ""
    MOTION_EMPTY_RANGE_CHANGED = "범위가 바뀌었습니다. 움직임 구간을 다시 찾아 주세요."
    MOTION_EMPTY_SCANNING = ""  # the progress line below says it
    MOTION_EMPTY_NONE_FOUND = "이 범위에는 움직임 구간이 없습니다."
    OPENAI_PROVIDER_ID = "openai"
    OPENAI_CHAT_COMPLETIONS_ENDPOINT = "https://api.openai.com/v1/chat/completions"
    OPENAI_API_KEY_ENV = "OPENAI_API_KEY"

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(self.APP_TITLE)
        self.resize(1220, 850)

        self._base_font = QFont(theme.load_fonts())
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
        # The candidate range last played from step ③, for the "처음으로" button.
        self._playback_range: tuple[int, int] | None = None
        self._worker: AnalysisWorker | None = None
        self._metadata_worker: MetadataWorker | None = None
        self._motion_worker: MotionScanWorker | None = None
        self._active_motion_scan_id: str | None = None
        self._sampling_interval_override: float | None = None
        self._sampling_timestamps_override: tuple[float, ...] = ()
        self._sampling_strategy_override: SamplingStrategy | None = None
        self._applied_motion_proposals: tuple[MotionProposal, ...] = ()
        # The range typed before checked candidates replaced it; restored if all are unchecked.
        self._motion_range_changed = False
        # "idle" (not scanned), "scanning", "none_found" or "found"; display only.
        self._motion_scan_state = "idle"
        # (metadata, intent) of the run that produced the range expansion proposal.
        self._expansion_context: tuple[object, object] | None = None
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
        self._focus_section.set_values(self._case_record.case_type, self._case_record.focus_situations)
        # A new case starts with an empty name field; the header says "새 사건" meanwhile.
        self._case_panel.title_edit.clear()
        self._refresh_case_choices()
        self._set_idle_state()
        self._refresh_saved_results()
        # Fills the list-dependent parts: candidate empty state, player placeholder,
        # "확인 필요" line and the case name in the header.
        self._refresh_video_list()
        # Drop-down lists drawn by the style sheet (rows, hover and selection) everywhere.
        for combo in self.findChildren(QComboBox):
            theme.polish_combo(combo)
        for spin in self.findChildren(QAbstractSpinBox):
            theme.polish_spin_box(spin)
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
        # One short status line: analysis mode and model, or the run/result state.
        status_row = QWidget()
        status_layout = QHBoxLayout(status_row)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(8)
        self._mode_dot = QLabel()
        self._mode_dot.setObjectName("modeDot")
        self._mode_dot.setAccessibleName("")
        self._mode_banner = QLabel()
        self._mode_banner.setObjectName("modeBanner")
        self._mode_banner.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._mode_banner.setAccessibleName("분석 상태")
        status_layout.addWidget(self._mode_dot)
        status_layout.addWidget(self._mode_banner)
        status_layout.addStretch(1)
        content_layout.addWidget(status_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_video_panel())
        splitter.addWidget(self._build_step_area(self._build_analysis_panel()))
        # The right column (case details, plan, review) gets as much room as the video.
        splitter.setSizes([600, 600])
        content_layout.addWidget(splitter, 1)
        root_layout.addWidget(content, 1)

        self._root_layout = content_layout

        self.setCentralWidget(root)
        self._build_step_navigation()
        self.statusBar().showMessage("영상 파일을 선택해 주세요.")

    def _build_header(self) -> QWidget:
        """Organization mark and app name on the left, the open case on the right."""
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
        # The open case and whether it is saved, like the case number area of the demo.
        case_caption = QLabel("사건")
        case_caption.setObjectName("headerHint")
        self._case_title_label = QLabel()
        self._case_title_label.setObjectName("headerCase")
        self._case_title_label.setAccessibleName("열린 사건")
        case_divider = QFrame()
        case_divider.setObjectName("headerDivider")
        case_divider.setFrameShape(QFrame.Shape.VLine)
        case_divider.setFixedWidth(1)
        self._case_state_label = QLabel()
        self._case_state_label.setObjectName("headerHint")
        self._case_state_label.setAccessibleName("사건 저장 상태")
        row.addWidget(case_caption)
        row.addWidget(self._case_title_label)
        row.addWidget(case_divider)
        row.addWidget(self._case_state_label)
        return header

    def _build_step_navigation(self) -> None:
        """Previous/next buttons on the right of the bottom status bar."""
        navigation = QWidget()
        row = QHBoxLayout(navigation)
        row.setContentsMargins(0, 0, 12, 0)
        row.setSpacing(8)
        self._prev_step_button = QPushButton()
        self._next_step_button = QPushButton()
        theme.set_icon(self._prev_step_button, "arrow-left")
        theme.set_icon(self._next_step_button, "arrow-right")
        # The arrow follows the step name, as on the demo page.
        self._next_step_button.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        for button in (self._prev_step_button, self._next_step_button):
            # Keep each button at its text width when the other one is hidden.
            button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        # [사건 저장] sits here in step ①, after the form is filled (not at the card top).
        save = self._case_panel.save_button
        save.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        row.addWidget(save)
        row.addWidget(self._prev_step_button)
        row.addWidget(self._next_step_button)
        # QStatusBar paints messages 6px from its left edge; its own text is transparent in
        # the theme and this label repeats the message with the page margin instead.
        self._status_label = QLabel()
        self._status_label.setObjectName("statusMessage")
        self._status_label.setAccessibleName("상태 메시지")
        status_bar = self.statusBar()
        status_bar.messageChanged.connect(self._status_label.setText)
        self._attention_label = QLabel()
        self._attention_label.setObjectName("statusAttention")
        self._attention_label.setAccessibleName("확인 필요")
        self._attention_label.setVisible(False)
        status_bar.addPermanentWidget(self._attention_label)
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
        # Step ② sets up the analysis beside the video list, above the player; the
        # settings card is added in _build_analysis_panel and shown only on that step.
        self._top_row = QHBoxLayout()
        self._top_row.addWidget(self._video_list, 5)
        layout.addLayout(self._top_row)

        self._video_widget = QVideoWidget()
        self._video_widget.setStyleSheet(f"background: {theme.VIDEO_BACKGROUND};")
        self._player.setVideoOutput(self._video_widget)
        # Until a video is open the player area says what to do instead of staying blank.
        placeholder = QWidget()
        placeholder.setObjectName("videoPlaceholder")
        placeholder.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        placeholder_layout = QVBoxLayout(placeholder)
        placeholder_layout.addStretch(1)
        placeholder_icon = QLabel()
        placeholder_icon.setPixmap(theme.icon("video-off", theme.GRAY_40).pixmap(40, 40))
        placeholder_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._video_placeholder_text = QLabel()
        self._video_placeholder_text.setObjectName("videoPlaceholderText")
        self._video_placeholder_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._video_placeholder_text.setWordWrap(True)
        placeholder_layout.addWidget(placeholder_icon)
        placeholder_layout.addWidget(self._video_placeholder_text)
        placeholder_layout.addStretch(1)
        self._video_stack = QStackedWidget()
        self._video_stack.setMinimumHeight(200)
        self._video_stack.addWidget(placeholder)
        self._video_stack.addWidget(self._video_widget)
        layout.addWidget(self._video_stack, 1)

        # Like the demo page: the playback bar takes a full line, the buttons sit below it.
        # Also marks the analysed candidate segments once a result for this video is shown.
        self._timeline = TimelineSlider()
        self._timeline.setRange(0, 0)
        self._timeline.setAccessibleName("재생 위치")
        layout.addWidget(self._timeline)
        # Names the candidate marks; shown only while the bar has marks.
        self._timeline_legend = TimelineLegend()
        self._timeline_legend.setVisible(False)
        layout.addWidget(self._timeline_legend)

        controls = QHBoxLayout()
        controls.setSpacing(4)
        self._start_button = QPushButton()
        self._back_button = QPushButton()
        self._forward_button = QPushButton()
        for button, icon, name in (
            (self._start_button, "skip-back", "처음으로 (구간 재생 중이면 구간 처음)"),
            (self._back_button, "rewind", "10초 뒤로"),
            (self._forward_button, "fast-forward", "10초 앞으로"),
        ):
            button.setObjectName("playerIcon")
            theme.set_icon(button, icon, theme.TEXT)
            button.setAccessibleName(name)
        # Only "처음으로" needs a hover note (it returns to the played range first).
        self._start_button.setToolTip("처음으로 (구간 재생 중이면 구간 처음)")
        self._play_button = QPushButton("재생")
        theme.set_icon(self._play_button, "play")
        theme.mark_compact(self._play_button)
        self._time_label = QLabel("00:00 / 00:00")
        self._time_label.setObjectName("playerTime")
        self._time_label.setAccessibleName("재생 시각")
        time_font = self._time_label.font()
        time_font.setFeature(QFont.Tag("tnum"), 1)  # digits keep their width while playing
        self._time_label.setFont(time_font)
        speed_label = QLabel("속도")
        speed_label.setObjectName("fieldLabel")
        self._speed_combo = QComboBox()
        self._speed_combo.setAccessibleName("재생 속도")
        for rate in PLAYBACK_RATES:
            self._speed_combo.addItem(f"{rate:g}×", rate)
        self._speed_combo.setCurrentIndex(PLAYBACK_RATES.index(1.0))
        theme.mark_compact(self._speed_combo)
        controls.addWidget(self._start_button)
        controls.addWidget(self._back_button)
        controls.addWidget(self._play_button)
        controls.addWidget(self._forward_button)
        controls.addWidget(self._time_label)
        controls.addStretch(1)
        controls.addWidget(speed_label)
        controls.addWidget(self._speed_combo)
        layout.addLayout(controls)
        return panel

    def _build_analysis_panel(self) -> tuple[QWidget, QWidget, QWidget]:
        """Build the step pages: case/video, planning and execution, candidate review."""
        case_page = QWidget()
        case_layout = QVBoxLayout(case_page)
        case_layout.setContentsMargins(0, 0, 0, 0)
        self._case_panel = CaseContextPanel()
        # The card fills the step; its clue list takes the spare height and scrolls inside.
        case_layout.addWidget(self._case_panel, 1)

        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)

        # Step ② is one card in three numbered parts (see below); this form holds the
        # analysis choice and the external rows, in part 3.
        method_form = QWidget()
        purpose_layout = QFormLayout(method_form)
        purpose_layout.setContentsMargins(0, 0, 0, 0)
        # How to analyse, in plain words: fast or accurate on this computer, a
        # demonstration, or an external AI service (models stay behind the choices).
        self._analysis_choice = AnalysisChoice()
        # Icon only, so the choice beside it keeps its full name in narrow cards.
        self._model_refresh_button = QPushButton()
        self._model_refresh_button.setObjectName("rowAction")
        theme.set_icon(self._model_refresh_button, "refresh-cw")
        self._model_refresh_button.setToolTip("이 컴퓨터에 설치된 AI 분석 프로그램을 다시 찾습니다")
        self._model_refresh_button.setAccessibleName("분석 프로그램 다시 찾기")
        choice_row = QWidget()
        choice_row_layout = QHBoxLayout(choice_row)
        choice_row_layout.setContentsMargins(0, 0, 0, 0)
        choice_row_layout.addWidget(self._analysis_choice, 1)
        choice_row_layout.addWidget(self._model_refresh_button)
        self._external_model_edit = QLineEdit()
        self._external_model_edit.setPlaceholderText("예: gpt-4.1-mini")
        self._external_model_edit.setAccessibleName("외부 AI 이름")
        self._external_api_key_edit = QLineEdit()
        self._external_api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._external_api_key_edit.setClearButtonEnabled(True)
        self._external_api_key_edit.setPlaceholderText("외부 서비스 인증키 (저장하지 않음)")
        self._external_api_key_edit.setToolTip(
            "키는 설정·결과·로그에 저장하지 않습니다. OPENAI_API_KEY 환경 변수도 사용할 수 있습니다."
        )
        # Two lines so the narrower settings card beside the video list keeps its width.
        self._external_vision_confirm = QCheckBox(
            "이 외부 AI가 사진을 읽을 수 있음을\n확인했습니다"
        )
        self._external_vision_confirm.setAccessibleName(
            "이 외부 AI가 사진을 읽을 수 있음을 확인했습니다"
        )
        self._external_vision_confirm.setToolTip(
            "텍스트 전용 모델을 영상 분석 모델처럼 사용하지 않기 위한 명시적 확인입니다."
        )
        self._purpose_edit = QPlainTextEdit()
        self._purpose_edit.setPlaceholderText(
            "예: 출입문 앞에 30초 이상 머무는 사람"
        )
        range_row = QWidget()
        range_layout = QHBoxLayout(range_row)
        range_layout.setContentsMargins(0, 0, 0, 0)
        # Entered as minutes:seconds ("1:30"), like the player's time; values stay seconds.
        self._range_start = TimeSpinBox()
        self._range_end = TimeSpinBox()
        for spin in (self._range_start, self._range_end):
            spin.setRange(0, 0)
        self._range_end.setSpecialValueText("영상 끝")
        for label, spin in (("시작", self._range_start), ("종료", self._range_end)):
            name = QLabel(label)
            name.setObjectName("fieldLabel")
            range_layout.addWidget(name)
            range_layout.addWidget(spin, 1)
        self._purpose_edit.setAccessibleName("분석 목적")
        self._range_start.setAccessibleName("분석 구간 시작")
        self._range_end.setAccessibleName("분석 구간 종료")
        # The external rows appear only for the external choice.
        purpose_layout.addRow("분석 방식", choice_row)
        purpose_layout.addRow("외부 AI", self._external_model_edit)
        purpose_layout.addRow("인증키", self._external_api_key_edit)
        purpose_layout.addRow("사진 입력", self._external_vision_confirm)
        self._last_mode: AnalysisMode | None = None
        self._purpose_form_layout = purpose_layout
        # Labels move above their fields only when a row would not fit (large text).
        self._purpose_form_layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        # Left-aligned labels and full-width fields on every platform (macOS centres forms).
        theme.polish_form(purpose_layout)

        self._plan_group = Card("분석 계획")
        plan_layout = QVBoxLayout(self._plan_group.body)
        plan_layout.setContentsMargins(12, 12, 12, 12)
        self._motion_help = WrapLabel(self.MOTION_HELP_GENERAL)
        self._motion_note = WrapLabel("움직임 구간은 AI의 행동 판정 결과가 아닙니다.")
        self._motion_note.setObjectName("subsectionHelp")
        # Shown instead of the empty candidate lists (see _refresh_motion_empty_state).
        self._motion_empty_label = WrapLabel("")
        self._motion_empty_label.setObjectName("emptyState")
        self._motion_scan_button = QPushButton("움직임 구간 찾기")
        self._motion_list = QListWidget()
        self._motion_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        # Rows drawn in columns under a header, like a table (see motion_table).
        self._motion_list.setItemDelegate(MotionItemDelegate(self._motion_list))
        self._motion_header = MotionHeader(self._motion_list)
        self._motion_list.setToolTip(
            "변화 크기는 이번 탐색 안에서 비교한 값이며 행동 판정이 아닙니다. "
            "체크박스로 최대 3개까지 고를 수 있습니다."
        )
        self._motion_selection_label = WrapLabel("선택 0/3")
        self._motion_selection_label.setObjectName("subsectionHelp")
        theme.mark_compact(self._motion_scan_button)
        # Shown only while the planner has a question to answer.
        self._question_label = WrapLabel("")
        self._clarification_edit = QLineEdit()
        self._clarification_edit.setPlaceholderText("답변 입력 후 '분석 계획 만들기'")
        self._question_label.setVisible(False)
        self._clarification_edit.setVisible(False)
        self._plan_view = QTextEdit()
        self._plan_view.setReadOnly(True)
        # Shown once there is a plan (no empty box before that).
        self._plan_view.setVisible(False)
        self._make_plan_button = QPushButton("분석 계획 만들기")
        self._make_plan_button.setToolTip(
            "체크한 움직임 구간과 입력한 목적·구간으로 계획을 만듭니다. 추가 질문이 있으면 먼저 표시합니다."
        )
        # What to look at first is part of the plan, as on the demo page; the case type
        # chosen in step ① marks its recommendations. Three columns fit the wide card.
        self._focus_section = FocusSection(columns=3)

        # Three numbered parts, one open at a time (the rest show a one-line summary):
        # 1 what to look for, 2 which part of the video, 3 the plan, approval and run.
        find_part = StepSection(1, "무엇을 찾을지")
        purpose_label = QLabel("분석 목적")
        purpose_label.setObjectName("fieldLabel")
        find_part.body_layout.addWidget(purpose_label)
        find_part.body_layout.addWidget(self._purpose_edit)
        find_part.body_layout.addWidget(self._focus_section)
        find_part.body_layout.addStretch(1)

        range_part = StepSection(2, "볼 구간")
        # The explanation sits behind ⓘ; its text follows the purpose (see _motion_help).
        self._motion_help.setVisible(False)
        self._motion_note.setVisible(False)
        self._motion_info_button = QPushButton("안내")
        self._motion_info_button.setObjectName("infoMark")
        theme.set_icon(self._motion_info_button, "info", theme.PRIMARY)
        self._motion_info_button.setAccessibleName("움직임 구간 안내")
        self._motion_info_button.clicked.connect(self._show_motion_help)
        range_part.add_header_widget(self._motion_info_button)
        range_line = QHBoxLayout()
        range_line.addWidget(self._motion_scan_button)
        range_line.addSpacing(8)
        range_line.addWidget(range_row, 1)
        range_part.body_layout.addLayout(range_line)
        range_part.body_layout.addWidget(self._motion_help)
        range_part.body_layout.addWidget(self._motion_note)
        range_part.body_layout.addWidget(self._motion_empty_label)
        motion_table = QWidget()
        motion_table_layout = QVBoxLayout(motion_table)
        motion_table_layout.setContentsMargins(0, 0, 0, 0)
        motion_table_layout.setSpacing(0)
        motion_table_layout.addWidget(self._motion_header)
        motion_table_layout.addWidget(self._motion_list, 1)
        range_part.body_layout.addWidget(motion_table, 1)
        range_part.body_layout.addWidget(self._motion_selection_label)

        plan_part = StepSection(3, "계획 확인·실행")
        plan_part.body_layout.addWidget(method_form)
        plan_part.body_layout.addWidget(self._question_label)
        plan_part.body_layout.addWidget(self._clarification_edit)
        plan_part.body_layout.addWidget(self._plan_view, 1)
        # Sampling, limits and model guidance, folded under the plan.
        self._plan_details_toggle = DisclosureButton("세부 조건")
        self._plan_details_view = QTextEdit()
        self._plan_details_view.setReadOnly(True)
        self._plan_details_view.setVisible(False)
        self._plan_details_toggle.setVisible(False)
        self._plan_details_toggle.toggled.connect(self._plan_details_view.setVisible)
        plan_part.body_layout.addWidget(self._plan_details_toggle)
        plan_part.body_layout.addWidget(self._plan_details_view, 1)
        plan_part.body_layout.addStretch(0)

        self._plan_parts = (find_part, range_part, plan_part)
        for number, part in enumerate(self._plan_parts):
            if number:
                divider = QFrame()
                divider.setObjectName("sectionDivider")
                plan_layout.addWidget(divider)
            plan_layout.addWidget(part)
            part.open_requested.connect(lambda value=number: self._toggle_plan_part(value))
        self._open_plan_part(0)
        self._plan_was_ready = False
        layout.addWidget(self._plan_group, 1)

        # One pinned bar for planning, approval, run and progress so the plan keeps the space.
        run_bar = QFrame()
        run_bar.setObjectName("runBar")
        run_bar.setAccessibleName("승인과 실행")
        run_layout = QVBoxLayout(run_bar)
        self._approve_button = QPushButton("분석 계획 승인")
        self._transfer_button = QPushButton("외부 전송 승인")
        self._run_button = QPushButton("분석 실행")
        theme.mark_primary(self._approve_button)
        theme.mark_primary(self._run_button)
        # KRDS xsmall (32px) buttons and 8-point margins keep the pinned bar short.
        for button in (
            self._make_plan_button,
            self._approve_button,
            self._run_button,
            self._transfer_button,
        ):
            theme.mark_compact(button)
        run_layout.setContentsMargins(12, 8, 12, 8)
        run_layout.setSpacing(6)
        # One always-visible place for the main action: plan, then approve, then run.
        approve_row = QHBoxLayout()
        approve_row.addWidget(self._make_plan_button, 1)
        approve_row.addWidget(self._approve_button, 1)
        approve_row.addWidget(self._run_button, 1)
        run_layout.addLayout(approve_row)
        # Only external mode needs the separate transfer approval.
        run_layout.addWidget(self._transfer_button)
        self._progress_bar = QProgressBar()
        self._progress_label = WrapLabel("대기 중")
        self._progress_label.setAccessibleName("실행 상태")
        self._cancel_button = QPushButton("취소")
        theme.set_variant(self._cancel_button, "chip")
        run_layout.addWidget(self._progress_bar)
        status_row = QHBoxLayout()
        status_row.addWidget(self._progress_label, 1)
        status_row.addWidget(self._cancel_button, 0, Qt.AlignmentFlag.AlignTop)
        run_layout.addLayout(status_row)

        # Approval and run stay visible below the scrolling plan content.
        self._plan_scroll = QScrollArea()
        self._plan_scroll.setWidgetResizable(True)
        self._plan_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._plan_scroll.setWidget(panel)
        plan_step = QWidget()
        plan_step_layout = QVBoxLayout(plan_step)
        plan_step_layout.setContentsMargins(0, 0, 0, 0)
        plan_step_layout.addWidget(self._plan_scroll, 1)
        plan_step_layout.addWidget(run_bar)

        result_group = Card("중요 구간 후보")
        result_layout = QVBoxLayout(result_group.body)
        result_layout.setContentsMargins(12, 8, 12, 8)
        help_text = (
            "후보를 선택하면 맥락 확인을 위해 원본 영상이 확인용 범위 시작의 최대 1초 전으로 이동하고, "
            "표시된 근거 범위를 지나 계속 재생합니다. "
            "입력한 분석 구간 수와 결과 후보 수는 같지 않을 수 있으며, 결과는 0개·1개·여러 개이거나 중복 정리로 합쳐질 수 있습니다.\n"
            + SHORTCUT_HELP
        )
        # Kept as a tooltip (like the demo's info marks) so the review step fits one screen.
        help_label = info_mark(help_text, "후보 재생과 결과 수 안내", "재생 안내")
        saved_row = QHBoxLayout()
        self._saved_results_combo = QComboBox()
        self._saved_results_combo.setAccessibleName("이 영상의 저장된 분석 결과")
        # Long result names are cut in the closed box (full text in the list) so the row
        # never forces a horizontal scroll bar.
        self._saved_results_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self._saved_results_combo.setMinimumContentsLength(3)
        self._open_result_button = QPushButton("열기")
        self._open_result_button.setToolTip("선택한 저장 결과와 검토 기록을 불러옵니다")
        saved_row.addWidget(QLabel("이전 결과"))
        saved_row.addWidget(self._saved_results_combo, 1)
        saved_row.addWidget(self._open_result_button)
        # Shown only when this video has an earlier result to switch to (the newest
        # one opens by itself).
        self._saved_results_row = QWidget()
        self._saved_results_row.setLayout(saved_row)
        saved_row.setContentsMargins(0, 0, 0, 0)
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
        # Tabs sized to their names and kept at the left (macOS would centre them).
        camera_row.addWidget(self._camera_tabs)
        camera_row.addStretch(1)
        camera_row.addWidget(self._case_overview.toggle_button)
        self._camera_row.setVisible(False)
        result_layout.addWidget(self._camera_row)
        result_layout.addWidget(self._case_overview)
        result_layout.addWidget(self._saved_results_row)
        self._review_panel = CandidateReviewPanel(self._segment_text)
        result_group.add_header_widget(self._review_panel.completion_bar)
        self._review_panel.playback_row.flow.addWidget(help_label)
        self._result_list = self._review_panel.candidate_list
        self._review_empty_label = QLabel("아직 분석 결과가 없습니다.")
        self._review_empty_label.setWordWrap(True)
        self._review_empty_label.setObjectName("placeholder")
        self._result_help_label = help_label
        result_layout.addWidget(self._build_expansion_proposal())
        result_layout.addWidget(self._review_empty_label)
        result_layout.addWidget(self._review_panel, 1)
        result_layout.addStretch(1)
        self._result_layout = result_layout

        review_page = QWidget()
        review_layout = QVBoxLayout(review_page)
        review_layout.setContentsMargins(0, 0, 0, 0)
        review_layout.addWidget(result_group, 1)
        self._step_layouts = (case_layout, layout, review_layout)
        self._approval_group = run_bar
        self._progress_group = run_bar
        self._result_group = result_group
        return case_page, plan_step, review_page

    def _build_expansion_proposal(self) -> QFrame:
        """Shown after a run with no candidates over part of the video; never runs anything."""
        frame = QFrame()
        frame.setObjectName("expansionProposal")
        frame.setStyleSheet(
            f"QFrame#expansionProposal {{{theme.banner_style('local')} border-radius:6px;}}"
        )
        layout = QVBoxLayout(frame)
        title = QLabel("범위 확대 제안")
        title.setStyleSheet(f"color:{theme.PRIMARY_DARK};font-weight:700;")
        self._expansion_label = QLabel()
        self._expansion_label.setWordWrap(True)
        self._expansion_apply_button = QPushButton("영상 전체로 범위 넓히기")
        self._expansion_apply_button.setToolTip(
            "범위만 바꿉니다. 계획을 다시 만들고 승인해야 실행됩니다."
        )
        theme.mark_primary(self._expansion_apply_button)
        self._expansion_close_button = QPushButton("닫기")
        buttons = QHBoxLayout()
        buttons.addWidget(self._expansion_apply_button)
        buttons.addWidget(self._expansion_close_button)
        buttons.addStretch(1)
        layout.addWidget(title)
        layout.addWidget(self._expansion_label)
        layout.addLayout(buttons)
        frame.setAccessibleName("범위 확대 제안")
        frame.setVisible(False)
        self._expansion_frame = frame
        return frame

    def _connect_signals(self) -> None:
        self._case_panel.changed.connect(self._case_inputs_changed)
        self._case_panel.save_requested.connect(self._save_case)
        self._case_panel.load_requested.connect(self._load_case)
        self._case_panel.case_type_chosen.connect(self._suggest_purpose)
        self._case_panel.case_type_chosen.connect(self._focus_section.set_case_type)
        self._focus_section.changed.connect(self._case_inputs_changed)
        self._case_panel.new_requested.connect(self._new_case)
        self._choose_button.clicked.connect(self._choose_video)
        self._video_list.video_activated.connect(self._activate_listed_video)
        self._video_list.relink_requested.connect(self._relink_video)
        self._video_list.remove_requested.connect(self._remove_video)
        self._video_list.clock_changed.connect(self._set_video_clock)
        self._camera_tabs.currentChanged.connect(self._camera_tab_changed)
        # A click on the already selected tab still opens its result when none is shown.
        self._camera_tabs.tabBarClicked.connect(self._camera_tab_changed)
        self._case_overview.candidate_activated.connect(self._activate_overview_candidate)
        self._review_panel.records_changed.connect(self._review_records_changed)
        self._expansion_apply_button.clicked.connect(self._apply_range_expansion)
        self._expansion_close_button.clicked.connect(self._hide_range_expansion)
        self._play_button.clicked.connect(self._toggle_playback)
        self._start_button.clicked.connect(self._go_to_playback_start)
        self._speed_combo.currentIndexChanged.connect(lambda _index: self._apply_playback_rate())
        self._back_button.clicked.connect(lambda: self._skip_playback(-10))
        self._forward_button.clicked.connect(lambda: self._skip_playback(10))
        self._timeline.sliderPressed.connect(self._slider_pressed)
        self._timeline.mark_clicked.connect(self._timeline_mark_clicked)
        self._review_panel.selection_changed.connect(self._refresh_timeline_marks)
        self._review_panel.play_toggle_requested.connect(self._toggle_playback)
        self._timeline.sliderReleased.connect(self._slider_released)
        self._purpose_edit.textChanged.connect(self._purpose_changed)
        self._analysis_choice.choice_changed.connect(self._analysis_choice_changed)
        self._external_model_edit.textChanged.connect(self._model_changed)
        self._model_refresh_button.clicked.connect(self._refresh_local_models)
        self._external_api_key_edit.textChanged.connect(self._external_credential_changed)
        self._external_vision_confirm.checkStateChanged.connect(
            self._external_credential_changed
        )
        self._range_start.valueChanged.connect(self._range_changed)
        self._range_end.valueChanged.connect(self._range_changed)
        self._motion_scan_button.clicked.connect(self._start_motion_scan)
        self._motion_list.itemClicked.connect(self._preview_motion_proposal)
        self._motion_list.itemChanged.connect(self._motion_check_changed)
        self._make_plan_button.clicked.connect(self._make_plan)
        self._approve_button.clicked.connect(self._approve_and_run)
        self._transfer_button.clicked.connect(self._approve_transfer)
        self._run_button.clicked.connect(self._start_analysis)
        self._cancel_button.clicked.connect(self._cancel_analysis)
        # Every enable/disable of a step ② action re-picks the one blue "next" button.
        self._next_action_buttons = (
            self._motion_scan_button,
            self._make_plan_button,
            self._approve_button,
            self._transfer_button,
            self._run_button,
        )
        for button in self._next_action_buttons:
            button.installEventFilter(self)
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

        self._zoom_reset_action = QAction("기본 크기", self)
        self._zoom_reset_action.setShortcut(QKeySequence("Ctrl+0"))
        self._zoom_reset_action.triggered.connect(lambda: self._set_ui_zoom(100))
        self.addAction(self._zoom_reset_action)
        self._build_menu_bar()

    def _build_menu_bar(self) -> None:
        """File and view menus with the usual shortcuts (the macOS menu bar on a Mac)."""
        file_menu = self.menuBar().addMenu("파일")
        self._new_case_action = file_menu.addAction("새 사건")
        self._new_case_action.setShortcut(QKeySequence.StandardKey.New)
        self._new_case_action.triggered.connect(self._new_case)
        self._open_case_menu = file_menu.addMenu("사건 불러오기")
        self._open_case_menu.aboutToShow.connect(
            lambda: self._case_panel.fill_case_menu(self._open_case_menu, with_new=False)
        )
        self._save_case_action = file_menu.addAction("사건 저장")
        self._save_case_action.setShortcut(QKeySequence.StandardKey.Save)
        self._save_case_action.triggered.connect(self._save_case)
        file_menu.addSeparator()
        self._add_video_action = file_menu.addAction("영상 추가…")
        self._add_video_action.setShortcut(QKeySequence.StandardKey.Open)
        self._add_video_action.triggered.connect(self._choose_video)
        file_menu.addSeparator()
        summary_action = file_menu.addAction("결과 요약…")
        summary_action.triggered.connect(self._review_panel.summary_button.click)
        view_menu = self.menuBar().addMenu("보기")
        for action in (self._zoom_in_action, self._zoom_out_action, self._zoom_reset_action):
            view_menu.addAction(action)

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

        def scaled(value: int) -> int:
            return max(1, round(value * scale))

        # KRDS sizes are in px; body text is 16px at 100%.
        font = QFont(self._base_font)
        font.setPixelSize(scaled(theme.BODY_PX))
        self.setFont(font)

        # KRDS 8-point spacing: 24px screen margin, 16px between components.
        self._root_layout.setContentsMargins(
            scaled(24), scaled(12), scaled(24), scaled(12)
        )
        self._root_layout.setSpacing(scaled(16))
        if not self._emblem_pixmap.isNull():
            self._emblem_label.setPixmap(
                self._emblem_pixmap.scaledToHeight(
                    scaled(34), Qt.TransformationMode.SmoothTransformation
                )
            )
        for step_layout in self._step_layouts:
            step_layout.setSpacing(scaled(16))
        # Two lines above the video; longer purposes scroll inside the box.
        self._purpose_edit.setFixedHeight(scaled(64))
        # Fixed so "재생" and "일시정지" do not move the buttons beside it.
        self._play_button.setFixedWidth(scaled(104))
        # Room above the bar for the small bookmark over a candidate mark.
        self._timeline.setMinimumHeight(scaled(38))
        self._motion_list.setMinimumHeight(scaled(128))
        self._motion_list.setMaximumHeight(scaled(320))
        self._plan_view.setMinimumHeight(scaled(145))
        self._plan_view.setMaximumHeight(scaled(220))
        # One table row stays visible; more rows scroll inside the list.
        self._case_panel.clue_list.setMinimumHeight(scaled(46))
        self._case_panel.notes_edit.setFixedHeight(scaled(64))
        # Lines up with the 40px choices beside it in the wrapping row.
        self._case_panel.clue_use.setMinimumHeight(scaled(40))
        self._plan_group.setMinimumHeight(scaled(480))
        self._top_row.setSpacing(scaled(12))
        self._review_panel.apply_scale(scaled)
        self._video_list.apply_scale(scaled)
        self._case_overview.apply_scale(scaled)

        self._zoom_reset_action.setText(f"기본 크기 (현재 {self._ui_zoom_percent}%)")
        self._zoom_out_action.setEnabled(self._ui_zoom_percent > self.UI_ZOOM_MIN)
        self._zoom_in_action.setEnabled(self._ui_zoom_percent < self.UI_ZOOM_MAX)
        self.setStyleSheet(theme.build_stylesheet(scaled))

    def _restore_settings(self) -> None:
        mode = self._settings.mode
        if self._local_vision_models():
            # Every start is "정확한 분석" (the largest model on this computer); a choice
            # made while the app is open lasts until it closes.
            mode = AnalysisMode.LOCAL
        self._external_model_edit.blockSignals(True)
        self._external_model_edit.setText(
            os.environ.get("VIDEO_AGENT_EXTERNAL_MODEL") or self._settings.external_model_id or ""
        )
        self._external_model_edit.blockSignals(False)
        self._analysis_choice.blockSignals(True)
        self._refresh_analysis_choices()
        self._analysis_choice.select(mode, self._preferred_local_model())
        self._analysis_choice.blockSignals(False)
        self._last_mode = self._current_mode()
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
            case_type=self._case_panel.case_type_text(),
            occurrence_time_text=self._case_panel.occurrence_edit.text().strip(),
            occurrence_time_certainty=self._case_panel.occurrence_certainty.currentData(),
            management_notes=self._case_panel.notes_edit.toPlainText().strip(),
            clues=self._current_case_clues(),
            video_links=tuple(sorted(links, key=lambda item: item.video_id)),
            focus_situations=self._focus_section.focus(),
            # Search hints are entered as clues now (older hints load as clues).
            focus_hints=(),
        )

    @staticmethod
    def _case_content(record: CaseRecord) -> dict[str, object]:
        data = record.to_dict()
        data.pop("revision", None)
        return data

    def _suggest_purpose(self, case_type: str) -> None:
        """Start an empty purpose from the case type (the user can rewrite it)."""
        suggestion = DEFAULT_PURPOSES.get(case_type)
        if suggestion and not self._purpose_edit.toPlainText().strip():
            self._purpose_edit.setPlainText(suggestion)

    def _case_inputs_changed(self) -> None:
        self._refresh_video_list()
        if self._background_work_running():
            return
        self._inputs_changed()

    def _save_case(self) -> None:
        if self._background_work_running():
            self._show_error("작업 중에는 사건 정보를 저장할 수 없습니다.")
            return
        if not self._case_panel.require_title():
            self._show_step(0)
            self.statusBar().showMessage("사건명을 입력한 뒤 저장해 주세요.", 6000)
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
        self._refresh_case_header()
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
        self._case_panel.set_record(record)
        self._focus_section.set_values(record.case_type, record.focus_situations)
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
        self._case_panel.set_record(self._case_record)
        self._focus_section.set_values(self._case_record.case_type, self._case_record.focus_situations)
        self._case_panel.title_edit.clear()
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
        self._set_player_controls_enabled(False)
        self._motion_scan_button.setEnabled(False)
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

    def _set_video_clock(
        self, video_id: str, offset_seconds: float, certainty: InformationCertainty
    ) -> None:
        """Each video keeps its own camera clock difference (edited from its table row)."""
        if self._background_work_running():
            self._show_error("작업 중에는 시각 오차를 바꿀 수 없습니다.")
            return
        self._link_drafts[video_id] = replace(
            self._link_for(video_id),
            clock_offset_seconds=offset_seconds,
            clock_certainty=certainty,
        )
        self._case_inputs_changed()

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

    def _link_for(self, video_id: str) -> CaseVideoLink:
        """Camera details of a video: unsaved edits first, then the saved case."""
        if video_id in self._link_drafts:
            return self._link_drafts[video_id]
        saved = next(
            (item for item in self._case_record.video_links if item.video_id == video_id), None
        )
        return saved or CaseVideoLink(video_id=video_id)

    _ORIGIN_SHORT = {
        ResultOrigin.SAMPLE: "시연용",
        ResultOrigin.TEST_DOUBLE: "테스트 응답",
        ResultOrigin.REAL_MODEL: "AI 분석",
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
                    clock_offset_seconds=link.clock_offset_seconds,
                    analysis_status=status,
                    analyzed=analyzed,
                )
            )
        self._video_list.set_entries(tuple(entries), self._video_id)
        self._refresh_camera_tabs()
        self._refresh_case_overview()
        self._refresh_motion_empty_state()
        self._refresh_video_placeholder()
        self._refresh_attention()
        self._refresh_case_header()
        self._refresh_timeline_marks()

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
            # Short names in case-list order ("영상 1", "영상 2"); the file name is in the
            # hover note, so long names do not stretch the row.
            for number, video_id in enumerate(self._case_videos, start=1):
                job = self._newest_jobs.get(video_id)
                found = f"({len(job.segments)})" if job is not None else "(결과 없음)"
                index = tabs.addTab(f"영상 {number} {found}")
                tabs.setTabData(index, video_id)
                linked = self._case_videos[video_id] is not None
                tabs.setTabToolTip(
                    index,
                    self._camera_name(video_id)
                    + ("" if linked else "\n파일 다시 연결 필요 · 저장된 결과와 기록만 볼 수 있음"),
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
        self._refresh_timeline_marks()

    def _refresh_timeline_marks(self) -> None:
        """Mark the shown result's candidates on the playback bar of the same video."""
        job = self._review_panel.job
        if job is None or job.video_id != self._video_id or self._duration_ms <= 0:
            self._timeline.set_marks(())
            self._timeline_legend.setVisible(False)
            return
        selected = self._review_panel.current_index
        marks = []
        for index, segment in enumerate(job.segments):
            record = self._review_panel.saved_record_for(index)
            judgement = record.judgement
            start = seconds_to_milliseconds(segment.start_sec)
            end = seconds_to_milliseconds(segment.end_sec)
            marks.append(
                TimelineMark(
                    start_ms=min(start, self._duration_ms),
                    end_ms=min(end, self._duration_ms),
                    style=JUDGEMENT_MARKS[judgement],
                    label=(
                        f"후보 #{index + 1} · {format_milliseconds(start)}–{format_milliseconds(end)}"
                        f" · {JUDGEMENT_LABELS[judgement]}"
                        + (" · 북마크" if record.bookmarked else "")
                    ),
                    selected=index == selected,
                    bookmarked=record.bookmarked,
                )
            )
        self._timeline.set_marks(tuple(marks))
        self._timeline_legend.setVisible(bool(marks))

    def _timeline_mark_clicked(self, index: int) -> None:
        """A band on the playback bar selects that candidate and plays from it."""
        if not self._review_panel.select_segment(index):
            return
        item = self._result_list.currentItem()
        if item is not None:
            self._seek_to_segment(item)

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
        self._video_path = path
        self._video_id = video_id
        self._review_video_id = video_id
        self._case_videos[video_id] = path
        self._metadata = None
        self._duration_ms = 0
        self._metadata_label.setText("영상 정보 확인 중…")
        self._timeline.setRange(0, 0)
        self._time_label.setText("00:00 / 길이 확인 중")
        self._set_player_controls_enabled(False)
        self._review_panel.clear()
        self._hide_range_expansion()
        self._refresh_review_empty_state()
        self._clear_motion_proposals()
        self._sampling_interval_override = None
        self._sampling_timestamps_override = ()
        self._sampling_strategy_override = None
        self._applied_motion_proposals = ()
        self._motion_scan_profile = MotionScanProfile.GENERAL_PEAK
        self._motion_scan_purpose = None
        self._invalidate_plan_display()
        self._playback_range = None
        self._player.setSource(QUrl.fromLocalFile(str(path)))
        self._apply_playback_rate()
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
        # Size and length for the reader; the technical probe details stay in the tooltip.
        self._metadata_label.setText(
            f"{metadata.display_width}×{metadata.display_height} · "
            f"{format_milliseconds(seconds_to_milliseconds(metadata.duration_seconds))}"
        )
        self._metadata_label.setToolTip(
            f"길이 {metadata.duration_seconds:.3f}초 · 평균 FPS {average_rate} · "
            f"회전 {metadata.rotation_degrees}° · {vfr}"
        )
        self._motion_scan_button.setEnabled(True)
        self._make_plan_button.setEnabled(True)
        self._inputs_changed()
        self.statusBar().showMessage("영상 정보 확인 완료", 5000)

    def _on_metadata_error(self, message: str) -> None:
        self._metadata = None
        self._metadata_label.setText("영상 정보를 읽지 못함")
        self._motion_scan_button.setEnabled(False)
        self._make_plan_button.setEnabled(False)
        self._show_error(message)

    def _on_metadata_finished(self) -> None:
        self._choose_button.setEnabled(not self._background_work_running())
        self._video_list.set_locked(self._background_work_running())

    def _start_motion_scan(self) -> None:
        if self._video_path is None or self._metadata is None:
            self._show_error("영상 정보 확인이 끝난 뒤 다시 시도해 주세요.")
            return
        if self._background_work_running():
            self._show_error("이미 백그라운드 작업이 실행 중입니다.")
            return
        start = self._range_start.value()
        end = self._range_end.value() or self._metadata.duration_seconds
        if not 0 <= start < end <= self._metadata.duration_seconds + 1e-6:
            self._show_error("움직임 탐색 범위가 영상 길이를 벗어났습니다.")
            return

        self._open_plan_part(1)
        self._motion_range_changed = False
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
            self._motion_help.setText(self.MOTION_HELP_FINE_DETAIL)
            self._last_progress_message = "작은 접촉 변화 찾는 중"
        elif profile is MotionScanProfile.REPEATED_SUBTLE:
            self._motion_help.setText(self.MOTION_HELP_REPEATED)
            self._last_progress_message = "반복 변화 찾는 중"
        else:
            self._motion_help.setText(self.MOTION_HELP_GENERAL)
            self._last_progress_message = "움직임 구간 찾는 중"
        self._motion_scan_state = "scanning"
        self._refresh_motion_empty_state()
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
                else "큰 움직임 구간"
            )
            message = (
                f"{profile_text} {len(result.proposals)}개, 상위 {min(3, len(result.proposals))}개 선택"
            )
        else:
            message = "움직임 구간 없음"
        self._motion_list.blockSignals(False)
        self._motion_scan_state = "found" if result.proposals else "none_found"
        self._refresh_motion_selection_display()
        self._refresh_motion_empty_state()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(100)
        self._progress_label.setText(message)

    def _on_motion_scan_error(self, message: str) -> None:
        if self._active_motion_scan_id is None:
            return
        self._active_motion_scan_id = None
        self._motion_scan_state = "idle"
        self._refresh_motion_empty_state()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_label.setText("움직임 탐색 오류")
        self._show_error(message)

    def _on_motion_scan_canceled(self) -> None:
        self._active_motion_scan_id = None
        self._motion_scan_state = "idle"
        self._refresh_motion_empty_state()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_label.setText("움직임 탐색 취소됨")

    def _on_motion_scan_finished(self) -> None:
        self._elapsed_timer.stop()
        self._analysis_started_at = None
        self._motion_worker = None
        self._set_analysis_running(False)
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
                "한 계획에는 움직임 구간을 최대 3개까지 선택할 수 있습니다.",
                5000,
            )
        self._refresh_motion_selection_display()
        # A plan made from other candidates no longer matches the checks: make it again.
        if self._session.plan is not None and self._session.state is not WorkflowState.RUNNING:
            self._inputs_changed()

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
            f"선택 {len(proposals)}/3"
        )

    def _sync_motion_selection(self) -> bool:
        """Plans use the checked motion candidates; there is no separate apply step."""
        checked = tuple(
            sorted(self._checked_motion_proposals(), key=lambda item: (item.start_sec, item.end_sec))
        )
        if checked == self._applied_motion_proposals:
            return True
        if checked:
            return self._apply_motion_proposal(announce=False)
        # Every candidate was unchecked: sample the range the user had set, evenly.
        self._sampling_interval_override = None
        self._sampling_timestamps_override = ()
        self._sampling_strategy_override = None
        self._applied_motion_proposals = ()
        self._inputs_changed()
        return True

    def _apply_motion_proposal(self, *, announce: bool = True) -> bool:
        proposals = self._checked_motion_proposals()
        if not proposals:
            self._show_error("먼저 움직임 구간을 선택해 주세요.")
            return False
        if len(proposals) > 3:
            self._show_error("한 계획에는 움직임 구간을 최대 3개까지 선택할 수 있습니다.")
            return False
        if any(proposal.video_id != self._video_id for proposal in proposals):
            self._show_error("현재 영상과 다른 움직임 구간입니다. 다시 탐색해 주세요.")
            return False
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
            return False

        # The entered range stays as typed; the plan takes the checked ranges' span
        # (see _intent_range).
        gaps = tuple(later - earlier for earlier, later in zip(timestamps, timestamps[1:]))
        self._sampling_interval_override = min(gaps, default=0.25)
        self._sampling_timestamps_override = timestamps
        self._sampling_strategy_override = sampling_strategy
        self._applied_motion_proposals = proposals
        self._inputs_changed()
        position_ms = seconds_to_milliseconds(proposals[0].start_sec)
        self._player.setPosition(min(position_ms, self._duration_ms))
        if announce:
            self.statusBar().showMessage(
                f"선택한 움직임 구간 {len(proposals)}개와 총 {len(timestamps)}개 원본 시각을 "
                f"{sampling_strategy.value} 전략으로 적용했습니다. "
                "새 계획을 만들고 다시 승인해 주세요.",
                7000,
            )
        return True

    def _clear_motion_proposals(self) -> None:
        self._motion_list.blockSignals(True)
        self._motion_list.clear()
        self._motion_list.blockSignals(False)
        self._motion_scan_state = "idle"
        self._refresh_motion_selection_display()
        self._refresh_motion_empty_state()

    def _refresh_motion_empty_state(self) -> None:
        """Short text instead of empty candidate lists; the lists return once found."""
        found = self._motion_list.count() > 0
        if found:
            text = ""
        elif not self._case_videos:
            text = self.MOTION_EMPTY_NO_VIDEO
        elif self._motion_scan_state == "scanning":
            text = self.MOTION_EMPTY_SCANNING
        elif self._motion_scan_state == "none_found":
            text = self.MOTION_EMPTY_NONE_FOUND
        elif self._motion_range_changed:
            text = self.MOTION_EMPTY_RANGE_CHANGED
        else:
            text = self.MOTION_EMPTY_NOT_SCANNED
        self._motion_empty_label.setText(text)
        # Before a scan there is nothing to say; the button itself is the next step.
        self._motion_empty_label.setVisible(not found and bool(text))
        for widget in (self._motion_header, self._motion_list, self._motion_selection_label):
            widget.setVisible(found)
        self._refresh_next_action()

    def _toggle_playback(self) -> None:
        if self._player.playbackState() is QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _go_to_playback_start(self) -> None:
        """Back to the start of the candidate range being played, else of the video."""
        if self._playback_range is not None:
            start_ms, end_ms = self._playback_range
            self._playback_stop_ms = end_ms
            self._player.setPosition(start_ms)
        else:
            self._player.setPosition(0)

    def _apply_playback_rate(self) -> None:
        rate = self._speed_combo.currentData()
        self._player.setPlaybackRate(float(rate) if rate else 1.0)

    def _slider_pressed(self) -> None:
        self._seeking = True
        self._playback_stop_ms = None
        self._playback_range = None

    def _slider_released(self) -> None:
        self._player.setPosition(self._timeline.value())
        self._seeking = False

    def _on_duration_changed(self, duration_ms: int) -> None:
        self._duration_ms = max(0, duration_ms)
        self._timeline.setRange(0, self._duration_ms)
        self._refresh_timeline_marks()
        self._set_player_controls_enabled(self._duration_ms > 0)
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
        theme.set_icon(self._play_button, "pause" if playing else "play")

    def _skip_playback(self, seconds: int) -> None:
        position = self._player.position() + seconds * 1000
        self._player.setPosition(max(0, min(position, self._duration_ms)))

    def _set_player_controls_enabled(self, enabled: bool) -> None:
        for widget in (
            self._start_button,
            self._back_button,
            self._play_button,
            self._forward_button,
            self._speed_combo,
        ):
            widget.setEnabled(enabled)

    def _refresh_attention(self) -> None:
        """Videos that still need a file or a checked clock, shown at the bottom left."""
        items = []
        for video_id, path in self._case_videos.items():
            name = path.name if path is not None else self._camera_name(video_id)
            if path is None:
                items.append(f"{name} 파일 연결 필요")
            elif self._link_for(video_id).clock_certainty is InformationCertainty.UNKNOWN:
                items.append(f"{name} 기준 시각 미확인")
        self._attention_label.setVisible(bool(items))
        if not items:
            return
        more = f" 외 {len(items) - 1}건" if len(items) > 1 else ""
        self._attention_label.setText(f"확인 필요 {len(items)}건  {items[0]}{more}")
        self._attention_label.setToolTip("\n".join(items))

    def _refresh_case_header(self) -> None:
        title = self._case_panel.title_edit.text().strip() or self._case_record.title
        self._case_title_label.setText(title)
        try:
            edited = self._case_content(
                self._case_from_panel(self._case_record.revision)
            ) != self._case_content(self._case_record)
        except ValueError:
            edited = True
        if not self._case_saved:
            state = "저장 안 됨"
        elif edited:
            state = "저장 안 된 수정 있음"
        else:
            state = f"저장됨 v{self._case_record.revision}"
        self._case_state_label.setText(state)

    def _refresh_video_placeholder(self) -> None:
        open_video = self._video_path is not None
        self._video_stack.setCurrentIndex(1 if open_video else 0)
        self._video_placeholder_text.setText(
            "목록에서 재생할 영상을 고르세요" if self._case_videos else "CCTV 영상을 추가하세요"
        )

    def _on_media_error(self, error: QMediaPlayer.Error, error_string: str) -> None:
        if error is QMediaPlayer.Error.NoError:
            return
        self._duration_ms = 0
        self._play_button.setEnabled(False)
        self._invalidate_plan_display()
        detail = error_string or "지원되지 않거나 손상된 영상일 수 있습니다."
        self._show_error(f"영상을 재생할 수 없습니다: {detail}")

    def _analysis_choice_changed(self) -> None:
        mode = self._current_mode()
        if mode is not self._last_mode:
            self._external_vision_confirm.setChecked(False)
            self._last_mode = mode
        self._update_external_controls()
        self._apply_ui_zoom()
        self._update_mode_banner()
        self._inputs_changed()
        self._persist_settings()

    def _current_mode(self) -> AnalysisMode:
        return self._analysis_choice.mode()

    def _model_id(self) -> str:
        """The model behind the current choice (typed name for the external AI)."""
        mode = self._current_mode()
        if mode is AnalysisMode.EXTERNAL:
            return self._external_model_edit.text().strip()
        return self._analysis_choice.model_id() if mode is AnalysisMode.LOCAL else ""

    def _select_mode(self, mode: AnalysisMode) -> None:
        """Choose a mode the way a user would (local: this computer's usual model)."""
        self._analysis_choice.blockSignals(True)
        self._refresh_analysis_choices()
        self._analysis_choice.blockSignals(False)
        self._analysis_choice.select(
            mode, self._preferred_local_model() if mode is AnalysisMode.LOCAL else ""
        )

    def _set_model_id(self, model_id: str) -> None:
        if self._current_mode() is AnalysisMode.EXTERNAL:
            self._external_model_edit.setText(model_id)
        else:
            self._analysis_choice.select(AnalysisMode.LOCAL, model_id)

    def _model_changed(self, *_args: object) -> None:
        if self._current_mode() is AnalysisMode.EXTERNAL:
            self._external_vision_confirm.setChecked(False)
        self._inputs_changed()

    def _external_credential_changed(self, *_args: object) -> None:
        self._update_mode_banner()
        self._update_post_run_buttons()

    def _update_external_controls(self) -> None:
        external = self._current_mode() is AnalysisMode.EXTERNAL
        form = self._purpose_form_layout
        for widget in (
            self._external_model_edit,
            self._external_api_key_edit,
            self._external_vision_confirm,
        ):
            form.setRowVisible(widget, external)
        self._refresh_next_action()
        editable = external and not self._background_work_running()
        for widget in (
            self._external_model_edit,
            self._external_api_key_edit,
            self._external_vision_confirm,
        ):
            widget.setEnabled(editable)

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

    def _local_vision_models(self) -> tuple[str, ...]:
        """Image-capable models that run on this computer (cloud models left out)."""
        return tuple(
            item
            for item in self._runtime_inventory.vision_ollama_models
            if not self._model_display_name(item)[1]
        )

    @staticmethod
    def _parameter_billions(model_id: str) -> float | None:
        tag = model_id.partition(":")[2]
        size = re.search(r"(?<![\w.])(\d+(?:\.\d+)?)b(?![a-z])", tag)
        return float(size.group(1)) if size else None

    def _local_choice_labels(self) -> dict[str, str]:
        """Plain names for the installed models: 빠른 / 보통 / 정확한 분석 by size."""
        models = self._local_vision_models()
        sizes = {item: self._parameter_billions(item) for item in models}
        known = sorted({size for size in sizes.values() if size is not None})
        names = {}
        for item in models:
            size = sizes[item]
            if len(known) < 2 or size is None:
                names[item] = "AI 분석"
            elif size == known[0]:
                names[item] = "빠른 분석"
            elif size == known[-1]:
                names[item] = "정확한 분석"
            else:
                names[item] = "보통 분석"
        # Two models of the same size keep apart by a number.
        seen: dict[str, int] = {}
        for item in models:
            label = names[item]
            seen[label] = seen.get(label, 0) + 1
            if seen[label] > 1:
                names[item] = f"{label} {seen[label]}"
        return names

    def _refresh_analysis_choices(self) -> None:
        labels = self._local_choice_labels()
        order = sorted(labels, key=lambda item: (self._parameter_billions(item) or 0, item))
        self._analysis_choice.set_local_models(
            [(item, labels[item]) for item in order],
            "AI 분석 (설치된 프로그램 없음)",
        )

    def _refresh_local_models(self) -> None:
        """Look for installed programs again (after one is installed while the app is open)."""
        if self._background_work_running():
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self._runtime_inventory = RuntimeInventory.detect()
        finally:
            QApplication.restoreOverrideCursor()
        self._analysis_choice.blockSignals(True)
        self._refresh_analysis_choices()
        if self._current_mode() is AnalysisMode.LOCAL:
            self._analysis_choice.select(AnalysisMode.LOCAL, self._preferred_local_model())
        self._analysis_choice.blockSignals(False)
        self._analysis_choice_changed()
        count = len(self._local_vision_models())
        self.statusBar().showMessage(f"이 컴퓨터에서 쓸 수 있는 AI 분석 {count}가지", 5000)

    def _preferred_local_model(self) -> str:
        configured = (os.environ.get("VIDEO_AGENT_LOCAL_MODEL") or "").strip()
        if configured:
            return configured
        saved = (self._settings.local_model_id or "").strip()
        confirmed = self._local_vision_models()
        # The largest model ("정확한 분석"), preferring instruct models: thinking tags
        # have returned no final answer for structured results.
        instruct_models = tuple(item for item in confirmed if "instruct" in item.lower())
        candidates = instruct_models or confirmed
        if candidates:
            return max(candidates, key=lambda item: (self._parameter_billions(item) or 0, item.lower()))
        return saved

    def _intent_range(self) -> tuple[float, float | None]:
        """The checked motion ranges' span when some are checked, else the entered range."""
        if self._applied_motion_proposals:
            return (
                min(item.start_sec for item in self._applied_motion_proposals),
                max(item.end_sec for item in self._applied_motion_proposals),
            )
        return self._range_start.value(), self._range_end.value() or None

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
            model_id=self._model_id() or None,
            range_start_sec=self._intent_range()[0],
            range_end_sec=self._intent_range()[1],
            sampling_interval_sec=self._sampling_interval_override,
            sample_timestamps_sec=self._sampling_timestamps_override,
            provider_id=provider_id,
            provider_endpoint=endpoint,
            sampling_strategy=self._sampling_strategy_override,
            case_id=self._case_record.case_id,
            case_revision=self._case_record.revision,
            analysis_clues=self._current_case_clues(for_analysis=True),
            focus_situations=self._focus_section.focus(),
            # Search hints are entered as clues now (older hints load as clues).
            focus_hints=(),
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
        # Ranges found in the old span no longer fit; say so instead of an empty table.
        self._motion_range_changed = self._motion_list.count() > 0 or self._motion_range_changed
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
        if self._expansion_context not in (None, (self._metadata, self._session.intent)):
            self._hide_range_expansion()
        self._invalidate_plan_display()
        self._update_mode_banner()
        self._persist_settings()

    def _invalidate_plan_display(self) -> None:
        self._plan_view.clear()
        self._question_label.setText("")
        self._question_label.setVisible(False)
        self._clarification_edit.clear()
        self._clarification_edit.setVisible(False)
        self._clarification_edit.setEnabled(True)
        self._clarification_id = None
        self._approve_button.setEnabled(False)
        self._transfer_button.setEnabled(False)
        self._run_button.setEnabled(False)

    def _make_plan(self) -> None:
        if self._metadata is None or self._video_path is None:
            self._show_error("영상 정보를 확인할 수 있는 영상을 먼저 선택해 주세요.")
            return
        if not self._purpose_edit.toPlainText().strip():
            self._show_error("분석 목적을 입력해 주세요.")
            return
        mode = self._current_mode()
        model_id = self._model_id() or None
        if mode is AnalysisMode.LOCAL and model_id and self._model_display_name(model_id)[1]:
            self._show_error(
                "선택한 분석 프로그램은 외부 서버에서 실행되어 이 컴퓨터 안에서 분석할 수 없습니다. "
                "분석 방식을 다시 고르세요."
            )
            return
        if mode is AnalysisMode.LOCAL and not self._runtime_inventory.confirms_vision(model_id):
            if model_id:
                self._show_error(
                    "선택한 분석 방식을 이 컴퓨터에서 쓸 수 없습니다. "
                    "분석 방식 옆 새로고침 단추를 누르거나 다른 분석 방식을 고르세요."
                )
            else:
                self._show_error(
                    "이 컴퓨터에 AI 분석 프로그램이 없습니다. 담당자에게 설치를 요청하세요."
                )
            return
        if mode is AnalysisMode.EXTERNAL and not self._external_vision_confirmed():
            self._show_error(
                "이 외부 AI가 사진을 읽을 수 있는지 확인한 뒤 "
                "'사진 입력' 항목을 체크해 주세요."
            )
            return
        if not self._sync_motion_selection():
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
                self._question_label.setVisible(True)
                self._clarification_edit.setVisible(True)
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
            sampling = "프레임을 뽑지 않음 (시연용 결과)"
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
                f"움직임 구간 {len(self._applied_motion_proposals)}개에서 원본 시각 "
                f"{len(plan.sample_timestamps_sec)}개 ({strategy_text}, "
                f"최소 {plan.sampling_interval_sec:.3f}초 간격)\n"
                f"등록된 분석 구간:\n{proposal_ranges}\n"
                "구간 수와 결과 후보 수는 다를 수 있음"
            )
            if (
                plan.mode is AnalysisMode.LOCAL
                and plan.sampling_strategy is SamplingStrategy.TEMPORAL_SEQUENCE
                and not fine_detail.required
            ):
                sampling += "\n프레임 4개씩 2×2 비교 화면으로 묶어 분석"
        elif plan.sampling_strategy is SamplingStrategy.TEMPORAL_SEQUENCE:
            fine_detail = fine_detail_requirement(plan.purpose)
            sampling = (
                f"반복 변화 비교용 원본 시각 {len(plan.sample_timestamps_sec)}개 "
                f"(약 {plan.sampling_interval_sec:.3f}초 간격)"
            )
            if plan.mode is AnalysisMode.LOCAL and not fine_detail.required:
                sampling += "\n프레임 4개씩 2×2 비교 화면으로 묶어 분석"
        else:
            sampling = (
                f"원본 시각 {len(plan.sample_timestamps_sec)}개를 고르게 선택 "
                f"(약 {plan.sampling_interval_sec:.3f}초 간격)"
            )
        if (
            plan.mode is AnalysisMode.LOCAL
            and fine_detail_requirement(plan.purpose).required
        ):
            sampling += (
                "\n작은 접촉 정밀 입력: 최대 1920px 원본에서 각 시각별 "
                "전체 화면 보기 + 중앙 60% 확대 보기(1280×1080)를 모델에 전달"
            )
        case_title = self._case_panel.title_edit.text().strip() or self._case_record.title
        mode_text = {
            AnalysisMode.SAMPLE: SAMPLE_LABEL,
            AnalysisMode.LOCAL: f"{self._choice_label(plan.model_id)} (이 컴퓨터에서 처리)",
            AnalysisMode.EXTERNAL: f"외부 AI 분석 · {plan.model_id}",
        }[plan.mode]
        video_name = self._video_path.name if self._video_path is not None else plan.video_id
        if self._applied_motion_proposals:
            ranges = ", ".join(
                f"{format_time(item.start_sec)}–{format_time(item.end_sec)}"
                for item in self._applied_motion_proposals
            )
            where = f"움직임 구간 {len(self._applied_motion_proposals)}개: {ranges}"
        else:
            where = f"{format_time(plan.range_start_sec)}–{format_time(plan.range_end_sec)}"
        limits = plan.limits
        focus = self._focus_section.focus()
        frames = (
            "프레임을 뽑지 않음 (시연용)"
            if plan.mode is AnalysisMode.SAMPLE
            else f"프레임 {len(plan.sample_timestamps_sec)}장"
        )
        # What the officer needs to approve: what, where, with which clues, and how.
        rows = [
            ("찾을 장면", plan.purpose),
            ("볼 영상", f"{video_name}\n{where}"),
            (
                "우선 확인",
                ", ".join(SITUATION_LABELS[item] for item in focus) if focus else "선택 없음",
            ),
            (
                "사용 단서",
                "\n".join(
                    f"{item.text} (출처: {SOURCE_LABELS[item.source]})"
                    for item in plan.analysis_clues
                )
                + "\n영상에서 확인한 사실이 아닙니다."
                if plan.analysis_clues
                else "없음",
            ),
            ("분석 방식", f"{mode_text} · {frames}"),
        ]
        # The rest stays one click away under "세부 조건".
        details = [
            ("사건", f"{case_title} (사건 정보 v{plan.case_revision})" if plan.case_revision else case_title),
            ("표본", sampling),
            (
                "한도",
                f"최대 {limits.max_runtime_seconds:g}초 · 프레임 {limits.max_frames}개 · "
                f"모델 요청 {limits.max_provider_requests}회(요청당 {limits.max_frames_per_request}프레임) · "
                f"자동 재분석 {limits.max_reanalysis_attempts}회",
            ),
            ("범위 확대", "필요하면 자동 실행하지 않고 제안만 합니다"),
            ("우선 확인 방식", "선택한 상황은 확인 순서에만 반영하고, 선택하지 않은 상황도 분석합니다."),
        ]
        if plan.observation_guidance:
            details.append(("관찰 기준", " / ".join(plan.observation_guidance)))
        if plan.transfer_manifest:
            # Anything leaving this computer is always shown, never folded away.
            manifest = plan.transfer_manifest
            times = ", ".join(f"{item:.3f}s" for item in manifest.frame_timestamps_sec)
            rows.extend(
                [
                    ("외부 전송", "분석 승인과 별도로 승인해야 합니다"),
                    ("전송 대상", f"{manifest.provider_id} · {manifest.endpoint_origin}"),
                    (
                        "전송 내용",
                        f"분석 목적·관찰 기준 텍스트 + JPEG 프레임 {len(manifest.frame_timestamps_sec)}개 ({times})",
                    ),
                    (
                        "전송 사전 정보",
                        " / ".join(item.text for item in manifest.analysis_clues)
                        if manifest.analysis_clues
                        else "없음",
                    ),
                    ("전송하지 않음", "PDF·원본 영상 전체·라벨 JSON"),
                ]
            )
        self._plan_details_view.setHtml(theme.label_value_table(details))
        self._plan_details_toggle.setChecked(False)
        self._plan_view.setHtml(theme.label_value_table(rows))
        self._clarification_edit.setEnabled(False)
        self._approve_button.setEnabled(True)
        self._transfer_button.setEnabled(False)
        self._run_button.setEnabled(False)
        self.statusBar().showMessage(
            "계획을 확인한 뒤 분석 계획을 승인해 주세요."
            if plan.mode is AnalysisMode.EXTERNAL
            else "계획을 확인한 뒤 [승인하고 실행]을 눌러 주세요.",
            5000,
        )

    def _approve_and_run(self) -> None:
        """Approving the plan shown is the click that starts it, except for external AI,
        whose separate transfer approval still comes between."""
        external = self._session.plan is not None and self._session.plan.mode is AnalysisMode.EXTERNAL
        self._approve_plan()
        if not external and self._run_button.isEnabled():
            self._start_analysis()

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
        self._focus_section.set_editing_enabled(not running)
        self._choose_button.setEnabled(not running and not self._metadata_probe_running())
        self._video_list.set_locked(running or self._metadata_probe_running())
        self._refresh_video_list()
        self._motion_scan_button.setEnabled(not running and self._metadata is not None)
        self._motion_list.setEnabled(not running)
        for widget in (
            self._purpose_edit,
            self._analysis_choice,
            self._model_refresh_button,
            self._external_model_edit,
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
            self._last_progress_message = "움직임 구간 찾기 취소 중"
            self._refresh_elapsed_status()
            self.statusBar().showMessage("움직임 탐색을 취소하고 있습니다.", 5000)
            return
        if self._worker is None or not self._worker.isRunning():
            self.statusBar().showMessage("취소할 작업이 없습니다.", 4000)
            return
        self._session.cancel_active()
        self._worker.request_cancel()
        self._cancel_button.setEnabled(False)
        self._last_progress_message = "취소 중 (정리가 끝나면 다시 실행할 수 있음)"
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
                ResultOrigin.SAMPLE: "시연용 결과 (실제 AI 분석 아님)",
                ResultOrigin.TEST_DOUBLE: "테스트 응답 (실제 모델 아님)",
                ResultOrigin.REAL_MODEL: "AI 분석",
            }[job.origin]
            elapsed = f" · {job.elapsed_seconds:.1f}초" if job.elapsed_seconds is not None else ""
            outcome = f"후보 {len(job.segments)}개" if job.segments else "후보 없음"
            self._progress_label.setText(f"완료 · {outcome} · {origin_text}{elapsed}")
            self._set_result_banner(job.origin, job.model_id)
            self._display_segments(job)
            self._refresh_saved_results(select_job_id=job.job_id)
            self._propose_range_expansion(job)
            self.statusBar().showMessage(
                f"분석이 끝났습니다. [{self.STEP_TITLES[2]}] 단계에서 후보를 검토하세요.", 10000
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
            self._set_terminal_banner("AI 분석 실패", job.model_id, failed=True)
            self._review_panel.show_message(
                f"분석 실패: {job.model_id or '모델 없음'}, "
                f"{job.error or '알 수 없는 분석 오류'}"
            )
            self._refresh_review_empty_state()
            self._show_error(job.error or "알 수 없는 분석 오류가 발생했습니다.")

    def _on_worker_finished(self) -> None:
        self._elapsed_timer.stop()
        self._analysis_started_at = None
        self._set_analysis_running(False)
        self._cancel_button.setEnabled(False)
        self._worker = None
        self._update_post_run_buttons()
        self._refresh_video_list()

    def _show_step(self, index: int) -> None:
        index = max(0, min(index, len(self.STEP_TITLES) - 1))
        self._step_stack.setCurrentIndex(index)
        self._step_buttons[index].setChecked(True)
        # Videos are added and linked in step ①; later steps only choose among them.
        self._video_list.set_select_only(index != 0)
        self._case_panel.save_button.setVisible(index == 0)
        previous_exists = index > 0
        next_exists = index < len(self.STEP_TITLES) - 1
        self._prev_step_button.setEnabled(previous_exists)
        self._prev_step_button.setVisible(previous_exists)
        self._prev_step_button.setText("이전")
        self._prev_step_button.setToolTip(
            f"{self.STEP_TITLES[index - 1]} 단계로" if previous_exists else ""
        )
        self._next_step_button.setEnabled(next_exists)
        self._next_step_button.setVisible(next_exists)
        self._next_step_button.setText(
            self.STEP_TITLES[index + 1] if next_exists else "다음"
        )
        self._step_navigation.adjustSize()
        self._refresh_review_empty_state()
        self._refresh_step_emphasis()

    def _refresh_review_empty_state(self) -> None:
        has_job = self._review_panel.job is not None
        has_content = has_job or self._result_list.count() > 0
        self._review_empty_label.setVisible(not has_job)
        self._review_panel.setVisible(has_content)
        self._review_panel.completion_bar.setVisible(has_job)
        # The trailing stretch keeps the empty state at the top; with a result the
        # review panel takes the spare height instead.
        self._result_layout.setStretch(self._result_layout.count() - 1, 0 if has_content else 1)

    def _display_segments(self, job: AnalysisJob) -> None:
        self._hide_range_expansion()
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
        self._refresh_timeline_marks()
        self._update_saved_results_controls()

    def _expansion_reason(self, job: AnalysisJob) -> str | None:
        """Why the finished run could be widened, or None when widening would not help."""
        if (
            job.status is not JobStatus.COMPLETED
            or job.segments
            or job.origin is ResultOrigin.SAMPLE
            or self._metadata is None
            or job.video_id != self._video_id
        ):
            return None
        duration = self._metadata.duration_seconds
        start = job.range_start_sec or 0.0
        end = job.range_end_sec if job.range_end_sec is not None else duration
        narrowed = start > 0.05 or end < duration - 0.05

        def clock(seconds: float) -> str:
            return format_milliseconds(seconds_to_milliseconds(seconds))

        if self._applied_motion_proposals:
            found = (
                f"선택한 움직임 구간 {len(self._applied_motion_proposals)}개"
                f"({clock(start)}–{clock(end)})에 프레임을 모아 분석했지만 후보를 찾지 못했습니다."
            )
        elif narrowed:
            found = (
                f"분석 범위 {clock(start)}–{clock(end)}(영상 {clock(duration)} 중 "
                f"{clock(end - start)})에서 후보를 찾지 못했습니다."
            )
        else:
            return None
        return (
            f"{found}\n영상 전체로 넓혀 다시 계획할 수 있습니다. 프레임 수 한도는 같으므로 "
            "넓은 범위에서는 프레임 사이 간격이 넓어집니다."
        )

    def _propose_range_expansion(self, job: AnalysisJob) -> None:
        reason = self._expansion_reason(job)
        if reason is None:
            return
        self._session.propose_expansion(reason)
        self._expansion_context = (self._metadata, self._session.intent)
        self._expansion_label.setText(reason)
        self._expansion_frame.setVisible(True)

    def _hide_range_expansion(self) -> None:
        self._expansion_context = None
        self._expansion_frame.setVisible(False)

    def _apply_range_expansion(self) -> None:
        """Widen to the whole video; the new plan still needs its own approval."""
        if self._background_work_running() or self._metadata is None:
            return
        self._hide_range_expansion()
        for spin in (self._range_start, self._range_end):
            spin.blockSignals(True)
            spin.setValue(0)
            spin.blockSignals(False)
        self._range_changed()
        self._update_post_run_buttons()
        self._show_step(1)
        self.statusBar().showMessage(
            "분석 범위를 영상 전체로 바꿨습니다. 계획을 다시 만들고 승인해야 실행됩니다.", 10000
        )

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
        self._refresh_next_action()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt API
        if event.type() == QEvent.Type.EnabledChange and watched in self._next_action_buttons:
            self._refresh_next_action()
        return super().eventFilter(watched, event)

    def _refresh_next_action(self) -> None:
        """Step ②: plan, approval and run share one place, and only the next action is blue."""
        state = self._session.state
        approved = state in (
            WorkflowState.ANALYSIS_APPROVED,
            WorkflowState.APPROVED,
            WorkflowState.RUNNING,
            WorkflowState.VALIDATING,
        )
        finished = state in (
            WorkflowState.COMPLETED,
            WorkflowState.PARTIAL,
            WorkflowState.EXPANSION_PROPOSED,
        )
        planned = finished or approved or state is WorkflowState.PLAN_READY
        scanned = self._motion_scan_state in ("found", "none_found")
        # Plan, approve and run take turns in the pinned bar.
        self._make_plan_button.setVisible(not approved and state is not WorkflowState.PLAN_READY)
        self._approve_button.setVisible(state is WorkflowState.PLAN_READY)
        # Approving starts the run, except for external AI (its transfer approval comes next).
        external = self._current_mode() is AnalysisMode.EXTERNAL
        self._approve_button.setText("분석 계획 승인" if external else "승인하고 실행")
        self._approve_button.setToolTip(
            "계획을 승인합니다. 외부 전송은 따로 승인합니다."
            if external
            else "위 계획을 승인하고 바로 분석을 시작합니다."
        )
        self._run_button.setVisible(approved)
        # External mode: the separate transfer approval appears at its turn, after approval.
        self._transfer_button.setVisible(
            self._current_mode() is AnalysisMode.EXTERNAL
            and state is WorkflowState.ANALYSIS_APPROVED
        )

        next_button: QPushButton | None = None
        if self._run_button.isEnabled() and approved:
            next_button = self._run_button
        elif self._transfer_button.isEnabled() and self._current_mode() is AnalysisMode.EXTERNAL:
            next_button = self._transfer_button
        elif self._approve_button.isEnabled():
            next_button = self._approve_button
        elif self._make_plan_button.isEnabled() and not planned:
            if self._motion_scan_state == "scanning":
                next_button = None
            elif not scanned and self._motion_scan_button.isEnabled():
                next_button = self._motion_scan_button
            else:
                next_button = self._make_plan_button
        # The single blue button (plan, approve, run in turn) shows what to do next.
        for button in self._next_action_buttons:
            theme.set_primary(button, button is next_button)
        self._refresh_plan_parts(planned)
        self._refresh_step_emphasis()

    # ----- step ② parts -------------------------------------------------------------

    def _open_plan_part(self, index: int) -> None:
        """Open one numbered part of the step ② card and fold the others."""
        for number, part in enumerate(self._plan_parts):
            part.set_open(number == index)

    def _toggle_plan_part(self, index: int) -> None:
        """Header click: › opens that part (folding the others), ⌄ folds it again."""
        if self._plan_parts[index].is_open():
            self._plan_parts[index].set_open(False)
        else:
            self._open_plan_part(index)

    def _show_motion_help(self) -> None:
        text = "\n".join(
            item
            for item in (
                self._motion_help.text(),
                self._motion_note.text(),
                "직접 시작·종료를 입력해도 됩니다. 비워 두면 영상 전체를 봅니다.",
            )
            if item
        )
        InfoPopover("움직임 구간 안내", text, self._motion_info_button).show_below(
            self._motion_info_button
        )

    def _refresh_plan_parts(self, planned: bool) -> None:
        """One-line summaries for folded parts; the plan part opens when a plan is made."""
        find_part, range_part, plan_part = self._plan_parts
        purpose = " ".join(self._purpose_edit.toPlainText().split())
        if len(purpose) > 28:
            purpose = purpose[:27] + "…"
        focus = len(self._focus_section.focus())
        find_part.set_summary(
            f"{purpose or '목적 미입력'} · " + (f"상황 {focus}개" if focus else "상황 선택 없음")
        )
        checked = self._checked_motion_proposals()
        if checked:
            first = min(item.start_sec for item in checked)
            last = max(item.end_sec for item in checked)
            range_part.set_summary(f"움직임 구간 {len(checked)}개 · {format_time(first)}–{format_time(last)}")
        else:
            start_value, end_value = self._range_start.value(), self._range_end.value()
            range_part.set_summary(
                "영상 전체"
                if not start_value and not end_value
                else f"{format_time(start_value)}–{format_time(end_value) if end_value else '영상 끝'}"
            )
        state = self._session.state
        if state in (WorkflowState.COMPLETED, WorkflowState.PARTIAL, WorkflowState.EXPANSION_PROPOSED):
            status = "분석 끝남"
        elif state in (WorkflowState.ANALYSIS_APPROVED, WorkflowState.APPROVED):
            status = "승인됨 · 실행 전"
        elif state in (WorkflowState.RUNNING, WorkflowState.VALIDATING):
            status = "실행 중"
        elif state is WorkflowState.PLAN_READY:
            status = "계획 확인 전"
        else:
            status = "계획 없음"
        plan_part.set_summary(f"{status} · {self._analysis_choice.currentText()}")
        has_plan = planned and bool(self._plan_view.toPlainText().strip())
        self._plan_view.setVisible(has_plan)
        self._plan_details_toggle.setVisible(has_plan)
        if not has_plan:
            self._plan_details_toggle.setChecked(False)
        if planned and not self._plan_was_ready:
            self._open_plan_part(2)
        self._plan_was_ready = planned
        # Progress shows only while something runs or has just finished.
        running = self._cancel_button.isEnabled()
        message = self._progress_label.text().strip()
        self._progress_bar.setVisible(running)
        self._cancel_button.setVisible(running)
        self._progress_label.setVisible(running or message not in ("", "대기 중"))

    def _refresh_step_emphasis(self) -> None:
        """The bottom 'next step' button turns blue once the current step's work is done."""
        step = self._step_stack.currentIndex()
        has_videos = bool(self._case_videos)
        theme.set_primary(self._video_list.add_button, not has_videos)
        if step == 0:
            ready = has_videos
        else:
            ready = self._review_panel.job is not None or self._session.state in (
                WorkflowState.COMPLETED,
                WorkflowState.PARTIAL,
                WorkflowState.EXPANSION_PROPOSED,
            )
        theme.set_primary(self._next_step_button, ready)

    def _show_status(self, kind: str, text: str, detail: str = "") -> None:
        text_color, dot_color = theme.status_line_colors(kind)
        self._mode_banner.setText(text)
        self._mode_banner.setStyleSheet(f"color:{text_color};")
        self._mode_dot.setPixmap(theme.dot_pixmap(dot_color, 8))
        self._mode_banner.setToolTip(detail)
        self._mode_banner.setAccessibleDescription(detail)

    def _set_result_banner(self, origin: ResultOrigin, model_id: str | None) -> None:
        if origin is ResultOrigin.SAMPLE:
            self._show_status("sample", "시연용 결과 (실제 AI 분석 아님)")
        elif origin is ResultOrigin.TEST_DOUBLE:
            self._show_status("test_double", "테스트 응답 (실제 모델 검증 아님)")
        else:
            self._show_status("real_model", f"AI 분석 결과 · {self._choice_label(model_id)}", model_id or "")

    def _set_running_banner(self, mode: AnalysisMode, model_id: str | None) -> None:
        if mode is AnalysisMode.SAMPLE:
            text = "시연용 분석 실행 중 (실제 AI 분석 아님)"
        elif mode is AnalysisMode.EXTERNAL:
            text = "외부 AI 분석 실행 중"
        else:
            text = f"분석 실행 중 · {self._choice_label(model_id)}"
        self._show_status("running", text, model_id or "")

    def _set_terminal_banner(
        self,
        label: str,
        model_id: str | None,
        *,
        failed: bool,
    ) -> None:
        self._show_status("failed" if failed else "neutral", label, model_id or "")

    @staticmethod
    def _model_display_name(model_id: str) -> tuple[str, bool]:
        """Short name for the status line and whether the model runs in the cloud.

        "qwen3-vl:8b-instruct" -> ("Qwen3-VL 8B", False); "kimi-k3:cloud" -> ("Kimi-K3", True).
        """
        name, _, tag = model_id.strip().partition(":")
        cloud = tag == "cloud" or tag.endswith("-cloud") or name.endswith("-cloud")
        name = name.removesuffix("-cloud")
        words = [
            part.upper() if part.isalpha() and len(part) <= 3 else part[:1].upper() + part[1:]
            for part in name.split("-")
            if part
        ]
        label = "-".join(words)
        size = re.search(r"(?<![\w.])(\d+(?:\.\d+)?)b(?![a-z])", tag)
        if size:
            label = f"{label} {size.group(1)}B"
        return label or model_id, cloud

    def _choice_label(self, model_id: str | None) -> str:
        """Plain name of a local model's choice ("빠른 분석"), or "AI 분석" if unknown."""
        return self._local_choice_labels().get(model_id or "", "AI 분석")

    def _update_mode_banner(self) -> None:
        mode = self._current_mode()
        if mode is AnalysisMode.SAMPLE:
            self._set_result_banner(ResultOrigin.SAMPLE, None)
            return
        model_id = self._model_id()
        if mode is AnalysisMode.LOCAL:
            # Technical details (program and model names) stay in the tooltip for staff.
            if self._runtime_inventory.ollama_executable is None:
                runtime = "설치된 Ollama 런타임 없음"
            elif not self._runtime_inventory.installed_ollama_models:
                runtime = "설치 모델 확인 안 됨"
            else:
                confirmed = self._runtime_inventory.vision_ollama_models
                runtime = (
                    "확인된 비전 모델: " + ", ".join(confirmed)
                    if confirmed
                    else "설치 모델은 있으나 vision 기능 확인 안 됨"
                )
            detail = f"{runtime}\n로컬 연결이 실패해도 외부 API나 샘플로 자동 전환하지 않습니다."
            if not model_id:
                self._show_status("local", "AI 분석 프로그램 없음", detail)
                return
            if self._model_display_name(model_id)[1]:
                self._show_status(
                    "warning",
                    "외부 서버에서 실행되는 프로그램 · 이 컴퓨터 분석에 쓸 수 없음",
                    f"{model_id}: Ollama를 거쳐 외부 서버에서 실행되는 모델입니다.\n{detail}",
                )
            else:
                self._show_status(
                    "local",
                    f"{self._analysis_choice.currentText()} · 이 컴퓨터에서 처리",
                    f"{model_id}\n{detail}",
                )
        else:
            key_status = (
                "인증키 준비됨(저장 안 함)"
                if self._external_api_key_value() is not None
                else "인증키 필요"
            )
            detail = f"{model_id or '외부 AI 이름 필요'}\n{key_status}\n분석 승인과 외부 전송 승인을 따로 받습니다."
            self._show_status("warning", "외부 AI 분석 · 영상 일부를 외부로 보냄", detail)

    @classmethod
    def _segment_text(cls, segment: Segment, purpose: str | None = None) -> str:
        start = format_milliseconds(seconds_to_milliseconds(segment.start_sec))
        end = format_milliseconds(seconds_to_milliseconds(segment.end_sec))
        context_start = format_milliseconds(
            seconds_to_milliseconds(cls._segment_playback_start_sec(segment))
        )
        evidence = " / ".join(segment.evidence)
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
        rows = [
            ("요약", segment.summary),
            ("확인용 재생 범위", f"{start}–{end}"),
            (
                "재생 시작",
                f"{context_start} (확인용 범위 시작 최대 "
                f"{cls.RESULT_CONTEXT_PREROLL_SECONDS:g}초 전부터 이어서 재생)",
            ),
            ("근거", evidence),
        ]
        # Evidence lines built above are "label: value" lines too.
        for line in f"{observed}{duration_evidence}{temporal_evidence}".splitlines():
            if line:
                label, _, value = line.partition(": ")
                rows.append((label, value))
        rows.append(("불확실성", segment.uncertainty))
        return "\n".join(f"{label}: {value}" for label, value in rows)

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
        self._playback_range = None
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
        self._playback_range = (start_ms, end_ms)
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

    def _saved_result_text(self, stored: StoredResult) -> str:
        job = stored.job
        saved_at = datetime.fromtimestamp(stored.saved_at).strftime("%m-%d %H:%M")
        if job.mode is AnalysisMode.SAMPLE:
            mode = "시연용"
        elif job.mode is AnalysisMode.LOCAL:
            mode = self._choice_label(job.model_id)
        else:
            mode = "외부 AI 분석"
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
        # Needed only to switch to an earlier result, or to open one not shown yet.
        shown = self._review_panel.job
        not_shown = any(shown is None or job_id != shown.job_id for job_id in self._saved_results)
        self._saved_results_row.setVisible(
            len(self._saved_results) >= 2 or (has_results and not_shown)
        )

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
        # Typing still waiting for its automatic save is saved first; only a save that
        # failed is left to ask about.
        self._review_panel.flush_pending()
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
                    self._model_id() or None
                    if mode is AnalysisMode.LOCAL
                    else self._settings.local_model_id
                ),
                external_provider_id=(
                    os.environ.get("VIDEO_AGENT_EXTERNAL_PROVIDER")
                    or self._settings.external_provider_id
                    or self.OPENAI_PROVIDER_ID
                ),
                external_model_id=(
                    self._model_id() or None
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
