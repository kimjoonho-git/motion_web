"""설정을 파일에 적는 일은 모터를 기다리지 않는다 · §6-269

`automation_configure` 가 실행 명령들과 같은 목록에 있었다 · 그래서 모터가
준비되지 않으면 반복 방식 저장이 이렇게 거절됐다.

    현재 프로젝트 실행 컨텍스트 적용 대기 중입니다

실측으로 모터 서비스를 멈춘 채 세 번 넣어 세 번 다 실패했다 · 그 사이에도
스튜디오 레이어 저장·모션축 설정 읽기·MIDI 뱅크 편집은 전부 통과했다 ·
같은 성격인데 하나만 막혀 있었다.

반복 방식을 고르는 것은 `motion_automation.json` 에 글자를 적는 일이고
모터를 건드리지 않는다.
"""

from motion_runtime.motion_run_manager import MotionRunManager

GATED = MotionRunManager.COMMANDS_REQUIRING_CONTEXT


def test_saving_the_repeat_setting_is_not_gated():
    assert 'automation_configure' not in GATED


def test_the_commands_that_move_motors_are_still_gated():
    for command in ('initialize', 'start', 'group_start_at', 'group_initialize_at'):
        assert command in GATED, command


def test_the_check_before_running_is_still_gated():
    """돌기 전 점검은 실행 컨텍스트가 있어야 뜻이 있다."""
    assert 'check' in GATED


def test_nothing_else_slipped_in():
    assert GATED == frozenset({
        'check',
        'initialize',
        'start',
        'group_prepare',
        'group_start_at',
        'group_initialize_at',
    })
