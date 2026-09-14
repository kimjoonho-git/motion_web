#!/usr/bin/env bash
# 빌드는 됐다는데 서비스가 자기 꾸러미를 못 찾을 때 고친다 · §6-97
#
#     PackageNotFoundError: No package metadata was found for motion-web-bridge
#
# `--symlink-install` 로 만든 파이썬 꾸러미는 메타데이터(egg-info)가 옛것과
# 어긋날 수 있다 · 그러면 `colcon` 은 "다 됐다" 하고 서비스는 시작에서 죽는다 ·
# 손으로 할 때 "캐시를 지우고 그 꾸러미만 다시 빌드" 하던 것이 이것이다.
#
# 혼자서도 쓰고(사람이 직접), 업데이트도 이것을 부른다 · 같은 규칙이 두 곳에
# 적히면 갈린다.
#
#     bash src/motion_web/web_bridge/deploy/repair_python_packages.sh
#
# 끝나면 서비스를 다시 등록할지는 부르는 쪽이 정한다 · 여기서는 빌드만 고친다.
set -Eeuo pipefail

WORKSPACE="${MOTION_WORKSPACE:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)}"
ROS_SETUP="${MOTION_ROS_SETUP:-/opt/ros/humble/setup.bash}"

# 서비스가 실제로 부르는 파이썬 배포 이름
read -r -a REQUIRED_DISTRIBUTIONS <<< "${MOTION_UPDATE_DISTRIBUTIONS-motion-web-bridge motion-coordination}"

BROKEN_PACKAGES=""

say() {
  echo "· $*"
}

#: 설치된 것을 **실제로 찾아본다** · 파일이 있는지가 아니라 파이썬이 찾는지가
#: 중요하다 · 서비스가 하는 일과 같은 방식으로 묻는다.
verify_installed_python() {
  BROKEN_PACKAGES=""
  local broken=()
  local distribution
  for distribution in "${REQUIRED_DISTRIBUTIONS[@]}"; do
    if ! (
      set +u
      # shellcheck disable=SC1090
      source "${ROS_SETUP}"
      # shellcheck disable=SC1091
      source "${WORKSPACE}/install/setup.bash"
      set -u
      python3 -c "import importlib.metadata as m; m.distribution('${distribution}')"
    ) >/dev/null 2>&1; then
      broken+=("${distribution//-/_}")
    fi
  done
  BROKEN_PACKAGES="${broken[*]}"
  [[ -z "${BROKEN_PACKAGES}" ]]
}

#: 어긋난 꾸러미만 지운다 · 전체를 지우면 몇 분이 몇십 분이 된다 ·
#: 그것을 쓰는 것들도 같이 다시 빌드한다 · 혼자만 새것이면 갈린다.
repair_packages() {
  local package
  for package in $1; do
    say "지우고 다시 빌드 · ${package}"
    rm -rf "${WORKSPACE}/build/${package}" "${WORKSPACE}/install/${package}"
  done
  set +u
  # shellcheck disable=SC1090
  source "${ROS_SETUP}"
  set -u
  colcon build --symlink-install --base-paths "${WORKSPACE}/src" \
    --packages-above $1
}

repair_if_broken() {
  if verify_installed_python; then
    say '꾸러미 정보 정상'
    return 0
  fi
  say "꾸러미 정보가 어긋났다 · ${BROKEN_PACKAGES}"
  repair_packages "${BROKEN_PACKAGES}"
  if verify_installed_python; then
    say '고쳤다'
    return 0
  fi

  # 마지막 수단 · 전체를 지우고 다시 빌드한다
  #
  # 몇 분 걸리지만, 여기서 포기하면 같은 실패가 **계속 반복된다** ·
  # 업데이트가 되돌아가면 고침까지 함께 지워져 다음 시도도 옛 코드로 돈다.
  say "부분 수리로 안 된다 · 전체를 지우고 다시 빌드한다 (몇 분 걸립니다)"
  rm -rf "${WORKSPACE}/build" "${WORKSPACE}/install"
  set +u
  # shellcheck disable=SC1090
  source "${ROS_SETUP}"
  set -u
  colcon build --symlink-install --base-paths "${WORKSPACE}/src"
  if verify_installed_python; then
    say '전체 빌드로 고쳤다'
    return 0
  fi
  say "고쳐지지 않았다 · ${BROKEN_PACKAGES}"
  return 1
}

# 직접 실행했을 때만 돈다 · 업데이트는 이 파일을 읽어 함수만 쓴다
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  cd "${WORKSPACE}"
  repair_if_broken
  say '서비스를 다시 등록하려면 · bash src/motion_web/web_bridge/deploy/install_user_service.sh'
fi
