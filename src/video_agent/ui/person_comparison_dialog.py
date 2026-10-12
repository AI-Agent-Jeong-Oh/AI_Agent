from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from video_agent.identity.comparison import (
    DECISION_LABELS,
    QUALITY_LABELS,
    PersonCandidateInput,
    PersonComparisonReport,
)
from video_agent.identity.worker import PersonComparisonWorker
from video_agent.ui import theme
from video_agent.ui.time_field import format_time
from video_agent.video.frames import ExtractedFrame


class PersonComparisonDialog(QDialog):
    """Select one event candidate per video and compare their evidence frames."""

    report_ready = Signal(object)
    running_changed = Signal(bool)

    def __init__(
        self,
        candidates: tuple[PersonCandidateInput, ...],
        model_label: str,
        worker_factory: Callable[
            [tuple[PersonCandidateInput, ...]], PersonComparisonWorker
        ],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("영상 간 인물 비교")
        self.resize(1280, 900)
        self.setMinimumSize(980, 720)
        self.setModal(True)
        self._candidates = candidates
        self._worker_factory = worker_factory
        self._worker: PersonComparisonWorker | None = None
        self._report: PersonComparisonReport | None = None
        self._evidence_frames: dict[str, tuple[ExtractedFrame, ...]] = {}

        layout = QVBoxLayout(self)
        notice = QLabel(
            "각 영상에서 비교할 사건 후보를 하나씩 선택하세요. 전체 프레임의 복장·소지품·보이는 "
            "외형만 대조하며 얼굴 검출이나 추적 모델은 사용하지 않습니다. 사진은 실제 비교에 사용한 "
            "전체 근거 프레임입니다. 외형 유사도 참고값은 신원 일치 확률이 아니며, 결과는 사람이 "
            "원본을 확인할 후보입니다."
        )
        notice.setWordWrap(True)
        layout.addWidget(notice)
        model = QLabel(f"비교 모델: {model_label}")
        model.setObjectName("fieldLabel")
        layout.addWidget(model)

        self.source_table = QTableWidget(len(candidates), 5)
        self.source_table.setHorizontalHeaderLabels(
            ("선택", "영상", "후보", "구간", "관찰 요약")
        )
        self.source_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.source_table.verticalHeader().setVisible(False)
        header = self.source_table.horizontalHeader()
        for column in (0, 2, 3):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        seen_videos: set[str] = set()
        for row, candidate in enumerate(candidates):
            check = QCheckBox()
            check.setChecked(candidate.video_id not in seen_videos)
            seen_videos.add(candidate.video_id)
            check.setAccessibleName(
                f"{candidate.video_name} 후보 {candidate.segment_index + 1} 인물 비교에 사용"
            )
            box = QWidget()
            row_layout = QHBoxLayout(box)
            row_layout.setContentsMargins(8, 0, 8, 0)
            row_layout.addWidget(check, 0, Qt.AlignmentFlag.AlignCenter)
            self.source_table.setCellWidget(row, 0, box)
            video = QTableWidgetItem(candidate.video_name)
            video.setData(Qt.ItemDataRole.UserRole, candidate.candidate_id)
            self.source_table.setItem(row, 1, video)
            self.source_table.setItem(
                row, 2, QTableWidgetItem(f"#{candidate.segment_index + 1}")
            )
            self.source_table.setItem(
                row,
                3,
                QTableWidgetItem(
                    f"{format_time(candidate.segment.start_sec)}–"
                    f"{format_time(candidate.segment.end_sec)}"
                ),
            )
            self.source_table.setItem(row, 4, QTableWidgetItem(candidate.segment.summary))
        layout.addWidget(self.source_table, 1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        self.status = QLabel("서로 다른 영상에서 후보를 선택하세요.")
        self.status.setWordWrap(True)
        layout.addWidget(self.progress)
        layout.addWidget(self.status)

        self.result_splitter = QSplitter(Qt.Orientation.Vertical)
        self.result_splitter.setChildrenCollapsible(False)
        self.result_splitter.setVisible(False)

        self.result_table = QTableWidget(0, 3)
        self.result_table.setHorizontalHeaderLabels(
            ("영상 비교", "결과", "외형 유사도 · 근거 품질")
        )
        self.result_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.result_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.result_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.result_table.setWordWrap(True)
        self.result_table.verticalHeader().setVisible(False)
        result_header = self.result_table.horizontalHeader()
        result_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        result_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        result_header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.result_table.cellClicked.connect(self._show_result_evidence)
        self.result_splitter.addWidget(self.result_table)

        self.result_detail_scroll = QScrollArea()
        self.result_detail_scroll.setWidgetResizable(True)
        self.result_detail_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        result_detail = QWidget()
        result_detail_layout = QVBoxLayout(result_detail)
        result_detail_layout.setContentsMargins(0, 8, 0, 0)

        self.result_detail_group = QGroupBox("선택한 비교 상세")
        detail_layout = QVBoxLayout(self.result_detail_group)
        self.result_detail_title = QLabel("결과 행을 선택하면 전체 내용을 표시합니다.")
        self.result_detail_title.setObjectName("fieldLabel")
        self.result_detail_title.setWordWrap(True)
        self.result_detail_title.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        detail_layout.addWidget(self.result_detail_title)
        self.similarities_detail = self._detail_text("유사하게 관찰된 점")
        self.differences_detail = self._detail_text("다르게 관찰된 점")
        self.limitations_detail = self._detail_text("판단 한계 · 원본 확인 사항")
        for title, value in (
            ("유사하게 관찰된 점", self.similarities_detail),
            ("다르게 관찰된 점", self.differences_detail),
            ("판단 한계 · 원본 확인 사항", self.limitations_detail),
        ):
            heading = QLabel(title)
            heading.setObjectName("fieldLabel")
            detail_layout.addWidget(heading)
            detail_layout.addWidget(value)
        result_detail_layout.addWidget(self.result_detail_group)

        self.photo_title = QLabel(
            "비교에 사용한 후보 인물 근거 사진 · 자동 인물 잘라내기 아님"
        )
        self.photo_title.setObjectName("fieldLabel")
        self.photo_title.setVisible(False)
        result_detail_layout.addWidget(self.photo_title)
        self.photo_panel = QWidget()
        photo_layout = QGridLayout(self.photo_panel)
        photo_layout.setContentsMargins(0, 0, 0, 0)
        photo_layout.setSpacing(8)
        self._photo_slots: list[tuple[QWidget, QLabel, QLabel]] = []
        for index in range(4):
            container = QWidget()
            item_layout = QVBoxLayout(container)
            item_layout.setContentsMargins(0, 0, 0, 0)
            image = QLabel("근거 사진 없음")
            image.setAlignment(Qt.AlignmentFlag.AlignCenter)
            image.setMinimumSize(360, 210)
            image.setStyleSheet(
                f"background:{theme.GRAY_5}; border:1px solid {theme.GRAY_20};"
            )
            caption = QLabel()
            caption.setWordWrap(True)
            caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
            item_layout.addWidget(image, 1)
            item_layout.addWidget(caption)
            container.setVisible(False)
            photo_layout.addWidget(container, index // 2, index % 2)
            self._photo_slots.append((container, image, caption))
        self.photo_panel.setVisible(False)
        result_detail_layout.addWidget(self.photo_panel)
        result_detail_layout.addStretch(1)
        self.result_detail_scroll.setWidget(result_detail)
        self.result_splitter.addWidget(self.result_detail_scroll)
        self.result_splitter.setStretchFactor(0, 1)
        self.result_splitter.setStretchFactor(1, 2)
        self.result_splitter.setSizes((220, 480))
        layout.addWidget(self.result_splitter, 2)

        buttons = QHBoxLayout()
        self.run_button = QPushButton("선택 후보 인물 비교")
        self.cancel_button = QPushButton("취소")
        self.close_button = QPushButton("닫기")
        theme.mark_primary(self.run_button)
        self.cancel_button.setEnabled(False)
        buttons.addWidget(self.run_button)
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)
        self.run_button.clicked.connect(self._run)
        self.cancel_button.clicked.connect(self._cancel)
        self.close_button.clicked.connect(self.close)

    @staticmethod
    def _detail_text(accessible_name: str) -> QLabel:
        value = QLabel("—")
        value.setWordWrap(True)
        value.setTextFormat(Qt.TextFormat.PlainText)
        value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        value.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        value.setAccessibleName(accessible_name)
        value.setMinimumWidth(1)
        value.setStyleSheet(
            f"background:{theme.GRAY_5}; border:1px solid {theme.GRAY_20}; padding:12px;"
        )
        return value

    @property
    def is_running(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def request_cancel(self) -> None:
        self._cancel()

    def wait_for_finished(self, timeout_ms: int) -> bool:
        return (
            self._worker is None
            or not self._worker.isRunning()
            or self._worker.wait(timeout_ms)
        )

    def selected_candidates(self) -> tuple[PersonCandidateInput, ...]:
        by_id = {item.candidate_id: item for item in self._candidates}
        selected = []
        for row in range(self.source_table.rowCount()):
            box = self.source_table.cellWidget(row, 0)
            check = box.findChild(QCheckBox) if box is not None else None
            if check is None or not check.isChecked():
                continue
            candidate_id = self.source_table.item(row, 1).data(Qt.ItemDataRole.UserRole)
            candidate = by_id.get(candidate_id)
            if candidate is not None:
                selected.append(candidate)
        return tuple(selected)

    def _run(self) -> None:
        selected = self.selected_candidates()
        if len(selected) < 2:
            QMessageBox.warning(self, "인물 비교", "서로 다른 영상의 후보를 두 개 이상 선택해 주세요.")
            return
        video_ids = tuple(item.video_id for item in selected)
        if len(video_ids) != len(set(video_ids)):
            QMessageBox.warning(self, "인물 비교", "한 영상에서는 후보를 하나만 선택해 주세요.")
            return
        pair_count = len(selected) * (len(selected) - 1) // 2
        answer = QMessageBox.question(
            self,
            "인물 비교 실행",
            f"{len(selected)}개 영상의 근거 프레임을 {pair_count}쌍으로 비교할까요?\n\n"
            "결과는 동일 인물 또는 범죄자 확정이 아니며 원본 확인이 필요합니다.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            worker = self._worker_factory(selected)
        except Exception as exc:
            QMessageBox.critical(self, "인물 비교", str(exc) or exc.__class__.__name__)
            return
        self._worker = worker
        worker.progress_changed.connect(self._on_progress)
        worker.evidence_ready.connect(self._on_evidence)
        worker.report_ready.connect(self._on_report)
        worker.error.connect(self._on_error)
        worker.finished.connect(self._on_finished)
        self._set_running(True)
        worker.start()

    def _on_progress(self, _value: object, message: str) -> None:
        self.status.setText(message)

    def _on_evidence(
        self,
        candidate_id: str,
        frames: tuple[ExtractedFrame, ...],
    ) -> None:
        self._evidence_frames[candidate_id] = frames

    def _on_report(self, report: PersonComparisonReport) -> None:
        self._report = report
        candidates = {
            str(item["candidate_id"]): str(item["video_name"])
            for item in report.candidates
        }
        self.result_table.setRowCount(len(report.comparisons))
        for row, comparison in enumerate(report.comparisons):
            pair = (
                f"{candidates.get(comparison.left_candidate_id, comparison.left_candidate_id)} ↔ "
                f"{candidates.get(comparison.right_candidate_id, comparison.right_candidate_id)}"
            )
            values = (
                pair,
                DECISION_LABELS[comparison.decision],
                f"{comparison.appearance_similarity_score}/100 · "
                f"{QUALITY_LABELS[comparison.evidence_quality]}\n신원 일치 확률 아님",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(
                        Qt.ItemDataRole.UserRole,
                        (comparison.left_candidate_id, comparison.right_candidate_id),
                    )
                self.result_table.setItem(row, column, item)
        self.result_table.resizeRowsToContents()
        self.result_splitter.setVisible(True)
        if report.comparisons:
            self.result_table.selectRow(0)
            self._show_result_evidence(0, 0)
        self.status.setText(
            f"인물 후보 비교 {report.completed_pair_count}/{report.attempted_pair_count}쌍 완료"
            + (" · 일부 비교 오류 있음" if report.error else "")
        )
        if report.error:
            self.status.setToolTip(report.error)
        self.report_ready.emit(report)

    def _show_result_evidence(self, row: int, _column: int) -> None:
        if self._report is None or not 0 <= row < len(self._report.comparisons):
            self._clear_result_detail()
            return
        item = self.result_table.item(row, 0)
        candidate_ids = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if (
            not isinstance(candidate_ids, tuple)
            or len(candidate_ids) != 2
            or not all(isinstance(value, str) for value in candidate_ids)
        ):
            self._clear_result_detail()
            return
        comparison = self._report.comparisons[row]
        candidate_names = {item.candidate_id: item.video_name for item in self._candidates}
        left_name = candidate_names.get(candidate_ids[0], candidate_ids[0])
        right_name = candidate_names.get(candidate_ids[1], candidate_ids[1])
        self.result_detail_title.setText(
            f"{left_name} ↔ {right_name} · {DECISION_LABELS[comparison.decision]} · "
            f"외형 유사도 {comparison.appearance_similarity_score}/100 · "
            f"근거 품질 {QUALITY_LABELS[comparison.evidence_quality]}"
        )
        self.similarities_detail.setText(
            self._format_detail_lines(comparison.observable_similarities)
        )
        self.differences_detail.setText(
            self._format_detail_lines(comparison.observable_differences)
        )
        self.limitations_detail.setText(self._format_detail_lines(comparison.limitations))
        evidence = []
        for side, candidate_id in zip(("A", "B"), candidate_ids, strict=True):
            for frame in self._evidence_frames.get(candidate_id, ()):
                evidence.append((side, candidate_names.get(candidate_id, candidate_id), frame))
        for index, (container, image, caption) in enumerate(self._photo_slots):
            if index >= len(evidence):
                container.setVisible(False)
                image.clear()
                caption.clear()
                continue
            side, video_name, frame = evidence[index]
            pixmap = QPixmap(str(frame.path))
            if pixmap.isNull():
                image.setPixmap(QPixmap())
                image.setText("근거 사진을 읽을 수 없음")
            else:
                image.setText("")
                image.setPixmap(
                    pixmap.scaled(
                        480,
                        280,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
            image.setAccessibleName(
                f"{side} {video_name} {frame.source_timestamp_sec:.3f}초 비교 근거 사진"
            )
            caption.setText(f"{side} · {video_name} · {format_time(frame.source_timestamp_sec)}")
            container.setVisible(True)
        visible = bool(evidence)
        self.photo_title.setVisible(visible)
        self.photo_panel.setVisible(visible)

    @staticmethod
    def _format_detail_lines(values: tuple[str, ...]) -> str:
        return "\n".join(f"• {value}" for value in values) if values else "관찰 내용 없음"

    def _clear_result_detail(self) -> None:
        self.result_detail_title.setText("결과 행을 선택하면 전체 내용을 표시합니다.")
        self.similarities_detail.setText("—")
        self.differences_detail.setText("—")
        self.limitations_detail.setText("—")
        self.photo_title.setVisible(False)
        self.photo_panel.setVisible(False)

    def _on_error(self, message: str) -> None:
        self.status.setText(message)
        QMessageBox.warning(self, "인물 비교", message)

    def _on_finished(self) -> None:
        self._worker = None
        self._set_running(False)

    def _cancel(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.request_cancel()
            self.cancel_button.setEnabled(False)
            self.status.setText("현재 비교를 정리하고 있습니다.")

    def _set_running(self, running: bool) -> None:
        self.run_button.setEnabled(not running)
        self.cancel_button.setEnabled(running)
        self.close_button.setEnabled(not running)
        self.source_table.setEnabled(not running)
        self.progress.setVisible(running)
        if running:
            self.progress.setRange(0, 0)
        self.running_changed.emit(running)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override name
        if self.is_running:
            QMessageBox.information(self, "인물 비교", "비교를 취소한 뒤 창을 닫아 주세요.")
            event.ignore()
            return
        event.accept()
