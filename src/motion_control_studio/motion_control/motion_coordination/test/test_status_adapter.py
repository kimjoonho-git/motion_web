import copy
import json

import pytest

from motion_coordination.status_adapter import (
    adapt_readiness_result,
    adapt_status,
    validate_readiness_payload,
    validate_status_payload,
)


def _local_status():
    return {
        'bridge_state': 'ok',
        'bridge_instance_id': 'private-bridge-session',
        'service_management': {'runtime': {'phase': 'ready'}},
        'project_scope': {
            'selected_project_id': 'project-a',
            'runtime_project_id': 'project-a',
            'motor_config_applied': True,
        },
        'execution_context': {
            'ready': True,
            'project_id': 'project-a',
            'context_id': 'private-context',
            'files': {'motion': {'name': 'private-motion.jsonl'}},
        },
        'motion_state': {
            'motors': [
                {
                    'controller_index': 0,
                    'connection_state': 'online',
                    'connection_connected': True,
                    'fault': False,
                    'position': 123.0,
                },
                {
                    'controller_index': 1,
                    'connection_state': 'online',
                    'connection_connected': True,
                    'fault': False,
                    'position': 456.0,
                },
            ],
        },
        'safety_status': {
            'commands_blocked': False,
            'servo_alarm_policy_project_id': 'project-a',
        },
        'motion_run_status': {
            'state': 'running',
            'motion_file_id': 'private-motion.jsonl',
            'mapping_file_id': 'private-mapping.yaml',
        },
    }


def test_status_adapter_emits_only_approved_summary_fields():
    payload = adapt_status(_local_status(), display_name='PC A')

    assert payload == {
        'status_payload_version': 1,
        'display_name': 'PC A',
        'program': {'state': 'ready'},
        'configuration': {'motor': 'ready', 'motion': 'ready'},
        'motors': {
            'state': 'online',
            'total_count': 2,
            'online_count': 2,
            'fault_count': 0,
        },
        'safety': {'state': 'ready'},
        'motion': {'state': 'running'},
        'coordination': {
            'mode': 'off',
            'role': 'peer',
            'coordinator_machine_id': '',
        },
    }


def test_project_specific_values_do_not_change_network_status():
    first = _local_status()
    second = copy.deepcopy(first)
    second['project_scope']['selected_project_id'] = 'project-b'
    second['project_scope']['runtime_project_id'] = 'project-b'
    second['execution_context']['project_id'] = 'project-b'
    second['execution_context']['context_id'] = 'another-context'
    second['execution_context']['files']['motion']['name'] = 'other-motion.jsonl'
    second['motion_run_status']['motion_file_id'] = 'other-motion.jsonl'
    second['motion_run_status']['mapping_file_id'] = 'other-mapping.yaml'

    assert adapt_status(first) == adapt_status(second)
    encoded = json.dumps(adapt_status(second), ensure_ascii=False)
    assert 'project-b' not in encoded
    assert 'other-motion' not in encoded
    assert 'other-mapping' not in encoded


def test_adapter_does_not_mutate_local_status():
    local = _local_status()
    original = copy.deepcopy(local)

    adapt_status(local)

    assert local == original


def test_motor_summary_reports_offline_and_fault_without_axis_details():
    local = _local_status()
    local['motion_state']['motors'][0]['connection_state'] = 'offline'
    local['motion_state']['motors'][0]['connection_connected'] = False
    local['motion_state']['motors'][1]['fault'] = True

    motors = adapt_status(local)['motors']

    assert motors == {
        'state': 'error',
        'total_count': 2,
        'online_count': 1,
        'fault_count': 1,
    }
    assert 'axes' not in motors


def test_unknown_internal_motion_state_fails_closed():
    local = _local_status()
    local['motion_run_status']['state'] = 'future_internal_state'

    assert adapt_status(local)['motion']['state'] == 'unknown'


def test_missing_local_sections_are_reported_as_unknown():
    payload = adapt_status({})

    assert payload['program']['state'] == 'unknown'
    assert payload['configuration'] == {'motor': 'unknown', 'motion': 'unknown'}
    assert payload['motors']['state'] == 'unknown'
    assert payload['safety']['state'] == 'unknown'
    assert payload['motion']['state'] == 'unknown'


@pytest.mark.parametrize(
    ('path', 'private_value'),
    [
        ((), {'project_id': 'private-project'}),
        (('motors',), {'axes': [{'position': 123.0}]}),
        (('motion',), {'motion_file_id': 'private-motion.jsonl'}),
    ],
)
def test_untrusted_status_rejects_unapproved_private_fields(path, private_value):
    payload = adapt_status({})
    target = payload
    for key in path:
        target = target[key]
    target.update(private_value)

    with pytest.raises(ValueError, match='허용되지 않은 필드'):
        validate_status_payload(payload)


def test_readiness_adapter_does_not_expose_local_project_or_file_names():
    readiness = adapt_readiness_result({
        'success': False,
        'message': 'project-a/private-motion.jsonl motion file missing',
    })

    assert readiness['reason_code'] == 'project_required'
    assert 'project-a' not in json.dumps(readiness, ensure_ascii=False)
    assert 'private-motion' not in json.dumps(readiness, ensure_ascii=False)
    assert validate_readiness_payload(readiness) == readiness


def test_readiness_exposes_only_duration_and_initialization_estimate():
    readiness = adapt_readiness_result({
        'success': True,
        'summary': {
            'duration_sec': 12.5,
            'initialization_duration_sec': 4.0,
            'motion_file_id': 'private-motion.jsonl',
        },
    })
    assert readiness['motion_duration_sec'] == 12.5
    assert readiness['initialization_duration_sec'] == 4.0
    assert 'private-motion' not in json.dumps(readiness, ensure_ascii=False)
    assert validate_readiness_payload(readiness) == readiness
