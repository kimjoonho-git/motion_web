# 대상 PC 에서 장치가 꽂힌 PC 로 돌아가는 페이더 명령 · §6-94
#
# "페이더를 몇 번 위치로 움직여라" · Pickup 과 SELECT OFF 복귀에 쓴다.
# 물리 페이더는 한 대뿐이므로 **대상 PC 하나만** 이것을 보낸다.

string group_id

# 이 MIDI 를 쓰고 있는 PC
string source_pc_id

# 장치가 꽂힌 PC
string target_pc_id

uint64 sequence
builtin_interfaces/Time sent_at

# `midi_control` 이 내보내는 그대로 · JSON
string payload
