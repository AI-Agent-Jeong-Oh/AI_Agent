from __future__ import annotations

import json
import base64
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from threading import Event

from PySide6.QtWidgets import QApplication, QLabel, QWidget

from video_agent.contracts import AnalysisMode, ResultOrigin, Segment
from video_agent.identity.comparison import (
    PersonCandidateInput,
    ComparisonEvidenceQuality,
    PersonComparisonAnalyzer,
    PersonMatchDecision,
    PersonPairComparison,
)
from video_agent.models.config import ProviderConfig
from video_agent.models.provider import ProviderResponse
from video_agent.storage.json_store import PersonComparisonStore
from video_agent.storage.paths import AppPaths
from video_agent.ui.person_comparison_dialog import PersonComparisonDialog
from video_agent.video.frames import ExtractedFrame
from video_agent.video.probe import VideoMetadata


class ComparisonProbe:
    def __init__(self, ids: dict[Path, str]) -> None:
        self.ids = ids

    def probe(self, path: Path, _cancel: Event) -> VideoMetadata:
        return VideoMetadata(
            video_id=self.ids[path],
            duration_seconds=20.0,
            coded_width=640,
            coded_height=360,
            display_width=640,
            display_height=360,
            rotation_degrees=0,
            average_frame_rate=24.0,
            nominal_frame_rate=24.0,
            variable_frame_rate_detected=False,
            video_codec="h264",
            has_audio=False,
            start_time_seconds=0.0,
            stream_time_base="1/12288",
            file_size_bytes=100,
        )


class ComparisonExtractor:
    def __init__(self, frame_path: Path) -> None:
        self.frame_path = frame_path
        self.calls = 0

    def extract(self, _path, metadata, timestamps, *_args, **_kwargs):
        self.calls += 1
        return tuple(
            ExtractedFrame(
                frame_id=f"{metadata.video_id}:{int(timestamp * 1000)}",
                path=self.frame_path,
                requested_timestamp_sec=timestamp,
                source_timestamp_sec=timestamp,
                timestamp_accuracy="decoded_pts",
                mime_type="image/jpeg",
                sha256="0" * 64,
            )
            for timestamp in timestamps
        )


class ComparisonProvider:
    def __init__(self) -> None:
        self.config = ProviderConfig(
            mode=AnalysisMode.LOCAL,
            provider_id="local-test",
            endpoint="http://127.0.0.1:11434/api/chat",
            model_id="vision-model",
            protocol="ollama_native",
            vision_capability_confirmed=True,
        )
        self.calls = []

    def analyze(
        self,
        prompt,
        frames,
        *,
        timeout_seconds,
        cancel_event,
        response_format=None,
    ) -> ProviderResponse:
        self.calls.append((prompt, frames, timeout_seconds, response_format))
        return ProviderResponse(
            provider_id="test-provider",
            model_id=self.config.model_id,
            content=json.dumps(
                {
                    "decision": "inconclusive",
                    "observable_similarities": ["어두운 상의가 관찰됨"],
                    "observable_differences": [],
                    "limitations": ["얼굴이 작고 조명이 서로 다름"],
                    "appearance_similarity_score": 58,
                    "evidence_quality": "limited",
                },
                ensure_ascii=False,
            ),
            is_test_double=True,
        )


class PersonComparisonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.frame_path = self.root / "frame.jpg"
        self.frame_path.write_bytes(
            base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
            )
        )
        self.candidates = tuple(self.candidate(index) for index in range(3))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def candidate(self, index: int) -> PersonCandidateInput:
        video_id = f"video-{index}"
        path = self.root / f"cam-{index}.mp4"
        path.write_bytes(b"video")
        return PersonCandidateInput(
            video_id=video_id,
            video_name=f"카메라 {index}",
            video_path=path,
            job_id=f"job-{index}",
            segment_index=0,
            segment=Segment(
                video_id=video_id,
                start_sec=2.0,
                end_sec=8.0,
                summary="사람이 물체를 들고 이동하는 장면 후보",
                evidence=("프레임에서 사람과 물체가 함께 보임",),
                uncertainty="가림으로 세부 외형 확인 필요",
                source_frame_ids=(f"{video_id}:3000", f"{video_id}:7000"),
                source_timestamps_sec=(3.0, 7.0),
            ),
        )

    def test_three_videos_produce_all_three_pairwise_candidates(self) -> None:
        provider = ComparisonProvider()
        extractor = ComparisonExtractor(self.frame_path)
        paths = {item.video_path: item.video_id for item in self.candidates}
        analyzer = PersonComparisonAnalyzer(
            provider,
            lambda report_id: self.root / report_id,
            probe=ComparisonProbe(paths),
            extractor=extractor,
        )

        evidence = []
        report = analyzer.compare(
            "case-1",
            self.candidates,
            Event(),
            lambda *_args: None,
            lambda candidate_id, frames: evidence.append((candidate_id, frames)),
        )

        self.assertEqual(report.attempted_pair_count, 3)
        self.assertEqual(report.completed_pair_count, 3)
        self.assertEqual(report.origin, ResultOrigin.TEST_DOUBLE)
        self.assertEqual(extractor.calls, 3)
        self.assertEqual(len(provider.calls), 3)
        self.assertEqual(len(evidence), 3)
        self.assertTrue(all(item.decision is PersonMatchDecision.INCONCLUSIVE for item in report.comparisons))
        self.assertTrue(
            all(item.appearance_similarity_score == 58 for item in report.comparisons)
        )
        self.assertTrue(
            all(
                item.evidence_quality is ComparisonEvidenceQuality.LIMITED
                for item in report.comparisons
            )
        )
        self.assertTrue(all(call[3]["properties"]["decision"] for call in provider.calls))
        self.assertTrue(all("범죄 여부" in call[0] for call in provider.calls))
        self.assertNotIn("video_path", json.dumps(report.to_dict(), ensure_ascii=False))

    def test_report_storage_round_trip_omits_local_paths(self) -> None:
        provider = ComparisonProvider()
        analyzer = PersonComparisonAnalyzer(
            provider,
            lambda report_id: self.root / report_id,
            probe=ComparisonProbe({item.video_path: item.video_id for item in self.candidates}),
            extractor=ComparisonExtractor(self.frame_path),
        )
        report = analyzer.compare("case-1", self.candidates, Event(), lambda *_args: None)
        paths = AppPaths(self.root / "data", self.root / "cache")
        store = PersonComparisonStore(paths)

        saved = store.save(report)

        self.assertEqual(store.load(report.report_id), report)
        self.assertEqual(store.list_for_case("case-1"), (report,))
        self.assertNotIn(str(self.root), saved.read_text(encoding="utf-8"))
        self.assertEqual(saved.stat().st_mode & 0o777, 0o600)

    def test_criminal_identity_conclusion_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "criminal identity"):
            PersonPairComparison(
                left_candidate_id="job-a:0",
                right_candidate_id="job-b:0",
                decision=PersonMatchDecision.SIMILAR_CANDIDATE,
                observable_similarities=("범인 확정",),
                observable_differences=(),
                limitations=("원본 확인 필요",),
                left_frame_ids=("a:1000",),
                right_frame_ids=("b:1000",),
                appearance_similarity_score=90,
                evidence_quality=ComparisonEvidenceQuality.GOOD,
            )


class PersonComparisonDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_dialog_fits_inside_the_main_window_before_and_after_results(self) -> None:
        fixture = PersonComparisonTests()
        fixture.setUp()
        parent = QWidget()
        parent.resize(980, 720)
        dialog = None
        try:
            dialog = PersonComparisonDialog(fixture.candidates, lambda _selected: None, parent)
            self.assertLess(dialog.width(), parent.width())
            self.assertLess(dialog.height(), parent.height())
            self.assertLessEqual(dialog.height(), 360)
            dialog._fit_to_parent(result_visible=True)
            self.assertLess(dialog.width(), parent.width())
            self.assertLess(dialog.height(), parent.height())
            self.assertIsInstance(dialog.result_detail_scroll, QWidget)
        finally:
            if dialog is not None:
                dialog.close()
            fixture.tearDown()

    def test_result_shows_similarity_reference_and_used_evidence_photos(self) -> None:
        fixture = PersonComparisonTests()
        fixture.setUp()
        dialog = None
        try:
            provider = ComparisonProvider()
            extractor = ComparisonExtractor(fixture.frame_path)
            analyzer = PersonComparisonAnalyzer(
                provider,
                lambda report_id: fixture.root / report_id,
                probe=ComparisonProbe(
                    {item.video_path: item.video_id for item in fixture.candidates}
                ),
                extractor=extractor,
            )
            evidence = []
            report = analyzer.compare(
                "case-1",
                fixture.candidates,
                Event(),
                lambda *_args: None,
                lambda candidate_id, frames: evidence.append((candidate_id, frames)),
            )
            dialog = PersonComparisonDialog(
                fixture.candidates,
                lambda _selected: None,
            )
            for candidate_id, frames in evidence:
                dialog._on_evidence(candidate_id, frames)
            dialog._on_report(report)

            self.assertEqual(dialog.result_table.columnCount(), 3)
            self.assertIn("58/100", dialog.result_table.item(0, 2).text())
            self.assertNotIn("신원 일치 확률", dialog.result_table.item(0, 2).text())
            self.assertIn("신원 일치 확률", dialog.score_note.text())
            self.assertIn("어두운 상의", dialog.similarities_detail.text())
            self.assertEqual(dialog.differences_detail.text(), "—")
            self.assertIn("얼굴이 작고", dialog.limitations_detail.text())
            self.assertNotIn(
                "사용 모델", " ".join(label.text() for label in dialog.findChildren(QLabel))
            )
            self.assertFalse(dialog.result_splitter.isHidden())
            self.assertFalse(dialog.photo_panel.isHidden())
            visible_images = [
                image
                for container, image, _caption in dialog._photo_slots
                if not container.isHidden()
            ]
            self.assertEqual(len(visible_images), 4)
            self.assertTrue(all(not image.pixmap().isNull() for image in visible_images))
            self.assertIn("문장마다 45자 이내", provider.calls[0][0])

            older_report = replace(
                report,
                comparisons=(
                    replace(
                        report.comparisons[0],
                        observable_similarities=("어두운 상의가 관찰됨", "밝은 신발이 관찰됨"),
                    ),
                    *report.comparisons[1:],
                ),
            )
            dialog._on_report(older_report)
            self.assertIn("외 1건", dialog.similarities_detail.text())
            self.assertFalse(dialog.detail_more_button.isHidden())
            dialog.detail_more_button.click()
            self.assertIn("밝은 신발", dialog.similarities_detail.text())
        finally:
            if dialog is not None:
                dialog.close()
            fixture.tearDown()


if __name__ == "__main__":
    unittest.main()
