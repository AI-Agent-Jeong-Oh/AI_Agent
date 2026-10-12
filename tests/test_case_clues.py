from __future__ import annotations

import unittest

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyleOptionViewItem

from video_agent.contracts import (
    AnalysisClue,
    CaseRecord,
    ClueSource,
    InformationCertainty,
)
from video_agent.ui.case_panel import CaseContextPanel

LONG_CLUE = (
    "이웃 주민은 회색 후드티를 입은 사람이 계단 쪽에서 내려와 문 앞에 잠시 멈춰 섰다가 "
    "상자를 들고 다시 계단 쪽으로 올라갔다고 진술했으나 정확한 시각은 기억하지 못함"
)


class CaseClueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.panel = CaseContextPanel()
        self.panel.resize(520, 700)
        self.changes = 0
        self.panel.changed.connect(self.count_change)

    def tearDown(self) -> None:
        self.panel.close()

    def count_change(self) -> None:
        self.changes += 1

    def add(self, text, source=ClueSource.REPORT, certainty=InformationCertainty.ESTIMATED, use=True):
        panel = self.panel
        panel.clue_text.setText(text)
        panel.clue_source.setCurrentIndex(panel.clue_source.findData(source))
        panel.clue_certainty.setCurrentIndex(panel.clue_certainty.findData(certainty))
        panel.clue_use.setChecked(use)
        panel.add_clue_button.click()

    def test_certainty_does_not_change_analysis_use(self) -> None:
        self.add("추정 단서", certainty=InformationCertainty.ESTIMATED)
        self.add("미확인 단서", certainty=InformationCertainty.UNKNOWN)
        self.add("관리용 단서", certainty=InformationCertainty.CONFIRMED, use=False)

        clues = self.panel.clues()
        self.assertEqual([clue.use_for_analysis for clue in clues], [True, True, False])
        self.assertEqual(self.panel.clue_count_label.text(), "분석 사용 2 / 전체 3")

    def test_selected_clue_is_edited_in_place(self) -> None:
        self.add("처음 문장", ClueSource.REPORT, InformationCertainty.ESTIMATED)
        self.add("다른 단서", use=False)
        original = self.panel.clues()[0]
        before = self.changes

        self.panel.clue_list.setCurrentRow(0)
        self.assertEqual(self.panel.clue_text.text(), "처음 문장")
        self.assertEqual(self.panel.add_clue_button.text(), "변경 저장")
        self.assertFalse(self.panel.cancel_clue_edit_button.isHidden())
        self.assertFalse(self.panel.clue_use.isEnabled())

        self.panel.clue_text.setText("고친 문장")
        self.panel.clue_source.setCurrentIndex(
            self.panel.clue_source.findData(ClueSource.DOCUMENT)
        )
        self.panel.clue_certainty.setCurrentIndex(
            self.panel.clue_certainty.findData(InformationCertainty.CONFIRMED)
        )
        self.panel.add_clue_button.click()

        edited, other = self.panel.clues()
        self.assertEqual(edited.clue_id, original.clue_id)
        self.assertEqual(edited.text, "고친 문장")
        self.assertIs(edited.source, ClueSource.DOCUMENT)
        self.assertIs(edited.certainty, InformationCertainty.CONFIRMED)
        self.assertTrue(edited.use_for_analysis)
        self.assertFalse(other.use_for_analysis)
        self.assertEqual(len(self.panel.clues()), 2)
        self.assertGreater(self.changes, before)
        self.assertEqual(self.panel.add_clue_button.text(), "단서 추가")
        self.assertTrue(self.panel.clue_use.isEnabled())
        self.assertEqual(self.panel.clue_text.text(), "")

    def test_cancel_edit_changes_nothing(self) -> None:
        self.add("그대로 둘 단서")
        saved = self.panel.clues()
        self.panel.clue_list.setCurrentRow(0)
        self.panel.clue_text.setText("저장하지 않을 문장")
        self.panel.cancel_clue_edit_button.click()

        self.assertEqual(self.panel.clues(), saved)
        self.assertEqual(self.panel.add_clue_button.text(), "단서 추가")
        self.assertEqual(self.panel.clue_text.text(), "")

    def test_edit_keeps_unchecked_state_from_the_list(self) -> None:
        self.add("체크 해제할 단서")
        item = self.panel.clue_list.item(0)
        item.setCheckState(Qt.CheckState.Unchecked)
        self.panel.clue_list.setCurrentRow(0)
        self.panel.clue_certainty.setCurrentIndex(
            self.panel.clue_certainty.findData(InformationCertainty.UNKNOWN)
        )
        self.panel.add_clue_button.click()

        (clue,) = self.panel.clues()
        self.assertFalse(clue.use_for_analysis)
        self.assertIs(clue.certainty, InformationCertainty.UNKNOWN)

    def test_saved_clues_load_and_can_be_edited(self) -> None:
        stored = AnalysisClue(
            clue_id="clue-saved",
            text="저장된 단서",
            source=ClueSource.USER_STATEMENT,
            certainty=InformationCertainty.UNKNOWN,
            use_for_analysis=False,
        )
        self.panel.set_record(CaseRecord(case_id="case-1", revision=1, title="사건", clues=(stored,)))
        self.assertEqual(self.panel.clues(), (stored,))
        self.assertIn("저장된 단서", self.panel.clue_list.item(0).text())
        self.assertIn("출처: 사용자 진술, 확실성: 미확인", self.panel.clue_list.item(0).text())
        self.assertEqual(self.panel.clue_list.item(0).checkState(), Qt.CheckState.Unchecked)

        self.panel.clue_list.setCurrentRow(0)
        self.assertEqual(self.panel.clue_source.currentData(), ClueSource.USER_STATEMENT)
        self.panel.clue_certainty.setCurrentIndex(
            self.panel.clue_certainty.findData(InformationCertainty.ESTIMATED)
        )
        self.panel.add_clue_button.click()
        (clue,) = self.panel.clues()
        self.assertEqual(clue.clue_id, "clue-saved")
        self.assertIs(clue.certainty, InformationCertainty.ESTIMATED)
        self.assertFalse(clue.use_for_analysis)

    def test_long_clue_row_wraps_to_more_lines(self) -> None:
        self.add("짧은 단서")
        self.add(LONG_CLUE)
        self.panel.show()
        self.app.processEvents()
        clue_list = self.panel.clue_list
        delegate = clue_list.itemDelegate()
        option = QStyleOptionViewItem()
        option.initFrom(clue_list)
        short_height = delegate.sizeHint(option, clue_list.model().index(0, 0)).height()
        long_height = delegate.sizeHint(option, clue_list.model().index(1, 0)).height()
        self.assertGreater(long_height, short_height)
        self.assertEqual(clue_list.horizontalScrollBar().maximum(), 0)

    def test_only_the_check_box_beside_the_first_line_toggles_analysis_use(self) -> None:
        self.add(LONG_CLUE)
        self.panel.show()
        self.app.processEvents()
        clue_list = self.panel.clue_list
        item = clue_list.item(0)
        option = QStyleOptionViewItem()
        clue_list.initViewItemOption(option)
        option.rect = clue_list.visualItemRect(item)
        delegate = clue_list.itemDelegate()
        check_rect = delegate._layout(option, clue_list.model().index(0, 0), item.data(Qt.ItemDataRole.UserRole))[3]
        # Beside the first line, not centred on the wrapped row.
        self.assertLess(check_rect.center().y(), option.rect.center().y())

        QTest.mouseClick(clue_list.viewport(), Qt.MouseButton.LeftButton, pos=check_rect.center())
        self.assertFalse(self.panel.clues()[0].use_for_analysis)
        text_point = option.rect.center() + QPoint(80, 0)
        QTest.mouseClick(clue_list.viewport(), Qt.MouseButton.LeftButton, pos=text_point)
        self.assertFalse(self.panel.clues()[0].use_for_analysis)
        QTest.mouseClick(clue_list.viewport(), Qt.MouseButton.LeftButton, pos=check_rect.center())
        self.assertTrue(self.panel.clues()[0].use_for_analysis)

    def test_row_is_a_table_line_with_source_and_certainty_columns(self) -> None:
        self.add("관리용 단서", ClueSource.REPORT, InformationCertainty.ESTIMATED, use=False)
        self.panel.show()
        self.app.processEvents()
        clue_list = self.panel.clue_list
        option = QStyleOptionViewItem()
        clue_list.initViewItemOption(option)
        option.rect = clue_list.visualItemRect(clue_list.item(0))
        clue = clue_list.item(0).data(Qt.ItemDataRole.UserRole)
        delegate = clue_list.itemDelegate()
        # The sentence cell holds only the clue; source and certainty have their own columns.
        self.assertEqual(delegate.document(option, clue, 400).toPlainText(), "관리용 단서")
        cells = delegate.columns(option.rect, option.font)
        self.assertLess(cells.check.right(), cells.text.left())
        self.assertLess(cells.text.right(), cells.source.left())
        self.assertLess(cells.source.right(), cells.certainty.left() + 1)
        self.assertLess(cells.certainty.right(), cells.edit.left())
        self.assertLess(cells.edit.right(), cells.remove.left())
        self.assertFalse(self.panel.clue_header.isHidden())

    def test_row_marks_edit_and_delete(self) -> None:
        self.add("첫 단서")
        self.add("둘째 단서")
        self.panel.show()
        self.app.processEvents()
        clue_list = self.panel.clue_list
        option = QStyleOptionViewItem()
        clue_list.initViewItemOption(option)
        option.rect = clue_list.visualItemRect(clue_list.item(1))
        cells = clue_list.itemDelegate().columns(option.rect, option.font)
        y = option.rect.top() + option.rect.height() // 2
        QTest.mouseClick(clue_list.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(cells.edit.center().x(), y))
        self.assertEqual(self.panel.clue_text.text(), "둘째 단서")
        self.assertEqual(self.panel.add_clue_button.text(), "변경 저장")
        self.assertIn("단서 수정 중", self.panel.clue_aside.text())
        QTest.mouseClick(clue_list.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(cells.remove.center().x(), y))
        self.assertEqual([clue.text for clue in self.panel.clues()], ["첫 단서"])

    def test_source_and_certainty_come_before_the_sentence(self) -> None:
        panel = self.panel
        self.assertEqual(panel.clue_source.currentIndex(), -1)
        self.assertEqual(panel.clue_certainty.currentIndex(), -1)
        self.assertFalse(panel.clue_text.isEnabled())
        self.assertFalse(panel.add_clue_button.isEnabled())
        panel.clue_text.setText("고르지 않은 단서")
        panel.add_clue_button.click()
        self.assertEqual(panel.clues(), ())
        panel.clue_source.setCurrentIndex(panel.clue_source.findData(ClueSource.REPORT))
        self.assertFalse(panel.clue_text.isEnabled())
        panel.clue_certainty.setCurrentIndex(
            panel.clue_certainty.findData(InformationCertainty.ESTIMATED)
        )
        self.assertTrue(panel.clue_text.isEnabled())
        panel.add_clue_button.click()
        self.assertEqual(len(panel.clues()), 1)
        # The next clue usually comes from the same report: the choice stays.
        self.assertEqual(panel.clue_source.currentData(), ClueSource.REPORT)
        self.assertEqual(panel.clue_certainty.currentData(), InformationCertainty.ESTIMATED)
        self.assertTrue(panel.clue_text.isEnabled())
        # Editing another clue and leaving it returns to that choice, not to blank.
        panel.clue_list.setCurrentRow(0)
        panel.clue_certainty.setCurrentIndex(panel.clue_certainty.findData(InformationCertainty.CONFIRMED))
        panel.cancel_clue_edit_button.click()
        self.assertEqual(panel.clue_certainty.currentData(), InformationCertainty.ESTIMATED)

    def test_empty_case_name_is_asked_for(self) -> None:
        self.panel.show()
        self.assertFalse(self.panel.require_title())
        self.assertFalse(self.panel.title_error.isHidden())
        self.panel.title_edit.setText("문 앞 택배 분실")
        self.assertTrue(self.panel.title_error.isHidden())
        self.assertTrue(self.panel.require_title())

    def test_no_bracketed_state_text_in_rows(self) -> None:
        self.add("단서", use=True)
        self.add("관리 단서", use=False)
        for row in range(2):
            text = self.panel.clue_list.item(row).text()
            self.assertNotIn("[분석 사용]", text)
            self.assertNotIn("[관리만]", text)

    def test_editing_is_locked_with_the_panel(self) -> None:
        self.add("단서")
        self.panel.clue_list.setCurrentRow(0)
        self.panel.set_editing_enabled(False)
        self.assertFalse(self.panel.add_clue_button.isEnabled())
        self.assertFalse(self.panel.cancel_clue_edit_button.isEnabled())
        self.assertFalse(self.panel.clue_list.isEnabled())


    def test_management_memo_is_folded_until_used(self) -> None:
        panel = self.panel
        self.assertTrue(panel.notes_edit.isHidden())
        panel.admin_toggle.click()
        self.assertFalse(panel.notes_edit.isHidden())
        panel.admin_toggle.click()
        panel.set_record(
            CaseRecord(case_id="case-a", revision=2, title="택배", management_notes="담당자 확인")
        )
        self.assertTrue(panel.admin_toggle.isChecked())
        self.assertFalse(panel.notes_edit.isHidden())

    def test_saved_cases_load_from_one_menu(self) -> None:
        records = (
            CaseRecord(case_id="case-a", revision=1, title="가"),
            CaseRecord(case_id="case-b", revision=3, title="나"),
        )
        self.panel.set_saved_cases(records, "case-unsaved")
        self.assertEqual(self.panel.selected_case_id(), "case-a")
        self.assertEqual(self.panel.saved_case_labels(), ("가 · v1", "나 · v3"))
        loads: list[str | None] = []
        self.panel.load_requested.connect(lambda: loads.append(self.panel.selected_case_id()))
        self.panel._load_saved_case("case-b")
        self.assertEqual(loads, ["case-b"])


if __name__ == "__main__":
    unittest.main()
