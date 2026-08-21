"""마스터 역할 판정 검증.

다중 PC에서 전원이 마스터가 되어 같은 모션을 중복 발화하던 결함의 회귀 방지.
"""

import pytest

yaml = pytest.importorskip('yaml')

from motion_common import coordination  # noqa: E402


def write(path, data):
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding='utf-8')
    return path


def test_missing_file_is_treated_as_standalone_master(tmp_path):
    role = coordination.resolve_master_role(tmp_path / 'absent.yaml')
    assert role.is_master is True
    assert '연동 설정 없음' in role.reason


def test_enabled_master_is_master(tmp_path):
    path = write(tmp_path / 'c.yaml', {'enabled': True, 'is_master': True, 'pc_id': 'a'})
    role = coordination.resolve_master_role(path)
    assert role.is_master is True
    assert 'pc_id=a' in role.reason


def test_enabled_slave_is_not_master(tmp_path):
    """이 검증이 핵심 · 슬레이브에서 스케줄이 발화하면 안 된다."""
    path = write(tmp_path / 'c.yaml', {'enabled': True, 'is_master': False, 'pc_id': 'b'})
    assert coordination.resolve_master_role(path).is_master is False


def test_missing_is_master_key_defaults_to_not_master(tmp_path):
    # 정본 로더(group_configuration)와 같은 기본값
    path = write(tmp_path / 'c.yaml', {'enabled': True, 'pc_id': 'c'})
    assert coordination.resolve_master_role(path).is_master is False


def test_disabled_coordination_is_standalone_master(tmp_path):
    path = write(tmp_path / 'c.yaml', {'enabled': False, 'is_master': False})
    role = coordination.resolve_master_role(path)
    assert role.is_master is True
    assert '연동 비활성' in role.reason


def test_malformed_file_refuses_master_role(tmp_path):
    path = tmp_path / 'c.yaml'
    path.write_text('enabled: true\n  bad indent: [', encoding='utf-8')
    role = coordination.resolve_master_role(path)
    assert role.is_master is False
    assert '읽지 못함' in role.reason


def test_non_mapping_document_refuses_master_role(tmp_path):
    path = tmp_path / 'c.yaml'
    path.write_text('- just\n- a\n- list\n', encoding='utf-8')
    assert coordination.resolve_master_role(path).is_master is False


def test_settings_path_points_at_canonical_file(monkeypatch, tmp_path):
    monkeypatch.setenv('MOTION_WORKSPACE', str(tmp_path))
    assert coordination.coordination_settings_path() == (
        tmp_path.resolve() / 'config' / 'motion_coordination.yaml'
    )


def test_load_returns_none_for_missing_file(tmp_path):
    assert coordination.load_coordination_settings(tmp_path / 'absent.yaml') is None


def test_is_master_pc_shorthand_matches_resolve(tmp_path):
    path = write(tmp_path / 'c.yaml', {'enabled': True, 'is_master': False})
    assert coordination.is_master_pc(path) is False
