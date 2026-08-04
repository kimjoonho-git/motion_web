from motion_web_bridge.bridge_node import MotionWebBridge
from motion_web_bridge.motor_restart_diagnostics import (
    diagnose_motor_restart_failure,
    motor_restart_service_failure,
)
from motion_web_bridge.project_repository import ProjectRepository


def _operation(*axes):
    return {'details': {'expected_axes': list(axes)}}


def test_ethercat_disconnection_has_axis_and_user_checklist():
    result = diagnose_motor_restart_failure(
        _operation(0),
        {
            'motors': [{
                'controller_index': 0,
                'display_name': '왼쪽 서보',
                'transport': 'ethercat',
                'connection_state': 'bus_down',
                'connection_connected': False,
                'fault': False,
            }],
        },
        {'phase': 'ready'},
    )

    assert result['failure_code'] == 'ethercat_not_ready'
    assert result['pending_axes'] == [0]
    assert '왼쪽 서보 (EtherCAT 버스 미연결)' in result['message']
    assert '서보 전원, EtherCAT 케이블 및 Master 상태' in result['message']


def test_serial_disconnection_has_dynamixel_checklist():
    result = diagnose_motor_restart_failure(
        _operation(2),
        {
            'motors': [{
                'controller_index': 2,
                'transport': 'serial',
                'connection_state': 'offline',
                'connection_connected': False,
            }],
        },
        {'phase': 'ready'},
    )

    assert result['failure_code'] == 'serial_not_ready'
    assert 'Dynamixel 통신 연결' in result['message']
    assert 'USB/직렬 케이블 및 포트 상태' in result['message']


def test_service_failure_guidance_does_not_claim_ethercat_is_the_cause():
    message = motor_restart_service_failure('service exited')

    assert '원인: service exited.' in message
    assert 'AC Servo 사용 시' in message


def test_motor_restart_timeout_persists_diagnosis_for_the_popup(
    tmp_path,
    monkeypatch,
):
    repository = ProjectRepository(tmp_path / 'projects')
    runtime = tmp_path / 'runtime.yaml'
    runtime.write_text('masters: []\n', encoding='utf-8')
    monkeypatch.setattr(
        'motion_web_bridge.project_repository.time.time',
        lambda: 100.0,
    )
    operation = repository.begin_motor_operation(
        'motor_restart',
        'verifying',
        timeout_sec=1.0,
        details={
            'runtime_file': str(runtime),
            'expected_axes': [0],
        },
    )
    monkeypatch.setattr(
        'motion_web_bridge.project_repository.time.time',
        lambda: 102.0,
    )
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.project_repository = repository
    bridge._bridge_started_at = 99.0

    result = bridge._reconcile_motor_operation_status(
        {'phase': 'ready'},
        {
            'motors': [{
                'controller_index': 0,
                'transport': 'ethercat',
                'connection_state': 'offline',
                'connection_connected': False,
                'fault': False,
            }],
        },
        {},
    )

    assert result['status'] == 'timeout'
    assert result['phase'] == 'timed_out'
    assert result['details']['failure_code'] == 'ethercat_not_ready'
    assert result['details']['pending_axes'] == [0]
    assert 'EtherCAT 연결을 확인하지 못했습니다' in result['error']
