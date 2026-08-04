#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE="${MOTION_WORKSPACE:-$(cd "${SCRIPT_DIR}/../../../.." && pwd)}"
CONTROL_TEMPLATE="${SCRIPT_DIR}/motion-control.service.in"
MOTOR_TEMPLATE="${SCRIPT_DIR}/motion-motor.service.in"
COORDINATION_TEMPLATE="${WORKSPACE}/src/motion_control_studio/motion_control/motion_coordination/deploy/motion-coordination.service.in"
COORDINATION_SERVICE_EXECUTABLE="${WORKSPACE}/install/motion_coordination/lib/motion_coordination/motion_coordination_node"
SERVICE_EXECUTABLE="${WORKSPACE}/install/motion_web_bridge/lib/motion_web_bridge/motion_control_service"
MOTOR_SERVICE_EXECUTABLE="${WORKSPACE}/install/motion_web_bridge/lib/motion_web_bridge/motion_motor_service"
SERVICE_RUNNER="${SCRIPT_DIR}/run_user_service.sh"
MOTOR_SERVICE_RUNNER="${SCRIPT_DIR}/run_motor_user_service.sh"
COORDINATION_SERVICE_RUNNER="${WORKSPACE}/src/motion_control_studio/motion_control/motion_coordination/deploy/run_coordination_user_service.sh"
USER_UNIT_DIR="${HOME}/.config/systemd/user"
CONTROL_UNIT_FILE="${USER_UNIT_DIR}/motion-control.service"
MOTOR_UNIT_FILE="${USER_UNIT_DIR}/motion-motor.service"
COORDINATION_UNIT_FILE="${USER_UNIT_DIR}/motion-coordination.service"
INSTALL_TMP=""
SERVICES_STOPPED=false
INSTALL_COMPLETE=false
CONTROL_WAS_ACTIVE=false
MOTOR_WAS_ACTIVE=false
COORDINATION_WAS_ACTIVE=false

cleanup_install_tmp() {
  if [[ -n "${INSTALL_TMP}" && -d "${INSTALL_TMP}" ]]; then
    rm -rf -- "${INSTALL_TMP}"
  fi
}

restore_previous_install_on_error() {
  local status=$?
  if [[ "${INSTALL_COMPLETE}" != true && "${SERVICES_STOPPED}" == true ]]; then
    if [[ -f "${INSTALL_TMP}/motion-control.service.previous" ]]; then
      cp -p "${INSTALL_TMP}/motion-control.service.previous" "${CONTROL_UNIT_FILE}" || true
    fi
    if [[ -f "${INSTALL_TMP}/motion-motor.service.previous" ]]; then
      cp -p "${INSTALL_TMP}/motion-motor.service.previous" "${MOTOR_UNIT_FILE}" || true
    fi
    if [[ -f "${INSTALL_TMP}/motion-coordination.service.previous" ]]; then
      cp -p "${INSTALL_TMP}/motion-coordination.service.previous" "${COORDINATION_UNIT_FILE}" || true
    fi
    systemctl --user daemon-reload 2>/dev/null || true
    if [[ "${MOTOR_WAS_ACTIVE}" == true ]]; then
      systemctl --user start motion-motor.service 2>/dev/null || true
    fi
    if [[ "${CONTROL_WAS_ACTIVE}" == true ]]; then
      systemctl --user start motion-control.service 2>/dev/null || true
    fi
    if [[ "${COORDINATION_WAS_ACTIVE}" == true ]]; then
      systemctl --user start motion-coordination.service 2>/dev/null || true
    fi
  fi
  cleanup_install_tmp
  exit "${status}"
}

trap restore_previous_install_on_error EXIT

if [[ ! -x "${SERVICE_EXECUTABLE}" ]]; then
  echo "설치 실행 파일이 없습니다: ${SERVICE_EXECUTABLE}" >&2
  echo "먼저 colcon build를 완료하세요." >&2
  exit 1
fi
if [[ ! -x "${MOTOR_SERVICE_EXECUTABLE}" ]]; then
  echo "Motor Manager 서비스 실행 파일이 없습니다: ${MOTOR_SERVICE_EXECUTABLE}" >&2
  echo "먼저 colcon build를 완료하세요." >&2
  exit 1
fi
if [[ ! -x "${COORDINATION_SERVICE_EXECUTABLE}" ]]; then
  echo "PC 연동 서비스 실행 파일이 없습니다: ${COORDINATION_SERVICE_EXECUTABLE}" >&2
  echo "먼저 colcon build를 완료하세요." >&2
  exit 1
fi
if [[ ! -f "${SERVICE_RUNNER}" ]]; then
  echo "서비스 환경 실행 스크립트가 없습니다: ${SERVICE_RUNNER}" >&2
  exit 1
fi
if [[ ! -f "${MOTOR_SERVICE_RUNNER}" ]]; then
  echo "Motor Manager 서비스 환경 실행 스크립트가 없습니다: ${MOTOR_SERVICE_RUNNER}" >&2
  exit 1
fi
if [[ ! -f "${COORDINATION_TEMPLATE}" || ! -f "${COORDINATION_SERVICE_RUNNER}" ]]; then
  echo "PC 연동 서비스 설치 파일을 찾을 수 없습니다." >&2
  exit 1
fi

# Resolve the durable runtime target before stopping an already healthy Motor
# Manager.  An upgrade with missing/invalid state must fail closed instead of
# turning a running motor service into an unrecoverable stopped service.
MOTOR_CONFIG="$("${MOTOR_SERVICE_EXECUTABLE}" --print-config)"
if systemctl --user is-active --quiet motion-motor.service && [[ -z "${MOTOR_CONFIG}" ]]; then
  echo "설치 중단 · 실행 중인 Motor Manager의 검증된 실행 설정을 확인할 수 없습니다." >&2
  echo "현재 프로젝트의 모터 설정을 다시 적용한 후 설치를 재시도하세요." >&2
  exit 78
fi

mkdir -p "${USER_UNIT_DIR}"
INSTALL_TMP="$(mktemp -d "${USER_UNIT_DIR}/.motion-install.XXXXXX")"
if [[ -f "${CONTROL_UNIT_FILE}" ]]; then
  cp -p "${CONTROL_UNIT_FILE}" "${INSTALL_TMP}/motion-control.service.previous"
fi
if [[ -f "${MOTOR_UNIT_FILE}" ]]; then
  cp -p "${MOTOR_UNIT_FILE}" "${INSTALL_TMP}/motion-motor.service.previous"
fi
if [[ -f "${COORDINATION_UNIT_FILE}" ]]; then
  cp -p "${COORDINATION_UNIT_FILE}" "${INSTALL_TMP}/motion-coordination.service.previous"
fi
CONTROL_WAS_ACTIVE="$(systemctl --user is-active --quiet motion-control.service && echo true || echo false)"
MOTOR_WAS_ACTIVE="$(systemctl --user is-active --quiet motion-motor.service && echo true || echo false)"
COORDINATION_WAS_ACTIVE="$(systemctl --user is-active --quiet motion-coordination.service && echo true || echo false)"

sed \
  -e "s|@WORKSPACE@|${WORKSPACE//&/\\&}|g" \
  -e "s|@SERVICE_EXECUTABLE@|${SERVICE_EXECUTABLE//&/\\&}|g" \
  -e "s|@SERVICE_RUNNER@|${SERVICE_RUNNER//&/\\&}|g" \
  "${CONTROL_TEMPLATE}" > "${INSTALL_TMP}/motion-control.service"
sed \
  -e "s|@WORKSPACE@|${WORKSPACE//&/\\&}|g" \
  -e "s|@MOTOR_SERVICE_RUNNER@|${MOTOR_SERVICE_RUNNER//&/\\&}|g" \
  "${MOTOR_TEMPLATE}" > "${INSTALL_TMP}/motion-motor.service"
sed \
  -e "s|@WORKSPACE@|${WORKSPACE//&/\\&}|g" \
  -e "s|@COORDINATION_SERVICE_RUNNER@|${COORDINATION_SERVICE_RUNNER//&/\\&}|g" \
  "${COORDINATION_TEMPLATE}" > "${INSTALL_TMP}/motion-coordination.service"
chmod 0644 \
  "${INSTALL_TMP}/motion-control.service" \
  "${INSTALL_TMP}/motion-motor.service" \
  "${INSTALL_TMP}/motion-coordination.service"

# Stop every existing state, including activating/auto-restart loops, so the
# newly rendered unit is guaranteed to start with the updated ExecStart.
SERVICES_STOPPED=true
systemctl --user stop motion-control.service 2>/dev/null || true
systemctl --user stop motion-motor.service 2>/dev/null || true
systemctl --user stop motion-coordination.service 2>/dev/null || true

mv "${INSTALL_TMP}/motion-control.service" "${CONTROL_UNIT_FILE}"
mv "${INSTALL_TMP}/motion-motor.service" "${MOTOR_UNIT_FILE}"
mv "${INSTALL_TMP}/motion-coordination.service" "${COORDINATION_UNIT_FILE}"

systemctl --user daemon-reload
systemctl --user enable motion-motor.service motion-control.service motion-coordination.service
if [[ -n "${MOTOR_CONFIG}" ]]; then
  systemctl --user start motion-motor.service
else
  echo "검증된 모터 실행 설정 없음 · motion-motor.service 시작 보류"
fi
systemctl --user start motion-control.service
systemctl --user start motion-coordination.service
INSTALL_COMPLETE=true
cleanup_install_tmp
trap - EXIT

echo "motion-motor.service, motion-control.service, motion-coordination.service 설치·자동실행 등록 완료"
echo "웹 주소: http://localhost:8000"
echo "상위 프로그램 로그: journalctl --user -u motion-control.service"
echo "Motor Manager 로그: journalctl --user -u motion-motor.service"
echo
echo "로그인 전에도 부팅 직후 실행하려면 최초 설치 중 한 번만 다음 명령을 실행하세요:"
echo "  sudo loginctl enable-linger $(id -un)"
