import json
import math
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

import rclpy
from midi_msgs.msg import Midi
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from std_msgs.msg import String


MIDI_CHANNEL_COUNT = 8
MIDI_VALUE_MIN = 0
MIDI_VALUE_MAX = 16383


class MidiControlNode(Node):
    """Read-only MIDI monitor and 14-bit-to-degree converter.

    This node intentionally has no motor command publisher. It only consumes the
    X-Touch state, converts fader values using a user mapping, and publishes a
    JSON status for the web bridge.
    """

    def __init__(self) -> None:
        super().__init__('midi_control_node')
        self.input_topic = str(self.declare_parameter('input_topic', '/xtouch/midi').value)
        self.state_topic = str(
            self.declare_parameter('state_topic', '/motion_web/midi_monitor/state').value
        )
        self.request_topic = str(
            self.declare_parameter('request_topic', '/motion_web/midi_monitor/request').value
        )
        self.response_topic = str(
            self.declare_parameter('response_topic', '/motion_web/midi_monitor/response').value
        )
        default_mapping = '/home/joonho_test/ros2_ws/motion_data/midi_mappings/default.json'
        self.mapping_file = Path(
            str(self.declare_parameter('mapping_file', default_mapping).value)
        ).expanduser()
        self.publish_hz = max(1.0, float(self.declare_parameter('publish_hz', 10.0).value))
        self.stale_timeout_sec = max(
            0.1,
            float(self.declare_parameter('stale_timeout_sec', 0.5).value),
        )

        self._lock = threading.Lock()
        self._last_received_monotonic: float | None = None
        self._last_received_wall: float | None = None
        self._channels = [0] * MIDI_CHANNEL_COUNT
        self._touch = [False] * MIDI_CHANNEL_COUNT
        self._dial = [0] * MIDI_CHANNEL_COUNT
        self._btn0 = [False] * MIDI_CHANNEL_COUNT
        self._btn1 = [False] * MIDI_CHANNEL_COUNT
        self._btn2 = [False] * MIDI_CHANNEL_COUNT
        self._btn3 = [False] * MIDI_CHANNEL_COUNT
        self._confirmed = [False] * MIDI_CHANNEL_COUNT
        self._mapping = self._load_mapping()

        self._state_publisher = self.create_publisher(String, self.state_topic, 10)
        self._response_publisher = self.create_publisher(String, self.response_topic, 10)
        midi_qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT)
        self._midi_subscription = self.create_subscription(
            Midi,
            self.input_topic,
            self._midi_callback,
            midi_qos,
        )
        self._request_subscription = self.create_subscription(
            String,
            self.request_topic,
            self._request_callback,
            10,
        )
        self._timer = self.create_timer(1.0 / self.publish_hz, self._publish_state)
        self.get_logger().info(
            f'MIDI monitor ready: input={self.input_topic}, state={self.state_topic}, '
            'motor_output=disabled'
        )

    @staticmethod
    def _default_mapping() -> List[Dict[str, Any]]:
        return [
            {
                'channel': channel,
                'enabled': True,
                'motion_id': str(channel + 1),
                'min_deg': -180.0,
                'max_deg': 180.0,
                'reversed': False,
            }
            for channel in range(MIDI_CHANNEL_COUNT)
        ]

    def _load_mapping(self) -> List[Dict[str, Any]]:
        try:
            payload = json.loads(self.mapping_file.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            return self._default_mapping()
        mappings = payload.get('mappings') if isinstance(payload, dict) else None
        try:
            return self._validated_mapping(mappings)
        except ValueError as exc:
            self.get_logger().warning(f'Invalid MIDI mapping file; using defaults: {exc}')
            return self._default_mapping()

    @staticmethod
    def _finite_float(value: Any, field: str) -> float:
        try:
            result = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f'{field} must be a number') from exc
        if not math.isfinite(result):
            raise ValueError(f'{field} must be finite')
        return result

    def _validated_mapping(self, mappings: Any) -> List[Dict[str, Any]]:
        if not isinstance(mappings, list):
            raise ValueError('mappings must be an array')
        by_channel: Dict[int, Dict[str, Any]] = {}
        for index, item in enumerate(mappings):
            if not isinstance(item, dict):
                raise ValueError(f'mappings[{index}] must be an object')
            try:
                channel = int(item.get('channel', index))
            except (TypeError, ValueError) as exc:
                raise ValueError(f'mappings[{index}].channel must be an integer') from exc
            if channel < 0 or channel >= MIDI_CHANNEL_COUNT:
                raise ValueError(f'mappings[{index}].channel must be 0..7')
            min_deg = self._finite_float(item.get('min_deg', -180.0), 'min_deg')
            max_deg = self._finite_float(item.get('max_deg', 180.0), 'max_deg')
            if abs(max_deg - min_deg) < 1e-9:
                raise ValueError(f'channel {channel + 1}: min_deg and max_deg must differ')
            motion_id = str(item.get('motion_id') or channel + 1).strip()
            if not motion_id:
                raise ValueError(f'channel {channel + 1}: motion_id is required')
            by_channel[channel] = {
                'channel': channel,
                'enabled': bool(item.get('enabled', True)),
                'motion_id': motion_id,
                'min_deg': min_deg,
                'max_deg': max_deg,
                'reversed': bool(item.get('reversed', False)),
            }
        defaults = self._default_mapping()
        return [by_channel.get(channel, defaults[channel]) for channel in range(MIDI_CHANNEL_COUNT)]

    @staticmethod
    def _array_value(values: Any, index: int, fallback: Any) -> Any:
        return values[index] if index < len(values) else fallback

    def _midi_callback(self, msg: Midi) -> None:
        with self._lock:
            for channel in range(MIDI_CHANNEL_COUNT):
                raw = int(self._array_value(msg.channel, channel, 0))
                touched = bool(self._array_value(msg.touch, channel, False))
                self._touch[channel] = touched
                # Only a hand-touched fader is an actionable MIDI input.
                # Keep publishing connection/touch state continuously, but
                # retain the last touched value after release and ignore any
                # untouch/motor-driven position changes.
                if touched:
                    self._channels[channel] = max(
                        MIDI_VALUE_MIN, min(MIDI_VALUE_MAX, raw)
                    )
                self._dial[channel] = int(self._array_value(msg.dial, channel, 0))
                self._btn0[channel] = bool(self._array_value(msg.btn0, channel, False))
                self._btn1[channel] = bool(self._array_value(msg.btn1, channel, False))
                self._btn2[channel] = bool(self._array_value(msg.btn2, channel, False))
                self._btn3[channel] = bool(self._array_value(msg.btn3, channel, False))
                self._confirmed[channel] = self._confirmed[channel] or self._btn0[channel]
            self._last_received_monotonic = time.monotonic()
            self._last_received_wall = time.time()

    @staticmethod
    def _motion_degrees(raw_value: int, mapping: Dict[str, Any]) -> float | None:
        if not mapping['enabled']:
            return None
        normalized = max(0.0, min(1.0, float(raw_value) / MIDI_VALUE_MAX))
        if mapping['reversed']:
            normalized = 1.0 - normalized
        value = mapping['min_deg'] + (
            (mapping['max_deg'] - mapping['min_deg']) * normalized
        )
        return round(value, 6)

    def _snapshot(self) -> Dict[str, Any]:
        now_monotonic = time.monotonic()
        with self._lock:
            last_monotonic = self._last_received_monotonic
            last_wall = self._last_received_wall
            raw_values = list(self._channels)
            touch = list(self._touch)
            dial = list(self._dial)
            buttons = [
                [self._btn0[index], self._btn1[index], self._btn2[index], self._btn3[index]]
                for index in range(MIDI_CHANNEL_COUNT)
            ]
            confirmed = list(self._confirmed)
            mappings = [dict(item) for item in self._mapping]
        age_sec = None if last_monotonic is None else max(0.0, now_monotonic - last_monotonic)
        connected = age_sec is not None and age_sec <= self.stale_timeout_sec
        channels = []
        for channel, mapping in enumerate(mappings):
            raw_value = raw_values[channel]
            channels.append({
                **mapping,
                'channel_number': channel + 1,
                'raw_value': raw_value,
                'normalized': round(raw_value / MIDI_VALUE_MAX, 6),
                'motion_deg': self._motion_degrees(raw_value, mapping),
                'value_confirmed': confirmed[channel],
                'touch': touch[channel],
                'dial': dial[channel],
                'buttons': buttons[channel],
            })
        return {
            'success': True,
            'node_state': 'ok',
            'connected': connected,
            'message': 'MIDI 데이터 수신 정상' if connected else 'MIDI 데이터 수신 대기',
            'input_topic': self.input_topic,
            'last_received_at': last_wall,
            'age_sec': None if age_sec is None else round(age_sec, 3),
            'value_bits': 14,
            'value_min': MIDI_VALUE_MIN,
            'value_max': MIDI_VALUE_MAX,
            'unit': 'deg',
            'motor_output_enabled': False,
            'touch_gated_input': True,
            'mapping_file': str(self.mapping_file),
            'channels': channels,
        }

    def _publish_json(self, publisher: Any, payload: Dict[str, Any]) -> None:
        msg = String()
        msg.data = json.dumps(payload, ensure_ascii=False)
        publisher.publish(msg)

    def _publish_state(self) -> None:
        self._publish_json(self._state_publisher, self._snapshot())

    def _save_mapping(self, mappings: Any) -> List[Dict[str, Any]]:
        validated = self._validated_mapping(mappings)
        self.mapping_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.mapping_file.with_suffix(self.mapping_file.suffix + '.tmp')
        temporary.write_text(
            json.dumps({
                'format': 'midi_motion_mapping',
                'version': 1,
                'value_bits': 14,
                'unit': 'deg',
                'mappings': validated,
            }, ensure_ascii=False, indent=2) + '\n',
            encoding='utf-8',
        )
        temporary.replace(self.mapping_file)
        with self._lock:
            self._mapping = validated
        return validated

    def _request_callback(self, msg: String) -> None:
        try:
            request = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        if not isinstance(request, dict):
            return
        request_id = str(request.get('request_id') or '')
        command = str(request.get('command') or 'status')
        payload = request.get('payload') if isinstance(request.get('payload'), dict) else {}
        response: Dict[str, Any]
        try:
            if command == 'save_mapping':
                self._save_mapping(payload.get('mappings'))
                response = self._snapshot()
                response['message'] = 'MIDI 매칭 설정 저장 완료'
            elif command == 'status':
                response = self._snapshot()
            else:
                response = {
                    'success': False,
                    'message': f'unsupported command: {command}',
                }
        except (OSError, ValueError) as exc:
            response = {
                'success': False,
                'message': str(exc),
            }
        response['request_id'] = request_id
        self._publish_json(self._response_publisher, response)


def main(args: List[str] | None = None) -> None:
    rclpy.init(args=args)
    node = MidiControlNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
