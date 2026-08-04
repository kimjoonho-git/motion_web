import json

import pytest

from motion_coordination.execution_control import (
    ExecutionLease,
    OperationJournal,
    build_synchronized_schedule,
    bounded_parallel_map,
    start_error_ms,
    validate_control_payload,
)


def test_control_payload_rejects_project_and_motion_data():
    with pytest.raises(ValueError):
        validate_control_payload({
            'network_operation_id': 'op-1', 'command': 'run_once',
            'project_id': 'secret-project',
        })


def test_start_at_contract_requires_lease_and_absolute_schedule():
    value = validate_control_payload({
        'network_operation_id': 'op-1', 'command': 'start_at',
        'lease_id': 'lease-1', 'start_at': 100.0,
        'cycle_sec': 1.2, 'repeat_count': 3, 'hold_final': True,
    })
    assert value['repeat_count'] == 3
    with pytest.raises(ValueError):
        validate_control_payload({
            'network_operation_id': 'op-2', 'command': 'start_at',
        })


def test_execution_lease_is_atomic_and_expires():
    now = [100.0]
    lease = ExecutionLease(clock=lambda: now[0])
    acquired = lease.acquire('pc-a', duration_sec=5, lease_id='lease-a')
    assert acquired['state'] == 'network'
    with pytest.raises(ValueError):
        lease.acquire('pc-b')
    lease.require('pc-a', 'lease-a')
    now[0] = 106.0
    assert lease.snapshot() == {'state': 'local'}
    with pytest.raises(ValueError):
        lease.require('pc-a', 'lease-a')


def test_operation_journal_blocks_duplicate_after_new_instance(tmp_path):
    path = tmp_path / 'operations.json'
    first = OperationJournal(path)
    first.begin('pc-a', 'op-1', 'run_once')
    first.finish('pc-a', 'op-1', {'success': True})
    second = OperationJournal(path)
    with pytest.raises(ValueError):
        second.begin('pc-a', 'op-1', 'run_once')
    stored = json.loads(path.read_text(encoding='utf-8'))
    assert stored['pc-a:op-1']['state'] == 'completed'


def test_schedule_uses_longest_motion_and_absolute_boundaries():
    schedule = build_synchronized_schedule(
        [7.0, 9.0, 12.001], start_at=1000.0,
        dwell_sec=2.0, repeat_count=4,
    )
    assert schedule['cycle_sec'] == pytest.approx(14.02)
    assert schedule['cycle_starts'] == pytest.approx([
        1000.0, 1014.02, 1028.04, 1042.06,
    ])
    assert start_error_ms(1000.0, 1000.0075) == pytest.approx(7.5)


@pytest.mark.parametrize('pc_count', [4, 16, 32])
def test_schedule_contract_scales_to_supported_pc_counts(pc_count):
    durations = [1.0 + (index * 0.01) for index in range(pc_count)]
    schedule = build_synchronized_schedule(
        durations, start_at=2000.0, repeat_count=100,
    )
    assert schedule['longest_motion_sec'] == pytest.approx(max(durations))
    assert len(schedule['cycle_starts']) == 100
    for index, started_at in enumerate(schedule['cycle_starts']):
        assert started_at == pytest.approx(2000.0 + index * schedule['cycle_sec'])


@pytest.mark.parametrize('pc_count', [4, 16, 32])
def test_peer_work_is_bounded_and_complete(pc_count):
    import threading
    import time
    lock = threading.Lock()
    active = 0
    peak = 0

    def work(value):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.002)
        with lock:
            active -= 1
        return value * 2

    assert bounded_parallel_map(work, range(pc_count)) == [
        value * 2 for value in range(pc_count)
    ]
    assert peak <= 16
