"""재생 등록은 모션축 설정과 남남이다 · §6-160

**모션 데이터만 건드렸는데 모션축 설정 창이 떴다.**

    모션축 설정 저장 충돌
    저장된 모션축 설정과 이 화면이 기준으로 삼은 설정이 다릅니다.
    현재 편집 내용은 저장되지 않았습니다.

모션 실행 화면에서 `재생 등록` 을 눌렀을 뿐인데 이런 창이 떴다 · 편집한 적도
없는 설정을 되돌릴지 물으니 무슨 말인지 알 수가 없다.

모션축 매칭 파일 하나에 **주인이 셋**이기 때문이었다.

    mappings        모션축 설정 화면
    midi_banks      MIDI 입력 설정 화면
    motion_file_id  모션 실행 화면 (재생 등록)

MIDI 는 오래전에 제 길을 얻었는데(`save_midi_banks`) 재생 등록만
「설정 전체 저장」 길로 다녔다 · 그 길에는 두 가지가 딸려 있다 ·
편집 중인 설정까지 같이 저장되고, 설정 개정 검사에 걸린다.

곁가지로 버튼까지 죽었다 · 저장 전에 「편집 중」 표시를 세워 놓고 저장에
실패하면 그 표시가 남았는데, 두 버튼의 비활성 조건에 그 표시가 들어 있었다 ·
프로그램이 제 손으로 세운 표시가 제 발을 묶어 되돌아갈 길이 사라졌다.

여기서 지키는 것 : **셋은 서로 상관하지 않는다.**
"""

from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[4]
UI = WORKSPACE / 'src/motion_web/web_ui/static/js'
MOTION_DATA = UI / 'motion_data.js'
API = UI / 'api.js'
MANAGER = (
    WORKSPACE
    / 'src/motion_control_studio/motion_control/motion_runtime/motion_runtime'
    / 'motion_mapping_manager.py'
)
ROUTES = (
    WORKSPACE
    / 'src/motion_web/web_bridge/motion_web_bridge/routes/motion_run_routes.py'
)
BRIDGE = WORKSPACE / 'src/motion_web/web_bridge/motion_web_bridge/bridge_node.py'


def _function(source: str, name: str) -> str:
    """`name` 함수의 몸통 · 들여쓰기가 돌아올 때까지."""
    start = source.index(f'function {name}(')
    line_start = source.rindex('\n', 0, start) + 1
    indent = len(source[line_start:start]) - len(source[line_start:start].lstrip())
    lines = source[line_start:].splitlines()
    body = [lines[0]]
    for line in lines[1:]:
        body.append(line)
        if line.strip() and line.startswith(' ' * indent + '}'):
            break
    return '\n'.join(body)


# --------------------------------------------------------------------------- #
# 버튼 · 모션축 설정을 편집 중이어도 눌린다
# --------------------------------------------------------------------------- #

def test_the_buttons_do_not_watch_the_mapping_draft():
    """`mappingDirty` 로 끄면 안 된다 · 모션 데이터와 상관없는 사정이다.

    이것이 막다른 길의 입구였다 · 등록이 한 번 실패하면 프로그램이 세운
    표시 때문에 등록도 해제도 영영 안 눌렸다.
    """
    source = MOTION_DATA.read_text(encoding='utf-8')
    for button in ('registerMotionFileButton', 'unregisterMotionFileButton'):
        block = source[source.index(f'el.{button}.disabled'):][:260]
        assert 'mappingDirty' not in block, (
            f'{button} 이 모션축 설정 편집 상태를 보고 있습니다 · '
            '모션 데이터와 상관없는 사정으로 버튼이 꺼집니다'
        )


def test_registering_does_not_refuse_while_the_mapping_is_being_edited():
    """진입에서도 막지 않는다 · 버튼만 살리고 안에서 막으면 같은 일이다."""
    source = MOTION_DATA.read_text(encoding='utf-8')
    for name in ('registerSelectedMotionFile', 'unregisterSelectedMotionFile'):
        body = _function(source, name)
        assert 'if (mappingDirty)' not in body, (
            f'{name} 이 모션축 설정 편집 중이라고 되돌려 보냅니다'
        )


# --------------------------------------------------------------------------- #
# 저장 경로 · 설정 전체를 보내지 않는다
# --------------------------------------------------------------------------- #

def test_registering_uses_its_own_narrow_path():
    """모션축 설정 전체 저장(`saveMotionMapping`)을 타면 안 된다.

    그 길을 타면 편집 중인 설정까지 같이 저장되고, 개정 검사에 걸려
    「모션축 설정 저장 충돌」 창이 뜬다.
    """
    body = _function(MOTION_DATA.read_text(encoding='utf-8'), 'applyMotionFileRegistration')

    assert 'saveRegisteredMotionFile(' in body, (
        '재생 등록이 제 길(saveRegisteredMotionFile)을 쓰지 않습니다'
    )
    assert 'saveCurrentMapping(' not in body, (
        '재생 등록이 모션축 설정 전체 저장을 타고 있습니다'
    )


def test_registering_keeps_the_mapping_draft_alone():
    """편집 중인 모션축 설정은 건드리지도, 저장하지도 않는다."""
    body = _function(MOTION_DATA.read_text(encoding='utf-8'), 'applyMotionFileRegistration')

    assert 'markMappingDirty()' not in body, (
        '재생 등록이 모션축 설정을 「편집 중」 으로 표시합니다'
    )
    assert 'mappingDraft.mappings' not in body


def test_the_narrow_path_exists_end_to_end():
    """화면 · 경로 · 브리지 · 노드가 모두 이어져 있어야 한다."""
    assert "'/api/motion-mappings/motion-file'" in API.read_text(encoding='utf-8')
    assert "@app.post('/api/motion-mappings/motion-file')" in ROUTES.read_text(encoding='utf-8')
    assert 'def save_registered_motion_file(' in BRIDGE.read_text(encoding='utf-8')
    assert "router.register('save_motion_file'" in MANAGER.read_text(encoding='utf-8')


# --------------------------------------------------------------------------- #
# 개정 번호 · 제 주인의 것만 센다
# --------------------------------------------------------------------------- #

def test_the_revision_ignores_the_registration():
    """재생 등록이 바뀌었다고 모션축 설정이 바뀐 것으로 세면 안 된다.

    **글자가 아니라 값을 잰다** · §6-242

    전에는 이 시험이 소스에서 `key != 'motion_file_id'` 라는 **글자**를
    찾았다 · 그래서 빼는 칸을 하나 더 늘려 고쳤더니, 동작은 더 맞아졌는데
    시험이 깨졌다 · 재는 것이 틀렸던 것이다 · 이제 함수를 직접 불러 본다.
    """
    import sys
    sys.path.insert(0, str(MANAGER.parent.parent))
    from motion_runtime.motion_mapping_manager import MotionMappingManager

    base = {
        'file_id': 'show.yaml',
        'name': 'show',
        'motion_file_id': '',
        'mappings': [{'motion_id': '1-1', 'enabled': True, 'motor_axis': 0}],
    }

    assert MotionMappingManager._mapping_revision(dict(base)) == (
        MotionMappingManager._mapping_revision({**base, 'motion_file_id': 'show.json'})
    ), '개정 번호가 아직 재생 등록을 센다 · 모션 파일만 바꿔도 창이 뜬다'

    assert MotionMappingManager._mapping_revision(dict(base)) == (
        MotionMappingManager._mapping_revision({**base, 'midi_banks': {'version': 1}})
    ), '개정 번호가 아직 MIDI 뱅크를 센다 · 저장 한 번 뒤 다음 저장이 막힌다'

    assert MotionMappingManager._mapping_revision(dict(base)) != (
        MotionMappingManager._mapping_revision({
            **base, 'mappings': [{'motion_id': '1-1', 'enabled': True, 'motor_axis': 1}],
        })
    ), '모션축 설정을 고쳤는데 개정 번호가 그대로다'


def test_running_motion_still_blocks_the_swap():
    """도는 중에 재생 파일이 바뀌면 다음 회차가 무엇을 돌지 알 수 없다.

    풀어야 할 것은 「모션축 설정 편집 중」 이지 「모션이 도는 중」 이 아니다.
    """
    source = BRIDGE.read_text(encoding='utf-8')
    start = source.index('def save_registered_motion_file(')
    body = source[start:source.index('def save_motion_mapping(', start)]

    assert 'change_blocker()' in body, (
        '모션이 도는 중에도 재생 파일을 갈아 끼울 수 있습니다'
    )
