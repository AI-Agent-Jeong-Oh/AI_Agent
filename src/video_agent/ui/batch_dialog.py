from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from video_agent.analysis.batch import (
    BatchExecutionWorker,
    BatchPreparation,
    BatchPreparationWorker,
    BatchVideoInput,
    PreparedBatchVideo,
)
from video_agent.contracts import AnalysisJob, JobStatus
from video_agent.ui import theme
from video_agent.ui.time_field import format_time


@dataclass(frozen=True, slots=True)
class BatchDialogConfig:
    purpose: str
    model_label: str
    make_preparation_worker: Callable[[tuple[BatchVideoInput, ...]], BatchPreparationWorker]
    make_execution_worker: Callable[
        [tuple[PreparedBatchVideo, ...]], BatchExecutionWorker
    ]


class CaseBatchDialog(QDialog):
    """Prepare, visibly approve, and sequentially execute a case-wide video batch."""

    result_ready = Signal(object)
    running_changed = Signal(bool)

    def __init__(
        self,
        videos: tuple[BatchVideoInput, ...],
        config: BatchDialogConfig,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("여러 영상 일괄 분석")
        self.resize(860, 620)
        self.setModal(True)
        self._videos = videos
        self._config = config
        self._preparation: BatchPreparation | None = None
        self._prepare_worker: BatchPreparationWorker | None = None
        self._execute_worker: BatchExecutionWorker | None = None
        self._results: list[AnalysisJob] = []

        layout = QVBoxLayout(self)
        intro = QLabel(
            "연결된 영상을 수에 제한 없이 대기열에 넣습니다. 먼저 영상별 움직임 구간과 모델 입력 "
            "시각을 준비하고, 표시된 계획을 승인한 뒤 로컬 AI 분석을 한 영상씩 실행합니다."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        model = QLabel(f"사용 모델: {config.model_label}")
        model.setObjectName("fieldLabel")
        purpose = QLabel(f"분석 목적: {config.purpose}")
        purpose.setWordWrap(True)
        layout.addWidget(model)
        layout.addWidget(purpose)

        self.table = QTableWidget(len(videos), 4)
        self.table.setHorizontalHeaderLabels(("분석", "영상", "준비된 구간", "상태"))
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        for row, video in enumerate(videos):
            check = QCheckBox()
            check.setChecked(True)
            check.setAccessibleName(f"{video.display_name} 일괄 분석에 포함")
            box = QWidget()
            box_layout = QHBoxLayout(box)
            box_layout.setContentsMargins(8, 0, 8, 0)
            box_layout.addWidget(check, 0, Qt.AlignmentFlag.AlignCenter)
            self.table.setCellWidget(row, 0, box)
            name = QTableWidgetItem(video.display_name)
            name.setData(Qt.ItemDataRole.UserRole, video.video_id)
            self.table.setItem(row, 1, name)
            self.table.setItem(row, 2, QTableWidgetItem("준비 전"))
            self.table.setItem(row, 3, QTableWidgetItem("대기"))
        layout.addWidget(self.table, 1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        self.status = QLabel("분석할 영상을 선택하고 일괄 계획을 준비하세요.")
        self.status.setWordWrap(True)
        layout.addWidget(self.progress)
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        self.prepare_button = QPushButton("일괄 계획 준비")
        self.run_button = QPushButton("계획 승인하고 일괄 실행")
        self.cancel_button = QPushButton("작업 취소")
        self.close_button = QPushButton("닫기")
        theme.mark_primary(self.prepare_button)
        self.run_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        buttons.addWidget(self.prepare_button)
        buttons.addWidget(self.run_button)
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

        self.prepare_button.clicked.connect(self._prepare)
        self.run_button.clicked.connect(self._approve_and_run)
        self.cancel_button.clicked.connect(self._cancel)
        self.close_button.clicked.connect(self.close)

    @property
    def is_running(self) -> bool:
        return any(
            worker is not None and worker.isRunning()
            for worker in (self._prepare_worker, self._execute_worker)
        )

    def request_cancel(self) -> None:
        self._cancel()

    def wait_for_finished(self, timeout_ms: int) -> bool:
        worker = self._prepare_worker or self._execute_worker
        return worker is None or not worker.isRunning() or worker.wait(timeout_ms)

    def selected_videos(self) -> tuple[BatchVideoInput, ...]:
        selected = []
        for row, video in enumerate(self._videos):
            box = self.table.cellWidget(row, 0)
            check = box.findChild(QCheckBox) if box is not None else None
            if check is not None and check.isChecked():
                selected.append(video)
        return tuple(selected)

    def _prepare(self) -> None:
        selected = self.selected_videos()
        if not selected:
            QMessageBox.warning(self, "일괄 분석", "분석할 영상을 하나 이상 선택해 주세요.")
            return
        self._set_running(True, "영상별 움직임 구간과 계획을 준비하고 있습니다.")
        self._preparation = None
        self._results.clear()
        self.run_button.setEnabled(False)
        self.progress.setRange(0, 0)
        self._set_selected_status(selected, "준비 중")
        try:
            worker = self._config.make_preparation_worker(selected)
        except Exception as exc:
            self._set_running(False, str(exc) or exc.__class__.__name__)
            return
        worker.progress_changed.connect(self._on_progress)
        worker.preparation_ready.connect(self._on_prepared)
        worker.finished.connect(self._on_prepare_finished)
        self._prepare_worker = worker
        worker.start()

    def _on_prepared(self, preparation: BatchPreparation) -> None:
        self._preparation = preparation
        by_id = {item.video.video_id: item for item in preparation.prepared}
        failures = {item.video.video_id: item.error for item in preparation.failures}
        for row, video in enumerate(self._videos):
            item = by_id.get(video.video_id)
            if item is not None:
                ranges = ", ".join(
                    f"{format_time(proposal.start_sec)}–{format_time(proposal.end_sec)}"
                    for proposal in item.selected_proposals
                )
                if not ranges:
                    ranges = f"전체 {format_time(item.metadata.duration_seconds)} · 균등 표본"
                self.table.item(row, 2).setText(ranges)
                self.table.item(row, 3).setText("승인 대기")
            elif video.video_id in failures:
                self.table.item(row, 2).setText("준비 실패")
                self.table.item(row, 2).setToolTip(failures[video.video_id])
                self.table.item(row, 3).setText("제외")
        ready = len(preparation.prepared)
        failed = len(preparation.failures)
        self.status.setText(
            f"일괄 계획 {ready}건 준비 완료"
            + (f" · 준비 실패 {failed}건" if failed else "")
            + ". 구간과 모델을 확인한 뒤 승인하세요."
        )
        self.run_button.setEnabled(ready > 0)

    def _on_prepare_finished(self) -> None:
        self._prepare_worker = None
        self._set_running(False, self.status.text())
        self.progress.setRange(0, 100)
        self.progress.setValue(100 if self._preparation is not None else 0)

    def _approve_and_run(self) -> None:
        if self._preparation is None or not self._preparation.prepared:
            return
        prepared = self._preparation.prepared
        details = "\n".join(
            f"• {item.video.display_name}: {len(item.plan.sample_timestamps_sec)}프레임, "
            f"{format_time(item.plan.range_start_sec)}–{format_time(item.plan.range_end_sec)}"
            for item in prepared
        )
        answer = QMessageBox.question(
            self,
            "일괄 분석 계획 승인",
            f"다음 {len(prepared)}개 영상의 표시된 계획을 승인하고 순차 실행할까요?\n\n"
            f"사용 모델: {self._config.model_label}\n{details}\n\n"
            "영상별 결과는 따로 저장되며, 실패를 시연용 결과로 바꾸지 않습니다.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._set_running(True, f"{len(prepared)}개 영상 일괄 분석을 시작합니다.")
        self.progress.setRange(0, 0)
        self.run_button.setEnabled(False)
        self._set_selected_status(tuple(item.video for item in prepared), "분석 대기")
        try:
            worker = self._config.make_execution_worker(prepared)
        except Exception as exc:
            self._set_running(False, str(exc) or exc.__class__.__name__)
            return
        worker.progress_changed.connect(self._on_progress)
        worker.result_ready.connect(self._on_result)
        worker.finished.connect(self._on_execute_finished)
        self._execute_worker = worker
        worker.start()

    def _on_result(self, job: AnalysisJob) -> None:
        self._results.append(job)
        status = {
            JobStatus.COMPLETED: "완료",
            JobStatus.PARTIAL: "일부 완료",
            JobStatus.FAILED: "실패",
            JobStatus.CANCELED: "취소",
        }.get(job.status, job.status.value)
        for row in range(self.table.rowCount()):
            if self.table.item(row, 1).data(Qt.ItemDataRole.UserRole) == job.video_id:
                self.table.item(row, 3).setText(status)
                if job.error:
                    self.table.item(row, 3).setToolTip(job.error)
                break
        self.result_ready.emit(job)

    def _on_execute_finished(self) -> None:
        self._execute_worker = None
        completed = sum(
            item.status in {JobStatus.COMPLETED, JobStatus.PARTIAL} for item in self._results
        )
        failed = sum(item.status is JobStatus.FAILED for item in self._results)
        canceled = sum(item.status is JobStatus.CANCELED for item in self._results)
        message = f"일괄 분석 종료 · 결과 {completed}건"
        if failed:
            message += f" · 실패 {failed}건"
        if canceled:
            message += f" · 취소 {canceled}건"
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self._set_running(False, message)
        self.close_button.setText("완료")

    def _on_progress(self, _value: object, message: str) -> None:
        self.status.setText(message)
        for row in range(self.table.rowCount()):
            name = self.table.item(row, 1).text()
            if name in message:
                self.table.item(row, 3).setText("처리 중")
                break

    def _cancel(self) -> None:
        worker = self._prepare_worker or self._execute_worker
        if worker is not None and worker.isRunning():
            worker.request_cancel()
            self.cancel_button.setEnabled(False)
            self.status.setText("현재 작업을 정리한 뒤 대기열을 중단합니다.")

    def _set_running(self, running: bool, message: str) -> None:
        self.prepare_button.setEnabled(not running)
        self.cancel_button.setEnabled(running)
        self.close_button.setEnabled(not running)
        for row in range(self.table.rowCount()):
            box = self.table.cellWidget(row, 0)
            if box is not None:
                box.setEnabled(not running)
        self.progress.setVisible(running or self._preparation is not None)
        self.status.setText(message)
        self.running_changed.emit(running)

    def _set_selected_status(
        self,
        selected: tuple[BatchVideoInput, ...],
        status: str,
    ) -> None:
        ids = {item.video_id for item in selected}
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 1)
            if item.data(Qt.ItemDataRole.UserRole) in ids:
                self.table.item(row, 3).setText(status)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override name
        if self.is_running:
            QMessageBox.information(self, "일괄 분석", "작업을 취소한 뒤 창을 닫아 주세요.")
            event.ignore()
            return
        event.accept()
