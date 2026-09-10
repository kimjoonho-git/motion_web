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
| 6 | 장기작업 Action 전환 · 스캔·초기화·모션 실행 | 중간 | **완료** · 스캔 전환(§6-26) · 초기화·모션 실행은 전환하지 않기로 결정(§6-28) |
| 7 | 프런트엔드 빌드 도입(해시 파일명) · CSS·HTML 분할 | 중간 | **완료**(§6-42~§6-44) · 캐시 재검증 · 재방문 전송 1.25MB → 0B · CSS 7,415줄 → 11조각 · `index.html` 2,004줄 → 셸 51줄 + 조각 11개 · 해시 파일명·번들러는 택하지 않음(사유 §6-42) |
| 8 | 하드웨어 스캐너 분리 · `motion_system` 범위 협의 후 | 높음 | **접음 · 결정 A**(§6-45) · 이동이 아니라 재구현이고 대상이 20커밋 뒤처진 별도 저장소다 · 현행 구조는 실물 검증 통과 · 재검토 조건 기록 |

분해 목표안

**4단계 완료 기준 · A** (2026-09-09 확정)

아래 서비스 목록을 다 세우면 4단계를 완료로 본다. `Node` 서브클래스 500줄 이하
(§7 규칙)는 **별도 항목**으로 분리한다 · 거기까지 가려면 콜백 계층과 상태 취합까지
손봐야 하고, 그것은 "신 노드 해소"와 성격이 다른 작업이다.

- 미달 잔여는 §7 지표로 계속 추적한다 · 현재 `MotionWebBridge` 2,141줄

- `bridge_node` → `ExecutionContextService` · `MotorConfigService` · `ScanOrchestrator` · `MotorEventLog` · `MotionFileService` · `ProjectService`
- `motion_run_manager` → `PlanBuilder` · `MotionPlayer` · `GroupSession` · `StatusStore`
- `midi_control_node` → `MidiDecoder` · `FaderStateMachine` · `PickupPolicy` · `MotionValueMapper` — **넷 중 셋 완료**(§6-38~§6-41) · 3,354 → 2,397줄 · `MidiDecoder` 잔여(루프 분해 선행 필요)
- `monitor_node` → `DynamixelScanner` · `EthercatScanner` · `StatePublisher` — **완료**(§6-32~§6-36) · 2,884 → 866줄

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

#### 후속 분석 · 2026-09-09 · **구조 가설은 틀렸다**

위에 적은 "기본 콜백 그룹이 막혔다"는 **코드가 반증한다.**

| 등록 | 콜백 그룹 |
| --- | --- |
| `_motion_state_callback` · `_jog_request_callback` · `_action_request_callback` · `_motion_run_command_callback` · `_midi_position_request_callback` | **기본** |
| `_safety_request_callback` | `_safety_callback_group` |
| **`_publish_safety_status` 타이머** | **기본** |

두 가지가 동시에 성립할 수 없다.

1. **타이머가 기본 그룹에 있다.** 기본 그룹이 막혔다면 `safety_status` 2 Hz
   발행도 함께 멈췄어야 한다 · 멈추지 않았다.
2. **`safety_request`는 별도 그룹인데도 무응답이었다.** 기본 그룹이 막힌 것으로는
   설명되지 않는다.

`MutuallyExclusive`는 같은 그룹의 콜백을 하나씩만 돌린다. 타이머가 계속 돌았다는
것은 **그 그룹에 멈춘 콜백이 없었다**는 뜻이다.

**따라서 콜백 그룹 봉쇄가 아니다.** 발신은 전부 살아 있고 수신만 전부 죽었다 ·
콜백 층이 아니라 **참가자의 수신 쪽**이 죽은 모양이다.

#### 새 가설 · Fast DDS 공유메모리 포트 재사용

근거로 삼은 관측이다.

```
RMW            기본값 rmw_fastrtps_cpp · ROS_LOCALHOST_ONLY=1
/dev/shm       fastrtps 세그먼트 26개 중 고아 5개
고아 예        fastrtps_port7435   2026-09-08 15:28   ← 사건(15:47) 19분 전
               fastrtps_port7411   2026-09-08 15:17
               fastrtps_port7413   2026-09-08 15:17
```

Fast DDS의 SHM 전송은 참가자마다 `/dev/shm/fastrtps_portNNNN` 하나를 **수신
창구**로 연다. 포트 번호는 도메인과 참가자 번호로 정해지므로, 프로세스가 깨끗하지
않게 죽으면 세그먼트가 남고 **다음 참가자가 같은 번호를 잡아 낡은 세그먼트에
붙는다.**

이 상태의 증상이 관측된 것과 일치한다.

| 관측 | 이 가설의 설명 |
| --- | --- |
| 발신 정상 | 쓰기는 **상대방** 창구에 밀어 넣는다 · 자기 창구와 무관 |
| 수신 전무 | 자기 창구가 낡은 세그먼트다 |
| 두 콜백 그룹 모두 죽음 | 창구는 참가자당 하나다 · 그룹과 무관 |
| 타이머 정상 | 실행기는 멀쩡했다 |
| 재시작 1회로 해소 | 새 참가자가 다른 번호를 잡는다 |

**아직 가설이다.** 재현하지 못했고 Fast DDS 경고 로그도 남아 있지 않다
(`~/.ros/log`·journal 모두 무소득). 다만 **앞선 구조 가설과 달리 관측과
모순되지 않는다.**

#### 가설을 약화시키는 관측 · 같은 날 추가

노드별로 SHM 세그먼트를 실제로 매핑하고 있는지 셌다(`/proc/PID/maps`).

```
SHM 매핑 있음   motor_manager(84) · midi_input_bridge(84)
                midi_control_node(74) · motion_coordination(49)
SHM 매핑 0      motion_supervisor · motion_state_monitor
                motion_run_manager · motion_mapping_manager
                motion_studio · motion_studio_editor
                motion_schedule · motion_web_bridge
```

**`motion_supervisor`는 지금 SHM을 하나도 매핑하고 있지 않다.** 언어 차이도
아니다 · `midi_control_node`도 파이썬인데 74개를 매핑한다. 같은 기동 배치에서
갈렸다.

지금 상태만 보면 이 노드는 SHM 경로를 타지 않는다 · **사건 당시에도 그랬는지는
알 수 없다.** 가설의 전제가 현재는 성립하지 않는다는 뜻이고, 그만큼 약해진다.

#### 재발하지 않고 있다

| 항목 | 값 |
| --- | --- |
| 발생 | 1회 · 2026-09-08 15:47 |
| 이후 `motion-control` 재시작 | **26회**(2026-09-09 하루) |
| 재발 | **0회** |
| `waiting_motor_runtime` 실패 로그 | **0건** |

#### 판별 도구 · `scripts/check_dds_shm.sh`

읽기만 한다 · 고아 세그먼트를 시각과 함께 보여준다.

```bash
./scripts/check_dds_shm.sh          # 고아만
./scripts/check_dds_shm.sh --all    # 사용 중인 것까지
```

**고아가 있다고 곧바로 결함은 아니다.** 아래와 함께일 때만 의심한다 ·
`safety_status`는 2 Hz로 나오는데 어떤 구독도 응답하지 않는다 ·
`POST /api/execution-context/apply`가 `waiting_motor_runtime`으로 실패한다.

정리는 **모든 ROS 서비스를 내린 뒤**에만 한다 · 살아 있는 노드가 쓰는 세그먼트를
지우면 통신이 끊긴다.

#### 다음 조치 후보 · 갱신

| 조치 | 성격 | 판단 |
| --- | --- | --- |
| 기동 시 고아 세그먼트 정리 | 서비스 스크립트 | 다른 서비스가 떠 있을 때의 경합을 먼저 정리해야 한다 |
| SHM 전송 끄기(UDP 루프백만) | Fast DDS 프로필 XML | **지금은 하지 않는다** · 아래 |
| 수신 기아 감시 | `motion_supervisor` 코드 | 발행 N회 동안 수신 0이면 로그 · 안전 노드라 신중히 |
| 콜백 그룹 분리 | — | **철회** · 원인이 아니다 |

#### 판단 · 전송 방식은 바꾸지 않는다

바꾸면 **모든 노드의 통신 경로**가 달라진다. 그 대가로 막으려는 것은 ·
1회 발생 · 재시작 26회 동안 재발 0 · 원인 미확정 · 그 가설조차 현재 관측과
어긋난다(`motion_supervisor` SHM 매핑 0).

**잘 도는 시스템을, 확인되지 않은 원인을 위해, 전면적으로 바꾸는 거래다.**
지금은 값이 맞지 않는다.

대신 **재발했을 때 증거를 남기는 쪽**에 든다.

| 준비 | 비용 | 얻는 것 |
| --- | --- | --- |
| `check_dds_shm.sh` 즉시 실행 | 없음 · 이미 있음 | 고아 목록과 시각 |
| 노드별 SHM 매핑 수 기록 | 없음 · 위 명령 | 사건 당시 SHM을 탔는지 |
| ~~Fast DDS 경고 로그 켜기~~ | — | **불가** · 아래 |

**Fast DDS 2.6.11에는 로그 수준 환경변수가 없다.** `Log::SetVerbosity`는 C++
API 전용이고, XML 프로필의 `<log>`는 소비자(consumer)만 정한다 ·
`librmw_fastrtps_shared_cpp`가 읽는 환경변수도 `RMW_FASTRTPS_PUBLICATION_MODE` ·
`RMW_FASTRTPS_USE_QOS_FROM_XML` 둘뿐이다.

그래서 같은 목적을 **한 번에 뜨는 명령**으로 대신한다.

```bash
./scripts/check_dds_shm.sh --capture
```

`docs/metrics/dds-capture-<시각>.txt`로 남긴다 · 담기는 것 ·

- 세그먼트 전체와 고아 여부 · 각 시각
- **노드별 SHM 매핑 수** · 0이면 그 노드는 SHM 경로를 타지 않는다
- 프로세스 상태와 스레드 수
- 브리지의 `project_generation` · `motion_state_age_sec` · `safety_status` ·
  `execution_context`

재발 시 **재시작하기 전에 이 한 줄을 먼저 실행한다** · 지난번에는 재시작이
증거를 함께 지웠다.

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
- 실물 검증 · `colcon build` · 재시작 · 실행 컨텍스트 `ready` · 노드 확인 8건 성공
- 실물 검증 · `GET /api/motion-run/status` · `_empty_status`가 만드는 31개 필드가
  그대로 나온다 · `state: idle` · `run_mode: once` · `repeat_mode: direct`
- 실물 검증 · `POST /api/motion-run/check` · **계획 수립 경로 전체 통과** ·
  실가동 모션 파일 `ㄴㅇㄹ.json` + 매핑 `ㄴㅇㄹ.yaml`

```
axis_count 1 · duration_sec 3.26 · period_sec 0.02 · sample_count 164
initial_move_time_sec None · continuous_available False · clamped_axis_count 0
```

모터 참조 해석 · 목표 범위 판정 · 보간 · 클램프 · 연속 재생 가능 판정이
모두 새 모듈에서 돌았다 · 모터를 움직이지 않는 검사 경로다.

`continuous_available False`는 이 모션 파일의 시작·끝 값이 이어지지 않아
연속 재생을 못 한다는 판정이고(`_continuous_capability`), `initial_move_time_sec`가
`None`인 것은 요청에 재정의가 없었다는 뜻이다 · 둘 다 정상 결과다.

- 실물 미검증 · 실제 모션 재생 · 모터가 움직인다

### 6-26. 6단계 착수 · 모터 검색 Action 전환

§5 6단계(장기작업 Action 전환) 셋 중 **스캔**을 먼저 옮겼다.

#### 무엇이 문제였나

스캔은 `std_srvs/Trigger` 서비스였다.

- 결과만 돌려준다 · 진행 상황은 **별도 토픽**(`scan_progress`)으로 흘렀다
- 취소 수단이 없다
- 호출 측이 응답까지 워커 스레드를 붙잡는다 · §3-2가 지적한 그 문제

#### 무엇을 했나

`motion_coordination_interfaces/action/MotorScan.action` 신설.

```
# 목표
string transport            # all | ac_servo | dynamixel
---
# 결과
bool success · string message(스캔 JSON) · bool cancelled
---
# 진행
string scan_id · phase · transport · message · details · float64 timestamp
```

진행 항목은 **기존 토픽 이벤트와 같은 형태**로 맞췄다. 서버는 같은 이벤트를
토픽과 Action 양쪽으로 보낸다 · 화면과 구코드 호출자가 아직 토픽을 본다.

| 층 | 변경 |
|---|---|
| `monitor_node` | `ActionServer('motor_scan')` · `ReentrantCallbackGroup` · `MultiThreadedExecutor(2)` |
| `scan_orchestrator` | `ActionClient` · feedback → 진행 상태 · 취소 API |
| 라우트 | `POST /api/motors/scan/cancel` 신설 |

`Trigger` 서비스는 **그대로 남겼다.** Action 서버가 없으면 그쪽으로 되돌아간다 ·
구버전 노드가 떠 있는 동안에도 검색이 멈추면 안 된다.

노드 실행기를 `MultiThreadedExecutor(2)`로 바꿨다. 예전에는 스캔 3초 동안 모니터
노드 전체가 멈췄다 · 이제 상태 발행과 취소 요청을 그동안에도 받는다.

#### 취소는 어디까지 듣는가 · 정직하게

**진행 중인 물리 검색은 끊지 않는다.** `ethercat rescan`과 Dynamixel Ping은
시작하면 끝까지 간다 · 모터 스캔 영구 불변조건이 물리 검색을 반쪽으로 만드는 것을
허락하지 않는다.

취소는 **장치 종류 사이**에서 확인한다.

```
전체 검색 중 취소 → EtherCAT은 끝까지 → Dynamixel은 시작하지 않음
                  → 결과에 cancelled: true · dynamixel_scan.skipped: true
```

취소 API의 응답 문구도 그렇게 적었다 ·
`모터 검색 취소를 요청했습니다 · 진행 중인 장치 검색은 끝난 뒤 중단됩니다`.

#### 검증

- 코드 검증 · `ruff check src` 55건 유지 · 신규 0건
- 실행 검증 · `pytest` **1,012건 통과** · 실패 0 · Action 계약 3건 + 중복 제거 1건 신규
  (장치 종류 사이 취소 · 취소 없을 때 둘 다 실행 · 종류 표 완전성)
- 실물 검증 · 재시작 후 `motion_state_monitor` 정상 기동 · AC Servo 물리 스캔 2회
- 실물 검증 · **Action 경로로 돌았다** · 브리지 로그에 `Trigger 서비스로 진행` 0건
- 실물 검증 · 진행 이벤트 8건이 Action feedback으로 순서대로 도착

```
0 started            EtherCAT 직접 스캔을 시작합니다
1 ethercat_preflight EtherCAT Slave 운전 상태를 확인합니다
2 ethercat_rescan    기존 Slave 정보를 폐기하고 물리 EtherCAT 버스를 재열거합니다
3 ethercat_rescan_done  ethercat rescan 명령 실제 실행 완료 (3.385ms)
4 ethercat_topology  새로 열거된 EtherCAT Slave 1개를 확인했습니다
5 ethercat_slave_read   Master 0 · Slave 0: SII EEPROM과 Alias 레지스터를 읽습니다
6 ethercat_slave_done   Master 0 · Slave 0: Alias 103, Serial 402982152 읽기
7 failed             Master 1: 재스캔 후 응답한 Slave가 없습니다
```

`rescan_performed: true` · 모터 서비스 일시 정지·복구 정상 · `cancelled: false`

#### 첫 검증에서 잡은 결함 · 진행 이벤트가 2배로 쌓였다

브리지는 진행 토픽을 구독하면서 Action feedback도 받는다. 서버가 **같은 이벤트를
양쪽으로** 보내므로 진행 목록이 두 배가 됐다 · 첫 스캔에서 16건이 나왔다.

`(scan_id, phase, transport, timestamp)`로 한 번만 세도록 고쳤다 · 재스캔에서
8건으로 정상. 검증 1건 추가.

토픽을 없애지 않은 이유는 §6-26 본문 그대로다 · 되돌림 경로가 그것을 쓴다.

- 실물 미검증 · **취소** · 스캔이 3초에 끝나 장치 종류 사이를 노려 취소하기 어렵고,
  Dynamixel 장치가 없어 건너뛸 대상 자체가 없다 · 단위 시험으로만 확인했다

### 6-27. 사용자 간이 검증 · 2026-09-09

§6-15 ~ §6-26에서 **미검증으로 남겨둔 모터 조작 경로**를 사용자가 화면에서 직접
확인했다 · "문제 없어 보인다".

| 항목 | 관련 절 |
|---|---|
| 조그 · 절대 이동 · 서보 제어 | §6-21 |
| 모터 설정 저장·적용·재시작 · 실행 해제 | §6-19 |
| 프로젝트 전환 격리 | §6-19 |
| 모션 재생 | §6-25 |

모션 재생은 이벤트 로그에도 남았다 · 2026-09-09 08:57 · 09:25 · 11:14 3회 ·
`초기 위치 이동 시작 → 완료 → 1회 모션 시작`. 재생 후 축 위치 `-124.683°`가
모션 파일의 마지막 값 `-124.68558`과 일치한다 · 끝까지 돌았다는 뜻이다.
오늘 오류 이벤트 0건.

**검증 등급 구분** · 이것은 `실물 검증`이되 **작업자 간이 확인**이다. 각 절이
적어둔 자동화된 확인(엔드포인트 응답·이벤트 순서·상태 필드 대조)과 달리
화면에서 눈으로 본 것이다. 회귀가 의심되면 절별 확인 절차를 다시 밟아야 한다.

여전히 미검증

| 항목 | 사유 |
|---|---|
| Dynamixel 스캔 · 40초 제한시간(§6-16) | 직렬 포트 부재 |
| MIDI 화면 | 컨트롤러 미연결 |
| 스캔 취소 실물(§6-26) | 스캔 3초 · 건너뛸 Dynamixel 없음 |
| 다중 PC · 연동 스케줄 | 다른 PC 재빌드·기동 필요 |

### 6-28. 6단계 재검토 · 모션 실행은 Action이 풀 문제가 없다

§5 6단계는 "스캔·초기화·모션 실행"을 Action으로 옮기라고 적었다. 스캔은 옮겼다
(§6-26). 나머지 둘을 보니 **전환할 이유가 없다.**

#### §3-2가 지적한 세 가지가 모션 실행에는 해당하지 않는다

| 문제 | 스캔(전환 전) | 모션 실행(현재) |
|---|---|---|
| 호출 측 워커 스레드 점유 | 응답까지 3~40초 붙잡음 | **없음** · `start`는 스레드를 띄우고 바로 돌아온다 · 브리지 대기 2초 |
| 진행 상황 없음 | 별도 토픽으로만 | **있음** · `status_topic`으로 상태를 계속 발행 |
| 취소 없음 | 없었다 | **있음** · `stop` · `stop_after_cycle` 두 가지 |

`initialize`도 같은 `_start_thread` 경로다.

#### 그래도 Action이 주는 것

목표·결과 상관관계를 프로토콜이 보장하고, 취소가 표준 경로가 된다. 지금은
`request_id`와 상태 폴링으로 맞추고 취소는 별도 명령이다.

**얻는 것보다 위험이 크다.** 모션 실행은 모터를 실제로 움직이는 경로이고,
사용자가 방금 재생을 확인했다(§6-27). 이미 비동기·진행 보고·취소를 갖춘 코드를
프로토콜만 바꾸려고 건드리는 것은 근거가 약하다.

#### 결론

6단계를 **스캔까지로 완료** 처리한다. 초기화·모션 실행 전환은 **하지 않는 것으로
결정**하고 사유를 여기 남긴다. 나중에 다음 중 하나가 생기면 다시 볼 일이다.

- 모션 실행 진행률을 화면에 실시간으로 그려야 할 때 · 지금은 상태만 보여준다
- 여러 클라이언트가 같은 실행을 동시에 지켜봐야 할 때
- 취소 응답을 요청 단위로 정확히 되돌려줘야 할 때

### 6-29. `GroupSession` 신설 · §5 분해 목표안

`MotionRunManager` 3,234 → **2,783줄** · 메서드 82 → 74 · 락 관여 2,772 → **2,360줄**

§5가 `motion_run_manager` 분해 목표로 적어둔 넷(`PlanBuilder` · `MotionPlayer` ·
`GroupSession` · `StatusStore`) 중 첫 번째다.

#### 옮긴 것 · 8메서드 435줄

`_run`(옛 `_prepare_and_run_group`) 192 · `prepare` 70 ·
`schedule_initialization` 51 · `schedule_cycle` 43 · `cancel` 30 ·
`_wait_initialization` 20 · `_finish` 17 · `_wait_cycle` 12

서비스가 갖는 것 · 세션 상태(`session`)와 그 조건변수(`condition`).

#### 조건변수는 실행 락 위에 선다

```python
self._group_condition = threading.Condition(self._run_lock)
```

**그룹 세션과 단일 실행이 같은 자원을 두고 다툰다.** 락을 나눠 가지면 그 다툼이
사라지지 않고 숨는다. 노드가 `_run_lock`을 소유하고 서비스가 그 위에 조건변수를
만든다 · §6-18에서 `lifecycle_lock`을 두 서비스가 나눠 가진 것과 같은 이유다.

#### 정지 경로를 온전히 옮겼다

`_handle_stop_after_cycle`의 그룹 분기를 서비스로 옮기면서 **세션 표시만 옮기지
않도록** 주의했다. 정지가 실제로 서려면 재생 루프가 보는 `_graceful_stop_event`도
함께 세워야 하고, 아직 움직이지 않는 중이면 `_stop_event`까지 세워야 한다.
세 가지가 한 락 안에서 같이 일어나야 한다.

`mark_stopping()` · `request_stop_after_cycle(current)` 두 개로 노드에 노출한다.

#### 검증

- 코드 검증 · `ruff check src` 55건 유지 · 신규 0건
- 실행 검증 · `pytest` 1,012건 통과 · 실패 0 · 그룹 실행 시험 그대로 통과
- 실물 검증 · `colcon build` · 재시작 · 실행 컨텍스트 `ready` · 노드 확인 8건 성공 ·
  `motion_run: idle` · `group_execution: false` · 모터 `Operation enabled` · fault 없음
- 실물 검증 · 계획 수립 재확인 · `axis_count 1` · `duration_sec 3.26` ·
  `sample_count 164` · 그룹 분리 뒤에도 단일 실행 경로가 그대로다
- 실물 검증 불가 · **그룹 실행 자체는 다른 PC가 있어야 한다** · 전원 차단

### 6-30. `PlanBuilder` 신설 · 워크스페이스 최장 함수를 집으로

`MotionRunManager` 2,783 → **2,362줄** · 메서드 74 → 73

`_build_plan` 421줄 · **워크스페이스에서 가장 긴 함수**였다. §5 분해 목표안의
`PlanBuilder`가 그 자리다.

#### 함수 자체는 아직 하나다

옮기기만 했고 **쪼개지 않았다.** 이건 의도한 것이다.

- 모터를 실제로 움직이는 계획을 만드는 코드다 · 경계를 잘못 그으면 축 목표값이 틀어진다
- 노드 안에 있으면 초점 맞춘 시험을 붙이기 어렵다 · 파일 하나로 떨어져 나와야
  거기서부터 나눌 수 있다

**먼저 집을 마련한 것**이고, 내부를 나누는 일은 별개 작업으로 남긴다.
§7 `함수 60줄 이하` 기준은 아직 이 함수로 깨져 있다.

노드에서 받는 것 · 모터 목록 · 파일 경로 해석 3종 · 파일 읽기 2종 · 제어 주기.
시험이 이 일곱을 스텁으로 갈아끼우던 이음매도 `self.manager.___`로 그대로 산다.

호출 지점 21곳을 갱신했다 · 노드 6 · `GroupSession` 3 · 시험 12.

#### 검증

- 코드 검증 · `ruff check src` 55건 유지 · 신규 0건
- 실행 검증 · `pytest` **1,013건 통과** · 실패 0 · 회귀 시험 1건 신규
- 실물 검증 · `POST /api/motion-run/check` · `axis_count 1` · `duration_sec 3.26` ·
  `sample_count 164` · `period_sec 0.02`

#### 실물 검증이 잡은 결함 · 시험은 못 잡았다

옮긴 직후 실기에서 `motion file not found: ㄴㅇㄹ.json`이 났다.

```python
if hasattr(self, 'motion_projects_dir'):   # self가 이제 PlanBuilder다
```

빌더에는 그 속성이 없으니 **항상 호환 분기로 빠져** 프로젝트 디렉터리를 무시했다.
`hasattr(self.manager, ...)`로 고쳤다.

**단위 시험은 이것을 못 잡았다.** 시험들이 바로 그 호환 분기를 쓰기 때문이다 ·
경로 도우미를 인자 하나로 스텁한다. 반대쪽 분기를 고정하는 시험을 새로 넣고,
일부러 되돌려 실패하는 것까지 확인했다.

**이름 치환에서 놓치는 형태가 이번이 세 번째다.**

```
self.___              §6-19에서 확인
getattr(self, '___')  §6-23에서 놓쳤다
hasattr(self, '___')  §6-30에서 놓쳤다   ← 이번
f(self)               §6-19에서 놓쳤다
```

`scripts/bridge_state_map.py`는 이 넷을 모두 센다. **옮길 때도 그 넷을 모두
바꿔야 한다.**

### 6-31. `MotionPlayer` 신설 · §5 목표안 셋째 · 상수 단일화

`MotionRunManager` 2,362 → **1,435줄** · 메서드 73 → 49

§5의 `motion_run_manager` 분해 목표 넷 중 셋째다. **모터를 실제로 움직이는
코드**이므로 계산도 순서도 바꾸지 않았다 · 같은 값을 같은 차례로 보낸다.
최종 출력은 여전히 `motion_supervisor`가 단독 발행한다(§2).

#### 옮긴 것 · 24메서드 899줄

`_run_motion` 222 · `_run_initialization` 111 · `_prepare_and_run` 71 ·
`_wait_between_cycles` 63 · `_run_initial_position_stream` 50 · `_run_countdown` 50 ·
`_wait_for_targets` 42 · `_wait_synchronized_boundary` 38 · `_finish_cycle_stop` 31 ·
발행 계층 6개 104 · 그 외

노드에 남긴 것 · 실행 락과 정지 신호 · 상태 저장·발행 · 자동 반복 · 현재 모터
목록 · 계획 수립기. 플레이어는 그것들을 `self.manager`로 본다.

#### 상수를 한 곳으로 · `motion_run_constants`

분해하면서 상수를 양쪽에 복사하면 **언젠가 갈라진다** · §6-16에서 Dynamixel 스캔
제한시간이 그렇게 갈라져 40초가 20초로 돌았다. 13개를 모듈 하나에 모으고
노드·플레이어·계획 수립기·규칙이 모두 거기서 본다.

#### 이름 치환에서 놓치는 형태 · 네 번째와 다섯 번째

```python
target=self._prepare_and_run        # 호출이 아닌 속성 참조 · 스레드 대상
self.manager._run_motion(...)       # 이미 옮긴 것을 또 옮길 때
```

`target=self.X`는 **호출 괄호가 없어** `self.X(` 치환에 걸리지 않는다.
`GroupSession`(§6-29)에도 같은 버그가 있었다 · `target=self._prepare_and_run_group`이
이름 변경 뒤에도 남아 있었고, **시험이 잡지 못했다**(그룹 실행은 다른 PC가 필요하다).
이번에 함께 고쳤다.

그리고 `GroupSession`이 `self.manager._run_motion`으로 부르던 것을 플레이어로
옮겼으니 `self.manager._player._run_motion`이 됐다 · **분해가 겹치면 앞서 옮긴
것의 참조도 따라가야 한다.**

정리하면 이름을 옮길 때 볼 형태는 다섯이다.

```
self.X(...)             호출
self.X                  속성 참조 · target= · 콜백 등록
getattr(self, 'X')      문자열 접근
hasattr(self, 'X')      문자열 존재 확인
f(self)                 self를 통째로 넘기기
```

#### 검증

- 코드 검증 · `ruff check src` 55건 유지 · 신규 0건
- 실행 검증 · `pytest` 1,013건 통과 · 실패 0
- 실물 검증 · 재시작 · 실행 컨텍스트 `ready` · 노드 확인 8건 · 모터 `Operation enabled`
- 실물 검증 · 계획 수립 · `axis_count 1` · `duration_sec 3.26` · `sample_count 164` ·
  `initialization_duration_sec 5.0` · `clamped_axis_count 0`
- 실물 검증 · **실제 재생 확인** · 작업자가 화면에서 돌렸고 정상 동작 ·
  899줄을 옮긴 재생 경로가 그대로 산다

### 6-32. `EthercatScanner` 신설 · 물리 스캔을 노드 밖으로

`MotionStateMonitor` 2,884 → **2,131줄** · 메서드 78 → 65

§5 `monitor_node` 분해 목표 셋 중 첫째다.

#### 모터 스캔 영구 불변조건을 그대로 지켰다

`AGENTS.md`가 세션·재시작을 넘어 유지하라고 못박은 조건이다. **명령도 순서도
판정도 바꾸지 않았다.**

- `전체 모터 검색`은 여전히 `ethercat rescan`으로 기존 열거정보를 폐기한 뒤
  Slave를 다시 열거한다
- 각 Slave의 SII EEPROM과 Alias 레지스터를 읽는다
- 물리 응답이 없으면 이전 값을 쓰지 않고 실패로 남긴다

`motion_system` 안으로 옮기는 것(§5 8단계)은 별개다 · 여기서는 노드 밖 모듈로만 뺐다.

#### 옮긴 것 · 13메서드 753줄

`_scan_ethercat_slaves` 360 · `_poll_ethercat_bus_status` 123 ·
`_read_station_alias_register` 66 · `_parse_ethercat_slaves` 50 ·
`_read_sii_identity` 43 · 그 외 8개

서비스가 갖는 것 · 마지막 버스 상태(`status`)와 그 시각(`last_status_at`).

#### 놓칠 뻔한 것 · `@staticmethod`

옮기면서 데코레이터를 일괄로 떼었는데 **원래 정적이던 둘**(`_skipped_ethercat_scan` ·
`_parse_sii_identity`)까지 떼여 `self`가 첫 인자를 먹었다. 시험이 잡았다.

원본에서 데코레이터 목록을 다시 읽어 복원했다. **이름 다섯 형태에 이어 여섯 번째
주의점이다 · 메서드의 종류(`staticmethod`·`classmethod`·`property`)도 따라가야 한다.**

#### 테스트 monkeypatch 대상도 이동

`motion_state_monitor.monitor_node.subprocess.run` → `...ethercat_scanner.subprocess.run` ·
§6-22에서 같은 일을 겪었다 · 모듈 경로 문자열 패치는 코드가 옮겨가면 조용히 어긋난다.

#### 검증

- 코드 검증 · `ruff check src` 55건 유지 · 신규 0건
- 실행 검증 · `pytest` 1,013건 통과 · 실패 0 · EtherCAT 스캔 계약 시험 그대로 통과
- 실물 검증 · **AC Servo 물리 스캔 1회** · `scan_id 1788930966106-1` ·
  **불변조건이 그대로 지켜졌다**

```
rescan_performed  True
source            ethercat_rescan_sii_and_register
direct            True
slave             master 0 · position 0 · alias 103
                  vendor 1647 · product 1614282756 · serial 402982152
                  direct_read_complete True
scan_contract     version 3 · physical_only true · ethercat_requires_rescan true
project_comparison compatible true · required [0] · unused [1]
motor_service_restored True
```

**값이 §6-13·§6-18·§6-26의 스캔과 동일하다.** alias·vendor·product·serial 네 가지가
같은 값으로 다시 읽혔다 · 물리 검색 경로가 그대로라는 뜻이다.

### 6-33. `DynamixelScanner` 신설 · 검색과 제원을 가른다

`MotionStateMonitor` 2,131 → **1,675줄** · 메서드 65 → 50

§5 `monitor_node` 분해 목표 둘째다 · §6-32 `EthercatScanner`와 대칭이다.

#### 불변조건을 그대로 지켰다

- 실제 직렬 포트를 열고 Protocol 2.0 **Broadcast Ping**과 ID `0~252` **개별 보조
  Ping**을 수행한다
- 포트 탐색 경로 `/dev/serial/by-id` · `/dev/ttyUSB*` · `/dev/ttyACM*` · YAML 그대로
- 물리 응답이 없으면 이전 값을 쓰지 않고 실패로 남긴다
- 명령도 순서도 판정도 바꾸지 않았다

#### 경계를 개념으로 그었다 · 검색 15개만

`grep dynamixel`로 걸리는 것은 19개 · 그중 **4개는 남겼다.**

| 남긴 것 | 이유 |
| --- | --- |
| `_dynamixel_raw_model_info` · `_read_dynamixel_model_file` | 제원 파일 읽기 · 검색이 아니라 메타데이터다 |
| `_calculated_dynamixel_position_raw` · `_dynamixel_statusword_text` | 상태 발행이 쓰는 값 해석이다 |

**이름이 같다고 개념이 같지 않다.** 넷을 끌고 왔으면 `DynamixelScanner`가
검색·제원·상태해석 셋을 겸했을 것이다. 78 + 27줄을 덜 옮겨 개념 하나를 지켰다.

#### 상수도 같이 옮겼다

`DYNAMIXEL_SCAN_BAUDRATES` · `DYNAMIXEL_SCAN_MAX_ID` · `DYNAMIXEL_SCAN_PROTOCOL` ·
프로토콜 규약이므로 프로토콜 모듈에 둔다 · 노드는 `scan_contract`와 파라미터
기본값에 쓰려고 되가져온다.

#### §6-32의 교훈을 절차로 썼다

데코레이터 소실을 이번엔 **원본과 기계 대조**로 막았다 · `git show HEAD:...`의
`decorator_list`와 신규 모듈의 것을 이름별로 비교 · 불일치 0 · `staticmethod` 6개 보존.

미사용이 된 `import os` · `select` · `termios`도 같이 걷어냈다.

#### 검증

- 코드 검증 · `ruff check src` 55건 유지 · 신규 0건 · 데코레이터 대조 불일치 0
- 실행 검증 · `pytest` 1,013건 통과 · 실패 0
- 실물 검증 · **부분 완료** · 아래

Dynamixel 장치가 없어 응답 경로는 **검증 불가**다. 다만 전체 스캔이 매번 타는
**실패 경로는 실물로 확인 가능**하다.

**전체 모터 검색 1회** · `scan_id 1788931552423-1`

```
판정            부분 완료 · complete false · success false
EtherCAT       rescan true · direct true · alias 103
               vendor 1647 · product 1614282756 · serial 402982152
               direct_read_complete true
Dynamixel      available false · direct true · mode direct_ping
               protocol 2.0 · devices_count 0 · targets 0
               scan_rule  auto serial port, baudrate 1000000,
                          broadcast ping plus ID 0-252
               error      직렬 포트를 찾지 못했습니다
                          /dev/serial/by-id, /dev/ttyUSB*, /dev/ttyACM*
scan_contract  version 3 · dynamixel_protocol 2.0 · baudrate 1000000
               id 0~252 · full_success_requires_all_requested_transports true
```

**확인된 것** · 포트가 없을 때 이전 값을 되쓰지 않고 실패로 남긴다 · 한 장치
종류만 성공하면 전체가 `부분 완료`다 · 검색 규약 값이 그대로다.

**여전히 미검증** · 실제 Ping 응답 해석 · CRC 검사 · 상태 패킷 분해 ·
**Dynamixel 실물 연결 시 재확인 필요** · §6-16의 40초 시한 확인도 함께 밀려 있다.

#### 실물 Dynamixel 연결 검증 · 2026-09-09

사용자가 Dynamixel을 물리 연결했다 · 위에서 미룬 것을 전부 확인했다.

```
포트           FTDI FT232H (0403:6014)
               /dev/serial/by-id/usb-FTDI_USB__-__Serial_Converter_FTAAMMJV-if00-port0
               → /dev/ttyUSB0 · port_source auto:/dev/serial/by-id
검색           available true · complete true · direct true · error 없음
               baudrate 1000000 · protocol 2.0 · attempts 2 · id_fallback true
장치 2대       ID 3 · model_number 1130 · XM540-W150 · fw 50 · packet_error 0
               ID 5 · model_number 1120 · XM540-W270 · fw 50 · packet_error 0
               둘 다 source broadcast_ping
소요           단독 스캔 11.5초 · 전체 스캔 14.0초
```

**해소된 미검증**

| 항목 | 근거 |
| --- | --- |
| Broadcast Ping | 두 대 모두 `source broadcast_ping`으로 검출 |
| CRC 검사 | CRC 불일치는 폐기되므로 검출 자체가 통과 근거 |
| 상태 패킷 분해 | `model_number`·`firmware_version`·`packet_error`가 바르게 뽑힘 |
| 모델명 매핑 | 1130 → XM540-W150 · 1120 → XM540-W270 · 실제 제품과 일치 |
| 포트 자동 탐색 | `/dev/serial/by-id` 경로를 잡았다 |
| ID 보조 Ping 경로 | 미검출 251개 × 2회 시도 · 소요 시간이 이를 뒷받침 |
| **매번 새로 물리 검색** | 2회 연속 스캔 · 장치 동일 · `scanned_at` 매번 갱신 |
| §6-16 40초 시한 | `scan_orchestrator.scan_dynamixel(timeout_sec=40.0)` 단일 경로 확인 · 중복 계층 없음 |

**아직 남은 것** · `_ping_dynamixel_id`의 **성공** 반환 · Broadcast에 응답하지
않는 개체가 있어야 탄다 · 지금 두 대는 모두 Broadcast로 잡힌다.

#### 함께 관측한 것 · 매칭표는 EtherCAT 전용이다 · 이번 변경과 무관

물리 Dynamixel 2대가 요약 문구에는 `Dynamixel 2축`으로 나오지만 **매칭표에는
행이 없다** · `matching_summary`는 `total 1 · matched 1 · unregistered 0`이다.

`_build_matching_rows(ethercat_scan['slaves'], configured_axes)` · 입력이 EtherCAT
Slave뿐이라 그렇다. **이번 분해 이전부터 그랬다**(`git log -S`로 확인) · 리팩터링
회귀가 아니다.

프로젝트에 Dynamixel이 등록되어 있지 않은 현 상태에서는 표시 누락이 문제로
드러나지 않는다. 등록 후에는 `미등록`/`누락` 판정이 필요해진다 · **별도 항목으로
남긴다** · 이번 범위 밖이다.

### 6-34. `motor_values` 신설 · 같은 것과 다른 것을 가른다

`MotionStateMonitor` 1,675 → **1,549줄**

변환 함수 13개와 라벨 상수 2개를 순수 함수 모듈로 뺐다. 핵심은 옮긴 것이 아니라
**두 가지를 구분한 것**이다.

| 이름 | 판단 | 근거 |
| --- | --- | --- |
| `_parse_int` | **합쳤다** → `motion_common.values.optional_int` | `int(str(v), 0)` · `None`·`''` 처리까지 완전히 같다 |
| `_optional_float` | **합치지 않았다** → `unchecked_float`로 개명 | `inf`·`nan`을 통과시킨다 · `values.optional_float`은 막는다 |

`_optional_float`를 그냥 `optional_float`로 바꿨다면 `inf`가 들어오던 자리에서
조용히 `None`이 됐을 것이다. **동작이 바뀌는 통합은 통합이 아니다.** §6-5의
'의도적으로 흡수하지 않은 변형'에 한 줄 더 붙는다.

이름을 `unchecked_float`으로 바꾼 이유도 같다 · 다음 사람이 `optional_float`와
같은 것으로 오해하지 않게 차이를 이름에 새겼다.

#### 새 함정 · 이미 떼어낸 모듈이 노드를 되부른다

`ethercat_scanner`가 `self.monitor._parse_int(...)`를 16곳에서 부르고 있었다.
**노드 파일만 보면 안 보인다** · 노드에서 메서드를 지우자 이미 분리된 모듈이
깨졌다. 시험이 잡았다.

**절차에 추가한다 · 메서드를 옮길 때는 패키지 전체를 grep한다.** 분해가 진행될수록
`self.monitor.<노드메서드>` 형태의 역참조가 늘어난다.

#### 지역 변수와 이름이 겹치면 조용히 가려진다

`pulse_per_revolution`을 import했더니 `_motor_from_status` 안의 지역 변수와
겹쳐 `F811`이 났다. **노드가 쓰지 않는 이름은 아예 가져오지 않는 것으로 정리했다** ·
§6-30에서 `values.finite_float` 때 겪은 것과 같은 종류다.

#### 미사용 2건 · 삭제하지 않았다

`pulse_per_revolution`과 `counts_to_degrees`는 호출부가 없다. 삭제는 판단이
필요하므로 그대로 옮겨두었다 · **정리 대상으로 남긴다.**

### 6-35. `connection_state` 신설 · 판정과 상태를 나눈다

`MotionStateMonitor` 1,549 → **1,297줄** · 메서드 43 → 30 · 클래스 1,212줄

연결 판정 규칙 5개는 **순수 함수**로, 확정 지연을 재는 것만 **상태를 가진 클래스**로
갈랐다.

- `set_connection_fields` · `set_physical_connection_fields` ·
  `connection_message` · `connection_summary` · `build_scan_connection_rows`
- `CommunicationHealth` · 축별 실패·복구 확정을 지연 판정한다 ·
  `_communication_health` 사전을 갖는다

**상태는 그 상태를 쓰는 것과 같이 옮긴다**는 규칙 그대로다.

#### 시한 두 개는 붙잡지 않았다

`connection_loss_confirm_sec` · `connection_recovery_confirm_sec`은 노드
파라미터다. `CommunicationHealth`가 생성 시점에 값으로 붙잡으면 나중에 바뀐 값이
반영되지 않는다 · **부를 때마다 노드에서 읽는다** · §6-11에서 두 번 데인 것이다.

`_last_ethercat_physical_scan`은 반대로 **인자로 바꿨다** · 호출 직전에 읽어
넘기므로 시점이 같다.

#### 이번에 새로 만든 함정 · `(self, ` 일괄 치환

인스턴스 메서드를 모듈 함수로 바꾸며 `(self, `를 `(`로 일괄 치환했더니
`getattr(self, '_last_ethercat_physical_scan', {})`까지 먹혀
`getattr('_last_ethercat_physical_scan', {})`가 됐다.

**인자 두 개짜리 `getattr`는 구문 오류가 아니다** · 첫 인자를 객체로 보고
실행 시점에야 `TypeError`를 낸다. 시험이 잡았다.

**교훈** · 일괄 치환은 `def` 줄과 `self.` 접두만 대상으로 삼고, `self`를 **인자로
받는 내장 함수**(`getattr`·`setattr`·`hasattr`·`isinstance`)는 먼저 걸러야 한다.
지금까지의 '이름 다섯 형태'가 *읽는* 쪽 함정이었다면 이것은 *쓰는* 쪽 함정이다.

#### 검증

- 코드 검증 · `ruff check src` 55건 유지 · 신규 0건
- 실행 검증 · `pytest` 1,013건 통과
- 실물 검증 · **AC Servo 실기 · 발행 토픽과 스캔 응답 양쪽 확인**

`/motion_control/motion_state` 수신 원문에서 확인한 값이다.

```
connection_summary   total 1 · online 1 · confirmed 1 · all_online true
connection_state     online · confirmed true · source runtime_topic
connection_message   모터 런타임 피드백이 정상 수신 중입니다.
physical_connection  unknown · confirmed false
                     "Master 1: 재스캔 후 응답한 Slave가 없습니다"
status_text          Operation enabled   ← statusword 1591 = 0x0637
errorcode_hex        0x0000              ← hex16
error_text           No error            ← error_text
motor_type_label     AC Servo            ← motor_type_label
transport_label      EtherCAT            ← transport_label
position_deg         -124.68 (실시간 갱신)
```

스캔 응답의 `connection_rows`·`connection_summary`도 같이 확인했다 ·
`discovery_state detected` · `통신 버스 검색에서 모터가 확인되었습니다`.

**회귀 아님을 확인한 것** · 스캔 응답의 `connected_axes`·`known_axes`에는
`physical_connection_*`이 실리지 않는다(`None`). §6-33 이전에 받아둔 응답에도
같아서 이번 변경과 무관하다 · `_build_scan_result`가 `_current_motor_list`를
`_last_ethercat_physical_scan` 갱신보다 **먼저** 부르는 순서 때문이다 ·
**별도 항목으로 남긴다.**

### 6-36. `StatePublisher` 신설 · `monitor_node` 분해 완료

`MotionStateMonitor` 1,297 → **866줄** · 메서드 30 → 20 · 클래스 800줄

§5 `monitor_node` 목표안 셋(`EthercatScanner` · `DynamixelScanner` ·
`StatePublisher`)의 **마지막**이다.

#### 세션 누적 · 하나가 다섯이 됐다

| | 시작 | 끝 |
| --- | --- | --- |
| `MotionStateMonitor` | 2,884줄 · 78메서드 | **866줄 · 20메서드** (−70%) |

| 갈라낸 것 | 줄수 | 개념 |
| --- | --- | --- |
| `ethercat_scanner` | 782 | EtherCAT 물리 검색 |
| `dynamixel_scanner` | 490 | Dynamixel 물리 검색 |
| `state_publisher` | 472 | 축 상태 수신·발행 |
| `connection_state` | 285 | 연결 판정 |
| `motor_values` | 157 | 값 변환 |

#### 상태 7개가 같이 갔다

`motors` · `last_healthy_motors` · `last_status_at` · `last_processed_at` ·
`last_disabled_publish_at` · `started_at` · `subscription`

노드에 남긴 것은 **ROS 개체 생성**(발행자·QoS·타이머)과 **설정 메타데이터 적재**뿐이다.
판정과 목록 구성은 전부 서비스 쪽이라 **노드가 사서함 노릇을 하지 않는다.**

#### 상수를 기억으로 적을 뻔했다

새 모듈에 `COMMUNICATION_UNAVAILABLE_ERROR = 65344`라고 적었다. **실제는
`0xFFFF`(65535)다.** 시험 전에 원본을 대조하다 잡았다.

만약 통과했다면 통신 불가 판정이 **영원히 성립하지 않아** 축이 죽어도 `online`으로
남았을 것이다. 시험도 못 잡는다 · 시험이 쓰는 값도 같이 틀렸을 테니.

**옮길 때 상수는 반드시 원본에서 복사한다** · 기억이나 추정으로 적지 않는다.
지금까지의 함정 목록에 붙는다.

| # | 함정 | 잡은 것 |
| --- | --- | --- |
| 1 | 이름 다섯 형태 | 시험·실물 |
| 2 | `@staticmethod` 등 메서드 종류 | 시험 (§6-32) |
| 3 | 떼어낸 모듈이 노드를 되부름 | 시험 (§6-34) |
| 4 | `(self, ` 치환이 `getattr(self, …)`를 먹음 | 시험 (§6-35) |
| 5 | **상수를 기억으로 적음** | **사전 대조** (§6-36) |
| 6 | 인자화로 평가 시점이 달라짐 | 실물 (§6-11) |

#### 검증

- 코드 검증 · `ruff check src` 55건 유지 · 데코레이터 원본 대조 불일치 0 ·
  패키지 전역 역참조 0
- 실행 검증 · `pytest` 1,013건 통과
- 실물 검증 · **AC Servo + Dynamixel 동시 연결 상태에서 발행·스캔 양쪽**

발행 · `/motion_control/motion_state` 6초 수신

```
수신          61건 · 주기 0.098초 (publish_hz 10)
motor_count   1 · online 1 · connection_summary all_online true
state         detected · age_sec 0.007 (실시간 갱신)
status_text   Operation enabled · errorcode_hex 0x0000 · No error
카탈로그       AC Servo · ZeroErr Motor · Dynamixel · CubeMars · Unknown
last_motor_status_at 갱신 확인 → 구독 콜백 정상
```

스캔 · 전체 검색 1회

```
EtherCAT 1 · Dynamixel 2 · known_axes 1 · connected 1 · online 1
known_axes    alias 103 · slave_position 0 · AC Servo · EtherCAT   ← _configured_axis_list
matching      matched 1 · driver_model MADLN05BE
connection    online · discovery detected · ethercat_slave_scan
```

`physical_connection_state`가 `unknown`인 것은 Master 1(랜선 분리)로 스캔이
`complete`가 아니어서다 · §6-35에 기록한 그대로다.

### 6-37. 전체 검색 시한이 두 종류 합보다 짧던 문제

§6-16의 후속이다. 그때 **Dynamixel 시한만** 20 → 40초로 올리고 전체 검색은
20초로 남겨두었다.

| 호출 | 이전 | 지금 |
| --- | --- | --- |
| `scan_ac_servo` | 10초 | 10초 |
| `scan_dynamixel` | 40초 | 40초 |
| `scan_all` | **20초** | **50초** |

전체 검색은 두 종류를 **차례로** 돌린다. 합이 50초인데 예산이 20초였다.

#### 실측이 아니었으면 안 보였다

Dynamixel을 실물 연결하고 나서야 드러났다.

```
Dynamixel 단독   11.5초 / 40초 예산   여유 28.5초
전체 검색        14.0초 / 20초 예산   여유  6.0초
```

장치 2대·포트 1개에서 이미 여유가 6초다. **포트가 하나만 늘어도 전체 검색만
실패한다** · 단독 검색은 멀쩡한데 전체만 안 되는, 원인을 짚기 어려운 형태다.

#### 근본 원인은 값이 흩어진 것

§6-16의 실수도 같은 모양이었다 · 같은 뜻의 값이 두 곳에 있고 한쪽만 고쳤다.
숫자를 지우고 **한 곳에서 유도**하게 했다.

```python
AC_SERVO_SCAN_TIMEOUT_SEC = 10.0
DYNAMIXEL_SCAN_TIMEOUT_SEC = 40.0
FULL_SCAN_TIMEOUT_SEC = AC_SERVO_SCAN_TIMEOUT_SEC + DYNAMIXEL_SCAN_TIMEOUT_SEC
```

#### 관계를 시험으로 고정했다

`test_scan_timeouts.py` 3건 · 값이 아니라 **관계**를 검사한다.

- 전체 ≥ AC Servo + Dynamixel
- 기본값은 상수에서만 나온다 · 숫자를 다시 적으면 실패
- Dynamixel은 40초 이상 · §6-16이 올린 값이 되돌아가지 않게

`scan_all` 기본값을 20.0으로 되돌려 **2건이 실패하는 것을 확인**했다.

#### 검증

- 코드 검증 · `ruff check src` 55건 유지
- 실행 검증 · `pytest` **1,016건** 통과 · 신규 3건 · 회귀 확인 완료
- 실물 검증 · 전체 검색 1회 · `scan_id 1788934167105-1` · EtherCAT 1 ·
  Dynamixel 2(ID 3·5) · 노드 소요 11.6초 · 왕복 15.1초 · 결과 이전과 동일

### 6-38. `motion_value_map` 신설 · 순수 변환을 먼저 뗀다

`midi_control_node` 3,354 → **3,100줄**

§5 `midi_control_node` 목표안 넷 중 `MotionValueMapper`에 해당한다.

이미 **모듈 수준**에 있던 순수 함수 13개와 상수 6개를 옮겼다 · 클래스는 그대로다 ·
파일 크기만 줄었다. 다음 분해를 위한 자리 정리다.

- 페이더 원시값 ↔ 모션값 ↔ 모터 목표각 변환
- 링크된 Motion ID들의 범위·값이 어긋났는지 보는 검사
- 2차 저역통과 필터 · LCD 표기

**상태도 노드 참조도 없다** · 옮기기 전부터 순수했다.

#### 시험 통로도 같이 옮겼다

`test_midi_control_node.py`가 `from midi_control.midi_control_node import
motion_value_display, ...`로 쓰고 있었다. 노드에서 되내보내면 통로는 유지되지만
**어디에 사는 코드인지 흐려진다** · 시험 import를 새 모듈로 바꿨다.

되내보내기를 택했다면 `ruff` F401이 나거나, 그것을 피하려고 `__all__`을 붙여
껍데기를 하나 더 만들었을 것이다.

#### `values` 이름 충돌이 드러났다

노드가 `from motion_common import ... values`를 쓰고 있었고, 클래스 안에는
`_array_value(values, ...)` 같은 지역 이름이 있었다. 함수를 옮기고 나니
`motion_common.values`가 미사용이 되어 `F811`이 10건 떴다.

가려진 import를 걷어냈다 · **가려져 있었을 뿐 원래 있던 문제다.**

### 6-39. `PickupPolicy` 신설 · 튐 방지 판정을 뗀다

`midi_control_node` 3,100 → **2,951줄** · 클래스 3,025 → **2,874줄** · 메서드 88 → 80

물리 페이더 위치와 실제 모션값이 어긋난 채로 SELECT를 켜면 축이 튄다. 페이더가
기준값을 지나갈 때까지 기다렸다가 그때부터 명령을 낸다 · 그 판정 8개를 모았다.

채널별 대기 상태 **넷이 같이 갔다** · `pending` · `reference_motion` ·
`previous_motion` · `reference_source`.

#### 락은 노드가 계속 갖는다

이름 끝의 `_locked`가 "노드의 `_lock` 아래에서만 부른다"는 약속이다. `_lock`은
`_midi_callback`을 비롯한 노드 전체가 공유하므로 옮기지 않았다 · §6-29에서
`GroupSession`에 `run_lock`을 넘긴 것과 같은 판단이다.

#### 이번에 겪은 것 · 초기화 자리와 재설정 자리

네 리스트를 만드는 코드가 **두 곳**에 있었다 · `__init__`과
`_reset_runtime_controls_locked`. 앞줄이 똑같아
(`self._motor_follow_active = [False] * MIDI_CHANNEL_COUNT`) 일괄 치환이
**생성 자리까지 `reset()`으로** 바꿔버렸다 · 객체가 없는데 `reset()`을 불렀다.

시험이 잡았다. 재설정은 객체를 새로 만들지 않고 제자리에서 되돌린다 ·
새로 만들면 참조를 쥔 곳이 낡은 객체를 보게 된다.

#### 검증

- 코드 검증 · `ruff check src` 55건 유지 · 데코레이터 원본 대조 불일치 0 ·
  잔여 참조 0
- 실행 검증 · `pytest` 1,016건 통과 · MIDI 시험 2,191줄이 그대로 통과
- 실물 검증 · **부분** · 노드는 기동하고 스냅샷을 정상 발행한다

```
midi_monitor   bridge_publish_age_sec 0.004 (갱신 중)
채널 0         pickup_pending false · pickup_reference_source ''
               pickup_complete · pickup_reference_motion_deg 존재
connected      false · "X-Touch MIDI input port not found"
```

**Pickup 판정 자체는 검증 불가** · 물리 페이더 입력이 있어야 탄다.

**X-Touch 연결 후 재확인 · 2026-09-09**

```
장치      X-Touch-Ext (BEHRINGER 1397:00b6) · ALSA card 2
ALSA      24:0 → 128:0 (입력) · 129:0 → 24:0 (출력) · 양방향 연결됨
노드      device_connected true · "X-Touch connected" · node_state ok
물리 입력  last_received_at 갱신 확인 (15:39:46)
사용자     화면에서 정상 동작 확인
```

`connected`가 `false`인 것은 결함이 아니다 · 계산식이
`device_connected AND 최근 물리 입력 ≤ stale_timeout`이라 손을 떼면 `false`가 된다.

**여전히 미검증** · SELECT를 켠 뒤 페이더가 기준값을 지나는 순간의 판정 ·
자동 관측으로 그 순간을 잡지 못했다 · 그 경로는 곧 모터가 움직이는 경로다.

### 6-40. `FaderStateMachine` 신설 · 보낸 목표와 실제 도착을 가른다

`midi_control_node` 3,100 → **2,800줄** · 클래스 2,874 → **2,722줄** · 메서드 80 → 74

전동 페이더는 명령을 보낸다고 즉시 그 자리에 있지 않다. 보낸 목표와 실제 도착을
따로 들고, 도착할 때까지 입력을 신뢰하지 않는다 · 그 대기 상태 **10개**를 옮겼다.

- `parking` · 0으로 되돌리는 중인가
- `awaiting_sync` · 보낸 목표에 아직 도착하지 않았는가
- `input_generation` · 재연결 전 입력을 뒤늦게 받아 쓰지 않으려는 세대 표식

#### 끌고 오지 않은 것

`_resync_controlled_faders_locked`(61줄)는 남겼다. 뱅크·축 등록부·SELECT 상태까지
건드리는 **노드 조율**이다 · 끌고 왔으면 `FaderStateMachine`이 페이더 정렬과
매핑 재계산 둘을 겸했을 것이다.

#### 이름 다섯 형태가 또 나왔다 · 이번엔 문자열

`getattr(self, '_studio_select_locked', False)` 하나를 놓쳤다. 옮긴 뒤 이 이름은
`FaderStateMachine`에 없으므로 **기본값 `False`가 조용히 반환된다.**

구문 오류도 예외도 아니다 · **판정만 뒤집힌다.** 스튜디오 녹화 중 페이더 파킹이
물리 0 복귀를 건너뛸 수 있는 분기였다 · 시험이 잡았다.

`ast`는 `getattr`의 문자열 인자를 속성 참조로 보지 않는다 · **정규식 감사를
따로 돌려** 이번에 만든 모듈 7개를 전부 확인했다.

```
getattr|hasattr|setattr(self, '<이름>')  중
클래스가 갖지 않는 <이름>을 찾는다
```

#### 시험 스텁이 지연 기본값에 기대고 있었다

`_fader_zero_required`는 노드 `__init__`이 `True`로, `_ensure_`가 지연으로
`False`로 만든다. 운영은 `__init__`이 먼저라 **늘 `True`**였고, `__new__`로 만드는
시험 스텁만 `False`를 받고 있었다.

생성자가 운영과 같은 값을 쓰게 되자 시험 하나가 깨졌다. **운영 경로는 변하지
않았다** · 스텁이 의도한 상태를 명시하도록 고쳤다.

옮기기 전에는 이 차이가 보이지 않았다 · 분해가 드러낸 것이다.

#### 검증

- 코드 검증 · `ruff check src` 55건 유지 · 데코레이터 대조 불일치 0 ·
  문자열 형태 감사 통과 · 잔여 참조 0
- 실행 검증 · `pytest` 1,016건 통과
- 실물 검증 · **부분** · X-Touch 연결 상태에서 재시작

```
node_state          ok · "X-Touch connected" · 오류 로그 없음
input_state         6초간 261건 (약 43Hz)
fader_input_generation  [0]×8 · 세대 표식 정상 스트림
스냅샷              fader_parking false · fader_syncing false
                    raw_value 0 · observed_raw_value 0
motor_command       inactive · "SELECT 사용 가능"
```

#### 실물 전 경로 검증 · 2026-09-09 · §6-38~§6-40 미검증 해소

사용자가 MIDI로 모터를 조작했다. **페이더에서 서보까지 한 줄로 확인됐다.**

```
MIDI 채널 0    control_enabled true · raw_value 5820 (페이더 이동됨)
Pickup        pickup_complete true · pickup_pending false
              pickup_reference_source  motor_feedback
              pickup_reference_motion_deg  -33.378
명령          "MIDI target published: Axis 0, motion -52.111 deg,
                                      motor -52.111 deg"
서보 실측     position_deg -52.111 · velocity 1.373 · Operation enabled
```

**서보 실제 위치가 명령값과 정확히 일치한다.**

| 해소된 미검증 | 근거 |
| --- | --- |
| `_pickup_reference_for_group_locked` | `motor_feedback` 경로로 기준을 잡았다 · 소스값 후보가 모두 걸러진 뒤 실측 역산으로 갔다는 뜻 |
| `_motor_feedback_ready_for_pickup` | 위 경로의 전제 조건 · 통과했다 |
| `_pickup_feedback_consistency_tolerance` | 같은 경로에서 실행됐다 |
| `_pickup_reached` | `pending`이 `true → false`로 넘어갔다 · 기준값 통과 판정 성립 |
| `motion_value_map` 변환 | 페이더 원시값 5820 → 모션 −52.111° → 모터 목표각 −52.111° |
| `FaderStateMachine` 파킹 왕복 | SELECT가 켜졌다 · 파킹·동기화 대기가 풀려야 도달하는 상태다 |

**남은 미검증** · 파킹 실패 분기(`fader_park_failed`) · 재연결 세대 표식 무효화 ·
정상 경로에서는 타지 않는 예외 분기다.

### 6-41. `midi_snapshot` 신설 · 화면 표현을 뗀다

`midi_control_node` 2,800 → **2,397줄** · 클래스 2,722 → **2,321줄**

`_snapshot` 400줄을 `build_snapshot(node)` 모듈 함수로 뺐다. **읽기만 하고
판정하지 않는다** · 채널 8개의 매핑·필터·SELECT·Pickup·모터 명령 상태를 화면과
응답이 쓰는 형태로 옮겨 담는다.

#### `MidiDecoder`는 미뤘다 · 이름보다 경계를 본다

§5 목표안의 넷째다. 입력 해석은 `_midi_callback`의 **459줄짜리 채널 루프 안에**
있고, 파킹·SELECT 판정과 줄 단위로 엇갈려 있다.

떼려면 상태 **16개**(`_raw_channels` · `_channels` · `_filter_stage1/2` ·
`_touch` · `_btn0~3` · `_previous_*` 등)를 옮겨야 한다 · 노드 참조 106곳 ·
시험 참조 71곳. **방금 실물 검증을 통과한 핫패스다.**

먼저 루프를 국면별로 가르는 것이 순서다 · **별도 항목으로 둔다.**

#### §6-39의 결함을 잡았다 · 다중행 `getattr`

```python
getattr(
    self,
    'pickup_feedback_consistency_deg',
    PICKUP_FEEDBACK_CONSISTENCY_DEG,
)
```

한 줄 치환이 지나갔다. `PickupPolicy`에 없는 이름이라 **늘 기본값 1.0을 쓰고
있었다** · launch override가 없어 관측 영향은 없었지만, 있었다면 조용히 무시됐다.

**§6-40에서 같은 종류를 하나 잡고도 또 나왔다.** 그때 만든 정규식 감사가
줄바꿈을 못 봤기 때문이다.

#### 감사 도구를 정규식에서 AST로 바꿨다

`ast.Call`의 인자를 보므로 줄바꿈에 영향받지 않는다.

```
self 를 첫 인자로 받는 getattr/hasattr/setattr 중
문자열 이름이 그 클래스에 없는 것을 찾는다
대상 · self.node / self.monitor / self.bridge 를 갖는 서비스 클래스
```

`src` 전체에 돌려 **잔여 0** 확인 · 정규식 감사는 폐기한다.

#### 시험 통로도 옮겼다

`node._snapshot = lambda: ...` 인스턴스 monkeypatch가 모듈 함수를 비껴갔다 ·
`monkeypatch.setattr(midi_node_module, 'build_snapshot', ...)`로 바꿨다.

**모듈 전역에 직접 대입하지 않았다** · 복원되지 않아 시험 간 오염이 된다 ·
`monkeypatch` 픽스처가 끝나면 되돌린다.

#### 검증

- 코드 검증 · `ruff check src` 55건 유지 · AST 감사 잔여 0
- 실행 검증 · `pytest` 1,016건 통과
- 실물 검증 · X-Touch 연결 상태 재시작 · **스냅샷 구조가 이전과 같다**

```
node_state ok · success true · "X-Touch connected" · 오류 로그 없음
최상위 키 43 · 채널 8 · 채널당 필드 55  ← 분리 전과 동일
bridge_publish_age_sec 0.004 (갱신 중)
```

### 6-42. 7단계 · 정적 자산이 매번 다시 전송되던 문제

`Cache-Control: no-store` → **`no-cache` + 조건부 요청 처리**

#### 진단 · 세 겹으로 캐시가 죽어 있었다

| 겹 | 상태 |
| --- | --- |
| 응답 헤더 | `no-store` · 브라우저가 **아예 캐시하지 않는다** |
| `ETag` | 이미 나가고 있었다 · `no-store`가 무의미하게 만들고 있었다 |
| 빌드 캐시버스트 | `update_cache.py`가 `?v=<타임스탬프>`를 새로 쓰지만, 런타임은
`MOTION_WORKSPACE`가 있으면 **소스 디렉터리**를 서빙한다 · 빌드본의 토큰은 쓰이지 않는다 |

게다가 `?v=` 토큰은 46개 JS 중 **3개**에만 붙어 있었다 · 나머지는 ES `import`로
불려서 토큰이 없다.

결과 · **화면을 열 때마다 1.25 MB를 다시 받는다.**

#### 고른 방법 · 재검증

`no-store`는 "캐시하지 마라", `no-cache`는 "**캐시하되 쓸 때마다 물어봐라**"다.
낡은 화면이 뜰 위험은 **같고**, 안 바뀌었으면 본문을 받지 않는다.

해시 파일명·번들러는 **택하지 않았다.**

| 후보 | 왜 아닌가 |
| --- | --- |
| 해시 파일명 + `immutable` | 46개 `import` 명세를 전부 재작성해야 하고, 소스/빌드 서빙 경로부터 정리해야 한다 |
| 번들러(esbuild) | `node`·`npm`이 **모든 빌드 PC**에 필요하다 · 슬레이브 2대에 도구가 는다 · localhost라 이득이 작다 |

#### Starlette은 304를 스스로 주지 않는다

`FileResponse`가 `etag`·`last-modified`를 **헤더에 넣기만** 한다 · 조건부 요청
판정은 `StaticFiles`에만 있고 이 라우트는 `FileResponse`를 직접 쓴다.

그래서 `If-None-Match` 비교와 304 응답을 라우트에 직접 넣었다.

**`Last-Modified`는 보지 않는다** · 초 단위라 같은 초 안의 수정을 놓친다 ·
낡은 화면이 뜨느니 한 번 더 보내는 편이 낫다 · `ETag`만 본다.

#### 남는 성질 · 알고 쓸 것

Starlette의 `ETag`는 **`mtime + 크기`**로 만든다 · 내용이 같아도 다시 빌드하면
`mtime`이 바뀌어 새 `ETag`가 된다 → 한 번 더 받는다. 보수적인 쪽이라 그대로 둔다.

#### 검증

- 코드 검증 · `ruff check src` 55건 유지
- 실행 검증 · `pytest` **1,017건** 통과 · 신규 1건
  - 기존 시험은 값(`no-store`)이 아니라 **뜻**(재검증 강제 · `max-age`·`immutable` 없음)을 보도록 고쳤다
  - 신규 · 같은 `ETag`면 304에 본문 0바이트 · 다른 `ETag`면 200
- 실물 검증 · **정적 파일 48개 + index 전수**

```
첫 로드   1,315,191 B  (1.25 MB)
재방문            0 B  ·  304 응답 48/48
파일 수정 후      새 ETag 발급 · 옛 ETag 요청은 200 + 전체 본문
```

### 6-43. 7단계 · `styles.css` 분할

`styles.css` **7,415줄 → 11조각** · 최대 1,449줄

#### 순서를 바꾸지 않았다

CSS는 **나중 규칙이 앞 규칙을 덮는다.** 개념별로 모으려면 규칙을 옮겨야 하고,
옮기는 순간 겹치기(cascade)가 달라져 화면이 조용히 바뀐다.

그래서 **위치로만 잘랐다** · 파일 이름은 그 구간에 주로 무엇이 있는지를 가리킨다 ·
개념별 재배치가 아니다.

| 조각 | 줄 | 원본 구간 |
| --- | --- | --- |
| `01-base` | 135 | 토큰 · 초기화 · 상단바 |
| `02-project` | 935 | 프로젝트 사이드바 · 파일 트리 |
| `03-studio-frame` | 709 | 스튜디오 틀 · 공용 패널 |
| `04-motor-axis` | 488 | 모터 · 축 · 모니터링 |
| `05-studio-editor` | 1,449 | 스튜디오 편집기 · 레이어 · 타임라인 |
| `06-config` | 1,140 | 설정 화면 · 모터 등록 |
| `07-motion` | 909 | 모션 목록 · 이벤트 · 재생 |
| `08-operation-servo` | 519 | 조작 · 서보 · 수동 명령 |
| `09-midi` | 131 | MIDI 화면 |
| `10-workspace-system` | 591 | 작업공간 탭 · 설정 · 시스템 정보 |
| `11-coordination` | 409 | 연동 화면 · 나머지 |

자를 자리는 **규칙 밖 빈 줄**만 골랐다 · 규칙 한가운데를 자르면 문법이 깨진다.

#### 계약을 시험으로 고정했다

`test_css_parts.py` 4건.

- `index.html`이 조각을 **하나도 빠짐없이 한 번씩** 건다
- 링크가 **번호 접두 순서**와 같다 · 번호가 곧 겹치기 순서다
- 조각마다 출처 주석이 있고 내용이 비어 있지 않다
- 옛 `styles.css`가 **남아 있지 않다** · 남기면 어느 쪽이 진짜인지 알 수 없다

#### 검증

- 실행 검증 · `pytest` **1,021건** 통과 · 신규 4건
- **동치 검증** · 조각을 링크 순서대로 이으면 원본 `styles.css`와 **바이트 단위로 같다**
- 실물 검증 · 서비스 재시작 후 11개 전부 `200` · **서버가 보낸 것을 이어도 원본과 같다** ·
  옛 경로 `/static/styles.css`는 `404`

```
01-base 2,624B · 02-project 17,667B · 03-studio-frame 12,862B
04-motor-axis 8,604B · 05-studio-editor 26,619B · 06-config 19,637B
07-motion 16,771B · 08-operation-servo 9,355B · 09-midi 1,984B
10-workspace-system 12,012B · 11-coordination 7,170B
```

#### `index.html` 분할은 하지 않았다

1,993줄 · 작업화면 11개 `section`과 모달 8개가 든 단일 셸이다.

| 방법 | 왜 지금은 아닌가 |
| --- | --- |
| 클라이언트에서 조각을 받아 끼우기 | `main.js` 등이 모듈 적재 시점에 DOM id를 찾는다 · 비동기로 끼우면 깨진다 |
| 빌드 시 합치기 | 런타임이 **소스 디렉터리**를 서빙한다 · 빌드 산출물이 쓰이지 않는다(§6-42와 같은 함정) |
| 서버가 조립 | 가능하다 · 다만 브리지에 **템플릿 기구**가 생기고 `ETag`를 조립 결과로 다시 계산해야 한다 |

셋째가 유일하게 성립하지만 **성격이 다른 변경**이다 · 별도 항목으로 남긴다 ·
지금 화면은 잘 돌고, `index.html`은 이제 재방문 시 전송되지 않는다(§6-42).

### 6-44. 7단계 완료 · `index.html` 서버 조립

`index.html` **2,004줄 → 셸 51줄 + 조각 11개**

#### 왜 서버가 조립하나

§6-43에서 셋 중 둘을 접었던 이유를 그대로 확인했다.

| 방법 | 결과 |
| --- | --- |
| 브라우저에서 끼우기 | `main.js`가 모듈 적재 시점에 DOM을 찾는다 · 비동기로 끼우면 깨진다 |
| 빌드 때 끼우기 | 런타임이 **소스 디렉터리**를 서빙한다 · 산출물이 쓰이지 않는다(§6-42의 함정) |
| **서버가 조립** | 성립한다 · 아래 |

`IndexComposer` · 자리표시자 한 줄을 조각 내용으로 바꾼다.

```html
      <!--#include panels/09-panel-studio.html -->
```

| 조각 | 줄 |
| --- | --- |
| `01-topbar` · `02-project-sidebar` | 68 · 20 |
| `03-panel-system` · `04-panel-coordination` · `05-panel-registration` | 111 · 171 · 227 |
| `06-panel-servo-monitoring` · `07-panel-manual` | 130 · 157 |
| `08-panel-motion-data` · `09-panel-studio` | 468 · 406 |
| `10-panel-event-log` · `11-modals` | 49 · 157 |

#### `ETag`는 **조립 결과**에서 낸다

셸의 `mtime`만 보면 조각을 고쳐도 브라우저가 옛 화면을 쓴다 · 조립한 HTML을
해싱한다.

부수 효과가 하나 좋다 · **내용이 같으면 `ETag`도 같다** · 정적 파일 쪽은
Starlette 규칙(`mtime`+크기)이라 `touch`만 해도 새 `ETag`가 되는데(§6-42),
조립본은 그렇지 않다.

조립은 조각의 `mtime`이 하나라도 바뀔 때만 다시 한다 · 매 요청 재조립은 아니다.

#### 조각 머리말은 화면으로 내보내지 않는다

조각 첫머리에 출처 주석(원본 몇 줄이었는지)을 달았다. 그대로 내보내면 원본과
바이트가 달라지므로 **조립할 때 첫 주석 블록을 뗀다.**

#### 시험 이음매가 크게 움직였다

`index.html`을 직접 읽던 시험이 **15개** 있었다 · 셸만 읽게 되어 37건이 깨졌다.
`styles.css`를 직접 읽던 시험도 6개 있었다(§6-43에서 놓쳤다).

`web_ui/tools/index_html.mjs`를 만들어 `indexHtml`·`stylesCss`를 내보내고 전부
그쪽을 보게 했다. **`test/` 아래에 두지 않았다** · Node는 그 디렉터리의 모든
파일을 시험으로 돌린다.

조립 규칙이 파이썬과 자바스크립트 두 곳에 생겼다 · 갈라지면 **시험이 보는 화면과
실제 화면이 달라진다** · `test_javascript_helper_uses_the_same_rules`가 지킨다.

#### 기준선을 재서 확인했다

Node 시험은 원래 몇 건이 실패하고 있었다. 내 변경이 늘린 것이 없는지 보려고
분할 이전 커밋으로 **별도 워크트리**를 만들어 쟀다.

```
기준선(6f23012)   248 통과 · 6 실패
현재              248 통과 · 6 실패   ← 실패 목록도 같다
```

중간에 한 번 249/5가 아니라 220/14였다 · `fs.readFileSync(...)`를 치환하면서
`fs.` 접두가 남아 `fs.indexHtml`이 된 파일이 있었다 · 기준선 대조가 잡았다.

#### 검증

- 코드 검증 · `ruff check src` 55건 유지
- 실행 검증 · `pytest` **1,028건** 통과(신규 7) · Node 248 통과 · **신규 실패 0**
- **동치 검증** · 조립 결과가 분할 전 `index.html`과 **바이트 단위로 같다**
- 실물 검증 · 재시작 후

```
GET /            200 · 126,872B · 서빙본 == 분할 전 원본
재요청           304 · 0B
조각 내용 수정    ETag 바뀜 · 복원하면 원래 ETag로 돌아옴
패널 확인        workspaceTabs · studio · motion · scheduleEditModal ·
                 coordinationGroupId 전부 존재
```

### 6-45. 8단계 설계 검토 · 하드웨어 스캐너 이관

**코드는 손대지 않았다.** §5 8단계가 "범위 협의 후"라고 적어둔 그 협의다.

#### 옮길 것과 받을 곳이 같은 물건이 아니다

| | 우리 스캐너 | `motion_system` |
| --- | --- | --- |
| 언어 | Python 1,272줄 | C++ (`.cpp` 22 · `.hpp` 30) |
| EtherCAT | **`ethercat` CLI 호출** · `master`·`slaves`·`rescan`·SII·레지스터 | `ethercat_controller.cpp` 411줄 · 마스터 **라이브러리** |
| Dynamixel | `os.open` + `termios` + 생 Protocol 2.0 패킷 | `dynamixel_driver.cpp` 431줄 · 드라이버 |
| 목적 | **한 번 훑어보기** | **주기 제어** |

**이동이 아니라 재구현이다.** 파일을 옮겨 붙일 수 있는 모양이 아니다.

#### 지금 구조가 왜 이렇게 됐나

`scan_orchestrator._call_ethercat_service_locked`의 주석이 그대로 답이다.

> 스캔 계약이 `ethercat rescan`을 요구한다. 따라서 상주 Motor Manager를 먼저
> 멈추고 나중에 되살려야 한다. 이 조율은 상위 웹 계층의 일이고,
> **`motion_system`은 손대지 않는다.**

즉 **일부러** 이렇게 만들었다 · `motion_system`을 건드리지 않으려고 상위에서
서비스를 세웠다 껐다 하는 값을 치르고 있다.

#### 8단계의 진짜 상금

마스터를 쥔 쪽이 직접 훑으면 **모터 서비스를 멈추지 않고** 스캔할 수 있다 ·
지금은 전체 검색마다 `motion-motor.service`를 정지·복구한다.

#### 값이 얼마인가

`src/motion_system`은 **별도 저장소(서브모듈)**다.

```
브랜치   main ... upstream/main [ahead 1, behind 20]
원격     github.com/kimjoonho-git/motion_system_ros2.git
안쪽     lib/robot_manager · playstation_joy_interface_ros2 (중첩 서브모듈, 미초기화)
```

- **upstream보다 20커밋 뒤처져 있다** · 여기에 얹으면 갈라짐이 커진다
- `AGENTS.md`는 일반 Git 작업에 이 저장소를 **포함하지 않는다**고 못박고 있다
- 다른 PC 2대도 서브모듈을 따로 받아야 한다

#### 갈림길 셋

| 안 | 하는 일 | 얻는 것 | 치르는 것 |
| --- | --- | --- | --- |
| **A · 현행 유지** | 8단계를 접고 이유를 남긴다 | 위험 0 | 스캔 때 모터 서비스 정지가 계속 남는다 |
| **B · 최소 이관** | Python 스캐너 두 개를 `motion_system` 안 새 패키지로 옮긴다 | "모터 통신은 `motion_system` 단일 통로" 원칙 충족 | **기능 이득 없음** · 여전히 CLI·직렬 · 여전히 서비스 정지 · 서브모듈 커밋 발생 |
| **C · 진짜 이관** | `motor_manager`(C++)에 스캔을 넣는다 | **무정지 스캔** · 마스터 재열거를 쥔 쪽에서 수행 | C++ 재구현 · 공유 upstream 저장소 변경 · 실물 검증 부담 큼 |

#### 판단 · 지금은 A를 권한다

- C의 상금(무정지 스캔)은 진짜지만, **20커밋 뒤처진 공유 저장소**에 큰 C++ 변경을
  얹는 일이다 · 되돌리기도 어렵다
- B는 원칙만 채우고 **동작은 하나도 나아지지 않는다** · 서브모듈 커밋만 남는다
- 현행 구조는 **실물 검증을 통과했다**(§6-13·18·32·33·37) · 스캔 불변조건도 지킨다

C로 가려면 먼저 `motion_system`을 upstream에 맞추는 일이 선행이다 · 그것부터가
별도 작업이다.

#### 결정 · **A · 현행 유지** (2026-09-09 확정)

사용자 확정. **8단계를 접는다** · 하드웨어 스캐너는 `motion_control_studio`에
그대로 둔다.

무엇이 그대로 남는지 분명히 적어둔다.

| 남는 것 | 내용 |
| --- | --- |
| 스캔 시 모터 서비스 정지 | 전체 검색·AC Servo 검색마다 `motion-motor.service` 정지·복구 |
| `ethercat` CLI 의존 | 스캐너가 외부 명령을 부른다 · 명령 출력 형식이 바뀌면 파싱이 깨진다 |
| 직렬 포트 직접 접근 | Dynamixel 검색이 `os.open`으로 포트를 연다 |

**"모터 통신은 `motion_system` 단일 통로"** 원칙과의 관계도 적어둔다 ·
**제어**는 그대로 단일 통로다 · **검색(read-only 한 번 훑기)**만 예외이고,
그 예외는 위 표의 값을 치르지 않기 위한 의도적 선택이다.

다시 꺼낼 조건 · 아래 중 하나가 생기면 C를 재검토한다.

- 스캔 때문에 모터 서비스를 멈추는 것이 **운용에 실제 지장**을 준다
- `motion_system`이 upstream과 **동기화**되어 변경을 얹기 쉬워진다
- `ethercat` CLI 출력 변경으로 파싱이 **실제로 깨진다**

### 6-46. `project_repository` 분해 1차 · 검증 · 트리 · 경로

`project_repository.py` **2,100 → 1,652줄** · 클래스 1,987 → **1,569줄** · 메서드 69 → 64

#### 공용 층을 먼저 만들었다

`_local_directory` · `_sha256` · `_sha256_file` · `_is_user_file` · `_safe_stem` ·
`PROJECT_CATEGORIES` · `DISPLAY_NAMES`를 트리 쪽으로 끌고 가면
`project_repository`가 다시 그것을 부르며 **순환 참조**가 된다.

그래서 `project_paths.py`를 먼저 세웠다 · 아무것도 import하지 않는 바닥이다.

```
project_paths              → (없음)
project_tree               → project_paths
motor_profile_validation   → motor_identity
project_repository         → project_tree · project_paths · motor_profile_validation
```

#### 떼어낸 것

| 모듈 | 줄 | 내용 |
| --- | --- | --- |
| `motor_profile_validation` | 245 | `validate_runtime_motor_profiles` · **워크스페이스 최장 함수 227줄이었다** |
| `project_tree` | 192 | 파일 트리 · 읽기 전용 하위 트리 · MIDI 뱅크 요약 |
| `project_paths` | 66 | 경로 안전 검사 · 해시 · 분류 상수 |

`validate_runtime_motor_profiles`는 원래도 `@staticmethod`였고 `self`를 하나도
쓰지 않았다 · **클래스 안에 있을 이유가 없던 227줄**이다.

#### 겪은 것 · 여러 줄 서명의 `self`

```python
def read_only_directory_tree(
    self,                      # ← `(self, ` 치환으로는 안 지워진다
    directory: Path,
```

인자 하나가 밀려 `category`가 사라졌다 · 시험이 잡았다.
§6-35의 `(self, ` 함정과 **같은 뿌리, 다른 모양**이다.

#### 시험이 하나 가벼워졌다

`test_runtime_rejects_duplicate_ethercat_master_index`가 `ProjectRepository(tmp_path)`를
세우고 있었다 · 검증이 순수 함수가 되면서 **저장소가 필요 없어졌다** ·
`ruff`의 `F841`이 그것을 알려줬다.

#### 검증

- 코드 검증 · `ruff check src` 55건 유지 · **계층 순환 0**
- 실행 검증 · `pytest` **1,028건** 통과
- 실물 검증 · 재시작 후 프로젝트 6개 조회 · 트리 8구획 전수

```
project_root          project.json                      sha d5af6878ec
motor_axes            motor_axes.yaml        active     sha 385fe47a3f
motion_axis_matching  ㄴㅇㄹ.yaml            active     sha 6f0eb73e07
                      midi_banks {stored false, count 0}
motions               ㄴㅇㄹ.json            active     sha 2f282f3eda
layers                연동2-…__layer_….json  active     sha c25737348d
logs                  2건 · runtime 7건 · trash 0건   ← 읽기 전용 하위 트리
```

#### 남은 군집

| 군집 | 줄 | 성격 |
| --- | --- | --- |
| 모터 런타임 상태·조작 기록 | ~305 | **`motor_runtime_file`과 그 락을 갖는다** · 다음 차례 |
| 기동 시 마이그레이션 4종 | ~181 | 한 번만 도는 코드 |

### 6-47. `MotorRuntimeStore` 신설 · 파일과 락을 소유자에게

`project_repository.py` **1,652 → 1,272줄** · 클래스 1,569 → **1,204줄** · 메서드 64 → 49

15메서드 338줄과 **파일락 데코레이터**가 함께 갔다.

#### 왜 다른 물건인가

`.motor_runtime.json` 하나에 **적용된 설정**과 **조작 진행 상황**이 같이 들어 있고,
웹·조율·모니터가 모두 이 파일을 통해 이야기한다.

저장소는 프로젝트 **파일들**을 다루고, 이쪽은 **지금 무엇이 돌고 있는지**를
다룬다 · 같은 클래스에 있을 이유가 없었다.

```
MotorRuntimeStore
  path      .motor_runtime.json      ← 이 파일과 그 락을 갖는다
  적용 설정  mark_runtime_motor_config_applied · motor_runtime_state · …
  조작 기록  begin/update/finish_motor_operation · motor_operation_status
```

#### 위임 껍데기를 남기지 않았다

바깥에서 부르는 곳이 **65군데**였다 · 전부 `repository.runtime.<이름>`으로 고쳤다 ·
`ProjectRepository`에 같은 이름의 껍데기를 두면 어느 쪽이 진짜인지 흐려진다.

#### 이름 다섯 형태가 또 나왔다 · 이번엔 판정이 뒤집혔다

```python
hasattr(repository, 'motor_operation_status')   # ← 호출부 치환에서 빠졌다
```

5곳. 옮긴 뒤 이 이름은 저장소에 없으므로 **참이던 것이 거짓이 된다** ·
`runtime_service_status`가 `ready` 대신 `motor_manager_disabled`를 냈다.

**예외도 아니고 구문 오류도 아니다 · 판정만 조용히 뒤집힌다.** §6-40·§6-41과
같은 종류가 세 번째로 나왔다 · 시험이 잡았다.

#### 시험 스텁도 함께 옮겼다 · 정규식으로는 안 됐다

시험이 `type('Repository', (), {...})()`로 스텁을 만든다. 정규식으로 일괄
이동했더니 **`selected_project_id`까지 `runtime` 밑으로 끌고 갔다** ·
그것은 저장소에 남아야 한다.

`ast`로 사전 항목을 읽어 **실행 상태 메서드만** 내리고 나머지는 저장소에 남겼다.

#### 계층

```
project_paths            → (없음)
project_tree             → project_paths
motor_runtime_store      → project_paths
motor_profile_validation → motor_identity
project_repository       → 위 넷 전부
```

순환 없음.

#### 검증

- 코드 검증 · `ruff check src` 55건 유지
- 실행 검증 · `pytest` **1,028건** 통과
- 실물 검증 · 재시작 후

```
execution_context  state ready · "저장 설정과 실행 설정이 일치합니다"
motor_operation    operation_id motor-4ec1c8a6… · status partial
                   ← 앞서 돌린 전체 검색의 기록이 그대로 읽힌다
런타임 파일        .motor_runtime.json 1,524B · 키 8종
락 파일            ..motor_runtime.json.lock  ← 숨김 규칙(§6-24)이
                   숨김 파일에도 적용된 모양이다
```

#### `project_repository` 분해 누적

| | 시작 | 지금 |
| --- | --- | --- |
| 파일 | 2,100줄 | **1,272줄** (−39%) |
| 클래스 | 1,987줄 · 69메서드 | **1,204줄 · 49메서드** |

남은 군집 · 기동 시 마이그레이션 4종 ~181줄 · 한 번만 도는 코드다.

### 6-48. 전체 재분석 · 스튜디오만 공용 락에 빠져 있었다

분해가 끝난 뒤 구조를 다시 훑었다 · 그 과정에서 나온 유일한 실결함이다.

#### 결함 · 한쪽만 잡는 락

`<프로젝트>/motions/`를 **세 프로세스**가 쓴다.

| 쓰는 쪽 | 락 |
| --- | --- |
| 웹 브리지 `ProjectRepository._atomic_write` | `store.locked_update` **잡음** |
| 매핑 관리자 `motion_mapping_manager:342` | `locked_update` **잡음** |
| **스튜디오 `ProjectStore._atomic_write`** | `atomic_write_text`만 · **안 잡음** |

웹 쪽 주석이 이미 답을 적어두고 있었다.

> 두 쪽이 같은 락 파일에서 만나야 한다.

**스튜디오가 그 자리에 나오지 않았다.** 한쪽만 잡는 락은 아무것도 막지 못한다 ·
원자적 기록은 찢긴 읽기만 막을 뿐, 각자 읽고 각자 쓰면 나중 기록이 앞선 수정을
지운다. §6-24가 남긴 마지막 구멍이다.

#### 시험은 원자성이 아니라 **락을 잡는가**를 본다

`test_project_store_locking.py` 3건 · `locked_update`를 감싸 **어느 경로의 락을
잡았는지** 기록하고 대조한다.

기록이 원자적인지만 보는 시험으로는 이 결함이 **통과해버린다** · 실제로
`atomic_write_text`만으로도 파일은 온전하게 쓰인다.

락을 되돌려 `1 failed`를 확인했다.

#### 함께 확인한 것 · 이상 없음

| 검사 | 결과 |
| --- | --- |
| 순환 import | **0** |
| `getattr(self,'없는이름')` AST 감사 | **0** |
| `pytest` | **1,031건** 통과 |
| `ruff` | 55건 기준선 |

#### 오해였던 것 · `ProjectStore` ≠ `ProjectRepository`

이름이 겹쳐(`create_project`·`delete_project`·`list_projects`) 중복으로 보였다.
**다른 물건이다.**

| | 다루는 것 | 위치 |
| --- | --- | --- |
| `ProjectStore` | 스튜디오 프로젝트 | `runtime/studio_projects/*.json` |
| `ProjectRepository` | 통합 프로젝트 | `project.json` |

겹치는 것은 `motions/` 디렉터리 **하나뿐**이고, 그게 위 결함이었다.

#### 남겨둔 것 · 판단 필요

**죽은 코드 7건 · 약 140줄** · 시험·다른 모듈·프런트 어디에서도 부르지 않는다.

| 함수 | 줄 | 파일 |
| --- | --- | --- |
| `_publish_initial_action_request` · `_wait_for_initial_action_start` · `_wait_for_initial_action_completion` | 71 | `motion_run_manager.py` |
| `interpolate_range` · `scale_time_segment` | 45 | `curve_engine.py` |
| `resolve_display_progress` | 12 | `motion_group_display.py` |
| `selected_published_names` | 12 | `project_service.py` |

**직접 시험이 없는 신규 모듈 2건** · `motor_runtime_store`(420줄) ·
`motor_values`(157줄) · 간접 커버는 된다.

### 6-49. 죽은 코드 209줄 삭제 · 빈 시험 자리 메우기

§6-48이 남긴 두 항목을 처리했다.

#### 죽은 코드 · 연쇄를 세 번 따라갔다

지우면 **그 함수만 부르던 것이 다시 죽는다.** 한 번에 끝나지 않았다.

| 차수 | 줄 | 지운 것 |
| --- | --- | --- |
| 1차 | 150 | `_publish_initial_action_request` · `_wait_for_initial_action_start` · `_wait_for_initial_action_completion` · `interpolate_range` · `scale_time_segment` · `resolve_display_progress` · `selected_published_names` |
| 2차 | 31 | `linear_sample` · `_wait_for_action_result` |
| 3차 | 28 | `_take_action_result` · `_clear_action_results` · `_is_terminal_action_result` |

**삭제 전에 문자열 디스패치를 확인했다.** 이 저장소는 명령을 문자열로 라우팅하는
곳이 있어서(`command_router`) 이름 참조만 세면 살아 있는 것을 지울 수 있다 ·
7건 모두 `'이름'` 형태 0건.

#### 남긴 것 · 구독은 떼지 않았다

`_action_result_callback`은 계속 `action_result` 토픽을 구독한다. 읽는 곳이
없어졌으니 **쓰기만 하는 버퍼**가 됐지만, 구독을 떼는 것은 **이 노드가 토픽에서
빠지는 일**이라 성격이 다르다 · 주석으로 사실을 남기고 별도 판단으로 둔다 ·
모으는 양은 60초로 제한된다.

작은 공개 API 8건(`mark_cycle_ready` · `discover_usb_projects` ·
`clear_selection` 등)도 남겼다 · `clear_selection`은 §8에 계약으로 적혀 있다.

#### 빈 시험 자리 · 1,031 → 1,063건

| 모듈 | 신규 | 무엇을 지키나 |
| --- | --- | --- |
| `motor_values` | 21 | 화면에 그대로 나가는 값 |
| `motor_runtime_store` | 19 | 한 번에 하나만 · 죽은 작업은 시한으로 풀림 |

`motor_values`는 실기에서 관측한 `0x0637`을 그대로 시험값으로 썼다 ·
MINAS 상위 바이트 표식 제거 · Dynamixel 위치 환산의 범위 자르기 ·
그리고 **`unchecked_float`이 `optional_float`과 다른 것**(`inf` 통과)을
붙잡아 둔다 · 합치면 조용히 `None`이 되는 자리다(§6-34).

#### 락 시험을 두 번 고쳤다

처음 쓴 시험은 `common_store.file_lock`을 감시했는데, **저장소 쪽 락에 가려**
데코레이터를 떼어도 통과했다 · 아래로 흐르는 호출이 어차피 락을 잡기 때문이다.

지금은 두 형태를 각각 잡는다.

| 무엇을 떼면 | 무엇이 잡나 |
| --- | --- |
| `@_motor_runtime_locked` 데코레이터 | 구조 시험 · `functools.wraps` 흔적 |
| `with store.file_lock(...)` 본체 | 네 스레드 동시 갱신 · 세부 20개 중 하나도 잃지 않아야 한다 |

둘 다 실제로 떼어 실패를 확인했다.

**감시 대상이 진짜 그 코드인지 봐야 한다** · 아래로 흐르는 호출이 같은 일을
하고 있으면 시험은 통과하고 결함만 남는다.

#### 검증

- 코드 검증 · `ruff check src` 55건 유지 · 미사용 import 0
- 실행 검증 · `pytest` **1,071건** 통과
- 실물 검증 · 4패키지 재빌드·재시작 후

```
실행 컨텍스트  ready
모터 조작      직전 검색 기록 유지
스튜디오       idle · MIDI ok · device_connected true
서보           11.47 deg · Operation enabled · online
오류 로그      없음
```

### 6-50. 모션값 급변 제한 제거 · 2026-09-10 확정

**MIDI 녹화가 정상 동작했는데 66건이 경고로 뜬 것**이 출발점이었다.

#### 왜 지웠나

옛 규칙은 출력축 기준이었다.

```
허용 = max(N°, 모션 범위 × N%)      4단계·범위 360° → 14.4° / 20ms
```

세 가지가 어긋나 있었다.

| 문제 | 내용 |
| --- | --- |
| 감속비를 안 본다 | 감속비 50이면 출력축 14.4°가 모터축 720° · **정격의 2배를 통과시킨다** |
| 모션 범위에 끌려간다 | 범위를 좁히면 허용도 같이 줄어든다 · 모터와 무관한 값 |
| 모터 능력과 무관 | 정격 3,000 rpm은 20ms에 360°다 · 14.4°는 정격의 4%였다 |

**모터 정격 기준으로 바꾸는 안**도 검토했다(§6-45 방식). 그런데 감속비 1인 축에서는
한계가 360°/프레임이 되어 **모션 범위(360°) 안에서는 절대 걸리지 않는다** ·
아무 일도 하지 않는 검사가 남는다.

#### 런타임에 이미 안전망이 있다

```
Err 24  '위치·속도 편차 과대'  ·  2등급  ·  전체 모션 종료
```

모터가 따라가지 못하면 드라이버가 편차 과대로 트립하고 시스템이 전체 모션을
종료한다. 서보 드라이버 자체도 `profile_velocity`·`profile_acceleration`으로
명령을 완충한다. **사전 검사는 런타임이 이미 하는 일을 흉내 내고 있었다.**

#### 지운 것과 남긴 것

| 검사 | 처리 |
| --- | --- |
| 레이어 축 시간 충돌 | **유지** · 결과가 정의되지 않는다 |
| 모션 범위 초과 | **유지** · 기구 한계 · 런타임이 못 잡는다 |
| 포인트 곡선 ↔ 20ms 프레임 불일치 | **유지** |
| **모션값 급변** | **제거** |

제거 범위 · `layer_transition_warnings` · `require_safe_layer_transitions` ·
`transition_safety_level`(저장 필드·명령·UI) · 그래프의 붉은 세로줄 ·
`재생 안전` 단계 선택 · 관련 문구 전부.

`render_project`의 `motion_ranges_deg`·`require_safe_transitions` 인자도 함께
사라졌다 · 급변 검사 전용이었다. `initial_motion_values_deg`는 남는다 ·
**시작값 유지 동작에 계속 쓰인다.**

#### 동작이 바뀐 곳

수동 시작값이 첫 프레임과 멀어도 이제 **막지 않고 그대로 내보낸다.**
값을 유지하다 첫 프레임에서 넘어가는 것은 전과 같다.

```
초기값 10° · 첫 프레임 3.0초 30°
이전 · ValueError('합성 모션값 급변')
지금 · 0~2.98초 10° 유지 → 3.00초 31°
```

#### 판단은 사람이 한다

급변 여부는 **그래프 모양을 보고 사용자가 판별**한다 · 문턱값을 없앴으므로
자동 경고도 함께 사라졌다. 잘못된 문턱값에서 나오는 경고는 없느니만 못하다.

#### 기록만 남기는 위험 · 다축 동기

축이 여럿일 때 한 축만 명령을 못 따라가면 **축 사이 동기가 깨진다** ·
조형물이 의도와 다르게 움직인다. 지금은 AC 서보 1축이라 드러나지 않는다.

- 런타임 보호는 `Err 24`(2등급 · 전체 모션 종료)뿐이고, 이것은 **동기 붕괴를
  막는 것이 아니라 붕괴 후 멈추는 것**이다
- 다축 구성이 붙으면 **재검토 항목**이다
- 그때 볼 것 · 축별 추종 지연 관측 · 명령 속도 상한 · 동기 이탈 감지

#### 검증

- 코드 검증 · `ruff check src` 55건 유지
- 실행 검증 · `pytest` **1,067건** 통과 · Node 245 통과 · **신규 실패 0**
  (Node 실패 6건은 기준선과 동일)
- 실물 검증 · 재빌드·재시작 후 스튜디오 `idle` · 브리지 `ok` · 오류 로그 0

### 6-51. 주인 없는 데이터 점검 · 매핑을 두 번 읽던 곳

§6-50 뒤에 "기능별 정리가 안 된 것 아니냐"는 물음이 나와, **같은 값을 두 번
구하는 자리**를 찾아봤다.

#### 먼저 · 앞서 지목한 `layer_conflicts`는 문제가 아니었다

`transition_warnings`와 같은 부류로 짚었는데, 재보니 아니다.

| | `transition_warnings` (제거됨) | `layer_conflicts` |
| --- | --- | --- |
| 결과의 주인 | **없음** · 3곳에서 계산해 12곳으로 흘렀다 | `workspace_session` 캐시 하나 |
| 호출 6곳의 성격 | 같은 값 재계산 | **목적이 다 다르다** · 화면 갱신 · 렌더 직전 방어 · 초기 위치 · 합치기 검사 |
| 다른 프로세스 | — | `editor_node`는 별도 노드다 |

렌더 직전에 다시 세는 것은 **경계에서 한 번 더 확인하는 것**이라 중복이 아니다 ·
그대로 둔다. **지목이 틀렸으면 고치지 않는 것이 맞다.**

#### 진짜 중복 · 한 요청에 매핑 파일을 두 번 읽었다

```
studio._validate_mapping_locked(project)   → mapping_check() → 읽기+파싱+해시
studio._store.mapping_check(project)       → 또 읽기+파싱+해시
```

검증한 쪽이 이미 읽어둔 것을 **버리고**, 부른 쪽이 다시 읽고 있었다.

네 경로가 그랬다 · 재생 · 초기 위치 이동 · 녹화 시작 · 모션 파일 내보내기.

#### 고친 방법 · 검증이 결과를 돌려준다

```python
def _validate_mapping_locked(self, project) -> Dict[str, Any]:
    check = self._store.mapping_check(project)
    if not check['matches_project']:
        raise ValueError('모션축 설정 파일이 변경되었습니다 …')
    return check          # ← 확인한 것을 넘긴다
```

부른 쪽은 `mapping = studio._validate_mapping_locked(project)` 한 줄이 된다.

| | 이전 | 지금 |
| --- | --- | --- |
| 요청당 파일 읽기 | 2회 | **1회** |
| 1회 비용 | 1.90 ms (읽기+YAML 파싱+SHA-256) | — |

파일이 494 B인데 1.9 ms인 것은 YAML 파싱과 해시 때문이다 · 모션축이 늘면
더 커진다.

#### 시험 이음매

스텁이 `_validate_mapping_locked = lambda _project: None`이었다 · 이제 매핑을
돌려줘야 하므로 `lambda project: Store.mapping_check(project)`로 바꿨다 ·
시험이 곧바로 잡았다(`'NoneType' object has no attribute 'get'`).

#### 검증

- 코드 검증 · `ruff check src` 55건 유지
- 실행 검증 · `pytest` **1,067건** 통과
- 구조 검증 · 스튜디오 노드 전 함수 대상 · **요청당 매핑 읽기 1회** 확인
- 실물 검증 · 재빌드·재시작 후 스튜디오 `idle` · 브리지 `ok` · 오류 0

### 6-52. 결함 · 스튜디오 내보내기 HTTP 500 · 옛 이름이 남아 있었다

사용자가 화면에서 `모션 실행 파일 저장`을 눌러 **HTTP 500**을 받았다.

#### 증상이 헷갈렸다 · 스튜디오는 성공했다

`/motion_studio/response` 토픽을 직접 보니

```
{"success": true, "message": "모션 파일 내보내기 완료", "frame_count": 446}
```

**파일은 실제로 쓰였다.** 실패한 것은 그 뒤 브리지의 후처리다 · 그래서 화면은
실패인데 `motions/`에는 파일이 남는 어정쩡한 상태가 됐다.

#### 원인 · §6-23이 남긴 호출부 하나

```python
# motion_studio_sync.py
return bridge._sync_project_file(...)   # ← 노드에 없는 이름
```

§6-23에서 `bridge._sync_project_file` → `ProjectService.sync_file`로 옮길 때
**이 호출부만 옛 이름으로 남았다.** `AttributeError` → HTTP 500.

시험이 없어서 드러나지 않았고, 화면에서 내보내기를 누를 때까지 **17일** 잠겨
있었다.

#### 왜 §6-47의 감사에 안 걸렸나

그때 만든 감사는 `getattr(self, '이름')` 문자열 형태를 봤다. 이번 것은
**평범한 속성 접근**(`bridge._sync_project_file`)이라 대상이 아니었다.

감사를 하나 더 만들었다 · **다른 모듈이 `bridge.<이름>`으로 부르는데 노드에 그
이름이 없는 경우**를 찾는다 · 지금은 0건.

```
대상 · bridge_node 밖의 모든 모듈
찾는 것 · bridge._xxx 중 MotionWebBridge 에 정의도 대입도 없는 이름
```

#### 시험

`test_motion_studio_export_sync.py` 3건 · 스튜디오가 성공을 돌려주면 브리지가
**그 파일을 프로젝트에 등록하는지**를 본다 · 실패·빈 `file_id`는 등록하지 않는다.
옛 이름으로 되돌려 실패를 확인했다.

#### 곁가지 · 낡은 `__pycache__`

검증 중 **소스와 오류 메시지가 어긋나는** 일이 있었다. 소스에는 새 이름이
있는데 추적은 옛 이름을 가리켰다.

`build/motion_web_bridge/**/__pycache__`에 옛 바이트코드가 남아 있었다 ·
`symlink-install`에서 원본을 되돌려 쓰면 생길 수 있다 · 캐시를 지우니 일치했다.

**검증 중 소스와 추적이 어긋나면 `__pycache__`를 먼저 의심한다.**

#### 남은 것

사용자의 `테스트_2.json`은 **정상적으로 쓰여 있다**(446프레임) · 다만 활성
파일로 등록되지 않았다 · 다시 내보내면 등록된다.

#### 검증

- 코드 검증 · `ruff check src` 55건 유지 · 신규 감사 0건
- 실행 검증 · `pytest` **1,070건** 통과 · 신규 3건 · 되돌림 실패 확인
- 실물 검증 · **통과** · 아래

```
POST /api/motion-studio/export     HTTP 200
success true · file_id 진단_비3.json · frame_count 446
project_sync {synced: true, category: motions}
```

#### 고쳤는데 한 번 더 실패했다 · 낡은 `__pycache__` 때문

고친 뒤 빌드·재시작했는데 사용자가 화면에서 다시 500을 받았다.

시간 순서가 원인이었다.

```
10:06  콜콘 빌드
10:07  서비스 재시작   ← 이때 build/**/__pycache__ 에 옛 바이트코드가 남아 있었다
10:13  캐시 정리       ← 시험은 여기서 통과하기 시작
```

**재시작이 캐시 정리보다 먼저였다.** 실행 중인 프로세스는 옛 코드를 메모리에
물고 있었고, 소스·빌드본은 이미 새것이라 어디를 봐도 원인이 안 보였다.

캐시를 지운 뒤 다시 재시작하니 곧바로 200이 나왔다.

**규칙으로 남긴다 · 고친 코드가 반영되지 않으면 순서를 의심한다.**

```bash
find build install -name __pycache__ -exec rm -rf {} +
colcon build --symlink-install --packages-select <패키지>
systemctl --user restart motion-control.service
```

`--symlink-install`은 소스를 되가리키므로 대개 문제가 없지만, **시험이 옛 소스를
잠깐 컴파일한 적이 있으면** 그 흔적이 남는다 · 이번이 그 경우다(회귀 확인을
위해 옛 이름으로 되돌렸다가 복원했다).

### 6-53. 실패한 내보내기가 화면과 디스크를 어긋나게 두던 문제

§6-52 뒤 사용자가 물었다.

> 에러 뜨고 나서 모션 파일에서 `테스트 2`를 지우고 나니, 저장하려다 에러 뜬
> `테스트 비`가 나오더라

#### 왜 그랬나

서버는 잘못이 없었다 · 목록은 `prepare`가 **디렉터리를 실시간으로 읽는다.**
문제는 **화면이 목록을 다시 읽지 않은 것**이다.

```js
const result = await run(() => exportMotionStudio(name), { onError: … });
if (!result) return null;              // ← 실패하면 여기서 끝
await onMotionFilesChange(result);     // ← 목록 갱신 · 도달하지 않는다
```

§6-52의 결함은 **스튜디오가 파일을 쓴 뒤** 브리지에서 터졌다. 그래서

```
디스크    테스트_비.json  있음
화면      "저장되지 않았습니다"  ·  목록에도 없음
```

이 어긋남이 다음 조작(`테스트 2` 삭제)이 목록을 새로 읽을 때까지 남았다.

#### 고친 것

실패해도 목록을 다시 읽는다.

```js
if (!result) {
  if (failed) await onMotionFilesChange(null);
  return null;
}
```

**서버가 진실이므로 실패했을 때야말로 다시 물어봐야 한다.** 성공 경로만
갱신하면, 쓰고 나서 실패하는 모든 경우에 같은 어긋남이 생긴다.

#### 남는 한계 · 안내 문구는 여전히 "저장 실패"

파일이 써진 뒤 등록에서 실패하면 화면은 실패라고 말하는데 파일은 남는다.
지금은 목록이 곧바로 갱신되므로 **사용자가 눈으로 확인할 수 있다** ·
문구까지 구분하려면 브리지가 "어디까지 됐는지"를 돌려줘야 한다 ·
§6-52를 고쳐 이 경로 자체가 드물어졌으므로 **여기까지만 한다.**

#### 검증

- 실행 검증 · `pytest` 1,070건 통과 · Node **248 통과 · 신규 실패 0**
- 계약 시험 갱신 · 성공·실패 양쪽에서 목록을 새로 읽는지 본다
- 실물 검증 · 캐시 정리 → 빌드 → 재시작 순서로 반영(§6-52의 교훈)

### 6-54. 내보낸 파일이 모션 실행 목록에 가끔 없던 문제

> 가끔 모션파일 내보내기 후 모션 실행에서 파일 이름이 없는 경우가 있다

§6-53과 같은 부류인데 자리가 다르다 · 이번에는 **늦은 응답을 버리기만 한 것**이다.

#### 세대 검사가 응답을 버린다

브라우저는 모든 요청에 `X-Project-Generation`을 실어 보내고, 응답의 세대가
다르면 **이전 프로젝트의 늦은 응답으로 보고 버린다**(`staleProjectResponse`).
프로젝트를 바꾸는 순간 옛 목록이 새 화면에 얹히는 것을 막는 장치다.

문제는 버린 뒤다.

```js
} catch (error) {
  if (loadToken !== fileLoadToken || error?.staleProjectResponse) return;
  …
} finally {
  if (loadToken !== fileLoadToken) return;
  onFilesChanged(files);        // ← files 는 옛 목록 그대로다
}
```

**버리기만 하고 다시 읽지 않는다.** 메시지도 없다 · 화면은 옛 목록을 그대로
보여주고, 사용자는 방금 내보낸 파일이 없다고 본다.

내보내기는 `project.json`을 다시 쓴다(활성 파일 갱신) · 그 근처에서 세대가
움직이면 곧이어 나가는 목록 요청이 걸린다 · **"가끔"인 이유다.**

#### 고친 것 · 한 번만 다시 읽는다

```js
if (error?.staleProjectResponse) {
  staleRetry = !retried;
  if (!staleRetry) setMessage('파일 목록을 다시 읽지 못했습니다. 새로고침하세요');
  return;
}
…
if (staleRetry) void loadFiles(targetFileId, { retried: true });
```

- 세대가 정해진 뒤 **한 번만** 다시 읽는다 · 무한 재시도가 되지 않게 `retried`를 넘긴다
- 두 번째도 실패하면 **조용히 넘어가지 않고 알린다** · 조용하면 사용자는 파일이
  사라진 줄 안다

#### 왜 재시도가 맞나

세대 불일치는 **일시적**이다 · 프로젝트 전환이 끝나면 맞는 세대로 다시 물어볼
수 있다. 반면 버린 채로 두면 **다음 사용자 조작 때까지** 어긋남이 남는다 ·
§6-53에서 본 것과 같은 모양이다.

#### 검증

- 실행 검증 · `pytest` 1,070건 · Node **251 통과 · 신규 실패 0** · 신규 3건
- 시험 · 재시도 존재 · 실패 시 안내 · **재시도는 한 번뿐**
- 실물 검증 · `/api/motion-files` 정상 응답 · 목록 갱신 확인

#### 같은 부류가 더 있다 · 손대지 않음

`staleProjectResponse`를 버리기만 하는 곳이 더 있다.

| 파일 | 위치 |
| --- | --- |
| `motion_data.js` | 매핑 목록 2곳 |
| `motor_config.js` | 3곳 |

같은 증상이 날 수 있으나 **이번 보고와 직접 관련이 없어 남긴다** ·
증상이 보고되면 같은 방식으로 고친다.

## 7. 유지보수 지표 · 신규 코드 규칙안

- 파일 1,000줄 이하 · 함수 60줄 이하
- `Node` 서브클래스 500줄 이하 — **목표에서 내렸다 · 2026-09-10 확정**
  - 크다는 이유만으로 쪼개지 않는다 · 아래 §7-1
- 신규 요청·응답은 `RequestChannel` 또는 srv/action만 사용 · String+JSON 신규 추가 금지
- 프로젝트 파일 기록은 단일 저장 API만 허용
- 패키지 간 Python 직접 import 금지 · 경계는 토픽·서비스 또는 `motion_common`

### 7-1. `Node` 500줄 규칙을 목표에서 내린다 · 2026-09-10 확정

사용자 결정 · **줄 수를 이유로 노드를 쪼개지 않는다 · 필요할 때만 나눈다.**

이번 작업에서 얻은 것과 값을 같이 놓고 본 판단이다.

| | |
| --- | --- |
| 얻은 것 | 4개 노드 분해 · 파일 1,000줄 초과 7 → 6 · 개념 경계가 생김 |
| 값 | 이름 다섯 형태 재발 **3회** · 데코레이터 소실 · 상수 오기 · 시험 이음매 이동 21곳 |
| 남은 것 | `Node` 500줄 초과 **8건 · 시작과 같음** |

큰 노드 셋(`MotionSupervisor` 2,378 · `MidiControlNode` 2,321 ·
`MotionCoordinationNode` 2,270)은 **모터 명령 경로**이거나 **연동 검증이
불가능**하다 · 검증할 수 없는 곳을 기계적으로 쪼개면 위험만 늘어난다.

**나눌 때는 이유가 있어야 한다.**

| 나눌 이유 | 예 |
| --- | --- |
| 상태와 락의 임자가 흐려졌다 | §6-47 `MotorRuntimeStore` |
| 순수 함수가 클래스에 갇혀 있다 | §6-46 `validate_runtime_motor_profiles` 227줄 |
| 물리 검증 단위가 다르다 | §6-32 `EthercatScanner` |
| 같은 파일이 두 관심사를 겸한다 | §6-35 판정과 상태 |

**줄 수는 이유가 아니다.** §7 지표는 계속 재되, 초과 자체를 결함으로 세지 않는다.

## 8. 검증 상태

- 최종 갱신 · 2026-09-09 (§6-41 반영)
- 실기 · joonhoTest 가동 중
  - AC Servo 1축 · Panasonic MADLN05BE · alias 103 · Master 0 · OP
  - **Dynamixel 2대** · XM540-W150(ID 3) · XM540-W270(ID 5) · FTDI FT232H
  - **X-Touch-Ext** · BEHRINGER 1397:00b6 · ALSA card 2
  - Master 1 랜선 분리 · 다른 PC 2대 전원 차단

### 검토 자체(§1~§5)

- 코드 검증 · 완료 · 파일·함수 규모 · 중복 위치 · 의존 방향 · 토픽·파라미터 정의 지점
- 미확인 · 런타임 성능 영향(폴링 대기의 워커 점유량) · 다중 writer 실제 충돌 빈도

### §6 반영분

| 구분 | 상태 |
|---|---|
| 빌드 검증 | 완료 · `colcon build --symlink-install` 31패키지 |
| 정적 검증 | 완료 · `ruff check src` **55건 유지** · 이번 작업 신규 0건 |
| 실행 검증 | 완료 · `pytest` **1,016건 통과** · 실패 0 · 건너뜀 5(DDS 통합, 선택) |
| 데이터 검증 | 완료 · 이동 전후 동치(§6-11) · 모션 파일 78개 전수(§6-2) |
| 이동 감사 | 완료 · 데코레이터 원본 대조 · **AST 기반 `getattr(self,…)` 감사**(§6-41) |
| 실물 검증 | 부분 · 아래 표 |

### 실물 검증 · 통과

| 항목 | 절 |
|---|---|
| 서비스 재시작 8회 · 실행 컨텍스트 자동 적용 · 노드 확인 8건 | §6-20·21·22 |
| AC Servo 물리 스캔 · 모터 서비스 정지·복구 · EtherCAT 재열거 | §6-13·18·32 |
| 프로젝트 EtherCAT 구성 판정 · 미사용 Master 미연결 허용 | §6-13 |
| 모션 스튜디오 레이어 165프레임 · 라우트→서비스→세션 전 경로 | §6-15 |
| 모터 동작 로그 조회 · 보존 정책 · 프로젝트 로그 경로 | §6-17 |
| 설정 `selected`·`applied` 경로 · 실행 세션 일치 | §6-19 |
| 조작 상태 조정 타이머 · systemd 서비스 판정 | §6-22 |
| 엔드포인트 10종 HTTP 200 | 전반 |
| **Dynamixel 물리 검색** · Broadcast Ping · CRC · 패킷 분해 · 모델명 매핑 | §6-33 |
| **매번 새로 물리 검색** · 2회 연속 스캔 값 동일 · `scanned_at` 갱신 | §6-33 |
| **상태 발행 10Hz** · `age_sec` 실시간 · 상태어·오류코드 변환 | §6-36 |
| **전체 검색 시한** · 50초 예산 · EtherCAT 1 + Dynamixel 2 완주 | §6-37 |
| **MIDI 전 경로** · 페이더 → Pickup → 값 변환 → 서보 도달 | §6-38~41 |

MIDI 전 경로 근거 · `raw_value 5820` → `pickup_reference_source motor_feedback` →
명령 `motion −52.111°` → **서보 실측 `−52.111°`** 일치.

### 실물 미검증 · 남은 것

| 항목 | 사유 |
|---|---|
| 다중 PC · 연동 스케줄 · 마스터 판정 | 다른 PC 전원 차단 |
| 조그·절대 이동·서보 제어 | 모터가 실제로 움직인다 |
| 설정 저장·적용·재시작·실행 해제 | 모터 설정을 다시 쓴다 |
| 프로젝트 전환 `clear_selection` 격리 계약 | 가동 중 프로젝트를 바꿔야 한다 |
| 스튜디오 녹화·재생·레이어 편집 | 화면 조작 필요 |
| `recover_interrupted_scan` · 안전 차단 분기 | 중단·이동 상태를 만들어야 한다 |
| `_ping_dynamixel_id` **성공** 반환 | 두 대 모두 Broadcast에 응답해 보조 Ping이 성공 경로로 가지 않는다 |
| 페이더 파킹 **실패** 분기 · 재연결 세대 무효화 | 정상 경로에서 타지 않는 예외 분기 |

### 알려진 것 · 결함은 아니나 손봐야 하는 것

| 항목 | 내용 |
|---|---|
| **매칭표가 화면에 쓰이지 않는다** | `matching_rows`·`matching_summary`는 프런트에서 병합만 되고 **렌더링되지 않는다** · 아래 |
| **`connected_axes` 물리 필드 누락도 화면 영향 없음** | 화면은 발행 토픽의 `motors`를 읽는다 · 아래 |
| 미사용 함수 2건 | `pulse_per_revolution` · `counts_to_degrees` · 호출부 없음 · §6-34 |

#### 정정 · 앞서 '결함'으로 적은 두 건은 화면 영향이 없다

2026-09-09 · 우선순위를 세우며 프런트 소비처를 확인한 결과다.

**매칭표** · `motor_config.js`의 `mergeAcServoScan`·`mergeDynamixelScan`이
`matching_rows`를 `latestScan`에 실어 나르기만 한다 · 읽어서 표를 그리는 곳이
없다. 화면의 `matching-table` 클래스는 설정값·마스터·축 표에 쓰이는 다른
테이블이다.

→ 따라서 §6-33에 적은 "Dynamixel 2대가 매칭표에 안 뜬다"는 **표시 결함이 아니다** ·
애초에 표가 없다. 실제 성격은 **쓰이지 않는 payload**다.

**`connected_axes`** · 화면(`monitoring.js`)이 읽는 `physical_connection_state`는
스캔 응답이 아니라 **발행 토픽 `/motion_control/motion_state`의 `motors`**에서
온다 · 그쪽에는 값이 정상으로 실린다(§6-35에서 실물 확인).

→ §6-35에 적은 "호출 순서 때문에 물리 판정이 안 실린다"도 **화면 영향이 없다.**

**판단** · 둘 다 고칠 이유가 약하다. 매칭표는 **쓰는 곳이 생길 때** 통합 형태를
정하는 편이 낫고, 지금 Dynamixel 행을 넣으면 아무도 읽지 않는 데이터만 늘어난다.
`connected_axes`도 같다. **정리 대상 목록에만 남긴다.**

이 정정은 우선순위 판단을 바꿨다 · 3순위였던 두 항목을 **5순위로 내린다**.

### 6-55. 모터 준비 검사 단일화 · 열 곳이 서로 다르게 검사하고 있었다

검증 · `colcon build` 4패키지 통과 · pytest **1077건 통과**(신규 7건 포함) · ruff 통과

#### 무엇이 갈라져 있었나

"이 축에 명령을 보내도 되는가" 판정이 **열 곳에 복사**돼 있었고, 복사본마다
검사 항목과 **순서**가 달랐다.

| 위치 | detected | fault | servo_on | 알람코드 | 내부리밋 |
|---|:-:|:-:|:-:|:-:|:-:|
| `motion_run_rules._motor_ready_error` | ✅ | ✅ | ✅ | **✅** | ❌ |
| supervisor `_handle_midi_position_batch` · `_request` | ✅ | ✅ | ✅ | ❌ | **✅** |
| supervisor `_handle_ac_servo_jog` · `_absolute_move` | ✅ | ✅ | ✅ | ❌ | ❌ |
| supervisor `_handle_dynamixel_jog` · `_absolute_move` | ✅ | ✅ | – | ❌ | – |
| `manual_motor_commands` × 4 | ✅ | ✅ | 일부 | ❌ | ❌ |

순서도 갈라져 있었다 · MIDI는 `fault → servo_on` · 조그는 `servo_on → fault` ·
같은 고장에 **다른 메시지**가 나왔다.

`manual_motor_commands`는 자기가 검사한 뒤 supervisor로 보내고 supervisor가
**다시** 검사한다. 선검사 자체는 타당하다(왕복을 기다리지 않고 사유를 준다).
문제는 두 검사가 일치해야 하는데 달랐다는 것이다.

#### 흡수하지 않은 차이 · 세 가지 모두 의도다

| 차이 | 이유 |
|---|---|
| 조그·절대이동이 **내부리밋을 안 본다** | 리밋에 걸린 축을 빼내는 수단이 조그다 · 여기서 막으면 복구 방법이 사라진다 · **사용자 확인 완료** |
| MIDI의 `hold` 명령이 **리밋 검사를 건너뛴다** | 이미 걸린 축을 그 자리에 붙잡아 두는 명령이다 |
| **알람코드 검사가 실행 경로에만 있다** | 파일 재생은 사람이 지켜보지 않는 동안에도 돈다 · **사용자 확인 완료** |

세 번째는 코드만으로 판별할 수 없어 통합 전 동작을 그대로 옮긴 뒤 확인을 받았다 ·
**모션 실행에만 제한을 두는 것이 맞다**(2026-09-10 확정).

이유가 검사 항목이 아니라 **감시자의 유무**에 있다. 조그·MIDI는 사람이 화면을
보며 축 하나를 움직이는 중이고, 알람이 떠 있으면 그 자리에서 보인다. 파일 재생은
사람이 자리를 뜬 동안에도 여러 축이 동시에 돈다 · 여기서만 사전에 막는다.

같은 이유로 알람 축을 조그로 빼내는 길이 열려 있어야 한다 · 내부리밋 예외와
**같은 판단**이다.

#### 형태 · 순서를 인자로 받는다

```python
MOTION_RUN_ORDER = ('detected', 'alarm', 'fault', 'servo_on')
MIDI_ORDER       = ('detected', 'fault', 'servo_on', 'internal_limit')
MANUAL_ORDER     = ('detected', 'servo_on', 'fault')
```

`order`가 검사 항목과 순서를 **모두** 정한다 · 목록에 없으면 검사하지 않는다 ·
조그가 `internal_limit`을 빼는 방식이 이것이다. 차이를 주석이 아니라 **코드가
읽히는 형태로** 남겼다.

모터 종류 판정과 내부리밋 판정은 **호출부가 한다** · 노드마다 판정 근거가 다르고
(`_is_ac_servo` · `motor_config_rules.is_ac_servo_motor` · 매핑의 `motor_type`),
그것까지 끌어오면 `motion_common`이 모터 모델을 알아야 한다.

#### 동치 검증

메시지 문구가 한 글자라도 달라지면 화면과 기존 테스트가 깨진다. 통합 전 원본을
그대로 옮겨 적은 참조 구현과 **상태 조합 전수**(state 3 × fault 2 × servo_on 3 ×
errorcode 2 × 모터종류 2 × 리밋 2)로 대조했다 · `test_motor_readiness.py`.

기존 테스트 1077건이 문구를 바꾸지 않았음을 다시 확인한다.

#### 남긴 것

`_collect_servo_action_axes`(supervisor:2100)는 `detected`만 보고 `fault`를 보지
않는다 · **의도다** · 알람 축에 `fault_reset`을 걸어야 하므로 fault를 막으면
복구가 안 된다. 검사가 하나뿐이라 단일화 대상이 아니다.

### 6-56. 실행 슬롯 점유 단일화 · 값 변환 잔여 중복 흡수

검증 · `colcon build` 5패키지 통과 · pytest **1077건 통과** · ruff **기준선 55건 유지**

#### 실행 슬롯 · 두 진입점이 같은 관문을 각자 지키고 있었다

단일 실행(`_start_thread`)과 그룹 실행(`GroupSession.prepare`)은 **슬롯 하나**를
두고 다툰다. 둘 다 같은 세 관문을 통과시키는데 코드가 따로였다.

```
앞선 실행 중인가  →  재생 소유권을 잡을 수 있나  →  모터 상태가 있나
                                                     → 정지 이벤트 초기화
```

관문이 하나 늘 때 한쪽만 고치면 **그쪽으로만 빠져나간다** · §6-55의 준비 검사와
같은 성격의 위험이다.

`MotionRunManager._claim_run_slot()`으로 모았다 · 막히면
`motion_run_rules.RunSlotUnavailable`을 올리고 **응답 모양은 호출부가 만든다**
(그룹 응답은 `execution_id`를 붙인다).

흡수하지 않은 것 둘 · 성격이 다르다.

| 남긴 것 | 이유 |
|---|---|
| 그룹의 **중복 요청 판정** | 같은 `execution_id` 재요청은 슬롯 경쟁이 아니라 중복 전달이다 · 여러 PC가 같은 트리거를 받으므로 재전송이 정상 · 슬롯 판정보다 **먼저** 와야 한다 |
| 단일 실행의 **자동 반복 상태 초기화** | 그룹 실행에는 자동 반복이 없다 |

`ValueError('current motion_state is unavailable')`는 그대로 뒀다 · 명령 라우터가
오류 응답으로 바꾸는 기존 계약이다.

#### 값 변환 · §6-5가 남긴 목록 밖의 완전중복 3곳

§6-5는 **의미가 다른 3곳**을 의도적으로 남겼다. 그 목록에 없으면서 동작이 같은
것이 3곳 더 있었다.

| 위치 | 성격 |
|---|---|
| `motor_config_rules`의 중첩 `optional_int` | 모듈 상단이 이미 가져온 `values.optional_int`를 **가리고 있었다** · 글자까지 같음 |
| `motor_identity.optional_int` | `values.optional_int(v, None)`과 동일 |
| `motion_model.finite_float` | `values.optional_float`와 동일 · **기본값 `0.0`은 계약이라 유지** |

세 번째가 함정이었다 · `project_store:597`이 기본값을 생략한 채 부른다. 공용
구현의 기본값(`None`)으로 갈아끼우면 그곳이 깨진다. 그래서 이름과 기본값을 남긴
채 안쪽만 위임한다.

교체 전 **20종 입력 × 기본값 조합**으로 원본과 대조해 불일치 0건을 확인했다
(`None` · `''` · `'0x10'` · `'3.7'` · `inf` · `nan` · `True` · `b'5'` 등).

### 6-57. 모션 파일·실행 화면 통합 · 배치가 아니라 물음 순서로

검증 · pytest **1077건 통과** · id 유실 0 · 헤드리스 크롬 실측(1680 / 1280 / 1100px)

#### 왜 합쳤나

`모션 파일`과 `모션 실행`이 별도 탭이라 한 가지 일에 탭을 오갔다.

```
스튜디오에서 만들기 → [모션 파일] 골라 재생 등록 → [모션 실행] 재생
```

`재생 등록`은 매핑 파일의 `motion_file_id`를 쓰는 일이고, `plan_builder`가 그
값과 다른 파일의 실행을 거부한다(`plan_builder.py:127`). 즉 **등록은 실행의
전제**인데 화면이 갈라져 있어서, 왜 실행이 막히는지 그 화면에서 알 수 없었다.

상단 메뉴에서 `모션 파일`을 없애고 `모션 실행` 하나로 합쳤다. 옛 경로(`files`)로
들어와도 실행 화면을 연다 · 프로젝트 탐색기와 북마크가 아직 그쪽을 가리킨다.

#### 1차 시도는 실패였다 · 나열은 정리가 아니다

처음에는 두 패널에 같은 `data-motion-panel` 값을 줘서 위아래로 붙였다. 탭 왕복이
스크롤로 바뀌었을 뿐 나아진 것이 없었다. 사용자 지적으로 다시 했다.

**두 번째는 헤드리스 크롬으로 실제 화면을 찍어 놓고 고쳤다.** 추측으로 CSS를
만지는 동안에는 아래 세 가지를 발견하지 못했다.

| 증상 | 실제 원인 |
|---|---|
| 재생 파일명·상태가 `테스트_비...` `모...`로 잘림 | 새 카드가 옛 3열 격자(`.motion-file-summary`) 안에 들어가 있었다 |
| `초기 이동 시 / 간`처럼 라벨이 두 줄로 끊김 | `.compact-field` 라벨에 줄바꿈 방지가 없었다 |
| `사용 순서` 안내가 숨겨지지 않음 | 전역 `.hidden`(09-midi.css)이 `.workspace-guide`(10-…)보다 **먼저** 선언돼 순서에서 졌다 |

세 번째는 이번 작업과 무관한 **기존 결함**이다. 같은 명시도면 뒤에 온 규칙이
이기므로 `toggle('hidden', …)`이 이 요소에는 처음부터 듣지 않았다.
`.workspace-guide.hidden`을 명시해 좁게 고쳤다.

#### 물음 순서로 배치했다

화면을 열었을 때 사용자가 던지는 물음이 곧 위에서 아래 순서다.

```
1. 무엇이 재생되는가   실행 대상 · 재생 파일 / 모션축 설정 / 상태  ← 크게
2. 실행                옵션 + 준비검사·초기위치·1회·연속
3. 다른 파일로 바꾸려면 파일 목록 · 등록·해제·삭제
4. 지금 어디까지 갔나   실행 상태 · 진행 그래프(전체 폭)
5. (가끔) 내용 확인     선택 파일 상세 · 실행 축   ← 접어 둠
```

**가장 큰 결함은 배치가 아니라 정보 부재였다** · 목록에 재생 등록 여부가 아예
없었다. `statusText`는 검증 결과일 뿐이라, 어느 파일이 등록됐는지 알려면 모션축
설정을 열어야 했다. 목록 행에 `재생` 배지와 행 강조를 넣었다.

부차 수치 다섯 개(초기 이동·실행 축·총 시간·연속 동작·주기)는 같은 크기 칸으로
늘어놓지 않고 한 줄에 작게 붙였다. 아홉 칸을 균등하게 두면 정작 알아야 할 셋이
묻힌다.

제어 상자는 셋에서 둘로 줄였다(`실행` / `정지 · 기타`). 가장 덜 쓰는 `부팅 시
자동 재생 예약`이 맨 위 상자를 차지하고 있었다 · 아래로 내렸다.

#### 좁은 화면

`table-layout: fixed`로 검사·시간 칸을 고정하고 파일명만 줄인다. 1180px 이하에서
2열이 1열로 접힌다. 1680 / 1280 / 1100px에서 넘침 0건 · 가로 스크롤 없음을
실측했다.

#### 마무리 · 좌우 동일 분할과 밀도

`모션 파일`과 `실행 상태`를 같은 폭으로 나눴다(`repeat(2, minmax(0, 1fr))`).
실측 1680px에서 **622 / 622 · 높이 273 / 274**로 맞는다.

세로는 이 화면 안에서만 좁혔다 · 상자 여백 12→10 · 묶음 간격 12→10 ·
표 행 여백 5px · 제어 상자 12/16→8/12 · 진행 그래프 320→240px. 전체 폭이 되어
가로가 넉넉해진 만큼 그래프 높이를 줄여도 곡선이 읽힌다.

바깥 화면(연동 등)과 공유하는 규칙이므로 **`.motion-run-workspace` 안으로 범위를
한정**했다. 작업 영역 높이 1252 → **1172px**.

#### 파일이 여러 개일 때 · 실측으로 두 가지를 더 잡았다

행을 늘려 찍어보니 좌우 높이가 어긋났다.

| 파일 수 | 목록 높이 | 스크롤 | 좌 / 우 (고치기 전) | 좌 / 우 (고친 뒤) |
|---:|---:|:-:|---:|---:|
| 2 | 104 | 없음 | 274 / 274 | 274 / 274 |
| 4 | 176 | 없음 | 345 / **274** | 337 / 337 |
| 6 | 248 | 없음 | 417 / **274** | 409 / 409 |
| 10 | 270 | 있음 | 429 / **274** | 431 / 431 |

`align-items: start`라 왼쪽만 길어지고 오른쪽 아래가 비었다 · `stretch`로 바꿔
두 칸이 항상 같은 높이가 되게 했다. 버튼은 `margin-top: auto`로 상자 아래에 붙는다.

두 번째가 더 중요하다 · **높이 제한을 행 경계에 딱 맞춰 두면 "파일이 여섯 개뿐"
으로 보인다.** 열 개일 때도 여섯 줄에서 정확히 잘려 더 있다는 단서가 없었다.
252 → 270px로 어긋나게 두어 일곱 번째 줄이 반쯤 걸치게 했다 · 잘린 줄 자체가
신호다. 여기에 제목 옆 개수 표시(`10개`)를 더했다.

#### 안 건드린 것

- **스튜디오** · 원래부터 파일 관리 요소가 없었다 · 녹화·레이어·편집·내보내기뿐
- **매핑 : 모션 파일 1:1 관계** · "내보낸 파일 중 하나를 고른다"가 곧 이 관계다 ·
  풀려면 매핑 스키마 13곳을 건드려야 하고, 지금 불편의 원인도 아니었다
- **`재생 등록` 이름** · 하는 일에 견주면 "이 파일로 재생 설정"이 가깝지만 확인 전이다
- **상단 표시줄** · 1280px에서 스케줄러 배지가 세로로 깨진다 · 이번 범위 밖의 기존 문제

### 6-58. 화면 회귀 검사 도구 · 그리고 정정 두 가지

#### 정정 ① · 프런트엔드 테스트는 이미 있었다

§6-57 작업 중 "프런트엔드에 테스트가 없다"고 판단했는데 **틀렸다**.
`src/motion_web/web_ui/test/` 에 `.mjs` 테스트가 **38개 · 257건** 있다.

    cd src/motion_web/web_ui && node --test test/

`colcon`·`pytest` 어디에도 걸려 있지 않아 파이썬 쪽만 돌려서는 보이지 않는다.
**이것이 실행 경로에 없다는 것 자체가 결함이다.**

#### 정정 ② · §6-57 커밋이 7건을 깨뜨렸다

돌려보니 13건 실패였고, 직전 커밋 기준선은 6건이었다. 차이 7건이 전부
`motion-files` 경로가 사라진 것을 테스트가 아직 모르는 경우였다.

| 테스트 | 옛 계약 | 새 계약 |
|---|---|---|
| `workspace_navigation` × 3 | `motion-files` 가 execution 그룹 | 경로 자체가 없음 · 옛 `files` 요청은 `motion-run` |
| `workspace_layout` × 3 | `data-motion-panel="files"` 존재 | `run` 하나로 합쳐짐 |
| `project_explorer_navigation` × 1 | 기본 패널 `'files'` | `'run'` |

통합이 의도된 변경이므로 **계약을 새 구조로 갱신**했다. 지우지 않고 반대 단언을
넣었다 · `motion-files` 탭이 어디에도 없을 것 · `data-motion-panel="run"` 이 정확히
하나일 것.

남은 6건은 이전부터 실패하던 것이다 · 전부 **소스 문자열을 정규식으로 대조**하는
테스트라 코드가 다른 파일로 옮겨가면 깨진다(`loadFiles` 가 `motion_file_manager.js`
로 이동한 것 등). 실제 결함이 아니라 낡은 단언이다.

#### 도구 · `tools/ui_smoke.mjs`

기존 테스트는 소스 문자열을 본다 · **그려진 결과는 못 본다.** §6-57 에서 놓쳤던
것들이 정확히 그 빈 곳이었다.

- 파일명이 잘려 `테스트_비...` 로 보이던 것
- 라벨이 `초기 이동 시 / 간` 으로 끊기던 것
- `.hidden` 이 선언 순서에서 져 안내줄이 안 숨던 것

셋 다 소스에는 아무 문제가 없다. 띄워 봐야 보인다.

```
node tools/ui_smoke.mjs                          # 1680 · 1280px
node tools/ui_smoke.mjs --widths 1680,1280,1100
node tools/ui_smoke.mjs --shots /tmp/shots       # 화면별 png 저장
```

모든 작업 화면을 차례로 열어 넷을 본다 · **콘솔 오류·예외** · **가로 스크롤** ·
**잘린 글자**(말줄임·스크롤 상자는 정상으로 보고 제외) · **화면마다 있어야 할 요소**.
회귀가 있으면 종료 코드 1.

**의존성이 없다.** `ws` 패키지 대신 `tools/cdp_client.mjs` 에 필요한 만큼만 구현했다
· 클라이언트 마스킹 · 텍스트 프레임 · 이어붙인 프레임(스크린샷이 수 MB 라 실제로
필요하다). 저장소에 `node_modules` 를 두지 않는 규칙을 검사 도구 하나 때문에 깨면
언젠가 도구가 안 돌아간다.

#### 도구가 실제로 잡는지 확인했다

초록불이 아무것도 뜻하지 않는 도구가 되지 않도록, 일부러 깨뜨려 대조했다.

| 심은 결함 | 결과 |
|---|---|
| 필수 요소 id 변경 | ✅ `요소 없음 #motionRunStartButton` |
| 라벨을 14px 로 클립 | ✅ `글자 잘림 · 실행` · `글자 잘림 · 정지 · 기타` |
| `renderFileRows` 에 예외 심기 | ✅ `예외 · TypeError: … reading 'boom'` |

셋 다 복원 후 다시 통과한다.

#### 한계 · 이 도구가 보지 않는 것

- **웹 브리지가 떠 있어야 한다** · 실제 프로젝트 데이터에 기대므로 완전한 격리는 아니다
- 색·간격 같은 시각 품질은 판단하지 않는다 · 스크린샷은 사람이 본다
- 모터가 도는 중의 화면(실행 중 상태 전이)은 다루지 않는다

### 6-59. 프런트엔드 테스트를 실행 경로에 올리고, 남은 6건을 정리했다

검증 · `node --test` **257건 전부 통과**(이전 251/6실패) · `pytest src/` **1078건**
· `ui_smoke` 1680/1280px 통과

#### 실행 경로 · `test_frontend_js.py`

`.mjs` 257건이 `colcon` 에도 `pytest` 에도 걸려 있지 않았다. **아무도 돌리지 않으니
UI 를 고치고도 깨진 줄 몰랐다** · §6-57 커밋이 7건을 깨뜨린 채 올라간 것이 그
결과다. 도구를 아무리 만들어도 이 구멍은 그대로다.

같은 디렉터리에 이미 `test_css_parts.py` 가 있다 · 옆에 감싸개 하나를 두어
`pytest src/` 한 번에 딸려 오게 했다. 테스트를 옮기지 않는다 · `.mjs` 는 그대로
두고 실행 경로만 잇는다. 실패하면 `not ok` 줄과 재현 명령을 함께 보여준다.

감싸개가 실제로 잡는지 확인했다 · `workspaceForProjectCategory` 를 옛 경로로
되돌리자 `not ok 256` 을 잡았고, 복원하니 다시 통과한다.

#### 남은 6건 · **5건은 낡은 단언, 1건은 실제 결함**

| 실패 | 성격 | 처리 |
|---|---|---|
| `coordination_ui` × 2 | **실제** · `coordination.js:144` 만 전역 `document` 를 잡았다 | 등록부 경유로 한 줄 수정 |
| `motion_automation_ui` × 1 | 화면에서 사라진 `motionAutomationStatus` 를 기대 | 죽은 코드 제거 + 단언 갱신 |
| `motion_execution_registration` × 3 | 코드가 `motion_file_manager.js` 로 옮겨감 | 소유자 기준으로 단언 이동 |

`coordination.js` 는 다른 요소를 모두 주입받은 등록부로 쓰는데 **그 한 줄만**
전역 `document.getElementById` 였다. 그래서 노드 없이 렌더를 검증할 수 없었다.

#### 그러다 실제 퇴행 둘을 찾았다

`motion_execution_registration` 의 단언을 옮기려다, 옛 단언이 요구하던 것 중
**지금 코드에 없는 것**이 있었다. 통과시키려고 단언을 지우는 대신 코드를 확인했고
둘 다 진짜 누락이었다 · `motion_file_manager.js` 추출 때 빠졌다.

**① 서버 거절을 무시한다.** 삭제 엔드포인트는 등록된 파일을 **HTTP 200 에
`success: False`** 로 거절한다(`bridge_node.py:1860`). 예외가 아니므로 `catch` 에
걸리지 않는다. 그런데 코드는 검사 없이 선택을 해제했다.

```js
const payload = await deleteMotionFile(selectedFileId);
selectedFileId = null;          // ← 삭제 안 됐는데 선택이 풀린다
```

클라이언트 선검사가 보통 막아주지만, **다른 브라우저가 방금 등록한 경우** 서버
경로로 간다 · §6-53·§6-54 와 같은 화면 갱신 계열이다.

**② 삭제 후 프로젝트 트리가 갱신되지 않는다.** `onProjectFilesChange` 가
추출된 관리자에 전달되지 않아, 지운 파일이 왼쪽 트리에 남았다.

셋째로 실패 알림이 대화상자에서 메시지 줄로 내려가 있었다 · 놓치기 쉬워 되돌렸다.

#### 실행 경로 정리 결과

```
pytest src/                    1078건 · .mjs 257건 포함
node --test test/              257건 · 개별 실행용
node tools/ui_smoke.mjs        화면 · 수동 (웹 브리지 + 크롬 필요)
```

#### 함께 발견 · `dom.js` 의 허공 등록 25개

화면에서 사라진 요소를 등록부가 계속 가리키는 것이 `motionAutomationStatus`
하나가 아니었다 · **26개**였고 그중 하나를 이번에 지웠다. 딸린 죽은 코드가
JS 69줄이다. `if (el.X)` 로 감싸여 있어 조용히 아무 일도 하지 않는다.

나머지 25개는 다음 정리 대상 · 실행 경로가 이제 이어졌으므로 안전하게 걷어낼 수
있다. 재발 방지 검사(등록부의 모든 id 가 HTML 에 있을 것)를 그때 함께 넣는다.

### 6-60. `api.js` 표로 · 683 → 337줄

검증 · `node --test` **257건** · `pytest src/` **1078건** · `ui_smoke` 통과 ·
브라우저 실측(모션 파일 3행 · 실행 대상 · 상태 8행 정상 적재)

74개 함수가 거의 같은 여섯 줄을 반복했다.

```js
export async function saveMotorConfig(payload) {
  const response = await projectFetch('/api/motor-config', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return readJson(response);
}
```
→
```js
export const saveMotorConfig = (payload) => request('PUT', '/api/motor-config', { body: payload });
```

**이 파일 안에 이미 같은 꼴이 있었다** · `motionStudioRequest` 기반 한 줄 내보내기
8개. 새 관례를 들이는 것이 아니라 있던 것을 파일 전체로 넓혔다.

72개가 한 줄이 됐고, 질의 문자열을 만드는 둘(`fetchMotorEvents` ·
`fetchReadOnlyProjectFile`)만 함수로 남았다. 정의만 있고 쓰이지 않던
`globalJson` 23줄도 걷어냈다.

#### 손으로 옮기지 않았다

74개를 손으로 옮기면 경로 하나를 조용히 틀린다. 변환기를 만들어 옮기고,
**원본과 새 파일에서 (함수 이름 → 메서드 · 경로)를 각각 추출해 대조**했다.

그 대조가 실제로 둘을 잡았다.

| 결함 | 원인 |
|---|---|
| `deleteMotorEventLogFile` 이 **DELETE → GET** | 원본이 여러 줄이라 옵션 인식이 어긋났다 |
| `fetchStatusSnapshot` 이 **시간 제한을 잃음** | 원본이 축약형 `{ timeoutMs }` 이라 `키: 값` 만 보던 정규식이 놓쳤다 |

둘째는 눈으로 보면 통과처럼 보인다 · 기본값 `timeoutMs = 5000` 이 서명에 그대로
남아 있고 호출만 사라졌다. **대조가 없었으면 상태 조회가 영영 안 끝나는 경우를
나중에 겪었을 것이다.** 축약형은 파일 전체에서 그 한 곳뿐임을 확인했다.

#### 테스트 단언 3곳을 옮겼다

`api.js` 의 **호출 모양**을 대조하던 단언이 셋 있었다. 봉투가 바뀌었으므로 검사
항목은 그대로 두고 모양만 새것으로 옮겼다 · 경로와 메서드는 여전히 확인한다.
`fetchStatusSnapshot` 에는 **시간 제한이 실제로 전달되는지** 보는 단언을 더했다 ·
이번에 놓친 것이 그것이다.

### 6-61. `dom.js` 의 허공 등록 26개 · 죽은 코드 162줄

검증 · `node --test` **259건**(신규 2건) · `pytest src/` 1078건 · `ui_smoke` 통과

화면에서 사라진 요소를 등록부가 계속 가리키고 있었다 · **26개**. `el.X` 는
`undefined` 가 되고, 그것을 갱신하는 코드는 `if (el.X)` 안에서 **조용히 아무 일도
하지 않는다.** 오류도 안 난다.

`motionAutomationStatus` 가 그랬다 · 자동 반복 상태 문구를 계산해서 버리고 있었다
(§6-59에서 하나 제거). 나머지 25개를 이번에 정리했다.

#### 지운 것 · 화면과 함께 사라진 기능들

| 사슬 | 내용 |
|---|---|
| 작업 맥락 표시줄 | `updateWorkContext` + `workContextStatus` + `basename` + 양쪽 모듈의 `getWorkContext` · `onWorkContextChange` 통지 5곳 |
| 등록 탭 | `renderRegistrationTabs` · `activeRegistrationTab` · 클릭 처리 · `registrationPanels` |
| 설정 요약 · 파일명 입력 | `registrySummary` · `motorConfigFileNameInput` |
| 프로젝트 상태 표시 | `projectSelectedStatus` · `projectRuntimeStatus` |
| 스튜디오 통합 프로젝트 표시 | `studioWorkspaceName` · `studioWorkspaceFiles` |

합계 **162줄**. `registrationPanels` 도 화면에 없어 `renderRegistrationTabs` 는
첫 줄에서 반환했다 · 뒤따르는 패널 전환까지 막혀 있었지만 그 마크업도 함께
사라진 상태라 숨은 버그는 아니었다.

#### 지우지 않은 것 · **테스트가 지키고 있다**

`studioImportFileSelect` · `studioImportButton` · `studioRecordMode` 셋은 화면
요소만 없고 **JS 와 테스트가 살아 있다**(`motion_studio_ui.test.mjs` 가 클릭까지
검증한다). 스튜디오의 "모션 파일 가져오기" 기능이다.

기능을 지우려고 테스트를 지우는 것은 앞뒤가 바뀐 일이다. **HTML 이 실수로 빠진
것인지, 기능을 접은 것인지는 코드가 답하지 못한다** · 판단이 필요한 항목으로
남긴다. 등록부에서는 뺐으므로 되살릴 때 함께 복구해야 한다.

#### 재발 방지 · `dom_registry.test.mjs`

등록부의 모든 id 가 조립된 HTML 에 있어야 한다. 없으면 어느 것인지 이름을 대고
**"요소를 되살리거나, 등록과 그것을 쓰는 코드를 함께 지우세요"** 라고 알린다.
중복 등록도 함께 본다.

가짜 등록(`ghostElement`)을 넣어 실제로 잡는지 확인했다.

#### 남은 판단 · 조용히 사라진 표시 둘

`motionAutomationStatus`(자동 반복 상태)와 작업 맥락 표시줄은 **화면에서 요소가
빠지면서 정보가 사라진 것**이다. 코드를 지운 것은 죽어 있었기 때문이고, 그 정보가
필요하면 화면과 함께 되살려야 한다 · 사용자 판단 대기.

### 6-62. 스튜디오 안의 옛 "모션 파일 가져오기" 입구 제거

검증 · `node --test` 259건 · `pytest src/` 1078건 · `ui_smoke` 통과

§6-61에서 판단을 미뤄 둔 항목이다. 확인해 보니 **기능은 살아 있고 입구만 옮겨간
것**이었다.

```
옛 입구 (죽음)    스튜디오 화면의 "가져올 모션 파일 선택" ▾ + [가져오기]
                     ↓ onImport
                  importMotionStudioFile({ motion_file_id })
                     ↓
현 입구 (살아있음) 모션 실행 화면의 [스튜디오로] 버튼
                     ↓ addMotionFile (motion_studio.js:1380)
                  importMotionStudioFile({ motion_file_id })   ← 같은 함수
                     ↓
                  POST /api/motion-studio/import               ← 살아 있음
```

HTML 만 `09-panel-studio.html` 에서 사라졌고 뒤쪽은 그대로였다. 새 입구가 더
낫다 · 파일 목록·검증·그래프가 모두 있는 모션 실행 화면에서 고르는 편이,
스튜디오 안 드롭다운에서 이름만 보고 고르는 것보다 낫다.

옛 입구의 죽은 코드 **36줄**을 지웠다 · `renderMotionStudioWorkspace` ·
가져오기 버튼 활성 판정 · 핸들러 연결 · 두 개의 bind.

테스트는 `import` 검증만 빼고 `record` · `stop` · `export` 는 그대로 뒀다 ·
그 함수가 검증하는 것은 "버튼을 누르면 값이 핸들러로 넘어가는가" 이고 나머지
셋은 여전히 화면에 있다.

#### 이번에 배운 것 · `node --check` 는 ESM 문법 오류를 놓친다

블록을 잘라내면서 `});` 의 `);` 가 남았는데 `node --check` 가 통과시켰다.
`.mjs` 테스트 3개가 모듈 적재 단계에서 실패해 잡혔다.

**ESM 은 `node --check` 가 아니라 실제 `import()` 로 확인해야 한다.**

```
node -e "import('./static/js/X.js').then(()=>console.log('OK')).catch(e=>console.log(e.message))"
```

#### 남긴 것 · 녹화 모드 선택

`studioRecordMode` 도 화면에서 사라졌다. 백엔드는 셋을 받는다
(`recording_session.py:27`) · `record` · `overdub`(오버더빙) ·
`append`(이어 녹화). 선택 UI 가 없어 지금은 **`record` 고정**이고 오버더빙과
이어 녹화를 화면에서 쓸 수 없다.

이번 범위에서 제외했다 · 사용자 판단 대기.

### 6-63. `escapeHtml` 4중 정의 · 셋이 작은따옴표를 빠뜨렸다

검증 · `node --test` **261건**(신규 2건) · `pytest src/motion_web` 332건 · `ui_smoke` 통과

`format.js` 가 제대로 된 것을 내보내는데 세 파일이 각자 정의했고, **셋 다
작은따옴표를 빠뜨렸다.**

| 위치 | 문자군 | 사용처 |
|---|---|---|
| `format.js` | `& < > " '` | 공용 |
| `motion_studio.js` | `& < > "` ✗ | 12곳 |
| `motion_studio_graph.js` | `& < > "` ✗ | 2곳 |
| `motion_studio_ui.js` | `& < > "` ✗ | 1곳 |
| `project_explorer.js` | `& < > " '` | 17곳 |

**당장 뚫리지는 않았다** · 세 파일 모두 `x='${…}'` 형태를 쓰지 않는다. 그래서
증상이 없었고, 그래서 넉 달을 살아남았다. 누군가 작은따옴표 속성을 쓰는 날
조용히 뚫린다.

§6-55 의 모터 준비 검사와 **정확히 같은 구조**다 · 같은 판정이 여러 벌로 갈라져
있고, 사본마다 조금씩 다르며, 차이가 겉으로 드러나지 않는다.

넷 다 `format.js` 를 가져다 쓰게 하고 지역 정의를 지웠다.

#### 재발 방지 · `escape_html_single_source.test.mjs`

`format.js` 밖에서 `escapeHtml` 을 정의하면 파일 이름을 대고 막는다. 그리고
`& < > " '` 다섯 문자를 모두 바꾸는지 값으로 확인한다 · **작은따옴표를 빠뜨린
사본이 통과하지 못하게** 따로 못 박았다.

`monitoring.js` 에 가짜 사본을 넣어 실제로 잡는지 확인했다.

### 6-64. 죽은 함수 14개 · 불필요한 `export` 14개

검증 · `node --test` **262건**(신규 1건) · `pytest src/` 1078건 ·
`ui_smoke` 1680/1280px 통과

내보내기만 하고 아무 데서도 쓰지 않는 것이 28개였다. 성격이 둘로 갈렸다.

**A · 완전히 죽은 함수 14개** — 자기 파일 안에서도 안 쓴다 · 지웠다.

| 파일 | 지운 것 |
|---|---|
| `api.js` | `createMotionStudioProject` · `loadMotionStudioProject` · `saveMidiMapping` · `saveProjectFile` |
| `format.js` | `countBy` · `formatHexByte` · `formatRotarySwitch` · `formatYamlHex` · `parseIntegerField` |
| `motor_type_ac_servo.js` | `scanRowButtonAttrs` · `scanRowFromButton` · `yamlRegisteredScanRow` |
| `motor_type_dynamixel.js` | `dynamixelDeviceFromButton` · `dynamixelScanMismatch` |

`api.js` 의 넷은 백엔드 엔드포인트가 살아 있는데 프런트가 안 부른다 · 필요해지면
표에 한 줄 다시 넣으면 된다(§6-60).

**B · 내부 전용인데 `export` 만 붙은 것 14개** — `export` 키워드만 뗐다 ·
`WORKSPACE_DEFAULTS` · `motorFilterKey` · `datasetNumber` 등.

#### 재발 방지 · `no_unused_exports.test.mjs`

쓰이지 않는 `export` 는 둘 중 하나다 · 지울 죽은 코드이거나, 내부 전용인데
`export` 만 붙은 것. 어느 쪽이든 남겨두면 "누군가 쓰겠지" 하고 계속 늘어난다 ·
실제로 28개가 그렇게 쌓였다.

이름을 대고 **"내부 전용이면 export 를 떼고, 아무도 안 쓰면 지우세요"** 라고
알린다. 가짜 export 를 넣어 잡히는 것을 확인했다.

#### 다시 배운 것 · 잘라내기는 기계로 하되 적재로 확인한다

§6-62 와 같은 실수를 또 했다 · 두 줄짜리 화살표 형태를 한 줄 정규식으로 잘라
`);` 가 남았고 `api.js` 가 깨졌다. 이번에도 **`import()` 적재 검사**가 즉시 잡았다.

모든 모듈을 적재해 보는 한 줄을 정리 작업의 기본 절차로 둔다.

```
for f in static/js/*.js; do node -e "import('./$f').catch(e=>console.log('$f', e.message))"; done
```

### 6-65. 실행 범위를 고르게 · 같은 이름의 버튼 두 벌을 없앴다

검증 · `node --test` **265건**(신규 3건) · `pytest src/` 1078건 · `ui_smoke` 통과 ·
브라우저 실측(범위 전환 시 판정·사유 즉시 반영)

#### 무엇이 위험했나

`1회 시작` · `현재 회차 후 정지` 같은 버튼이 **두 화면에 같은 이름으로** 있었다.

```
모션 실행 화면의 "1회 시작"   → 이 PC 만
장비 연동 화면의 "1회 시작"   → 참가한 전체 PC
```

이름으로 구별할 수 없다. "전체를 세우는 줄 알고 이 PC만 세우는" 실수가 가능했다.
중복 항목이 여덟 쌍이었다 · 초기 위치 이동 · 1회 시작 · 연속 시작 · 회차 후 정지 ·
상태 새로고침 · 부팅 자동 재생 · 정지 목표 · 대기 시간.

#### 둘은 배타 관계다

같은 모터 경로를 두 주인이 번갈아 쓴다. 그룹이 도는 동안 브리지가 로컬 실행을
거절한다(`coordination_bridge.local_execution_blocker`). 화면이 그 관계를 전혀
보여주지 않고, 버튼만 두 벌 두고 있었다.

#### 버튼은 한 벌 · 범위가 목적지를 정한다

```
실행 범위  (•) 이 PC 단독        ( ) 그룹 전체
              연결된 모터만 움직임    참가 PC 3대에 동시 전달
```

배타 관계가 **라디오 버튼 하나로** 표현된다. 버튼은 한 벌이고 `motionRunScope()`
가 로컬 경로와 그룹 창구 중 하나로 보낸다.

#### 규칙을 버튼에서 떼어냈다

`coordination.js` 가 버튼의 `disabled` 안에 숨겨 두던 판정을
`groupRunAvailability()` 로 꺼냈다 · 참가 여부 · 실행 중 · PC 수 · 통신/알람 ·
그룹 오류를 보고 **`{ok, reason}`** 을 돌려준다.

실행 화면은 이 규칙을 그대로 쓰고, **못 하는 이유를 그 자리에 보여준다.**

```
[그룹 전체] 초기 위치 이동 · 잠김 · "연결된 다른 PC가 없습니다"
```

전에는 툴팁에만 있어 마우스를 올려야 알 수 있었다.

연동 화면에는 실행 제어 대신 한 줄이 남는다 ·
`그룹 실행 준비됨 · 모션 실행 화면에서 시작하세요`.

#### 시험도 규칙 기준으로 옮겼다

`coordination_ui.test.mjs` 가 `coordinationStartButton.disabled` 를 보던 것을
`groupRun.availability()` 를 보도록 바꿨다 · **위젯이 아니라 규칙을 시험한다.**
버튼이 어느 화면에 있든 규칙은 같아야 한다.

새 시험 셋을 더했다(`motion_run_scope.test.mjs`) · 범위 선택이 존재하는가 ·
다섯 버튼이 모두 범위를 보고 그룹 창구로 가는가 · 연동 화면에 실행 버튼이
남아 있지 않은가.

파이썬 계약 시험(`test_coordination_web_contract.py`)도 새 구조로 갱신했다.

#### 남긴 것

- **`부팅 시 자동 재생 예약` 이 두 곳에 있다** · 연동 화면 것은 그룹 설정
  (`coordinationAutoPlayToggle`), 실행 화면 것은 로컬 자동 반복
  (`motionAutomationEnabled`) · **다른 기능인데 이름이 같다** · 다음 정리 대상
- 실행 준비 검사는 이 PC 기준이라 그룹 범위에서 잠근다

### 6-66. 실행과 연동을 한 화면으로 · 역할을 알린다

검증 · `node --test` 265건 · `pytest src/` 1078건 · `ui_smoke` 1680/1280px ·
브라우저 실측(연동 범위 전환 시 역할·상세 표시)

§6-65 에서 버튼을 한 벌로 줄였지만 화면은 여전히 둘이었다. 실행하려면
`모션 실행` 을 보고, 연동 상태를 보려면 `장비 연동` 으로 옮겨야 했다 ·
**"지금 어느 쪽으로 나가는가"를 두 화면을 오가며 맞춰야 했다.**

`장비 연동` 탭과 패널을 없애고 내용을 실행 화면 안으로 옮겼다.

```
모션 실행
├ 실행 범위   (•) 이 PC 단독      ( ) 그룹 전체
├ [연동 선택 시] 역할 한 줄
│    [이 PC · 마스터] 현재 마스터입니다 · 참가 3대 · 부팅 자동 재생을 이 PC가 몹니다
│    PC별 프로젝트는 유지하고 시작·완료·오류 트리거만 공유합니다 · 실물 미검증
├ 실행 대상 · 실행 제어            ← 범위와 무관하게 늘 같은 자리
├ 모션 파일 · 실행 상태 · 진행 그래프
├ ▸ 연동 상세  설정 · 세션 · 자동 재생 · 참가 PC 명단   ← 접힘
└ ▸ 선택 파일 상세 / 실행 축
```

연동 내용을 그대로 위에 붙였더니 실행 버튼이 **800px 아래로 밀렸다.** 매번 보는
것은 역할 한 줄이면 충분하므로 나머지(설정·세션·자동 재생·명단)를 접이식으로
내렸다 · 화면 높이 2466 → 1665px.

#### 역할은 잠그는 것이 아니라 알리는 것이다

`is_master` 가 실제로 가르는 것은 둘뿐이다 · **부팅 자동 재생**(`_drive_auto_play`)
과 **다중 마스터 감지**(`_check_multiple_masters`). 그룹 시작 자체는
`_start_group_execution` 이 `_joined` 만 요구한다 · 참가한 PC 면 누구나 가능하다.

그래서 역할로 버튼을 잠그지 않았다. **정지는 어디서든 되어야 한다.**
대신 어디서 몰아야 하는지 알린다.

```
마스터   현재 마스터입니다 · 참가 3대 · 부팅 자동 재생을 이 PC가 몹니다
슬레이브 현재 마스터는 joonhoTest 입니다 · 보통 마스터에서 시작합니다
```

#### 같은 이름의 자동 재생 둘 · 범위가 갈랐다

`부팅 시 자동 재생 예약` 이 두 곳에 있었고 **다른 기능**이었다 · 하나는 그룹
설정(`coordinationAutoPlayToggle`), 하나는 로컬 자동 반복(`motionAutomationEnabled`).
합치면서 같은 화면에 나란히 놓이게 되어 더 위험해졌다.

범위마다 자기 것만 보이게 하고 이름에 범위를 박았다 ·
`이 PC 부팅 시 자동 재생` / `그룹 부팅 시 자동 재생`.

#### 시험이 잡은 것

옛 구조를 못박은 계약이 넷 있었다 · `dom_registry`(사라진
`coordinationRefreshButton`) · `servo_alarm_ui`(상위 탭 목록) ·
`test_index_compose`(패널 목록) · `test_coordination_web_contract`.

마지막 것이 **`실물 미검증` 문구가 사라진 것**을 잡았다 · 연동 화면 머리말과 함께
날아갔는데, 그룹 실행이 아직 실물 검증 전이라는 경고라 되살려야 했다 ·
연동 범위를 고를 때마다 보인다.

### 6-67. 탭 배치 정리 · 이름과 내용을 맞췄다

검증 · `node --test` 265건 · `pytest src/` 1078건 · `ui_smoke` 11화면 통과

```
전                                    후
운영      모니터링·로그·btop            운영      모니터링·서보 에러·로그·btop
프로젝트·장비  시스템·연동·모터·서보에러   프로젝트·장비  시스템 정보·프로젝트 관리·모터 관리
모션 제작  모션축·MIDI·스튜디오          모션 제작  (그대로)
실행·시험  동작 테스트·모션 파일·모션 실행 실행·시험  동작 테스트·모션 실행
```

**서보 에러 관리** · [프로젝트·장비]에 있었지만 설정이 아니라 **운영 중 대응**이다 ·
[운영]으로 옮겼다.

**시스템 정보** · 세 가지를 담고 있어 탭 이름과 내용이 어긋났다.

```
① 운영 상태          컴퓨터명·접속주소·Git·마지막 갱신   ← 남김
② 프로그램 상태·복구  상태 확인·화면 갱신·재시작          ← 남김
③ 프로젝트 관리      생성·삭제·복사·가져오기·메모·파일 편집기 → 새 탭
```

③을 `프로젝트 관리` 탭으로 갈랐다(`03b-panel-project.html`). 프로젝트 탐색기가
파일을 열 때 이동하는 대상도 그쪽으로 바꿨다.

#### 하지 않은 것 둘 · 검토가 과했다

**정지 버튼 통합** · 5화면 8개가 흩어져 보였지만 각각 다른 일을 한다.

| 버튼 | 하는 일 |
|---|---|
| 상단바 `전체 동작 정지` · `긴급 정지` | 전역 · 항상 보임 |
| 모션 실행 `즉시 정지` · `현재 회차 후 정지` | 범위(이 PC/그룹)에 따라 · 후자는 **다른 기능**이다 |
| 동작 테스트 `모터 동작 정지` | 같은 안전 정지 + **결과 기록**(`lastOutputCapture`) |
| 스튜디오 `녹화·재생 정지` | 스튜디오 세션 정지 |

같은 API 를 부르는 것은 상단바와 동작 테스트뿐이고, 후자는 시험 결과를 남긴다 ·
지우면 그 기록이 사라진다. **중복이 아니라 문맥별 정지다.**

**`프로젝트 정보 파일` 제거** · 개발자용 원본 표시로 봤는데, 사이드바가 파일을
여는 **편집기**였다 · 프로젝트 관리 탭으로 함께 옮겼다.

둘 다 화면만 보고 판단했다가 코드를 확인하고 접었다.

#### 남긴 판단 · 영구 숨김 버튼 2개

`motionAutomationStartButton` · `motionAutomationReserveButton` 은
`style="display:none"` 이고 라벨이 `(숨김)` 이다. 핸들러
(`startCurrentMotionAutomation` · `reserveCurrentMotionAutomation`)가 살아 있고
시험도 있다 · **접어 둔 기능이지 죽은 코드가 아니다** · 사용자 판단 대기.

### 6-68. 스케줄이 단독 실행에도 적용된다

검증 · `pytest src/` **1082건**(신규 4건) · `colcon build` 2패키지 ·
`node --test` 265건

#### 조용히 실패하고 있었다

스케줄 노드는 `start_group` 과 `stop_after_cycle` 을
`/api/coordination/control` 로만 보냈다 · **그룹 전용이었다.**

연동을 쓰지 않는 PC 에서도 스케줄은 **발화한다** ·
`resolve_master_role` 이 연동 미사용을 "단독 동작으로 간주" 하며 마스터 판정을
통과시키기 때문이다(`coordination.py:72`). 그리고 그룹 명령이
**"먼저 DDS 그룹에 참가하세요"** 로 매번 실패했다.

화면에는 아무 표시가 없다 · 로그를 보지 않으면 "스케줄이 안 돈다"는 것만 알 수
있었다.

#### 범위로 갈라 보낸다

```
연동 사용 (enabled)   → /api/coordination/control  start_group / stop_after_cycle
단독                  → /api/motion-run/start · /api/motion-run/stop-after-cycle
```

판정은 연동 설정 파일의 `enabled` 하나다 · 파일이 없거나 읽지 못하면 **단독으로
본다** · 그룹 명령이 실패하는 것보다 낫다.

#### 화면 없는 호출자를 위해 서버가 채운다

로컬 실행은 `motion_file_id` · `mapping_file_id` 를 요구한다. 화면은 무엇을
재생할지 알고 보내지만 **스케줄러는 모른다.**

`motion_automation_configure` 가 이미 프로젝트의 활성 파일을 채우고 있었다 ·
그 부분을 `_with_active_project_files()` 로 떼어 `motion_run_start` 도 쓰게 했다.
프로젝트가 "재생 등록" 으로 정해 둔 값을 그대로 쓴다.

빠진 값만 채우므로 화면에서 오는 요청은 그대로다.

#### 시험

노드를 띄우려면 `rclpy` 가 필요하므로 **어느 엔드포인트로 나가는지**를 소스에서
확인한다(`motion_schedule/test/`) · 두 경로가 모두 있는지 · 연동 경로가 먼저
갈라지는지 · 설정을 못 읽으면 단독으로 보는지 · 브리지가 활성 파일을 채우는지.

단독 경로를 지워 보고 실제로 잡히는 것을 확인했다.

### 배포 잔여

**다른 PC 2대는 `colcon build` 필수** · `motion_common` 외 신규 패키지 다수 ·
`motion_coordination_interfaces`에 Action 추가 ·
미빌드 시 `ModuleNotFoundError`로 노드 기동 실패 · `docs/HANDOFF.md` §3-①

**원격 미반영** · 이 저장소는 로컬에 **61커밋** 앞서 있다 · 원격 최신 `dbe1256`.

### 미해결 결함

`motion_supervisor` 수신 정지 · §6-14 · 근본 원인 미특정 · 재발 판별법만 기록
