import os
import json
import urllib.request
import urllib.error
import rclpy
from rclpy.node import Node
from std_msgs.msg import String
from datetime import datetime
from typing import Dict, Any

from pathlib import Path

from motion_common.coordination import coordination_settings_path, resolve_master_role
from motion_common.paths import motion_projects_dir, workspace_root
from motion_common import topics

from motion_common.schedule_models import ScheduleItem
from motion_common.schedule_store import ScheduleStore

try:
    from motion_schedule.schedule_engine import ScheduleEngine
except ImportError:
    from .schedule_engine import ScheduleEngine

PACKAGE_HINT = 'motion_schedule'


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
        self.engine = ScheduleEngine(grace_period_sec=30)

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
                self.get_logger().info(f"HTTP Request [{endpoint}] success: {result}")
                return True
        except (OSError, ValueError) as exc:
            self.get_logger().error(f"HTTP Request [{endpoint}] failed: {exc}")
            return False

    def _on_timer_tick(self):
        # 0. Realtime Sync: Get current active project directly from Web Bridge
        try:
            with urllib.request.urlopen("http://127.0.0.1:8000/api/schedule/status", timeout=0.5) as response:
                data = json.loads(response.read().decode())
                api_proj = data.get("active_project_id")
                if api_proj and api_proj != self.store.current_project_id:
                    self.get_logger().info(f"Syncing active project from Web API: {api_proj}")
                    self.store.load_project(api_proj)
        except (OSError, ValueError) as exc:
            # 브리지 미기동·재시작 중에는 정상적으로 실패한다. 파일 기반 경로로 대체된다.
            self.get_logger().debug(f"Active project sync from Web API skipped: {exc}")

        if not self.store.current_project_id:
            self._load_active_project_from_file()

        # 1. Realtime store mtime check & reload if store updated via web UI
        self.store.check_and_reload()

        # 1. Master PC check
        if not self._is_master_pc():
            # Slave PC: do not process local schedule triggers
            return

        now = datetime.now().astimezone()
        schedules = self.store.list_schedules()
        actions = self.engine.tick(now, schedules)

        for action, item in actions:
            if action == 'start':
                self._execute_start(item)
            elif action == 'stop-after-cycle':
                self._execute_stop_after_cycle(item)

        # Publish status
        self._publish_status(now)

    def _execute_start(self, item: ScheduleItem):
        self.get_logger().info(f"[SCHEDULE TRIGGER] START -> {item.schedule_name} ({item.schedule_id})")
        
        # 스케줄러 자체 판단을 제거하고, 연동 설정 파일에 사용자가 저장한 값을 그대로 가져와 웹 UI와 100% 동일하게 쏩니다.
        req_repeat_mode = "direct"
        req_dwell_sec = 0.0
        automation_file = os.path.join(self.projects_dir, self.store.current_project_id, "runtime", "motion_automation.json")
        try:
            if os.path.exists(automation_file):
                with open(automation_file, "r", encoding="utf-8") as f:
                    auto_config = json.load(f)
                    req_repeat_mode = str(auto_config.get("repeat_mode", "direct"))
                    req_dwell_sec = float(auto_config.get("dwell_sec", 0.0))
        except (OSError, ValueError) as exc:
            self.get_logger().warning(f"Failed to read motion_automation.json: {exc}")
            
        payload = {
            "command": "start_group",
            "run_mode": "continuous",
            "repeat_mode": req_repeat_mode,
            "dwell_sec": req_dwell_sec,
            "target_cycle_count": 0,
            "schedule_id": item.schedule_id
        }
        self._send_http_request("/api/coordination/control", payload)

    def _execute_stop_after_cycle(self, item: ScheduleItem):
        self.get_logger().info(f"[SCHEDULE TRIGGER] STOP-AFTER-CYCLE -> '{item.schedule_name}' (ID: {item.schedule_id})")
        payload = {
            "command": "stop_after_cycle",
            "schedule_id": item.schedule_id
        }
        self._send_http_request("/api/coordination/control", payload)

    def _publish_status(self, now: datetime):
        status = {
            "is_master": self._is_master_pc(),
            "active_project_id": self.store.current_project_id,
            "current_time": now.isoformat(),
            "schedule_count": len(self.store.list_schedules()),
            "active_schedule_id": self.engine.active_schedule_id
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
