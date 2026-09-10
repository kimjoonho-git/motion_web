"""수동 모터 명령 · 조그 · 절대 이동 · 서보 제어 요청과 응답 대기.

`MotionWebBridge`에서 떼어냈다 · §6-21

화면에서 사람이 직접 내리는 명령만 다룬다. 모션 실행(`motion_run`)이나 MIDI는
각자의 경로가 따로 있다. 최종 모터 출력은 여전히 `motion_supervisor`가 단독으로
발행한다 · 이 서비스는 요청을 보내고 결과를 기다릴 뿐이다.

서비스가 갖는 것 · 조그·동작 요청 발행자와 응답 저장소.
"""

from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Optional

from std_msgs.msg import String

from motion_common import generation, motor_readiness, rpc
from motion_common.values import optional_float, optional_int

from motion_web_bridge import motor_config_rules


class ManualMotorCommandService:
    def __init__(
        self,
        bridge: Any,
        *,
        repository: Any,
        jog_publisher: Any,
        action_publisher: Any,
        jog_result_topic: str,
        action_result_topic: str,
    ) -> None:
        self.bridge = bridge
        self.repository = repository
        self._jog_request_publisher = jog_publisher
        self._action_request_publisher = action_publisher
        self.jog_result_topic = jog_result_topic
        self.action_result_topic = action_result_topic
        self._jog_store = rpc.ResultStore()
        self._action_store = rpc.ResultStore()

    def clear_pending(self) -> None:
        """프로젝트가 바뀌면 이전 프로젝트의 응답을 남기지 않는다."""
        self._jog_store.clear()
        self._action_store.clear()

    def jog_result_callback(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            self.bridge.get_logger().warn(f'Invalid {self.jog_result_topic} JSON received.')
            return
        if not isinstance(payload, dict):
            return

        request_id = str(payload.get('request_id') or '')
        if not request_id or not self._request_matches_current_generation(request_id):
            return

        self._jog_store.store(request_id, payload)

    def action_result_callback(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            self.bridge.get_logger().warn(f'Invalid {self.action_result_topic} JSON received.')
            return
        if not isinstance(payload, dict):
            return

        request_id = str(payload.get('request_id') or '')
        if not request_id or not self._request_matches_current_generation(request_id):
            return

        self._action_store.store(request_id, payload)

    def wait_for_jog_result(
        self,
        request_id: str,
        timeout_sec: float = 1.0,
    ) -> Optional[Dict[str, Any]]:
        return self._jog_store.wait(request_id, timeout_sec)

    def wait_for_action_result(
        self,
        request_id: str,
        timeout_sec: float = 1.0,
    ) -> Optional[Dict[str, Any]]:
        return self._action_store.wait(request_id, timeout_sec)

    def _request_matches_current_generation(self, request_id: Any) -> bool:
        return generation.request_id_matches(
            request_id, self.bridge._current_project_generation()
        )

    def _motion_state_motors(self) -> List[Dict[str, Any]]:
        with self.bridge._lock:
            state = self.bridge._motion_state
        if not isinstance(state, dict):
            return []
        motors = state.get('motors', [])
        if not isinstance(motors, list):
            return []
        return [motor for motor in motors if isinstance(motor, dict)]

    def _motion_state_motor(self, axis: int) -> Optional[Dict[str, Any]]:
        for motor in self._motion_state_motors():
            if optional_int(motor.get('controller_index'), None) == axis:
                return motor
        return None

    @staticmethod
    def _readiness_error(
        motor: Dict[str, Any],
        axis: Any,
        *,
        is_ac_servo: bool = True,
    ) -> str:
        """화면 명령의 선검사 · `motion_supervisor`와 같은 규칙을 쓴다.

        최종 판단은 supervisor가 한다. 여기서 먼저 보는 것은 왕복을 기다리지
        않고 사람에게 사유를 돌려주기 위해서다. 두 곳이 같은 단일 구현을
        경유해야 "화면은 통과했는데 supervisor가 막는" 어긋남이 없다.

        조그·절대이동이므로 내부리밋은 보지 않는다 · `MANUAL_ORDER` 참조.
        """
        return motor_readiness.readiness_error(
            motor,
            order=motor_readiness.MANUAL_ORDER,
            axis=axis,
            is_ac_servo=is_ac_servo,
        )

    def ac_servo_jog(self, axis: Any, relative_deg: Any) -> Dict[str, Any]:
        axis_value = optional_int(axis, None)
        relative_value = optional_float(relative_deg, None)
        if axis_value is None:
            return {
                'success': False,
                'message': 'axis is required',
                **self.bridge.snapshot(),
            }
        if relative_value is None or math.isclose(relative_value, 0.0, abs_tol=1e-9):
            return {
                'success': False,
                'message': 'relative_deg is required',
                **self.bridge.snapshot(),
            }

        motor = self._motion_state_motor(axis_value)
        if motor is None:
            return {
                'success': False,
                'message': f'Axis {axis_value} not found in current motion_state',
                **self.bridge.snapshot(),
            }
        if not motor_config_rules.is_ac_servo_motor(motor):
            return {
                'success': False,
                'message': f'Axis {axis_value} is not AC Servo',
                **self.bridge.snapshot(),
            }
        ready_error = self._readiness_error(motor, axis_value)
        if ready_error:
            return {
                'success': False,
                'message': ready_error,
                **self.bridge.snapshot(),
            }

        request_id = self.bridge._new_project_request_id('jog')
        payload = {
            'request_id': request_id,
            'project_generation': self.bridge._current_project_generation(),
            'command': 'ac_servo_jog',
            'axis': axis_value,
            'relative_deg': relative_value,
        }
        self._jog_request_publisher.publish(
            String(data=json.dumps(payload, ensure_ascii=False, separators=(',', ':')))
        )

        result = self.wait_for_jog_result(request_id)
        if result is None:
            return {
                'success': False,
                'message': (
                    f'AC Servo jog request published, but motion_supervisor result '
                    f'timed out: Axis {axis_value}, {relative_value:+.3f} deg'
                ),
                'request_id': request_id,
                **self.bridge.snapshot(),
            }

        success = bool(result.get('success'))
        if success:
            self.repository.mark_jog_verified()
        return {
            'success': success,
            'message': str(result.get('message') or 'motion_supervisor returned empty result'),
            'request_id': request_id,
            'supervisor_result': result,
            **self.bridge.snapshot(),
        }

    def dynamixel_jog(self, axis: Any, relative_deg: Any) -> Dict[str, Any]:
        axis_value = optional_int(axis, None)
        relative_value = optional_float(relative_deg, None)
        if axis_value is None:
            return {
                'success': False,
                'message': 'axis is required',
                **self.bridge.snapshot(),
            }
        if relative_value is None or math.isclose(relative_value, 0.0, abs_tol=1e-9):
            return {
                'success': False,
                'message': 'relative_deg is required',
                **self.bridge.snapshot(),
            }

        motor = self._motion_state_motor(axis_value)
        if motor is None:
            return {
                'success': False,
                'message': f'Axis {axis_value} not found in current motion_state',
                **self.bridge.snapshot(),
            }
        if not motor_config_rules.is_dynamixel_motor(motor):
            return {
                'success': False,
                'message': f'Axis {axis_value} is not Dynamixel',
                **self.bridge.snapshot(),
            }
        ready_error = self._readiness_error(motor, axis_value, is_ac_servo=False)
        if ready_error:
            return {
                'success': False,
                'message': ready_error,
                **self.bridge.snapshot(),
            }

        request_id = self.bridge._new_project_request_id('dynamixel-jog')
        payload = {
            'request_id': request_id,
            'project_generation': self.bridge._current_project_generation(),
            'command': 'dynamixel_jog',
            'axis': axis_value,
            'relative_deg': relative_value,
        }
        self._jog_request_publisher.publish(
            String(data=json.dumps(payload, ensure_ascii=False, separators=(',', ':')))
        )

        result = self.wait_for_jog_result(request_id)
        if result is None:
            return {
                'success': False,
                'message': (
                    f'Dynamixel jog request published, but motion_supervisor result '
                    f'timed out: Axis {axis_value}, {relative_value:+.3f} deg'
                ),
                'request_id': request_id,
                **self.bridge.snapshot(),
            }

        success = bool(result.get('success'))
        if success:
            self.repository.mark_jog_verified()
        return {
            'success': success,
            'message': str(result.get('message') or 'motion_supervisor returned empty result'),
            'request_id': request_id,
            'supervisor_result': result,
            **self.bridge.snapshot(),
        }

    def ac_servo_action(
        self,
        axis: Any,
        target_deg: Any,
        duration_sec: Any = None,
        range_recovery: Any = False,
    ) -> Dict[str, Any]:
        axis_value = optional_int(axis, None)
        target_value = optional_float(target_deg, None)
        duration_value = optional_float(duration_sec, None)
        if axis_value is None:
            return {
                'success': False,
                'message': 'axis is required',
                **self.bridge.snapshot(),
            }
        if target_value is None:
            return {
                'success': False,
                'message': 'target_deg is required',
                **self.bridge.snapshot(),
            }
        if duration_sec not in (None, '') and (duration_value is None or duration_value <= 0):
            return {
                'success': False,
                'message': 'duration_sec must be greater than 0',
                **self.bridge.snapshot(),
            }

        motor = self._motion_state_motor(axis_value)
        if motor is None:
            return {
                'success': False,
                'message': f'Axis {axis_value} not found in current motion_state',
                **self.bridge.snapshot(),
            }
        if not motor_config_rules.is_ac_servo_motor(motor):
            return {
                'success': False,
                'message': f'Axis {axis_value} is not AC Servo',
                **self.bridge.snapshot(),
            }
        ready_error = self._readiness_error(motor, axis_value)
        if ready_error:
            return {
                'success': False,
                'message': ready_error,
                **self.bridge.snapshot(),
            }

        request_id = self.bridge._new_project_request_id('ac-servo-action')
        payload = {
            'request_id': request_id,
            'project_generation': self.bridge._current_project_generation(),
            'command': 'ac_servo_absolute_move',
            'axis': axis_value,
            'target_deg': target_value,
            'range_recovery': range_recovery is True,
        }
        if duration_value is not None:
            payload['duration_sec'] = duration_value
        self._action_request_publisher.publish(
            String(data=json.dumps(payload, ensure_ascii=False, separators=(',', ':')))
        )

        result = self.wait_for_action_result(request_id)
        if result is None:
            return {
                'success': False,
                'message': (
                    f'AC Servo action request published, but motion_supervisor result '
                    f'timed out: Axis {axis_value}, target {target_value:.3f} deg'
                ),
                'request_id': request_id,
                **self.bridge.snapshot(),
            }

        return {
            'success': bool(result.get('success')),
            'message': str(result.get('message') or 'motion_supervisor returned empty result'),
            'request_id': request_id,
            'supervisor_result': result,
            **self.bridge.snapshot(),
        }

    def dynamixel_action(
        self,
        axis: Any,
        target_deg: Any,
        duration_sec: Any = None,
        range_recovery: Any = False,
    ) -> Dict[str, Any]:
        axis_value = optional_int(axis, None)
        target_value = optional_float(target_deg, None)
        duration_value = optional_float(duration_sec, None)
        if axis_value is None:
            return {
                'success': False,
                'message': 'axis is required',
                **self.bridge.snapshot(),
            }
        if target_value is None:
            return {
                'success': False,
                'message': 'target_deg is required',
                **self.bridge.snapshot(),
            }
        if duration_sec not in (None, '') and (duration_value is None or duration_value <= 0):
            return {
                'success': False,
                'message': 'duration_sec must be greater than 0',
                **self.bridge.snapshot(),
            }

        motor = self._motion_state_motor(axis_value)
        if motor is None:
            return {
                'success': False,
                'message': f'Axis {axis_value} not found in current motion_state',
                **self.bridge.snapshot(),
            }
        if not motor_config_rules.is_dynamixel_motor(motor):
            return {
                'success': False,
                'message': f'Axis {axis_value} is not Dynamixel',
                **self.bridge.snapshot(),
            }
        ready_error = self._readiness_error(motor, axis_value, is_ac_servo=False)
        if ready_error:
            return {
                'success': False,
                'message': ready_error,
                **self.bridge.snapshot(),
            }

        request_id = self.bridge._new_project_request_id('dynamixel-action')
        payload = {
            'request_id': request_id,
            'project_generation': self.bridge._current_project_generation(),
            'command': 'dynamixel_absolute_move',
            'axis': axis_value,
            'target_deg': target_value,
            'range_recovery': range_recovery is True,
        }
        if duration_value is not None:
            payload['duration_sec'] = duration_value
        self._action_request_publisher.publish(
            String(data=json.dumps(payload, ensure_ascii=False, separators=(',', ':')))
        )

        result = self.wait_for_action_result(request_id)
        if result is None:
            return {
                'success': False,
                'message': (
                    f'Dynamixel action request published, but motion_supervisor result '
                    f'timed out: Axis {axis_value}, target {target_value:.3f} deg'
                ),
                'request_id': request_id,
                **self.bridge.snapshot(),
            }

        return {
            'success': bool(result.get('success')),
            'message': str(result.get('message') or 'motion_supervisor returned empty result'),
            'request_id': request_id,
            'supervisor_result': result,
            **self.bridge.snapshot(),
        }

    def ac_servo_control(
        self,
        action: Any,
        axis: Any = None,
        scope: Any = 'selected',
    ) -> Dict[str, Any]:
        action_value = str(action or '').strip().lower().replace('-', '_')
        scope_value = str(scope or 'selected').strip().lower()
        if action_value not in ('servo_on', 'servo_off', 'fault_reset'):
            return {
                'success': False,
                'message': 'action must be servo_on, servo_off, or fault_reset',
                **self.bridge.snapshot(),
            }
        if scope_value not in ('selected', 'all'):
            return {
                'success': False,
                'message': 'scope must be selected or all',
                **self.bridge.snapshot(),
            }

        if scope_value == 'all':
            axes = [
                optional_int(motor.get('controller_index'), None)
                for motor in self._motion_state_motors()
                if motor_config_rules.is_ac_servo_motor(motor)
                and str(motor.get('state') or '') == 'detected'
            ]
            axes = [item for item in axes if item is not None]
            if not axes:
                return {
                    'success': False,
                    'message': 'detected AC Servo axis not found',
                    **self.bridge.snapshot(),
                }
            axis_value = None
        else:
            axis_value = optional_int(axis, None)
            if axis_value is None:
                return {
                    'success': False,
                    'message': 'axis is required',
                    **self.bridge.snapshot(),
                }
            motor = self._motion_state_motor(axis_value)
            if motor is None:
                return {
                    'success': False,
                    'message': f'Axis {axis_value} not found in current motion_state',
                    **self.bridge.snapshot(),
                }
            if not motor_config_rules.is_ac_servo_motor(motor):
                return {
                    'success': False,
                    'message': f'Axis {axis_value} is not AC Servo',
                    **self.bridge.snapshot(),
                }
            if str(motor.get('state') or '') != 'detected':
                return {
                    'success': False,
                    'message': f'Axis {axis_value} is not detected',
                    **self.bridge.snapshot(),
                }
            axes = [axis_value]

        request_id = self.bridge._new_project_request_id('ac-servo-control')
        payload = {
            'request_id': request_id,
            'project_generation': self.bridge._current_project_generation(),
            'command': 'ac_servo_control',
            'action': action_value,
            'scope': scope_value,
            'axes': axes,
        }
        if axis_value is not None:
            payload['axis'] = axis_value

        self._jog_request_publisher.publish(
            String(data=json.dumps(payload, ensure_ascii=False, separators=(',', ':')))
        )

        result = self.wait_for_jog_result(request_id, timeout_sec=2.0)
        if result is None:
            return {
                'success': False,
                'message': (
                    f'AC Servo control request published, but motion_supervisor result '
                    f'timed out: {action_value}'
                ),
                'request_id': request_id,
                **self.bridge.snapshot(),
            }

        return {
            'success': bool(result.get('success')),
            'message': str(result.get('message') or 'motion_supervisor returned empty result'),
            'request_id': request_id,
            'supervisor_result': result,
            **self.bridge.snapshot(),
        }
