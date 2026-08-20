import os, sys, json
sys.path.insert(0, '/home/joonho_test/ros2_ws/src/motion_web/web_bridge')
from motion_web_bridge.bridge_node import MotionWebBridge

import rclpy
rclpy.init()
node = MotionWebBridge(test_mode=True)
try:
    node.project_repository.select_project('연동2-29d895ca')
    payload = node.load_motor_config()
    json.dumps(payload)
    print("Success JSON serialization")
except Exception as e:
    import traceback
    traceback.print_exc()
