import pytest

from motion_web_bridge.bridge_node import MotionWebBridge
from motion_web_bridge.project_repository import ProjectRepository
from motion_web_bridge.servo_alarm_policy import (
    SERVO_ALARM_CATALOG,
    catalog_payload,
    effective_grade_map,
    normalize_overrides,
    policy_revision,
)


def test_catalog_contains_panasonic_main_alarm_families_and_default_grades():
    codes = {entry['code'] for entry in SERVO_ALARM_CATALOG}
    grades = effective_grade_map()

    assert {11, 16, 24, 80, 98}.issubset(codes)
    assert grades['16'] == 1
    assert grades['24'] == 2
    assert grades['98'] == 3


def test_invalid_or_unknown_project_overrides_are_discarded():
    overrides = normalize_overrides({
        '16': 3,
        '24.0': 1,
        '80': 4,
        '999': 2,
        'bad': 1,
    })

    assert overrides == {'16': 3, '24': 1}
    rows = {row['code']: row for row in catalog_payload(overrides)}
    assert rows[16]['effective_grade'] == 3
    assert rows[16]['modified'] is True
    assert rows[80]['effective_grade'] == rows[80]['default_grade']


def test_effective_action_changes_with_project_grade():
    rows = {row['code']: row for row in catalog_payload({'16': 3})}

    assert rows[16]['default_action'] == '해당 에러축 정지'
    assert rows[16]['effective_grade'] == 3
    assert rows[16]['action'] == '전체 모터 제어 차단'


def test_policy_revision_changes_with_effective_grade_or_catalog_version():
    grades = effective_grade_map()

    assert policy_revision(grades, 1) == policy_revision(dict(grades), 1)
    changed = dict(grades)
    changed['16'] = 3
    assert policy_revision(changed, 1) != policy_revision(grades, 1)
    assert policy_revision(grades, 2) != policy_revision(grades, 1)


def test_servo_alarm_policy_is_isolated_between_projects(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    first_id = repository.create_project('first')['project']['project_id']
    second_id = repository.create_project('second')['project']['project_id']

    repository.select_project(first_id)
    repository.save_servo_alarm_policy(first_id, {'16': 2})
    repository.select_project(second_id)
    repository.save_servo_alarm_policy(second_id, {'16': 3, '24': 1})

    assert repository.load_servo_alarm_policy(first_id)['overrides'] == {'16': 2}
    assert repository.load_servo_alarm_policy(second_id)['overrides'] == {
        '16': 3,
        '24': 1,
    }


def test_policy_is_not_saved_when_supervisor_does_not_acknowledge(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    project_id = repository.create_project('transaction')['project']['project_id']
    repository.select_project(project_id)
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.project_repository = repository
    bridge._project_generation = 1
    bridge._ensure_project_change_allowed = lambda: None
    bridge.publish_servo_alarm_policy = lambda _policy=None: {
        'success': False,
        'message': 'no acknowledgement',
    }

    with pytest.raises(ValueError, match='저장하지 않았습니다'):
        bridge.save_servo_alarm_policy({'overrides': {'16': 3}})

    assert repository.load_servo_alarm_policy(project_id)['overrides'] == {}


def test_policy_save_returns_the_revision_acknowledged_by_supervisor(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    project_id = repository.create_project('transaction-ok')['project']['project_id']
    repository.select_project(project_id)
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.project_repository = repository
    bridge._project_generation = 1
    bridge._ensure_project_change_allowed = lambda: None
    applied = []
    bridge.publish_servo_alarm_policy = lambda policy=None: (
        applied.append(dict(policy or {}))
        or {'success': True, 'message': 'applied'}
    )

    result = bridge.save_servo_alarm_policy({'overrides': {'16': 3}})

    assert result['supervisor_applied'] is True
    assert result['policy_revision'] == applied[-1]['policy_revision']
    assert repository.load_servo_alarm_policy(project_id)['overrides'] == {'16': 3}
