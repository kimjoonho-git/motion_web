# 인수인계 · 출장 중 Windows 작업

- 작성일 · 2026-08-22
- 브랜치 · `refactor/motion-common-extract` (원격 반영 완료)
- 마지막 커밋 · `293d7e5` + 이 문서
- 상황 · Linux 실기(joonhoTest) 전원 차단 · Windows PC에서 코드 작업만 진행

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
| 4 · `bridge_node` 분해 | 🔸 진행 중 · 순수 함수까지 |
| 5 · 영속 계층 통합 | 🔸 `store.py`는 완료 · 호출부 이관 미완 |
| 6 · Action 전환 | ⬜ 미착수 |
| 7 · 프런트엔드 빌드 | ⬜ 미착수 |
| 8 · 하드웨어 스캐너 분리 | ⬜ 미착수 |

### 지표

```
bridge_node        7,407 → 6,300줄
테스트             68파일(실패 11) → 989건(실패 0)
ruff               도입 · 잔여 55건
파일 1,000줄 초과   7개
함수 60줄 초과      124개
Node 클래스 500줄 초과  8개
```

기준선 · `docs/metrics/baseline-20260822.json`
현재 · `docs/metrics/after-decomposition-1.json`

```bash
python3 scripts/code_metrics.py --baseline docs/metrics/baseline-20260822.json
```

---

## 3. 복귀 직후 해야 할 일 · 순서대로

### ① 슬레이브 PC 2대 재빌드 — 가장 시급

두 대(`floating3-Ecolite-Series`, `pc-a`)가 아직 구코드(`7772c6b`)다.

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

### ② MIDI 화면 검증 — 미검증 부채

`midi_control_node`의 명령 19개를 처리기 표로 옮겼는데(283줄 → 20줄), MIDI
장치가 연결되지 않아 확인하지 못했다. 컨트롤러를 붙이고 아래를 볼 것.

- 뱅크 생성·전환·삭제
- 페이더 SELECT · 재동기화
- 프로젝트 전환 시 매핑 반영
- 스튜디오 녹화 준비 (페이더 0 복귀)

### ③ 모터 재시작 경로 재확인

3차 추출에서 `motor_operation_runtime_readiness`를 `functools.partial`로
넘기도록 바꿨다. 웹 화면 검증은 통과했으나 **실패 시 롤백 경로는 타보지 않았다.**

---

## 4. 다음 개발 단계 · `bridge_node` 상태 동반 이동

### 무엇이 남았나

`docs/ARCHITECTURE_REVIEW.md` §6-8의 지도 기준.

| 분류 | 메서드 | 줄수 | 상태 |
|---|---|---|---|
| 상태 무의존 | 41 | 585 | ✅ 대부분 추출 완료 |
| **상태만 (락 없음)** | **89** | **1,826** | ⬅ **다음** |
| 락 관여 | 84 | 3,239 | 마지막 |

### 왜 성격이 다른가

지금까지는 **함수를 옮겼다.** 다음은 **상태를 옮긴다.** 순수 함수는 인자만
받으면 어디서든 같게 동작하지만, 상태를 옮기면 그 상태를 누가 소유하고 누가
갱신하는지가 바뀐다. 잘못하면 두 곳이 같은 값을 따로 들고 갈라진다.

### 권장 방법

1. **상태 묶음 하나를 고른다** · 서로만 참조하는 상태 + 그 상태만 쓰는 메서드
2. **서비스 클래스를 만든다** · 그 상태를 필드로 갖고, 메서드를 옮긴다
3. **노드가 서비스를 소유한다** · `self._xxx_service = XxxService(...)`
4. **노드에는 위임을 남기지 않는다** · 호출부가 서비스를 직접 부른다
5. 빌드 · 테스트 · 실물 검증을 **묶음 하나마다** 거친다

껍데기 위임을 남기면 §3-1이 지적한 문제를 재생산한다. `motor_service.py`(110줄
전량이 `self.bridge.___` 형태)가 그 예다.

### 지도를 다시 그리는 방법

분석 스크립트는 세션 임시 디렉터리에 있었으므로 남아 있지 않다. 필요하면
`docs/ARCHITECTURE_REVIEW.md` §6-8의 방법을 따라 다시 만들면 된다. **핵심은
`getattr(self, '...')` 같은 문자열 기반 접근을 반드시 포함하는 것** — 이걸
빠뜨려 순수 메서드를 65개로 과대평가했고, 추출 도중에야 발견했다.

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
pytest                                    # 989건 · Linux 전용
pytest src/motion_common                  # 153건 · Windows 가능
ruff check src
python3 scripts/code_metrics.py --baseline docs/metrics/baseline-20260822.json
colcon build --symlink-install
./scripts/build_and_restart.sh
```

### 되돌리기

```bash
git switch main && ./scripts/build_and_restart.sh
```

`main`은 `7772c6b`에서 멈춰 있고 이번 작업은 전부 브랜치에 있다.
커밋 단위 되돌리기도 가능하다 · `git revert <해시>`

---

## 7. 참고 문서

- `docs/ARCHITECTURE_REVIEW.md` · 전체 검토 · §6에 반영 이력
  - §6-8 · `bridge_node` 상태·락 의존 지도 ← **다음 단계의 근거**
  - §6-9 · 순수 함수 추출 3차까지
  - §6-10 · 손대지 않은 범위
- `docs/metrics/` · 지표 스냅숏
- `.claude/settings.json` · Claude Code 권한 · 모터 관련 명령은 확인을 받도록 설정
