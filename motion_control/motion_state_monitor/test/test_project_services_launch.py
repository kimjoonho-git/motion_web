"""Integrated project-service launch wiring contracts."""

from __future__ import annotations

import ast
from pathlib import Path


LAUNCH_FILE = (
    Path(__file__).resolve().parents[1] / 'launch' / 'project_services.launch.py'
)


def _node_parameters() -> dict[str, dict[str, str]]:
    tree = ast.parse(LAUNCH_FILE.read_text(encoding='utf-8'))
    result: dict[str, dict[str, str]] = {}
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name):
            continue
        if call.func.id != 'Node':
            continue
        keywords = {keyword.arg: keyword.value for keyword in call.keywords}
        executable = keywords.get('executable')
        parameters = keywords.get('parameters')
        if not isinstance(executable, ast.Constant) or not isinstance(
            executable.value, str
        ):
            continue
        node_parameters: dict[str, str] = {}
        if (
            isinstance(parameters, ast.List)
            and parameters.elts
            and isinstance(parameters.elts[0], ast.Dict)
        ):
            for key, value in zip(
                parameters.elts[0].keys,
                parameters.elts[0].values,
            ):
                if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                    continue
                if (
                    isinstance(value, ast.Call)
                    and isinstance(value.func, ast.Name)
                    and value.func.id == 'LaunchConfiguration'
                    and value.args
                    and isinstance(value.args[0], ast.Constant)
                ):
                    node_parameters[key.value] = str(value.args[0].value)
        result[executable.value] = node_parameters
    return result


def test_studio_topics_are_wired_only_to_studio_node():
    parameters = _node_parameters()

    assert 'request_topic' not in parameters['motion_run_manager']
    assert 'response_topic' not in parameters['motion_run_manager']
    assert parameters['motion_studio_node']['request_topic'] == (
        'motion_studio_request_topic'
    )
    assert parameters['motion_studio_node']['response_topic'] == (
        'motion_studio_response_topic'
    )


def test_web_bridge_uses_the_same_studio_launch_arguments():
    parameters = _node_parameters()['motion_web_bridge']

    assert parameters['motion_studio_request_topic'] == (
        'motion_studio_request_topic'
    )
    assert parameters['motion_studio_response_topic'] == (
        'motion_studio_response_topic'
    )
    assert parameters['motion_studio_editor_request_topic'] == (
        'motion_studio_editor_request_topic'
    )
    assert parameters['motion_studio_editor_response_topic'] == (
        'motion_studio_editor_response_topic'
    )
