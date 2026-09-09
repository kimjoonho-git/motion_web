#!/usr/bin/env bash
# Fast DDS 공유메모리 세그먼트 점검 · §6-14 재발 판별용
#
# 노드가 죽으면 /dev/shm 의 fastrtps 세그먼트가 남는다. 다음 참가자가 같은
# 포트 번호를 잡으면 낡은 세그먼트에 붙게 되고, 그때 **발신은 되는데 수신만
# 죽는** 상태가 나올 수 있다. 그 후보를 눈으로 확인하는 도구다.
#
# 읽기만 한다. 지우지 않는다 · 살아있는 노드가 쓰는 것을 지우면 통신이 끊긴다.
set -Eeuo pipefail

SHOW_ALL=0
[[ "${1:-}" == "--all" ]] && SHOW_ALL=1

live_pids=$(pgrep -f "install/.*/lib/" 2>/dev/null || true)

is_referenced() {
  local seg="$1" pid
  for pid in $live_pids; do
    if ls -l "/proc/$pid/map_files" 2>/dev/null | grep -q "/dev/shm/${seg}\b"; then
      return 0
    fi
  done
  return 1
}

total=0; orphan=0
printf '%-28s %-17s %10s  %s\n' 세그먼트 시각 크기 상태
printf -- '---------------------------------------------------------------------\n'
for path in /dev/shm/fastrtps_*; do
  [[ -e "$path" ]] || continue
  seg=$(basename "$path")
  [[ "$seg" == *_el ]] && continue
  total=$((total + 1))
  when=$(stat -c %y "$path" | cut -c1-16)
  size=$(stat -c %s "$path")
  if is_referenced "$seg"; then
    state='사용 중'
    [[ $SHOW_ALL -eq 1 ]] || continue
  else
    state='고아 · 참조 프로세스 없음'
    orphan=$((orphan + 1))
  fi
  printf '%-28s %-17s %10s  %s\n' "$seg" "$when" "$size" "$state"
done

printf -- '---------------------------------------------------------------------\n'
echo "세그먼트 ${total}개 · 고아 ${orphan}개 · 실행 중 ROS 프로세스 $(echo "$live_pids" | grep -c . || true)개"
echo
if [[ $orphan -gt 0 ]]; then
  cat <<'MSG'
고아가 있다고 곧바로 결함은 아니다. 아래 증상과 함께일 때만 의심한다.

  · safety_status 는 2 Hz 로 나오는데 어떤 구독도 응답하지 않는다
  · POST /api/execution-context/apply 가 waiting_motor_runtime 으로 실패한다

정리는 **모든 ROS 서비스를 내린 뒤**에만 한다.

  systemctl --user stop motion-control motion-coordination motion-motor
  rm -f /dev/shm/fastrtps_*
  systemctl --user start motion-motor motion-coordination motion-control
MSG
fi
