"""같은 상황에는 같은 말을 한다 · §6-169

**같은 두 줄이 여덟 곳에 붙어 있었다.**

    project_id = repository.selected_project_id()
    if not project_id:
        raise ValueError('통합 프로젝트를 먼저 선택하세요')

같은 말을 여덟 번 적으면 한 번은 다르게 적는다 · 실제로 스튜디오만
「왼쪽에서 통합 프로젝트를…」 이라고 해서, 같은 상황인데 화면마다 다른 말이
나왔다 · 사용자는 다른 문제인 줄 안다.

문구의 주인은 `motion_common.paths` 다 · 「어느 프로젝트인가」의 주인은 웹
브리지의 저장소지만, 그것을 못 가져오는 **순수 모듈**도 같은 말을 해야 하므로
양쪽이 볼 수 있는 곳에 둔다.
"""

import re
from pathlib import Path

import pytest

from motion_common.paths import NO_PROJECT_SELECTED

WORKSPACE = Path(__file__).resolve().parents[4]
BRIDGE_DIR = WORKSPACE / 'src/motion_web/web_bridge/motion_web_bridge'


def _sources():
    return sorted(
        path for path in BRIDGE_DIR.rglob('*.py')
        if '__pycache__' not in path.parts
    )


def test_nobody_writes_the_sentence_by_hand():
    """글자로 적힌 곳이 있으면 언젠가 한쪽만 고쳐진다."""
    offenders = []
    for path in _sources():
        text = path.read_text(encoding='utf-8')
        for match in re.finditer(r"'[^']*통합 프로젝트를 먼저 선택하세요[^']*'", text):
            line = text[:match.start()].count('\n') + 1
            offenders.append(f'{path.name}:{line}')

    assert offenders == [], (
        '안내문을 직접 적고 있습니다 · motion_common.paths.NO_PROJECT_SELECTED 를 '
        f'쓰세요:\n  ' + '\n  '.join(offenders)
    )


def test_the_check_and_the_message_travel_together():
    """검사 따로 문구 따로 적으면 또 갈라진다 · 저장소가 둘을 함께 한다."""
    text = (BRIDGE_DIR / 'project_repository.py').read_text(encoding='utf-8')
    start = text.index('def require_selected_project_id(')
    body = text[start:text.index('\n    def ', start)]

    assert 'self.selected_project_id()' in body
    assert 'raise ValueError(NO_PROJECT_SELECTED)' in body


def test_nobody_re_implements_the_requirement():
    """`selected_project_id()` 뒤에 손으로 막는 곳이 다시 생기면 안 된다."""
    pattern = re.compile(
        r'selected_project_id\(\)\s*\n\s*if not \w+:\s*\n\s*raise ValueError',
    )
    offenders = [
        # 주인 자신은 예외다 · 검사와 문구를 함께 하는 곳이 바로 여기다
        path.name for path in _sources()
        if path.name != 'project_repository.py'
        and pattern.search(path.read_text(encoding='utf-8'))
    ]
    assert offenders == [], (
        '고른 프로젝트 확인을 직접 하고 있습니다 · '
        f'require_selected_project_id() 를 쓰세요: {offenders}'
    )


def test_the_owner_lives_where_pure_modules_can_reach_it():
    """순수 모듈은 웹 브리지를 가져올 수 없다 · 그래서 공용 커널에 둔다."""
    text = (BRIDGE_DIR / 'motion_file_analysis.py').read_text(encoding='utf-8')
    assert 'from motion_common.paths import NO_PROJECT_SELECTED' in text
    assert 'project_repository' not in text, (
        '순수 모듈이 저장소를 가져오면 순수성 검사가 깨집니다'
    )


@pytest.mark.parametrize('name', [
    'bridge_node.py',
    'motor_config_service.py',
    'motion_studio_sync.py',
    'motion_file_analysis.py',
])
def test_every_speaker_uses_the_same_name(name):
    text = (BRIDGE_DIR / name).read_text(encoding='utf-8')
    assert 'NO_PROJECT_SELECTED' in text, f'{name} 이 주인을 부르지 않습니다'


def test_the_sentence_itself_has_not_drifted():
    """문구가 바뀌면 사용법 문서와도 어긋난다 · 바꾸려면 여기도 같이 본다."""
    assert NO_PROJECT_SELECTED == '통합 프로젝트를 먼저 선택하세요'
