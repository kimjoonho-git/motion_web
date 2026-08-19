import asyncio
from typing import Any, Dict, List, Optional


class AutomationService:
    def __init__(self, run_service: 'MotionRunService') -> None:
        self.run_service = run_service

    @property
    def bridge(self):
        return self.run_service.bridge

    def configure(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.bridge.motion_automation_configure(payload)

    def start(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.bridge.motion_automation_start(payload)

    def reserve(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.bridge.motion_automation_reserve(payload)

    def disable(self) -> Dict[str, Any]:
        return self.bridge.motion_automation_disable()


class MotionRunService:
    def __init__(self, bridge) -> None:
        self.bridge = bridge
        self.automation = AutomationService(self)

    def status(self) -> Dict[str, Any]:
        return self.bridge.motion_run_status()

    def check(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.bridge.motion_run_check(payload)

    def initialize(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.bridge.motion_run_initialize(payload)

    def start(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.bridge.motion_run_start(payload)

    def stop(self) -> Dict[str, Any]:
        return self.bridge.motion_run_stop()

    def stop_after_cycle(self) -> Dict[str, Any]:
        return self.bridge.motion_run_stop_after_cycle()

    # Motion Files & Mappings
    def list_files(self) -> Dict[str, Any]:
        return self.bridge.list_motion_files()

    def load_file(self, file_id: str) -> Dict[str, Any]:
        return self.bridge.load_motion_file(file_id)

    def delete_file(self, file_id: str) -> Dict[str, Any]:
        return self.bridge.delete_motion_file(file_id)

    def list_mappings(self) -> Dict[str, Any]:
        return self.bridge.list_motion_mappings()

    def save_mapping(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.bridge.save_motion_mapping(payload)

    def validate_mapping(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.bridge.validate_motion_mapping(payload)

    def load_mapping(self, file_id: str) -> Dict[str, Any]:
        return self.bridge.load_motion_mapping(file_id)

    def delete_mapping(self, file_id: str) -> Dict[str, Any]:
        return self.bridge.delete_motion_mapping(file_id)
