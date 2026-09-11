cmake_minimum_required(VERSION 3.8)
project(motion_web_ui)

find_package(ament_cmake REQUIRED)

set(BUILD_STATIC_DIR ${CMAKE_CURRENT_BINARY_DIR}/static)

# 캐시버스트 토큰(`?v=`)은 걷어냈다 · §6-92
#
# 서버가 `Cache-Control: no-cache` 와 `ETag` 를 보내므로 브라우저는 쓰기 전에
# 반드시 물어본다 · 토큰은 아무 일도 안 하면서 한 줄만 고쳐도 26개 파일을
# 함께 바꿨고, 사슬이 끊기면 브라우저가 옛 코드를 계속 쓰게 만들었다 · §6-75
add_custom_target(update_web_cache ALL
  COMMAND ${CMAKE_COMMAND} -E copy_directory ${CMAKE_CURRENT_SOURCE_DIR}/static ${BUILD_STATIC_DIR}
  COMMENT "Copying static files"
  VERBATIM
)

install(DIRECTORY ${BUILD_STATIC_DIR}
  DESTINATION share/${PROJECT_NAME}
)

ament_package()
