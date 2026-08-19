import os
import json
import urllib.request
import urllib.error
import rclpy
from rclpy.node import Node
from std_msgs.msg import String
from datetime import datetime
from typing import Optional, Dict, Any

from .schedule_models import ScheduleItem
from .schedule_store import ScheduleStore
from .schedule_engine import ScheduleEngine


class MotionScheduleNode(Node):
    def __init__(self):
        super().__init__('motion_schedule_node')

        self.workspace_dir = os.environ.get('MOTION_WORKSPACE', '/home/joonho_test/ros2_ws')
        self.projects_dir = os.path.join(self.workspace_dir, 'motion_projects')

        self.declare_parameter('coordination_file', os.path.join(self.workspace_dir, 'config/coordination_settings.yaml'))
        self.coordination_file = self.get_parameter('coordination_file').value

        self.store = ScheduleStore(projects_dir=self.projects_dir)
        self.engine = ScheduleEngine(grace_period_sec=30)

        # Status publisher
        self.status_pub = self.create_publisher(String, '/motion_schedule/status', 10)

        # Subscribers
        self.active_project_sub = self.create_subscription(
            String,
            '/motion_control/active_project',
            self._on_active_project_changed,
            10
        )

        # Load initial active project if exists
        self._load_active_project_from_file()

        # 1-second background timer for schedule checking
        self.timer = self.create_timer(1.0, self._on_timer_tick)

        self.get_logger().info("motion_schedule_node started (Master Only Coordinated Motion Scheduler)")

    def _is_master_pc(self) -> bool:
        """Check if current PC role is master."""
        if not os.path.exists(self.coordination_file):
            return True

        try:
            with open(self.coordination_file, 'r', encoding='utf-8') as f:
                content = f.read()
                if "role: master" in content or "role: 'master'" in content or 'role: "master"' in content:
                    return True
                if "role: slave" in content or "role: 'slave'" in content or 'role: "slave"' in content:
                    return False
        except Exception as e:
            self.get_logger().warning(f"Failed to read coordination file {self.coordination_file}: {e}")
        return True

    def _load_active_project_from_file(self):
        active_file = os.path.join(self.projects_dir, 'active_project.json')
        if os.path.exists(active_file):
            try:
                with open(active_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    project_id = data.get('active_project_id')
                    if project_id:
                        self.store.load_project(project_id)
            except Exception as e:
                self.get_logger().warning(f"Failed to load active_project.json: {e}")

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
        except Exception as e:
            self.get_logger().error(f"HTTP Request [{endpoint}] failed: {e}")
            return False

    def _on_timer_tick(self):
        # 0. Realtime store mtime check & reload if store updated via web UI
        self.store.check_and_reload()

        # 1. Master PC check
        if not self._is_master_pc():
            # Slave PC: do not process local schedule triggers
            return

        if not self.store.current_project_id:
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
        self.get_logger().info(f"[SCHEDULE TRIGGER] START -> '{item.schedule_name}' (ID: {item.schedule_id})")
        # Trigger Coordinated Motion Continuous Start via Web Bridge API
        payload = {
            "command": "start_group",
            "run_mode": "continuous",
            "repeat_mode": item.motion_config.repeat_mode or "direct",
            "schedule_id": item.schedule_id
        }
        self._send_http_request("/api/coordination/control", payload)

    def _execute_stop_after_cycle(self, item: ScheduleItem):
        self.get_logger().info(f"[SCHEDULE TRIGGER] STOP-AFTER-CYCLE -> '{item.schedule_name}' (ID: {item.schedule_id})")
        # Trigger Coordinated Motion Stop-After-Cycle via Web Bridge API
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
