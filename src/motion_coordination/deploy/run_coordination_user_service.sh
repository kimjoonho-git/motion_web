#!/usr/bin/env bash
set -Eeuo pipefail

WORKSPACE="${MOTION_WORKSPACE:?MOTION_WORKSPACE is required}"
export ROS_LOCALHOST_ONLY="${ROS_LOCALHOST_ONLY:-0}"
ROS_SETUP="/opt/ros/humble/setup.bash"
WORKSPACE_SETUP="${WORKSPACE}/install/setup.bash"
EXECUTABLE="${WORKSPACE}/install/motion_coordination/lib/motion_coordination/motion_coordination_node"

if [[ ! -f "${ROS_SETUP}" ]]; then
  echo "ROS 2 Humble 환경을 찾을 수 없습니다: ${ROS_SETUP}" >&2
  exit 1
fi
if [[ ! -f "${WORKSPACE_SETUP}" ]]; then
  echo "작업공간 빌드 환경을 찾을 수 없습니다: ${WORKSPACE_SETUP}" >&2
  exit 1
fi
if [[ ! -x "${EXECUTABLE}" ]]; then
  echo "PC 연동 서비스 실행 파일을 찾을 수 없습니다: ${EXECUTABLE}" >&2
  exit 1
fi

set +u
source "${ROS_SETUP}"
source "${WORKSPACE_SETUP}"
set -u
exec "${EXECUTABLE}"
