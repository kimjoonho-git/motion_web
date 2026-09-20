"""설정 파일을 지우고 처음 검색하면 늘 실패로 뜨던 것 · §6-218

    설정 파일 삭제 → 전체 모터 검색
    → 「전체 모터 검색에 실패했습니다」
    → 버스 위에는 서보 2대 · 다이나믹셀 2대가 멀쩡히 보이는데도

프로젝트에 등록된 EtherCAT 축이 없으면 `ethercat_project` 가
`compatible: False` 였다 · 판정은 그것을 「프로젝트가 필요로 하는 것을 못
찾았다」로 읽는다 · 그래서 랜선이 빠진 미사용 Master 하나만 있어도 첫 검색이
반드시 실패가 됐다.

    masters: [ {0: complete}, {1: '재스캔 후 응답한 Slave가 없습니다'} ]

**필요한 것이 없으면 못 찾은 것도 없다** · §6-198 과 같은 이야기이고, 그때
빠뜨린 경우다.
"""

import pytest

from motion_web_bridge import ethercat_project_compat, motor_config_rules


def _scan(masters_complete=(True, False), slaves=2):
    return {
        'ethercat_scan': {
            'available': True,
            'complete': all(masters_complete),
            'slaves_count': slaves,
            'slaves': [
                {'master_index': 0, 'slave_position': index, 'ethercat_alias': 103 + index,
                 'vendor_id': 1647, 'product_code': 1614282756, 'serial_number': 1 + index}
                for index in range(slaves)
            ],
            'masters': [
                {'master_index': index, 'complete': complete,
                 'slaves_count': slaves if index == 0 else 0,
                 'error': '' if complete else '재스캔 후 응답한 Slave가 없습니다'}
                for index, complete in enumerate(masters_complete)
            ],
        },
        'dynamixel_scan': {
            'available': True, 'complete': True, 'devices_count': 2,
            'devices': [{'id': 3, 'port': '/dev/ttyUSB0'}, {'id': 5, 'port': '/dev/ttyUSB0'}],
        },
    }


def _annotated(scan, motors):
    ethercat_project_compat.annotate_ethercat_project_compatibility(
        scan, lambda: {'success': True, 'registry': {'motors': motors}}
    )
    return scan


def test_a_first_scan_on_an_empty_project_is_a_success():
    """**이것이 그 버그다** · 등록된 축이 없으면 못 찾은 것도 없다."""
    scan = _annotated(_scan(masters_complete=(True, False)), [])

    outcome = motor_config_rules.scan_operation_outcome(
        scan, operation_type='full_scan', fallback_success=True
    )

    assert outcome == 'success'


def test_an_empty_project_requires_no_master():
    scan = _annotated(_scan(), [])
    project = scan['project_comparison']['ethercat_project']

    assert project['required_master_indices'] == []
    assert project['compatible'] is True


@pytest.mark.parametrize('operation', ['full_scan', 'ac_servo_scan', 'dynamixel_scan'])
def test_every_scan_kind_succeeds_on_an_empty_project(operation):
    scan = _annotated(_scan(masters_complete=(True, False)), [])

    assert motor_config_rules.scan_operation_outcome(
        scan, operation_type=operation, fallback_success=True
    ) == 'success'


def test_a_project_that_needs_a_silent_master_still_fails():
    """비었을 때만 봐주는 것이다 · 쓰는 Master 가 조용하면 그대로 실패다."""
    motors = [{
        'transport': 'ethercat', 'enabled': True, 'axis': 0,
        'identity': {'ethercat_master_index': 1, 'slave_position': 0,
                     'ethercat_alias': 0, 'vendor_id': 1647,
                     'product_code': 1614282756, 'serial_number': 9},
        'config': {'controller_index': 0, 'ethercat_master_index': 1, 'position': 0},
    }]
    scan = _annotated(_scan(masters_complete=(True, False)), motors)

    assert scan['project_comparison']['ethercat_project']['compatible'] is False
    assert motor_config_rules.scan_operation_outcome(
        scan, operation_type='full_scan', fallback_success=True
    ) == 'failure'
