"""First-run and settings dialog for an explicit local Ollama vision model."""

from __future__ import annotations

import os
import re
from dataclasses import replace
from urllib.parse import urlsplit

from PySide6.QtCore import QProcess, QProcessEnvironment, QTimer, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
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

from video_agent.contracts import AnalysisMode
from video_agent.models.config import (
    DEFAULT_OLLAMA_CHAT_ENDPOINT,
    ModelConfigurationError,
    RuntimeInventory,
    normalize_ollama_chat_endpoint,
)
from video_agent.storage.json_store import AppSettings, SettingsStore
from video_agent.ui import theme


OLLAMA_DOWNLOAD_URL = "https://ollama.com/download"
RECOMMENDED_VISION_MODELS = (
    "qwen3-vl:4b-instruct",
    "qwen3-vl:8b-instruct",
)
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*(?::[A-Za-z0-9][A-Za-z0-9._-]*)?$")


class LocalAiSetupDialog(QDialog):
    """Choose an existing image-capable model or download one with the local Ollama CLI."""

    DOWNLOAD_TIMEOUT_MS = 30 * 60 * 1000

    def __init__(
        self,
        store: SettingsStore,
        *,
        inventory: RuntimeInventory | None = None,
    ) -> None:
        super().__init__()
        self._store = store
        self._load_error: str | None = None
        try:
            self._settings = store.load()
        except (OSError, ValueError) as exc:
            self._settings = AppSettings()
            self._load_error = str(exc)
        endpoint = (
            os.environ.get("VIDEO_AGENT_LOCAL_ENDPOINT")
            or self._settings.local_endpoint
            or DEFAULT_OLLAMA_CHAT_ENDPOINT
        )
        self._inventory = inventory or RuntimeInventory.detect(endpoint)
        self._process: QProcess | None = None
        self._download_canceled = False
        self._download_timed_out = False
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(self._download_timeout)

        self.setObjectName("localAiSetupDialog")
        self.setWindowTitle("로컬 AI 설정")
        self.setModal(True)
        self.setMinimumWidth(660)
        self.setStyleSheet(theme.build_stylesheet(lambda value: value))
        self._build_ui(endpoint)
        self._show_inventory(self._settings.local_model_id)

    @property
    def selected_model_id(self) -> str | None:
        value = self.model_combo.currentData()
        return value if isinstance(value, str) and value else None

    @property
    def selected_endpoint(self) -> str:
        return self.endpoint_edit.text().strip()

    def _build_ui(self, endpoint: str) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 26, 28, 26)
        outer.setSpacing(14)

        heading = QLabel("이 컴퓨터에서 사용할 AI를 선택하세요")
        heading.setObjectName("loginTitle")
        intro = QLabel(
            "CCTV 프레임을 이해하려면 일반 언어 모델이 아니라 이미지 입력을 지원하는 "
            "Ollama Vision 모델이 필요합니다. 원본 영상은 이 컴퓨터에 그대로 남습니다."
        )
        intro.setObjectName("loginIntro")
        intro.setWordWrap(True)
        outer.addWidget(heading)
        outer.addWidget(intro)

        card = QFrame()
        card.setObjectName("loginCard")
        form = QFormLayout(card)
        form.setContentsMargins(24, 22, 24, 22)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(12)
        theme.polish_form(form)

        endpoint_row = QHBoxLayout()
        self.endpoint_edit = QLineEdit(endpoint)
        self.endpoint_edit.setObjectName("localEndpoint")
        self.endpoint_edit.setAccessibleName("로컬 Ollama 주소")
        self.endpoint_edit.setPlaceholderText(DEFAULT_OLLAMA_CHAT_ENDPOINT)
        self.scan_button = QPushButton("다시 찾기")
        self.scan_button.clicked.connect(self._scan)
        endpoint_row.addWidget(self.endpoint_edit, 1)
        endpoint_row.addWidget(self.scan_button)
        form.addRow("Ollama 주소", endpoint_row)

        self.model_combo = QComboBox()
        self.model_combo.setObjectName("localVisionModel")
        self.model_combo.setAccessibleName("사용할 로컬 Vision 모델")
        self.model_combo.currentIndexChanged.connect(self._selection_changed)
        theme.polish_combo(self.model_combo)
        form.addRow("사용 모델", self.model_combo)

        self.inventory_label = QLabel()
        self.inventory_label.setObjectName("subsectionHelp")
        self.inventory_label.setWordWrap(True)
        form.addRow("확인 결과", self.inventory_label)
        outer.addWidget(card)

        download_card = QFrame()
        download_card.setObjectName("loginCard")
        download_layout = QVBoxLayout(download_card)
        download_layout.setContentsMargins(24, 20, 24, 20)
        download_layout.setSpacing(10)
        download_title = QLabel("새 모델 준비")
        download_title.setObjectName("fieldLabel")
        download_help = QLabel(
            "Ollama가 없다면 설치 페이지를 먼저 여세요. 모델은 용량이 크므로 이름과 "
            "저장 공간을 확인한 뒤 직접 다운로드를 시작해야 합니다."
        )
        download_help.setWordWrap(True)
        download_help.setMinimumHeight(46)
        self.download_model_combo = QComboBox()
        self.download_model_combo.setEditable(True)
        self.download_model_combo.addItems(RECOMMENDED_VISION_MODELS)
        self.download_model_combo.setAccessibleName("다운로드할 Ollama 모델 이름")
        theme.polish_combo(self.download_model_combo)
        download_actions = QHBoxLayout()
        self.install_button = QPushButton("Ollama 설치 페이지")
        self.install_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(OLLAMA_DOWNLOAD_URL))
        )
        self.download_button = QPushButton("모델 다운로드")
        self.download_button.clicked.connect(self._start_download)
        self.cancel_download_button = QPushButton("다운로드 취소")
        self.cancel_download_button.clicked.connect(self._cancel_download)
        self.cancel_download_button.setVisible(False)
        download_actions.addWidget(self.install_button)
        download_actions.addWidget(self.download_button)
        download_actions.addWidget(self.cancel_download_button)
        download_actions.addStretch(1)
        self.download_progress = QProgressBar()
        self.download_progress.setRange(0, 0)
        self.download_progress.setAccessibleName("모델 다운로드 진행 중")
        self.download_progress.setVisible(False)
        download_layout.addWidget(download_title)
        download_layout.addWidget(download_help)
        download_layout.addWidget(self.download_model_combo)
        download_layout.addLayout(download_actions)
        download_layout.addWidget(self.download_progress)
        outer.addWidget(download_card)

        self.error_label = QLabel(self._load_error or "")
        self.error_label.setObjectName("loginError")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(bool(self._load_error))
        outer.addWidget(self.error_label)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.later_button = QPushButton("설정 없이 계속")
        self.later_button.clicked.connect(self._continue_without_local)
        self.save_button = QPushButton("이 모델로 시작")
        theme.mark_primary(self.save_button)
        self.save_button.clicked.connect(self._save_and_accept)
        actions.addWidget(self.later_button)
        actions.addWidget(self.save_button)
        outer.addLayout(actions)

    def _show_inventory(self, preferred_model: str | None = None) -> None:
        models = self._inventory.vision_ollama_models
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        for model_id in models:
            self.model_combo.addItem(model_id, model_id)
        if preferred_model and preferred_model in models:
            self.model_combo.setCurrentIndex(models.index(preferred_model))
        self.model_combo.blockSignals(False)
        if models:
            self.inventory_label.setText(
                f"이미지 입력이 확인된 모델 {len(models)}개를 찾았습니다. 원하는 모델을 선택하세요."
            )
        elif self._inventory.ollama_executable:
            self.inventory_label.setText(
                "Ollama는 설치되어 있지만 이미지 입력이 확인된 모델이 없습니다. 새 모델을 다운로드해 주세요."
            )
        else:
            self.inventory_label.setText(
                "사용할 수 있는 로컬 Vision 모델을 찾지 못했습니다. Ollama를 설치하거나 주소를 확인해 주세요."
            )
        self.download_button.setEnabled(bool(self._inventory.ollama_executable))
        self._selection_changed()

    def _selection_changed(self, *_args: object) -> None:
        self.save_button.setEnabled(self.selected_model_id is not None and self._process is None)

    def _normalized_endpoint(self) -> str | None:
        try:
            endpoint = normalize_ollama_chat_endpoint(self.selected_endpoint)
        except ModelConfigurationError as exc:
            self._show_error(str(exc))
            return None
        self.endpoint_edit.setText(endpoint)
        return endpoint

    def _scan(self, preferred_model: str | None = None) -> None:
        if self._process is not None:
            return
        endpoint = self._normalized_endpoint()
        if endpoint is None:
            return
        self._show_error("")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self._inventory = RuntimeInventory.detect(endpoint)
        finally:
            QApplication.restoreOverrideCursor()
        self._show_inventory(preferred_model or self.selected_model_id)

    def _start_download(self) -> None:
        endpoint = self._normalized_endpoint()
        executable = self._inventory.ollama_executable
        model_id = self.download_model_combo.currentText().strip()
        if endpoint is None:
            return
        if not executable:
            self._show_error("Ollama 설치를 확인한 뒤 ‘다시 찾기’를 눌러 주세요.")
            return
        if len(model_id) > 200 or not _MODEL_ID.fullmatch(model_id):
            self._show_error("Ollama 모델 이름을 확인해 주세요. 공백이나 명령 문자는 사용할 수 없습니다.")
            return
        self._show_error("")
        self._download_canceled = False
        self._download_timed_out = False
        process = QProcess(self)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        environment = QProcessEnvironment.systemEnvironment()
        parsed = urlsplit(endpoint)
        environment.insert("OLLAMA_HOST", f"{parsed.scheme}://{parsed.netloc}")
        process.setProcessEnvironment(environment)
        process.readyReadStandardOutput.connect(process.readAllStandardOutput)
        process.finished.connect(
            lambda exit_code, _status, requested=model_id: self._download_finished(
                exit_code, requested
            )
        )
        self._process = process
        self._set_download_running(True)
        self.inventory_label.setText(f"{model_id} 모델을 다운로드하고 있습니다…")
        process.start(executable, ["pull", model_id])
        if not process.waitForStarted(3000):
            self._process = None
            self._set_download_running(False)
            self._show_error("모델 다운로드를 시작하지 못했습니다. Ollama 실행 상태를 확인해 주세요.")
            process.deleteLater()
            return
        self._timeout.start(self.DOWNLOAD_TIMEOUT_MS)

    def _set_download_running(self, running: bool) -> None:
        self.endpoint_edit.setEnabled(not running)
        self.scan_button.setEnabled(not running)
        self.model_combo.setEnabled(not running)
        self.install_button.setEnabled(not running)
        self.download_model_combo.setEnabled(not running)
        self.download_button.setEnabled(not running and bool(self._inventory.ollama_executable))
        self.cancel_download_button.setVisible(running)
        self.download_progress.setVisible(running)
        self.later_button.setEnabled(not running)
        self.save_button.setEnabled(not running and self.selected_model_id is not None)

    def _cancel_download(self) -> None:
        process = self._process
        if process is None:
            return
        self._download_canceled = True
        self.inventory_label.setText("다운로드를 취소하고 있습니다…")
        process.terminate()
        QTimer.singleShot(3000, self._kill_download_if_running)

    def _kill_download_if_running(self) -> None:
        process = self._process
        if process is not None and process.state() != QProcess.ProcessState.NotRunning:
            process.kill()

    def _download_timeout(self) -> None:
        if self._process is None:
            return
        self._download_timed_out = True
        self._cancel_download()

    def _download_finished(self, exit_code: int, model_id: str) -> None:
        process = self._process
        self._timeout.stop()
        self._process = None
        self._set_download_running(False)
        if process is not None:
            process.deleteLater()
        if self._download_timed_out:
            self._show_error("모델 다운로드가 30분을 넘어 중단했습니다. 네트워크와 저장 공간을 확인해 주세요.")
            self._show_inventory()
            return
        if self._download_canceled:
            self.inventory_label.setText("모델 다운로드를 취소했습니다.")
            return
        if exit_code != 0:
            self._show_error("모델 다운로드에 실패했습니다. Ollama 실행 상태와 모델 이름을 확인해 주세요.")
            self._show_inventory()
            return
        self._scan(model_id)
        if model_id not in self._inventory.vision_ollama_models:
            self._show_error(
                "다운로드는 끝났지만 이미지 입력 지원을 확인하지 못했습니다. Vision 모델인지 확인해 주세요."
            )

    def _save_and_accept(self) -> None:
        endpoint = self._normalized_endpoint()
        if endpoint is None:
            return
        model_id = self.selected_model_id
        if model_id is None:
            self._show_error("이미지 입력이 확인된 로컬 모델을 선택해 주세요.")
            return
        settings = replace(
            self._settings,
            mode=AnalysisMode.LOCAL,
            local_model_id=model_id,
            local_endpoint=endpoint,
            local_setup_completed=True,
        )
        if not self._save(settings):
            return
        self.accept()

    def _continue_without_local(self) -> None:
        if not self._save(replace(self._settings, local_setup_completed=True)):
            return
        self.accept()

    def _save(self, settings: AppSettings) -> bool:
        try:
            self._store.save(settings)
        except (OSError, ValueError) as exc:
            self._show_error(f"설정을 저장하지 못했습니다: {exc}")
            return False
        self._settings = settings
        return True

    def _show_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.setVisible(bool(message))

    def reject(self) -> None:
        if self._process is not None:
            self._cancel_download()
            return
        super().reject()
