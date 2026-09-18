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

# 랜이 생기기 전에 뜨면 DDS 가 루프백에 갇힌다 · §6-96
#
# Fast DDS 는 참가자를 만드는 그 순간의 랜카드만 훑는다 · 주소가 없을 때 뜨면
# `127.0.0.1` 만 광고하고, 나중에 랜이 살아나도 다시 보지 않는다 · 그래서 그 뒤로
# **영영 다른 PC 를 못 본다** · 화면에는 그냥 「통신 단절」로만 보인다.
#
# 유닛의 `After=network-online.target` 으로는 못 막는다 · 그 타깃은 시스템
# 스코프에만 있고 이건 사용자 서비스라, systemd 가 없는 유닛으로 보고 조용히
# 넘어간다 · 그래서 여기서 직접 기다린다.
#
# 랜이 영영 없어도 서비스는 떠야 한다 · 도우미는 기다리다 포기하고 0 으로 끝낸다.
NET_READY_HELPER="${WORKSPACE}/src/motion_common/motion_common/net_ready.py"
if [[ -f "${NET_READY_HELPER}" ]]; then
  python3 "${NET_READY_HELPER}" || true
fi
set +u
source "${ROS_SETUP}"
source "${WORKSPACE_SETUP}"
set -u
exec "${EXECUTABLE}"
