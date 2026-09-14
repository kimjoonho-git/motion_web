#!/usr/bin/env bash
set -Eeuo pipefail

WORKSPACE="${MOTION_WORKSPACE:?MOTION_WORKSPACE is required}"
export ROS_LOCALHOST_ONLY="${ROS_LOCALHOST_ONLY:-0}"
# PC 이름공간과 DDS 도메인 · §6-96
#
# 조정 노드는 그룹 토픽만 쓰던 동안에는 이름표가 필요 없었다 · 이제 원시 MIDI
# 중계를 맡아 **이 PC 의** `/xtouch/midi` 를 연다 · 이름표가 없으면 옛 이름
# (`/xtouch/midi`)을 열어 아무 말 없이 아무것도 안 흐른다.
#
# 다른 서비스와 **같은 곳에서** 가져온다 · 따로 읽으면 갈린다.
GROUP_ENV_HELPER="${WORKSPACE}/src/motion_common/motion_common/group_env.py"
if [[ -f "${GROUP_ENV_HELPER}" ]]; then
  eval "$(python3 "${GROUP_ENV_HELPER}")"
fi
# 도우미가 없거나 실패해도 서비스는 떠야 한다 · 빈 값이면 예전 이름 그대로다
export MOTION_PC_NAMESPACE="${MOTION_PC_NAMESPACE:-}"
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
