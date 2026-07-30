import re
import subprocess
from typing import Any, Callable, Dict, List, Optional


class EthercatAliasError(RuntimeError):
    pass


class EthercatAliasManager:
    """Read and write EtherCAT SII aliases without depending on motion_system."""

    def __init__(self, runner: Callable[..., Any] = subprocess.run) -> None:
        self._runner = runner

    @staticmethod
    def _parse_int(value: Any) -> Optional[int]:
        if value is None:
            return None
        text = str(value).strip().split()[0]
        try:
            return int(text, 0)
        except (TypeError, ValueError, IndexError):
            return None

    @classmethod
    def parse_slaves(cls, output: str) -> List[Dict[str, Any]]:
        slaves: List[Dict[str, Any]] = []
        current: Optional[Dict[str, Any]] = None
        for raw_line in str(output or '').splitlines():
            line = raw_line.strip()
            match = re.match(r'^===\s+Master\s+(\d+),\s+Slave\s+(\d+)\s+===$', line)
            if match:
                if current is not None:
                    slaves.append(current)
                current = {
                    'master_index': int(match.group(1)),
                    'slave_position': int(match.group(2)),
                    'ethercat_alias': None,
                    'vendor_id': None,
                    'product_code': None,
                    'serial_number': None,
                }
                continue
            if current is None or ':' not in line:
                continue
            key, value = [part.strip() for part in line.split(':', 1)]
            first = value.split()[0] if value else ''
            if key == 'Alias':
                current['ethercat_alias'] = cls._parse_int(first)
            elif key == 'Vendor Id':
                current['vendor_id'] = cls._parse_int(first)
            elif key == 'Product code':
                current['product_code'] = cls._parse_int(first)
            elif key == 'Serial number':
                current['serial_number'] = cls._parse_int(first)
            elif key == 'State':
                current['device_state'] = first
            elif key == 'Order number':
                current['order_number'] = value
            elif key == 'Device name':
                current['device_name'] = value
        if current is not None:
            slaves.append(current)
        return slaves

    @staticmethod
    def parse_sii_identity(data: bytes) -> Dict[str, int]:
        if not isinstance(data, (bytes, bytearray)) or len(data) < 32:
            raise EthercatAliasError('SII EEPROM 헤더가 32바이트보다 짧습니다.')
        return {
            'ethercat_alias': int.from_bytes(data[8:10], 'little'),
            'vendor_id': int.from_bytes(data[16:20], 'little'),
            'product_code': int.from_bytes(data[20:24], 'little'),
            'revision_number': int.from_bytes(data[24:28], 'little'),
            'serial_number': int.from_bytes(data[28:32], 'little'),
        }

    @staticmethod
    def _error_text(value: Any) -> str:
        if isinstance(value, bytes):
            return value.decode('utf-8', errors='replace').strip()
        return str(value or '').strip()

    def read_slaves(self, timeout_sec: float = 3.0) -> List[Dict[str, Any]]:
        try:
            completed = self._runner(
                ['ethercat', 'slaves', '-v'],
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise EthercatAliasError(f'EtherCAT Alias 읽기 실패: {exc}') from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise EthercatAliasError(f'EtherCAT Alias 읽기 실패: {detail}')
        slaves = self.parse_slaves(completed.stdout)
        if not slaves:
            raise EthercatAliasError('현재 연결된 EtherCAT Slave를 찾지 못했습니다.')
        for slave in slaves:
            master_index = int(slave.get('master_index') or 0)
            position = int(slave['slave_position'])
            try:
                sii = self._runner(
                    [
                        'ethercat',
                        'sii_read',
                        '-m',
                        str(master_index),
                        '-p',
                        str(position),
                    ],
                    check=False,
                    capture_output=True,
                    timeout=timeout_sec,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise EthercatAliasError(
                    f'Master {master_index} Slave {position} '
                    f'SII EEPROM 읽기 실패: {exc}'
                ) from exc
            if sii.returncode != 0:
                detail = self._error_text(sii.stderr) or self._error_text(sii.stdout)
                raise EthercatAliasError(
                    f'Master {master_index} Slave {position} '
                    f'SII EEPROM 읽기 실패: {detail}'
                )
            try:
                identity = self.parse_sii_identity(sii.stdout)
            except EthercatAliasError as exc:
                raise EthercatAliasError(
                    f'Master {master_index} Slave {position} {exc}'
                ) from exc
            slave.update(identity)
            # These strings are SII descriptors, not a verified nameplate
            # model and not a safe motion-profile selector.
            slave['sii_order_number'] = str(
                slave.get('order_number') or ''
            ).strip()
            slave['sii_device_name'] = str(
                slave.get('device_name') or ''
            ).strip()
            slave['identity_source'] = 'physical_sii'
        return slaves

    def write_alias(
        self,
        slave_position: int,
        new_alias: int,
        expected: Dict[str, Any],
        timeout_sec: float = 5.0,
        master_index: int = 0,
    ) -> Dict[str, Any]:
        if not isinstance(master_index, int) or master_index < 0:
            raise EthercatAliasError('EtherCAT Master 번호는 0 이상의 정수여야 합니다.')
        if not isinstance(slave_position, int) or slave_position < 0:
            raise EthercatAliasError('Slave Position은 0 이상의 정수여야 합니다.')
        if not isinstance(new_alias, int) or not 0 <= new_alias <= 0xFFFF:
            raise EthercatAliasError('EEPROM Alias는 0~65535 범위여야 합니다.')

        slaves = self.read_slaves(timeout_sec=min(timeout_sec, 3.0))
        matches = [
            item
            for item in slaves
            if int(item.get('master_index') or 0) == master_index
            and item['slave_position'] == slave_position
        ]
        if len(matches) != 1:
            raise EthercatAliasError(
                f'Master {master_index} Slave {slave_position} 장비를 '
                '하나로 확인할 수 없습니다.'
            )
        actual = matches[0]
        labels = {
            'ethercat_alias': 'EEPROM Alias',
            'vendor_id': 'Vendor ID',
            'product_code': 'Product Code',
            'serial_number': 'Serial Number',
        }
        for key, label in labels.items():
            expected_value = self._parse_int(expected.get(key))
            if expected_value is None:
                raise EthercatAliasError(f'{label} 예상값이 없어 쓰기를 중단했습니다.')
            if actual.get(key) != expected_value:
                raise EthercatAliasError(
                    f'{label}가 선택 당시 값과 다릅니다: '
                    f'{expected_value} → {actual.get(key)}'
                )
        if actual['ethercat_alias'] == new_alias:
            raise EthercatAliasError('새 EEPROM Alias가 현재 값과 같습니다.')
        if new_alias != 0:
            duplicate = next(
                (
                    item for item in slaves
                    if int(item.get('master_index') or 0) == master_index and
                    item['slave_position'] != slave_position and
                    item.get('ethercat_alias') == new_alias
                ),
                None,
            )
            if duplicate is not None:
                raise EthercatAliasError(
                    f'EEPROM Alias {new_alias} 값이 Slave Position '
                    f'{duplicate["slave_position"]}에서 이미 사용 중입니다 '
                    f'(Master {master_index}).'
                )

        try:
            completed = self._runner(
                [
                    'ethercat',
                    'alias',
                    '-m',
                    str(master_index),
                    '-p',
                    str(slave_position),
                    str(new_alias),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise EthercatAliasError(f'EEPROM Alias 쓰기 실패: {exc}') from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise EthercatAliasError(f'EEPROM Alias 쓰기 실패: {detail}')
        return {
            'master_index': master_index,
            'slave_position': slave_position,
            'previous_alias': actual['ethercat_alias'],
            'new_alias': new_alias,
            'vendor_id': actual['vendor_id'],
            'product_code': actual['product_code'],
            'serial_number': actual['serial_number'],
            'message': (
                'EEPROM Alias 쓰기 완료. 서보 드라이버 제어 전원을 재투입한 뒤 '
                '전체 모터 검색을 실행하세요.'
            ),
        }
