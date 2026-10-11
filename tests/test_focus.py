from __future__ import annotations

import unittest

from PySide6.QtWidgets import QApplication

from video_agent.agent.planning import AnalysisIntent, PlanBuilder
from video_agent.agent.situations import (
    DEFAULT_PURPOSES,
    RECOMMENDED,
    SITUATION_LABELS,
    focus_guidance,
)
from video_agent.contracts import CaseRecord, FocusSituation
from video_agent.ui.focus_panel import FocusSection
from video_agent.video.probe import VideoMetadata


def metadata() -> VideoMetadata:
    from test_ui_workflow import metadata as ui_metadata

    return ui_metadata()


class FocusContractTests(unittest.TestCase):
    def test_case_record_keeps_focus_and_hints_and_reads_older_files(self) -> None:
        record = CaseRecord(
            case_id="case-a",
            revision=1,
            title="실종 확인",
            case_type="실종",
            focus_situations=(FocusSituation.MOVEMENT, FocusSituation.STAY),
            focus_hints=("보라색 조끼",),
        )
        data = record.to_dict()
        self.assertEqual(data["focus_situations"], ["movement", "stay"])
        self.assertEqual(CaseRecord.from_dict(data), record)
        older = {key: value for key, value in data.items() if not key.startswith("focus_")}
        self.assertEqual(CaseRecord.from_dict(older).focus_situations, ())

    def test_invalid_focus_values_are_rejected(self) -> None:
        base = dict(case_id="case-a", revision=1, title="사건")
        for changes in (
            {"focus_situations": ("movement", "movement")},
            {"focus_situations": ("fight",)},
            {"focus_hints": ("가" * 31,)},
            {"focus_hints": ("가방", "가방")},
            {"focus_hints": tuple(f"힌트 {index}" for index in range(11))},
            {"focus_hints": (" ",)},
        ):
            with self.subTest(changes), self.assertRaises(ValueError):
                CaseRecord(**base, **changes)


class FocusPlanningTests(unittest.TestCase):
    def build(self, **changes):
        intent = AnalysisIntent(purpose="사람이 출입문을 지나가는 장면 후보 찾기", **changes)
        return PlanBuilder().build(metadata(), intent)

    def test_checked_situations_and_hints_become_observation_guidance(self) -> None:
        plan = self.build(
            focus_situations=(FocusSituation.MOVEMENT,),
            focus_hints=("가방을 든 사람",),
        )
        guidance = "\n".join(plan.observation_guidance)
        self.assertIn("우선 확인 상황", guidance)
        self.assertIn("이동·동선(화면 진입·퇴장", guidance)
        self.assertIn("선택하지 않은 상황도 관찰", guidance)
        self.assertIn("범죄 여부는 판단하지 않음", guidance)
        self.assertIn("가방을 든 사람", guidance)
        self.assertIn("영상 관찰 사실이 아님", guidance)

    def test_no_choice_adds_nothing_and_a_change_needs_a_new_plan(self) -> None:
        self.assertEqual(focus_guidance((), ()), ())
        plain = self.build()
        focused = self.build(focus_situations=(FocusSituation.STAY,))
        self.assertNotEqual(plain.fingerprint, focused.fingerprint)

    def test_every_case_type_recommends_known_groups_and_has_a_purpose(self) -> None:
        for case_type, groups in RECOMMENDED.items():
            with self.subTest(case_type):
                self.assertTrue(groups)
                self.assertTrue(all(group in SITUATION_LABELS for group in groups))
                self.assertTrue(DEFAULT_PURPOSES[case_type])


class FocusSectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.section = FocusSection()

    def tearDown(self) -> None:
        self.section.close()

    def test_case_type_checks_its_recommendations_until_the_user_chooses(self) -> None:
        section = self.section
        section.set_case_type("실종")
        self.assertEqual(section.focus(), RECOMMENDED["실종"])
        self.assertFalse(section.tiles[FocusSituation.MOVEMENT].tag.isHidden())
        section.set_case_type("폭행")
        self.assertEqual(section.focus(), RECOMMENDED["폭행"])

        section.tiles[FocusSituation.ENVIRONMENT].check.click()  # the user's own choice
        section.set_case_type("아동학대")
        self.assertIn(FocusSituation.ENVIRONMENT, section.focus())
        self.assertNotIn(FocusSituation.COMPANY, section.focus())
        # The recommendation is still shown as a tag.
        self.assertFalse(section.tiles[FocusSituation.COMPANY].tag.isHidden())

    def test_hints_are_short_unique_and_removable(self) -> None:
        section = self.section
        for text in ("가방을 든 사람", "가방을  든 사람", "", "자전거"):
            section._start_hint()
            section.hint_edit.setText(text)
            section._add_hint()
        self.assertEqual(section.hints(), ("가방을 든 사람", "자전거"))
        self.assertEqual(section.hint_edit.maxLength(), 30)
        section._remove_hint("가방을 든 사람")
        self.assertEqual(section.hints(), ("자전거",))

    def test_loaded_case_keeps_its_saved_choice(self) -> None:
        self.section.set_values("실종", (FocusSituation.ENVIRONMENT,), ("차량",))
        self.assertEqual(self.section.focus(), (FocusSituation.ENVIRONMENT,))
        self.section.set_case_type("폭행")
        self.assertEqual(self.section.focus(), (FocusSituation.ENVIRONMENT,))


if __name__ == "__main__":
    unittest.main()
