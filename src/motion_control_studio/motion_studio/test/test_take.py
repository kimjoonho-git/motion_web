"""테이크 · 한 번의 작업이 자기가 무엇인지 안다 · §6-80

전에는 그 자리를 공유 딕셔너리 안의 문자열 하나가 맡았다 · 문자열은 자기가
무엇인지 모른다. 실행 노드가 `running` 을 보내자 녹화 중이던 화면이 "미리보기
재생"으로 바뀌었고, 재생이 끝나는 순간 녹화까지 함께 끝났다 · §6-76

여기서 확인하는 것은 **그 일이 이제 불가능한가** 하나다.
"""

import threading

import pytest

from motion_studio.take import (
    StudioTake,
    StudioTakeBoard,
    take_status_fields,
)


class _Studio:
    """판이 무엇을 비추는지만 보기 위한 최소 대역."""

    def __init__(self):
        self._lock = threading.RLock()
        self.messages = []
        self._generation = 0

    def _operation_machine(self):
        studio = self

        class _Machine:
            def begin(self, state):
                if state not in {'idle', 'error'}:
                    raise ValueError('이미 무언가 돌고 있습니다')
                studio._generation += 1
                return studio._generation

        return _Machine()

    def _project_status_locked(self, message):
        self.messages.append(message)


def _board():
    return StudioTakeBoard(_Studio())


# --------------------------------------------------------------------- #
# 종류는 만들 때 정해지고 끝까지 그대로다
# --------------------------------------------------------------------- #

def test_a_recording_take_can_never_call_itself_playback():
    """이 한 줄이 §6-76 버그의 재발을 막는다.

    추가 녹화는 재생도 한다 · 그래서 실행 노드가 `running` 을 보낸다. 단계는
    옮겨지지만 **종류는 그대로**여야 한다.
    """
    board = _board()
    board.begin('overdub', '준비')
    assert board.state == 'initializing'

    board.advance('running', '재생 중')
    assert board.state == 'recording', '추가 녹화가 재생으로 둔갑했다'
    assert board.take.kind == 'overdub'


def test_there_is_no_way_to_set_a_state_directly():
    """상태를 인자로 받는 문이 없어야 한다 · 있으면 언젠가 누가 쓴다."""
    board = _board()
    board.begin('record', '준비')
    for name in ('set_state', 'set_status', 'force_state'):
        assert not hasattr(board, name), f'{name} 이 상태를 직접 바꿀 수 있다'
    with pytest.raises(ValueError):
        board.advance('playing', '재생')


def test_an_unknown_kind_or_phase_is_refused_at_the_door():
    with pytest.raises(ValueError, match='종류'):
        StudioTake('playback', 'running', 1, '')
    with pytest.raises(ValueError, match='단계'):
        StudioTake('record', 'playing', 1, '')


# --------------------------------------------------------------------- #
# 어떤 상태로 비치는가
# --------------------------------------------------------------------- #

@pytest.mark.parametrize('kind,phase,expected', [
    ('record', 'preparing', 'initializing'),
    ('record', 'countdown', 'initializing'),
    ('record', 'running', 'recording'),
    ('record', 'stopping', 'stopping'),
    ('overdub', 'running', 'recording'),
    ('preview', 'running', 'playing'),
    ('preview', 'countdown', 'initializing'),
    ('initialize', 'running', 'initializing'),
])
def test_the_state_follows_from_the_kind(kind, phase, expected):
    assert StudioTake(kind, phase, 1, '').state == expected


def test_a_board_with_no_take_rests():
    board = _board()
    assert board.state == 'idle'
    board.begin('preview', '준비')
    board.fail('실패')
    assert board.state == 'error'
    board.note_idle('프로젝트를 열었습니다')
    assert board.state == 'idle'


def test_stop_works_even_when_no_take_is_known():
    """노드가 다시 뜬 뒤에는 테이크를 모른 채 실행 노드만 돌 수 있다 ·
    그때도 사용자는 멈출 수 있어야 한다."""
    board = _board()
    board.begin_stop('정지 명령 전달 중')
    assert board.state == 'stopping'
    board.finish('정지 완료')
    assert board.state == 'idle'


def test_stop_keeps_the_kind_so_the_layer_still_gets_saved():
    """정지가 "녹화였는가" 를 테이크에게 묻는다 · 종류를 잃으면 레이어를
    저장하지 않고 끝난다."""
    board = _board()
    board.begin('overdub', '준비')
    board.advance('running', '녹화 중')
    board.begin_stop('정지 명령 전달 중')
    assert board.state == 'stopping'
    assert board.take.records is True


def test_two_takes_cannot_run_at_once():
    board = _board()
    board.begin('record', '준비')
    with pytest.raises(ValueError):
        board.begin('preview', '준비')


# --------------------------------------------------------------------- #
# 화면으로 나가는 필드도 테이크 하나에서 나온다
# --------------------------------------------------------------------- #

def test_status_fields_come_from_the_take_alone():
    spans = {'1-1': [(2.36, 8.92)]}
    overdub = StudioTake('overdub', 'running', 1, '', spans)
    fields = take_status_fields(overdub)
    assert fields['record_mode'] == 'overdub'
    assert fields['overdub_spans'] == {'1-1': [[2.36, 8.92]]}

    plain = StudioTake('record', 'running', 1, '', spans)
    assert take_status_fields(plain)['overdub_spans'] == {}, \
        '일반 녹화가 잠금 구간을 내보낸다'

    assert take_status_fields(StudioTake('preview', 'running', 1, ''))['record_mode'] is None

    resting = take_status_fields(None)
    assert resting['record_mode'] is None
    assert resting['overdub_spans'] == {}
    assert resting['elapsed_sec'] == 0.0 and resting['total_sec'] == 0.0


def test_the_take_carries_its_own_clock():
    """전에는 시간이 다섯 군데에 흩어져 있었고 **어느 게 진짜인지가 상태에
    달려 있었다** · 화면이 그걸 다시 조립하다 녹화 중의 축 길이를 놓쳤다 · §6-79"""
    take = StudioTake('overdub', 'running', 1, '').timed(3.4, 8.92)
    fields = take_status_fields(take)
    assert fields['elapsed_sec'] == 3.4
    assert fields['total_sec'] == 8.92

    # 단계 진행은 테이크 시계와 별개다 · 초기 이동 3.2/5.0 초 같은 것
    staged = take.phase_timed(3.2, 5.0)
    assert take_status_fields(staged)['phase_elapsed_sec'] == 3.2
    assert take_status_fields(staged)['elapsed_sec'] == 3.4, '단계 진행이 테이크 시계를 덮었다'


def test_the_board_clock_survives_a_phase_change():
    """단계가 바뀌어도 시계는 이어진다 · 종류가 안 바뀌듯이."""
    board = StudioTakeBoard(_Studio())
    board.studio._status = {}
    board.begin('overdub', '준비')
    board.tick(3.4, 8.92)
    board.advance('stopping', '정지 중')
    assert board.take.elapsed_sec == 3.4
    assert board.take.total_sec == 8.92


# --------------------------------------------------------------------- #
# 상태 이름은 한 곳에서만 정해진다 · §6-88
# --------------------------------------------------------------------- #

def test_the_state_names_are_exactly_what_takes_can_produce():
    """손으로 적은 목록은 실제와 갈린다 · 테이크가 낼 수 있는 것에서 뽑는다."""
    from motion_studio.take import KINDS, PHASES, RESTING_STATES, STATES

    produced = {StudioTake(kind, phase, 1, '').state
                for kind in KINDS for phase in PHASES}
    assert STATES == produced | RESTING_STATES


def test_the_frontend_knows_the_same_state_names():
    """같은 이름을 파이썬 50곳과 JS 54곳이 맨 문자열로 들고 있었다 · 한쪽에
    새 이름이 생기면 다른 쪽은 모른 채로 돌고, 알 방법도 없었다.

    주기(`motion_common.timing`)를 한 곳에서 정의하는 것과 같은 방식이다.
    """
    import re
    from pathlib import Path

    from motion_studio.take import STATES, STATUS_PHASES

    source = (
        Path(__file__).resolve().parents[3]
        / 'motion_web' / 'web_ui' / 'static' / 'js'
        / 'motion_studio_constants.js'
    ).read_text(encoding='utf-8')

    def listed(name):
        block = re.search(
            rf'export const {name} = Object\.freeze\(\[(.*?)\]', source, re.S,
        )
        assert block, f'{name} 를 찾지 못했다'
        return set(re.findall(r"'([a-z]+)'", block.group(1)))

    assert listed('MOTION_STUDIO_STATES') == set(STATES), (
        '화면과 서버가 아는 상태 이름이 다르다'
    )
    assert listed('MOTION_STUDIO_STATUS_PHASES') | set(STATES) == set(STATUS_PHASES)


def test_every_state_the_studio_branches_on_is_a_known_name():
    """오타 하나면 그 가지는 영영 안 탄다 · 조용히 안 도는 것이 제일 나쁘다."""
    import re
    from pathlib import Path

    from motion_studio.take import STATUS_PHASES

    root = Path(__file__).resolve().parents[1] / 'motion_studio'
    run_node_states = {
        'preparing', 'initialized', 'running', 'verifying', 'completed', 'stopped',
    }
    known = set(STATUS_PHASES) | run_node_states | {'preparing', 'record', 'overdub'}

    for path in sorted(root.glob('*.py')):
        body = path.read_text(encoding='utf-8')
        for match in re.finditer(
            r"_status(?:\.get\('state'\)|\['state'\])\s*(?:==|!=)\s*'([a-z_]+)'", body,
        ):
            assert match.group(1) in known, f'{path.name}: 모르는 상태 {match.group(1)}'
