import math
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from ament_index_python.packages import get_package_share_directory
from motion_common.timing import CONTROL_PERIOD_SEC

DYNAMIXEL_BAUDRATE = 1000000
#: 공용 커널이 단일 정의 · 기존 이름은 호환을 위해 남긴다
MOTION_DATA_PERIOD_SEC = CONTROL_PERIOD_SEC


def motor_activity_snapshot(
    motion_run: Dict[str, Any],
    motion_studio: Dict[str, Any],
    safety_status: Dict[str, Any],
) -> Dict[str, Any]:
    """Return one conservative, display-only motor activity classification."""
    run = motion_run if isinstance(motion_run, dict) else {}
    studio = motion_studio if isinstance(motion_studio, dict) else {}
    safety = safety_status if isinstance(safety_status, dict) else {}
    run_state = str(run.get('state') or 'idle')
    run_phase = str(run.get('phase') or '')
    studio_state = str(studio.get('state') or 'idle')
    owner = str(safety.get('command_owner') or 'none')
    manual_values = safety.get('manual_activity_modes')
    if not isinstance(manual_values, list):
        manual_values = []
    manual_modes = {str(item) for item in manual_values if str(item)}

    def active(kind: str, label: str, source: str) -> Dict[str, Any]:
        return {
            'active': True,
            'kind': kind,
            'label': label,
            'source': source,
            'warning': False,
        }

    if run_state == 'initializing':
        return active('initializing', '초기 위치 이동 중', 'motion_run')
    if run_state in {'running', 'verifying'}:
        if bool(run.get('automation_run')):
            return active('automation', '자동 반복 모션 동작 중', 'motion_run')
        return active('motion_run', '모션 동작 중', 'motion_run')
    if run_state == 'countdown':
        return {
            'active': False,
            'kind': 'countdown',
            'label': '',
            'source': 'motion_run',
            'warning': False,
        }
    if run_state == 'initialized' and studio_state == 'initializing':
        return {
            'active': False,
            'kind': 'initialized',
            'label': '',
            'source': 'motion_run',
            'warning': False,
        }
    if studio_state == 'initializing':
        return active('initializing', '초기 위치 이동 중', 'motion_studio')
    if studio_state == 'playing':
        return active('studio_playback', '모션 스튜디오 동작 중', 'motion_studio')
    if studio_state == 'recording':
        return active('studio_recording', '모션 스튜디오 녹화 중', 'motion_studio')
    if 'action' in manual_modes:
        return active('action', '동작 모드 동작 중', 'motion_supervisor')
    if 'jog' in manual_modes:
        return active('jog', '조그 모드 동작 중', 'motion_supervisor')
    if owner == 'midi':
        return active('midi', 'MIDI 모터 제어 중', 'motion_supervisor')

    repeat_waiting = run_state == 'waiting' and run_phase == 'repeat_waiting'
    if repeat_waiting and owner == 'playback':
        return {
            'active': False,
            'kind': 'repeat_waiting',
            'label': '',
            'source': 'motion_run',
            'warning': False,
        }
    if owner not in {'', 'none'}:
        return {
            'active': True,
            'kind': 'unknown',
            'label': '모터 동작 상태 확인 필요',
            'source': 'motion_supervisor',
            'warning': True,
        }
    return {
        'active': False,
        'kind': 'idle',
        'label': '',
        'source': '',
        'warning': False,
    }


def _monitoring_finite_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _monitoring_motor_ref(motor: Dict[str, Any]) -> str:
    text = ' '.join(
        str(motor.get(key) or '').lower()
        for key in ('motor_type', 'motor_type_label', 'driver_model', 'transport')
    )
    if 'dynamixel' in text:
        value = motor.get('bus_id', motor.get('node_id'))
        serial_port = str(motor.get('serial_port') or '').strip()
        try:
            return (
                f'dynamixel:port:{quote(serial_port, safe="")}:id:{int(value)}'
                if serial_port
                else ''
            )
        except (TypeError, ValueError):
            return ''
    if 'minas' in text or 'ac servo' in text or 'ac_servo' in text:
        value = motor.get('alias', motor.get('ethercat_alias'))
        master_index = motor.get('ethercat_master_index', 0)
        try:
            master = int(master_index)
        except (TypeError, ValueError):
            return ''
        try:
            alias = int(value)
        except (TypeError, ValueError):
            alias = 0
        if alias > 0 and master >= 0:
            return f'ac_servo:master:{master}:alias:{alias}'
        try:
            position = int(motor.get('slave_position'))
            return (
                f'ac_servo:master:{master}:slave:{position}'
                if master >= 0 and position >= 0
                else ''
            )
        except (TypeError, ValueError):
            return ''
    return ''


def _monitoring_motor_refs(motor: Dict[str, Any]) -> List[str]:
    canonical = _monitoring_motor_ref(motor)
    text = ' '.join(
        str(motor.get(key) or '').lower()
        for key in ('motor_type', 'motor_type_label', 'driver_model', 'transport')
    )
    try:
        if 'minas' in text or 'ac servo' in text or 'ac_servo' in text:
            alias = int(motor.get('alias', motor.get('ethercat_alias')))
            legacy = f'ac_servo:alias:{alias}' if alias > 0 else ''
        elif 'dynamixel' in text:
            bus_id = int(motor.get('bus_id', motor.get('node_id')))
            legacy = f'dynamixel:id:{bus_id}' if bus_id >= 0 else ''
        else:
            legacy = ''
    except (TypeError, ValueError):
        legacy = ''
    return [item for item in (canonical, legacy) if item]


def add_monitoring_motion_values(
    motion_state: Dict[str, Any],
    mapping_rows: List[Dict[str, Any]],
    motion_value_state: Optional[Dict[str, Any]] = None,
) -> None:
    """Attach control-layer motion values without deriving them from feedback."""
    motors = motion_state.get('motors')
    if not isinstance(motors, list):
        return
    value_state = motion_value_state if isinstance(motion_value_state, dict) else {}
    received_values = value_state.get('values')
    if not isinstance(received_values, dict):
        received_values = {}
    value_sources = value_state.get('sources')
    if not isinstance(value_sources, dict):
        value_sources = {}
    valid_motors = [motor for motor in motors if isinstance(motor, dict)]
    rows_by_axis: Dict[int, List[Dict[str, Any]]] = {}
    for row in mapping_rows:
        if not isinstance(row, dict) or row.get('enabled') is False:
            continue
        motor_ref = str(row.get('motor_ref') or '').strip()
        axis: Optional[int] = None
        if motor_ref:
            matches = [
                motor
                for motor in valid_motors
                if motor_ref.lower()
                in {ref.lower() for ref in _monitoring_motor_refs(motor)}
            ]
            if len(matches) == 1:
                try:
                    axis = int(matches[0].get('controller_index'))
                except (TypeError, ValueError):
                    axis = None
        else:
            try:
                axis = int(row.get('motor_axis'))
            except (TypeError, ValueError):
                axis = None
        if axis is not None and axis >= 0:
            rows_by_axis.setdefault(axis, []).append(row)

    for motor in valid_motors:
        motor.update({
            'motion_axis_configured': False,
            'motion_id': None,
            'motion_value_deg': None,
            'motion_value_status': 'unmapped',
            'motion_value_message': '모션축 미설정',
            'motion_value_source': None,
        })
        try:
            axis = int(motor.get('controller_index'))
        except (TypeError, ValueError):
            continue
        rows = rows_by_axis.get(axis, [])
        if not rows:
            continue
        motor['motion_axis_configured'] = True
        if len(rows) != 1:
            motor.update({
                'motion_value_status': 'missing',
                'motion_value_message': (
                    '활성 모션축 중복 설정으로 모션값을 연결할 수 없음'
                ),
            })
            continue

        row = rows[0]
        motion_id = str(row.get('motion_id') or '').strip()
        motor['motion_id'] = motion_id or None
        motion_value = _monitoring_finite_float(received_values.get(motion_id))
        if not motion_id or motion_value is None:
            motor.update({
                'motion_value_status': 'missing',
                'motion_value_message': '모션값 토픽 미수신',
            })
            continue
        source = str(value_sources.get(motion_id) or '')
        source_label = {'midi': 'MIDI', 'motion_run': '모션 실행'}.get(source, source)
        motor.update({
            'motion_value_deg': round(motion_value, 6),
            'motion_value_status': 'received',
            'motion_value_message': (
                f'{source_label} 제어 모션값 수신'
                if source_label
                else '제어 모션값 수신'
            ),
            'motion_value_source': source or None,
        })


def _workspace_root() -> Path:
    configured = str(os.environ.get('MOTION_WORKSPACE') or '').strip()
    if configured:
        return Path(configured).expanduser().resolve()

    candidates = [Path.cwd(), Path(__file__).resolve()]
    try:
        candidates.insert(
            0, Path(get_package_share_directory('motion_web_bridge')).resolve()
        )
    except Exception:
        pass
    for candidate in candidates:
        for parent in (candidate, *candidate.parents):
            if parent.name == 'install':
                return parent.parent
            if (parent / 'src').is_dir() and (parent / 'scripts').is_dir():
                return parent
    return Path.cwd().resolve()


def _safe_project_publish_stem(value: Any) -> str:
    text = ''.join(
        character if character.isalnum() or character in '._-' else '_'
        for character in str(value or '')
    ).strip('._-')
    return text[:80] or 'project_file'
