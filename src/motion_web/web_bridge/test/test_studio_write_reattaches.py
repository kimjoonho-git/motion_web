"""쓰기 명령은 노드가 프로젝트를 놓쳤으면 다시 붙이고 한 번 더 · §6-109

서비스가 다시 뜨면(갱신·재시작) 스튜디오 노드는 빈손으로 시작한다 · 그런데
열려 있던 화면은 그것을 모르고 편집·삭제를 보낸다 · 화면에는 프로젝트가 멀쩡히
보이는데 "프로젝트를 선택하세요" 가 떴다.

새로 고치면 되던 이유는 **조회 경로만** 노드에 되물어 보고 어긋나면 다시 붙이기
때문이다 · 쓰기 경로에는 그 확인이 없었다.
"""

from motion_web_bridge.motion_studio_sync import MotionStudioSync


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, command, payload=None, timeout_sec=4.0):
        self.calls.append((command, dict(payload or {}), timeout_sec))
        return self.responses.pop(0)


def _sync(responses, prepare_result=None):
    sync = MotionStudioSync.__new__(MotionStudioSync)
    sync.transport = FakeTransport(responses)
    sync.prepare_calls = []

    def prepare():
        sync.prepare_calls.append(True)
        return prepare_result if prepare_result is not None else {'success': True}

    sync.prepare = prepare
    return sync


def test_an_attached_node_is_written_to_once():
    """평소에는 값이 더 들지 않는다 · 다시 붙이지도, 두 번 보내지도 않는다."""
    sync = _sync([{'success': True}])

    result = sync.request_attached('delete_layer', {'layer_id': 'a'})

    assert result == {'success': True}
    assert len(sync.transport.calls) == 1
    assert sync.prepare_calls == []


def test_a_node_that_lost_the_project_is_reattached_and_retried():
    sync = _sync([
        {'success': False, 'project_attached': False, 'message': '프로젝트 없음'},
        {'success': True, 'message': '레이어를 삭제했습니다'},
    ])

    result = sync.request_attached('delete_layer', {'layer_id': 'a'})

    assert result['success'] is True
    assert sync.prepare_calls == [True]
    assert len(sync.transport.calls) == 2
    assert sync.transport.calls[0] == sync.transport.calls[1], (
        '두 번째 요청이 첫 번째와 달라졌다'
    )


def test_a_failed_reattach_is_reported_instead_of_retrying():
    sync = _sync(
        [{'success': False, 'project_attached': False}],
        prepare_result={'success': False, 'message': '통합 프로젝트 미선택'},
    )

    result = sync.request_attached('update_layer', {})

    assert result['message'] == '통합 프로젝트 미선택'
    assert len(sync.transport.calls) == 1, '붙지도 않았는데 또 보냈다'


def test_other_failures_are_not_retried():
    """다시 붙여도 해결되지 않는 실패까지 두 번 보내면 안 된다."""
    sync = _sync([{'success': False, 'message': '잠긴 레이어는 삭제할 수 없습니다'}])

    result = sync.request_attached('delete_layer', {'layer_id': 'a'})

    assert result['message'] == '잠긴 레이어는 삭제할 수 없습니다'
    assert len(sync.transport.calls) == 1
    assert sync.prepare_calls == []


def test_the_timeout_is_carried_into_the_retry():
    sync = _sync([
        {'success': False, 'project_attached': False},
        {'success': True},
    ])

    sync.request_attached('replace_layer_data', {'layer_id': 'a'}, timeout_sec=8.0)

    assert [call[2] for call in sync.transport.calls] == [8.0, 8.0]


# 길을 실제로 이 함수로 냈는지 · 검사가 통과해도 길이 그대로면 소용없다

from pathlib import Path

ROUTES = (
    Path(__file__).resolve().parents[1]
    / 'motion_web_bridge' / 'motion_studio_routes.py'
).read_text(encoding='utf-8')


def test_every_studio_write_goes_through_the_reattaching_call():
    writes = (
        'save',
        'update_layer',
        'create_layer',
        'replace_layer_data',
        'delete_layer',
        'duplicate_layer',
        'commit_merged_layer',
    )
    for command in writes:
        assert f"transport().request(\n                    '{command}'" not in ROUTES
        assert f"transport().request('{command}'" not in ROUTES, (
            f'{command} 가 아직 옛 길로 나간다'
        )
        assert f"'{command}'" in ROUTES
