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
| 4 | `bridge_node` 분해 · 서비스 6개 | 중간 | **완료**(기준 A · §6-23) · 7,407 → 2,037줄(-73%) · 목표안 서비스 6개 + 추가 3개 신설 · 잔여 1,537줄은 노드 고유(구성·상태 취합·전송) |
| 5 | 영속 계층 통합 · 단일 저장 API + 파일락 · 다중 writer 제거 | 중간 | **완료**(§6-24) · 직접 기록 잔여 0 · 재진입 락 · 다중 writer 4곳 잠금 · mtime 폴링은 6단계에서 |
| 6 | 장기작업 Action 전환 · 스캔·초기화·모션 실행 | 중간 | 진행률·취소 실물 검증 |
| 7 | 프런트엔드 빌드 도입(해시 파일명) · CSS·HTML 분할 | 중간 | 브라우저 캐시 확인 |
| 8 | 하드웨어 스캐너 분리 · `motion_system` 범위 협의 후 | 높음 | 모터 스캔 계약 + 실물 검증 |

분해 목표안

**4단계 완료 기준 · A** (2026-09-09 확정)

아래 서비스 목록을 다 세우면 4단계를 완료로 본다. `Node` 서브클래스 500줄 이하
(§7 규칙)는 **별도 항목**으로 분리한다 · 거기까지 가려면 콜백 계층과 상태 취합까지
손봐야 하고, 그것은 "신 노드 해소"와 성격이 다른 작업이다.

- 미달 잔여는 §7 지표로 계속 추적한다 · 현재 `MotionWebBridge` 2,141줄

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

### 6-11. 불변 경로 인자화 · 순수 추출 4차

`MotionWebBridge` 6,300 → 5,656줄 (-644) · 메서드 200 → 188 (2026-09-08)

의존 지도를 다시 그리면서 §6-8이 `상태만`으로 묶었던 것의 성격을 나눴다.
`__init__`이 세우는 125개 필드 중 **80개는 재대입도 변형도 없는 불변값**이다.
`workspace_root` · `motion_projects_dir` · `event_log_dir` · `host` · `port` 따위가
그렇다. 이것들은 상태가 아니라 설정이므로 **옮길 상태가 없다 · 인자로 넘기면 끝난다.**

| 묶음 | 대상 | 이동처 | 감소 |
|---|---|---|---|
| A | 모션 파일 목록·상세 3함수 | `motion_file_analysis` | -57 |
| B | 바탕화면 바로가기 1함수 | `desktop_shortcut` 신설 | -122 |
| C | 모터 설정 생성 8함수 | `motor_config_build` 신설 | -465 |

C는 처음에 `motor_config_rules`로 넣었더니 1,345줄이 되어 §7 파일 기준을 넘겼다.
판정 규칙과 생성 규칙을 나눠 `motor_config_build`를 세웠다 · 857 + 500줄.
의존은 생성 → 판정 한쪽이다.

`DYNAMIXEL_BAUDRATE`는 `bridge_helpers`에서 `motor_config_build`로 옮겼다.
쓰는 곳이 함께 이동해 노드에는 남을 이유가 없었다.

`test_pure_modules.py`에 두 모듈을 등록하고, **순수 모듈끼리의 import를 허용**하도록
규칙을 넓혔다. 금지의 목적은 노드로 되돌아가는 의존을 막는 것이고 그 경계는
`test_module_does_not_import_the_node`가 따로 지킨다.

검증

- 코드 검증 · `ruff check src` 55건 유지 · 신규 0건
- 실행 검증 · `pytest` 1,000건 통과 · 실패 0 (직전 995건)
- 데이터 검증 · **이동 전후 동치** · 실가동 프로젝트(`연동2`) 레지스트리 1건 +
  합성 4종(Dynamixel W150/W270 · 혼합 · 다중 EtherCAT 마스터 · 빈 레지스트리) × 기준설정 2종
- 실물 검증 · `colcon build` 31패키지 · 서비스 재시작 · A `GET /api/motion-files`
  목록·상세·실패 경로 · B `POST /api/system/desktop-shortcut` `already_installed`
- 실물 미검증 · B의 `created` 경로(바로가기가 이미 있어 확인 불가 · 단위 테스트로만)
  · C의 웹 저장 경로 `PUT /api/motor-config`(가동 중 프로젝트 설정을 다시 쓰므로 미수행)
- 검증 불가 · Dynamixel(직렬 포트 부재) · MIDI(장치 미연결) · 다중 PC(전원 차단)

작업 중 잡은 회귀 1건 · `_read_current_motor_config`에서 기본 설정을 미리 계산하도록
바꿨다가 지연 평가가 깨져 `test_project_repository`가 실패했다. 원래 호출 위치를
그대로 두는 것으로 되돌렸다. 인자화는 **호출 시점까지 같아야** 동치다.

분해 남은 몫 · 상태 무의존 7메서드 56줄 · 상태만 49메서드 722줄 · 락 관여 132메서드
4,487줄 · 지도 `docs/metrics/bridge-state-map-20260908.json`

### 6-12. 다음 단계 · 위임 껍데기 27개

`상태만` 722줄 중 137줄이 **이미 존재하는 서비스로의 위임 껍데기**다 · §3-1 지목분.

| 대상 서비스 | 껍데기 | 줄 | 외부 호출 |
|---|---|---|---|
| `MotionStudioRosBridge` | 10 | 49 | 21 |
| `MotionStudioSync` | 6 | 22 | 24 |
| `CoordinationWebBridge` | 4 | 14 | 2 |
| `rpc.ResultStore` ×5 | 5 | 30 | 0 |
| `MotorRestartCoordinator` | 1 | 12 | 0 |
| `EthercatAliasManager` | 1 | 10 | 0 |

껍데기를 지우기 전에 **역참조를 끊어야 한다.** 서비스가 노드를 다시 부른다 ·
`motion_studio_sync.py:284` → `bridge._motion_studio_start_order_lock()` ·
`motion_studio_sync.py:287` → `bridge.prepare_unified_motion_studio()`.
지금 껍데기만 지우면 그 호출이 끊긴다.

외부 호출 0인 `rpc.ResultStore` 5개(30줄)부터가 가장 안전하다.

그 뒤가 진짜 가변 상태다 · `motor_config_file`(재대입 10곳 · 락 관여 15메서드 1,039줄) ·
`applied_motor_config_file`(락 관여 39메서드 2,476줄). 락 구간과 함께 설계해야 한다.

### 6-13. 판정 로직 추출 · 5차 · §6-12 계획 정정

`MotionWebBridge` 5,656 → 5,418줄 (-238) · 메서드 188 → 186 (2026-09-08)

| 대상 | 줄 | 이동처 | 노드 결합 |
|---|---|---|---|
| `_annotate_ethercat_project_compatibility` | 185 | `ethercat_project_compat` 신설 | `self.load_motor_config()` 하나뿐 · 콜러블로 전달 |
| `_runtime_service_status` | 73 | `motor_config_rules` | 읽기 전용 상태 3개 · 인자화 |

`상태만` 분류 722 → 464줄.

#### §6-12 정정 · `rpc.ResultStore` 껍데기 제거는 이득이 적다

§6-12는 외부 호출 0인 `rpc.ResultStore` 껍데기 5개(30줄)를 "가장 안전한 첫
대상"으로 지목했으나, 측정해 보니 그렇지 않다.

- 이 5개는 **테스트 이음매다** · `bridge._wait_for_jog_result = lambda ...` 형태로
  6곳이 인스턴스에 직접 꽂아 쓴다
- 각 껍데기가 **채널별 기본 대기시간을 담고 있다** · jog 1.0초 · mapping 2.0초 등 ·
  지우면 그 값이 호출 지점 14곳으로 흩어진다

껍데기 제거의 목적은 로직을 노드 밖으로 꺼내는 것인데, 이 5개에는 꺼낼 로직이
없다. 지우면 기본값만 흩어진다. **보류한다.**

같은 이유로 `상태만` 잔여분 중 불변 필드만 쓰는 14메서드 111줄도 보류한다.
`project_repository` 위임 3~13줄짜리라 모듈로 빼면 껍데기가 늘어난다 · §6-9의
판단과 같다.

#### 남은 것은 설계가 필요하다

기계적 추출은 여기서 끝이다. 남은 `상태만` 464줄의 중심은 가변 상태 2개다.

| 상태 | 재대입 | 상태만 | 락 관여 |
|---|---|---|---|
| `motor_config_file` | 15곳 | 7메서드 147줄 | 15메서드 1,039줄 |
| `applied_motor_config_file` | 1곳 | 5메서드 87줄 | 39메서드 2,476줄 |

`motor_config_file`은 겉보기에 "저장소에서 파생되는 캐시"라 없앨 수 있어 보이지만,
`test_project_repository`가 **프로젝트 전환 시 `Path()`로 비워지는 것**을 격리
보장으로 검증한다. 즉 이 필드는 계약의 일부다. 없애려면 그 계약을 어디로 옮길지
먼저 정해야 한다 · 락 구간과 함께 설계할 것.

#### 테스트 이음매의 이동

`_runtime_service_status`는 인스턴스에 꽂아 쓰던 이음매였다. 순수 모듈로 옮기면서
이음매도 모듈 함수로 옮기고, `mock.patch.stopall()`을 도는 autouse 픽스처로
테스트마다 되돌린다. 로직이 73줄이라 옮길 값어치가 있었고, 이 점이 위 5개
껍데기와 다르다.

작업 중 잡은 회귀 1건 · `self.workspace_root`가 원본에서는 `or` 뒤에 있어 늦게
평가됐는데, 인자로 올리면서 호출 시점으로 앞당겨져 노드 스텁 10건이 실패했다.
`getattr(self, 'workspace_root', Path())`로 되돌렸다. §6-11에서 겪은 것과 같은
종류다 · **인자화는 호출 시점까지 같아야 동치다.**

#### 검증

- 코드 검증 · `ruff check src` 55건 유지 · 신규 0건
- 실행 검증 · `pytest` 1,005건 통과 · 실패 0
- 실물 검증 · `colcon build` · `motion-control.service` 재시작 · 실행 컨텍스트 **자동 적용**
  (§6-14 재발 없음) · `runtime_service_status` 산출 `service_management.runtime`에서
  `phase: ready` · `runtime_target_matches_process: True` 확인
- 실물 검증 · **AC Servo 물리 스캔 1회** · `scan_id 1788853757137-1` ·
  `ethercat_project_compat`가 정확히 이 상황을 위해 만든 판정을 냈다

스캔 시점의 물리 조건이 마침 이 판정의 목적과 일치했다. 프로젝트는 Master 0의
1축만 쓰는데 Master 1은 랜선이 빠져 미응답이었다. 결과:

```
compatible: true
required_master_indices: [0]
unused_registered_master_indices: [1]
masters: [{master_index: 0, expected 1, observed 1, compatible: true, errors: []}]
message: 프로젝트 EtherCAT 구성 확인 완료 · Master 0
```

**쓰이지 않는 Master의 미응답을 프로젝트 불일치로 판정하지 않는다** — 이 함수의
docstring이 적어둔 구분이 실물에서 그대로 나왔다. Slave 0은 alias 103 ·
vendor_id 1647 · product_code 1614282756 · serial 402982152를 직접 읽어 대조했다
(`direct_read_complete: true`).

전체 스캔 결과 자체는 `부분 완료`다 · Master 1 미응답 · Dynamixel은 포트 부재로
이번 스캔 대상에서 제외(AC Servo 전용 스캔). 스캔 후 모터 서비스는 자동 복구됐고
(`motor_service_restored: True`), 재열거 과정에서 축 0의 통신 알람(0xFF50)도 해소됐다.

참고 · 검증 중 관측한 축 0 알람과 Slave 1 이탈은 **작업자가 의도적으로 랜선 하나를
분리한 결과**로 확인됐다. 결함이 아니다.

### 6-14. 결함 기록 · `motion_supervisor` 수신 정지

발생 · 2026-09-08 15:47 재시작 직후 · 현상 해소는 재시작 1회

증상은 "모션 스튜디오의 레이어가 사라졌다"였으나 **데이터 손실은 없었다.**
레이어 파일도 `project.json`의 `studio_managed_layer_sha256`도 그대로였다.

연쇄

```
motion_supervisor 수신 정지
  → project_generation_boundary 무응답
  → 브리지 1초 주기 자동 재적용(bridge_node.py:422) 계속 실패
  → motion_studio_node · motion_mapping_manager 세대 0 유지
  → 모든 명령 "현재 프로젝트 세대와 다른 요청을 폐기했습니다"
  → 화면에는 레이어가 없는 것처럼 보임
```

측정된 사실

| 항목 | 결과 |
|---|---|
| 프로세스 | 생존 · 13스레드 · State S |
| 발신 | 정상 · `safety_status` 2 Hz · 브리지가 수신 |
| 수신 | 전무 · 구독 콜백 미실행 |
| 근거 | 브리지 발행 `project_generation_boundary` 6건을 버스에서 관측 · 응답 0건 |
| 근거 | 락을 쓰지 않는 잘못된 JSON 경로조차 무응답 · jog·safety 요청도 무응답 |

구조상의 소인 · `MultiThreadedExecutor(num_threads=2)` + 기본 콜백 그룹
`MutuallyExclusive`. `safety_status`만 별도 그룹(`_safety_callback_group`)이라
살아남았고, 기본 그룹의 콜백 하나가 막히면 나머지 구독이 전부 멈춘다.
막힌 지점은 특정하지 못했다.

재발 판별 · `safety_status`는 2 Hz로 나오는데
`POST /api/execution-context/apply`가 `waiting_motor_runtime`으로 실패하면 같은 증상.

다음 조치 후보

- `motion_supervisor`에 `faulthandler.register(SIGUSR1)` 추가 · 재발 시 `kill -USR1`로
  스레드 덤프 확보 (현재 `py-spy` 미설치 · `pip` 부재로 sudo 필요)
- 구독별 콜백 그룹 분리 검토 · 하나가 막혀도 나머지가 살아남도록
- 자동 재적용이 N회 연속 실패하면 로그로 드러내기 · 지금은 조용히 재시도만 한다

### 6-15. 상태 동반 이동 1차 · 모션 스튜디오 · 순환 절단

`MotionWebBridge` 5,418 → 5,345줄 · 메서드 186 → 176 · 상태 필드 125 → 119 · 락 18 → 16

**4단계에서 성격이 바뀌는 지점이다.** 지금까지는 함수를 옮겼고, 여기서는 상태를 옮겼다.

#### 무엇이 문제였나

스튜디오 상태 7개(`_motion_studio_lock` · `_motion_studio_status` · `_motion_studio_store` ·
`_motion_studio_editor_store` · `_motion_studio_workspace_signatures` ·
`_motion_studio_command_order_lock` · `_motion_studio_start_generation`)를 세 곳이
각자 `bridge.___`로 집어 썼다 · 노드 · `MotionStudioRosBridge` · `MotionStudioSync`.

그리고 **순환이 있었다.**

```
MotionStudioRosBridge.request()
  → bridge._wait_for_motion_studio_result()      ← 노드 껍데기
      → bridge._motion_studio_transport().wait   ← 다시 전송 계층

MotionStudioSync.request_prepared()
  → bridge.prepare_unified_motion_studio()       ← 노드 껍데기
      → bridge._motion_studio_sync().prepare()   ← 다시 자기 자신
```

§3-1이 지목한 "추출 모듈은 역참조 껍데기"의 실물이다.

#### 무엇을 했나

`motion_studio_session.py` 신설 · `MotionStudioSession`이 상태 7개를 **혼자 갖는다.**
노드는 이 객체를 소유하기만 하고, 전송·동기화 서비스가 이 객체를 직접 받는다.

| 층 | 이전 | 이후 |
|---|---|---|
| 노드 | 상태 7필드 소유 + 위임 껍데기 10개 | 세션 1개 소유 · 껍데기 0 |
| `MotionStudioRosBridge` | `bridge.<X>` 21종 | **12종** · 상태 접근 0 |
| `MotionStudioSync` | `bridge.<X>` 15종 | **5종** · 상태 접근 0 |
| 라우트 | 노드 껍데기 호출 | 서비스 직접 호출 |

제거한 노드 껍데기 10개 · `_wait_for_motion_studio_result` ·
`_wait_for_motion_studio_editor_result` · `_motion_studio_start_order_lock` ·
`request_motion_studio` · `request_motion_studio_editor` ·
`request_prepared_motion_studio` · `prepare_unified_motion_studio` ·
`sync_motion_studio_result` · `import_motion_studio_layer` · `export_motion_studio`

남긴 것 · `_motion_studio_transport()` · `_motion_studio_sync()` 두 접근자와
`cancel_pending_motion_studio_start`. 앞 둘은 게으른 생성자이고, 마지막 하나는
`safety_routes`와 노드가 `getattr`로 있는지 물어보고 쓰는 안전 정지 경로다.

`MotionStudioSync`는 이제 전송 계층을 인자로 받는다 · `MotionStudioSync(bridge, session,
transport)`. 노드를 거치지 않고 `self.transport.request(...)`를 부른다.

#### 테스트 이음매도 함께 옮겼다

노드에 꽂아 쓰던 이음매가 89곳 있었다. 상태는 `bridge._motion_studio_session.<X>`로,
동작은 서비스로 옮겼다.

- `bridge.request_motion_studio = fn` → 전송 계층 대역 객체(`_StubTransport`)를 꽂는다
- `bridge._wait_for_motion_studio_result = fn` → 세션 저장소에 응답을 미리 넣는다 ·
  실제 대기 경로를 그대로 탄다
- `bridge.prepare_unified_motion_studio = fn` → 동기화 서비스의 `prepare`를 대체한다

`test_motion_studio_boundaries.py`의 경계 계약도 뒤집었다. 이전에는 "노드에 위임만
남아 있을 것"을 검사했으나, 이제 **"노드에 위임이 없을 것"** 과 **"서비스가 노드를
되부르지 않을 것"** 을 검사한다.

#### 검증

- 코드 검증 · `ruff check src` 55건 유지 · 신규 0건
- 실행 검증 · `pytest` 1,005건 통과 · 실패 0
- 실물 검증 · `colcon build` · `motion-control.service` 재시작 · 실행 컨텍스트 자동 적용
  `ready` · 차단 사유 없음
- 실물 검증 · `GET /api/motion-studio` · `통합 프로젝트 연결 완료` ·
  레이어 `layer_55c7ff6a` frames 165 · mappings 1 · motion_files 1 ·
  라우트 → `sync().prepare()` → `transport().request('list')` → 세션 저장소 대기까지
  새 경로를 전부 탄다
- 실물 검증 · `/api/status`의 `motion_studio` · `state: idle` ·
  노드 `snapshot`이 `session.snapshot_status()`를 읽는 경로 확인
- 실물 미검증 · 녹화·재생·레이어 편집 · 화면 조작이 필요하다

### 6-16. 빈 위임 층 제거 · `MotorService` · `MotionRunService`

`MotionWebBridge` 5,345 → 5,327줄 · 메서드 176 → 173 · 파일 2개 삭제(-187줄)

§6-15가 스튜디오에서 한 것과 **방향이 반대다.** 스튜디오의 서비스에는 옮길 로직이
있었다. 여기 둘에는 **아무것도 없었다.**

| 모듈 | 줄 | 내용 |
|---|---|---|
| `motor_service.py` | 110 | 전량 `self.bridge.___` 위임 · §3-1이 지목한 그 파일 |
| `motion_run_service.py` | 77 | 16개 전량 위임 + `AutomationService` 4개 |

로직이 없는 층은 옮길 것이 없다. 부풀리지 않고 **지웠다.** 라우트가 노드를 직접
부른다. 로직 자체를 노드에서 꺼내는 일은 락 구간과 함께 설계해야 하며 그대로 남는다.

#### 발견한 결함 · Dynamixel 스캔 제한시간이 절반이었다

복제 층이 값을 갈라놓고 있었다.

```
bridge_node.scan_dynamixel_motors(timeout_sec=40.0)    ← cc73228이 20 → 40으로 올림
MotorService.scan_dynamixel_motors(timeout_sec=20.0)   ← 같이 올리지 않음
```

라우트는 `getattr(bridge, 'motor', bridge)`로 **복제 층을 먼저** 골랐다. 그래서
`cc73228`이 올린 40초는 한 번도 효력이 없었고 실제로는 20초로 동작했다.

Dynamixel 스캔은 Protocol 2.0 Broadcast Ping과 ID `0~252` 개별 Ping을 모두 수행한다.
제한시간이 짧으면 응답을 다 받기 전에 끊길 수 있다.

**이 변경으로 40초가 실효를 갖는다.** 동작이 바뀌는 유일한 항목이다 ·
Dynamixel 장치가 없어 **실물 미검증**이다.

이것이 껍데기 층의 대가다. 한쪽만 고치면 다른 쪽이 조용히 이긴다.

#### 조정(coordination) 위임 3개도 제거

`coordination_control` · `coordination_status` · `update_coordination_settings` ·
각 3~5줄 · `system_routes`가 `bridge._coordination_web_bridge`를 직접 부른다.

`_coordination_execution_blocker`는 남긴다 · 서비스가 없을 때 빈 문자열을 돌려주는
방어가 들어 있고 노드 안에서 5곳이 쓴다.

#### 검증

- 코드 검증 · `ruff check src` 55건 유지 · 신규 0건
- 실행 검증 · `pytest` 1,005건 통과 · 실패 0
- 실물 검증 · `colcon build` · 재시작 · 실행 컨텍스트 `ready` · 차단 없음 ·
  손댄 라우트 7종 전부 HTTP 200 · `success: true`

```
/api/motion-files            현재 프로젝트 모션 파일을 불러왔습니다
/api/motor-config            motor config YAML loaded
/api/motion-mappings         현재 프로젝트 모션축 설정을 불러왔습니다
/api/motors/scan/progress    (진행 상태)
/api/motors/ethercat-aliases EtherCAT EEPROM Alias 1축 읽기 완료
/api/motion-run/status       motion run status
/api/motion-studio           통합 프로젝트 연결 완료
```

- 실물 미검증 · Dynamixel 40초 제한시간 · 장치 부재
- 실물 미검증 · AC Servo 재스캔 경로 · 같은 라우트 파일의
  `ethercat-aliases`·`scan/progress`가 통과해 배선은 확인됐으나 스캔 자체는
  버스를 재열거하므로 별도 지시 없이 다시 돌리지 않았다

### 6-17. `MotorEventLog` 신설 · §5 분해 목표안 첫 서비스

`MotionWebBridge` 5,327 → **4,987줄** · 메서드 173 → 163 · 락 16 → 15 ·
락 관여 4,486 → 4,147줄

§5의 `bridge_node` 분해 목표안 6개 중 하나를 실제로 세웠다.

#### 옮긴 것 · 10메서드 335줄 + 상태 9개

| 메서드 | 줄 | 서비스 이름 |
|---|---|---|
| `_prune_motor_event_logs` | 64 | `prune` |
| `motor_events` | 57 | `events` |
| `_record_motor_error_transitions` | 53 | `record_motor_error_transitions` |
| `_record_motion_run_transition` | 50 | `record_motion_run_transition` |
| `_append_motor_event` | 34 | `append` |
| `_motor_event_log_context` | 26 | `context` |
| `clear_motor_events` | 20 | `clear` |
| `delete_motor_event_file` | 19 | `delete_file` |
| `_event_log_paths` · `_event_log_lines` | 10 | 모듈 함수 |

상태 · `_event_log_lock`(RLock) · `event_log_dir` · `event_log_retention_days` ·
`event_log_max_bytes` · `event_log_max_records` · `event_log_max_files` ·
`_active_motor_errors` · `_last_motion_run_state`

노드에서 받는 것은 협력자뿐이다 · `project_repository` · `workspace_root` ·
`runtime_project_id` 콜러블 · `logger` 콜러블.

#### 전이 기록을 함께 옮긴 이유

`_record_motor_error_transitions`와 `_record_motion_run_transition`은 로그 기록이
아니라 **판정**이다 · 이전 상태와 비교해 달라진 것만 이벤트로 남긴다. 이 판정과
기록이 같은 락 아래에서 일어나야 같은 오류가 두 번 적히지 않는다.

로그 저장소만 떼어내고 판정을 노드에 남기면, 노드는 판정용으로 별도 락을 들어야
하고 그 순간 **판정과 기록 사이가 벌어진다.** 그래서 락과 함께 옮겼다.

`append`가 락 안에서 `prune`을 다시 부르므로 `RLock`을 그대로 유지했다.

#### 라우트

`bridge.motor_events` · `bridge.clear_motor_events` · `bridge.delete_motor_event_file`
세 껍데기를 지우고 `motor_routes`가 `bridge._motor_event_log.events(...)` 형태로
직접 부른다 · §6-15·§6-16과 같은 규칙.

#### 테스트

`test_motor_event_log.py`가 **노드를 아예 만들지 않는다.** 이전에는
`MotionWebBridge.__new__`로 껍데기를 세우고 필드 8개를 손으로 꽂았다. 지금은
`MotorEventLog(...)`를 직접 만든다 · 7건 통과.

`test_execution_context.py`의 `make_bridge`는 파일에 쓰지 않는 로그 서비스를
꽂는다 · 프로젝트 전환 시 전이 기억이 지워지는지만 검사한다.

#### 검증

- 코드 검증 · `ruff check src` 55건 유지 · 신규 0건
- 실행 검증 · `pytest` 1,005건 통과 · 실패 0
- 실물 검증 · `colcon build` · 재시작 · 실행 컨텍스트 `ready` · 차단 없음 ·
  모터 1축 온라인 · `Master 0 [0:OP]`
- 실물 검증 · `GET /api/motor-events` · 실가동 프로젝트 `연동2`의 로그를 읽는다 ·
  `2026-09-08.jsonl` 6건 · 보존 정책 4종 그대로 반환 · `context()`가 프로젝트
  로그 디렉터리를 정확히 찾는다
- 실물 검증 · 엔드포인트 8종 전부 HTTP 200
- 실물 미검증 · `append` 기록 경로 · `context()`를 `events`와 공유하므로 배선은
  확인됐으나 새 이벤트를 낼 조건(모터 오류 · 모션 실행 전이)을 만들지 않았다
- 실물 미검증 · `clear` · `delete_file` · 로그를 지우므로 돌리지 않았다

### 6-18. `ScanOrchestrator` 신설 · §5 분해 목표안 두 번째 서비스

`MotionWebBridge` 4,987 → **4,430줄** · 메서드 163 → 152 · 락 15 → 13 ·
락 관여 4,147 → **3,635줄**

#### 모터 스캔 불변조건은 건드리지 않았다

먼저 밝혀둔다. 이 작업은 스캔의 **조율**만 옮긴 것이다. 물리 검색은 여전히
`motion_system`의 스캔 서비스가 수행하고, `ethercat rescan` 요구도 `scan_contract`도
Protocol 2.0 Ping 범위도 그대로다. 코드 이동이며 규약 변경이 아니다.

#### 옮긴 것 · 11메서드 540줄

| 메서드 | 줄 | 서비스 이름 |
|---|---|---|
| `_call_ethercat_scan_service_locked` | 187 | `_call_ethercat_service_locked` |
| `_call_scan_service` | 118 | `_call_service` |
| `_call_scan_service_locked` | 67 | `_call_service_locked` |
| `_expected_runtime_ethercat_axes` | 38 | 그대로 |
| `_ethercat_scan_runtime_handoff` | 34 | `_runtime_handoff` |
| `_scan_progress_callback` | 33 | `progress_callback` |
| `_expected_runtime_axes` | 31 | 그대로 |
| `motor_scan_progress` | 9 | `progress` |
| `scan_motors` · `scan_ac_servo_motors` · `scan_dynamixel_motors` | 23 | `scan_all` · `scan_ac_servo` · `scan_dynamixel` |

서비스가 갖는 상태 · `_scan_request_lock` · `_progress_lock`(RLock) · `_progress` ·
스캔 클라이언트 3종 · 서비스 이름 3종.

#### 락 하나를 나눠 갖는다

`_motor_lifecycle_lock`은 **노드가 소유하고 서비스에 넘긴다.** 설정 적용
(`apply_motor_config`) · 재시작(`restart_motor_control_system`) · 실행 해제
(`clear_motor_runtime_application`)가 같은 락을 쓰기 때문이다. 이 락은 "지금
모터 관련 작업이 하나 돌고 있다"를 뜻하므로 **서비스마다 따로 만들면 그 뜻이
깨진다.** 그래서 소유자를 노드에 두고 생성자 인자로 건넸다.

이것이 남은 락 구간(3,635줄)의 핵심 난점이다. 락이 서비스 경계를 가로지른다.

#### 노드에 남긴 것

`_monitoring_mapping_rows_for_context`(52줄)는 처음에 스캔 전용으로 분류했으나
실제 호출자는 `snapshot` 하나였다 · 노드에 남겼다. **전이 도달 집합만 보고
"전용"이라 판단하면 안 된다** — 도달 집합 안에 노드에 남을 메서드가 섞여 있으면
그 하위도 남아야 한다.

#### 테스트

`test_scan_progress.py`는 노드 없이 `ScanOrchestrator`만 세운다.
`test_execution_context.py`는 `_scan_of(bridge)` 도우미로 스텁에 조율기를 붙인다 ·
스텁이 저장소를 나중에 꽂는 경우가 있어 매번 최신 값을 따라가게 했다.

#### 검증

- 코드 검증 · `ruff check src` 55건 유지 · 신규 0건
- 실행 검증 · `pytest` 1,005건 통과 · 실패 0
- 실물 검증 · **AC Servo 물리 스캔 1회** · `scan_id 1788912484901-1` ·
  새 조율 경로가 처음부터 끝까지 돌았다

```
motor_service_was_active: True → restore_required: True → restored: True
motor_runtime_recovery: expected_axes [0] · online_axes [0] · recovered True · 2.185s
physical_scan: rescan_performed true · source ethercat_rescan_sii_and_register
project_comparison: compatible true · required [0] · unused [1]
결과: 부분 완료 (Master 1 미연결)
```

진행 이벤트 9건이 순서대로 쌓였다 · `started` → `ethercat_preflight` →
`ethercat_rescan` → `ethercat_rescan_done`(2.915ms) → `ethercat_topology` …
`_progress_lock`과 `_progress`가 서비스로 옮겨간 뒤에도 그대로다.

스캔 후 · 실행 컨텍스트 `ready` · 차단 없음 · 모터 1축 온라인 ·
`Master 0 [0:OP]` · `runtime: ready`

**모터 서비스 일시 정지와 복구가 서비스 안에서 정상 동작했다.** 이것이
`_call_ethercat_service_locked` 187줄의 핵심이고, 이번 이동에서 가장 위험한
부분이었다.

- 실물 미검증 · Dynamixel 스캔 · 포트 부재
- 실물 미검증 · `scan_all`(전체 검색) · AC Servo·Dynamixel 동시 경로

### 6-19. `MotorConfigService` 신설 · 가변 상태 소유 이전 · ① 항목 해소

`MotionWebBridge` 4,430 → **3,722줄** · 메서드 152 → 140 · 락 관여 3,635 → **3,069줄**

§5 분해 목표안의 세 번째 서비스이자, §6-13이 **"계약을 어디로 옮길지 먼저 정해야
한다"** 고 남겨둔 가변 상태 문제의 답이다.

#### 가변 상태 두 개의 소유자를 정했다

| 옛 이름 | 새 이름 | 뜻 |
|---|---|---|
| `motor_config_file` | `MotorConfigService.selected` | 지금 고른 모터축 설정 파일 |
| `applied_motor_config_file` | `MotorConfigService.applied` | Motor Manager가 실제로 물고 있는 파일 |

§6-13은 "`motor_config_file`은 파생 캐시처럼 보이지만 프로젝트 전환 시 `Path()`로
비워지는 것을 테스트가 격리 보장으로 검증하므로 계약의 일부"라고 적었다.
**그 계약이 이제 이 객체 안에 있다** · `clear_selection()`.

노드에 남은 프로젝트 전환 메서드들(`select_motion_project` ·
`_bind_selected_project_sources` · `delete_motion_project` 등)은
`self._motor_config.selected`를 통해 같은 소유자를 갱신한다.

#### 옮긴 것 · 12메서드 703줄

`clear_runtime_application` 142 · `apply` 140 · `save` 96 · `restart_motor_control` 84 ·
`_payload_from_path` 51 · `restart_managed_program` 48 · `delete` 38 ·
`_file_from_payload` 27 · `clear_stopping_release_state` 26 · `load` 23 · `_write` 16 ·
`_read_current` 12

#### 락은 여전히 노드가 소유한다

`lifecycle_lock`은 `ScanOrchestrator`와 **같은 객체**다. 노드가 만들고 두 서비스에
넘긴다 · §6-18에 적은 이유 그대로다.

#### `ScanOrchestrator`의 설정 의존을 인자로 바꿨다

스캔이 프로젝트 설정을 읽을 때 `self.bridge.load_motor_config`를 부르고 있었다.
설정이 서비스로 옮겨가면서 `self.bridge._motor_config.load`가 될 뻔했는데, 그러면
스캔이 노드를 거쳐 다른 서비스를 아는 꼴이다. `load_motor_config` 콜러블을
생성자 인자로 받게 했다.

#### 이동 중 잡은 버그 1건

`clear_stopping_release_state`가 `motion_studio_session.session_of(self)`를 부른다.
`self`가 노드일 때는 맞았지만 서비스로 옮기니 **서비스에서 스튜디오 세션을 찾게
됐고**, 세션이 없어 `stopping` 상태가 정리되지 않았다. `session_of(self.bridge)`로
고쳤다 · 테스트가 잡았다.

`self.___` 형태만 기계적으로 바꾸면 이런 것을 놓친다. **`self`를 통째로 넘기는
호출**(`f(self)`)도 함께 봐야 한다.

#### 검증

- 코드 검증 · `ruff check src` 55건 유지 · 신규 0건
- 실행 검증 · `pytest` 1,005건 통과 · 실패 0
- 실물 검증 · `colcon build` · 재시작 · 실행 컨텍스트 `ready` · 차단 없음 ·
  엔드포인트 7종 HTTP 200
- 실물 검증 · `applied` · `service_management.runtime.runtime_config_file`이
  실행 중인 세션 yaml을 가리키고 `runtime_target_matches_process: True`
- 실물 검증 · `selected` · `GET /api/motor-config` ·
  `연동2-29d895ca/motor_axes/motor_axes.yaml` · `config_revision 51145012…` ·
  registry 1축
- 실물 미검증 · `save` · `apply` · `delete` · `restart_motor_control` ·
  `clear_runtime_application` · 모터 설정을 다시 쓰거나 서보를 재시작하는 경로다
- 실물 미검증 · 프로젝트 전환 시 `clear_selection` 계약 · 가동 중 프로젝트를
  바꿔야 확인된다

### 6-20. `ExecutionContextService` 신설 · §5 분해 목표안 네 번째 서비스

`MotionWebBridge` 3,722 → **3,409줄** · 메서드 140 → 132 · 락 13 → 11 ·
락 관여 3,069 → **2,756줄**

#### 옮긴 것 · 8메서드 305줄

`reconcile` 182 · `_ack_matches` 49 · `status` 25 · `reconcile_blocking` 19 ·
`schedule_reconcile` 13 · `invalidate_nodes` 10 · `_set_status` 4 · `context_id` 3

서비스가 갖는 것 · `_status`와 그 락(RLock) · 적용 직렬화 락(`_apply_lock`).

#### 세대 번호는 노드에 남겼다

`_project_generation`과 `_current_project_generation`은 옮기지 않았다.
**노드 안 25곳이 쓰는 전역 개념이고 실행 컨텍스트만의 것이 아니다.**
`_establish_project_generation_boundary`도 같은 이유로 남겼다.

경계를 락으로만 그으면 이렇게 여러 관심사가 한 락 아래 섞인 것을 통째로 옮기게
된다. 락은 후보를 찾는 데 쓰고, 실제 경계는 **누가 그 개념을 쓰는가**로 정했다.

#### 서비스끼리의 의존을 또 인자로

`MotionStudioRosBridge`가 `record`·`play` 요청에 컨텍스트 식별자를 실어 보낸다.
`bridge._execution_context.context_id()`로 두면 스튜디오 전송이 노드를 거쳐 다른
서비스를 아는 꼴이므로 `context_id` 콜러블을 생성자 인자로 받게 했다 ·
§6-19에서 `load_motor_config`에 한 것과 같다.

#### 검증

- 코드 검증 · `ruff check src` 55건 유지 · 신규 0건
- 실행 검증 · `pytest` 1,005건 통과 · 실패 0
- 실물 검증 · `colcon build` · 재시작 · **`reconcile`이 전체 적용 사이클을 돌았다**

```
state ready · control_allowed True · context_id 4a5d43b3d5097ecd7f9a9fc5…
motion_mapping True · midi_control True · motion_run True · motion_studio True
motor_runtime True · midi_control_confirm True · motion_run_confirm True
motion_studio_confirm True
```

노드 4개 적용과 확인 4건이 모두 성공했다 · 엔드포인트 8종 HTTP 200

### 6-21. `ManualMotorCommandService` 신설 · 다섯 번째 서비스

`MotionWebBridge` 3,409 → **2,902줄** · 메서드 132 → 120 · 락 관여 2,756 → **2,273줄**

§5 목록에는 없던 서비스다. 남은 코드를 훑으니 **화면에서 사람이 직접 내리는 모터
명령**이 500줄로 가장 큰 덩어리였고, 의존이 셋뿐이라 가장 깨끗했다.

#### 옮긴 것 · 12메서드 500줄

`ac_servo_control` 100 · `ac_servo_action` 95 · `dynamixel_action` 89 ·
`ac_servo_jog` 82 · `dynamixel_jog` 76 · 응답 콜백 2개 28 · 대기 2개 12 ·
모터 상태 조회 도우미 2개 14 · 세대 대조 4

서비스가 갖는 것 · 조그·동작 요청 발행자와 응답 저장소(`_jog_store` · `_action_store`).

최종 모터 출력은 여전히 `motion_supervisor`가 단독으로 발행한다 · 이 서비스는
요청을 보내고 결과를 기다릴 뿐이다 · §2의 유지 대상 구조를 건드리지 않았다.

#### 노드에 남긴 세 곳

`publish_servo_alarm_policy` · `request_safety_stop` · `_establish_project_generation_boundary`가
응답 대기를 쓴다 · `self._manual.wait_for_jog_result(...)` 형태로 서비스를 부른다.
안전 정지와 세대 경계는 노드의 책임이므로 남겼다.

#### 검증

- 코드 검증 · `ruff check src` 55건 유지 · 신규 0건
- 실행 검증 · `pytest` 1,005건 통과 · 실패 0
- 실물 검증 · `colcon build` · 재시작 · 실행 컨텍스트 `ready` ·
  노드 확인 8건 전부 성공 · `control_allowed True` · 엔드포인트 9종 HTTP 200
- 실물 검증 · **응답 대기 경로** · `publish_servo_alarm_policy`가 실행 컨텍스트
  적용 중 `wait_for_jog_result`를 타고 성공했다 (`motor_runtime: True` ·
  safety `동작 가능`)
- 실물 미검증 · 조그·절대 이동·서보 제어 · **모터를 실제로 움직이는 명령이라
  별도 지시 없이 돌리지 않았다**

### 6-22. `MotorRuntimeService` 신설 · 서비스 간 공유 협력자

`MotionWebBridge` 2,902 → **2,452줄** · 메서드 120 → 111 · 락 11 → 9 ·
락 관여 2,273 → **1,871줄**

#### 왜 만들었나 · 두 서비스가 노드를 거쳐 같은 것을 쓰고 있었다

`ScanOrchestrator`(§6-18)와 `MotorConfigService`(§6-19)가 각각
`self.bridge._wait_for_motor_runtime_recovery` · `self.bridge._ethercat_scan_safety_blocker` ·
`self.bridge._run_managed_user_service` 를 부르고 있었다.

서비스가 **노드를 우편함처럼 써서** 서로의 필요를 충족하는 모양이다. §6-19·§6-20에서
`load_motor_config`·`context_id`를 콜러블로 넘긴 것과 같은 문제인데, 여기서는
대상이 하나가 아니라 다섯이라 **객체로 묶어 넘기는 편이 맞았다.**

이제 둘 다 생성자에서 `runtime=`으로 받는다 · `self.runtime.___`.

#### 옮긴 것 · 9메서드 434줄

| 메서드 | 줄 | 서비스 이름 |
|---|---|---|
| `_reconcile_motor_operation_status` | 151 | `reconcile_operation_status` |
| `_wait_for_motor_runtime_recovery` | 69 | `wait_for_runtime_recovery` |
| `_ethercat_scan_safety_blocker` | 68 | `ethercat_scan_safety_blocker` |
| `_recover_interrupted_scan` | 55 | `recover_interrupted_scan` |
| `_motor_operation_reconcile_callback` | 29 | `reconcile_callback` |
| `_schedule_interrupted_scan_recovery` | 28 | `schedule_interrupted_scan_recovery` |
| `_run_managed_user_service` | 13 | `run_managed_service` |
| `_motor_restart_lifecycle` | 12 | `restart_lifecycle` |
| `_managed_user_service_active` | 9 | `managed_service_active` |

서비스가 갖는 것 · 복구 락 · 조정 락 · `MotorRestartCoordinator`.

#### 테스트 monkeypatch 대상도 따라 옮겼다

`motion_web_bridge.bridge_node.subprocess.Popen`을 패치하던 곳이 4군데 있었다.
`subprocess` 호출이 `motor_config_service`로 옮겨갔으므로 패치 대상도 옮겼다 ·
**모듈 경로를 문자열로 쓰는 패치는 코드 이동 때 조용히 어긋난다.**

작업 중 정규식이 `motion_web_bridge.motor_restart_coordinator`라는 **모듈 경로까지
치환**해 두 파일이 깨졌다. 구문 검사로 즉시 잡아 되돌렸다 · 이름 치환은
`bridge.___` 앞에 무엇이 붙어 있는지 봐야 한다.

#### 검증

- 코드 검증 · `ruff check src` 55건 유지 · 신규 0건
- 실행 검증 · `pytest` 1,005건 통과 · 실패 0
- 실물 검증 · `colcon build` · 재시작 · 실행 컨텍스트 `ready` · 노드 확인 8건 성공 ·
  엔드포인트 10종 HTTP 200
- 실물 검증 · `reconcile_callback`(0.2초 주기 타이머)과 `reconcile_operation_status`가
  서비스 안에서 돌며 직전 스캔 결과를 보고한다 · `motor_operation: partial` ·
  `모터 검색 부분 완료 · AC Servo 1축`
- 실물 검증 · `managed_service_active` · `service_management.motor_managed: True`
- 실물 미검증 · `recover_interrupted_scan` · 스캔이 중단된 상태를 만들어야 한다
- 실물 미검증 · `ethercat_scan_safety_blocker`의 차단 분기 · 축이 움직이는 중에
  스캔을 걸어야 한다

### 6-23. `ProjectService` 신설 · **4단계 완료 (기준 A)**

`MotionWebBridge` 2,452 → **2,037줄** · 메서드 111 → 83 · 락 관여 1,871 → **1,537줄**

§5가 적어둔 서비스 6개가 모두 섰다 · **4단계 완료 기준 A 충족.**

#### 옮긴 것 · 28메서드 397줄

프로젝트 생성·전환·삭제 · 프로젝트 파일 조작(불러오기·저장·이름변경·복사·가져오기·
활성화·삭제·편집열기) · 선택 프로젝트 판정(`change_blocker` · `payload_matches_selected` ·
`runtime_project_id` · `selected_owns_runtime` 등).

#### 노드에 남긴 것 · 세대 번호

`_current_project_generation`(외부 24곳) · `_advance_project_generation` ·
`_ensure_project_mutation_allowed`. §6-20에서 정한 대로 **세대는 노드 전역 개념**이다.

#### 서비스 간 참조를 또 인자로

`MotorConfigService` · `MotorRuntimeService` · `ExecutionContextService` ·
`ScanOrchestrator` · `MotionStudioRosBridge` 다섯이 노드를 거쳐 프로젝트 판정을
쓰고 있었다. `project=`으로 직접 받게 했다 · §6-22와 같은 규칙.

`ProjectService`는 반대 방향(설정·스캔·로그 서비스)을 **노드를 통해** 본다.
그쪽은 자기보다 늦게 만들어지기 때문이다 · 늦게 묶이는 협력자는 노드가 중개한다.

#### 이동 중 잡은 버그 1건 · 같은 종류가 반복됐다

`getattr(self, 'project_repository', None)` **문자열 형태**가 치환되지 않아
`change_blocker`가 저장소를 못 찾고 항상 빈 문자열을 돌려줬다 · 프로젝트 변경
차단이 통째로 무력화될 뻔했다. 테스트가 잡았다.

§6-19의 `session_of(self)`와 같은 종류다. 이름 치환은 세 형태를 모두 봐야 한다.

```
self.___              ← 속성 접근
getattr(self, '___')  ← 문자열 접근   ← 두 번 놓쳤다
f(self)               ← self 통째로 넘기기
```

`scripts/bridge_state_map.py`가 이 셋을 모두 세도록 만들어 둔 이유가 이것이다.

#### 검증

- 코드 검증 · `ruff check src` 55건 유지 · 신규 0건
- 실행 검증 · `pytest` 1,005건 통과 · 실패 0
- 실물 검증 · `colcon build` · 재시작 · 실행 컨텍스트 `ready` · 노드 확인 8건 성공 ·
  엔드포인트 10종 HTTP 200
- 실물 검증 · `list_projects` · 프로젝트 6개 · 선택 프로젝트 `연동2` 표시
- 실물 검증 · `project_scope` · `selected_project_id` = `runtime_project_id` ·
  `runtime_matches_selected: true` · `runtime_project_id`·`selected_owns_runtime`
  판정이 서비스 안에서 동작한다
- 실물 미검증 · 프로젝트 전환·생성·삭제 · 가동 중 프로젝트를 바꿔야 한다
- 실물 미검증 · 프로젝트 파일 저장·이름변경·복사·가져오기·삭제

### 6-24. 영속 계층 통합 · 5단계 · 직접 기록 0 · 다중 writer 락

§3-4가 지적한 세 가지를 모두 처리했다.

#### ① 직접 기록 모듈 · 프로덕션 잔여 **0**

| 모듈 | 이전 | 이후 |
|---|---|---|
| `motion_automation_store` | 자체 임시파일+`replace` (fsync 없음) | `store.atomic_write_json` |
| `project_repository._atomic_write` | 자체 구현 | `store.atomic_write_text` + 락 |
| `motor_config_service._write` | `target.write_text` · **원자성 없음** | `store.atomic_write_text` |
| `motor_event_log.prune` | `write_bytes` | `store.atomic_write_text` |
| `motor_config_rules` 선택 파일 | `write_text` | `store.atomic_write_text` |
| `midi_bank_store` 백업 | `write_text` | `store.atomic_write_text` |
| `desktop_shortcut` | `NamedTemporaryFile`+`chmod`+`replace` 15줄 | `store.atomic_write_text(mode=0o755)` |

**모터축 설정 저장이 원자적이지 않았다.** `target.write_text`는 기록 도중 죽으면
반쪽 파일을 남긴다 · 모터 설정이 그렇게 깨지면 다음 기동이 실패한다.

#### ② 파일락을 재진입 가능하게 · 이것이 전제였다

`flock`은 **파일 서술자 단위**다. 같은 프로세스가 다른 서술자로 다시 잠그면
자기 자신을 기다리며 멈춘다. 저장 API가 서로를 감싸는 구조(예: `save_midi_banks`
안에서 `atomic_write_with_backup`)에서 실제로 걸린다.

`store.file_lock`을 고쳤다.

- 경로마다 프로세스 안 `threading.RLock` · 같은 스레드는 재진입, 다른 스레드는 대기
- 이미 `flock`을 잡은 경로면 다시 걸지 않는다 · 스레드별 깊이 계수
- 프로세스 사이는 여전히 `flock`이 막는다

검증 3건 추가 · 중첩 진입 · 다른 스레드 직렬화 · 4스레드 × 25회 증가에서
갱신 손실 0(`test_store.py`).

#### ③ 다중 writer에 락을 걸었다

모션축 설정 파일(`motion_axis_matching`)을 **두 프로세스가 쓴다** ·
`project_repository`(web_bridge) ↔ `motion_mapping_manager`(motion_runtime).

원자적 기록만으로는 찢긴 읽기만 막는다. 각자 읽고 각자 쓰면 나중 기록이 앞선
수정을 지운다. 읽기-수정-기록 구간을 감쌌다.

- `project_repository._atomic_write` · `locked_update`
- `midi_bank_store.atomic_write_with_backup` · `locked_update`
- `midi_bank_store.save_midi_banks` · 읽기부터 감싼다 (중첩 · 재진입 필요)
- `motion_mapping_manager._save_mapping` · MIDI 구간 병합부터 감싼다

#### 락 파일을 숨김 이름으로 바꿨다 · 규약 변경

`<이름>.lock` → **`.<이름>.lock`**

락 파일은 프로젝트 데이터 디렉터리 안에 생긴다. 프로젝트 파일 전체에 락을 걸자
`layers/`·`motions/`에 락 파일이 쌓였고, **활성 파일 판정이 `.hello.json.lock`을
사용자 파일로 골랐다.** 테스트가 잡았다.

두 가지를 함께 고쳤다.

- 락 파일을 숨김 이름으로
- `project_repository`의 파일 열거 4곳에 `_is_user_file()` 적용 ·
  숨김 파일과 심볼릭 링크 제외

기존 `schedule_store.json.lock`은 고아가 된다 · 무해하며 지워도 된다.

#### 손으로 만든 재진입 락도 흡수했다

`project_repository._motor_runtime_locked`가 **재진입 `flock`을 직접 구현**하고
있었다 · 스레드 지역 깊이 계수까지 손으로 셌다. 공용 API가 같은 일을 하게 됐으므로
20줄을 지우고 `store.file_lock(self.motor_runtime_file)` 한 줄로 바꿨다.

락 파일도 규약에 맞춰 옮겨졌다 · `.motor_runtime.lock` → `..motor_runtime.json.lock`.
구 파일은 재시작 후 지웠다.

#### 확인 도구 · `scripts/check_locks.sh`

락 파일이 **있다**는 것과 **지금 잠겨 있다**는 것은 다르다. 파일은 한 번 쓰면
계속 남고, 잠금 여부는 커널만 안다(`/proc/locks`). 스크립트가 둘을 갈라 보여준다.

```bash
bash scripts/check_locks.sh          # 락 파일 목록 + 현재 점유
bash scripts/check_locks.sh --held   # 지금 잡혀 있는 것만
bash scripts/check_locks.sh --stale  # 대상 파일이 없는 잔재
```

#### 남은 것

`check_and_reload()` mtime 폴링은 그대로다. 웹이 바꾼 것을 노드가 알아채는
수단이 폴링뿐이기 때문이다 · 알림 채널은 6단계(Action 전환)에서 함께 볼 일이다.

#### 검증

- 코드 검증 · `ruff check src` 55건 유지 · 신규 0건
- 실행 검증 · `pytest` **1,008건 통과** · 실패 0 · 락 검증 3건 신규
- 실물 검증 · `./scripts/build_and_restart.sh` 31패키지 전체 빌드 · 두 서비스 재시작 ·
  실행 컨텍스트 `ready` · 노드 확인 8건 성공 · 모터 1축 온라인
- 실물 검증 · **락 파일이 어디에도 노출되지 않는다**

```
active_files   motor_axes.yaml · ㄴㅇㄹ.yaml · ㄴㅇㄹ.json · …__layer_….json
motion-files   ['ㄴㅇㄹ.json']
motion-mappings ['ㄴㅇㄹ']
디스크          .schedule_store.json.lock (신규 · 숨김)
               schedule_store.json.lock  (구 규약 · 고아 · 0바이트)
```

- 실물 미검증 · 두 프로세스 동시 기록 경합 · 웹과 매핑 관리자가 같은 파일을
  같은 순간에 저장해야 한다 · 단위 테스트로는 4스레드 경합까지 확인했다

### 6-25. `motion_run_manager` 분해 착수 · 순수 규칙 추출

`MotionRunManager` 3,693 → **3,234줄** · 메서드 120 → 82 · 상태 무의존 483 → 84줄

§5 분해 목표안에서 `bridge_node` 다음으로 적혀 있던 노드다. 지금 워크스페이스에서
가장 큰 파일이었다. `bridge_node`에 쓴 순서를 그대로 적용했다.

#### ① 죽은/위임 껍데기 11개 제거

`motion_common`으로 그냥 넘기기만 하던 것들이다 · §6-9가 `bridge_node`에서 지운
것과 같은 패턴.

| 상태 | 메서드 |
|---|---|
| **호출 0** · 죽은 껍데기 | `_column_key` · `_column_value` · `_header_map` · `_header_has_required` · `_parse_header_line` · `_extract_motion_rows_from_text` |
| 호출부 갱신 후 제거 | `_finite_float`(23곳) · `_optional_int`(12곳) · `_expand_pair_rows` · `_parse_text_row` · `_parse_motion_row` |

이름 충돌 하나를 만났다. `values.finite_float(...)`로 바꾸자 **지역 변수 `values`가
모듈을 가렸다** · `_publish_motion_values(self, values)`의 인자다.
`from motion_common.values import finite_float, optional_int`로 이름을 직접 들여와
피했다. 테스트가 잡았다.

#### ② 순수 규칙 27개 → `motion_run_rules` 신설

실행 상태 초안 · 모터 참조 해석 · 목표값 판정 · 보간과 클램프 · 재생 주기 계산 ·
420줄.

#### 파일 입출력은 옮기지 않았다

`_load_motion_records`(64줄) · `_load_mapping`(5줄)은 한 번 옮겼다가 **되돌렸다.**
파일을 열어 읽는 일이고, 규칙 모듈을 I/O 없는 채로 두는 편이 낫다 ·
`test_pure_modules`가 `motion_web_bridge`에 요구하는 성질과 같은 기준이다.

되돌린 덕에 이 둘을 스텁으로 쓰던 시험 19곳도 그대로 남았다.

#### 테스트 이음매 30곳 이동

`manager._motor_type = ...` 처럼 인스턴스에 꽂던 것을 `_patch_rule('_motor_type', ...)`로
바꿨다 · `mock.patch.object` + autouse 픽스처가 테스트마다 되돌린다 · §6-13과 같다.

이음매가 옮겨가자 `manager` 인스턴스를 만들 이유가 없어진 시험이 6개 나왔다 ·
그 생성도 지웠다.

#### 검증

- 코드 검증 · `ruff check src` 55건 유지 · 신규 0건
- 실행 검증 · `pytest` 1,008건 통과 · 실패 0
- 실물 검증 · 아래 별도 기록

## 7. 유지보수 지표 · 신규 코드 규칙안

- 파일 1,000줄 이하 · 함수 60줄 이하 · `Node` 서브클래스 500줄 이하
- 신규 요청·응답은 `RequestChannel` 또는 srv/action만 사용 · String+JSON 신규 추가 금지
- 프로젝트 파일 기록은 단일 저장 API만 허용
- 패키지 간 Python 직접 import 금지 · 경계는 토픽·서비스 또는 `motion_common`

## 8. 검증 상태

- 최종 갱신 · 2026-09-09
- 실기 · joonhoTest 가동 중 · AC Servo 1축(alias 103) · 다른 PC 전원 차단

### 검토 자체(§1~§5)

- 코드 검증 · 완료 · 파일·함수 규모 · 중복 위치 · 의존 방향 · 토픽·파라미터 정의 지점
- 미확인 · 런타임 성능 영향(폴링 대기의 워커 점유량) · 다중 writer 실제 충돌 빈도

### §6 반영분

| 구분 | 상태 |
|---|---|
| 빌드 검증 | 완료 · `colcon build --symlink-install` 31패키지 |
| 정적 검증 | 완료 · `ruff check src` **55건 유지** · 이번 작업 신규 0건 |
| 실행 검증 | 완료 · `pytest` **1,005건 통과** · 실패 0 |
| 데이터 검증 | 완료 · 이동 전후 동치(§6-11) · 모션 파일 78개 전수(§6-2) |
| 실물 검증 | 부분 · 아래 표 |

### 실물 검증 · 통과

| 항목 | 절 |
|---|---|
| 서비스 재시작 8회 · 실행 컨텍스트 자동 적용 · 노드 확인 8건 | §6-20·21·22 |
| AC Servo 물리 스캔 2회 · 모터 서비스 정지·복구 · EtherCAT 재열거 | §6-13·18 |
| 프로젝트 EtherCAT 구성 판정 · 미사용 Master 미연결 허용 | §6-13 |
| 모션 스튜디오 레이어 165프레임 · 라우트→서비스→세션 전 경로 | §6-15 |
| 모터 동작 로그 조회 · 보존 정책 · 프로젝트 로그 경로 | §6-17 |
| 설정 `selected`·`applied` 경로 · 실행 세션 일치 | §6-19 |
| 조작 상태 조정 타이머 · systemd 서비스 판정 | §6-22 |
| 엔드포인트 10종 HTTP 200 | 전반 |

### 실물 미검증 · 남은 것

| 항목 | 사유 |
|---|---|
| Dynamixel 스캔 · **40초 제한시간**(§6-16 결함 수정) | 직렬 포트 부재 |
| MIDI 화면 · 뱅크·페이더·매핑 | 컨트롤러 미연결 |
| 다중 PC · 연동 스케줄 · 마스터 판정 | 다른 PC 전원 차단 |
| 조그·절대 이동·서보 제어 | 모터가 실제로 움직인다 |
| 설정 저장·적용·재시작·실행 해제 | 모터 설정을 다시 쓴다 |
| 프로젝트 전환 `clear_selection` 격리 계약 | 가동 중 프로젝트를 바꿔야 한다 |
| 스튜디오 녹화·재생·레이어 편집 | 화면 조작 필요 |
| `recover_interrupted_scan` · 안전 차단 분기 | 중단·이동 상태를 만들어야 한다 |

### 배포 잔여

**다른 PC 2대는 `colcon build` 필수** · `motion_common` 외 신규 패키지 다수 ·
미빌드 시 `ModuleNotFoundError`로 노드 기동 실패 · `docs/HANDOFF.md` §3-①

### 미해결 결함

`motion_supervisor` 수신 정지 · §6-14 · 근본 원인 미특정 · 재발 판별법만 기록
