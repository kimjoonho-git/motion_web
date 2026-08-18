#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"

export DEBIAN_FRONTEND=noninteractive

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
  sudo add-apt-repository -y universe
  sudo apt update
  sudo apt install -y curl gnupg lsb-release
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
    git \
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
  # shellcheck disable=SC1091
  source /opt/ros/humble/setup.bash
  rosdep install --from-paths "${WORKSPACE_DIR}/src" --ignore-src -r -y
  colcon build --symlink-install --base-paths "${WORKSPACE_DIR}/src"
}

install_user_services() {
  # shellcheck disable=SC1091
  source "${WORKSPACE_DIR}/install/setup.bash"
  bash "${SCRIPT_DIR}/web_bridge/deploy/install_user_service.sh"
}

print_step "1. Ubuntu 버전 확인"
require_ubuntu_2204

print_step "2. ROS 2 저장소 확인"
ensure_ros_apt_source

print_step "3. 필수 프로그램 설치"
install_system_packages

print_step "4. 사용자 권한·언어 설정"
configure_locale_and_groups

print_step "5. rosdep 초기화"
initialize_rosdep

print_step "6. 전체 빌드"
cd "${WORKSPACE_DIR}"
build_workspace

print_step "7. 자동실행 서비스 등록"
install_user_services

print_step "설치 완료"
echo "웹 주소: http://localhost:8000"
echo "상태 확인: systemctl --user status --no-pager motion-control.service motion-coordination.service"
echo
echo "설치 중 재부팅 안내가 나왔으면 sudo reboot 후 같은 명령을 다시 실행하세요:"
echo "  cd ${WORKSPACE_DIR}"
echo "  bash src/motion_web/install.sh"
