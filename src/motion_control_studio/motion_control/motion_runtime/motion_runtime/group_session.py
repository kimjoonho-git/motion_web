"""DDS 그룹 실행 세션 · 여러 PC가 같은 시각에 같은 동작을 하도록 맞춘다.

`MotionRunManager`에서 떼어냈다 · §5 분해 목표안의 `GroupSession` · §6-29

이 객체가 갖는 것 · 세션 상태와 그 조건변수.
**조건변수는 실행 락(`_run_lock`) 위에 만든다** · 그룹 세션과 단일 실행이 같은
자원을 두고 다투므로 락을 나눠 가지면 안 된다. 노드가 락을 소유하고 넘겨준다 ·
`ScanOrchestrator`·`MotorConfigService`가 `lifecycle_lock`을 나눠 갖는 것과 같다.
"""

from __future__ import annotations

import threading
import time
import traceback
from typing import Any, Dict, List, Optional

from motion_common.values import finite_float

from . import motion_run_rules


class GroupSession:
    def __init__(self, manager: Any, *, run_lock: threading.RLock) -> None:
        self.manager = manager
        #: 단일 실행과 같은 락 위에서 기다린다
        self.condition = threading.Condition(run_lock)
        self.session: Dict[str, Any] = {}

    # -- 정지 경로가 부르는 것 ------------------------------------------- #

    def mark_stopping(self) -> None:
        """즉시 정지 · 세션을 비활성으로 표시하고 대기 중인 쪽을 깨운다."""
        with self.condition:
            if self.session:
                self.session.update({'active': False, 'state': 'stopping'})
            self.condition.notify_all()

    def request_stop_after_cycle(self, current: Dict[str, Any]) -> bool:
        """이번 주기까지만 · 활성 세션이 없으면 거짓.

        정지 지시가 실제로 서려면 세션 표시만으로는 부족하다 · 재생 루프가 보는
        `_graceful_stop_event`도 함께 세운다. 아직 움직이는 중이 아니면
        `_stop_event`까지 세워 즉시 끝낸다.
        """
        with self.condition:
            if not self.session.get('active'):
                return False
            self.session['stop_after_cycle'] = True
            self.manager._graceful_stop_event.set()
            if current.get('state') not in {'running', 'verifying', 'motion_completed'}:
                self.manager._stop_event.set()
            self.condition.notify_all()
            return True

    def prepare(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Prepare one persistent group session without enabling local repeat."""
        execution_id = str(payload.get('execution_id') or '').strip()
        initialize_monotonic = finite_float(
            payload.get('initialize_monotonic')
        )
        if not execution_id:
            raise ValueError('그룹 execution_id가 필요합니다')
        if initialize_monotonic is None or initialize_monotonic <= time.monotonic():
            raise ValueError('그룹 초기 위치 이동 예약 트리거가 이미 지났습니다')
        with self.manager._run_lock:
            # 같은 execution_id의 재요청은 슬롯 경쟁이 아니라 중복 전달이다 ·
            # 그룹 명령은 여러 PC가 같은 트리거를 받으므로 재전송이 정상이다.
            if (
                self.manager._run_thread is not None
                and self.manager._run_thread.is_alive()
                and self.session.get('execution_id') == execution_id
            ):
                return {
                    'success': True,
                    'duplicate': True,
                    'message': '이미 준비 중인 그룹 실행 세션',
                    'status': self.manager.status(),
                }
            try:
                motors_snapshot = self.manager._claim_run_slot()
            except motion_run_rules.RunSlotUnavailable as exc:
                return {
                    'success': False,
                    'message': str(exc),
                    'status': self.manager.status(),
                }
            self.session = {
                'active': True,
                'execution_id': execution_id,
                'state': 'preparing',
                'cycle_number': 0,
                'next_start_at': 0.0,
                'next_cycle_number': 0,
                'next_initialize_at': 0.0,
                'next_initialize_cycle_number': 0,
                'stop_after_cycle': False,
            }
            status = motion_run_rules._empty_status()
            status.update({
                'state': 'preparing',
                'phase': 'group_preparing',
                'message': '그룹 실행 계획 생성 중',
                'group_execution': True,
                'execution_id': execution_id,
                'project_id': str(payload.get('project_id') or ''),
                'motion_file_id': str(payload.get('motion_file_id') or ''),
                'mapping_file_id': str(payload.get('mapping_file_id') or ''),
                'request_source': 'group_control',
                'phase_started_at': time.time(),
            })
            self.manager._set_status(status)
            self.manager._run_thread = threading.Thread(
                target=self._run,
                args=(dict(payload), list(motors_snapshot)),
                name=f'group-motion-{execution_id[:24]}',
                daemon=True,
            )
            self.manager._run_thread.start()
        return {
            'success': True,
            'message': '그룹 실행 준비 시작',
            'execution_id': execution_id,
            'status': self.manager.status(),
        }

    def schedule_initialization(
        self, payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        execution_id = str(payload.get('execution_id') or '').strip()
        initialize_monotonic = finite_float(payload.get('initialize_monotonic'))
        try:
            cycle_number = int(payload.get('cycle_number'))
        except (TypeError, ValueError) as exc:
            raise ValueError('그룹 회차 번호가 필요합니다') from exc
        if initialize_monotonic is None or initialize_monotonic <= time.monotonic():
            raise ValueError('그룹 회차 초기화 트리거가 이미 지났습니다')
        with self.condition:
            session = self.session
            if not session.get('active') or session.get('execution_id') != execution_id:
                raise ValueError('활성 그룹 실행 세션이 일치하지 않습니다')
            if session.get('state') not in {
                'motion_completed', 'cycle_initialize_scheduled',
            }:
                raise ValueError('그룹 회차 초기화를 예약할 수 있는 상태가 아닙니다')
            if cycle_number != int(session.get('cycle_number') or 0):
                raise ValueError('그룹 회차 초기화 번호가 일치하지 않습니다')
            existing_cycle = int(
                session.get('next_initialize_cycle_number') or 0
            )
            existing_at = float(session.get('next_initialize_at') or 0.0)
            if existing_cycle:
                if (
                    existing_cycle == cycle_number
                    and existing_at == float(initialize_monotonic)
                ):
                    return {
                        'success': True,
                        'duplicate': True,
                        'message': '이미 예약된 그룹 회차 초기화 명령',
                        'status': self.manager.status(),
                    }
                raise ValueError('다른 그룹 회차 초기화 명령이 이미 예약되었습니다')
            session.update({
                'next_initialize_cycle_number': cycle_number,
                'next_initialize_at': float(initialize_monotonic),
                'state': 'cycle_initialize_scheduled',
            })
            self.condition.notify_all()
        return {
            'success': True,
            'message': f'그룹 모션 {cycle_number}회차 후 초기화 예약',
            'execution_id': execution_id,
            'cycle_number': cycle_number,
            'initialize_monotonic': float(initialize_monotonic),
            'status': self.manager.status(),
        }

    def schedule_cycle(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        execution_id = str(payload.get('execution_id') or '').strip()
        start_monotonic = finite_float(payload.get('start_monotonic'))
        try:
            cycle_number = int(payload.get('cycle_number'))
        except (TypeError, ValueError) as exc:
            raise ValueError('그룹 회차 번호가 필요합니다') from exc
        if start_monotonic is None or start_monotonic <= time.monotonic():
            raise ValueError('그룹 모션 시작 트리거가 이미 지났습니다')
        with self.condition:
            session = self.session
            if not session.get('active') or session.get('execution_id') != execution_id:
                raise ValueError('활성 그룹 실행 세션이 일치하지 않습니다')
            if session.get('state') not in {'armed', 'cycle_ready', 'start_scheduled'}:
                raise ValueError('그룹 모션 시작을 예약할 수 있는 상태가 아닙니다')
            expected = int(session.get('cycle_number') or 0) + 1
            if cycle_number != expected:
                raise ValueError(f'그룹 모션 회차가 일치하지 않습니다: 예상 {expected}')
            existing_cycle = int(session.get('next_cycle_number') or 0)
            existing_start = float(session.get('next_start_at') or 0.0)
            if existing_cycle:
                if existing_cycle == cycle_number and existing_start == float(start_monotonic):
                    return {
                        'success': True,
                        'duplicate': True,
                        'message': '이미 예약된 그룹 모션 시작 명령',
                        'status': self.manager.status(),
                    }
                raise ValueError('다른 그룹 모션 시작 명령이 이미 예약되었습니다')
            session.update({
                'next_cycle_number': cycle_number,
                'next_start_at': float(start_monotonic),
                'state': 'start_scheduled',
            })
            self.condition.notify_all()
        return {
            'success': True,
            'message': f'그룹 모션 {cycle_number}회차 시작 예약',
            'execution_id': execution_id,
            'cycle_number': cycle_number,
            'start_monotonic': float(start_monotonic),
            'status': self.manager.status(),
        }

    def cancel(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        execution_id = str(payload.get('execution_id') or '').strip()
        with self.condition:
            if execution_id and self.session.get('execution_id') != execution_id:
                raise ValueError('취소하려는 그룹 실행 세션이 일치하지 않습니다')
            self.manager._stop_event.set()
            self.session.update({'active': False, 'state': 'stopped'})
            self.condition.notify_all()
            worker = getattr(self.manager, '_run_thread', None)
        # Do not report the group session as released until its worker has
        # observed cancellation. Starting another group session earlier races
        # the shared stop event and causes "previous motion run task" errors.
        if (
            worker is not None and worker.is_alive()
            and worker is not threading.current_thread()
        ):
            worker.join(timeout=5.0)
        if worker is not None and worker.is_alive():
            return {
                'success': False,
                'message': '이전 그룹 실행 정리 중입니다. 잠시 후 다시 시도하세요',
                'status': self.manager.status(),
            }
        self.manager._update_status({
            'state': 'stopped',
            'phase': 'stopped',
            'message': '그룹 실행 취소',
            'phase_finished_at': time.time(),
        })
        return {'success': True, 'message': '그룹 실행 취소', 'status': self.manager.status()}

    def _run(
        self, payload: Dict[str, Any], motors_snapshot: List[Dict[str, Any]]
    ) -> None:
        execution_id = str(payload.get('execution_id') or '')
        try:
            validation_payload = {
                **payload,
                # A direct group repeat has the same end-to-start continuity
                # requirement as a local continuous run. Validate it before
                # the per-cycle plan is deliberately converted to one-shot.
                'run_mode': (
                    'continuous'
                    if (
                        not payload.get('initialization_only')
                        and str(payload.get('run_mode') or 'once') == 'continuous'
                        and str(payload.get('repeat_mode') or 'direct')
                        in {'direct', 'dwell'}
                    ) else 'once'
                ),
            }
            validation_plan = self.manager._plan_builder.build(
                validation_payload, motors_snapshot=motors_snapshot,
            )
            guard_error = motion_run_rules._motion_auto_start_guard_error(validation_plan)
            if guard_error:
                raise ValueError(guard_error)
            motion_payload = {
                **payload,
                'run_mode': 'once',
                'automation_run': False,
                'group_execution': True,
                'request_source': 'group_control',
                'scheduled_start_at': 0.0,
                'synchronized_cycle_sec': 0.0,
                'synchronized_repeat_count': 0,
            }
            plan = self.manager._plan_builder.build(motion_payload, motors_snapshot=motors_snapshot)
            initialization_plan = self.manager._plan_builder.build(
                motion_payload,
                initialization_only=True,
                motors_snapshot=motors_snapshot,
            )
            if self.manager._stop_event.is_set():
                return
            self.manager._wait_group_deadline(
                float(payload['initialize_monotonic']),
                phase='group_initialize_scheduled',
                message='그룹 초기 위치 이동 시작 대기',
                execution_id=execution_id,
            )
            initialize_triggered_at = time.time()
            initialize_triggered_monotonic = time.monotonic()
            self.manager._player._run_initialization(initialization_plan)
            if self.manager._stop_event.is_set() or self.manager.status().get('state') != 'initialized':
                return
            with self.condition:
                self.session.update({
                    'state': 'armed',
                    'initialize_triggered_at': initialize_triggered_at,
                    'initialize_triggered_monotonic': (
                        initialize_triggered_monotonic
                    ),
                })
            self.manager._update_status({
                'state': 'armed',
                'phase': 'group_armed',
                'message': '그룹 초기 위치 이동 완료 · 모션 시작 대기',
                'group_execution': True,
                'execution_id': execution_id,
                'initialize_triggered_at': initialize_triggered_at,
                'initialize_triggered_monotonic': (
                    initialize_triggered_monotonic
                ),
            })
            while not self.manager._stop_event.is_set():
                scheduled = self._wait_cycle(execution_id)
                if scheduled is None:
                    break
                cycle_number, start_at = scheduled
                self.manager._wait_group_deadline(
                    start_at,
                    phase='group_start_scheduled',
                    message=f'그룹 모션 {cycle_number}회차 시작 대기',
                    execution_id=execution_id,
                    cycle_number=cycle_number,
                )
                if self.manager._stop_event.is_set():
                    break
                plan['scheduled_start_at'] = 0.0
                plan['group_execution'] = True
                plan['execution_id'] = execution_id
                plan['group_cycle_number'] = cycle_number
                self.manager._player._run_motion(plan)
                result = self.manager.status()
                if result.get('state') == 'error' or self.manager._stop_event.is_set():
                    break
                triggered_at = float(
                    (result.get('lifecycle') or {}).get('motion_started_at') or 0.0
                )
                self.manager._update_status({
                    'state': 'motion_completed',
                    'phase': 'group_motion_completed',
                    'message': f'그룹 모션 {cycle_number}회차 완료',
                    'group_execution': True,
                    'execution_id': execution_id,
                    'cycle_count': cycle_number,
                    'current_cycle': cycle_number,
                    'group_cycle_number': cycle_number,
                    'cycle_triggered_at': triggered_at,
                })
                with self.condition:
                    self.session.update({
                        'state': 'motion_completed',
                        'cycle_number': cycle_number,
                    })
                    stop_after_cycle = bool(self.session.get('stop_after_cycle'))
                if stop_after_cycle:
                    self._finish('현재 그룹 모션 회차 완료 후 정지')
                    return
                scheduled_initialize = self._wait_initialization(
                    execution_id,
                )
                if scheduled_initialize is None:
                    break
                initialized_cycle, initialize_at = scheduled_initialize
                initialization_plan = {
                    **initialization_plan,
                    'group_execution': True,
                    'execution_id': execution_id,
                    'group_cycle_number': initialized_cycle,
                }
                self.manager._wait_group_deadline(
                    initialize_at,
                    phase='group_cycle_initialize_scheduled',
                    message=f'그룹 모션 {initialized_cycle}회차 후 초기화 대기',
                    execution_id=execution_id,
                    cycle_number=initialized_cycle,
                )
                if self.manager._stop_event.is_set():
                    break
                self.manager._player._run_initialization(initialization_plan)
                if self.manager._stop_event.is_set() or self.manager.status().get('state') != 'initialized':
                    break
                with self.condition:
                    self.session.update({
                        'state': 'cycle_ready',
                        'cycle_number': cycle_number,
                        'next_start_at': 0.0,
                        'next_cycle_number': 0,
                    })
                self.manager._update_status({
                    'state': 'cycle_ready',
                    'phase': 'group_cycle_initialized',
                    'message': f'그룹 모션 {cycle_number}회차 후 초기화 완료',
                    'group_execution': True,
                    'execution_id': execution_id,
                    'cycle_count': cycle_number,
                    'current_cycle': cycle_number,
                    'group_cycle_number': cycle_number,
                })
        except InterruptedError:
            pass
        except Exception as exc:
            if not self.manager._stop_event.is_set():
                self.manager.get_logger().error(
                    f'group motion execution failed\n{traceback.format_exc()}'
                )
                self.manager._update_status({
                    'state': 'error',
                    'phase': 'error',
                    'message': f'그룹 모션 실행 실패: {exc}',
                    'group_execution': True,
                    'execution_id': execution_id,
                    'phase_finished_at': time.time(),
                })
        finally:
            with self.condition:
                if self.session.get('execution_id') == execution_id:
                    self.session['active'] = False
                    if self.session.get('state') not in {'error', 'stopped'}:
                        self.session['state'] = str(self.manager.status().get('state') or 'stopped')
                self.condition.notify_all()
            if (
                self.manager._stop_event.is_set()
                and self.manager.status().get('state') not in {'stopped', 'error'}
            ):
                self.manager._update_status({
                    'state': 'stopped',
                    'phase': 'stopped',
                    'message': '그룹 실행 정지',
                    'phase_finished_at': time.time(),
                })

    def _wait_initialization(
        self, execution_id: str,
    ) -> Optional[tuple[int, float]]:
        with self.condition:
            while not self.manager._stop_event.is_set():
                if self.session.get('execution_id') != execution_id:
                    return None
                cycle = int(
                    self.session.get('next_initialize_cycle_number') or 0
                )
                initialize_at = float(
                    self.session.get('next_initialize_at') or 0.0
                )
                if cycle > 0 and initialize_at > 0.0:
                    self.session['state'] = 'cycle_initialize_scheduled'
                    self.session['next_initialize_cycle_number'] = 0
                    self.session['next_initialize_at'] = 0.0
                    return cycle, initialize_at
                self.condition.wait(timeout=0.2)
        return None

    def _wait_cycle(self, execution_id: str) -> Optional[tuple[int, float]]:
        with self.condition:
            while not self.manager._stop_event.is_set():
                if self.session.get('execution_id') != execution_id:
                    return None
                cycle = int(self.session.get('next_cycle_number') or 0)
                start_at = float(self.session.get('next_start_at') or 0.0)
                if cycle > 0 and start_at > 0.0:
                    self.session['state'] = 'start_scheduled'
                    return cycle, start_at
                self.condition.wait(timeout=0.2)
        return None

    def _finish(self, message: str) -> None:
        with self.condition:
            self.manager._stop_event.set()
            self.session.update({'active': False, 'state': 'stopped'})
            self.condition.notify_all()
            worker = getattr(self.manager, '_run_thread', None)
        if (
            worker is not None and worker.is_alive()
            and worker is not threading.current_thread()
        ):
            worker.join(timeout=5.0)
        self.manager._update_status({
            'state': 'stopped',
            'phase': 'stopped',
            'message': message,
            'phase_finished_at': time.time(),
        })
