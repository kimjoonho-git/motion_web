"""숨김이 언제나 이긴다 · §6-161

**사용법 화면이 모든 탭 아래에 붙어 나왔다.**

화면 전환은 패널에 `hidden` 을 붙였다 떼는 것으로 한다 · 그 뜻은
`09-midi.css` 의 한 줄이 정한다.

    .hidden { display: none; }

그런데 새로 넣은 `12-docs.css` 에 이렇게 썼다.

    .docs-panel { display: flex; }

클래스 하나끼리는 특정도가 같아서 **나중에 실린 쪽이 이긴다** · 12 는 09
뒤에 실리므로 `display: flex` 가 `display: none` 을 눌렀다 · 그래서 사용법
화면이 어느 탭에서도 안 사라졌다.

다른 패널들은 `display` 를 아예 안 정해서 이 일이 없었다 · 정해야 한다면
`:not(.hidden)` 이나 `.그이름.hidden` 예외를 같이 써야 한다.

눈으로 잡기 어려운 종류다 · 만든 사람 화면에서는 그 탭만 보기 때문에
멀쩡해 보인다 · 그래서 여기서 센다.
"""

import re
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[4]
UI = WORKSPACE / 'src/motion_web/web_ui/static'

#: 한 클래스만 쓴 규칙 · 이것이 `.hidden` 과 특정도가 같다
SINGLE_CLASS_RULE = re.compile(r'^(\.[A-Za-z0-9_-]+)\s*\{([^}]*)\}', re.M)
DECLARES_DISPLAY = re.compile(r'\bdisplay\s*:')


def _stylesheets():
    return sorted(UI.glob('css/*.css'))


def _hidden_index(sheets):
    for index, path in enumerate(sheets):
        if re.search(r'^\.hidden\s*\{', path.read_text(encoding='utf-8'), re.M):
            return index
    raise AssertionError('`.hidden` 규칙을 못 찾았습니다 · 숨김 방식이 바뀌었나요')


def _markup():
    parts = [path.read_text(encoding='utf-8') for path in UI.glob('panels/*.html')]
    parts += [path.read_text(encoding='utf-8') for path in UI.glob('js/*.js')]
    parts.append((UI / 'index.html').read_text(encoding='utf-8'))
    return '\n'.join(parts)


def _rules_that_could_beat_hidden():
    """`.hidden` 보다 나중에 실리면서 display 를 정하고, 예외가 없는 규칙."""
    sheets = _stylesheets()
    start = _hidden_index(sheets)
    found = []
    for path in sheets[start:]:
        text = path.read_text(encoding='utf-8')
        for match in SINGLE_CLASS_RULE.finditer(text):
            name = match.group(1)[1:]
            if name == 'hidden' or not DECLARES_DISPLAY.search(match.group(2)):
                continue
            escaped = re.escape(name)
            exempted = (
                re.search(rf'\.{escaped}\.hidden\b', text)
                or f'.{name}:not(.hidden)' in text
            )
            if not exempted:
                found.append((path.name, name))
    return found


def test_the_hidden_rule_is_where_we_think():
    """이 시험이 딛고 선 바닥부터 · 숨김 방식이 바뀌면 알아야 한다."""
    sheets = _stylesheets()
    _hidden_index(sheets)  # 없으면 여기서 터진다
    assert (UI / 'css/09-midi.css').exists()


def test_nothing_that_gets_hidden_also_forces_its_own_display():
    """숨겨지는 요소가 제 `display` 를 우겨서는 안 된다.

    실제로 그렇게 새 화면이 모든 탭에 눌러앉았다.
    """
    markup = _markup()
    offenders = []
    for sheet, name in _rules_that_could_beat_hidden():
        pattern = re.compile(rf'class="[^"]*\b{re.escape(name)}\b[^"]*"')
        for element in pattern.finditer(markup):
            if 'hidden' in element.group(0):
                offenders.append(f'{sheet} · .{name}')
                break

    assert offenders == [], (
        '숨겨지는데 display 를 정해 둔 규칙이 있습니다 · '
        '그 요소는 어느 화면에서도 안 사라집니다 · '
        '`:not(.hidden)` 을 붙이세요:\n  ' + '\n  '.join(offenders)
    )


def test_every_workspace_panel_starts_hidden_except_the_default():
    """탭 패널은 하나만 보이고 나머지는 `hidden` 으로 시작해야 한다."""
    panels = []
    for path in sorted(UI.glob('panels/*.html')):
        text = path.read_text(encoding='utf-8')
        for match in re.finditer(r'<section([^>]*data-workspace-panel="[^"]+"[^>]*)>', text):
            attributes = match.group(1)
            name = re.search(r'data-workspace-panel="([^"]+)"', attributes).group(1)
            panels.append((name, 'hidden' in attributes))

    visible = [name for name, is_hidden in panels if not is_hidden]
    assert visible == ['monitoring'], (
        f'처음부터 보이는 패널이 기본 화면 하나가 아닙니다: {visible}'
    )


def test_the_docs_panel_is_one_of_them():
    """이 시험이 생긴 계기 · 사용법 패널이 실제로 그 규칙을 따르는지."""
    text = (UI / 'panels/12-panel-docs.html').read_text(encoding='utf-8')
    assert 'data-workspace-panel="docs"' in text
    assert 'workspace-panel hidden' in text

    css = (UI / 'css/12-docs.css').read_text(encoding='utf-8')
    assert '.docs-panel:not(.hidden)' in css, (
        '사용법 패널이 다시 숨김을 이기고 있습니다 · 모든 탭에 나옵니다'
    )
