"""Recording frame accumulation and final layer persistence."""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Dict

from .constants import DEFAULT_PERIOD_SEC

#: 실행 노드가 돌리는 카운트다운 · 합성 미리보기와 같은 값
COUNTDOWN_SEC = 3.0
from .layer_commands import next_numbered_layer_name
from .procedure import ProcedureStopped, StudioProcedure
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
                ownership = playback_ownership(project)
            else:
                ownership = {}
            studio._record_frames = []
            studio._record_eligible_motion_ids = set(motion_ids)
            studio._recorded_motion_ids = set()
            # 소유 구간은 테이크가 쥔다 · 따로 두면 둘이 어긋난다 · §6-80
            operation_generation = studio._takes().begin(
                mode, '초기 위치 이동 준비 중', ownership,
            )
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
        """녹화를 시작하기까지 · §6-82

        무엇을 하는지만 적는다 · 언제 멈추는지, 어떻게 기다리는지, 실패하면
        무엇을 되감는지는 `StudioProcedure` 가 맡는다.
        """
        studio = self.studio
        with studio._lock:
            take = studio._take
            overdub_mode = bool(take and take.overdub)
        steps = StudioProcedure(studio, operation_generation)
        release_midi = lambda: studio._request_midi('studio_recording_ready', {}, 2.0)
        state: Dict[str, Any] = {}

        def lock_midi() -> None:
            response = studio._request_midi('studio_recording_prepare', {}, 5.0)
            steps.unwind(release_midi)
            if not response.get('success'):
                raise ValueError(response.get('message') or 'MIDI 녹화 초기화 준비 실패')

        def move_to_zero() -> None:
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
            response = studio._request_run_for_operation(
                'initialize',
                studio._run_payload(project, file_id, motion_ids, move_time),
                30.0,
                operation_generation,
                'initializing',
            )
            if not response.get('success'):
                raise ValueError(response.get('message') or '초기 위치 이동 실패')
            steps.wait_for_run_state(
                {'initialized'},
                timeout=max(15.0, move_time + 10.0),
                timeout_message='초기 위치 도착 확인',
            )

        def countdown() -> None:
            if not studio._countdown('녹화', operation_generation):
                raise ProcedureStopped()

        def unlock_midi() -> None:
            response = studio._request_midi('studio_recording_ready', {}, 5.0)
            if not response.get('success'):
                raise ValueError(response.get('message') or 'MIDI SELECT 잠금 해제 실패')
            steps.cancel_unwind(release_midi)

        def start_playback() -> None:
            # 추가 녹화는 녹화된 대로 모터를 돌리면서 그 위에 얹는다 · §6-74
            #
            # 재생과 녹화가 같은 소유 구간을 본다 · 재생은 구간 밖에서 그 축을
            # 놓고, 녹화는 구간 안을 버린다.
            state['overdub'] = self.start_overdub_playback(
                operation_generation, move_time,
            )
            if state['overdub']:
                # 녹화 시계의 0 초는 **재생의 0 초**여야 한다 · §6-76
                #
                # 이 한 번의 요청이 초기 이동과 카운트다운까지 한다 · 그만큼
                # 기다려 준다 · §6-87
                steps.wait_for_run_state(
                    {'running', 'verifying'},
                    timeout=max(40.0, move_time + COUNTDOWN_SEC + 25.0),
                    timeout_message='추가 녹화 재생 시작 확인',
                )

        def begin_recording() -> None:
            with studio._lock:
                studio._record_started = time.monotonic()
                studio._record_frames = []
                studio._recorded_motion_ids = set()
                studio._takes().advance(
                    'running',
                    '추가 녹화 중 · 녹화된 축은 재생되고 나머지는 MIDI로 기록합니다'
                    if state.get('overdub')
                    else '모션 녹화 중 · MIDI SELECT로 움직이는 축을 자동 기록합니다',
                )

        # 추가 녹화는 실행 노드가 초기 이동·카운트다운·재생을 이어서 한다 ·
        # 스튜디오가 흉내 내던 앞의 두 단계가 빠진다 · 초기 이동이 두 번
        # 일어나던 것이 그래서 사라진다 · §6-87
        steps.run([
            ('MIDI 녹화 준비', lock_midi),
            ('MIDI 페이더 0 복귀', lambda: self.wait_for_midi_faders_zero(8.0)),
        ] + ([
            ('MIDI SELECT 잠금 해제', unlock_midi),
            ('추가 녹화 재생 시작', start_playback),
        ] if overdub_mode else [
            ('초기 위치 이동', move_to_zero),
            ('카운트다운', countdown),
            ('MIDI SELECT 잠금 해제', unlock_midi),
        ]) + [
            ('녹화 시작', begin_recording),
        ])

    def start_overdub_playback(
        self, operation_generation: int, move_time: float,
    ) -> bool:
        """추가 녹화 · **요청 한 번**으로 초기 이동·카운트다운·재생을 잇는다 · §6-87

        전에는 스튜디오가 0 도 이동을 따로 시키고, 카운트다운도 직접 돌리고,
        그 다음 재생을 시켰다 · 그런데 실행 노드의 `start` 는 원래 그 셋을
        이어서 한다(합성 미리보기가 이미 그렇게 쓴다). 그래서 **초기 이동이 두
        번** 일어났다 · 0 도로 한 번, 합성 시작 위치로 또 한 번.

        이제 한 번만 움직인다 · 목적지는 합성의 0 초 값이고, 레이어에 없는 축은
        그 파일 안에서 0 도다.

        재생이 쥐는 구간은 축마다 따로 준다 · 레이어에 없는 축은 **빈 목록**이라
        초기 이동에는 함께 나서고 그 뒤로는 재생이 건드리지 않는다.

        일반 녹화면 아무 일도 하지 않고 거짓을 돌려준다.
        """
        studio = self.studio
        with studio._lock:
            take = studio._take
            ownership = dict(take.ownership if take else {})
            if take is None or not take.overdub or not ownership:
                return False
            project = dict(studio._require_project_locked())
            mapping = studio._validate_mapping_locked(project)
            # 녹화 대상 축 전부 · 레이어에 없는 축도 0 도로 함께 맞춘다
            motion_ids = sorted(studio._record_eligible_motion_ids)
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
            **studio._run_payload(project, file_id, motion_ids, move_time),
            'countdown_sec': COUNTDOWN_SEC,
            # 축마다 재생이 쥐는 구간 · 이 밖에서는 그 축을 명령하지 않는다.
            #
            # 끝 시각만 보내면 **시작 전**이 빈다 · 합성은 모든 축을 매 순간
            # 채우므로, 10 초부터 데이터가 있는 축도 0 초부터 명령돼 그 앞
            # 구간을 MIDI 가 못 쓴다 · 구간을 통째로 보낸다 · §6-77
            'axis_playback_spans': {
                motion_id: [[start, end] for start, end in ownership.get(motion_id, ())]
                for motion_id in motion_ids
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
        take = self.studio._take
        ownership = take.ownership if take else None
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
            # 시계는 테이크가 쥔다 · 화면이 조립할 게 없어진다 · §6-81
            take = studio._take
            studio._takes().tick(
                frame['time_sec'],
                max(frame['time_sec'], take.total_sec if take else 0.0),
            )
            studio._status['recorded_frames'] = index
            studio._status['updated_at'] = time.time()

    def finish_locked(self, message: str = '모션 녹화 완료') -> str:
        """녹화된 프레임을 레이어로 남긴다 · **테이크는 닫지 않는다** · §6-80

        닫는 것은 정지 절차의 몫이다 · 여기서 닫아 버리면 정지 중(`stopping`)을
        표시할 테이크가 남지 않는다.
        """
        studio = self.studio
        if not studio._record_frames or not studio._recorded_motion_ids:
            studio._record_frames = []
            studio._status['message'] = (
                '기록된 축이 없어 레이어를 만들지 않았습니다 · 녹화 중 MIDI SELECT 축을 움직이세요'
            )
            return ''
        project = studio._require_project_locked()
        layers = project.setdefault('layers', [])
        layer_name = next_numbered_layer_name(
            layers, self.mode_label(studio._take.kind if studio._take else 'record')
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
        studio._status['message'] = (
            f'{message} · {motion_id_count}개 축 · {count} 프레임 저장'
        )
        return layer['layer_id']
