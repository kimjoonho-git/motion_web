# Motion Web 실행·DDS 그룹 연동 흐름

- 문서 기준일 · 2026-08-06
- 구현 상태 · 코드 검증 완료 · 로컬 2프로세스 실행 검증 완료
- 운영 상태 · 신규 코드 빌드·세 서비스 재시작 반영 완료 · 실제 2대 PC·모터 실물 미검증
- 범위 · PC 1~8대 · 사용자가 직접 그룹 참가·시작·정지

## 구조

```text
브라우저 :8000
  → Motion Web Bridge (ROS_LOCALHOST_ONLY=1)
      → motion_run_manager → motion_supervisor → motion_system
      ↕ 127.0.0.1:8011 로컬 고수준 어댑터
    motion_coordination_node (ROS_LOCALHOST_ONLY=0, 선택한 DDS Domain)
      ↔ 다른 PC의 motion_coordination_node (typed ROS 2 DDS)
```

- PC 간 전송 · `GroupHeartbeat`, `GroupCommand`, `GroupEvent`, `GroupAlarm`, `GroupTimeSync`
- PC 간 미전송 · 프로젝트 ID·파일명·모션 데이터·모터 목표값
- 모터 제어 · 기존 `motion_system` 단일 통로 유지
- `127.0.0.1:8011` · 같은 PC의 두 프로세스만 연결하는 로컬 API
- 외부 `8010` HTTP·HMAC·페어링 · 제거됨

## 단독 실행

- `1회 모션` · 기존 동작 유지
- `자동 반복` · 기존 `direct`·`dwell`·`reinitialize` 동작 유지
- 그룹에 참가하지 않은 PC · DDS 그룹 기능과 무관하게 기존 기능 사용
- 활성 DDS 그룹 실행 중 · 충돌하는 로컬 모션 시작 차단
- 프로젝트 변경 운영 규칙 · 그룹 정지 → 전체 프로그램 재시작 → 프로젝트 변경

## 그룹 설정

설정 파일은 프로젝트와 분리된 `config/motion_coordination.yaml`이다. 프로젝트
파일 구조와 PC별 프로젝트 선택은 변경하지 않는다. 기존 version 1 설정은 신규
서비스 최초 실행 시 `pc_id`·`display_name`만 보존하고 `enabled: false`인
version 2로 교체한다. 기존 peer·주소·역할·자격증명 참조는 이전하지 않는다.

```yaml
version: 2
pc_id: pc-a
display_name: 무대 왼쪽 PC
enabled: true
group_id: stage-a
dds_domain_id: 21
heartbeat_sec: 0.5
warning_timeout_sec: 1.5
peer_timeout_sec: 3.0
start_lead_sec: 0.5
schedule_ack_margin_sec: 0.1
max_trigger_sync_uncertainty_ms: 5.0
trigger_sync_samples: 5
prepare_timeout_sec: 6.0
trigger_report_timeout_sec: 1.0
```

같은 `group_id`와 `dds_domain_id`를 설정한 PC만 발견된다. 각 PC 사용자가 웹의
`DDS 그룹 연동`에서 직접 `그룹 참가`를 눌러야 한다. 고정 마스터는 없으며
`그룹 모션 시작`을 누른 PC가 해당 실행의 임시 진행 PC가 된다.

- 참가 상태가 `warning` 또는 `offline`인 PC를 자동 제외하지 않는다.
- 참가 PC 전체가 `online`이고 Servo 알람과 그룹 동기화 오류가 없을 때만 시작한다.
- `그룹 나가기`는 `joined: false` heartbeat로 즉시 공유한다.

## 그룹 실행 상태

```text
그룹 모션 시작
  → PREPARE (고정 참가 목록 2~8대)
  → 전체 READY
  → DDS 상대 monotonic 왕복 측정
  → INITIALIZE_AT (500ms 뒤)
  → 각 PC 초기위치 이동
  → 전체 ARMED
  → DDS 상대 monotonic 왕복 재측정
  → START_AT 1회 발행 (500ms 뒤)
  → 각 PC 로컬 모션 정확히 1회
  → 각 PC별 direct/dwell/reinitialize 준비
  → 전체 CYCLE_READY
  → DDS 상대 monotonic 왕복 재측정
  → 다음 START_AT 1회 발행
```

- 그룹에서 로컬 자동 반복은 시작하지 않는다.
- `START_AT` 한 번은 로컬 모션 한 회차와 일대일 대응한다.
- 각 PC의 모션시간과 회차 사이 준비시간은 서로 달라도 된다.
- 공통 대기시간·공통 반복주기·반복 횟수는 사용하지 않는다.
- 다음 회차는 모든 고정 참가 PC의 `CYCLE_READY` 이후에만 시작한다.
- 사용자가 정지할 때까지 회차가 계속되며 향후 스케줄러는 별도 기능으로 추가한다.

## 트리거·통신 조건

- 시스템 UTC·NTP·chrony · 사용하지 않음
- 고정 시간 기준 PC · 없음
- 실행 기준 · 실행 요청 PC의 해당 실행 monotonic 시간
- 상대시간 측정 · DDS 왕복 5회 · 지연이 작은 측정값 우선
- 최대 추정 불확실성 · 5ms
- 예약 여유 · 500ms
- 예약 수락 완료 한계 · 예약시각 100ms 전
- 준비 응답 제한 · 6초
- 모션 시작 트리거 보고 제한 · 예약시각 후 1초
- heartbeat · 0.5초
- 통신 지연 경고 · 1.5초
- 통신 단절 · 3초
- 측정 대상 · 초기위치 이동 소프트웨어 트리거와 모션 시작 소프트웨어 트리거
- 목표 편차 · 각 트리거별 PC 간 20ms 이내

각 참가 PC는 coordinator monotonic 목표를 실행별 offset으로 로컬 monotonic
마감시각으로 변환한다. DDS 측정 불확실성이 5ms를 넘으면 실행을 취소한다. 20ms를 넘은
초기화 또는 모션 시작 트리거가 20ms를 넘으면 전체 `STOP_NOW` 후 전체 PC 정지를
확인하고 새로운 `execution_id`로 초기위치 이동부터 자동 재시도 1회를 수행한다.
자동 재시도에서도 20ms를 넘으면 다시 `STOP_NOW` 후 단계별 트리거 편차 오류를
공유하고 사용자 확인 전 그룹 재실행을 차단한다. 이 값은 모터축의 물리 움직임
측정값이 아니다.

## 정지·오류

- `현재 회차 후 정지` · 실행 중인 로컬 모션만 완료 · dwell/reinitialize 생략 · 다음 `START_AT` 없음
- `전체 즉시 정지` · 요청 PC의 로컬 motion_run 우선 정지 · 모든 참가 PC에 `STOP_NOW`
- 고정 참가 목록의 어느 PC에서도 두 정지를 요청할 수 있다.
- 1등급 Servo 오류 · 오류축 차단 · 나머지 현재 회차 완료 · 다음 회차 차단
- 2등급 Servo 오류 · 전체 즉시 정지
- 3등급 Servo 오류 · 전체 즉시 정지 · 기존 supervisor의 재시작 전 모터 제어 차단 유지
- 미분류 Servo 오류 · 기존 supervisor 정책에 따라 2등급
- 참가 PC 통신 단절·프로그램 재시작 · 로컬 우선 정지 · 남은 고정 참가 PC에 `STOP_NOW`
- 동일 `pc_id`·다른 `boot_id` · `DUPLICATE_PC_ID`·그룹 참가와 실행 차단
- `PREPARE` 참가 목록 불일치 · 전체 시작 전 취소
- 예약 ACK 일부 누락 · 전체 `CANCEL_BEFORE_START`
- `START_AT` 시각 경과 후 ACK 누락 · 전체 `STOP_NOW`
- `motion_started` 보고 누락 · 전체 `STOP_NOW`·그룹 재실행 차단
- DDS 트리거 동기화 불량 · 초기화·시작·다음 회차 차단
- 동시 그룹 시작 · 가장 낮은 `pc_id` 요청 선택 · 패배 세션·ACK·제한시간 초기화
- 초기화 또는 모션 시작 트리거 첫 20ms 초과 · 로컬 우선 `STOP_NOW`·전체 정지 확인·초기위치부터 자동 재시도 1회
- 자동 재시도 중 두 번째 20ms 초과 · `STOP_NOW`·`GROUP_TRIGGER_SPREAD_EXCEEDED`·사용자 확인 전 그룹 실행 차단
- 그룹 동기화 오류 · Servo 알람과 분리 · 단독 `1회 모션`·`자동 반복`은 차단하지 않음

## 검증 상태

- 관련 Python 표준 자동 테스트 · 443개 통과
- 로컬 API·독립 DDS 2프로세스 실행 테스트 · 8개 통과
- Web UI Node 테스트 · 그룹 상태 DOM 동작 테스트 포함 38개 통과
- typed DDS 인터페이스 생성·빌드 · 확인됨
- 1~8 PC 상태기계·전체 barrier 시뮬레이션 · 코드 검증 확인됨
- 단독 1회·자동 반복 회귀 테스트 · 확인됨
- 두 로컬 프로세스의 DDS 발견·heartbeat · 실행 검증 확인됨
- 두 로컬 프로세스의 `PREPARE`→`ARMED`→`START_AT`→`CYCLE_READY` 2회차 · 실행 검증 확인됨
- 서로 다른 회차 완료시간·전체 barrier·회차당 시작 1회 · 실행 검증 확인됨
- 실제 콜백 monotonic 기준 첫 20ms 초과·즉시 정지·새 실행 자동 재시도 · 로컬 실행 검증 확인됨
- 자동 재시도 중 두 번째 20ms 초과·오류 공유·재실행 차단·사용자 확인 · 로컬 실행 검증 확인됨
- loopback 로컬 API · 실행 검증 확인됨
- 현재 PC의 systemd unit 환경 · `ROS_LOCALHOST_ONLY=0`·`127.0.0.1:8011`·외부 `8010` 미사용 확인됨
- 이번 DDS 참가자 합의·20ms 자동 재시도·오류 차단 신규 코드 · 실행 서비스 반영 확인됨
- 현재 PC의 version 1 설정 변환 · `pc_id`·`display_name` 보존·version 2 `enabled: false` 확인됨
- chrony·NTP·고정 시간 기준 PC 의존 제거 · 코드 검증 확인됨
- 현재 PC의 `trigger_sync` API·500ms 예약 여유·5회 DDS 측정 설정 · 실행 검증 확인됨
- 실제 서로 다른 PC 2대 · 실물 미검증
- 실제 모터 초기화·모션 시작 편차 20ms · 실물 미검증

실제 장비 검증은 [DDS_MULTI_PC_VALIDATION.md](DDS_MULTI_PC_VALIDATION.md)를 따른다.
