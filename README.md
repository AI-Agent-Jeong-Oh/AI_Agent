# 영상 데이터 중요 구간 분석 Agent

PC의 로컬 영상에서 사용자 목적에 맞는 중요 구간 후보를 만들고 원본 시점에서 확인하는 macOS 데스크톱 앱입니다. 현재 기본 모드는 **샘플 결과이며 실제 AI 분석이 아닙니다.** 실제 VLM 연결 코드는 준비되어 있지만 이 저장소가 모델을 다운로드하거나 외부 API를 자동 호출하지 않습니다.

## 구현 상태

- 로컬 파일 선택, 영상 재생, 원본 타임라인 탐색
- FFprobe 기반 길이·해상도·평균/명목 FPS·회전·오디오·timebase 확인
- FFmpeg로 승인된 원본 시각의 프레임만 제한 추출하고 디코딩 PTS 기록
- 목적이 모호할 때만 추가 질문 → 계획 → 분석 승인 → 실행 → 결과 검증
- 목적·영상·모델·범위 변경 시 승인 무효화
- 외부 API의 분석 승인과 전송 내용·범위 승인을 별도 처리
- 샘플/테스트 가짜 응답/실제 모델 결과 출처 구분
- 백그라운드 메타데이터·분석 작업, 취소, 시간·프레임·실행 횟수 제한
- macOS 사용자 데이터 폴더에 설정·결과·캐시 분리 저장
- 로컬/외부 VLM HTTP 어댑터와 엄격한 JSON 응답 검증
- 팀원 구현용 PDF 검색 계약과 “자료 없음/미연결” 기본 구현
- 서명·공증 없는 개발용 macOS `.app` 빌드 설정

## 개발 환경

- 기준 OS: macOS 26.6.2, Apple Silicon
- Python: 3.13.x
- PySide6 Essentials/Addons: 6.12.0
- FFmpeg/ffprobe: 개발 PC에서 8.1.2로 검증
- PyInstaller: 6.22.3, 개발 패키징에만 사용

Qt 공식 설치 문서는 Python 3.10 이상과 가상환경을 권장합니다. PySide6 6.12.0과 PyInstaller 6.22.3의 macOS universal2 배포본은 Python 3.13과 arm64를 지원합니다.

## 설치와 실행

FFmpeg가 없다면 먼저 설치하고 `ffprobe`, `ffmpeg`가 실행되는지 확인합니다. 앱은 PATH 외에도 Apple Silicon/Homebrew의 `/opt/homebrew/bin`과 Intel Homebrew의 `/usr/local/bin`을 확인합니다.

```bash
cd /Users/seungjae/Desktop/SNU/AI_Agent
python3.13 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements.txt
PYTHONPATH=src ./.venv/bin/python -m video_agent
```

VS Code에서는 `.venv/bin/python`을 interpreter로 선택합니다. 앱은 선택한 원본을 수정하거나 저장소로 복사하지 않습니다.

## 분석 모드

### 샘플

모델이나 프레임 해석 없이 승인 범위의 길이 비율로 후보를 만듭니다. 화면과 결과에 `샘플 결과 — 실제 AI 분석 아님`을 표시합니다.

### 로컬 VLM

현재 PC에는 Ollama/MLX-LM/llama.cpp와 로컬 모델이 발견되지 않았으므로 실제 추론은 미검증입니다. 아래 변수에 **이미 설치되어 있고 영상 입력 지원을 확인한 모델**을 명시해야 합니다. 로컬 endpoint는 loopback 주소만 허용하며 실패해도 외부 API로 전환하지 않습니다.

```bash
export VIDEO_AGENT_LOCAL_MODEL='설치된-vision-model-id'
export VIDEO_AGENT_LOCAL_MODEL_SUPPORTS_VISION=true
export VIDEO_AGENT_LOCAL_ENDPOINT='http://127.0.0.1:11434/api/chat'
PYTHONPATH=src ./.venv/bin/python -m video_agent
```

Ollama native `/api/chat` 형식은 프레임을 base64 `images`로 보내고 JSON schema 응답을 요청합니다. 모델 설치·다운로드 명령은 프로젝트에서 자동 실행하지 않습니다.

### 외부 API VLM

아래와 같은 명시적 설정이 필요합니다. `.env.example`에는 이름만 있으며 앱은 `.env`를 자동으로 읽지 않습니다. 실제 키는 환경 변수나 후속 비밀 저장소에서 주입합니다.

```bash
export VIDEO_AGENT_EXTERNAL_PROVIDER='provider-id'
export VIDEO_AGENT_EXTERNAL_ENDPOINT='https://provider.example/v1/chat/completions'
export VIDEO_AGENT_EXTERNAL_PROTOCOL='openai_chat_completions'
export VIDEO_AGENT_EXTERNAL_MODEL='vision-model-id'
export VIDEO_AGENT_EXTERNAL_MODEL_SUPPORTS_VISION=true
export VIDEO_AGENT_EXTERNAL_API_KEY='실제 값은 셸/비밀 저장소에서만 설정'
PYTHONPATH=src ./.venv/bin/python -m video_agent
```

계획 화면은 공급자, 목적지 origin, 목적 텍스트, JPEG 프레임 수와 원본 시각을 보여 줍니다. 분석 계획 승인 후에도 외부 전송을 별도로 승인해야 실행됩니다. 원본 영상 전체, PDF, 라벨 JSON은 전송 계획에 포함하지 않습니다.

## 시간과 결과 정책

- 계약 시간은 원본 시작 기준 초(`float`), Qt UI 경계만 밀리초(`int`)입니다.
- 가변 프레임률 여부를 FPS 값 하나로 확정하지 않으며 프레임 번호/FPS로 시각을 만들지 않습니다.
- 추출 결과는 요청 시각과 FFmpeg가 디코딩한 원본 PTS를 함께 기록합니다.
- 모델 구간이 승인 범위를 벗어나거나 `end <= start`이면 몰래 보정하지 않고 결과 전체를 거부합니다.
- 모델 실패를 샘플 결과로 대체하지 않습니다.
- 진행률을 모르는 메타데이터/추출/모델 호출은 불확정 상태로 표시합니다.

## 사용자 데이터

기본 위치:

```text
~/Library/Application Support/VideoAnalysisAgent/settings.json
~/Library/Application Support/VideoAnalysisAgent/results/*.json
~/Library/Caches/VideoAnalysisAgent/<job-id>/
```

설정에는 API 키 값이 아니라 환경 변수 이름만 저장합니다. 결과에는 원본 파일 경로를 저장하지 않습니다. 캐시는 앱 marker가 있는 작업 폴더만 정리합니다. 테스트에서는 `VIDEO_AGENT_DATA_DIR`, `VIDEO_AGENT_CACHE_DIR`로 격리할 수 있습니다.

## 테스트

```bash
QT_QPA_PLATFORM=offscreen PYTHONPATH=src \
  ./.venv/bin/python -m unittest discover -s tests -v
```

테스트는 순수 계약, Agent 상태, 가짜 공급자, 저장소, 오프스크린 UI와 짧은 합성 H.264 영상의 FFprobe/FFmpeg 처리를 포함합니다. 이는 실제 CCTV 정확도나 실제 VLM 추론 검증이 아닙니다.

## 개발용 `.app`

```bash
./.venv/bin/python -m pip install -r requirements-dev.txt
./scripts/build_macos_app.sh
open dist/VideoAnalysisAgent.app
```

결과는 `dist/VideoAnalysisAgent.app`입니다. 모델 가중치, 영상, 키와 Homebrew FFmpeg는 번들에 포함하지 않습니다. 현재 빌드는 arm64이며 PyInstaller의 ad-hoc 서명만 있습니다. 개발자 인증서 서명·공증·배포 호환성은 미검증입니다.

## 문서

- [현재까지 구현한 내용과 방식](docs/progress.md)
- [아키텍처](docs/architecture.md)
- [UI/분석/PDF 연결 계약](docs/contracts.md)
- [역할별 다음 작업](docs/tasks.md)
- [승재 작업 인계](docs/handoff-seungjae.md)

AI-Hub 데이터셋 515의 `VS9.zip`/`VL9.zip`은 다운로드하거나 확인하지 않았습니다. 제공되면 팀원 담당 절차에 따라 실제 MP4/JSON 구조와 파일명 대응부터 확인하며 정답 라벨을 분석 입력에 넣지 않습니다.
