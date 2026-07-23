#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE="${MOTION_WORKSPACE:-$(cd "${SCRIPT_DIR}/../../../.." && pwd)}"
CONTROL_TEMPLATE="${SCRIPT_DIR}/motion-control.service.in"
MOTOR_TEMPLATE="${SCRIPT_DIR}/motion-motor.service.in"
SERVICE_EXECUTABLE="${WORKSPACE}/install/motion_web_bridge/lib/motion_web_bridge/motion_control_service"
MOTOR_SERVICE_EXECUTABLE="${WORKSPACE}/install/motion_web_bridge/lib/motion_web_bridge/motion_motor_service"
SERVICE_RUNNER="${SCRIPT_DIR}/run_user_service.sh"
MOTOR_SERVICE_RUNNER="${SCRIPT_DIR}/run_motor_user_service.sh"
USER_UNIT_DIR="${HOME}/.config/systemd/user"
CONTROL_UNIT_FILE="${USER_UNIT_DIR}/motion-control.service"
MOTOR_UNIT_FILE="${USER_UNIT_DIR}/motion-motor.service"

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
if [[ ! -f "${SERVICE_RUNNER}" ]]; then
  echo "서비스 환경 실행 스크립트가 없습니다: ${SERVICE_RUNNER}" >&2
  exit 1
fi
if [[ ! -f "${MOTOR_SERVICE_RUNNER}" ]]; then
  echo "Motor Manager 서비스 환경 실행 스크립트가 없습니다: ${MOTOR_SERVICE_RUNNER}" >&2
  exit 1
fi

# Stop every existing state, including activating/auto-restart loops, so the
# newly rendered unit is guaranteed to start with the updated ExecStart.
systemctl --user stop motion-control.service 2>/dev/null || true
systemctl --user stop motion-motor.service 2>/dev/null || true

mkdir -p "${USER_UNIT_DIR}"
sed \
  -e "s|@WORKSPACE@|${WORKSPACE//&/\\&}|g" \
  -e "s|@SERVICE_EXECUTABLE@|${SERVICE_EXECUTABLE//&/\\&}|g" \
  -e "s|@SERVICE_RUNNER@|${SERVICE_RUNNER//&/\\&}|g" \
  "${CONTROL_TEMPLATE}" > "${CONTROL_UNIT_FILE}"
sed \
  -e "s|@WORKSPACE@|${WORKSPACE//&/\\&}|g" \
  -e "s|@MOTOR_SERVICE_RUNNER@|${MOTOR_SERVICE_RUNNER//&/\\&}|g" \
  "${MOTOR_TEMPLATE}" > "${MOTOR_UNIT_FILE}"

systemctl --user daemon-reload
systemctl --user enable motion-motor.service motion-control.service
systemctl --user start motion-motor.service
systemctl --user start motion-control.service

echo "motion-motor.service 및 motion-control.service 설치·자동실행 등록 완료"
echo "웹 주소: http://localhost:8000"
echo "상위 프로그램 로그: journalctl --user -u motion-control.service"
echo "Motor Manager 로그: journalctl --user -u motion-motor.service"
echo
echo "로그인 전에도 부팅 직후 실행하려면 최초 설치 중 한 번만 다음 명령을 실행하세요:"
echo "  sudo loginctl enable-linger $(id -un)"
