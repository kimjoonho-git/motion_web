"""그룹 나가기는 **남는다** · §6-282

전에는 나가기가 메모리에만 남았다 · 노드가 뜰 때마다 설정만 보고 자동으로
참가했으므로, 서비스가 다시 뜨면(갱신·재부팅·설정 저장) 도로 들어갔다 ·
사람 눈에는 「화면에서만 막고 그룹은 계속 잡고 있다」로 보였다.

마지막으로 누른 것이 그대로 남는다 · 참가했으면 다시 떠도 참가, 나갔으면
다시 떠도 나간 채다.
"""

from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from motion_common.group_config import (
    GroupConfig,
    load_group_config,
    save_group_config,
)


def _config(**changes):
    base = GroupConfig(
        pc_id='pc_a', display_name='A', enabled=True,
        group_id='group1', dds_domain_id=21,
    )
    return replace(base, **changes) if changes else base


def test_leaving_is_written_to_the_settings(tmp_path: Path):
    path = tmp_path / 'motion_coordination.yaml'

    save_group_config(path, _config(joined=False))

    assert load_group_config(path).joined is False, '나간 것이 안 남았다'


def test_joining_is_written_to_the_settings(tmp_path: Path):
    path = tmp_path / 'motion_coordination.yaml'

    save_group_config(path, _config(joined=True))

    assert load_group_config(path).joined is True


def test_an_old_settings_file_keeps_working(tmp_path: Path):
    """옛 설정에는 이 값이 없다 · 그때 규칙(설정돼 있으면 참가)을 그대로 옮긴다.

    갱신했다고 돌고 있던 그룹이 빠지면 안 된다.
    """
    path = tmp_path / 'motion_coordination.yaml'
    path.write_text(yaml.safe_dump({
        'version': 2, 'pc_id': 'pc_a', 'enabled': True,
        'group_id': 'group1', 'dds_domain_id': 21,
    }), encoding='utf-8')

    assert load_group_config(path).joined is True


def test_an_old_settings_file_that_was_off_stays_off(tmp_path: Path):
    path = tmp_path / 'motion_coordination.yaml'
    path.write_text(yaml.safe_dump({
        'version': 2, 'pc_id': 'pc_a', 'enabled': False,
        'group_id': 'group1', 'dds_domain_id': 21,
    }), encoding='utf-8')

    assert load_group_config(path).joined is False


def test_the_node_starts_from_what_was_written():
    """노드가 뜰 때 보는 것은 **설정에 적힌 참가 여부**다."""
    source = (
        Path(__file__).resolve().parents[1]
        / 'motion_coordination' / 'coordination_node.py'
    ).read_text(encoding='utf-8')

    assert 'self._joined = bool(self._config.configured and self._config.joined)' in source, (
        '뜰 때 설정을 안 보고 자동으로 참가한다'
    )


def test_leaving_also_gives_the_midi_surface_back():
    """넘겨 둔 MIDI 도 되찾는다 · 안 그러면 나갔는데 표면이 남의 것으로 남는다."""
    source = (
        Path(__file__).resolve().parents[1]
        / 'motion_coordination' / 'coordination_node.py'
    ).read_text(encoding='utf-8')
    leave = source[source.index('def _leave_group'):source.index('def _alarm_callback')]

    assert "relay.set_target('')" in leave
    assert 'self._remember_joined(False)' in leave


# 준비 확인 예산은 **설정 하나**에서 나온다 · §6-297
#
# 전에는 기다리는 시간(4초)과 브리지가 안에서 쓰는 시간(10초)이 따로 적혀
# 있었고 서로 어긋났다 · 모터가 멀쩡해도 「로컬 Web Bridge 응답 없음: timed
# out」이 떴고, 진짜 원인은 어디에도 안 실렸다.

def test_the_readiness_budget_comes_from_the_settings():
    source = (
        Path(__file__).resolve().parents[1]
        / 'motion_coordination' / 'coordination_node.py'
    ).read_text(encoding='utf-8')
    body = source[source.index('def _local_readiness'):source.index('def _call_local_control')]

    assert 'self._config.prepare_timeout_sec' in body, '예산이 설정에서 안 나온다'
    assert "'budget_sec': budget" in body, '브리지에 예산을 안 알려 준다'
    assert 'timeout_sec=budget' in body, '기다리는 시간이 예산과 다르다'
