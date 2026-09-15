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
# 규격은 이것 하나다 · 다만 **토픽은 둘**이다 · 200Hz 원시 MIDI 는 최선형으로
# 흘리고(놓친 것보다 다음 것이 낫다), 나머지는 확실히 전달해야 한다 ·
# 한 토픽에 묶으면 둘 중 하나는 반드시 손해를 본다.

string group_id

# 보내는 PC
string source_pc_id

# 받을 PC
string target_pc_id

uint64 sequence
builtin_interfaces/Time sent_at

# 통로 이름 · `midi` · `input_state` · `connection_state` · `feedback` ·
# `connection_command`
string channel

# 글로 오가던 통로의 내용 그대로 · JSON · `midi` 일 때는 비어 있다
string payload

# 장치 상태 그대로 · `midi` 일 때만 채운다 · 베껴 적으면 규격이 갈린다
midi_msgs/Midi midi
