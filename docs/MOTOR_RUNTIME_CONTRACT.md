# Motor Runtime Contract

Contract version: `3`

## 1. 결론

- 프로젝트 모터 설정 파일은 사용자가 편집·저장하는 유일한 원본이다.
- Motor Manager는 설정 파일 경로를 요구하므로 실행용 불변 RuntimeSession
  파일 하나는 필요하다.
- RuntimeSession은 별도 설정 원본이 아니라 프로젝트 파일에서 생성한 내부
  실행 스냅샷이다.
- 사용자는 RuntimeSession을 선택·편집·삭제하지 않는다.

Motor Manager가 프로젝트 파일을 직접 실행하도록 하면 프로젝트 편집 후
서비스만 재시작돼도 아직 적용하지 않은 값이 실행될 수 있다. 또한 정확히
어떤 설정이 실행됐는지와 실패 시 복원할 설정을 확정하기 어렵다. 따라서
프로젝트 파일 직접 실행 대신 불변 스냅샷 하나를 사용한다.

## 2. 최소 파일 구조

### 프로젝트 파일

- 현재 프로젝트의 `motor_axes.yaml`
- 사용자가 저장하는 유일한 모터 설정
- 모든 변경과 비교의 기준

### RuntimeSession

- 현재 프로젝트 파일의 검증된 내용으로 생성한
  `runtime/sessions/motor-<sha256>.yaml`
- 생성 후 수정하지 않는 읽기 전용 실행 파일
- 프로젝트 설정 내용이 같으면 기존 세션을 재사용

### 실행 대상 기록

- 전역 `.motor_lifecycle_state.json` 하나
- 활성 프로젝트 ID, 프로젝트 세대, 활성·직전 세션 ID, 프로젝트 내부 상대
  경로와 SHA-256을 기록
- 현재 operation ID, 작업 단계와 최종 결과를 같은 상태에 기록
- 모터 설정 내용을 중복 저장하지 않음

`runtime/applied_motor_config.yaml`과 같은 중간 설정 사본은 사용하지 않는다.
선택 프로젝트 파일에 실행 프로젝트를 함께 기록하는 호환 필드와 이전 구조
fallback도 사용하지 않는다. 이전 형식은 실행 시 해석하지 않고 일회성 변환
도구로만 새 형식으로 이전한다.

## 3. 저장과 적용 구분

- `프로젝트 설정 저장`은 `motor_axes.yaml`만 변경한다.
- 저장 후 프로젝트 SHA와 활성 RuntimeSession SHA가 다르면
  `적용 및 재시작 필요`로 표시한다.
- `설정 적용 및 재시작`을 실행해야 새 RuntimeSession을 만들고 실행 대상을
  변경한다.
- 적용 성공 전까지 기존 RuntimeSession은 변경하지 않는다.
- 적용 실패 시 검증된 이전 RuntimeSession이 있을 때만 이전 실행을 복원한다.

프로젝트 파일과 RuntimeSession의 값이 다른 것은 파일 충돌이 아니라
`저장 후 미적용` 상태다. 화면에는 두 파일을 보여주지 않고 이 상태만 표시한다.

## 4. 명시적 실행 소유권

- `.motor_lifecycle_state.json`과 RuntimeSession의 생성·선택·복원 권한은
  MotorLifecycleCoordinator만 가진다.
- RuntimeSession을 사용하는 모든 노드에 `project_id`,
  `project_generation`, `runtime_session_id`를 명시적으로 전달한다.
- 소비 노드는 파일 경로에서 프로젝트 소유권을 추측하지 않는다.
- Web Bridge, RuntimeStateMonitor와 서비스 시작 스크립트는 실행 대상 기록을
  직접 수정하지 않는다.
- 선택 프로젝트 변경만으로 실행 프로젝트를 자동 변경하지 않는다.
- 선택 프로젝트와 실행 프로젝트가 다르면 모터 제어를 허용하지 않고
  `설정 적용 및 재시작 필요`를 표시한다.

## 5. 적용 완료 조건

다음 조건을 모두 만족해야 적용 완료다.

1. 현재 선택 프로젝트에서 RuntimeSession 생성
2. RuntimeSession SHA-256 재검증
3. Motor Manager가 정확한 RuntimeSession으로 시작
4. RuntimeStateMonitor가 같은 프로젝트·세션 정보로 시작
5. 새 RuntimeFeedback 수신
6. 프로젝트 설정의 전체 사용 축 확인
7. 실행 대상 기록을 새 세션으로 원자적 변경

설정 파일 생성이나 프로세스 존재만으로 성공 처리하지 않는다.

## 6. 세션 보관

- 활성 RuntimeSession과 검증된 직전 RuntimeSession만 보관한다.
- 나머지 미사용 세션은 적용 작업이 없을 때 정리한다.
- 활성 세션이 있는 프로젝트는 실행 해제 전 삭제하지 않는다.
- 프로젝트 삭제 시 다른 프로젝트 세션과 실행 대상 기록을 변경하지 않는다.

## 7. 동시 작업

- 설정 적용, Motor Manager 재시작과 물리 검색은 동시에 하나만 실행한다.
- 작업마다 operation ID를 사용한다.
- 제한시간 초과, 복구 및 롤백은 한 번만 확정한다.
- 이전 작업의 완료 이벤트를 새 작업에 적용하지 않는다.

## 8. motion_system 관계

- `motion_system`은 모터 통신과 제어의 단일 통로다.
- RuntimeSession은 기존 Motor Manager의 `config_file` 입력으로만 전달한다.
- 이 계약을 위해 `motion_system` 내부 통신 경로를 추가하거나 우회하지 않는다.
