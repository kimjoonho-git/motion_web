"""레이어·모션 파일이 마지막으로 바뀐 시각 · §6-91

레이어가 여러 개 쌓이면 어느 것이 방금 만든 것인지 사용자가 알 수 없다.

시각의 **주인은 하나다** · 레이어는 `save_project` 의 upsert 길목, 모션 파일은
파일시스템의 mtime. 수정 지점마다 찍게 하면 언젠가 누가 빠뜨리고, 빠뜨린 줄도
모른다.
"""

import time

import pytest

from motion_studio.motion_model import normalize_layer


def _layer(layer_id='a', **extra):
    return {
        'layer_id': layer_id,
        'frames': [{'frame': 1, 'time_sec': 0.02, 'values': {'1-1': 0.0}}],
        **extra,
    }


# --------------------------------------------------------------------- #
# 읽기로는 시각이 바뀌지 않는다
# --------------------------------------------------------------------- #

def test_a_new_layer_starts_with_both_times_equal():
    layer = normalize_layer(_layer(created_at=100.0))
    assert layer['created_at'] == 100.0
    assert layer['updated_at'] == 100.0, '만든 시각과 달라질 이유가 없다'


def test_reading_a_layer_never_moves_its_times():
    """`normalize_layer` 는 읽을 때도 불린다 · 여기서 현재 시각을 쓰면
    목록을 열어보기만 해도 전부 "방금 수정" 이 된다."""
    once = normalize_layer(_layer(created_at=100.0, updated_at=200.0))
    twice = normalize_layer(once)
    thrice = normalize_layer(twice)

    assert twice['updated_at'] == 200.0
    assert thrice['updated_at'] == 200.0
    assert thrice['created_at'] == 100.0


def test_an_old_project_without_the_field_still_makes_sense():
    """예전에 만든 레이어에는 이 칸이 없다 · 만든 시각을 그대로 쓴다."""
    layer = normalize_layer(_layer(created_at=100.0))
    assert layer['updated_at'] == layer['created_at']


# --------------------------------------------------------------------- #
# 저장 길목이 유일한 주인이다
# --------------------------------------------------------------------- #

@pytest.fixture
def store(tmp_path):
    from motion_studio.project_store import ProjectStore

    mappings = tmp_path / 'motion_axis_matching'
    mappings.mkdir(parents=True)
    (mappings / 'face.yaml').write_text(
        'mappings:\n'
        '- motion_id: 1-1\n'
        '  enabled: true\n'
        '  motor_axis: 0\n'
        '  motion_lower_deg: -20\n'
        '  motion_upper_deg: 20\n',
        encoding='utf-8',
    )
    return ProjectStore(tmp_path)


def _project(store, layer_ids):
    """실제 흐름대로 · 먼저 통째로 저장해 모두 자리를 잡은 뒤 돌려준다."""
    project = store.create_project('타임스탬프', 'face.yaml')
    project['layers'] = [
        _layer(layer_id, created_at=100.0) for layer_id in layer_ids
    ]
    return store.save_project(project)


def test_only_the_saved_layers_get_a_new_time(store, monkeypatch):
    """바꾼 레이어만 찍힌다 · 나머지를 함께 찍으면 "언제 바뀌었나" 가 뜻을 잃는다."""
    project = _project(store, ['a', 'b'])
    monkeypatch.setattr(time, 'time', lambda: 500.0)
    saved = store.save_project(project, upsert_layer_ids=['a'])

    by_id = {layer['layer_id']: layer for layer in saved['layers']}
    assert by_id['a']['updated_at'] == 500.0
    assert by_id['b']['updated_at'] == 100.0, '안 바꾼 레이어까지 찍혔다'


def test_a_project_wide_save_does_not_restamp_layers(store, monkeypatch):
    """프로젝트 이름만 바꿔도 모든 레이어가 "방금 수정" 이 되면 안 된다."""
    project = _project(store, ['a'])
    monkeypatch.setattr(time, 'time', lambda: 500.0)
    project = store.save_project(project, upsert_layer_ids=['a'])

    monkeypatch.setattr(time, 'time', lambda: 900.0)
    saved = store.save_project(project, upsert_layer_ids=[])

    assert saved['layers'][0]['updated_at'] == 500.0, '레이어를 안 건드렸는데 찍혔다'


def test_saving_again_moves_the_time_forward(store, monkeypatch):
    project = _project(store, ['a'])
    monkeypatch.setattr(time, 'time', lambda: 500.0)
    project = store.save_project(project, upsert_layer_ids=['a'])

    monkeypatch.setattr(time, 'time', lambda: 900.0)
    saved = store.save_project(project, upsert_layer_ids=['a'])

    assert saved['layers'][0]['updated_at'] == 900.0
    assert saved['layers'][0]['created_at'] == 100.0, '만든 시각까지 바뀌었다'


def test_only_the_store_stamps_a_layer_time():
    """수정 지점마다 찍게 하면 언젠가 누가 빠뜨리고, 빠뜨린 줄도 모른다 ·
    시각을 찍는 곳이 한 군데인지 본다."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / 'motion_studio'
    offenders = []
    for path in sorted(root.glob('*.py')):
        if path.name in {'project_store.py', 'motion_model.py'}:
            continue
        for line in path.read_text(encoding='utf-8').splitlines():
            if 'layer' not in line.lower():
                continue  # 상태 스냅샷의 시각 · 레이어와 다른 것이다
            if re.search(r"\['updated_at'\]\s*=|'updated_at':\s*time\.time", line):
                offenders.append(f'{path.name}: {line.strip()}')
    assert offenders == [], (
        '레이어 시각을 저장 길목 밖에서 찍는 곳:\n  ' + '\n  '.join(offenders)
    )


# --------------------------------------------------------------------- #
# 모션 파일은 파일시스템이 주인이다
# --------------------------------------------------------------------- #

def test_motion_files_report_when_they_changed(store, tmp_path):
    import json as json_module

    files_dir = store.files_dir
    files_dir.mkdir(parents=True, exist_ok=True)
    target = files_dir / 'sample.json'
    target.write_text(json_module.dumps({
        'title': 't', 'period_sec': 0.02,
        'frames': [{'frame': 1, 'time_sec': 0.02, 'values': {'1-1': 0.0}}],
    }), encoding='utf-8')

    listed = store.list_motion_files()

    assert listed, '파일을 찾지 못했다'
    assert listed[0]['updated_at'] == pytest.approx(target.stat().st_mtime)
