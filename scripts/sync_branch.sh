#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE="${MOTION_WORKSPACE:-$(cd "${SCRIPT_DIR}/.." && pwd)}"
BRANCH="${MOTION_WEB_BRANCH:-fix/coordination-safety}"

cd "${WORKSPACE}"

echo "[1/3] motion_web pull: ${BRANCH}"
git fetch origin
git checkout "${BRANCH}"
git pull origin "${BRANCH}"

echo "[2/3] submodule update"
git submodule update --init --recursive

echo "[3/3] 빌드·재시작"
bash "${SCRIPT_DIR}/build_and_restart.sh"

echo "동기화 완료 · 브랜치 ${BRANCH}"
