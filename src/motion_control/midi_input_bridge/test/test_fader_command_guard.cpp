#include <gtest/gtest.h>

#include "midi_input_bridge/fader_command_guard.hpp"

TEST(FaderCommandGuard, AcceptsCurrentIdleCommand)
{
  EXPECT_TRUE(midi_input_bridge::should_accept_fader_command(7, 7, false, false));
}

TEST(FaderCommandGuard, RejectsCommandCreatedBeforeNewPhysicalInput)
{
  EXPECT_FALSE(midi_input_bridge::should_accept_fader_command(7, 8, false, false));
}

TEST(FaderCommandGuard, RejectsCommandWhileHandOwnsFader)
{
  EXPECT_FALSE(midi_input_bridge::should_accept_fader_command(8, 8, true, false));
  EXPECT_FALSE(midi_input_bridge::should_accept_fader_command(8, 8, false, true));
}
