"""Independent ROS node for temporary motion-layer calculations."""

from __future__ import annotations

import json
from typing import Any, Dict

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from .layer_editor import (
    approximate_motion_points,
    edit_layer,
    frames_are_curve_derived,
    merge_layers,
)
from .layer_validation import point_curve_frame_mismatches, validate_ranges
from .mapping_model import manual_initial_values, motion_ranges
from .motion_model import layer_motion_ids
from .timeline import layer_conflicts
from motion_common import command_router, topics


def _project_axes(project: Dict[str, Any]) -> set:
    """이 프로젝트 사본에 프레임이 실려 있는 축 · §6-293

    화면이 걸러 보낸 사본이라 모든 축이 들어 있지 않다 · 무엇이 들어 있는지
    적어 두어야 다음 편집에 그대로 써도 되는지 알 수 있다.
    """
    axes = set()
    for layer in project.get('layers') or []:
        if not isinstance(layer, dict):
            continue
        for frame in layer.get('frames') or []:
            if isinstance(frame, dict):
                axes.update(str(key) for key in (frame.get('values') or {}))
    return axes


class MotionStudioEditorNode(Node):
    def __init__(self) -> None:
        super().__init__('motion_studio_editor_node')
        self.request_topic = str(self.declare_parameter(
            'request_topic', topics.STUDIO_EDITOR_REQUEST
        ).value)
        self.response_topic = str(self.declare_parameter(
            'response_topic', topics.STUDIO_EDITOR_RESPONSE
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
            # 막은 것인가 고장 난 것인가 · §6-174
            command_router.log_command_failure(
                self.get_logger(), f'studio editor command failed: {locals().get("command", "")}', exc,
            )
            request_id = locals().get('request_id', '')
            project_generation = locals().get('project_generation')
            result = {'success': False, 'message': str(exc)}
        result['request_id'] = request_id
        result['project_generation'] = project_generation
        self._response_pub.publish(String(data=json.dumps(
            result, ensure_ascii=False, separators=(',', ':')
        )))

    _ranges = staticmethod(motion_ranges)
    _manual_values = staticmethod(manual_initial_values)
    _layer_motion_ids = staticmethod(layer_motion_ids)

    def _project_for(self, payload: Dict[str, Any]):
        """편집에 쓸 프로젝트 · **한 번만 받아 기억한다** · §6-293

        편집 노드가 프로젝트를 쓰는 곳은 **충돌 판정 한 군데**다 · 그런데
        화면이 편집할 때마다 프로젝트를 통째로 올렸다 · 실측으로 2.98 MB 였고,
        레이어까지 합쳐 한 번에 4.2 MB 가 오갔다 · 포인트 하나를 끌 때마다다.

        프로젝트가 바뀌면 `project_generation` 이 올라간다 · 같은 세대면 기억한
        것을 그대로 쓴다 · 다르면 화면에 「보내 주세요」라고 답하고, 화면이 그때
        한 번 실어 보낸다.

        기억은 **한 세대만** 둔다 · 옛 세대로 판정하면 이미 지워진 레이어와
        충돌한다고 말하게 된다.
        """
        generation = payload.get('project_generation')
        # **키가 있으면 그대로 쓴다** · 빈 프로젝트도 뜻이 있다(비교할 레이어가
        # 없다) · 키가 아예 없을 때만 기억한 것을 찾는다 · §6-293
        given = payload.get('project') if 'project' in payload else None
        if isinstance(given, dict):
            self._project_cache = (generation, given, _project_axes(given))
            return given
        cached = getattr(self, '_project_cache', None)
        if cached is None or cached[0] != generation:
            return None
        # **고른 축이 다르면 못 쓴다** · §6-293
        #
        # 화면은 프로젝트를 보낼 때 그 편집에 걸린 축의 프레임만 남겨 보낸다 ·
        # 다른 축을 편집하면서 그것을 그대로 쓰면, 없는 축은 충돌이 없다고
        # 보게 된다 · 겹치는 레이어를 그냥 지나친다.
        needed = self._layer_motion_ids(payload.get('layer') or {})
        if not needed.issubset(cached[2]):
            return None
        return cached[1]

    def _handle(self, command: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        ranges = self._ranges(payload)
        if command == 'edit':
            operation_report = None
            if str(payload.get('operation') or '') == 'create_axis_point_curve':
                selected = {
                    str(value) for value in payload.get('motion_ids') or [] if str(value)
                }
                if len(selected) == 1:
                    motion_id = next(iter(selected))
                    samples = sorted(
                        (
                            float(frame.get('time_sec') or 0.0),
                            float((frame.get('values') or {})[motion_id]),
                        )
                        for frame in (payload.get('layer') or {}).get('frames') or []
                        if motion_id in (frame.get('values') or {})
                    )
                    _points, operation_report = approximate_motion_points(
                        samples,
                        payload.get('approximation_tolerance_deg', 0.1),
                        payload.get('approximation_maximum_points', 50),
                        payload.get('approximation_interpolation_order', 1),
                    )
            original_layer = payload.get('layer') or {}
            layer = edit_layer(original_layer, payload)
            range_issues = validate_ranges(layer, ranges)
            source_project = self._project_for(payload)
            if source_project is None:
                # 기억한 것이 없다 · 화면이 한 번만 보내 주면 된다 · §6-293
                return {
                    'success': False,
                    'need_project': True,
                    'message': '프로젝트를 함께 보내세요 (편집 노드에 기억된 것이 없습니다)',
                }
            project = dict(source_project)
            project['layers'] = list(source_project.get('layers') or [])
            for index, existing in enumerate(project['layers']):
                if str(existing.get('layer_id') or '') == str(layer.get('layer_id') or ''):
                    project['layers'][index] = layer
                    break
            affected_motion_ids = (
                self._layer_motion_ids(original_layer)
                | self._layer_motion_ids(layer)
            )
            conflicts = layer_conflicts(
                project, motion_ids=affected_motion_ids
            ) if project else []
            curve_mismatches = point_curve_frame_mismatches(layer)
            return {
                'success': True,
                'message': '편집 결과를 임시 반영했습니다',
                'layer': layer,
                # 프레임을 다시 그릴 수 있나 · 화면은 이 답만 보고 판단한다 · §6-292
                #
                # 같은 규칙을 화면에도 두면 언젠가 갈린다 · 판정은 여기 한 곳이다.
                'frames_are_curve_derived': frames_are_curve_derived(layer),
                'operation_report': operation_report,
                'validation': {
                    'conflicts': conflicts,
                    'range_warnings': range_issues,
                    'point_curve_mismatches': curve_mismatches,
                    'playable': not conflicts and not curve_mismatches,
                    'scope': 'affected_motion_ids',
                    'motion_ids': sorted(affected_motion_ids),
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
                name=payload.get('name'),
                append_layer_id=payload.get('append_layer_id'),
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
