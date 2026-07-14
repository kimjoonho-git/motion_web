cmake_minimum_required(VERSION 3.8)
project(midi_input_bridge CXX)

if(NOT CMAKE_CXX_STANDARD)
  set(CMAKE_CXX_STANDARD 17)
endif()

find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(midi_msgs REQUIRED)
find_package(PkgConfig REQUIRED)
pkg_check_modules(RTMIDI REQUIRED IMPORTED_TARGET rtmidi)

add_executable(midi_input_node src/midi_input_node.cpp)
ament_target_dependencies(midi_input_node rclcpp midi_msgs)
target_link_libraries(midi_input_node PkgConfig::RTMIDI)
install(TARGETS midi_input_node DESTINATION lib/${PROJECT_NAME})

ament_package()
