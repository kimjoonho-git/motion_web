#include <gtest/gtest.h>

#include "midi_input_bridge/device_state_publish.hpp"

/// 장치가 없는 PC 가 0 을 200Hz 로 쏘면, 다른 PC 에서 중계받은 진짜 값과
/// 번갈아 들어가 모터가 떤다 · 연동에서 실제로 그랬다.
TEST(DeviceStatePublish, PublishesOnlyWhileTheDeviceIsThere)
{
  EXPECT_TRUE(midi_input_bridge::should_publish_device_state(true));
  EXPECT_FALSE(midi_input_bridge::should_publish_device_state(false));
}
