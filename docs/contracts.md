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

모든 시간은 원본 시작 기준 초입니다. Qt UI 밀리초는 UI 경계에서만 변환합니다.

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
  "model_id": "explicit-vision-model",
  "range_start_sec": 10.0,
  "range_end_sec": 40.0,
  "sampling_interval_sec": 2.5,
  "sample_timestamps_sec": [11.25, 13.75],
  "limits": {
    "max_runtime_seconds": 30.0,
    "max_reanalysis_attempts": 0,
    "max_frames": 12,
    "max_provider_attempts": 1
  }
}
```

샘플 모드는 실제 프레임을 추출하지 않으므로 timestamp 배열이 비어 있습니다. 로컬/외부 모드는 최대 `max_frames` 안에서 승인 범위의 timestamp를 만듭니다.

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
  "segments": [
    {
      "video_id": "video-hash",
      "start_sec": 12.5,
      "end_sec": 15.0,
      "summary": "출입문 주변에 사람이 보이는 후보 장면",
      "evidence": ["12.500초 프레임에서 사람 형태가 관찰됨"],
      "source_frame_ids": ["video-hash:12500"],
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

실패 job은 `error`가 필수이고 후보를 샘플로 대체하지 않습니다. `progress=null`은 측정 불가능한 단계이며 UI는 숫자 대신 불확정 상태로 표시합니다.

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

프레임 추출은 원본을 수정하지 않으며 전체 영상을 메모리에 올리지 않습니다.

## 상태와 이벤트

| 상태/이벤트 | UI 처리 |
|---|---|
| `needs_clarification` | 질문·이유를 표시하고 답변 전 계획 생성 중단 |
| `plan_ready` | 계획 표시, 실행 버튼 비활성, 분석 승인만 허용 |
| `analysis_approved` | 외부 모드의 분석 계획만 승인됨. 전송·실행은 아직 불가 |
| `approved` | 필요한 승인이 모두 완료되어 실행 가능 |
| `running` | 입력 잠금, cancel 활성 |
| progress 숫자 | 실제로 셀 수 있는 작업 단위만 progress bar 표시 |
| progress `None` | 불확정 progress 표시 |
| `canceled` | active job 해제, 뒤늦은 progress/result 무시 |
| `failed` | 안전한 오류 표시, 샘플 fallback 없음 |
| `expansion_proposed` | 이유만 표시. 새 범위·계획·승인 전 실행 없음 |

## VLM 응답 형식

최상위는 `{"segments": [...]}`만 허용합니다. 각 segment는 `start_sec`, `end_sec`, `summary`, `evidence`, `source_frame_ids`, `uncertainty`를 정확히 포함해야 합니다. 구간 범위와 입력 frame ID를 검증하며 잘못된 값은 보정하지 않고 `ModelResponseError`로 거부합니다.

`Analyzer` 프로토콜은 유지됩니다.

```python
def analyze(request, cancel_event, on_progress) -> AnalysisJob: ...
```

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

상태는 `ready`, `no_results`, `unavailable`, `failed`입니다. 자료나 구현이 없을 때는 hit 없이 `unavailable`과 `PDF 지식 자료 없음/검색기 미연결`을 반환합니다. 실제 문서가 없는 상태에서 가짜 본문·법령·페이지를 반환하지 않습니다.

## 변경 규칙

필드·상태·시간 기준·진입점 변경은 두 담당자가 합의합니다. 계약 구현, 이 문서, 테스트, UI 연결을 같은 PR에서 변경합니다. 정답 라벨·분류 경로를 `AnalysisRequest` 또는 모델 prompt에 추가하지 않습니다.
