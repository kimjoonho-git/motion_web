#pragma once

namespace midi_input_bridge
{

/// 장치 상태를 내보낼 것인가.
///
/// **장치가 없으면 장치 상태도 없다.** 없는 값을 0 으로 채워 200Hz 로 흘리면,
/// 받는 쪽은 그것이 "사람이 페이더를 0 으로 내렸다" 인지 "장치가 없다" 인지
/// 구별할 수 없다.
///
/// 연동에서는 이것이 바로 드러났다 · 다른 PC 의 MIDI 를 중계받는 PC 에서
/// 제 입력 브리지가 같은 토픽에 0 을 함께 쏘아, 진짜 값과 0 이 번갈아 들어갔다.
///
/// 장치가 있는지는 `/xtouch/connection/state` 가 따로 알린다 · 값이 멈추는
/// 것으로 장치 없음을 알릴 필요가 없다.
inline bool should_publish_device_state(bool device_connected)
{
  return device_connected;
}

}  // namespace midi_input_bridge
