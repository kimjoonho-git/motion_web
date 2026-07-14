#!/usr/bin/env bash
set -Eeuo pipefail

WORKSPACE="/home/joonho_test/ros2_ws"
LOG_DIR="${WORKSPACE}/log/web_apply_restart"
STAMP="$(date +%Y%m%d-%H%M%S)"

mkdir -p "${LOG_DIR}"
exec >> "${LOG_DIR}/restart-${STAMP}.log" 2>&1

log() {
  echo "[$(date +%Y%m%d-%H%M%S.%3N)] $*"
}

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
  "install/motion_control_bridge/lib/motion_control_bridge/motor_manager_node"
  "install/motion_state_monitor/lib/motion_state_monitor/motion_state_monitor"
  "install/motion_supervisor/lib/motion_supervisor/motion_supervisor"
  "ros2 run motion_supervisor motion_supervisor"
  "install/motion_runtime/lib/motion_runtime/motion_mapping_manager"
  "install/motion_runtime/lib/motion_runtime/motion_run_manager"
  "install/midi_control/lib/midi_control/midi_control_node"
  "install/motion_web_bridge/lib/motion_web_bridge/motion_web_bridge"
  "install/midi_input_bridge/lib/midi_input_bridge/midi_input_node"
  "ros2 launch midi_control midi_control.launch.py"
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

recover_ethercat_errors_after_launch() {
  if ! command -v ethercat >/dev/null 2>&1; then
    return 0
  fi

  local delay="${ETHERCAT_RECOVERY_DELAY_SEC:-15}"
  local attempts="${ETHERCAT_RECOVERY_ATTEMPTS:-2}"
  local interval="${ETHERCAT_RECOVERY_INTERVAL_SEC:-1}"
  local attempt position positions
  sleep "${delay}"

  if ! kill -0 "${launch_pid}" >/dev/null 2>&1; then
    return 0
  fi

  for ((attempt = 1; attempt <= attempts; attempt++)); do
    positions="$(ethercat slaves 2>/dev/null | awk '/ERROR/ {print $1}' || true)"
    if [[ -z "${positions}" ]]; then
      return 0
    fi

    for position in ${positions}; do
      log "recovering EtherCAT slave ${position} after launch (attempt ${attempt}/${attempts})"
      ethercat states -p "${position}" INIT || true
      sleep "${interval}"
      ethercat states -p "${position}" PREOP || true
      sleep "${interval}"
      ethercat states -p "${position}" OP || true
      sleep "${interval}"
    done
  done

  if ethercat slaves 2>/dev/null | grep -q 'ERROR'; then
    log "EtherCAT error flag remains after post-launch recovery"
  fi
}

cd "${WORKSPACE}"
CONFIG_FILE="${MOTOR_CONFIG_FILE:-/home/joonho_test/ros2_ws/config/active_motor_config.yaml}"
SELECTED_CONFIG_PATH_FILE="/home/joonho_test/ros2_ws/config/selected_motor_config_path.txt"
if [[ -z "${MOTOR_CONFIG_FILE:-}" && -f "${SELECTED_CONFIG_PATH_FILE}" ]]; then
  selected_config="$(head -n 1 "${SELECTED_CONFIG_PATH_FILE}" | tr -d '\r\n')"
  if [[ -f "${selected_config}" ]]; then
    CONFIG_FILE="${selected_config}"
  else
    log "selected motor config not found: ${selected_config}; using ${CONFIG_FILE}"
  fi
fi

export CONFIG_FILE
log "starting motion_monitor.launch.py with config_file=${CONFIG_FILE}"
sg dialout -c 'bash -lc '"'"'source /home/joonho_test/ros2_ws/install/setup.bash && ros2 launch motion_state_monitor motion_monitor.launch.py config_file:="$CONFIG_FILE" start_motor_manager:=true'"'" &
launch_pid="$!"
log "starting MIDI control monitor (robot motor output disabled)"
bash -lc 'source /home/joonho_test/ros2_ws/install/setup.bash && ros2 launch midi_control midi_control.launch.py' &
midi_launch_pid="$!"
recover_ethercat_errors_after_launch &
recovery_pid="$!"

wait "${launch_pid}"
launch_status="$?"
kill "${midi_launch_pid}" 2>/dev/null || true
wait "${midi_launch_pid}" 2>/dev/null || true
wait "${recovery_pid}" 2>/dev/null || true
exit "${launch_status}"
