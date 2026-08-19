import asyncio
from typing import Any, Dict, List, Optional

from .ethercat_alias_manager import EthercatAliasError


class MotorService:
    def __init__(self, bridge) -> None:
        self.bridge = bridge

    @property
    def project_repository(self):
        return getattr(self.bridge, 'project_repository', None)

    @property
    def alias_manager(self):
        return getattr(self.bridge, 'ethercat_alias_manager', None)

    # -------------------------------------------------------------------------
    # Motor Scanning & EtherCAT Aliases
    # -------------------------------------------------------------------------
    def scan_motors(self, timeout_sec: float = 20.0) -> Dict[str, Any]:
        return self.bridge._call_scan_service(
            self.bridge._scan_client,
            self.bridge.scan_service,
            timeout_sec,
            release_ethercat=True,
            operation_type='full_scan',
        )

    def scan_ac_servo_motors(self, timeout_sec: float = 10.0) -> Dict[str, Any]:
        return self.bridge._call_scan_service(
            self.bridge._scan_ac_servo_client,
            self.bridge.scan_ac_servo_service,
            timeout_sec,
            release_ethercat=True,
            operation_type='ac_servo_scan',
        )

    def scan_dynamixel_motors(self, timeout_sec: float = 20.0) -> Dict[str, Any]:
        return self.bridge._call_scan_service(
            self.bridge._scan_dynamixel_client,
            self.bridge.scan_dynamixel_service,
            timeout_sec,
            operation_type='dynamixel_scan',
        )

    def motor_scan_progress(self) -> Dict[str, Any]:
        return self.bridge.motor_scan_progress()

    def read_ethercat_aliases(self) -> Dict[str, Any]:
        alias_mgr = self.alias_manager or self.bridge.ethercat_alias_manager
        try:
            slaves = alias_mgr.read_slaves()
        except EthercatAliasError as exc:
            return {'success': False, 'message': str(exc), 'slaves': []}
        return {
            'success': True,
            'message': f'EtherCAT EEPROM Alias {len(slaves)}축 읽기 완료',
            'slaves': slaves,
        }

    def write_ethercat_alias(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.bridge.write_ethercat_alias(payload)

    # -------------------------------------------------------------------------
    # Motor Config Management
    # -------------------------------------------------------------------------
    def load_motor_config() -> Dict[str, Any]:
        return self.bridge.load_motor_config()

    def save_motor_config(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.bridge.save_motor_config(payload)

    def delete_motor_config(self) -> Dict[str, Any]:
        return self.bridge.delete_motor_config()

    def apply_motor_config(self) -> Dict[str, Any]:
        return self.bridge.apply_motor_config()

    # -------------------------------------------------------------------------
    # Motor Events & Servo Alarm Policy
    # -------------------------------------------------------------------------
    def motor_events(
        self, limit: int = 200, category: str = 'all', file_name: str = 'all'
    ) -> Dict[str, Any]:
        return self.bridge.motor_events(limit=limit, category=category, file_name=file_name)

    def clear_motor_events(self) -> Dict[str, Any]:
        return self.bridge.clear_motor_events()

    def delete_motor_event_file(self, file_name: str) -> Dict[str, Any]:
        return self.bridge.delete_motor_event_file(file_name)

    def servo_alarm_policy(self) -> Dict[str, Any]:
        return self.bridge.servo_alarm_policy()

    def save_servo_alarm_policy(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.bridge.save_servo_alarm_policy(payload)

    # -------------------------------------------------------------------------
    # System & Motor Lifecycle / Restart Orchestration
    # -------------------------------------------------------------------------
    def restart_managed_program(self) -> Dict[str, Any]:
        return self.bridge.restart_managed_program()

    def restart_motor_control_system(self) -> Dict[str, Any]:
        return self.bridge.restart_motor_control_system()

    def clear_motor_runtime_application(self) -> Dict[str, Any]:
        return self.bridge.clear_motor_runtime_application()
