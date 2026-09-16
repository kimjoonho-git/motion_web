#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"

export DEBIAN_FRONTEND=noninteractive
ROS_DAEMON_UPDATED=false

print_step() {
  echo
  echo "========================================="
  echo "$1"
  echo "========================================="
}

require_ubuntu_2204() {
  if [[ ! -f /etc/os-release ]]; then
    echo "Ubuntu 버전을 확인할 수 없습니다." >&2
    exit 1
  fi
  # shellcheck disable=SC1091
  source /etc/os-release
  if [[ "${ID:-}" != "ubuntu" || "${VERSION_ID:-}" != "22.04" ]]; then
    echo "지원 대상: Ubuntu 22.04" >&2
    echo "현재 OS: ${PRETTY_NAME:-unknown}" >&2
    exit 1
  fi
}

ensure_ros_apt_source() {
  if [[ -f /etc/apt/sources.list.d/ros2.list ]]; then
    return 0
  fi
  sudo apt update
  sudo apt install -y curl gnupg lsb-release software-properties-common
  sudo add-apt-repository -y universe
  sudo curl -sSL \
    https://raw.githubusercontent.com/ros/rosdistro/master/ros.key \
    -o /usr/share/keyrings/ros-archive-keyring.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] http://packages.ros.org/ros2/ubuntu $(. /etc/os-release && echo "$UBUNTU_CODENAME") main" \
    | sudo tee /etc/apt/sources.list.d/ros2.list >/dev/null
}

install_system_packages() {
  sudo apt update
  sudo apt install -y \
    btop \
    build-essential \
    chrony \
    cmake \
    curl \
    ethtool \
    gcc-12 \
    g++-12 \
    git \
    librtmidi-dev \
    locales \
    python3-colcon-common-extensions \
    python3-fastapi \
    python3-rosdep \
    python3-uvicorn \
    python3-yaml \
    ros-humble-desktop \
    software-properties-common \
    ttyd
}

sync_git_repository() {
  if [[ "${MOTION_WEB_SKIP_GIT_PULL:-}" == "1" ]]; then
    echo "Git 수신 건너뜀 · MOTION_WEB_SKIP_GIT_PULL=1"
    return 0
  fi
  if ! git -C "${WORKSPACE_DIR}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "Git 저장소가 아니므로 수신 건너뜀"
    return 0
  fi
  if ! git -C "${WORKSPACE_DIR}" remote get-url origin >/dev/null 2>&1; then
    echo "Git origin 없음 · 수신 건너뜀"
    return 0
  fi
  # 서브모듈과 추적 안 하는 파일은 보지 않는다 · §6-99
  #
  # 그냥 `status --porcelain` 을 보면 `src/motion_system`(서브모듈) 이 늘
  # "변경됨" 으로 나온다 · 그 안에 빌드 찌꺼기(`__pycache__`)가 생기기 때문이다 ·
  # 그래서 **모든 PC 에서 git 수신이 조용히 건너뛰어졌다** · 설치를 돌려도
  # 코드가 그대로였다.
  #
  # 막아야 하는 것은 "이 PC 에서 손으로 고친 추적 파일" 하나뿐이다.
  local dirty
  dirty="$(git -C "${WORKSPACE_DIR}" status --porcelain \
    --untracked-files=no --ignore-submodules=all)"
  if [[ -n "${dirty}" ]]; then
    echo "!! 고친 파일이 있어 Git 수신을 건너뜁니다 · 코드가 갱신되지 않습니다" >&2
    echo "${dirty}" >&2
    echo "!! 되돌리려면: git checkout -- <파일>" >&2
    return 0
  fi
  git -C "${WORKSPACE_DIR}" pull --recurse-submodules --ff-only
  git -C "${WORKSPACE_DIR}" submodule update --init --recursive
}

configure_locale_and_groups() {
  sudo locale-gen en_US en_US.UTF-8
  sudo update-locale LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8
  sudo usermod -aG dialout,audio "$USER"
}

initialize_rosdep() {
  if [[ ! -f /etc/ros/rosdep/sources.list.d/20-default.list ]]; then
    sudo rosdep init
  fi
  rosdep update
}

build_workspace() {
  systemctl --user stop motion-control.service motion-motor.service motion-coordination.service 2>/dev/null || true
  systemctl --user reset-failed 2>/dev/null || true
  # 옛 작업공간이 환경에 남아 있으면 그쪽 경로를 먼저 본다 · 깨끗한 ROS 만 켠다
  unset AMENT_PREFIX_PATH CMAKE_PREFIX_PATH COLCON_PREFIX_PATH || true
  unset ROS_PACKAGE_PATH LD_LIBRARY_PATH PYTHONPATH || true
  # `CMAKE_INCLUDE_PATH`·`CMAKE_LIBRARY_PATH` 는 7단계가 정해 준 EtherCAT
  # 자리다 · 여기서 지우면 다시 못 찾는다
  set +u
  source /opt/ros/humble/setup.bash
  set -u
  rosdep install --from-paths "${WORKSPACE_DIR}/src" --ignore-src -r -y
  # 지우고 처음부터 빌드한다 · §6-99
  #
  # `colcon` 은 **지워진 파일을 정리하지 않는다** · 꾸러미에서 파일이 빠지면
  # `install/` 에 옛 흔적(끊어진 링크 등)이 남아 다음 빌드가 거기서 깨진다 ·
  # 다른 PC 가 실제로 그렇게 멈췄다.
  #
  # 이 스크립트는 설치·업데이트 때만 돈다 · 1~2분 더 걸리는 대신 **늘 같은
  # 결과**가 나온다 · 빌드 상태를 사람이 추측할 일이 없어진다.
  rm -rf "${WORKSPACE_DIR}/build" "${WORKSPACE_DIR}/install"

  # `robot_manager` 는 심볼릭 링크로 깔지 않는다 · §6-102
  #
  # 이 꾸러미는 `ament_python` 인데 **소스 뿌리가 둘**이다.
  #     find_packages(where='robots/src') + find_packages(where='robot_manager/src')
  #     package_dir = {'robots': 'robots/src/robots', ...}
  # `--symlink-install` 은 `setup.py develop` 로 도는데, develop 은 뿌리 하나를
  # 전제해서 경로가 어긋난다 · `No such file or directory: .../robots/src/robots`
  # 가 그것이다. 예전에는 "한 번 더 빌드" 로 우연히 넘겼는데, 실패가 남는 PC 가
  # 있었다.
  #
  # 이 꾸러미는 서브모듈(`motion_system`) 것이라 우리가 고칠 수 없다 · 대신
  # **이것만** 평범하게 복사해 깐다. 우리가 손대는 꾸러미들은 그대로 심볼릭
  # 링크라 파이썬을 고치면 즉시 반영된다.
  echo "1/2 · robot_manager (심볼릭 링크 없이)"
  colcon build --base-paths "${WORKSPACE_DIR}/src" --packages-up-to robot_manager
  echo "2/2 · 나머지 전부"
  colcon build --symlink-install --base-paths "${WORKSPACE_DIR}/src" \
    --packages-skip-up-to robot_manager
  if command -v ros2 >/dev/null 2>&1; then
    ros2 daemon stop || true
    ros2 daemon start || true
    ROS_DAEMON_UPDATED=true
  fi
}

install_user_services() {
  set +u
  source "${WORKSPACE_DIR}/install/setup.bash"
  set -u
  bash "${SCRIPT_DIR}/web_bridge/deploy/install_user_service.sh"
}

restart_user_services() {
  systemctl --user daemon-reload
  systemctl --user restart motion-control.service || true
  systemctl --user restart motion-coordination.service || true
}

# IgH EtherCAT 이 **어디에 깔렸든** 찾아서 빌드·실행에 넘긴다 · §6-102
#
# 왜 PC 마다 결과가 달랐나.
#   피시1·2 : `--prefix=/opt/etherlab` 로 깔았고 `/usr/lib` 에도 복사돼 있었다.
#   피시3   : `--prefix=/usr/local/etherlab` 로 깔았다.
# `motor_manager/CMakeLists.txt` 가 뒤지는 자리는 고정돼 있다 ·
#   헤더 `/usr/local/include /usr/include /opt/etherlab/include`
#   라이브러리 `/usr/local/lib /usr/lib/x86_64-linux-gnu /usr/lib /opt/etherlab/lib`
# `/usr/local/etherlab/**` 은 **둘 다 없다.** 그래서 같은 코드가 한 PC 에서만
# 깨졌다. 깔린 자리 하나 차이였다.
#
# 게다가 옛 검사는 진짜 헤더를 못 찾으면 **빈 가짜 헤더**를 만들었다 · CMake 의
# `find_path` 는 통과하고 `find_library` 만 실패해서, "라이브러리가 없다" 는
# 엉뚱한 곳을 가리켰다. 있는 것을 없다고 판정하고 그 위에 가짜를 덮은 셈이다.
#
# 여기서는 자리를 **실제로 뒤져** 찾고, 찾으면 CMake 변수로 직접 넘긴다
# (`motor_manager` 가 덮어쓰라고 열어 둔 변수다) · 서브모듈은 건드리지 않는다.
ethercat_search_roots() {
  printf '%s\n' \
    /opt/etherlab \
    /usr/local/etherlab \
    /usr/local \
    /usr \
    /opt/ethercat \
    /usr/local/src/ethercat
}

find_ethercat_include_dir() {
  local root
  while read -r root; do
    # 크기가 0 인 것은 예전 설치가 만든 가짜다 · 진짜만 인정한다
    if [[ -s "${root}/include/ecrt.h" ]]; then
      echo "${root}/include"
      return 0
    fi
  done < <(ethercat_search_roots)
  local found
  found="$(find /opt /usr/local /usr/include -maxdepth 4 -name ecrt.h -size +0 \
    -print -quit 2>/dev/null || true)"
  [[ -n "${found}" ]] && dirname "${found}"
}

find_ethercat_library() {
  local root candidate
  while read -r root; do
    for candidate in "${root}/lib/libethercat.so" "${root}/lib/x86_64-linux-gnu/libethercat.so"; do
      if [[ -e "${candidate}" ]]; then
        echo "${candidate}"
        return 0
      fi
    done
  done < <(ethercat_search_roots)
  find /opt /usr/local /usr/lib -maxdepth 4 -name 'libethercat.so' -print -quit 2>/dev/null || true
}

register_ethercat_runtime_path() {
  # 빌드에서 찾아도 **실행할 때** 못 찾으면 소용없다 · ldconfig 에 등록한다
  local lib_dir="$1"
  if ldconfig -p 2>/dev/null | grep -q 'libethercat\.so'; then
    return 0
  fi
  echo "${lib_dir}" | sudo tee /etc/ld.so.conf.d/motion-etherlab.conf >/dev/null
  sudo ldconfig
  echo "EtherCAT 실행 경로 등록 · ${lib_dir}"
}

resolve_ethercat_paths() {
  local include_dir lib_file
  include_dir="$(find_ethercat_include_dir)"
  lib_file="$(find_ethercat_library)"

  if [[ -n "${include_dir}" && -n "${lib_file}" ]]; then
    echo "EtherCAT 헤더 · ${include_dir}/ecrt.h"
    echo "EtherCAT 라이브러리 · ${lib_file}"
    # `-D` 로 넘기면 그 변수를 안 쓰는 꾸러미마다 CMake 가 "쓰이지 않은
    # 변수" 경고를 낸다 · 경고가 쌓이면 진짜 오류가 묻힌다. CMake 가 표준으로
    # 읽는 탐색 경로를 쓴다 · `find_path`·`find_library` 가 HINTS 보다 **먼저**
    # 본다.
    export CMAKE_INCLUDE_PATH="${include_dir}${CMAKE_INCLUDE_PATH:+:${CMAKE_INCLUDE_PATH}}"
    export CMAKE_LIBRARY_PATH="$(dirname "${lib_file}")${CMAKE_LIBRARY_PATH:+:${CMAKE_LIBRARY_PATH}}"
    register_ethercat_runtime_path "$(dirname "${lib_file}")"
    # 예전 설치가 만든 빈 가짜 헤더는 치운다 · 두면 다음 사람이 또 속는다
    if [[ -f /opt/etherlab/include/ecrt.h && ! -s /opt/etherlab/include/ecrt.h \
          && "${include_dir}" != /opt/etherlab/include ]]; then
      sudo rm -f /opt/etherlab/include/ecrt.h
      echo "예전에 만들어 둔 빈 ecrt.h 를 지웠습니다"
    fi
    return 0
  fi

  echo
  echo "========================================="
  echo "EtherCAT 서보 모터 미설치 경고 (빌드는 진행됨)"
  echo "========================================="
  [[ -z "${include_dir}" ]] && echo "- ecrt.h 를 찾지 못했습니다"
  [[ -z "${lib_file}" ]] && echo "- libethercat.so 를 찾지 못했습니다"
  echo "찾아본 자리:"
  ethercat_search_roots | sed 's/^/  /'
  echo
  echo "AC 서보를 쓰신다면 IgH EtherCAT Master 를 설치하세요."
  echo "다이나믹셀만 쓰신다면 이 경고를 무시해도 됩니다."
  echo "========================================="

  if [[ -z "${include_dir}" ]]; then
    # 빌드만 통과시키는 자리표시 헤더 · **진짜로 아무 데도 없을 때만** 만든다
    sudo mkdir -p /opt/etherlab/include
    sudo touch /opt/etherlab/include/ecrt.h
    echo "!! 빈 ecrt.h 를 만들었습니다 · AC 서보는 동작하지 않습니다" >&2
  fi
}

print_step "1. Ubuntu 버전 확인"
require_ubuntu_2204

print_step "2. Git 코드 수신"
sync_git_repository

print_step "3. ROS 2 저장소 확인"
ensure_ros_apt_source

print_step "4. 필수 프로그램 설치"
install_system_packages

print_step "5. 사용자 권한·언어 설정"
configure_locale_and_groups

print_step "6. rosdep 초기화"
initialize_rosdep

print_step "7. EtherCAT 경로 확인"
resolve_ethercat_paths

print_step "8. 전체 빌드"
cd "${WORKSPACE_DIR}"
build_workspace

print_step "9. 자동실행 서비스 등록"
install_user_services

print_step "10. 서비스 적용"
restart_user_services

print_step "설치 완료"
echo "웹 주소: http://localhost:8000"
echo "상태 확인: systemctl --user status --no-pager motion-control.service motion-coordination.service"
if [[ "${ROS_DAEMON_UPDATED}" == true ]]; then
  echo "ROS 2 daemon 초기화 완료"
fi
echo
echo "설치 중 재부팅 안내가 나왔으면 sudo reboot 후 같은 명령을 다시 실행하세요:"
echo "  cd ${WORKSPACE_DIR}"
echo "  bash src/motion_web/install.sh"
