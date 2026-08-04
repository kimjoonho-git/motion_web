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

inline bool should_rearm_fader_release_hold(
  bool hold_enabled,
  bool was_touched,
  bool is_touched,
  bool changed_while_touched)
{
  return hold_enabled &&
         was_touched &&
         !is_touched &&
         changed_while_touched;
}

}  // namespace midi_input_bridge
