"""모터 연결 판정 · 상태 문구·요약·물리 대조.

`MotionStateMonitor`에서 떼어냈다 · §6-35

판정 규칙은 순수 함수로 두고, 확정 지연을 재는 `CommunicationHealth`만 상태를
갖는다. **상태는 그 상태를 쓰는 것과 같이 옮긴다**는 규칙 그대로다.

확정 시한 두 개는 노드가 갖는 파라미터라 생성 시점에 붙잡지 않고 부를 때마다
읽는다 · 붙잡으면 나중에 바뀐 값이 반영되지 않는다(§6-11의 교훈).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .motor_values import parse_int


class CommunicationHealth:
    """축별 통신 실패·복구 확정을 지연 판정한다."""

    def __init__(self, monitor: Any) -> None:
        self.monitor = monitor
        #: controller_index -> 판정 상태
        self.entries: Dict[int, Dict[str, Any]] = {}

    def update(
        self,
        controller_index: int,
        communication_unavailable: bool,
        now: float,
    ) -> Dict[str, Any]:
        health = self.entries.setdefault(controller_index, {
            'unavailable_since': None,
            'available_since': None,
            'confirmed_offline': False,
        })
        if communication_unavailable:
            health['available_since'] = None
            if health['unavailable_since'] is None:
                health['unavailable_since'] = now
            if now - float(health['unavailable_since']) >= self.monitor.connection_loss_confirm_sec:
                health['confirmed_offline'] = True
        else:
            health['unavailable_since'] = None
            if health['available_since'] is None:
                health['available_since'] = now
            if (
                health['confirmed_offline']
                and now - float(health['available_since'])
                >= self.monitor.connection_recovery_confirm_sec
            ):
                health['confirmed_offline'] = False
        return health


def set_physical_connection_fields(
    motor: Dict[str, Any],
    scan: Optional[Dict[str, Any]],
) -> None:
    if str(motor.get('transport') or '').lower() != 'ethercat':
        return
    scanned_at = scan.get('scanned_at') if isinstance(scan, dict) else None
    if not scan or not scan.get('complete'):
        motor.update({
            'physical_connection_state': 'not_scanned' if not scan else 'unknown',
            'physical_connection_confirmed': False,
            'physical_connection_checked_at': scanned_at,
            'physical_connection_message': (
                '물리 검색을 아직 실행하지 않았습니다.'
                if not scan
                else str(scan.get('error') or '최근 물리 검색을 완료하지 못했습니다.')
            ),
        })
        return

    expected_alias = parse_int(motor.get('alias'))
    expected_position = parse_int(motor.get('slave_position'))
    expected_master = parse_int(motor.get('ethercat_master_index')) or 0
    matched = None
    for slave in scan.get('slaves') or []:
        if not isinstance(slave, dict):
            continue
        physical_alias = parse_int(
            slave.get('ethercat_alias', slave.get('rotary_alias'))
        )
        physical_master = parse_int(slave.get('master_index')) or 0
        if (
            expected_alias not in (None, 0)
            and physical_alias == expected_alias
            and physical_master == expected_master
        ):
            matched = slave
            break
        if (
            expected_alias in (None, 0)
            and parse_int(slave.get('slave_position')) == expected_position
            and physical_master == expected_master
        ):
            matched = slave
            break

    detected = matched is not None
    motor.update({
        'physical_connection_state': 'detected' if detected else 'missing',
        'physical_connection_confirmed': True,
        'physical_connection_checked_at': scanned_at,
        'physical_connection_message': (
            '최근 EtherCAT 물리 검색에서 확인됐습니다.'
            if detected
            else '최근 EtherCAT 물리 검색에서 확인되지 않았습니다.'
        ),
        'physical_slave_position': (
            matched.get('slave_position') if matched is not None else None
        ),
    })

def set_connection_fields(
    motor: Dict[str, Any],
    state: str,
    reason: str,
    source: str,
    checked_at: float,
) -> None:
    connection_state = {
        'detected': 'online',
        'disconnected': 'offline',
        'ethercat_down': 'bus_down',
        'stale': 'stale',
        'monitoring_off': 'monitoring_off',
        'initializing': 'initializing',
    }.get(state, 'unknown')
    message = connection_message(reason)
    connected = connection_state == 'online'
    confirmed = connection_state in {'online', 'offline', 'bus_down'}
    evidence = {
        'source': source,
        'reason_code': reason,
        'checked_at': checked_at,
        'last_feedback_at': motor.get('last_seen_at'),
        'feedback_age_sec': motor.get('age_sec'),
    }
    motor.update({
        'connection_state': connection_state,
        'connection_connected': connected,
        'connection_confirmed': confirmed,
        'connection_reason': reason,
        'connection_source': source,
        'connection_message': message,
        'connection_evidence': evidence,
        'state_detail': message,
    })

def connection_message(reason: str) -> str:
    messages = {
        'runtime_feedback_fresh': '모터 런타임 피드백이 정상 수신 중입니다.',
        'communication_unavailable': '제어 노드가 이 축의 통신 불가를 보고했습니다.',
        'ethercat_bus_down': 'EtherCAT Master 또는 물리 링크가 내려가 있습니다.',
        'ethercat_axis_missing': 'EtherCAT 버스에서 설정된 Slave를 찾지 못했습니다.',
        'ethercat_axis_not_operational': 'EtherCAT Slave가 운전 가능 상태가 아닙니다.',
        'feedback_timeout': '마지막 모터 피드백 이후 연결 제한 시간을 초과했습니다.',
        'feedback_stale': '모터 피드백 갱신이 지연되고 있습니다.',
        'monitoring_disabled': '모터 상태 모니터링이 꺼져 있습니다.',
        'awaiting_first_feedback': '제어 노드의 첫 모터 피드백을 기다리고 있습니다.',
        'communication_confirmation_pending': '일시적인 통신 실패인지 확인하고 있습니다.',
        'no_runtime_feedback': '설정된 축이지만 제어 노드에서 피드백을 받지 못했습니다.',
        'scan_detected': '통신 버스 검색에서 모터가 확인되었습니다.',
        'scan_missing': '통신 버스 검색에서 설정된 모터를 찾지 못했습니다.',
        'scan_failed': '통신 버스 검색에 실패하여 연결 여부를 확정할 수 없습니다.',
    }
    return messages.get(reason, '모터 연결 상태를 확인할 수 없습니다.')

def connection_summary(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    counts: Dict[str, int] = {}
    for item in items:
        state = str(item.get('connection_state') or 'unknown')
        counts[state] = counts.get(state, 0) + 1
    online = counts.get('online', 0)
    confirmed = sum(1 for item in items if item.get('connection_confirmed', False))
    return {
        'total': len(items),
        'online': online,
        'offline': counts.get('offline', 0),
        'bus_down': counts.get('bus_down', 0),
        'stale': counts.get('stale', 0),
        'initializing': counts.get('initializing', 0),
        'monitoring_off': counts.get('monitoring_off', 0),
        'unknown': counts.get('unknown', 0),
        'confirmed': confirmed,
        'all_online': bool(items) and online == len(items),
        'counts': counts,
    }

def build_scan_connection_rows(
    motors: List[Dict[str, Any]],
    ethercat_scan: Dict[str, Any],
    dynamixel_scan: Dict[str, Any],
    *,
    scan_ethercat: bool,
    scan_dynamixel: bool,
) -> List[Dict[str, Any]]:
    ethercat_aliases = {
        parse_int(slave.get('ethercat_alias'))
        for slave in ethercat_scan.get('slaves', [])
        if parse_int(slave.get('ethercat_alias')) is not None
    }
    dynamixel_ids = {
        parse_int(device.get('id'))
        for device in dynamixel_scan.get('devices', [])
        if parse_int(device.get('id')) is not None
    }
    rows: List[Dict[str, Any]] = []
    for motor in motors:
        transport = str(motor.get('transport', '')).lower()
        motor_type = str(motor.get('motor_type', '')).lower()
        scanned = False
        scan_available = False
        found = False
        scan_source = ''

        if transport == 'ethercat' and scan_ethercat:
            scanned = not bool(ethercat_scan.get('skipped', False))
            scan_available = bool(ethercat_scan.get('available', False))
            found = parse_int(motor.get('alias')) in ethercat_aliases
            scan_source = 'ethercat_slave_scan'
        elif (transport == 'serial' or motor_type == 'dynamixel') and scan_dynamixel:
            scanned = not bool(dynamixel_scan.get('skipped', False))
            scan_available = bool(dynamixel_scan.get('available', False))
            raw_identity = (
                motor.get('bus_id')
                if motor.get('bus_id') is not None
                else motor.get('node_id')
            )
            identity = parse_int(raw_identity)
            found = identity in dynamixel_ids
            scan_source = (
                'runtime_topic'
                if dynamixel_scan.get('mode') == 'runtime_topic'
                else 'direct_ping'
            )

        row = {
            'controller_index': motor.get('controller_index'),
            'display_name': motor.get('display_name'),
            'motor_type': motor.get('motor_type'),
            'motor_type_label': motor.get('motor_type_label'),
            'transport': motor.get('transport'),
            'transport_label': motor.get('transport_label'),
            'runtime_state': motor.get('connection_state', 'unknown'),
            'runtime_reason': motor.get('connection_reason', ''),
            'connection_state': motor.get('connection_state', 'unknown'),
            'connection_connected': bool(motor.get('connection_connected', False)),
            'connection_confirmed': bool(motor.get('connection_confirmed', False)),
            'connection_reason': motor.get('connection_reason', ''),
            'connection_source': motor.get('connection_source', 'runtime_topic'),
            'connection_message': motor.get('connection_message', ''),
        }
        if scanned and scan_available:
            row.update({
                'discovery_state': 'detected' if found else 'missing',
                'discovery_detected': found,
                'discovery_confirmed': True,
                'discovery_source': scan_source,
                'discovery_message': connection_message(
                    'scan_detected' if found else 'scan_missing'
                ),
            })
        elif scanned and not scan_available:
            row.update({
                'discovery_state': 'unknown',
                'discovery_detected': False,
                'discovery_confirmed': False,
                'discovery_source': scan_source or 'bus_scan',
                'discovery_message': connection_message('scan_failed'),
            })
        else:
            row.update({
                'discovery_state': 'not_scanned',
                'discovery_detected': False,
                'discovery_confirmed': False,
                'discovery_source': '',
                'discovery_message': '이 통신 버스는 이번 검색 대상이 아닙니다.',
            })
        rows.append(row)
    return rows
