from __future__ import annotations

import unittest

from video_agent.contracts import Segment
from video_agent.review.wording import tidy_seconds
from video_agent.ui.segment_detail import precise_text, segment_rows, split_time_note


def segment(**changes) -> Segment:
    values = dict(
        video_id="video-a",
        start_sec=4.005,
        end_sec=36.035,
        summary="모든 프레임에서 여성이 유아를 밀고 있는 동일한 자세 유지",
        evidence=(
            "여성은 항상 유아의 머리 위에 손을 뻗어 있음",
            "유아는 모든 시각에서 같은 방향으로 고정된 자세로 앉음",
        ),
        uncertainty=(
            "모든 프레임에서 동일한 자세 유지하므로 반복성 확실, 하지만 행동 변화 없음 "
            "앱 시간 검증: 모델 제시 확인 범위 5.000~35.000초가 근거 프레임을 포함하지 않아 "
            "근거 시각 기준 4.005~36.035초로 대체했습니다."
        ),
        source_frame_ids=("a:5005", "a:15015", "a:25025", "a:35035"),
        source_timestamps_sec=(5.005, 15.015, 25.025, 35.035),
    )
    values.update(changes)
    return Segment(**values)


class SegmentDetailTests(unittest.TestCase):
    def test_reported_candidate_reads_short_with_minutes_and_seconds(self) -> None:
        rows = dict(segment_rows(segment(), "여성이 반복해서 유모차를 미는 장면", 1.0))
        self.assertEqual(rows["확인 범위"], "00:04–00:36 (00:03부터 재생)")
        self.assertEqual(
            rows["근거"],
            "• 여성은 항상 유아의 머리 위에 손을 뻗어 있음\n"
            "• 유아는 모든 시각에서 같은 방향으로 고정된 자세로 앉음",
        )
        self.assertEqual(rows["관찰 시각"], "00:05 · 00:15 · 00:25 · 00:35 (4장, 10초 간격)")
        self.assertEqual(rows["확인 필요"], "중간 동작과 실제 반복 횟수는 원본에서 확인")
        self.assertEqual(
            rows["불확실성"], "모든 프레임에서 동일한 자세 유지하므로 반복성 확실, 하지만 행동 변화 없음"
        )
        self.assertEqual(
            rows["시간 보정"], "AI가 제시한 범위(00:05–00:35)에 근거 사진이 빠져 근거 시각으로 바꿈"
        )
        self.assertNotIn("재생 시작", rows)
        self.assertFalse(any(".000" in value or ".005" in value for value in rows.values()))

    def test_model_seconds_read_as_whole_seconds(self) -> None:
        self.assertEqual(tidy_seconds("6.0초 프레임에서 관찰됨"), "6초 프레임에서 관찰됨")
        self.assertEqual(tidy_seconds("5.005~35.035초 사이"), "5~35초 사이")
        self.assertEqual(tidy_seconds("12초 동안, 버전 2.0"), "12초 동안, 버전 2.0")
        rows = dict(segment_rows(segment(evidence=("6.0초 프레임에서 관찰됨",)), None, 1.0))
        self.assertEqual(rows["근거"], "6초 프레임에서 관찰됨")

    def test_exact_times_stay_available_for_staff(self) -> None:
        self.assertIn("4.005–36.035초", precise_text(segment()))
        self.assertIn("5.005, 15.015", precise_text(segment()))

    def test_uncertainty_without_time_check_is_kept_as_is(self) -> None:
        self.assertEqual(split_time_note("원본 확인 필요"), ("원본 확인 필요", ""))
        rounded = split_time_note(
            "희소 프레임 앱 시간 검증: 모델에 표시된 0.001초 단위 시각과 원본 PTS의 반올림 차이를 "
            "보정해 근거 프레임을 포함했습니다."
        )
        self.assertEqual(rounded, ("희소 프레임", "근거 사진 시각에 맞춰 범위를 조금 넓힘"))


if __name__ == "__main__":
    unittest.main()
