# 연동 모션 스케줄 제어 시스템 설계서 (Schedule Motion Control Design)

## 1. 개요 및 목적
- **목적**: 설정된 시각(컴퓨터 로컬 현지 시각) 기준 연동 모션 자동 연속 재생 및 지정 시각/유지시간 도달 시 자동 감속 정지 기능 구현.
- **시간 기준**: 호스트 컴퓨터 현지 시각 (NTP 와이파이 시간 동기화 및 오프라인 RTC 지원).

## 2. 데이터 구조 및 저장 (Schedule Data & Store)
- **저장 파일**: `motion_automation_store.py` 연동 `schedule_store.json` (프로젝트 단위 독립 저장).
- **데이터 스키마**:
  - `schedule_id`: 스케줄 고유 식별자 (GUID)
  - `schedule_name`: 스케줄 명칭
  - `start_time`: 동작 시작 시각 (HH:MM:SS)
  - `stop_time` / `duration_sec`: 동작 정지 시각 또는 유효 동작 시간(초)
  - `repeat_days`: 반복 요일 (매일 / 월~일 선택 / 1회)
  - `motion_config`: `motion_file_id`, `mapping_file_id`, `repeat_mode`, `target_cycles`
  - `enabled`: 활성화 여부 (boolean)

## 3. 아키텍처 및 별도 노드 분리 (`motion_schedule_node`)
- **시스템 역할 분담**:
  1. `motion_schedule_node` (신규 독립 ROS 2 노드):
     - 스케줄 JSON 파일 읽기 및 백그라운드 1초 주기 시각 감시
     - `datetime.now().astimezone()` 기반 호스트 로컬 시각 계산
     - 시작/정지 시각 도달 시 ROS 2 명령 발행
  2. `bridge_node` (웹 브릿지):
     - 스케줄 CRUD REST API 제공 (`POST /api/schedule/save`, `GET /api/schedule/list`, `DELETE /api/schedule/{id}`)
     - 웹 UI ↔ 스케줄 노드 상태 중계
  3. `coordination_node` / `motion_run_manager`:
     - 스케줄 명령 수신 후 연동 연속 재생 시작 및 감속 정지 수행

## 4. 제어 동작 시퀀스 (Operation Sequence)
- **시작 트리거**: `start_time` 도달 ➔ `group_motion_start` / `automation_start` 호출 ➔ 연동 모션 연속 재생 자동 시작
- **정지 트리거**: `stop_time` 또는 동작 유지시간 완료 ➔ `group_motion_stop` / `motion_run_stop` 호출 ➔ UI 정지 버튼 클릭과 동일한 감속 정지
- **수동 개입 처리**: 수동 정지 버튼 클릭 시 해당 스케줄 실행 세션 중단 및 다음 스케줄 대기 상태 전환

## 5. 검증 계획 (Validation Strategy)
- `코드 검증`: `motion_schedule_node` 신규 생성 및 REST API 인터페이스 설계 검증
- `실행 검증`: 시각 비교 백그라운드 스케줄러 및 연동 시작/정지 명령어 발행 검증
- `실물 미검증`: 와이파이 재연결 시 시각 동기화 반영 및 실제 모터 연동 스케줄 정지 타임아웃 검증
