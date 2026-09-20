"""검색 결과와 프로젝트 축을 **서버에서** 합친다 · §6-216

화면이 하던 판단을 여기로 옮긴다.

전에는 화면이 검색 응답의 날것(`ethercat_scan.slaves` · `dynamixel_scan.devices`)을
받아 스스로 판단했다.

    이 슬레이브가 프로젝트의 몇 번 축인가        `resolveRegistryMotorForScanRow`
    이 축의 이름(id)을 무엇으로 하나              `dynamixelMotorIdFromDevice` 외
    모델 이름을 뭐라고 쓰나                       `canonicalDynamixelModel`
    모델을 안다고 볼 것인가                       `axisRowDriverModel`
    새 장치에 몇 번 축을 줄 것인가                `createAxisAllocator`

같은 판단이 서버에도 있는 것이 다섯 가지였고, 그 다섯이 오늘 사고 넷을 냈다 ·
모델 이름이 달랐고(`XM540-W270` / `XM540-W270-R`), 축 이름이 달라 저장하면
선택이 풀렸고, 검색 판정이 달라 성공을 실패로 띄웠다.

**판단은 서버에서 한 번만 한다 · 화면은 받아서 그린다.**

짝 맞추는 규칙은 지금 화면이 쓰던 것을 그대로 옮긴 것이다 (바꾸지 않았다).

    EtherCAT   같은 Master 안에서
               저장된 제조번호와 검색 제조번호가 둘 다 있으면 → 제조번호로
               저장된 alias 가 있으면                        → alias 로
               저장된 rotary 가 있으면                       → rotary 로
               그래도 못 찾으면, Slave 값이 같은 축이 **딱 하나**일 때만
               「연결 확인 필요」로 짝지어 준다

    Dynamixel  포트와 ID 로
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from motion_common.values import optional_int

from motion_web_bridge.motor_config_rules import motor_id_for
from motion_web_bridge.motor_identity import (
    canonical_dynamixel_model,
    model_is_unknown,
)


def _assigned(value: Any) -> bool:
    number = optional_int(value, None)
    return number is not None and number > 0


def _master_of(motor: Dict[str, Any]) -> int:
    config = motor.get('config') or {}
    identity = motor.get('identity') or {}
    return optional_int(
        config.get('ethercat_master_index'),
        optional_int(identity.get('ethercat_master_index'), 0),
    ) or 0


def scanned_ac_servo_model(slave: Dict[str, Any]) -> str:
    """검색이 SII EEPROM 에서 읽어 온 모델 이름."""
    return str(
        slave.get('sii_order_number')
        or slave.get('order_number')
        or slave.get('sii_device_name')
        or slave.get('device_name')
        or ''
    ).strip()


def scanned_dynamixel_model(device: Dict[str, Any]) -> str:
    """검색이 모델 번호로 읽어 온 이름 · 이 프로그램이 쓰는 이름으로 바꾼다."""
    raw = str(
        device.get('model_name')
        or device.get('model')
        or device.get('driver_model')
        or ''
    ).strip()
    if not raw:
        number = optional_int(device.get('model_number'), None)
        raw = f'Model {number:,}' if number else ''
    return canonical_dynamixel_model(raw)


def _ac_servo_matches(slave: Dict[str, Any], motor: Dict[str, Any]) -> bool:
    if _master_of(motor) != (optional_int(slave.get('master_index'), 0) or 0):
        return False
    config = motor.get('config') or {}
    identity = motor.get('identity') or {}
    stored_serial = optional_int(
        config.get('serial_number'), optional_int(identity.get('serial_number'), None)
    )
    scanned_serial = optional_int(slave.get('serial_number'), None)
    if stored_serial is not None and scanned_serial is not None:
        return stored_serial == scanned_serial
    if stored_serial is not None:
        return False
    stored_alias = optional_int(
        config.get('alias'), optional_int(identity.get('ethercat_alias'), None)
    )
    scanned_alias = optional_int(slave.get('ethercat_alias'), None)
    if _assigned(stored_alias) and scanned_alias is not None:
        return stored_alias == scanned_alias
    stored_rotary = optional_int(identity.get('rotary_alias'), None)
    scanned_rotary = optional_int(slave.get('rotary_alias'), None)
    if _assigned(stored_rotary) and _assigned(scanned_rotary):
        return stored_rotary == scanned_rotary
    # Slave 값은 배선 순서일 뿐 장치가 아니다 · 사람이 한 번 짝지어 주면
    # 그때 제조번호가 저장되어 다음부터는 자동으로 맞는다.
    return False


def _ac_servo_shares_position(slave: Dict[str, Any], motor: Dict[str, Any]) -> bool:
    if _master_of(motor) != (optional_int(slave.get('master_index'), 0) or 0):
        return False
    identity = motor.get('identity') or {}
    config = motor.get('config') or {}
    stored = optional_int(
        identity.get('slave_position'), optional_int(config.get('position'), None)
    )
    scanned = optional_int(slave.get('slave_position'), None)
    return stored is not None and scanned is not None and stored == scanned


def _dynamixel_matches(device: Dict[str, Any], motor: Dict[str, Any]) -> bool:
    identity = motor.get('identity') or {}
    config = motor.get('config') or {}
    stored_id = optional_int(
        identity.get('bus_id'),
        optional_int(identity.get('node_id'), optional_int(config.get('bus_id'), None)),
    )
    if stored_id is None:
        return False
    if stored_id != optional_int(device.get('id'), None):
        return False
    stored_port = str(identity.get('serial_port') or config.get('serial_port') or '')
    scanned_port = str(device.get('port') or '')
    if stored_port and scanned_port and stored_port != scanned_port:
        return False
    return True


def _stored_view(motor: Dict[str, Any]) -> Dict[str, Any]:
    identity = motor.get('identity') or {}
    config = motor.get('config') or {}
    profile = motor.get('profile') or {}
    model = str(profile.get('driver_model') or '').strip()
    return {
        'model': '' if model_is_unknown(model) else model,
        'alias': optional_int(
            identity.get('ethercat_alias'), optional_int(config.get('alias'), None)
        ),
        'rotary_alias': optional_int(identity.get('rotary_alias'), None),
        'slave_position': optional_int(
            identity.get('slave_position'), optional_int(config.get('position'), None)
        ),
        'serial_number': optional_int(identity.get('serial_number'), None),
        'vendor_id': optional_int(identity.get('vendor_id'), None),
        'product_code': optional_int(identity.get('product_code'), None),
        'master_index': _master_of(motor) if motor.get('transport') == 'ethercat' else None,
        'serial_port': identity.get('serial_port') or config.get('serial_port') or '',
        'bus_id': optional_int(
            identity.get('bus_id'), optional_int(config.get('bus_id'), None)
        ),
    }


def _scanned_ac_servo_view(slave: Dict[str, Any]) -> Dict[str, Any]:
    return {
        'model': scanned_ac_servo_model(slave),
        'alias': optional_int(slave.get('ethercat_alias'), None),
        'rotary_alias': optional_int(slave.get('rotary_alias'), None),
        'slave_position': optional_int(slave.get('slave_position'), None),
        'serial_number': optional_int(slave.get('serial_number'), None),
        'vendor_id': optional_int(slave.get('vendor_id'), None),
        'product_code': optional_int(slave.get('product_code'), None),
        'master_index': optional_int(slave.get('master_index'), 0),
        'device_state': str(slave.get('device_state') or ''),
        'identity_source': str(slave.get('identity_source') or ''),
        'revision_number': optional_int(slave.get('revision_number'), None),
        'serial_port': '',
        'bus_id': None,
    }


def _scanned_dynamixel_view(device: Dict[str, Any]) -> Dict[str, Any]:
    return {
        'model': scanned_dynamixel_model(device),
        'alias': None,
        'rotary_alias': None,
        'slave_position': None,
        'serial_number': None,
        'vendor_id': None,
        'product_code': None,
        'master_index': None,
        'device_state': '',
        'identity_source': str(device.get('source') or ''),
        'model_number': optional_int(device.get('model_number'), None),
        'firmware_version': optional_int(device.get('firmware_version'), None),
        'serial_port': str(device.get('port') or ''),
        'bus_id': optional_int(device.get('id'), None),
    }


#: 저장된 값과 검색값을 견주는 항목 · 다른 것만 이름을 댄다
COMPARED_FIELDS = (
    'model',
    'alias',
    'rotary_alias',
    'slave_position',
    'serial_number',
    'vendor_id',
    'product_code',
    'serial_port',
    'bus_id',
)


def _changed_fields(stored: Dict[str, Any], scanned: Dict[str, Any]) -> List[str]:
    """저장된 값과 검색값이 **둘 다 있는데 다른** 항목만 고른다.

    한쪽이 비어 있는 것은 「다르다」가 아니라 「아직 모른다」이다 · 그것까지
    바뀌었다고 하면 새 프로젝트가 온통 빨갛게 보인다.
    """
    changed = []
    for field in COMPARED_FIELDS:
        old, new = stored.get(field), scanned.get(field)
        if old in (None, '') or new in (None, ''):
            continue
        if old != new:
            changed.append(field)
    return changed


def _axis_row(
    motor: Dict[str, Any],
    scanned: Optional[Dict[str, Any]],
    state: str,
    confirmation_required: bool = False,
) -> Dict[str, Any]:
    stored = _stored_view(motor)
    return {
        'id': str(motor.get('id') or ''),
        'axis': optional_int(
            (motor.get('config') or {}).get('controller_index'),
            optional_int(motor.get('axis'), None),
        ),
        'name': str(motor.get('name') or ''),
        'transport': str(motor.get('transport') or ''),
        'motor_type': str(motor.get('motor_type') or ''),
        'enabled': bool(motor.get('enabled')),
        'state': state,
        'confirmation_required': confirmation_required,
        'stored': stored,
        'scanned': scanned,
        'changed': _changed_fields(stored, scanned) if scanned else [],
        'model': (scanned or {}).get('model') or stored.get('model') or '',
    }


def _new_device_row(
    scanned: Dict[str, Any],
    motor_type: str,
    transport: str,
    axis: int,
    association_candidates: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    return {
        # 위치가 겹치는 기존 축이 **여럿**이라 자동으로 못 정한 경우 · §6-216
        #
        # 하나뿐이면 위에서 「연결 확인 필요」로 짝지어 준다 · 여럿이면
        # 어느 것인지 사람만 안다 · 전에는 화면이 그중 **첫 번째**를 골랐다.
        'association_candidates': list(association_candidates or []),
        'id': motor_id_for(
            motor_type=motor_type,
            transport=transport,
            axis=axis,
            ethercat_master_index=scanned.get('master_index'),
            alias=scanned.get('alias'),
            rotary_alias=scanned.get('rotary_alias'),
            slave_position=scanned.get('slave_position'),
            serial_port=scanned.get('serial_port'),
            bus_id=scanned.get('bus_id'),
        ),
        'proposed_axis': axis,
        'motor_type': motor_type,
        'transport': transport,
        'scanned': scanned,
        'model': scanned.get('model') or '',
    }


def axis_rows_from_scan(
    registry: Dict[str, Any],
    scan: Dict[str, Any],
) -> Dict[str, Any]:
    """프로젝트 축과 검색 결과를 합쳐 **화면이 그릴 행 목록**을 만든다."""
    motors = [
        motor
        for motor in (registry or {}).get('motors') or []
        if isinstance(motor, dict) and not motor.get('deleted')
    ]
    scan = scan or {}
    ethercat = scan.get('ethercat_scan') or {}
    dynamixel = scan.get('dynamixel_scan') or {}
    ethercat_scanned = bool(ethercat) and not ethercat.get('skipped')
    dynamixel_scanned = bool(dynamixel) and not dynamixel.get('skipped')
    slaves = [s for s in ethercat.get('slaves') or [] if isinstance(s, dict)]
    devices = [d for d in dynamixel.get('devices') or [] if isinstance(d, dict)]

    paired: Dict[str, Dict[str, Any]] = {}
    confirmation: Dict[str, bool] = {}
    used_slaves = set()
    used_devices = set()

    for index, slave in enumerate(slaves):
        match = next((m for m in motors if m.get('transport') == 'ethercat'
                      and _ac_servo_matches(slave, m)), None)
        required = False
        if match is None:
            shares = [m for m in motors if m.get('transport') == 'ethercat'
                      and _ac_servo_shares_position(slave, m)]
            if len(shares) == 1:
                match, required = shares[0], True
        if match is None:
            continue
        paired[str(match.get('id'))] = _scanned_ac_servo_view(slave)
        confirmation[str(match.get('id'))] = required
        used_slaves.add(index)

    for index, device in enumerate(devices):
        match = next((m for m in motors if m.get('transport') != 'ethercat'
                      and _dynamixel_matches(device, m)), None)
        if match is None:
            continue
        paired[str(match.get('id'))] = _scanned_dynamixel_view(device)
        used_devices.add(index)

    axes = []
    for motor in motors:
        motor_id = str(motor.get('id') or '')
        scanned = paired.get(motor_id)
        if scanned is not None:
            state = 'matched'
        elif motor.get('transport') == 'ethercat':
            state = 'missing' if ethercat_scanned else 'not_scanned'
        else:
            state = 'missing' if dynamixel_scanned else 'not_scanned'
        axes.append(
            _axis_row(motor, scanned, state, confirmation.get(motor_id, False))
        )
    axes.sort(key=lambda row: (row['axis'] is None, row['axis'] or 0, row['id']))

    used_axes = {row['axis'] for row in axes if row['axis'] is not None}
    next_axis = (max(used_axes) + 1) if used_axes else 0
    new_devices = []
    for index, slave in enumerate(slaves):
        if index in used_slaves:
            continue
        candidates = [
            optional_int(
                (m.get('config') or {}).get('controller_index'),
                optional_int(m.get('axis'), None),
            )
            for m in motors
            if m.get('transport') == 'ethercat' and _ac_servo_shares_position(slave, m)
        ]
        new_devices.append(
            _new_device_row(
                _scanned_ac_servo_view(slave),
                'ac_servo',
                'ethercat',
                next_axis,
                candidates,
            )
        )
        next_axis += 1
    for index, device in enumerate(devices):
        if index in used_devices:
            continue
        new_devices.append(
            _new_device_row(_scanned_dynamixel_view(device), 'dynamixel', 'serial', next_axis)
        )
        next_axis += 1

    return {
        #: 이번 검색이 실제로 돌아간 통로 · 목록을 이 통로만 맞춘다
        'scanned_transports': (
            (['ethercat'] if ethercat_scanned else [])
            + (['serial'] if dynamixel_scanned else [])
        ),
        'axes': axes,
        'new_devices': new_devices,
        'summary': {
            'matched': sum(1 for row in axes if row['state'] == 'matched'),
            'missing': sum(1 for row in axes if row['state'] == 'missing'),
            'changed': sum(1 for row in axes if row['changed']),
            'new': len(new_devices),
        },
    }
