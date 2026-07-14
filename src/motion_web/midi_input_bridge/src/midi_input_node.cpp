#include <algorithm>
#include <array>
#include <cctype>
#include <chrono>
#include <cstdint>
#include <functional>
#include <iostream>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <vector>

#include <rtmidi/RtMidi.h>

#include <midi_msgs/msg/midi.hpp>
#include <rclcpp/rclcpp.hpp>

namespace
{

constexpr std::size_t kChannelCount = 8;
constexpr int32_t kFaderMax = 16383;
constexpr uint8_t kDialCcStart = 16;
constexpr uint8_t kTouchNoteStart = 104;

std::string upper(std::string value)
{
  std::transform(
    value.begin(), value.end(), value.begin(),
    [](unsigned char ch) {return static_cast<char>(std::toupper(ch));});
  return value;
}

bool is_xtouch_port(const std::string & name)
{
  const std::string text = upper(name);
  return text.find("X-TOUCH") != std::string::npos ||
         text.find("XTOUCH") != std::string::npos ||
         text.find("BEHRINGER") != std::string::npos;
}

template<typename T>
std::vector<T> vector_of(const std::array<T, kChannelCount> & values)
{
  return std::vector<T>(values.begin(), values.end());
}

}  // namespace

class MidiInputNode : public rclcpp::Node
{
public:
  using Midi = midi_msgs::msg::Midi;

  MidiInputNode()
  : Node("midi_input_node")
  {
    const auto topic = declare_parameter<std::string>("midi_topic", "/xtouch/midi");
    hold_fader_on_release_ = declare_parameter<bool>("hold_fader_on_release", true);
    movement_release_delay_ = std::chrono::milliseconds(std::max<int64_t>(
      50, declare_parameter<int64_t>("movement_release_delay_ms", 300)));
    const auto publish_period_ms = std::max<int64_t>(
      1, declare_parameter<int64_t>("publish_period_ms", 5));
    publisher_ = create_publisher<Midi>(
      topic, rclcpp::QoS(rclcpp::KeepLast(1)).best_effort());

    midi_input_ = std::make_unique<RtMidiIn>();
    midi_input_->ignoreTypes(true, true, true);
    midi_input_->setCallback(&MidiInputNode::midi_callback, this);
    open_input_port();
    if (hold_fader_on_release_) {
      midi_output_ = std::make_unique<RtMidiOut>();
      open_output_port();
    }

    timer_ = create_wall_timer(
      std::chrono::milliseconds(publish_period_ms),
      std::bind(&MidiInputNode::publish_state, this));
    RCLCPP_INFO(
      get_logger(),
      "MIDI input ready: topic=%s, fader_feedback=%s, robot_motor_output=disabled",
      topic.c_str(), hold_fader_on_release_ ? "touch_release_hold" : "disabled");
  }

  ~MidiInputNode() override
  {
    if (midi_input_) {
      midi_input_->cancelCallback();
      if (midi_input_->isPortOpen()) {
        midi_input_->closePort();
      }
    }
    if (midi_output_ && midi_output_->isPortOpen()) {
      midi_output_->closePort();
    }
  }

private:
  void open_input_port()
  {
    const unsigned int count = midi_input_->getPortCount();
    RCLCPP_INFO(get_logger(), "Scanning %u MIDI input port(s)...", count);
    for (unsigned int index = 0; index < count; ++index) {
      const std::string name = midi_input_->getPortName(index);
      RCLCPP_INFO(get_logger(), "  [input %u] %s", index, name.c_str());
      if (!is_xtouch_port(name)) {
        continue;
      }
      midi_input_->openPort(index);
      RCLCPP_INFO(get_logger(), "Connected MIDI input: '%s'", name.c_str());
      return;
    }
    throw std::runtime_error("X-Touch MIDI input port not found");
  }

  void open_output_port()
  {
    const unsigned int count = midi_output_->getPortCount();
    for (unsigned int index = 0; index < count; ++index) {
      const std::string name = midi_output_->getPortName(index);
      if (!is_xtouch_port(name)) {
        continue;
      }
      midi_output_->openPort(index);
      RCLCPP_INFO(
        get_logger(), "Connected MIDI output for touch-release fader hold: '%s'",
        name.c_str());
      return;
    }
    throw std::runtime_error("X-Touch MIDI output port not found for fader hold");
  }

  static void midi_callback(
    double /*timestamp*/, std::vector<unsigned char> * bytes, void * user_data)
  {
    if (bytes != nullptr && user_data != nullptr) {
      static_cast<MidiInputNode *>(user_data)->handle_midi(*bytes);
    }
  }

  void handle_midi(const std::vector<unsigned char> & bytes)
  {
    if (bytes.size() < 3) {
      return;
    }
    const uint8_t status = bytes[0] & 0xF0;
    const uint8_t channel = bytes[0] & 0x0F;
    const uint8_t data1 = bytes[1];
    const uint8_t data2 = bytes[2];
    std::lock_guard<std::mutex> lock(mutex_);
    if (status == 0xE0 && channel < kChannelCount) {
      fader_[channel] = std::clamp<int32_t>((data2 << 7) | data1, 0, kFaderMax);
      seen_[channel] = true;
      changed_while_touched_[channel] = true;
      movement_active_[channel] = true;
      movement_deadline_[channel] =
        std::chrono::steady_clock::now() + movement_release_delay_;
      // Do not feed a position back while the user is moving the fader.
      // Even an immediate echo can make the motor resist the hand. The final
      // position is sent once after the touch-release event below.
    } else if (
      status == 0xB0 && data1 >= kDialCcStart &&
      data1 < kDialCcStart + kChannelCount)
    {
      dial_[data1 - kDialCcStart] = data2;
    } else if (
      (status == 0x90 || status == 0x80) && data1 >= kTouchNoteStart &&
      data1 < kTouchNoteStart + kChannelCount)
    {
      const std::size_t touch_channel = data1 - kTouchNoteStart;
      const bool was_touched = touch_[touch_channel];
      touch_[touch_channel] = status == 0x90 && data2 > 0;
      seen_[touch_channel] = seen_[touch_channel] || touch_[touch_channel];
      if (hold_fader_on_release_ && was_touched && !touch_[touch_channel] &&
        changed_while_touched_[touch_channel])
      {
        // Do not trust a single touch-OFF event. Capacitive touch can flicker
        // while the hand is still moving, which would let the motor grab the
        // fader. The movement inactivity timer performs the final hold.
        movement_deadline_[touch_channel] =
          std::chrono::steady_clock::now() + movement_release_delay_;
      }
    }
  }

  void send_fader_position(std::size_t channel, int32_t value)
  {
    if (!midi_output_ || !midi_output_->isPortOpen()) {
      return;
    }
    value = std::clamp<int32_t>(value, 0, kFaderMax);
    std::vector<unsigned char> bytes = {
      static_cast<unsigned char>(0xE0 | (channel & 0x0F)),
      static_cast<unsigned char>(value & 0x7F),
      static_cast<unsigned char>((value >> 7) & 0x7F),
    };
    try {
      midi_output_->sendMessage(&bytes);
    } catch (const RtMidiError & error) {
      RCLCPP_WARN(get_logger(), "Failed to hold MIDI fader: %s", error.what());
    }
  }

  void publish_state()
  {
    Midi msg;
    std::array<int32_t, kChannelCount> hold_values{};
    std::array<bool, kChannelCount> send_hold{};
    {
      std::lock_guard<std::mutex> lock(mutex_);
      const auto now = std::chrono::steady_clock::now();
      std::array<bool, kChannelCount> input_active{};
      for (std::size_t channel = 0; channel < kChannelCount; ++channel) {
        if (movement_active_[channel] && now >= movement_deadline_[channel]) {
          movement_active_[channel] = false;
          // Some X-Touch modes do not report the expected touch note. In
          // that case, a short end-of-movement timeout acts as touch release.
          if (hold_fader_on_release_ && !touch_[channel] &&
            changed_while_touched_[channel])
          {
            send_hold[channel] = true;
            hold_values[channel] = fader_[channel];
            changed_while_touched_[channel] = false;
          }
        }
        input_active[channel] = touch_[channel] || movement_active_[channel];
      }
      msg.channel = vector_of(fader_);
      // Consumers use touch as an input-valid gate. Physical touch is
      // preferred; recent physical fader movement is the fallback.
      msg.touch = vector_of(input_active);
      msg.dial = vector_of(dial_);
      // btn0 is used only as an input-seen marker by the read-only monitor.
      msg.btn0 = vector_of(seen_);
      msg.btn1.assign(kChannelCount, false);
      msg.btn2.assign(kChannelCount, false);
      msg.btn3.assign(kChannelCount, false);
      for (std::size_t channel = 0; channel < kChannelCount; ++channel) {
        if (hold_pending_[channel] && !touch_[channel]) {
          send_hold[channel] = true;
          hold_values[channel] = fader_[channel];
          hold_pending_[channel] = false;
        }
      }
    }
    publisher_->publish(msg);
    for (std::size_t channel = 0; channel < kChannelCount; ++channel) {
      if (send_hold[channel]) {
        send_fader_position(channel, hold_values[channel]);
      }
    }
  }

  std::unique_ptr<RtMidiIn> midi_input_;
  std::unique_ptr<RtMidiOut> midi_output_;
  rclcpp::Publisher<Midi>::SharedPtr publisher_;
  rclcpp::TimerBase::SharedPtr timer_;
  std::mutex mutex_;
  std::array<int32_t, kChannelCount> fader_{};
  std::array<int32_t, kChannelCount> dial_{};
  std::array<bool, kChannelCount> touch_{};
  std::array<bool, kChannelCount> seen_{};
  std::array<bool, kChannelCount> changed_while_touched_{};
  std::array<bool, kChannelCount> hold_pending_{};
  std::array<bool, kChannelCount> movement_active_{};
  std::array<std::chrono::steady_clock::time_point, kChannelCount>
    movement_deadline_{};
  std::chrono::milliseconds movement_release_delay_{300};
  bool hold_fader_on_release_{true};
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  try {
    rclcpp::spin(std::make_shared<MidiInputNode>());
  } catch (const std::exception & error) {
    std::cerr << error.what() << std::endl;
    if (rclcpp::ok()) {
      rclcpp::shutdown();
    }
    return 1;
  }
  if (rclcpp::ok()) {
    rclcpp::shutdown();
  }
  return 0;
}
