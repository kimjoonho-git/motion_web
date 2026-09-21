"""한 번 저장하고 나서 또 저장할 수 있어야 한다 · §6-242

전에는 저장할 때마다 개정 번호가 **두 값**으로 갈렸다.

    저장 응답이 준 값   메모리 객체로 셈 · `midi_banks` 가 끼어 있음
    다음 저장 때 비교할 값  파일에서 읽어 셈 · `midi_banks` 는 정규화가 버림

화면은 앞의 값을 기준으로 들고 가는데 서버는 뒤의 값과 비교하니, **두 번째
저장이 언제나 「모션축 설정 저장 충돌」로 거부됐다** · 사람은 편집을 버리는
수밖에 없었다.

실측: 저장 응답 87a61175… / 실제 파일 b36833a6…
"""

from motion_runtime.motion_mapping_manager import MotionMappingManager


BASE = {
    'file_id': 'show_mapping.yaml',
    'name': 'show_mapping',
    'motion_file_id': '',
    'mappings': [{'motion_id': '1-1', 'enabled': True, 'motor_axis': 0}],
}

BANKS = {
    'version': 1,
    'active_bank_id': 'bank_1',
    'banks': [{'bank_id': 'bank_1', 'name': 'Bank 1'}],
}


def test_midi_banks_do_not_move_the_mapping_revision():
    without = MotionMappingManager._mapping_revision(dict(BASE))
    with_banks = MotionMappingManager._mapping_revision({**BASE, 'midi_banks': BANKS})

    assert without == with_banks


def test_changing_only_the_midi_banks_keeps_the_same_revision():
    one = MotionMappingManager._mapping_revision({**BASE, 'midi_banks': BANKS})
    other = MotionMappingManager._mapping_revision({
        **BASE,
        'midi_banks': {**BANKS, 'active_bank_id': 'bank_2'},
    })

    assert one == other


def test_the_playback_registration_still_does_not_move_it():
    # §6-160 에서 정한 것 · 재생 등록은 모션축 설정이 아니다
    one = MotionMappingManager._mapping_revision(dict(BASE))
    other = MotionMappingManager._mapping_revision({**BASE, 'motion_file_id': 'show.json'})

    assert one == other


def test_changing_the_motion_axis_rows_does_move_it():
    one = MotionMappingManager._mapping_revision(dict(BASE))
    other = MotionMappingManager._mapping_revision({
        **BASE,
        'mappings': [{'motion_id': '1-1', 'enabled': True, 'motor_axis': 1}],
    })

    assert one != other
