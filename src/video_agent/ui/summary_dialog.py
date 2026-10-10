from __future__ import annotations

import html
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
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


_BANNER_STYLES = {
    ResultOrigin.SAMPLE: "background:#fff0d7;color:#813f00;border:1px solid #e6a654;",
    ResultOrigin.TEST_DOUBLE: "background:#f5e9ff;color:#5d2b7a;border:1px solid #b98bd0;",
    ResultOrigin.REAL_MODEL: "background:#e4f4eb;color:#125d35;border:1px solid #75b991;",
}


class ResultSummaryDialog(QDialog):
    """Editable six-section summary draft with HTML export.

    Edits live only in this window and in exported files; they are not stored by the app.
    """

    def __init__(self, build: Callable[[], ResultSummary], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("결과 요약 — 검토 기록 기반 초안")
        self.setModal(True)
        self.resize(760, 820)
        self._build = build
        self._summary = build()
        self.editors: dict[str, QPlainTextEdit] = {}
        self._build_ui()
        self._fill(self._summary)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        self.banner_label = QLabel()
        self.banner_label.setWordWrap(True)
        self.warning_label = QLabel()
        self.warning_label.setWordWrap(True)
        self.warning_label.setStyleSheet(
            "background:#fff6d8;color:#6e5200;border:1px solid #d6b858;"
            "padding:6px;border-radius:5px;"
        )
        note = QLabel(
            "저장된 분석 결과와 검토 기록으로 만든 초안입니다. 문구를 고칠 수 있으며, "
            "고친 문구는 앱에 저장되지 않고 내보낸 파일에만 남습니다."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color:#4a5a6a;")
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
            _BANNER_STYLES.get(
                summary.origin, "background:#eef1f4;color:#46515a;border:1px solid #adb7c0;"
            )
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
            str(Path.home() / f"검토요약_{self._summary.job_id}.html"),
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
            self.export_status_label.setStyleSheet("color:#b3261e;")
            self.export_status_label.setToolTip(str(exc))
            return None
        self.export_status_label.setText(f"내보냄: {path.name}")
        self.export_status_label.setStyleSheet("color:#1f6b3a;")
        self.export_status_label.setToolTip(str(path))
        return path
