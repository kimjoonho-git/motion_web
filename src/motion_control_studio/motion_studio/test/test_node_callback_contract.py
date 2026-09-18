"""Request/response callback boundary contracts without a live ROS graph."""

import json
from types import SimpleNamespace

import pytest

from std_msgs.msg import String

from motion_studio.editor_node import MotionStudioEditorNode
from motion_studio.studio_node import MotionStudioNode


class CapturePublisher:
    def __init__(self):
        self.messages = []

    def publish(self, message):
        self.messages.append(json.loads(message.data))


def quiet_logger():
    # 실제 rclpy 로거와 같은 이름들 · 정상 거부는 `warn` 으로 나간다 · §6-174
    return SimpleNamespace(
        error=lambda _message: None,
        warn=lambda _message: None,
        warning=lambda _message: None,
        info=lambda _message: None,
    )


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


# 프로젝트를 놓쳤다는 것은 **말로** 알린다 · §6-109
#
# 서비스가 다시 뜨면 이 노드는 빈손으로 시작한다 · 열려 있던 화면은 그것을
# 모르고 편집·삭제를 보낸다 · 부른 쪽이 다시 붙이고 한 번 더 보낼 수 있도록
# 다른 실패와 구분해서 표시한다 · 메시지 글자를 맞춰 보게 하면 문구만 바뀌어도
# 조용히 깨진다.


def _callback_node():
    node = MotionStudioNode.__new__(MotionStudioNode)
    node._project_generation = 1
    node._response_pub = CapturePublisher()
    node._publish_status = lambda: None
    node.get_logger = quiet_logger
    return node


def _request(command='delete_layer'):
    return String(data=json.dumps({
        'request_id': 'studio-request-1',
        'project_generation': 1,
        'command': command,
        'payload': {'project_id': 'project-a', 'project_generation': 1},
    }, ensure_ascii=False))


def test_a_missing_project_is_reported_as_such():
    node = _callback_node()
    node._handle = lambda *_args: MotionStudioNode._require_project_locked(
        SimpleNamespace(_current_project=None)
    )

    node._request_callback(_request())

    response = node._response_pub.messages[-1]
    assert response['success'] is False
    assert response['project_attached'] is False, (
        '프로젝트를 놓친 것을 다른 실패와 구분하지 못한다'
    )


def test_other_failures_are_not_marked_as_a_missing_project():
    node = _callback_node()
    node._handle = lambda *_args: (_ for _ in ()).throw(
        ValueError('잠긴 레이어는 삭제할 수 없습니다')
    )

    node._request_callback(_request())

    response = node._response_pub.messages[-1]
    assert response['success'] is False
    assert 'project_attached' not in response, (
        '다시 붙여도 해결되지 않는 실패까지 재시도하게 된다'
    )


def test_it_is_still_a_value_error():
    """기존 처리부가 ValueError 로 잡고 있다 · 그 계약을 깨지 않는다."""
    with pytest.raises(ValueError):
        MotionStudioNode._require_project_locked(
            SimpleNamespace(_current_project=None)
        )
