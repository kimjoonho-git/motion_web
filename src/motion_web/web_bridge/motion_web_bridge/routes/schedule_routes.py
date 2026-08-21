import logging
from fastapi import FastAPI, HTTPException, Request

from motion_common.paths import config_file, motion_projects_dir
from motion_schedule.schedule_store import ScheduleStore
from motion_schedule.schedule_models import ScheduleItem

logger = logging.getLogger("bridge.routes.schedule")

PACKAGE_HINT = "motion_web_bridge"


def register_schedule_routes(app: FastAPI, bridge, project_call) -> None:
    projects_dir = str(motion_projects_dir(PACKAGE_HINT))

    store = ScheduleStore(projects_dir=projects_dir)

    def _sync_store_project():
        curr_proj = None
        if hasattr(bridge, "project_repository"):
            try:
                curr_proj = bridge.project_repository.selected_project_id()
            except Exception:
                logger.debug("selected_project_id() 조회 실패 · 대체 경로로 진행", exc_info=True)

        if not curr_proj:
            curr_proj = getattr(bridge, "current_project_id", None)

        if not curr_proj:
            curr_proj = "default"

        if curr_proj and store.current_project_id != curr_proj:
            store.load_project(curr_proj)

    @app.get('/api/schedule/list')
    async def get_schedule_list():
        _sync_store_project()
        items = store.list_schedules()
        return [item.to_dict() for item in items]

    @app.post('/api/schedule/save')
    async def save_schedule(request: Request):
        _sync_store_project()
        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError("Request body must be a JSON object")
            item = ScheduleItem.from_dict(data)
            success = store.upsert_schedule(item)
            if not success:
                raise HTTPException(status_code=500, detail=f"Failed to save schedule to store for project '{store.current_project_id}'.")
            return {"status": "ok", "schedule": item.to_dict()}
        except Exception as e:
            logger.error(f"Error saving schedule: {e}", exc_info=True)
            raise HTTPException(status_code=400, detail=str(e))

    @app.delete('/api/schedule/{schedule_id}')
    async def delete_schedule(schedule_id: str):
        _sync_store_project()
        success = store.delete_schedule(schedule_id)
        if not success:
            raise HTTPException(status_code=404, detail="Schedule not found or delete failed.")
        return {"status": "ok", "deleted_id": schedule_id}

    @app.post('/api/schedule/{schedule_id}/enable')
    async def enable_schedule(schedule_id: str):
        _sync_store_project()
        success = store.set_enabled(schedule_id, True)
        if not success:
            raise HTTPException(status_code=404, detail="Schedule not found.")
        return {"status": "ok", "schedule_id": schedule_id, "enabled": True}

    @app.post('/api/schedule/{schedule_id}/disable')
    async def disable_schedule(schedule_id: str):
        _sync_store_project()
        success = store.set_enabled(schedule_id, False)
        if not success:
            raise HTTPException(status_code=404, detail="Schedule not found.")
        return {"status": "ok", "schedule_id": schedule_id, "enabled": False}

    @app.get('/api/schedule/status')
    async def get_schedule_status():
        _sync_store_project()
        coordination_file = config_file("coordination_settings.yaml", PACKAGE_HINT)
        is_master = True
        if coordination_file.exists():
            try:
                content = coordination_file.read_text(encoding="utf-8")
            except OSError:
                logger.warning(
                    "협조 설정 파일 읽기 실패 · master로 간주 · %s",
                    coordination_file,
                    exc_info=True,
                )
            else:
                if "role: slave" in content or "role: 'slave'" in content:
                    is_master = False

        return {
            "status": "ok",
            "is_master": is_master,
            "active_project_id": store.current_project_id,
            "schedule_count": len(store.list_schedules())
        }
