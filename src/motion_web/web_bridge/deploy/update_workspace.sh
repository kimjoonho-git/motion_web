#!/usr/bin/env bash
# 작업공간을 최신 main 으로 올리고 다시 빌드한다 · §6-97
#
# **서비스 바깥에서 돌아야 한다** · 웹 브리지가 자기 자식으로 띄우면
# `systemctl --user stop motion-control` 이 같은 cgroup 인 자기 자신을 같이
# 죽여 업데이트가 중간에 끊긴다 · 그래서 `systemd-run` 으로 따로 띄운다.
#
# 브랜치는 **main 고정**이다 · PC 마다 다른 브랜치가 조용히 도는 것이 제일
# 위험하다 · 다른 브랜치에 있으면 아무것도 안 하고 멈춘다.
#
# 진행 상황은 상태 파일에 적는다 · 웹 브리지는 이 일을 하는 중에 멈췄다가
# 다시 뜨므로, 기억이 아니라 **디스크**에 있어야 화면이 결과를 볼 수 있다.
set -Eeuo pipefail

# 받는 도중 **이 파일 자신이 바뀐다** · 오늘도 이 스크립트를 두 번 고쳤다.
#
# `git` 은 파일을 제자리에서 고쳐 쓴다(inode 가 그대로다) · bash 는 스크립트를
# 읽어 가며 실행하므로, 파일이 바뀌면 **읽던 자리부터 다른 글이 이어진다** ·
# 업데이트가 서비스를 멈춘 채로 깨진다.
#
# 그래서 먼저 자기를 복사해 그쪽에서 다시 시작한다 · 복사본은 아무도 안 건드린다.
if [[ "${MOTION_UPDATE_REEXEC:-}" != '1' ]]; then
  SELF_COPY="$(mktemp /tmp/motion-update-XXXXXX.sh)"
  cp "${BASH_SOURCE[0]}" "${SELF_COPY}"
  export MOTION_UPDATE_REEXEC=1
  export MOTION_UPDATE_SELF_COPY="${SELF_COPY}"
  exec /bin/bash "${SELF_COPY}" "$@"
fi
trap 'rm -f "${MOTION_UPDATE_SELF_COPY:-}"' EXIT

WORKSPACE="${MOTION_WORKSPACE:?MOTION_WORKSPACE is required}"
STATE_DIR="${MOTION_UPDATE_STATE_DIR:-${XDG_STATE_HOME:-${HOME}/.local/state}/motion-update}"
STATE_FILE="${STATE_DIR}/state.json"
LOG_FILE="${STATE_DIR}/update.log"
OPERATION_ID="${MOTION_UPDATE_OPERATION_ID:-update-$(date +%s)}"
BRANCH="main"
ROS_SETUP="${MOTION_ROS_SETUP:-/opt/ros/humble/setup.bash}"
INSTALLER="${WORKSPACE}/src/motion_web/web_bridge/deploy/install_user_service.sh"
# 검사는 이 목록을 비워 실제 장비를 건드리지 않고 전체 흐름을 돌린다
read -r -a SERVICES <<< "${MOTION_UPDATE_SERVICES-motion-control.service motion-motor.service motion-coordination.service}"

# 서비스가 실제로 부르는 파이썬 배포 이름 · 빌드가 끝나도 이것이 어긋나면
# 서비스가 시작에서 죽는다(`No package metadata was found for ...`)
read -r -a REQUIRED_DISTRIBUTIONS <<< "${MOTION_UPDATE_DISTRIBUTIONS-motion-web-bridge motion-coordination}"
BROKEN_PACKAGES=""

mkdir -p "${STATE_DIR}"
: > "${LOG_FILE}"
exec >>"${LOG_FILE}" 2>&1

FROM_COMMIT=""
TO_COMMIT=""
ROLLED_BACK="false"

write_state() {
  # status phase message · 값은 전부 인자로 넘긴다 · 셸 치환으로 파이썬 안에
  # 글자를 끼워 넣으면 따옴표 하나에 깨진다
  python3 - "$1" "$2" "$3" \
    "${OPERATION_ID}" "${FROM_COMMIT}" "${TO_COMMIT}" "${ROLLED_BACK}" \
    "${STATE_FILE}" <<'PY'
import json
import os
import sys
import tempfile
import time

status, phase, message, operation_id, from_commit, to_commit, rolled_back, path = (
    sys.argv[1:9]
)
payload = {
    'operation_id': operation_id,
    'status': status,
    'phase': phase,
    'message': message,
    'from_commit': from_commit,
    'to_commit': to_commit,
    'rolled_back': rolled_back == 'true',
    'updated_at': time.time(),
}
# 통째로 새로 쓰고 옮긴다 · 반쯤 쓰인 파일을 화면이 읽으면 JSON 이 깨진다
handle, temporary = tempfile.mkstemp(dir=os.path.dirname(path))
with os.fdopen(handle, 'w', encoding='utf-8') as stream:
    json.dump(payload, stream, ensure_ascii=False)
os.replace(temporary, path)
PY
}

say() {
  echo "· $(date '+%H:%M:%S') $*"
}

stop_services() {
  for service in "${SERVICES[@]}"; do
    systemctl --user stop "${service}" 2>/dev/null || true
  done
}

start_services() {
  # 설치 스크립트가 유닛을 다시 그리고 켠다 · 오늘처럼 유닛 템플릿이 바뀌면
  # 빌드만으로는 옛 유닛이 그대로 남는다
  #
  # 설치가 실패해도 **서비스는 켜고 나간다** · 여기서 그냥 끝내면 장비가
  # 꺼진 채로 남는다 · 실패했다는 것은 돌려주고, 판단은 부르는 쪽이 한다
  local status=0
  bash "${INSTALLER}" || status=$?
  if [[ "${status}" -ne 0 ]]; then
    say "설치 스크립트가 ${status} 로 끝났다 · 옛 유닛으로 서비스만 켠다"
    for service in "${SERVICES[@]}"; do
      systemctl --user start "${service}" || true
    done
  fi
  return "${status}"
}

build() {
  set +u
  # shellcheck disable=SC1090
  source "${ROS_SETUP}"
  set -u
  colcon build --symlink-install --base-paths "${WORKSPACE}/src"
}

#: 빌드가 깨지면 **옛 찌꺼기부터 의심한다**
#:
#: 손으로 할 때도 늘 그랬다 · 빌드 캐시를 지우고 그 꾸러미만 다시 빌드하면
#: 통과했다 · 그 일을 사람이 하지 않게 한다.
#:
#: 실패한 꾸러미만 지운다 · 전체를 지우면 몇 분이 몇십 분이 된다 · 어느
#: 꾸러미인지 못 찾았을 때만 통째로 지운다.
build_with_recovery() {
  if build; then
    return 0
  fi
  say '빌드가 실패했다 · 캐시를 지우고 다시 해 본다'
  write_state running building '빌드 실패 · 캐시를 지우고 다시 시도합니다'

  local failed
  failed="$(grep -oE 'Failed[[:space:]]+<<<[[:space:]]+[^ ]+' "${LOG_FILE}" \
    | awk '{print $NF}' | sort -u | tr '\n' ' ')"

  if [[ -z "${failed// /}" ]]; then
    say '어느 꾸러미인지 못 찾았다 · 전체를 지우고 다시 빌드한다'
    rm -rf "${WORKSPACE}/build" "${WORKSPACE}/install"
    build
    return
  fi

  say "다시 빌드할 꾸러미 · ${failed}"
  local package
  for package in ${failed}; do
    rm -rf "${WORKSPACE}/build/${package}" "${WORKSPACE}/install/${package}"
  done
  set +u
  # shellcheck disable=SC1090
  source "${ROS_SETUP}"
  set -u
  # 그 꾸러미를 쓰는 것들도 같이 다시 빌드한다 · 혼자만 새것이면 갈린다
  colcon build --symlink-install --base-paths "${WORKSPACE}/src" \
    --packages-above ${failed}
}

#: 빌드가 끝나도 **서비스가 못 뜨는 자리**가 하나 더 있다
#:
#: `--symlink-install` 로 만든 파이썬 꾸러미는 메타데이터(egg-info)가 옛것과
#: 어긋날 수 있다 · 그러면 빌드는 31개 전부 성공이라고 해놓고, 서비스는
#: `No package metadata was found for motion-web-bridge` 로 죽는다 ·
#: 실제로 그렇게 실패했다.
#:
#: **서비스를 켜기 전에** 잡는다 · 켜 보고 알면 그때는 이미 장비가 멈춘 뒤다.
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

repair_packages() {
  local package
  for package in $1; do
    say "꾸러미 정보를 지우고 다시 빌드 · ${package}"
    rm -rf "${WORKSPACE}/build/${package}" "${WORKSPACE}/install/${package}"
  done
  set +u
  # shellcheck disable=SC1090
  source "${ROS_SETUP}"
  set -u
  colcon build --symlink-install --base-paths "${WORKSPACE}/src" \
    --packages-above $1
}

roll_back() {
  ROLLED_BACK="true"
  write_state running rollback "되돌리는 중 · ${FROM_COMMIT}"
  say "되돌린다 → ${FROM_COMMIT}"
  git -C "${WORKSPACE}" reset --hard "${FROM_COMMIT}" || true
  git -C "${WORKSPACE}" submodule update --init --recursive || true
  build_with_recovery || say '되돌린 뒤 빌드도 실패했다'
}

on_error() {
  local line=$?
  say "실패했다 (exit ${line})"
  if [[ -n "${FROM_COMMIT}" ]]; then
    roll_back
  fi
  start_services || say '서비스 시작도 실패했다'
  write_state failure failed "업데이트 실패 · 기록을 확인하세요"
  exit 1
}
trap on_error ERR

cd "${WORKSPACE}"

write_state running checking '확인 중'
CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [[ "${CURRENT_BRANCH}" != "${BRANCH}" ]]; then
  write_state failure blocked "${BRANCH} 브랜치에서만 업데이트합니다 · 지금은 ${CURRENT_BRANCH}"
  exit 1
fi
# 추적 중인 파일이 고쳐져 있으면 멈춘다 · 합치다가 사용자의 수정을 덮거나
# 충돌 난 채로 빌드하는 것이 제일 나쁘다
if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  write_state failure blocked '고친 파일이 있습니다 · 먼저 정리하세요'
  exit 1
fi

FROM_COMMIT="$(git rev-parse HEAD)"
say "지금 ${FROM_COMMIT}"
git fetch origin "${BRANCH}"
TO_COMMIT="$(git rev-parse "origin/${BRANCH}")"
say "받을 것 ${TO_COMMIT}"

write_state running stopping '서비스 정지'
say '서비스를 멈춘다'
stop_services

write_state running pulling '코드 받는 중'
# 빨리 감기만 한다 · 여기서 합치기(merge)가 생기면 PC 마다 다른 역사가 된다
git merge --ff-only "origin/${BRANCH}"
git submodule update --init --recursive

write_state running building '빌드 중 · 몇 분 걸립니다'
say '빌드'
build_with_recovery

if ! verify_installed_python; then
  say "꾸러미 정보가 어긋났다 · ${BROKEN_PACKAGES}"
  write_state running building '꾸러미 정보가 어긋났다 · 지우고 다시 빌드합니다'
  repair_packages "${BROKEN_PACKAGES}"
  if ! verify_installed_python; then
    say "고쳐지지 않았다 · ${BROKEN_PACKAGES}"
    false  # 여기서 서비스를 켜면 죽은 채로 남는다 · 되돌린다
  fi
fi

write_state running installing '서비스 설치·시작'
say '서비스를 켠다'
INSTALL_STATUS=0
start_services || INSTALL_STATUS=$?

if [[ "${INSTALL_STATUS}" -eq 78 ]]; then
  # 78 은 "사람이 sudo 로 해야 할 일이 남았다" 는 뜻이다(실시간 우선순위 권한) ·
  # 코드는 이미 새것이고 빌드도 끝났다 · 되돌릴 이유가 없다 · 서비스는 위에서
  # 이미 켰다
  write_state success needs_attention \
    '업데이트는 됐지만 서비스 설치에 사람 손이 필요합니다 · 기록을 확인하세요'
  say '설치만 남았다'
  exit 0
fi
if [[ "${INSTALL_STATUS}" -ne 0 ]]; then
  false  # 여기서부터는 실패다 · ERR 트랩이 되돌린다
fi

write_state success done "업데이트 완료 · ${TO_COMMIT}"
say '끝'
