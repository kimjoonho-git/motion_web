"""순수 모듈이 상태에 닿지 않는다는 성질을 지킨다.

이 모듈은 `MotionWebBridge`에서 떼어낸 순수 함수 모음이다. 누군가 나중에
`self` 접근이나 노드 import를 끼워 넣으면 다시 노드에 묶이고, 노드를 띄우지
않고 테스트할 수 있다는 성질이 사라진다. 그 순간을 여기서 잡는다.

`bridge_node` 분해가 진행되면서 새로 만들어지는 순수 모듈도 이 검사에 추가한다.
"""

import ast
import inspect
from pathlib import Path

import pytest

from motion_web_bridge import motion_file_analysis, motor_config_rules

#: 검사 대상 순수 모듈 · 분해가 진행되면 여기에 추가한다
PURE_MODULES = [motor_config_rules, motion_file_analysis]

#: 순수 모듈이 기대어도 되는 것 · 공용 커널과 표준 라이브러리만
ALLOWED_PROJECT_IMPORTS = {'motion_common'}


def _tree(module):
    return ast.parse(Path(inspect.getfile(module)).read_text(encoding='utf-8'))


@pytest.mark.parametrize('module', PURE_MODULES, ids=lambda m: m.__name__)
def test_module_has_no_self_parameter(module):
    """인스턴스 메서드가 딸려 들어오면 안 된다."""
    offenders = [
        node.name
        for node in ast.walk(_tree(module))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.args.args and node.args.args[0].arg == 'self'
    ]
    assert not offenders, f'self를 받는 함수가 있다: {offenders}'


@pytest.mark.parametrize('module', PURE_MODULES, ids=lambda m: m.__name__)
def test_module_never_touches_self(module):
    """`self.x`도 `getattr(self, "x")`도 없어야 한다.

    문자열 기반 동적 접근은 눈에 잘 띄지 않는다 · 실제로 추출 도중
    `getattr(self, '_motion_state')`를 쓰는 메서드가 순수로 잘못 분류됐다.
    """
    offenders = []
    for node in ast.walk(_tree(module)):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id == 'self':
                offenders.append(f'self.{node.attr}')
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in ('getattr', 'setattr', 'hasattr')
            and node.args and isinstance(node.args[0], ast.Name)
            and node.args[0].id == 'self'
        ):
            offenders.append(f'{node.func.id}(self, ...)')
    assert not offenders, f'상태에 닿는다: {sorted(set(offenders))}'


@pytest.mark.parametrize('module', PURE_MODULES, ids=lambda m: m.__name__)
def test_module_does_not_import_the_node(module):
    """노드를 import하면 의존 방향이 되돌아간다."""
    offenders = []
    for node in ast.walk(_tree(module)):
        names = []
        if isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        elif isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        for name in names:
            if 'bridge_node' in name or 'MotionWebBridge' in name:
                offenders.append(name)
    assert not offenders, f'노드를 import한다: {offenders}'


@pytest.mark.parametrize('module', PURE_MODULES, ids=lambda m: m.__name__)
def test_module_only_depends_on_the_shared_kernel(module):
    """프로젝트 내부 의존은 `motion_common`만 허용한다."""
    offenders = []
    for node in ast.walk(_tree(module)):
        if isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                offenders.append(f'상대 import (level={node.level})')
            elif node.module:
                top = node.module.split('.')[0]
                if top.startswith('motion_') and top not in ALLOWED_PROJECT_IMPORTS:
                    offenders.append(node.module)
    assert not offenders, f'허용되지 않은 의존: {offenders}'


@pytest.mark.parametrize('module', PURE_MODULES, ids=lambda m: m.__name__)
def test_public_functions_are_importable_without_a_node(module):
    """노드를 만들지 않고 호출할 수 있어야 한다."""
    functions = [
        name for name, value in vars(module).items()
        if inspect.isfunction(value) and not name.startswith('_')
        and inspect.getmodule(value) is module
    ]
    assert functions, '공개 함수가 하나도 없다'
    for name in functions:
        signature = inspect.signature(getattr(module, name))
        assert 'self' not in signature.parameters, name


def test_motor_config_rules_still_answers_without_any_bridge():
    """대표 함수 몇 개를 노드 없이 그대로 호출해 본다."""
    assert motor_config_rules.is_ac_servo_motor({'motor_type': 'ac_servo'}) is True
    assert motor_config_rules.is_ac_servo_motor({'driver_model': 'MINAS-A6'}) is True
    assert motor_config_rules.is_ac_servo_motor({'motor_type': 'dynamixel'}) is False
    assert motor_config_rules.is_dynamixel_motor({'motor_type': 'dynamixel'}) is True
    assert motor_config_rules.is_dynamixel_motor({'motor_type': 'ac_servo'}) is False
    assert motor_config_rules.empty_motor_registry() == (
        motor_config_rules.empty_motor_registry()
    )
    assert motor_config_rules.scan_item_has_detected_devices({}) is False
