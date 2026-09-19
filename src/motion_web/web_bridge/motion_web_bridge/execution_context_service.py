"""실행 컨텍스트 조율 · 프로젝트 설정을 각 노드에 적용하고 상태를 지킨다.

`MotionWebBridge`에서 떼어냈다 · §5 분해 목표안의 `ExecutionContextService` · §6-20

이 객체가 갖는 것 · 컨텍스트 상태와 그 락 · 적용 직렬화 락.
세대 번호(`_project_generation`)는 노드에 남겼다 · 25곳이 쓰는 노드 전역 개념이고
실행 컨텍스트만의 것이 아니다.
"""

from __future__ import annotations

import contextlib
import copy
import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterator

from motion_web_bridge import motor_config_rules


#: 적용할 때만 MIDI 는 다른 말을 알아듣는다 · 나머지는 `apply_context` · §6-185
APPLY_COMMANDS = {'midi_control': 'select_project'}

#: 확인은 세 노드만 받는다 · `motion_mapping` 은 적용 응답으로 끝난다 · §6-185
#:
#: 되묻지 않는 이유 · 매핑 노드는 적용 시점에 파일을 이미 다 읽었다 ·
#: 나머지 셋은 적용 뒤에도 준비가 더 필요해서 한 번 더 묻는다.
CONFIRM_NODES = ('midi_control', 'motion_run', 'motion_studio')


class ExecutionContextService:
    def __init__(
        self,
        bridge: Any,
        *,
        project: Any,
        repository: Any,
        workspace_root: Path,
    ) -> None:
        self.bridge = bridge
        #: 프로젝트 서비스 협력자 (§6-23)
        self.project = project
        self.repository = repository
        self.workspace_root = workspace_root
        self._lock = threading.RLock()
        #: 적용을 한 번에 하나만 돌린다
        self._apply_lock = threading.Lock()
        self._status: Dict[str, Any] = {
            'state': 'starting',
            'ready': False,
            'message': '현재 프로젝트 실행 컨텍스트 확인 중',
            'context_id': '',
            'project_id': '',
            'nodes': {},
            'updated_at': time.time(),
        }

    def status(self, *, validate_files: bool = True) -> Dict[str, Any]:
        with self._lock:
            status = copy.deepcopy(self._status)
        project_id = self.repository.selected_project_id()
        if validate_files and project_id and status.get('ready'):
            try:
                current = self.repository.execution_context(project_id)
            except (OSError, ValueError, json.JSONDecodeError):
                current = {}
            if current.get('context_id') != status.get('context_id'):
                status.update({
                    'state': 'stale',
                    'ready': False,
                    'message': '저장 설정이 변경되어 실행 컨텍스트 재적용 대기 중',
                    'stored_context_id': current.get('context_id', ''),
                })
        runtime_blocker = (
            self.bridge._motor_runtime_control_blocker()
            if status.get('ready')
            else ''
        )
        status['control_allowed'] = bool(status.get('ready') and not runtime_blocker)
        status['control_block_reason'] = runtime_blocker
        status['stored_equals_runtime'] = bool(status.get('ready'))
        return status

    def _set_status(self, **values: Any) -> None:
        with self._lock:
            self._status.update(values)
            self._status['updated_at'] = time.time()

    @contextlib.contextmanager
    def paused_for_project_change(self) -> Iterator[None]:
        """프로젝트가 바뀌는 동안 적용을 멈춘다 · §6-183

        **자물쇠는 주인이 연다.**

        전에는 `project_service` 가 `bridge._execution_context._apply_lock`
        를 직접 잡았다 놓았다 · 남의 객체의, 그것도 밑줄 붙은 자물쇠를 두 겹
        건너 잡는 것이라 세 자리에서 `acquire` / `try` / `finally` 를 손으로
        적어야 했다 · 한 곳에서 `finally` 를 빠뜨리면 실행 컨텍스트가 영영
        잠긴다 (`reconcile` 이 조용히 건너뛴다).

        이 자물쇠의 뜻은 「지금 바뀌는 중이니 재조정하지 마라」다 · 그 뜻을
        아는 것은 이 객체뿐이므로, 멈추는 방법도 여기서 내준다.
        """
        self._apply_lock.acquire()
        try:
            yield
        finally:
            self._apply_lock.release()

    def mark_project_selected(self, project_id: str) -> None:
        """골랐지만 아직 적용 전이다 · §6-183

        `_set_status` 는 이 객체의 속살이다 · 무엇을 「선택 완료」 상태라
        부를지는 여기서 정한다 · 부르는 쪽이 여섯 칸을 직접 채우면, 칸이
        하나 늘 때 부르는 쪽이 깨진다.
        """
        self._set_status(
            state='selected', ready=False, project_id=str(project_id), context_id='',
            message='프로젝트 선택 완료 · 실행 컨텍스트 적용 대기 중', nodes={},
        )

    def context_id(self) -> str:
        status = self.status()
        return str(status.get('context_id') or '') if status.get('ready') else ''

    def invalidate_nodes(self, context_id: str = '') -> None:
        payload = {'context_id': context_id}
        # A forced boundary also stops any command that belonged to the
        # invalidated context, even when the numeric generation is unchanged.
        self.bridge._establish_project_generation_boundary(force=True)
        for send in self.bridge.managed_context_nodes().values():
            send('invalidate_context', payload, timeout_sec=0.5)
        self.project.clear_scoped_memory()

    def _ack_matches(
        self, result: Dict[str, Any], context_id: str, project_id: str
    ) -> bool:
        """Accept the common acknowledgement fields, including UI snapshots.

        MIDI status snapshots historically expose the context as a nested
        object, while the other managed nodes return it at the top level.
        The coordinator must validate the values, not mistake that harmless
        response-shape difference for a failed project application.
        """
        nested = result.get('execution_context')
        if not isinstance(nested, dict):
            nested = {}
        status = result.get('status')
        status_context = (
            status.get('execution_context')
            if isinstance(status, dict) else {}
        )
        if not isinstance(status_context, dict):
            status_context = {}
        acknowledged_context = str(
            result.get('context_id')
            or nested.get('context_id')
            or status_context.get('context_id')
            or ''
        )
        acknowledged_project = str(
            result.get('project_id')
            or nested.get('project_id')
            or status_context.get('project_id')
            or ''
        )
        acknowledged_generation = result.get('project_generation')
        if acknowledged_generation is None:
            acknowledged_generation = nested.get('project_generation')
        if acknowledged_generation is None:
            acknowledged_generation = status_context.get('project_generation')
        try:
            generation_matches = (
                int(acknowledged_generation) == self.bridge.current_project_generation()
            )
        except (TypeError, ValueError):
            generation_matches = False
        return (
            result.get('success') is True
            and acknowledged_context == context_id
            and acknowledged_project == project_id
            and generation_matches
        )

    def schedule_reconcile(self) -> None:
        """Run orchestration outside the single ROS callback thread.

        Response subscriptions must remain free while the coordinator waits
        for acknowledgements from the managed nodes.
        """
        if self._apply_lock.locked():
            return
        threading.Thread(
            target=self.reconcile,
            name='project-context-coordinator',
            daemon=True,
        ).start()

    def reconcile(self) -> Dict[str, Any]:
        if not self._apply_lock.acquire(blocking=False):
            return self.status()
        try:
            project_id = self.repository.selected_project_id()
            if not project_id:
                self._set_status(
                    state='no_project', ready=False, project_id='', context_id='',
                    message='현재 프로젝트를 선택하세요', nodes={},
                )
                return self.status()
            try:
                context = self.repository.execution_context(project_id)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                self._set_status(
                    state='error', ready=False, project_id=project_id, context_id='',
                    message=f'프로젝트 실행 컨텍스트 생성 실패: {exc}', nodes={},
                )
                return self.status()

            context_id = str(context.get('context_id') or '')
            with self._lock:
                previous = dict(self._status)
            try:
                self.bridge._establish_project_generation_boundary()
            except ValueError as exc:
                self._set_status(
                    state='waiting_motor_runtime', ready=False,
                    project_id=project_id, context_id=context_id,
                    message=str(exc), nodes={},
                    failures={'motor_runtime': str(exc)}, context=context,
                )
                return self.status()
            if context.get('missing'):
                if previous.get('state') != 'configuration_required' or previous.get('context_id') != context_id:
                    self.invalidate_nodes(context_id)
                self._set_status(
                    state='configuration_required', ready=False,
                    project_id=project_id, context_id=context_id,
                    message='모터축 설정과 모션축 설정 파일을 확정하세요',
                    missing=list(context.get('missing') or []), nodes={}, context=context,
                )
                return self.status()
            mapping = context['files']['motion_axis_matching']
            if (
                previous.get('state') == 'motor_apply_required'
                and previous.get('context_id') == context_id
                and time.time() - float(previous.get('updated_at') or 0.0) < 5.0
            ):
                return self.status()
            payload = {
                'context_id': context_id,
                'project_generation': self.bridge.current_project_generation(),
                'mapping_file_id': mapping['name'],
                'mapping_sha256': mapping['sha256'],
            }
            if previous.get('ready') and previous.get('context_id') == context_id:
                # A ready context is immutable: its id already includes the
                # selected project's configuration file hashes. Re-sending
                # apply_context as a periodic health check is unsafe because
                # motion_run intentionally rejects configuration changes while
                # initialization/playback is active. Treating that rejection as
                # a node failure used to invalidate MIDI, motion_run and studio
                # in the middle of recording. A changed file produces a new
                # context_id and naturally takes the normal apply path below.
                return self.status()
            if not previous.get('ready') or previous.get('context_id') != context_id:
                self._set_status(
                    state='applying', ready=False, project_id=project_id,
                    context_id=context_id, message='프로젝트 설정을 각 노드에 적용 중',
                    context=context,
                )
            nodes = {
                name: send(
                    APPLY_COMMANDS.get(name, 'apply_context'), payload, timeout_sec=2.0
                )
                for name, send in self.bridge.managed_context_nodes().items()
            }
            failed = {
                name: str(result.get('message') or '응답 없음')
                for name, result in nodes.items()
                if not self._ack_matches(
                    result, context_id, project_id
                )
            }
            if failed:
                self.invalidate_nodes(context_id)
                self._set_status(
                    state='waiting_nodes', ready=False, nodes=nodes,
                    message='필수 노드의 프로젝트 설정 적용 응답 대기 중',
                    failures=failed,
                )
                return self.status()

            if not context.get('motor_applied'):
                # The project files were accepted by every consumer above.
                # Keep that project-scoped mapping and MIDI bank loaded while
                # motor control remains blocked.  Invalidating here used to
                # erase the MIDI node's project_id and restore its default
                # Bank 1 once per reconciliation cycle, even though the saved
                # project data itself was valid.
                self._set_status(
                    state='motor_apply_required', ready=False,
                    project_id=project_id, context_id=context_id,
                    message='프로젝트 파일은 각 노드에 전달됐지만 모터축 장비에 적용 · 모터 재시작이 필요합니다',
                    nodes=nodes, failures={}, context=context,
                )
                return self.status()

            motion_state = self.bridge.motion_state()
            motor_runtime = motor_config_rules.runtime_service_status(
                motion_state,
                applied_motor_config_file=getattr(getattr(self.bridge, '_motor_config', None), 'applied', None),
                repository=getattr(self, 'repository', None),
                workspace_root=getattr(self, 'workspace_root', Path()),
            )
            motor_runtime.update({
                'success': (
                    self.project.runtime_project_id() == project_id
                    and motor_runtime.get('phase') == 'ready'
                ),
                'project_id': self.project.runtime_project_id(),
                'context_id': context_id,
            })
            nodes['motor_runtime'] = motor_runtime
            if not motor_runtime['success']:
                self.invalidate_nodes(context_id)
                self._set_status(
                    state='waiting_motor_runtime', ready=False, nodes=nodes,
                    message='현재 프로젝트의 모터 관리 노드 상태 확인 대기 중',
                    failures={'motor_runtime': str(motor_runtime.get('message') or '')},
                )
                return self.status()

            confirmations = {
                name: send('confirm_context', payload, timeout_sec=2.0)
                for name, send in self.bridge.managed_context_nodes().items()
                if name in CONFIRM_NODES
            }
            confirm_failed = {
                name: str(result.get('message') or '응답 없음')
                for name, result in confirmations.items()
                if not self._ack_matches(
                    result, context_id, project_id
                )
            }
            nodes.update({f'{name}_confirm': value for name, value in confirmations.items()})
            if confirm_failed:
                self.invalidate_nodes(context_id)
                self._set_status(
                    state='waiting_nodes', ready=False, nodes=nodes,
                    message='필수 노드의 제어 허용 확인 대기 중',
                    failures=confirm_failed,
                )
                return self.status()
            self._set_status(
                state='ready', ready=True, nodes=nodes, failures={},
                message='저장 설정과 실행 설정이 일치합니다 · 사용자 제어 가능',
                verified_at=time.time(),
            )
            return self.status()
        finally:
            self._apply_lock.release()

    def reconcile_blocking(
        self, *, timeout_sec: float = 10.0, poll_interval: float = 0.1,
    ) -> Dict[str, Any]:
        """Wait until project execution context is ready or a terminal state is reached."""
        terminal_states = {
            'ready', 'error', 'configuration_required', 'no_project',
        }
        deadline = time.monotonic() + max(float(timeout_sec), 0.0)
        last_status = self.status()
        while time.monotonic() < deadline:
            if self._apply_lock.locked():
                time.sleep(min(poll_interval, 0.05))
                continue
            last_status = self.reconcile()
            state = str(last_status.get('state') or '')
            if state in terminal_states:
                return last_status
            time.sleep(poll_interval)
        return last_status
