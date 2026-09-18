"""스케줄 살림살이는 서비스가 한다 · §6-182

**스케줄만 서비스 층이 없었다.**

다른 기능은 전부 `라우트 → 서비스 → 저장소` 인데 스케줄만 라우트 안에 업무
로직이 여덟 개 들어 있었다 · 길 하나당 27.7줄로 다른 것(5~10줄)의 서너 배 ·
그래서 스케줄 로직을 시험하려면 HTTP 를 거쳐야 했다.

**이 파일이 그 증거다** · 여기서는 HTTP 도 FastAPI 도 안 쓴다.

그리고 「어느 프로젝트인가」를 제 나름대로 알아내고 있었다.

    curr_proj = bridge.project_repository.selected_project_id()
    except: pass
    curr_proj = getattr(bridge, "current_project_id", None)   ← 그런 것이 없다
    curr_proj = "default"                                     ← 지어낸다

첫 줄이 실패하면 곧장 `"default"` 로 떨어져 **없는 프로젝트에 저장할 뻔했다** ·
`motion_projects/default/` 폴더가 실제로 만들어져 있었다 (비어 있었다).
"""

import pytest

from motion_web_bridge.schedule_service import (
    ScheduleNotFound,
    ScheduleOwnershipError,
    ScheduleService,
)


class _Store:
    """저장소 대역 · 어느 프로젝트를 따라갔는지 기억한다."""

    def __init__(self, current=''):
        self.current_project_id = current
        self.loaded = []
        self.mode = 'manual'
        self.items = []
        self.accept = True

    def load_project(self, project_id):
        self.loaded.append(project_id)
        self.current_project_id = project_id

    def set_mode(self, mode):
        if not self.accept:
            return False
        self.mode = mode
        return True

    def list_schedules(self):
        return list(self.items)

    def upsert_schedule(self, item):
        if not self.accept:
            return False
        self.items.append(item)
        return True

    def delete_schedule(self, schedule_id):
        return self.accept

    def set_enabled(self, schedule_id, enabled):
        return self.accept


class _Repository:
    def __init__(self, selected='프로젝트-A'):
        self.selected = selected

    def require_selected_project_id(self):
        if not self.selected:
            raise ValueError('통합 프로젝트를 먼저 선택하세요')
        return self.selected


class _Bridge:
    def __init__(self, selected='프로젝트-A'):
        self.project_repository = _Repository(selected)


def _service(selected='프로젝트-A', current=''):
    return ScheduleService(_Bridge(selected), store=_Store(current))


@pytest.fixture(autouse=True)
def _master(monkeypatch):
    """기본은 마스터 · 소유권 시험에서만 바꾼다."""
    from motion_web_bridge import schedule_service

    monkeypatch.setattr(
        schedule_service, 'resolve_master_role',
        lambda **_kwargs: type('R', (), {'is_master': True, 'reason': ''})(),
    )


# --------------------------------------------------------------------------- #
# 어느 프로젝트인가 · 묻는 곳은 하나다
# --------------------------------------------------------------------------- #

def test_the_store_follows_the_selected_project():
    service = _service(selected='프로젝트-B')

    service.list_schedules()

    assert service.store.loaded == ['프로젝트-B']


def test_it_does_not_reload_the_same_project():
    """요청마다 다시 읽으면 디스크를 헛되이 친다."""
    service = _service(selected='프로젝트-A', current='프로젝트-A')

    service.list_schedules()
    service.status()

    assert service.store.loaded == []


def test_no_project_means_no_made_up_project():
    """전에는 `"default"` 로 떨어져 없는 프로젝트에 저장할 뻔했다."""
    service = _service(selected='')

    with pytest.raises(ValueError, match='프로젝트를 먼저 선택'):
        service.list_schedules()

    assert service.store.loaded == []


def test_it_asks_the_one_owner():
    """`selected_project_id` 가 아니라 검사까지 하는 쪽을 부른다."""
    import inspect

    source = inspect.getsource(ScheduleService._follow_selected_project)
    assert 'require_selected_project_id()' in source
    assert "'default'" not in source


# --------------------------------------------------------------------------- #
# 소유권 · 슬레이브는 못 고친다
# --------------------------------------------------------------------------- #

@pytest.fixture
def slave(monkeypatch):
    from motion_web_bridge import schedule_service

    monkeypatch.setattr(
        schedule_service, 'resolve_master_role',
        lambda **_kwargs: type('R', (), {'is_master': False, 'reason': '마스터가 아님'})(),
    )


@pytest.mark.parametrize('call', [
    lambda s: s.save_schedule({'schedule_name': 'x', 'start_time': '09:00:00'}),
    lambda s: s.delete_schedule('id-1'),
    lambda s: s.set_enabled('id-1', True),
])
def test_a_slave_cannot_change_schedules(slave, call):
    """돌지 않는 스케줄이 조용히 쌓이면 안 된다 · §6-69"""
    with pytest.raises(ScheduleOwnershipError, match='슬레이브'):
        call(_service())


def test_a_slave_can_still_read(slave):
    """읽기까지 막으면 왜 안 되는지도 못 본다."""
    service = _service()

    assert service.list_schedules() == []
    assert service.status()['is_master'] is False


def test_the_run_mode_is_not_owner_gated(slave):
    """수동 모드로 바꾸는 것은 정비 행위다 · 슬레이브도 해야 한다."""
    assert _service().set_run_mode('manual')['run_mode'] == 'manual'


# --------------------------------------------------------------------------- #
# 쓰기
# --------------------------------------------------------------------------- #

def test_an_unknown_run_mode_is_refused():
    with pytest.raises(ValueError, match='schedule 또는 manual'):
        _service().set_run_mode('무엇')


def test_saving_stamps_this_pc_timezone():
    """브라우저가 정하게 두면 원격 설정 때 엉뚱한 시간대가 박힌다 · §6-150"""
    service = _service()

    service.save_schedule({'schedule_name': '아침', 'start_time': '09:00:00'})

    assert service.store.items[0].saved_timezone


def test_a_missing_schedule_is_reported_as_missing():
    service = _service()
    service.store.accept = False

    with pytest.raises(ScheduleNotFound):
        service.delete_schedule('없는-id')


def test_a_failed_save_is_not_silent():
    service = _service()
    service.store.accept = False

    with pytest.raises(RuntimeError, match='저장하지 못했습니다'):
        service.save_schedule({'schedule_name': 'x', 'start_time': '09:00:00'})


# --------------------------------------------------------------------------- #
# 상태 · 연동 노드가 없어도 떠야 한다
# --------------------------------------------------------------------------- #

def test_status_works_without_a_coordination_node():
    """조정 노드가 없는 PC 에서도 화면은 떠야 한다."""
    status = _service().status()

    assert status['coordination_enabled'] is False
    assert status['coordination_joined'] is False
    assert status['clock']


def test_status_survives_a_broken_node_reader():
    """상태 조회 실패가 화면 전체를 막으면 안 된다."""
    service = _service()

    def explode():
        raise RuntimeError('노드 응답 없음')

    service.bridge.schedule_node_status = explode

    assert service.status()['schedule_node_seen'] is False
