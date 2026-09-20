"""EtherCAT 물리 스캔과 버스 상태 조회.

`MotionStateMonitor`에서 떼어냈다 · §5 분해 목표안의 `EthercatScanner` · §6-32

**모터 스캔 영구 불변조건을 그대로 지킨다.** `전체 모터 검색`은 `ethercat rescan`
으로 기존 열거정보를 폐기한 뒤 Slave를 다시 열거하고, 각 Slave의 SII EEPROM과
Alias 레지스터를 읽는다. 옮기면서 명령도 순서도 판정도 바꾸지 않았다.

`motion_system` 안으로 옮기는 것(§5 8단계)은 별개 작업이다 · 여기서는 노드 밖
모듈로만 뺀다.
"""

from __future__ import annotations

import re
import subprocess
import time
from copy import deepcopy
from typing import Any, Dict, List, Optional

from .motor_values import parse_int


#: EEPROM 읽기 시도 횟수 · 실패하면 곧바로 다시 요청한다 · §6-230
SII_READ_ATTEMPTS = 3


class EthercatScanner:
    def __init__(self, monitor: Any) -> None:
        self.monitor = monitor
        #: 마지막으로 관측한 버스 상태
        self.status: Dict[str, Any] = {}
        self.last_status_at: Optional[float] = None

    def _poll_ethercat_bus_status(self) -> None:
        now = time.time()
        master_indices = sorted({
            parse_int(metadata.get('ethercat_master_index')) or 0
            for metadata in getattr(self.monitor, '_motor_metadata', {}).values()
            if str(metadata.get('transport') or '').lower() == 'ethercat'
        }) or [0]
        masters: Dict[str, Dict[str, Any]] = {}
        errors: List[str] = []

        for master_index in master_indices:
            try:
                master = subprocess.run(
                    ['ethercat', 'master', '-m', str(master_index)],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=2.0,
                )
                slaves = subprocess.run(
                    ['ethercat', 'slaves', '-m', str(master_index)],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=2.0,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                error = str(exc)
                masters[str(master_index)] = {
                    'available': False,
                    'master_index': master_index,
                    'error': error,
                }
                errors.append(f'Master {master_index}: {error}')
                continue

            if master.returncode != 0 or slaves.returncode != 0:
                error = (
                    master.stderr.strip()
                    or slaves.stderr.strip()
                    or master.stdout.strip()
                    or slaves.stdout.strip()
                )
                masters[str(master_index)] = {
                    'available': False,
                    'master_index': master_index,
                    'error': error,
                }
                errors.append(f'Master {master_index}: {error}')
                continue

            phase_match = re.search(
                r'^\s*Phase:\s*(.+?)\s*$',
                master.stdout,
                re.MULTILINE | re.IGNORECASE,
            )
            states_by_position: Dict[str, str] = {}
            states_by_alias: Dict[str, str] = {}
            for match in re.finditer(
                r'^\s*(\d+)\s+(\d+):\d+\s+([A-Z]+)\b',
                slaves.stdout,
                re.MULTILINE | re.IGNORECASE,
            ):
                position, alias, state = match.groups()
                states_by_position[position] = state.upper()
                if int(alias) > 0:
                    states_by_alias[alias] = state.upper()

            masters[str(master_index)] = {
                'available': True,
                'master_index': master_index,
                'master_active': bool(re.search(
                    r'^\s*Active:\s*yes\s*$',
                    master.stdout,
                    re.MULTILINE | re.IGNORECASE,
                )),
                'link_up': bool(re.search(
                    r'^\s*Link:\s*UP\s*$',
                    master.stdout,
                    re.MULTILINE | re.IGNORECASE,
                )),
                'slaves_responding': len(states_by_position),
                'phase': phase_match.group(1).strip() if phase_match else '',
                'state_text': ', '.join(
                    f'{position}:{state}'
                    for position, state in sorted(
                        states_by_position.items(),
                        key=lambda item: int(item[0]),
                    )
                ),
                'states_by_position': states_by_position,
                'states_by_alias': states_by_alias,
                'error': '',
            }

        available_masters = [
            status for status in masters.values() if status.get('available')
        ]
        first_master = masters.get(str(master_indices[0]), {})
        self.status = {
            'available': bool(available_masters),
            'complete': len(available_masters) == len(master_indices),
            'last_seen_at': now,
            'master_active': bool(available_masters) and all(
                status.get('master_active', False) for status in available_masters
            ),
            'link_up': bool(available_masters) and all(
                status.get('link_up', False) for status in available_masters
            ),
            'slaves_responding': sum(
                int(status.get('slaves_responding') or 0)
                for status in available_masters
            ),
            'phase': str(first_master.get('phase') or ''),
            'state_text': ' · '.join(
                f'Master {master_index} [{masters[str(master_index)].get("state_text", "")}]'
                for master_index in master_indices
                if masters[str(master_index)].get('available')
            ),
            'masters': masters,
            'error': ' / '.join(errors),
        }
        self.last_status_at = now

    def _current_ethercat_status(self, now: float) -> Dict[str, Any]:
        if not self.status:
            return {
                'available': False,
                'age_sec': None,
                'master_active': None,
                'link_up': None,
                'slaves_responding': None,
                'phase': '',
                'state_text': '',
            }

        status = deepcopy(self.status)
        status['available'] = True
        status['age_sec'] = round(now - float(status.get('last_seen_at', now)), 3)
        return status

    def _scan_ethercat_slaves(self) -> Dict[str, Any]:
        started_at = time.time()
        self.monitor._publish_scan_progress(
            'ethercat_preflight',
            'EtherCAT Slave 운전 상태를 확인합니다',
            transport='ethercat',
        )
        try:
            master_status = subprocess.run(
                ['ethercat', 'master'],
                check=False,
                capture_output=True,
                text=True,
                timeout=2.0,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {
                'available': False,
                'complete': False,
                'direct': True,
                'rescan_performed': False,
                'rescan_blocked': True,
                'scanned_at': started_at,
                'error': f'EtherCAT Master 운전 상태 확인 실패: {exc}',
                'slaves_count': 0,
                'slaves': [],
            }

        if master_status.returncode != 0:
            return {
                'available': False,
                'complete': False,
                'direct': True,
                'rescan_performed': False,
                'rescan_blocked': True,
                'scanned_at': started_at,
                'error': (
                    'EtherCAT Master 운전 상태 확인 실패: '
                    + (master_status.stderr.strip() or master_status.stdout.strip())
                ),
                'slaves_count': 0,
                'slaves': [],
            }

        master_output = master_status.stdout
        master_claimed = bool(
            re.search(r'^\s*Phase:\s*Operation\s*$', master_output, re.MULTILINE | re.IGNORECASE)
            or re.search(r'^\s*Active:\s*yes\s*$', master_output, re.MULTILINE | re.IGNORECASE)
        )
        if master_claimed:
            return {
                'available': False,
                'complete': False,
                'direct': True,
                'rescan_performed': False,
                'rescan_blocked': True,
                'scanned_at': started_at,
                'error': (
                    'EtherCAT Master를 모터 제어 프로그램이 사용 중입니다. '
                    '초기화 또는 운전 중 버스 재열거는 안전하지 않아 직접 스캔을 중단했습니다'
                ),
                'slaves_count': 0,
                'slaves': [],
            }
        try:
            preflight = subprocess.run(
                ['ethercat', 'slaves', '-v'],
                check=False,
                capture_output=True,
                text=True,
                timeout=2.0,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {
                'available': False,
                'complete': False,
                'direct': True,
                'scanned_at': started_at,
                'error': str(exc),
                'slaves_count': 0,
                'slaves': [],
            }

        if preflight.returncode != 0:
            return {
                'available': False,
                'complete': False,
                'direct': True,
                'scanned_at': started_at,
                'error': preflight.stderr.strip() or preflight.stdout.strip(),
                'slaves_count': 0,
                'slaves': [],
            }

        preflight_slaves = self._parse_ethercat_slaves(preflight.stdout)
        active_states = sorted({
            str(slave.get('device_state') or '').upper()
            for slave in preflight_slaves
            if str(slave.get('device_state') or '').upper() in {'SAFEOP', 'OP'}
        })
        if active_states:
            reason = f'EtherCAT Slave가 {"/".join(active_states)} 상태입니다'
            return {
                'available': False,
                'complete': False,
                'direct': True,
                'rescan_performed': False,
                'rescan_blocked': True,
                'scanned_at': started_at,
                'error': (
                    f'{reason}. 운전 중 버스 재열거는 안전하지 않아 직접 스캔을 중단했습니다'
                ),
                'slaves_count': 0,
                'slaves': [],
            }

        self.monitor._publish_scan_progress(
            'ethercat_rescan',
            '기존 Slave 정보를 폐기하고 물리 EtherCAT 버스를 재열거합니다',
            transport='ethercat',
        )
        try:
            rescan_started_at = time.time()
            rescanned = subprocess.run(
                ['ethercat', 'rescan'],
                check=False,
                capture_output=True,
                text=True,
                timeout=3.0,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {
                'available': False,
                'complete': False,
                'direct': True,
                'rescan_performed': False,
                'scanned_at': started_at,
                'error': f'EtherCAT 버스 재스캔 실패: {exc}',
                'slaves_count': 0,
                'slaves': [],
            }
        rescan_duration_ms = round((time.time() - rescan_started_at) * 1000.0, 3)
        if rescanned.returncode != 0:
            return {
                'available': False,
                'complete': False,
                'direct': True,
                'rescan_performed': False,
                'rescan_duration_ms': rescan_duration_ms,
                'scanned_at': started_at,
                'error': (
                    'EtherCAT 버스 재스캔 실패: '
                    + (rescanned.stderr.strip() or rescanned.stdout.strip() or 'unknown error')
                ),
                'slaves_count': 0,
                'slaves': [],
            }

        self.monitor._publish_scan_progress(
            'ethercat_rescan_done',
            f'ethercat rescan 명령 실제 실행 완료 ({rescan_duration_ms:g}ms)',
            transport='ethercat',
            details={'rescan_duration_ms': rescan_duration_ms},
        )

        slaves: List[Dict[str, Any]] = []
        previous_signature = None
        topology_stable = False
        listing_error = ''
        for attempt in range(60):
            try:
                completed = subprocess.run(
                    ['ethercat', 'slaves', '-v'],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=2.0,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                listing_error = str(exc)
                break
            if completed.returncode != 0:
                listing_error = completed.stderr.strip() or completed.stdout.strip()
            else:
                slaves = self._parse_ethercat_slaves(completed.stdout)
                signature = tuple(
                    (
                        slave.get('master_index'),
                        slave.get('slave_position'),
                        slave.get('vendor_id'),
                        slave.get('product_code'),
                        slave.get('revision_number'),
                        slave.get('serial_number'),
                    )
                    for slave in slaves
                )
                identity_ready = bool(slaves) and all(
                    all(
                        int(slave.get(key) or 0) > 0
                        for key in ('vendor_id', 'product_code', 'revision_number', 'serial_number')
                    )
                    for slave in slaves
                )
                if identity_ready and signature == previous_signature:
                    topology_stable = True
                    break
                previous_signature = signature if identity_ready else None
            if attempt < 59:
                time.sleep(0.05)

        errors: List[str] = []
        if not slaves:
            return {
                'available': False,
                'complete': False,
                'direct': True,
                'rescan_performed': True,
                'rescan_duration_ms': rescan_duration_ms,
                'scanned_at': started_at,
                'error': listing_error or '재스캔 후 연결된 EtherCAT Slave를 찾지 못했습니다',
                'slaves_count': 0,
                'slaves': [],
            }
        if not topology_stable:
            errors.append('재스캔 후 Slave 목록이 제한 시간 안에 안정화되지 않았습니다')
        else:
            self.monitor._publish_scan_progress(
                'ethercat_topology',
                f'새로 열거된 EtherCAT Slave {len(slaves)}개를 확인했습니다',
                transport='ethercat',
                details={'slaves_count': len(slaves)},
            )
        for slave in slaves:
            master_index = int(slave.get('master_index') or 0)
            position = slave['slave_position']
            self.monitor._publish_scan_progress(
                'ethercat_slave_read',
                (
                    f'Master {master_index} · Slave {position}: '
                    'SII EEPROM과 Alias 레지스터를 읽습니다'
                ),
                transport='ethercat',
                details={
                    'master_index': master_index,
                    'slave_position': position,
                },
            )
            master_identity = {
                key: slave.get(key)
                for key in ('vendor_id', 'product_code', 'revision_number', 'serial_number')
            }
            sii_identity = self._read_sii_identity(master_index, position)
            slave['master_identity'] = master_identity
            slave.update(sii_identity)
            rotary = self._read_station_alias_register(master_index, position)
            slave.update(rotary)
            slave_errors = []
            if slave.get('sii_error'):
                slave_errors.append(str(slave['sii_error']))
            if slave.get('rotary_alias_error'):
                slave_errors.append(str(slave['rotary_alias_error']))
            identity_mismatches = []
            if not slave.get('sii_error'):
                for key in ('vendor_id', 'product_code', 'revision_number', 'serial_number'):
                    master_value = master_identity.get(key)
                    sii_value = slave.get(key)
                    if (
                        master_value is not None
                        and sii_value is not None
                        and int(master_value) > 0
                        and master_value != sii_value
                    ):
                        identity_mismatches.append(
                            f'{key} master={master_value} SII={sii_value}'
                        )
            slave['identity_consistent'] = not identity_mismatches
            if identity_mismatches:
                slave_errors.append(
                    'Master/SII 장치 식별값 불일치: ' + ', '.join(identity_mismatches)
                )
            slave['direct_read_complete'] = not slave_errors
            slave['scan_error'] = ' / '.join(slave_errors)
            if slave_errors:
                errors.append(
                    f'Master {master_index} · Slave '
                    f'{slave["slave_position"]}: {slave["scan_error"]}'
                )
            self.monitor._publish_scan_progress(
                'ethercat_slave_done' if not slave_errors else 'ethercat_slave_failed',
                (
                    f'Master {master_index} · Slave {position}: '
                    f'Alias {slave.get("ethercat_alias")}, '
                    f'Serial {slave.get("serial_number")} 읽기 완료'
                    if not slave_errors
                    else (
                        f'Master {master_index} · Slave {position}: '
                        f'{slave["scan_error"]}'
                    )
                ),
                transport='ethercat',
                details={
                    'master_index': master_index,
                    'slave_position': position,
                    'ethercat_alias': slave.get('ethercat_alias'),
                    'serial_number': slave.get('serial_number'),
                    'success': not slave_errors,
                },
            )

        configured_master_indices = sorted({
            int(value)
            for value in re.findall(
                r'^\s*Master\s*(\d+)\s*$',
                master_output,
                re.MULTILINE | re.IGNORECASE,
            )
        })
        if not configured_master_indices:
            configured_master_indices = sorted({
                int(slave.get('master_index') or 0) for slave in slaves
            })
        master_results = []
        for master_index in configured_master_indices:
            master_slaves = [
                slave
                for slave in slaves
                if int(slave.get('master_index') or 0) == master_index
            ]
            master_errors = [
                str(slave.get('scan_error') or '')
                for slave in master_slaves
                if str(slave.get('scan_error') or '')
            ]
            if not master_slaves:
                master_errors.append('재스캔 후 응답한 Slave가 없습니다')
                errors.append(
                    f'Master {master_index}: 재스캔 후 응답한 Slave가 없습니다'
                )
            master_results.append({
                'master_index': master_index,
                'complete': bool(master_slaves) and not master_errors,
                'slaves_count': len(master_slaves),
                'error': ' / '.join(master_errors),
            })

        return {
            'available': True,
            'complete': not errors,
            'direct': True,
            'source': 'ethercat_rescan_sii_and_register',
            'rescan_performed': True,
            'rescan_duration_ms': rescan_duration_ms,
            'scan_duration_ms': round((time.time() - started_at) * 1000.0, 3),
            'scanned_at': started_at,
            'error': ' / '.join(errors),
            'masters_count': len(master_results),
            'masters': master_results,
            'slaves_count': len(slaves),
            'slaves': slaves,
        }

    def _safe_scan_ethercat_slaves(self) -> Dict[str, Any]:
        try:
            return self._scan_ethercat_slaves()
        except Exception as exc:
            self.monitor.get_logger().error(f'EtherCAT scan failed: {exc}')
            return {
                'available': False,
                'complete': False,
                'direct': True,
                'scanned_at': time.time(),
                'error': str(exc),
                'slaves_count': 0,
                'slaves': [],
            }

    @staticmethod
    def _skipped_ethercat_scan(scanned_at: float) -> Dict[str, Any]:
        return {
            'available': False,
            'complete': False,
            'direct': True,
            'skipped': True,
            'scanned_at': scanned_at,
            'error': '',
            'slaves_count': 0,
            'slaves': [],
        }

    def _parse_ethercat_slaves(self, output: str) -> List[Dict[str, Any]]:
        slaves: List[Dict[str, Any]] = []
        current: Optional[Dict[str, Any]] = None
        header_pattern = re.compile(r'^===\s+Master\s+(\d+),\s+Slave\s+(\d+)\s+===$')

        for raw_line in output.splitlines():
            line = raw_line.strip()
            header = header_pattern.match(line)
            if header:
                if current is not None:
                    slaves.append(current)
                current = {
                    'master_index': int(header.group(1)),
                    'slave_position': int(header.group(2)),
                    'ethercat_alias': None,
                    'device_state': '',
                    'vendor_id': None,
                    'product_code': None,
                    'revision_number': None,
                    'serial_number': None,
                    'order_number': '',
                    'device_name': '',
                }
                continue

            if current is None or ':' not in line:
                continue

            key, value = [part.strip() for part in line.split(':', 1)]
            first_value = value.split()[0] if value else ''
            if key == 'Alias':
                current['ethercat_alias'] = parse_int(first_value)
            elif key == 'State':
                current['device_state'] = first_value
            elif key == 'Vendor Id':
                current['vendor_id'] = parse_int(first_value)
            elif key == 'Product code':
                current['product_code'] = parse_int(first_value)
            elif key == 'Revision number':
                current['revision_number'] = parse_int(first_value)
            elif key == 'Serial number':
                current['serial_number'] = parse_int(first_value)
            elif key == 'Order number':
                current['order_number'] = value
            elif key == 'Device name':
                current['device_name'] = value

        if current is not None:
            slaves.append(current)
        return slaves

    def _read_sii_identity(
        self, master_index: int, slave_position: int
    ) -> Dict[str, Any]:
        """EEPROM 을 읽는다 · **실패하면 다시 요청한다** · §6-230

        `ethercat rescan` 직후에는 EEPROM 읽기가 가끔 32바이트도 안 되게
        돌아온다 · 목록이 안정된 뒤에 읽는데도 그렇다 · 목록이 채워진 것과
        EEPROM 을 읽을 수 있는 것이 같지 않다.

        한 번 실패하면 그 슬레이브의 alias 를 모르게 되고, 프로젝트에 저장된
        alias 와 「불일치」로 판정되어 **검색 전체가 실패**가 된다 · 서보도
        로보티즈도 다 찾은 뒤에 그렇다.

        읽기 한 번이 3ms 라 다시 요청해도 부담이 없다 · 기다리지 않고
        곧바로 다시 요청한다.
        """
        result = None
        for _ in range(SII_READ_ATTEMPTS):
            result = self._read_sii_identity_once(master_index, slave_position)
            if not result.get('sii_error'):
                return result
        return result

    def _read_sii_identity_once(
        self, master_index: int, slave_position: int
    ) -> Dict[str, Any]:
        try:
            completed = subprocess.run(
                [
                    'ethercat',
                    'sii_read',
                    '-m',
                    str(master_index),
                    '-p',
                    str(slave_position),
                ],
                check=False,
                capture_output=True,
                timeout=2.0,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {
                'ethercat_alias': None,
                'sii_error': f'SII EEPROM 읽기 실패: {exc}',
                'identity_source': 'physical_sii',
            }
        if completed.returncode != 0:
            detail = completed.stderr.decode('utf-8', errors='replace').strip()
            return {
                'ethercat_alias': None,
                'sii_error': f'SII EEPROM 읽기 실패: {detail or "unknown error"}',
                'identity_source': 'physical_sii',
            }
        try:
            identity = self._parse_sii_identity(completed.stdout)
        except ValueError as exc:
            return {
                'ethercat_alias': None,
                'sii_error': str(exc),
                'identity_source': 'physical_sii',
            }
        return {
            **identity,
            'sii_error': '',
            'identity_source': 'physical_sii',
        }

    @staticmethod
    def _parse_sii_identity(data: bytes) -> Dict[str, Any]:
        if not isinstance(data, (bytes, bytearray)) or len(data) < 32:
            raise ValueError('SII EEPROM 헤더가 32바이트보다 짧습니다')
        return {
            'ethercat_alias': int.from_bytes(data[8:10], 'little'),
            'vendor_id': int.from_bytes(data[16:20], 'little'),
            'product_code': int.from_bytes(data[20:24], 'little'),
            'revision_number': int.from_bytes(data[24:28], 'little'),
            'serial_number': int.from_bytes(data[28:32], 'little'),
        }

    def _read_station_alias_register(
        self, master_index: int, slave_position: int
    ) -> Dict[str, Any]:
        completed = None
        last_error = ''
        for attempt in range(3):
            try:
                completed = subprocess.run(
                    [
                        'ethercat',
                        'reg_read',
                        '-m',
                        str(master_index),
                        '-p',
                        str(slave_position),
                        '-t',
                        'uint16',
                        '0x0012',
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=1.0,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                last_error = str(exc)
                completed = None
            if completed is not None and completed.returncode == 0:
                break
            if completed is not None:
                last_error = completed.stderr.strip() or completed.stdout.strip()
            if attempt < 2:
                self.monitor._publish_scan_progress(
                    'ethercat_register_retry',
                    (
                        f'Master {master_index} · Slave {slave_position}: '
                        'Alias 레지스터 응답 지연, '
                        f'{attempt + 2}번째 읽기를 재시도합니다'
                    ),
                    transport='ethercat',
                    details={
                        'master_index': master_index,
                        'slave_position': slave_position,
                        'next_attempt': attempt + 2,
                        'error': last_error,
                    },
                )
                time.sleep(0.05)

        if completed is None or completed.returncode != 0:
            return {
                'rotary_alias': None,
                'rotary_alias_hex': '',
                'rotary_alias_error': last_error or 'Alias 레지스터 읽기 실패',
            }

        parts = completed.stdout.strip().split()
        raw_hex = parts[0] if parts else ''
        value = parse_int(parts[-1]) if parts else None
        if raw_hex.lower().startswith('0x'):
            raw_hex = '0x' + raw_hex[2:].upper()
        return {
            'rotary_alias': value,
            'rotary_alias_hex': raw_hex,
            'rotary_alias_error': '',
        }

    def _scanned_ethercat_identity(
        self, slave: Dict[str, Any]
    ) -> Optional[tuple]:
        master_index = parse_int(slave.get('master_index')) or 0
        alias = parse_int(slave.get('ethercat_alias'))
        if alias is None or alias <= 0:
            alias = parse_int(slave.get('rotary_alias'))
        if alias is not None and alias > 0:
            return ('alias', master_index, alias)
        position = parse_int(slave.get('slave_position'))
        if position is None:
            return None
        return ('position', master_index, position)

    def _configured_ethercat_identity(
        self, axis: Dict[str, Any]
    ) -> Optional[tuple]:
        master_index = parse_int(axis.get('ethercat_master_index')) or 0
        alias = parse_int(axis.get('ethercat_alias'))
        if alias is not None and alias > 0:
            return ('alias', master_index, alias)
        position = parse_int(axis.get('slave_position'))
        if position is None:
            return None
        return ('position', master_index, position)

    def _ethercat_axis_state(self, motor: Dict[str, Any]) -> str:
        status = self._ethercat_master_status(motor)
        alias = parse_int(motor.get('alias'))
        if alias is not None and alias > 0:
            return str((status.get('states_by_alias') or {}).get(str(alias)) or '')
        position = parse_int(motor.get('slave_position'))
        if position is None:
            return ''
        return str(
            (status.get('states_by_position') or {}).get(str(position)) or ''
        )

    def _ethercat_master_status(self, motor: Dict[str, Any]) -> Dict[str, Any]:
        status = self.status if isinstance(self.status, dict) else {}
        master_index = parse_int(motor.get('ethercat_master_index')) or 0
        masters = status.get('masters') if isinstance(status.get('masters'), dict) else {}
        master_status = masters.get(str(master_index))
        if isinstance(master_status, dict):
            return master_status
        if master_index == 0:
            return status
        return {}
