"""프로젝트 폴더를 들여보내는 문은 하나다 · §6-194

**같은 검사가 네 벌 복사돼 있었고, 둘이 틀렸다.**

    workspace_session      (스튜디오)      ❌ 푼 경로와 안 푼 경로를 견줌
    motion_mapping_manager (모션축 매핑)    ❌ 같은 실수
    motion_run_manager     (모션 실행)      ✅
    midi_control_node      (MIDI)          ✅

틀린 둘은 이랬다.

    project_dir = (projects_dir / project_id).resolve()   ← 풀었다
    if project_dir.parent != projects_dir:                ← 안 풀었다

작업공간 경로에 심링크가 하나라도 끼면 이 둘은 **모든 프로젝트를 거부**한다 ·
모션 실행과 MIDI 는 멀쩡히 도는데 스튜디오와 매핑만 「통합 프로젝트를 찾을
수 없습니다」를 낸다.

**반쪽만 도는 것이 가장 나쁘다** · 전부 안 되면 바로 알아채는데, 반만 되면
「스튜디오가 이상하다」로 며칠을 헤맨다 · 게다가 문구가 「없다」라서 경로
문제라는 생각이 안 든다.

지금 세 대는 경로에 심링크가 없어 무사하다 · `~/ros2_ws` 를 옮기거나 홈이
심링크인 PC 에 깔면 그때 터진다.
"""

from pathlib import Path

import pytest

from motion_common.paths import INVALID_PROJECT_ID, project_dir_for


@pytest.fixture
def projects(tmp_path):
    """진짜 폴더 하나와, 그것을 가리키는 심링크 하나."""
    real = tmp_path / 'real' / 'motion_projects'
    (real / '플로팅헤드-1').mkdir(parents=True)
    (real / '플로팅헤드-1' / 'project.json').write_text('{}', encoding='utf-8')
    (tmp_path / 'link').symlink_to(tmp_path / 'real')
    return real, tmp_path / 'link' / 'motion_projects'


# --------------------------------------------------------------------------- #
# 심링크 · 이것 때문에 반쪽만 돌았다
# --------------------------------------------------------------------------- #

def test_a_symlinked_workspace_still_finds_the_project(projects):
    """**이것이 그 버그다** · 틀린 두 곳은 여기서 거부했다."""
    _real, linked = projects

    found = project_dir_for(linked, '플로팅헤드-1')

    assert found.name == '플로팅헤드-1'
    assert (found / 'project.json').is_file()


def test_both_paths_lead_to_the_same_folder(projects):
    """심링크로 가든 진짜 경로로 가든 같은 곳이어야 한다."""
    real, linked = projects

    assert project_dir_for(real, '플로팅헤드-1') == project_dir_for(linked, '플로팅헤드-1')


# --------------------------------------------------------------------------- #
# 밖으로 나가는 이름은 막는다
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize('bad', [
    '../다른곳',
    '..',
    'a/b',
    'a\\b',
    '.숨김',
    '',
    '   ',
    None,
])
def test_a_name_that_is_not_a_name_is_refused(projects, bad):
    real, _linked = projects

    with pytest.raises(ValueError, match=INVALID_PROJECT_ID):
        project_dir_for(real, bad)


def test_a_project_outside_the_folder_is_refused(tmp_path):
    """이름은 멀쩡한데 심링크가 밖을 가리키는 경우 · 마지막 빗장이다."""
    projects = tmp_path / 'motion_projects'
    projects.mkdir()
    outside = tmp_path / '밖' / '남의것'
    outside.mkdir(parents=True)
    (outside / 'project.json').write_text('{}', encoding='utf-8')
    (projects / '남의것').symlink_to(outside)

    with pytest.raises(ValueError, match='찾을 수 없습니다'):
        project_dir_for(projects, '남의것')


def test_a_folder_without_project_json_is_not_a_project(projects):
    real, _linked = projects
    (real / '빈폴더').mkdir()

    with pytest.raises(ValueError, match='찾을 수 없습니다'):
        project_dir_for(real, '빈폴더')


def test_the_name_is_trimmed(projects):
    """화면에서 온 값에 공백이 붙는다 · 네 곳이 각자 `.strip()` 했었다."""
    real, _linked = projects

    assert project_dir_for(real, '  플로팅헤드-1  ').name == '플로팅헤드-1'


# --------------------------------------------------------------------------- #
# 아무도 제 검사를 따로 들고 있지 않다
# --------------------------------------------------------------------------- #

SRC = Path(__file__).resolve().parents[2]   # .../ros2_ws/src

CALLERS = [
    'motion_control_studio/motion_studio/motion_studio/workspace_session.py',
    'motion_control_studio/motion_control/motion_runtime/motion_runtime/motion_mapping_manager.py',
    'motion_control_studio/motion_control/motion_runtime/motion_runtime/motion_run_manager.py',
    'motion_control_studio/motion_control/midi_control/midi_control/midi_control_node.py',
]


@pytest.mark.parametrize('name', CALLERS)
def test_nobody_keeps_a_copy(name):
    source = (SRC / name).read_text(encoding='utf-8')

    assert 'project_dir_for(' in source, '공용 문을 쓰지 않습니다'
    assert 'project_dir.parent' not in source, (
        '제 검사를 따로 들고 있습니다 · 네 벌 중 둘이 틀렸던 그 검사입니다'
    )
    assert "'유효한 통합 프로젝트 ID가 필요합니다'" not in source
