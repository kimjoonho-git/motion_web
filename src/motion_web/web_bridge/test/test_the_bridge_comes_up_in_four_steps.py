"""브리지는 네 마디로 뜬다 · §6-193

**전에는 `__init__` 이 424줄짜리 한 줄기였다.**

브리지가 뜰 때 문제가 생기면 — 구독 이름이 틀렸다, 파라미터가 빠졌다,
순서가 꼬였다 — 424줄을 눈으로 훑어야 했다.

**파일은 쪼개지 않았다** · 브리지는 원래 모든 것을 잇는 자리라 넓은 것이
맞다 · 나누면 「무엇이 어디에 꽂혀 있나」를 찾으려고 파일 여럿을 오가야
한다 · 읽는 순서만 드러냈다.

    1  이름을 정한다      통로 이름·경로 · 아직 아무것도 안 만든다
    2  제 것을 만든다     상태 칸·락·파일을 다루는 서비스
    3  통로를 연다        ROS 구독·발행·클라이언트 + 그 통로를 쥔 서비스
    4  떴다고 알린다

**나누다가 실제로 터뜨렸다.**

`launch_motor_config_file` 과 `restart_script` 는 1번에서 만들어 2번에서
쓰는 지역 변수였다 · 마디로 자르자 2번에서 `NameError` 가 났고 브리지가
아예 뜨지 않았다 (API 가 죽었다).

**시험이 이것을 못 잡았다** · 557개가 전부 통과했다 · 아무도 노드를 실제로
만들어 보지 않기 때문이다 (만들면 진짜 DDS 가 열려 돌아가는 장비를 건드린다).

그래서 여기서는 **이름이 마디를 넘는지**를 본다 · 그것이 그때 난 오류의
정체다.
"""

import ast
import builtins
from pathlib import Path

import pytest

SOURCE = (
    Path(__file__).resolve().parents[1]
    / 'motion_web_bridge' / 'bridge_node.py'
).read_text(encoding='utf-8')

TREE = ast.parse(SOURCE)
KLASS = next(
    node for node in TREE.body
    if isinstance(node, ast.ClassDef) and node.name == 'MotionWebBridge'
)

#: 뜨는 순서 · **이 차례가 곧 뜻이다**
PHASES = [
    '_name_the_channels',
    '_create_own_state',
    '_open_channels',
    '_log_started',
]

MODULE_NAMES = {
    alias.asname or alias.name.split('.')[0]
    for node in ast.walk(TREE)
    if isinstance(node, (ast.Import, ast.ImportFrom))
    for alias in node.names
} | {
    node.name for node in TREE.body
    if isinstance(node, (ast.ClassDef, ast.FunctionDef))
} | {
    target.id
    for node in TREE.body if isinstance(node, ast.Assign)
    for target in node.targets if isinstance(target, ast.Name)
} | set(dir(builtins))


def _phase(name: str) -> ast.FunctionDef:
    return next(
        node for node in KLASS.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def test_the_constructor_reads_like_a_table_of_contents():
    init = _phase('__init__')
    calls = [
        node.func.attr
        for node in ast.walk(init)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == 'self'
    ]

    assert calls == PHASES, f'뜨는 차례가 바뀌었습니다: {calls}'


@pytest.mark.parametrize('name', PHASES)
def test_no_name_leaks_across_a_step(name):
    """마디 안에서 쓰는 이름은 마디 안에서 만들어져야 한다.

    **실제로 여기서 터졌다** · 1번에서 만든 지역 변수를 2번이 썼고,
    `NameError` 로 브리지가 아예 뜨지 않았다 · 마디를 넘겨야 하는 값은
    `self.` 에 둔다.
    """
    phase = _phase(name)
    made = {
        node.id for node in ast.walk(phase)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    } | {
        item.optional_vars.id
        for node in ast.walk(phase) if isinstance(node, ast.With)
        for item in node.items
        if isinstance(item.optional_vars, ast.Name)
    } | {
        node.target.id for node in ast.walk(phase)
        if isinstance(node, ast.For) and isinstance(node.target, ast.Name)
    } | {
        # 람다 인자 · `lambda msg: self._on_x(msg)` 처럼 콜백에 쓴다
        argument.arg
        for node in ast.walk(phase) if isinstance(node, ast.Lambda)
        for argument in node.args.args
    } | {
        # 컴프리헨션 변수
        target.id
        for node in ast.walk(phase)
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp))
        for generator in node.generators
        for target in ast.walk(generator.target) if isinstance(target, ast.Name)
    } | {argument.arg for argument in phase.args.args}

    leaked = sorted(
        {
            node.id for node in ast.walk(phase)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        }
        - made - MODULE_NAMES
    )

    assert leaked == [], (
        f'{name} 이 바깥 이름을 씁니다: {leaked}\n'
        '마디를 넘겨야 하는 값은 self 에 두세요 · 지역 변수면 NameError 로 '
        '브리지가 아예 뜨지 않습니다'
    )


def test_timers_start_last():
    """타이머는 **일을 시작한다** · 아직 안 만든 것을 건드리면 터진다."""
    phase = _phase('_open_channels')
    body = '\n'.join(SOURCE.splitlines()[phase.lineno - 1:phase.end_lineno])
    first_timer = body.index('create_timer(')

    for earlier in ('create_subscription(', 'create_publisher(', 'create_client('):
        assert body.index(earlier) < first_timer, f'{earlier} 가 타이머보다 늦습니다'


def test_naming_the_channels_creates_nothing():
    """1번은 이름만 정한다 · 여기서 통로를 열면 순서가 무너진다."""
    phase = _phase('_name_the_channels')
    body = '\n'.join(SOURCE.splitlines()[phase.lineno - 1:phase.end_lineno])

    for made in ('create_subscription(', 'create_publisher(', 'create_client(',
                 'create_timer('):
        assert made not in body, f'이름만 정하는 자리에서 {made} 를 합니다'


def test_the_file_was_not_split_up():
    """**나누지 않기로 했다** · 브리지는 모든 것을 잇는 자리다.

    메서드 102개 중 72개가 20줄 이하였다 · 얽힌 덩어리가 아니라 작은 것의
    모음이다 · 억지로 쪼개면 「무엇이 어디에 꽂혀 있나」를 찾으려고 파일
    여럿을 오가야 한다.
    """
    methods = [
        node for node in KLASS.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]

    assert len(methods) > 90, '브리지가 쪼개졌습니다 · 그러기로 한 적이 없습니다'
