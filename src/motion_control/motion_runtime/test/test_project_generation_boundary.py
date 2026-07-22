import pytest

from motion_runtime.motion_mapping_manager import MotionMappingManager
from motion_runtime.motion_run_manager import MotionRunManager


@pytest.mark.parametrize('node_type', [MotionMappingManager, MotionRunManager])
def test_old_project_generation_is_rejected_before_command_execution(node_type):
    node = node_type.__new__(node_type)
    node._project_generation = 8

    with pytest.raises(ValueError, match='이전 프로젝트 세대'):
        node._validate_request_generation(
            'invalidate_context', 7, {'project_generation': 7}
        )

    assert node._project_generation == 8


@pytest.mark.parametrize('node_type', [MotionMappingManager, MotionRunManager])
def test_new_boundary_is_adopted_but_operational_future_request_is_rejected(node_type):
    node = node_type.__new__(node_type)
    node._project_generation = 8

    assert node._validate_request_generation(
        'invalidate_context', 9, {'project_generation': 9}
    ) == 9
    assert node._project_generation == 9

    with pytest.raises(ValueError, match='현재 프로젝트 세대'):
        node._validate_request_generation('status', 10, {'project_generation': 10})
