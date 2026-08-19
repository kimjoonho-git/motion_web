# 연동 모션 스케줄 제어 시스템 설계서

## Schedule Motion Control Design — Simplified Version

## 1. 목적

설정된 로컬 시각에 기존 시스템의 **연속모션 시작 기능과 정지 기능을 자동 호출**하는 스케줄 기능을 구현한다.

`motion_schedule_node`는 새로운 모션 제어 로직을 구현하지 않는다.

핵심 개념은 다음과 같다.

```text
시작 시각 도달
    ↓
기존 UI "연속모션 시작" 버튼과 동일한 기능 호출

종료 시각 도달
    ↓
기존 UI "정지" 버튼과 동일한 기능 호출
```

즉 Scheduler는 모션 제어기가 아니라 **시간 기반 Trigger 역할만 담당**한다.

---

# 2. 핵심 설계 원칙

## 2.1 기존 모션 기능 재사용

Schedule 전용 Motion Start/Stop 로직을 새로 만들지 않는다.

사용자가 UI에서 실행하는 기존 기능을 그대로 사용한다.

```text
사용자 연속모션 버튼
        │
        ▼
기존 Motion Start 경로


Schedule Start Trigger
        │
        ▼
동일한 Motion Start 경로
```

정지도 동일하다.

```text
사용자 정지 버튼
        │
        ├──────────────┐
        │              │
Schedule Stop Trigger  │
        │              │
        └──────┬───────┘
               ▼
       기존 Motion Stop 경로
               │
               ▼
            감속 정지
```

---

# 3. 구현 범위

`motion_schedule_node`의 책임은 다음으로 제한한다.

```text
1. Schedule 저장/조회/삭제
2. 현재 컴퓨터 로컬 시각 확인
3. Schedule 시작 시각 감시
4. 시작 시각 도달 시 기존 연속모션 Start 호출
5. Schedule 종료 시각 감시
6. 종료 시각 또는 Duration 도달 시 기존 Stop 호출
7. 동일 Schedule Start/Stop 중복 호출 방지
8. 현재 Schedule 상태 제공
```

다음 기능은 Scheduler에서 구현하지 않는다.

```text
- 별도의 Safety 판단 로직
- Motor 상태 검사
- Servo 상태 검사
- Motion Ready 검사
- Mapping 유효성 검사
- Coordination 상태 검사
- Schedule 전용 Motion Control
- Schedule 전용 감속 정지
- 기존 Motion 오류 처리 로직 복제
```

필요한 검사와 처리는 기존 Motion 시스템의 Start/Stop 경로에 맡긴다.

---

# 4. 시스템 구조

```text
                  Web UI
                     │
                     ▼
                bridge_node
                     │
              Schedule CRUD
                     │
                     ▼
           motion_schedule_node
              │            │
              │            ▼
              │      ScheduleStore
              │            │
              │            ▼
              │    schedule_store.json
              │
              │ Start / Stop Trigger
              ▼
        기존 Motion 실행 경로
              │
              ▼
       coordination_node
              │
              ▼
       motion_run_manager
              │
              ▼
         Motion Control
```

---

# 5. 각 컴포넌트 책임

## 5.1 Web UI

사용자가 Schedule을 관리한다.

주요 기능:

```text
Schedule 생성
Schedule 수정
Schedule 삭제
Schedule 활성화/비활성화
Schedule 목록 조회
Schedule 실행 상태 표시
```

Schedule 설정 예:

```text
Schedule Name
Start Time
Stop Mode
Stop Time
Duration
Repeat Type
Repeat Days
Motion File
Mapping File
Enabled
```

---

## 5.2 bridge_node

`bridge_node`는 Web UI와 `motion_schedule_node` 사이의 인터페이스 역할만 담당한다.

담당:

```text
REST API
WebSocket 상태 전달
ROS Schedule 요청 전달
```

담당하지 않음:

```text
Schedule 시간 계산
Schedule Timer 실행
Schedule 실행 여부 판단
Schedule JSON 직접 수정
Motion Schedule 실행 로직
```

예상 REST API:

```text
GET    /api/schedule/list

POST   /api/schedule/save

DELETE /api/schedule/{schedule_id}

POST   /api/schedule/{schedule_id}/enable

POST   /api/schedule/{schedule_id}/disable

GET    /api/schedule/status
```

---

# 6. motion_schedule_node

신규 독립 ROS 2 Node로 구현한다.

예:

```text
motion_schedule_node
```

이 Node의 목적은 단순하다.

> 현재 시간을 확인하고 Schedule 시간이 되면 기존 Start 또는 Stop 기능을 호출한다.

주요 기능:

```text
ScheduleStore 관리
Schedule CRUD 처리
1초 주기 시간 확인
Start Time Trigger
Stop Time Trigger
Duration Trigger
중복 Trigger 방지
Schedule 상태 제공
```

---

# 7. ScheduleStore

Schedule 저장은 별도 클래스로 관리한다.

```text
motion_schedule_node
        │
        ▼
   ScheduleStore
        │
        ▼
schedule_store.json
```

권장 파일:

```text
motion_schedule_node.py
schedule_store.py
schedule_models.py
schedule_engine.py
```

---

# 8. Store Ownership

`motion_schedule_node`만 `schedule_store.json`을 직접 읽고 수정한다.

다음과 같이 여러 프로세스가 동시에 파일을 수정하지 않는다.

```text
금지

bridge_node ──────────┐
                      ├── schedule_store.json
schedule_node ────────┘
```

권장:

```text
bridge_node
     │
     │ ROS Request
     ▼
motion_schedule_node
     │
     ▼
ScheduleStore
     │
     ▼
schedule_store.json
```

---

# 9. 프로젝트별 Schedule 저장

Schedule은 프로젝트 단위로 독립 저장한다.

개념적으로:

```text
Project A
 └── schedule_store.json

Project B
 └── schedule_store.json

Project C
 └── schedule_store.json
```

현재 활성 프로젝트에 해당하는 ScheduleStore를 사용한다.

프로젝트 변경 시 해당 프로젝트의 Schedule 목록을 다시 로드한다.

---

# 10. Schedule 데이터 구조

권장 Schema:

```json
{
    "schedule_id": "GUID",
    "schedule_name": "Morning Motion",

    "start_time": "10:00:00",

    "stop_mode": "time",
    "stop_time": "18:00:00",
    "duration_sec": null,

    "repeat_type": "weekly",

    "repeat_days": [
        "MON",
        "TUE",
        "WED",
        "THU",
        "FRI"
    ],

    "motion_config": {
        "motion_file_id": "motion-id",
        "mapping_file_id": "mapping-id",
        "repeat_mode": "continuous",
        "target_cycles": null
    },

    "enabled": true
}
```

---

# 11. Schedule ID

각 Schedule은 고유 ID를 가진다.

```text
schedule_id = GUID
```

예:

```text
3f921af4-59e1-4cf8-a762-65af2dd40d42
```

Schedule 수정 시 기존 `schedule_id`를 유지한다.

---

# 12. 반복 방식

`repeat_type`은 다음 값을 지원한다.

```text
once
daily
weekly
```

## once

특정 날짜에 한 번 실행한다.

```json
{
    "repeat_type": "once",
    "run_date": "2026-08-20"
}
```

## daily

매일 동일한 시각에 실행한다.

```json
{
    "repeat_type": "daily"
}
```

## weekly

선택된 요일에 실행한다.

```json
{
    "repeat_type": "weekly",
    "repeat_days": [
        "MON",
        "WED",
        "FRI"
    ]
}
```

---

# 13. 종료 방식

Schedule은 두 가지 종료 방식을 지원한다.

```text
time
duration
```

## 13.1 지정 시각 정지

```json
{
    "stop_mode": "time",
    "start_time": "10:00:00",
    "stop_time": "18:00:00",
    "duration_sec": null
}
```

동작:

```text
10:00
 ↓
연속모션 Start

18:00
 ↓
기존 Stop
```

---

## 13.2 Duration 정지

```json
{
    "stop_mode": "duration",
    "start_time": "10:00:00",
    "stop_time": null,
    "duration_sec": 3600
}
```

동작:

```text
10:00
 ↓
연속모션 Start
 ↓
3600초
 ↓
기존 Stop
```

`stop_time`과 `duration_sec`를 동시에 사용하지 않는다.

---

# 14. 시간 기준

Schedule 시간은 호스트 컴퓨터의 Local Time을 사용한다.

```python
datetime.now().astimezone()
```

구조:

```text
RTC
 │
 ▼
Linux System Clock
 │
 ├── NTP
 │
 ▼
Local Time
 │
 ▼
motion_schedule_node
```

Scheduler가 NTP 또는 RTC를 직접 관리하지 않는다.

운영체제에서 관리되는 시스템 시간을 사용한다.

---

# 15. Schedule 감시 주기

`motion_schedule_node`는 기본적으로 1초 주기로 현재 시간을 확인한다.

```text
Timer
 ↓
1 second
 ↓
Schedule Check
```

개념:

```python
def timer_callback():

    now = local_now()

    check_start_schedule(now)
    check_stop_schedule(now)
```

---

# 16. 시간 Trigger 판단

다음 방식은 사용하지 않는다.

```python
if now == schedule_time:
    start()
```

정확한 시각을 놓칠 가능성이 있기 때문이다.

다음 방식으로 판단한다.

```text
previous_check < schedule_time <= current_time
```

개념:

```python
if previous_check < scheduled_datetime <= now:
    trigger()
```

예:

```text
previous_check = 09:59:59.5
scheduled      = 10:00:00
current        = 10:00:00.6

→ Start Trigger
```

---

# 17. Start Trigger

Schedule 시작 시각이 도달하면 기존 **연속모션 시작 버튼과 동일한 기능**을 호출한다.

```text
Schedule Start Time
        │
        ▼
motion_schedule_node
        │
        ▼
기존 Continuous Motion Start
        │
        ▼
coordination_node
        │
        ▼
motion_run_manager
```

중요:

Schedule 전용 Motion Start 기능을 새로 구현하지 않는다.

---

# 18. Motion 설정

Schedule에는 기존 연속모션 실행에 필요한 설정을 저장한다.

```json
{
    "motion_config": {
        "motion_file_id": "...",
        "mapping_file_id": "...",
        "repeat_mode": "continuous",
        "target_cycles": null
    }
}
```

Schedule Start 시 해당 설정을 기존 연속모션 Start 기능에 전달한다.

---

# 19. 연속모션 실행

기본 Schedule 실행은 연속모션 실행을 목적으로 한다.

예:

```text
10:00 Schedule Trigger
        │
        ▼
Motion A 선택
Mapping A 선택
        │
        ▼
Continuous Motion Start
        │
        ▼
반복 실행
```

종료 조건이 발생할 때까지 기존 연속모션 시스템이 동작한다.

---

# 20. Stop Trigger

Schedule 종료 조건에 도달하면 기존 **정지 버튼과 동일한 기능**을 호출한다.

```text
Schedule Stop Time
        │
        ▼
motion_schedule_node
        │
        ▼
기존 Motion Stop
        │
        ▼
기존 감속 정지
```

Schedule 전용 정지 알고리즘을 구현하지 않는다.

---

# 21. Duration 처리

Duration 방식은 Motion Start Trigger 이후 경과 시간을 기준으로 Stop을 호출한다.

Duration 측정에는 Monotonic Clock을 사용한다.

```python
time.monotonic()
```

예:

```text
Schedule Start
10:00

duration_sec
3600

실행
 ↓
3600초 경과
 ↓
Stop Trigger
```

NTP에 의해 시스템 시간이 변경되더라도 Duration 측정에는 영향을 주지 않도록 한다.

---

# 22. 중복 Start 방지

1초 주기로 Schedule을 확인하기 때문에 동일 Start 명령이 반복 호출되지 않도록 한다.

간단한 Trigger Key를 사용한다.

예:

```text
schedule_id:
schedule-001

date:
2026-08-20

trigger:
start
```

Start Key:

```text
schedule-001:2026-08-20:start
```

이미 처리된 Key라면 다시 Start하지 않는다.

---

# 23. 중복 Stop 방지

Stop도 동일하게 처리한다.

Stop Key:

```text
schedule-001:2026-08-20:stop
```

한 번 Stop Trigger가 발생했다면 동일 Schedule의 동일 실행에 대해 반복 Stop 요청을 보내지 않는다.

---

# 24. 최소 Runtime 상태

복잡한 Schedule State Machine은 초기 버전에서 구현하지 않는다.

필요한 최소 상태만 유지한다.

예:

```text
last_start_key
last_stop_key

active_schedule_id
active_start_time
active_monotonic_start
```

필요하면 UI 표시를 위해 간단한 상태를 추가한다.

```text
WAITING
RUNNING
STOPPED
ERROR
```

---

# 25. 수동 정지

Schedule로 실행된 Motion도 사용자가 기존 UI 정지 버튼을 누를 수 있다.

```text
Schedule Start
      │
      ▼
Motion Running
      │
      ├──── Schedule Stop
      │
      └──── User Stop
                │
                ▼
          기존 Stop 기능
```

사용자가 수동 정지하면 기존 Motion 시스템이 정지한다.

Scheduler가 별도의 Safety 또는 Recovery 동작을 수행하지 않는다.

---

# 26. 수동 정지 후 재실행 방지

현재 Schedule의 Start Trigger가 이미 처리되었다면 사용자가 수동 정지하더라도 같은 Start Trigger를 다시 실행하지 않는다.

예:

```text
10:00 Schedule Start

Start Key:
schedule-001:2026-08-20:start

10:05 User Stop
```

Scheduler는 10:05 이후:

```text
schedule-001:2026-08-20:start
```

를 다시 호출하지 않는다.

다음 Schedule 실행 시각까지 대기한다.

---

# 27. Safety 처리 원칙

`motion_schedule_node`에서 별도의 Safety Preflight 로직을 구현하지 않는다.

Schedule은 기존 연속모션 버튼과 동일한 Start 기능을 호출한다.

따라서:

```text
Schedule
   │
   ▼
기존 Motion Start
   │
   ▼
기존 시스템의 Safety / Control 정책
```

기존 Motion 시스템에서 Start가 거부되거나 실패하면 Scheduler가 이를 우회하지 않는다.

Scheduler의 목적은 기존 버튼 기능을 **시간에 맞춰 자동 호출하는 것**이다.

---

# 28. 기존 Motion 실행 중 처리

Scheduler가 별도로 기존 Motion을 검사하거나 강제 종료하는 로직은 초기 버전에서 구현하지 않는다.

Start Trigger가 발생하면 기존 Start 기능을 호출한다.

이후 성공/실패 판단은 기존 Motion 시스템의 응답을 사용한다.

Scheduler에서 새로운 충돌 제어 정책을 만들지 않는다.

---

# 29. NTP 시간 변경

시스템 시간이 NTP 동기화로 약간 변경될 수 있다.

예:

```text
09:59:59
   ↓
NTP 보정
   ↓
10:00:02
```

Schedule Trigger를 놓치지 않도록 `previous_check`와 `current_time` 사이에 Schedule 시각이 존재하는지 확인한다.

필요하면 간단한 Grace Period를 사용할 수 있다.

예:

```text
trigger_grace_sec = 30
```

오래전에 지나간 Schedule을 자동 실행하지 않는다.

---

# 30. 재부팅 및 Node 재시작

초기 버전에서는 재부팅 이후 이미 오래 지난 Schedule을 자동 복구 실행하지 않는다.

예:

```text
Schedule Start = 10:00

PC Start = 12:00
```

이 경우 10:00 Schedule을 자동 실행하지 않는다.

다음 정상 Schedule 시각을 기다린다.

Scheduler 시작 직전의 매우 짧은 시간 차이에 대해서만 Grace Period를 적용할 수 있다.

---

# 31. Schedule 상태

UI에 필요한 최소 상태를 제공한다.

예:

```json
{
    "scheduler_running": true,
    "active_schedule_id": "schedule-001",
    "last_trigger": "start",
    "last_trigger_at": "2026-08-20T10:00:00+09:00",
    "last_result": "success"
}
```

복잡한 Execution History 시스템은 초기 구현 범위에서 제외한다.

---

# 32. 권장 코드 구조

```text
motion_schedule/
│
├── motion_schedule_node.py
├── schedule_store.py
├── schedule_models.py
└── schedule_engine.py
```

## motion_schedule_node.py

담당:

```text
ROS Node
ROS Service
ROS Publisher/Client
Timer
ScheduleEngine 연결
기존 Motion Start/Stop 연결
```

## schedule_store.py

담당:

```text
schedule_store.json 읽기
Schedule 저장
Schedule 수정
Schedule 삭제
프로젝트별 Schedule 관리
```

## schedule_models.py

담당:

```text
Schedule 데이터 모델
Schedule Validation
repeat_type
stop_mode
motion_config
```

## schedule_engine.py

담당:

```text
현재 시간 확인
Schedule 시간 계산
Start Trigger 판단
Stop Trigger 판단
Duration 판단
중복 Trigger 방지
```

---

# 33. 권장 내부 인터페이스

구현 시 책임이 명확하도록 다음과 같은 형태를 권장한다.

```python
class ScheduleStore:

    def list(self):
        ...

    def get(self, schedule_id):
        ...

    def save(self, schedule):
        ...

    def delete(self, schedule_id):
        ...
```

Schedule Engine:

```python
class ScheduleEngine:

    def tick(self, now):
        ...

    def check_start(self, schedule, now):
        ...

    def check_stop(self, schedule, now):
        ...

    def mark_start_triggered(self, key):
        ...

    def mark_stop_triggered(self, key):
        ...
```

Node에서 기존 Motion 기능 연결:

```python
def trigger_motion_start(schedule):
    # 기존 연속모션 시작 기능 호출
    pass


def trigger_motion_stop(schedule):
    # 기존 정지 버튼 기능 호출
    pass
```

---

# 34. 중요한 구현 규칙

AI 또는 개발자가 구현할 때 다음 규칙을 우선한다.

### RULE-01

`bridge_node`에 Schedule Timer 로직을 추가하지 않는다.

### RULE-02

Schedule 시간 감시는 `motion_schedule_node`에서 수행한다.

### RULE-03

Schedule Start는 기존 UI의 **연속모션 시작 기능과 동일한 실행 경로**를 사용한다.

### RULE-04

Schedule Stop은 기존 UI의 **정지 버튼과 동일한 실행 경로**를 사용한다.

### RULE-05

Schedule 전용 Motion Start/Stop 제어 로직을 만들지 않는다.

### RULE-06

Scheduler에서 기존 Safety 로직을 복제하지 않는다.

### RULE-07

Scheduler에서 Motor 상태 검사 로직을 새로 만들지 않는다.

### RULE-08

`bridge_node`에서 `schedule_store.json`을 직접 수정하지 않는다.

### RULE-09

ScheduleStore의 Owner는 `motion_schedule_node` 하나로 제한한다.

### RULE-10

동일 Schedule Start/Stop Trigger는 한 번만 호출한다.

### RULE-11

Schedule Start 이후 수동 Stop이 발생해도 동일 Start Trigger를 다시 실행하지 않는다.

### RULE-12

Duration 측정은 `time.monotonic()`을 사용한다.

### RULE-13

Schedule 시각 판단은 Host Local Time을 사용한다.

### RULE-14

기존 Motion Control 구조를 최대한 변경하지 않는다.

---

# 35. 구현 우선순위

## Phase 1 — Schedule 데이터

```text
Schedule Model
ScheduleStore
schedule_store.json
프로젝트별 저장
CRUD
```

## Phase 2 — Schedule Node

```text
motion_schedule_node 생성
1초 Timer
Local Time 확인
Schedule Load
Start Time 판단
중복 Trigger 방지
```

## Phase 3 — Motion Start 연결

```text
Schedule Start
      ↓
기존 연속모션 Start 기능
```

실제 UI 연속모션 버튼과 동일한 결과가 나오는지 확인한다.

## Phase 4 — Motion Stop 연결

```text
Schedule Stop
      ↓
기존 Stop 기능
      ↓
기존 감속 정지
```

실제 UI 정지 버튼과 동일한 결과가 나오는지 확인한다.

## Phase 5 — Duration

```text
Start
 ↓
time.monotonic()
 ↓
duration_sec
 ↓
Stop
```

## Phase 6 — Web UI

```text
Schedule 목록
Schedule 생성
Schedule 수정
Schedule 삭제
Enable / Disable
현재 상태 표시
```

---

# 36. 테스트 항목

## Schedule CRUD

```text
Schedule 생성
Schedule 수정
Schedule 삭제
Schedule Enable
Schedule Disable
프로젝트 변경
프로젝트별 저장 확인
```

## Start

```text
Start Time 도달
연속모션 자동 시작
Start 중복 호출 없음
daily 반복
weekly 반복
once 실행
```

## Stop

```text
Stop Time 도달
기존 감속 정지 실행
Stop 중복 호출 없음
```

## Duration

```text
Start
Duration 경과
Stop
NTP 시간 변경 시 Duration 영향 없음
```

## 수동 조작

```text
Schedule 자동 Start
사용자 수동 Stop
Motion 정지
동일 Schedule 자동 재시작 없음
```

## 시간

```text
Local Time 확인
Timer 지연
NTP 시간 보정
Grace Period
PC 재부팅
Node 재시작
```

---

# 37. 초기 버전에서 구현하지 않을 기능

다음 기능은 요구사항이 발생하기 전까지 구현하지 않는다.

```text
복잡한 Safety Preflight
Motor 상태 사전 검사
Schedule Priority
Schedule Queue
Schedule Replace
Schedule 간 Arbitration
자동 Recovery
자동 Resume
복잡한 Execution State Machine
Schedule 전용 Motion Control
Schedule 전용 Stop 알고리즘
Holiday Calendar
복잡한 Timezone 관리
```

기능을 미리 확장하지 않고 Schedule Trigger라는 본래 목적에 집중한다.

---

# 38. 최종 동작

## 시작

```text
현재 시간
   │
   ▼
Schedule Start Time 도달?
   │
   ├── NO → 대기
   │
   └── YES
         │
         ▼
   이미 Trigger 했는가?
         │
         ├── YES → 아무 작업 없음
         │
         └── NO
               │
               ▼
       기존 연속모션 Start 호출
               │
               ▼
        Start Trigger 기록
```

## 정지

```text
Stop Time 또는 Duration 도달
             │
             ▼
      이미 Stop 했는가?
             │
        ┌────┴────┐
       YES        NO
        │          │
        │          ▼
        │    기존 Stop 호출
        │          │
        │          ▼
        │    Stop Trigger 기록
        │
        ▼
      대기
```

---

# 39. 최종 아키텍처

```text
                       Web UI
                          │
                          │ Schedule CRUD
                          ▼
                     bridge_node
                          │
                          ▼
                motion_schedule_node
                    │           │
                    │           ▼
                    │     ScheduleStore
                    │           │
                    │           ▼
                    │   schedule_store.json
                    │
                    ▼
               ScheduleEngine
                    │
          ┌─────────┴─────────┐
          │                   │
      Start Time          Stop Time /
        도달              Duration 도달
          │                   │
          ▼                   ▼
 기존 연속모션 Start      기존 Stop
          │                   │
          └─────────┬─────────┘
                    ▼
             기존 Motion System
                    │
                    ▼
             coordination_node
                    │
                    ▼
             motion_run_manager
                    │
                    ▼
               Motion Control
```

---

# 40. 최종 정의

`motion_schedule_node`는 **모션 제어 노드가 아니라 시간 기반 자동 Trigger Node**로 정의한다.

핵심 기능은 다음 두 가지이다.

```text
Schedule Start Time
        ↓
"연속모션 시작 버튼 클릭"과 동일한 기능


Schedule Stop Time / Duration
        ↓
"정지 버튼 클릭"과 동일한 기능
```

Scheduler는 기존 Motion 시스템을 대체하지 않는다.

Scheduler는 Safety, Motor Control, Coordination 등의 기존 기능을 다시 구현하지 않는다.

**기존에 사용자가 정상적으로 사용하는 Start/Stop 기능을 지정된 시간에 자동 호출하는 것이 Schedule 시스템의 핵심 목적이다.**
