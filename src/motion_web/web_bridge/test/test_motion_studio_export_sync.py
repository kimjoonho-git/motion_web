"""스튜디오 내보내기 → 프로젝트 반영 계약 · §6-52.

스튜디오 노드가 파일을 쓰고 나면 브리지가 그것을 프로젝트에 등록한다.
이 연결이 끊기면 **스튜디오는 성공했는데 화면은 HTTP 500**을 본다 ·
파일은 남고 활성 파일은 갱신되지 않는 어정쩡한 상태가 된다.

§6-23에서 `bridge._sync_project_file` → `ProjectService.sync_file` 로 옮길 때
이 호출부만 옛 이름으로 남아 있었다. 시험이 없어서 드러나지 않았다.
"""

from pathlib import Path
from types import SimpleNamespace

from motion_web_bridge.motion_studio_sync import MotionStudioSync


class _Transport:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def request(self, command, payload):
        self.calls.append((command, payload))
        return dict(self.result)


def _service(result, synced):
    project_calls = []

    bridge = SimpleNamespace()
    bridge.project_repository = SimpleNamespace(
        selected_project_id=lambda: 'project-a',
        export_path=lambda project_id, category, file_id: Path(
            f'/projects/{project_id}/{category}/{file_id}'
        ),
    )
    bridge._project = SimpleNamespace(
        sync_file=lambda payload, category, path: (
            project_calls.append((category, path)) or {**payload, 'project_sync': synced}
        ),
    )
    service = MotionStudioSync.__new__(MotionStudioSync)
    service.bridge = bridge
    service.transport = _Transport(result)
    return service, project_calls


def test_export_registers_the_written_file_in_the_project():
    service, project_calls = _service(
        {'success': True, 'file_id': 'take-1.json', 'frame_count': 446},
        {'success': True, 'synced': True},
    )

    result = service.export({'file_id': 'take-1'})

    assert result['success'] is True
    assert result['project_sync']['synced'] is True
    assert project_calls == [
        ('motions', Path('/projects/project-a/motions/take-1.json')),
    ]


def test_export_failure_is_passed_through_without_touching_the_project():
    service, project_calls = _service(
        {'success': False, 'message': '내보낼 레이어가 없습니다'},
        {'success': True, 'synced': True},
    )

    result = service.export({'file_id': 'take-1'})

    assert result['success'] is False
    assert project_calls == []


def test_export_without_a_file_id_does_not_register_anything():
    service, project_calls = _service({'success': True, 'file_id': ''}, {})

    result = service.export({'file_id': ''})

    assert result == {'success': True, 'file_id': ''}
    assert project_calls == []
