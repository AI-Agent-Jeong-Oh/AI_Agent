from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from video_agent.contracts import (
    AnalysisClue,
    AnalysisJob,
    AnalysisMode,
    CandidateReview,
    CaseRecord,
    CaseVideoLink,
    JobStatus,
    ModelExecutionInfo,
    ResultOrigin,
    ReviewJudgement,
    ReviewSession,
    SamplingStrategy,
    Segment,
)
from video_agent.storage.json_store import (
    AppSettings,
    CaseStore,
    ResultStore,
    ReviewStore,
    SettingsStore,
)
from video_agent.storage.paths import AppPaths, CacheManager


class JsonStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.paths = AppPaths(data_dir=root / "Application Support", cache_dir=root / "Caches")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_settings_round_trip_stores_env_name_not_secret(self) -> None:
        store = SettingsStore(self.paths.settings_file)
        settings = AppSettings(
            mode=AnalysisMode.EXTERNAL,
            external_provider_id="provider",
            external_model_id="vision-model",
            external_endpoint="https://provider.invalid/v1/chat/completions",
            api_key_env_name="VIDEO_AGENT_EXTERNAL_API_KEY",
        )

        store.save(settings)

        self.assertEqual(store.load(), settings)
        text = self.paths.settings_file.read_text(encoding="utf-8")
        self.assertIn("VIDEO_AGENT_EXTERNAL_API_KEY", text)
        self.assertNotIn("sk-secret", text)

    def test_default_external_key_name_matches_openai_without_containing_a_secret(self) -> None:
        settings = AppSettings()

        self.assertEqual(settings.api_key_env_name, "OPENAI_API_KEY")
        self.assertNotIn("sk-", json.dumps(settings.to_dict()))

    def test_analysis_result_round_trip_excludes_video_path(self) -> None:
        job = AnalysisJob(
            job_id="job-1",
            plan_id="plan-1",
            status=JobStatus.COMPLETED,
            progress=100,
            video_id="video-1",
            origin=ResultOrigin.SAMPLE,
            purpose="샘플 목적",
            range_start_sec=0.0,
            range_end_sec=3.0,
            elapsed_seconds=1.25,
            segments=(
                Segment(
                    video_id="video-1",
                    start_sec=1.0,
                    end_sec=2.0,
                    summary="샘플 후보",
                    evidence=("샘플 규칙",),
                    uncertainty="실제 분석 아님",
                ),
            ),
        )
        store = ResultStore(self.paths)

        path = store.save(job)
        restored = store.load("job-1")

        self.assertEqual(restored, job)
        self.assertNotIn("video_path", json.loads(path.read_text(encoding="utf-8")))

    def test_real_model_provenance_round_trip(self) -> None:
        job = AnalysisJob(
            job_id="job-real",
            plan_id="plan-real",
            status=JobStatus.COMPLETED,
            progress=100,
            video_id="video-1",
            mode=AnalysisMode.LOCAL,
            model_id="vision-model",
            is_sample=False,
            origin=ResultOrigin.REAL_MODEL,
            purpose="사람의 발이 물체에 접촉하는 것으로 보이는 장면 후보",
            range_start_sec=0.0,
            range_end_sec=10.0,
            sample_timestamps_sec=(2.0,),
            elapsed_seconds=4.5,
            requested_frame_count=1,
            analyzed_frame_count=1,
            sampling_strategy=SamplingStrategy.TEMPORAL_SEQUENCE,
            observation_guidance=("머리 방향을 시간순으로 비교",),
            case_id="case-1",
            case_revision=2,
            analysis_clues=(
                AnalysisClue(
                    clue_id="clue-1",
                    text="신고자는 문 앞 물체가 이동했다고 설명함",
                ),
            ),
            model_execution=ModelExecutionInfo(
                provider_id="ollama-local",
                context_window_tokens=16384,
                max_output_tokens=800,
                temperature=0.0,
                provider_request_count=1,
                completed_request_count=1,
                input_frame_count=1,
                input_frame_dimensions=("320x240",),
                input_frame_ids=("video-1:2000",),
                input_frame_timestamps_sec=(2.0,),
                provider_image_count=1,
                provider_image_dimensions=("960x540",),
                input_representation="temporal_storyboard",
                provider_total_seconds=4.0,
                prompt_version="candidate-recall-v9-overview-detail-crop",
                batch_candidate_counts=(1,),
            ),
        )
        store = ResultStore(self.paths)

        store.save(job)

        self.assertEqual(store.load(job.job_id), job)

    def test_case_store_round_trip_omits_original_video_path(self) -> None:
        record = CaseRecord(
            case_id="case-package",
            revision=1,
            title="문 앞 택배 이동 확인",
            clues=(AnalysisClue(clue_id="clue-1", text="문 앞 상자 이동 신고"),),
            video_links=(CaseVideoLink(video_id="video-hash", camera_location="현관"),),
        )
        store = CaseStore(self.paths)

        path = store.save(record)

        self.assertEqual(store.load(record.case_id), record)
        self.assertEqual(store.list_records(), (record,))
        text = path.read_text(encoding="utf-8")
        self.assertNotIn("video_path", text)
        self.assertNotIn("/Users/", text)

    def test_review_store_round_trip_keeps_result_file_unchanged(self) -> None:
        job = AnalysisJob(
            job_id="job-review",
            status=JobStatus.COMPLETED,
            progress=100,
            video_id="video-review",
            origin=ResultOrigin.SAMPLE,
        )
        result_path = ResultStore(self.paths).save(job)
        result_before = result_path.read_bytes()
        store = ReviewStore(self.paths)
        session = ReviewSession(
            review_id="review-1",
            job_id="job-review",
            video_id="video-review",
            started_at="2026-10-10T22:00:00+09:00",
            reviews=(
                CandidateReview(
                    job_id="job-review",
                    segment_index=0,
                    judgement=ReviewJudgement.EXCLUDED,
                    memo="상자가 아닌 그림자",
                ),
            ),
        )

        path = store.save(session)

        self.assertEqual(path, self.paths.data_dir / "reviews" / "job-review.json")
        self.assertEqual(store.load("job-review"), session)
        self.assertEqual(result_path.read_bytes(), result_before)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_review_store_missing_file_and_unsafe_ids(self) -> None:
        store = ReviewStore(self.paths)
        self.assertIsNone(store.load("job-none"))
        with self.assertRaises(ValueError):
            store.load("../job")

    def test_review_store_rejects_file_for_another_job(self) -> None:
        store = ReviewStore(self.paths)
        session = ReviewSession(
            review_id="review-1",
            job_id="job-a",
            video_id="video-review",
            started_at="2026-10-10T22:00:00+09:00",
        )
        store.save(session)
        (self.paths.reviews_dir / "job-a.json").rename(self.paths.reviews_dir / "job-b.json")
        with self.assertRaises(ValueError):
            store.load("job-b")


class CacheManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "cache"
        self.manager = CacheManager(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_only_marked_app_cache_is_removed(self) -> None:
        owned = self.manager.create_job_dir("job-owned")
        (owned / "frame.jpg").write_bytes(b"frame")
        foreign = self.root / "foreign"
        foreign.mkdir()
        (foreign / "keep.txt").write_text("keep", encoding="utf-8")

        self.assertTrue(self.manager.remove_job_cache("job-owned"))
        self.assertFalse(owned.exists())
        self.assertTrue(foreign.exists())
        self.assertFalse(self.manager.remove_job_cache("foreign"))

    def test_cleanup_older_than_ignores_unmarked_directories(self) -> None:
        owned = self.manager.create_job_dir("job-old")
        foreign = self.root / "old-foreign"
        foreign.mkdir()
        old = time.time() - 100
        owned.touch()
        foreign.touch()
        # Directory mtime changes after marker creation; set it explicitly for deterministic cleanup.
        import os

        os.utime(owned, (old, old))
        os.utime(foreign, (old, old))

        removed = self.manager.cleanup_older_than(10, now=time.time())

        self.assertEqual(removed, ("job-old",))
        self.assertTrue(foreign.exists())

    def test_path_traversal_job_id_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.manager.create_job_dir("../outside")


if __name__ == "__main__":
    unittest.main()
