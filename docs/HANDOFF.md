# 인수인계

- 최초 작성 · 2026-08-22 (출장 중 Windows 작업 대비)
- 최종 갱신 · 2026-09-09 (§6-41 반영)
- 브랜치 · `refactor/motion-common-extract`
- 마지막 커밋 · `bc5268a` · **원격보다 61커밋 앞섬**(원격 최신 `dbe1256`)
- 상황 · Linux 실기(joonhoTest) 가동 중
  - AC Servo 1축 · MADLN05BE · alias 103 · Master 0 · OP
  - **Dynamixel 2대** · XM540-W150(ID 3) · XM540-W270(ID 5) · FTDI FT232H
  - **X-Touch-Ext** MIDI · ALSA card 2
  - Master 1 랜선 분리 · 다른 PC 2대 전원 차단

> 08-22 작성분은 출장 중 Windows 전용 지침이었다. 복귀 후 실기에서 4단계를
> 크게 진행했으므로 §2 이후를 현행화했다. §1(Windows 제약)은 다음 출장 때
> 다시 쓸 수 있어 남긴다.

---

## 1. Windows에서 되는 것과 안 되는 것

이 워크스페이스는 ROS 2 Humble + 실제 모터 하드웨어를 전제로 한다. Windows는
**코드 편집과 단위 테스트 전용**으로 보는 것이 정확하다.

| 항목 | Windows | 비고 |
|---|---|---|
| 코드 편집 · git | ✅ | 제한 없음 |
| `pytest` (motion_common) | ✅ | 153건 · ROS 불필요 |
| `pytest` (전체) | ❌ | `rclpy` 등 ROS 패키지 필요 |
| `ruff check` | ✅ | 정적 바이너리 |
| `colcon build` | ❌ | ROS 2 Windows 설치가 있어도 이 워크스페이스는 Linux 전제 |
| 노드 실행 · 실물 검증 | ❌ | 복귀 후 |

### Windows에서 할 수 있는 최소 준비

```powershell
git clone https://github.com/kimjoonho-git/motion_web.git
cd motion_web
git switch refactor/motion-common-extract

pip install pyyaml pytest ruff
$env:PYTHONPATH="src/motion_common"
pytest src/motion_common -q          # 153건 통과해야 정상
ruff check src/motion_common         # 무결점이어야 정상
```

`motion_common`은 `rclpy`에 의존하지 않도록 설계했고
(`test_package_boundaries.py`가 이 성질을 지킨다), `fcntl`도 Windows에서는
없는 채로 동작하도록 처리해 두었다 — 다만 **프로세스 간 락이 사라진다.**
Windows에서 파일 기록 관련 동작을 판단할 때 이 차이를 잊지 말 것.

### 주의 · Windows에서 검증할 수 없는 것

`motion_common` 밖의 변경은 Windows에서 **테스트로 확인할 수 없다.**
`bridge_node`·노드 코드를 고치면 문법과 ruff만 통과할 뿐, 회귀는 복귀 후에야
드러난다. 그러므로 출장 중에는 아래를 권한다.

- `motion_common` 안에서 완결되는 작업만 진행
- 노드 코드는 **읽고 계획만** · 실제 수정은 복귀 후
- 부득이 고쳤다면 커밋 메시지에 `[미검증]`을 남길 것

---

## 2. 현재 상태

### 로드맵 (`docs/ARCHITECTURE_REVIEW.md` §5)

| 단계 | 상태 |
|---|---|
| 0 · lint · pytest 설정 · 지표 | ✅ 완료 |
| 1 · `motion_common` 신설 | ✅ 완료 · 9모듈 |
| 2 · `RequestChannel` 단일화 | ✅ 완료 |
| 3 · 토픽 상수 단일화 | ✅ 완료 |
| 4 · `bridge_node` 분해 | ✅ 완료 (기준 A) · 서비스 6+3개 · -73% |
| 5 · 영속 계층 통합 | ✅ 완료 · 직접 기록 잔여 0 · 재진입 락 |
| 6 · Action 전환 | ✅ 완료 · 스캔 전환 · 초기화·모션 실행은 전환 안 함(§6-28) |
| 7 · 프런트엔드 빌드 | ⬜ **미착수 ← 다음 번호 단계** |
| 8 · 하드웨어 스캐너 분리 | ⬜ 미착수 · 범위 협의 필요 |

### §5 분해 목표안 · 어디까지 왔나 · `docs/ARCHITECTURE_REVIEW.md` §6

| 절 | 작업 |
|---|---|
| §6-9 | 순수 함수 추출 1~3차 |
| §6-11 | 불변 경로 인자화 · 4차 |
| §6-13 | 판정 로직 추출 · 5차 |
| §6-15 | `MotionStudioSession` · 서비스↔노드 순환 절단 |
| §6-16 | 빈 위임 층 제거 (`MotorService` · `MotionRunService` 삭제) |
| §6-17 | `MotorEventLog` |
| §6-18 | `ScanOrchestrator` |
| §6-19 | `MotorConfigService` · 가변 상태 이관 |
| §6-20 | `ExecutionContextService` |
| §6-21 | `ManualMotorCommandService` |
| §6-23 | `ProjectService` · 4단계 목표안 완료 |
| §6-24 | 영속 계층 통합 · 재진입 락 · 숨김 락 파일 |
| §6-26 | 스캔 Action 전환 · 진행·취소 |
| §6-29~31 | `GroupSession` · `PlanBuilder` · `MotionPlayer` |
| §6-32~36 | `monitor_node` 분해 완료 · 2,884 → 866줄 |
| §6-37 | 전체 검색 시한 결함 수정 |
| §6-38~41 | `midi_control_node` 분해 · 3,354 → 2,397줄 |

**목표안 넷 중 둘 완료** · `bridge_node` · `monitor_node` ·
`midi_control_node`는 넷 중 셋(`MidiDecoder` 잔여) ·
`motion_run_manager`는 넷 중 셋(`StatusStore` 잔여).

### 지표

```
bridge_node          7,407 → 2,037줄  (-73%)
MotionStateMonitor   2,884 →   866줄  (-70%)  메서드 78 → 20
midi_control_node    3,354 → 2,397줄  (-29%)  클래스 3,025 → 2,321
motion_run_manager   3,693 → 1,435줄  (-61%)
테스트               68파일(실패 11) → 1,016건(실패 0) · 건너뜀 5
ruff                 잔여 55건 (신규 0 · 기준선 유지)
파일 1,000줄 초과     7 → 6개
Node 클래스 500줄 초과   8개 (변화 없음) · 최대 MotionSupervisor 2,378줄
```

기준선 · `docs/metrics/baseline-20260822.json`
현재 · `docs/metrics/after-decomposition-25.json`
의존 지도 · `docs/metrics/bridge-state-map-20260908.json`

```bash
python3 scripts/code_metrics.py --baseline docs/metrics/baseline-20260822.json
```

---

## 3. 남은 부채 · 우선순위

### ① 슬레이브 PC 2대 재빌드 — 여전히 미수행

두 대(`floating3-Ecolite-Series`, `pc-a`)가 아직 구코드(`7772c6b`)다. 전원이
꺼져 있어 이번에도 못 했다.

```bash
git fetch origin
git switch refactor/motion-common-extract
./scripts/build_and_restart.sh
```

**빌드하지 않으면 노드가 `ModuleNotFoundError`로 죽는다** — `motion_common`이
신규 패키지다. 그리고 이 빌드를 해야 마스터 판정 수정(§6-4)이 실효를 갖는다.
지금은 슬레이브도 자신을 마스터로 보므로, 슬레이브에 스케줄이 등록돼 있으면
중복 발화한다. 각 PC에서 확인할 것:

```bash
find ~/ros2_ws/motion_projects -name schedule_store.json -exec sh -c 'echo "== $1"; cat "$1"' _ {} \;
```

빈 배열이면 안전.

### ② MIDI — **검증 완료** (2026-09-09)

X-Touch-Ext를 붙이고 페이더에서 서보까지 한 줄로 확인했다.

```
raw_value 5820 → pickup_reference_source motor_feedback (기준 −33.378°)
             → 명령 motion −52.111° → 서보 실측 −52.111° 일치
```

**남은 미검증** · 페이더 파킹 *실패* 분기 · 재연결 세대 무효화 ·
정상 경로에서 타지 않는 예외 분기다.

### ③ Dynamixel — **검증 완료** (2026-09-09)

XM540-W150(ID 3) · XM540-W270(ID 5) 2대 검출. Broadcast Ping · CRC ·
상태 패킷 분해 · 모델명 매핑 · 포트 자동 탐색 · **매번 새로 물리 검색**
(2회 연속 값 동일 · `scanned_at` 갱신) 전부 통과 · §6-33.

§6-16의 40초 시한은 단일 경로임을 확인했고, 그 과정에서 **전체 검색만 20초**로
남아 있던 결함을 찾아 고쳤다(§6-37 · 지금은 50초).

**남은 미검증** · `_ping_dynamixel_id`의 *성공* 반환 · 두 대 모두 Broadcast에
응답해 보조 Ping이 성공 경로로 가지 않는다.

### ④ 모터 조작 경로 미검증 3종

- 조그·절대 이동·서보 제어 (§6-21) · 모터가 실제로 움직인다
- 설정 저장·적용·재시작·실행 해제 (§6-19)
- 프로젝트 전환 시 `clear_selection` 격리 계약 (§6-19)

### ⑤ `motion_supervisor` 수신 정지 — 근본 원인 미특정

§6-14 참조. 재발 판별법만 적어뒀다 · `safety_status`는 2 Hz로 나오는데
`POST /api/execution-context/apply`가 `waiting_motor_runtime`으로 실패하면
같은 증상이다. 재발 시 `py-spy dump`로 스레드를 남길 것 (현재 미설치 ·
`pip` 부재로 sudo 필요).

---

## 4. 다음 개발 단계

### 다음 후보 · 우선순위순

| # | 항목 | 근거 |
|---|---|---|
| 1 | **원격 푸시** | 61커밋이 이 PC에만 있다 · 유실 위험 |
| 2 | **§6-14 근본 원인** | 유일한 미해결 결함 · 재발 판별법만 존재 |
| 3 | **슬레이브 2대 재빌드** | 미빌드 시 노드 기동 실패 · 아래 ① |
| 4 | **7단계 프런트엔드 빌드** | 번호 단계 다음 · 장치 무관 |
| 5 | `MotionSupervisor` 2,378줄 | 현재 최대 Node · 모터 명령 경로라 위험 |
| 6 | `MidiDecoder` | `_midi_callback` 459줄 루프 국면 분해 선행 필요 |
| 7 | 8단계 스캐너 이관 | 범위 협의 필요 |

매칭표 Dynamixel 포함·`connected_axes` 물리 필드는 **화면에 쓰이지 않는
payload**임을 확인해 순위에서 내렸다 · `ARCHITECTURE_REVIEW.md` §8 정정 참조.

`StatusStore`(목표안 잔여)는 접근자 위주라 가치가 낮다고 평가했다 ·
제외 여부를 결정할 것.

### 이번에 확립한 방식

서비스 5개를 같은 절차로 만들었다. 그대로 따르면 된다.

1. **후보를 락으로 찾되, 경계는 개념으로 긋는다** · 한 락 아래 여러 관심사가
   섞여 있으면 통째로 옮기지 말 것 · §6-20에서 세대 번호를 남긴 이유
2. **상태와 그 락을 함께 옮긴다** · 판정과 기록이 갈라지면 안 된다 · §6-17
3. **노드에서 받는 것은 협력자뿐** · 저장소 · 로거 · 콜러블
4. **서비스끼리는 콜러블 인자로 안다** · 노드를 거쳐 다른 서비스를 알지 않게 ·
   §6-19 `load_motor_config` · §6-20 `context_id`
5. **노드에 위임 껍데기를 남기지 않는다** · 라우트가 서비스를 직접 부른다
6. **테스트 이음매도 함께 옮긴다** · 노드 없이 서비스만 세워 검사한다

### 흔한 함정 여섯 가지 · 전부 실제로 겪었다

| # | 함정 | 무엇을 잡았나 | 절 |
|---|---|---|---|
| 1 | **이름 다섯 형태** · `self.X()` · `self.X` · `getattr/hasattr(self,'X')` · `f(self)` | 시험·실물 | §6-19 |
| 2 | **메서드 종류** · `@staticmethod`·`@classmethod`·`@property`도 따라가야 한다 | 시험 | §6-32 |
| 3 | **떼어낸 모듈이 노드를 되부른다** · 노드 파일만 보면 안 보인다 | 시험 | §6-34 |
| 4 | **`(self, ` 일괄 치환이 `getattr(self, …)`를 먹는다** · 실행 시점에야 터진다 | 시험 | §6-35 |
| 5 | **상수를 기억으로 적는다** · `0xFFFF`를 65344로 적을 뻔했다 | 사전 대조 | §6-36 |
| 6 | **인자화로 평가 시점이 달라진다** · 호출 직전에 읽어야 동치다 | 실물 | §6-11 |

특히 1번은 **여러 줄로 쪼개진 형태**에서 두 번 재발했다.

```python
getattr(
    self,                       # ← 한 줄 정규식으로는 안 잡힌다
    'pickup_feedback_consistency_deg',
    기본값,
)
```

**감사는 정규식이 아니라 AST로 한다** · `ast.Call`의 인자를 보므로 줄바꿈에
영향받지 않는다 · §6-41에서 정규식 감사를 폐기했다.

```
self 를 첫 인자로 받는 getattr/hasattr/setattr 중
문자열 이름이 그 클래스에 없는 것을 찾는다
대상 · self.node / self.monitor / self.bridge 를 갖는 서비스 클래스
```

그리고 **시험 이음매도 코드를 따라간다**.

- 모듈 경로 문자열 패치 · `patch('a.b.subprocess')` → 코드가 옮겨가면 조용히 어긋난다
- 인스턴스 monkeypatch(`node._snapshot = ...`)는 모듈 함수를 비껴간다
- 모듈 전역에 직접 대입하지 말 것 · 복원되지 않아 시험 간 오염이 된다 ·
  `monkeypatch.setattr`를 쓴다

### 지도를 다시 그리는 방법

`scripts/bridge_state_map.py` · 세션 임시본이 소실됐던 것을 재작성해 보존했다.

```bash
python3 scripts/bridge_state_map.py --bundles
python3 scripts/bridge_state_map.py --json docs/metrics/bridge-state-map-<날짜>.json
```

**`getattr(self, '...')` 문자열 접근을 반드시 포함한다** · 빠뜨리면 순수
메서드를 과대평가한다.

---

## 5. 출장 중 할 만한 작업 (Windows 안전)

`motion_common` 안에서 완결되므로 테스트로 검증된다.

### (a) ruff 잔여 정리 · 55건

```
BLE001  광범위 except   37건
F841    미사용 변수      11건
S110    예외 무음 삼킴    7건
```

**`S110` 7건부터** 권한다. 조용히 삼키던 오류가 로그로 드러나면 진단이 쉬워진다.
다만 이 7건은 `bridge_node`·`coordination_node`·`bridge_helpers`에 있어
**노드 코드다** — Windows에서는 회귀를 확인할 수 없다. 고친다면 예외 타입만
좁히고 동작은 건드리지 말 것.

### (b) `motion_common` 문서화·테스트 보강

9모듈 모두 테스트가 있으나 경계 조건이 더 있을 수 있다. 안전하고 유용하다.

### (c) 계획 수립

상태 동반 이동의 첫 묶음을 문서로 설계해두면 복귀 후 바로 착수할 수 있다.

---

## 6. 알아둘 것

### 이 워크스페이스의 함정

- **웹 UI는 소스에서 서빙된다** · `system_routes.py:13-17`이 소스 트리가 있으면
  설치본 대신 소스를 쓴다. JS를 고치면 새로고침만으로 반영되고 빌드는 무의미하다.
- **`ROS_LOCALHOST_ONLY=1`** · 서비스가 이 값으로 뜨므로 셸도 맞춰야 `ros2` 명령이
  노드를 본다. 안 맞으면 "Node not found"가 난다.
- **스케줄 노드 로그는 systemd가 아니라 ROS 로그로 간다** · `~/.ros/log/python3_*.log`
- **`src/motion_system`은 보호 대상 서브모듈** · `__pycache__` 때문에 항상 dirty로
  보이지만 커밋 포인터는 그대로다. 손대지 말 것.
- **`restart_motion_monitor.sh`에 실행 권한이 없다** · `bash scripts/...`로 실행.

### 검증 명령 모음

```bash
pytest                                    # 1,016건 · Linux 전용
pytest src/motion_common                  # Windows 가능
ruff check src                            # 55건이면 정상 (신규 0)
python3 scripts/code_metrics.py --baseline docs/metrics/baseline-20260822.json
python3 scripts/bridge_state_map.py --bundles
colcon build --symlink-install --packages-select motion_web_bridge
systemctl --user restart motion-control.service
./scripts/build_and_restart.sh          # 전체 빌드 + 재시작
```

### 되돌리기

```bash
git switch main && ./scripts/build_and_restart.sh
```

`main`은 `7772c6b`에서 멈춰 있고 이번 작업은 전부 브랜치에 있다.
커밋 단위 되돌리기도 가능하다 · `git revert <해시>`

---

## 7. 참고 문서

- `docs/ARCHITECTURE_REVIEW.md` · 전체 검토 · **§5 표가 계획 · §6이 이력**
  - §5 · 8단계 로드맵 · **7단계 행이 현재 위치** · 표 아래가 분해 목표안
  - §6-8 · 상태·락 의존 지도 · 분해의 출발점
  - §6-14 · **결함 기록** · `motion_supervisor` 수신 정지 · 미해결
  - §6-15 ~ §6-23 · `bridge_node` 서비스 9개 신설
  - §6-24 ~ §6-28 · 영속 계층 · Action 전환 · 전환하지 않기로 한 이유
  - §6-29 ~ §6-31 · `motion_run_manager` 분해
  - §6-32 ~ §6-36 · `monitor_node` 분해 완료
  - §6-37 · **결함 기록** · 전체 검색 시한 · 수정 완료
  - §6-38 ~ §6-41 · `midi_control_node` 분해
  - §8 · 검증 상태 · 실물 통과/미통과 표
- `docs/metrics/` · 지표 스냅숏 · 의존 지도 JSON
- `scripts/bridge_state_map.py` · 의존 지도 재작성 도구
- `scripts/check_locks.sh` · 락 파일 점검
- `.claude/settings.json` · Claude Code 권한 · 모터 관련 명령은 확인을 받도록 설정

### 신설된 서비스·모듈 (`src/motion_web/web_bridge/motion_web_bridge/`)

| 파일 | 역할 | 절 |
|---|---|---|
| `motion_studio_session.py` | 스튜디오 상태 단일 소유 | §6-15 |
| `motor_event_log.py` | 모터 동작 로그 · 전이 판정 | §6-17 |
| `scan_orchestrator.py` | 물리 검색 조율 | §6-18 |
| `motor_config_service.py` | 설정 저장·적용·재시작 | §6-19 |
| `execution_context_service.py` | 실행 컨텍스트 조율 | §6-20 |
| `manual_motor_commands.py` | 수동 조그·동작·서보 제어 | §6-21 |
| `ethercat_project_compat.py` | 스캔·프로젝트 대조 | §6-13 |
| `motor_config_build.py` | 레지스트리 → 설정 생성 | §6-11 |
| `desktop_shortcut.py` | 바탕화면 바로가기 | §6-11 |

삭제 · `motor_service.py` · `motion_run_service.py` (빈 위임 층 · §6-16)

### 신설된 모듈 · `motion_state_monitor/`

| 파일 | 역할 | 절 |
|---|---|---|
| `ethercat_scanner.py` | EtherCAT 물리 검색 · 버스 상태 | §6-32 |
| `dynamixel_scanner.py` | Dynamixel 물리 검색 · Protocol 2.0 | §6-33 |
| `motor_values.py` | 값 변환 순수 함수 · 라벨 | §6-34 |
| `connection_state.py` | 연결 판정 · `CommunicationHealth` | §6-35 |
| `state_publisher.py` | 축 상태 수신·발행 | §6-36 |

### 신설된 모듈 · `midi_control/`

| 파일 | 역할 | 절 |
|---|---|---|
| `motion_value_map.py` | 페이더↔모션↔모터 변환 · 범위 검사 | §6-38 |
| `pickup_policy.py` | 튐 방지 판정 · 채널별 대기 상태 | §6-39 |
| `fader_state.py` | 페이더 파킹·동기화 대기 | §6-40 |
| `midi_snapshot.py` | 화면 표현 · 읽기 전용 | §6-41 |

### 신설된 모듈 · `motion_runtime/`

| 파일 | 역할 | 절 |
|---|---|---|
| `motion_run_rules.py` · `plan_builder.py` | 실행 규칙 · 계획 수립 | §6-30 |
| `group_session.py` | 그룹 실행 세션 | §6-29 |
| `motion_player.py` · `motion_run_constants.py` | 재생 · 상수 단일화 | §6-31 |
