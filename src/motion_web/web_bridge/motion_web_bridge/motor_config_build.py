"""모터 설정 생성 · 레지스트리 → `motion_system` 설정 · 상태 비의존.

`MotionWebBridge`에서 떼어냈다. 노드 상태 의존은 `workspace_root` 하나뿐이었고
재대입되지 않는 불변 경로였으므로 첫 인자로 받는다 · §6-11

판정 규칙(`motor_config_rules`)과 나눈 이유는 §7 파일 1,000줄 기준이다.
의존 방향은 한쪽이다 · 이 모듈이 `motor_config_rules`를 쓰고, 반대는 없다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from motion_common.values import optional_int

from motion_web_bridge.motor_identity import (
    UNKNOWN_DRIVER_MODEL,
    driver_model_from,
    model_is_unknown,
)

from motion_web_bridge.motor_config_rules import (
    expand_shared_driver_profiles,
    prune_unused_drivers,
)

#: Dynamixel 직렬 통신 속도 · 화면(`motor_type_dynamixel.js`)과 같은 값
DYNAMIXEL_BAUDRATE = 1000000


def dynamixel_param_file_for_model(workspace_root: Path, driver_model: str) -> str:
    model = driver_model.lower().replace('_', '-')
    if 'xm540-w150' in model:
        return str(workspace_root / 'config/dynamixel_xm540_w150.yaml')
    return str(workspace_root / 'config/dynamixel_xm540_w270.yaml')


def normalize_driver_configs(
    workspace_root: Path, drivers: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    normalized = []
    for driver in drivers:
        if not isinstance(driver, dict):
            normalized.append(driver)
            continue
        item = dict(driver)
        if str(item.get('type') or '') == 'dynamixel':
            item['param_file'] = dynamixel_param_file_for_model(
                workspace_root, str(item.get('driver_model') or '')
            )
        normalized.append(item)
    return normalized


def default_dynamixel_driver(workspace_root: Path, driver_model: str = '') -> Dict[str, Any]:
    model = str(driver_model or '').strip().upper().replace('_', '-')
    if 'XM540-W150' in model:
        canonical_model = 'XM540-W150'
        rated_speed_rpm = 66
        velocity = 396.0
    elif 'XM540-W270' in model:
        canonical_model = 'XM540-W270-R'
        rated_speed_rpm = 37
        velocity = 222.0
    else:
        canonical_model = str(driver_model or 'Dynamixel').strip() or 'Dynamixel'
        rated_speed_rpm = 30
        velocity = 100.0

    return {
        'driver_model': canonical_model,
        'pulse_per_revolution': 4096,
        'rated_effort': 1.0,
        'unit_effort': 0.00269,
        'rated_current': 1.0,
        'rated_speed_rpm': rated_speed_rpm,
        'lower': -180.0,
        'upper': 180.0,
        'speed': velocity,
        'acceleration': 703104.5,
        'deceleration': 703104.5,
        'profile_velocity': velocity,
        'profile_acceleration': 703104.5,
        'profile_deceleration': 703104.5,
        'profile_position_value': 3,
        'profile_velocity_value': 1,
        'profile_effort_value': 0,
        'type': 'dynamixel',
        'param_file': dynamixel_param_file_for_model(workspace_root, canonical_model),
    }


def default_motor_config(workspace_root: Path) -> Dict[str, Any]:
    return {
        'period': 1000000,
        'masters': [
            {
                'id': 0,
                'type': 'ethercat',
                'number_of_slaves': 0,
                'ethercat_master_index': 0,
                'slaves': [],
            },
        ],
        'drivers': [
            {
                'id': 0,
                'driver_model': UNKNOWN_DRIVER_MODEL,
                'pulse_per_revolution': 8388608,
                'rated_effort': 0.16,
                'unit_effort': 0.1,
                'rated_current': 1.1,
                'rated_power_w': 50,
                'rated_speed_rpm': 3000,
                'lower': -36000.0,
                'upper': 36000.0,
                'speed': 2000000.0,
                'acceleration': 180000.0,
                'deceleration': 180000.0,
                'profile_velocity': 18000.0,
                'profile_acceleration': 180000.0,
                'profile_deceleration': 180000.0,
                'profile_position_value': 1,
                'profile_velocity_value': 3,
                'profile_effort_value': 4,
                'type': 'minas',
                'param_file': str(
                    workspace_root
                    / 'src/motion_system/ros2/motion_system_ros2/motion_control_bridge/param'
                ),
            },
        ],
    }


def resolved_motor_profile(
    motor: Dict[str, Any],
    driver: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """축 하나의 **모델 사실을 여기서 한 번만 정한다** · §6-210

    전에는 이 판단이 `driver_id_for_registry_motor` 안의 **사본**에서만
    일어났다 · 드라이버 고르기에는 쓰이고 그대로 버려졌다 · 그래서 생성된
    드라이버는 `MADLN05BE` 인데 프로젝트 파일의 `web_axis_profiles` 에는
    「모름」 표식이 그대로 남았다.

        drivers:            driver 4  minas  'MADLN05BE'
        web_axis_profiles:  0번 축    driver_model 「모름」 표식

    같은 사실이 두 곳에 다른 값으로 적히면 화면은 「모델 미확인」을 띄우고
    적용은 통과한다 · 어느 쪽이 맞는지 사람이 알 길이 없다.

    모델을 알면 확인된 것이다 · `model_confirmed` 를 따로 들고 다니지 않고
    모델에서 끌어낸다 · 두 값이 갈릴 자리를 없앤다.
    """
    identity = motor.get('identity') if isinstance(motor.get('identity'), dict) else {}
    profile = dict(motor.get('profile')) if isinstance(motor.get('profile'), dict) else {}
    # 옛 프로젝트는 모델을 물리 식별 정보에 적어 두었다.
    if not profile.get('driver_model') and identity.get('driver_model'):
        profile['driver_model'] = identity.get('driver_model')
    model = driver_model_from(profile, identity)
    # **읽는 쪽과 같은 순서로 되짚는다** · §6-210
    #
    # 축이 가리키는 드라이버가 모델을 알고 있으면 그것이 답이다 · 읽는 쪽
    # (`motor_config_rules.axis_profile`)은 이미 그렇게 되짚는데 쓰는 쪽이
    # 안 그랬다 · 그래서 파일 안에서 프로필은 빈 값, 드라이버는 `MADLN05BE`
    # 로 갈렸다 · 읽을 때 가려져 보이지 않을 뿐 같은 사고다.
    if not model and isinstance(driver, dict):
        model = '' if model_is_unknown(driver.get('driver_model')) else str(
            driver.get('driver_model')
        ).strip()
    source = str(profile.get('model_source') or '')
    if not source:
        if profile.get('model_confirmed') is True or identity.get('nameplate_confirmed') is True:
            source = 'user_nameplate'
        elif model:
            source = 'physical_sii'
    return {
        'driver_model': model,
        'model_confirmed': bool(model),
        'model_source': source if model else '',
    }


def driver_id_for_registry_motor(
    workspace_root: Path,
    motor: Dict[str, Any],
    drivers: List[Dict[str, Any]],
) -> int:
    motor_config = motor.get('config') if isinstance(motor.get('config'), dict) else {}
    profile = resolved_motor_profile(motor)
    driver_type = str(motor.get('driver_family') or motor.get('motor_type') or 'unknown')
    driver_model = str(profile.get('driver_model') or '').strip()
    requested_id = optional_int(motor_config.get('driver_id'), None)

    drivers_by_id = {
        optional_int(driver.get('id'), None): driver
        for driver in drivers
        if isinstance(driver, dict)
    }
    if requested_id is not None:
        requested_driver = drivers_by_id.get(requested_id)
        if requested_driver is not None:
            same_type = str(requested_driver.get('type') or '') == driver_type
            same_model = not driver_model or str(requested_driver.get('driver_model') or '') == driver_model
            if same_type and same_model:
                return requested_id

    for driver in drivers:
        if not isinstance(driver, dict):
            continue
        if str(driver.get('type') or '') != driver_type:
            continue
        if driver_model and str(driver.get('driver_model') or '') != driver_model:
            continue
        driver_id = optional_int(driver.get('id'), None)
        if driver_id is not None:
            return driver_id

    return append_driver_for_registry_motor(
        workspace_root, driver_type, driver_model, drivers
    )


def append_driver_for_registry_motor(
    workspace_root: Path,
    driver_type: str,
    driver_model: str,
    drivers: List[Dict[str, Any]],
) -> int:
    if driver_type == 'dynamixel':
        # Dynamixel values are model-specific.  Do not clone the first
        # registered Dynamixel profile and merely rename it, because scan
        # order would then give W150 values to W270 (or vice versa).
        template = default_dynamixel_driver(workspace_root, driver_model)
    else:
        template = next(
            (
                dict(driver)
                for driver in drivers
                if isinstance(driver, dict) and str(driver.get('type') or '') == driver_type
            ),
            None,
        )
    if template is None and driver_type == 'minas':
        template = dict(default_motor_config(workspace_root)['drivers'][0])
    elif template is None:
        template = {
            'type': driver_type,
            'driver_model': driver_model or driver_type,
        }

    used_driver_ids = {
        optional_int(driver.get('id'), -1)
        for driver in drivers
        if isinstance(driver, dict)
    }
    next_driver_id = max([item for item in used_driver_ids if item is not None] + [-1]) + 1
    while next_driver_id in used_driver_ids:
        next_driver_id += 1

    template['id'] = next_driver_id
    template['type'] = driver_type
    if driver_model and driver_type != 'dynamixel':
        template['driver_model'] = driver_model
    elif not template.get('driver_model'):
        template['driver_model'] = driver_type
    if driver_type == 'dynamixel':
        template['param_file'] = dynamixel_param_file_for_model(
            workspace_root, str(template.get('driver_model') or '')
        )
    drivers.append(template)
    return next_driver_id


def serial_masters_from_registry(
    workspace_root: Path,
    registry: Dict[str, Any],
    current_masters: List[Dict[str, Any]],
    drivers: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    serial_masters_by_key: Dict[tuple, Dict[str, Any]] = {}
    used_master_ids = {
        optional_int(master.get('id'), -1)
        for master in current_masters
        if isinstance(master, dict)
    }
    next_master_id = max([item for item in used_master_ids if item is not None] + [-1]) + 1

    def master_for(port: str, baudrate: int) -> Dict[str, Any]:
        nonlocal next_master_id
        key = (port, baudrate)
        if key in serial_masters_by_key:
            return serial_masters_by_key[key]

        existing = next(
            (
                dict(master)
                for master in current_masters
                if master.get('type') == 'serial'
                and str(master.get('serial_port') or '') == port
                and optional_int(master.get('serial_baudrate'), None) == baudrate
            ),
            None,
        )
        if existing is None:
            while next_master_id in used_master_ids:
                next_master_id += 1
            existing = {
                'id': next_master_id,
                'type': 'serial',
                'serial_port': port,
                'serial_baudrate': baudrate,
            }
            used_master_ids.add(next_master_id)
            next_master_id += 1

        existing['type'] = 'serial'
        existing['serial_port'] = port
        existing['serial_baudrate'] = baudrate
        existing['slaves'] = []
        serial_masters_by_key[key] = existing
        return existing

    for motor in registry.get('motors', []):
        if not isinstance(motor, dict):
            continue
        if motor.get('deleted') or not motor.get('enabled', False):
            continue
        if motor.get('transport') != 'serial':
            continue
        motor_config = motor.get('config') if isinstance(motor.get('config'), dict) else {}
        identity = motor.get('identity') if isinstance(motor.get('identity'), dict) else {}
        axis = optional_int(motor_config.get('controller_index'), motor.get('axis'))
        bus_id = optional_int(
            motor_config.get('bus_id'),
            optional_int(identity.get('bus_id'), identity.get('node_id')),
        )
        port = str(motor_config.get('serial_port') or identity.get('serial_port') or '').strip()
        baudrate = optional_int(
            motor_config.get('serial_baudrate'),
            optional_int(identity.get('serial_baudrate'), None),
        )
        if str(motor.get('driver_family') or motor.get('motor_type') or '') == 'dynamixel':
            baudrate = DYNAMIXEL_BAUDRATE
        if axis is None or bus_id is None or not port or baudrate is None:
            continue

        driver_id = driver_id_for_registry_motor(workspace_root, motor, drivers)
        master = master_for(port, baudrate)
        name = str(motor.get('name') or f'{axis}번 축').strip() or f'{axis}번 축'
        master['slaves'].append(
            {
                'controller_index': axis,
                'name': name,
                'driver_id': driver_id,
                'bus_id': bus_id,
                'profile_mode': optional_int(motor_config.get('profile_mode'), 0),
            }
        )

    serial_masters = []
    for master in serial_masters_by_key.values():
        master['slaves'].sort(key=lambda item: int(item.get('controller_index') or 0))
        master['number_of_slaves'] = len(master['slaves'])
        if master['number_of_slaves'] > 0:
            serial_masters.append(master)
    serial_masters.sort(key=lambda item: int(item.get('id') or 0))
    return serial_masters


def motor_config_from_registry(
    workspace_root: Path,
    registry: Dict[str, Any],
    current: Dict[str, Any],
) -> Dict[str, Any]:
    if not isinstance(current, dict):
        current = default_motor_config(workspace_root)
    config = dict(current)
    config['period'] = 1000000
    drivers = config.get('drivers')
    if not isinstance(drivers, list) or not drivers:
        drivers = default_motor_config(workspace_root)['drivers']
    drivers = [dict(driver) if isinstance(driver, dict) else {} for driver in drivers]

    masters = config.get('masters')
    if not isinstance(masters, list) or not masters:
        masters = default_motor_config(workspace_root)['masters']
    masters = [dict(master) if isinstance(master, dict) else {} for master in masters]

    ethercat_slaves_by_master: Dict[int, List[Dict[str, Any]]] = {}
    web_axis_identities = []
    web_axis_profiles = []
    for motor in registry.get('motors', []):
        if not isinstance(motor, dict):
            continue
        if motor.get('deleted') or not motor.get('enabled', False):
            continue
        if motor.get('transport') != 'ethercat':
            continue
        motor_config = motor.get('config') if isinstance(motor.get('config'), dict) else {}
        axis = optional_int(motor_config.get('controller_index'), motor.get('axis'))
        if axis is None:
            continue
        name = str(motor.get('name') or f'{axis}번 축').strip() or f'{axis}번 축'
        identity = motor.get('identity') if isinstance(motor.get('identity'), dict) else {}
        ethercat_master_index = optional_int(
            motor_config.get('ethercat_master_index'),
            optional_int(identity.get('ethercat_master_index'), 0),
        )
        if ethercat_master_index is None or ethercat_master_index < 0:
            raise ValueError(
                f'{axis}번 축의 EtherCAT Master 번호가 올바르지 않습니다'
            )
        eeprom_alias = optional_int(
            identity.get('ethercat_alias'),
            optional_int(motor_config.get('alias'), 0),
        )
        slave_position = optional_int(
            identity.get('slave_position'),
            optional_int(motor_config.get('position'), 0),
        )
        driver_id = driver_id_for_registry_motor(workspace_root, motor, drivers)
        ethercat_slaves_by_master.setdefault(
            ethercat_master_index, []
        ).append(
            {
                'controller_index': axis,
                'name': name,
                'driver_id': driver_id,
                'alias': eeprom_alias,
                # **alias 가 있으면 position 은 0 이다** · §6-201
                #
                # `ecrt_master_slave_config(master, alias, position, ...)` 에서
                # `position` 은 **alias 로부터의 상대 위치**다 (IgH 규약) ·
                # ring 위치가 아니다.
                #
                #     403:0  →  alias 403 인 바로 그 슬레이브    ← 맞다
                #     403:1  →  alias 403 에서 한 칸 뒤          ← 그런 건 없다
                #
                # 전에는 ring 위치를 그대로 넣었다 · ring 0번은 `103:0` 이라
                # 우연히 맞았지만 **2번째 이후 서보는 영영 안 붙었다** ·
                # `ethercat config` 가 `403:1 ... - -` 로 보여 준다 (미결합) ·
                # 슬레이브는 PREOP 에 머물고 알람은 뜨지 않는다 · 마스터가
                # 그 슬레이브를 제 것으로 여기지 않으니 올릴 이유가 없다.
                #
                # alias 가 0 이면(=EEPROM 에 안 써 넣었으면) ring 위치로
                # 찾아야 하므로 그대로 쓴다.
                'position': 0 if eeprom_alias else slave_position,
                # 사람이 보는 **링 위치** · §6-207
                #
                # 위 `position` 은 마스터가 쓰는 주소값이라 alias 를 쓰면 늘
                # 0 이다 · 그것을 화면에 「Slave Position」으로 내보내면 서보
                # 두 대가 모두 0 으로 보인다.
                #
                # 실행 설정에서는 `web_axis_identities` 가 떨어져 나가므로
                # (§6-22 · 모터 노드가 안 쓰는 값이라 뺀다) 슬레이브에 같이
                # 적어 보낸다 · 모터 매니저는 이름으로 읽는 키만 보므로
                # 모르는 키는 그냥 지나간다.
                'ring_position': slave_position,
                'vendor_id': optional_int(
                    identity.get('vendor_id'),
                    optional_int(motor_config.get('vendor_id'), None),
                ),
                'product_id': optional_int(
                    identity.get('product_code'),
                    optional_int(motor_config.get('product_id'), None),
                ),
                'profile_mode': optional_int(motor_config.get('profile_mode'), 0),
            }
        )
        web_axis_identities.append({
            'controller_index': axis,
            'ethercat_master_index': ethercat_master_index,
            'eeprom_alias': eeprom_alias,
            'rotary_alias': optional_int(identity.get('rotary_alias'), None),
            'slave_position': slave_position,
            'vendor_id': optional_int(
                identity.get('vendor_id'),
                optional_int(motor_config.get('vendor_id'), None),
            ),
            'product_id': optional_int(
                identity.get('product_code'),
                optional_int(motor_config.get('product_id'), None),
            ),
            'revision_number': optional_int(
                identity.get('revision_number'),
                optional_int(motor_config.get('revision_number'), None),
            ),
            'serial_number': optional_int(
                identity.get('serial_number'),
                optional_int(motor_config.get('serial_number'), None),
            ),
            'identity_source': str(identity.get('identity_source') or ''),
            'sii_order_number': str(identity.get('sii_order_number') or ''),
            'sii_device_name': str(identity.get('sii_device_name') or ''),
        })
        # **드라이버에 적은 것과 같은 값을 적는다** · §6-210
        #
        # 전에는 레지스트리의 원본 프로필을 그대로 옮겨 적었다 · 모델을
        # SII 에서 풀어낸 것은 드라이버 고르기에만 쓰이고 버려졌으므로
        # 여기엔 「모름」 표식이 남았다 · 화면은 그걸 읽어 영영
        # 「모델 미확인」을 띄웠다.
        web_axis_profiles.append({
            'controller_index': axis,
            **resolved_motor_profile(
                motor,
                next(
                    (
                        item
                        for item in drivers
                        if isinstance(item, dict)
                        and optional_int(item.get('id'), None) == driver_id
                    ),
                    None,
                ),
            ),
        })

    existing_ethercat_masters = {
        optional_int(master.get('ethercat_master_index'), 0): master
        for master in masters
        if isinstance(master, dict) and master.get('type') == 'ethercat'
    }
    used_master_ids = {
        optional_int(master.get('id'), None)
        for master in masters
        if isinstance(master, dict)
        and optional_int(master.get('id'), None) is not None
    }
    next_master_id = max(used_master_ids | {-1}) + 1
    ethercat_masters = []
    for master_index in sorted(ethercat_slaves_by_master):
        ethercat_master = dict(
            existing_ethercat_masters.get(master_index) or {}
        )
        master_id = optional_int(ethercat_master.get('id'), None)
        if master_id is None:
            while next_master_id in used_master_ids:
                next_master_id += 1
            master_id = next_master_id
            used_master_ids.add(master_id)
            next_master_id += 1
        slaves = ethercat_slaves_by_master[master_index]
        slaves.sort(key=lambda item: int(item.get('controller_index') or 0))
        ethercat_master.update({
            'id': master_id,
            'type': 'ethercat',
            'ethercat_master_index': master_index,
            'slaves': slaves,
            'number_of_slaves': len(slaves),
        })
        ethercat_masters.append(ethercat_master)

    if web_axis_identities:
        config['web_axis_identities'] = web_axis_identities
    else:
        config.pop('web_axis_identities', None)
    if web_axis_profiles:
        config['web_axis_profiles'] = web_axis_profiles
    else:
        config.pop('web_axis_profiles', None)

    non_bus_masters = [
        master
        for master in masters
        if master.get('type') not in {'ethercat', 'serial'}
    ]
    master_context = ethercat_masters + non_bus_masters + [
        master for master in masters if master.get('type') == 'serial'
    ]
    serial_masters = serial_masters_from_registry(
        workspace_root, registry, master_context, drivers
    )
    masters = ethercat_masters + non_bus_masters + serial_masters

    config['masters'] = masters
    config['drivers'] = prune_unused_drivers(
        normalize_driver_configs(workspace_root, drivers),
        masters,
    )
    return expand_shared_driver_profiles(config)
