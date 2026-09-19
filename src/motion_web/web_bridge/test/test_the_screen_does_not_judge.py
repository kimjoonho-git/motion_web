"""「모터를 움직일 수 있나」는 서버가 답한다 · §6-171

**같은 판단이 브라우저에 통째로 또 있었다.**

`main.js` 의 `studioMotorActionBlockReason()` 이 긴급정지 · 안전 차단 ·
실행 컨텍스트 · 상태 수신 · 모니터링 · 모터 연결을 순서대로 보고 제 나름의
문구를 만들었다 · 「상태가 오래됐나」의 임계값까지 따로 들고 있었다.

    서버   받은 지 1.0초 → '모터 상태 수신이 중단되었습니다'
    화면   스냅샷 안 1.5초 → '최신 모터 상태를 확인할 수 없습니다'

같은 질문에 두 답이 있으면 언젠가 갈린다 · 버튼은 켜져 있는데 눌러 보면
서버가 거절하거나, 그 반대가 된다 · 사용자는 프로그램이 고장 났다고 본다.

오늘 겪은 버튼 사고가 같은 병이었다 · 화면이 제 나름대로 「편집 중」 표시를
세워 두고, 그 표시 때문에 되돌아갈 버튼까지 꺼졌다 · 서버는 멀쩡했다.
"""

import re
import threading
from pathlib import Path

import pytest

WORKSPACE = Path(__file__).resolve().parents[4]
MAIN_JS = WORKSPACE / 'src/motion_web/web_ui/static/js/main.js'
BRIDGE = WORKSPACE / 'src/motion_web/web_bridge/motion_web_bridge/bridge_node.py'


class _Bridge:
    """판단에 필요한 만큼만 · 노드를 띄우지 않는다."""

    def __init__(self, *, safety=None, context=None, runtime=''):
        from motion_web_bridge.bridge_node import MotionWebBridge

        self.motor_action_blocker = MotionWebBridge.motor_action_blocker.__get__(self)
        self._safety_status = dict(safety or {})
        self._safety_status_lock = threading.Lock()
        self._context = dict(context or {'ready': True})
        self._runtime = runtime
        self._execution_context = type(
            'Ctx', (), {'status': lambda _self, validate_files=True: self._context},
        )()
        self.motor_runtime_control_blocker = lambda: self._runtime


def _reason(**kwargs):
    return _Bridge(**kwargs).motor_action_blocker()


# --------------------------------------------------------------------------- #
# 서버가 답한다 · 순서까지
# --------------------------------------------------------------------------- #

def test_the_emergency_latch_comes_first():
    """긴급정지는 다른 무엇보다 먼저다 · 재시작 전에는 아무것도 안 된다."""
    reason = _reason(
        safety={'emergency_latched': True, 'commands_blocked': True, 'message': '다른 말'},
        context={'ready': False},
    )
    assert '긴급정지' in reason


def test_a_servo_alarm_speaks_with_its_own_words():
    reason = _reason(safety={'commands_blocked': True, 'message': '2축 과부하'})
    assert reason == '2축 과부하'


def test_a_servo_alarm_without_words_still_says_something():
    reason = _reason(safety={'commands_blocked': True})
    assert '서보 에러' in reason


def test_an_unapplied_context_blocks():
    """오늘 MIDI 가 안 되던 것이 이것이었다 · 장비 적용을 안 한 상태."""
    reason = _reason(context={'ready': False, 'message': '모터축 장비에 적용이 필요합니다'})
    assert reason == '모터축 장비에 적용이 필요합니다'


def test_the_runtime_answer_is_passed_through_last():
    """앞의 셋이 통과하면 기존 판단(모터 상태·연결)이 답한다."""
    assert _reason(runtime='실행할 모터축이 없습니다') == '실행할 모터축이 없습니다'


def test_nothing_wrong_means_no_reason():
    assert _reason() == ''


# --------------------------------------------------------------------------- #
# 화면은 받아쓰기만 한다
# --------------------------------------------------------------------------- #

def _screen_reason_body():
    text = MAIN_JS.read_text(encoding='utf-8')
    start = text.index('function studioMotorActionBlockReason()')
    return text[start:text.index('\n}', start)]


def test_the_screen_reads_the_answer_instead_of_deriving_it():
    body = _screen_reason_body()
    assert 'appState.motorActionBlocker' in body, (
        '화면이 서버의 답을 읽지 않습니다'
    )


@pytest.mark.parametrize('leftover', [
    'commands_blocked',
    'monitoring_enabled',
    'last_motor_status_at',
    'connection_state',
    'executionContext',
])
def test_the_screen_no_longer_re_derives_the_rules(leftover):
    """규칙 조각이 남아 있으면 언젠가 그쪽만 고쳐진다."""
    assert leftover not in _screen_reason_body(), (
        f'화면이 아직 {leftover} 로 직접 판단합니다'
    )


def test_no_threshold_is_left_in_the_screen():
    """임계값이 양쪽에 있으면 1.0 과 1.5 처럼 갈린다."""
    assert not re.search(r'>\s*1\.\d', _screen_reason_body()), (
        '화면에 시간 임계값이 남아 있습니다 · 판단은 서버가 합니다'
    )


def test_not_yet_received_is_not_treated_as_fine():
    """아직 못 받은 것을 「막힘 없음」 으로 읽으면 버튼이 먼저 살아난다."""
    body = _screen_reason_body()
    assert "typeof answered === 'string'" in body
    assert '아직 확인하지 못했습니다' in body


def test_the_screen_keeps_only_what_the_server_cannot_know():
    """모터 등록 중 신원 불일치는 아직 화면 안의 일이다 · 그것만 덧붙인다."""
    body = _screen_reason_body()
    assert 'motorIdentityBlockMessage' in body
    assert 'emergencyLatched' in body, (
        '긴급정지를 누른 직후는 화면이 먼저 안다 · 그 한 번은 앞세운다'
    )


# --------------------------------------------------------------------------- #
# 답이 실제로 화면까지 간다
# --------------------------------------------------------------------------- #

def test_the_answer_rides_along_with_the_status():
    source = BRIDGE.read_text(encoding='utf-8')
    assert "'motor_action_blocker': self.motor_action_blocker(" in source, (
        '서버가 답을 스냅샷에 싣지 않습니다 · 화면은 영영 못 받습니다'
    )


def test_the_screen_stores_what_it_received():
    text = MAIN_JS.read_text(encoding='utf-8')
    assert 'appState.motorActionBlocker = typeof payload?.motor_action_blocker' in text
    assert 'motorActionBlocker: null,' in text, (
        '처음 값이 없으면 `undefined` 를 「막힘 없음」 으로 읽을 수 있습니다'
    )
