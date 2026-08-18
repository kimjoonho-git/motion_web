#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "update.sh는 호환용 별칭입니다."
echo "앞으로는 같은 명령 하나만 사용하세요:"
echo "  bash src/motion_web/install.sh"

exec bash "${SCRIPT_DIR}/install.sh" "$@"
