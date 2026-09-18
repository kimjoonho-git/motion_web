"""연동 화면이 매초 제 버튼을 부수지 않는다 · §6-162

**명단 버튼이 눌러도 안 먹는다고 했다.**

연동 화면은 1초마다 상태를 받아 통째로 다시 그린다 · 그런데 그 표 안에는
누를 것이 들어 있다 — `명단 등록` · `명단 제외` · MIDI 대상 고르기.

`innerHTML` 로 갈아 끼우면 그 버튼은 **사라졌다 새로 생긴다** · 누름과 뗌
사이에 갈리면 클릭이 통째로 사라져 아무 일도 안 일어난다 · 손을 올려 둔
표시도 매초 풀리고 표가 깜빡인다.

1초 전과 글자 하나 안 다른 때가 대부분이다 · 그러니 달라졌을 때만 갈아
끼운다.

그리고 명단을 바꾸면 연동 서비스가 재시작하는데, 전에는 **무조건 2초를
잤다** · PC 하나 빼는 일에도 화면이 2초 넘게 굳었다 · 상한은 그대로 두고
끝난 것을 확인하면 바로 나온다.
"""

import re
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[4]
COORDINATION = WORKSPACE / 'src/motion_web/web_ui/static/js/coordination.js'


def _source() -> str:
    return COORDINATION.read_text(encoding='utf-8')


def test_the_panel_still_polls_every_second():
    """이 시험이 지키려는 상황이 실재하는지 · 폴링이 사라지면 전제가 바뀐다."""
    assert re.search(r'setInterval\(refresh,\s*1000\)', _source()), (
        '1초 폴링이 없어졌습니다 · 이 시험들의 전제를 다시 보세요'
    )


def test_nothing_is_rewritten_blindly_every_tick():
    """`innerHTML =` 를 그대로 쓰면 매초 부서진다 · `setHtml` 을 지나야 한다."""
    source = _source()
    # `setHtml` 안의 대입은 이 규칙의 **유일한 출구**다 · 거기서만 실제로 쓴다
    guard_start = source.index('function setHtml(')
    guard_end = source.index('function render()', guard_start)

    offenders = []
    for match in re.finditer(r'^\s*(?:el\.)?(\w+)\.innerHTML\s*=', source, re.M):
        if guard_start <= match.start() < guard_end:
            continue
        name = match.group(1)
        if name in {'rosterBanner'}:  # 누를 것이 없는 안내문
            continue
        offenders.append(f'{name}:{source[:match.start()].count(chr(10)) + 1}')

    assert offenders == [], (
        '매초 통째로 다시 그리는 곳이 있습니다 · 그 안의 버튼은 누름과 뗌 '
        f'사이에 사라집니다:\n  ' + '\n  '.join(offenders)
    )


def test_the_interactive_tables_go_through_the_guard():
    """누를 것이 든 세 곳은 반드시 `setHtml` 로."""
    source = _source()
    for element in ('coordinationPeerRows', 'motionRunPeerRows', 'midiTargetChoices'):
        assert re.search(rf'setHtml\(el\.{element}', source), (
            f'{element} 이 변화 검사를 안 지나갑니다'
        )


def test_the_guard_skips_identical_content():
    """같으면 손대지 않아야 한다 · 그냥 대입하면 고친 의미가 없다."""
    source = _source()
    start = source.index('function setHtml(')
    body = source[start:source.index('function render()', start)]

    assert 'lastHtml.get(element) === html' in body, (
        'setHtml 이 이전 내용과 비교하지 않습니다'
    )
    assert 'return;' in body


def test_saving_settings_stops_waiting_once_the_node_is_back():
    """무조건 2초 자면 PC 하나 빼는 데도 화면이 2초 굳는다."""
    source = _source()
    start = source.index('async function save(')
    body = source[start:source.index('async function control(', start)]

    assert 'node_connected === true' in body and 'break;' in body, (
        '재시작 확인 없이 정해진 시간만큼 자고 있습니다'
    )
    assert not re.search(r'setTimeout\(resolve,\s*2000\)', body), (
        '고정 2초 대기가 남아 있습니다'
    )


def test_the_waiting_budget_is_unchanged():
    """빨리 끝나는 것과 오래 기다리는 것은 다르다 · 상한은 그대로 2초."""
    source = _source()
    start = source.index('async function save(')
    body = source[start:source.index('async function control(', start)]

    assert 'Date.now() + 2000' in body, (
        '기다리는 상한이 2초가 아닙니다 · 운영 수치를 임의로 바꾸지 마세요'
    )
