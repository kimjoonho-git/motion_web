"""모터 실행 상태 저장소 계약 · §6-47 · §6-49.

`.motor_runtime.json` 하나에 **적용된 설정**과 **조작 진행 상황**이 함께 들어 있고
웹·조율·모니터가 모두 이 파일로 이야기한다. 여기가 틀리면 두 작업이 동시에
모터를 건드리거나, 끝난 작업이 영원히 진행 중으로 남는다.
"""

import threading
import time
from pathlib import Path

import pytest
from motion_common import store as common_store

from motion_web_bridge.motor_runtime_store import MotorRuntimeStore


class FakeRepository:
    """저장소는 프로젝트 경로와 선택 상태만 답한다 (§6-47)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.selection_file = root / '.selected_project.json'
        self._selected = 'project-a'

    def selected_project_id(self) -> str:
        return self._selected

    def project_generation(self) -> int:
        return 7

    def _project_dir(self, project_id: str) -> Path:
        return self.root / str(project_id)

    def _read_selection(self):
        return {'project_id': self._selected}

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        with common_store.locked_update(path):
            common_store.atomic_write_text(path, content)


@pytest.fixture()
def runtime(tmp_path):
    repository = FakeRepository(tmp_path)
    return MotorRuntimeStore(repository, tmp_path / '.motor_runtime.json')


# --------------------------------------------------------------------------- #
# 조작 기록 · 한 번에 하나만
# --------------------------------------------------------------------------- #

def test_second_operation_is_refused_while_the_first_runs(runtime):
    runtime.begin_motor_operation('ac_servo_scan', 'preparing', timeout_sec=30.0)

    with pytest.raises(ValueError, match='진행 중'):
        runtime.begin_motor_operation('motor_restart', 'preparing', timeout_sec=30.0)


def test_a_finished_operation_frees_the_slot(runtime):
    first = runtime.begin_motor_operation('ac_servo_scan', 'preparing', timeout_sec=30.0)
    runtime.finish_motor_operation(first['operation_id'], 'success', phase='completed')

    second = runtime.begin_motor_operation('motor_restart', 'preparing', timeout_sec=30.0)

    assert second['operation_id'] != first['operation_id']
    assert second['status'] == 'running'


def test_an_expired_operation_frees_the_slot(runtime):
    """작업이 끝을 알리지 못하고 죽어도 영원히 막히면 안 된다."""
    runtime.begin_motor_operation('ac_servo_scan', 'preparing', timeout_sec=1.0)
    payload = runtime._read_motor_runtime_payload()
    payload['operation']['deadline_at'] = time.time() - 1.0
    runtime._write_motor_runtime_state(payload)

    allowed = runtime.begin_motor_operation('motor_restart', 'preparing', timeout_sec=5.0)

    assert allowed['status'] == 'running'


def test_status_reports_timeout_without_rewriting_the_file(runtime):
    runtime.begin_motor_operation('ac_servo_scan', 'preparing', timeout_sec=1.0)
    payload = runtime._read_motor_runtime_payload()
    payload['operation']['deadline_at'] = time.time() - 1.0
    runtime._write_motor_runtime_state(payload)

    status = runtime.motor_operation_status()

    assert status['status'] == 'timeout'
    assert status['error']
    # 파일은 그대로다 · 읽기가 쓰기를 겸하면 누가 고쳤는지 알 수 없게 된다
    assert runtime._read_motor_runtime_payload()['operation']['status'] == 'running'


def test_finishing_a_different_operation_is_refused(runtime):
    runtime.begin_motor_operation('ac_servo_scan', 'preparing', timeout_sec=30.0)

    with pytest.raises(ValueError, match='일치하지 않습니다'):
        runtime.finish_motor_operation('motor-other', 'success', phase='completed')


def test_unknown_end_status_is_refused(runtime):
    started = runtime.begin_motor_operation('ac_servo_scan', 'preparing', timeout_sec=30.0)

    with pytest.raises(ValueError, match='올바르지 않은'):
        runtime.finish_motor_operation(started['operation_id'], 'done', phase='completed')


def test_update_keeps_the_operation_identity(runtime):
    started = runtime.begin_motor_operation('ac_servo_scan', 'preparing', timeout_sec=30.0)

    updated = runtime.update_motor_operation(
        started['operation_id'], 'scanning', message='진행 중',
    )

    assert updated['operation_id'] == started['operation_id']
    assert updated['phase'] == 'scanning'
    assert updated['status'] == 'running'


def test_finish_merges_details_instead_of_replacing_them(runtime):
    started = runtime.begin_motor_operation(
        'ac_servo_scan', 'preparing', timeout_sec=30.0, details={'project_id': 'p1'},
    )

    done = runtime.finish_motor_operation(
        started['operation_id'], 'partial', phase='partial', details={'axes': 1},
    )

    assert done['details'] == {'project_id': 'p1', 'axes': 1}


def test_status_is_empty_before_anything_ran(runtime):
    assert runtime.motor_operation_status() == {}


# --------------------------------------------------------------------------- #
# 파일과 락
# --------------------------------------------------------------------------- #

def test_the_store_owns_its_file_and_lock(runtime, tmp_path):
    runtime.begin_motor_operation('ac_servo_scan', 'preparing', timeout_sec=30.0)

    assert runtime.path == tmp_path / '.motor_runtime.json'
    assert runtime.path.is_file()
    # 락 파일은 숨김 이름으로 그 옆에 선다 (§6-24)
    assert common_store.lock_path_for(runtime.path).name == '..motor_runtime.json.lock'


#: 읽고-고치고-쓰는 메서드 · 전부 하나의 락 안에서 끝나야 한다
MUTATING = (
    'mark_runtime_motor_config_applied',
    'restore_motor_runtime_target',
    'begin_motor_operation',
    'update_motor_operation',
    'finish_motor_operation',
    'clear_motor_runtime_target',
    'motor_runtime_state',
    'motor_operation_status',
)


@pytest.mark.parametrize('name', MUTATING)
def test_every_read_modify_write_is_lock_wrapped(name):
    """기록 한 번이 원자적인 것으로는 부족하다.

    조율·모니터가 같은 파일을 본다 · 읽고 고쳐 쓰는 사이에 남이 끼어들면
    나중 기록이 앞선 수정을 지운다. **읽기부터 쓰기까지 한 락 안**이어야 한다.

    `_motor_runtime_locked`가 `functools.wraps`를 쓰므로 감싼 흔적이 남는다 ·
    데코레이터를 떼면 이 시험이 곧바로 걸린다.
    """
    method = getattr(MotorRuntimeStore, name)
    assert hasattr(method, '__wrapped__'), f'{name}: 파일락으로 감싸여 있지 않다'


def test_concurrent_updates_do_not_lose_details(runtime):
    """두 스레드가 같은 작업의 세부를 덧붙여도 하나도 사라지지 않아야 한다."""
    started = runtime.begin_motor_operation(
        'ac_servo_scan', 'preparing', timeout_sec=30.0,
    )
    barrier = threading.Barrier(4)

    def add(index):
        barrier.wait(timeout=5.0)
        for step in range(5):
            runtime.update_motor_operation(
                started['operation_id'],
                'scanning',
                details={f'w{index}-{step}': True},
            )

    threads = [threading.Thread(target=add, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20.0)

    details = runtime.motor_operation_status()['details']
    assert len([k for k in details if k.startswith('w')]) == 20
