cmake_minimum_required(VERSION 3.8)
project(motion_web_ui)

find_package(ament_cmake REQUIRED)

install(DIRECTORY static
  DESTINATION share/${PROJECT_NAME}
)

ament_package()
