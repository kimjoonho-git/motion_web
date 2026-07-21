"""Independent ROS node for temporary motion-layer calculations."""

from __future__ import annotations

import copy
import json
import traceback
from typing import Any, Dict

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from .layer_editor import edit_layer, merge_layers
from .layer_validation import point_curve_frame_mismatches, validate_ranges
from .timeline import layer_conflicts, layer_transition_warnings


class MotionStudioEditorNode(Node):
    def __init__(self) -> None:
        # This node shares a process with MotionStudioNode. Ignore the launch
        # file's global ``__node:=motion_studio_node`` remap so both nodes keep
        # distinct graph and rosout identities.
        super().__init__('motion_studio_editor_node', use_global_arguments=False)
        request_topic = str(self.declare_parameter(
            'request_topic', '/motion_studio/editor/request'
        ).value)
        response_topic = str(self.declare_parameter(
            'response_topic', '/motion_studio/editor/response'
        ).value)
        self._response_pub = self.create_publisher(String, response_topic, 10)
        self.create_subscription(String, request_topic, self._request_callback, 10)
        self.get_logger().info('motion_studio_editor_node started')

    def _request_callback(self, msg: String) -> None:
        try:
            request = json.loads(msg.data)
            request_id = str(request.get('request_id') or '')
            command = str(request.get('command') or '')
            payload = request.get('payload') if isinstance(request.get('payload'), dict) else {}
            result = self._handle(command, payload)
        except Exception as exc:
            self.get_logger().error(traceback.format_exc())
            request_id = locals().get('request_id', '')
            result = {'success': False, 'message': str(exc)}
        result['request_id'] = request_id
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
            if str(payload.get('operation') or '') in {'add_axis', 'point_curve'}:
                requested_ids = {
                    str(value).strip()
                    for value in payload.get('motion_ids') or []
                    if str(value).strip()
                }
                unavailable = sorted(requested_ids - set(ranges))
                if unavailable:
                    raise ValueError(
                        '현재 모션축 설정에 없는 Motion ID: ' + ', '.join(unavailable)
                    )
            layer = edit_layer(payload.get('layer') or {}, payload)
            range_issues = validate_ranges(layer, ranges)
            if range_issues:
                first = range_issues[0]
                raise ValueError(
                    f"{first['motion_id']} {first['time_sec']:.3f}초 값 "
                    f"{first['value_deg']:.3f}°가 모션 범위를 벗어납니다"
                )
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
                'validation': {
                    'conflicts': conflicts,
                    'transition_warnings': warnings,
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
