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

ethercat_master_indices() {
  ethercat master 2>/dev/null \
    | awk '/^Master[0-9]+$/ {sub(/^Master/, ""); print}' \
    || true
}

ethercat_error_slaves() {
  local master_index

  while IFS= read -r master_index; do
    [[ -n "${master_index}" ]] || continue
    ethercat slaves -m "${master_index}" 2>/dev/null \
      | awk -v master="${master_index}" \
          '$3 ~ /ERROR/ || $4 == "E" {print master ":" $1}' \
      || true
  done < <(ethercat_master_indices)
}

recover_ethercat_errors_before_launch() {
  if ! command -v ethercat >/dev/null 2>&1; then
    return 0
  fi

  local attempts="${ETHERCAT_RECOVERY_ATTEMPTS:-2}"
  local interval="${ETHERCAT_RECOVERY_INTERVAL_SEC:-0.5}"
  local command_timeout="${ETHERCAT_STATE_TIMEOUT_SEC:-3}"
  local attempt error_slave error_slaves master_index position

  for ((attempt = 1; attempt <= attempts; attempt++)); do
    error_slaves="$(ethercat_error_slaves)"
    if [[ -z "${error_slaves}" ]]; then
      return 0
    fi

    while IFS= read -r error_slave; do
      [[ -n "${error_slave}" ]] || continue
      IFS=: read -r master_index position <<< "${error_slave}"
      echo "EtherCAT Master ${master_index} Slave ${position} 오류 플래그 해제 시도 (${attempt}/${attempts})"
      timeout "${command_timeout}" \
        ethercat reg_write -m "${master_index}" -p "${position}" \
          -t uint16 0x0120 0x0011 || true
      sleep "${interval}"
      timeout "${command_timeout}" \
        ethercat states -m "${master_index}" -p "${position}" PREOP || true
      sleep "${interval}"
    done <<< "${error_slaves}"
  done

  error_slaves="$(ethercat_error_slaves)"
  if [[ -n "${error_slaves}" ]]; then
    echo "EtherCAT 오류 플래그를 해제하지 못했습니다:" >&2
    while IFS= read -r error_slave; do
      [[ -n "${error_slave}" ]] || continue
      IFS=: read -r master_index position <<< "${error_slave}"
      echo "  Master ${master_index} Slave ${position}" >&2
    done <<< "${error_slaves}"
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
