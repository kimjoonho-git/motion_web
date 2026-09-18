import asyncio
import logging
from fastapi import FastAPI, HTTPException, Request

from motion_common import local_clock
from motion_common.coordination import resolve_master_role
from motion_common.paths import motion_projects_dir
from motion_common.schedule_models import ScheduleItem
from motion_common.schedule_store import ScheduleStore, normalize_run_mode

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

    def _set_mode_blocking(body):
        _sync_store_project()
        mode = normalize_run_mode(body.get("run_mode"), default="")
        if not mode:
            raise HTTPException(status_code=400, detail="run_mode must be schedule or manual")
        if not store.set_mode(mode):
            raise HTTPException(status_code=500, detail="failed to save schedule mode")
        return {"status": "ok", "run_mode": store.mode}

    def _save_schedule_blocking(data):
        _sync_store_project()
        _require_schedule_owner()
        try:
            if not isinstance(data, dict):
                raise ValueError("Request body must be a JSON object")
            item = ScheduleItem.from_dict(data)
            # 지문은 **이 PC** 가 찍는다 · §6-150
            #
            # 브라우저가 정하게 두면 한국에서 원격으로 파리 PC 를 설정할 때
            # 한국 시간대가 박힌다 · 어긋남을 잡으려고 둔 값이 되레 어긋남을
            # 만든다 · 스케줄이 실제로 해석되는 곳은 이 PC 다.
            item.saved_timezone = local_clock.timezone_name() or None
            if not store.upsert_schedule(item):
                raise HTTPException(status_code=500, detail=f"Failed to save schedule to store for project '{store.current_project_id}'.")
            return {"status": "ok", "schedule": item.to_dict()}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error saving schedule: {e}", exc_info=True)
            raise HTTPException(status_code=400, detail=str(e))

    def _delete_schedule_blocking(schedule_id: str):
        _sync_store_project()
        _require_schedule_owner()
        if not store.delete_schedule(schedule_id):
            raise HTTPException(status_code=404, detail="Schedule not found or delete failed.")
        return {"status": "ok", "deleted_id": schedule_id}

    def _set_enabled_blocking(schedule_id: str, enabled: bool):
        _sync_store_project()
        _require_schedule_owner()
        if not store.set_enabled(schedule_id, enabled):
            raise HTTPException(status_code=404, detail="Schedule not found.")
        return {"status": "ok", "schedule_id": schedule_id, "enabled": enabled}

    def _schedule_status_blocking():
        _sync_store_project()
        role = resolve_master_role(package_hint=PACKAGE_HINT)
        if not role.is_master:
            logger.debug("마스터 아님 · %s", role.reason)
        session = _coordination_session()

        node = {}
        reader = getattr(bridge, 'schedule_node_status', None)
        if callable(reader):
            try:
                node = reader()
            except Exception:
                logger.debug("스케줄 노드 상태 조회 실패", exc_info=True)
        return {
            "status": "ok",
            "is_master": role.is_master,
            "active_project_id": store.current_project_id,
            "schedule_count": len(store.list_schedules()),
            "coordination_enabled": session['enabled'],
            "coordination_joined": session['joined'],
            "coordination_node_connected": session['node_connected'],
            # 스케줄이 실행을 관리하는가 · 사람이 정한다 · §6-143
            "run_mode": store.mode,
            # 시각이 됐는데 거부당했는가 · 비어 있으면 정상 · §6-147
            "last_failure": node.get('last_failure') or {},
            "schedule_node_seen": bool(node.get('received')),
            # 지금 멈추면 스케줄이 되돌리는가 · §6-149
            "active_schedule_id": node.get('active_schedule_id') or '',
            "reconcile_interval_sec": node.get('reconcile_interval_sec'),
            # 이 PC 가 몇 시라고 믿는가 · 해외 설치에서 시간대만 안 바뀐다
            "clock": local_clock.snapshot(),
        }

    def _schedule_list_blocking():
        _sync_store_project()
        return [item.to_dict() for item in store.list_schedules()]

    @app.get('/api/schedule/list')
    async def get_schedule_list():
        return await asyncio.to_thread(_schedule_list_blocking)

    @app.post('/api/schedule/save')
    async def save_schedule(request: Request):
        data = await request.json()
        return await asyncio.to_thread(_save_schedule_blocking, data)

    @app.delete('/api/schedule/{schedule_id}')
    async def delete_schedule(schedule_id: str):
        return await asyncio.to_thread(_delete_schedule_blocking, schedule_id)

    @app.post('/api/schedule/{schedule_id}/enable')
    async def enable_schedule(schedule_id: str):
        return await asyncio.to_thread(_set_enabled_blocking, schedule_id, True)

    @app.post('/api/schedule/{schedule_id}/disable')
    async def disable_schedule(schedule_id: str):
        return await asyncio.to_thread(_set_enabled_blocking, schedule_id, False)

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

    @app.put('/api/schedule/mode')
    async def set_schedule_mode(request: Request):
        """스케줄 모드 · 수동 모드 · §6-143

        수동 모드에서는 스케줄이 아무것도 하지 않는다 · 정비·시험 중에 1분
        점검이 모션을 되살리면 위험하다.
        """
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="request body must be an object")
        return await asyncio.to_thread(_set_mode_blocking, body)

    @app.get('/api/schedule/status')
    async def get_schedule_status():
        return await asyncio.to_thread(_schedule_status_blocking)
