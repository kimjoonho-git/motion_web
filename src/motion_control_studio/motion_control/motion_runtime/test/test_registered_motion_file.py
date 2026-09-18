"""재생 등록 칸만 따로 쓴다 · §6-160

**모션 데이터만 건드렸는데 모션축 설정 창이 떴다.**

모션축 매칭 파일 하나에 주인이 셋이다 — 모션축 설정(`mappings`),
MIDI 입력 설정(`midi_banks`), 재생 등록(`motion_file_id`).

MIDI 는 오래전에 제 길을 얻었는데 재생 등록만 「설정 전체 저장」 길로 다녔다 ·
그래서 모션 실행 화면에서 파일 하나 갈아 끼우려는 사람에게

    모션축 설정 저장 충돌
    저장된 모션축 설정과 이 화면이 기준으로 삼은 설정이 다릅니다.
    현재 편집 내용은 저장되지 않았습니다.

라는 창이 떴다 · 편집한 적도 없는 설정을 되돌릴지 물으니 알 수가 없다.

여기서 지키는 것 : **한 칸만 바뀐다 · 나머지는 글자 하나 안 움직인다.**
"""

import yaml

from motion_runtime.registered_motion_file import (
    load_registered_motion_file,
    render_with_registered_motion_file,
    save_registered_motion_file,
)

MAPPING = """file_id: 모션1.yaml
name: 모션1
motion_file_id: 옛파일.json
created_at: 1789546691.1
updated_at: 1789717025.4
mappings:
- motion_id: 1-1
  enabled: true
  motor_axis: 0
  motor_ref: ac_servo:master:0:alias:103
# motion-control-web: midi-banks start
midi_banks:
  version: 1
  active_bank_id: bank_1
# motion-control-web: midi-banks end
"""


def _write(tmp_path, text=MAPPING):
    path = tmp_path / '모션1.yaml'
    path.write_text(text, encoding='utf-8')
    return path


def test_only_the_registration_line_changes(tmp_path):
    """줄 하나만 갈린다 · 나머지 줄은 그대로여야 한다."""
    path = _write(tmp_path)

    save_registered_motion_file(path, '새파일.json', tmp_path / 'history')

    before = MAPPING.splitlines()
    after = path.read_text(encoding='utf-8').splitlines()
    changed = [
        (old, new) for old, new in zip(before, after) if old != new
    ]
    assert changed == [('motion_file_id: 옛파일.json', 'motion_file_id: 새파일.json')]
    assert len(before) == len(after)


def test_the_motion_axis_settings_are_untouched(tmp_path):
    """모션축 설정은 남의 것이다 · 손대면 안 된다."""
    path = _write(tmp_path)

    save_registered_motion_file(path, '새파일.json', tmp_path / 'history')

    root = yaml.safe_load(path.read_text(encoding='utf-8'))
    assert root['mappings'] == yaml.safe_load(MAPPING)['mappings']


def test_the_midi_block_survives(tmp_path):
    """MIDI 구간의 표시 주석까지 그대로 남아야 한다."""
    path = _write(tmp_path)

    save_registered_motion_file(path, '새파일.json', tmp_path / 'history')

    text = path.read_text(encoding='utf-8')
    assert '# motion-control-web: midi-banks start' in text
    assert '# motion-control-web: midi-banks end' in text
    assert yaml.safe_load(text)['midi_banks']['active_bank_id'] == 'bank_1'


def test_clearing_the_registration(tmp_path):
    """등록 해제 · 빈 값으로 남는다."""
    path = _write(tmp_path)

    save_registered_motion_file(path, '', tmp_path / 'history')

    assert load_registered_motion_file(path) == ''
    assert yaml.safe_load(path.read_text(encoding='utf-8'))['mappings']


def test_a_file_without_the_key_gets_one(tmp_path):
    """옛 파일에는 그 칸이 없을 수 있다 · 이름 뒤에 만들어 넣는다."""
    path = _write(tmp_path, 'file_id: 모션1.yaml\nname: 모션1\nmappings: []\n')

    save_registered_motion_file(path, '새파일.json', tmp_path / 'history')

    assert load_registered_motion_file(path) == '새파일.json'
    assert yaml.safe_load(path.read_text(encoding='utf-8'))['name'] == '모션1'


def test_writing_the_same_value_changes_nothing(tmp_path):
    """같은 값이면 파일도 백업도 안 만든다 · 쓸데없는 개정을 늘리지 않는다."""
    path = _write(tmp_path)

    backup = save_registered_motion_file(path, '옛파일.json', tmp_path / 'history')

    assert backup is None
    assert path.read_text(encoding='utf-8') == MAPPING


def test_the_previous_content_is_kept_as_history(tmp_path):
    """되돌릴 수 있어야 한다 · 다른 저장들과 같은 곳에 남긴다."""
    path = _write(tmp_path)
    history = tmp_path / 'history'

    backup = save_registered_motion_file(path, '새파일.json', history)

    assert backup is not None and backup.is_file()
    assert load_registered_motion_file(backup) == '옛파일.json'


def test_a_korean_name_survives_the_round_trip(tmp_path):
    """파일명이 한글이다 · 인코딩이 깨지면 등록이 통째로 어긋난다."""
    path = _write(tmp_path)

    save_registered_motion_file(path, '합친_레이어_테스트.json', tmp_path / 'history')

    assert load_registered_motion_file(path) == '합친_레이어_테스트.json'
    assert '합친_레이어_테스트.json' in path.read_text(encoding='utf-8')


def test_rendering_does_not_reformat_the_rest(tmp_path):
    """본문을 다시 뽑지 않는다 · 주석·줄바꿈·따옴표가 다 바뀌면 diff 가 못 쓰게 된다."""
    rendered = render_with_registered_motion_file(MAPPING, '새파일.json')

    assert rendered.count('# motion-control-web') == 2
    assert 'motor_ref: ac_servo:master:0:alias:103' in rendered
