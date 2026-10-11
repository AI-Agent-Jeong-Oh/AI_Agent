from __future__ import annotations

import html
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from video_agent.contracts import ResultOrigin
from video_agent.review.summary import ResultSummary, render_html
from video_agent.ui import theme


SCOPE_CASE = "case"
SCOPE_CAMERA = "camera"

_BANNER_KINDS = {
    ResultOrigin.SAMPLE: "sample",
    ResultOrigin.TEST_DOUBLE: "test_double",
    ResultOrigin.REAL_MODEL: "real_model",
}


class ResultSummaryDialog(QDialog):
    """Editable six-section summary draft with HTML export.

    Edits live only in this window and in exported files; they are not stored by the app.
    With `case_build`, the window opens on the case-wide draft and can switch to the
    current camera's draft.
    """

    def __init__(
        self,
        build: Callable[[], ResultSummary],
        parent: QWidget | None = None,
        *,
        case_build: Callable[[], ResultSummary] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("결과 요약 (초안)")
        self.setModal(True)
        self.resize(760, 820)
        self._builders = {SCOPE_CAMERA: build}
        if case_build is not None:
            self._builders[SCOPE_CASE] = case_build
        self._scope = SCOPE_CASE if case_build is not None else SCOPE_CAMERA
        self._build = self._builders[self._scope]
        self._summary = self._build()
        self.editors: dict[str, QPlainTextEdit] = {}
        self._build_ui()
        self._fill(self._summary)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        self.scope_buttons: dict[str, QPushButton] = {}
        if len(self._builders) > 1:
            scope_row = QHBoxLayout()
            scope_label = QLabel("요약 범위")
            scope_label.setObjectName("fieldLabel")
            scope_row.addWidget(scope_label)
            scope_row.addSpacing(8)
            self._scope_group = QButtonGroup(self)
            scopes = ((SCOPE_CASE, "사건 전체"), (SCOPE_CAMERA, "현재 카메라"))
            for position, (key, label) in enumerate(scopes):
                button = QPushButton(label)
                button.setCheckable(True)
                button.setAccessibleName(f"요약 범위: {label}")
                theme.set_segment(button, position, len(scopes))
                self._scope_group.addButton(button)
                self.scope_buttons[key] = button
                scope_row.addWidget(button)
                button.clicked.connect(lambda _checked=False, k=key: self.set_scope(k))
            self.scope_buttons[self._scope].setChecked(True)
            scope_row.addStretch(1)
            layout.addLayout(scope_row)
        self.banner_label = QLabel()
        self.banner_label.setWordWrap(True)
        self.warning_label = QLabel()
        self.warning_label.setWordWrap(True)
        self.warning_label.setStyleSheet(
            theme.banner_style("warning") + "padding:6px;border-radius:5px;"
        )
        note = QLabel(
            "저장된 분석 결과와 검토 기록으로 만든 초안입니다. 문구를 고칠 수 있으며, "
            "고친 문구는 앱에 저장되지 않고 내보낸 파일에만 남습니다."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{theme.TEXT_SUB};")
        layout.addWidget(self.banner_label)
        layout.addWidget(self.warning_label)
        layout.addWidget(note)

        sections = QWidget()
        sections_layout = QVBoxLayout(sections)
        sections_layout.setContentsMargins(0, 0, 0, 0)
        self._section_bodies: dict[str, QLabel] = {}
        for section in self._summary.sections:
            group = QGroupBox(section.title)
            group_layout = QVBoxLayout(group)
            if section.editable:
                editor = QPlainTextEdit()
                editor.setAccessibleName(section.title)
                editor.textChanged.connect(lambda e=editor: self._fit_editor(e))
                self.editors[section.key] = editor
                group_layout.addWidget(editor)
            else:
                fixed = QLabel()
                fixed.setWordWrap(True)
                fixed.setTextFormat(Qt.TextFormat.RichText)
                fixed.setOpenExternalLinks(True)
                fixed.setAccessibleName(section.title)
                self._section_bodies[section.key] = fixed
                group_layout.addWidget(fixed)
            sections_layout.addWidget(group)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(sections)
        layout.addWidget(scroll, 1)

        self.export_status_label = QLabel("")
        self.export_status_label.setWordWrap(True)
        layout.addWidget(self.export_status_label)

        buttons = QHBoxLayout()
        self.regenerate_button = QPushButton("초안 다시 만들기")
        self.regenerate_button.setToolTip("지금 저장된 검토 기록으로 초안을 다시 만듭니다")
        self.export_button = QPushButton("HTML로 내보내기")
        theme.mark_primary(self.export_button)
        self.close_button = QPushButton("닫기")
        buttons.addStretch(1)
        buttons.addWidget(self.regenerate_button)
        buttons.addWidget(self.export_button)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

        self.regenerate_button.clicked.connect(self.regenerate)
        self.export_button.clicked.connect(self.export_html)
        self.close_button.clicked.connect(self.reject)

    def _fill(self, summary: ResultSummary) -> None:
        self._summary = summary
        self.banner_label.setText(summary.origin_notice)
        self.banner_label.setStyleSheet(
            theme.banner_style(_BANNER_KINDS.get(summary.origin, "neutral"))
            + "padding:8px;border-radius:5px;font-weight:700;"
        )
        self.warning_label.setText("\n".join(summary.warnings))
        self.warning_label.setVisible(bool(summary.warnings))
        for section in summary.sections:
            if section.key in self.editors:
                self.editors[section.key].setPlainText(section.body)
            elif section.html_body is not None:
                self._section_bodies[section.key].setText(
                    section.html_body.replace("\n  ", "<br>&nbsp;&nbsp;&nbsp;").replace("\n", "<br>")
                )
            else:
                self._section_bodies[section.key].setText(
                    html.escape(section.body).replace("\n", "<br>")
                )

    @staticmethod
    def _fit_editor(editor: QPlainTextEdit) -> None:
        """Size each box to its text (one spare line) so short sections stay compact."""
        lines = max(2, editor.document().blockCount() + 1)
        height = editor.fontMetrics().lineSpacing() * lines + 16
        editor.setFixedHeight(max(64, min(height, 320)))

    def bodies(self) -> dict[str, str]:
        return {key: editor.toPlainText() for key, editor in self.editors.items()}

    def is_edited(self) -> bool:
        return any(
            self.bodies()[section.key] != section.body
            for section in self._summary.sections
            if section.editable
        )

    @property
    def scope(self) -> str:
        return self._scope

    def set_scope(self, scope: str) -> bool:
        """Rebuild the draft for another scope; edits are lost only after confirmation."""
        if scope == self._scope or scope not in self._builders:
            return False
        if self.is_edited() and not self._confirm_regenerate():
            self.scope_buttons[self._scope].setChecked(True)
            return False
        self._scope = scope
        self.scope_buttons[scope].setChecked(True)
        self._build = self._builders[scope]
        self._fill(self._build())
        self.export_status_label.setText("")
        return True

    def regenerate(self) -> None:
        if self.is_edited() and not self._confirm_regenerate():
            return
        self._fill(self._build())
        self.export_status_label.setText("")

    def _confirm_regenerate(self) -> bool:
        answer = QMessageBox.question(
            self,
            "초안 다시 만들기",
            "고친 문구가 사라지고 저장된 검토 기록으로 다시 작성됩니다. 계속할까요?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Yes

    def export_html(self) -> Path | None:
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "결과 요약 HTML로 내보내기",
            str(Path.home() / f"검토요약_{self._summary.file_stem}.html"),
            "HTML 파일 (*.html)",
        )
        if not filename:
            return None
        path = Path(filename)
        if path.suffix.lower() not in {".html", ".htm"}:
            path = path.with_name(path.name + ".html")
        try:
            path.write_text(render_html(self._summary, self.bodies()), encoding="utf-8")
        except OSError as exc:
            self.export_status_label.setText(f"내보내기 실패: {exc.strerror or exc}")
            self.export_status_label.setStyleSheet(f"color:{theme.status_color('error')};")
            self.export_status_label.setToolTip(str(exc))
            return None
        self.export_status_label.setText(f"내보냄: {path.name}")
        self.export_status_label.setStyleSheet(f"color:{theme.status_color('ok')};")
        self.export_status_label.setToolTip(str(path))
        return path
