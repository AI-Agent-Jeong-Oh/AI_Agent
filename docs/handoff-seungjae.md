# 승재 담당 연속 작업 인계

작성 기준: 2026-10-09, macOS 26.6.2 arm64, Python 3.13.14

## 1. 구현한 기능

### 영상 처리

- FFprobe 8.1.2 JSON에서 duration, coded/display 해상도, 평균/명목 FPS, VFR 징후, 회전, codec, audio, start time, timebase를 검증해 읽습니다.
- UI의 밀리초와 계약의 원본 초 변환 함수를 분리했습니다.
- FFmpeg 8.1.2로 승인된 timestamp의 프레임만 JPEG로 추출합니다. 전체 영상을 메모리에 적재하지 않습니다.
- 요청 timestamp와 `showinfo` decoded PTS를 모두 기록하며 한글·공백 경로, 짧은 영상, 손상 파일을 테스트했습니다.
- subprocess는 shell 없이 실행하고 cancel/timeout 때 process group을 종료합니다.

### Agent 흐름

- 목적이 짧거나 일반적일 때만 focus 질문을 생성합니다.
- 계획은 영상/목적/모드/모델/공급자 endpoint/범위/timestamp/한도의 fingerprint를 가집니다.
- 분석 승인과 외부 전송 승인을 구분하고 외부 모드의 중간 상태를 `analysis_approved`로 명시했습니다.
- 승인 후 입력 변경 시 계획과 승인을 무효화합니다.
- 기본 실행 1회, 자동 재분석 0회, 공급자 retry 0회, 최대 12프레임, 30초를 계약으로 강제합니다.
- 범위 확대는 `expansion_proposed` 상태만 만들고 자동 실행하지 않습니다.
- 취소 직후 active job을 해제해 늦은 progress/result가 UI를 변경하지 못합니다.

### VLM 연결

- 샘플 분석기는 보존했고 실제 `VisionAnalyzer`와 분리했습니다.
- 로컬/외부 공통 `VisionProvider`와 HTTP adapter를 구현했습니다.
- Ollama native `/api/chat`의 base64 images + JSON schema 형식과 OpenAI-compatible chat completions image data URL 형식을 지원합니다.
- 로컬 모드는 loopback endpoint만 허용하고 외부로 fallback하지 않습니다.
- 실제 모드는 모델 ID와 `*_MODEL_SUPPORTS_VISION=true`가 없으면 거부합니다.
- 외부 모드는 HTTPS와 `transfer_approval_id`가 없으면 request를 만들 수 없습니다.
- 출력 JSON 필드, 승인 범위, `start < end`, 실제 입력 frame ID, 근거·불확실성, 일부 위험한 확정 표현을 검증합니다. 잘못된 구간은 자동 보정하지 않습니다.

### 저장·PDF 계약·패키징

- macOS Application Support에 설정·결과, Caches에 프레임을 저장합니다.
- API key 값은 저장하지 않고 환경 변수 이름만 저장합니다. 결과 JSON에는 원본 경로가 없습니다.
- root/job marker가 있는 앱 생성 캐시만 삭제합니다.
- 팀원 PDF 검색 구현을 위한 query/hit/status/embedding 계약과 unavailable 구현을 추가했습니다.
- PyInstaller 6.22.3 spec과 빌드 스크립트를 추가하고 arm64 `.app`을 만들었습니다.
- `.gitignore`를 루트 데이터/가중치 폴더에 한정해 `src/video_agent/models/` 소스가 잘못 제외되지 않게 했습니다.

## 2. 변경한 주요 파일

- 공통 계약: `src/video_agent/contracts.py`
- Agent: `src/video_agent/agent/planning.py`
- 영상: `src/video_agent/video/probe.py`, `frames.py`, `process.py`, `worker.py`
- 분석: `src/video_agent/analysis/vision.py`, `factory.py`, 기존 `sample.py`, `worker.py`
- 모델: `src/video_agent/models/config.py`, `provider.py`
- 저장: `src/video_agent/storage/paths.py`, `json_store.py`
- PDF 연결: `src/video_agent/knowledge/contracts.py`
- 최소 연결 UI: `src/video_agent/ui/main_window.py`
- 패키징: `packaging/video_analysis_agent.spec`, `scripts/build_macos_app.sh`, `requirements-dev.txt`
- 계약/설계/작업 문서: `docs/contracts.md`, `architecture.md`, `tasks.md`

UI 변경은 승재 기능을 연결하는 데 필요한 모드·모델·범위·추가 질문·두 승인·메타데이터 표시에 한정했습니다. 화면 재설계와 접근성 마무리는 팀원 범위로 남겼습니다.

## 3. 실행 방법

```bash
python3.13 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
PYTHONPATH=src ./.venv/bin/python -m video_agent
```

테스트:

```bash
QT_QPA_PLATFORM=offscreen PYTHONPATH=src \
  ./.venv/bin/python -m unittest discover -s tests -v
```

개발용 앱:

```bash
./.venv/bin/python -m pip install -r requirements-dev.txt
./scripts/build_macos_app.sh
open dist/VideoAnalysisAgent.app
```

## 4. 실제 검증 결과

- 기존 테스트 10개를 먼저 재확인했습니다.
- 최종 자동 테스트 47개가 통과했습니다. 계약, Agent 승인, 실행 한도, provider timeout, invalid model JSON, 외부 fallback 금지, 저장 round-trip, cache 소유권, 오프스크린 UI와 영상 처리가 포함됩니다.
- `compileall`과 `pip check`가 성공했습니다.
- 합성 H.264 320×240/24fps/3초 영상을 한글·공백 경로에 생성했습니다.
- FFprobe duration 3.000초, Qt duration 3000ms, 재생 위치 208ms까지 전진을 확인했습니다.
- 새 UI에서 계획 전 실행 차단 → 분석 승인 → 샘플 후보 2개 → 결과 1개 저장 → 첫 후보의 원본 300ms seek를 확인했습니다.
- 목적을 바꿔 재승인한 두 번째 작업을 취소하고 상태 `canceled`, 후보 0개, 저장 결과 1개로 늦은 결과가 화면과 저장소를 덮어쓰지 않음을 확인했습니다.
- 가짜 VisionProvider를 사용해 프레임/목적 request 구성과 정상·invalid 응답을 확인했습니다. 이는 실제 모델 검증이 아닙니다.
- `.app` 빌드 성공: 113MB, Mach-O arm64, bundle identifier `org.ai-agent-jeong-oh.video-analysis-agent.dev`.
- 번들 내부 실행 파일을 offscreen으로 시작해 자동 종료 코드 0을 확인했습니다.
- `codesign` 결과는 TeamIdentifier가 없는 `Signature=adhoc`입니다. 인증서 서명·공증은 하지 않았습니다.
- PyInstaller warning은 Windows/Java 등 조건부 모듈뿐이었으며 앱 smoke 실행에서 import 실패는 없었습니다.

## 5. 미구현·미검증과 이유

- **실제 로컬 VLM 추론:** Ollama, MLX-LM, llama.cpp 실행기와 설치 모델이 현재 PC에서 발견되지 않았습니다. 자동 다운로드 금지 조건에 따라 설치하지 않았습니다.
- **실제 외부 API 호출:** 키·비용·외부 전송 승인이 없고 이번 무인 작업에서 전송 금지이므로 호출하지 않았습니다.
- **실제 CCTV 성능:** VS9/VL9 파일이 없으므로 구조·정확도·속도·행동 유형을 확인하지 않았습니다.
- **PDF 검색:** 팀원 담당이며 자료도 없어 계약과 unavailable 상태만 구현했습니다.
- **실제 화면/오디오:** 오프스크린 GUI와 playback position만 확인했습니다. 화면에 프레임이 보이고 오디오가 들리는지는 미검증입니다.
- **회전/VFR 실물:** parser와 정책은 구현했지만 실제 회전 metadata/VFR 샘플 영상의 육안 결과는 미검증입니다.
- **배포:** Finder 수동 실행, 다른 계정/최저 지원 macOS, 개발자 인증서 서명·공증·외부 배포는 미검증입니다.
- `docs/collaboration-guide.html`은 저장소에 없었습니다. 첨부된 `영상분석_Agent_2인협업가이드_수정본.html`을 읽고 현재 요구사항을 우선 적용했습니다.

## 6. 승재가 돌아와서 확인할 항목

1. `git status`와 전체 diff를 검토합니다. 특히 공통 계약 변경은 팀원과 합의합니다.
2. 실제 화면에서 MP4 프레임, 오디오, timeline, 긴 plan/result 표시를 확인합니다.
3. 실제 로컬 VLM 후보를 정한 뒤 이미지 입력 지원·라이선스·M5 24GB 메모리를 확인하고 명시적으로 설치합니다.
4. `.env.example`의 이름을 참고해 shell에만 모델 설정을 넣고 실제 모델 한 건을 검증합니다.
5. 외부 API가 필요하면 공급자 개인정보·비용 정책을 확인한 뒤 사용자 입회하에 payload와 전송 승인을 비교합니다.
6. `dist/VideoAnalysisAgent.app`을 Finder에서 열고 FFprobe/Homebrew 경로를 확인합니다.

## 7. 팀원이 시작할 작업과 파일

- UI/접근성: `src/video_agent/ui/main_window.py`. Agent와 계약 코드는 변경하지 않고 현재 signal/contract에 연결합니다.
- 데이터 조사: `tools/inspect_dataset.py`를 새로 만들고 `FFprobeVideoProbe`를 재사용합니다. 데이터는 Git 밖에 둡니다.
- PDF: `src/video_agent/knowledge/contracts.py`를 구현하는 검색기와 `tools/build_knowledge.py`를 작성합니다.
- 사용성 문서: `docs/tasks.md` 수동 절차에 실제 확인 환경과 실패를 기록합니다.

공통 `contracts.py`, 실행 진입점, 상태 의미를 바꾸려면 `docs/contracts.md`와 테스트를 함께 수정하고 승재 검토를 요청합니다.

## 8. 현재 Git 상태

- 브랜치: `main`, `origin/main` 추적
- 초기 구현 커밋: `0635f02 feat: add initial desktop video analysis agent`
- 초기 앱 코드·문서·테스트를 원격 `main`에 push함
- force push, PR, merge는 수행하지 않음
- `.venv`, `build`, `dist`, 영상, ZIP, 모델, `.env`, 사용자 결과·캐시는 ignore 상태

## 샘플과 실제 모델 구분

### 샘플로만 동작하는 부분

- 기본 UI 실행과 후보 구간 생성
- 샘플 후보 내용과 근거
- 테스트의 `FakeVisionProvider` 응답
- 실제 VLM이 없을 때의 모델 연결 오류 경로

### 실제 모델로 확인한 부분

- **없음.** 실제 로컬/외부 VLM 추론은 한 번도 수행하지 않았습니다.

### 실제 도구로 확인한 기반

- 실제 FFprobe/FFmpeg 8.1.2 메타데이터·프레임 처리
- 실제 Qt Multimedia backend의 합성 MP4 duration·playback position
- 실제 PyInstaller 6.22.3 macOS arm64 bundle 빌드와 실행 시작/정상 종료
