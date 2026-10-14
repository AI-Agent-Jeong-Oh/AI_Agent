# UI ↔ Agent ↔ 분석 ↔ PDF 검색 계약

Python 기준 정의는 `src/video_agent/contracts.py`, Agent 계획은 `agent/planning.py`, PDF 검색 형식은 `knowledge/contracts.py`입니다.

## 이번 계약 변경 이유

초기 계약은 영상·목적과 결과만 표현해 “어떤 계획을 승인했는지”, “외부로 무엇을 보낼지”, “샘플과 실제 모델 결과가 어떻게 다른지”를 검증할 수 없었습니다. 다음 필드를 추가했습니다.

- `plan_id`, `approval_id`: 승인 전 실행 차단과 변경 후 재승인
- `mode`, `model_id`: 샘플/로컬/외부 및 명시적 모델 구분
- `range_start_sec`, `range_end_sec`, `sample_timestamps_sec`: 승인 시간 범위와 프레임 상한
- `transfer_approval_id`: 외부 전송의 별도 승인
- `origin`: `sample`, `test_double`, `real_model` 결과 출처
- `source_frame_ids`: 모델 근거가 실제 입력 프레임인지 확인
- `source_timestamps_sec`: 확인용 재생 범위와 별개인 실제 관찰 프레임 원본 시각
- `model_execution`: context·출력 한도·입력 해상도·요청 수·Ollama 시간 지표
- `model_execution.prompt_version`, `batch_candidate_counts`: 실행한 후보 선별 지침 버전과 완료 배치별 검증 전 후보 수
- `partial`, `origin=none`: 일부 배치 결과와 응답 없는 실패·취소를 실제 모델 완료와 분리
- `MotionScanResult`, `MotionProposal`: AI 호출 전의 로컬 픽셀 변화 제안과 실제 분석 결과를 분리
- `sampling_strategy`, `observation_guidance`: 균등·변화 정점·시간 변화 표본의 의미와 승인된 관찰 기준을 계획·요청·저장 결과에서 재현
- `MotionScanResult.profile`: 일반 최대 변화와 반복·미세 국소 변화 점수 정책을 구분
- `CaseRecord`, `AnalysisClue`, `CaseVideoLink`: 관리 정보와 모델에 전달할 사전 단서를 분리하고 사건 버전·영상 연결·카메라 시각 오차를 보존
- `AnalysisPlan.analysis_clues`: 승인한 단서 스냅샷을 fingerprint에 포함해 변경 후 재승인
- `TransferManifest.analysis_clues`: 외부로 전송할 사전 정보 문구를 프레임과 함께 명시
- `ReviewSession`, `CandidateReview`, `ReviewJudgement`: 모델 후보에 대한 사람의 검토 판단·메모를 모델 결과와 분리해 저장
- `PersonComparisonReport`, `PersonPairComparison`: 서로 다른 영상의 사용자 선택 후보를 쌍별로 비교한 외형 유사성 검토 자료를 사건 분석 결과와 분리해 저장
- `CaseRecord.focus_situations`, `CaseRecord.focus_hints`, `FocusSituation`: 사건 유형별로 제안된 "우선 확인할 상황"과 사용자가 직접 입력한 짧은 탐색 힌트를 사건과 함께 저장

모든 시간은 원본 시작 기준 초입니다. Qt UI 밀리초는 UI 경계에서만 변환합니다.

## 시작 설정 경계

`python -m video_agent`는 성공한 모든 로컬 로그인 뒤 메인 창보다 먼저 로컬 AI 설정을 엽니다. 이전에 저장한 `local_model_id`와 loopback `local_endpoint`를 기본값으로 보여 주며, 사용자는 확인된 Vision 모델을 선택하거나 명시적으로 설정 없이 계속할 수 있습니다. `local_setup_completed`는 이전 설정 파일과의 호환 및 선택 완료 기록으로 유지하지만 시작 창 표시를 생략하는 조건으로 사용하지 않습니다. 선택한 모델과 endpoint는 일반 설정이며 credential이 아닙니다. 모델 다운로드는 사용자가 버튼으로 시작한 `ollama pull`만 허용하고 취소와 30분 한도를 적용합니다. 다운로드된 모델도 Ollama capability에서 `vision`이 확인되기 전에는 분석 모델로 승인하지 않습니다.

이 설정은 분석 승인을 대신하지 않습니다. 모델이나 endpoint를 바꾸면 이후 `AnalysisPlan` fingerprint가 달라지므로 기존 계획과 승인을 다시 사용할 수 없습니다. 외부 host는 로컬 endpoint로 저장하거나 조사하지 않습니다.

## 사건 정보와 사전 단서

```json
{
  "case_id": "case-a1b2c3",
  "revision": 1,
  "title": "문 앞 택배 이동 확인",
  "case_type": "관리용 분류",
  "occurrence_time_text": "발생 시각 미확인",
  "occurrence_time_certainty": "unknown",
  "management_notes": "모델에 전송하지 않는 관리 메모",
  "clues": [{
    "clue_id": "clue-01",
    "text": "신고자는 문 앞 물체가 이동했다고 설명함",
    "source": "report",
    "certainty": "estimated",
    "use_for_analysis": true
  }],
  "video_links": [{
    "video_id": "video-hash",
    "camera_location": "공동현관 앞",
    "clock_offset_seconds": 0.0,
    "clock_certainty": "unknown"
  }],
  "focus_situations": ["movement", "stay"],
  "focus_hints": ["보라색 조끼"]
}
```

- 관리 정보인 `title`, `case_type`, `occurrence_time_text`, `management_notes`는 사건 저장과 화면 표시에 사용하며 VLM 프롬프트에는 자동 전달하지 않습니다.
- `use_for_analysis=true`인 `AnalysisClue`만 계획, 분석 요청, 결과 provenance에 포함됩니다. `source`는 `user_statement`, `report`, `document`, `other`, `certainty`는 `confirmed`, `estimated`, `unknown`입니다.
- 사전 정보는 모델 프롬프트에서 “영상 관찰 사실이 아닌 사용자 제공 정보”로 표시합니다. 단서가 사실이라고 가정하거나 단서의 문구를 관찰 결과로 복사하지 않도록 요구합니다.
- 사건 버전 또는 선택 단서의 ID·내용·출처·확실성이 바뀌면 plan fingerprint가 바뀌어 기존 분석·외부 전송 승인을 사용할 수 없습니다.
- `clock_offset_seconds`는 사건 메타데이터입니다. 프레임 추출·결과 계약은 계속 원본 영상 시작 기준 초와 decoded PTS를 사용하며 시각 오차로 원본 타임라인을 다시 쓰지 않습니다.
- 사건 JSON에는 `video_id`만 저장하고 원본 파일 경로는 저장하지 않습니다. 기본 저장 위치는 사용자 데이터 폴더의 `cases/`입니다.
- 외부 모드의 `TransferManifest`에는 전송할 단서 전체가 포함되며 UI가 문구를 표시합니다. 사건명·관리 메모·PDF·라벨 JSON은 기본 전송 대상이 아닙니다.

### 우선 확인할 상황 (`focus_situations`, `focus_hints`)

- `FocusSituation` 값: `interaction`(인물 간 상호작용), `movement`(이동·동선), `posture`(자세·움직임 변화), `stay`(체류·이탈), `company`(동행·분리), `environment`(주변 객체·환경 변화). 범주별 세부 예시와 사건 유형별 추천은 `agent/situations.py`에 있으며 팀 데모 화면과 같습니다(실종: 이동·동선·체류·이탈, 폭행: 상호작용·자세 변화, 아동학대: 상호작용·동행·분리·체류·이탈).
- 선택은 **확인 순서**에만 반영합니다. `AnalysisIntent.focus_situations`/`focus_hints`가 `PlanBuilder`에서 `observation_guidance` 문장으로 바뀌어 계획 fingerprint·분석 요청·모델 프롬프트("승인된 관찰 기준")에 들어갑니다. 문장에는 "선택하지 않은 상황도 관찰", "보이는 움직임만 기술하며 범죄 여부는 판단하지 않음", 힌트는 "영상 관찰 사실이 아님"을 함께 적습니다.
- 선택이나 힌트가 바뀌면 fingerprint가 바뀌어 기존 승인을 쓸 수 없습니다. 외부 모드에서는 관찰 기준 문장도 전송 내용에 포함됨을 계획 화면에 표시합니다.
- `focus_hints`는 최대 10개, 각 1~30자, 대소문자·공백 차이를 무시하고 중복 불가입니다. 두 필드가 없는 이전 사건 파일은 빈 값으로 읽습니다.
- `case_type` 자체는 여전히 모델에 전달하지 않습니다. 사건 유형은 사용자가 고른 값만 쓰며 데이터셋 폴더명·라벨에서 채우지 않습니다.

## `AnalysisPlan`

```json
{
  "plan_id": "plan-a1b2c3",
  "fingerprint": "sha256...",
  "video_id": "video-hash",
  "video_duration_sec": 60.0,
  "purpose": "출입문 앞 장시간 체류 후보 찾기",
  "mode": "local",
  "provider_id": "ollama-local",
  "provider_endpoint": "http://127.0.0.1:11434/api/chat",
  "model_id": "qwen3-vl:4b-instruct",
  "range_start_sec": 10.0,
  "range_end_sec": 40.0,
  "sampling_interval_sec": 2.5,
  "sample_timestamps_sec": [11.25, 13.75],
  "sampling_strategy": "temporal_sequence",
  "observation_guidance": ["사람의 의도나 심리를 추정하지 말고 머리·얼굴·몸통 방향을 서로 다른 원본 시각에서 비교"],
  "case_id": "case-a1b2c3",
  "case_revision": 1,
  "analysis_clues": [{
    "clue_id": "clue-01",
    "text": "신고자는 문 앞 물체가 이동했다고 설명함",
    "source": "report",
    "certainty": "estimated",
    "use_for_analysis": true
  }],
  "limits": {
    "max_runtime_seconds": 300.0,
    "max_reanalysis_attempts": 0,
    "max_frames": 12,
    "max_provider_attempts": 1,
    "max_provider_requests": 3,
    "max_frames_per_request": 4
  }
}
```

샘플 모드는 실제 프레임을 추출하지 않으므로 timestamp 배열이 비어 있습니다. 로컬/외부 모드는 최대 `max_frames` 안에서 승인 범위의 timestamp를 만듭니다. 현재 로컬 계획은 원본 시각 순서대로 4프레임씩 최대 3회 요청하며 전체 작업이 300초를 공유합니다. UI 기본 한도는 샘플·외부 30초입니다. 실제 계획에 표시된 값이 최종 승인 한도이고 기존 승인 뒤 한도를 바꾸면 fingerprint가 달라져 재승인이 필요합니다.

사용자가 움직임 제안을 적용하면 선택 범위와 명시 timestamp가 `AnalysisIntent`에 반영됩니다. 일반 프로필은 변화 정점 중심, 반복·미세 프로필은 구간 전체 시간순 표본을 사용합니다. `PlanBuilder`는 최대 12프레임 한도 안에서 범위·timestamp·`sampling_strategy`·`observation_guidance`를 fingerprint에 포함합니다. 적용만으로 분석하지 않으며 반드시 새 계획 승인부터 다시 진행합니다.

## 로컬 움직임 제안 계약

움직임 제안은 사용자 목적을 해석하지 않는 FFmpeg 전처리이며 `AnalysisJob`이나 `origin=real_model` 결과가 아닙니다.

```json
{
  "scan_id": "motion-a1b2c3",
  "video_id": "video-hash",
  "range_start_sec": 0.0,
  "range_end_sec": 120.0,
  "sample_interval_sec": 0.5,
  "sampled_frame_count": 239,
  "profile": "repeated_subtle",
  "proposals": [{
    "proposal_id": "motion-1-93000",
    "video_id": "video-hash",
    "start_sec": 92.0,
    "end_sec": 98.0,
    "peak_timestamp_sec": 93.0,
    "motion_score": 5.684,
    "recommended_sampling_interval_sec": 0.5
  }]
}
```

- `general_peak`의 `motion_score`는 320px 이하 전체 화면 차이에서 큰 변화를 순위화합니다. 최대 5개의 비중첩 6초 구간을 제안합니다.
- `repeated_subtle`은 160px 회색조를 8×5 타일로 나눠 상위 20% 국소 차이를 계산하고, 10초 동안의 활동 분포를 평가하며 가장 큰 전역 변화에 지배된 구간은 감점합니다. 최대 5개의 비중첩 10초 구간을 제안합니다.
- 두 프로필 모두 최대 600개 원본 PTS 기반 표본만 사용합니다. `motion_score`는 프로필 안의 상대 순위이며 확률·이상행동 점수·사람의 의도 판정이 아닙니다. 서로 다른 프로필의 숫자를 직접 비교하지 않습니다.
- 후보 선택은 원본의 최대 변화 시각으로 이동할 수 있지만 분석 범위 변경은 별도 `적용` 동작이 필요합니다.
- 적용 시 기존 계획·분석 승인·외부 전송 승인이 무효화됩니다.
- 취소 후 도착한 다른 `scan_id` 결과는 UI가 무시합니다.

## `AnalysisRequest`

| 필드 | 규칙 |
|---|---|
| `job_id` | 실행별 고유 ID |
| `plan_id`, `approval_id` | 필수. 계획 승인 없이 request를 만들 수 없음 |
| `video_id`, `video_path` | ID는 파일 경로·크기·수정시각 hash, path는 분석 worker 내부에서만 사용 |
| `purpose` | 사용자 목적과 필요한 추가 답변을 합친 문자열 |
| `duration_seconds` | FFprobe로 확인한 양수 길이 |
| `mode` | `sample`, `local`, `external` |
| `model_id` | 실제 모드에서 필수 |
| `sampling_strategy` | `uniform`, `motion_peak`, `temporal_sequence`; 승인 뒤 변경 시 재승인 |
| `observation_guidance` | 목적을 관찰 가능한 자세·방향·시간 비교 기준으로 바꾼 문장 배열 |
| `range_start_sec`, `range_end_sec` | `0 <= start < end <= duration` |
| `sample_timestamps_sec` | 모두 승인 범위 안이며 `max_frames` 이하 |
| `transfer_approval_id` | 외부 모드에서만 필수인 별도 전송 승인 |

외부 request 예시:

```json
{
  "job_id": "external-001",
  "plan_id": "plan-a1b2c3",
  "approval_id": "approval-analysis",
  "transfer_approval_id": "approval-transfer",
  "video_id": "video-hash",
  "purpose": "출입문 앞 장시간 체류 후보 찾기",
  "duration_seconds": 60.0,
  "mode": "external",
  "model_id": "vision-model",
  "range_start_sec": 10.0,
  "range_end_sec": 40.0,
  "sample_timestamps_sec": [11.25, 13.75]
}
```

## 분석 승인과 외부 전송 승인

| 승인 | 의미 | 무효화 조건 |
|---|---|---|
| 분석 승인 | 표시된 목적·모드·모델·시간·샘플링·한도로 분석 실행 허용 | 영상, 목적, 모드, 모델, 공급자 endpoint, 범위, 한도 변경 |
| 외부 전송 승인 | 표시된 공급자 origin에 목적 텍스트와 특정 시각 JPEG 전송 허용 | 위 변경 전부. 새 계획마다 다시 승인 |

분석 승인만으로 외부 전송할 수 없습니다. 로컬 모드에는 외부 전송 승인이 존재하지 않습니다.

API 키는 이 계약의 데이터 필드가 아닙니다. UI 입력 키는 현재 프로세스 메모리에서 인증 헤더를 구성할 때만 사용하고, `AppSettings`에는 비밀값이 아니라 환경 변수 이름(`OPENAI_API_KEY`)만 저장합니다. 키 입력·교체는 전송할 목적·프레임·공급자 범위를 바꾸지 않으므로 분석 승인을 대신하지 않으며, 키가 없으면 승인 완료 후에도 실행 버튼을 활성화하지 않습니다.

## `TransferManifest`

```json
{
  "provider_id": "configured-provider",
  "endpoint_origin": "https://provider.example",
  "sends_purpose_text": true,
  "frame_timestamps_sec": [11.25, 13.75],
  "sends_pdf_content": false
}
```

UI는 실행 전에 위 내용을 사람이 읽을 수 있게 표시합니다. 현재 원본 전체, PDF, 라벨 JSON은 전송 대상이 아닙니다.

## `AnalysisJob`

```json
{
  "job_id": "local-001",
  "plan_id": "plan-a1b2c3",
  "status": "completed",
  "progress": 100,
  "video_id": "video-hash",
  "mode": "local",
  "model_id": "explicit-vision-model",
  "is_sample": false,
  "origin": "real_model",
  "purpose": "출입문 앞 장시간 체류 후보 찾기",
  "range_start_sec": 10.0,
  "range_end_sec": 40.0,
  "sample_timestamps_sec": [11.25, 13.75],
  "elapsed_seconds": 12.4,
  "requested_frame_count": 2,
  "analyzed_frame_count": 2,
  "model_execution": {
    "provider_id": "ollama-local",
    "context_window_tokens": 16384,
    "max_output_tokens": 800,
    "temperature": 0.0,
    "provider_request_count": 1,
    "completed_request_count": 1,
    "input_frame_count": 2,
    "input_frame_dimensions": ["960x540", "960x540"],
    "input_frame_ids": ["video-hash:11250", "video-hash:13750"],
    "input_frame_timestamps_sec": [11.25, 13.75],
    "provider_image_count": 1,
    "provider_image_dimensions": ["960x270"],
    "input_representation": "temporal_storyboard",
    "prompt_version": "candidate-recall-v10-prior-context-separated",
    "batch_candidate_counts": [1]
  },
  "segments": [
    {
      "video_id": "video-hash",
      "start_sec": 10.5,
      "end_sec": 12.0,
      "summary": "출입문 주변에 사람이 보이는 후보 장면",
      "evidence": ["11.250초 프레임에서 사람 형태가 관찰됨"],
      "source_frame_ids": ["video-hash:11250"],
      "source_timestamps_sec": [11.25],
      "uncertainty": "샘플 간 움직임은 원본에서 추가 확인 필요"
    }
  ],
  "error": null
}
```

`origin` 의미:

- `sample`: 규칙 기반 샘플, 실제 AI 분석 아님
- `test_double`: 테스트 가짜 응답, 실제 모델 검증 아님
- `real_model`: 실제 공급자 adapter의 응답이 계약 검증을 통과함
- `none`: 모델 응답으로 인정할 결과가 없는 실패·취소

`completed`에서 후보 배열이 비어 있으면 “분석한 희소 프레임에서 후보 없음”이며 행동 부재 확정이 아닙니다. `partial`은 한 개 이상 배치 응답은 검증됐지만 전체 프레임을 완료하지 못한 상태로 `error`와 분석 프레임 수가 필수입니다. 실패 job도 `error`가 필수이고 후보를 샘플로 대체하지 않습니다. 취소 결과는 저장하지 않습니다. `progress=null`은 측정 불가능한 단계이며 UI는 숫자 대신 단계와 경과 시간을 표시합니다.

## 프레임 계약

`ExtractedFrame`은 다음을 제공합니다.

| 필드 | 의미 |
|---|---|
| `frame_id` | `video_id:decoded_pts_ms` |
| `path` | 앱 사용자 캐시 안의 JPEG 경로 |
| `requested_timestamp_sec` | 계획이 요청한 원본 상대 시각 |
| `source_timestamp_sec` | FFmpeg `showinfo` decoded PTS를 원본 시작 기준으로 변환한 시각 |
| `timestamp_accuracy` | 현재 `decoded_pts` |
| `mime_type`, `sha256` | 전송/무결성 정보 |
| `width`, `height` | 실제 모델에 전달된 추출 JPEG 해상도 |

프레임 추출은 원본을 수정하지 않으며 전체 영상을 메모리에 올리지 않습니다.

## 상태와 이벤트

| 상태/이벤트 | UI 처리 |
|---|---|
| `needs_clarification` | 질문·이유를 표시하고 답변 전 계획 생성 중단 |
| `plan_ready` | 계획 표시, 실행 버튼 비활성, 분석 승인만 허용 |
| `analysis_approved` | 외부 모드의 분석 계획만 승인됨. 전송·실행은 아직 불가 |
| `approved` | 필요한 승인이 모두 완료되어 실행 가능 |
| `running` | 입력 잠금, cancel 활성 |
| 로컬 움직임 탐색 | 분석과 별도의 백그라운드 FFmpeg 작업. 불확정 진행률과 경과 시간 표시 |
| 움직임 제안 적용 | 분석 범위·샘플링 변경, 기존 승인 무효화, 자동 VLM 실행 없음 |
| progress 숫자 | 실제로 셀 수 있는 작업 단위만 progress bar 표시 |
| progress `None` | 불확정 progress 표시 |
| `canceled` | active job 해제, 뒤늦은 progress/result 무시 |
| `failed` | 안전한 오류 표시, 샘플 fallback 없음 |
| `partial` | 완료/요청 프레임 수와 원인을 표시하고 검증된 후보만 표시 |
| `expansion_proposed` | 이유만 표시. 새 범위·계획·승인 전 실행 없음 |
| 결과 후보 선택 | 저장된 확인용 범위·관찰 시각을 수정하지 않고, 플레이어만 확인용 범위 시작의 최대 1초 전으로 이동한 뒤 계속 재생 |

## VLM 응답 형식

최상위는 `{"segments": [...]}`만 허용하며 공급자 응답 하나의 후보는 최대 5개입니다. 각 segment는 `start_sec`, `end_sec`, `summary`, `evidence`, `source_frame_ids`, `uncertainty`를 정확히 포함해야 합니다. 앱은 `source_frame_ids`에서 `source_timestamps_sec`를 계산합니다. `start_sec~end_sec`는 정밀한 행동 경계가 아니라 근거 프레임을 포함하는 확인용 재생 범위입니다. 승인 범위, 시간 순서, 실제 입력 frame ID와 근거 시각 포함 여부를 검증합니다. 승인 범위 밖·역전·알 수 없는 근거 ID는 `ModelResponseError`로 거부하고, 아래의 공개된 확인용 범위 정책만 예외로 적용합니다. 여러 배치의 후보는 근거 없이 하나로 합치지 않습니다.

모델이 제시한 `start_sec~end_sec`가 승인 범위를 벗어나거나 역전되면 거부합니다. 범위 자체는 승인 안에 있지만 인용 근거 시각을 포함하지 못하면 두 경우를 구분합니다. 모델에 노출한 0.001초 단위 표시 시각과 정확한 decoded PTS의 차이가 0.001초 이하이면 정확한 PTS를 포함하도록 경계만 최소로 확장하고 반올림 보정 사실을 `uncertainty`에 남깁니다. 그보다 큰 불일치는 근거 프레임 앞뒤 1초의 확인용 범위로 대체하고 원래 값과 대체 사실을 기록합니다. 이는 행동 경계를 보정하는 것이 아니라 원본 확인 링크가 실제 근거를 포함하도록 만드는 정책입니다.

`evidence`는 화면에서 관찰한 자세·신체 부위·물체·변화를 설명하는 문자열 배열입니다. 실제 입력 frame ID는 `source_frame_ids`에만 둡니다. 모델이 `evidence` 항목에 frame ID 자체만 복사하면 관찰 근거가 없는 응답이므로 해당 배치의 형식 오류로 처리합니다.

배치별 후보는 임의로 합치지 않습니다. 다음 조건을 모두 만족하는 연속 후보만 중복 표시로 간주해 하나의 `Segment`로 묶습니다.

- `summary`를 공백·문장부호 없이 비교했을 때 동일
- 확인용 재생 범위가 서로 겹침
- 실제 근거 frame 시각 사이의 최소 간격이 1초 이하
- 같은 `video_id`

묶은 결과는 시작·종료 범위의 합집합, 중복 제거한 모든 관찰 근거, 모든 `source_frame_ids`와 `source_timestamps_sec`를 유지합니다. `uncertainty`에는 몇 개 후보를 묶었는지 명시합니다. 요약이 다르거나 근거 시각이 떨어진 후보는 합치지 않습니다.

## 움직임 후보 기반 명시 시각 계획

`AnalysisIntent.sample_timestamps_sec`는 움직임 전처리 결과를 사용자가 적용한 뒤 Agent에 전달하는 선택적 명시 시각입니다. 비어 있으면 기존 균등 샘플링을 사용합니다. 값이 있으면 다음을 검증하고 계획 fingerprint 및 외부 전송 manifest에도 동일하게 포함합니다.

- 원본 초 단위이며 시간순·중복 없음
- 승인 범위 안이며 `AnalysisLimits.max_frames` 이하
- 움직임 후보 최대 3개, 전체 최대 12프레임
- 후보 1개는 최대 변화 시각 중심 0.25초 간격 12개
- 후보 2~3개는 각 최대 변화 시각 중심 0.25초 간격 4개씩
- 반복·미세 프로필 후보 1개는 구간 전체에 12개, 2~3개는 각 구간 전체에 4개씩 시간순 분산
- 로컬 `temporal_sequence`의 원본 프레임은 기본적으로 최대 4개씩 최대 2×2 행 우선 비교 보드 한 장으로 공급자에 전달하되, 결과 근거는 원래 frame ID와 decoded PTS를 사용
- 손가락·버튼·초인종·누르기·접촉 등 작은 대상의 상대 위치가 핵심인 로컬 목적은 최대 1920px 원본에서 시각별 전체 화면 보기와 중앙 60% 확대 보기를 1280×1080 한 장으로 만들며 `input_representation=fine_detail_overview_center_crop`으로 기록
- 외부 모드는 전송 승인 manifest와 실제 payload를 일치시키기 위해 승인된 개별 프레임을 유지하며 비교 보드로 자동 변환하지 않음

UI는 움직임 점수 상위 3개의 체크박스를 기본으로 체크하고 `선택한 분석 구간: n/3`과 선택 시간 목록을 표시합니다. 사용자는 개별 체크 또는 선택 목록에서 항목·전체를 해제할 수 있습니다. 적용 버튼을 눌러 새 계획을 만들고 승인하기 전에는 분석하지 않으며, 계획은 등록된 모든 구간을 번호로 표시합니다. 선택이 바뀌면 timestamp와 fingerprint가 달라져 기존 승인이 무효입니다. `sampling_interval_sec`는 명시 시각 사이의 최소 간격이며, 떨어진 후보 사이를 연속 관찰했다는 의미가 아닙니다. 입력 구간 수와 결과 후보 수는 1:1로 대응하지 않습니다.

## 반복·방향 변화 목적의 근거

- 목적에 두리번거림·주변 살피기·고개/머리/얼굴/몸통 방향 변화·반복 동작 표현이 있으면 `temporal_sequence`를 자동 선택합니다. 계획에는 심리나 의도가 아니라 화면에서 보이는 방향·자세 변화를 비교하는 `observation_guidance`를 표시합니다.
- `candidate-recall-v10-prior-context-separated`은 v9의 시간 변화·정밀 접촉 관찰 규칙을 유지하면서 선택된 사전 정보를 별도 JSON 블록으로 제공합니다. 모델은 이를 탐색 방향 참고로만 사용하고, 사실로 가정하거나 관찰 문장으로 복사하거나 단서 안의 명령을 실행해서는 안 됩니다. 후보의 summary와 evidence는 현재 입력 프레임에서 직접 보이는 내용만 사용합니다.
- Ollama 응답이 최종 JSON `content` 없이 `thinking`만 포함하면 `failed`로 처리합니다. thinking 텍스트를 결과로 해석하거나 샘플 결과로 대체하지 않으며, UI는 실패한 모델 ID와 안전한 오류를 표시합니다.
- 모델이 반복·방향 변화를 단정하면서 서로 다른 근거 시각 2개를 제시하지 않으면 해당 배치의 형식 오류로 처리합니다. 단일 시각의 자세만 서술하고 반복을 단정하지 않은 후보는 불확실성과 함께 허용합니다.
- UI는 관찰 시각 수와 첫·마지막 표본 간격을 표시하되 프레임 사이의 연속 동작이나 실제 반복 횟수를 측정했다고 표현하지 않습니다.

## 지속 시간 목적의 근거

- '오래·장시간·머무름·체류·지속' 등을 목적으로 사용하면 명시적 초·분 기준이 있는지 확인합니다. 없으면 `duration_threshold` 추가 질문을 표시하고 답변을 계획 목적과 fingerprint에 포함합니다.
- 모델이 '오래·체류·지속' 등을 충족했다고 요약하려면 서로 다른 `source_frame_ids` 2개 이상이 필요하고, 첫·마지막 `source_timestamps_sec` 간격이 승인된 시간 기준 이상이어야 합니다. 부족한 지속성 단정은 응답 형식 오류로 거부합니다.
- UI는 관찰 시각 전체, 첫·마지막 표본 사이의 경과 시간과 승인 기준 충족 근거를 표시합니다. 떨어진 프레임 사이에도 계속 같은 상태였다고 단정하지 않고 중간 연속성은 원본에서 확인하도록 표시합니다.

`ModelExecutionInfo.input_frame_*`는 분석 근거가 되는 원본 프레임을, `provider_image_*`는 HTTP 공급자에 실제 전달한 이미지 수와 크기를 기록합니다. `input_representation`은 `individual_frames`, `temporal_storyboard`, `fine_detail_overview_center_crop` 중 하나입니다. `prompt_version`은 실제 전송된 후보 선별 지침 버전이고, `batch_candidate_counts`는 형식 검증을 통과한 각 완료 배치의 후보 수입니다. 후자는 정확도·확률·행동 점수가 아니며, 최종 `segments` 수와 다를 수 있습니다. 인접 중복 후보가 정리되기 전의 수이기 때문입니다. 이전 저장 결과와의 호환을 위해 새 공급자 이미지 필드는 원본 입력 필드를 기본값으로 사용합니다.

`Analyzer` 프로토콜은 유지됩니다.

```python
def analyze(request, cancel_event, on_progress) -> AnalysisJob: ...
```

## 검토 기록 (사람 판단)

```json
{
  "review_id": "review-a1b2c3",
  "job_id": "job-1",
  "video_id": "video-hash",
  "case_id": "case-a1b2c3",
  "started_at": "2026-10-10T22:00:00+09:00",
  "completed_at": null,
  "total_review_seconds": 0.0,
  "reviews": [{
    "job_id": "job-1",
    "segment_index": 0,
    "judgement": "relevant",
    "memo": "원본 98초에 손가락이 버튼에 닿아 보임. 누름 여부 미확인",
    "tags": ["손 접촉", "재확인"],
    "bookmarked": true,
    "reviewed_at": "2026-10-10T22:01:00+09:00",
    "review_seconds": 0.0
  }]
}
```

- `judgement`는 `unreviewed`, `relevant`, `needs_check`, `excluded`입니다. 사람이 분석 목적과의 관련성을 판단한 값이며 모델 출력, 정답 라벨, 범죄 성립·의도 판단이 아닙니다.
- `segment_index`는 같은 `job_id`의 저장된 `AnalysisJob.segments` 순서입니다. 결과 파일은 실행 후 바뀌지 않으므로 순서로 후보를 식별하고, 불러올 때 `validate_against(job)`로 작업·영상·범위를 확인합니다.
- 저장 위치는 사용자 데이터 폴더의 `reviews/<job_id>.json`입니다. `results/<job_id>.json`의 모델 결과는 수정하지 않습니다.
- 검토 판단·메모·태그는 `AnalysisRequest`, 모델 프롬프트, `TransferManifest`에 넣지 않습니다.
- 메모는 최대 4000자, 태그는 1~50자·최대 20개·중복 불가입니다. 시각은 ISO 8601 문자열입니다.
- `total_review_seconds`는 후보가 있는 결과를 검토 패널에 띄워 두고 앱 창이 활성일 때만 늘어나는 사람 확인 시간입니다. `review_seconds`는 그 후보를 선택해 둔 시간입니다. 다시 열면 저장된 값에 이어서 더합니다.
- 시간은 [기록 저장]과 [분석 완료]를 누를 때만 파일에 기록합니다. 보기만 하고 버튼 없이 닫으면 그 사이 시간은 저장되지 않습니다. `review_seconds`는 기록을 저장한 후보에만 남고, 저장한 기록이 없는 후보를 본 시간은 `total_review_seconds`에만 포함됩니다.
- `completed_at`은 마지막으로 [분석 완료]를 누른 시각입니다. 완료 후에도 기록을 수정할 수 있고 시간 측정도 계속됩니다.
- 사람 확인 시간은 AI 처리 시간(`AnalysisJob.elapsed_seconds`)과 분리해 저장합니다. 발표·평가용 지표이므로 앱 화면과 결과 요약에는 표시하지 않습니다.

## 영상 간 인물 후보 비교 보고서

이 형식은 `contracts.py`의 이벤트 분석 결과를 바꾸지 않는 보조 저장 계약이며 `identity/comparison.py`에 정의합니다.

```json
{
  "report_id": "person-compare-a1b2c3",
  "case_id": "case-a1b2c3",
  "model_id": "qwen3-vl:8b-instruct",
  "origin": "real_model",
  "candidates": [{
    "candidate_id": "job-1:0",
    "video_id": "video-hash-a",
    "video_name": "영상 1",
    "job_id": "job-1",
    "segment_index": 0,
    "start_sec": 10.0,
    "end_sec": 14.0,
    "summary": "사람이 물체를 들고 이동하는 장면 후보",
    "source_frame_ids": ["video-hash-a:11000", "video-hash-a:13000"],
    "source_timestamps_sec": [11.0, 13.0]
  }],
  "comparisons": [{
    "left_candidate_id": "job-1:0",
    "right_candidate_id": "job-2:1",
    "decision": "inconclusive",
    "observable_similarities": ["어두운 상의와 밝은 신발이 관찰됨"],
    "observable_differences": [],
    "limitations": ["얼굴이 작고 카메라 각도와 조명이 다름"],
    "appearance_similarity_score": 58,
    "evidence_quality": "limited",
    "left_frame_ids": ["video-hash-a:11000", "video-hash-a:13000"],
    "right_frame_ids": ["video-hash-b:9000", "video-hash-b:12000"]
  }],
  "attempted_pair_count": 1,
  "completed_pair_count": 1,
  "elapsed_seconds": 8.4,
  "error": null
}
```

- 한 실행은 서로 다른 영상에서 고른 후보를 하나씩만 받으며 가능한 모든 영상 쌍을 비교합니다. 영상 수는 고정하지 않지만 쌍 수는 `n(n-1)/2`로 늘어나므로 실행 전 UI에 표시합니다.
- `decision`은 `similar_candidate`, `different_candidate`, `inconclusive` 중 하나입니다. 어떤 값도 신원 확인, 동일 범죄자 확정, 범죄 성립, 의도 또는 법적 책임 판단이 아닙니다.
- `appearance_similarity_score`는 0~100 정수인 외형 유사도 검토 참고값입니다. 학습 데이터로 보정된 신원 일치 확률이나 정확도 지표가 아니며, `evidence_quality`의 `good`, `limited`, `poor`와 한계를 항상 함께 표시합니다.
- 모델은 복장·신발·소지품·보이는 비민감 외형을 비교할 수 있지만 인종·민족·성별 같은 민감 속성을 추정하지 않습니다. 중심 인물이 모호하거나 품질·각도·가림·시간차가 크면 `inconclusive`를 사용합니다.
- `limitations`는 비어 있을 수 없습니다. 저장 보고서에는 원본 영상 경로나 추출 프레임 경로를 넣지 않고 후보·프레임 ID와 원본 시각만 보존합니다.
- UI의 A/B 사진은 실행 중 추출한 전체 근거 프레임을 캐시에서 읽어 표시할 뿐 보고서에 이미지나 경로를 넣지 않습니다. 자동 인물 검출·잘라내기 결과로 표현하지 않습니다.
- `origin`은 `real_model`, `test_double`, `none`으로 실제 모델 비교, 자동 테스트, 유효 비교 없음 상태를 구분합니다. 보고서는 사용자 데이터 폴더의 `person_comparisons/<report_id>.json`에 결과·검토 기록과 별도로 저장합니다.
- 이 기능은 최신 `origin=real_model` 이벤트 후보만 UI 선택지로 노출합니다. 시연용·테스트 결과를 실제 사람 비교처럼 제시하지 않습니다.

## PDF 검색 계약

입력:

```json
{"text": "관련 안전 지침", "top_k": 5}
```

검색 hit:

```json
{
  "text": "근거 본문",
  "document_id": "doc-1",
  "document_name": "안전 지침.pdf",
  "page": 3,
  "material_version": "2026-10-01",
  "score": 0.87,
  "embedding": {"model_id": "embed-model", "model_version": "1.0", "dimensions": 768}
}
```

결과 요약 ⑤의 법령 칸은 아직 이 검색 계약을 쓰지 않습니다. 사건과 관계없이 영상 자료를 다룰 때 참고할 공통 규정 7개(수사준칙 제3·5·42조, 디지털 증거 규칙 제5·9조, 개인정보 보호법 제3조, 경찰수사규칙 제2조)의 제목·출처·국가법령정보센터 링크만 `knowledge/data/common_rules.json`에 두고 표시합니다. 본문은 넣지 않으며, 목록은 팀 시연 자료(원문 확인일 2026-10-07)에서 옮겼고 이 저장소에서 원문과 다시 대조하지 않았습니다. 사건별 참고 법령은 관련 영상과 검색 자료를 확정한 뒤 이 계약으로 연결합니다.

상태는 `ready`, `no_results`, `unavailable`, `failed`입니다. 자료나 구현이 없을 때는 hit 없이 `unavailable`과 `PDF 지식 자료 없음/검색기 미연결`을 반환합니다. 실제 문서가 없는 상태에서 가짜 본문·법령·페이지를 반환하지 않습니다.

## 변경 규칙

필드·상태·시간 기준·진입점 변경은 두 담당자가 합의합니다. 계약 구현, 이 문서, 테스트, UI 연결을 같은 PR에서 변경합니다. 정답 라벨·분류 경로를 `AnalysisRequest` 또는 모델 prompt에 추가하지 않습니다.
