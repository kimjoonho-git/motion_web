import json
import threading
import time
from typing import Any, Dict, List

import rclpy
from midi_msgs.msg import Midi
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from std_msgs.msg import String

from midi_control.bank_manager import MIDI_CHANNEL_COUNT, MidiBankManager


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
        self._banks = MidiBankManager()

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

    def _validated_mapping(self, mappings: Any) -> List[Dict[str, Any]]:
        return MidiBankManager.validate_mappings(mappings)

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
            bank_state = self._banks.snapshot()
            mappings = bank_state['active_bank']['mappings']
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
            'bank_storage': bank_state['storage'],
            'bank_persistent': bank_state['persistent'],
            'max_banks': bank_state['max_banks'],
            'active_bank_id': bank_state['active_bank_id'],
            'active_bank': bank_state['active_bank'],
            'banks': bank_state['banks'],
            'channels': channels,
        }

    def _publish_json(self, publisher: Any, payload: Dict[str, Any]) -> None:
        msg = String()
        msg.data = json.dumps(payload, ensure_ascii=False)
        publisher.publish(msg)

    def _publish_state(self) -> None:
        self._publish_json(self._state_publisher, self._snapshot())

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
            if command in {'save_mapping', 'update_bank'}:
                bank_id = payload.get('bank_id') or self._banks.snapshot()['active_bank_id']
                with self._lock:
                    self._banks.update_bank(
                        bank_id,
                        name=payload.get('name'),
                        mappings=payload.get('mappings'),
                    )
                response = self._snapshot()
                response['message'] = 'MIDI 뱅크 설정 메모리 적용 완료'
            elif command == 'create_bank':
                with self._lock:
                    bank = self._banks.create_bank(payload.get('name'), copy_from_active=True)
                    self._banks.select_bank(bank['bank_id'])
                response = self._snapshot()
                response['message'] = 'MIDI 뱅크 추가 완료 (메모리 전용)'
            elif command == 'select_bank':
                with self._lock:
                    self._banks.select_bank(payload.get('bank_id'))
                response = self._snapshot()
                response['message'] = 'MIDI 뱅크 전환 완료'
            elif command == 'delete_bank':
                with self._lock:
                    self._banks.delete_bank(payload.get('bank_id'))
                response = self._snapshot()
                response['message'] = 'MIDI 뱅크 삭제 완료'
            elif command == 'status':
                response = self._snapshot()
            else:
                response = {
                    'success': False,
                    'message': f'unsupported command: {command}',
                }
        except ValueError as exc:
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
