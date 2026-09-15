# 장치와 PC 사이의 통로 하나를 **그대로** 나른다 · §6-94
#
# USB 선이 나르던 것을 그대로 나르는 것이 전부다 · 중간에서 판단하지 않는다 ·
# 무엇이 연결됐는지, 지금 눌렸는지, 녹화해도 되는지는 **받는 PC 가 정한다**.
#
# 통로 이름만 붙이고 내용은 손대지 않는다 ·
#
#   장치 → PC   input_state · connection_state
#   PC → 장치   feedback · connection_command
#
# 원시 MIDI(200Hz)만 `GroupMidi` 로 따로 간다 · 그것만 형이 다르고 양이 많다.

string group_id

# 보내는 PC
string source_pc_id

# 받을 PC
string target_pc_id

uint64 sequence
builtin_interfaces/Time sent_at

# 통로 이름 · `input_state` · `connection_state` · `feedback` · `connection_command`
string channel

# 그 통로가 나르던 내용 그대로 · JSON
string payload
