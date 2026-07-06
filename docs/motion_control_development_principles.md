# Motion Control Development Principles

이 문서는 `motion_system`을 기반으로 상위 모션 제어기와 웹 UI를 개발하기 위한 구조, 역할, 원칙, 개발 순서를 정리한 문서이다.

## 1. 개발 방향

현재 프로젝트는 `motion_system`을 하위 모터 구동 계층으로 유지하고, 그 위에 상위 제어 계층과 웹 UI 계층을 새로 구성한다.

`motion_system`은 EtherCAT 통신, 서보드라이버 통신, 기본 모터 구동, 모터 피드백 수집을 담당한다. 상위 제어기와 웹 UI는 `motion_system`을 직접 수정하거나 우회하지 않고, `motion_system`이 제공하는 ROS2 토픽과 인터페이스를 통해서만 제어한다.

가장 중요한 방향은 다음과 같다.

- 모터와 EtherCAT 통신은 `motion_system`을 통해서만 수행한다.
- `motion_system`은 하위 기반 계층으로 유지한다.
- 상위 제어 로직은 별도 `motion_control` 계층에서 구현한다.
- 웹 UI는 모터 제어 로직을 갖지 않고, 상태 표시와 사용자 요청 전달만 담당한다.
- `/motor_command`를 최종 발행하는 노드는 `motion_supervisor` 하나로 제한한다.
- 모터는 최대 50축까지 확장 가능하도록 설계한다.
- 상위 제어 계층은 특정 모터 종류에 종속되지 않고, `motion_system`이 제공하는 공통 상태 필드 중심으로 동작한다.

## 2. 전체 패키지 구조

현재 기준 구조는 다음과 같다.

```text
ros2_ws/src
├─ motion_system
│   ├─ lib
│   │  ├─ motor_manager        # 별도 Git 저장소, submodule
│   │  └─ robot_manager        # 별도 Git 저장소, submodule
│   └─ ros2/motion_system_ros2
│      ├─ motion_control_bridge
│      ├─ motion_control_midi
│      ├─ motion_control_msgs
│      ├─ motion_control_robot
│      └─ motion_control_rqt
│
├─ motion_control
│   ├─ motion_state_monitor
│   ├─ motion_supervisor
│   ├─ motion_manual_control
│   ├─ motion_config
│   └─ motion_sequence_executor
│
└─ motion_web
    ├─ web_bridge
    └─ web_ui
```

## 3. 계층별 역할

### 3.1 motion_system

`motion_system`은 하위 모터 구동 계층이다.

주요 역할:

- EtherCAT 통신
- Panasonic MINAS 등 서보드라이버와 실제 통신
- 모터 기본 구동
- 모터 상태 피드백 수집
- `/motor_status` 발행
- `/motor_command` 수신

개발 원칙:

- 상위 제어 로직을 넣지 않는다.
- 웹 UI 로직을 넣지 않는다.
- 모션 시퀀스 로직을 넣지 않는다.
- 필요한 모터 피드백이나 기본 인터페이스가 부족한 경우에만 수정한다.
- `motion_system` 파일을 수정해야 할 경우, 수정 전에 반드시 한 번 더 검토하고 사용자 확인을 받는다.

### 3.2 motion_control

`motion_control`은 `motion_system` 위에 올라가는 상위 제어 계층이다.

주요 역할:

- `/motor_status` 데이터 구독
- 모터 상태 가공
- 소프트웨어 오프셋 적용
- 상태 판단
- 조그 요청 처리
- 동작 테스트 처리
- 초기화 모션 처리
- 자동 모션 실행 처리
- 안전조건 검사
- `/motor_command` 생성

단, `/motor_command` 최종 발행은 `motion_supervisor`만 담당한다.

### 3.3 motion_web

`motion_web`은 웹 UI와 웹 API 계층이다.

주요 역할:

- 웹 화면 제공
- 상태 표시
- 사용자 입력 수집
- 웹 요청을 ROS2 요청으로 변환
- ROS2 상태를 웹 UI에 전달

개발 원칙:

- 웹 UI는 직접 `/motor_command`를 발행하지 않는다.
- 웹 UI는 `motion_system`에 직접 명령하지 않는다.
- 웹 UI는 모션 계산, 안전조건 판단, 직접 명령 생성을 하지 않는다.

## 4. motion_control 세부 구성

### 4.1 motion_state_monitor

`motion_state_monitor`는 모터 상태를 읽고 상위 제어기와 웹 UI가 사용하기 쉬운 상태로 가공하는 노드이다.

입력:

- `/motor_status`

주요 기능:

- 모터별 상태 구독
- position, velocity, torque, current 수집
- raw position, raw velocity, raw torque, raw current 수집
- station alias 수집
- driver_name 수집
- statusword 해석
- errorcode 해석
- 마지막 수신 시간 관리
- 통신 상태 판단
- 웹 UI 표시용 상태 생성
- 상위 제어용 `/motion_state` 발행

주의:

- 직접 `/motor_command`를 발행하지 않는다.
- 상태 가공과 모니터링에 집중한다.

### 4.2 motion_supervisor

`motion_supervisor`는 전체 모션 제어의 중심 노드이다.

주요 역할:

- 전체 시스템 상태 머신 관리
- 현재 제어 모드 관리
- 안전조건 판단
- 명령 허용/차단 판단
- `/motor_command` 최종 발행
- 정지/비상정지/알람 처리

상태 예시:

```text
IDLE
READY
INIT
MANUAL
JOG
TEST
AUTO_RUN
STOPPING
FAULT
```

중요 원칙:

- `/motor_command`를 발행하는 유일한 노드로 둔다.
- 다른 노드들은 `motion_supervisor`에 요청만 보낸다.
- `/motor_status` 또는 `/motion_state`가 일정 시간 이상 갱신되지 않으면 명령을 차단한다.
- fault 또는 alarm 상태에서는 명령을 차단한다.
- 위치/속도/토크/전류 제한을 강제한다.

### 4.3 motion_manual_control

`motion_manual_control`은 조그 모드와 동작 테스트 요청을 담당한다.

주요 기능:

- 축 선택
- 조그 방향 선택
- 조그 속도 설정
- 조그 이동량 제한
- 단일 축 테스트 이동
- 지정 위치 이동 테스트
- dead-man 방식 조그 요청
- 웹 연결 끊김 또는 버튼 release 시 자동 정지 요청

주의:

- 직접 `/motor_command`를 발행하지 않는다.
- `motion_supervisor`에 조그 시작, 조그 정지, 테스트 이동 요청을 보낸다.

### 4.4 motion_config

`motion_config`는 모션 제어에 필요한 설정값을 관리한다.

주요 기능:

- 축 이름 설정
- 축별 사용 여부 설정
- 소프트웨어 원점 오프셋 설정
- 위치 제한값 설정
- 속도 제한값 설정
- 토크 제한값 설정
- 전류 제한값 설정
- 조그 기본 속도 설정
- 테스트 이동 거리 설정
- 초기화 모션 파라미터 설정
- 자동 모션 파라미터 설정
- 설정 파일 저장
- 설정 파일 로드

주의:

- 설정값은 `motion_state_monitor`, `motion_supervisor`, `motion_manual_control`, `motion_sequence_executor`가 사용한다.
- 웹 UI는 설정을 표시하고 수정 요청만 보낸다.

### 4.5 motion_sequence_executor

`motion_sequence_executor`는 초기화 모션과 자동 모션 시퀀스를 관리한다.

주요 기능:

- 초기화 모션 실행
- 자동 모션 실행
- 시퀀스 단계 관리
- 각 단계별 목표 위치/속도/조건 관리
- 단계 완료 조건 판단
- 실패/중단/재시도 처리
- 실행 진행률 관리
- 실행 상태 발행

구성:

- initialization sequence
- motion sequence

주의:

- 직접 `/motor_command`를 발행하지 않는다.
- 실제 모터 명령은 `motion_supervisor`를 통해서만 나간다.

## 5. motion_web 세부 구성

### 5.1 web_bridge

`web_bridge`는 ROS2와 웹 UI 사이의 중계 계층이다.

주요 기능:

- `/motion_state` 구독
- `motion_supervisor` 상태 구독
- 웹 API 제공
- WebSocket 또는 HTTP API 제공
- 웹 요청을 `motion_control` 계층으로 전달
- 상태 데이터를 웹 UI용 JSON 통신 포맷으로 변환

주의:

- 직접 `/motor_command`를 발행하지 않는다.
- `motion_system`에 직접 명령하지 않는다.

### 5.2 web_ui

`web_ui`는 사용자 화면이다.

주요 화면:

- 대시보드
- 모터 상태 모니터링 화면
- 조그 모드 화면
- 동작 테스트 화면
- 모션 설정 화면
- 초기화 모션 실행 화면
- 자동 모션 실행 화면
- 알람/로그 화면

주요 표시 항목:

- controller index
- driver name
- station alias
- statusword
- errorcode
- position
- velocity
- torque
- current
- raw position
- raw velocity
- raw torque
- raw current
- 마지막 수신 시간
- 통신 상태
- 알람 상태

주의:

- 화면과 사용자 입력만 담당한다.
- 모션 계산, 안전조건 판단, 직접 명령 생성은 하지 않는다.

## 6. 데이터 흐름

상태 흐름:

```text
Servo Driver / EtherCAT
    ↓
motion_system
    ↓ /motor_status
motion_state_monitor
    ↓ /motion_state
motion_supervisor / web_bridge
    ↓
web_ui
```

명령 흐름:

```text
web_ui
    ↓
web_bridge
    ↓
motion_manual_control 또는 motion_sequence_executor
    ↓
motion_supervisor
    ↓ /motor_command
motion_system
    ↓
Servo Driver / EtherCAT
```

## 7. 핵심 개발 원칙

1. EtherCAT 및 서보드라이버 직접 통신은 `motion_system`만 담당한다.
2. `motion_system`은 하위 기반 계층으로 유지한다.
3. 상위 제어기는 `motion_system`의 `/motor_status` 데이터를 받아서 가공, 판단, 제어 요청을 수행한다.
4. 웹 UI는 모터와 직접 통신하지 않는다.
5. 웹 UI는 `/motor_command`를 직접 발행하지 않는다.
6. `/motor_command`를 최종 발행하는 노드는 `motion_supervisor` 하나로 제한한다.
7. 조그, 테스트, 초기화, 자동 모션은 모두 `motion_supervisor`의 상태 관리 아래에서 수행한다.
8. 안전조건, 제한값, watchdog, 상태 머신은 웹 UI가 아니라 상위 제어기에서 관리한다.
9. raw 데이터는 보존하고, 가공 데이터는 별도로 관리한다.
10. 소프트웨어 원점 오프셋, 단위 변환, 표시용 값 계산은 `motion_control` 계층에서 처리한다.
11. `/motor_status` 또는 `/motion_state`가 일정 시간 이상 갱신되지 않으면 명령을 차단한다.
12. 조그 동작은 반드시 watchdog/dead-man 구조를 가진다.
13. 위치/속도/토크/전류 제한은 `motion_supervisor`에서 강제한다.
14. 초기화가 완료되지 않으면 자동 모션 실행을 허용하지 않는다.
15. fault 또는 alarm 상태에서는 명령을 차단한다.
16. 모든 명령과 상태 변화는 로그로 남긴다.
17. `motion_system` 파일 수정은 최소화한다.
18. `motion_system` 파일을 수정해야 할 경우, 수정 전에 반드시 별도 검토와 사용자 확인을 진행한다.
19. 모터는 최대 50축까지 확장 가능하도록 자료구조와 UI를 설계한다.
20. 특정 모터 모델에만 의존하는 로직은 `motion_system` 또는 모터별 어댑터 계층에 격리하고, 상위 제어 계층은 가능한 공통 상태 필드를 사용한다.
21. 모터 설정 파일이 없어도 모니터링은 동작해야 한다.
22. 실제 감지된 모터 목록과 사용자가 설정한 모터 목록은 분리해서 관리한다.
23. 실행 중 새 모터가 감지되면 자동 표시하되, 설정에는 사용자가 명시적으로 추가할 때만 저장한다.
24. 실행 중 모터가 사라져도 설정에서 자동 삭제하지 않고 `stale`, `disconnected`, `missing` 상태로 표시한다.
25. 작업 전 실제 Git 저장소 경계와 수정 대상 저장소를 먼저 확인한다.
26. 수정하지 않는 저장소에는 브랜치를 만들지 않는다.
27. `motion_system` 원본 구조와 폴더명을 임의로 변경하지 않는다.
28. `motion_system` 내부 설정 파일을 직접 수정하지 않고, 가능한 외부 실사용 YAML을 `config_file`로 연결한다.

## 8. 모터 감지와 설정 관리 원칙

모터 정보는 실행 중 감지되는 정보와 사용자가 저장한 설정 정보를 분리해서 관리한다.

```text
detected motors
- /motor_status에서 실제 수신된 모터
- 실행 중 계속 갱신
- 파일 저장 대상 아님

configured motors
- YAML 설정 파일에서 로드한 모터
- 사용자가 웹 UI에서 추가/수정/삭제 가능
- 파일 저장 대상
```

초기 1단계에서는 모터 설정 파일이 없어도 동작해야 한다.

```text
motor_config.yaml 없음
→ /motor_status 기반으로 detected motors 생성
→ 임시 이름 자동 부여
→ 웹 UI에서 모니터링 가능
``` 

나중에 `motion_config` 단계에서 설정 파일을 추가한다.

```text
motor_config.yaml 로드
→ configured motors 생성
→ /motor_status 기반 detected motors와 비교
→ 일치/누락/불일치 상태 표시
```

모터 리스트를 저장하는 설정 파일은 YAML만 사용한다. JSON은 HTTP API, WebSocket, ROS2 String 메시지의 상태 전달 포맷으로만 사용하고, 모터 리스트 저장 파일로 사용하지 않는다.

상태 분류:

```text
detected
- 실제 수신 중인 모터

stale
- 최근까지 수신됐지만 일정 시간 이상 갱신되지 않은 모터

disconnected
- 오래 미수신 상태인 모터

configured
- 설정 파일에 존재하는 모터

unconfigured
- 실제 감지됐지만 설정 파일에는 없는 모터

missing
- 설정 파일에는 있지만 실제 감지되지 않은 모터

mismatch
- 설정값과 실제 감지값이 다른 모터
```

실행 중 새 모터가 감지되면 웹 UI에 `unconfigured` 상태로 표시한다. 사용자가 명시적으로 "설정에 추가"를 선택할 때만 설정 파일에 저장한다.

실행 중 모터가 사라지면 바로 삭제하지 않는다. 일정 시간 동안 `stale`로 표시하고, 더 오래 미수신이면 `disconnected` 또는 `missing`으로 표시한다. 설정 삭제는 사용자가 명시적으로 수행해야 한다.

최대 모터 수는 50축을 기준으로 설계한다. 현재 테스트는 2축 Panasonic AC Servo 기준으로 진행하되, UI와 자료구조는 20~30축 이상, 최대 50축까지 확장 가능한 형태를 유지한다.

## 9. Git 저장소와 브랜치 관리 기준

작업 전에는 항상 실제 Git 저장소 경계를 먼저 확인한다. 브랜치는 수정하는 Git 저장소에만 필요하다.

현재 확인된 저장소 경계:

```text
/home/joonho_test/ros2_ws
- 정상 Git 저장소로 사용하지 않는다.

/home/joonho_test/ros2_ws/src/motion_system
- Git 저장소이다.
- 최선일 원본 `main`을 따라가는 기준 저장소이다.
- 원본 구조, 폴더명, 패키지명을 임의로 바꾸지 않는다.

/home/joonho_test/ros2_ws/src/motion_system/lib/motor_manager
- 별도 Git 저장소인 submodule이다.
- 로터리 alias, 전류 측정, EtherCAT raw 데이터처럼 모터 통신 엔진 수정이 필요할 때만 별도 브랜치에서 수정한다.

/home/joonho_test/ros2_ws/src/motion_control
- 현재 독립 Git 저장소가 아니다.
- 여기만 수정하는 경우 `motion_system` 브랜치는 만들 필요가 없다.

/home/joonho_test/ros2_ws/src/motion_web
- 현재 독립 Git 저장소가 아니다.
- 여기만 수정하는 경우 `motion_system` 브랜치는 만들 필요가 없다.
```

브랜치 판단 기준:

```text
motion_system 파일 수정 없음
→ motion_system 브랜치 필요 없음

motion_system/lib/motor_manager 파일 수정 없음
→ motor_manager 브랜치 필요 없음

motion_control 또는 motion_web만 수정
→ motion_system 브랜치 필요 없음
→ 단, 현재 두 폴더는 Git 저장소가 아니므로 별도 백업 또는 저장소화 방침이 필요함

motion_system 내부 launch/config/package/CMake 수정 필요
→ motion_system 브랜치 필요
→ 수정 전 사용자 확인 필요

motor_manager 내부 통신/드라이버 코드 수정 필요
→ motor_manager 브랜치 필요
→ motion_system에는 submodule 포인터 변경이 생길 수 있음
```

`motion_system` 기준 원격:

```text
origin   = https://github.com/kimjoonho-git/motion_system_ros2.git
upstream = https://github.com/SeonilChoi/motion_system.git 또는 redirect된 motion_system_ros2 원본
```

`motor_manager` 기준 원격:

```text
origin/upstream = https://github.com/SeonilChoi/motor_manager.git
```

백업 브랜치는 기능 개발 브랜치가 아니라 복원용 보관 브랜치이다.

```text
motion_system 백업:
backup/pre-main-migration-20260701-145939

motor_manager 백업:
backup/pre-main-migration-20260701-145939
```

## 10. 모터 설정 YAML 관리 기준

`motor_manager_node`는 실행 시 `config_file` 파라미터로 전달된 메인 config YAML 하나를 읽는다. 저장소 안에 YAML 파일이 여러 개 있어도, 한 번 실행될 때 직접 적용되는 메인 config YAML은 하나이다.

```text
저장소에 여러 YAML 보관 가능
→ 실행할 때 `config_file`로 하나 선택
→ 실행 중 여러 메인 YAML을 자동 병합하지 않음
→ YAML 변경 적용은 `motor_manager_node` 재시작 필요
```

최선일 최신 원본 기준 기본 config:

```text
motion_control_bridge/config/example_ethercat_zeroerr.yaml
```

Panasonic MINAS AC Servo 기준 config:

```text
motion_control_bridge/config/example_ethercat_minas.yaml
motion_control_bridge/param/minas.yaml
```

메인 config YAML에는 여러 모터 타입을 함께 넣을 수 있다. 이때 `masters[].slaves[].driver_id`가 `drivers[].id`를 참조하고, 각 `drivers[].type`이 실제 드라이버 타입을 결정한다.

```text
slave axis 0 → driver_id: 0 → drivers id 0 → type: minas
slave axis 1 → driver_id: 1 → drivers id 1 → type: zeroerr
```

`param_file`이 디렉터리이면 `type` 이름을 기준으로 파라미터 YAML을 추가로 읽는다.

```yaml
type: minas
param_file: ../param
```

위 설정은 다음 파일을 읽는다.

```text
../param/minas.yaml
```

프로젝트 운영 기준:

```text
motion_system 내부 예제 YAML은 원본 유지
실사용 모터 설정은 가능하면 motion_system 밖의 외부 YAML로 관리
웹 UI는 외부 실사용 YAML을 읽고 쓴다
motor_manager_node는 같은 외부 YAML을 `config_file`로 받는다
```

예시:

```text
/home/joonho_test/ros2_ws/config/active_motor_config.yaml
```

실행 예시:

```bash
ros2 launch motion_control_bridge motor_manager_node.launch.py \
  config_file:=/home/joonho_test/ros2_ws/config/active_motor_config.yaml
```

웹에서 YAML 저장 후 실제 적용하려면 다음 순서를 따른다.

```text
1. YAML 백업
2. YAML 저장
3. servo on 또는 동작 중 상태 확인
4. 안전 조건 만족 시 motor_manager_node 재시작
5. /motion_control/motor_status 수신 확인
6. 실패 시 이전 YAML로 복구 가능해야 함
```

## 11. Codex 작업 전 확인 규칙

Codex는 이 프로젝트에서 작업을 시작할 때 다음을 먼저 확인한다.

```text
1. 이 문서의 원칙
2. 현재 수정 대상 파일이 어느 Git 저장소에 속하는지
3. motion_system 또는 motor_manager를 수정해야 하는지
4. 외부 YAML 또는 상위 패키지 수정으로 해결 가능한지
5. 사용자 확인 없이 motion_system 원본 구조를 변경하지 않는지
```

단, 모든 대화 턴마다 모든 Markdown 파일을 자동으로 읽는 것은 아니다. 이 문서는 프로젝트 작업 기준 문서이며, 구조 변경, Git 작업, YAML 관리, motion_system 관련 작업 전에는 우선 확인 대상으로 둔다.

### 11.1 답변 방식 규칙

Codex는 이 프로젝트에서 사용자의 질문에 답할 때 다음 원칙을 항상 지킨다. 사용자가 매번 별도로 다시 명시하지 않아도 이 기준을 적용한다.

```text
1. 사용자가 질문한 항목에는 반드시 직접 답한다.
2. 사용자의 질문을 자의적으로 건너뛰지 않는다.
3. 결론을 먼저 말하고, 그다음 이유를 설명한다.
4. 애매한 표현으로 뭉뚱그리지 않는다.
5. "이것", "저것", "그거", "해당 내용"처럼 대상을 흐리는 표현을 줄이고 실제 명사를 사용한다.
6. Git 저장소, 브랜치, 파일, 패키지, YAML 이름은 정확한 이름으로 말한다.
7. `motion_system`, `motor_manager`, `motion_control`, `motion_web`을 섞어서 말하지 않는다.
8. 사용자가 "정확히", "명확히", "요점", "시간순"을 요구하면 그 형식을 우선한다.
9. 답변이 이전 답변과 달라질 경우, 달라진 이유와 새로 확인한 근거를 함께 말한다.
10. 확인하지 않은 내용을 확정처럼 말하지 않는다.
```

예시:

```text
나쁜 답변:
- 브랜치 만들 필요 없습니다.

좋은 답변:
- `motion_system` 저장소에는 새 브랜치가 필요 없습니다.
- `motion_web`은 현재 독립 Git 저장소가 아니므로 브랜치 개념이 없습니다.
```

질문에 대한 직접 답변이 필요한 경우:

```text
사용자 질문:
- "이거 push 된 거야?"

좋은 답변:
- "아니요. `motion_system` 백업 브랜치는 아직 GitHub에 push되지 않았습니다."
- "이유는 GitHub HTTPS 인증 오류입니다."
```

## 12. 추천 개발 단계

### 0단계: 개발 원칙 및 구조 확정

목표:

- 전체 시스템 구조를 확정한다.
- `motion_system`을 하위 모터 구동 계층으로 유지한다.
- `motion_control`과 `motion_web`을 상위 계층으로 새로 만든다.

주요 작업:

- 패키지 구조 결정
- 토픽/서비스/action 역할 결정
- `/motor_command` 발행 주체를 `motion_supervisor` 하나로 제한
- `motion_system` 수정 원칙 확정

결과물:

- 개발 원칙 문서
- 시스템 구조 문서

### 1단계: 모니터링 시스템 구축

목표:

- 모터를 움직이지 않고 상태만 안전하게 확인한다.

구성:

- `motion_state_monitor`
- `web_bridge`
- `web_ui` 모니터링 화면

주요 기능:

- `/motor_status` 구독
- 모터별 상태 표시
- position, velocity, torque, current 표시
- raw position, raw velocity, raw torque, raw current 표시
- station alias 표시
- driver_name 표시
- statusword, errorcode 표시
- 마지막 수신 시간 표시
- 모터 상태 정상/경고/에러 판단

결과물:

- 웹 UI에서 모터 상태를 실시간으로 확인할 수 있다.
- 아직 모터 명령은 보내지 않는다.

### 2단계: motion_supervisor 기본 상태 머신 구축

목표:

- 상위 제어기의 중심 노드를 만든다.
- 모터 명령을 보낼 수 있는 유일한 통로를 만든다.

구성:

- `motion_supervisor`

주요 기능:

- 전체 시스템 상태 관리
- `/motion_state` 구독
- 모터 상태 유효성 검사
- fault/alarm 상태 검사
- `/motor_status` timeout 검사
- `/motor_command` 발행 구조 준비
- 명령 허용/차단 판단

결과물:

- 웹이나 다른 노드가 직접 모터 명령을 보내지 않고 `motion_supervisor`를 거치게 된다.
- 이후 조그/모션 실행을 위한 안전 기반을 마련한다.

### 3단계: 조그 모드 및 동작 테스트 구축

목표:

- 수동으로 모터를 조금씩 움직여 테스트할 수 있게 한다.
- 안전한 수동 제어 구조를 만든다.

구성:

- `motion_manual_control`
- `motion_supervisor` 연동
- `web_ui` 조그/테스트 화면

주요 기능:

- 축 선택
- 조그 방향 선택
- 조그 속도 설정
- 조그 이동량 제한
- 누르고 있을 때만 움직이는 dead-man 방식
- 버튼을 떼면 자동 정지
- 웹 연결 끊김 시 자동 정지
- 단일 축 테스트 이동
- 지정 위치 이동 테스트
- 속도/위치 제한 검사

결과물:

- 웹 UI에서 안전하게 조그할 수 있다.
- 간단한 동작 테스트를 수행할 수 있다.

### 4단계: 모션 설정 시스템 구축

목표:

- 모션 실행에 필요한 설정값을 관리한다.

구성:

- `motion_config`
- `web_ui` 설정 화면

주요 기능:

- 축 이름 설정
- 축별 사용 여부 설정
- 소프트웨어 원점 오프셋 설정
- 위치 제한값 설정
- 속도 제한값 설정
- 토크 제한값 설정
- 전류 제한값 설정
- 조그 기본 속도 설정
- 테스트 이동 거리 설정
- 초기화 모션 파라미터 설정
- 자동 모션 파라미터 설정
- 설정 저장/로드

결과물:

- 코드 수정 없이 웹 또는 설정 파일로 기본 동작 조건을 변경할 수 있다.

### 5단계: 초기화 모션 구축

목표:

- 장비 시작 후 모터를 안전한 기준 상태로 만드는 초기화 절차를 만든다.

구성:

- `motion_sequence_executor`
- `motion_supervisor` 연동
- `web_ui` 초기화 화면

주요 기능:

- 초기화 시작 요청
- 초기화 진행 상태 표시
- 각 축 상태 확인
- 알람/에러 확인
- 현재 위치 확인
- 소프트웨어 오프셋 적용
- 초기 위치 또는 준비 위치 이동
- 초기화 완료 조건 판단
- 실패 시 중단 및 에러 표시

결과물:

- 웹 UI에서 초기화를 실행할 수 있다.
- 초기화 완료 후에만 자동 모션 실행을 허용할 수 있다.

### 6단계: 자동 모션 실행 구축

목표:

- 정의된 모션 시퀀스를 실행할 수 있게 한다.

구성:

- `motion_sequence_executor`
- `motion_supervisor`
- `web_ui` 모션 실행 화면

주요 기능:

- 모션 시퀀스 선택
- 모션 시작
- 일시정지
- 재개
- 정지
- 단계별 진행 상태 표시
- 목표 위치/속도 표시
- 완료 조건 판단
- 실행 중 fault/alarm 감지
- 실행 중 제한값 초과 감지
- 실패 시 안전 정지

결과물:

- 웹 UI에서 실제 모션 시퀀스를 실행할 수 있다.

### 7단계: 알람/로그/이벤트 시스템 구축

목표:

- 운영 중 발생하는 상태 변화를 기록하고 추적한다.

구성:

- `motion_event_logger`
- `web_ui` 로그 화면

주요 기능:

- 명령 로그 기록
- 상태 변화 기록
- fault/alarm 기록
- 사용자 조작 기록
- 모션 시작/종료 기록
- 초기화 성공/실패 기록
- 로그 조회
- 중요 이벤트 웹 UI 표시

결과물:

- 문제 발생 시 원인을 추적할 수 있다.
- 작업 이력을 확인할 수 있다.

### 8단계: 안전 기능 강화

목표:

- 실제 장비 운용에 필요한 보호 기능을 강화한다.

구성:

- `motion_supervisor` 중심

주요 기능:

- `/motor_status` timeout 감지
- `web_bridge` heartbeat 감지
- 조그 watchdog
- 모션 실행 watchdog
- 위치 제한 초과 차단
- 속도 제한 초과 차단
- 전류 제한 초과 차단
- fault 상태 명령 차단
- 초기화 전 자동 모션 차단
- 정지 명령 우선순위 보장

결과물:

- 장비와 작업자를 보호하는 기본 안전 체계를 확보한다.

### 9단계: UI 개선 및 운영 화면 정리

목표:

- 실제 사용자가 보기 좋고 조작하기 쉬운 화면으로 정리한다.

구성:

- `web_ui`

주요 기능:

- 대시보드 화면
- 모터 상태 테이블
- 조그 패널
- 모션 설정 화면
- 초기화 실행 화면
- 자동 모션 실행 화면
- 알람/로그 화면
- 연결 상태 표시
- 권한/잠금 기능

결과물:

- 실제 운용 가능한 웹 기반 모션 제어 UI를 만든다.

### 10단계: 통합 테스트 및 배포 정리

목표:

- 전체 시스템을 안정적으로 실행할 수 있게 한다.

주요 작업:

- launch 파일 정리
- 실행 명령 정리
- 설정 파일 정리
- README 작성
- 테스트 절차 문서화
- Git 브랜치/커밋 정리
- 실제 장비 테스트
- 비상 상황 테스트

결과물:

- 재현 가능한 실행 환경
- 협업자가 받아도 이해 가능한 구조
- 운영 가능한 모션 제어 시스템

## 13. motion_system 수정 규칙

`motion_system`은 기반 패키지이므로 수정 전 반드시 다음을 확인한다.

1. 상위 제어기에서 해결 가능한 문제인가?
2. 단순 설정 변경으로 해결 가능한가?
3. 새 토픽/노드 추가로 해결 가능한가?
4. 정말 `motion_system` 내부 수정이 필요한가?
5. 수정 대상 파일은 무엇인가?
6. 수정 후 기존 기능이 깨질 가능성은 없는가?

수정이 필요하다고 판단되면 다음 절차를 따른다.

```text
1. 수정 필요 이유 설명
2. 수정 대상 파일 목록 제시
3. 예상 변경 내용 설명
4. 사용자 확인
5. 코드 수정
6. 빌드 확인
7. 동작 확인
8. Git 상태 확인
```

사용자 확인 전에는 `motion_system` 파일을 수정하지 않는다.
