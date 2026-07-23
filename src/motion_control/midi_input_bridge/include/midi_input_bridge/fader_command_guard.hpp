#pragma once

#include <cstdint>

namespace midi_input_bridge
{

inline bool should_accept_fader_command(
  std::uint64_t expected_input_generation,
  std::uint64_t current_input_generation,
  bool physically_touched,
  bool physically_moving)
{
  return expected_input_generation == current_input_generation &&
         !physically_touched &&
         !physically_moving;
}

}  // namespace midi_input_bridge
