"""Dynamixel 물리 검색 · Protocol 2.0 직접 Ping.

`MotionStateMonitor`에서 떼어냈다 · §5 분해 목표안의 `DynamixelScanner` · §6-33

**모터 스캔 영구 불변조건을 그대로 지킨다.** Dynamixel은 실제 직렬 포트를 열고
Protocol 2.0 Broadcast Ping과 ID `0~252` 개별 보조 Ping을 수행한다. 물리 응답이
없으면 이전 값을 쓰지 않고 실패로 남긴다. 옮기면서 명령도 순서도 판정도 바꾸지
않았다.

모델 제원 파일 읽기(`_dynamixel_raw_model_info`)는 검색이 아니라 메타데이터라서
노드에 남겼다. `motion_system` 안으로 옮기는 것(§5 8단계)은 별개 작업이다.
"""

from __future__ import annotations

import os
import select
import termios
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

#: 검색 규약 · 화면과 `scan_contract`가 같은 값을 쓴다
DYNAMIXEL_SCAN_BAUDRATES = (1000000,)
DYNAMIXEL_SCAN_MAX_ID = 252
DYNAMIXEL_SCAN_PROTOCOL = '2.0'


class DynamixelScanner:
    def __init__(self, monitor: Any) -> None:
        self.monitor = monitor

    def _scan_dynamixel_motors(self) -> Dict[str, Any]:
        started_at = time.time()
        self.monitor._publish_scan_progress(
            'dynamixel_targets',
            'Dynamixel 직렬 포트와 검색 대상을 확인합니다',
            transport='dynamixel',
        )
        targets = self._dynamixel_scan_targets()

        if not targets:
            self.monitor._publish_scan_progress(
                'dynamixel_unavailable',
                'Dynamixel 직렬 포트를 찾지 못해 실제 Ping을 실행할 수 없습니다',
                transport='dynamixel',
            )
            return {
                'available': False,
                'complete': False,
                'direct': True,
                'scanned_at': started_at,
                'mode': 'direct_ping',
                'protocol': DYNAMIXEL_SCAN_PROTOCOL,
                'scan_rule': 'auto serial port, baudrate 1000000, broadcast ping plus ID 0-252',
                'error': (
                    'Dynamixel 직렬 포트를 찾지 못했습니다. '
                    '/dev/serial/by-id, /dev/ttyUSB*, /dev/ttyACM* 경로를 확인했습니다.'
                ),
                'targets': [],
                'devices_count': 0,
                'devices': [],
            }

        devices: List[Dict[str, Any]] = []
        errors: List[str] = []
        for target in targets:
            port = str(target.get('port') or '')
            baudrate = int(target.get('baudrate') or 0)
            ids = list(target.get('ids') or [])
            if not port or not baudrate:
                continue
            self.monitor._publish_scan_progress(
                'dynamixel_port',
                f'{port} @ {baudrate}bps에서 직접 Ping을 시작합니다',
                transport='dynamixel',
                details={'port': port, 'baudrate': baudrate},
            )
            try:
                fd = self._open_dynamixel_port(port, baudrate)
            except OSError as exc:
                errors.append(f'{port}@{baudrate}: {exc}')
                continue
            try:
                devices_by_id: Dict[int, Dict[str, Any]] = {}
                attempts = max(1, int(self.monitor.dynamixel_scan_attempts))
                for attempt in range(attempts):
                    if self.monitor.dynamixel_scan_settle_sec > 0:
                        time.sleep(float(self.monitor.dynamixel_scan_settle_sec))
                    for device in self._broadcast_ping_dynamixel(
                        fd,
                        float(self.monitor.dynamixel_broadcast_timeout_sec),
                    ):
                        if ids and int(device['id']) not in ids:
                            continue
                        devices_by_id[int(device['id'])] = device

                    if self.monitor.dynamixel_scan_id_fallback:
                        for dxl_id in ids:
                            current = devices_by_id.get(int(dxl_id))
                            if current is not None and self._dynamixel_device_has_valid_model(current):
                                continue
                            device = self._ping_dynamixel_id(
                                fd,
                                int(dxl_id),
                                float(self.monitor.dynamixel_scan_timeout_sec),
                            )
                            if device is None:
                                continue
                            devices_by_id[int(device['id'])] = device
                    if attempt >= attempts - 1:
                        break

                for device in devices_by_id.values():
                    device.update({
                        'port': port,
                        'baudrate': baudrate,
                        'model_name': (
                            device.get('model_name')
                            or self._dynamixel_model_name(device.get('model_number'))
                        ),
                    })
                    devices.append(device)
                    self.monitor._publish_scan_progress(
                        'dynamixel_device',
                        f'Dynamixel ID {device.get("id")}: 실제 Ping 응답 확인',
                        transport='dynamixel',
                        details={
                            'id': device.get('id'),
                            'model_number': device.get('model_number'),
                            'port': port,
                        },
                    )
            finally:
                os.close(fd)

        return {
            'available': bool(devices),
            'complete': bool(devices) and not errors,
            'direct': True,
            'scanned_at': started_at,
            'mode': 'direct_ping',
            'protocol': DYNAMIXEL_SCAN_PROTOCOL,
            'scan_rule': 'auto serial port, baudrate 1000000, broadcast ping plus ID 0-252',
            'error': '; '.join(errors) if errors else (
                '' if devices else '직접 Ping에 응답한 Dynamixel이 없습니다'
            ),
            'warning': '',
            'targets': targets,
            'attempts': max(1, int(self.monitor.dynamixel_scan_attempts)),
            'id_fallback': bool(self.monitor.dynamixel_scan_id_fallback),
            'devices_count': len(devices),
            'devices': devices,
        }

    def _dynamixel_scan_targets(self) -> List[Dict[str, Any]]:
        path = Path(str(self.monitor.motor_config_file)).expanduser() if self.monitor.motor_config_file else None
        config: Dict[str, Any] = {}
        if path is not None and path.is_file():
            try:
                with path.open('r', encoding='utf-8') as file:
                    loaded = yaml.safe_load(file) or {}
                if isinstance(loaded, dict):
                    config = loaded
            except (OSError, yaml.YAMLError) as exc:
                self.monitor.get_logger().warn(f'Failed to read Dynamixel scan YAML: {exc}')

        ports = self._dynamixel_serial_ports(config)
        scan_max_id = max(0, min(252, int(self.monitor.dynamixel_scan_max_id)))
        ids = list(range(0, scan_max_id + 1))
        for master in config.get('masters', []):
            if not isinstance(master, dict):
                continue
            if str(master.get('type', '')).lower() not in {'serial', 'dynamixel'}:
                continue
            port = str(master.get('serial_port') or '')
            if port and os.path.exists(port):
                self._append_dynamixel_port(ports, port, 'yaml')

        targets: List[Dict[str, Any]] = []
        for port_info in ports:
            for baudrate in DYNAMIXEL_SCAN_BAUDRATES:
                targets.append({
                    'port': port_info['port'],
                    'port_source': port_info['source'],
                    'baudrate': baudrate,
                    'protocol': DYNAMIXEL_SCAN_PROTOCOL,
                    'ids': ids,
                    'id_range': f'broadcast ping plus ID 0-{scan_max_id}',
                })
        return targets

    def _dynamixel_serial_ports(self, config: Dict[str, Any]) -> List[Dict[str, str]]:
        ports: List[Dict[str, str]] = []
        by_id_dir = Path('/dev/serial/by-id')
        if by_id_dir.is_dir():
            for path in sorted(by_id_dir.iterdir(), key=lambda item: item.name):
                if path.exists():
                    self._append_dynamixel_port(ports, str(path), 'auto:/dev/serial/by-id')

        for pattern, source in (
            ('ttyUSB*', 'auto:/dev/ttyUSB'),
            ('ttyACM*', 'auto:/dev/ttyACM'),
        ):
            for path in sorted(Path('/dev').glob(pattern), key=lambda item: item.name):
                if path.exists():
                    self._append_dynamixel_port(ports, str(path), source)

        for master in config.get('masters', []):
            if not isinstance(master, dict):
                continue
            if str(master.get('type', '')).lower() not in {'serial', 'dynamixel'}:
                continue
            port = str(master.get('serial_port') or '')
            if port and os.path.exists(port):
                self._append_dynamixel_port(ports, port, 'yaml')
        return ports

    @staticmethod
    def _append_dynamixel_port(
        ports: List[Dict[str, str]],
        port: str,
        source: str,
    ) -> None:
        try:
            resolved = str(Path(port).resolve(strict=False))
        except OSError:
            resolved = port
        if any(item.get('resolved') == resolved for item in ports):
            return
        ports.append({
            'port': port,
            'resolved': resolved,
            'source': source,
        })

    @staticmethod
    def _open_dynamixel_port(port: str, baudrate: int) -> int:
        fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            speed = getattr(termios, f'B{baudrate}')
            attrs = termios.tcgetattr(fd)
            attrs[0] = termios.IGNPAR
            attrs[1] = 0
            attrs[2] = speed | termios.CS8 | termios.CLOCAL | termios.CREAD
            attrs[3] = 0
            attrs[4] = speed
            attrs[5] = speed
            attrs[6][termios.VTIME] = 0
            attrs[6][termios.VMIN] = 0
            termios.tcflush(fd, termios.TCIOFLUSH)
            termios.tcsetattr(fd, termios.TCSANOW, attrs)
        except Exception:
            os.close(fd)
            raise
        return fd

    def _broadcast_ping_dynamixel(self, fd: int, timeout_sec: float) -> List[Dict[str, Any]]:
        termios.tcflush(fd, termios.TCIOFLUSH)
        packet = bytearray([0xFF, 0xFF, 0xFD, 0x00, 0xFE, 0x03, 0x00, 0x01])
        crc = self._dynamixel_crc(packet)
        packet.extend([crc & 0xFF, (crc >> 8) & 0xFF])
        if not self._write_dynamixel_packet(fd, bytes(packet), timeout_sec=0.05):
            return []

        deadline = time.time() + max(timeout_sec, 0.001)
        data = bytearray()
        devices_by_id: Dict[int, Dict[str, Any]] = {}
        while time.time() < deadline:
            remaining = max(0.0, deadline - time.time())
            readable, _, _ = select.select([fd], [], [], remaining)
            if not readable:
                break
            try:
                chunk = os.read(fd, 1024)
            except BlockingIOError:
                continue
            if not chunk:
                continue
            data.extend(chunk)
            for packet_data in self._extract_dynamixel_status_packets(data):
                params = packet_data.get('params', b'')
                dxl_id = int(packet_data.get('id', -1))
                if dxl_id < 0:
                    continue
                devices_by_id[dxl_id] = {
                    'id': dxl_id,
                    'packet_error': packet_data.get('error', 0),
                    'model_number': params[0] | (params[1] << 8) if len(params) >= 2 else None,
                    'firmware_version': params[2] if len(params) >= 3 else None,
                    'source': 'broadcast_ping',
                }
        return [devices_by_id[key] for key in sorted(devices_by_id)]

    def _ping_dynamixel_id(
        self,
        fd: int,
        dxl_id: int,
        timeout_sec: float,
    ) -> Optional[Dict[str, Any]]:
        termios.tcflush(fd, termios.TCIOFLUSH)
        packet = bytearray([0xFF, 0xFF, 0xFD, 0x00, dxl_id & 0xFF, 0x03, 0x00, 0x01])
        crc = self._dynamixel_crc(packet)
        packet.extend([crc & 0xFF, (crc >> 8) & 0xFF])
        if not self._write_dynamixel_packet(fd, bytes(packet), timeout_sec=0.02):
            return None

        deadline = time.time() + max(timeout_sec, 0.001)
        data = bytearray()
        while time.time() < deadline:
            remaining = max(0.0, deadline - time.time())
            readable, _, _ = select.select([fd], [], [], remaining)
            if not readable:
                break
            try:
                chunk = os.read(fd, 256)
            except BlockingIOError:
                continue
            if not chunk:
                continue
            data.extend(chunk)
            packet_data = self._extract_dynamixel_status_packet(data, dxl_id)
            if packet_data is None:
                continue
            params = packet_data.get('params', b'')
            return {
                'id': dxl_id,
                'packet_error': packet_data.get('error', 0),
                'model_number': params[0] | (params[1] << 8) if len(params) >= 2 else None,
                'firmware_version': params[2] if len(params) >= 3 else None,
                'source': 'id_ping',
            }
        return None

    @staticmethod
    def _write_dynamixel_packet(fd: int, packet: bytes, timeout_sec: float) -> bool:
        deadline = time.time() + max(timeout_sec, 0.001)
        written_total = 0
        while written_total < len(packet):
            remaining = max(0.0, deadline - time.time())
            if remaining <= 0.0:
                return False
            _, writable, _ = select.select([], [fd], [], remaining)
            if not writable:
                return False
            try:
                written = os.write(fd, packet[written_total:])
            except BlockingIOError:
                continue
            if written <= 0:
                return False
            written_total += written
        return True

    def _extract_dynamixel_status_packet(
        self,
        data: bytearray,
        expected_id: int,
    ) -> Optional[Dict[str, Any]]:
        header = b'\xFF\xFF\xFD\x00'
        while True:
            index = bytes(data).find(header)
            if index < 0:
                if len(data) > 3:
                    del data[:-3]
                return None
            if index > 0:
                del data[:index]
            if len(data) < 7:
                return None
            length = data[5] | (data[6] << 8)
            total = 7 + length
            if len(data) < total:
                return None
            packet = bytes(data[:total])
            del data[:total]
            if packet[4] != (expected_id & 0xFF):
                continue
            received_crc = packet[-2] | (packet[-1] << 8)
            calculated_crc = self._dynamixel_crc(packet[:-2])
            if received_crc != calculated_crc:
                continue
            if packet[7] != 0x55:
                continue
            return {
                'id': packet[4],
                'error': packet[8] if len(packet) > 8 else 0,
                'params': packet[9:-2],
            }

    def _extract_dynamixel_status_packets(self, data: bytearray) -> List[Dict[str, Any]]:
        packets: List[Dict[str, Any]] = []
        header = b'\xFF\xFF\xFD\x00'
        while True:
            index = bytes(data).find(header)
            if index < 0:
                if len(data) > 3:
                    del data[:-3]
                break
            if index > 0:
                del data[:index]
            if len(data) < 7:
                break
            length = data[5] | (data[6] << 8)
            total = 7 + length
            if len(data) < total:
                break
            packet = bytes(data[:total])
            del data[:total]
            received_crc = packet[-2] | (packet[-1] << 8)
            calculated_crc = self._dynamixel_crc(packet[:-2])
            if received_crc != calculated_crc:
                continue
            if packet[7] != 0x55:
                continue
            packets.append({
                'id': packet[4],
                'error': packet[8] if len(packet) > 8 else 0,
                'params': packet[9:-2],
            })
        return packets

    @staticmethod
    def _dynamixel_crc(data: bytes) -> int:
        crc = 0
        for byte in data:
            crc ^= int(byte) << 8
            for _ in range(8):
                if crc & 0x8000:
                    crc = ((crc << 1) ^ 0x8005) & 0xFFFF
                else:
                    crc = (crc << 1) & 0xFFFF
        return crc & 0xFFFF

    @staticmethod
    def _dynamixel_model_name(model_number: Any) -> str:
        if model_number is None:
            return ''
        known = {
            1120: 'XM540-W270',
            1130: 'XM540-W150',
            1100: 'XH540-W270',
            1110: 'XH540-W150',
        }
        return known.get(int(model_number), '')

    @staticmethod
    def _dynamixel_device_has_valid_model(device: Dict[str, Any]) -> bool:
        model_number = device.get('model_number')
        try:
            return int(model_number) > 0
        except (TypeError, ValueError):
            return False

    def _safe_scan_dynamixel_motors(self) -> Dict[str, Any]:
        try:
            return self._scan_dynamixel_motors()
        except Exception as exc:
            self.monitor.get_logger().error(f'Dynamixel scan failed: {exc}')
            return {
                'available': False,
                'complete': False,
                'direct': True,
                'scanned_at': time.time(),
                'mode': 'direct_ping',
                'protocol': DYNAMIXEL_SCAN_PROTOCOL,
                'scan_rule': 'auto serial port, baudrate 1000000, broadcast ping plus ID 0-252',
                'error': str(exc),
                'targets': [],
                'devices_count': 0,
                'devices': [],
            }

    def _skipped_dynamixel_scan(self, scanned_at: float) -> Dict[str, Any]:
        return {
            'available': False,
            'complete': False,
            'direct': True,
            'skipped': True,
            'scanned_at': scanned_at,
            'mode': 'direct_ping',
            'protocol': DYNAMIXEL_SCAN_PROTOCOL,
            'scan_rule': 'auto serial port, baudrate 1000000, broadcast ping plus ID 0-252',
            'error': '',
            'targets': [],
            'devices_count': 0,
            'devices': [],
        }
