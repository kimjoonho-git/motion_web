# Motion Studio 구조 정리 계획

작성 기준: 2026-08-03 · `wip-next`

## 목적

- 기능 동작을 유지하면서 Motion Studio의 화면, 웹 브리지, 백엔드 책임을 분리한다.
- 파일만 나누어 연결 코드가 증가하지 않도록 컨트롤러와 데이터 경계를 먼저 정의한다.
- 프로젝트별 데이터와 비동기 결과의 격리를 유지한다.
- 모터 제어는 기존 `motion_system` 통로만 사용한다.

## 현재 구조

### 프런트엔드

- Motion Studio JavaScript: 약 6,029줄
- `motion_studio.js`: 약 2,999줄
- 그래프 이벤트: `motion_studio_graph_interactions.js`
- 포인트 편집 이벤트: `motion_studio_point_editor.js`
- 재생 상태: `motion_studio_playback.js`
- 레이어 관리 표시: `motion_studio_layer_manager.js`

`motion_studio.js`에는 프로젝트 상태, 레이어 작업, 편집기 작업, 축 관리, 저장,
실행 취소, API 요청, 렌더링 및 일부 버튼 연결이 함께 남아 있다.

### 웹 브리지

- `bridge_node.py`: 약 8,311줄
- Motion Studio ROS 요청·응답, 상태, 저장 동기화 및 FastAPI 경로가 여러 위치에 분산되어 있다.

### Motion Studio 백엔드

- 전체 Python 코드: 약 4,924줄
- `studio_node.py`: 약 1,063줄
- `project_store.py`: 약 744줄
- `layer_editor.py`: 약 662줄

레이어 명령, 재생 및 녹화 저장은 분리됐지만 프로젝트 전환, 실행 컨텍스트,
합성, 녹화 준비, MIDI 0 복귀 확인, 내보내기 및 상태 생성은 `studio_node.py`에 남아 있다.

## 정리 우선순위

### 1. 프런트엔드 컨트롤러 분리

- `motion_studio.js`: 초기화와 컨트롤러 연결만 담당
- `motion_studio_layer_controller.js`: 레이어 선택·생성·복사·삭제·병합
- `motion_studio_editor_controller.js`: 편집기 열기·닫기·미리보기·반영·저장·이력
- `motion_studio_axis_editor.js`: 축 추가·복사·삭제
- 기존 그래프·포인트·재생·레이어 표시 모듈은 유지

목표: `motion_studio.js`를 1,000~1,500줄 이하로 줄인다.

### 2. 웹 브리지 Motion Studio 경계 분리

- `motion_studio_bridge.py`: ROS 요청·응답·상태
- `motion_studio_sync.py`: 레이어 저장 동기화·서명·프로젝트 전환 결과 검증
- `motion_studio_routes.py`: `/api/motion-studio/*` 경로 등록

`bridge_node.py`에는 서비스 초기화와 공통 시스템 기능만 유지한다.

### 3. 백엔드 `studio_node.py` 정리

- `recording_session.py`: 녹화 시작·준비·MIDI 0 확인·종료
- `workspace_session.py`: 프로젝트 선택·실행 컨텍스트·합성 캐시
- `export_service.py`: 최종 모션 파일 생성·내보내기
- `studio_node.py`: ROS 초기화·콜백·명령 분배·상태 발행

목표: `studio_node.py`를 500~650줄 이하로 줄인다.

### 4. 공통 모델과 중복 함수 통합

- `motion_model.py`
  - `normalize_layer`
  - `unique_motion_ids`
  - `layer_motion_ids`
  - 곡선 범위 계산
- `mapping_model.py`
  - 모션 범위 변환
  - 수동 초기값 변환
- 20ms 시간 상수는 `constants.py`에서 직접 가져온다.

정리 대상 중복:

- `layer_motion_ids`: `layer_commands.py`, `editor_node.py`
- 곡선 범위: `layer_editor.py`, `point_curve_operations.py`
- 모션 범위·초기값 변환: `studio_node.py`, `editor_node.py`
- 저장소를 거쳐 가져오는 `DEFAULT_PERIOD_SEC`, `normalize_layer`, `unique_motion_ids`

### 5. 대형 계산·그래프 파일 분리

`motion_studio_calculations.js`는 다음 세 영역으로만 분리한다.

- `motion_studio_project_model.js`
- `motion_studio_point_model.js`
- `motion_studio_editor_math.js`

`motion_studio_graph.js`는 다음 두 영역으로 분리한다.

- 트랙 생성·샘플링
- Canvas 렌더링

파일 수만 늘어나지 않도록 이보다 더 세분화하지 않는다.

### 6. 테스트 구조 정리

- 프로젝트·레이어
- 편집기 상태
- 포인트 편집
- 그래프 이벤트
- 저장·실행 취소
- 재생·녹화

구현 문자열 검사는 필수 DOM ID와 화면 문구에만 사용한다. 기능 검증은 입력과 결과를
확인하는 동작 테스트로 전환한다.

## 유지 대상

현재 책임이 비교적 명확해 우선 분리하지 않는 파일:

- `motion_studio_point_editor.js`
- `motion_studio_graph_interactions.js`
- `motion_studio_playback.js`
- `motion_studio_layer_manager.js`
- `layer_commands.py`
- `project_commands.py`
- `playback_session.py`
- `curve_engine.py`
- `ros_gateway.py`
- `operation_state.py`

## 단계별 검증 기준

각 단계에서 다음을 확인한다.

1. 기능별 단위 테스트
2. 웹 UI 전체 테스트
3. Motion Studio 백엔드 전체 테스트
4. 웹 브리지 전체 테스트
5. 서로 다른 두 프로젝트의 데이터·비동기 결과 격리
6. `motion_web_ui`, `motion_web_bridge`, `motion_studio` 빌드
7. 브라우저 모듈 HTTP 200 및 JavaScript 초기화 오류 없음
8. 실제 장비를 사용하지 않은 결과는 `실물 미검증`으로 표시

## 권장 진행 순서

1. 프런트엔드 레이어·편집기·축 컨트롤러
2. 웹 브리지 서비스·동기화·API 경로
3. 백엔드 녹화·워크스페이스·내보내기
4. 공통 모델과 중복 함수
5. 계산·그래프 파일
6. 대형 테스트 파일
7. 전체 코드 수·의존성·성능 재측정
