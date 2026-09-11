#!/usr/bin/env bash
# 고친 것을 검사하고 띄운다 · §6-92
#
# 손으로 치던 다섯 단계를 한 줄로 모았다 · 어느 하나라도 실패하면 그 자리에서
# 멈춘다 · 검사에 실패한 것을 모르고 재시작해 버리는 일이 없어야 한다.
#
#   scripts/ship.sh                    빌드까지 전부
#   scripts/ship.sh motion_studio      그 패키지만 빌드 (빠르다)
#   scripts/ship.sh --check            검사만 · 빌드도 재시작도 안 한다
#
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE="${MOTION_WORKSPACE:-$(cd "${SCRIPT_DIR}/.." && pwd)}"
UI_DIR="${WORKSPACE}/src/motion_web/web_ui"
SERVICES=(motion-control.service)

cd "${WORKSPACE}"

CHECK_ONLY=0
PACKAGES=()
for arg in "$@"; do
  case "${arg}" in
    --check) CHECK_ONLY=1 ;;
    -*) echo "모르는 옵션: ${arg}" >&2; exit 2 ;;
    *) PACKAGES+=("${arg}") ;;
  esac
done

step() { printf '\n\033[1m[%s] %s\033[0m\n' "$1" "$2"; }
fail() { printf '\n\033[31m멈춤 · %s\033[0m\n' "$1" >&2; exit 1; }

# 빠른 것부터 돌린다 · 화면 검사는 1초대, 파이썬은 12초대다
#
# 순서가 반대면 pytest 가 화면 검사를 감싸 먼저 실패해서, 화면이 깨진 것을
# "파이썬 검사 실패" 라고 알린다 · 어디가 깨졌는지 알 수 없다.
step 1/5 "화면 검사"
( cd "${UI_DIR}" && node --test test/ ) || fail "화면 검사 실패 · 고치고 다시 실행하세요"

step 2/5 "파이썬 검사"
python3 -m pytest src/ -q || fail "파이썬 검사 실패 · 고치고 다시 실행하세요"

if [[ ${CHECK_ONLY} -eq 1 ]]; then
  printf '\n검사만 마쳤습니다 · 빌드와 재시작은 하지 않았습니다\n'
  exit 0
fi

step 3/5 "빌드"
set +u
source /opt/ros/humble/setup.bash
set -u
if [[ ${#PACKAGES[@]} -gt 0 ]]; then
  colcon build --symlink-install --packages-select "${PACKAGES[@]}" || fail "빌드 실패"
else
  colcon build --symlink-install || fail "빌드 실패"
fi

step 4/5 "서비스 재시작"
systemctl --user restart "${SERVICES[@]}"
for _ in $(seq 30); do
  systemctl --user is-active --quiet "${SERVICES[@]}" && break
  sleep 1
done
systemctl --user is-active "${SERVICES[@]}" || fail "서비스가 올라오지 않았습니다"

step 5/5 "화면 회귀 검사"
# 화면이 실제로 그려지는지 · 서비스가 뜬 뒤라야 뜻이 있다
for _ in $(seq 20); do
  curl -sf -o /dev/null --max-time 2 http://127.0.0.1:8000/ && break
  sleep 1
done
( cd "${UI_DIR}" && node tools/ui_smoke.mjs ) || fail "화면 회귀 검사 실패"

printf '\n\033[32m올렸습니다\033[0m · 브라우저 새로고침이면 새 화면이 뜹니다\n'
