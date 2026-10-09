from __future__ import annotations

import unittest

from video_agent.contracts import AnalysisJob, JobStatus, Segment


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


if __name__ == "__main__":
    unittest.main()
