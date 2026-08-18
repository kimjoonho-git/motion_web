#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE="${MOTION_WORKSPACE:-$(cd "${SCRIPT_DIR}/.." && pwd)}"
ROS_SETUP="/opt/ros/humble/setup.bash"
WORKSPACE_SETUP="${WORKSPACE}/install/setup.bash"

cd "${WORKSPACE}"

if [[ ! -f "${ROS_SETUP}" ]]; then
  echo "ROS 2 Humble 환경을 찾을 수 없습니다: ${ROS_SETUP}" >&2
  exit 1
fi

set +u
source "${ROS_SETUP}"
set -u

echo "[1/3] 전체 빌드: colcon build --symlink-install"
# UI 정적 자산 업데이트가 누락되지 않도록 기존 UI 빌드 캐시 강제 삭제
rm -rf "${WORKSPACE}/build/motion_web_ui" "${WORKSPACE}/install/motion_web_ui"
colcon build --symlink-install

if [[ ! -f "${WORKSPACE_SETUP}" ]]; then
  echo "빌드 환경을 찾을 수 없습니다: ${WORKSPACE_SETUP}" >&2
  exit 1
fi

set +u
source "${WORKSPACE_SETUP}"
set -u

echo "[2/3] 서비스 재시작: motion-control.service motion-coordination.service"
systemctl --user restart motion-control.service motion-coordination.service

echo "[3/3] 서비스 상태"
systemctl --user is-active motion-control.service motion-coordination.service

echo "빌드·재시작 완료"
