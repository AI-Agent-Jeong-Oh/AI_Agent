# 아키텍처

## 구성 원칙

앱은 한 프로세스의 PySide6 Widgets 데스크톱 애플리케이션입니다. 웹 서버나 데이터베이스를 두지 않고 사용자가 고른 로컬 경로를 직접 읽습니다. UI는 공통 계약만 사용하며 샘플, 로컬 VLM, 외부 VLM 분석기는 같은 `Analyzer` 프로토콜을 구현합니다.

```text
로컬 영상
  ├─ QMediaPlayer ──────────────────────────► 재생·원본 seek
  ├─ MetadataWorker ─► FFprobe ─► VideoMetadata
  └─ MotionScanWorker ─► FFmpeg 프레임 차이 ─► MotionScanResult
                                                   │
                                사용자 후보 최대 3개 선택·범위/정점 시각 적용
                                                   │
                                                   ▼
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
| `video/motion.py` | 일반 최대 변화 및 반복·미세 국소 변화 프로필의 제한 구간 제안. 행동 판정은 하지 않음 | 승재 |
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
- 움직임 탐색도 `fps` 필터가 원본 PTS에 따라 만든 시각을 사용합니다. 일반 프로필은 320px 이하 전체 화면 차이, 반복·미세 프로필은 160px 회색조를 8×5 타일로 나눈 뒤 변화가 큰 상위 20% 타일을 비교합니다. 두 방식 모두 최대 600개 저해상도 표본으로 제한하며 원본 영상 전체를 메모리에 올리지 않습니다.
- 움직임 후보 하나를 적용하면 최대 변화 시각을 중심으로 0.25초 간격 12개, 두세 개를 적용하면 각 후보에 0.25초 간격 4개씩 배정합니다. 총 12프레임과 최대 3회 요청 한도는 늘리지 않으며 선택 구간 밖으로 확장하지 않습니다.
- 목적이 두리번거림·주변 살피기·반복 방향 변화이면 큰 변화 하나를 우선하는 대신 10초 동안 분산된 국소 변화를 평가합니다. 이 후보를 적용하면 한 구간은 전체에 12개, 여러 구간은 각 구간 전체에 4개씩 시간순으로 분산합니다. 픽셀 변화는 행동 의미를 판정하지 않습니다.
- 회전 metadata는 표시 해상도에 반영하며 FFmpeg 기본 autorotation으로 추출합니다. 원본 파일은 쓰지 않습니다.

## Agent 상태

```text
empty
  ├─ 목적 부족 ─► needs_clarification ─► plan_ready
  └─ 정보 충분 ────────────────────────► plan_ready
plan_ready ─► approved ─► running ─► validating ─► completed | partial | failed
           └─► analysis_approved ─► approved   (external transfer approval)
                              └──────► canceled
completed 등 ─► expansion_proposed (자동 실행 없음)
```

계획 fingerprint에는 영상 ID·길이, 목적, 모드, 모델, 공급자 endpoint, 범위, 프레임 timestamp, 실행 한도가 포함됩니다. 영상·목적·모델·범위가 바뀌면 계획과 두 승인이 폐기됩니다. 기본 실행은 1회, 자동 재분석 0회, 공급자 자동 retry 0회입니다.

목적이 너무 넓으면 관찰 대상을 묻고, '오래·장시간·체류·지속'처럼 시간이 핵심인데 몇 초·몇 분인지 없으면 `duration_threshold`을 추가로 묻습니다. 답변은 최종 목적과 fingerprint에 포함되어 다른 시간 기준을 기존 승인으로 실행할 수 없습니다.

UI 취소는 active job을 먼저 해제한 뒤 worker에 cancel event를 보냅니다. 이후 같은 job ID의 결과 signal은 `AgentSession.accept_result()`에서 거부하므로 늦은 결과가 화면을 덮거나 저장되지 않습니다. HTTP 요청을 중간에 확실히 중단할 수 있다고 가정하지 않으므로 worker가 응답 또는 timeout으로 끝날 때까지 입력과 새 실행을 계속 잠급니다.

## 모델 연결

- `sample`: 모델/프레임 해석 없음.
- `local`: loopback endpoint만 허용. 실패 시 외부 모드로 전환하지 않음.
- `external`: HTTPS만 허용. 분석 승인 외에 `TransferManifest` 전송 승인이 있어야 요청 생성 가능.

로컬 Ollama는 `/api/show`의 `capabilities`에 `vision`이 있는 설치 모델만 자동 확인합니다. 명시적 환경 override는 설정된 모델 ID에만 적용됩니다. Ollama native adapter는 `/api/chat`의 base64 `images`와 JSON schema를 사용하고 16K context, 800 출력 token으로 제한합니다. 현재 로컬 UI 계획은 최대 12프레임을 연속된 4프레임 묶음 최대 3개로 나누며 모든 묶음이 하나의 300초 deadline을 공유합니다. `SamplingStrategy`는 `uniform`, `motion_peak`, `temporal_sequence`를 구분하고 계획 fingerprint·요청·저장 결과에 포함됩니다.

로컬 모드의 일반 `temporal_sequence`는 각 묶음의 원본 프레임을 FFmpeg로 최대 2×2 행 우선 비교 보드 한 장에 합칩니다. 공급자에는 비교 보드만 전달하지만 응답 검증은 원래 frame ID와 decoded PTS를 기준으로 하며 원본 파일은 수정하지 않습니다. 다만 손가락·버튼·초인종·누르기·접촉처럼 작은 대상의 상대 위치가 핵심인 목적은 `fine_detail`로 분류합니다. 최대 1920px 원본을 추출한 뒤 각 원본 시각마다 전체 화면 축소 보기와 중앙 60% 확대 보기를 세로로 배치한 1280×1080 이미지를 만듭니다. 시간 시각끼리는 합치지 않으며 중앙 밖 대상은 전체 보기로 확인합니다. `ModelExecutionInfo`는 원본 입력 프레임과 실제 공급자 이미지의 수·크기·`input_representation=fine_detail_overview_center_crop`을 따로 기록합니다. 현재 `candidate-recall-v9-overview-detail-crop`은 일반 시간 변화 입력에서는 비교 시각별 방향·자세를, 정밀 접촉 입력에서는 손·손가락과 버튼·대상 사이의 간격·겹침·접촉 가능성을 우선 확인합니다. 목적과 무관한 큰 동작으로 대체하지 않고 심리·의도나 실제 작동 여부를 단정하지 않는 규칙도 포함합니다. 외부 모드는 승인 manifest와 실제 전송 이미지가 달라지지 않도록 승인된 개별 프레임을 그대로 사용하며 로컬 합성 입력을 자동 적용하지 않습니다. 완료 배치별 원시 후보 수와 프롬프트 버전을 저장하고 묶음 후보를 근거 없이 합치지 않습니다. OpenAI-compatible adapter는 text와 data URL image content를 구성합니다. 외부 실제 호출은 수행하지 않았습니다.

개발 PC에서는 `qwen3-vl:4b-instruct`와 `qwen3-vl:8b-instruct` 실제 추론을 확인했습니다. 기본은 자원 사용이 작은 4B instruct로 유지하고 8B instruct는 명시적으로 선택하는 비교 모델입니다. `qwen3-vl:8b` 기본 태그는 thinking variant라 구조화 응답보다 긴 사고 출력을 우선하여 현재 앱 기본 선택으로 사용하지 않습니다. 최종 JSON `content` 없이 `thinking`만 반환한 응답은 명시적 실패이며, UI 상단 배너와 결과 목록은 실패한 모델과 오류를 표시해 이전 성공 배너가 남지 않게 합니다. 설치 모델 가운데 저장된 선택이 없으면 `instruct` 이름을 우선합니다.

모델 출력은 다음 정책으로 검증합니다.

- 최상위 `segments` 이외 필드 거부
- 필수 필드와 형식, 공급자 응답당 최대 5개 제한
- 승인 범위 밖 또는 역전 구간 거부; 범위를 승인 안으로 몰래 자르는 보정 없음
- 입력하지 않은 frame ID 근거 거부
- 관찰 설명 대신 입력 frame ID 자체만 넣은 `evidence` 거부
- 승인 범위 밖·역전 구간은 거부. 승인 범위 안의 확인용 재생 범위가 근거 시각만 포함하지 못한 경우 근거 앞뒤 1초로 대체하고 불확실성에 보정 사실 기록
- 여러 배치의 후보는 기본적으로 별개로 유지. 다만 관찰 요약이 정규화 후 같고 확인 범위가 겹치며 실제 근거 시각이 1초 이내로 이어질 때만 중복 표시를 하나로 묶고, 모든 frame ID·원본 시각과 묶음 사실을 유지
- 빈 근거/불확실성 거부
- 일부 명시적 범죄·의도 확정 표현 거부
- 실패 시 샘플 결과로 대체하지 않음

## 저장과 보안

macOS 기본 위치는 다음과 같습니다.

- 설정·결과: `~/Library/Application Support/VideoAnalysisAgent/`
- 추출 프레임 캐시: `~/Library/Caches/VideoAnalysisAgent/`

설정에는 API key 환경 변수 이름만 저장하고 key 값은 저장·로그하지 않습니다. 결과에는 원본 경로가 없으며 영상 ID, 목적·범위, 모델 설정, 요청/실제 입력 시각·해상도, 공급자 요청 수·시간 지표와 출처를 저장합니다. 캐시 정리는 root marker와 job marker가 모두 있는 앱 생성 디렉터리만 대상으로 합니다.

## PDF 연결 경계

이 단계에서는 PDF를 추출·색인하지 않습니다. `KnowledgeSearch`는 질의에 `KnowledgeResult`를 반환하며 hit는 본문, 문서 ID·이름, 페이지, 자료 버전, 선택적 점수·임베딩 정보를 포함합니다. 현재 기본 구현은 hit 없이 `unavailable`을 반환하므로 가짜 출처가 나타나지 않습니다.

## 개발 패키징

`packaging/video_analysis_agent.spec`은 PySide6 Widgets/Multimedia만 수집하고 WebEngine/PDF 모듈을 제외합니다. 영상·모델·키·Homebrew FFmpeg는 포함하지 않습니다. macOS `.app`은 one-folder BUNDLE이고 PyInstaller ad-hoc 서명만 적용됩니다. Finder 실행에서도 Homebrew FFmpeg를 찾도록 알려진 경로를 확인합니다.
