"""매핑 줄이 가리키는 모터가 이 PC 에 있는가 · §6-141

로보티즈 2축을 뗐는데 모션축 설정 화면은 세 줄 다 `ok` 라고 했다 · 정작
재생은 그 두 축을 건너뛴다(§6-139) · 화면과 실제가 달랐다.

판단은 **브리지가** 한다 · 매핑 검사를 하는 `motion_mapping_manager` 는 모터
상태를 받지 않는 노드라 무엇이 달려 있는지 모른다 · 브리지만 안다.

**끄지는 않는다** · 자동으로 `enabled: false` 로 바꾸면 모르는 사이 설정이
바뀌고, 모터를 다시 달았을 때 손으로 되켜야 한다 · 줄은 그대로 두고 말만
한다 · 다시 달면 저절로 조용해진다.
"""

import time

from motion_web_bridge.bridge_node import MotionWebBridge


SERVO_REF = 'ac_servo:master:0:alias:103'
DXL_3_REF = 'dynamixel:port:%2Fdev%2FttyUSB0:id:3'


def _motor(kind, number):
    if kind == 'dynamixel':
        return {
            'motor_type': 'dynamixel', 'bus_id': number,
            'serial_port': '/dev/ttyUSB0',
        }
    return {
        'motor_type': 'ac_servo', 'alias': number,
        'ethercat_master_index': 0,
    }


def _bridge(present, *, received_ago_sec=0.0):
    import threading
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._lock = threading.Lock()
    bridge._motion_state = {'motors': [_motor(*item) for item in present]}
    bridge._motion_state_received_at = time.time() - received_ago_sec
    return bridge


def _result(refs):
    return {
        'mapping': {
            'mappings': [
                {'motion_id': f'1-{index + 1}', 'enabled': True, 'motor_ref': ref}
                for index, ref in enumerate(refs)
            ],
        },
        'validation': {
            'valid': True,
            'errors': [],
            'warnings': [],
            'rows': {
                f'1-{index + 1}': {'status': 'ok', 'messages': []}
                for index in range(len(refs))
            },
        },
    }


def test_every_motor_present_stays_quiet():
    bridge = _bridge([('ac_servo', 103), ('dynamixel', 3)])
    result = _result([SERVO_REF, DXL_3_REF])

    bridge._note_missing_motors(result)

    rows = result['validation']['rows']
    assert [row['status'] for row in rows.values()] == ['ok', 'ok']
    assert result['validation']['warnings'] == []


def test_a_removed_motor_is_reported_on_its_row():
    bridge = _bridge([('ac_servo', 103)])
    result = _result([SERVO_REF, DXL_3_REF])

    bridge._note_missing_motors(result)

    rows = result['validation']['rows']
    assert rows['1-1']['status'] == 'ok'
    assert rows['1-2']['status'] == 'warning'
    assert '모터축 설정에 없습니다' in ' '.join(rows['1-2']['messages'])
    assert any('1-2' in text for text in result['validation']['warnings'])


def test_a_removed_motor_does_not_make_the_mapping_invalid():
    """저장까지 막으면 모터를 다시 달 때까지 아무것도 못 고친다."""
    bridge = _bridge([('ac_servo', 103)])
    result = _result([SERVO_REF, DXL_3_REF])

    bridge._note_missing_motors(result)

    assert result['validation']['valid'] is True
    assert result['validation']['errors'] == []


def test_the_row_is_not_switched_off():
    bridge = _bridge([('ac_servo', 103)])
    result = _result([SERVO_REF, DXL_3_REF])

    bridge._note_missing_motors(result)

    assert result['mapping']['mappings'][1]['enabled'] is True


def test_an_old_motor_reading_raises_no_false_alarm():
    """모터 상태가 끊겼으면 「없다」고 하지 않는다 · 없는 문제를 만들지 않는다."""
    bridge = _bridge([('ac_servo', 103)], received_ago_sec=10.0)
    result = _result([SERVO_REF, DXL_3_REF])

    bridge._note_missing_motors(result)

    assert [row['status'] for row in result['validation']['rows'].values()] == ['ok', 'ok']


def test_a_legacy_motor_ref_still_matches():
    """옛 이름으로 저장된 매핑도 찾는다 · 없는 모터라고 하면 안 된다."""
    bridge = _bridge([('ac_servo', 103)])
    result = _result(['ac_servo:alias:103'])

    bridge._note_missing_motors(result)

    assert result['validation']['rows']['1-1']['status'] == 'ok'


def test_the_mapping_load_actually_runs_the_check():
    """검사를 만들어 놓고 부르지 않으면 아무 일도 안 난다."""
    from pathlib import Path
    source = (
        Path(__file__).resolve().parents[1]
        / 'motion_web_bridge' / 'bridge_node.py'
    ).read_text(encoding='utf-8')
    start = source.index('def load_motion_mapping(')
    body = source[start:source.index('\n    def ', start)]
    assert '_note_missing_motors(result)' in body, '매핑을 읽을 때 검사하지 않는다'
