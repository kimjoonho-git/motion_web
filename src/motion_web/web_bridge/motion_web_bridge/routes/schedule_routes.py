import asyncio
import os
import json
import logging
from typing import Any, Dict
from fastapi import FastAPI, HTTPException, Request

from motion_schedule.schedule_store import ScheduleStore
from motion_schedule.schedule_models import ScheduleItem

logger = logging.getLogger("bridge.routes.schedule")


def register_schedule_routes(app: FastAPI, bridge, project_call) -> None:
    workspace_dir = os.environ.get("MOTION_WORKSPACE", "/home/joonho_test/ros2_ws")
    projects_dir = os.path.join(workspace_dir, "motion_projects")

    store = ScheduleStore(projects_dir=projects_dir)

    def _sync_store_project():
        curr_proj = getattr(bridge, "current_project_id", None)
        if not curr_proj and hasattr(bridge, "project_repository"):
            try:
                curr_proj = bridge.project_repository.get_active_project_id()
            except Exception:
                pass
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
            item = ScheduleItem.from_dict(data)
            success = store.upsert_schedule(item)
            if not success:
                raise HTTPException(status_code=500, detail="Failed to save schedule to store.")
            return {"status": "ok", "schedule": item.to_dict()}
        except Exception as e:
            logger.error(f"Error saving schedule: {e}")
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
        coordination_file = os.path.join(workspace_dir, "config/coordination_settings.yaml")
        is_master = True
        if os.path.exists(coordination_file):
            try:
                with open(coordination_file, "r", encoding="utf-8") as f:
                    content = f.read()
                    if "role: slave" in content or "role: 'slave'" in content:
                        is_master = False
            except Exception:
                pass

        return {
            "status": "ok",
            "is_master": is_master,
            "active_project_id": store.current_project_id,
            "schedule_count": len(store.list_schedules())
        }
