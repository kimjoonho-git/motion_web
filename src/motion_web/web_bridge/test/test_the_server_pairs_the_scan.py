"""검색 결과와 프로젝트 축은 **서버가** 합친다 · §6-216

전에는 화면이 합쳤다 · 같은 판단이 서버에도 있었고 규칙이 미묘하게 달라서
오늘 사고 넷이 났다.

    모델 이름   화면 `XM540-W270` · 서버 `XM540-W270-R`
    축 이름     alias 가 없을 때 화면만 `rotary` 를 봄 → 저장하면 선택이 풀림
    검색 판정   화면 `scan_complete` · 서버 `success` → 성공을 실패로 띄움

여기서 한 번만 판단한다 · 화면은 받아서 그린다.
"""

import pytest

from motion_web_bridge.scan_axis_rows import axis_rows_from_scan


def _servo(axis=0, alias=103, slave=0, serial=402982152, model='MADLN05BE'):
    return {
        'id': f'ac_servo_ethercat_master_0_alias_{alias}',
        'axis': axis,
        'name': f'alias {alias}',
        'enabled': True,
        'transport': 'ethercat',
        'motor_type': 'ac_servo',
        'identity': {
            'ethercat_master_index': 0,
            'ethercat_alias': alias,
            'slave_position': slave,
            'serial_number': serial,
            'vendor_id': 1647,
            'product_code': 1614282756,
        },
        'profile': {'driver_model': model},
        'config': {'controller_index': axis, 'ethercat_master_index': 0,
                   'alias': alias, 'position': slave},
    }


def _dynamixel(axis=2, bus_id=3, port='/dev/ttyUSB0', model='XM540-W150'):
    return {
        'id': f'dynamixel_serial_port_{port}_id_{bus_id}',
        'axis': axis,
        'name': f'ID {bus_id}',
        'enabled': True,
        'transport': 'serial',
        'motor_type': 'dynamixel',
        'identity': {'bus_id': bus_id, 'node_id': bus_id, 'serial_port': port},
        'profile': {'driver_model': model},
        'config': {'controller_index': axis, 'bus_id': bus_id, 'serial_port': port},
    }


def _slave(alias=103, slave=0, serial=402982152, model='MADLN05BE', rotary=None):
    return {
        'master_index': 0,
        'slave_position': slave,
        'ethercat_alias': alias,
        'rotary_alias': rotary,
        'serial_number': serial,
        'vendor_id': 1647,
        'product_code': 1614282756,
        'order_number': model,
        'device_name': model,
        'device_state': 'PREOP',
        'identity_source': 'physical_sii',
    }


def _device(bus_id=3, port='/dev/ttyUSB0', model_number=1130, model_name='XM540-W150'):
    return {'id': bus_id, 'port': port, 'model_number': model_number,
            'model_name': model_name, 'firmware_version': 50}


def _scan(slaves=None, devices=None, ethercat=True, dynamixel=True,
          masters=(0, 1), answered=(0, 1)):
    """검색 응답 · `masters` 는 대답한 Master 를 적어 둔다 · §6-220"""
    return {
        'ethercat_scan': (
            {
                'available': True,
                'slaves': slaves or [],
                'masters': [
                    {'master_index': index, 'complete': index in answered}
                    for index in masters
                ],
            }
            if ethercat else {'skipped': True}
        ),
        'dynamixel_scan': (
            {'available': True, 'error': '', 'devices': devices or []}
            if dynamixel else {'skipped': True}
        ),
    }


def _rows(motors, scan):
    return axis_rows_from_scan({'motors': motors}, scan)


# --------------------------------------------------------------------------- #
# 짝 맞추기 · 규칙은 화면이 쓰던 것 그대로다
# --------------------------------------------------------------------------- #

def test_a_servo_is_found_by_its_serial_number():
    out = _rows([_servo()], _scan(slaves=[_slave()]))

    assert out['axes'][0]['state'] == 'matched'
    assert out['axes'][0]['confirmation_required'] is False
    assert out['new_devices'] == []


def test_a_servo_moved_to_another_slave_position_is_still_found():
    """제조번호가 같으면 배선이 바뀌어도 같은 서보다."""
    out = _rows([_servo(slave=0)], _scan(slaves=[_slave(slave=3)]))

    assert out['axes'][0]['state'] == 'matched'
    assert out['axes'][0]['changed'] == ['slave_position']


def test_a_servo_without_a_stored_serial_is_found_by_alias():
    motor = _servo()
    motor['identity']['serial_number'] = None
    motor['config'].pop('serial_number', None)

    out = _rows([motor], _scan(slaves=[_slave()]))

    assert out['axes'][0]['state'] == 'matched'


def test_a_servo_matched_only_by_position_asks_for_confirmation():
    """장치가 아니라 배선 순서로 맞은 것이다 · 사람이 한 번 확인해야 한다."""
    motor = _servo(alias=0)
    motor['identity'].update({'serial_number': None, 'ethercat_alias': 0})
    motor['config']['alias'] = 0

    out = _rows([motor], _scan(slaves=[_slave(alias=0, serial=None)]))

    assert out['axes'][0]['state'] == 'matched'
    assert out['axes'][0]['confirmation_required'] is True


def test_two_axes_on_the_same_position_are_not_paired_by_guessing():
    a, b = _servo(axis=0, alias=0), _servo(axis=1, alias=0)
    for motor in (a, b):
        motor['identity'].update({'serial_number': None, 'ethercat_alias': 0})
        motor['config']['alias'] = 0
    b['id'] = 'ac_servo_ethercat_master_0_slave_0_b'

    out = _rows([a, b], _scan(slaves=[_slave(alias=0, serial=None)]))

    assert [row['state'] for row in out['axes']] == ['missing', 'missing']
    assert len(out['new_devices']) == 1


def test_a_dynamixel_is_found_by_port_and_id():
    out = _rows([_dynamixel()], _scan(devices=[_device()]))

    assert out['axes'][0]['state'] == 'matched'


def test_a_dynamixel_on_another_port_is_a_different_motor():
    out = _rows([_dynamixel(port='/dev/ttyUSB0')],
                _scan(devices=[_device(port='/dev/ttyUSB1')]))

    assert out['axes'][0]['state'] == 'missing'
    assert len(out['new_devices']) == 1


# --------------------------------------------------------------------------- #
# 모델 이름 · 서버와 화면이 같은 글자를 쓴다
# --------------------------------------------------------------------------- #

def test_a_dynamixel_model_gets_the_name_the_file_uses():
    """**이것이 그 버그다** · 검색은 `XM540-W270`, 파일은 `XM540-W270-R` 이었다."""
    motor = _dynamixel(bus_id=5, model='XM540-W270-R')
    device = _device(bus_id=5, model_number=1120, model_name='XM540-W270')

    out = _rows([motor], _scan(devices=[device]))

    assert out['axes'][0]['model'] == 'XM540-W270-R'
    assert out['axes'][0]['changed'] == [], '이름을 다르게 부르면 바뀐 것으로 보인다'


def test_a_scanned_model_wins_over_the_stored_one():
    out = _rows([_servo(model='')], _scan(slaves=[_slave(model='MADLN05BE')]))

    assert out['axes'][0]['model'] == 'MADLN05BE'


def test_the_unknown_marker_is_not_a_model():
    out = _rows([_servo(model='UNVERIFIED_MINAS')], _scan(slaves=[_slave(model='')]))

    assert out['axes'][0]['model'] == ''


# --------------------------------------------------------------------------- #
# 검색에 없는 축 · 검색 안 한 통로
# --------------------------------------------------------------------------- #

def test_an_axis_the_scan_did_not_find_is_named():
    out = _rows([_servo(axis=0), _servo(axis=1, alias=403, serial=587429781)],
                _scan(slaves=[_slave()]))

    assert [row['state'] for row in out['axes']] == ['matched', 'missing']
    assert out['summary']['missing'] == 1


def test_a_transport_that_was_not_scanned_is_not_reported_missing():
    """AC 서보만 검색했는데 다이나믹셀이 사라졌다고 하면 안 된다."""
    out = _rows([_servo(), _dynamixel()],
                _scan(slaves=[_slave()], dynamixel=False))

    states = {row['transport']: row['state'] for row in out['axes']}
    assert states['ethercat'] == 'matched'
    assert states['serial'] == 'not_scanned'
    assert out['summary']['missing'] == 0


# --------------------------------------------------------------------------- #
# 새 장치 · 축 번호도 서버가 준다
# --------------------------------------------------------------------------- #

def test_a_device_not_in_the_project_gets_the_next_axis_number():
    out = _rows([_servo(axis=0)],
                _scan(slaves=[_slave(), _slave(alias=403, slave=1, serial=587429781)]))

    assert out['axes'][0]['state'] == 'matched'
    assert [d['proposed_axis'] for d in out['new_devices']] == [1]
    assert out['new_devices'][0]['id'] == 'ac_servo_ethercat_master_0_alias_403'


def test_new_devices_do_not_reuse_an_axis_number():
    out = _rows([], _scan(slaves=[_slave(), _slave(alias=403, slave=1, serial=1)],
                          devices=[_device()]))

    assert [d['proposed_axis'] for d in out['new_devices']] == [0, 1, 2]


# --------------------------------------------------------------------------- #
# 바뀐 항목 · 「모른다」를 「달라졌다」로 말하지 않는다
# --------------------------------------------------------------------------- #

def test_only_values_present_on_both_sides_count_as_changed():
    """한쪽이 비어 있는 것은 아직 모르는 것이다 · 새 프로젝트가 온통 빨개진다."""
    motor = _servo(model='')
    motor['identity']['serial_number'] = None

    out = _rows([motor], _scan(slaves=[_slave()]))

    assert out['axes'][0]['changed'] == []


def test_a_replaced_drive_reports_what_differs():
    out = _rows([_servo(serial=402982152, model='MADLN05BE')],
                _scan(slaves=[_slave(serial=402982152, model='MBDLN25SE')]))

    assert out['axes'][0]['changed'] == ['model']


@pytest.mark.parametrize('deleted', [True, False])
def test_a_deleted_axis_is_left_out(deleted):
    motor = _servo()
    motor['deleted'] = deleted

    out = _rows([motor], _scan(slaves=[_slave()]))

    assert len(out['axes']) == (0 if deleted else 1)
    assert len(out['new_devices']) == (1 if deleted else 0)


def test_an_ambiguous_new_device_names_the_axes_it_might_be():
    """**화면이 첫 번째를 골라 버리던 자리다.**

    위치가 겹치는 기존 축이 하나뿐이면 「연결 확인 필요」로 짝지어 준다 ·
    여럿이면 어느 것인지 사람만 안다 · 후보를 대 주고 사람이 고르게 한다.
    """
    a, b = _servo(axis=0, alias=0), _servo(axis=1, alias=0)
    for motor in (a, b):
        motor['identity'].update({'serial_number': None, 'ethercat_alias': 0})
        motor['config']['alias'] = 0
    b['id'] = 'ac_servo_ethercat_master_0_slave_0_b'

    out = _rows([a, b], _scan(slaves=[_slave(alias=0, serial=None)]))

    assert out['new_devices'][0]['association_candidates'] == [0, 1]


def test_a_new_device_with_no_lookalike_has_no_candidates():
    out = _rows([], _scan(slaves=[_slave()]))

    assert out['new_devices'][0]['association_candidates'] == []


# --------------------------------------------------------------------------- #
# 마스터 경계 · 화면에 있던 규칙을 그대로 옮겼다
# --------------------------------------------------------------------------- #

def test_the_same_alias_on_another_master_is_another_motor():
    """Master 가 다르면 같은 alias 라도 다른 장치다."""
    motor = _servo()
    motor['identity']['serial_number'] = None
    slave = _slave(serial=None)
    slave['master_index'] = 1

    out = _rows([motor], _scan(slaves=[slave]))

    assert out['axes'][0]['state'] == 'missing'


def test_alias_zero_does_not_match_a_different_slave_position():
    motor = _servo(alias=0, slave=0)
    motor['identity'].update({'serial_number': None, 'ethercat_alias': 0})
    motor['config']['alias'] = 0

    out = _rows([motor], _scan(slaves=[_slave(alias=0, slave=2, serial=None)]))

    assert out['axes'][0]['state'] == 'missing'


def test_a_different_alias_is_not_the_same_device_without_confirmation():
    """저장된 alias 가 0이 아니면 그것이 신원 요건이다.

    신원으로는 안 맞는다 · 다만 Slave 값이 같은 축이 하나뿐이면 「연결 확인
    필요」로 짝지어 사람에게 물어본다 (화면이 쓰던 규칙 그대로다) · 사람이
    확인하면 그때 제조번호가 저장되어 다음부터는 자동으로 맞는다.
    """
    motor = _servo(alias=103)
    motor['identity']['serial_number'] = None

    out = _rows([motor], _scan(slaves=[_slave(alias=404, serial=None)]))

    assert out['axes'][0]['state'] == 'matched'
    assert out['axes'][0]['confirmation_required'] is True
    assert 'alias' in out['axes'][0]['changed']


def test_a_drive_that_cannot_report_its_serial_needs_confirmation():
    """제조번호를 아는 축인데 검색이 못 읽어 왔으면 자동으로 단정하지 않는다."""
    out = _rows([_servo(serial=402982152)], _scan(slaves=[_slave(serial=None)]))

    assert out['axes'][0]['confirmation_required'] is True


# --------------------------------------------------------------------------- #
# 검색한 통로가 어디인지 말해 준다 · §6-219
#
# 화면은 **검색한 통로의 축을 전부 지우고** 찾은 것으로 채운다 · 어느 통로를
# 실제로 검색했는지 모르면, AC Servo 검색만 했는데 다이나믹셀 축까지
# 지워진다.
# --------------------------------------------------------------------------- #

def test_a_full_scan_says_both_transports_were_scanned():
    out = _rows([], _scan(slaves=[_slave()], devices=[_device()]))

    assert out['scanned_transports'] == ['ethercat', 'serial']


def test_an_ac_servo_scan_says_only_ethercat():
    out = _rows([_servo(), _dynamixel()], _scan(slaves=[_slave()], dynamixel=False))

    assert out['scanned_transports'] == ['ethercat']


def test_a_dynamixel_scan_says_only_serial():
    out = _rows([_servo(), _dynamixel()], _scan(devices=[_device()], ethercat=False))

    assert out['scanned_transports'] == ['serial']
