import json

import pytest

from motion_runtime.motion_automation_store import (
    MotionAutomationStore,
    default_automation_state,
    normalize_automation_state,
)


def _project(root, project_id):
    directory = root / project_id
    (directory / 'runtime').mkdir(parents=True)
    (directory / 'project.json').write_text(
        json.dumps({'project_id': project_id}),
        encoding='utf-8',
    )
    return directory


def test_default_automation_is_disabled_and_not_armed():
    assert default_automation_state() == {
        'version': 1,
        'enabled': False,
        'armed': False,
        'repeat_mode': 'direct',
        'dwell_sec': 0.0,
        'motion_file_id': '',
        'mapping_file_id': '',
        'motion_sha256': '',
        'mapping_sha256': '',
        'last_error': '',
        'updated_at': None,
    }


def test_disabled_automation_cannot_remain_armed():
    state = normalize_automation_state({
        'enabled': False,
        'armed': True,
        'repeat_mode': 'dwell',
        'dwell_sec': 10,
    })

    assert state['armed'] is False
    assert state['repeat_mode'] == 'dwell'
    assert state['dwell_sec'] == 10.0


def test_store_isolates_projects_and_writes_only_runtime_state(tmp_path):
    root = tmp_path / 'projects'
    first = _project(root, 'first')
    second = _project(root, 'second')
    store = MotionAutomationStore(root)

    saved = store.save('first', {
        'enabled': True,
        'armed': True,
        'repeat_mode': 'reinitialize',
        'motion_file_id': 'first.json',
        'mapping_file_id': 'first.yaml',
    })

    assert saved['armed'] is True
    assert store.load('first')['motion_file_id'] == 'first.json'
    assert store.load('second') == default_automation_state()
    assert (first / 'runtime' / 'motion_automation.json').is_file()
    assert not (second / 'runtime' / 'motion_automation.json').exists()


@pytest.mark.parametrize('project_id', ('', '../outside', 'nested/project'))
def test_store_rejects_invalid_or_external_project_paths(tmp_path, project_id):
    store = MotionAutomationStore(tmp_path / 'projects')

    with pytest.raises(ValueError):
        store.load(project_id)


@pytest.mark.parametrize(
    'payload',
    (
        {'repeat_mode': 'unknown'},
        {'repeat_mode': 'dwell', 'dwell_sec': -1},
        {'repeat_mode': 'dwell', 'dwell_sec': float('nan')},
    ),
)
def test_invalid_repeat_policy_is_rejected(payload):
    with pytest.raises(ValueError):
        normalize_automation_state(payload)
