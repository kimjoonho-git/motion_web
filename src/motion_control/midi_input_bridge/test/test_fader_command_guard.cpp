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

TEST(FaderCommandGuard, RearmsFinalHoldWhenTouchEndsAfterMovementTimeout)
{
  EXPECT_TRUE(midi_input_bridge::should_rearm_fader_release_hold(
    true, true, false, true));
}

TEST(FaderCommandGuard, DoesNotRearmFinalHoldWithoutACompletedTouchRelease)
{
  EXPECT_FALSE(midi_input_bridge::should_rearm_fader_release_hold(
    false, true, false, true));
  EXPECT_FALSE(midi_input_bridge::should_rearm_fader_release_hold(
    true, false, false, true));
  EXPECT_FALSE(midi_input_bridge::should_rearm_fader_release_hold(
    true, true, true, true));
  EXPECT_FALSE(midi_input_bridge::should_rearm_fader_release_hold(
    true, true, false, false));
}
