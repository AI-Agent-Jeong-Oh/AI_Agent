# 다음 작업과 완료 기준

## 승재 구현 완료 범위

- FFprobe 메타데이터와 FFmpeg 원본 timestamp 프레임 추출
- 추가 질문·계획 fingerprint·분석/전송 승인·실행 상한 상태 관리
- 샘플/로컬/외부 분석기 분리와 자동 fallback 금지
- Ollama native 및 OpenAI-compatible vision HTTP 요청 구성
- 모델 JSON·구간·입력 frame ID·불확실성 검증
- 취소 subprocess 정리와 늦은 결과 무시
- 사용자 설정·결과·marker 기반 캐시 저장
- PDF 검색 계약과 unavailable 기본 구현
- PyInstaller 개발용 arm64 `.app` 설정·빌드·자동 종료 smoke 실행

## 승재가 돌아와서 확인할 작업

1. 실제 화면 수동 확인
   - 재생 영상 프레임, 오디오, 버튼 배치, 모달 오류를 직접 확인합니다.
   - 완료 기준: 아래 수동 절차를 실제 화면에서 수행하고 사용 영상 codec과 결과를 기록합니다.
2. 실제 로컬 VLM 선택·설치 후 검증
   - 모델 카드와 런타임 문서에서 이미지 입력 지원, 라이선스, M5 24GB 메모리 요구량을 확인합니다.
   - 모델을 자동 다운로드하지 말고 팀 합의 후 설치합니다.
   - 완료 기준: 실제 모델 결과의 `origin=real_model`, 모델 ID, 처리 시간, 메모리, 실패 사례를 기록합니다.
3. 실제 공급자 연동 여부 결정
   - 비용·개인정보·보관정책을 검토한 뒤 endpoint와 모델을 선택합니다.
   - 완료 기준: 별도 승인 UI에 표시된 내용과 실제 request payload가 일치하는지 사용자 입회하에 확인합니다.
4. 배포 정책
   - 지원할 최저 macOS에서 다시 빌드하고 인증서 서명·공증 여부를 별도 결정합니다.
   - 완료 기준: 새 사용자 계정에서 `.app`, FFmpeg 발견, 사용자 데이터 경로를 확인합니다.

## 팀원이 바로 시작할 작업

### UI 디자인·접근성

- 주 수정 파일: `src/video_agent/ui/main_window.py`
- 유지할 연결 지점: `AgentSession`, `AnalysisPlan`, `AnalysisWorker`, `AnalysisJob`, `ResultOrigin`
- 해야 할 일:
  - 현재 기능 순서와 승인 분리를 유지하면서 layout을 컴포넌트로 나눕니다.
  - 키보드 tab 순서, VoiceOver 이름/설명, 작은 화면, 긴 근거 표시를 개선합니다.
  - `sample`, `test_double`, `real_model` banner가 시각·텍스트 모두 구분되는지 확인합니다.
- 완료 기준: 승인 전 실행 불가, 입력 변경 후 재승인, 외부 전송 별도 승인 테스트를 유지하고 수동 접근성 결과를 기록합니다.

### VS9/VL9 실물 조사

- 새 도구 권장 위치: `tools/inspect_dataset.py`
- 파일이 실제 제공된 뒤에만 MP4/JSON 수, 파일명 대응, duration·회전·codec·라벨 시간 필드를 조사합니다.
- `FFprobeVideoProbe`를 재사용하고 정답 JSON을 모델 입력 또는 prompt에 넣지 않습니다.
- 완료 기준: 데이터 file key·checksum·확인 사실·예외·미확인을 문서화합니다.

### PDF 전처리·검색

- 구현 위치: `src/video_agent/knowledge/`와 `tools/build_knowledge.py`
- 기준 계약: `knowledge/contracts.py`
- hit는 본문·문서 ID·문서명·페이지·자료 버전과 선택적 점수·임베딩 정보를 반환합니다.
- 자료/색인이 없으면 현재 `UnavailableKnowledgeSearch` 동작을 유지합니다.
- 완료 기준: 배포 권한이 있는 실제 자료로 페이지 대조를 하고, manifest에 임베딩 모델·버전·차원을 기록합니다.

## 수동 확인 절차

1. `PYTHONPATH=src ./.venv/bin/python -m video_agent`로 실행합니다.
2. 파일 선택 취소, 텍스트 파일, 손상 영상, 한글·공백 경로 영상을 확인합니다.
3. MP4 재생·일시정지·timeline 이동·오디오를 확인합니다.
4. 짧고 모호한 목적에서만 추가 질문이 나타나는지 확인합니다.
5. 계획에 대상·원본 범위·샘플링·시간/프레임/실행 한도가 보이는지 확인합니다.
6. 승인 전 실행할 수 없고 목적·모드·모델·범위를 바꾸면 승인 버튼부터 다시 거쳐야 하는지 확인합니다.
7. 외부 모드에서 분석 승인만으로 실행되지 않고 전송 공급자·목적지·프레임 시각을 별도 승인해야 하는지 확인합니다. 실제 외부 호출은 승인된 검증 시간에만 수행합니다.
8. 샘플 실행 중 UI 반응, 취소, 중복 실행 차단, 후보 클릭 원본 이동을 확인합니다.
9. 앱 종료 후 `~/Library/Application Support/VideoAnalysisAgent` 결과를 다시 읽을 수 있는지 확인합니다.
10. `dist/VideoAnalysisAgent.app`은 build 성공과 별도로 Finder에서 화면·영상 재생을 확인합니다.

자동 테스트·오프스크린 UI·합성 영상·실제 화면·실제 VLM은 서로 다른 검증 수준입니다. 하나의 성공을 다른 수준의 성공으로 기록하지 않습니다.
