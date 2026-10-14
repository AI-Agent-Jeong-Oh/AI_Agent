"""Startup loading and local operator sign-in windows."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from video_agent.storage.auth import LocalAuthStore
from video_agent.ui import theme


class StartupScreen(QDialog):
    """A short branded opening screen with indeterminate progress."""

    finished = Signal()

    def __init__(self, duration_ms: int = 1100) -> None:
        super().__init__()
        self._duration_ms = max(0, min(int(duration_ms), 10_000))
        self._started = False
        self.setObjectName("startupDialog")
        self.setWindowTitle("숭재 수사대 다연캅 시작")
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setModal(True)
        self.setFixedSize(620, 430)
        self.setStyleSheet(theme.build_stylesheet(lambda value: value))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        panel = QFrame()
        panel.setObjectName("startupPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(48, 34, 48, 34)
        layout.setSpacing(10)

        emblem = QLabel()
        emblem.setAlignment(Qt.AlignmentFlag.AlignCenter)
        emblem.setPixmap(
            QPixmap(str(theme.ASSETS_DIR / "police_emblem.png")).scaledToHeight(
                54, Qt.TransformationMode.SmoothTransformation
            )
        )
        emblem.setAccessibleName("대한민국 경찰청 상징마크")
        organization = QLabel("대한민국 경찰청")
        organization.setObjectName("startupOrganization")
        organization.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel("숭재 수사대 다연캅")
        title.setObjectName("startupTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        mascot = QLabel()
        mascot.setObjectName("startupMascots")
        mascot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mascot.setAccessibleName("포돌이와 포순이 환영 일러스트")
        mascot.setPixmap(
            QPixmap(str(theme.ASSETS_DIR / "podori_posuni_welcome.png")).scaledToHeight(
                150, Qt.TransformationMode.SmoothTransformation
            )
        )

        self.status_label = QLabel("프로그램을 여는 중입니다")
        self.status_label.setObjectName("startupStatus")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.progress = QProgressBar()
        self.progress.setObjectName("startupProgress")
        self.progress.setRange(0, 0)
        self.progress.setAccessibleName("프로그램 시작 중")

        layout.addWidget(emblem)
        layout.addWidget(organization)
        layout.addWidget(title)
        layout.addStretch(1)
        layout.addWidget(mascot)
        layout.addStretch(1)
        layout.addWidget(self.status_label)
        layout.addWidget(self.progress)
        outer.addWidget(panel)

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        QTimer.singleShot(self._duration_ms, self._finish)

    def _finish(self) -> None:
        self.hide()
        self.finished.emit()


class LocalLoginDialog(QDialog):
    """Local sign-in with an in-dialog account registration mode."""

    def __init__(self, store: LocalAuthStore) -> None:
        super().__init__()
        self._store = store
        self.authenticated_username: str | None = None
        self._load_error: str | None = None
        self._usernames: tuple[str, ...] = ()
        try:
            self._usernames = store.list_usernames()
            self._is_signup = not self._usernames
        except (OSError, ValueError) as exc:
            self._is_signup = False
            self._load_error = str(exc)

        self.setObjectName("loginDialog")
        self.setWindowTitle("회원가입" if self._is_signup else "로그인")
        self.setModal(True)
        self.setMinimumWidth(520)
        self.setStyleSheet(theme.build_stylesheet(lambda value: value))
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 28, 28, 28)
        outer.setSpacing(16)

        brand = QHBoxLayout()
        emblem = QLabel()
        emblem.setPixmap(
            QPixmap(str(theme.ASSETS_DIR / "police_emblem.png")).scaledToHeight(
                44, Qt.TransformationMode.SmoothTransformation
            )
        )
        emblem.setAccessibleName("대한민국 경찰청 상징마크")
        brand_text = QVBoxLayout()
        organization = QLabel("대한민국 경찰청")
        organization.setObjectName("loginOrganization")
        app_name = QLabel("숭재 수사대 다연캅")
        app_name.setObjectName("loginAppName")
        brand_text.addWidget(organization)
        brand_text.addWidget(app_name)
        brand.addWidget(emblem)
        brand.addLayout(brand_text)
        brand.addStretch(1)
        outer.addLayout(brand)

        card = QFrame()
        card.setObjectName("loginCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(28, 24, 28, 24)
        card_layout.setSpacing(14)
        self.heading = QLabel()
        self.heading.setObjectName("loginTitle")
        self.intro = QLabel()
        self.intro.setObjectName("loginIntro")
        self.intro.setWordWrap(True)
        card_layout.addWidget(self.heading)
        card_layout.addWidget(self.intro)

        form = QFormLayout()
        form.setContentsMargins(0, 6, 0, 0)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(12)
        self.username_edit = QLineEdit(self._usernames[0] if self._usernames else "")
        self.username_edit.setPlaceholderText("사용자 이름")
        self.username_edit.setAccessibleName("사용자 이름")
        self.username_edit.setMaxLength(80)
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setPlaceholderText("8자 이상")
        self.password_edit.setAccessibleName("비밀번호")
        self.password_edit.setMaxLength(1024)
        self.confirm_edit = QLineEdit()
        self.confirm_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm_edit.setPlaceholderText("비밀번호 다시 입력")
        self.confirm_edit.setAccessibleName("비밀번호 확인")
        self.confirm_edit.setMaxLength(1024)
        form.addRow("사용자 이름", self.username_edit)
        form.addRow("비밀번호", self.password_edit)
        form.addRow("비밀번호 확인", self.confirm_edit)
        self.confirm_label = form.labelForField(self.confirm_edit)
        card_layout.addLayout(form)

        self.error_label = QLabel(self._load_error or "")
        self.error_label.setObjectName("loginError")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(bool(self._load_error))
        card_layout.addWidget(self.error_label)

        scope = QLabel(
            "로그인 정보는 이 컴퓨터에만 저장됩니다. 이 기능은 앱 접근을 구분하지만 "
            "영상과 결과 파일 자체를 암호화하지는 않습니다."
        )
        scope.setObjectName("loginScope")
        scope.setWordWrap(True)
        card_layout.addWidget(scope)

        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = QPushButton("취소")
        self.mode_button = QPushButton()
        self.submit_button = QPushButton()
        theme.mark_primary(self.submit_button)
        self.submit_button.setDefault(True)
        self.submit_button.setEnabled(self._load_error is None)
        actions.addWidget(cancel)
        actions.addWidget(self.mode_button)
        actions.addWidget(self.submit_button)
        card_layout.addLayout(actions)
        outer.addWidget(card)

        cancel.clicked.connect(self.reject)
        self.mode_button.clicked.connect(lambda: self._set_mode(not self._is_signup))
        self.submit_button.clicked.connect(self._submit)
        self.username_edit.returnPressed.connect(self.password_edit.setFocus)
        self.password_edit.returnPressed.connect(self._password_return_pressed)
        self.confirm_edit.returnPressed.connect(self._submit)
        self._set_mode(self._is_signup, clear_fields=False)
        if self._usernames:
            self.password_edit.setFocus()
        else:
            self.username_edit.setFocus()

    def _set_mode(self, signup: bool, *, clear_fields: bool = True) -> None:
        self._is_signup = bool(signup)
        self.setWindowTitle("회원가입" if self._is_signup else "로그인")
        self.heading.setText("첫 사용자 등록" if self._is_signup and not self._usernames else (
            "회원가입" if self._is_signup else "로그인"
        ))
        self.intro.setText(
            "이 컴퓨터에서 사용할 사용자 이름과 비밀번호를 등록해 주세요."
            if self._is_signup
            else "등록한 로컬 사용자 정보로 로그인해 주세요."
        )
        self.confirm_edit.setVisible(self._is_signup)
        self.confirm_label.setVisible(self._is_signup)
        self.submit_button.setText("가입하고 시작" if self._is_signup else "로그인")
        self.mode_button.setText("로그인으로 돌아가기" if self._is_signup else "회원가입")
        self.mode_button.setVisible(bool(self._usernames) and self._load_error is None)
        if clear_fields:
            self.password_edit.clear()
            self.confirm_edit.clear()
            self.error_label.clear()
            self.error_label.hide()
            if not self._is_signup and not self.username_edit.text() and self._usernames:
                self.username_edit.setText(self._usernames[0])
        if self._is_signup:
            self.username_edit.selectAll()
            self.username_edit.setFocus()
        else:
            self.password_edit.setFocus()

    def _password_return_pressed(self) -> None:
        if self._is_signup:
            self.confirm_edit.setFocus()
        else:
            self._submit()

    def _submit(self) -> None:
        username = self.username_edit.text()
        password = self.password_edit.text()
        self.error_label.hide()
        try:
            if self._is_signup:
                if password != self.confirm_edit.text():
                    raise ValueError("비밀번호 확인이 일치하지 않습니다")
                account = self._store.create(username, password)
                self.authenticated_username = account.username
            else:
                account = self._store.authenticated_account(username, password)
                if account is None:
                    raise ValueError("사용자 이름 또는 비밀번호를 확인해 주세요")
                self.authenticated_username = account.username
        except (OSError, ValueError) as exc:
            self.password_edit.clear()
            self.confirm_edit.clear()
            self.error_label.setText(str(exc))
            self.error_label.show()
            self.password_edit.setFocus()
            return
        self.password_edit.clear()
        self.confirm_edit.clear()
        self.accept()
