#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE="${MOTION_WORKSPACE:-$(cd "${SCRIPT_DIR}/../../../.." && pwd)}"
TEMPLATE="${SCRIPT_DIR}/motion-control.service.in"
SERVICE_EXECUTABLE="${WORKSPACE}/install/motion_web_bridge/lib/motion_web_bridge/motion_control_service"
SERVICE_RUNNER="${SCRIPT_DIR}/run_user_service.sh"
USER_UNIT_DIR="${HOME}/.config/systemd/user"
UNIT_FILE="${USER_UNIT_DIR}/motion-control.service"

if [[ ! -x "${SERVICE_EXECUTABLE}" ]]; then
  echo "설치 실행 파일이 없습니다: ${SERVICE_EXECUTABLE}" >&2
  echo "먼저 colcon build를 완료하세요." >&2
  exit 1
fi
if [[ ! -f "${SERVICE_RUNNER}" ]]; then
  echo "서비스 환경 실행 스크립트가 없습니다: ${SERVICE_RUNNER}" >&2
  exit 1
fi

# Stop every existing state, including activating/auto-restart loops, so the
# newly rendered unit is guaranteed to start with the updated ExecStart.
systemctl --user stop motion-control.service 2>/dev/null || true

mkdir -p "${USER_UNIT_DIR}"
sed \
  -e "s|@WORKSPACE@|${WORKSPACE//&/\\&}|g" \
  -e "s|@SERVICE_EXECUTABLE@|${SERVICE_EXECUTABLE//&/\\&}|g" \
  -e "s|@SERVICE_RUNNER@|${SERVICE_RUNNER//&/\\&}|g" \
  "${TEMPLATE}" > "${UNIT_FILE}"

systemctl --user daemon-reload
systemctl --user enable --now motion-control.service

echo "motion-control.service 설치 및 자동실행 등록 완료"
echo "웹 주소: http://localhost:8000"
echo "로그 확인: journalctl --user -u motion-control.service"
echo
echo "로그인 전에도 부팅 직후 실행하려면 최초 설치 중 한 번만 다음 명령을 실행하세요:"
echo "  sudo loginctl enable-linger $(id -un)"
