import logging
from fastapi import FastAPI, HTTPException, Request

from motion_common.coordination import resolve_master_role
from motion_common.paths import motion_projects_dir
from motion_common.schedule_models import ScheduleItem
from motion_common.schedule_store import ScheduleStore

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

    def _require_schedule_owner():
        """이 PC 가 스케줄을 소유할 수 있는지 확인한다 · §6-69

        연동 중인 슬레이브 PC 는 스케줄을 만들어도 발화하지 않는다 ·
        `motion_schedule_node` 가 마스터가 아니면 타이머 자체를 건너뛴다.
        그런데 화면과 API 는 저장을 받아 줬다 · 돌지 않는 스케줄이 조용히
        쌓이고, 마스터의 목록과도 따로 놀았다.

        연동을 쓰지 않는 PC 는 `resolve_master_role` 이 "단독 동작으로 간주" 해
        마스터로 판정하므로 그대로 편집할 수 있다.
        """
        role = resolve_master_role(package_hint=PACKAGE_HINT)
        if not role.is_master:
            raise HTTPException(
                status_code=409,
                detail=(
                    '이 PC 는 연동 슬레이브라 스케줄을 설정할 수 없습니다 · '
                    '마스터 PC 에서 설정하세요 · ' + role.reason
                ),
            )

    @app.get('/api/schedule/list')
    async def get_schedule_list():
        _sync_store_project()
        items = store.list_schedules()
        return [item.to_dict() for item in items]

    @app.post('/api/schedule/save')
    async def save_schedule(request: Request):
        _sync_store_project()
        _require_schedule_owner()
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
        _require_schedule_owner()
        success = store.delete_schedule(schedule_id)
        if not success:
            raise HTTPException(status_code=404, detail="Schedule not found or delete failed.")
        return {"status": "ok", "deleted_id": schedule_id}

    @app.post('/api/schedule/{schedule_id}/enable')
    async def enable_schedule(schedule_id: str):
        _sync_store_project()
        _require_schedule_owner()
        success = store.set_enabled(schedule_id, True)
        if not success:
            raise HTTPException(status_code=404, detail="Schedule not found.")
        return {"status": "ok", "schedule_id": schedule_id, "enabled": True}

    @app.post('/api/schedule/{schedule_id}/disable')
    async def disable_schedule(schedule_id: str):
        _sync_store_project()
        _require_schedule_owner()
        success = store.set_enabled(schedule_id, False)
        if not success:
            raise HTTPException(status_code=404, detail="Schedule not found.")
        return {"status": "ok", "schedule_id": schedule_id, "enabled": False}

    def _coordination_session():
        """연동을 쓰는가 · 지금 그룹에 들어가 있는가 · §6-133

        스케줄은 `enabled` 를 보고 그룹으로 쏘지만, 실제 발화는 `joined` 가
        아니면 조정 노드가 거부한다 · 화면이 그 어긋남을 말할 수 있게 둘 다
        내려준다. 조정 노드가 없는 PC 에서도 status 는 떠야 하므로 실패는
        "연동 안 씀" 으로 본다.
        """
        service = getattr(bridge, '_coordination_web_bridge', None)
        if service is None:
            return {'enabled': False, 'joined': False, 'node_connected': False}
        try:
            return service.session_summary()
        except (OSError, ValueError) as exc:
            logger.debug("연동 세션 상태 조회 실패 · %s", exc)
            return {'enabled': False, 'joined': False, 'node_connected': False}

    @app.get('/api/schedule/status')
    async def get_schedule_status():
        _sync_store_project()
        role = resolve_master_role(package_hint=PACKAGE_HINT)
        if not role.is_master:
            logger.debug("마스터 아님 · %s", role.reason)
        session = _coordination_session()

        return {
            "status": "ok",
            "is_master": role.is_master,
            "active_project_id": store.current_project_id,
            "schedule_count": len(store.list_schedules()),
            "coordination_enabled": session['enabled'],
            "coordination_joined": session['joined'],
            "coordination_node_connected": session['node_connected'],
        }
