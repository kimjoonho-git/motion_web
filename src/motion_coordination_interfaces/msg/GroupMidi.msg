# 장치가 꽂힌 PC 에서 대상 PC 로 나르는 원시 MIDI · §6-94
#
# 장치 상태를 **그대로** 나른다 · 받는 PC 의 `midi_control` 이 보기에 USB 를
# 직접 꽂은 것과 구별되지 않아야 한다 · 그래야 SELECT·Pickup·한계값·중재기가
# 전부 기존대로 돈다.
#
# `midi_msgs/Midi` 를 그대로 담는다 · 모양을 베껴 적으면 장치 규격이 바뀔 때
# 둘이 갈린다.
#
# 200Hz 로 흐른다 · 깊이 1 · 놓친 것을 다시 보내느니 다음 것을 보낸다.

string group_id

# 장치가 꽂힌 PC
string source_pc_id

# 이 MIDI 를 쓸 PC · 한 번에 한 대다
string target_pc_id

# 늦게 도착한 옛 값을 버리는 데 쓴다
uint64 sequence
builtin_interfaces/Time sent_at

midi_msgs/Midi midi
