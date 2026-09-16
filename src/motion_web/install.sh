#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"

export DEBIAN_FRONTEND=noninteractive
ROS_DAEMON_UPDATED=false
CURRENT_STEP="시작 전"

print_step() {
  CURRENT_STEP="$1"
  echo
  echo "========================================="
  echo "$1"
  echo "========================================="
}

# 멈추면 **어디서 왜 멈췄는지**를 마지막 화면에 남긴다 · §6-102
#
# `set -Eeuo pipefail` 은 오류 한 줄만 뱉고 끝난다 · 화면을 위로 한참 올려야
# 원인이 보이고, 그마저 PC 앞에 있는 사람이 옮겨 적어야 했다. PC 가 늘어날수록
# 그 전달이 병목이 된다 · 마지막 스무 줄만 찍어 보내면 되게 한다.
report_failure() {
  local exit_code=$1 line=$2 command=$3
  echo >&2
  echo "=========================================" >&2
  echo "설치 실패" >&2
  echo "=========================================" >&2
  echo "멈춘 단계 · ${CURRENT_STEP}" >&2
  echo "실행하려던 것 · ${command}" >&2
  echo "스크립트 ${line}행 · 끝난 값 ${exit_code}" >&2
  echo >&2
  case "${CURRENT_STEP}" in
    *"Git"*)
      echo "자주 있는 원인" >&2
      echo "  - 원격에 닿지 못함 · 인터넷·사내망 확인" >&2
      echo "  - 계정 권한 없음 · git 자격증명 확인" >&2
      ;;
    *"필수 프로그램"*|*"ROS 2 저장소"*|*"rosdep"*)
      echo "자주 있는 원인" >&2
      echo "  - apt 잠김 · 다른 설치가 돌고 있는지 확인" >&2
      echo "  - 인터넷 안 됨 · 저장소에 닿는지 확인" >&2
      ;;
    *"빌드"*)
      echo "자주 있는 원인" >&2
      echo "  - 위 로그의 '--- stderr:' 아래 줄이 진짜 원인이다" >&2
      echo "  - 저장 공간 부족 · df -h ~" >&2
      echo "  - EtherCAT 자리 · 7단계가 찍은 경로를 확인" >&2
      ;;
  esac
  echo >&2
  echo "이 화면 그대로(위 20줄 포함) 알려 주시면 됩니다." >&2
  echo "=========================================" >&2
  exit "${exit_code}"
}
trap 'report_failure "$?" "${LINENO}" "${BASH_COMMAND}"' ERR

# 중간에 멈추는 흔한 이유 둘을 **먼저** 걸러낸다
preflight_checks() {
  local avail_gb
  avail_gb="$(df -BG --output=avail "${WORKSPACE_DIR}" | tail -1 | tr -dc '0-9')"
  echo "작업공간 · ${WORKSPACE_DIR}"
  echo "남은 공간 · ${avail_gb}GB"
  if [[ "${avail_gb}" -lt 5 ]]; then
    echo "!! 저장 공간이 5GB 미만입니다 · 전체 빌드가 중간에 멈출 수 있습니다" >&2
  fi
  # sudo 는 여러 단계에서 쓴다 · 중간에 물어 멈추지 않게 여기서 한 번만 받는다
  if ! sudo -n true 2>/dev/null; then
    echo "관리자 권한이 필요합니다 · 비밀번호를 한 번만 입력하세요"
    sudo -v
  fi
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
  if ! git -C "${WORKSPACE_DIR}" fetch --prune origin; then
    echo "!! 원격에 닿지 못했습니다 · 지금 있는 코드로 빌드합니다" >&2
    return 0
  fi

  local upstream
  upstream="$(git -C "${WORKSPACE_DIR}" rev-parse --abbrev-ref \
    --symbolic-full-name '@{u}' 2>/dev/null || echo 'origin/main')"

  # **어떤 PC 든 이 명령 하나로 원격과 같아진다** · §6-102
  #
  # 예전에는 두 자리에서 사람 손을 요구했다.
  #   - 고친 파일이 있으면 수신을 건너뛰고 **옛 코드로 빌드**했다 · 조용한 실패라
  #     설치를 돌려도 코드가 그대로인 줄 몰랐다.
  #   - 이력이 갈라지면 `pull --ff-only` 가 실패하고 `set -e` 로 **스크립트가
  #     통째로 죽었다** · 빌드 근처에도 못 갔다.
  # 그래서 PC 마다 다른 명령을 손으로 치게 됐고, **그 자체가 다음 사고**가 됐다.
  #
  # 여기서는 원격에 맞추되 **아무것도 잃지 않는다** · 이 PC 에서 고친 파일은
  # `backups/` 로 떠 두고, 원격에 없는 커밋에는 표를 붙인다. 둘 다 되돌릴 수 있다.
  #
  # 서브모듈은 보지 않는다 · 빌드 찌꺼기(`__pycache__`)로 늘 "변경됨" 이라
  # 그것까지 세면 모든 PC 가 갱신을 멈춘다 · §6-99
  local stamp dirty backup_dir file
  stamp="$(date +%Y%m%d-%H%M%S)"
  dirty="$(git -C "${WORKSPACE_DIR}" status --porcelain \
    --untracked-files=no --ignore-submodules=all)"
  if [[ -n "${dirty}" ]]; then
    backup_dir="${WORKSPACE_DIR}/backups/pre-update-${stamp}"
    echo "이 PC 에서 고친 파일을 옮겨 둡니다 · ${backup_dir}"
    while read -r _ file; do
      [[ -z "${file}" || ! -f "${WORKSPACE_DIR}/${file}" ]] && continue
      mkdir -p "${backup_dir}/$(dirname "${file}")"
      cp -p "${WORKSPACE_DIR}/${file}" "${backup_dir}/${file}"
      echo "  ${file}"
    done <<< "${dirty}"
  fi

  if [[ -n "$(git -C "${WORKSPACE_DIR}" log --oneline "${upstream}..HEAD" 2>/dev/null)" ]]; then
    echo "이 PC 에만 있는 커밋에 표를 붙입니다 · pre-update-${stamp}"
    git -C "${WORKSPACE_DIR}" log --oneline "${upstream}..HEAD" | sed 's/^/  /'
    git -C "${WORKSPACE_DIR}" tag "pre-update-${stamp}" >/dev/null 2>&1 || true
  fi

  git -C "${WORKSPACE_DIR}" reset --hard "${upstream}"
  git -C "${WORKSPACE_DIR}" submodule update --init --recursive --force
  echo "원격과 같아졌습니다 · $(git -C "${WORKSPACE_DIR}" log --oneline -1)"
  if [[ -n "${dirty}" ]]; then
    echo
    echo "!! 고친 파일은 덮어썼습니다 · 필요하면 아래에서 가져오세요" >&2
    echo "!! ${backup_dir}" >&2
  fi
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
  # 2단계는 **따로 도는 colcon** 이다 · 1단계가 깐 것을 환경으로 받아야
  # 거기 기대는 꾸러미가 파이썬 경로를 제대로 잡는다.
  #
  # 이 줄을 빠뜨렸더니 `motion_control_robot`(robot_manager 에 기댄다)과
  # `motion_state_monitor` 가 이렇게 죽었다:
  #     Fatal Python error: init_import_site: Failed to import the site module
  # 한 번에 빌드하던 때는 colcon 이 제 안에서 이어 줘서 필요 없던 줄이다 ·
  # 둘로 가르면서 그 이음매가 생겼다.
  set +u
  source "${WORKSPACE_DIR}/install/setup.bash"
  set -u
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

print_step "0. 사전 확인"
preflight_checks

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
# 끝났는지 **눈으로 한 번에** 보이게 · 단계 제목만 지나가면 끝난 줄 알기 어렵다
echo "작업공간 · ${WORKSPACE_DIR}"
echo "코드     · $(git -C "${WORKSPACE_DIR}" log --oneline -1 2>/dev/null || echo '(git 아님)')"
echo "빌드     · $(find "${WORKSPACE_DIR}/install" -maxdepth 1 -mindepth 1 -type d 2>/dev/null | wc -l)개 꾸러미"
echo -n "서비스   · "
for unit in motion-control motion-motor motion-coordination; do
  echo -n "${unit}=$(systemctl --user is-active "${unit}.service" 2>/dev/null || echo unknown) "
done
echo
echo "웹 주소: http://localhost:8000"
echo "상태 확인: systemctl --user status --no-pager motion-control.service motion-coordination.service"
if [[ "${ROS_DAEMON_UPDATED}" == true ]]; then
  echo "ROS 2 daemon 초기화 완료"
fi
echo
echo "설치 중 재부팅 안내가 나왔으면 sudo reboot 후 같은 명령을 다시 실행하세요:"
echo "  cd ${WORKSPACE_DIR}"
echo "  bash src/motion_web/install.sh"
