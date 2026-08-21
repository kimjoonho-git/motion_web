"""모션 표 파서 단일 구현 검증.

표시 경로(web_bridge)와 실행 경로(motion_runtime)가 같은 입력에 같은 레코드를
내는지가 이 모듈의 존재 이유다.
"""

import json

from motion_common import motion_table


# --------------------------------------------------------------------------- #
# 컬럼 해석
# --------------------------------------------------------------------------- #

def test_column_key_normalizes_known_labels():
    assert motion_table.column_key('Frame') == 'frame'
    assert motion_table.column_key('time(sec)') == 'time'
    assert motion_table.column_key('time_sec') == 'time'
    assert motion_table.column_key('motion Id') == 'motion_id'
    assert motion_table.column_key('id') == 'motion_id'
    assert motion_table.column_key('angle(deg)') == 'value'
    assert motion_table.column_key('unknown column') == 'unknowncolumn'


def test_header_map_requires_all_four_columns():
    assert motion_table.header_map(['frame', 'time(sec)', 'motion Id', 'value']) == {
        'frame': 0,
        'time': 1,
        'motion_id': 2,
        'value': 3,
    }
    assert motion_table.header_map(['frame', 'time(sec)', 'motion Id']) == {}
    assert motion_table.header_map(None) == {}


def test_header_map_keeps_declared_column_order():
    mapping = motion_table.header_map(['value', 'motion Id', 'time(sec)', 'frame'])
    assert mapping == {'value': 0, 'motion_id': 1, 'time': 2, 'frame': 3}


# --------------------------------------------------------------------------- #
# 값 변환
# --------------------------------------------------------------------------- #

def test_finite_float_rejects_non_finite_and_blank():
    assert motion_table.finite_float('1.5') == 1.5
    assert motion_table.finite_float('') is None
    assert motion_table.finite_float(None) is None
    assert motion_table.finite_float('abc') is None
    assert motion_table.finite_float(float('inf')) is None


def test_motion_id_text_collapses_integral_floats():
    assert motion_table.motion_id_text(3) == '3'
    assert motion_table.motion_id_text(3.0) == '3'
    assert motion_table.motion_id_text('1-1') == '1-1'
    assert motion_table.motion_id_text(1.5) == '1.5'
    assert motion_table.motion_id_text(None) == ''


# --------------------------------------------------------------------------- #
# 텍스트 행 파싱
# --------------------------------------------------------------------------- #

def test_parse_text_row_accepts_bracket_and_csv_forms():
    assert motion_table.parse_text_row('[1, 0.02, "1-1", 3.5]') == [1, 0.02, '1-1', 3.5]
    assert motion_table.parse_text_row("[1, 0.02, '1-1', 3.5]") == [1, 0.02, '1-1', 3.5]
    assert motion_table.parse_text_row('1, 0.02, 1-1, 3.5') == ['1', '0.02', '1-1', '3.5']
    assert motion_table.parse_text_row('[') is None
    assert motion_table.parse_text_row('') is None


def test_parse_header_line_reads_motion_header_object():
    line = '{"title":"편집 가능 모션","type":"motion_header","fields":["frame","time_sec","id","value"]}'
    assert motion_table.parse_header_line(line) == ['frame', 'time_sec', 'id', 'value']


def test_parse_header_line_returns_empty_for_unreadable_line():
    assert motion_table.parse_header_line('nonsense') == []
    assert motion_table.parse_header_line('') == []


# --------------------------------------------------------------------------- #
# 행 확장
# --------------------------------------------------------------------------- #

def test_expand_pair_rows_splits_multi_pair_rows_without_header():
    rows = [[1, 0.02, '1-1', 3.5, '1-2', 4.5]]
    assert motion_table.expand_pair_rows(rows) == [
        [1, 0.02, '1-1', 3.5],
        [1, 0.02, '1-2', 4.5],
    ]


def test_expand_pair_rows_skips_when_header_order_differs():
    rows = [[3.5, '1-1', 0.02, 1, 'extra', 'extra']]
    headers = ['value', 'motion Id', 'time(sec)', 'frame']
    assert motion_table.expand_pair_rows(rows, headers) == rows


def test_expand_pair_rows_leaves_odd_length_rows_untouched():
    rows = [[1, 0.02, '1-1', 3.5, '1-2']]
    assert motion_table.expand_pair_rows(rows) == rows


# --------------------------------------------------------------------------- #
# 행 추출
# --------------------------------------------------------------------------- #

def test_extract_rows_from_array_with_header():
    payload = [['frame', 'time(sec)', 'motion Id', 'value'], [1, 0.02, '1-1', 3.5]]
    rows, headers, source = motion_table.extract_rows(payload)
    assert source == 'array_with_header'
    assert headers == ['frame', 'time(sec)', 'motion Id', 'value']
    assert rows == [[1, 0.02, '1-1', 3.5]]


def test_extract_rows_from_dict_data_key():
    payload = {'header': ['frame', 'time(sec)', 'motion Id', 'value'], 'data': [[1, 0.02, '1-1', 3.5]]}
    rows, headers, source = motion_table.extract_rows(payload)
    assert source == 'data'
    assert headers == ['frame', 'time(sec)', 'motion Id', 'value']
    assert rows == [[1, 0.02, '1-1', 3.5]]


def test_extract_rows_from_single_object_record():
    payload = {'frame': 1, 'time(sec)': 0.02, 'motion Id': '1-1', 'value': 3.5}
    rows, _headers, source = motion_table.extract_rows(payload)
    assert source == 'object'
    assert rows == [payload]


def test_extract_rows_reports_unknown_shape():
    assert motion_table.extract_rows('text') == ([], [], 'unknown')


def test_extract_rows_from_text_keeps_first_row_when_no_header():
    content = '[1, 0.02, "1-1", 3.5]\n[2, 0.04, "1-1", 4.0]\n'
    rows, headers, _source, warning = motion_table.extract_rows_from_text(content)
    assert headers == []
    assert warning == ''
    assert rows == [[1, 0.02, '1-1', 3.5], [2, 0.04, '1-1', 4.0]]


def test_extract_rows_from_text_consumes_valid_header():
    content = 'frame,time(sec),motion Id,value\n[1, 0.02, "1-1", 3.5]\n'
    rows, headers, _source, _warning = motion_table.extract_rows_from_text(content)
    assert headers == ['frame', 'time(sec)', 'motion Id', 'value']
    assert rows == [[1, 0.02, '1-1', 3.5]]


def test_extract_rows_from_text_ignores_comments_and_blank_lines():
    content = '# comment\n\nframe,time(sec),motion Id,value\n[1, 0.02, "1-1", 3.5]\n'
    rows, headers, _source, _warning = motion_table.extract_rows_from_text(content)
    assert headers == ['frame', 'time(sec)', 'motion Id', 'value']
    assert rows == [[1, 0.02, '1-1', 3.5]]


def test_extract_rows_from_content_prefers_strict_json():
    content = json.dumps({'data': [[1, 0.02, '1-1', 3.5]]})
    rows, _headers, source, _warning = motion_table.extract_rows_from_content(content)
    assert source == 'data'
    assert rows == [[1, 0.02, '1-1', 3.5]]


# --------------------------------------------------------------------------- #
# 레코드 파싱
# --------------------------------------------------------------------------- #

def test_parse_row_from_list_uses_positional_fallback():
    record, error = motion_table.parse_row([1, 0.02, '1-1', 3.5])
    assert error == ''
    assert record == {'frame': 1, 'time_sec': 0.02, 'motion_id': '1-1', 'value': 3.5}


def test_parse_row_from_dict_matches_by_column_name():
    record, error = motion_table.parse_row(
        {'Frame': 1, 'time_sec': 0.02, 'id': 2.0, 'angle(deg)': 3.5}
    )
    assert error == ''
    assert record == {'frame': 1, 'time_sec': 0.02, 'motion_id': '2', 'value': 3.5}


def test_parse_row_rejects_invalid_records():
    assert motion_table.parse_row(['x', 0.02, '1-1', 3.5])[1] == 'frame must be numeric'
    assert motion_table.parse_row([1, 'x', '1-1', 3.5])[1] == 'time(sec) must be numeric'
    assert motion_table.parse_row([1, -0.1, '1-1', 3.5])[1].startswith('time(sec) must be greater')
    assert motion_table.parse_row([1, 0.02, '', 3.5])[1] == 'motion Id is required'
    assert motion_table.parse_row([1, 0.02, '1-1', 'x'])[1] == 'value must be numeric'
    assert motion_table.parse_row([1, 0.02])[1] == 'required columns not found'
    assert motion_table.parse_row('text')[1] == 'record must be an object or array'


def test_parse_rows_skips_invalid_and_indexes_survivors():
    rows = [[1, 0.02, '1-1', 3.5], ['bad'], [2, 0.04, '1-1', 4.0]]
    records = motion_table.parse_rows(rows)
    assert [record['row_index'] for record in records] == [0, 2]


# --------------------------------------------------------------------------- #
# 표시 경로 · 실행 경로 동치
# --------------------------------------------------------------------------- #

def test_display_and_runtime_paths_agree_on_multi_pair_text_file():
    """표시용 검증과 실행용 재생이 같은 레코드를 얻어야 한다."""
    content = (
        '{"type":"motion_header","fields":["frame","time_sec","id","value"]}\n'
        '[1, 0.02, "1-1", 3.5, "1-2", 10.0]\n'
        '[2, 0.04, "1-1", 4.0, "1-2", 11.0]\n'
    )

    rows, headers, _source, _warning = motion_table.extract_rows_from_content(content)
    records = motion_table.parse_rows(rows, headers)

    assert [(r['frame'], r['motion_id'], r['value']) for r in records] == [
        (1, '1-1', 3.5),
        (1, '1-2', 10.0),
        (2, '1-1', 4.0),
        (2, '1-2', 11.0),
    ]
