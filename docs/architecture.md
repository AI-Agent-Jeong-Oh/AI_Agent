# 아키텍처

## 구성 원칙

앱은 한 프로세스의 PySide6 Widgets 데스크톱 애플리케이션입니다. 웹 서버나 데이터베이스를 두지 않고 사용자가 고른 로컬 경로를 직접 읽습니다. UI는 공통 계약만 사용하며 샘플, 로컬 VLM, 외부 VLM 분석기는 같은 `Analyzer` 프로토콜을 구현합니다.

```text
로컬 영상
  ├─ QMediaPlayer ──────────────────────────► 재생·원본 seek
  └─ MetadataWorker ─► FFprobe ─► VideoMetadata
                                │
사용자 목적·모드·범위 ─► AgentSession
  ├─ 필요한 경우 ClarificationQuestion
  ├─ AnalysisPlan ─► 분석 승인
  └─ 외부 모드 ───► TransferManifest ─► 별도 전송 승인
                                │
                                ▼
                         AnalysisWorker(QThread)
              ┌─────────────────┴────────────────┐
              ▼                                  ▼
       SampleAnalyzer                    VisionAnalyzer
       프레임 해석 없음             FFmpeg 제한 추출 → VisionProvider
                                                 │
                                                 ▼
                                     응답 JSON·근거·구간 검증
                                                 │
                                                 ▼
                                       AnalysisJob / ResultStore
```

## 모듈 경계

| 경로 | 책임 | 주 담당 |
|---|---|---|
| `contracts.py` | 요청·한도·상태·구간·결과 출처 공통 형식 | 합의 후 변경 |
| `agent/planning.py` | 추가 질문, 계획 fingerprint, 승인, 실행 횟수, 상태 전환 | 승재 |
| `video/probe.py` | FFprobe JSON을 검증해 메타데이터로 변환 | 승재 |
| `video/frames.py` | 원본 초 기준 제한 프레임 추출과 decoded PTS 기록 | 승재 |
| `video/process.py` | shell 없는 subprocess, 취소·timeout·프로세스 종료 | 승재 |
| `analysis/sample.py` | 실제 장면을 보지 않는 샘플 후보 | 승재 |
| `analysis/vision.py` | 프레임 추출, VLM prompt, 엄격한 출력 검증 | 승재 |
| `models/` | 설정 검증, 로컬/외부 HTTP 어댑터, 런타임 조사 | 승재 |
| `storage/` | 사용자 설정·결과·앱 소유 캐시 | 승재 |
| `knowledge/contracts.py` | 팀원 PDF 검색 구현이 따를 형식 | 공통 계약/팀원 구현 |
| `ui/main_window.py` | 현재 최소 연결 UI. 디자인·접근성 개선 대상 | 팀원 중심 |

실행 진입점은 `python -m video_agent`입니다. 공통 계약이나 진입점을 바꾸면 코드, `docs/contracts.md`, 테스트를 같은 변경에서 갱신하고 상대 담당자의 검토를 받습니다.

## 원본 시간 처리

- 앱 계약은 원본 시작을 0으로 한 초 단위 `float`입니다.
- Qt 재생 위치 직전에만 `seconds_to_milliseconds()`로 변환합니다.
- FFprobe의 `avg_frame_rate`와 `r_frame_rate` 차이는 VFR 징후로만 기록합니다. 값이 같아도 고정 프레임률이라고 단정하지 않습니다.
- 샘플링은 승인 범위의 timestamp를 균등 배치합니다. 프레임 번호를 FPS로 나누지 않습니다.
- FFmpeg는 각 timestamp의 한 프레임만 디코딩합니다. `showinfo`의 PTS에서 source start time을 빼 원본 상대 시각을 기록합니다.
- 회전 metadata는 표시 해상도에 반영하며 FFmpeg 기본 autorotation으로 추출합니다. 원본 파일은 쓰지 않습니다.

## Agent 상태

```text
empty
  ├─ 목적 부족 ─► needs_clarification ─► plan_ready
  └─ 정보 충분 ────────────────────────► plan_ready
plan_ready ─► approved ─► running ─► validating ─► completed | failed
           └─► analysis_approved ─► approved   (external transfer approval)
                              └──────► canceled
completed 등 ─► expansion_proposed (자동 실행 없음)
```

계획 fingerprint에는 영상 ID·길이, 목적, 모드, 모델, 공급자 endpoint, 범위, 프레임 timestamp, 실행 한도가 포함됩니다. 영상·목적·모델·범위가 바뀌면 계획과 두 승인이 폐기됩니다. 기본 실행은 1회, 자동 재분석 0회, 공급자 자동 retry 0회입니다.

UI 취소는 active job을 먼저 해제한 뒤 worker에 cancel event를 보냅니다. 이후 같은 job ID의 결과 signal은 `AgentSession.accept_result()`에서 거부하므로 늦은 결과가 화면을 덮지 않습니다.

## 모델 연결

- `sample`: 모델/프레임 해석 없음.
- `local`: loopback endpoint만 허용. 실패 시 외부 모드로 전환하지 않음.
- `external`: HTTPS만 허용. 분석 승인 외에 `TransferManifest` 전송 승인이 있어야 요청 생성 가능.

`VIDEO_AGENT_*_MODEL_SUPPORTS_VISION=true`를 명시하지 않으면 텍스트 모델을 거부합니다. Ollama native adapter는 `/api/chat`의 base64 `images`와 JSON schema를 사용합니다. OpenAI-compatible adapter는 text와 data URL image content를 구성합니다. 외부 실제 호출은 이번 구현·검증에서 수행하지 않았습니다.

모델 출력은 다음 정책으로 검증합니다.

- 최상위 `segments` 이외 필드 거부
- 필수 필드와 형식, 최대 20개 제한
- 승인 범위 밖 또는 역전 구간 거부; 자동 보정 없음
- 입력하지 않은 frame ID 근거 거부
- 빈 근거/불확실성 거부
- 일부 명시적 범죄·의도 확정 표현 거부
- 실패 시 샘플 결과로 대체하지 않음

## 저장과 보안

macOS 기본 위치는 다음과 같습니다.

- 설정·결과: `~/Library/Application Support/VideoAnalysisAgent/`
- 추출 프레임 캐시: `~/Library/Caches/VideoAnalysisAgent/`

설정에는 API key 환경 변수 이름만 저장하고 key 값은 저장·로그하지 않습니다. 결과에는 원본 경로가 없습니다. 캐시 정리는 root marker와 job marker가 모두 있는 앱 생성 디렉터리만 대상으로 합니다.

## PDF 연결 경계

이 단계에서는 PDF를 추출·색인하지 않습니다. `KnowledgeSearch`는 질의에 `KnowledgeResult`를 반환하며 hit는 본문, 문서 ID·이름, 페이지, 자료 버전, 선택적 점수·임베딩 정보를 포함합니다. 현재 기본 구현은 hit 없이 `unavailable`을 반환하므로 가짜 출처가 나타나지 않습니다.

## 개발 패키징

`packaging/video_analysis_agent.spec`은 PySide6 Widgets/Multimedia만 수집하고 WebEngine/PDF 모듈을 제외합니다. 영상·모델·키·Homebrew FFmpeg는 포함하지 않습니다. macOS `.app`은 one-folder BUNDLE이고 PyInstaller ad-hoc 서명만 적용됩니다. Finder 실행에서도 Homebrew FFmpeg를 찾도록 알려진 경로를 확인합니다.
