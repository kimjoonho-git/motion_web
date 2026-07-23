#!/usr/bin/env bash
set -Eeuo pipefail

WORKSPACE="${MOTION_WORKSPACE:?MOTION_WORKSPACE is required}"
export ROS_LOCALHOST_ONLY="${ROS_LOCALHOST_ONLY:-1}"
ROS_SETUP="/opt/ros/humble/setup.bash"
WORKSPACE_SETUP="${WORKSPACE}/install/setup.bash"
SERVICE_EXECUTABLE="${WORKSPACE}/install/motion_web_bridge/lib/motion_web_bridge/motion_motor_service"

if [[ ! -f "${ROS_SETUP}" ]]; then
  echo "ROS 2 Humble 환경을 찾을 수 없습니다: ${ROS_SETUP}" >&2
  exit 1
fi
if [[ ! -f "${WORKSPACE_SETUP}" ]]; then
  echo "작업공간 빌드 환경을 찾을 수 없습니다: ${WORKSPACE_SETUP}" >&2
  exit 1
fi
if [[ ! -x "${SERVICE_EXECUTABLE}" ]]; then
  echo "Motor Manager 서비스 실행 파일을 찾을 수 없습니다: ${SERVICE_EXECUTABLE}" >&2
  exit 1
fi

set +u
source "${ROS_SETUP}"
source "${WORKSPACE_SETUP}"
set -u
exec "${SERVICE_EXECUTABLE}"
