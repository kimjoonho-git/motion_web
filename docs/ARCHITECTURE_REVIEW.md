# 전체 코드 구조 검토 · 정리 방향

- 문서 기준일 · 2026-08-21
- 검토 범위 · `src` 내 자체 패키지 9개 · `motion_web/web_ui` 정적 자원 · launch 구성
- 검토 제외 · `src/motion_system` 내부 구현 (보호 대상 · 스캐너 분리 항목만 협의 대상으로 언급)
- 검증 상태 · 코드 검증 완료 · 실행 미검증 · 실물 미검증
- 목적 · 기능 변경 없이 유지보수성 확보 · 신규 기능 추가 비용 축소

## 1. 규모 현황 · 코드 검증

| 항목 | 수치 |
|---|---|
| Python 소스(테스트 제외) | 약 39,600줄 · 자체 패키지 9개 |
| 테스트 | 922건 통과 · 실패 0 (2026-08-22) |
| 프런트엔드 | 32,595줄 (CSS 7,415 · JS 약 24,000 · `index.html` 1,993) |
| 100줄 초과 함수 | 56개 (60줄 초과 126개 / 총 1,463개) · `scripts/code_metrics.py` |
| 최장 파일 | `bridge_node.py` 6,300 · `motion_run_manager.py` 3,692 · `midi_control_node.py` 3,276 · `monitor_node.py` 2,752 · `supervisor_node.py` 2,486 · `coordination_node.py` 2,370 · `project_repository.py` 2,098 |
| 최장 함수 | `_midi_callback` 475줄 · `_build_plan` 421 · `_snapshot` 400 · `MotionWebBridge.__init__` 380 · `_scan_ethercat_slaves` 360 |
| 사설 RPC | `std_msgs/String` + JSON pub/sub 22 · `json.dumps` 137회 · ROS srv 4개 · action 0개 |
| 영속 계층 | 파일 기록 모듈 8개 · atomic write `motion_common/store.py` 단일화(§6-5) |
| 동시성 | `bridge_node` 락 30개 |
| 도구 기반 | lint · ruff 도입 완료(§6-3 · 잔여 56건) · type · CI 설정 없음 |

## 2. 유지 대상 · 구조 양호

- 명령 최종 출력 단일화 · `motion_supervisor` 단독 `/motion_control/motor_command` 발행 · `CommandArbiter` 소유권 중재
- 라우트 분리 완료 · `motion_web_bridge/routes/` 8모듈 · 엔드포인트 107개 · `create_app` 66줄
- `motion_coordination` 모듈 분해 · 노드 외 10모듈 (`trigger_sync`, `group_execution`, `alarm_registry` 등)
- `motion_studio` 세션 분해 · `recording_session`, `playback_session`, `workspace_session`, `project_store`
- DDS 그룹 전용 타입 패키지 · `motion_coordination_interfaces` msg 6종
- 저장소 위생 · `.gitignore` 정합 · `.bak` 추적 0건
- 설계 문서 4건 존재

## 3. 구조 문제 · 우선순위

### 3-1. 신(God) 노드 + 위임 껍데기 · 최우선

- 증상 · 로직은 `Node` 서브클래스 잔류 · 추출 모듈은 역참조 껍데기
- 근거 · `motion_web_bridge/motor_service.py` 111줄 전량이 `self.bridge._call_scan_service(...)` 형태 위임
- 근거 · `motion_studio/ros_gateway.py`가 `studio._lock`, `studio._run_results` 직접 접근
- 결과 · 의존 방향 미역전 · 단위 테스트 시 Node 전체 모킹 필요 · 파일 수만 증가

### 3-2. 사설 요청·응답 RPC 5중 중복

- 패턴 · `request_id` 발급 → String+JSON 발행 → 콜백에서 dict 저장 → 10ms 폴링 대기 → 만료 항목 정리
- 중복 위치 · `bridge_node.py:574-841` (콜백 5 + 대기 5) · `motion_studio/ros_gateway.py` · `motion_web_bridge/motion_studio_bridge.py` · `motion_run_manager.py` · `motion_coordination/local_api.py`
- 부작용 · 스키마 검증 부재 · 만료 주기 불일치(10초/20초) · 동기 폴링이 FastAPI 워커 스레드 점유
- 계약 매핑 부재 · 즉답형 = Service · 장기작업(스캔·초기화·모션 실행) = Action(feedback·cancel) · 상태 = latched Topic

### 3-3. 도메인 로직 중복

- 모션 표 파서 2중 구현 · `_extract_motion_rows` · `_parse_motion_row` · `_expand_pair_rows` · `_column_key` · `_header_has_required`
  - `bridge_node.py:5917-6130` ↔ `motion_run_manager.py:3339-3465`
  - 위험 · 표시용 파서와 실행용 파서 분기 · 화면 값과 실제 모터 목표 불일치 가능
- 값 변환 중복 · `_finite_float` 5곳 · `_optional_int` 4곳 · `_optional_float` 4곳
- 세대 검증 중복 · `_validate_request_generation` 4곳 · `_payload_matches_selected_project` 등 개별 구현
- 원인 · 공용 라이브러리 패키지 부재

### 3-4. 영속 계층 분산

- 프로젝트 디렉터리 직접 기록 모듈 8개 · atomic write 5종 구현
- 다중 프로세스 동시 기록 · `routes/schedule_routes.py:19` `ScheduleStore` 생성 ↔ `motion_schedule_node.py:31` 동일 파일 생성
- 우회 수단 · 노드측 `check_and_reload()` mtime 폴링
- 락 규약 불일치 · `.motor_runtime.lock`, `.motion_coordination.yaml.lock`만 존재 · 나머지 무락

### 3-5. 토픽·파라미터 단일 출처 부재

- 3중 정의 · 노드 기본값 + launch 리터럴 + 상대 노드 기본값
- 의미 불일치 · `motion_run_manager.py:65` 파라미터명 `motor_command_topic` · 값 `/motion_control/motion_run_command`
  - `motion_supervisor` 동명 파라미터는 최종 출력 토픽 · launch 재정의 시 오배선 위험
- 표기 불일치 · `project_services.launch.py` 내 `midi_control` 파라미터만 리터럴 하드코딩 · 타 노드는 `LaunchConfiguration` 사용

### 3-6. 프런트엔드 빌드 부재

- 수동 캐시버스트 토큰 96개 · 20종
- `api.js` 3종 토큰 동시 사용 · 모듈 3중 인스턴스화 · 세대 상태를 `window.__motionProjectGeneration` 전역으로 회피
- 단일 대형 파일 · `styles.css` 7,415 · `motor_config.js` 4,032 · `motion_data.js` 3,036 · `index.html` 1,993
- 개발 잔여물 정적 배포 · `web_ui/static/js/refactor.py` · `/static/js/refactor.py` 노출
- 빌드 산출물 미사용 · `system_routes.py:13-17`이 소스 트리(`src/motion_web/web_ui/static`)가 있으면
  설치본 대신 소스를 서빙 · `update_cache.py`가 만든 해시 토큰은 개발 환경에서 쓰이지 않음
  · `build_and_restart.sh`가 매번 `motion_web_ui` 빌드 캐시를 지우고 다시 만들지만 그 산출물은 서빙되지 않음
  · 7단계(프런트엔드 빌드 도입) 시 서빙 경로 규약도 함께 정해야 함

### 3-7. 하드웨어 프로토콜 코드가 노드 내부

- `monitor_node.py` 혼재 항목 · Dynamixel 직렬 패킷·CRC·Ping(`_write_dynamixel_packet`, `_dynamixel_crc`, `_ping_dynamixel_id`) · EtherCAT CLI 파싱 · SII EEPROM·Alias 레지스터 읽기
- 제약 · 모터 스캔 영구 불변조건상 물리 스캔 필수 · 제거 아닌 스캐너 라이브러리 분리 필요
- 절차 · `src/motion_system` 보호 규정 대상 · 범위 명시 및 지시 후 진행

### 3-8. 신규 기능의 규약 이탈

- 최신 모듈 `motion_schedule`에 기존 규약 미적용 사례 집중
- 하드코딩 절대경로 2건 · `routes/schedule_routes.py:15` · `motion_schedule_node.py:25` · 다중 PC 목표와 상충
- 광범위 예외 55건(`except Exception`) · 신규 모듈 12건 · `except: pass` 포함
- 패키지 경계 역참조 · `web_bridge` → `motion_schedule` 내부 · `web_bridge` → `motion_coordination` 내부 직접 import

## 4. 목표 구조

```text
계층                 책임                          현재 → 목표
────────────────────────────────────────────────────────────────
web_ui              화면·입력                      빌드 도구 도입 · CSS/JS 분할
web_bridge/routes   HTTP 경계                      유지
web_bridge/services 유스케이스 (Node 비의존)        신설 · bridge_node 로직 이관
motion_common       공용 커널 (신규 패키지)         신설
  ├ rpc.py          RequestChannel 단일 구현        5중 중복 흡수
  ├ motion_table.py 모션 표 파서 단일 구현          2중 중복 흡수
  ├ topics.py       토픽·파라미터 단일 정의         코드·launch 공유
  ├ paths.py        workspace/project 경로          하드코딩·중복 제거
  ├ values.py       수치 변환                      11곳 흡수 · 3곳 의도적 유지
  └ store.py        atomic write + 파일락           5종 통합
motion_* 노드        전송·수명주기만                도메인 클래스로 위임
motion_system(C++)  모터 단일 통로                 유지 · 스캐너만 분리 협의
```

핵심 원칙 3가지

- 노드는 전송만 · 도메인은 `rclpy` 비의존 순수 클래스 · 노드 없이 단위 테스트
- 껍데기 위임 금지 · 추출 시 로직 이동 + 의존 역전(도메인이 노드를 모름)
- 계약 단일화 · 즉답 = Service · 장기작업 = Action · 상태 = latched Topic

## 5. 단계별 전환 로드맵

| 단계 | 작업 | 위험 | 검증 |
|---|---|---|---|
| 0 | ruff/flake8 + pytest 워크스페이스 설정 · 함수길이 지표 기록 | 없음 | **완료** · ruff 56건 · `pytest.ini` · `scripts/code_metrics.py` · 선재 실패 0 |
| 1 | `motion_common` 신설 · 순수 함수 이관(파서·값·경로) | 최저 | **완료** · 목표 6모듈 전부 · 911테스트 통과 |
| 2 | `RequestChannel` 단일화 · 5곳 교체 · 토픽명·페이로드 형식 유지 | 낮음 | **완료** · `rpc.ResultStore` 4곳 · 전송 계약 불변 · 실물 미검증 |
| 3 | 토픽 상수 단일화 · `motor_command_topic` 명칭 정정 | 낮음 | **완료** · `topics.py` 27종 · 리터럴 잔여 0 · launch 7개 로드 확인 |
| 4 | `bridge_node` 분해 · 서비스 6개 | 중간 | **진행 중** · 순수 함수 -1,107줄(§6-9) · 상태 동반 이동 미착수 |
| 5 | 영속 계층 통합 · 단일 저장 API + 파일락 · 다중 writer 제거 | 중간 | **부분 완료** · `store.py` 5종 통합 · 2개 프로젝트 격리 미검증 |
| 6 | 장기작업 Action 전환 · 스캔·초기화·모션 실행 | 중간 | 진행률·취소 실물 검증 |
| 7 | 프런트엔드 빌드 도입(해시 파일명) · CSS·HTML 분할 | 중간 | 브라우저 캐시 확인 |
| 8 | 하드웨어 스캐너 분리 · `motion_system` 범위 협의 후 | 높음 | 모터 스캔 계약 + 실물 검증 |

분해 목표안

- `bridge_node` → `ExecutionContextService` · `MotorConfigService` · `ScanOrchestrator` · `MotorEventLog` · `MotionFileService` · `ProjectService`
- `motion_run_manager` → `PlanBuilder` · `MotionPlayer` · `GroupSession` · `StatusStore`
- `midi_control_node` → `MidiDecoder` · `FaderStateMachine` · `PickupPolicy` · `MotionValueMapper`
- `monitor_node` → `DynamixelScanner` · `EthercatScanner` · `StatePublisher`

## 6. 즉시 처리 권고 · 저위험·고효과

- 반영일 · 2026-08-21 · 5개 항목 전부 반영
- 검증 · 전체 31패키지 `colcon build` 통과 · pytest 911건 통과(선재 실패 11건 유지) · `ruff check` 기준선 확정
- 실물 검증 · 연동 스케줄 1사이클 통과(§6-4) · 모션 재생 미검증

| # | 항목 | 상태 | 반영 내용 |
|---|---|---|---|
| 1 | 하드코딩 절대경로 2건 제거 | 완료 | `motion_common` 패키지 신설 · `paths.py` 경유 · `src` 내 잔여 0건 |
| 2 | `api.js` 캐시버스트 토큰 단일화 | 완료 | 소스 3종 → 1종(12파일) · `refactor.py` 삭제 |
| 3 | 모션 표 파서 단일화 | 완료 | `motion_common/motion_table.py` 단일 구현 · 양쪽 노드 위임 |
| 4 | `motor_command_topic` 명칭 정정 | 완료 | `motion_run_manager` 파라미터 → `motion_run_command_topic` |
| 5 | ruff 도입 · `except Exception: pass` 정리 | 부분 | `ruff.toml` 추가 · 신규 모듈 4건 정리 · 구모듈 7건 잔존 |

### 6-1. `motion_common` 신설 · 로드맵 1단계 착수

`src/motion_common` · ament_python · `rclpy` 비의존 순수 모듈 · 테스트 132건 · `ruff check` 무결점

| 모듈 | 책임 | 흡수 |
|---|---|---|
| `paths.py` | workspace/project 경로 | 하드코딩 2건 · 환경변수 → 설치 트리 → 소스 트리 → cwd |
| `motion_table.py` | 모션 표 파서 | 2중 구현 |
| `values.py` | 수치 변환 | 11곳 위임 · 의미가 다른 3곳은 유지(아래) |
| `store.py` | atomic write + 파일락 | 5종 통합 |
| `topics.py` | 토픽 단일 정의 | 노드·launch 리터럴 전량 · 잔여 0건 |
| `coordination.py` | 마스터 역할 판정 | 로드맵 외 · 실물 테스트 중 발견(§6-4) |
| `rpc.py` | 요청·응답 채널 | 폴링 대기·만료 정리 4곳 |
| `generation.py` | 프로젝트 세대 검증 | 검증 4곳 + 식별자 형식 8곳 |
| `group_config.py` | 그룹 연동 설정 | `motion_coordination`에서 이전 |
| `schedule_models.py` `schedule_store.py` | 스케줄 모델·저장 | `motion_schedule`에서 이전 |

§4 목표 6모듈 전부 + 경계 공유용 4모듈.

의존 추가 · `motion_web_bridge` · `motion_runtime` · `motion_schedule` · `motion_supervisor`
· `midi_control` · `motion_studio` · `motion_coordination`

### 6-2. 파서 단일화 · 동치 검증 결과

실제 `motion_projects` 62개 파일(레코드 보유 18개) 기준 · 구 런타임 파서 · 구 브리지 파서 ·
신 통합 파서 3자 레코드 완전 일치 · 실데이터 회귀 없음.

구 파서가 실제로 갈라지던 입력(합성)과 통합 후 결과:

| 입력 | 구 런타임(실행) | 구 브리지(표시) | 신 통합 |
|---|---|---|---|
| 헤더 없는 다중쌍 텍스트 | 2건 (첫 행 소실) | 0건 | 4건 |
| CSV 데이터 행 | 0건 | 2건 | 2건 |
| 숫자형 motion_id `3.0` | `'3.0'` | `'3'` | `'3'` |
| 음수 `time(sec)` 포함 | 포함 실행 | 제외 | 제외 |
| 헤더 없는 첫 행 | 1건 (첫 행 소실) | 0건 | 2건 |

채택 기준 · 실행 경로를 진실로 보되, 실행 경로의 명백한 결함 3건은 표시 경로 규칙으로 교정

- 헤더 없는 파일의 첫 데이터 행을 헤더로 오인해 버리던 문제 해소
- 숫자형 motion_id가 `'3.0'`으로 굳어 매핑 조회에서 어긋나던 문제 해소
- 음수 `time(sec)` 행을 실행하던 문제 해소

주의 · 헤더 없는 모션 파일은 이제 첫 행이 추가로 실행된다.

노출 여부 전수 조사 · `motion_projects` · `motion_data` · `backups` 하위 `json`·`txt`·`csv`
78개 파일 검사 · **헤더 없는 텍스트 모션 파일 0건** · 이 PC에서 동작이 바뀌는 파일 없음.
전부 `{"type":"motion_header", ...}` 또는 엄격 JSON 형식.

잔여 · 다른 PC의 프로젝트 디렉터리와 외부 반입 파일은 미조사 · 동일 검사 필요

### 6-3. ruff 도입 결과 · 기준선 확정

- 설치 · ruff 0.16.4 정적 바이너리 · `~/.local/bin/ruff` · `pip` 불필요
  (`curl -LsSf https://astral.sh/ruff/install.sh | sh`)
- 선택 규칙 · `F` · `E4` · `E7` · `E9` · `BLE001` · `S110` · `S112`
- 광범위 스타일 규칙(E501·W)은 제외 · 39,000줄에 대량 경고를 만들어 게이트로 쓸 수 없음

최초 실행 98건 → 정리 후 69건 (`src` 기준 63건).

| 규칙 | 최초 | 현재 | 조치 |
|---|---|---|---|
| `F821` undefined-name | 1 | 0 | **실버그 수정** · 아래 참조 |
| `F401` unused-import | 28 | 1 | `src` 27건 제거 · 재수출 4건은 `noqa`로 의도 명시 |
| `E401` multiple-imports | 2 | 2 | 루트 scratch 파일 · 정리 대상 |
| `BLE001` blind-except | 47 | 46 | `motion_common` 1건 정리 · 구모듈 46건 잔존 |
| `S110` try-except-pass | 7 | 7 | 구모듈 잔존 |
| `F841` unused-variable | 13 | 13 | 미착수 |

`F821` · `bridge_node.py:1642` · `_establish_project_generation_boundary()`에서 정의되지 않은
`project_id`를 참조. `publish_servo_alarm_policy()` 실패 경로에서만 실행되어 여태 드러나지 않았고,
발생 시 상태 갱신 대신 `NameError`로 중단된다. 코드베이스 관용구인
`self.project_repository.selected_project_id()`로 교정.

재수출 4건은 소비처가 있어 유지 · `noqa: F401` 주석으로 표시:
`bridge_node._project_tree_category_signature` · `studio_node.next_numbered_layer_name` ·
`studio_node.project_initial_motion_values` · `project_store.unique_motion_ids`

잔여 정리 우선순위 · `S110` 7건 → `F841` 13건 → `BLE001` 46건(대규모·별도 작업)

### 6-4. 연동 스케줄 실물 테스트 · 발견 결함 수정

테스트 · 2026-08-21 · 시작 `16:40:20` → 정지 `16:45:20` 1사이클 · 참가 PC 3대

| 항목 | 결과 |
|---|---|
| `paths.py` 경로 해석 | 정상 · 프로젝트 특정 · 스토어 적재 |
| 시작 트리거 | 정상 · 지연 0.5초 이내 |
| 정지 트리거 | 정상 · `dds_stop_published: True` |
| 예외 · Traceback | 0건 |

발견 결함 · 마스터 판정 무력화 · **수정 완료**

- 증상 · 스케줄 노드·웹 API가 존재하지 않는 `config/coordination_settings.yaml`을 읽고
  `role: slave` 문자열을 찾음 · 정본은 `config/motion_coordination.yaml`의 `is_master` 불리언
- 결과 · 파일 부재로 항상 `return True` · **모든 PC가 마스터로 판정** · 슬레이브에서도 스케줄 중복 발화
- 성격 · 선재 결함 · 즉시 처리 1번 항목은 경로 조립만 바꿨고 파일명·판정 로직은 그대로였음
- 수정 · `motion_common/coordination.py` 신설 · 두 호출부 단일화

판정 규칙:

| 설정 상태 | 판정 | 근거 |
|---|---|---|
| 파일 없음 | 마스터 | 연동 미구성 · 단독 동작 · 기존 동작 보존 |
| `enabled: false` | 마스터 | 그룹 미참여 · 단독 동작 |
| `enabled: true` + `is_master: true` | 마스터 | 정본 값 |
| `enabled: true` + `is_master: false` | **아님** | 슬레이브 · 발화 금지 |
| 파싱 실패 | **아님** | 중복 발화가 스케줄 정지보다 위험 |

`is_master` 키 누락 시 기본값은 `False` · 정본 로더 `group_configuration.py:67`과 동일.
스케줄 노드는 1초 주기 호출이므로 설정 파일 mtime·크기 기준 캐시 · 판정 변화 시에만 로그.

기각한 항목 · `repeat_mode` 불일치는 결함 아님

- 최초 관찰 · 스케줄 저장값 `continuous` ↔ 전송값 `reinitialize`
- 실제 · 스케줄의 `motion_config`는 UI가 채우지 않는 데이터클래스 기본값 ·
  실 설정은 웹 UI가 기록하는 `runtime/motion_automation.json`(`repeat_mode: reinitialize`)
- 결론 · 노드가 정본을 읽는 것이 맞음 · `MotionConfig`는 사용되지 않는 잔존 구조체 · 정리 대상이나 결함 아님

### 6-5. 공용 커널 3종 추가 이관 · `values` · `store` · `topics`

검증 · 전체 31패키지 `colcon build` 통과 · pytest 859건 통과 · launch 7개 로드 확인

**`values.py`** · 13개 정의 중 11곳을 위임으로 교체. 의미가 다른 3곳은 **의도적으로 남겼다**.

| 남긴 구현 | 차이 | 흡수하지 않은 이유 |
|---|---|---|
| `supervisor_node._optional_int` | `int(value)` · 진법 접두사 미해석 · 실수 절사 | `int(str(v), 0)`으로 바꾸면 `3.7`이 실패로 바뀐다 |
| `monitor_node._optional_float` | 유한성 검사 없음 · `inf` 통과 | 하드웨어 텔레메트리 경로 · 실물 검증 없이 바꿀 수 없다 |
| `bank_manager._finite_float` | 실패 시 `None`이 아니라 `ValueError` | 계약 자체가 다르다 · 호출부가 예외를 기대한다 |

**`store.py`** · atomic write 5종 통합. 실제로 갈라져 있던 지점:

| 항목 | 통합 전 | 통합 후 |
|---|---|---|
| 임시파일 이름 | 고정 `<name>.tmp` 3종 ↔ `mkstemp` 2종 | `mkstemp` · 두 프로세스가 서로의 임시파일을 덮어쓰지 않음 |
| `fsync` | 1종만 수행 | 전부 수행 · 전원 차단 시 빈 파일 방지 |
| 실패 시 정리 | 일부만 | 전부 |
| 파일락 | 없음(`project_repository`만 별도 규약) | `<이름>.lock` · `flock` · 기존 규약과 동일 명명 |

이관 대상 · `schedule_store` · `project_store`(2종) · `midi_bank_store` · `group_configuration`
· `project_repository`의 `.motor_runtime.lock` 규약은 범위(파일 아닌 작업 단위)가 달라 유지

주의 · `ScheduleStore`는 현재 웹 브리지만 기록하고 노드는 읽기 전용이라 실제 경합은
관찰되지 않았다. 락은 향후 다중 writer 대비 · 고정 임시파일명 제거가 즉시 효과.

**`topics.py`** · 토픽 27종 단일 정의. 노드 파라미터 기본값과 launch 리터럴을 전량 교체해
`topics.py` 밖 토픽 문자열 **0건**. 회귀 방지 테스트가 소스 전체를 훑어 잔존을 잡는다.

파라미터 **이름**은 통합 대상이 아니다 · 노드마다 역할이 다르므로 각자 정하되 기본값만
`topics.py`에서 가져온다. §3-5가 지적한 `motor_command_topic` 오배선은 이름 축과 토픽 축을
혼동한 사례였다.

### 6-6. 경계·RPC·세대 검증 이관

검증 · 31패키지 `colcon build` 통과 · pytest 911건 통과 · `motion_common` ruff 무결점

**패키지 경계 3건 해소** · §3-8 · §7 규칙 4

`motion_common`을 경계로 삼아 순수 모듈을 옮겼다. 이제 자체 패키지 간 Python 직접 import는 **0건**이며, AST로 소스 전체를 훑는 회귀 테스트가 재발을 막는다.

| 이전 | 이후 |
|---|---|
| `web_bridge` → `motion_coordination.group_configuration` | `motion_common.group_config` |
| `web_bridge` → `motion_schedule.schedule_store` · `schedule_models` | `motion_common.schedule_*` |

**`rpc.py` · 요청·응답 채널** · §3-2 · §5 2단계

폴링 대기와 만료 정리를 `ResultStore` 하나로 흡수했다. 흩어져 있던 동안 어긋나 있던 것:

| 항목 | 이전 | 이후 |
|---|---|---|
| 만료 주기 | 10초 · 20초 혼재 | 20초 |
| 만료 기준 시각 | 발신자 `stamp` ↔ 수신 시각 혼재 | **수신 시각** · 발신자 시계에 의존하지 않음 |
| 폴링 간격 | 10ms · 20ms | 10ms |
| 시계 | `time.time()`(벽시계) ↔ `time.monotonic()` | **단조 시계** · NTP 보정에 흔들리지 않음 |

벽시계 사용이 실질적 결함이었다. NTP가 시각을 뒤로 돌리면 대기가 즉시 끝나거나
과도하게 길어진다. 전송 계약(토픽·페이로드)은 바꾸지 않았다 · Service/Action 전환은 6단계.

**`generation.py` · 프로젝트 세대 검증** · §3-3

동일 구현 4곳을 흡수했다. 차이는 "세대를 올릴 수 있는 명령 집합" 하나뿐이었다
(`midi_control`만 `apply_context` 대신 `select_project`). 호출부가 자기 집합을 넘긴다.

요청 식별자 형식 `{접두사}-g{세대}-{꼬리}`도 8곳에서 각자 조립하던 것을 흡수했다.
형식이 한 곳만 어긋나면 응답이 조용히 버려지는 구조였다.

검증 함수는 상태를 바꾸지 않는다 · 세대 반영은 호출부가 결정한다.

### 6-7. 진단 항목 대조 · 코드 변경이 문서 어디에 걸리는가

| 코드 변경 | 진단 위치 |
|---|---|
| `paths.py` 신설 · `schedule_routes.py` · `motion_schedule_node.py` 경로 교체 | §3-8 하드코딩 절대경로 2건 |
| `api.js` 토큰 1종화 · `refactor.py` 삭제 | §3-6 3종 토큰 · 개발 잔여물 정적 배포 |
| `motion_table.py` 신설 · 양쪽 노드 위임 | §3-3 모션 표 파서 2중 구현 |
| `motion_run_command_topic` 개명 | §3-5 의미 불일치 |
| `ruff.toml` · `except Exception: pass` 4건 정리 | §1 lint 설정 없음 · §3-8 광범위 예외 |

로드맵·목표 구조 기준 진척:

| 문서 항목 | 상태 |
|---|---|
| §5 0단계 · lint/pytest 설정 · 지표 기록 | 부분 · ruff 설정만 · pytest 워크스페이스 설정과 함수길이 지표 미착수 |
| §5 1단계 · `motion_common` 신설 · 순수 함수 이관 | 부분 · 파서·경로 이관 · 값 변환 미이관 |
| §5 2~8단계 | 미착수 |
| §4 `motion_common` 6모듈 | 2/6 · `motion_table.py` · `paths.py` 생성 · `rpc.py` · `topics.py` · `values.py` · `store.py` 미생성 |
| §7 규칙 4 · 경계는 `motion_common` | 경계 패키지 실체 확보 · 규칙 적용은 신규 코드부터 |

### 6-8. `bridge_node` 분해 준비 · 상태·락 의존 지도

측정 · 2026-08-22 · AST 기반 · 호출을 타고 간 전이 의존 포함

`MotionWebBridge` · 7,407줄 · 메서드 239개 · `__init__`이 세우는 상태 112개 · 락 18개

**락은 이미 잘 나뉘어 있다.** 클래스 전체를 덮는 락이 없고, 각 락이 좁은 범위를
지킨다. 분해를 막는 것은 락 구조가 아니라 상태 공유다.

전이 의존 기준 3분류:

| 분류 | 메서드 | 줄수 | 성격 |
|---|---|---|---|
| 상태 무의존 | 65 | 1,528 | 락·상태를 전혀 건드리지 않음 · **위험 없이 분리 가능** |
| 상태만 (락 없음) | 89 | 1,826 | 상태를 함께 옮기면 됨 |
| 락 관여 | 84 | 3,239 | 신중히 · 마지막 |

상태 무의존 65개 중 32개는 이미 `@staticmethod`다. 주제는 모터·스캔 719줄,
모션 파일 345줄, 프로젝트 134줄 순.

가장 널리 쓰이는 것은 `project_repository`(65개 메서드)인데, 이는 가변 상태가
아니라 협력자 객체다 · 서비스에 넘겨주면 된다.

전이 관여가 넓은 락 · `_execution_context_lock` 50 · `_lock` 40 ·
`_motion_run_lock` 33 · `_midi_monitor_lock` 33

분해 순서 · 상태 무의존 → 상태만 → 락 관여. 문서 §5 4단계의 서비스 6분할은
도메인 기준이었으나, 측정 결과 **위험도 기준으로 나누는 편이 안전하다.**

### 6-9. `bridge_node` 순수 함수 추출 · 3차까지

`MotionWebBridge` 7,407 → 6,300줄 (-1,107 · 15%) · 실물 검증 통과 (2026-08-22)

| 차수 | 대상 | 감소 |
|---|---|---|
| 1 | `motor_config_rules` · 모터 설정·스캔 판정 13함수 | -643 |
| 2 | `motion_file_analysis` · 모션 파일 해석 8함수 · 죽은 껍데기 10개 | -301 |
| 3 | 값 변환 껍데기 3개 · 저장소 인자화 5함수 | -163 |

위임 껍데기를 남기지 않고 호출 지점을 전부 갱신했다. 순수 모듈은 노드를 모르므로
의존 방향이 한쪽이며, `test_pure_modules.py`가 이 성질을 지킨다 · `self` 접근 ·
동적 `getattr(self, ...)` · `bridge_node` import를 모두 막는다.

저장소가 필요한 함수는 `self.project_repository` 대신 첫 인자로 받는다. 콜러블로
넘기던 두 곳은 `functools.partial`로 저장소를 묶었다.

부수로 정리한 것:

- 파서 통합 때 남긴 죽은 껍데기 7개 · 호출 지점 0곳이었다
- `_optional_int`(65곳) `_optional_float`(9곳) 위임 제거 · 호출부가 `motion_common.values`를 직접 부른다
- 제어 주기 20ms 4중 정의 → `motion_common/timing.py`

분석 정정 · 동적 `getattr`까지 반영하니 순수 메서드가 65 → 41개였다. AST가
문자열 기반 접근을 못 본 탓이며, 추출 도중 `getattr(self, '_motion_state')`를
쓰는 메서드가 순수로 잘못 분류된 것을 발견해 제외했다.

순수 함수 추출은 여기까지가 실질적 한계다. 남은 순수 메서드 약 130줄은 3~5줄짜리
저장소 위임이라 모듈로 빼면 오히려 껍데기가 는다.

다음 · 상태 동반 이동 89메서드 1,826줄 · 상태를 서비스 객체로 옮기고 노드가
그 객체를 갖는 구조로 바꿔야 하므로 지금까지와 성격이 다르다.

### 6-10. 같은 절 안에서 손대지 않은 범위

즉시 처리 5개 항목은 각 절의 일부만 건드린다. 아래는 진단은 그대로 유효한 잔여분이다.

- §3-1 신 노드 + 위임 껍데기 · **전부 미착수** · 최우선 지목 항목
- §3-4 영속 계층 · `store.py`는 만들었으나 직접 기록 모듈 6개 잔존 · mtime 폴링 우회 그대로
- §3-6 프런트엔드 빌드 부재 · 대형 파일 · `window.__motionProjectGeneration` 전역 회피 그대로
- §3-7 하드웨어 프로토콜 혼재 · 미착수 · `motion_system` 범위 협의 필요
- §7 규모 지표 · 파일 1,000줄 초과 **7개**(최대 7,506) · 함수 60줄 초과 **124개**
- ruff 잔여 56건 · `BLE001` 37 · `F841` 12 · `S110` 7

정정 · §3-6 "모듈 3중 인스턴스화"는 배포본에서 성립하지 않는다. `web_ui/CMakeLists.txt`가
빌드 시 `scripts/update_cache.py`로 모든 `?v=` 토큰을 빌드 타임스탬프 1종으로 덮어쓴다.
소스 위생 문제였고, 배포까지 노출되던 `refactor.py`는 실제 문제가 맞았다.

## 7. 유지보수 지표 · 신규 코드 규칙안

- 파일 1,000줄 이하 · 함수 60줄 이하 · `Node` 서브클래스 500줄 이하
- 신규 요청·응답은 `RequestChannel` 또는 srv/action만 사용 · String+JSON 신규 추가 금지
- 프로젝트 파일 기록은 단일 저장 API만 허용
- 패키지 간 Python 직접 import 금지 · 경계는 토픽·서비스 또는 `motion_common`

## 8. 검증 상태

검토 자체(§1~§5)의 상태:

- 코드 검증 · 완료 · 파일·함수 규모 · 중복 위치 · 의존 방향 · 토픽·파라미터 정의 지점
- 실행 검증 · 미수행
- 실물 검증 · 미수행
- 미확인 · 런타임 성능 영향(폴링 대기의 워커 점유량) · 다중 writer 실제 충돌 빈도

§6 반영분의 상태는 별도다:

- 빌드 검증 · 완료 · `colcon build --symlink-install` 6패키지 통과
- 정적 검증 · 완료 · `ruff check` 실행 · 기준선 69건 확정(§6-3)
- 실행 검증 · 부분 · pytest 726건 통과 · 파서 동치 검증(실파일 62개 + 합성 5종) 완료
  · 기존 실패 1건(`test_coordination_web_contract.py`)은 반영 전부터 실패 · `motion_coordination` 10건도 선재 실패
- 데이터 검증 · 완료 · 모션 파일 78개 전수 · 동작 변경 대상 0건(§6-2)
- 실물 검증 · 미수행 · launch 기동 · 모션 재생 · 다중 PC 경로 해석
- 배포 잔여 · **다른 PC는 `colcon build` 필수** · `motion_common` 신규 패키지 · 미빌드 시 `ModuleNotFoundError`로 노드 기동 실패
