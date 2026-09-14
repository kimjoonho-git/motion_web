#!/usr/bin/env bash
set -Eeuo pipefail

WORKSPACE="${MOTION_WORKSPACE:?MOTION_WORKSPACE is required}"
# 네트워크를 여는가 · §6-96
#
# 전에는 모터 쪽 노드를 이 컴퓨터 안에 가둬 뒀다(`ROS_LOCALHOST_ONLY=1`) ·
# 그런데 연동된 PC 의 MIDI 로 모터를 움직이려면 열어야 한다.
#
# 열어도 무방한 이유 · 토픽마다 PC 이름표가 붙고(`/joonhoTest/...`), DDS
# 도메인이 그룹 전용(기본 21)이라 남의 ROS 장비와 섞이지 않는다.
#
# 문제가 생기면 `MOTION_GROUP_NETWORK=0` 으로 도로 잠근다.
export MOTION_GROUP_NETWORK="${MOTION_GROUP_NETWORK:-1}"
if [[ "${MOTION_GROUP_NETWORK}" == "1" ]]; then
  export ROS_LOCALHOST_ONLY=0
else
  export ROS_LOCALHOST_ONLY=1
fi
# PC 이름공간과 DDS 도메인 · §6-96
#
# "이 PC 는 누구인가"와 "어느 DDS 망에 있는가"는 그룹 설정이 주인이다 ·
# `config/motion_coordination.yaml` 의 `pc_id` 와 `dds_domain_id` · 호스트
# 이름을 따로 읽으면 주인이 둘이 된다.
#
# 바깥에서 이미 정했으면 그것을 존중한다 · 되돌릴 수 있어야 한다.
GROUP_ENV_HELPER="${WORKSPACE}/src/motion_common/motion_common/group_env.py"
if [[ -f "${GROUP_ENV_HELPER}" ]]; then
  eval "$(python3 "${GROUP_ENV_HELPER}")"
fi
# 도우미가 없거나 실패해도 서비스는 떠야 한다 · 빈 값이면 예전 이름 그대로다
export MOTION_PC_NAMESPACE="${MOTION_PC_NAMESPACE:-}"
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
