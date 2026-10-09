from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from video_agent.contracts import AnalysisLimits, AnalysisMode, AnalysisRequest
from video_agent.video.probe import VideoMetadata


class WorkflowState(StrEnum):
    EMPTY = "empty"
    NEEDS_CLARIFICATION = "needs_clarification"
    PLAN_READY = "plan_ready"
    ANALYSIS_APPROVED = "analysis_approved"
    APPROVED = "approved"
    RUNNING = "running"
    VALIDATING = "validating"
    COMPLETED = "completed"
    CANCELED = "canceled"
    FAILED = "failed"
    EXPANSION_PROPOSED = "expansion_proposed"


@dataclass(frozen=True, slots=True)
class AnalysisIntent:
    purpose: str
    mode: AnalysisMode = AnalysisMode.SAMPLE
    model_id: str | None = None
    range_start_sec: float = 0.0
    range_end_sec: float | None = None
    sampling_interval_sec: float | None = None
    provider_id: str | None = None
    provider_endpoint: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.mode, AnalysisMode):
            object.__setattr__(self, "mode", AnalysisMode(self.mode))


@dataclass(frozen=True, slots=True)
class ClarificationQuestion:
    question_id: str
    prompt: str
    reason: str


@dataclass(frozen=True, slots=True)
class TransferManifest:
    provider_id: str
    endpoint_origin: str
    sends_purpose_text: bool
    frame_timestamps_sec: tuple[float, ...]
    sends_pdf_content: bool = False


@dataclass(frozen=True, slots=True)
class AnalysisPlan:
    plan_id: str
    fingerprint: str
    video_id: str
    video_duration_sec: float
    purpose: str
    mode: AnalysisMode
    model_id: str | None
    provider_id: str | None
    provider_endpoint: str | None
    range_start_sec: float
    range_end_sec: float
    sampling_interval_sec: float
    sample_timestamps_sec: tuple[float, ...]
    limits: AnalysisLimits
    transfer_manifest: TransferManifest | None = None


@dataclass(frozen=True, slots=True)
class Approval:
    approval_id: str
    plan_id: str
    plan_fingerprint: str
    kind: str


class PlanningError(ValueError):
    pass


class PlanBuilder:
    _GENERIC_PURPOSES = {"분석", "영상 분석", "중요 장면", "이상행동", "확인"}

    def questions(
        self,
        intent: AnalysisIntent,
        answers: dict[str, str] | None = None,
    ) -> tuple[ClarificationQuestion, ...]:
        answers = answers or {}
        questions: list[ClarificationQuestion] = []
        purpose = " ".join(intent.purpose.split())
        if (len(purpose) < 10 or purpose in self._GENERIC_PURPOSES) and not answers.get("focus", "").strip():
            questions.append(
                ClarificationQuestion(
                    question_id="focus",
                    prompt="어떤 장면·행동·객체를 중요 구간으로 보고 싶은지 구체적으로 적어 주세요.",
                    reason="목적이 넓어 후보 구간의 판단 기준을 정할 수 없습니다.",
                )
            )
        if intent.mode is not AnalysisMode.SAMPLE and not (
            intent.model_id or answers.get("model_id") or ""
        ).strip():
            questions.append(
                ClarificationQuestion(
                    question_id="model_id",
                    prompt="영상 입력을 지원한다고 확인한 모델 ID를 입력해 주세요.",
                    reason="텍스트 전용 모델을 영상 분석 모델처럼 사용할 수 없습니다.",
                )
            )
        return tuple(questions)

    def build(
        self,
        metadata: VideoMetadata,
        intent: AnalysisIntent,
        answers: dict[str, str] | None = None,
        limits: AnalysisLimits | None = None,
    ) -> AnalysisPlan:
        answers = answers or {}
        questions = self.questions(intent, answers)
        if questions:
            raise PlanningError("추가 답변이 필요합니다: " + ", ".join(item.question_id for item in questions))

        purpose = " ".join(intent.purpose.split())
        focus = answers.get("focus", "").strip()
        if focus:
            purpose = f"{purpose}\n추가 기준: {focus}"
        model_id = (intent.model_id or answers.get("model_id") or "").strip() or None
        active_limits = limits or AnalysisLimits()
        start = intent.range_start_sec
        end = metadata.duration_seconds if intent.range_end_sec is None else intent.range_end_sec
        if not 0 <= start < end <= metadata.duration_seconds + 1e-6:
            raise PlanningError("분석 시간 범위가 원본 영상 길이를 벗어났습니다.")

        span = end - start
        interval = intent.sampling_interval_sec or max(0.25, span / active_limits.max_frames)
        if interval <= 0:
            raise PlanningError("샘플링 간격은 0보다 커야 합니다.")
        if intent.mode is AnalysisMode.SAMPLE:
            timestamps: tuple[float, ...] = ()
        else:
            count = min(active_limits.max_frames, max(1, math.ceil(span / interval)))
            step = span / count
            timestamps = tuple(
                round(min(end - 0.001, start + (index + 0.5) * step), 6)
                for index in range(count)
            )

        transfer_manifest = None
        if intent.mode is AnalysisMode.EXTERNAL:
            if not intent.provider_id or not intent.provider_endpoint:
                raise PlanningError("외부 모드는 공급자 ID와 endpoint를 명시해야 합니다.")
            endpoint = urlsplit(intent.provider_endpoint)
            if endpoint.scheme != "https" or not endpoint.netloc:
                raise PlanningError("외부 endpoint는 유효한 HTTPS 주소여야 합니다.")
            transfer_manifest = TransferManifest(
                provider_id=intent.provider_id,
                endpoint_origin=f"{endpoint.scheme}://{endpoint.netloc}",
                sends_purpose_text=True,
                frame_timestamps_sec=timestamps,
            )

        fingerprint_payload = {
            "video_id": metadata.video_id,
            "duration": metadata.duration_seconds,
            "purpose": purpose,
            "mode": intent.mode.value,
            "model_id": model_id,
            "provider_id": intent.provider_id,
            "provider_endpoint": intent.provider_endpoint,
            "range": [start, end],
            "timestamps": timestamps,
            "limits": asdict(active_limits),
            "transfer": asdict(transfer_manifest) if transfer_manifest else None,
        }
        fingerprint = hashlib.sha256(
            json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return AnalysisPlan(
            plan_id=f"plan-{uuid4().hex[:12]}",
            fingerprint=fingerprint,
            video_id=metadata.video_id,
            video_duration_sec=metadata.duration_seconds,
            purpose=purpose,
            mode=intent.mode,
            model_id=model_id,
            provider_id=intent.provider_id,
            provider_endpoint=intent.provider_endpoint,
            range_start_sec=start,
            range_end_sec=end,
            sampling_interval_sec=interval,
            sample_timestamps_sec=timestamps,
            limits=active_limits,
            transfer_manifest=transfer_manifest,
        )


class AgentSession:
    def __init__(self, builder: PlanBuilder | None = None) -> None:
        self._builder = builder or PlanBuilder()
        self.state = WorkflowState.EMPTY
        self.metadata: VideoMetadata | None = None
        self.intent: AnalysisIntent | None = None
        self.answers: dict[str, str] = {}
        self.plan: AnalysisPlan | None = None
        self.analysis_approval: Approval | None = None
        self.transfer_approval: Approval | None = None
        self.active_job_id: str | None = None
        self.expansion_reason: str | None = None
        self._execution_counts: dict[str, int] = {}

    def update_context(self, metadata: VideoMetadata | None, intent: AnalysisIntent | None) -> None:
        changed = metadata != self.metadata or intent != self.intent
        if changed and self.active_job_id is not None:
            raise PlanningError("실행 중에는 영상·목적·모드·범위를 변경할 수 없습니다.")
        self.metadata = metadata
        self.intent = intent
        if changed:
            self._invalidate_approval()
            self.answers = {}
        if metadata is None or intent is None:
            self.state = WorkflowState.EMPTY
        elif self._builder.questions(intent, self.answers):
            self.state = WorkflowState.NEEDS_CLARIFICATION
        else:
            self.state = WorkflowState.EMPTY

    def questions(self) -> tuple[ClarificationQuestion, ...]:
        if self.intent is None:
            return ()
        return self._builder.questions(self.intent, self.answers)

    def answer(self, question_id: str, value: str) -> None:
        if not value.strip():
            raise PlanningError("추가 답변은 비워 둘 수 없습니다.")
        self.answers[question_id] = value.strip()
        self._invalidate_approval()
        self.state = WorkflowState.NEEDS_CLARIFICATION if self.questions() else WorkflowState.EMPTY

    def prepare_plan(self, limits: AnalysisLimits | None = None) -> AnalysisPlan:
        if self.metadata is None or self.intent is None:
            raise PlanningError("영상과 분석 목적이 필요합니다.")
        self.plan = self._builder.build(self.metadata, self.intent, self.answers, limits)
        self.analysis_approval = None
        self.transfer_approval = None
        self.state = WorkflowState.PLAN_READY
        return self.plan

    def approve_analysis(self) -> Approval:
        if self.plan is None or self.state is not WorkflowState.PLAN_READY:
            raise PlanningError("먼저 현재 입력으로 분석 계획을 만들어야 합니다.")
        self.analysis_approval = self._new_approval("analysis")
        self.state = (
            WorkflowState.ANALYSIS_APPROVED
            if self.plan.mode is AnalysisMode.EXTERNAL
            else WorkflowState.APPROVED
        )
        return self.analysis_approval

    def approve_external_transfer(self) -> Approval:
        if self.plan is None or self.plan.mode is not AnalysisMode.EXTERNAL:
            raise PlanningError("외부 전송 승인은 외부 모드 계획에만 적용됩니다.")
        if self.analysis_approval is None:
            raise PlanningError("분석 계획을 먼저 승인해야 합니다.")
        self.transfer_approval = self._new_approval("external_transfer")
        self.state = WorkflowState.APPROVED
        return self.transfer_approval

    def create_request(self, video_path: str, job_id: str) -> AnalysisRequest:
        if self.plan is None or self.analysis_approval is None:
            raise PlanningError("승인된 분석 계획이 없습니다.")
        if self.analysis_approval.plan_fingerprint != self.plan.fingerprint:
            raise PlanningError("입력이 변경되어 분석 승인이 무효화되었습니다.")
        if self.plan.mode is AnalysisMode.EXTERNAL:
            if self.transfer_approval is None or self.transfer_approval.plan_fingerprint != self.plan.fingerprint:
                raise PlanningError("현재 전송 내용과 범위에 대한 외부 전송 승인이 필요합니다.")
        if self.active_job_id is not None:
            raise PlanningError("이미 실행 중인 작업이 있습니다.")
        execution_count = self._execution_counts.get(self.plan.fingerprint, 0)
        allowed_executions = 1 + self.plan.limits.max_reanalysis_attempts
        if execution_count >= allowed_executions:
            raise PlanningError(
                f"승인된 실행 한도 {allowed_executions}회를 이미 사용했습니다. 새 계획과 승인이 필요합니다."
            )

        request = AnalysisRequest(
            job_id=job_id,
            plan_id=self.plan.plan_id,
            approval_id=self.analysis_approval.approval_id,
            transfer_approval_id=(
                self.transfer_approval.approval_id if self.transfer_approval is not None else None
            ),
            video_id=self.plan.video_id,
            video_path=Path(video_path),
            purpose=self.plan.purpose,
            duration_seconds=self.plan.video_duration_sec,
            mode=self.plan.mode,
            model_id=self.plan.model_id,
            range_start_sec=self.plan.range_start_sec,
            range_end_sec=self.plan.range_end_sec,
            sample_timestamps_sec=self.plan.sample_timestamps_sec,
            limits=self.plan.limits,
        )
        self.active_job_id = job_id
        self._execution_counts[self.plan.fingerprint] = execution_count + 1
        self.state = WorkflowState.RUNNING
        return request

    def cancel_active(self) -> str | None:
        job_id = self.active_job_id
        self.active_job_id = None
        if job_id is not None:
            self.state = WorkflowState.CANCELED
        return job_id

    def accept_result(self, job_id: str, *, failed: bool = False, canceled: bool = False) -> bool:
        if job_id != self.active_job_id:
            return False
        self.active_job_id = None
        if canceled:
            self.state = WorkflowState.CANCELED
        else:
            self.state = WorkflowState.FAILED if failed else WorkflowState.COMPLETED
        return True

    def mark_validating(self, job_id: str) -> bool:
        if job_id != self.active_job_id:
            return False
        self.state = WorkflowState.VALIDATING
        return True

    def propose_expansion(self, reason: str) -> None:
        if self.active_job_id is not None:
            raise PlanningError("실행 중에는 분석 범위를 확대할 수 없습니다.")
        self.expansion_reason = reason.strip()
        self.state = WorkflowState.EXPANSION_PROPOSED

    def _new_approval(self, kind: str) -> Approval:
        assert self.plan is not None
        return Approval(
            approval_id=f"approval-{uuid4().hex[:12]}",
            plan_id=self.plan.plan_id,
            plan_fingerprint=self.plan.fingerprint,
            kind=kind,
        )

    def _invalidate_approval(self) -> None:
        self.plan = None
        self.analysis_approval = None
        self.transfer_approval = None
        self.expansion_reason = None
