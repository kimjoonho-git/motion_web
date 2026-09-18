"""실행 컨텍스트는 함께 맞춰야 하는 것만 센다 · §6-175

**스튜디오에서 레이어를 저장할 때마다 네 노드가 재적용을 받았다.**

실행 컨텍스트 ID 는 프로젝트의 **네 종류 파일 전부**를 해시했다 —
모터축 · 모션축 · 모션 파일 · 레이어 · 그런데 노드에게 실제로 보내는 것은
**모션축 설정 하나**뿐이다.

    payload = {'context_id', 'project_generation',
               'mapping_file_id', 'mapping_sha256'}

그래서 레이어를 저장하거나 모션 파일이 바뀌면, **아무 새 정보도 없는**
재적용이 네 노드에 나갔다 · 하나라도 2초 안에 응답 못 하면
`invalidate_nodes()` 가 돌아 MIDI·모션 실행·스튜디오의 기억이 통째로 지워졌다.

코드에 이런 주석이 남아 있다.

    Treating that rejection as a node failure used to invalidate MIDI,
    motion_run and studio **in the middle of recording**.

실제로 녹화 중에 터진 적이 있다.

**빼는 것과 남기는 것**

    빼기  motions  재생할 파일은 요청마다 이름으로 받는다 · 재생 등록 자체는
                   모션축 설정 파일 안에 있어 그쪽 해시가 이미 잡는다
    빼기  layers   스튜디오가 제 안에서 쓰는 자료다 · 노드끼리 맞출 것이 없다
    남김  motor_axes            축 구성이 바뀌면 모든 노드의 전제가 달라진다
    남김  motion_axis_matching  노드에게 보내는 바로 그것
"""

import pytest

from motion_web_bridge.project_repository import (
    SHARED_CONTEXT_CATEGORIES,
    ProjectRepository,
)

MOTOR = 'masters: []\n'
MAPPING = "file_id: m.yaml\nname: m\nmotion_file_id: 모션.json\nmappings: []\n"
#: 진짜 모션 파일과 같은 모양 · 머리줄 + 프레임 · 저장소가 형식을 검사한다
MOTION = (
    '{"type": "motion_header", "rotation_mode": "relative",'
    ' "rotation_unit": "deg", "fields": ["frame", "time_sec", "id", "value"]}\n'
    '[1,0.02,"1-1",0.0]\n'
)
LAYER = '{"layer_id": "a", "points": []}\n'


@pytest.fixture
def project(tmp_path):
    repo = ProjectRepository(tmp_path / 'motion_projects')
    created = repo.create_project('시험')
    project_id = created['project']['project_id']
    files = {
        'motor_axes': ('motor.yaml', MOTOR),
        'motion_axis_matching': ('m.yaml', MAPPING),
        'motions': ('모션.json', MOTION),
        'layers': ('layer.json', LAYER),
    }
    for category, (name, body) in files.items():
        repo.import_text(project_id, category, name, body)
        repo.set_active(project_id, category, name)
    return repo, project_id, tmp_path / 'motion_projects' / project_id


def _context_id(repo, project_id):
    return repo.execution_context(project_id)['context_id']


def _touch(root, category, name, body):
    (root / category / name).write_text(body, encoding='utf-8')


# --------------------------------------------------------------------------- #
# 흔들리면 안 되는 것
# --------------------------------------------------------------------------- #

def test_saving_a_layer_does_not_move_the_context(project):
    """스튜디오 편집이 네 노드를 흔들면 안 된다 · 녹화 중이면 사고다."""
    repo, project_id, root = project
    before = _context_id(repo, project_id)

    _touch(root, 'layers', 'layer.json', '{"layer_id": "a", "points": [1, 2, 3]}\n')

    assert _context_id(repo, project_id) == before


def test_a_changed_motion_file_does_not_move_the_context(project):
    """재생할 파일은 요청마다 이름으로 받는다 · 컨텍스트가 알 필요가 없다."""
    repo, project_id, root = project
    before = _context_id(repo, project_id)

    _touch(root, 'motions', '모션.json', MOTION + '[2,0.04,"1-1",1.0]\n')

    assert _context_id(repo, project_id) == before


# --------------------------------------------------------------------------- #
# 반드시 흔들려야 하는 것
# --------------------------------------------------------------------------- #

def test_a_changed_mapping_moves_the_context(project):
    """노드에게 보내는 바로 그 파일이다 · 안 알리면 옛 매핑으로 돈다."""
    repo, project_id, root = project
    before = _context_id(repo, project_id)

    _touch(root, 'motion_axis_matching', 'm.yaml', MAPPING.replace('모션.json', '다른.json'))

    assert _context_id(repo, project_id) != before


def test_a_changed_motor_config_moves_the_context(project):
    """축 구성이 바뀌면 모든 노드의 전제가 달라진다."""
    repo, project_id, root = project
    before = _context_id(repo, project_id)

    _touch(root, 'motor_axes', 'motor.yaml', MOTOR + '# 바뀜\n')

    assert _context_id(repo, project_id) != before


def test_choosing_another_mapping_moves_the_context(project):
    """내용뿐 아니라 **어느 파일인가**도 컨텍스트다."""
    repo, project_id, root = project
    before = _context_id(repo, project_id)

    repo.import_text(project_id, 'motion_axis_matching', 'm2.yaml', MAPPING)
    repo.set_active(project_id, 'motion_axis_matching', 'm2.yaml')

    assert _context_id(repo, project_id) != before


# --------------------------------------------------------------------------- #
# 목록 자체
# --------------------------------------------------------------------------- #

def test_the_shared_list_is_exactly_these_two():
    """늘리려면 「노드끼리 맞출 것인가」를 먼저 물어야 한다."""
    assert SHARED_CONTEXT_CATEGORIES == ('motor_axes', 'motion_axis_matching')


def test_the_full_file_list_is_still_reported(project):
    """ID 만 좁혔다 · 화면과 서비스는 네 종류를 다 본다."""
    repo, project_id, _ = project

    files = repo.execution_context(project_id)['files']

    assert set(files) >= {'motor_axes', 'motion_axis_matching', 'motions', 'layers'}


def test_a_missing_shared_file_is_still_reported(project):
    """설정이 빠진 것은 여전히 잡아야 한다."""
    repo, project_id, root = project
    (root / 'motion_axis_matching' / 'm.yaml').unlink()

    context = repo.execution_context(project_id)

    assert 'motion_axis_matching' in context['missing']
    assert context['configuration_complete'] is False
