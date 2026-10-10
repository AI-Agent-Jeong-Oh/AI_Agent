from __future__ import annotations

import errno
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
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
from video_agent.ui.flow_layout import FlowLayout
from video_agent.video.files import format_milliseconds, seconds_to_milliseconds


JUDGEMENT_LABELS = {
    ReviewJudgement.UNREVIEWED: "미검토",
    ReviewJudgement.RELEVANT: "관련 있음",
    ReviewJudgement.NEEDS_CHECK: "추가 확인",
    ReviewJudgement.EXCLUDED: "제외",
}

SEGMENT_INDEX_ROLE = Qt.ItemDataRole.UserRole + 1

FILTER_ALL = "all"
FILTER_BOOKMARKED = "bookmarked"
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


class _CompactListWidget(QListWidget):
    """Prefers its minimum height and grows only into spare space."""

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(super().sizeHint().width(), self.minimumHeight())


class _CompactTextView(QPlainTextEdit):
    """Prefers its minimum height and grows only into spare space."""

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(super().sizeHint().width(), self.minimumHeight())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(super().minimumSizeHint().width(), self.minimumHeight())


class CandidateReviewPanel(QWidget):
    """Candidate list, selected-segment detail and reviewer notes for one analysis job.

    Reviewer records are human judgement only. They never modify the AnalysisJob and are
    never sent to an analyzer.
    """

    playback_range_requested = Signal(float, float)
    records_changed = Signal()
    summary_requested = Signal()

    def __init__(
        self,
        detail_formatter: Callable[[Segment, str | None], str],
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

        self.origin_label = QLabel("결과 없음")
        self.origin_label.setObjectName("reviewOrigin")
        self.origin_label.setWordWrap(True)
        self.origin_label.setAccessibleName("검토 중인 결과의 출처")
        layout.addWidget(self.origin_label)

        sort_row = QHBoxLayout()
        sort_row.setSpacing(0)
        sort_label = QLabel("정렬")
        sort_label.setObjectName("fieldLabel")
        sort_row.addWidget(sort_label)
        sort_row.addSpacing(8)
        self.sort_group = QButtonGroup(self)
        self.sort_buttons: dict[str, QPushButton] = {}
        sorts = (("시간순", SORT_TIME), ("모델 순서", SORT_MODEL))
        for position, (label, key) in enumerate(sorts):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setAccessibleName(f"후보 정렬: {label}")
            theme.set_segment(button, position, len(sorts))
            self.sort_group.addButton(button)
            self.sort_buttons[key] = button
            sort_row.addWidget(button)
        self.sort_buttons[SORT_TIME].setChecked(True)
        sort_row.addStretch(1)
        layout.addLayout(sort_row)

        # Chips wrap onto a second line when the panel is narrow or the UI is zoomed.
        self.filter_chips = QWidget()
        filter_row = FlowLayout(self.filter_chips, spacing=6)
        self.filter_group = QButtonGroup(self)
        self.filter_buttons: dict[str, QPushButton] = {}
        filters = [("전체", FILTER_ALL)]
        filters += [(JUDGEMENT_LABELS[j], j.value) for j in ReviewJudgement]
        filters.append(("북마크", FILTER_BOOKMARKED))
        self._filter_labels = {key: label for label, key in filters}
        for label, key in filters:
            button = QPushButton(label)
            button.setCheckable(True)
            button.setAccessibleName(f"검토 상태 필터: {label}")
            theme.set_variant(button, "chip")
            self.filter_group.addButton(button)
            self.filter_buttons[key] = button
            filter_row.addWidget(button)
        self.filter_buttons[FILTER_ALL].setChecked(True)
        layout.addWidget(self.filter_chips)

        self.candidate_list = _CompactListWidget()
        self.candidate_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.candidate_list.setWordWrap(True)
        # Grow from the minimum height into spare space instead of claiming a fixed preferred
        # height, so the reviewer notes stay on screen in the step layout.
        self.candidate_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.candidate_list.setSpacing(3)
        self.candidate_list.setAccessibleName("중요 구간 후보 목록")
        layout.addWidget(self.candidate_list, 1)

        detail_label = QLabel("선택 구간 상세 (모델 출력 — 사람 확인 전)")
        self.detail_view = _CompactTextView()
        self.detail_view.setReadOnly(True)
        self.detail_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.detail_view.setAccessibleName("선택 구간 상세")
        layout.addWidget(detail_label)
        layout.addWidget(self.detail_view, 1)

        playback_row = QHBoxLayout()
        playback_row.setSpacing(0)
        playback_label = QLabel("주변 구간 재생")
        playback_label.setObjectName("fieldLabel")
        playback_row.addWidget(playback_label)
        playback_row.addStretch(1)
        self.playback_buttons: list[QPushButton] = []
        for position, (label, padding) in enumerate(PLAYBACK_RANGES):
            button = QPushButton(label)
            button.setToolTip(
                "선택 후보의 확인용 범위만 재생"
                if padding == 0
                else f"확인용 범위 앞뒤 {label[3:]}를 포함해 재생 (원본 길이 안으로 제한)"
            )
            theme.set_segment(button, position, len(PLAYBACK_RANGES))
            button.clicked.connect(
                lambda _checked=False, value=padding: self._request_playback(value)
            )
            playback_row.addWidget(button)
            self.playback_buttons.append(button)
        layout.addLayout(playback_row)

        record_group = QFrame()
        record_group.setObjectName("reviewCard")
        card_layout = QVBoxLayout(record_group)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)
        header = QWidget()
        header.setObjectName("reviewCardHeader")
        header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        header_row = QHBoxLayout(header)
        header_row.setContentsMargins(12, 4, 10, 4)
        header_title = QLabel("수사관 기록")
        header_title.setObjectName("reviewCardTitle")
        header_note = QLabel("영상을 직접 본 뒤 기록 · 모델에 전송하지 않음")
        header_note.setObjectName("reviewCardNote")
        # Wraps instead of widening the panel when the UI is zoomed.
        header_note.setWordWrap(True)
        header_note.setMinimumWidth(1)
        self.bookmark_check = QPushButton("☆ 북마크")
        self.bookmark_check.setCheckable(True)
        self.bookmark_check.setObjectName("bookmarkButton")
        self.bookmark_check.setAccessibleName("북마크")
        self.bookmark_check.toggled.connect(
            lambda checked: self.bookmark_check.setText("★ 북마크" if checked else "☆ 북마크")
        )
        header_row.addWidget(header_title)
        header_row.addSpacing(6)
        header_row.addWidget(header_note, 1)
        header_row.addWidget(self.bookmark_check)
        card_layout.addWidget(header)
        body = QWidget()
        record_layout = QFormLayout(body)
        # Field labels move above their fields only when the row would not fit (large zoom).
        record_layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        record_layout.setContentsMargins(12, 8, 12, 8)
        card_layout.addWidget(body)
        self._record_layout = record_layout
        judgement_row = QHBoxLayout()
        judgement_row.setSpacing(6)
        self.judgement_group = QButtonGroup(self)
        self.judgement_buttons: dict[ReviewJudgement, QRadioButton] = {}
        for judgement in ReviewJudgement:
            button = QRadioButton(JUDGEMENT_LABELS[judgement])
            button.setAccessibleName(f"판단: {JUDGEMENT_LABELS[judgement]}")
            theme.set_variant(
                button, "judgementQuiet" if judgement is ReviewJudgement.UNREVIEWED else "judgement"
            )
            self.judgement_group.addButton(button)
            self.judgement_buttons[judgement] = button
            judgement_row.addWidget(button, 1)
        self.memo_edit = _CompactTextView()
        self.memo_edit.setPlaceholderText("원본에서 확인한 내용·제외 사유 등")
        self.memo_edit.setAccessibleName("검토 메모")
        self.tags_edit = QLineEdit()
        self.tags_edit.setPlaceholderText("쉼표로 구분 예: 손 접촉, 재확인")
        self.tags_edit.setAccessibleName("검토 태그")
        self.save_button = QPushButton("기록 저장")
        theme.mark_primary(self.save_button)
        self.revert_button = QPushButton("되돌리기")
        self.revert_button.setToolTip("마지막으로 저장한 기록으로 되돌립니다")
        self.unsaved_label = QLabel("")
        self.unsaved_label.setWordWrap(True)
        self.unsaved_label.setStyleSheet(f"color:{theme.WARNING_TEXT};")
        self.save_status_label = QLabel("")
        self.save_status_label.setWordWrap(True)
        self.save_status_label.setAccessibleName("검토 기록 저장 상태")
        button_row = QHBoxLayout()
        button_row.addWidget(self.save_status_label, 1)
        button_row.addWidget(self.revert_button)
        button_row.addWidget(self.save_button)
        record_layout.addRow("판단", judgement_row)
        record_layout.addRow("메모", self.memo_edit)
        record_layout.addRow("태그", self.tags_edit)
        record_layout.addRow("", button_row)
        record_layout.addRow("", self.unsaved_label)
        record_layout.setRowVisible(self.unsaved_label, False)
        self.record_group = record_group
        layout.addWidget(record_group)

        completion_row = QHBoxLayout()
        self.completion_label = QLabel("")
        self.completion_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.completion_label.setAccessibleName("분석 완료 상태")
        self.complete_button = QPushButton("분석 완료")
        theme.mark_primary(self.complete_button)
        self.complete_button.setToolTip(
            "검토를 마친 시각과 확인 시간을 기록합니다. 완료 후에도 수정할 수 있습니다."
        )
        self.summary_button = QPushButton("결과 요약")
        self.summary_button.setToolTip("저장된 검토 기록으로 결과 요약 초안을 엽니다")
        completion_row.addStretch(1)
        completion_row.addWidget(self.completion_label)
        completion_row.addWidget(self.summary_button)
        completion_row.addWidget(self.complete_button)
        layout.addLayout(completion_row)

    def _connect_signals(self) -> None:
        self.filter_group.buttonClicked.connect(lambda _button: self._rebuild_list())
        self.sort_group.buttonClicked.connect(lambda _button: self._rebuild_list())
        self.candidate_list.currentItemChanged.connect(self._current_item_changed)
        self.judgement_group.buttonToggled.connect(self._editor_changed)
        self.memo_edit.textChanged.connect(self._editor_changed)
        self.tags_edit.textChanged.connect(self._editor_changed)
        self.bookmark_check.toggled.connect(self._editor_changed)
        self.save_button.clicked.connect(self.save_current)
        self.revert_button.clicked.connect(self.revert_current)
        self.complete_button.clicked.connect(self.complete_review)
        self.summary_button.clicked.connect(self.summary_requested.emit)

    def apply_scale(self, scaled: Callable[[int], int]) -> None:
        self.candidate_list.setMinimumHeight(scaled(72))
        self.detail_view.setMinimumHeight(scaled(72))
        self.memo_edit.setMinimumHeight(scaled(48))
        self.memo_edit.setMaximumHeight(scaled(110))

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
            self._set_save_status("아직 파일에 저장한 기록 없음", "idle")
        else:
            self._set_save_status("파일 저장 미연결 · 앱 실행 중에만 유지", "idle")
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
            text = "출처: 샘플 결과 — 실제 AI 분석 아님"
            style = theme.banner_style("sample")
        elif job.origin is ResultOrigin.TEST_DOUBLE:
            text = "출처: 테스트용 가짜 모델 응답 — 실제 모델 검증 아님"
            style = theme.banner_style("test_double")
        elif job.origin is ResultOrigin.REAL_MODEL:
            text = f"출처: 실제 모델 응답 — {job.model_id} · 사람 확인 전 후보"
            style = theme.banner_style("real_model")
        else:
            text = "출처: 확인 안 됨"
            style = theme.banner_style("neutral")
        self.origin_label.setText(text)
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
            return False
        del self._drafts[index]
        self._saved = saved
        self._session = session
        if path is not None:
            self._set_save_status(
                f"마지막 저장 {datetime.now():%H:%M:%S} · {path.parent.name}/{path.name}",
                "ok",
            )
        else:
            self._set_save_status("파일 저장 미연결 · 앱 실행 중에만 유지", "idle")
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
        box = QMessageBox(QMessageBox.Icon.Question, "분석 완료", message, parent=self)
        accept = box.addButton("완료로 기록", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("취소", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return box.clickedButton() is accept

    def _refresh_completion_label(self) -> None:
        session = self._session
        if session is None or session.completed_at is None:
            self.completion_label.setText("완료 기록 없음")
            return
        completed = datetime.fromisoformat(session.completed_at)
        self.completion_label.setText(f"완료 {completed:%m-%d %H:%M}")

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
        if self.sort_buttons[SORT_TIME].isChecked():
            indexes.sort(key=lambda i: (segments[i].start_sec, segments[i].end_sec, i))
        selected = next(
            (key for key, button in self.filter_buttons.items() if button.isChecked()), FILTER_ALL
        )
        if selected == FILTER_BOOKMARKED:
            return [i for i in indexes if self.record_for(i).bookmarked]
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

    def _item_text(self, index: int) -> str:
        segment = self._segments()[index]
        record = self.record_for(index)
        start = format_milliseconds(seconds_to_milliseconds(segment.start_sec))
        end = format_milliseconds(seconds_to_milliseconds(segment.end_sec))
        marks = [f"[{JUDGEMENT_LABELS[record.judgement]}]"]
        if record.bookmarked:
            marks.append("★")
        if index in self._drafts:
            marks.append("●저장 안 됨")
        return f"{' '.join(marks)} 후보 #{index + 1} · {start}–{end}\n{segment.summary}"

    def _refresh_item(self, index: int) -> None:
        for row in range(self.candidate_list.count()):
            item = self.candidate_list.item(row)
            if item.data(SEGMENT_INDEX_ROLE) == index:
                item.setText(self._item_text(index))
                return

    def _current_item_changed(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        index = current.data(SEGMENT_INDEX_ROLE) if current is not None else None
        valid = isinstance(index, int) and 0 <= index < len(self._segments())
        self._current_index = index if valid else None
        self._sync_timing()
        self._show_editor_for(self._current_index)

    def _refresh_status(self) -> None:
        segments = self._segments()
        if not segments:
            self._set_filter_counts(None)
            self.unsaved_label.setText("")
            self._record_layout.setRowVisible(self.unsaved_label, False)
            return
        counts = {judgement: 0 for judgement in ReviewJudgement}
        bookmarks = 0
        for index in range(len(segments)):
            record = self.record_for(index)
            counts[record.judgement] += 1
            bookmarks += int(record.bookmarked)
        totals = {FILTER_ALL: len(segments), FILTER_BOOKMARKED: bookmarks}
        totals.update({judgement.value: counts[judgement] for judgement in ReviewJudgement})
        self._set_filter_counts(totals)
        unsaved = self.unsaved_indexes()
        self.unsaved_label.setText(
            "저장하지 않은 기록: " + ", ".join(f"후보 #{i + 1}" for i in unsaved)
            if unsaved
            else ""
        )
        self._record_layout.setRowVisible(self.unsaved_label, bool(unsaved))

    def select_segment(self, index: int) -> bool:
        """Select one candidate, showing every candidate first if a filter hides it."""
        if self._job is None or not 0 <= index < len(self._job.segments):
            return False
        if index not in self._visible_indexes():
            self.filter_buttons[FILTER_ALL].setChecked(True)
            self._rebuild_list()
        for row in range(self.candidate_list.count()):
            if self.candidate_list.item(row).data(SEGMENT_INDEX_ROLE) == index:
                self.candidate_list.setCurrentRow(row)
                return True
        return False

    def _set_filter_counts(self, totals: dict[str, int] | None) -> None:
        """Show each filter's candidate count on its chip (labels only without a result)."""
        for key, button in self.filter_buttons.items():
            label = self._filter_labels[key]
            button.setText(label if totals is None else f"{label} {totals[key]}")

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
                    "후보를 선택하면 요약·관찰 근거·불확실성·관찰 시각·확인용 범위가 표시됩니다."
                    if self._segments()
                    else ""
                )
                record = ReviewRecord()
            else:
                segment = self._segments()[index]
                purpose = self._job.purpose if self._job is not None else None
                self.detail_view.setPlainText(
                    f"후보 #{index + 1}\n" + self._detail_formatter(segment, purpose)
                )
                record = self.record_for(index)
            self.judgement_group.setExclusive(False)
            for judgement, button in self.judgement_buttons.items():
                button.setChecked(judgement is record.judgement and index is not None)
            self.judgement_group.setExclusive(True)
            self.memo_edit.setPlainText(record.memo)
            self.tags_edit.setText(", ".join(record.tags))
            self.bookmark_check.setChecked(record.bookmarked)
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
            bookmarked=self.bookmark_check.isChecked(),
        )

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

    def _update_buttons(self) -> None:
        dirty = self._current_index is not None and self._current_index in self._drafts
        self.save_button.setEnabled(dirty and not self._save_blocked)
        self.revert_button.setEnabled(dirty)

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
