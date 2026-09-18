import os
import json
import time
import urllib.request
import urllib.error
import rclpy
from rclpy.node import Node
from std_msgs.msg import String
from datetime import datetime
from typing import Dict, Any

from pathlib import Path

from motion_common.coordination import (
    coordination_settings_path,
    load_coordination_settings,
    resolve_master_role,
)
from motion_common.paths import motion_projects_dir, workspace_root
from motion_common import topics

from motion_common.repeat_policy import DEFAULT_REPEAT_MODE, normalize_repeat_mode
from motion_common.run_state import group_is_active, is_running
from motion_common.schedule_models import ScheduleItem
from motion_common.schedule_store import (
    DEFAULT_RUN_MODE,
    SCHEDULE_MODE,
    ScheduleStore,
    normalize_run_mode,
)

try:
    from motion_schedule.schedule_engine import ScheduleEngine
except ImportError:
    from .schedule_engine import ScheduleEngine

PACKAGE_HINT = 'motion_schedule'

#: 몇 초마다 「스케줄이 말하는 상태」와 실제를 맞출 것인가 · §6-137
#:
#: 짧게 하면 시작이 정확해지는 대신 브리지를 자주 두드린다 · 1분이면 전시·
#: 무대에서 충분하고, 개장 시각을 정확히 맞춰야 하면 시작 시각을 1분 당겨
#: 적으면 된다.
#:
#: 한 번 10초로 줄였다가 되돌렸다 · §6-149 · "공연 중에 멈추면 최대 1분을
#: 죽어 있는다" 가 줄인 이유였는데, **이 값은 현장 운영이 정하는 값이지
#: 코드가 정할 값이 아니다** · 바꾸려면 현장에서 어느 쪽이 나은지 보고 정한다.
RECONCILE_INTERVAL_SEC = 60.0


class MotionScheduleNode(Node):
    def __init__(self):
        super().__init__('motion_schedule_node')

        self.workspace_dir = str(workspace_root(PACKAGE_HINT))
        self.projects_dir = str(motion_projects_dir(PACKAGE_HINT))

        self.declare_parameter(
            'coordination_file',
            str(coordination_settings_path(PACKAGE_HINT)),
        )
        self.coordination_file = self.get_parameter('coordination_file').value
        self._master_role_cache = None
        self._master_role_stamp = None

        self.store = ScheduleStore(projects_dir=self.projects_dir)
        self.engine = ScheduleEngine()
        self._last_reconcile_monotonic = 0.0
        self._run_mode = DEFAULT_RUN_MODE
        # 마지막으로 거부당한 시도 · §6-147
        #
        # 전에는 아무 데도 안 남았다 · 스케줄이 발화하고 연동이 거부해도
        # 로그에 `success` 라고 찍히고 끝이라, 화면 배지는 초록불이었다 ·
        # 한 시간 내내 매분 거부당하는 동안 아무도 몰랐다.
        self._last_failure = {}

        # Status publisher
        self.status_pub = self.create_publisher(String, topics.SCHEDULE_STATUS, 10)

        # Subscribers
        self.active_project_sub = self.create_subscription(
            String,
            topics.ACTIVE_PROJECT,
            self._on_active_project_changed,
            10
        )

        # Load initial active project if exists
        self._load_active_project_from_file()

        # 1-second background timer for schedule checking
        self.timer = self.create_timer(1.0, self._on_timer_tick)

        self.get_logger().info("motion_schedule_node started (Master Only Coordinated Motion Scheduler)")

    def _is_master_pc(self) -> bool:
        """Check if current PC role is master.

        1초 주기 타이머에서 반복 호출되므로 설정 파일 mtime·크기가 그대로면
        직전 판정을 재사용한다. 판정 결과가 바뀔 때만 로그를 남긴다.
        """
        path = Path(self.coordination_file)
        try:
            stat = path.stat()
            stamp = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            stamp = None

        if self._master_role_cache is not None and stamp == self._master_role_stamp:
            return self._master_role_cache.is_master

        role = resolve_master_role(path)
        previous = self._master_role_cache
        self._master_role_cache = role
        self._master_role_stamp = stamp

        if previous is None or previous.is_master != role.is_master:
            self.get_logger().info(f"마스터 판정 · {role.is_master} · {role.reason}")
        return role.is_master

    def _coordination_enabled(self) -> bool:
        """연동을 쓰는 PC 인가 · 아니면 단독으로 돈다.

        스케줄은 그룹 전용이었다 · `start_group` 만 보내서, 연동을 쓰지 않는
        PC 에서는 발화는 하는데 "먼저 DDS 그룹에 참가하세요" 로 매번 실패했다.
        `resolve_master_role` 이 연동 미사용을 "단독 동작으로 간주" 하며 마스터
        판정을 통과시키기 때문에 조용히 실패했다 · §6-68
        """
        path = Path(self.coordination_file)
        if not path.is_file():
            return False
        settings = load_coordination_settings(path)
        if settings is None:
            return False
        return bool(settings.get('enabled', False))

    def _load_active_project_from_file(self):
        project_id = None
        
        # 1. Single Source of Truth: .selected_project.json (written by project_repository.py)
        selected_file = os.path.join(self.projects_dir, '.selected_project.json')
        if os.path.exists(selected_file):
            try:
                with open(selected_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    project_id = data.get('selected_project_id') or data.get('project_id')
            except (OSError, ValueError) as exc:
                self.get_logger().warning(f"Failed to load .selected_project.json: {exc}")

        # 2. Legacy fallback
        if not project_id:
            active_file = os.path.join(self.projects_dir, 'active_project.json')
            if os.path.exists(active_file):
                try:
                    with open(active_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        project_id = data.get('active_project_id') or data.get('project_id')
                except (OSError, ValueError) as exc:
                    self.get_logger().warning(
                        f"Failed to load active_project.json: {exc}"
                    )

        if not project_id:
            project_id = "default"

        self.get_logger().info(f"Loading schedule store for project: {project_id}")
        self.store.load_project(project_id)

    def _on_active_project_changed(self, msg: String):
        project_id = msg.data.strip()
        if project_id and project_id != self.store.current_project_id:
            self.get_logger().info(f"Switching schedule store to active project: {project_id}")
            self.store.load_project(project_id)

    def _send_http_request(self, endpoint: str, payload: Dict[str, Any]) -> bool:
        """Send HTTP POST request to local Web Bridge API."""
        url = f"http://127.0.0.1:8000{endpoint}"
        try:
            data = json.dumps(payload).encode('utf-8')
            req = urllib.request.Request(
                url,
                data=data,
                headers={'Content-Type': 'application/json'},
                method='POST'
            )
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                result = json.loads(resp.read().decode('utf-8'))
        except (OSError, ValueError) as exc:
            self._remember_failure(endpoint, str(exc))
            self.get_logger().error(f"HTTP Request [{endpoint}] failed: {exc}")
            return False
        # 200 이 왔다고 받아들여진 것이 아니다 · §6-147
        #
        # 전에는 여기서 바로 `success` 라고 찍고 True 를 돌려줬다 · 그래서
        # 로그에 `success: {'success': False, ...}` 같은 자기모순이 남았고,
        # 거부가 실패로 세어지지 않아 알람도 화면 표시도 생기지 않았다.
        if isinstance(result, dict) and result.get('success') is False:
            message = str(result.get('message') or '이유를 알려주지 않았습니다')
            self._remember_failure(endpoint, message)
            self.get_logger().warning(f"[거부] {endpoint} · {message}")
            return False
        self._forget_failure()
        self.get_logger().info(f"HTTP Request [{endpoint}] success: {result}")
        return True

    def _remember_failure(self, endpoint: str, message: str) -> None:
        """연달아 몇 번째인지까지 센다 · 한 번은 경합, 계속이면 고장이다."""
        previous = self._last_failure.get('count') or 0
        self._last_failure = {
            'endpoint': endpoint,
            'message': message,
            'count': int(previous) + 1,
            'at': datetime.now().astimezone().isoformat(),
        }

    def _forget_failure(self) -> None:
        if self._last_failure:
            self.get_logger().info('스케줄 시도가 다시 받아들여졌습니다')
        self._last_failure = {}

    def _read_json(self, endpoint: str, timeout_sec: float = 0.5):
        url = f"http://127.0.0.1:8000{endpoint}"
        try:
            with urllib.request.urlopen(url, timeout=timeout_sec) as response:
                return json.loads(response.read().decode())
        except (OSError, ValueError) as exc:
            self.get_logger().debug(f"조회 실패 [{endpoint}] · {exc}")
            return None

    def _local_run_state(self) -> str:
        """이 PC 의 모션이 지금 어느 단계인가 · 못 읽으면 빈 문자열."""
        payload = self._read_json('/api/motion-run/status')
        if not isinstance(payload, dict):
            return ''
        status = payload.get('status') if isinstance(payload.get('status'), dict) else payload
        return str(status.get('state') or '')

    def _group_execution(self) -> dict:
        """지금 살아 있는 그룹 실행 · 없으면 빈 것."""
        payload = self._read_json('/api/coordination')
        runtime = (payload or {}).get('runtime') if isinstance(payload, dict) else None
        execution = (runtime or {}).get('execution') if isinstance(runtime, dict) else None
        return execution if isinstance(execution, dict) else {}

    def _motion_is_running(self) -> bool:
        """지금 모션이 돌고 있는가 · 로컬과 그룹을 **둘 다** 본다 · §6-145

        그룹 실행은 준비가 길다 · 마스터가 신호를 보내고 각 PC 가 응답하고
        시각을 맞추는 동안, 이 PC 의 로컬 모션은 아직 `stopped` 다.

        전에는 로컬만 봐서 그 틈을 「멈춤」으로 읽고, 이미 시작된 그룹 실행을
        1분마다 또 시작시켰다 · 조정 노드가 `이전 그룹 실행 정리 확인 중입니다`
        로 막아 피해는 없었지만 헛시도가 계속 나갔다.
        """
        if is_running(self._local_run_state()):
            return True
        return group_is_active(self._group_execution())

    def _on_timer_tick(self):
        # 0. 활성 프로젝트를 브리지에 맞춘다
        data = self._read_json('/api/schedule/status')
        if isinstance(data, dict):
            api_proj = data.get("active_project_id")
            if api_proj and api_proj != self.store.current_project_id:
                self.get_logger().info(f"Syncing active project from Web API: {api_proj}")
                self.store.load_project(api_proj)
        self._run_mode = normalize_run_mode((data or {}).get('run_mode'))

        if not self.store.current_project_id:
            self._load_active_project_from_file()

        self.store.check_and_reload()

        now = datetime.now().astimezone()

        # 슬레이브는 아무것도 하지 않는다 · 마스터가 그룹 전체를 몬다 · §6-137
        if not self._is_master_pc():
            return

        if time.monotonic() - self._last_reconcile_monotonic >= RECONCILE_INTERVAL_SEC:
            self._last_reconcile_monotonic = time.monotonic()
            self._reconcile(now)

        self._publish_status(now)

    def _reconcile(self, now: datetime) -> None:
        """스케줄이 말하는 상태와 실제를 맞춘다 · §6-137

        시각을 지나갔는지 보지 않는다 · **지금 구간 안인가**만 본다 · 그래서
        재부팅해도, 시작을 놓쳐도, 어긋나도 다음 점검에서 스스로 맞춘다.
        """
        if self._run_mode != SCHEDULE_MODE:
            # 수동 모드 · 스케줄은 아무것도 하지 않는다 · §6-143
            #
            # 전에는 「사람이 멈췄나」를 요청 내용으로 추측했다 · 그룹 정지나
            # 안전 정지까지 사람이 멈춘 것으로 읽어서, 1회 연동 실행만 해도
            # "사람이 모션을 정지했습니다" 가 떴다 · 추측을 없앴다.
            return

        wanted = self.engine.active(now, self.store.list_schedules())
        running = self._motion_is_running()

        if wanted is not None and not running:
            self.get_logger().info(
                f"[점검] 구간 안인데 멈춰 있다 · 시작 · {wanted.schedule_name}"
            )
            self._execute_start(wanted)
            return

        if wanted is None and running:
            self.get_logger().info("[점검] 구간 밖인데 돌고 있다 · 회차 후 정지")
            self._execute_stop_after_cycle(None)

    def _execute_start(self, item: ScheduleItem):
        self.get_logger().info(f"[SCHEDULE TRIGGER] START -> {item.schedule_name} ({item.schedule_id})")
        
        # 스케줄러 자체 판단을 제거하고, 연동 설정 파일에 사용자가 저장한 값을 그대로 가져와 웹 UI와 100% 동일하게 쏩니다.
        # 반복 방식의 주인은 `motion_common.repeat_policy` 다 · §6-135
        #
        # 전에는 여기 기본값만 `direct` 였다 · 화면에는 「초기 위치 이동 후
        # 다음」이 골라져 보이는데 스케줄은 `direct` 로 쐈고, 시작값과 끝값이
        # 5° 이상 벌어진 모션은 "연속 동작할 수 없습니다" 로 죽었다.
        req_repeat_mode = DEFAULT_REPEAT_MODE
        req_dwell_sec = 0.0
        automation_file = os.path.join(self.projects_dir, self.store.current_project_id, "runtime", "motion_automation.json")
        try:
            if os.path.exists(automation_file):
                with open(automation_file, "r", encoding="utf-8") as f:
                    auto_config = json.load(f)
                    req_repeat_mode = normalize_repeat_mode(auto_config.get("repeat_mode"))
                    req_dwell_sec = float(auto_config.get("dwell_sec", 0.0))
        except (OSError, ValueError) as exc:
            self.get_logger().warning(f"Failed to read motion_automation.json: {exc}")
            
        if self._coordination_enabled():
            payload = {
                "command": "start_group",
                "run_mode": "continuous",
                "repeat_mode": req_repeat_mode,
                "dwell_sec": req_dwell_sec,
                "target_cycle_count": 0,
                "schedule_id": item.schedule_id,
            }
            self._send_http_request("/api/coordination/control", payload)
            return

        # 단독 · 모션·매핑 파일은 브리지가 프로젝트의 활성 파일로 채운다
        payload = {
            "run_mode": "continuous",
            "repeat_mode": req_repeat_mode,
            "dwell_sec": req_dwell_sec,
            "target_cycle_count": 0,
            "schedule_id": item.schedule_id,
        }
        self._send_http_request("/api/motion-run/start", payload)

    def _execute_stop_after_cycle(self, item=None):
        name = getattr(item, 'schedule_name', '구간 밖')
        schedule_id = getattr(item, 'schedule_id', '')
        self.get_logger().info(f"[SCHEDULE TRIGGER] STOP-AFTER-CYCLE -> '{name}'")
        if self._coordination_enabled():
            self._send_http_request("/api/coordination/control", {
                "command": "stop_after_cycle",
                "schedule_id": schedule_id or 'reconcile',
            })
            return
        self._send_http_request("/api/motion-run/stop-after-cycle", {
            "schedule_id": schedule_id or 'reconcile',
        })

    def _publish_status(self, now: datetime):
        status = {
            "is_master": self._is_master_pc(),
            "active_project_id": self.store.current_project_id,
            "current_time": now.isoformat(),
            "schedule_count": len(self.store.list_schedules()),
            "active_schedule_id": getattr(
                self.engine.active(now, self.store.list_schedules()),
                'schedule_id', None,
            ),
            "run_mode": self._run_mode,
            # 마지막으로 거부당한 시도 · 비어 있으면 정상이다
            "last_failure": dict(self._last_failure),
            # 멈춰도 몇 초 뒤에 다시 맞추는가 · 화면이 사람에게 알려준다 · §6-149
            "reconcile_interval_sec": RECONCILE_INTERVAL_SEC,
        }
        msg = String()
        msg.data = json.dumps(status)
        self.status_pub.publish(msg)


def main(args=None):
    rclpy.init(args=args)
    node = MotionScheduleNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
