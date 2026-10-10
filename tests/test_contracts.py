from __future__ import annotations

import unittest

from video_agent.contracts import (
    AnalysisClue,
    AnalysisJob,
    CaseRecord,
    CaseVideoLink,
    ClueSource,
    InformationCertainty,
    JobStatus,
    ModelExecutionInfo,
    MotionProposal,
    MotionScanResult,
    ResultOrigin,
    Segment,
)


class CaseContractTests(unittest.TestCase):
    def test_case_round_trip_separates_management_info_and_selected_clues(self) -> None:
        record = CaseRecord(
            case_id="case-1",
            revision=2,
            title="문 앞 택배 이동 확인",
            case_type="관리용 분류",
            occurrence_time_text="20시 전후",
            occurrence_time_certainty=InformationCertainty.ESTIMATED,
            management_notes="담당자 메모",
            clues=(
                AnalysisClue(
                    clue_id="clue-1",
                    text="신고자는 사람이 상자를 들었다고 설명함",
                    source=ClueSource.REPORT,
                    certainty=InformationCertainty.ESTIMATED,
                ),
                AnalysisClue(
                    clue_id="clue-2",
                    text="내부 관리 메모",
                    use_for_analysis=False,
                ),
            ),
            video_links=(
                CaseVideoLink(
                    video_id="video-1",
                    camera_location="공동현관",
                    clock_offset_seconds=2.5,
                    clock_certainty=InformationCertainty.CONFIRMED,
                ),
            ),
        )

        restored = CaseRecord.from_dict(record.to_dict())

        self.assertEqual(restored, record)
        self.assertEqual(restored.analysis_clues, (record.clues[0],))

    def test_case_rejects_duplicate_clue_ids(self) -> None:
        clue = AnalysisClue(clue_id="same", text="첫 단서")
        with self.assertRaisesRegex(ValueError, "unique"):
            CaseRecord(
                case_id="case-1",
                revision=1,
                title="사건",
                clues=(clue, AnalysisClue(clue_id="same", text="둘째 단서")),
            )


class AnalysisJobTests(unittest.TestCase):
    def test_unknown_progress_is_explicitly_allowed(self) -> None:
        job = AnalysisJob(
            job_id="job-1",
            status=JobStatus.RUNNING,
            progress=None,
            video_id="video-1",
        )
        self.assertIsNone(job.progress)

    def test_out_of_range_progress_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            AnalysisJob(
                job_id="job-1",
                status=JobStatus.RUNNING,
                progress=101,
                video_id="video-1",
            )

    def test_failed_job_requires_error(self) -> None:
        with self.assertRaises(ValueError):
            AnalysisJob(
                job_id="job-1",
                status=JobStatus.FAILED,
                progress=None,
                video_id="video-1",
            )

    def test_segment_cannot_exceed_video_duration(self) -> None:
        job = AnalysisJob(
            job_id="job-1",
            status=JobStatus.COMPLETED,
            progress=100,
            video_id="video-1",
            origin=ResultOrigin.SAMPLE,
            segments=(
                Segment(
                    video_id="video-1",
                    start_sec=9.0,
                    end_sec=11.0,
                    summary="sample",
                    evidence=("rule",),
                    uncertainty="unknown",
                ),
            ),
        )
        with self.assertRaises(ValueError):
            job.validate_for_duration(10.0)

    def test_batch_candidate_counts_must_match_completed_requests(self) -> None:
        with self.assertRaisesRegex(ValueError, "candidate counts"):
            ModelExecutionInfo(
                provider_id="local",
                context_window_tokens=16384,
                max_output_tokens=800,
                temperature=0.0,
                provider_request_count=2,
                completed_request_count=2,
                input_frame_count=0,
                input_frame_dimensions=(),
                input_frame_ids=(),
                input_frame_timestamps_sec=(),
                prompt_version="candidate-recall-v2",
                batch_candidate_counts=(1,),
            )


class MotionContractTests(unittest.TestCase):
    def test_motion_proposal_is_local_preprocessing_not_analysis_result(self) -> None:
        proposal = MotionProposal(
            proposal_id="motion-1",
            video_id="video-1",
            start_sec=2.0,
            end_sec=8.0,
            peak_timestamp_sec=5.0,
            motion_score=3.2,
            recommended_sampling_interval_sec=0.5,
        )
        result = MotionScanResult(
            scan_id="scan-1",
            video_id="video-1",
            range_start_sec=0.0,
            range_end_sec=10.0,
            sample_interval_sec=0.5,
            sampled_frame_count=19,
            proposals=(proposal,),
        )

        self.assertEqual(result.proposals, (proposal,))

    def test_motion_proposal_cannot_exceed_scanned_range(self) -> None:
        proposal = MotionProposal(
            proposal_id="motion-1",
            video_id="video-1",
            start_sec=2.0,
            end_sec=8.0,
            peak_timestamp_sec=5.0,
            motion_score=3.2,
            recommended_sampling_interval_sec=0.5,
        )
        with self.assertRaisesRegex(ValueError, "exceeds scanned range"):
            MotionScanResult(
                scan_id="scan-1",
                video_id="video-1",
                range_start_sec=3.0,
                range_end_sec=10.0,
                sample_interval_sec=0.5,
                sampled_frame_count=10,
                proposals=(proposal,),
            )


if __name__ == "__main__":
    unittest.main()
