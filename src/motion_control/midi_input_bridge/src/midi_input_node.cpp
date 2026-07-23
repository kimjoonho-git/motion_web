// X-Touch hardware transport only. Bank, mapping, and motion policy belong
// to the separate midi_control node.
#include <algorithm>
#include <array>
#include <cctype>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <functional>
#include <iostream>
#include <memory>
#include <mutex>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

#include <rtmidi/RtMidi.h>

#include <midi_msgs/msg/midi.hpp>
#include <rclcpp/rclcpp.hpp>
#include <std_msgs/msg/string.hpp>

#include "midi_input_bridge/fader_command_guard.hpp"

namespace
{

constexpr std::size_t kChannelCount = 8;
constexpr int32_t kFaderMax = 16383;
constexpr uint8_t kDialCcStart = 16;
constexpr uint8_t kDialLedCcStart = 48;
constexpr uint8_t kRecNoteStart = 0;
constexpr uint8_t kSelectNoteStart = 24;
constexpr uint8_t kTouchNoteStart = 104;
constexpr uint8_t kDisplayBottomRowOffset = 56;
constexpr std::size_t kDisplayCharsPerChannel = 7;

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
    last_select_led_.fill(-1);
    last_rec_led_.fill(-1);
    last_dial_led_.fill(-1);
    last_display_top_.fill("\x01");
    last_display_bottom_.fill("\x01");
    const auto topic = declare_parameter<std::string>("midi_topic", "/xtouch/midi");
    const auto feedback_topic =
      declare_parameter<std::string>("feedback_topic", "/xtouch/feedback");
    const auto input_state_topic =
      declare_parameter<std::string>("input_state_topic", "/xtouch/input_state");
    const auto connection_command_topic = declare_parameter<std::string>(
      "connection_command_topic", "/xtouch/connection/command");
    const auto connection_state_topic = declare_parameter<std::string>(
      "connection_state_topic", "/xtouch/connection/state");
    display_device_id_ = static_cast<uint8_t>(std::clamp<int64_t>(
      declare_parameter<int64_t>("display_device_id", 0x15), 0, 127));
    hold_fader_on_release_ = declare_parameter<bool>("hold_fader_on_release", true);
    movement_release_delay_ = std::chrono::milliseconds(std::max<int64_t>(
      50, declare_parameter<int64_t>("movement_release_delay_ms", 300)));
    fader_command_settle_delay_ = std::chrono::milliseconds(std::max<int64_t>(
      100, declare_parameter<int64_t>("fader_command_settle_ms", 1000)));
    const auto publish_period_ms = std::max<int64_t>(
      1, declare_parameter<int64_t>("publish_period_ms", 5));
    publisher_ = create_publisher<Midi>(
      topic, rclcpp::QoS(rclcpp::KeepLast(1)).best_effort());
    input_state_publisher_ = create_publisher<std_msgs::msg::String>(input_state_topic, 10);
    connection_state_publisher_ = create_publisher<std_msgs::msg::String>(
      connection_state_topic, rclcpp::QoS(1).reliable().transient_local());
    feedback_subscription_ = create_subscription<std_msgs::msg::String>(
      feedback_topic, 10,
      std::bind(&MidiInputNode::feedback_callback, this, std::placeholders::_1));
    connection_command_subscription_ = create_subscription<std_msgs::msg::String>(
      connection_command_topic, 10,
      std::bind(&MidiInputNode::connection_command_callback, this, std::placeholders::_1));

    connect_device();

    timer_ = create_wall_timer(
      std::chrono::milliseconds(publish_period_ms),
      std::bind(&MidiInputNode::publish_state, this));
    RCLCPP_INFO(
      get_logger(),
      "MIDI input ready: topic=%s, feedback_topic=%s, fader_feedback=%s, "
      "robot_motor_output=disabled",
      topic.c_str(), feedback_topic.c_str(),
      hold_fader_on_release_ ? "touch_release_hold" : "disabled");
  }

  ~MidiInputNode() override
  {
    disconnect_device(false);
  }

private:
  bool open_input_port(RtMidiIn & input)
  {
    const unsigned int count = input.getPortCount();
    RCLCPP_INFO(get_logger(), "Scanning %u MIDI input port(s)...", count);
    for (unsigned int index = 0; index < count; ++index) {
      const std::string name = input.getPortName(index);
      RCLCPP_INFO(get_logger(), "  [input %u] %s", index, name.c_str());
      if (!is_xtouch_port(name)) {
        continue;
      }
      input.openPort(index);
      RCLCPP_INFO(get_logger(), "Connected MIDI input: '%s'", name.c_str());
      return true;
    }
    return false;
  }

  bool open_output_port(RtMidiOut & output)
  {
    const unsigned int count = output.getPortCount();
    for (unsigned int index = 0; index < count; ++index) {
      const std::string name = output.getPortName(index);
      if (!is_xtouch_port(name)) {
        continue;
      }
      output.openPort(index);
      RCLCPP_INFO(
        get_logger(), "Connected MIDI output for device feedback: '%s'",
        name.c_str());
      return true;
    }
    return false;
  }

  bool connect_device()
  {
    disconnect_device(false);
    try {
      auto input = std::make_unique<RtMidiIn>();
      input->ignoreTypes(true, true, true);
      if (!open_input_port(*input)) {
        connection_message_ = "X-Touch MIDI input port not found";
        publish_connection_state();
        return false;
      }
      input->setCallback(&MidiInputNode::midi_callback, this);

      auto output = std::make_unique<RtMidiOut>();
      if (!open_output_port(*output)) {
        input->cancelCallback();
        input->closePort();
        connection_message_ = "X-Touch MIDI output port not found";
        publish_connection_state();
        return false;
      }
      midi_input_ = std::move(input);
      midi_output_ = std::move(output);
      device_connected_ = true;
      connection_message_ = "X-Touch connected";
      {
        std::lock_guard<std::mutex> lock(mutex_);
        // A newly opened MIDI port is a new hardware session. Never keep a
        // pressed button or an unfinished motorized-fader synchronization
        // from the previous port instance.
        touch_.fill(false);
        fader_.fill(0);
        seen_.fill(false);
        rec_pressed_.fill(false);
        select_pressed_.fill(false);
        changed_while_touched_.fill(false);
        hold_pending_.fill(false);
        movement_active_.fill(false);
        commanded_fader_.fill(0);
        fader_command_syncing_.fill(false);
        input_event_seen_ = false;
      }
      last_select_led_.fill(-1);
      last_rec_led_.fill(-1);
      last_dial_led_.fill(-1);
      last_display_top_.fill("\x01");
      last_display_bottom_.fill("\x01");
      publish_connection_state();
      return true;
    } catch (const std::exception & error) {
      device_connected_ = false;
      connection_message_ = error.what();
      RCLCPP_ERROR(get_logger(), "MIDI reconnect failed: %s", error.what());
      publish_connection_state();
      return false;
    }
  }

  void disconnect_device(bool publish = true)
  {
    device_connected_ = false;
    if (midi_input_) {
      try {
        midi_input_->cancelCallback();
        if (midi_input_->isPortOpen()) {
          midi_input_->closePort();
        }
      } catch (const std::exception &) {
      }
      midi_input_.reset();
    }
    {
      std::lock_guard<std::mutex> output_lock(output_mutex_);
      if (midi_output_) {
        try {
          if (midi_output_->isPortOpen()) {
            midi_output_->closePort();
          }
        } catch (const std::exception &) {
        }
        midi_output_.reset();
      }
    }
    connection_message_ = "X-Touch disconnected";
    if (publish) {
      publish_connection_state();
    }
  }

  void connection_command_callback(const std_msgs::msg::String::SharedPtr msg)
  {
    if (msg->data == "connect") {
      connect_device();
    } else if (msg->data == "disconnect") {
      disconnect_device();
    }
  }

  void publish_connection_state()
  {
    if (!connection_state_publisher_) {
      return;
    }
    std_msgs::msg::String msg;
    msg.data = std::string("{\"connected\":") +
      (device_connected_ ? "true" : "false") +
      ",\"message\":\"" + connection_message_ + "\"}";
    connection_state_publisher_->publish(msg);
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
    input_event_seen_ = true;
    last_input_event_at_ = std::chrono::steady_clock::now();
    if (status == 0xE0 && channel < kChannelCount) {
      const int32_t fader_value =
        std::clamp<int32_t>((data2 << 7) | data1, 0, kFaderMax);
      ++fader_input_generation_[channel];
      fader_[channel] = fader_value;
      // X-Touch does not echo host-driven motor moves on this input port.
      // Any received pitch-bend is therefore fresh physical user input and
      // cancels the command-settle estimate for this channel.
      fader_command_syncing_[channel] = false;
      seen_[channel] = true;
      const auto now = std::chrono::steady_clock::now();
      // X-Touch host-driven motor movement is not echoed as a pitch-bend
      // input on the connected port. Therefore every received pitch-bend is
      // a real surface input and must never be swallowed as output feedback.
      changed_while_touched_[channel] = true;
      movement_active_[channel] = true;
      movement_deadline_[channel] = now + movement_release_delay_;
      // Do not feed a position back while the user is moving the fader.
      // Even an immediate echo can make the motor resist the hand. The final
      // position is sent once after the touch-release event below.
    } else if (
      status == 0xB0 && data1 >= kDialCcStart &&
      data1 < kDialCcStart + kChannelCount)
    {
      // X-Touch encoders report relative movement. Preserve every event in a
      // cumulative counter so the policy node can consume deltas exactly once.
      const int32_t delta = encoder_delta(data2);
      dial_[data1 - kDialCcStart] += delta;
    } else if (
      (status == 0x90 || status == 0x80) && data1 >= kTouchNoteStart &&
      data1 < kTouchNoteStart + kChannelCount)
    {
      const std::size_t touch_channel = data1 - kTouchNoteStart;
      const bool was_touched = touch_[touch_channel];
      touch_[touch_channel] = status == 0x90 && data2 > 0;
      ++fader_input_generation_[touch_channel];
      seen_[touch_channel] = seen_[touch_channel] || touch_[touch_channel];
      if (midi_input_bridge::should_rearm_fader_release_hold(
          hold_fader_on_release_,
          was_touched,
          touch_[touch_channel],
          changed_while_touched_[touch_channel]))
      {
        // Do not trust a single touch-OFF event. Capacitive touch can flicker
        // while the hand is still moving. Re-arm the inactivity window even
        // if it already expired while the hand was resting on the fader.
        // Otherwise the eventual real release never sends the final hold and
        // the surface can return to its previous host-commanded position.
        movement_active_[touch_channel] = true;
        movement_deadline_[touch_channel] =
          std::chrono::steady_clock::now() + movement_release_delay_;
      }
    } else if (status == 0x90 || status == 0x80) {
      const bool pressed = status == 0x90 && data2 > 0;
      if (data1 >= kRecNoteStart && data1 < kRecNoteStart + kChannelCount) {
        const std::size_t button_channel = data1 - kRecNoteStart;
        const bool led_echo =
          std::chrono::steady_clock::now() < rec_led_suppress_until_[button_channel] &&
          pressed == rec_led_expected_[button_channel];
        // As with SELECT, a release must never be suppressed by an LED-OFF
        // echo window or the next physical press has no rising edge.
        if (!pressed || !led_echo) {
          rec_pressed_[button_channel] = pressed;
        }
      } else if (
        data1 >= kSelectNoteStart && data1 < kSelectNoteStart + kChannelCount)
      {
        const std::size_t button_channel = data1 - kSelectNoteStart;
        const bool led_echo =
          std::chrono::steady_clock::now() < select_led_suppress_until_[button_channel] &&
          pressed == select_led_expected_[button_channel];
        // Always accept Note-OFF. Suppressing a quick physical release after
        // SELECT LED-OFF leaves this channel latched and prevents the next
        // physical press from producing a rising edge.
        if (!pressed || !led_echo) {
          select_pressed_[button_channel] = pressed;
        }
      }
    }
  }

  static int32_t encoder_delta(uint8_t value)
  {
    if (value == 0 || value == 64) {
      return 0;
    }
    return value < 64 ? static_cast<int32_t>(value) :
           -static_cast<int32_t>(value - 64);
  }

  static std::string display_text(std::string value)
  {
    for (char & ch : value) {
      if (static_cast<unsigned char>(ch) < 0x20 ||
        static_cast<unsigned char>(ch) > 0x7E)
      {
        ch = '?';
      }
    }
    value.resize(std::min(value.size(), kDisplayCharsPerChannel));
    value.append(kDisplayCharsPerChannel - value.size(), ' ');
    return value;
  }

  void feedback_callback(const std_msgs::msg::String::SharedPtr msg)
  {
    std::vector<std::string> fields;
    std::stringstream stream(msg->data);
    std::string field;
    while (std::getline(stream, field, '\t')) {
      fields.push_back(field);
    }
    if (fields.size() != 8) {
      RCLCPP_WARN_THROTTLE(
        get_logger(), *get_clock(), 5000, "Invalid X-Touch feedback payload");
      return;
    }
    try {
      const int channel = std::stoi(fields[0]);
      if (channel < 0 || channel >= static_cast<int>(kChannelCount)) {
        return;
      }
      const bool selected = std::stoi(fields[1]) != 0;
      const bool motor_angle_mode = std::stoi(fields[2]) != 0;
      const int filter_level = std::clamp(std::stoi(fields[3]), 0, 13);
      const int fader_position = std::stoi(fields[6]);
      const std::uint64_t expected_input_generation = std::stoull(fields[7]);
      // A motorized-fader target is time-critical during recording prepare.
      // Send it before cosmetic LED/LCD feedback so display traffic cannot
      // delay or starve the physical zero command.
      if (fader_position >= 0) {
        send_commanded_fader_position(
          channel, fader_position, expected_input_generation);
      }
      if (last_select_led_[channel] != static_cast<int32_t>(selected)) {
        send_button_led(kSelectNoteStart + channel, selected);
        last_select_led_[channel] = static_cast<int32_t>(selected);
      }
      if (last_rec_led_[channel] != static_cast<int32_t>(motor_angle_mode)) {
        send_button_led(kRecNoteStart + channel, motor_angle_mode);
        last_rec_led_[channel] = static_cast<int32_t>(motor_angle_mode);
      }
      const int dial_level = selected ? filter_level : 0;
      if (last_dial_led_[channel] != dial_level) {
        send_dial_led(channel, dial_level);
        last_dial_led_[channel] = dial_level;
      }
      const std::string display_top = display_text(fields[4]);
      if (last_display_top_[channel] != display_top) {
        send_display_text(channel * kDisplayCharsPerChannel, display_top);
        last_display_top_[channel] = display_top;
      }
      const std::string display_bottom = display_text(fields[5]);
      if (last_display_bottom_[channel] != display_bottom) {
        send_display_text(
          kDisplayBottomRowOffset + channel * kDisplayCharsPerChannel,
          display_bottom);
        last_display_bottom_[channel] = display_bottom;
      }
    } catch (const std::exception &) {
      RCLCPP_WARN_THROTTLE(
        get_logger(), *get_clock(), 5000, "Invalid X-Touch feedback values");
    }
  }

  void send_message(std::vector<unsigned char> bytes, const char * label)
  {
    if (!midi_output_ || !midi_output_->isPortOpen()) {
      return;
    }
    std::lock_guard<std::mutex> lock(output_mutex_);
    try {
      midi_output_->sendMessage(&bytes);
    } catch (const RtMidiError & error) {
      RCLCPP_WARN(get_logger(), "Failed to send MIDI %s: %s", label, error.what());
    }
  }

  void send_button_led(uint8_t note, bool on)
  {
    {
      std::lock_guard<std::mutex> lock(mutex_);
      const auto until =
        std::chrono::steady_clock::now() + std::chrono::milliseconds(80);
      if (note >= kSelectNoteStart && note < kSelectNoteStart + kChannelCount) {
        const std::size_t channel = note - kSelectNoteStart;
        select_led_expected_[channel] = on;
        select_led_suppress_until_[channel] = until;
      } else if (note >= kRecNoteStart && note < kRecNoteStart + kChannelCount) {
        const std::size_t channel = note - kRecNoteStart;
        rec_led_expected_[channel] = on;
        rec_led_suppress_until_[channel] = until;
      }
    }
    send_message({0x90, note, static_cast<unsigned char>(on ? 127 : 0)}, "button LED");
  }

  void send_dial_led(std::size_t channel, int level)
  {
    level = std::clamp(level, 0, 13);
    // Mackie/X-Touch "wrap" mode lights the ring cumulatively from the
    // left. Levels 1..12 select the bar end; level 13 additionally lights
    // the center/bottom segment so the complete 13-step ring is on.
    int ring_value = 0;
    if (level > 0) {
      ring_value = 0x20 | std::min(level - 1, 11);
      if (level == 13) {
        ring_value |= 0x40;
      }
    }
    send_message(
      {0xB0, static_cast<unsigned char>(kDialLedCcStart + channel),
        static_cast<unsigned char>(ring_value)},
      "encoder LED ring");
  }

  void send_display_text(std::size_t offset, const std::string & text)
  {
    std::vector<unsigned char> bytes = {
      0xF0, 0x00, 0x00, 0x66, display_device_id_, 0x12,
      static_cast<unsigned char>(offset),
    };
    for (const char ch : text) {
      bytes.push_back(static_cast<unsigned char>(ch) & 0x7F);
    }
    bytes.push_back(0xF7);
    send_message(std::move(bytes), "LCD text");
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
    send_message(std::move(bytes), "fader hold");
  }

  void send_commanded_fader_position(
    std::size_t channel,
    int32_t value,
    std::uint64_t expected_input_generation)
  {
    if (!midi_output_ || !midi_output_->isPortOpen() || channel >= kChannelCount) {
      return;
    }
    value = std::clamp<int32_t>(value, 0, kFaderMax);
    std::lock_guard<std::mutex> lock(mutex_);
    if (!midi_input_bridge::should_accept_fader_command(
        expected_input_generation,
        fader_input_generation_[channel],
        touch_[channel],
        movement_active_[channel]))
    {
      RCLCPP_WARN_THROTTLE(
        get_logger(), *get_clock(), 2000,
        "Dropped stale/busy fader target: channel=%zu target=%d "
        "expected_generation=%llu current_generation=%llu touch=%d moving=%d",
        channel + 1, value,
        static_cast<unsigned long long>(expected_input_generation),
        static_cast<unsigned long long>(fader_input_generation_[channel]),
        touch_[channel] ? 1 : 0,
        movement_active_[channel] ? 1 : 0);
      return;
    }
    if (
      !fader_command_syncing_[channel] && fader_[channel] == value &&
      !touch_[channel] && !movement_active_[channel])
    {
      // The reported state already matches. Still transmit the command so
      // a newly reconnected surface is driven to the requested position,
      // but do not create an artificial extra busy interval.
      commanded_fader_[channel] = value;
    } else {
      // Repeated policy retries for the same target must not restart the
      // settle timer forever. A new target starts a new settle interval.
      if (!fader_command_syncing_[channel] || commanded_fader_[channel] != value) {
        commanded_fader_[channel] = value;
        fader_command_deadline_[channel] =
          std::chrono::steady_clock::now() + fader_command_settle_delay_;
      }
      fader_command_syncing_[channel] = true;
    }
    send_fader_position(channel, value);
  }

  void publish_state()
  {
    Midi msg;
    std_msgs::msg::String input_state_msg;
    std::array<int32_t, kChannelCount> hold_values{};
    std::array<bool, kChannelCount> send_hold{};
    bool publish_input_state = false;
    {
      std::lock_guard<std::mutex> lock(mutex_);
      const auto now = std::chrono::steady_clock::now();
      std::array<bool, kChannelCount> input_active{};
      for (std::size_t channel = 0; channel < kChannelCount; ++channel) {
        if (
          fader_command_syncing_[channel] &&
          now >= fader_command_deadline_[channel] &&
          !touch_[channel] && !movement_active_[channel])
        {
          // The surface provides no position echo for host-driven movement.
          // After a full settle interval without physical input, report the
          // commanded target as settled. Physical input always cancels this
          // estimate in handle_midi().
          fader_[channel] = commanded_fader_[channel];
          seen_[channel] = true;
          fader_command_syncing_[channel] = false;
        }
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
      msg.btn0 = vector_of(rec_pressed_);
      msg.btn1.assign(kChannelCount, false);
      msg.btn2.assign(kChannelCount, false);
      msg.btn3 = vector_of(select_pressed_);
      if (now >= next_input_state_publish_) {
        std::ostringstream stream;
        stream << "{\"physical_touch\":[";
        for (std::size_t channel = 0; channel < kChannelCount; ++channel) {
          if (channel > 0) stream << ',';
          stream << (touch_[channel] ? "true" : "false");
        }
        stream << "],\"fader_moving\":[";
        for (std::size_t channel = 0; channel < kChannelCount; ++channel) {
          if (channel > 0) stream << ',';
          stream << (movement_active_[channel] ? "true" : "false");
        }
        stream << "],\"fader_syncing\":[";
        for (std::size_t channel = 0; channel < kChannelCount; ++channel) {
          if (channel > 0) stream << ',';
          stream << (fader_command_syncing_[channel] ? "true" : "false");
        }
        stream << "],\"fader_input_generation\":[";
        for (std::size_t channel = 0; channel < kChannelCount; ++channel) {
          if (channel > 0) stream << ',';
          stream << fader_input_generation_[channel];
        }
        stream << "],\"input_event_seen\":"
               << (input_event_seen_ ? "true" : "false")
               << ",\"last_input_event_age_ms\":";
        if (input_event_seen_) {
          stream << std::chrono::duration_cast<std::chrono::milliseconds>(
            now - last_input_event_at_).count();
        } else {
          stream << -1;
        }
        stream << '}';
        input_state_msg.data = stream.str();
        publish_input_state = true;
        next_input_state_publish_ = now + std::chrono::milliseconds(20);
      }
      for (std::size_t channel = 0; channel < kChannelCount; ++channel) {
        if (hold_pending_[channel] && !touch_[channel]) {
          send_hold[channel] = true;
          hold_values[channel] = fader_[channel];
          hold_pending_[channel] = false;
        }
      }
    }
    publisher_->publish(msg);
    if (publish_input_state) {
      input_state_publisher_->publish(input_state_msg);
    }
    for (std::size_t channel = 0; channel < kChannelCount; ++channel) {
      if (send_hold[channel]) {
        send_fader_position(channel, hold_values[channel]);
      }
    }
  }

  std::unique_ptr<RtMidiIn> midi_input_;
  std::unique_ptr<RtMidiOut> midi_output_;
  rclcpp::Publisher<Midi>::SharedPtr publisher_;
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr input_state_publisher_;
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr connection_state_publisher_;
  rclcpp::Subscription<std_msgs::msg::String>::SharedPtr feedback_subscription_;
  rclcpp::Subscription<std_msgs::msg::String>::SharedPtr connection_command_subscription_;
  rclcpp::TimerBase::SharedPtr timer_;
  std::mutex mutex_;
  std::mutex output_mutex_;
  std::array<int32_t, kChannelCount> fader_{};
  std::array<int32_t, kChannelCount> dial_{};
  std::array<bool, kChannelCount> touch_{};
  std::array<bool, kChannelCount> seen_{};
  std::array<bool, kChannelCount> rec_pressed_{};
  std::array<bool, kChannelCount> select_pressed_{};
  std::array<bool, kChannelCount> changed_while_touched_{};
  std::array<bool, kChannelCount> hold_pending_{};
  std::array<bool, kChannelCount> movement_active_{};
  std::array<int32_t, kChannelCount> commanded_fader_{};
  std::array<std::uint64_t, kChannelCount> fader_input_generation_{};
  std::array<bool, kChannelCount> fader_command_syncing_{};
  std::array<std::chrono::steady_clock::time_point, kChannelCount>
    fader_command_deadline_{};
  std::array<std::chrono::steady_clock::time_point, kChannelCount>
    movement_deadline_{};
  std::array<std::chrono::steady_clock::time_point, kChannelCount>
    select_led_suppress_until_{};
  std::array<std::chrono::steady_clock::time_point, kChannelCount>
    rec_led_suppress_until_{};
  std::array<bool, kChannelCount> select_led_expected_{};
  std::array<bool, kChannelCount> rec_led_expected_{};
  std::array<int32_t, kChannelCount> last_select_led_{};
  std::array<int32_t, kChannelCount> last_rec_led_{};
  std::array<int32_t, kChannelCount> last_dial_led_{};
  std::array<std::string, kChannelCount> last_display_top_{};
  std::array<std::string, kChannelCount> last_display_bottom_{};
  std::chrono::milliseconds movement_release_delay_{300};
  std::chrono::milliseconds fader_command_settle_delay_{1000};
  std::chrono::steady_clock::time_point next_input_state_publish_{};
  std::chrono::steady_clock::time_point last_input_event_at_{};
  uint8_t display_device_id_{0x15};
  bool hold_fader_on_release_{true};
  bool device_connected_{false};
  bool input_event_seen_{false};
  std::string connection_message_{"X-Touch disconnected"};
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
