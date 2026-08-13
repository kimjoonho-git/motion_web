cmake_minimum_required(VERSION 3.8)
project(motion_web_ui)

find_package(ament_cmake REQUIRED)

find_package(Python3 REQUIRED COMPONENTS Interpreter)

set(BUILD_STATIC_DIR ${CMAKE_CURRENT_BINARY_DIR}/static)

add_custom_target(update_web_cache ALL
  COMMAND ${CMAKE_COMMAND} -E copy_directory ${CMAKE_CURRENT_SOURCE_DIR}/static ${BUILD_STATIC_DIR}
  COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/scripts/update_cache.py ${BUILD_STATIC_DIR}
  COMMENT "Copying static files and updating cache busting versions"
  VERBATIM
)

install(DIRECTORY ${BUILD_STATIC_DIR}
  DESTINATION share/${PROJECT_NAME}
)

ament_package()
