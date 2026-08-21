"""모션 표 파서 단일 구현.

표시용(web_bridge 검증)과 실행용(motion_runtime 재생)이 각각 파서를 들고 있으면
화면에 보이는 값과 실제 모터 목표값이 갈라진다. 두 경로 모두 이 모듈을 경유해
동일한 행 집합·동일한 레코드를 얻는다.

레코드 형식 · ``{'frame': int, 'time_sec': float, 'motion_id': str, 'value': float}``
"""

from __future__ import annotations

import ast
import json
import math
from typing import Any, Dict, List, Optional, Tuple

REQUIRED_COLUMNS: Tuple[str, ...] = ('frame', 'time', 'motion_id', 'value')

DATA_KEYS: Tuple[str, ...] = (
    'data',
    'rows',
    'records',
    'motion_data',
    'motions',
    'frames',
    'values',
)

HEADER_KEYS: Tuple[str, ...] = ('header', 'headers', 'columns')

MISSING_HEADER_MESSAGE = 'required header not found: frame, time(sec), motion Id, value'


# --------------------------------------------------------------------------- #
# 값 변환
# --------------------------------------------------------------------------- #

def finite_float(value: Any) -> Optional[float]:
    """유한 실수로 변환한다. 불가하면 None."""
    if value is None or value == '':
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def motion_id_text(value: Any) -> str:
    """모션 ID를 문자열로 정규화한다.

    숫자 3.0과 문자열 '3'이 서로 다른 그룹으로 갈리지 않도록 정수형 실수는
    소수점을 떼고 표기한다.
    """
    if value is None:
        return ''
    if isinstance(value, bool):
        return str(value).strip()
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        number = float(value)
        return str(int(number)) if math.isclose(number, round(number)) else str(number)
    return str(value).strip()


# --------------------------------------------------------------------------- #
# 컬럼 해석
# --------------------------------------------------------------------------- #

def column_key(label: Any) -> str:
    """컬럼 표기를 정규 키로 환산한다."""
    compact = ''.join(char for char in str(label).lower() if char.isalnum())
    if compact in ('frame', 'frameid', 'frameindex'):
        return 'frame'
    if compact in ('time', 'times', 'timesec', 'seconds', 'sec', 'timestamp'):
        return 'time'
    if compact in ('motionid', 'motion', 'id', 'jointid', 'channelid'):
        return 'motion_id'
    if compact in ('value', 'angle', 'angledeg', 'deg', 'position', 'positiondeg'):
        return 'value'
    return compact


def column_value(row: Dict[str, Any], target: str) -> Any:
    """dict 행에서 정규 키에 해당하는 값을 찾는다."""
    for key, value in row.items():
        if column_key(key) == target:
            return value
    return None


def header_map(headers: Any) -> Dict[str, int]:
    """헤더 목록을 ``{정규키: 인덱스}``로 환산한다.

    필수 4개 컬럼이 모두 잡히지 않으면 빈 dict를 돌려준다.
    """
    if not isinstance(headers, (list, tuple)):
        return {}
    mapping: Dict[str, int] = {}
    for index, header in enumerate(headers):
        key = column_key(header)
        if key in REQUIRED_COLUMNS and key not in mapping:
            mapping[key] = index
    return mapping if all(key in mapping for key in REQUIRED_COLUMNS) else {}


def header_has_required(headers: Any) -> bool:
    """헤더가 필수 4개 컬럼을 모두 담고 있는지 판정한다."""
    return bool(header_map(headers))


# --------------------------------------------------------------------------- #
# 텍스트 행 파싱
# --------------------------------------------------------------------------- #

def _literal_list(text: str) -> Optional[List[Any]]:
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        try:
            parsed = ast.literal_eval(text)
        except (SyntaxError, ValueError):
            parsed = None
    return parsed if isinstance(parsed, list) else None


def _literal_object(text: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        try:
            parsed = ast.literal_eval(text)
        except (SyntaxError, ValueError):
            parsed = {}
    return parsed if isinstance(parsed, dict) else {}


def parse_text_row(line: str) -> Optional[List[Any]]:
    """한 줄을 값 목록으로 파싱한다.

    대괄호 리터럴 행과 쉼표/탭 구분 행을 모두 받는다. 두 형식 중 하나만 받으면
    한쪽 파서는 통과하고 다른 쪽은 행을 통째로 버리는 불일치가 생긴다.
    """
    text = str(line).strip().rstrip(',').strip()
    if not text or text in ('[', ']'):
        return None
    if text.startswith('[') and text.endswith(']'):
        parsed = _literal_list(text)
        if parsed is not None:
            return parsed
        text = text[1:-1]
    if ',' in text:
        return [part.strip().strip('"\'') for part in text.split(',')]
    if '\t' in text:
        return [part.strip().strip('"\'') for part in text.split('\t')]
    return None


def parse_header_line(line: str) -> List[str]:
    """헤더 줄을 컬럼 목록으로 파싱한다.

    ``{"fields": [...]}`` 형태의 모션 헤더 객체, 대괄호 리터럴, 쉼표/탭 구분을
    모두 받는다. 어느 쪽으로도 읽히지 않으면 빈 목록을 돌려주며, 호출자는 이를
    '헤더 없음'으로 처리해야 한다.
    """
    text = str(line).strip().strip('\ufeff').rstrip(',').strip()
    if not text:
        return []
    if text.startswith('{') and text.endswith('}'):
        fields = _literal_object(text).get('fields')
        if isinstance(fields, list):
            return [str(item).strip() for item in fields]
        return []
    parsed = parse_text_row(text)
    if parsed is not None:
        return [str(item).strip() for item in parsed]
    lowered = text.lower()
    if all(token in lowered for token in ('frame', 'time', 'motion', 'value')):
        return ['frame', 'time(sec)', 'motion Id', 'value']
    return []


# --------------------------------------------------------------------------- #
# 행 확장
# --------------------------------------------------------------------------- #

def expand_pair_rows(rows: List[Any], headers: Any = None) -> List[Any]:
    """``[frame, time, id1, v1, id2, v2, ...]`` 행을 4열 행으로 펼친다.

    헤더가 없으면 위치 기준(0,1,2,3)으로 간주해 펼친다. 헤더가 있으면서 순서가
    ``frame, time, motion_id, value``가 아니면 확장 규칙이 성립하지 않으므로
    원본을 그대로 돌려준다.
    """
    mapping = header_map(headers)
    if mapping and (
        mapping.get('frame') != 0
        or mapping.get('time') != 1
        or mapping.get('motion_id') != 2
        or mapping.get('value') != 3
    ):
        return rows

    expanded: List[Any] = []
    changed = False
    for row in rows:
        if not isinstance(row, list) or len(row) <= 4:
            expanded.append(row)
            continue
        if (len(row) - 2) % 2 != 0:
            expanded.append(row)
            continue
        frame = row[0]
        time_sec = row[1]
        for index in range(2, len(row), 2):
            expanded.append([frame, time_sec, row[index], row[index + 1]])
        changed = True
    return expanded if changed else rows


# --------------------------------------------------------------------------- #
# 행 추출
# --------------------------------------------------------------------------- #

def _payload_headers(payload: Dict[str, Any]) -> List[str]:
    for key in HEADER_KEYS:
        headers = payload.get(key)
        if isinstance(headers, list):
            return [str(item) for item in headers]
    return []


def extract_rows(payload: Any) -> Tuple[List[Any], List[str], str]:
    """디코딩된 JSON 페이로드에서 ``(행, 헤더, 출처)``를 뽑는다."""
    if isinstance(payload, list):
        if payload and isinstance(payload[0], list):
            possible_header = [str(item) for item in payload[0]]
            if header_has_required(possible_header):
                return (
                    expand_pair_rows(payload[1:], possible_header),
                    possible_header,
                    'array_with_header',
                )
        return expand_pair_rows(payload), [], 'array'

    if isinstance(payload, dict):
        headers = _payload_headers(payload)
        for key in DATA_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                if value and isinstance(value[0], list):
                    possible_header = [str(item) for item in value[0]]
                    if header_has_required(possible_header):
                        return (
                            expand_pair_rows(value[1:], possible_header),
                            possible_header,
                            f'{key}_with_header',
                        )
                return expand_pair_rows(value, headers), headers, key
        if all(column_value(payload, name) is not None for name in REQUIRED_COLUMNS):
            return [payload], headers, 'object'
    return [], [], 'unknown'


def extract_rows_from_text(content: str) -> Tuple[List[Any], List[str], str, str]:
    """헤더+행 텍스트 형식에서 ``(행, 헤더, 출처, 경고문)``을 뽑는다.

    첫 줄이 유효한 헤더로 읽히면 헤더로 쓰고 나머지를 데이터로 본다. 헤더로
    읽히지 않으면 첫 줄도 데이터로 본다 — 헤더 없는 파일의 첫 행을 잃지 않는다.
    """
    lines = [
        line.strip()
        for line in content.splitlines()
        if line.strip() and not line.strip().startswith('#')
    ]
    if not lines:
        return [], [], 'text', 'text format requires at least one data row'

    candidate = parse_header_line(lines[0])
    if header_has_required(candidate):
        headers = candidate
        data_lines = lines[1:]
    else:
        headers = []
        data_lines = lines

    rows: List[Any] = []
    skipped = 0
    for line in data_lines:
        row = parse_text_row(line)
        if row is None:
            skipped += 1
            continue
        rows.append(row)

    if not rows:
        return [], headers, 'text_header_list_rows', 'bracket data rows not found'
    rows = expand_pair_rows(rows, headers)
    warning = f'skipped non-data lines: {skipped}' if skipped else ''
    return rows, headers, 'text_header_list_rows', warning


def extract_rows_from_content(content: str) -> Tuple[List[Any], List[str], str, str]:
    """파일 본문에서 ``(행, 헤더, 출처, 경고문)``을 뽑는다.

    엄격한 JSON을 먼저 시도하고, 실패하면 헤더+행 텍스트 형식으로 해석한다.
    """
    try:
        payload = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return extract_rows_from_text(content)
    rows, headers, source = extract_rows(payload)
    return rows, headers, source, ''


# --------------------------------------------------------------------------- #
# 레코드 파싱
# --------------------------------------------------------------------------- #

def parse_row(row: Any, headers: Any = None) -> Tuple[Optional[Dict[str, Any]], str]:
    """한 행을 레코드로 환산한다. 실패하면 ``(None, 사유)``."""
    if isinstance(row, dict):
        frame = column_value(row, 'frame')
        time_sec = column_value(row, 'time')
        motion_id = column_value(row, 'motion_id')
        value = column_value(row, 'value')
    elif isinstance(row, list):
        mapping = header_map(headers)
        if not mapping and len(row) >= 4:
            mapping = {'frame': 0, 'time': 1, 'motion_id': 2, 'value': 3}
        try:
            frame = row[mapping['frame']]
            time_sec = row[mapping['time']]
            motion_id = row[mapping['motion_id']]
            value = row[mapping['value']]
        except (KeyError, IndexError):
            return None, 'required columns not found'
    else:
        return None, 'record must be an object or array'

    frame_value = finite_float(frame)
    time_value = finite_float(time_sec)
    value_number = finite_float(value)
    motion_text = motion_id_text(motion_id)

    if frame_value is None:
        return None, 'frame must be numeric'
    if time_value is None:
        return None, 'time(sec) must be numeric'
    if time_value < 0:
        return None, 'time(sec) must be greater than or equal to 0'
    if not motion_text:
        return None, 'motion Id is required'
    if value_number is None:
        return None, 'value must be numeric'

    return {
        'frame': int(round(frame_value)),
        'time_sec': float(time_value),
        'motion_id': motion_text,
        'value': float(value_number),
    }, ''


def parse_rows(rows: List[Any], headers: Any = None) -> List[Dict[str, Any]]:
    """행 목록을 레코드 목록으로 환산한다. 실패한 행은 건너뛰고 ``row_index``를 붙인다."""
    records: List[Dict[str, Any]] = []
    for index, row in enumerate(rows):
        record, error = parse_row(row, headers)
        if error:
            continue
        record['row_index'] = index
        records.append(record)
    return records
