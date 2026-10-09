from __future__ import annotations

import unittest

from video_agent.agent.planning import (
    AgentSession,
    AnalysisIntent,
    PlanBuilder,
    PlanningError,
    WorkflowState,
)
from video_agent.contracts import AnalysisLimits, AnalysisMode
from video_agent.video.probe import VideoMetadata


def metadata(video_id: str = "video-1") -> VideoMetadata:
    return VideoMetadata(
        video_id=video_id,
        duration_seconds=20.0,
        coded_width=1920,
        coded_height=1080,
        display_width=1920,
        display_height=1080,
        rotation_degrees=0,
        average_frame_rate=29.97,
        nominal_frame_rate=30.0,
        variable_frame_rate_detected=True,
        video_codec="h264",
        has_audio=False,
        start_time_seconds=0.0,
        stream_time_base="1/90000",
        file_size_bytes=1234,
    )


class PlanBuilderTests(unittest.TestCase):
    def test_question_is_only_required_for_missing_focus(self) -> None:
        builder = PlanBuilder()
        vague = AnalysisIntent(purpose="영상 분석")
        detailed = AnalysisIntent(purpose="출입문 앞에 사람이 오래 머무르는 장면 후보 찾기")

        self.assertEqual(builder.questions(vague)[0].question_id, "focus")
        self.assertFalse(builder.questions(detailed))

    def test_plan_samples_by_timestamp_within_limit(self) -> None:
        plan = PlanBuilder().build(
            metadata(),
            AnalysisIntent(
                purpose="출입문 앞에 사람이 오래 머무르는 장면 후보 찾기",
                mode=AnalysisMode.LOCAL,
                model_id="vision-model",
                range_start_sec=5.0,
                range_end_sec=10.0,
            ),
            limits=AnalysisLimits(max_frames=3),
        )

        self.assertEqual(len(plan.sample_timestamps_sec), 3)
        self.assertTrue(all(5.0 <= item < 10.0 for item in plan.sample_timestamps_sec))


class AgentSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = AgentSession()
        self.intent = AnalysisIntent(purpose="출입문 앞에 사람이 오래 머무르는 장면 후보 찾기")
        self.session.update_context(metadata(), self.intent)

    def test_execution_is_blocked_before_approval(self) -> None:
        self.session.prepare_plan()
        with self.assertRaises(PlanningError):
            self.session.create_request("/tmp/example.mp4", "job-1")

    def test_context_change_invalidates_existing_approval(self) -> None:
        self.session.prepare_plan()
        self.session.approve_analysis()
        self.assertEqual(self.session.state, WorkflowState.APPROVED)

        changed = AnalysisIntent(purpose="주차 차량 주변의 쓰러짐 장면 후보 찾기")
        self.session.update_context(metadata(), changed)

        self.assertIsNone(self.session.plan)
        self.assertIsNone(self.session.analysis_approval)
        with self.assertRaises(PlanningError):
            self.session.create_request("/tmp/example.mp4", "job-1")

    def test_external_transfer_needs_separate_approval(self) -> None:
        intent = AnalysisIntent(
            purpose="출입문 앞에 사람이 오래 머무르는 장면 후보 찾기",
            mode=AnalysisMode.EXTERNAL,
            model_id="vision-model",
            provider_id="configured-provider",
            provider_endpoint="https://provider.invalid/v1/chat/completions",
        )
        self.session.update_context(metadata(), intent)
        self.session.prepare_plan()
        self.session.approve_analysis()
        self.assertEqual(self.session.state, WorkflowState.ANALYSIS_APPROVED)
        with self.assertRaises(PlanningError):
            self.session.create_request("/tmp/example.mp4", "job-1")

        self.session.approve_external_transfer()
        self.assertEqual(self.session.state, WorkflowState.APPROVED)
        request = self.session.create_request("/tmp/example.mp4", "job-1")
        self.assertEqual(request.mode, AnalysisMode.EXTERNAL)
        self.assertIsNotNone(request.transfer_approval_id)

    def test_canceled_job_rejects_late_result(self) -> None:
        self.session.prepare_plan()
        self.session.approve_analysis()
        self.session.create_request("/tmp/example.mp4", "job-1")

        self.assertEqual(self.session.cancel_active(), "job-1")
        self.assertFalse(self.session.accept_result("job-1"))
        self.assertEqual(self.session.state, WorkflowState.CANCELED)

    def test_execution_count_limit_blocks_unapproved_repeat(self) -> None:
        self.session.prepare_plan()
        self.session.approve_analysis()
        self.session.create_request("/tmp/example.mp4", "job-1")
        self.assertTrue(self.session.accept_result("job-1"))

        with self.assertRaises(PlanningError):
            self.session.create_request("/tmp/example.mp4", "job-2")

    def test_scope_expansion_is_only_a_proposal(self) -> None:
        self.session.propose_expansion("분석 범위를 전체 영상으로 넓혀야 합니다.")
        self.assertEqual(self.session.state, WorkflowState.EXPANSION_PROPOSED)
        self.assertIsNone(self.session.active_job_id)


if __name__ == "__main__":
    unittest.main()
