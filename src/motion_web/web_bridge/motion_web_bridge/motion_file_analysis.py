"""모션 파일 해석·요약 · 상태 비의존.

`MotionWebBridge`에서 떼어낸 순수 함수 모음이다. 모션 파일 내용과 인자만 보고
판단하며 노드의 상태도 락도 건드리지 않는다.

파서 자체는 `motion_common.motion_table`이 단일 구현으로 갖는다 · 이 모듈은
그 결과를 화면·API가 쓰는 형태로 요약한다.

`test_pure_modules.py`가 순수성을 검사한다.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List

import yaml

from motion_common import motion_table
from motion_common.timing import CONTROL_PERIOD_SEC


def motion_mapping_file_id(result: Dict[str, Any]) -> str:
    file_info = result.get('file')
    if not isinstance(file_info, dict):
        return ''
    return str(file_info.get('id') or file_info.get('filename') or '').strip()


def midi_mapping_file_id(result: Dict[str, Any]) -> str:
    file_id = str(result.get('motion_mapping_file_id') or '').strip()
    if file_id:
        return Path(file_id).name
    file_path = str(result.get('bank_config_file') or '').strip()
    return Path(file_path).name if file_path else ''


def motion_file_path(file_id: Any, directory: Path) -> Path:
    name = str(file_id or '').strip()
    if not name:
        raise ValueError('file_id is required')
    if name != Path(name).name or '/' in name or '\\' in name:
        raise ValueError('invalid motion file id')
    path = directory / name
    if not path.is_file():
        raise ValueError(f'motion file not found: {name}')
    return path


def configured_axes_from_runtime_file(
    runtime: Path | str,
    *,
    transport: str = '',
) -> List[int]:
    try:
        payload = yaml.safe_load(
            Path(runtime).read_text(encoding='utf-8')
        ) or {}
        axes = [
            int(slave['controller_index'])
            for master in payload.get('masters') or []
            if isinstance(master, dict)
            and (
                not transport
                or str(master.get('type') or '').lower() == transport.lower()
            )
            for slave in master.get('slaves') or []
            if isinstance(slave, dict) and 'controller_index' in slave
        ]
        return sorted(set(axes))
    except (OSError, TypeError, ValueError, yaml.YAMLError):
        return []


def motion_id_sort_key(value: str) -> tuple[int, Any]:
    try:
        return (0, int(value))
    except ValueError:
        return (1, value)


def motion_graph_series(
    groups: Dict[str, List[Dict[str, Any]]],
    max_series: int = 12,
    max_points: int = 300,
) -> List[Dict[str, Any]]:
    series = []
    for motion_id in sorted(groups, key=motion_id_sort_key)[:max_series]:
        records = groups[motion_id]
        stride = max(1, int(math.ceil(len(records) / max_points)))
        points = [
            {
                'time_sec': record['time_sec'],
                'value': record['value'],
            }
            for record in records[::stride]
        ]
        series.append({
            'motion_id': motion_id,
            'points': points,
        })
    return series


def analyze_motion_json(content: str, *, include_records: bool) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        'json_valid': False,
        'format_valid': False,
        'valid': False,
        'message': 'not analyzed',
        'headers': [],
        'total_records': 0,
        'valid_records': 0,
        'motion_id_count': 0,
        'motion_ids': [],
        'time': {},
        'frame': {},
        'interpolation': {
            'period_sec': CONTROL_PERIOD_SEC,
            'required': False,
        },
        'errors': [],
        'warnings': [],
    }
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        rows, headers, source, text_error = motion_table.extract_rows_from_text(content)
        if not rows:
            result['message'] = f'invalid JSON: {exc}'
            result['errors'].append(str(exc))
            if text_error:
                result['errors'].append(text_error)
            return result
        result['message'] = 'motion data parsed from header/list rows'
        result['warnings'].append('strict JSON 형식은 아니지만 헤더+대괄호 행 형식으로 해석했습니다')
    else:
        result['json_valid'] = True
        rows, headers, source = motion_table.extract_rows(payload)
    result['format_valid'] = True
    result['headers'] = headers
    result['source'] = source
    result['total_records'] = len(rows)
    if not rows:
        result['message'] = 'motion data rows not found'
        result['errors'].append('motion data rows not found')
        return result

    parsed_records = []
    errors = result['errors']
    for index, row in enumerate(rows):
        parsed, error = motion_table.parse_row(row, headers)
        if error:
            if len(errors) < 50:
                errors.append(f'row {index + 1}: {error}')
            continue
        parsed['row_index'] = index
        parsed_records.append(parsed)

    result['valid_records'] = len(parsed_records)
    if not parsed_records:
        result['message'] = 'valid motion records not found'
        if not errors:
            errors.append('valid motion records not found')
        return result

    times_in_input = [record['time_sec'] for record in parsed_records]
    for previous, current in zip(times_in_input, times_in_input[1:]):
        if current + 1e-9 < previous:
            result['warnings'].append('time values are not monotonic in file order')
            break

    duplicate_pairs = set()
    duplicated_count = 0
    for record in parsed_records:
        key = (round(record['time_sec'], 9), record['motion_id'])
        if key in duplicate_pairs:
            duplicated_count += 1
        duplicate_pairs.add(key)
    if duplicated_count:
        result['warnings'].append(f'duplicate time/motion_id records: {duplicated_count}')

    sorted_records = sorted(
        parsed_records,
        key=lambda item: (item['time_sec'], str(item['motion_id']), item['row_index']),
    )
    time_values = [record['time_sec'] for record in sorted_records]
    frame_values = [record['frame'] for record in sorted_records]
    min_time = min(time_values)
    max_time = max(time_values)
    result['time'] = {
        'start_sec': min_time,
        'end_sec': max_time,
        'duration_sec': max_time - min_time,
        'unique_count': len(set(round(value, 9) for value in time_values)),
    }
    result['frame'] = {
        'min': min(frame_values),
        'max': max(frame_values),
        'unique_count': len(set(frame_values)),
    }

    groups: Dict[str, List[Dict[str, Any]]] = {}
    for record in sorted_records:
        groups.setdefault(str(record['motion_id']), []).append(record)

    motion_ids = []
    interpolation_required = False
    for motion_id in sorted(groups, key=motion_id_sort_key):
        records = groups[motion_id]
        values = [record['value'] for record in records]
        group_times = [record['time_sec'] for record in records]
        diffs = [
            group_times[index] - group_times[index - 1]
            for index in range(1, len(group_times))
        ]
        off_period = [
            diff for diff in diffs
            if abs(diff - CONTROL_PERIOD_SEC) > 0.001
        ]
        if off_period:
            interpolation_required = True
        motion_ids.append({
            'motion_id': motion_id,
            'count': len(records),
            'first_value': records[0]['value'],
            'last_value': records[-1]['value'],
            'min_value': min(values),
            'max_value': max(values),
            'first_time_sec': min(group_times),
            'last_time_sec': max(group_times),
            'period_sec_min': min(diffs) if diffs else None,
            'period_sec_max': max(diffs) if diffs else None,
            'requires_interpolation': bool(off_period),
        })

    unique_times = sorted(set(round(value, 9) for value in time_values))
    if unique_times:
        for value in unique_times:
            offset = (value - min_time) / CONTROL_PERIOD_SEC
            if abs(offset - round(offset)) > 0.001:
                interpolation_required = True
                break

    sample_count = 1
    duration = max_time - min_time
    if duration > 0.0:
        sample_count = int(math.floor(duration / CONTROL_PERIOD_SEC)) + 1
        last_sample_time = min_time + ((sample_count - 1) * CONTROL_PERIOD_SEC)
        if max_time - last_sample_time > 0.001:
            sample_count += 1

    result['motion_ids'] = motion_ids
    result['motion_id_count'] = len(motion_ids)
    result['interpolation'] = {
        'period_sec': CONTROL_PERIOD_SEC,
        'required': interpolation_required,
        'sample_count': sample_count,
        'estimated_record_count': sample_count * len(motion_ids),
        'method': 'linear',
    }
    if include_records:
        result['preview_records'] = sorted_records[:80]
        result['graph_series'] = motion_graph_series(groups)

    result['valid'] = len(errors) == 0
    result['message'] = 'motion data valid' if result['valid'] else 'motion data has errors'
    return result


def motion_file_entry(path: Path, *, include_detail: bool) -> Dict[str, Any]:
    stat = path.stat()
    entry: Dict[str, Any] = {
        'id': path.name,
        'filename': path.name,
        'path': str(path),
        'size_bytes': stat.st_size,
        'updated_at': stat.st_mtime,
    }
    try:
        content = path.read_text(encoding='utf-8')
        analysis = analyze_motion_json(content, include_records=include_detail)
    except OSError as exc:
        analysis = {
            'json_valid': False,
            'valid': False,
            'message': f'failed to read file: {exc}',
            'errors': [str(exc)],
            'warnings': [],
        }
        content = ''
    entry['analysis'] = analysis
    if include_detail:
        entry['content'] = content
        entry['content_preview'] = content[:12000]
    return entry
