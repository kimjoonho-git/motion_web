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

WORKSPACE="${MOTION_WORKSPACE:?MOTION_WORKSPACE is required}"
STATE_DIR="${MOTION_UPDATE_STATE_DIR:-${XDG_STATE_HOME:-${HOME}/.local/state}/motion-update}"
STATE_FILE="${STATE_DIR}/state.json"
LOG_FILE="${STATE_DIR}/update.log"
OPERATION_ID="${MOTION_UPDATE_OPERATION_ID:-update-$(date +%s)}"
BRANCH="main"
ROS_SETUP="/opt/ros/humble/setup.bash"
INSTALLER="${WORKSPACE}/src/motion_web/web_bridge/deploy/install_user_service.sh"
SERVICES=(motion-control.service motion-motor.service motion-coordination.service)

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
  bash "${INSTALLER}"
}

build() {
  set +u
  # shellcheck disable=SC1090
  source "${ROS_SETUP}"
  set -u
  colcon build --symlink-install --base-paths "${WORKSPACE}/src"
}

roll_back() {
  ROLLED_BACK="true"
  write_state running rollback "되돌리는 중 · ${FROM_COMMIT}"
  say "되돌린다 → ${FROM_COMMIT}"
  git -C "${WORKSPACE}" reset --hard "${FROM_COMMIT}" || true
  git -C "${WORKSPACE}" submodule update --init --recursive || true
  build || say '되돌린 뒤 빌드도 실패했다'
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
build

write_state running installing '서비스 설치·시작'
say '서비스를 켠다'
start_services

write_state success done "업데이트 완료 · ${TO_COMMIT}"
say '끝'
