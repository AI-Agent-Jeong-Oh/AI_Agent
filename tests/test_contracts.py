from __future__ import annotations

import unittest

from video_agent.contracts import (
    AnalysisClue,
    AnalysisJob,
    CandidateReview,
    CaseRecord,
    CaseVideoLink,
    ClueSource,
    InformationCertainty,
    JobStatus,
    ModelExecutionInfo,
    MotionProposal,
    MotionScanResult,
    ResultOrigin,
    ReviewJudgement,
    ReviewSession,
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


class ReviewContractTests(unittest.TestCase):
    def job(self) -> AnalysisJob:
        return AnalysisJob(
            job_id="job-review",
            status=JobStatus.COMPLETED,
            progress=100,
            video_id="video-review",
            origin=ResultOrigin.SAMPLE,
            segments=(
                Segment(
                    video_id="video-review",
                    start_sec=1.0,
                    end_sec=2.0,
                    summary="후보",
                    evidence=("1.5초 프레임에서 관찰됨",),
                    uncertainty="원본 확인 필요",
                ),
            ),
        )

    def session(self, *reviews: CandidateReview) -> ReviewSession:
        return ReviewSession(
            review_id="review-1",
            job_id="job-review",
            video_id="video-review",
            started_at="2026-10-10T22:00:00+09:00",
            case_id="case-1",
            reviews=reviews,
        )

    def test_review_session_round_trip(self) -> None:
        review = CandidateReview(
            job_id="job-review",
            segment_index=0,
            judgement=ReviewJudgement.RELEVANT,
            memo="1.5초에 손이 상자에 닿음",
            tags=(" 손 접촉 ", "재확인"),
            bookmarked=True,
            reviewed_at="2026-10-10T22:01:00+09:00",
        )
        session = self.session(review)

        restored = ReviewSession.from_dict(session.to_dict())

        self.assertEqual(restored, session)
        self.assertEqual(restored.review_for(0).tags, ("손 접촉", "재확인"))
        self.assertIsNone(restored.review_for(1))
        self.assertEqual(session.to_dict()["reviews"][0]["judgement"], "relevant")

    def test_review_defaults_to_unreviewed(self) -> None:
        review = CandidateReview(job_id="job-review", segment_index=0)
        self.assertIs(review.judgement, ReviewJudgement.UNREVIEWED)
        self.assertEqual(review.review_seconds, 0.0)

    def test_review_rejects_invalid_values(self) -> None:
        invalid = (
            {"segment_index": -1},
            {"memo": "가" * 4001},
            {"tags": tuple(f"t{i}" for i in range(21))},
            {"tags": ("",)},
            {"tags": ("가" * 51,)},
            {"tags": ("a", " a ")},
            {"reviewed_at": "어제"},
            {"review_seconds": -1.0},
            {"judgement": "guilty"},
        )
        for overrides in invalid:
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                CandidateReview(**{"job_id": "job-review", "segment_index": 0, **overrides})

    def test_session_rejects_foreign_or_duplicate_reviews(self) -> None:
        with self.assertRaises(ValueError):
            self.session(CandidateReview(job_id="other-job", segment_index=0))
        with self.assertRaises(ValueError):
            self.session(
                CandidateReview(job_id="job-review", segment_index=0),
                CandidateReview(job_id="job-review", segment_index=0),
            )
        with self.assertRaises(ValueError):
            ReviewSession(
                review_id="review-1",
                job_id="job-review",
                video_id="video-review",
                started_at="not-a-time",
            )

    def test_session_rejects_unknown_fields(self) -> None:
        data = self.session().to_dict()
        data["model_override"] = "relevant"
        with self.assertRaises(ValueError):
            ReviewSession.from_dict(data)
        data = self.session(CandidateReview(job_id="job-review", segment_index=0)).to_dict()
        data["reviews"][0]["prompt"] = "x"
        with self.assertRaises(ValueError):
            ReviewSession.from_dict(data)

    def test_session_must_match_result(self) -> None:
        self.session(CandidateReview(job_id="job-review", segment_index=0)).validate_against(
            self.job()
        )
        with self.assertRaises(ValueError):
            self.session(CandidateReview(job_id="job-review", segment_index=1)).validate_against(
                self.job()
            )
        other_video = ReviewSession(
            review_id="review-1",
            job_id="job-review",
            video_id="other-video",
            started_at="2026-10-10T22:00:00+09:00",
        )
        with self.assertRaises(ValueError):
            other_video.validate_against(self.job())
