"""Independent ROS node for temporary motion-layer calculations."""

from __future__ import annotations

import copy
import json
import traceback
from typing import Any, Dict

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from .layer_editor import (
    approximate_motion_points,
    edit_layer,
    merge_layers,
    spike_correction_report,
)
from .layer_validation import point_curve_frame_mismatches, validate_ranges
from .timeline import layer_conflicts, layer_transition_warnings


class MotionStudioEditorNode(Node):
    def __init__(self) -> None:
        super().__init__('motion_studio_editor_node')
        self.request_topic = str(self.declare_parameter(
            'request_topic', '/motion_studio/editor/request'
        ).value)
        self.response_topic = str(self.declare_parameter(
            'response_topic', '/motion_studio/editor/response'
        ).value)
        self._response_pub = self.create_publisher(String, self.response_topic, 10)
        self.create_subscription(String, self.request_topic, self._request_callback, 10)
        self.get_logger().info(
            'motion_studio_editor_node started: '
            f'request={self.request_topic}, response={self.response_topic}'
        )

    def _request_callback(self, msg: String) -> None:
        try:
            request = json.loads(msg.data)
            request_id = str(request.get('request_id') or '')
            project_generation = request.get('project_generation')
            command = str(request.get('command') or '')
            payload = request.get('payload') if isinstance(request.get('payload'), dict) else {}
            result = self._handle(command, payload)
        except Exception as exc:
            self.get_logger().error(traceback.format_exc())
            request_id = locals().get('request_id', '')
            project_generation = locals().get('project_generation')
            result = {'success': False, 'message': str(exc)}
        result['request_id'] = request_id
        result['project_generation'] = project_generation
        self._response_pub.publish(String(data=json.dumps(
            result, ensure_ascii=False, separators=(',', ':')
        )))

    @staticmethod
    def _ranges(payload: Dict[str, Any]) -> Dict[str, tuple[float, float]]:
        result = {}
        for row in payload.get('mapping_rows') or []:
            if not isinstance(row, dict) or not row.get('motion_id'):
                continue
            result[str(row['motion_id'])] = (
                float(row.get('motion_lower_deg', -180.0)),
                float(row.get('motion_upper_deg', 180.0)),
            )
        return result

    @staticmethod
    def _manual_values(payload: Dict[str, Any]) -> Dict[str, float]:
        return {
            str(row['motion_id']): float(row.get('initial_motion_position_deg', 0.0))
            for row in payload.get('mapping_rows') or []
            if isinstance(row, dict)
            and row.get('motion_id')
            and str(row.get('initial_mode') or 'first_frame') == 'manual'
        }

    def _handle(self, command: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        ranges = self._ranges(payload)
        if command == 'edit':
            operation_report = None
            if str(payload.get('operation') or '') == 'repair_spikes':
                operation_report = spike_correction_report(
                    payload.get('layer') or {},
                    payload.get('motion_ids') or [],
                    payload.get('start_sec', 0.0),
                    payload.get('end_sec', 0.0),
                    payload.get('spike_detection_threshold_deg', 0.1),
                    payload.get('spike_maximum_correction_deg', 1.0),
                )
            if str(payload.get('operation') or '') == 'convert_motion_to_point_curve':
                selected = {
                    str(value) for value in payload.get('motion_ids') or [] if str(value)
                }
                if len(selected) == 1:
                    motion_id = next(iter(selected))
                    start_sec = float(payload.get('start_sec') or 0.0)
                    end_sec = float(payload.get('end_sec') or start_sec)
                    samples = sorted(
                        (
                            float(frame.get('time_sec') or 0.0),
                            float((frame.get('values') or {})[motion_id]),
                        )
                        for frame in (payload.get('layer') or {}).get('frames') or []
                        if (
                            motion_id in (frame.get('values') or {})
                            and start_sec <= float(frame.get('time_sec') or 0.0) <= end_sec
                        )
                    )
                    _points, operation_report = approximate_motion_points(
                        samples,
                        payload.get('approximation_tolerance_deg', 0.1),
                        payload.get('approximation_maximum_points', 50),
                        payload.get('approximation_interpolation_order', 1),
                    )
            layer = edit_layer(payload.get('layer') or {}, payload)
            range_issues = validate_ranges(layer, ranges)
            project = copy.deepcopy(payload.get('project') or {})
            for index, existing in enumerate(project.get('layers') or []):
                if str(existing.get('layer_id') or '') == str(layer.get('layer_id') or ''):
                    project['layers'][index] = copy.deepcopy(layer)
                    break
            conflicts = layer_conflicts(project) if project else []
            warnings = layer_transition_warnings(
                project, ranges, self._manual_values(payload)
            ) if project else []
            curve_mismatches = point_curve_frame_mismatches(layer)
            return {
                'success': True,
                'message': '편집 결과를 임시 반영했습니다',
                'layer': layer,
                'operation_report': operation_report,
                'validation': {
                    'conflicts': conflicts,
                    'transition_warnings': warnings,
                    'range_warnings': range_issues,
                    'point_curve_mismatches': curve_mismatches,
                    'playable': not conflicts and not warnings and not curve_mismatches,
                },
            }
        if command == 'merge':
            mismatch_layers = []
            for layer in (payload.get('project') or {}).get('layers') or []:
                if str(layer.get('layer_id') or '') not in {
                    str(value) for value in payload.get('layer_ids') or []
                }:
                    continue
                if point_curve_frame_mismatches(layer):
                    mismatch_layers.append(str(layer.get('name') or layer.get('layer_id') or ''))
            if mismatch_layers:
                raise ValueError(
                    '포인트 곡선과 20ms 프레임이 다른 레이어를 먼저 정리하세요: '
                    + ', '.join(mismatch_layers)
                )
            layer = merge_layers(
                payload.get('project') or {}, payload.get('layer_ids') or [],
                name=payload.get('name'), motion_ranges_deg=ranges,
                initial_motion_values_deg=self._manual_values(payload),
            )
            return {'success': True, 'message': '레이어 합성 결과를 만들었습니다', 'layer': layer}
        raise ValueError('지원하지 않는 모션 편집 명령입니다')


def main(args=None) -> None:
    rclpy.init(args=args)
    node = MotionStudioEditorNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
