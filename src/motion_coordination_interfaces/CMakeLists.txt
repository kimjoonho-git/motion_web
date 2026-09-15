cmake_minimum_required(VERSION 3.8)
project(motion_coordination_interfaces)

find_package(ament_cmake REQUIRED)
find_package(builtin_interfaces REQUIRED)
find_package(rosidl_default_generators REQUIRED)
find_package(action_msgs REQUIRED)
# 원시 MIDI 를 그대로 담는다 · 모양을 베껴 적으면 장치 규격과 갈린다 · §6-94
find_package(midi_msgs REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/GroupCommand.msg"
  "msg/GroupEvent.msg"
  "msg/GroupHeartbeat.msg"
  "msg/GroupTimeSync.msg"
  "msg/GroupAlarm.msg"
  "msg/GroupSystemInfo.msg"
  "msg/GroupMidi.msg"
  "msg/GroupMidiChannel.msg"
  "action/MotorScan.action"
  DEPENDENCIES
  midi_msgs builtin_interfaces action_msgs
)

ament_export_dependencies(rosidl_default_runtime)
ament_package()
