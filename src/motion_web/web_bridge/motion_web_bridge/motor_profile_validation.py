"""실행용 모터 설정 검증 · 순수 함수.

`ProjectRepository`에서 떼어냈다 · §6-46

버스 검색은 장치를 **찾아줄 뿐 안전한 동작 프로파일을 재주지 않는다.** 드라이버를
가리키지 않는 슬레이브나, 조그가 고장난 것처럼 보일 만큼 느린 프로파일은 제어
노드를 재시작하기 **전에** 걸러야 한다.

상태도 파일도 만지지 않는다 · 받은 설정을 보고 통과시키거나 `ValueError`를 낸다.
"""

from __future__ import annotations

from typing import Any, Dict

from .motor_identity import missing_ethercat_identity


def validate_runtime_motor_profiles(payload: Dict[str, Any]) -> None:
    """Reject incomplete or accidentally count-scaled motion profiles.

    A bus scan identifies devices, but it does not measure a safe motion
    profile.  Applying a slave that references no driver, or a profile so
    slow that ordinary jog appears broken, must fail before control nodes
    are restarted.
    """
    drivers = {
        driver.get('id'): driver
        for driver in payload.get('drivers') or []
        if isinstance(driver, dict) and driver.get('id') is not None
    }
    required_positive = (
        'profile_velocity',
        'profile_acceleration',
        'profile_deceleration',
    )
    used_controller_indices = set()
    used_nonzero_aliases = set()
    used_zero_alias_positions = set()
    used_serial_devices = set()
    used_master_ids = set()
    used_ethercat_master_indices = set()
    identity_by_axis = {
        item.get('controller_index'): item
        for item in payload.get('web_axis_identities') or []
        if isinstance(item, dict) and item.get('controller_index') is not None
    }
    profile_by_axis = {
        item.get('controller_index'): item
        for item in payload.get('web_axis_profiles') or []
        if isinstance(item, dict) and item.get('controller_index') is not None
    }
    for master in payload.get('masters') or []:
        if not isinstance(master, dict):
            continue
        try:
            master_id = int(master.get('id'))
        except (TypeError, ValueError) as exc:
            raise ValueError('모터 Master ID는 정수여야 합니다') from exc
        if master_id in used_master_ids:
            raise ValueError(f'Motor Master ID {master_id} 값이 중복되어 있습니다')
        used_master_ids.add(master_id)
        ethercat_master_index = None
        if str(master.get('type') or '') == 'ethercat':
            try:
                ethercat_master_index = int(
                    master.get('ethercat_master_index', 0)
                )
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    'EtherCAT Master 번호는 0 이상의 정수여야 합니다'
                ) from exc
            if ethercat_master_index < 0:
                raise ValueError('EtherCAT Master 번호는 0 이상의 정수여야 합니다')
            if ethercat_master_index in used_ethercat_master_indices:
                raise ValueError(
                    f'EtherCAT Master {ethercat_master_index} 설정이 중복되어 있습니다'
                )
            used_ethercat_master_indices.add(ethercat_master_index)
        for slave in master.get('slaves') or []:
            if not isinstance(slave, dict):
                continue
            axis = slave.get('controller_index', '?')
            if axis in used_controller_indices:
                raise ValueError(f'Control Index {axis} 값이 중복되어 있습니다')
            used_controller_indices.add(axis)
            if str(master.get('type') or '') == 'ethercat':
                try:
                    alias = int(slave.get('alias') or 0)
                    position = int(slave.get('position') or 0)
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        f'{axis}번 축의 EEPROM Alias 또는 Position 값이 올바르지 않습니다'
                    ) from exc
                identity = identity_by_axis.get(axis)
                if isinstance(identity, dict):
                    try:
                        identity_master_index = int(
                            identity.get(
                                'ethercat_master_index',
                                ethercat_master_index,
                            )
                        )
                        identity_alias = int(identity.get('eeprom_alias'))
                        identity_position = int(identity.get('slave_position'))
                    except (TypeError, ValueError) as exc:
                        raise ValueError(
                            f'{axis}번 축의 물리 식별 정보가 완전하지 않습니다'
                        ) from exc
                    if identity_master_index != ethercat_master_index:
                        raise ValueError(
                            f'{axis}번 축의 EtherCAT Master가 실행 설정'
                            f'({ethercat_master_index})과 물리 식별 정보'
                            f'({identity_master_index})에서 다릅니다'
                        )
                    if alias != identity_alias:
                        raise ValueError(
                            f'{axis}번 축의 EEPROM Alias가 실행 설정({alias})과 '
                            f'물리 식별 정보({identity_alias})에서 다릅니다. '
                            '모터축 설정에서 확인 후 변경 내용 저장을 누르세요'
                        )
                    # **alias 를 쓰면 이 둘은 원래 다르다** · §6-201
                    #
                    # 실행 설정의 `position` 은 **alias 로부터의 상대 위치**라
                    # 항상 0 이고, 물리 식별의 `slave_position` 은 사람이 보는
                    # **링 위치**다 (0, 1, 2 …) · 같은 이름이지만 다른 값이다.
                    #
                    # 전에는 같아야 한다고 보고 견줬다 · 그래서 두 번째 서보의
                    # 주소를 바로잡자 이 검사가 막았다 · alias 로 찾을 때는
                    # 링 위치가 바뀌어도(체인 순서를 바꿔도) 그대로 찾아야
                    # 하므로, 애초에 견줄 값이 아니다.
                    #
                    # alias 가 없으면 링 위치로 찾으므로 그때는 같아야 한다.
                    if not alias and position != identity_position:
                        raise ValueError(
                            f'{axis}번 축의 Slave Position이 실행 설정({position})과 '
                            f'물리 식별 정보({identity_position})에서 다릅니다. '
                            '모터축 설정에서 확인 후 변경 내용 저장을 누르세요'
                        )
                    missing_identity = missing_ethercat_identity({
                        **identity,
                        'product_code': identity.get('product_id'),
                    })
                    if missing_identity:
                        raise ValueError(
                            f'{axis}번 축의 실제 EtherCAT 식별정보가 완전하지 않습니다: '
                            f'{", ".join(missing_identity)}. '
                            '전체 모터 검색 후 해당 검색 장비의 연결정보를 반영하고 저장하세요'
                        )
                if alias != 0:
                    alias_key = (ethercat_master_index, alias)
                    if alias_key in used_nonzero_aliases:
                        raise ValueError(
                            f'EtherCAT Master {ethercat_master_index}의 '
                            f'EEPROM Alias {alias} 값이 중복되어 있습니다'
                        )
                    used_nonzero_aliases.add(alias_key)
                else:
                    position_key = (ethercat_master_index, position)
                    if position_key in used_zero_alias_positions:
                        raise ValueError(
                            f'EtherCAT Master {ethercat_master_index}의 '
                            f'EEPROM Alias 0 Slave Position {position} 값이 '
                            '중복되어 있습니다'
                        )
                    used_zero_alias_positions.add(position_key)
            elif str(master.get('type') or '') == 'serial':
                serial_port = str(
                    master.get('serial_port') or master.get('port') or ''
                ).strip()
                if not serial_port:
                    raise ValueError(
                        f'{axis}번 축의 Dynamixel 직렬 포트가 설정되지 않았습니다'
                    )
                try:
                    bus_id = int(
                        slave.get('bus_id')
                        if slave.get('bus_id') is not None
                        else slave.get('id')
                    )
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        f'{axis}번 축의 Dynamixel ID가 올바르지 않습니다'
                    ) from exc
                if bus_id < 0 or bus_id > 252:
                    raise ValueError(
                        f'{axis}번 축의 Dynamixel ID는 0~252여야 합니다'
                    )
                serial_key = (serial_port, bus_id)
                if serial_key in used_serial_devices:
                    raise ValueError(
                        f'Dynamixel 직렬 포트 {serial_port}의 ID {bus_id}가 '
                        '중복되어 있습니다'
                    )
                used_serial_devices.add(serial_key)
                identity = identity_by_axis.get(axis)
                if isinstance(identity, dict):
                    identity_port = str(identity.get('serial_port') or '').strip()
                    try:
                        identity_bus_id = int(
                            identity.get('bus_id', identity.get('node_id'))
                        )
                    except (TypeError, ValueError) as exc:
                        raise ValueError(
                            f'{axis}번 축의 Dynamixel 물리 식별 정보가 '
                            '완전하지 않습니다'
                        ) from exc
                    if identity_port != serial_port or identity_bus_id != bus_id:
                        raise ValueError(
                            f'{axis}번 축의 Dynamixel 직렬 포트·ID가 실행 설정과 '
                            '물리 식별 정보에서 다릅니다'
                        )
            driver_id = slave.get('driver_id')
            driver = drivers.get(driver_id)
            if not isinstance(driver, dict):
                raise ValueError(
                    f'{axis}번 축의 driver_id {driver_id} 설정이 없습니다'
                )
            if (
                str(driver.get('type') or '') == 'minas'
                and str(driver.get('driver_model') or '').strip().upper()
                == 'UNVERIFIED_MINAS'
            ):
                raise ValueError(
                    f'{axis}번 축의 실제 서보 드라이버 모델이 확인되지 않았습니다. '
                    '드라이버 명판을 확인해 실제 드라이버 모델을 입력하세요'
                )
            if (
                str(driver.get('type') or '') == 'minas'
                and str(driver.get('driver_model') or '').strip()
                and (
                    profile_by_axis.get(axis, {}).get(
                        'model_confirmed',
                        identity_by_axis.get(axis, {}).get('nameplate_confirmed'),
                    ) is not True
                )
            ):
                raise ValueError(
                    f'{axis}번 축의 서보 드라이버 모델이 명판 확인되지 않았습니다. '
                    '모델·운전 프로필 설정에서 모델을 확인하고 저장하세요'
                )
            for field in required_positive:
                try:
                    value = float(driver.get(field))
                except (TypeError, ValueError):
                    value = 0.0
                if value <= 0.0:
                    raise ValueError(
                        f'{axis}번 축의 {field} 값을 0보다 크게 설정하세요'
                    )
            if str(driver.get('type') or '') == 'minas':
                velocity = float(driver['profile_velocity'])
                if velocity < 0.1:
                    raise ValueError(
                        f'{axis}번 축의 AC profile_velocity가 {velocity:g} deg/s로 '
                        '지나치게 낮습니다. 모터 모델의 운전 프로파일을 확인하세요'
                    )
