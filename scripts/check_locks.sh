#!/usr/bin/env bash
# 프로젝트 파일 락 확인 · docs/ARCHITECTURE_REVIEW.md §6-24
#
# 락 파일은 대상 파일 옆에 `.<이름>.lock`으로 생기는 0바이트 표식이다.
# 파일이 있다는 것은 "예전에 락을 쓴 적 있다"일 뿐 "지금 잠겨 있다"가 아니다.
# 지금 잠겨 있는지는 커널에게 물어야 한다.
#
# 사용
#   bash scripts/check_locks.sh            # 락 파일 목록 + 현재 점유 상태
#   bash scripts/check_locks.sh --held     # 지금 잡혀 있는 것만
#   bash scripts/check_locks.sh --stale    # 구 규약(점 없는) 잔재만

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE="${MOTION_WORKSPACE:-$(cd "${SCRIPT_DIR}/.." && pwd)}"
PROJECTS="${WORKSPACE}/motion_projects"
MODE="${1:-all}"

if [[ "${MODE}" == "--stale" ]]; then
  echo "■ 잔재 락 파일 · 대상 파일이 없는 락 · 지워도 된다"
  found=''
  while IFS= read -r lock; do
    base="$(basename "${lock}")"; base="${base%.lock}"
    target="$(dirname "${lock}")/${base#.}"
    [[ -e "${target}" ]] || found+="  $(stat -c %s "${lock}")바이트  ${lock}"$'\n'
  done < <(find "${PROJECTS}" -name '*.lock' 2>/dev/null)
  echo "${found:-  (없음)}"
  echo "지우기 전에 잠금 여부를 확인할 것 · bash scripts/check_locks.sh --held"
  exit 0
fi

echo "■ 락 파일 목록 · ${PROJECTS}"
mapfile -t locks < <(find "${PROJECTS}" -name '.*.lock' 2>/dev/null | sort)
if [[ ${#locks[@]} -eq 0 ]]; then
  echo "  (없음 · 아직 아무도 저장한 적이 없다는 뜻)"
  exit 0
fi

printf '%-10s %-8s %s\n' '상태' 'PID' '대상 파일'
for lock in "${locks[@]}"; do
  # `.<이름>.lock` → `<이름>` · 대상이 실제로 있을 때만 이름을 보여준다
  base="$(basename "${lock}")"
  base="${base%.lock}"
  target="$(dirname "${lock}")/${base#.}"
  [[ -e "${target}" ]] || target="${lock}  (대상 없음)"
  inode="$(stat -c %i "${lock}" 2>/dev/null || echo '')"
  holder=''
  if [[ -n "${inode}" ]]; then
    # /proc/locks 는 경로가 아니라 inode로 적힌다 · major:minor:inode
    holder="$(awk -v ino=":${inode} " '$0 ~ ino {print $5}' /proc/locks 2>/dev/null | head -1)"
  fi
  if [[ -n "${holder}" ]]; then
    state='잠김'
    cmd="$(ps -p "${holder}" -o comm= 2>/dev/null || echo '?')"
    printf '%-10s %-8s %s  ← %s\n' "${state}" "${holder}" "${target#${WORKSPACE}/}" "${cmd}"
  else
    [[ "${MODE}" == "--held" ]] && continue
    printf '%-10s %-8s %s\n' '해제됨' '-' "${target#${WORKSPACE}/}"
  fi
done

echo
echo "참고"
echo "  락 파일이 남아 있는 것은 정상이다 · 다음 저장 때 다시 쓴다"
echo "  '잠김'이 오래 지속되면 그 PID가 저장 중이거나 멈춰 있는 것이다"
