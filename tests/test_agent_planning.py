from __future__ import annotations

import unittest

from video_agent.agent.planning import (
    AgentSession,
    AnalysisIntent,
    PlanBuilder,
    PlanningError,
    WorkflowState,
)
from video_agent.contracts import AnalysisLimits, AnalysisMode, SamplingStrategy
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
    def test_questions_cover_missing_focus_and_vague_duration(self) -> None:
        builder = PlanBuilder()
        vague = AnalysisIntent(purpose="영상 분석")
        vague_duration = AnalysisIntent(
            purpose="출입문 앞에 사람이 오래 머무르는 장면 후보 찾기"
        )
        explicit_duration = AnalysisIntent(
            purpose="출입문 앞에 사람이 30초 이상 머무르는 장면 후보 찾기"
        )

        self.assertEqual(builder.questions(vague)[0].question_id, "focus")
        self.assertEqual(
            builder.questions(vague_duration)[0].question_id,
            "duration_threshold",
        )
        self.assertFalse(builder.questions(explicit_duration))

    def test_duration_answer_is_included_in_approved_purpose(self) -> None:
        intent = AnalysisIntent(
            purpose="출입문 앞에 사람이 오래 머무르는 장면 후보 찾기"
        )

        plan = PlanBuilder().build(
            metadata(),
            intent,
            answers={"duration_threshold": "30초 이상"},
        )

        self.assertIn("지속 시간 기준: 30초 이상", plan.purpose)

    def test_direction_change_purpose_uses_temporal_sequence_and_observable_guidance(self) -> None:
        plan = PlanBuilder().build(
            metadata(),
            AnalysisIntent(
                purpose="사람이 주변을 두리번거리는 장면 후보 찾기",
                mode=AnalysisMode.LOCAL,
                model_id="vision-model",
                range_start_sec=2.0,
                range_end_sec=14.0,
            ),
        )

        self.assertEqual(plan.sampling_strategy, SamplingStrategy.TEMPORAL_SEQUENCE)
        self.assertEqual(len(plan.sample_timestamps_sec), 12)
        self.assertTrue(plan.observation_guidance)
        self.assertIn("의도나 심리", plan.observation_guidance[0])
        self.assertIn("머리·얼굴·몸통", plan.observation_guidance[0])

    def test_repeated_button_purpose_keeps_temporal_and_fine_detail_guidance(self) -> None:
        plan = PlanBuilder().build(
            metadata(),
            AnalysisIntent(
                purpose="사람의 손가락이 초인종 버튼을 반복해서 누르는 장면 후보 찾기",
                mode=AnalysisMode.LOCAL,
                model_id="vision-model",
                range_start_sec=2.0,
                range_end_sec=14.0,
            ),
        )

        self.assertEqual(plan.sampling_strategy, SamplingStrategy.TEMPORAL_SEQUENCE)
        self.assertEqual(len(plan.observation_guidance), 2)
        self.assertIn("손·손가락", plan.observation_guidance[0])
        self.assertIn("버튼·물체", plan.observation_guidance[0])
        self.assertIn("반복되는지", plan.observation_guidance[1])

    def test_sampling_strategy_change_invalidates_fingerprint(self) -> None:
        intent = AnalysisIntent(
            purpose="사람이 주변을 두리번거리는 장면 후보 찾기",
            mode=AnalysisMode.LOCAL,
            model_id="vision-model",
        )
        temporal = PlanBuilder().build(metadata(), intent)
        uniform = PlanBuilder().build(
            metadata(),
            AnalysisIntent(
                purpose=intent.purpose,
                mode=intent.mode,
                model_id=intent.model_id,
                sampling_strategy=SamplingStrategy.UNIFORM,
            ),
        )

        self.assertNotEqual(temporal.fingerprint, uniform.fingerprint)

    def test_plan_samples_by_timestamp_within_limit(self) -> None:
        plan = PlanBuilder().build(
            metadata(),
            AnalysisIntent(
                purpose="출입문 앞에 사람이 30초 이상 머무르는 장면 후보 찾기",
                mode=AnalysisMode.LOCAL,
                model_id="vision-model",
                range_start_sec=5.0,
                range_end_sec=10.0,
            ),
            limits=AnalysisLimits(max_frames=3),
        )

        self.assertEqual(len(plan.sample_timestamps_sec), 3)
        self.assertTrue(all(5.0 <= item < 10.0 for item in plan.sample_timestamps_sec))

    def test_missing_model_is_not_accepted_as_a_clarification_answer(self) -> None:
        builder = PlanBuilder()
        intent = AnalysisIntent(
            purpose="출입문 앞에 사람이 30초 이상 머무르는 장면 후보 찾기",
            mode=AnalysisMode.LOCAL,
        )

        self.assertFalse(builder.questions(intent))
        with self.assertRaisesRegex(PlanningError, "모델 입력란"):
            builder.build(metadata(), intent, answers={"model_id": "질문이 표시된 경우에"})

    def test_approved_short_window_uses_dense_timestamp_spacing(self) -> None:
        plan = PlanBuilder().build(
            metadata(),
            AnalysisIntent(
                purpose="사람의 발과 물체 위치 변화가 보이는 장면 후보 찾기",
                mode=AnalysisMode.LOCAL,
                model_id="vision-model",
                range_start_sec=8.0,
                range_end_sec=14.0,
                sampling_interval_sec=0.5,
            ),
        )

        self.assertEqual(len(plan.sample_timestamps_sec), 12)
        self.assertAlmostEqual(plan.sampling_interval_sec, 0.5)
        self.assertAlmostEqual(
            plan.sample_timestamps_sec[1] - plan.sample_timestamps_sec[0],
            0.5,
        )

    def test_explicit_clustered_timestamps_are_preserved_in_the_approved_plan(self) -> None:
        timestamps = (2.0, 2.25, 2.5, 2.75, 12.0, 12.25, 12.5, 12.75)

        plan = PlanBuilder().build(
            metadata(),
            AnalysisIntent(
                purpose="사람과 물체의 위치 변화가 보이는 장면 후보 찾기",
                mode=AnalysisMode.LOCAL,
                model_id="vision-model",
                range_start_sec=0.0,
                range_end_sec=15.0,
                sample_timestamps_sec=timestamps,
            ),
        )

        self.assertEqual(plan.sample_timestamps_sec, timestamps)
        self.assertAlmostEqual(plan.sampling_interval_sec, 0.25)

    def test_explicit_timestamps_outside_the_approved_range_are_rejected(self) -> None:
        with self.assertRaisesRegex(PlanningError, "분석 범위"):
            PlanBuilder().build(
                metadata(),
                AnalysisIntent(
                    purpose="사람과 물체의 위치 변화가 보이는 장면 후보 찾기",
                    mode=AnalysisMode.LOCAL,
                    model_id="vision-model",
                    range_start_sec=5.0,
                    range_end_sec=10.0,
                    sample_timestamps_sec=(4.5, 6.0),
                ),
            )


class AgentSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = AgentSession()
        self.intent = AnalysisIntent(
            purpose="출입문 앞에 사람이 30초 이상 머무르는 장면 후보 찾기"
        )
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
            purpose="출입문 앞에 사람이 30초 이상 머무르는 장면 후보 찾기",
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
