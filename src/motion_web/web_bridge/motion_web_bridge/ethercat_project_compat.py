"""EtherCAT 물리 스캔 결과와 선택 프로젝트 설정의 대조 · 노드 비의존.

`MotionWebBridge._annotate_ethercat_project_compatibility`에서 떼어냈다.
노드에 묶인 것은 `self.load_motor_config()` 호출 하나뿐이었으므로 콜러블로 받는다
· §6-13

물리 스캔 자체는 바꾸지 않는다. 등록된 Master는 모두 재스캔되고 보고되며,
이 판정은 **선택 프로젝트가 쓰는 Master만 정확히 일치하는가**에만 답한다.
쓰이지 않는 Master의 미응답을 프로젝트 불일치로 오해하지 않기 위한 구분이다.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List

import yaml

from motion_common.values import optional_int


def annotate_ethercat_project_compatibility(
    scan: Dict[str, Any],
    load_motor_config: Callable[[], Dict[str, Any]],
) -> None:
    """Compare physical EtherCAT evidence with the selected project.

    Physical scan completeness remains unchanged: every registered Master
    is still rescanned and reported.  This additional result only answers
    whether the Masters used by the selected project match exactly, so an
    unused disconnected Master is not confused with a project mismatch.
    """
    ethercat = scan.get('ethercat_scan')
    if not isinstance(ethercat, dict) or ethercat.get('skipped') is True:
        return

    comparison = scan.setdefault('project_comparison', {})
    if not isinstance(comparison, dict):
        comparison = {}
        scan['project_comparison'] = comparison

    try:
        registry_result = load_motor_config()
    except (OSError, ValueError, yaml.YAMLError) as exc:
        comparison['ethercat_project'] = {
            'available': False,
            'compatible': False,
            'message': f'현재 프로젝트 모터축 설정 확인 실패: {exc}',
        }
        return
    if registry_result.get('success') is not True:
        comparison['ethercat_project'] = {
            'available': False,
            'compatible': False,
            'message': str(
                registry_result.get('message')
                or '현재 프로젝트 모터축 설정을 확인할 수 없습니다'
            ),
        }
        return

    expected_by_master: Dict[int, List[Dict[str, Any]]] = {}
    registry = registry_result.get('registry')
    for motor in (
        registry.get('motors', [])
        if isinstance(registry, dict)
        else []
    ):
        if not isinstance(motor, dict):
            continue
        if str(motor.get('transport') or '').lower() != 'ethercat':
            continue
        if motor.get('enabled') is False or motor.get('deleted') is True:
            continue
        config = motor.get('config') if isinstance(motor.get('config'), dict) else {}
        identity = (
            motor.get('identity')
            if isinstance(motor.get('identity'), dict)
            else {}
        )
        master_index = optional_int(
            config.get('ethercat_master_index'),
            optional_int(identity.get('ethercat_master_index'), 0),
        )
        position = optional_int(config.get('position'), None)
        if master_index is None or master_index < 0 or position is None:
            continue
        expected_by_master.setdefault(master_index, []).append({
            'controller_index': optional_int(
                config.get('controller_index'),
                optional_int(motor.get('axis'), None),
            ),
            'position': position,
            'alias': optional_int(
                identity.get('ethercat_alias'),
                optional_int(config.get('alias'), 0),
            ),
            'vendor_id': optional_int(
                identity.get('vendor_id'),
                optional_int(config.get('vendor_id'), None),
            ),
            'product_code': optional_int(
                identity.get('product_code'),
                optional_int(config.get('product_id'), None),
            ),
            'serial_number': optional_int(
                identity.get('serial_number'), None
            ),
        })

    if not expected_by_master:
        comparison['ethercat_project'] = {
            'available': False,
            'compatible': False,
            'message': '현재 프로젝트에 EtherCAT 모터축 설정이 없습니다',
            'required_master_indices': [],
        }
        return

    observed_by_master: Dict[int, List[Dict[str, Any]]] = {}
    for slave in ethercat.get('slaves') or []:
        if not isinstance(slave, dict):
            continue
        master_index = optional_int(slave.get('master_index'), 0)
        if master_index is None:
            continue
        observed_by_master.setdefault(master_index, []).append(slave)

    master_rows = []
    compatible = True
    for master_index in sorted(expected_by_master):
        expected = expected_by_master[master_index]
        observed = observed_by_master.get(master_index, [])
        errors = []
        if len(observed) != len(expected):
            errors.append(f'축 수 {len(observed)}/{len(expected)}')
        observed_by_position = {
            optional_int(item.get('slave_position'), None): item
            for item in observed
            if optional_int(item.get('slave_position'), None) is not None
        }
        for target in expected:
            position = target['position']
            actual = observed_by_position.get(position)
            if actual is None:
                errors.append(f'Slave {position} 응답 없음')
                continue
            if actual.get('direct_read_complete') is not True:
                errors.append(
                    f'Slave {position} 물리 식별정보 읽기 미완료'
                )
            for field in ('vendor_id', 'product_code', 'serial_number'):
                expected_value = target.get(field)
                actual_value = optional_int(actual.get(field), None)
                if (
                    expected_value is not None
                    and expected_value > 0
                    and actual_value != expected_value
                ):
                    errors.append(
                        f'Slave {position} {field} 불일치'
                    )
            expected_alias = target.get('alias')
            actual_alias = optional_int(
                actual.get('ethercat_alias'), 0
            )
            if (
                expected_alias is not None
                and expected_alias > 0
                and actual_alias != expected_alias
            ):
                errors.append(f'Slave {position} EEPROM Alias 불일치')
        row_complete = not errors
        compatible = compatible and row_complete
        master_rows.append({
            'master_index': master_index,
            'expected_slaves_count': len(expected),
            'observed_slaves_count': len(observed),
            'compatible': row_complete,
            'errors': errors,
        })

    registered_indices = {
        optional_int(row.get('master_index'), 0)
        for row in ethercat.get('masters') or []
        if isinstance(row, dict)
    }
    required_indices = sorted(expected_by_master)
    unused_indices = sorted(
        index
        for index in registered_indices
        if index is not None and index not in expected_by_master
    )
    comparison['ethercat_project'] = {
        'available': True,
        'compatible': compatible,
        'required_master_indices': required_indices,
        'unused_registered_master_indices': unused_indices,
        'masters': master_rows,
        'message': (
            f'프로젝트 EtherCAT 구성 확인 완료 · '
            f'Master {", ".join(str(index) for index in required_indices)}'
            if compatible
            else '프로젝트 EtherCAT 구성 불일치'
        ),
    }
