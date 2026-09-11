# 연동된 PC 로 보내는 실시간 모션값 · §6-93
#
# 모터 명령이 아니라 **모션값(각도)** 을 보낸다 · 받는 PC 가 자기 매핑과
# 한계값으로 자기 모터축에 옮긴다 · 그래야 PC 마다 기구가 달라도 된다.
#
# 20ms 마다 흐른다 · 끊기면 받는 쪽이 마지막 값을 유지한다.

string group_id
string pc_id

# 보낸 순서 · 늦게 도착한 오래된 값을 버리는 데 쓴다
uint64 sequence
builtin_interfaces/Time sent_at

# 같은 자리끼리 짝이다 · 길이가 다르면 통째로 버린다
string[] motion_ids
float64[] values_deg

# 보내는 쪽이 지금 값을 내고 있는가 · 거짓이면 받는 쪽은 마지막 값을 유지한다
bool active
