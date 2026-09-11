"""재생과 MIDI 가 서로 다른 축을 동시에 몬다 · §6-72

오버더빙의 바탕이다 · 녹화된 축은 재생이 몰고, 나머지 축은 MIDI 로 녹화한다.
전에는 최종 출력 전체에 주인이 하나라 재생이 돌면 MIDI 가 어느 축도 쓸 수 없었다.

노드를 띄우려면 rclpy 가 필요하므로 중재기와 소스 계약만 본다 ·
모터를 쓰는 확인은 사용자가 직접 한다.
"""

from pathlib import Path

from motion_supervisor.command_arbiter import CommandArbiter, CommandOwner

NODE = (
    Path(__file__).resolve().parents[1]
    / 'motion_supervisor' / 'supervisor_node.py'
).read_text(encoding='utf-8')


def _body(name: str) -> str:
    start = NODE.index(f'def {name}(')
    nxt = NODE.find('\n    def ', start)
    return NODE[start:nxt if nxt > 0 else len(NODE)]


def test_playback_claims_only_the_axes_it_commands():
    body = NODE[NODE.index('CommandOwner.PLAYBACK,'):][:400]
    assert 'axes=playback_axes' in body, '재생이 전체를 잡고 있다'


def test_commanded_axes_skips_untouched_slots():
    """슬롯이 0 인 축은 이번 명령이 건드리지 않는다 · 그 축은 MIDI 가 쓸 수 있다."""
    class Msg:
        number_of_target_interfaces = [1, 0, 2]
        controller_index = [0, 1, 2]

    from motion_supervisor.supervisor_node import MotionSupervisor
    assert MotionSupervisor._commanded_axes(Msg()) == [0, 2]


def test_midi_claims_only_its_own_axes():
    assert 'axes=commanded_axes' in NODE, 'MIDI 배치가 전체를 잡는다'
    assert 'axes=[axis]' in NODE, 'MIDI 단건이 전체를 잡는다'


def test_the_global_playback_gate_is_gone():
    """재생 중이면 MIDI 전체를 막던 시간 잠금이 없어야 한다."""
    assert 'motion playback is active' not in NODE, (
        '전역 재생 잠금이 남아 있다 · 축별 소유권이 판정해야 한다'
    )


def test_overdub_shape_works_end_to_end_in_the_arbiter():
    """녹화된 축 0 은 재생이, 빈 축 1 은 MIDI 가 · 같은 순간에."""
    arbiter = CommandArbiter()
    assert arbiter.acquire(CommandOwner.PLAYBACK, axes=[0], lease_sec=0.15)[0] is True
    assert arbiter.acquire(CommandOwner.MIDI, axes=[1], lease_sec=0.15)[0] is True
    # 재생이 쥔 축을 MIDI 가 가져가지는 못한다
    assert arbiter.acquire(CommandOwner.MIDI, axes=[0], lease_sec=0.15)[0] is False
