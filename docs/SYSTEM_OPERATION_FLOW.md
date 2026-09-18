# Motion Web 실행·DDS 그룹 연동 흐름

- 문서 기준일 · 2026-08-06
- 구현 상태 · 코드 검증 완료 · 로컬 2프로세스 실행 검증 완료
- 운영 상태 · 내부 구조 개선 소스·빌드·두 실행 서비스 재시작 반영 완료 · 실제 2대 PC·모터 실물 미검증
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

`motion_coordination_node` 내부 책임은 다음과 같이 분리한다.

- `GroupExecution` · 실행 ID·고정 참가자·상태·예약 ACK·제한시간 소유
- `CommandDispatcher` · 일반 DDS 명령 직렬 적용·`STOP_NOW`·시작 취소 전용 안전 처리
- `LocalRuntimeMonitor` · ROS 실행 루프 밖에서 경량 로컬 상태 수집
- `SafetyStopController` · 로컬 우선 즉시 정지·DDS 전파 순서 통일
- `AlarmRegistry` · PC별 `boot_id`·sequence·재시작 전후 알람 순서 관리

## 단독 실행

- `1회 모션` · 기존 동작 유지
- `자동 반복` · 기존 `direct`·`dwell`·`reinitialize` 동작 유지
- 그룹에 참가하지 않은 PC · DDS 그룹 기능과 무관하게 기존 기능 사용
- 활성 DDS 그룹 실행 중 · 충돌하는 로컬 모션 시작 차단
- 프로젝트 변경 운영 규칙 · 그룹 정지 → 전체 프로그램 재시작 → 프로젝트 변경

## 해외 설치 · 시간대 · §6-150

**NTP 는 시간대를 못 고친다.**

NTP 가 맞추는 것은 절대 시각(UTC)뿐이다 · 시간대는 사람이 `timedatectl` 로
정하는 값이라, PC 를 들고 다른 나라에 가서 네트워크에 붙여도 **바뀌지 않는다** ·
시계는 정확한데 현지 시각만 틀린 상태가 되고, 화면에는 아무 표시도 없었다.

    한국에서 만든 09:17 스케줄 → 파리에서 그대로 두면 현지 02:17 에 돈다
    (여름 CEST 기준 · 겨울에는 01:17)

개장 시각에 안 돌고 아무도 없는 새벽에 돈다 · 하루가 지나야 안다.

### 설치할 때 할 일

웹 `터미널` 화면의 **시간대 바꾸기** 칸에서 설치할 곳을 고르면 칠 명령이
만들어진다 · 복사해서 아래 터미널에 붙여넣는다.

```bash
sudo timedatectl set-timezone Europe/Paris    # 화면이 만들어 주는 명령
bash src/motion_web/install.sh                # 위 칸의 다시 빌드 명령
```

두 번째 줄이 필요한 이유 · 돌고 있던 노드가 **기동할 때 읽은 시간대를 그대로
들고 있다** · 리눅스가 시간대를 캐시해서 프로세스마다 다시 읽는 시점이 다르다.

### 지역 이름으로 잡을 것

| 이렇게 | 서머타임 |
|---|---|
| `Europe/Paris` ⭕ | OS 가 알아서 처리한다 · **연 2회 손볼 필요 없다** |
| `Etc/GMT-2` ❌ | 고정 오프셋 · 전환 때마다 사람이 고쳐야 한다 |
| `date -s` 로 시계 직접 돌리기 ❌ | 같은 문제 + NTP 와 싸운다 |

화면이 주는 목록은 지역 이름뿐이라 고르기만 하면 맞다.

### 인터넷이 없는 현장

NTP 가 못 붙으면 시계 자체가 틀어져 있을 수 있다 · 화면의 「시계가 맞춰지지
않았습니다」 경고가 그것이다.

```bash
sudo timedatectl set-ntp false
sudo timedatectl set-time "2026-09-18 10:30:00"
```

나중에 인터넷이 되면 `sudo timedatectl set-ntp true` 로 되돌린다.

### 자동 시간대를 켜지 않는 이유

우분투에 위치 기반 자동 시간대가 있지만 쓰지 않는다.

- 위치 서비스·인터넷 지오로케이션에 기댄다 · 전시장 네트워크에서 자주 틀린다
- 더 나쁜 건 **전시 중에 저절로 바뀔 수 있다**는 것이다 · 스케줄이 예고 없이
  한 시간 튄다

시간대는 설치할 때 한 번 손으로 못 박고, 화면이 늘 보여주는 것이 맞다 ·
스케줄 화면 상단과 터미널 화면 두 곳에서 지금 시간대를 보여준다.

### 확인

| 어디서 | 무엇이 보여야 하나 |
|---|---|
| 웹 `📅 모션 스케줄` 상단 | `🕒 PC 시각: 09:17:00 · Europe/Paris` |
| 웹 `터미널` 화면 | `지금 이 PC · Europe/Paris (UTC+0200)` |
| 터미널 | `timedatectl` 의 `Time zone` 줄 |

연동 그룹이라도 **스케줄 판단은 마스터 한 대만 한다** · 마스터 시간대만 맞으면
스케줄 시각은 어긋나지 않는다 · 다만 로그 시각이 PC 마다 달라지면 나중에 문제를
볼 때 헷갈리므로 전부 맞추는 것을 권한다.

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
max_trigger_sync_uncertainty_ms: 20.0
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
- 최대 추정 불확실성 · 20ms
- 예약 여유 · 500ms
- 예약 수락 완료 한계 · 예약시각 100ms 전
- 준비 응답 제한 · 6초
- 모션 시작 트리거 보고 제한 · 예약시각 후 1초
- 그룹 실행 중 로컬 런타임 상태 수집 · 50ms
- 로컬 런타임 상태 중단 판정 · 500ms
- 유휴→활성 전환 · 즉시 폴링·첫 활성 샘플 최대 500ms 대기
- heartbeat · 0.5초
- 통신 지연 경고 · 1.5초
- 통신 단절 · 3초
- 측정 대상 · 초기위치 이동 소프트웨어 트리거와 모션 시작 소프트웨어 트리거
- 목표 편차 · 각 트리거별 PC 간 20ms 이내

각 참가 PC는 coordinator monotonic 목표를 실행별 offset으로 로컬 monotonic
마감시각으로 변환한다. DDS 측정 불확실성이 20ms를 넘으면 실행을 취소한다. 초기화 또는
모션 시작 트리거가 20ms를 넘으면 전체 `STOP_NOW`와 오류를 공유하고 사용자 확인 전
그룹 재실행을 차단한다. 자동 재시도와 PC별 정지 확인 상태는 사용하지 않는다. 이 값은
모터축의 물리 움직임 측정값이 아니다.

## 정지·오류

- `현재 회차 후 정지` · 실행 중인 로컬 모션만 완료 · dwell/reinitialize 생략 · 다음 `START_AT` 없음
- `전체 즉시 정지` · 요청 PC의 `motion_supervisor` 안전 정지 명령·로컬 motion_run 정지 · 모든 참가 PC에 `STOP_NOW`
- 고정 참가 목록의 어느 PC에서도 두 정지를 요청할 수 있다.
- 1등급 Servo 오류 · 오류축 차단 · 나머지 현재 회차 완료 · 다음 회차 차단
- 2등급 Servo 오류 · 전체 즉시 정지
- 3등급 Servo 오류 · 전체 즉시 정지 · 기존 supervisor의 재시작 전 모터 제어 차단 유지
- 미분류 Servo 오류 · 기존 supervisor 정책에 따라 2등급
- 참가 PC 통신 단절·프로그램 재시작 · 로컬 우선 정지 · 남은 고정 참가 PC에 `STOP_NOW`
- 로컬 Web Bridge 상태 500ms 중단 · 로컬 우선 정지 시도·전체 `STOP_NOW`·그룹 재실행 차단
- 동일 `pc_id`·다른 `boot_id` · `DUPLICATE_PC_ID`·그룹 참가와 실행 차단
- `PREPARE` 참가 목록 불일치 · 전체 시작 전 취소
- 예약 ACK 일부 누락 · 전체 `CANCEL_BEFORE_START`
- `START_AT` 시각 경과 후 ACK 누락 · 전체 `STOP_NOW`
- `motion_started` 보고 누락 · 전체 `STOP_NOW`·그룹 재실행 차단
- DDS 트리거 동기화 불량 · 초기화·시작·다음 회차 차단
- 동시 그룹 시작 · 가장 낮은 `pc_id` 요청 선택 · 패배 세션·ACK·제한시간 초기화
- 초기화 또는 모션 시작 트리거 20ms 초과 · 로컬 우선 `STOP_NOW`·오류 공유·사용자 확인 전 그룹 실행 차단
- 그룹 동기화 오류 · Servo 알람과 분리 · 단독 `1회 모션`·`자동 반복`은 차단하지 않음

## 검증 상태

- 관련 Python 자동 테스트 · 332개 통과·7개 선택 실행 제외
- 독립 DDS 2프로세스 실행 테스트 · 5개 통과
- Web UI Node 테스트 · 그룹 상태 DOM 동작 테스트 포함 38개 통과
- typed DDS 인터페이스 생성·빌드 · 확인됨
- 1~8 PC 상태기계·전체 barrier 시뮬레이션 · 코드 검증 확인됨
- 단독 1회·자동 반복 회귀 테스트 · 확인됨
- 두 로컬 프로세스의 DDS 발견·heartbeat · 실행 검증 확인됨
- 두 로컬 프로세스의 `PREPARE`→`ARMED`→`START_AT`→`CYCLE_READY` 2회차 · 실행 검증 확인됨
- heartbeat 1초·모션 시작 보고 제한 1초 조합의 2회차 실행 · 실행 검증 확인됨
- 서로 다른 회차 완료시간·전체 barrier·회차당 시작 1회 · 실행 검증 확인됨
- 실제 콜백 monotonic 기준 20ms 초과·즉시 정지·오류 공유·재실행 차단 · 로컬 실행 검증 확인됨
- loopback 로컬 API · 실행 검증 확인됨
- 현재 PC의 systemd unit 환경 · `ROS_LOCALHOST_ONLY=0`·`127.0.0.1:8011`·외부 `8010` 미사용 확인됨
- 내부 상태 소유권·명령 직렬화·실행 중 안전 정지 선점·로컬 상태 비동기화·안전 정지 통합 · 코드 검증 확인됨
- 위 내부 구조 개선 코드의 현재 실행 서비스 반영 · 두 서비스 재시작·로컬 API 응답 확인됨
- 현재 PC의 version 1 설정 변환 · `pc_id`·`display_name` 보존·version 2 `enabled: false` 확인됨
- chrony·NTP·고정 시간 기준 PC 의존 제거 · 코드 검증 확인됨
- 현재 PC의 `trigger_sync` API·500ms 예약 여유·5회 DDS 측정 설정 · 실행 검증 확인됨
- 실제 서로 다른 PC 2대 · 실물 미검증
- 실제 모터 초기화·모션 시작 편차 20ms · 실물 미검증

독립 DDS 테스트는 두 실제 DDS 프로세스와 가짜 로컬 Web Bridge를 사용한다. 실제
`motion_run_manager`·모터까지 포함한 결과로 확대하지 않는다.

실제 장비 검증은 [DDS_MULTI_PC_VALIDATION.md](DDS_MULTI_PC_VALIDATION.md)를 따른다.
