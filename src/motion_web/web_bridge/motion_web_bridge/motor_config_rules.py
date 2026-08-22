"""모터 설정·스캔 판정 규칙 · 상태 비의존.

`MotionWebBridge`에서 떼어낸 순수 함수 모음이다. 노드의 상태도 락도 건드리지
않고 인자로 받은 값만 보고 판단하므로, 노드를 띄우지 않고 단위 테스트할 수 있다.

`test_pure_modules.py`가 이 성질을 지킨다 · `self` 접근이나
`bridge_node` import가 끼어들면 실패한다.
"""

from __future__ import annotations

import copy
import re
import subprocess
import time
from urllib.parse import quote
from typing import Any, Dict, List, Optional

from motion_common.values import optional_int


def is_ac_servo_motor(motor: Dict[str, Any]) -> bool:
    values = [
        motor.get('motor_type'),
        motor.get('motor_type_label'),
        motor.get('driver_model'),
        motor.get('driver_name'),
        motor.get('transport'),
    ]
    text = ' '.join(str(value or '').lower() for value in values)
    return 'minas' in text or 'ac servo' in text or 'ac_servo' in text


def is_dynamixel_motor(motor: Dict[str, Any]) -> bool:
    values = [
        motor.get('motor_type'),
        motor.get('motor_type_label'),
        motor.get('driver_model'),
        motor.get('driver_name'),
        motor.get('transport'),
    ]
    text = ' '.join(str(value or '').lower() for value in values)
    return 'dynamixel' in text


def empty_motor_registry() -> Dict[str, Any]:
    return {
        'version': 1,
        'updated_at': None,
        'motors': [],
    }


def normalize_motor_entry(motor: Dict[str, Any], index: int) -> Dict[str, Any]:
    motor_type = str(motor.get('motor_type') or 'unknown')
    transport = str(motor.get('transport') or 'unknown')
    driver_family = str(motor.get('driver_family') or motor_type)
    identity = dict(motor.get('identity')) if isinstance(motor.get('identity'), dict) else {}
    profile = dict(motor.get('profile')) if isinstance(motor.get('profile'), dict) else {}
    if not profile.get('driver_model') and identity.get('driver_model'):
        profile['driver_model'] = identity.pop('driver_model')
    else:
        identity.pop('driver_model', None)
    if 'model_confirmed' not in profile and 'nameplate_confirmed' in identity:
        profile['model_confirmed'] = identity.get('nameplate_confirmed') is True
    identity.pop('nameplate_confirmed', None)
    if not profile.get('model_source') and profile.get('model_confirmed') is True:
        profile['model_source'] = 'user_nameplate'
    config = dict(motor.get('config')) if isinstance(motor.get('config'), dict) else {}

    def optional_int(value: Any, default: Optional[int]) -> Optional[int]:
        if value is None or value == '':
            return default
        try:
            return int(str(value), 0)
        except (TypeError, ValueError):
            return default

    axis = optional_int(
        config.get('controller_index'),
        optional_int(motor.get('axis'), None),
    )
    if axis is not None:
        config['controller_index'] = axis

    motor_id = str(motor.get('id') or '').strip()
    if not motor_id:
        motor_id = f'{transport}_{motor_type}_{index}'

    return {
        'id': motor_id,
        'enabled': bool(motor.get('enabled', False)),
        'hidden': bool(motor.get('hidden', False)),
        'deleted': bool(motor.get('deleted', False)),
        'axis': axis,
        'name': str(motor.get('name') or ''),
        'motor_type': motor_type,
        'driver_family': driver_family,
        'transport': transport,
        'identity': identity,
        'profile': profile,
        'config': config,
    }


def normalize_motor_registry(registry: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(registry, dict):
        registry = {}

    motors = registry.get('motors', [])
    if not isinstance(motors, list):
        motors = []

    normalized_motors: List[Dict[str, Any]] = []
    used_ids = set()
    for index, motor in enumerate(motors):
        if not isinstance(motor, dict):
            continue
        normalized = normalize_motor_entry(motor, index)
        motor_id = str(normalized['id'])
        if motor_id in used_ids:
            motor_id = f'{motor_id}_{index}'
            normalized['id'] = motor_id
        used_ids.add(motor_id)
        normalized_motors.append(normalized)

    return {
        'version': int(registry.get('version') or 1),
        'updated_at': registry.get('updated_at'),
        'motors': normalized_motors,
    }


def expand_shared_driver_profiles(config: Dict[str, Any]) -> Dict[str, Any]:
    """Give every configured axis an independent driver profile.

    The runtime schema stores limits on driver entries. Reusing one driver ID
    therefore makes lower/upper and velocity settings change for every axis
    that references it. Cloning only repeated references preserves the schema
    while making axis editing independent for AC Servo and Dynamixel alike.
    """
    if not isinstance(config, dict):
        return config

    expanded = copy.deepcopy(config)
    drivers = expanded.get('drivers')
    masters = expanded.get('masters')
    if not isinstance(drivers, list) or not isinstance(masters, list):
        return expanded

    drivers_by_id = {
        optional_int(driver.get('id'), None): driver
        for driver in drivers
        if isinstance(driver, dict)
        and optional_int(driver.get('id'), None) is not None
    }
    used_ids = set(drivers_by_id)
    next_id = max(used_ids | {-1}) + 1
    reference_counts: Dict[int, int] = {}

    for master in masters:
        if not isinstance(master, dict):
            continue
        slaves = master.get('slaves')
        if not isinstance(slaves, list):
            continue
        for slave in slaves:
            if not isinstance(slave, dict):
                continue
            driver_id = optional_int(slave.get('driver_id'), None)
            if driver_id is None or driver_id not in drivers_by_id:
                continue
            count = reference_counts.get(driver_id, 0)
            reference_counts[driver_id] = count + 1
            if count == 0:
                continue

            while next_id in used_ids:
                next_id += 1
            cloned_driver = copy.deepcopy(drivers_by_id[driver_id])
            cloned_driver['id'] = next_id
            drivers.append(cloned_driver)
            slave['driver_id'] = next_id
            used_ids.add(next_id)
            next_id += 1

    return expanded


def prune_unused_drivers(
    drivers: List[Dict[str, Any]],
    masters: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    used_driver_ids = set()
    for master in masters:
        if not isinstance(master, dict):
            continue
        slaves = master.get('slaves', [])
        if not isinstance(slaves, list):
            continue
        for slave in slaves:
            if not isinstance(slave, dict):
                continue
            driver_id = optional_int(slave.get('driver_id'), None)
            if driver_id is not None:
                used_driver_ids.add(driver_id)

    return [
        driver
        for driver in drivers
        if not isinstance(driver, dict)
        or optional_int(driver.get('id'), None) is None
        or optional_int(driver.get('id'), None) in used_driver_ids
    ]


def registry_from_motor_config(config: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(config, dict):
        config = {}
    identity_by_axis = {
        optional_int(item.get('controller_index'), None): item
        for item in config.get('web_axis_identities', [])
        if isinstance(item, dict)
        and optional_int(item.get('controller_index'), None) is not None
    }
    profile_by_axis = {
        optional_int(item.get('controller_index'), None): item
        for item in config.get('web_axis_profiles', [])
        if isinstance(item, dict)
        and optional_int(item.get('controller_index'), None) is not None
    }
    drivers_by_id = {
        int(driver.get('id')): driver
        for driver in config.get('drivers', [])
        if isinstance(driver, dict) and driver.get('id') is not None
    }

    motors: List[Dict[str, Any]] = []
    for master in config.get('masters', []):
        if not isinstance(master, dict):
            continue
        master_id = optional_int(master.get('id'), 0)
        transport = str(master.get('type') or 'unknown')
        ethercat_master_index = optional_int(
            master.get('ethercat_master_index'), 0
        )
        serial_port = master.get('serial_port') or master.get('port')
        serial_baudrate = optional_int(
            master.get('serial_baudrate'),
            optional_int(master.get('baudrate'), None),
        )
        for index, slave in enumerate(master.get('slaves', [])):
            if not isinstance(slave, dict):
                continue
            driver_id = optional_int(slave.get('driver_id'), 0)
            driver = drivers_by_id.get(driver_id, {})
            driver_family = str(driver.get('type') or 'unknown')
            motor_type = 'ac_servo' if driver_family == 'minas' else driver_family
            axis = optional_int(slave.get('controller_index'), index)
            alias = optional_int(slave.get('alias'), None)
            web_identity = identity_by_axis.get(axis, {})
            web_profile = profile_by_axis.get(axis, {})
            bus_id = optional_int(
                slave.get('bus_id'),
                optional_int(slave.get('id'), None),
            )
            slave_position = optional_int(slave.get('position'), index)
            name = str(slave.get('name') or f'Axis {axis}')
            motor_id = (
                f'{motor_type}_{transport}_master_{ethercat_master_index}_alias_{alias}'
                if transport == 'ethercat' and alias is not None and alias > 0
                else (
                    f'{motor_type}_{transport}_master_'
                    f'{ethercat_master_index}_slave_{slave_position}'
                )
                if transport == 'ethercat'
                else (
                    f'{motor_type}_{transport}_port_'
                    f'{quote(str(serial_port or ""), safe="")}_id_{bus_id}'
                )
                if bus_id is not None
                else f'{motor_type}_{transport}_axis_{axis}'
            )
            motors.append(
                normalize_motor_entry(
                    {
                        'id': motor_id,
                        'enabled': True,
                        'hidden': False,
                        'deleted': False,
                        'axis': axis,
                        'name': name,
                        'motor_type': motor_type,
                        'driver_family': driver_family,
                        'transport': transport,
                        'identity': {
                            'ethercat_master_index': (
                                ethercat_master_index
                                if transport == 'ethercat'
                                else None
                            ),
                            'rotary_alias': optional_int(
                                web_identity.get('rotary_alias'), None
                            ),
                            'ethercat_alias': optional_int(
                                web_identity.get('eeprom_alias'), alias
                            ),
                            'node_id': bus_id,
                            'bus_id': bus_id,
                            'serial_port': serial_port,
                            'serial_baudrate': serial_baudrate,
                            'slave_position': optional_int(
                                web_identity.get('slave_position'),
                                slave_position
                                if alias in (None, 0) else None,
                            ),
                            'identity_source': str(
                                web_identity.get('identity_source') or ''
                            ),
                            'vendor_id': optional_int(
                                web_identity.get('vendor_id'),
                                optional_int(slave.get('vendor_id'), None),
                            ),
                            'product_code': optional_int(
                                web_identity.get('product_id'),
                                optional_int(slave.get('product_id'), None),
                            ),
                            'revision_number': optional_int(
                                web_identity.get('revision_number'), None
                            ),
                            'serial_number': optional_int(
                                web_identity.get('serial_number'), None
                            ),
                            'sii_order_number': str(
                                web_identity.get('sii_order_number') or ''
                            ),
                            'sii_device_name': str(
                                web_identity.get('sii_device_name') or ''
                            ),
                        },
                        'profile': {
                            'driver_model': str(
                                web_profile.get('driver_model')
                                or driver.get('driver_model')
                                or ''
                            ),
                            'model_confirmed': (
                                web_profile.get(
                                    'model_confirmed',
                                    web_identity.get('nameplate_confirmed'),
                                ) is True
                            ),
                            'model_source': str(
                                web_profile.get('model_source')
                                or (
                                    'user_nameplate'
                                    if web_identity.get('nameplate_confirmed') is True
                                    else ''
                                )
                            ),
                        },
                        'config': {
                            'controller_index': axis,
                            'ethercat_master_index': (
                                ethercat_master_index
                                if transport == 'ethercat'
                                else None
                            ),
                            'master_id': master_id,
                            'driver_id': driver_id,
                            'bus_id': bus_id,
                            'serial_port': serial_port,
                            'serial_baudrate': serial_baudrate,
                            'alias': alias,
                            'position': slave_position,
                            'vendor_id': optional_int(slave.get('vendor_id'), None),
                            'product_id': optional_int(slave.get('product_id'), None),
                            'profile_mode': optional_int(slave.get('profile_mode'), 0),
                        },
                    },
                    len(motors),
                )
            )

    return {
        'version': 1,
        'updated_at': None,
        'motors': motors,
    }


def scan_item_has_detected_devices(scan_item: Any) -> bool:
    if not isinstance(scan_item, dict) or scan_item.get('skipped') is True:
        return False
    for key in ('slaves_count', 'devices_count'):
        try:
            if int(scan_item.get(key) or 0) > 0:
                return True
        except (TypeError, ValueError):
            pass
    for key in ('slaves', 'devices'):
        value = scan_item.get(key)
        if isinstance(value, list) and len(value) > 0:
            return True
    return False


def scan_operation_outcome(
    scan: Any,
    *,
    operation_type: str,
    fallback_success: bool,
) -> str:
    if not isinstance(scan, dict):
        return 'success' if fallback_success else 'failure'

    ethercat = scan.get('ethercat_scan')
    dynamixel = scan.get('dynamixel_scan')
    physical = scan.get('physical_scan')
    if isinstance(physical, dict):
        if not isinstance(ethercat, dict):
            ethercat = physical.get('ethercat')
        if not isinstance(dynamixel, dict):
            dynamixel = physical.get('dynamixel')

    if operation_type == 'full_scan':
        requested = [ethercat, dynamixel]
    elif operation_type == 'ac_servo_scan':
        requested = [ethercat]
    elif operation_type == 'dynamixel_scan':
        requested = [dynamixel]
    else:
        requested = [
            item
            for item in (ethercat, dynamixel)
            if isinstance(item, dict) and item.get('skipped') is not True
        ]
    requested = [item for item in requested if isinstance(item, dict)]
    if not requested:
        return 'success' if fallback_success else 'failure'
    project_comparison = scan.get('project_comparison')
    ethercat_project = (
        project_comparison.get('ethercat_project')
        if isinstance(project_comparison, dict)
        else None
    )
    if (
        operation_type in {'full_scan', 'ac_servo_scan'}
        and isinstance(ethercat_project, dict)
        and ethercat_project.get('available') is True
        and ethercat_project.get('compatible') is not True
    ):
        return 'failure'
    completed = [item.get('complete') is True for item in requested]
    if all(completed):
        return 'success'
    if any(completed):
        return 'partial'
    if any(
        scan_item_has_detected_devices(item)
        for item in requested
    ):
        return 'partial'
    if (
        operation_type in {'full_scan', 'ac_servo_scan'}
        and isinstance(ethercat_project, dict)
        and ethercat_project.get('compatible') is True
    ):
        return 'partial'
    return 'failure'


def scan_result_message(
    success: bool,
    scan: Any,
    fallback: str,
) -> str:
    """Keep scan evidence in ``scan`` and expose only a concise UI message."""
    if not isinstance(scan, dict):
        return fallback

    ethercat = scan.get('ethercat_scan')
    if not isinstance(ethercat, dict):
        physical = scan.get('physical_scan')
        if isinstance(physical, dict):
            ethercat = physical.get('ethercat')

    dynamixel = scan.get('dynamixel_scan')
    if not isinstance(dynamixel, dict):
        physical = scan.get('physical_scan')
        if isinstance(physical, dict):
            dynamixel = physical.get('dynamixel')
    requested = [
        item
        for item in (ethercat, dynamixel)
        if isinstance(item, dict) and item.get('skipped') is not True
    ]
    project_comparison = scan.get('project_comparison')
    ethercat_project = (
        project_comparison.get('ethercat_project')
        if isinstance(project_comparison, dict)
        else None
    )
    project_compatible = bool(
        isinstance(ethercat_project, dict)
        and ethercat_project.get('compatible') is True
    )
    project_incompatible = bool(
        isinstance(ethercat_project, dict)
        and ethercat_project.get('available') is True
        and ethercat_project.get('compatible') is not True
    )
    partial = bool(
        not success
        and not project_incompatible
        and (
            project_compatible
            or (
                any(item.get('complete') is True for item in requested)
                and any(item.get('complete') is not True for item in requested)
            )
            or any(
                scan_item_has_detected_devices(item)
                for item in requested
            )
        )
    )
    parts = [
        (
            '모터 검색 완료'
            if success
            else '모터 검색 부분 완료'
            if partial
            else '모터 검색 실패'
        )
    ]
    if isinstance(ethercat, dict) and ethercat.get('skipped') is not True:
        try:
            parts.append(f'AC Servo {int(ethercat.get("slaves_count") or 0)}축')
        except (TypeError, ValueError):
            pass
        master_rows = ethercat.get('masters')
        if isinstance(master_rows, list) and master_rows:
            parts.append(
                ' / '.join(
                    (
                        f'Master {int(row.get("master_index") or 0)} '
                        f'{int(row.get("slaves_count") or 0)}축'
                    )
                    for row in master_rows
                    if isinstance(row, dict)
                )
            )
    if isinstance(dynamixel, dict) and dynamixel.get('skipped') is not True:
        try:
            parts.append(f'Dynamixel {int(dynamixel.get("devices_count") or 0)}축')
        except (TypeError, ValueError):
            pass
    if project_compatible:
        parts.append('프로젝트 EtherCAT 구성 확인 완료')
        unused = ethercat_project.get('unused_registered_master_indices') or []
        if unused:
            parts.append(
                '미사용 Master '
                + ', '.join(str(index) for index in unused)
                + ' 미연결 허용'
            )

    scan_id = str(scan.get('scan_id') or '').strip()
    if scan_id:
        parts.append(f'scan_id {scan_id}')
    errors = scan.get('scan_errors')
    if not success and isinstance(errors, list):
        concise_errors = [
            str(
                error.get('message')
                if isinstance(error, dict)
                else error
            ).strip()
            for error in errors[:2]
            if str(
                error.get('message')
                if isinstance(error, dict)
                else error
            ).strip()
        ]
        if concise_errors:
            parts.append(', '.join(concise_errors))
    return ' · '.join(parts)


def wait_for_ethercat_release(timeout_sec: float) -> None:
    deadline = time.time() + timeout_sec
    last_output = ''
    while time.time() < deadline:
        master = subprocess.run(
            ['ethercat', 'master'],
            check=False,
            capture_output=True,
            text=True,
            timeout=2.0,
        )
        last_output = master.stderr.strip() or master.stdout.strip()
        if master.returncode == 0:
            claimed = bool(
                re.search(
                    r'^\s*Phase:\s*Operation\s*$',
                    master.stdout,
                    re.MULTILINE | re.IGNORECASE,
                )
                or re.search(
                    r'^\s*Active:\s*yes\s*$',
                    master.stdout,
                    re.MULTILINE | re.IGNORECASE,
                )
            )
            if not claimed:
                slaves = subprocess.run(
                    ['ethercat', 'slaves'],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=2.0,
                )
                last_output = slaves.stderr.strip() or slaves.stdout.strip()
                active_slave = bool(
                    slaves.returncode == 0
                    and re.search(
                        r'^\s*\d+\s+\S+\s+(?:SAFEOP|OP)\b',
                        slaves.stdout,
                        re.MULTILINE | re.IGNORECASE,
                    )
                )
                if slaves.returncode == 0 and not active_slave:
                    return
        time.sleep(0.05)
    raise RuntimeError(
        'Motor Manager 정지 후에도 EtherCAT Master 또는 Slave 운전 상태가 해제되지 않았습니다'
        + (f': {last_output}' if last_output else '')
    )


def schedule_managed_service_restart(*managed_services: str) -> None:
    """Return the HTTP response before stopping the process serving it.

    Starting systemctl immediately races the API response against
    Uvicorn shutdown.  A detached, fixed-command shell gives the response
    a short window to leave the socket, then asks systemd to restart the
    validated service.  Positional arguments keep the service name out of
    shell parsing.
    """
    allowed_services = {
        'motion-control.service',
        'motion-motor.service',
        'motion-coordination.service',
    }
    if (
        not managed_services
        or any(service not in allowed_services for service in managed_services)
    ):
        raise ValueError('허용되지 않은 자동실행 서비스 이름입니다')
    subprocess.Popen(
        [
            '/bin/bash',
            '-c',
            'sleep 0.5; exec "$@"',
            'motion-control-delayed-restart',
            '/usr/bin/systemctl',
            '--user',
            'restart',
            '--no-block',
            *managed_services,
        ],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
