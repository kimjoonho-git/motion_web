"""ROS node and HTTP transport for authenticated PC status sharing."""

import asyncio
import json
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Dict, Mapping

import rclpy
import uvicorn
import yaml
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from rclpy.node import Node
from rclpy.executors import ExternalShutdownException
from std_msgs.msg import String

from .configuration import ConfigurationError, load_config
from .execution_control import (
    ExecutionLease, OperationJournal, build_synchronized_schedule,
    validate_control_payload,
)
from .runtime import (
    CONTROL_PATH, CoordinationRuntime, READINESS_PATH, STATUS_PATH,
)
from .security import (
    AuthenticationError,
    ReplayError,
    peer_secrets_from_config,
)
from .status_adapter import adapt_readiness_result
from .time_sync import inspect_time_sync


MAX_STATUS_BODY_BYTES = 64 * 1024


def create_app(
    runtime: CoordinationRuntime,
    readiness_checker: Callable[[], Mapping[str, object]] | None = None,
    control_handler: Callable[[Mapping[str, object]], Mapping[str, object]] | None = None,
) -> FastAPI:
    """Create the isolated 8010 status-sharing application."""
    app = FastAPI(title='Motion Coordination', docs_url=None, redoc_url=None)

    @app.post(STATUS_PATH)
    async def receive_status(request: Request):
        remote_ip = request.client.host if request.client else ''
        body = await request.body()
        if len(body) > MAX_STATUS_BODY_BYTES:
            return JSONResponse({'error': 'message_too_large'}, status_code=413)
        try:
            signed = runtime.accept_status_request(
                body=body,
                headers=request.headers,
                remote_ip=remote_ip,
            )
        except ReplayError:
            return JSONResponse({'error': 'replayed_request'}, status_code=409)
        except AuthenticationError:
            return JSONResponse({'error': 'authentication_failed'}, status_code=401)
        except ValueError:
            return JSONResponse({'error': 'request_rejected'}, status_code=403)
        return Response(
            content=signed.body,
            headers=signed.headers,
            media_type='application/json',
        )

    @app.post(READINESS_PATH)
    async def receive_readiness(request: Request):
        remote_ip = request.client.host if request.client else ''
        body = await request.body()
        if len(body) > MAX_STATUS_BODY_BYTES:
            return JSONResponse({'error': 'message_too_large'}, status_code=413)
        try:
            accepted = runtime.accept_readiness_request(
                body=body,
                headers=request.headers,
                remote_ip=remote_ip,
            )
            local_result = (
                await asyncio.to_thread(readiness_checker)
                if readiness_checker is not None else {}
            )
            readiness = {
                **adapt_readiness_result(local_result),
                **inspect_time_sync(),
            }
            signed = runtime.build_readiness_response(
                accepted['machine_id'],
                accepted['request_sequence'],
                accepted['network_operation_id'],
                readiness,
            )
        except ReplayError:
            return JSONResponse({'error': 'replayed_request'}, status_code=409)
        except AuthenticationError:
            return JSONResponse({'error': 'authentication_failed'}, status_code=401)
        except ValueError:
            return JSONResponse({'error': 'request_rejected'}, status_code=403)
        return Response(
            content=signed.body,
            headers=signed.headers,
            media_type='application/json',
        )

    @app.post(CONTROL_PATH)
    async def receive_control(request: Request):
        remote_ip = request.client.host if request.client else ''
        body = await request.body()
        if len(body) > MAX_STATUS_BODY_BYTES:
            return JSONResponse({'error': 'message_too_large'}, status_code=413)
        try:
            accepted = runtime.accept_control_request(
                body=body, headers=request.headers, remote_ip=remote_ip
            )
            result = await asyncio.to_thread(
                control_handler, accepted
            ) if control_handler is not None else {
                'success': False, 'message': 'local control handler unavailable'
            }
            signed = runtime.build_control_response(
                accepted['machine_id'], accepted['request_sequence'],
                accepted['payload']['network_operation_id'], result,
            )
        except ReplayError:
            return JSONResponse({'error': 'replayed_request'}, status_code=409)
        except AuthenticationError:
            return JSONResponse({'error': 'authentication_failed'}, status_code=401)
        except ValueError:
            return JSONResponse({'error': 'request_rejected'}, status_code=403)
        return Response(
            content=signed.body, headers=signed.headers,
            media_type='application/json',
        )

    return app


class MotionCoordinationNode(Node):
    """Publish local coordination state and exchange signed peer heartbeats."""

    def __init__(self) -> None:
        super().__init__('motion_coordination_node')
        workspace = Path(os.environ.get('MOTION_WORKSPACE') or Path.cwd()).resolve()
        config_path = Path(
            os.environ.get('MOTION_COORDINATION_CONFIG')
            or workspace / 'config/motion_coordination.yaml'
        )
        self._config = load_config(config_path, workspace=workspace)
        secrets = self._load_secrets()
        self._runtime = CoordinationRuntime(self._config, secrets)
        self._execution_lease = ExecutionLease()
        self._operation_journal = OperationJournal(
            workspace / 'runtime/motion_coordination/operations.json'
        )
        self._coordinator_lease_id = ''
        self._last_execution_control_state = 'local'
        self._synchronized_operation_active = False
        self._synchronized_seen_running = False
        self._sync_finalize_lock = threading.Lock()
        self._publisher = self.create_publisher(
            String,
            '/motion_coordination/status',
            10,
        )
        self._response_publisher = self.create_publisher(
            String,
            '/motion_coordination/response',
            10,
        )
        self._request_subscription = self.create_subscription(
            String,
            '/motion_coordination/request',
            self._request_callback,
            10,
        )
        self._send_lock = threading.Lock()
        self._readiness_lock = threading.Lock()
        self._server = None
        self._server_thread = None
        self._timer = self.create_timer(
            self._config.heartbeat_sec,
            self._heartbeat,
        )
        if (
            self._config.mode != 'off'
            and self._config.access.coordination_enabled
        ):
            self._start_server()
        self.get_logger().info(
            'Motion coordination initialized · '
            f'mode={self._config.mode} · role={self._config.role}'
        )

    def destroy_node(self):
        if self._server is not None:
            self._server.should_exit = True
        if self._server_thread is not None:
            self._server_thread.join(timeout=3.0)
        return super().destroy_node()

    def _load_secrets(self) -> Dict[str, bytes]:
        if self._config.mode == 'off':
            return {}
        path = self._config.credential_file
        try:
            value = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise ConfigurationError(f'연동 자격증명을 읽을 수 없습니다: {exc}') from exc
        secrets = peer_secrets_from_config(value)
        required = {peer.machine_id for peer in self._config.peers}
        missing = sorted(required.difference(secrets))
        if missing:
            raise ConfigurationError(f'peer HMAC 키가 없습니다: {", ".join(missing)}')
        return secrets

    def _start_server(self) -> None:
        config = uvicorn.Config(
            create_app(
                self._runtime, self._check_local_readiness,
                self._handle_inbound_control,
            ),
            host=self._config.access.coordination_host,
            port=self._config.access.coordination_port,
            log_level='warning',
            access_log=False,
        )
        self._server = uvicorn.Server(config)
        self._server_thread = threading.Thread(
            target=self._server.run,
            name='motion-coordination-http',
            daemon=True,
        )
        self._server_thread.start()

    def _heartbeat(self) -> None:
        self._refresh_local_status()
        self._maintain_execution_lease()
        self._publish_snapshot()
        if self._config.mode == 'off' or not self._config.peers:
            return
        if not self._send_lock.acquire(blocking=False):
            return
        threading.Thread(
            target=self._send_all,
            name='motion-coordination-send',
            daemon=True,
        ).start()

    def _maintain_execution_lease(self) -> None:
        control = self._execution_lease.snapshot()
        if control.get('state') == 'network':
            coordinator = self._runtime.snapshot().get('coordinator', {})
            if coordinator.get('authority_allowed'):
                try:
                    self._execution_lease.renew(
                        str(control.get('owner') or ''),
                        str(control.get('lease_id') or ''),
                    )
                except ValueError:
                    pass
        current = self._execution_lease.snapshot().get('state', 'local')
        if self._last_execution_control_state == 'network' and current == 'local':
            self._call_local_control({
                'network_operation_id': f'lease-expired-{uuid.uuid4().hex}',
                'command': 'stop_after_cycle', 'lease_id': 'expired',
            })
        self._last_execution_control_state = current
        self._maybe_finalize_synchronized_run()

    def _maybe_finalize_synchronized_run(self) -> None:
        if self._config.role != 'coordinator' or not self._synchronized_operation_active:
            return
        snapshot = self._runtime.snapshot()
        states = [str(snapshot.get('local', {}).get('motion', {}).get('state') or 'unknown')]
        peers = snapshot.get('peers', [])
        states.extend(
            str(record.get('payload', {}).get('motion', {}).get('state') or 'unknown')
            for record in peers
        )
        active_states = {'initializing', 'waiting_start', 'running', 'waiting'}
        if any(state in active_states for state in states):
            self._synchronized_seen_running = True
            return
        if not self._synchronized_seen_running:
            return
        if len(peers) != len(self._config.peers):
            states.append('error')
        if not all(state in {'completed', 'error'} for state in states):
            return
        if not self._sync_finalize_lock.acquire(blocking=False):
            return
        threading.Thread(
            target=self._finalize_synchronized_run,
            args=('error' in states,),
            name='motion-coordination-sync-finalize', daemon=True,
        ).start()

    def _finalize_synchronized_run(self, failed: bool) -> None:
        try:
            lease_id = self._coordinator_lease_id
            if failed:
                self._broadcast_control({
                    'network_operation_id': f'sync-stop-{uuid.uuid4().hex}',
                    'command': 'stop_now',
                })
            if lease_id:
                self._broadcast_control({
                    'network_operation_id': f'sync-release-{uuid.uuid4().hex}',
                    'command': 'release_control', 'lease_id': lease_id,
                })
            self._coordinator_lease_id = ''
            self._synchronized_operation_active = False
            self._synchronized_seen_running = False
        finally:
            self._sync_finalize_lock.release()

    def _refresh_local_status(self) -> None:
        url = f'http://127.0.0.1:{self._config.access.web_port}/api/status'
        try:
            with urllib.request.urlopen(url, timeout=0.8) as response:
                payload = json.loads(response.read(MAX_STATUS_BODY_BYTES).decode('utf-8'))
            if not isinstance(payload, dict):
                payload = {}
        except (OSError, ValueError, urllib.error.URLError):
            payload = {}
        self._runtime.update_local_status(payload)

    def _publish_snapshot(self) -> None:
        snapshot = self._runtime.snapshot()
        snapshot['execution_control'] = self._execution_lease.snapshot()
        snapshot['synchronized_operation_active'] = (
            self._synchronized_operation_active
        )
        snapshot['coordinator_lease_id'] = (
            self._coordinator_lease_id
            if self._config.role == 'coordinator' else ''
        )
        self._publisher.publish(String(data=json.dumps(
            snapshot,
            ensure_ascii=False,
            separators=(',', ':'),
        )))

    def _send_all(self) -> None:
        try:
            with ThreadPoolExecutor(max_workers=min(16, len(self._config.peers) or 1)) as pool:
                futures = [
                    pool.submit(self._send_peer, peer.machine_id, peer.url)
                    for peer in self._config.peers
                ]
                for future in as_completed(futures):
                    future.result()
        finally:
            self._send_lock.release()

    def _send_peer(self, peer_id: str, peer_url: str) -> None:
        sequence, signed = self._runtime.build_status_request(peer_id)
        request = urllib.request.Request(
            f'{peer_url}{STATUS_PATH}',
            data=signed.body,
            headers={**signed.headers, 'Content-Type': 'application/json'},
            method='POST',
        )
        try:
            with urllib.request.urlopen(request, timeout=1.0) as response:
                body = response.read(MAX_STATUS_BODY_BYTES)
                headers = dict(response.headers.items())
            self._runtime.verify_status_response(peer_id, sequence, body, headers)
        except (OSError, ValueError, urllib.error.URLError):
            return

    def _request_callback(self, message: String) -> None:
        try:
            request = json.loads(message.data)
        except (TypeError, ValueError):
            return
        if not isinstance(request, dict):
            return
        request_id = str(request.get('request_id') or '').strip()
        command = str(request.get('command') or '').strip()
        if not request_id or command not in {'check_readiness', 'control'}:
            return
        if not self._readiness_lock.acquire(blocking=False):
            self._publish_response({
                'request_id': request_id,
                'success': False,
                'message': '다른 전체 준비 확인이 진행 중입니다',
                'results': [],
            })
            return
        threading.Thread(
            target=(self._run_readiness if command == 'check_readiness' else self._run_control),
            args=(
                (request_id, request.get('network_operation_id'))
                if command == 'check_readiness'
                else (request_id, request.get('payload'))
            ),
            name=f'motion-coordination-{command}',
            daemon=True,
        ).start()

    def _run_readiness(self, request_id: str, operation_id: object) -> None:
        try:
            clean_operation_id = self._runtime.begin_readiness_operation(
                str(operation_id or '')
            )
            results = [{
                'machine_id': self._config.machine_id,
                'display_name': self._config.display_name,
                **self._runtime.attach_local_readiness_session(
                    {
                        **adapt_readiness_result(self._check_local_readiness()),
                        **inspect_time_sync(),
                    }
                ),
            }]
            live = {
                item['machine_id']: item
                for item in self._runtime.snapshot().get('peers', [])
            }
            pending = []
            for peer in self._config.peers:
                record = live.get(peer.machine_id)
                coordination = (
                    record.get('payload', {}).get('coordination', {})
                    if isinstance(record, dict) else {}
                )
                if coordination.get('mode') != 'participant':
                    results.append({
                        'machine_id': peer.machine_id,
                        'display_name': str(
                            (record or {}).get('payload', {}).get('display_name')
                            or peer.machine_id
                        ),
                        'readiness_version': 1,
                        'state': 'unavailable',
                        'reason_code': 'not_participant',
                        'message': '연동 참여 상태가 아닙니다',
                    })
                    continue
                pending.append((peer, record))
            with ThreadPoolExecutor(max_workers=min(16, len(pending) or 1)) as pool:
                futures = [pool.submit(
                    self._request_peer_readiness, peer.machine_id, peer.url,
                    clean_operation_id, record,
                ) for peer, record in pending]
                for future in as_completed(futures):
                    results.append(future.result())
            ready = bool(results) and all(
                item.get('state') == 'ready' for item in results
            )
            self._publish_response({
                'request_id': request_id,
                'success': ready,
                'message': '전체 실행 준비 완료' if ready else '실행 준비 확인 필요',
                'network_operation_id': clean_operation_id,
                'results': results,
            })
        except (AuthenticationError, OSError, ValueError) as exc:
            self._publish_response({
                'request_id': request_id,
                'success': False,
                'message': str(exc),
                'results': [],
            })
        finally:
            self._readiness_lock.release()

    def _run_control(self, request_id: str, raw_payload: object) -> None:
        try:
            if self._config.role != 'coordinator' or self._config.mode != 'participant':
                raise ValueError('연동 참여 중앙 PC만 전체 실행 명령을 보낼 수 있습니다')
            payload = dict(raw_payload) if isinstance(raw_payload, dict) else {}
            command = str(payload.get('command') or '')
            if command == 'synchronized_run':
                result = self._start_synchronized(payload)
            else:
                operation_id = str(
                    payload.get('network_operation_id')
                    or f'control-{uuid.uuid4().hex}'
                )
                wire_payload = {
                    **payload,
                    'network_operation_id': operation_id,
                }
                if command == 'acquire_control':
                    lease_id = f'lease-{uuid.uuid4().hex}'
                    wire_payload['lease_id'] = lease_id
                elif command in {'run_once', 'initialize', 'release_control'}:
                    wire_payload['lease_id'] = str(
                        payload.get('lease_id') or self._coordinator_lease_id
                    )
                if (
                    command == 'release_control'
                    and self._synchronized_operation_active
                ):
                    raise ValueError(
                        '동기 실행 중에는 모션 실행 제어권을 반환할 수 없습니다'
                    )
                wire = validate_control_payload(wire_payload)
                result = self._broadcast_control(wire)
                if command == 'acquire_control' and result['success']:
                    self._coordinator_lease_id = wire['lease_id']
                elif command == 'release_control' and result['success']:
                    self._coordinator_lease_id = ''
            self._publish_response({'request_id': request_id, **result})
        except (OSError, ValueError) as exc:
            self._publish_response({
                'request_id': request_id, 'success': False,
                'message': str(exc), 'results': [],
            })
        finally:
            self._readiness_lock.release()

    def _broadcast_control(self, payload: Mapping[str, object]) -> Dict[str, object]:
        local = self._execute_control(self._config.machine_id, payload)
        results = [{
            'machine_id': self._config.machine_id,
            'display_name': self._config.display_name,
            **local,
        }]
        with ThreadPoolExecutor(max_workers=min(16, len(self._config.peers) or 1)) as pool:
            futures = [pool.submit(
                self._request_peer_control, peer.machine_id, peer.url, payload
            ) for peer in self._config.peers]
            for future in as_completed(futures):
                results.append(future.result())
        success = bool(results) and all(item.get('success') for item in results)
        if not success and payload.get('command') in {'acquire_control', 'start_at'}:
            # Cancel an accepted scheduled run before releasing a partially
            # acquired lease.  Releasing first could allow a local run to race
            # with an already accepted START_AT operation.
            if payload.get('command') == 'start_at':
                cancel = {
                    'network_operation_id': f'rollback-cancel-{uuid.uuid4().hex}',
                    'command': 'cancel_before_start',
                }
                self._execute_control(self._config.machine_id, cancel)
                with ThreadPoolExecutor(
                    max_workers=min(16, len(self._config.peers) or 1)
                ) as pool:
                    list(pool.map(
                        lambda peer: self._request_peer_control(
                            peer.machine_id, peer.url, cancel
                        ),
                        self._config.peers,
                    ))
            lease_id = str(payload.get('lease_id') or '')
            if lease_id:
                rollback = {
                    'network_operation_id': f'rollback-{uuid.uuid4().hex}',
                    'command': 'release_control', 'lease_id': lease_id,
                }
                self._execute_control(self._config.machine_id, rollback)
                with ThreadPoolExecutor(max_workers=min(16, len(self._config.peers) or 1)) as pool:
                    list(pool.map(
                        lambda peer: self._request_peer_control(
                            peer.machine_id, peer.url, rollback
                        ),
                        self._config.peers,
                    ))
        return {
            'success': success,
            'message': '전체 실행 명령 승인' if success else '일부 PC 실행 명령 거부',
            'network_operation_id': payload.get('network_operation_id'),
            'lease_id': payload.get('lease_id', ''), 'results': results,
        }

    def _request_peer_control(
        self, peer_id: str, peer_url: str, payload: Mapping[str, object]
    ) -> Dict[str, object]:
        try:
            sequence, signed = self._runtime.build_control_request(peer_id, payload)
            request = urllib.request.Request(
                f'{peer_url}{CONTROL_PATH}', data=signed.body,
                headers={**signed.headers, 'Content-Type': 'application/json'},
                method='POST',
            )
            with urllib.request.urlopen(request, timeout=4.0) as response:
                body = response.read(MAX_STATUS_BODY_BYTES)
                headers = dict(response.headers.items())
            result = self._runtime.verify_control_response(
                peer_id, sequence, str(payload['network_operation_id']), body, headers
            )
            return {'machine_id': peer_id, **result}
        except (AuthenticationError, OSError, ValueError, urllib.error.URLError):
            return {
                'machine_id': peer_id, 'success': False, 'state': 'unavailable',
                'message': '연동 PC 실행 응답 없음',
            }

    def _handle_inbound_control(self, accepted: Mapping[str, object]) -> Dict[str, object]:
        sender = str(accepted['machine_id'])
        payload = accepted['payload']
        assert isinstance(payload, dict)
        return self._execute_control(sender, payload)

    def _execute_control(
        self, sender: str, payload: Mapping[str, object]
    ) -> Dict[str, object]:
        safe = validate_control_payload(payload)
        operation_id = safe['network_operation_id']
        command = safe['command']
        self._operation_journal.begin(sender, operation_id, command)
        try:
            if command == 'acquire_control':
                readiness = adapt_readiness_result(self._check_local_readiness())
                if readiness.get('state') != 'ready':
                    raise ValueError(str(readiness.get('message') or '로컬 실행 준비 거부'))
                result = {
                    'success': True, 'message': '네트워크 모션 실행 제어권 획득',
                    **self._execution_lease.acquire(
                        sender,
                        duration_sec=max(
                            float(safe.get('expires_at') or 0.0) - time.time(),
                            5.0,
                        ),
                        lease_id=str(safe.get('lease_id') or '')
                    ),
                }
            elif command == 'release_control':
                result = {
                    'success': True, 'message': '네트워크 모션 실행 제어권 반환',
                    **self._execution_lease.release(sender, str(safe['lease_id'])),
                }
            elif command in {'run_once', 'initialize', 'start_at'}:
                self._execution_lease.require(sender, str(safe['lease_id']))
                result = self._call_local_control(safe)
            elif command in {'stop_after_cycle'}:
                self._execution_lease.require(sender, str(safe['lease_id']))
                result = self._call_local_control(safe)
            else:
                result = self._call_local_control(safe)
        except ValueError as exc:
            result = {'success': False, 'state': 'rejected', 'message': str(exc)}
        self._operation_journal.finish(sender, operation_id, result)
        return result

    def _call_local_control(self, payload: Mapping[str, object]) -> Dict[str, object]:
        request = urllib.request.Request(
            f'http://127.0.0.1:{self._config.access.web_port}/api/coordination/local-control',
            data=json.dumps(dict(payload), separators=(',', ':')).encode('utf-8'),
            headers={'Content-Type': 'application/json'}, method='POST',
        )
        try:
            with urllib.request.urlopen(request, timeout=4.0) as response:
                value = json.loads(response.read(MAX_STATUS_BODY_BYTES).decode('utf-8'))
            return value if isinstance(value, dict) else {
                'success': False, 'message': '로컬 실행 응답 형식 오류'
            }
        except (OSError, ValueError, urllib.error.URLError):
            return {'success': False, 'state': 'unavailable', 'message': '로컬 실행 관리자 응답 없음'}

    def _start_synchronized(self, payload: Mapping[str, object]) -> Dict[str, object]:
        operation_id = f'sync-ready-{uuid.uuid4().hex}'
        readiness = [{
            'machine_id': self._config.machine_id,
            **self._runtime.attach_local_readiness_session(
                {
                    **adapt_readiness_result(self._check_local_readiness()),
                    **inspect_time_sync(),
                }
            ),
        }]
        for peer in self._config.peers:
            readiness.append(self._request_peer_readiness(
                peer.machine_id, peer.url, operation_id, {}
            ))
        if not all(
            item.get('state') == 'ready'
            and item.get('clock_sync_state') == 'ready'
            for item in readiness
        ):
            return {'success': False, 'message': '동기 실행 준비 확인 필요', 'results': readiness}
        initialization_lead = max(
            (float(item.get('initialization_duration_sec') or 0.0) for item in readiness),
            default=0.0,
        ) + 3.0
        schedule = build_synchronized_schedule(
            [float(item['motion_duration_sec']) for item in readiness],
            start_at=time.time() + max(
                float(payload.get('lead_sec') or 15.0), initialization_lead, 2.0
            ),
            dwell_sec=float(payload.get('dwell_sec') or 0.0),
            repeat_count=int(payload.get('repeat_count') or 1),
        )
        lease_id = f'lease-{uuid.uuid4().hex}'
        acquired = self._broadcast_control({
            'network_operation_id': f'sync-lease-{uuid.uuid4().hex}',
            'command': 'acquire_control', 'lease_id': lease_id,
        })
        if not acquired['success']:
            return acquired
        self._coordinator_lease_id = lease_id
        started = self._broadcast_control({
            'network_operation_id': f'sync-start-{uuid.uuid4().hex}',
            'command': 'start_at', 'lease_id': lease_id,
            'start_at': schedule['start_at'], 'cycle_sec': schedule['cycle_sec'],
            'repeat_count': schedule['repeat_count'], 'hold_final': True,
        })
        if started['success']:
            self._synchronized_operation_active = True
            self._synchronized_seen_running = False
        return {**started, 'schedule': schedule}

    def _request_peer_readiness(
        self,
        peer_id: str,
        peer_url: str,
        operation_id: str,
        record: Mapping[str, object],
    ) -> Dict[str, object]:
        display_name = str(
            record.get('payload', {}).get('display_name')
            if isinstance(record.get('payload'), dict) else ''
        ) or peer_id
        try:
            sequence, signed = self._runtime.build_readiness_request(
                peer_id, operation_id
            )
            request = urllib.request.Request(
                f'{peer_url}{READINESS_PATH}',
                data=signed.body,
                headers={**signed.headers, 'Content-Type': 'application/json'},
                method='POST',
            )
            with urllib.request.urlopen(request, timeout=4.0) as response:
                body = response.read(MAX_STATUS_BODY_BYTES)
                headers = dict(response.headers.items())
            readiness = self._runtime.verify_readiness_response(
                peer_id, sequence, operation_id, body, headers
            )
            return {
                'machine_id': peer_id,
                'display_name': display_name,
                **readiness,
            }
        except (AuthenticationError, OSError, ValueError, urllib.error.URLError):
            return {
                'machine_id': peer_id,
                'display_name': display_name,
                'readiness_version': 1,
                'state': 'unavailable',
                'reason_code': 'peer_unavailable',
                'message': '연동 PC 준비 응답 없음',
            }

    def _check_local_readiness(self) -> Dict[str, object]:
        request = urllib.request.Request(
            f'http://127.0.0.1:{self._config.access.web_port}'
            '/api/coordination/local-readiness',
            data=b'{}',
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        try:
            with urllib.request.urlopen(request, timeout=3.5) as response:
                result = json.loads(
                    response.read(MAX_STATUS_BODY_BYTES).decode('utf-8')
                )
            return result if isinstance(result, dict) else {}
        except (OSError, ValueError, urllib.error.URLError):
            return {'success': False, 'message': 'local readiness timeout'}

    def _publish_response(self, payload: Mapping[str, object]) -> None:
        self._response_publisher.publish(String(data=json.dumps(
            dict(payload),
            ensure_ascii=False,
            separators=(',', ':'),
        )))


def main(args=None) -> None:
    """Run the coordination node until shutdown."""
    rclpy.init(args=args)
    node = None
    try:
        node = MotionCoordinationNode()
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
