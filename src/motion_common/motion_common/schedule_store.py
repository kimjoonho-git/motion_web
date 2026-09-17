import os
import logging
from typing import List, Optional, Dict

from . import store as common_store

from .schedule_models import ScheduleItem

logger = logging.getLogger("motion_schedule.store")

#: 스케줄이 실행을 관리하는가 · §6-143
#:
#: 전에는 「사람이 멈췄나」를 요청 내용으로 **추측**했다 · 그룹 정지·안전 정지·
#: 모터 설정 적용도 로컬 모션 실행을 세우는데, 그것까지 사람이 멈춘 것으로
#: 읽어서 1회 연동 실행만 해도 "사람이 모션을 정지했습니다" 가 떴다.
#:
#: 추측을 없애고 **스위치 하나**로 만든다 · 지금 어느 쪽인지 화면에 보인다.
SCHEDULE_MODE = 'schedule'   # 스케줄이 1분마다 맞춘다 · 전시·공연
MANUAL_MODE = 'manual'       # 스케줄은 아무것도 안 한다 · 정비·시험
RUN_MODES = (SCHEDULE_MODE, MANUAL_MODE)
DEFAULT_RUN_MODE = SCHEDULE_MODE


def normalize_run_mode(value, default: str = DEFAULT_RUN_MODE) -> str:
    mode = str(value or '').strip().lower()
    return mode if mode in RUN_MODES else default


class ScheduleStore:
    def __init__(self, projects_dir: str, current_project_id: Optional[str] = None):
        self.projects_dir = projects_dir
        self.current_project_id = current_project_id
        self._schedules: Dict[str, ScheduleItem] = {}
        self._mode: str = DEFAULT_RUN_MODE
        self._last_mtime: float = 0.0
        if current_project_id:
            self.load_project(current_project_id)

    def _get_store_path(self, project_id: str) -> str:
        proj_dir = os.path.join(self.projects_dir, project_id)
        os.makedirs(proj_dir, exist_ok=True)
        return os.path.join(proj_dir, "schedule_store.json")

    def load_project(self, project_id: str) -> None:
        self.current_project_id = project_id
        self._schedules.clear()
        self._mode = DEFAULT_RUN_MODE
        file_path = self._get_store_path(project_id)

        if not os.path.exists(file_path):
            self._last_mtime = 0.0
            logger.info(f"Schedule store file not found for project {project_id}. Starting empty.")
            return

        try:
            self._last_mtime = os.path.getmtime(file_path)
            # 기록 측이 배타 락을 잡으므로 읽기는 공유 락으로 충분하다
            with common_store.file_lock(file_path, exclusive=False):
                data = common_store.read_json(file_path, default=[])
            # 옛 파일은 스케줄만 담은 목록이다 · 모드가 생기면서 꾸러미가 됐다
            if isinstance(data, dict):
                self._mode = normalize_run_mode(data.get('mode'))
                items = data.get('schedules')
            else:
                items = data
            for item_data in items if isinstance(items, list) else []:
                item = ScheduleItem.from_dict(item_data)
                self._schedules[item.schedule_id] = item
            logger.info(f"Loaded {len(self._schedules)} schedules for project {project_id}.")
        except (OSError, ValueError, TypeError, KeyError) as exc:
            logger.error(f"Failed to load schedule_store.json for project {project_id}: {exc}")

    def check_and_reload(self) -> bool:
        if not self.current_project_id:
            return False
        file_path = self._get_store_path(self.current_project_id)
        if not os.path.exists(file_path):
            return False
        try:
            current_mtime = os.path.getmtime(file_path)
            if current_mtime > self._last_mtime:
                logger.info(f"Detected schedule_store.json change (mtime: {current_mtime}). Reloading...")
                self.load_project(self.current_project_id)
                return True
        except OSError as exc:
            logger.error(f"Error checking schedule mtime: {exc}")
        return False

    def save(self) -> bool:
        if not self.current_project_id:
            logger.error("Cannot save schedule store: No current_project_id set.")
            return False

        file_path = self._get_store_path(self.current_project_id)

        try:
            payload = {
                'version': 2,
                'mode': self._mode,
                'schedules': [item.to_dict() for item in self._schedules.values()],
            }
            with common_store.locked_update(file_path):
                common_store.atomic_write_json(file_path, payload)
            items_data = payload['schedules']
            logger.info(f"Saved {len(items_data)} schedules for project {self.current_project_id}.")
            return True
        except (OSError, ValueError, TypeError) as exc:
            logger.error(f"Failed to save schedule store: {exc}")
            return False

    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> bool:
        self._mode = normalize_run_mode(mode)
        return self.save()

    def list_schedules(self) -> List[ScheduleItem]:
        return list(self._schedules.values())

    def get_schedule(self, schedule_id: str) -> Optional[ScheduleItem]:
        return self._schedules.get(schedule_id)

    def upsert_schedule(self, item: ScheduleItem) -> bool:
        self._schedules[item.schedule_id] = item
        return self.save()

    def delete_schedule(self, schedule_id: str) -> bool:
        if schedule_id in self._schedules:
            del self._schedules[schedule_id]
            return self.save()
        return False

    def set_enabled(self, schedule_id: str, enabled: bool) -> bool:
        item = self.get_schedule(schedule_id)
        if item:
            item.enabled = enabled
            return self.save()
        return False
