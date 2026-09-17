"""모터 하나를 가리키는 이름 · 이 낱말의 주인 · §6-141

매핑 줄은 `motor_ref` 로 모터를 가리킨다 · 같은 모터라도 부르는 이름이
둘이다 (지금 쓰는 이름과 옛 이름) · 그래서 「이 줄의 모터가 달려 있나」는
반드시 이 한 곳을 거쳐야 한다.

전에는 이 계산이 `motion_runtime` 안에만 있었다 · 웹 브리지는 모터 상태를
가지고 있으면서도 그 이름을 만들 수 없어서, 매핑 화면이 「모터가 없는 줄」을
`ok` 라고 말했다 · 정작 재생은 그 축을 건너뛰고 있었다.
"""

from typing import Any, Dict, List
from urllib.parse import quote

from motion_common.values import optional_int


def motor_type(motor: Dict[str, Any]) -> str:
    values = [
        motor.get('motor_type'),
        motor.get('motor_type_label'),
        motor.get('driver_model'),
        motor.get('driver_name'),
        motor.get('transport'),
    ]
    text = ' '.join(str(value or '').lower() for value in values)
    if 'dynamixel' in text:
        return 'dynamixel'
    if 'minas' in text or 'ac servo' in text or 'ac_servo' in text:
        return 'ac_servo'
    return 'unknown'


def motor_ref_for_motor(motor: Dict[str, Any]) -> str:
    """지금 쓰는 이름 · 못 만들면 빈 문자열."""
    kind = motor_type(motor)
    if kind == 'ac_servo':
        alias = optional_int(motor.get('alias', motor.get('ethercat_alias')))
        master_index = optional_int(motor.get('ethercat_master_index'))
        if master_index is None:
            master_index = 0
        if alias is not None and alias > 0 and master_index >= 0:
            return f'ac_servo:master:{master_index}:alias:{alias}'
        slave_position = optional_int(motor.get('slave_position'))
        return (
            f'ac_servo:master:{master_index}:slave:{slave_position}'
            if slave_position is not None
            and slave_position >= 0
            and master_index >= 0
            else ''
        )
    if kind == 'dynamixel':
        bus_id = optional_int(motor.get('bus_id', motor.get('node_id')))
        serial_port = str(motor.get('serial_port') or '').strip()
        return (
            f'dynamixel:port:{quote(serial_port, safe="")}:id:{bus_id}'
            if bus_id is not None and bus_id >= 0 and serial_port else ''
        )
    return ''


def motor_refs_for_motor(motor: Dict[str, Any]) -> List[str]:
    """이 모터를 부르는 모든 이름 · 옛 이름으로 저장된 매핑도 찾아야 한다."""
    canonical = motor_ref_for_motor(motor)
    kind = motor_type(motor)
    if kind == 'ac_servo':
        alias = optional_int(motor.get('alias', motor.get('ethercat_alias')))
        legacy = f'ac_servo:alias:{alias}' if alias is not None and alias > 0 else ''
    elif kind == 'dynamixel':
        bus_id = optional_int(motor.get('bus_id', motor.get('node_id')))
        legacy = f'dynamixel:id:{bus_id}' if bus_id is not None and bus_id >= 0 else ''
    else:
        legacy = ''
    return [item for item in (canonical, legacy) if item]


def motors_for_ref(
    motor_ref: Any, motors: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    target = str(motor_ref or '').strip().lower()
    if not target:
        return []
    return [
        motor for motor in motors
        if target in {ref.lower() for ref in motor_refs_for_motor(motor)}
    ]


def present_motor_refs(motors: List[Dict[str, Any]]) -> set:
    """지금 달려 있는 모터들의 이름 전부 · 소문자로."""
    refs = set()
    for motor in motors or []:
        if not isinstance(motor, dict):
            continue
        for ref in motor_refs_for_motor(motor):
            refs.add(str(ref).strip().lower())
    return refs
