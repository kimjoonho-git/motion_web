"""두 번째 AC 서보가 영영 안 붙던 이유 · §6-201

**`alias` 가 있으면 `position` 은 0 이다.**

`ecrt_master_slave_config(master, alias, position, vendor, product)` 에서
`position` 은 **alias 로부터의 상대 위치**다 (IgH EtherCAT 규약) · 링 위치가
아니다.

    403:0  →  alias 403 인 바로 그 슬레이브       ← 맞다
    403:1  →  alias 403 에서 한 칸 뒤             ← 그런 것은 없다

전에는 링 위치를 그대로 넣었다.

    'position': slave_position     # 링 위치 0, 1, 2 …

링 0번은 `103:0` 이라 **우연히** 맞았다 · 그래서 서보 한 대짜리 현장에서는
멀쩡했다 · 두 대째부터 `403:1` 이 되어 마스터가 끝내 못 붙였다.

    $ ethercat config
      103:0  0x066f/0x60380004  0  OP        ← 붙음
      403:1  0x066f/0x60380004  -  -         ← 아무 슬레이브에도 안 붙음

증상이 고약했다.

    검색      두 대 다 보인다 (버스 위에 있으니까)
    적용      한 대만 올라온다 · 45초 기다리다 실패
    알람      없다 · 마스터가 제 것으로 여기지 않으니 올릴 이유가 없다
    슬레이브  PREOP 에 머문다

`ethercat slaves` 로는 `403:0 PREOP` 로 보여서 더 헷갈린다 · 장비는 제
alias 를 알고 있는데 **설정만 딴 곳을 가리킨다.**
"""

import pytest

from motion_web_bridge import motor_config_build
from test_motor_driver_profiles import _registry_motor


def _motor(alias, slave_position, axis=0):
    motor = _registry_motor(axis, transport='ethercat')
    motor['identity'].update({
        'ethercat_alias': alias,
        'slave_position': slave_position,
    })
    return motor


def _registry(alias, slave_position, axis=0):
    return {'motors': [_motor(alias, slave_position, axis)]}


def _slave(tmp_path, alias, slave_position):
    config = motor_config_build.motor_config_from_registry(
        tmp_path,
        _registry(alias, slave_position),
        motor_config_build.default_motor_config(tmp_path),
    )
    return config['masters'][0]['slaves'][0], config


@pytest.mark.parametrize('ring_position', [0, 1, 2, 7])
def test_an_aliased_slave_is_addressed_relative_to_its_alias(tmp_path, ring_position):
    """**이것이 그 버그다** · 링 위치가 무엇이든 alias 기준은 0 이다."""
    slave, _config = _slave(tmp_path, 403, ring_position)

    assert slave['alias'] == 403
    assert slave['position'] == 0, (
        f'링 {ring_position}번을 그대로 넣으면 403:{ring_position} 이 되어 '
        '그런 슬레이브가 없습니다'
    )


def test_the_first_servo_worked_by_luck(tmp_path):
    """링 0번은 옛 코드로도 맞았다 · 그래서 한 대짜리 현장은 멀쩡했다."""
    slave, _config = _slave(tmp_path, 103, 0)

    assert slave['position'] == 0


def test_a_slave_without_an_alias_still_uses_the_ring_position(tmp_path):
    """EEPROM 에 alias 를 안 써 넣었으면 링 위치로 찾는 수밖에 없다."""
    slave, _config = _slave(tmp_path, 0, 2)

    assert slave['alias'] == 0
    assert slave['position'] == 2


def test_the_ring_position_people_see_is_kept(tmp_path):
    """사람이 보는 값은 링 위치다 · 화면과 검색이 그것으로 짝을 맞춘다."""
    _slave_entry, config = _slave(tmp_path, 403, 1)

    assert config['web_axis_identities'][0]['slave_position'] == 1


def test_two_servos_get_two_different_aliases_and_the_same_offset(tmp_path):
    """두 대일 때가 문제였다 · 둘 다 제 alias 기준 0 이어야 한다."""
    registry = {'motors': [_motor(103, 0, axis=0), _motor(403, 1, axis=1)]}
    config = motor_config_build.motor_config_from_registry(
        tmp_path, registry, motor_config_build.default_motor_config(tmp_path)
    )
    slaves = config['masters'][0]['slaves']

    assert [s['alias'] for s in slaves] == [103, 403]
    assert [s['position'] for s in slaves] == [0, 0]


# --------------------------------------------------------------------------- #
# 검색 종류가 이름 바뀜에 휩쓸리지 않는다 · §6-202
# --------------------------------------------------------------------------- #

def test_every_scan_service_maps_to_its_own_transport():
    """**조용히 「전체 검색」으로 바뀌던 것.**

    통로 이름을 `/motor/` 아래로 옮기면서(§6-3) 이 표를 안 고쳤다 · 표에서
    못 찾으면 `'all'` 로 떨어지는데, 그러면 AC 서보 검색이 다이나믹셀까지
    뒤져 10초 제한을 넘긴다 · 화면엔 「scan service timeout」만 뜬다.

    오류도 경고도 없다 · 그래서 며칠을 헤맨다.
    """
    from motion_common import topics
    from motion_web_bridge.scan_orchestrator import ScanOrchestrator

    table = ScanOrchestrator.TRANSPORT_BY_SERVICE
    for service, expected in (
        (topics.SCAN_MOTORS, 'all'),
        (topics.SCAN_AC_SERVO_MOTORS, 'ac_servo'),
        (topics.SCAN_DYNAMIXEL_MOTORS, 'dynamixel'),
    ):
        key = service.rsplit('/', 1)[-1]
        assert table.get(key) == expected, (
            f'{service} 가 표에 없습니다 · 조용히 전체 검색이 됩니다'
        )


def test_the_table_is_derived_not_retyped():
    """글자로 다시 적으면 이름이 바뀔 때 또 갈린다."""
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / 'motion_web_bridge' / 'scan_orchestrator.py'
    ).read_text(encoding='utf-8')
    start = source.index('TRANSPORT_BY_SERVICE = {')
    body = source[start:source.index('}', start)]

    assert 'topics.SCAN_MOTORS' in body
    assert 'topics.SCAN_AC_SERVO_MOTORS' in body
    assert 'topics.SCAN_DYNAMIXEL_MOTORS' in body
