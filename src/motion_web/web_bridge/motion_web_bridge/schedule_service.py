"""스케줄 살림살이 · 라우트가 아니라 여기서 한다 · §6-182

**스케줄만 서비스 층이 없었다.**

다른 기능은 전부 `라우트 → 서비스 → 저장소` 인데 스케줄만 라우트 안에 업무
로직이 여덟 개 들어 있었다 · 길 하나당 27.7줄로 다른 것(5~10줄)의 서너 배였다.

그래서 스케줄 로직을 시험하려면 HTTP 를 거쳐야 했고, 상태(`ScheduleStore`)가
라우트 등록 시점에 만들어져 요청마다 바뀌었다.

**그리고 「어느 프로젝트인가」를 제 나름대로 알아내고 있었다.**

    curr_proj = bridge.project_repository.selected_project_id()
    except: pass
    curr_proj = getattr(bridge, "current_project_id", None)   ← 그런 것이 없다
    curr_proj = "default"                                     ← 지어낸다

`bridge.current_project_id` 는 저장소 어디에도 없다 · 첫 줄이 실패하면 곧장
`"default"` 로 떨어져 **없는 프로젝트에 스케줄을 저장할 뻔했다** ·
`motion_projects/default/` 폴더가 실제로 만들어져 있었다 (다행히 비어 있었다).

이제 묻는 곳은 하나다 — `require_selected_project_id()`.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from motion_common import local_clock
from motion_common.coordination import resolve_master_role
from motion_common.paths import motion_projects_dir
from motion_common.schedule_models import ScheduleItem
from motion_common.schedule_store import ScheduleStore, normalize_run_mode

logger = logging.getLogger('bridge.schedule')

PACKAGE_HINT = 'motion_web_bridge'


class ScheduleOwnershipError(Exception):
    """이 PC 는 스케줄을 소유할 수 없다 · 연동 슬레이브다."""


class ScheduleNotFound(Exception):
    """그런 스케줄이 없다."""


class ScheduleService:
    """스케줄을 읽고 쓰고, 이 PC 가 그럴 자격이 있는지 본다."""

    def __init__(self, bridge: Any, *, store: ScheduleStore | None = None) -> None:
        self.bridge = bridge
        self.store = store or ScheduleStore(
            projects_dir=str(motion_projects_dir(PACKAGE_HINT))
        )

    # ----------------------------------------------------------------- #
    # 어느 프로젝트인가 · 묻는 곳은 하나다
    # ----------------------------------------------------------------- #

    def _follow_selected_project(self) -> None:
        """고른 프로젝트를 따라간다 · §6-182

        **없으면 지어내지 않는다** · 전에는 `"default"` 로 떨어져 없는
        프로젝트에 저장할 뻔했다 · 프로젝트가 없으면 스케줄도 없는 것이 맞다.
        """
        repository = getattr(self.bridge, 'project_repository', None)
        if repository is None:
            return
        project_id = repository.require_selected_project_id()
        if project_id and self.store.current_project_id != project_id:
            self.store.load_project(project_id)

    def _require_owner(self) -> None:
        """이 PC 가 스케줄을 소유할 수 있는지 · §6-69

        연동 중인 슬레이브 PC 는 스케줄을 만들어도 발화하지 않는다 ·
        `motion_schedule_node` 가 마스터가 아니면 타이머 자체를 건너뛴다.
        그런데 화면과 API 는 저장을 받아 줬다 · 돌지 않는 스케줄이 조용히
        쌓이고, 마스터의 목록과도 따로 놀았다.

        연동을 쓰지 않는 PC 는 `resolve_master_role` 이 「단독 동작으로 간주」해
        마스터로 판정하므로 그대로 편집할 수 있다.
        """
        role = resolve_master_role(package_hint=PACKAGE_HINT)
        if not role.is_master:
            raise ScheduleOwnershipError(
                '이 PC 는 연동 슬레이브라 스케줄을 설정할 수 없습니다 · '
                '마스터 PC 에서 설정하세요 · ' + role.reason
            )

    # ----------------------------------------------------------------- #
    # 읽기
    # ----------------------------------------------------------------- #

    def list_schedules(self) -> List[Dict[str, Any]]:
        self._follow_selected_project()
        return [item.to_dict() for item in self.store.list_schedules()]

    def status(self) -> Dict[str, Any]:
        self._follow_selected_project()
        role = resolve_master_role(package_hint=PACKAGE_HINT)
        if not role.is_master:
            logger.debug('마스터 아님 · %s', role.reason)
        session = self._coordination_session()
        node = self._schedule_node_status()
        return {
            'status': 'ok',
            'is_master': role.is_master,
            'active_project_id': self.store.current_project_id,
            'schedule_count': len(self.store.list_schedules()),
            'coordination_enabled': session['enabled'],
            'coordination_joined': session['joined'],
            'coordination_node_connected': session['node_connected'],
            # 스케줄이 실행을 관리하는가 · 사람이 정한다 · §6-143
            'run_mode': self.store.mode,
            # 시각이 됐는데 거부당했는가 · 비어 있으면 정상 · §6-147
            'last_failure': node.get('last_failure') or {},
            # 시각을 못 읽어 **영영 안 도는** 스케줄 · §6-285
            #
            # 24:00 처럼 못 읽는 시각은 구간이 없는 것과 같아 조용히 건너뛰었다 ·
            # 화면·스위치는 멀쩡해 보이는데 아무 일도 일어나지 않았다.
            'unreadable_schedules': list(node.get('unreadable_schedules') or []),
            'schedule_node_seen': bool(node.get('received')),
            # 지금 멈추면 스케줄이 되돌리는가 · §6-149
            'active_schedule_id': node.get('active_schedule_id') or '',
            'reconcile_interval_sec': node.get('reconcile_interval_sec'),
            # 이 PC 가 몇 시라고 믿는가 · 해외 설치에서 시간대만 안 바뀐다
            'clock': local_clock.snapshot(),
        }

    def _coordination_session(self) -> Dict[str, Any]:
        """연동을 쓰는가 · 지금 그룹에 들어가 있는가 · §6-133

        스케줄은 `enabled` 를 보고 그룹으로 쏘지만, 실제 발화는 `joined` 가
        아니면 조정 노드가 거부한다 · 화면이 그 어긋남을 말할 수 있게 둘 다
        내려준다 · 조정 노드가 없는 PC 에서도 상태는 떠야 하므로 실패는
        「연동 안 씀」으로 본다.
        """
        service = getattr(self.bridge, '_coordination_web_bridge', None)
        if service is None:
            return {'enabled': False, 'joined': False, 'node_connected': False}
        try:
            return service.session_summary()
        except (OSError, ValueError) as exc:
            logger.debug('연동 세션 상태 조회 실패 · %s', exc)
            return {'enabled': False, 'joined': False, 'node_connected': False}

    def _schedule_node_status(self) -> Dict[str, Any]:
        reader = getattr(self.bridge, 'schedule_node_status', None)
        if not callable(reader):
            return {}
        try:
            return reader() or {}
        except Exception:  # 상태 조회 실패가 화면을 막지 않는다
            logger.debug('스케줄 노드 상태 조회 실패', exc_info=True)
            return {}

    # ----------------------------------------------------------------- #
    # 쓰기 · 전부 소유권부터 본다
    # ----------------------------------------------------------------- #

    def set_run_mode(self, run_mode: Any) -> Dict[str, Any]:
        """스케줄 모드 · 수동 모드 · §6-143

        수동 모드에서는 스케줄이 아무것도 하지 않는다 · 정비·시험 중에 1분
        점검이 모션을 되살리면 위험하다.
        """
        self._follow_selected_project()
        mode = normalize_run_mode(run_mode, default='')
        if not mode:
            raise ValueError('run_mode 는 schedule 또는 manual 이어야 합니다')
        if not self.store.set_mode(mode):
            raise RuntimeError('스케줄 모드를 저장하지 못했습니다')
        return {'status': 'ok', 'run_mode': self.store.mode}

    def save_schedule(self, data: Any) -> Dict[str, Any]:
        self._follow_selected_project()
        self._require_owner()
        if not isinstance(data, dict):
            raise ValueError('요청 본문은 객체여야 합니다')
        item = ScheduleItem.from_dict(data)
        # 지문은 **이 PC** 가 찍는다 · §6-150
        #
        # 브라우저가 정하게 두면 한국에서 원격으로 파리 PC 를 설정할 때
        # 한국 시간대가 박힌다 · 어긋남을 잡으려고 둔 값이 되레 어긋남을
        # 만든다 · 스케줄이 실제로 해석되는 곳은 이 PC 다.
        item.saved_timezone = local_clock.timezone_name() or None
        if not self.store.upsert_schedule(item):
            raise RuntimeError(
                f'프로젝트 {self.store.current_project_id!r} 에 스케줄을 저장하지 못했습니다'
            )
        return {'status': 'ok', 'schedule': item.to_dict()}

    def delete_schedule(self, schedule_id: str) -> Dict[str, Any]:
        self._follow_selected_project()
        self._require_owner()
        if not self.store.delete_schedule(schedule_id):
            raise ScheduleNotFound('그 스케줄이 없거나 지우지 못했습니다')
        return {'status': 'ok', 'deleted_id': schedule_id}

    def set_enabled(self, schedule_id: str, enabled: bool) -> Dict[str, Any]:
        self._follow_selected_project()
        self._require_owner()
        if not self.store.set_enabled(schedule_id, enabled):
            raise ScheduleNotFound('그 스케줄이 없습니다')
        return {'status': 'ok', 'schedule_id': schedule_id, 'enabled': enabled}
