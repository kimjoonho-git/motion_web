"""막은 것과 고장 난 것을 구별해 남긴다 · §6-174

**정상 거부를 스택 추적과 함께 ERROR 로 남기면 진짜 고장이 묻힌다.**

이 저장소에서 `ValueError` 는 「사용자에게 보일 거부」의 뜻으로 쓴다 ·
그 문구는 이미 사람이 읽을 말이다.

    포인트 곡선을 만들 Motion ID를 하나만 선택하세요
    현재 프로젝트 세대와 다른 요청을 폐기했습니다
    잠긴 레이어는 삭제할 수 없습니다

전에는 이런 것도 열넉 줄짜리 스택 추적과 함께 ERROR 로 남았다 · 이틀치
로그를 세어 보니 ERROR·WARN 의 **절반 이상**이 그것이었다.

    53회  스튜디오 에디터 · 포인트 선택 안내
    46회  모션축 설정 · 세대 불일치
    17회  모션 실행 · 상태 조회 경합

그래서 2026-09-18 에 세 대를 세운 진짜 버그
(`sleep length must be non-negative`)를 찾는 데 반나절이 걸렸다.

**아무것도 숨기지 않는다** · 거부도 남긴다 · 다만 조용히 남긴다.
"""

import pytest

from motion_common import command_router


class _Logger:
    def __init__(self):
        self.errors = []
        self.warns = []

    def error(self, message):
        self.errors.append(message)

    def warn(self, message):
        self.warns.append(message)


def _fail(exc):
    """실제 호출부와 같은 모양 · `except` 안에서 부른다 (스택이 있어야 한다)."""
    logger = _Logger()
    try:
        raise exc
    except Exception as caught:
        command_router.log_command_failure(logger, '어떤 명령 실패', caught)
    return logger


def test_a_deliberate_refusal_is_a_warning():
    logger = _fail(ValueError('잠긴 레이어는 삭제할 수 없습니다'))

    assert logger.errors == []
    assert logger.warns == ['어떤 명령 실패: 잠긴 레이어는 삭제할 수 없습니다']


def test_a_refusal_keeps_the_sentence_meant_for_the_person():
    """문구가 곧 안내다 · 줄이거나 바꾸면 안 된다."""
    message = '포인트 곡선을 만들 Motion ID를 하나만 선택하세요'
    logger = _fail(ValueError(message))

    assert message in logger.warns[0]


def test_a_refusal_carries_no_stack():
    """어디서 났는지는 도움이 안 된다 · 로그만 열넉 줄씩 길어진다."""
    logger = _fail(ValueError('현재 프로젝트 세대와 다른 요청을 폐기했습니다'))

    assert 'Traceback' not in logger.warns[0]
    assert 'File "' not in logger.warns[0]


@pytest.mark.parametrize('exc', [
    TypeError('None 을 더했다'),
    KeyError('없는 열쇠'),
    AttributeError('없는 이름'),
    ZeroDivisionError('0 으로 나눔'),
    RuntimeError('그룹 실행 실패'),
])
def test_a_real_fault_is_an_error_with_its_stack(exc):
    """진짜 고장은 어디서 났는지가 전부다 · 크게 남긴다."""
    logger = _fail(exc)

    assert logger.warns == []
    assert len(logger.errors) == 1
    assert 'Traceback' in logger.errors[0]
    assert '어떤 명령 실패' in logger.errors[0]


def test_the_sleep_bug_would_still_have_been_loud():
    """2026-09-18 에 세 대를 세운 그 버그 · 묻히면 안 된다."""
    logger = _fail(TypeError('sleep length must be non-negative'))

    assert logger.errors and 'Traceback' in logger.errors[0]


def test_nothing_is_swallowed():
    """조용히 하는 것과 숨기는 것은 다르다 · 거부도 반드시 한 줄 남는다."""
    for exc in (ValueError('막음'), RuntimeError('고장')):
        logger = _fail(exc)
        assert len(logger.errors) + len(logger.warns) == 1


def test_every_command_boundary_uses_the_rule():
    """네 곳이 같은 모양이었다 · 한 곳만 옛 방식으로 돌아가면 다시 시끄러워진다."""
    from pathlib import Path

    workspace = Path(__file__).resolve().parents[3]
    boundaries = [
        'src/motion_control_studio/motion_studio/motion_studio/editor_node.py',
        'src/motion_control_studio/motion_studio/motion_studio/studio_node.py',
        'src/motion_control_studio/motion_control/motion_runtime/motion_runtime/motion_run_manager.py',
        'src/motion_control_studio/motion_control/motion_runtime/motion_runtime/motion_mapping_manager.py',
    ]
    missing = [
        path for path in boundaries
        if 'log_command_failure' not in (workspace / path).read_text(encoding='utf-8')
    ]
    assert missing == [], f'옛 방식으로 남기는 곳이 있습니다: {missing}'
