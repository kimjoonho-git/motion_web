cmake_minimum_required(VERSION 3.8)
project(motion_coordination_interfaces)

find_package(ament_cmake REQUIRED)
find_package(builtin_interfaces REQUIRED)
find_package(rosidl_default_generators REQUIRED)
find_package(action_msgs REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/GroupCommand.msg"
  "msg/GroupEvent.msg"
  "msg/GroupHeartbeat.msg"
  "msg/GroupTimeSync.msg"
  "msg/GroupAlarm.msg"
  "msg/GroupSystemInfo.msg"
  "msg/GroupMotionValues.msg"
  "action/MotorScan.action"
  DEPENDENCIES builtin_interfaces action_msgs
)

ament_export_dependencies(rosidl_default_runtime)
ament_package()
