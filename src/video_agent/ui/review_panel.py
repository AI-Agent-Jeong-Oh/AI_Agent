from __future__ import annotations

import errno
import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QGridLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPlainTextEdit,
    QTextEdit,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from video_agent.contracts import (
    AnalysisJob,
    CandidateReview,
    ResultOrigin,
    ReviewJudgement,
    ReviewSession,
    Segment,
)
from video_agent.ui import theme
from video_agent.ui.message import confirm
from video_agent.ui.segment_detail import precise_text, tidy_seconds
from video_agent.ui.candidate_delegate import ROW_ROLE, CandidateDelegate, CandidateRow
from video_agent.ui.flow_layout import FlowLayout, FlowRow
from video_agent.ui.judgement_marks import JUDGEMENT_MARKS, MarkStyle, paint_mark
from video_agent.video.files import format_milliseconds, seconds_to_milliseconds


JUDGEMENT_LABELS = {
    ReviewJudgement.UNREVIEWED: "미검토",
    ReviewJudgement.RELEVANT: "관련 있음",
    ReviewJudgement.NEEDS_CHECK: "추가 확인",
    ReviewJudgement.EXCLUDED: "제외",
}

SEGMENT_INDEX_ROLE = Qt.ItemDataRole.UserRole + 1

FILTER_ALL = "all"
SORT_TIME = "time"
SORT_MODEL = "model"

# (label, seconds of context before/after the candidate range)
PLAYBACK_RANGES = (
    ("구간만", 0.0),
    ("전후 30초", 30.0),
    ("전후 2분", 120.0),
)


@dataclass(frozen=True, slots=True)
class ReviewRecord:
    judgement: ReviewJudgement = ReviewJudgement.UNREVIEWED
    memo: str = ""
    tags: tuple[str, ...] = ()
    bookmarked: bool = False


def parse_tags(text: str) -> tuple[str, ...]:
    tags: list[str] = []
    for raw in text.split(","):
        tag = raw.strip()
        if tag and tag not in tags:
            tags.append(tag)
    return tuple(tags)



def length_text(seconds: float) -> str:
    """Length of a candidate range, e.g. "2초" or "1분 5초"."""
    total = max(0, round(seconds))
    minutes, rest = divmod(total, 60)
    if minutes and rest:
        return f"{minutes}분 {rest}초"
    return f"{minutes}분" if minutes else f"{rest}초"

SHORTCUT_JUDGEMENTS = {
    Qt.Key.Key_1: ReviewJudgement.RELEVANT,
    Qt.Key.Key_2: ReviewJudgement.NEEDS_CHECK,
    Qt.Key.Key_3: ReviewJudgement.EXCLUDED,
    Qt.Key.Key_0: ReviewJudgement.UNREVIEWED,
}
SHORTCUT_HELP = "단축키(목록을 누른 뒤): 1 관련 있음 · 2 추가 확인 · 3 제외 · 0 미검토 · Space 재생 · ↑↓ 이동"


class _CompactListWidget(QListWidget):
    """Prefers its minimum height and grows only into spare space.

    While the list has focus, number keys set the judgement and Space plays or pauses;
    the memo and tag fields keep their keys, since these work only on the list.
    """

    judgement_key = Signal(object)
    play_key = Signal()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(super().sizeHint().width(), self.minimumHeight())

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        key = Qt.Key(event.key())
        if key in SHORTCUT_JUDGEMENTS and not event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier:
            self.judgement_key.emit(SHORTCUT_JUDGEMENTS[key])
            return
        if key == Qt.Key.Key_Space and not event.modifiers():
            self.play_key.emit()
            return
        super().keyPressEvent(event)


class _CompactTextView(QPlainTextEdit):
    """Prefers its minimum height and grows only into spare space."""

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(super().sizeHint().width(), self.minimumHeight())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(super().minimumSizeHint().width(), self.minimumHeight())


class _CompactRichView(QTextEdit):
    """Read-only rich text with the same sizing as _CompactTextView (for the detail grid)."""

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(super().sizeHint().width(), self.minimumHeight())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(super().minimumSizeHint().width(), self.minimumHeight())


def _pill_icon(judgement: ReviewJudgement, *, checked: bool) -> QIcon:
    """The small judgement mark inside a round judgement button (white when chosen)."""
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    style = JUDGEMENT_MARKS[judgement]
    if checked:
        style = MarkStyle(
            theme.SURFACE if not style.dashed else theme.PRIMARY_FILL,
            theme.SURFACE,
            dashed=style.dashed,
        )
    paint_mark(painter, QRectF(6, 6, 12, 12), style, radius=3.0)
    painter.end()
    return QIcon(pixmap)


class CandidateReviewPanel(QWidget):
    """Candidate list, selected-segment detail and reviewer notes for one analysis job.

    Reviewer records are human judgement only. They never modify the AnalysisJob and are
    never sent to an analyzer.
    """

    playback_range_requested = Signal(float, float)
    records_changed = Signal()
    # The selected candidate changed (or none is selected any more).
    selection_changed = Signal()
    play_toggle_requested = Signal()
    summary_requested = Signal()

    def __init__(
        self,
        detail_formatter: Callable[[Segment, str | None], str | Sequence[tuple[str, str]]],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__()
        self._detail_formatter = detail_formatter
        self._clock = clock
        # Human review time: counted only while a job with candidates is shown and the
        # app is active. Kept separate from the model's AnalysisJob.elapsed_seconds and
        # stored for later evaluation; it is not shown on screen.
        self._app_active = True
        self._total_seconds = 0.0
        self._segment_seconds: dict[int, float] = {}
        self._tracking_since: float | None = None
        self._tracked_index: int | None = None
        self._job: AnalysisJob | None = None
        self._session: ReviewSession | None = None
        self._persist: Callable[[ReviewSession], Path] | None = None
        self._save_blocked = False
        self._saved: dict[int, ReviewRecord] = {}
        self._drafts: dict[int, ReviewRecord] = {}
        # Records save themselves: a judgement at once, memo and tags once
        # typing pauses, and anything pending when another candidate is chosen.
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(1000)
        self._autosave_timer.timeout.connect(self.save_current)
        self._save_failed = False
        self._current_index: int | None = None
        self._loading_editor = False
        self._build_ui()
        self._connect_signals()
        self.complete_button.setEnabled(False)
        self.summary_button.setEnabled(False)
        self._show_editor_for(None)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        # 8-point spacing keeps the review step on one screen at a MacBook window height.
        layout.setSpacing(8)

        self.origin_label = QLabel("결과 없음")
        self.origin_label.setObjectName("reviewOrigin")
        self.origin_label.setWordWrap(True)
        self.origin_label.setAccessibleName("검토 중인 결과의 출처")
        # The window's banner at the top already says where the result came from (text
        # and colour); this copy is kept for screen readers and tests, not shown twice.
        self.origin_label.setVisible(False)
        layout.addWidget(self.origin_label)

        # One line above the list: how far the review is, and one "보기" menu holding the
        # state filters and the sort order (instead of a row of chips).
        self._filter_key = FILTER_ALL
        self._sort_key = SORT_TIME
        self._filter_labels = {FILTER_ALL: "전체"}
        self._filter_labels.update({j.value: JUDGEMENT_LABELS[j] for j in ReviewJudgement})
        self.progress_label = QLabel("")
        self.progress_label.setObjectName("reviewProgress")
        self.progress_label.setAccessibleName("검토 진행")
        self.view_button = QPushButton("보기: 전체")
        self.view_button.setAccessibleName("후보 보기와 정렬")
        self.view_button.setObjectName("viewButton")
        theme.mark_compact(self.view_button)
        self.view_menu = QMenu(self.view_button)
        self.filter_actions: dict[str, QAction] = {}
        filter_group = QActionGroup(self)
        for key, label in self._filter_labels.items():
            action = self.view_menu.addAction(label)
            action.setCheckable(True)
            action.triggered.connect(lambda _checked=False, value=key: self.set_filter(value))
            filter_group.addAction(action)
            self.filter_actions[key] = action
        self.filter_actions[FILTER_ALL].setChecked(True)
        self.view_menu.addSeparator()
        self.sort_actions: dict[str, QAction] = {}
        sort_group = QActionGroup(self)
        for label, key in (("시간순", SORT_TIME), ("모델 순서", SORT_MODEL)):
            action = self.view_menu.addAction(label)
            action.setCheckable(True)
            action.triggered.connect(lambda _checked=False, value=key: self.set_sort(value))
            sort_group.addAction(action)
            self.sort_actions[key] = action
        self.sort_actions[SORT_TIME].setChecked(True)
        self.view_button.setMenu(self.view_menu)
        # The progress line and the candidate list form their own part, which the window
        # places beside the video (step ③); the rest of the panel is the AI result and
        # the reviewer's record.
        self.list_section = QWidget()
        list_layout = QVBoxLayout(self.list_section)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(6)
        list_bar = QHBoxLayout()
        list_bar.addWidget(self.progress_label, 1)
        list_bar.addWidget(self.view_button)
        list_layout.addLayout(list_bar)

        self.candidate_list = _CompactListWidget()
        self.candidate_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.candidate_list.setWordWrap(True)
        # Grow from the minimum height into spare space instead of claiming a fixed preferred
        # height, so the reviewer notes stay on screen in the step layout.
        self.candidate_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        # Rows drawn like the demo list: number, time, summary and review mark.
        self.candidate_list.setObjectName("candidateList")
        self.candidate_list.setItemDelegate(CandidateDelegate(self.candidate_list))
        self.candidate_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.candidate_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.candidate_list.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.candidate_list.setAccessibleName("중요 구간 후보 목록")
        self.candidate_list.setAccessibleDescription(SHORTCUT_HELP)
        self.candidate_list.setToolTip(SHORTCUT_HELP)
        list_layout.addWidget(self.candidate_list, 1)

        self.detail_view = _CompactRichView()
        # The grid has its own lines; no frame around it.
        self.detail_view.setObjectName("plainTable")
        self.detail_view.setReadOnly(True)
        self.detail_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.detail_view.setAccessibleName("선택 구간 상세")
        layout.addWidget(self.detail_view, 1)

        # The reviewer's part, on a light grey panel under the AI result: watch (playback
        # range), judge (four round buttons), note (memo) and tag (chips), in that order.
        record_group = QFrame()
        record_group.setObjectName("recordPanel")
        record_group.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        panel = QVBoxLayout(record_group)
        panel.setContentsMargins(14, 12, 14, 12)
        panel.setSpacing(8)
        title = QLabel("수사관 기록")
        title.setObjectName("recordTitle")
        panel.addWidget(title)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)
        panel.addLayout(grid)
        self._record_grid = grid

        # Wraps at large zoom instead of widening the panel.
        self.playback_row = FlowRow(spacing=8)
        segments = QWidget()
        segment_layout = QHBoxLayout(segments)
        segment_layout.setContentsMargins(0, 0, 0, 0)
        segment_layout.setSpacing(0)
        self.playback_buttons: list[QPushButton] = []
        # The range last played stays marked, so the next candidate plays the same way.
        self._playback_group = QButtonGroup(self)
        self._playback_group.setExclusive(True)
        for position, (label, padding) in enumerate(PLAYBACK_RANGES):
            button = QPushButton(label)
            button.setCheckable(True)
            theme.set_icon(button, "play")
            theme.mark_compact(button)
            self._playback_group.addButton(button)
            button.setToolTip(
                "선택 후보의 확인용 범위만 재생"
                if padding == 0
                else f"확인용 범위 앞뒤 {label[3:]}를 포함해 재생 (원본 길이 안으로 제한)"
            )
            theme.set_segment(button, position, len(PLAYBACK_RANGES))
            button.clicked.connect(
                lambda _checked=False, value=padding: self._request_playback(value)
            )
            segment_layout.addWidget(button)
            self.playback_buttons.append(button)
        self.playback_row.flow.addWidget(segments)

        judgement_row = FlowRow(spacing=6)
        judgement_row.setAccessibleName("판단")
        self.judgement_group = QButtonGroup(self)
        self.judgement_buttons: dict[ReviewJudgement, QRadioButton] = {}
        for judgement in ReviewJudgement:
            button = QRadioButton(JUDGEMENT_LABELS[judgement])
            button.setAccessibleName(f"판단: {JUDGEMENT_LABELS[judgement]}")
            theme.set_variant(button, "pill")
            button.setIcon(_pill_icon(judgement, checked=False))
            button.toggled.connect(
                lambda checked, b=button, j=judgement: b.setIcon(_pill_icon(j, checked=checked))
            )
            self.judgement_group.addButton(button)
            self.judgement_buttons[judgement] = button
            judgement_row.flow.addWidget(button)

        self.memo_edit = _CompactTextView()
        self.memo_edit.setPlaceholderText("원본에서 확인한 내용·제외 사유 등")
        self.memo_edit.setAccessibleName("검토 메모")

        # Tags show as chips (× removes one); [+ 태그] opens a small input for new ones.
        # tags_edit keeps the comma-separated text that is saved.
        self.tags_edit = QLineEdit()
        self.tags_edit.setVisible(False)
        self.tags_edit.setAccessibleName("검토 태그")
        self.tag_chips = FlowRow(spacing=6)
        # A small round "+" (no words); it opens a chip-shaped input, Enter adds the tag.
        self.tags_add_button = QPushButton()
        self.tags_add_button.setObjectName("tagAdd")
        theme.set_icon(self.tags_add_button, "plus", theme.PRIMARY)
        self.tags_add_button.setToolTip("태그 추가 (쉼표로 여러 개, Enter로 넣기)")
        self.tags_add_button.setAccessibleName("태그 추가")
        self.tag_input = QLineEdit()
        self.tag_input.setObjectName("tagInput")
        self.tag_input.setToolTip("Enter로 넣기 · 쉼표로 여러 개")
        self.tag_input.setAccessibleName("새 태그")
        self.tag_input.setVisible(False)
        self.save_button = QPushButton("기록 저장")
        self.revert_button = QPushButton("되돌리기")
        self.revert_button.setToolTip("마지막으로 저장한 기록으로 되돌립니다")
        self.unsaved_label = QLabel("")
        self.unsaved_label.setWordWrap(True)
        self.unsaved_label.setStyleSheet(f"color:{theme.WARNING_TEXT};")
        self.unsaved_label.setVisible(False)
        self.save_status_label = QLabel("")
        self.save_status_label.setWordWrap(True)
        self.save_status_label.setAccessibleName("검토 기록 저장 상태")
        # Chips, then [+ 태그] (or its input) flow on one line and wrap when needed.
        self.tag_chips.flow.addWidget(self.tags_add_button)
        self.tag_chips.flow.addWidget(self.tag_input)
        self.tag_input.setFixedWidth(140)
        tag_row = QHBoxLayout()
        tag_row.setSpacing(6)
        tag_row.addWidget(self.tag_chips, 1)
        tag_row.addWidget(self.save_status_label)
        tag_row.addWidget(self.revert_button)
        tag_row.addWidget(self.save_button)

        for row, (name, widget) in enumerate(
            (
                ("재생", self.playback_row),
                ("판단", judgement_row),
                ("메모", self.memo_edit),
                ("태그", tag_row),
            )
        ):
            label = QLabel(name)
            label.setObjectName("fieldLabel")
            label.setMinimumHeight(32)
            label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            grid.addWidget(label, row, 0, Qt.AlignmentFlag.AlignTop)
            if isinstance(widget, QHBoxLayout):
                grid.addLayout(widget, row, 1)
            else:
                grid.addWidget(widget, row, 1)
        grid.addWidget(self.unsaved_label, 4, 1)
        self.record_group = record_group
        layout.addWidget(record_group)

        # Finish and summary sit in the card's title line (the window places this bar).
        self.completion_bar = QWidget()
        completion_row = QHBoxLayout(self.completion_bar)
        completion_row.setContentsMargins(0, 0, 0, 0)
        self.completion_label = QLabel("")
        self.completion_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.completion_label.setAccessibleName("분석 완료 상태")
        self.completion_label.setObjectName("subsectionHelp")
        # Wraps instead of widening the title line at large zoom.
        self.completion_label.setWordWrap(True)
        # The words say what happens (no tooltip box): mark the review done (it can still
        # be changed), and open the summary draft from the saved records.
        self.complete_button = QPushButton("검토 완료 기록")
        self.summary_button = QPushButton("요약 초안 열기")
        # Dense review card: KRDS xsmall buttons keep the step on one screen.
        for button in (self.save_button, self.revert_button, self.complete_button, self.summary_button):
            theme.mark_compact(button)
        completion_row.addStretch(1)
        completion_row.addWidget(self.completion_label)
        completion_row.addWidget(self.summary_button)
        completion_row.addWidget(self.complete_button)

    def _connect_signals(self) -> None:
        self.tags_add_button.clicked.connect(self._open_tag_input)
        self.tag_input.returnPressed.connect(self._add_typed_tags)
        self.tag_input.editingFinished.connect(self._close_tag_input)
        self.tags_edit.textChanged.connect(self._refresh_tag_chips)
        self.candidate_list.currentItemChanged.connect(self._current_item_changed)
        self.candidate_list.judgement_key.connect(self._judgement_shortcut)
        self.candidate_list.play_key.connect(self.play_toggle_requested.emit)
        self.judgement_group.buttonToggled.connect(self._editor_changed)
        self.memo_edit.textChanged.connect(self._editor_changed)
        self.tags_edit.textChanged.connect(self._editor_changed)
        self.save_button.clicked.connect(self.save_current)
        self.revert_button.clicked.connect(self.revert_current)
        self.complete_button.clicked.connect(self.complete_review)
        self.summary_button.clicked.connect(self.summary_requested.emit)

    def apply_scale(self, scaled: Callable[[int], int]) -> None:
        # About three one-line rows above the video.
        self.candidate_list.setMinimumHeight(scaled(126))
        self.detail_view.setMinimumHeight(scaled(72))
        self.memo_edit.setMinimumHeight(scaled(48))
        self.memo_edit.setMaximumHeight(scaled(110))
        # Row names line up with the 32px buttons beside them.
        for row in range(self._record_grid.rowCount()):
            item = self._record_grid.itemAtPosition(row, 0)
            if item is not None and isinstance(item.widget(), QLabel):
                item.widget().setMinimumHeight(scaled(32))

    # ----- job / message display -------------------------------------------------

    @property
    def job(self) -> AnalysisJob | None:
        return self._job

    @property
    def review_session(self) -> ReviewSession | None:
        """The saved review state (unsaved drafts are not included)."""
        return self._session

    def clear(self) -> None:
        self._tracking_since = None
        self._tracked_index = None
        self._total_seconds = 0.0
        self._segment_seconds = {}
        self._job = None
        self._session = None
        self._persist = None
        self._save_blocked = False
        self._saved.clear()
        self._drafts.clear()
        self._current_index = None
        self.candidate_list.blockSignals(True)
        self.candidate_list.clear()
        self.candidate_list.blockSignals(False)
        self.origin_label.setText("결과 없음")
        self.origin_label.setStyleSheet("")
        self._set_filter_counts(None)
        self._set_save_status("", "")
        self.completion_label.setText("")
        self.complete_button.setEnabled(False)
        self.summary_button.setEnabled(False)
        self._show_editor_for(None)

    def show_message(self, text: str) -> None:
        """Show a non-candidate line such as an empty or failed result."""
        self.candidate_list.addItem(QListWidgetItem(text))

    def set_job(
        self,
        job: AnalysisJob,
        *,
        session: ReviewSession | None = None,
        persist: Callable[[ReviewSession], Path] | None = None,
        load_error: Exception | None = None,
    ) -> None:
        """Show a job with any previously stored reviews.

        `persist` writes the session and raises OSError/ValueError on failure. Without it,
        saved records live only in memory. A `load_error` blocks saving so an unreadable
        review file is never overwritten.
        """
        self.clear()
        self._job = job
        self._persist = persist
        self._set_origin(job)
        if session is not None:
            session.validate_against(job)
            self._session = session
            self._total_seconds = session.total_review_seconds
            for review in session.reviews:
                self._saved[review.segment_index] = ReviewRecord(
                    judgement=review.judgement,
                    memo=review.memo,
                    tags=review.tags,
                    bookmarked=review.bookmarked,
                )
                self._segment_seconds[review.segment_index] = review.review_seconds
        else:
            self._session = ReviewSession(
                review_id=f"review-{uuid4().hex[:12]}",
                job_id=job.job_id,
                video_id=job.video_id,
                started_at=_now_iso(),
                case_id=job.case_id,
            )
        if load_error is not None:
            self._save_blocked = True
            self._set_save_status(
                f"기존 검토 기록 파일을 읽지 못했습니다({_describe_load_error(load_error)}). "
                "덮어쓰지 않도록 저장을 막았습니다.",
                "error",
                detail=str(load_error),
            )
        elif session is not None and session.reviews:
            self._set_save_status(
                f"저장된 검토 기록 {len(session.reviews)}건을 불러왔습니다.", "ok"
            )
        elif persist is not None:
            self._set_save_status("", "idle")
        else:
            self._set_save_status("저장 위치 없음 (앱을 닫으면 사라짐)", "idle")
        if not job.segments:
            self.show_message(
                "분석된 프레임에서 목적에 맞는 후보를 찾지 못했습니다. "
                "이는 해당 행동이 없다는 확정이 아닙니다."
            )
            return
        self._rebuild_list()
        self.complete_button.setEnabled(not self._save_blocked)
        self.summary_button.setEnabled(True)
        self._refresh_completion_label()
        self._sync_timing()

    def _set_origin(self, job: AnalysisJob) -> None:
        if job.origin is ResultOrigin.SAMPLE:
            text = "시연용 결과 (실제 AI 분석 아님)"
            style = theme.banner_style("sample")
        elif job.origin is ResultOrigin.TEST_DOUBLE:
            text = "테스트 응답 (실제 모델 검증 아님)"
            style = theme.banner_style("test_double")
        elif job.origin is ResultOrigin.REAL_MODEL:
            text = "AI 분석 결과 (수사관 확인 전)"
            style = theme.banner_style("real_model")
        else:
            text = "출처 확인 안 됨"
            style = theme.banner_style("neutral")
        self.origin_label.setText(text)
        self.origin_label.setToolTip(f"모델: {job.model_id}" if job.model_id else "")
        self.origin_label.setStyleSheet(style + "padding:6px;border-radius:5px;font-weight:700;")

    # ----- records -------------------------------------------------------------

    def record_for(self, index: int) -> ReviewRecord:
        """Record as currently shown, including unsaved edits."""
        return self._drafts.get(index, self._saved.get(index, ReviewRecord()))

    def saved_record_for(self, index: int) -> ReviewRecord:
        return self._saved.get(index, ReviewRecord())

    def unsaved_indexes(self) -> tuple[int, ...]:
        return tuple(sorted(self._drafts))

    def has_unsaved_changes(self) -> bool:
        return bool(self._drafts)

    def save_current(self) -> bool:
        index = self._current_index
        if index is None or self._save_blocked or self._session is None:
            return False
        record = self._drafts.get(index)
        if record is None:
            return False
        saved = dict(self._saved)
        saved[index] = record
        self._sync_timing()
        try:
            session = self._session_with(saved, index)
            path = self._persist(session) if self._persist is not None else None
        except (OSError, ValueError) as exc:
            self._set_save_status(
                f"저장 실패: {_describe_save_error(exc)} · 입력한 내용은 화면에 남아 있습니다.",
                "error",
                detail=str(exc),
            )
            self._save_failed = True
            self._refresh_status()
            self._update_buttons()
            return False
        self._save_failed = False
        del self._drafts[index]
        self._saved = saved
        self._session = session
        if path is not None:
            # The time is enough on screen; the file stays in the hover note.
            self._set_save_status(f"저장됨 {datetime.now():%H:%M}", "ok")
            self.save_status_label.setToolTip(f"저장 파일: {path.parent.name}/{path.name}")
        else:
            self._set_save_status("저장 위치 없음 (앱을 닫으면 사라짐)", "idle")
        self._refresh_item(index)
        self._refresh_status()
        self._update_buttons()
        self.records_changed.emit()
        return True

    def _session_with(
        self, saved: dict[int, ReviewRecord], changed_index: int | None
    ) -> ReviewSession:
        assert self._session is not None and self._job is not None
        reviews = []
        for index in sorted(saved):
            record = saved[index]
            previous = self._session.review_for(index)
            reviews.append(
                CandidateReview(
                    job_id=self._job.job_id,
                    segment_index=index,
                    judgement=record.judgement,
                    memo=record.memo,
                    tags=record.tags,
                    bookmarked=record.bookmarked,
                    reviewed_at=(
                        _now_iso()
                        if index == changed_index or previous is None
                        else previous.reviewed_at
                    ),
                    review_seconds=self._segment_seconds.get(index, 0.0),
                )
            )
        return replace(
            self._session,
            reviews=tuple(reviews),
            total_review_seconds=self._total_seconds,
        )

    # ----- completion -----------------------------------------------------------

    def complete_review(self) -> bool:
        job = self._job
        if job is None or not job.segments or self._save_blocked or self._session is None:
            return False
        unreviewed = sum(
            1
            for index in range(len(job.segments))
            if self.saved_record_for(index).judgement is ReviewJudgement.UNREVIEWED
        )
        unsaved = self.unsaved_indexes()
        if unreviewed or unsaved:
            parts = []
            if unreviewed:
                parts.append(f"미검토 후보 {unreviewed}개")
            if unsaved:
                names = ", ".join(f"후보 #{index + 1}" for index in unsaved)
                parts.append(f"저장하지 않은 기록 {len(unsaved)}개({names})")
            message = ", ".join(parts) + "가 있습니다."
            if unsaved:
                message += " 저장하지 않은 기록은 완료 기록에 포함되지 않습니다."
            if not self._confirm_completion(message + " 완료로 기록할까요?"):
                return False
        self._sync_timing()
        try:
            session = replace(self._session_with(self._saved, None), completed_at=_now_iso())
            path = self._persist(session) if self._persist is not None else None
        except (OSError, ValueError) as exc:
            self._set_save_status(
                f"완료 기록 실패: {_describe_save_error(exc)}", "error", detail=str(exc)
            )
            return False
        self._session = session
        if path is not None:
            self._set_save_status(
                f"완료 기록 {datetime.now():%H:%M:%S} · {path.parent.name}/{path.name}", "ok"
            )
        self._refresh_completion_label()
        return True

    def _confirm_completion(self, message: str) -> bool:
        return confirm(self, "검토 완료 기록", message, confirm_text="완료로 기록")

    def _refresh_completion_label(self) -> None:
        session = self._session
        # Before finishing only [분석 완료] shows; the time and [결과 요약] come after.
        if session is None or session.completed_at is None:
            self.completion_label.setText("완료 기록 없음")
            self.completion_label.setVisible(False)
            self.summary_button.setVisible(False)
            self._refresh_primary_action()
            return
        completed = datetime.fromisoformat(session.completed_at)
        self.completion_label.setText(f"완료 {completed:%m-%d %H:%M}")
        self.completion_label.setVisible(True)
        self.summary_button.setVisible(True)
        self._refresh_primary_action()

    # ----- review time ----------------------------------------------------------

    def set_app_active(self, active: bool) -> None:
        self._app_active = active
        self._sync_timing()

    def total_review_seconds(self) -> float:
        return self._total_seconds + self._running_seconds()

    def segment_review_seconds(self, index: int) -> float:
        running = self._running_seconds() if index == self._tracked_index else 0.0
        return self._segment_seconds.get(index, 0.0) + running

    def _running_seconds(self) -> float:
        if self._tracking_since is None:
            return 0.0
        return max(0.0, self._clock() - self._tracking_since)

    def _sync_timing(self) -> None:
        """Fold the running interval into the totals, then restart or stop the clock."""
        now = self._clock()
        if self._tracking_since is not None:
            elapsed = max(0.0, now - self._tracking_since)
            self._total_seconds += elapsed
            if self._tracked_index is not None:
                self._segment_seconds[self._tracked_index] = (
                    self._segment_seconds.get(self._tracked_index, 0.0) + elapsed
                )
        if self._app_active and self._job is not None and self._job.segments:
            self._tracking_since = now
            self._tracked_index = self._current_index
        else:
            self._tracking_since = None
            self._tracked_index = None

    def _set_save_status(self, text: str, state: str, *, detail: str = "") -> None:
        self.save_status_label.setText(text)
        self.save_status_label.setToolTip(f"오류 원문: {detail}" if detail else "")
        self.save_status_label.setStyleSheet(f"color:{theme.status_color(state)};")

    def revert_current(self) -> None:
        index = self._current_index
        if index is None or index not in self._drafts:
            return
        del self._drafts[index]
        self._show_editor_for(index)
        self._refresh_item(index)
        self._refresh_status()

    # ----- list ---------------------------------------------------------------

    def _segments(self) -> tuple[Segment, ...]:
        return self._job.segments if self._job is not None else ()

    def _visible_indexes(self) -> list[int]:
        segments = self._segments()
        indexes = list(range(len(segments)))
        if self._sort_key == SORT_TIME:
            indexes.sort(key=lambda i: (segments[i].start_sec, segments[i].end_sec, i))
        selected = self._filter_key
        if selected != FILTER_ALL:
            return [i for i in indexes if self.record_for(i).judgement.value == selected]
        return indexes

    def _rebuild_list(self) -> None:
        if self._job is None or not self._job.segments:
            return
        keep = self._current_index
        self.candidate_list.blockSignals(True)
        self.candidate_list.clear()
        current_row = -1
        for row, index in enumerate(self._visible_indexes()):
            item = QListWidgetItem(self._item_text(index))
            item.setData(ROW_ROLE, self._item_row(index))
            item.setData(Qt.ItemDataRole.UserRole, self._segments()[index])
            item.setData(SEGMENT_INDEX_ROLE, index)
            self.candidate_list.addItem(item)
            if index == keep:
                current_row = row
        self.candidate_list.setCurrentRow(current_row)
        self.candidate_list.blockSignals(False)
        if current_row < 0:
            self._current_index = None
            self._show_editor_for(None)
            self._sync_timing()
        self._refresh_status()

    def _item_row(self, index: int) -> CandidateRow:
        segment = self._segments()[index]
        record = self.record_for(index)
        start = format_milliseconds(seconds_to_milliseconds(segment.start_sec))
        end = format_milliseconds(seconds_to_milliseconds(segment.end_sec))
        return CandidateRow(
            number=index + 1,
            time_text=f"{start}–{end}",
            length_text=length_text(segment.end_sec - segment.start_sec),
            summary=tidy_seconds(segment.summary),
            judgement=record.judgement,
            judgement_label=JUDGEMENT_LABELS[record.judgement],
            unsaved=index in self._drafts,
        )

    def _item_text(self, index: int) -> str:
        """Plain text for screen readers; the list draws the row itself."""
        row = self._item_row(index)
        parts = [f"후보 #{row.number}", row.time_text, row.judgement_label]
        if row.unsaved:
            parts.append("저장 안 됨")
        return f"{' · '.join(parts)}\n{row.summary}"

    def _refresh_item(self, index: int) -> None:
        for row in range(self.candidate_list.count()):
            item = self.candidate_list.item(row)
            if item.data(SEGMENT_INDEX_ROLE) == index:
                item.setText(self._item_text(index))
                item.setData(ROW_ROLE, self._item_row(index))
                return

    def _current_item_changed(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        index = current.data(SEGMENT_INDEX_ROLE) if current is not None else None
        valid = isinstance(index, int) and 0 <= index < len(self._segments())
        self.flush_pending()
        self._current_index = index if valid else None
        self._sync_timing()
        self._show_editor_for(self._current_index)
        self.selection_changed.emit()

    def _refresh_status(self) -> None:
        segments = self._segments()
        if not segments:
            self._set_filter_counts(None)
            self.unsaved_label.setText("")
            self.unsaved_label.setVisible(False)
            return
        counts = {judgement: 0 for judgement in ReviewJudgement}
        for index in range(len(segments)):
            counts[self.record_for(index).judgement] += 1
        totals = {FILTER_ALL: len(segments)}
        totals.update({judgement.value: counts[judgement] for judgement in ReviewJudgement})
        self._set_filter_counts(totals)
        self._refresh_primary_action()
        unsaved = self.unsaved_indexes()
        self.unsaved_label.setText(
            "저장하지 않은 기록: " + ", ".join(f"후보 #{i + 1}" for i in unsaved)
            if unsaved
            else ""
        )
        # Typing waits a moment for its automatic save; the line is for saves that failed.
        self.unsaved_label.setVisible(bool(unsaved) and (self._save_failed or self._save_blocked))

    @property
    def current_index(self) -> int | None:
        return self._current_index

    def select_segment(self, index: int) -> bool:
        """Select one candidate, showing every candidate first if a filter hides it."""
        if self._job is None or not 0 <= index < len(self._job.segments):
            return False
        if index not in self._visible_indexes():
            self.set_filter(FILTER_ALL)
        for row in range(self.candidate_list.count()):
            if self.candidate_list.item(row).data(SEGMENT_INDEX_ROLE) == index:
                self.candidate_list.setCurrentRow(row)
                return True
        return False

    def set_filter(self, key: str) -> None:
        """Show only the candidates in one review state (or all)."""
        self._filter_key = key
        self.filter_actions[key].setChecked(True)
        self._rebuild_list()
        self._refresh_view_button()

    def set_sort(self, key: str) -> None:
        self._sort_key = key
        self.sort_actions[key].setChecked(True)
        self._rebuild_list()
        self._refresh_view_button()

    def _set_filter_counts(self, totals: dict[str, int] | None) -> None:
        """Counts in the "보기" menu, and the review progress above the list."""
        self._totals = totals
        for key, action in self.filter_actions.items():
            label = self._filter_labels[key]
            action.setText(label if totals is None else f"{label} {totals[key]}")
        if totals is None:
            self.progress_label.setText("")
        else:
            total = totals[FILTER_ALL]
            done = sum(
                self.saved_record_for(index).judgement is not ReviewJudgement.UNREVIEWED
                for index in range(total)
            )
            self.progress_label.setText(
                f"확인 {done}/{total} · 남은 후보 {total - done}" if done < total else f"확인 {done}/{total} · 모두 확인"
            )
        self._refresh_view_button()

    def _refresh_view_button(self) -> None:
        totals = getattr(self, "_totals", None)
        label = self._filter_labels[self._filter_key]
        count = f" {totals[self._filter_key]}" if totals else ""
        self.view_button.setText(f"보기: {label}{count}")

    # ----- editor -------------------------------------------------------------

    def _show_editor_for(self, index: int | None) -> None:
        self._loading_editor = True
        try:
            enabled = index is not None
            for widget in (
                self.record_group,
                *self.playback_buttons,
            ):
                widget.setEnabled(enabled)
            if index is None:
                self.detail_view.setPlainText(
                    ""
                    if self._segments()
                    else ""
                )
                record = ReviewRecord()
            else:
                segment = self._segments()[index]
                purpose = self._job.purpose if self._job is not None else None
                # The formatter gives (label, value) rows, or "label: value" lines.
                detail = self._detail_formatter(segment, purpose)
                rows = (
                    list(detail)
                    if not isinstance(detail, str)
                    else [tuple(line.partition(": ")[::2]) for line in detail.splitlines() if line]
                )
                self.detail_view.setToolTip(precise_text(segment))
                self.detail_view.setHtml(
                    f"<p><b>후보 #{index + 1}</b> "
                    f'<span style="color:{theme.TEXT_SUB};">AI 분석 결과 (수사관 확인 전)</span></p>'
                    + theme.label_value_table(rows)
                )
                record = self.record_for(index)
            self.judgement_group.setExclusive(False)
            for judgement, button in self.judgement_buttons.items():
                button.setChecked(judgement is record.judgement and index is not None)
            self.judgement_group.setExclusive(True)
            self.memo_edit.setPlainText(record.memo)
            self.tags_edit.setText(", ".join(record.tags))
            self._refresh_tag_chips()
            self.tag_input.clear()
            self._close_tag_input()
            self._update_buttons()
        finally:
            self._loading_editor = False

    def _editor_record(self) -> ReviewRecord:
        judgement = next(
            (j for j, button in self.judgement_buttons.items() if button.isChecked()),
            ReviewJudgement.UNREVIEWED,
        )
        return ReviewRecord(
            judgement=judgement,
            memo=self.memo_edit.toPlainText(),
            tags=parse_tags(self.tags_edit.text()),
            # Bookmarks are no longer offered; one saved earlier is kept as it was.
            bookmarked=(
                self.saved_record_for(self._current_index).bookmarked
                if self._current_index is not None
                else False
            ),
        )

    # ----- tags (chips) ------------------------------------------------------------

    def _show_tag_input(self, shown: bool) -> None:
        self._tag_input_open = shown
        self.tags_add_button.setVisible(not shown)
        self.tag_input.setVisible(shown)

    def _open_tag_input(self) -> None:
        self._show_tag_input(True)
        self.tag_input.setFocus()

    def _close_tag_input(self) -> None:
        if self.tag_input.text().strip():
            self._add_typed_tags()
        self._show_tag_input(False)

    def _add_typed_tags(self) -> None:
        typed = parse_tags(self.tag_input.text())
        self.tag_input.clear()
        if typed:
            tags = parse_tags(", ".join((*parse_tags(self.tags_edit.text()), *typed)))
            self.tags_edit.setText(", ".join(tags))

    def _remove_tag(self, tag: str) -> None:
        tags = [item for item in parse_tags(self.tags_edit.text()) if item != tag]
        self.tags_edit.setText(", ".join(tags))

    def _refresh_tag_chips(self, *_args: object) -> None:
        flow = self.tag_chips.flow
        while flow.count():
            widget = flow.takeAt(0).widget()
            if widget is not None and widget.objectName() == "tagChip":
                # Off the panel at once (deleteLater runs only later).
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        for tag in parse_tags(self.tags_edit.text()):
            chip = QPushButton(f"{tag}  ×")
            chip.setObjectName("tagChip")
            chip.setToolTip(f"태그 '{tag}' 빼기")
            chip.setAccessibleName(f"태그 {tag} 빼기")
            chip.clicked.connect(lambda _checked=False, value=tag: self._remove_tag(value))
            flow.addWidget(chip)
        flow.addWidget(self.tags_add_button)
        flow.addWidget(self.tag_input)
        # Re-adding to the layout may show them; keep whichever one is in use.
        self._show_tag_input(getattr(self, "_tag_input_open", False))
        self.tag_chips.updateGeometry()

    def _editor_changed(self, *_args: object) -> None:
        if self._loading_editor or self._current_index is None:
            return
        index = self._current_index
        record = self._editor_record()
        if record == self.saved_record_for(index):
            self._drafts.pop(index, None)
        else:
            self._drafts[index] = record
        self._refresh_item(index)
        self._refresh_status()
        self._update_buttons()
        if index not in self._drafts:
            self._autosave_timer.stop()
        elif self.sender() in (self.memo_edit, self.tags_edit):
            self._autosave_timer.start()
        else:
            self._autosave_timer.stop()
            self.save_current()

    def _judgement_shortcut(self, judgement: ReviewJudgement) -> None:
        if self._current_index is not None and self.record_group.isEnabled():
            self.judgement_buttons[judgement].setChecked(True)

    def flush_pending(self) -> None:
        """Save what was typed into the shown candidate before moving away from it."""
        if self._autosave_timer.isActive() or (
            self._current_index is not None and self._current_index in self._drafts
        ):
            self._autosave_timer.stop()
            self.save_current()

    def _update_buttons(self) -> None:
        dirty = self._current_index is not None and self._current_index in self._drafts
        self.save_button.setEnabled(dirty and not self._save_blocked)
        self.revert_button.setEnabled(dirty)
        # Saving is automatic; the buttons appear only when a save did not go through.
        stuck = dirty and (self._save_failed or self._save_blocked)
        self.save_button.setText("다시 저장")
        self.save_button.setVisible(stuck)
        self.revert_button.setVisible(stuck)
        self._refresh_primary_action()

    def _refresh_primary_action(self) -> None:
        """One blue button, in order: save the record, then finish, then the summary."""
        segments = self._segments()
        completed = self._session is not None and self._session.completed_at is not None
        all_reviewed = bool(segments) and all(
            self.saved_record_for(index).judgement is not ReviewJudgement.UNREVIEWED
            for index in range(len(segments))
        )
        if self.save_button.isEnabled():
            primary = self.save_button
        elif completed:
            primary = self.summary_button
        elif all_reviewed and self.complete_button.isEnabled():
            primary = self.complete_button
        else:
            primary = None
        for button in (self.save_button, self.complete_button, self.summary_button):
            theme.set_primary(button, button is primary)

    # ----- playback -----------------------------------------------------------

    def _request_playback(self, padding_seconds: float) -> None:
        if self._current_index is None:
            return
        segment = self._segments()[self._current_index]
        start = max(0.0, segment.start_sec - padding_seconds)
        end = segment.end_sec + padding_seconds
        self.playback_range_requested.emit(start, end)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _describe_save_error(exc: Exception) -> str:
    if isinstance(exc, OSError):
        if exc.errno == errno.ENOSPC:
            return "저장 공간이 부족합니다"
        if exc.errno in {errno.EACCES, errno.EPERM, errno.EROFS}:
            return "저장 폴더에 쓸 권한이 없습니다"
        return "파일을 쓰는 중 오류가 발생했습니다"
    return "메모 4000자, 태그 20개·각 50자 이하인지 확인해 주세요"


def _describe_load_error(exc: Exception) -> str:
    if isinstance(exc, json.JSONDecodeError):
        return "파일 형식이 손상됨"
    if isinstance(exc, OSError):
        return "파일에 접근할 수 없음"
    return "다른 결과의 기록이거나 형식이 맞지 않음"
