#!/usr/bin/env bash
set -Eeuo pipefail

WORKSPACE="${MOTION_WORKSPACE:?MOTION_WORKSPACE is required}"
export ROS_LOCALHOST_ONLY="${ROS_LOCALHOST_ONLY:-1}"
# PC 이름공간 · §6-95
#
# 여러 PC 를 한 DDS 망에 두면 같은 토픽 이름이 부딪힌다 · 이 PC 것에 접두사를
# 붙여 가른다 · 그룹 토픽(`/motion_group/...`)에는 안 붙는다.
#
# 비워 두면 토픽 이름이 예전과 글자 하나 다르지 않다 · 끄고 싶으면 비우면 된다.
export MOTION_PC_NAMESPACE="${MOTION_PC_NAMESPACE:-$(hostname)}"
ROS_SETUP="/opt/ros/humble/setup.bash"
WORKSPACE_SETUP="${WORKSPACE}/install/setup.bash"
SERVICE_EXECUTABLE="${WORKSPACE}/install/motion_web_bridge/lib/motion_web_bridge/motion_control_service"

if [[ ! -f "${ROS_SETUP}" ]]; then
  echo "ROS 2 Humble 환경을 찾을 수 없습니다: ${ROS_SETUP}" >&2
  exit 1
fi
if [[ ! -f "${WORKSPACE_SETUP}" ]]; then
  echo "작업공간 빌드 환경을 찾을 수 없습니다: ${WORKSPACE_SETUP}" >&2
  exit 1
fi
if [[ ! -x "${SERVICE_EXECUTABLE}" ]]; then
  echo "서비스 실행 파일을 찾을 수 없습니다: ${SERVICE_EXECUTABLE}" >&2
  exit 1
fi

# ROS/ament setup scripts probe optional variables and are not nounset-safe.
set +u
source "${ROS_SETUP}"
source "${WORKSPACE_SETUP}"
set -u
exec "${SERVICE_EXECUTABLE}"
