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
  "install/motion_web_bridge/lib/motion_web_bridge/motion_mapping_manager"
  "install/motion_web_bridge/lib/motion_web_bridge/motion_run_manager"
  "install/motion_web_bridge/lib/motion_web_bridge/motion_web_bridge"
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

request_ethercat_op_recovery() {
  if ! command -v ethercat >/dev/null 2>&1; then
    return 0
  fi

  local attempts="${ETHERCAT_OP_RECOVERY_ATTEMPTS:-6}"
  local interval="${ETHERCAT_OP_RECOVERY_INTERVAL_SEC:-0.5}"
  local attempt
  for ((attempt = 1; attempt <= attempts; attempt++)); do
    sleep "${interval}"
    if ! ethercat slaves >/dev/null 2>&1; then
      continue
    fi
    log "requesting EtherCAT OP state (attempt ${attempt}/${attempts})"
    ethercat states OP || true
  done
}

cd "${WORKSPACE}"
log "starting motion_monitor.launch.py"
sg dialout -c 'bash -lc "source /home/joonho_test/ros2_ws/install/setup.bash && ros2 launch motion_state_monitor motion_monitor.launch.py start_motor_manager:=true"' &
launch_pid="$!"
request_ethercat_op_recovery &
recovery_pid="$!"

wait "${launch_pid}"
launch_status="$?"
wait "${recovery_pid}" 2>/dev/null || true
exit "${launch_status}"
