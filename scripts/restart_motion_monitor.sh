#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE="${MOTION_WORKSPACE:-$(cd "${SCRIPT_DIR}/.." && pwd)}"
LOG_DIR="${WORKSPACE}/log/web_apply_restart"
STAMP="$(date +%Y%m%d-%H%M%S)"

mkdir -p "${LOG_DIR}"
exec >> "${LOG_DIR}/restart-${STAMP}.log" 2>&1

log() {
  echo "[$(date +%Y%m%d-%H%M%S.%3N)] $*"
}

# Until the multi-PC namespace/synchronization layer is implemented, every PC
# must keep its low-level motor topics local. The web UI is still reachable
# from another PC because this setting affects ROS DDS only, not HTTP.
export ROS_LOCALHOST_ONLY="${ROS_LOCALHOST_ONLY:-1}"

any_running() {
  for pattern in "$@"; do
    if pgrep -f "${pattern}" >/dev/null 2>&1; then
      return 0
    fi
  done
  return 1
}

wait_until_stopped() {
  local timeout_sec="$1"
  shift
  local start_ms now_ms elapsed_ms timeout_ms
  start_ms="$(date +%s%3N)"
  timeout_ms="$(python3 -c "print(int(float('${timeout_sec}') * 1000))")"
  while any_running "$@"; do
    now_ms="$(date +%s%3N)"
    elapsed_ms=$((now_ms - start_ms))
    if (( elapsed_ms >= timeout_ms )); then
      return 1
    fi
    sleep 0.05
  done
  return 0
}

log "restart_motion_monitor.sh started"

sleep "${RESTART_DELAY_SEC:-0.2}"

patterns=(
  "ros2 launch motion_state_monitor motion_monitor.launch.py"
  "ros2 launch motion_state_monitor project_services.launch.py"
  "install/motion_control_bridge/lib/motion_control_bridge/motor_manager_node"
  "install/motion_state_monitor/lib/motion_state_monitor/motion_state_monitor"
  "install/motion_supervisor/lib/motion_supervisor/motion_supervisor"
  "ros2 run motion_supervisor motion_supervisor"
  "install/motion_runtime/lib/motion_runtime/motion_mapping_manager"
  "install/motion_runtime/lib/motion_runtime/motion_run_manager"
  "install/motion_studio/lib/motion_studio/motion_studio_node"
  "install/motion_studio/lib/motion_studio/motion_studio_editor_node"
  "install/midi_control/lib/midi_control/midi_control_node"
  "install/motion_web_bridge/lib/motion_web_bridge/motion_web_bridge"
  "install/midi_input_bridge/lib/midi_input_bridge/midi_input_node"
  "ros2 launch midi_control midi_control.launch.py"
  # Stop legacy pre-refactor processes so they cannot publish duplicate MIDI
  # or motion state on the current topics.
  "install/motion_web_bridge/lib/motion_web_bridge/midi_monitor_node"
  "install/motion_web_bridge/lib/motion_web_bridge/motion_mapping_manager"
  "install/motion_web_bridge/lib/motion_web_bridge/motion_run_manager"
  "ros2 launch motion_web_bridge midi_monitor.launch.py"
)

for pattern in "${patterns[@]}"; do
  pkill -TERM -f "${pattern}" || true
done

if wait_until_stopped "${TERM_WAIT_SEC:-1.5}" "${patterns[@]}"; then
  log "previous nodes stopped after TERM"
else
  log "TERM wait timeout; sending KILL to remaining nodes"
fi

for pattern in "${patterns[@]}"; do
  pkill -KILL -f "${pattern}" || true
done

wait_until_stopped "${KILL_WAIT_SEC:-0.5}" "${patterns[@]}" || true

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
      log "acknowledging EtherCAT slave ${position} before motor node start (attempt ${attempt}/${attempts})"
      # AL Control 0x11 = request INIT + acknowledge the slave error flag.
      # This never enables the servo or sends a position command.
      timeout "${command_timeout}" \
        ethercat reg_write -p "${position}" -t uint16 0x0120 0x0011 || true
      sleep "${interval}"
      timeout "${command_timeout}" ethercat states -p "${position}" PREOP || true
      sleep "${interval}"
    done
  done

  positions="$(ethercat_error_positions)"
  if [[ -n "${positions}" ]]; then
    log "EtherCAT error flag remains on slave(s): ${positions}"
    return 1
  fi
  return 0
}

cd "${WORKSPACE}"
CONFIG_FILE="${MOTOR_CONFIG_FILE:-${WORKSPACE}/config/bootstrap_motor_config.yaml}"
START_MOTOR_MANAGER="false"
if [[ -n "${MOTOR_CONFIG_FILE:-}" ]]; then
  START_MOTOR_MANAGER="true"
fi

MOTOR_START_BLOCK_REASON=""
if [[ "${START_MOTOR_MANAGER}" == "true" ]] \
  && ! recover_ethercat_errors_before_launch; then
  START_MOTOR_MANAGER="false"
  MOTOR_START_BLOCK_REASON="EtherCAT 오류 플래그를 해제하지 못해 모터 관리 노드 시작을 차단했습니다"
  log "${MOTOR_START_BLOCK_REASON}"
fi

export CONFIG_FILE
export START_MOTOR_MANAGER
export MOTOR_START_BLOCK_REASON
export WORKSPACE
PROJECT_GENERATION="${MOTION_PROJECT_GENERATION:-0}"
export PROJECT_GENERATION
log "ROS DDS isolation: ROS_LOCALHOST_ONLY=${ROS_LOCALHOST_ONLY}"
log "starting motion_monitor.launch.py with config_file=${CONFIG_FILE}, project_generation=${PROJECT_GENERATION}, start_motor_manager=${START_MOTOR_MANAGER}"
sg dialout -c 'bash -lc '"'"'source "$WORKSPACE/install/setup.bash" && ros2 launch motion_state_monitor motion_monitor.launch.py config_file:="$CONFIG_FILE" motion_projects_dir:="$WORKSPACE/motion_projects" start_motor_manager:="$START_MOTOR_MANAGER"'"'" &
launch_pid="$!"
log "starting MIDI control (motor requests routed through motion_supervisor, config_file=${CONFIG_FILE})"
bash -lc 'source "$WORKSPACE/install/setup.bash" && ros2 launch midi_control midi_control.launch.py motor_config_file:="$CONFIG_FILE" motion_projects_dir:="$WORKSPACE/motion_projects"' &
midi_launch_pid="$!"
wait "${launch_pid}"
launch_status="$?"
kill "${midi_launch_pid}" 2>/dev/null || true
wait "${midi_launch_pid}" 2>/dev/null || true
exit "${launch_status}"
