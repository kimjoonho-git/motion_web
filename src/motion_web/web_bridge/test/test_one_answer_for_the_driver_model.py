"""드라이버는 `MADLN05BE`, 프로젝트 파일은 「모름」 · §6-210

**같은 사실이 두 곳에 다른 값으로 적혔다.**

검색은 SII EEPROM 에서 모델을 읽어 온다 · 그 값을 쓰는 판단이
`driver_id_for_registry_motor` 안의 **사본**에서만 일어났고, 드라이버를 고르는
데 쓰인 뒤 그대로 버려졌다 · 그래서 저장된 프로젝트가 이렇게 갈렸다.

    drivers:
      - id: 4   type: minas   driver_model: MADLN05BE      ← 실제로 이걸로 돈다
    web_axis_profiles:
      - controller_index: 0   driver_model: UNVERIFIED_MINAS   ← 화면이 읽는 값

증상은 「적용은 되는데 화면은 모델 미확인」이다 · 서버 검증은 드라이버를 보니
통과하고, 화면은 `web_axis_profiles` 를 보니 영영 「실행 적용 불가 ·
모델·운전 프로필 미확인 축: 0, 1」을 띄운다 · 어느 쪽이 맞는지 사람이 알 길이
없다.

`UNVERIFIED_MINAS` 는 **「모른다」는 표식**이지 모델 이름이 아니다 · 읽는 쪽이
그걸 글자로만 보면 「값이 있다」고 세어 버린다.
"""

import pytest

from motion_web_bridge import motor_config_build, motor_config_rules
from motion_web_bridge.motor_identity import UNKNOWN_DRIVER_MODEL
from test_motor_driver_profiles import _registry_motor


def _servo(model='', sii='MADLN05BE', axis=0):
    motor = _registry_motor(axis, transport='ethercat')
    motor['profile'] = {
        'driver_model': model,
        'model_confirmed': False,
        'model_source': '',
    }
    motor['identity'].update({'sii_order_number': sii})
    return motor


def _config(tmp_path, *motors):
    return motor_config_build.motor_config_from_registry(
        tmp_path,
        {'motors': list(motors)},
        motor_config_build.default_motor_config(tmp_path),
    )


def _driver_model(config, axis):
    slave = next(
        slave
        for master in config['masters']
        for slave in master.get('slaves', [])
        if slave.get('controller_index') == axis
    )
    driver = next(d for d in config['drivers'] if d['id'] == slave['driver_id'])
    return str(driver.get('driver_model') or '')


@pytest.mark.parametrize('stored', ['', UNKNOWN_DRIVER_MODEL])
def test_the_file_says_what_the_driver_says(tmp_path, stored):
    """**이것이 그 버그다** · 드라이버와 프로젝트 파일이 갈리면 안 된다."""
    config = _config(tmp_path, _servo(model=stored))

    assert _driver_model(config, 0) == 'MADLN05BE'
    assert config['web_axis_profiles'][0]['driver_model'] == 'MADLN05BE', (
        '드라이버는 모델을 아는데 화면이 읽는 값에는 「모름」이 남았습니다'
    )
    assert config['web_axis_profiles'][0]['model_confirmed'] is True
    assert config['web_axis_profiles'][0]['model_source'] == 'physical_sii'


def test_a_model_the_user_typed_wins_over_the_scan(tmp_path):
    """사람이 적어 넣은 값이 있으면 그것이 답이다."""
    config = _config(tmp_path, _servo(model='MBDLN25SE', sii='MADLN05BE'))

    assert _driver_model(config, 0) == 'MBDLN25SE'
    assert config['web_axis_profiles'][0]['driver_model'] == 'MBDLN25SE'


def test_a_model_nobody_knows_stays_unknown(tmp_path):
    """SII 도 못 읽었으면 모르는 것이다 · 지어내지 않는다."""
    config = _config(tmp_path, _servo(model='', sii=''))

    assert config['web_axis_profiles'][0]['driver_model'] == ''
    assert config['web_axis_profiles'][0]['model_confirmed'] is False
    assert config['web_axis_profiles'][0]['model_source'] == ''


def test_the_marker_never_reaches_the_screen_from_an_old_project(tmp_path):
    """이미 저장된 프로젝트에도 표식이 남아 있다 · 읽을 때 걷어낸다."""
    config = _config(tmp_path, _servo(model=UNKNOWN_DRIVER_MODEL))
    config['web_axis_profiles'][0]['driver_model'] = UNKNOWN_DRIVER_MODEL
    config['web_axis_profiles'][0]['model_confirmed'] = False

    registry = motor_config_rules.registry_from_motor_config(config)
    profile = registry['motors'][0]['profile']

    assert profile['driver_model'] == 'MADLN05BE'
    assert profile['model_confirmed'] is True


def test_the_driver_answers_when_the_profile_is_empty(tmp_path):
    """프로필이 비어 있어도 드라이버가 알고 있으면 그것이 답이다."""
    config = _config(tmp_path, _servo(model='MBDLN25SE', sii=''))
    config['web_axis_profiles'] = []

    profile = motor_config_rules.registry_from_motor_config(config)['motors'][0]['profile']

    assert profile['driver_model'] == 'MBDLN25SE'
    assert profile['model_confirmed'] is True


def test_the_marker_is_written_down_in_one_place():
    """글자로 다시 적으면 한 곳을 고칠 때 또 갈린다."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / 'motion_web_bridge'
    offenders = [
        path.name
        for path in sorted(root.glob('*.py'))
        if path.name != 'motor_identity.py'
        and f"'{UNKNOWN_DRIVER_MODEL}'" in path.read_text(encoding='utf-8')
    ]

    assert offenders == [], f'표식을 글자로 다시 적었습니다: {offenders}'


def test_the_file_never_disagrees_with_its_own_driver(tmp_path):
    """**쓰는 쪽도 읽는 쪽과 같은 순서로 되짚는다.**

    검색이 모델을 못 읽어 와도 축이 가리키는 드라이버는 알고 있을 수 있다 ·
    읽는 쪽은 그것을 되짚는데 쓰는 쪽이 안 그래서 한 파일 안에서 갈렸다.

        web_axis_profiles:  0번 축  driver_model ''
        drivers:            id 4    driver_model 'MADLN05BE'

    읽을 때 가려져 보이지 않을 뿐 같은 사고다.
    """
    config = _config(tmp_path, _servo(model='MBDLN25SE', sii=''))
    registry = motor_config_rules.registry_from_motor_config(config)
    for motor in registry['motors']:
        motor['profile'] = {'driver_model': '', 'model_confirmed': False, 'model_source': ''}
        motor['identity']['sii_order_number'] = ''
        motor['identity']['sii_device_name'] = ''

    again = motor_config_build.motor_config_from_registry(tmp_path, registry, config)

    assert _driver_model(again, 0) == 'MBDLN25SE'
    assert again['web_axis_profiles'][0]['driver_model'] == 'MBDLN25SE', (
        '드라이버는 아는데 프로필에는 빈 값이 적혔습니다'
    )
