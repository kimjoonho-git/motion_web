"""Recording frame accumulation and final layer persistence."""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Dict

from .constants import DEFAULT_PERIOD_SEC
from .layer_commands import next_numbered_layer_name
from .motion_model import layer_motion_ids
from .timeline import (
    motion_file_text,
    owned_at,
    render_project,
    playback_ownership,
    project_motion_ids,
    recording_values,
)


class StudioRecordingSession:
    def __init__(self, studio: Any) -> None:
        self.studio = studio

    @staticmethod
    def mode_label(mode: str) -> str:
        return '추가 녹화' if mode == 'overdub' else '녹화'

    def overdub_take_locked(self) -> bool:
        """지금 추가 녹화 테이크가 도는 중인가 · 잠금 안에서 부른다."""
        return getattr(self.studio, '_record_mode', 'record') == 'overdub'

    def clear_take_locked(self) -> None:
        """테이크를 끝낸다 · 스튜디오가 idle/error 로 갈 때 부른다.

        녹화 모드가 남아 있으면 다음 합성 미리보기가 추가 녹화로 오인돼 상태
        전이를 통째로 잃는다 · §6-76
        """
        self.studio._record_mode = 'record'
        self.studio._record_ownership = {}

    def start(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        studio = self.studio
        mode = str(payload.get('mode') or 'record').strip().lower()
        if mode not in {'record', 'overdub'}:
            raise ValueError('녹화 모드는 record 또는 overdub 이어야 합니다')
        with studio._lock:
            studio._require_idle_locked()
            project = studio._require_project_locked()
            mapping = studio._validate_mapping_locked(project)
            motion_ids = list(mapping.get('motion_ids') or [])
            if not motion_ids:
                raise ValueError('모션축 설정에 녹화 가능한 Motion ID가 없습니다')

            # 추가 녹화 · 축을 빼지 않는다.
            #
            # 재생이 소유하는 것은 **축이 아니라 축×시간**이다 · 축 1-1 이 10초에
            # 끝나면 10초 이후에는 같은 축을 MIDI 로 이어 녹화할 수 있어야 한다 ·
            # 그래서 대상은 전부로 두고 `record_tick` 이 그 시각의 소유만 버린다 ·
            # §6-74
            if mode == 'overdub':
                if not project.get('layers'):
                    raise ValueError(
                        '추가 녹화는 녹화된 레이어가 있어야 합니다 · 먼저 녹화하세요'
                    )
                studio._record_ownership = playback_ownership(project)
            else:
                studio._record_ownership = {}
            studio._record_mode = mode
            studio._record_frames = []
            studio._record_eligible_motion_ids = set(motion_ids)
            studio._recorded_motion_ids = set()
            operation_generation = studio._operation_machine().begin(
                str(studio._status.get('state') or '')
            )
            studio._set_status_locked('initializing', '초기 위치 이동 준비 중')
        threading.Thread(
            target=self.prepare,
            args=(
                float(payload.get('initial_move_time_sec') or 5.0),
                operation_generation,
            ),
            daemon=True,
        ).start()
        return {
            'success': True,
            'message': '자동 초기 위치 이동을 시작합니다',
            'status': studio.snapshot(),
        }

    def prepare(self, move_time: float, operation_generation: int) -> None:
        studio = self.studio
        midi_locked = False
        try:
            studio._require_active_operation(operation_generation, 'initializing')
            midi_prepare = studio._request_midi(
                'studio_recording_prepare', {}, 5.0
            )
            midi_locked = True
            if not midi_prepare.get('success'):
                raise ValueError(
                    midi_prepare.get('message') or 'MIDI 녹화 초기화 준비 실패'
                )
            self.wait_for_midi_faders_zero(8.0)
            studio._require_active_operation(operation_generation, 'initializing')
            with studio._lock:
                project = dict(studio._require_project_locked())
                motion_ids = list(studio._record_eligible_motion_ids)
            zero_frames = [{
                'frame': 1,
                'time_sec': DEFAULT_PERIOD_SEC,
                'values': {motion_id: 0.0 for motion_id in motion_ids},
            }]
            file_id = studio._store.write_motion_file(
                f'{project["project_id"]}_record_init',
                motion_file_text(project, zero_frames),
                hidden=True,
            )
            run_payload = studio._run_payload(
                project, file_id, motion_ids, move_time
            )
            response = studio._request_run_for_operation(
                'initialize',
                run_payload,
                30.0,
                operation_generation,
                'initializing',
            )
            if not response.get('success'):
                raise ValueError(response.get('message') or '초기 위치 이동 실패')
            deadline = time.monotonic() + max(15.0, move_time + 10.0)
            while time.monotonic() < deadline:
                with studio._lock:
                    if operation_generation != studio._operation_generation:
                        return
                    status = dict(studio._motion_run_status)
                if status.get('state') == 'initialized':
                    break
                if status.get('state') == 'error':
                    raise ValueError(status.get('message') or '초기 위치 이동 실패')
                time.sleep(0.05)
            else:
                raise ValueError('초기 위치 도착 확인 시간 초과')
            if not studio._countdown('녹화', operation_generation):
                return
            studio._require_active_operation(operation_generation, 'initializing')
            midi_ready = studio._request_midi(
                'studio_recording_ready', {}, 5.0
            )
            if not midi_ready.get('success'):
                raise ValueError(
                    midi_ready.get('message') or 'MIDI SELECT 잠금 해제 실패'
                )
            midi_locked = False
            # 추가 녹화는 녹화된 대로 모터를 돌리면서 그 위에 얹는다 · §6-74
            #
            # 재생과 녹화가 같은 20ms 타이머 위에서 돈다 · 재생은 축이 끝나면
            # 둘 다 같은 소유 구간을 본다 · 재생은 구간 밖에서 그 축을 놓고,
            # 녹화는 구간 안을 버린다.
            overdub = self.start_overdub_playback(operation_generation)
            if overdub:
                self.wait_for_playback_running(operation_generation, 20.0)
            with studio._lock:
                studio._record_started = time.monotonic()
                studio._record_frames = []
                studio._recorded_motion_ids = set()
                studio._set_status_locked(
                    'recording',
                    '추가 녹화 중 · 녹화된 축은 재생되고 나머지는 MIDI로 기록합니다'
                    if overdub
                    else '모션 녹화 중 · MIDI SELECT로 움직이는 축을 자동 기록합니다',
                )
        except Exception as exc:
            with studio._lock:
                if operation_generation == studio._operation_generation:
                    studio._set_status_locked('error', str(exc))
        finally:
            if midi_locked:
                studio._request_midi('studio_recording_ready', {}, 2.0)

    def wait_for_playback_running(
        self, operation_generation: int, timeout: float
    ) -> None:
        """재생이 실제로 돌기 시작할 때까지 기다린다 · §6-76

        녹화 시계의 0 초는 **재생의 0 초**여야 한다. 실행 요청이 받아들여진
        순간부터 재면 계획 생성과 초기 이동에 걸린 시간만큼 새 레이어가 통째로
        밀린다 · 사용자가 본 움직임과 저장된 것이 어긋난다.
        """
        studio = self.studio
        deadline = time.monotonic() + max(0.1, float(timeout))
        while time.monotonic() < deadline:
            with studio._lock:
                if operation_generation != studio._operation_generation:
                    return
                status = dict(studio._motion_run_status)
            state = str(status.get('state') or '')
            if state in {'running', 'verifying'}:
                return
            if state == 'error':
                raise ValueError(status.get('message') or '추가 녹화 재생 실패')
            time.sleep(0.01)
        raise ValueError('추가 녹화 재생 시작 확인 시간 초과')

    def start_overdub_playback(self, operation_generation: int) -> bool:
        """추가 녹화일 때 기존 레이어 재생을 함께 시작한다 · §6-74

        재생은 `axis_release_sec` 로 **축이 끝나면 그 축을 놓는다** · 놓은 축은
        `CommandArbiter` 에서 풀려 MIDI 가 이어받는다. 그래서 같은 축이라도
        재생이 끝난 뒤 구간은 MIDI 로 녹화된다.

        일반 녹화면 아무 일도 하지 않고 거짓을 돌려준다.
        """
        studio = self.studio
        with studio._lock:
            ownership = dict(getattr(studio, '_record_ownership', {}) or {})
            if studio._record_mode != 'overdub' or not ownership:
                return False
            project = dict(studio._require_project_locked())
            mapping = studio._validate_mapping_locked(project)
            motion_ids = project_motion_ids(project)
        if not motion_ids:
            return False
        frames = render_project(
            project,
            motion_ids=motion_ids,
            initial_motion_values_deg=studio._manual_initial_values(mapping),
        )
        file_id = studio._store.write_motion_file(
            f'{project["project_id"]}_overdub',
            motion_file_text(project, frames),
            hidden=True,
        )
        payload = {
            **studio._run_payload(project, file_id, motion_ids, 0.0),
            # 축마다 재생이 쥐는 구간 · 이 밖에서는 그 축을 명령하지 않는다.
            #
            # 끝 시각만 보내면 **시작 전**이 빈다 · 합성은 모든 축을 매 순간
            # 채우므로, 10 초부터 데이터가 있는 축도 0 초부터 명령돼 그 앞
            # 구간을 MIDI 가 못 쓴다 · 구간을 통째로 보낸다 · §6-77
            'axis_playback_spans': {
                motion_id: [[start, end] for start, end in spans]
                for motion_id, spans in ownership.items() if spans
            },
        }
        response = studio._request_run_for_operation(
            'start', payload, 10.0, operation_generation, 'initializing',
        )
        if not response.get('success'):
            raise ValueError(response.get('message') or '추가 녹화 재생 시작 실패')
        return True

    def wait_for_midi_faders_zero(self, timeout: float) -> None:
        """Block motor initialization until all physical MIDI faders are at zero."""
        studio = self.studio
        deadline = time.monotonic() + max(0.1, float(timeout))
        last_message = 'MIDI 페이더 물리 0 복귀 확인 중'
        while time.monotonic() < deadline:
            response = studio._request_midi(
                'studio_recording_zero_status', {}, min(2.0, timeout)
            )
            if not response.get('success'):
                raise ValueError(
                    response.get('message') or 'MIDI 페이더 0 위치 확인 실패'
                )
            if not response.get('device_connected', True):
                raise ValueError('MIDI 장치 연결이 끊겨 녹화를 시작할 수 없습니다')
            if response.get('ready'):
                return
            last_message = str(
                response.get('message') or 'MIDI 페이더 물리 0 복귀 확인 중'
            )
            with studio._lock:
                if studio._status.get('state') != 'initializing':
                    raise ValueError('녹화 초기화가 취소되었습니다')
                studio._status['phase'] = 'midi_zero_wait'
                studio._status['message'] = last_message
                studio._status['updated_at'] = time.time()
            studio._publish_status()
            time.sleep(0.05)
        raise ValueError(
            f'{last_message} · 제한 시간 초과로 모터 이동을 차단했습니다'
        )

    def selected_motion_values_locked(self) -> Dict[str, float]:
        result = {}
        for channel in self.studio._midi_state.get('channels') or []:
            if (
                not isinstance(channel, dict)
                or not channel.get('control_enabled')
                or channel.get('motion_group_valid') is False
                or channel.get('motion_command_valid') is False
            ):
                continue
            linked_values = channel.get('motion_values_deg')
            if isinstance(linked_values, dict):
                for motion_id, value in linked_values.items():
                    motion_id = str(motion_id or '')
                    if motion_id and isinstance(value, (int, float)):
                        result[motion_id] = float(value)
                if linked_values:
                    continue
            motion_id = str(channel.get('motion_id') or '')
            value = channel.get('motion_value_deg')
            if motion_id and isinstance(value, (int, float)):
                result[motion_id] = float(value)
        return result

    def update_snapshot(self, result: Dict[str, Any]) -> None:
        studio = self.studio
        result['recording_motion_ids'] = sorted(studio._recorded_motion_ids)
        result['recorded_frames'] = len(studio._record_frames)
        if result.get('state') != 'recording':
            return
        result['elapsed_sec'] = round(
            len(studio._record_frames) * DEFAULT_PERIOD_SEC, 3
        )
        preview_limit = 240
        frame_count = len(studio._record_frames)
        stride = max(1, (frame_count + preview_limit - 1) // preview_limit)
        preview_frames = studio._record_frames[::stride]
        if studio._record_frames and preview_frames[-1] is not studio._record_frames[-1]:
            preview_frames = [*preview_frames, studio._record_frames[-1]]
        result['recording_preview_frames'] = [
            {
                'time_sec': float(frame.get('time_sec') or 0.0),
                'values': dict(frame.get('values') or {}),
            }
            for frame in preview_frames
        ]
        result['recording_preview_stride'] = stride

    def drop_owned_values(self, values: dict, time_sec: float) -> dict:
        """그 시각에 재생이 쥔 축을 녹화에서 버린다 · §6-74

        `playback_ownership` 이 낸 ``{motion_id: [(시작, 끝), ...]}`` 를 본다 ·
        구간 안이면 재생이 주인이므로 MIDI 로 만져도 기록하지 않는다. 구간 밖이면
        같은 축이라도 기록한다 · "축 1-1 이 10초에 끝나면 10초 이후부터 녹화" 가
        이 규칙이다.
        """
        ownership = getattr(self.studio, '_record_ownership', None)
        if not ownership:
            return values
        return {
            motion_id: value
            for motion_id, value in values.items()
            if not owned_at(ownership.get(str(motion_id), ()), time_sec)
        }

    def record_tick(self) -> None:
        studio = self.studio
        with studio._lock:
            if studio._status.get('state') != 'recording':
                return
            selected = studio._selected_motion_values_locked()
            values = recording_values(selected, studio._record_eligible_motion_ids)
            index = len(studio._record_frames) + 1
            time_sec = round(index * DEFAULT_PERIOD_SEC, 9)
            # 이 시각에 재생이 쥔 축은 버린다 · 그 축은 MIDI 가 움직여도 기록하지
            # 않는다. 소유는 축 × 시간이므로 같은 축이라도 재생이 끝난 뒤에는
            # 기록된다 · §6-74
            values = self.drop_owned_values(values, time_sec)
            studio._recorded_motion_ids.update(values)
            frame = {
                'frame': index,
                'time_sec': time_sec,
                'values': values,
            }
            studio._record_frames.append(frame)
            studio._status['elapsed_sec'] = frame['time_sec']
            studio._status['recorded_frames'] = index
            studio._status['updated_at'] = time.time()

    def finish_locked(self, message: str = '모션 녹화 완료') -> str:
        studio = self.studio
        if not studio._record_frames or not studio._recorded_motion_ids:
            studio._record_frames = []
            studio._set_status_locked(
                'idle',
                '기록된 축이 없어 레이어를 만들지 않았습니다 · 녹화 중 MIDI SELECT 축을 움직이세요',
            )
            return ''
        project = studio._require_project_locked()
        layers = project.setdefault('layers', [])
        layer_name = next_numbered_layer_name(
            layers, self.mode_label(studio._record_mode)
        )
        layer = {
            'layer_id': f'layer_{uuid.uuid4().hex[:8]}',
            'name': layer_name,
            'enabled': True,
            'locked': False,
            'created_at': time.time(),
            'frames': list(studio._record_frames),
        }
        layers.append(layer)
        studio._current_project = studio._store.save_project(
            project, upsert_layer_ids=[layer['layer_id']]
        )
        try:
            mapping = studio._store.mapping_check(studio._current_project)
            studio._project_composition(
                studio._current_project,
                mapping,
                affected_motion_ids=layer_motion_ids(layer),
                affected_layer_ids={layer['layer_id']},
            )
        except Exception:
            studio._workspace().clear_composition_cache()
        count = len(studio._record_frames)
        motion_id_count = len(studio._recorded_motion_ids)
        studio._record_frames = []
        studio._recorded_motion_ids = set()
        studio._set_status_locked(
            'idle', f'{message} · {motion_id_count}개 축 · {count} 프레임 저장'
        )
        return layer['layer_id']
