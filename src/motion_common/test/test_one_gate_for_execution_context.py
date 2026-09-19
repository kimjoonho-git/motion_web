"""실행 컨텍스트를 들여보내는 문도 하나다 · §6-195

**노드 셋이 같은 판정을 따로 들고 있었고, 하나가 달랐다.**

브리지가 「이 설정으로 일해라」를 쏘면 각 노드는 받은 모션축 설정 파일이
정말 그 파일인지 sha256 으로 본다.

    스튜디오      if actual != expected:                 항상 검사
    모션축 매핑    expected 없으면 「필요합니다」로 거부      항상 검사
    MIDI         if expected and actual != expected:    **비면 건너뜀**

MIDI 만 지문이 비어 오면 그냥 통과했다 · 게다가 매핑 파일이 **아예 없으면**
`actual` 도 빈 문자열이라 파일 없이도 「확인 완료」로 답했다.

**이것이 실제로 닿는 길이었다** · `test_midi_control_node.py` 의 기존 시험이
지문 없이 `select_project` 를 보내고 성공을 기대하고 있었다 · 고치자마자
그 시험이 깨졌다 · 옛 동작을 그대로 적어 둔 시험이었던 셈이다.

매핑이 틀리면 **엉뚱한 축이 움직인다** · 지문이 없다는 것은 「검사하지
말라」가 아니라 「보낸 쪽이 무엇을 주는지 모른다」는 뜻이다.
"""

import hashlib
import re
from pathlib import Path

import pytest

from motion_common.execution_context import (
    CONTEXT_MISMATCH,
    MAPPING_FILE_MISSING,
    MAPPING_FINGERPRINT_MISMATCH,
    MAPPING_FINGERPRINT_REQUIRED,
    confirm_context_id,
    verify_mapping_fingerprint,
)


@pytest.fixture
def mapping(tmp_path):
    path = tmp_path / 'mapping.yaml'
    path.write_text('mappings: []\n', encoding='utf-8')
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


# --------------------------------------------------------------------------- #
# 지문 · MIDI 만 건너뛰던 그 검사
# --------------------------------------------------------------------------- #

def test_a_matching_file_passes(mapping):
    path, sha = mapping

    assert verify_mapping_fingerprint(path, sha) == sha


@pytest.mark.parametrize('nothing', [None, '', '   '])
def test_no_fingerprint_is_a_refusal_not_a_free_pass(nothing, mapping):
    """**이것이 그 구멍이다** · MIDI 는 여기서 통과시켰다."""
    path, _sha = mapping

    with pytest.raises(ValueError, match=re.escape(MAPPING_FINGERPRINT_REQUIRED)):
        verify_mapping_fingerprint(path, nothing)


def test_no_file_and_no_fingerprint_is_still_a_refusal():
    """가장 나쁜 경우 · 파일도 없고 지문도 없는데 「확인 완료」라고 답했다."""
    with pytest.raises(ValueError, match=re.escape(MAPPING_FINGERPRINT_REQUIRED)):
        verify_mapping_fingerprint(None, '')


def test_a_fingerprint_without_a_file_is_refused(tmp_path):
    with pytest.raises(ValueError, match=MAPPING_FILE_MISSING):
        verify_mapping_fingerprint(tmp_path / '없는파일.yaml', 'a' * 64)

    with pytest.raises(ValueError, match=MAPPING_FILE_MISSING):
        verify_mapping_fingerprint(None, 'a' * 64)


def test_a_changed_file_is_caught(mapping):
    """누가 파일을 고쳤다 · 그대로 쓰면 엉뚱한 축이 움직인다."""
    path, sha = mapping
    path.write_text('mappings: [{axis: 9}]\n', encoding='utf-8')

    with pytest.raises(ValueError, match=MAPPING_FINGERPRINT_MISMATCH):
        verify_mapping_fingerprint(path, sha)


# --------------------------------------------------------------------------- #
# 컨텍스트 확인 · 셋 다 같았지만 세 벌이었다
# --------------------------------------------------------------------------- #

def test_the_same_context_is_confirmed():
    applied = {'context_id': 'ctx-1'}

    assert confirm_context_id(applied, {'context_id': 'ctx-1'}) == 'ctx-1'


@pytest.mark.parametrize('payload', [
    {'context_id': 'ctx-2'},
    {'context_id': ''},
    {},
    None,
])
def test_a_different_context_is_refused(payload):
    """적용한 뒤 한 번 더 묻는 사이에 프로젝트가 바뀌었을 수 있다."""
    with pytest.raises(ValueError, match=CONTEXT_MISMATCH):
        confirm_context_id({'context_id': 'ctx-1'}, payload)


def test_an_empty_applied_context_confirms_nothing():
    """아직 아무것도 적용 안 했는데 확인부터 오면 거부한다."""
    with pytest.raises(ValueError, match=CONTEXT_MISMATCH):
        confirm_context_id({}, {'context_id': 'ctx-1'})


# --------------------------------------------------------------------------- #
# 아무도 제 판정을 따로 들고 있지 않다
# --------------------------------------------------------------------------- #

SRC = Path(__file__).resolve().parents[2]

CALLERS = {
    'motion_control_studio/motion_studio/motion_studio/workspace_session.py':
        ['verify_mapping_fingerprint(', 'confirm_context_id('],
    'motion_control_studio/motion_control/motion_runtime/motion_runtime/motion_mapping_manager.py':
        ['verify_mapping_fingerprint('],
    'motion_control_studio/motion_control/motion_runtime/motion_runtime/motion_run_manager.py':
        ['confirm_context_id('],
    'motion_control_studio/motion_control/midi_control/midi_control/midi_control_node.py':
        ['verify_mapping_fingerprint(', 'confirm_context_id('],
}


@pytest.mark.parametrize('name, expected', sorted(CALLERS.items()))
def test_nobody_keeps_a_copy(name, expected):
    source = (SRC / name).read_text(encoding='utf-8')

    for call in expected:
        assert call in source, f'{call} 공용 문을 쓰지 않습니다'

    # 판정 문구를 제가 들고 있으면 제 판정을 한다는 뜻이다
    assert "raise ValueError('확인하려는 실행 컨텍스트가" not in source
    assert "raise ValueError('모션축 설정 파일 버전이" not in source
