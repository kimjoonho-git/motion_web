"""서비스가 남의 속을 함부로 만지지 않는다 · §6-170

**한 메서드 때문에 프로젝트가 온 집안을 알아야 했다.**

`project_service.clear_scoped_memory()` 는 프로젝트가 바뀔 때 옛 기억을
버리는 일인데, 그것을 이렇게 했다.

    with self.bridge._lock:
        self.bridge._motion_state = None
    with self.bridge._motion_run_lock:
        self.bridge._motion_run_status = {}
    with self.bridge._midi_monitor_lock:
        self.bridge._midi_monitor_status = {}
    self.bridge._midi_monitor_store.clear()
    ...

프로젝트를 다루는 쪽이 **브리지의 속살 13가지**와 **락 3개**를 알아야 했다 ·
그래서 프로젝트를 건드릴 때마다 MIDI·모터·스튜디오가 딸려 왔고, 어느 하나의
이름이 바뀌면 프로젝트 쪽이 깨졌다.

이제 프로젝트 쪽은 한 마디만 한다 — 「잊어라」 · 무엇을 어떻게 잊을지는
가진 쪽이 안다.

여기서 지키는 것 : **결합도는 늘지 않는다.**

상한은 그때그때의 실제 값이다 · 줄면 같이 낮춘다 · 안 그러면 「여기까지는
괜찮다」는 여유가 생기고, 그 여유는 반드시 쓰인다.

    2026-09-18   project_service 18 → 5
    2026-09-19   project_service  5 → 0 · scan_orchestrator 4 → 0
                 manual_motor_commands 4 → 1 · motor_runtime_service 4 → 1
                 execution_context_service 9 → 2
"""

import re
from pathlib import Path

import pytest

WORKSPACE = Path(__file__).resolve().parents[4]
SERVICES = WORKSPACE / 'src/motion_web/web_bridge/motion_web_bridge'

#: 서비스마다 브리지에서 꺼내 써도 되는 **가짓수** 상한
#:
#: 지금 값이다 · 줄이는 것은 환영이고 늘리는 것은 막는다 · 늘려야 한다면
#: 그 전에 「그 일의 주인이 정말 여기인가」를 묻는다.
LIMITS = {
    # 0 은 「이제 아예 안 만진다」는 뜻이다 · 다시 생기면 걸린다 · §6-183
    'project_service.py': 0,
    'scan_orchestrator.py': 0,
    'execution_context_service.py': 2,
    'motor_config_service.py': 3,
    'manual_motor_commands.py': 1,
    'motor_runtime_service.py': 1,
}

REACH = re.compile(r'self\.bridge\.(_[a-z_]+)')


def _reach(name: str):
    return set(REACH.findall((SERVICES / name).read_text(encoding='utf-8')))


@pytest.mark.parametrize('name, limit', sorted(LIMITS.items()))
def test_no_service_reaches_deeper_than_it_does_today(name, limit):
    names = _reach(name)
    assert len(names) <= limit, (
        f'{name} 이 브리지 속살을 {len(names)}가지 만집니다 (상한 {limit}) · '
        f'늘어난 것: {sorted(names)}\n'
        '그 일의 주인이 정말 여기인지 먼저 물으세요'
    )


def test_every_service_is_listed():
    """새 서비스가 조용히 늘어나면 이 검사를 비껴간다."""
    found = {
        path.name for path in SERVICES.glob('*.py')
        if REACH.search(path.read_text(encoding='utf-8'))
    }
    assert found <= set(LIMITS), f'상한이 없는 서비스: {sorted(found - set(LIMITS))}'


# --------------------------------------------------------------------------- #
# 옆문 · 길목도 같은 규칙을 받는다 · §6-187
# --------------------------------------------------------------------------- #

ROUTES = SERVICES / 'routes'

#: 길목이 브리지를 부르는 모양 · `bridge._project` 처럼 속살을 집는 것
ROUTE_REACH = re.compile(r'bridge\.(_[a-z_]+)')

#: 길목이 `getattr` 로 속살을 집는 것 · 정규식을 비껴간다
ROUTE_GETATTR = re.compile(r"getattr\(\s*bridge\s*,\s*'(_[a-z_]+)'")


@pytest.mark.parametrize(
    'name', sorted(path.name for path in (SERVICES / 'routes').glob('*.py'))
)
def test_no_route_reaches_into_the_bridge(name):
    """**서비스만 보고 길목을 안 봤다** · 그래서 옆문이 열려 있었다.

    서비스 쪽 결합을 0으로 만들어 놓고도 길목에서 38회 속살을 집었다.

        project_routes   15회   bridge._project
        motor_routes     17회   bridge._scan · _manual · _motor_config …
        system_routes     6회   bridge._motor_config · _coordination_web_bridge

    브리지를 **서비스 찾는 창구**로 쓰면서 밑줄 붙은 칸 이름을 직접 알았다 ·
    서비스 하나의 칸 이름을 바꾸면 길목이 우수수 깨진다.

    이제 창구가 공개돼 있다 — `bridge.project` · `bridge.scan` ·
    `bridge.manual` · `bridge.motor_config` · `bridge.motor_event_log` ·
    `bridge.coordination`.
    """
    text = (ROUTES / name).read_text(encoding='utf-8')
    offenders = sorted(set(ROUTE_REACH.findall(text)) | set(ROUTE_GETATTR.findall(text)))

    assert offenders == [], (
        f'{name} 이 브리지 속살을 집습니다: {offenders}\n'
        '공개된 창구를 쓰세요 · 없으면 브리지에 만드세요'
    )


def test_the_public_counters_exist():
    """창구를 목록에 적었는데 브리지에 없으면 목록이 거짓말이다."""
    bridge = (SERVICES / 'bridge_node.py').read_text(encoding='utf-8')

    for name in (
        'project', 'scan', 'manual',
        'motor_config', 'motor_event_log', 'coordination',
    ):
        assert f'    def {name}(self):' in bridge, f'브리지에 {name} 창구가 없습니다'


def test_the_coordination_counter_tolerates_absence():
    """연동을 쓰지 않는 PC 에서는 없다 · 없다고 터지면 안 된다."""
    bridge = (SERVICES / 'bridge_node.py').read_text(encoding='utf-8')
    start = bridge.index('    def coordination(self):')
    body = bridge[start:bridge.index('\n    @', start)]

    assert 'getattr(' in body and 'None' in body


def test_clearing_memory_is_one_sentence():
    """프로젝트 쪽은 「잊어라」 한 마디만 한다."""
    text = (SERVICES / 'project_service.py').read_text(encoding='utf-8')
    start = text.index('def clear_scoped_memory(')
    body = text[start:text.index('\n    def ', start)]

    assert 'forget_project_memory()' in body
    for forbidden in ('_motion_state', '_motion_run_status', '_midi_monitor_status',
                      '_lock', '_store'):
        assert forbidden not in body.split('"""')[-1], (
            f'프로젝트 쪽이 아직 {forbidden} 을 직접 만집니다'
        )


def test_the_owner_clears_everything_it_used_to():
    """옮기면서 빠뜨리면 옛 프로젝트 값이 남는다 · 그게 다음 유령 버그다."""
    text = (SERVICES / 'bridge_node.py').read_text(encoding='utf-8')
    start = text.index('def forget_project_memory(')
    body = text[start:text.index('\n    def ', start)]

    for cleared in (
        'self._motion_state = None',
        'self._motion_state_received_at = None',
        'self._motion_run_status = {}',
        'self._midi_monitor_status = {}',
        'self._motor_event_log.clear_project_memory()',
        'self._manual.clear_pending()',
        'self._motion_mapping_store.clear()',
        'self._motion_run_store.clear()',
        'self._midi_monitor_store.clear()',
        'self._motion_studio_sync().clear_project_memory()',
        'scan.clear_progress()',
    ):
        assert cleared in body, f'버리는 것을 빠뜨렸습니다: {cleared}'


def test_the_locks_are_still_taken():
    """락 없이 비우면 읽는 쪽이 반쯤 비운 값을 본다."""
    text = (SERVICES / 'bridge_node.py').read_text(encoding='utf-8')
    start = text.index('def forget_project_memory(')
    body = text[start:text.index('\n    def ', start)]

    for lock in ('with self._lock:', 'with self._motion_run_lock:',
                 'with self._midi_monitor_lock:'):
        assert lock in body, f'락을 빠뜨렸습니다: {lock}'
