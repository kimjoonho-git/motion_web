"""Request/response callback boundary contracts without a live ROS graph."""

import json
from types import SimpleNamespace

from std_msgs.msg import String

from motion_studio.editor_node import MotionStudioEditorNode
from motion_studio.studio_node import MotionStudioNode


class CapturePublisher:
    def __init__(self):
        self.messages = []

    def publish(self, message):
        self.messages.append(json.loads(message.data))


def quiet_logger():
    return SimpleNamespace(error=lambda _message: None)


def test_studio_request_callback_preserves_request_and_project_generation():
    node = MotionStudioNode.__new__(MotionStudioNode)
    node._project_generation = 4
    node._response_pub = CapturePublisher()
    node._handle = lambda command, payload: {
        'success': True,
        'command': command,
        'project_id': payload['project_id'],
    }
    node._publish_status = lambda: None
    node.get_logger = quiet_logger

    node._request_callback(String(data=json.dumps({
        'request_id': 'studio-request-1',
        'project_generation': 4,
        'command': 'status',
        'payload': {
            'project_id': 'project-a',
            'project_generation': 4,
        },
    })))

    assert node._response_pub.messages == [{
        'success': True,
        'command': 'status',
        'project_id': 'project-a',
        'request_id': 'studio-request-1',
        'project_generation': 4,
    }]


def test_studio_request_callback_rejects_previous_project_generation():
    node = MotionStudioNode.__new__(MotionStudioNode)
    node._project_generation = 5
    node._response_pub = CapturePublisher()
    node._handle = lambda *_args: {'success': True}
    node._publish_status = lambda: None
    node.get_logger = quiet_logger

    node._request_callback(String(data=json.dumps({
        'request_id': 'stale-request',
        'project_generation': 4,
        'command': 'status',
        'payload': {
            'project_id': 'project-a',
            'project_generation': 4,
        },
    })))

    response = node._response_pub.messages[0]
    assert response['success'] is False
    assert response['request_id'] == 'stale-request'
    assert response['project_generation'] == 4
    assert '현재 프로젝트 세대와 다른 요청' in response['message']


def test_editor_request_callback_returns_correlated_point_creation_result():
    editor = MotionStudioEditorNode.__new__(MotionStudioEditorNode)
    editor._response_pub = CapturePublisher()
    editor.get_logger = quiet_logger

    editor._request_callback(String(data=json.dumps({
        'request_id': 'editor-request-1',
        'project_generation': 8,
        'command': 'edit',
        'payload': {
            'operation': 'create_axis_point_curve',
            'motion_ids': ['1-1'],
            'layer': {
                'layer_id': 'layer-a',
                'name': '프로젝트 A',
                'enabled': True,
                'locked': False,
                'frames': [
                    {'frame': 1, 'time_sec': 0.02, 'values': {'1-1': 1.0}},
                    {'frame': 2, 'time_sec': 0.04, 'values': {'1-1': 2.0}},
                ],
            },
            'project': {'project_id': 'project-a', 'layers': []},
            'mapping_rows': [],
        },
    })))

    response = editor._response_pub.messages[0]
    assert response['success'] is True
    assert response['request_id'] == 'editor-request-1'
    assert response['project_generation'] == 8
    assert [
        frame['values']['1-1'] for frame in response['layer']['frames']
    ] == [1.0, 2.0]
    assert response['layer']['point_curves'][0]['motion_id'] == '1-1'
