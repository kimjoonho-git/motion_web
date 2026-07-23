#!/usr/bin/env bash
set -Eeuo pipefail

WORKSPACE="${MOTION_WORKSPACE:?MOTION_WORKSPACE is required}"
MOTOR_CONFIG_FILE="${MOTOR_CONFIG_FILE:?MOTOR_CONFIG_FILE is required}"
export ROS_LOCALHOST_ONLY="${ROS_LOCALHOST_ONLY:-1}"
ROS_SETUP="/opt/ros/humble/setup.bash"
WORKSPACE_SETUP="${WORKSPACE}/install/setup.bash"
MOTOR_EXECUTABLE="${WORKSPACE}/install/motion_control_bridge/lib/motion_control_bridge/motor_manager_node"

if [[ ! -f "${ROS_SETUP}" ]]; then
  echo "ROS 2 Humble 환경을 찾을 수 없습니다: ${ROS_SETUP}" >&2
  exit 1
fi
if [[ ! -f "${WORKSPACE_SETUP}" ]]; then
  echo "작업공간 빌드 환경을 찾을 수 없습니다: ${WORKSPACE_SETUP}" >&2
  exit 1
fi
if [[ ! -x "${MOTOR_EXECUTABLE}" ]]; then
  echo "Motor Manager 실행 파일을 찾을 수 없습니다: ${MOTOR_EXECUTABLE}" >&2
  exit 1
fi
if [[ ! -f "${MOTOR_CONFIG_FILE}" ]]; then
  echo "적용된 모터 설정 파일을 찾을 수 없습니다: ${MOTOR_CONFIG_FILE}" >&2
  exit 1
fi

ethercat_error_positions() {
  ethercat slaves 2>/dev/null \
    | awk '$3 ~ /ERROR/ || $4 == "E" {print $1}' \
    || true
}

recover_ethercat_errors_before_launch() {
  if ! command -v ethercat >/dev/null 2>&1; then
    return 0
  fi

  local attempts="${ETHERCAT_RECOVERY_ATTEMPTS:-2}"
  local interval="${ETHERCAT_RECOVERY_INTERVAL_SEC:-0.5}"
  local command_timeout="${ETHERCAT_STATE_TIMEOUT_SEC:-3}"
  local attempt position positions

  for ((attempt = 1; attempt <= attempts; attempt++)); do
    positions="$(ethercat_error_positions)"
    if [[ -z "${positions}" ]]; then
      return 0
    fi
    for position in ${positions}; do
      echo "EtherCAT Slave ${position} 오류 플래그 해제 시도 (${attempt}/${attempts})"
      timeout "${command_timeout}" \
        ethercat reg_write -p "${position}" -t uint16 0x0120 0x0011 || true
      sleep "${interval}"
      timeout "${command_timeout}" ethercat states -p "${position}" PREOP || true
      sleep "${interval}"
    done
  done

  positions="$(ethercat_error_positions)"
  if [[ -n "${positions}" ]]; then
    echo "EtherCAT 오류 플래그를 해제하지 못했습니다: ${positions}" >&2
    return 1
  fi
}

if ! recover_ethercat_errors_before_launch; then
  exit 1
fi

set +u
source "${ROS_SETUP}"
source "${WORKSPACE_SETUP}"
set -u
exec "${MOTOR_EXECUTABLE}" --ros-args -p "config_file:=${MOTOR_CONFIG_FILE}"
