import os
import json
import logging
from typing import List, Optional, Dict, Any
from .schedule_models import ScheduleItem

logger = logging.getLogger("motion_schedule.store")


class ScheduleStore:
    def __init__(self, projects_dir: str, current_project_id: Optional[str] = None):
        self.projects_dir = projects_dir
        self.current_project_id = current_project_id
        self._schedules: Dict[str, ScheduleItem] = {}
        if current_project_id:
            self.load_project(current_project_id)

    def _get_store_path(self, project_id: str) -> str:
        proj_dir = os.path.join(self.projects_dir, project_id)
        os.makedirs(proj_dir, exist_ok=True)
        return os.path.join(proj_dir, "schedule_store.json")

    def load_project(self, project_id: str) -> None:
        self.current_project_id = project_id
        self._schedules.clear()
        file_path = self._get_store_path(project_id)

        if not os.path.exists(file_path):
            logger.info(f"Schedule store file not found for project {project_id}. Starting empty.")
            return

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    for item_data in data:
                        item = ScheduleItem.from_dict(item_data)
                        self._schedules[item.schedule_id] = item
            logger.info(f"Loaded {len(self._schedules)} schedules for project {project_id}.")
        except Exception as e:
            logger.error(f"Failed to load schedule_store.json for project {project_id}: {e}")

    def save(self) -> bool:
        if not self.current_project_id:
            logger.error("Cannot save schedule store: No current_project_id set.")
            return False

        file_path = self._get_store_path(self.current_project_id)
        temp_path = f"{file_path}.tmp"

        try:
            items_data = [item.to_dict() for item in self._schedules.values()]
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(items_data, f, ensure_ascii=False, indent=2)
            os.replace(temp_path, file_path)
            logger.info(f"Saved {len(items_data)} schedules for project {self.current_project_id}.")
            return True
        except Exception as e:
            logger.error(f"Failed to save schedule store: {e}")
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            return False

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
