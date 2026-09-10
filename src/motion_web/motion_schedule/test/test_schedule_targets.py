"""스케줄이 단독·연동 어느 쪽으로 나가는지 · §6-68.

스케줄은 그룹 전용이었다 · `start_group` 만 보내서 연동을 쓰지 않는 PC 에서는
발화는 하는데 "먼저 DDS 그룹에 참가하세요" 로 매번 실패했다. `resolve_master_role`
이 연동 미사용을 "단독 동작으로 간주" 하며 마스터 판정을 통과시키기 때문에 조용히
실패했다 · 로그를 보지 않으면 알 수 없었다.

노드를 띄우려면 rclpy 가 필요하므로, 여기서는 **어느 엔드포인트로 나가는지**를
소스에서 확인한다.
"""

from pathlib import Path

NODE = (
    Path(__file__).resolve().parents[1]
    / 'motion_schedule' / 'motion_schedule_node.py'
).read_text(encoding='utf-8')


def _body(name: str) -> str:
    start = NODE.index(f'def {name}(')
    nxt = NODE.find('\n    def ', start)
    return NODE[start:nxt if nxt > 0 else len(NODE)]


def test_schedule_start_targets_both_scopes():
    body = _body('_execute_start')
    assert 'self._coordination_enabled()' in body, '범위를 보지 않는다'
    assert '/api/coordination/control' in body, '연동 경로가 없다'
    assert '/api/motion-run/start' in body, '단독 경로가 없다'
    # 연동 경로가 먼저 나가고 단독으로 떨어진다
    assert body.index('/api/coordination/control') < body.index('/api/motion-run/start')


def test_schedule_stop_targets_both_scopes():
    body = _body('_execute_stop_after_cycle')
    assert 'self._coordination_enabled()' in body
    assert '/api/coordination/control' in body
    assert '/api/motion-run/stop-after-cycle' in body


def test_coordination_enabled_reads_the_settings_file():
    body = _body('_coordination_enabled')
    assert 'load_coordination_settings' in body
    # 설정이 없거나 읽지 못하면 단독으로 본다 · 그룹 명령이 실패하는 것보다 낫다
    assert 'return False' in body
    assert "settings.get('enabled'" in body


def test_local_run_fills_active_project_files():
    """단독 스케줄은 무엇을 재생할지 모른다 · 브리지가 활성 파일로 채운다."""
    bridge = (
        Path(__file__).resolve().parents[2]
        / 'web_bridge' / 'motion_web_bridge' / 'bridge_node.py'
    ).read_text(encoding='utf-8')
    start = bridge.index('def motion_run_start(')
    body = bridge[start:bridge.index('\n    def ', start)]
    assert '_with_active_project_files(payload)' in body
