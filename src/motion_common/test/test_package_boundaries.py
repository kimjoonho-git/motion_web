"""패키지 경계 검증 · §7 규칙 4.

패키지 간 Python 직접 import는 금지다. 경계는 토픽·서비스 또는 `motion_common`.
직접 import가 생기면 한 패키지를 빌드·테스트하기 위해 다른 패키지 전체가 필요해지고,
노드를 띄우지 않고는 단위 테스트를 할 수 없게 된다.
"""

import ast
from pathlib import Path

import pytest

WORKSPACE = Path(__file__).resolve().parents[3]

#: 자체 ROS 패키지 이름 → 소스 루트
PACKAGES = {
    'motion_web_bridge': 'src/motion_web/web_bridge',
    'motion_schedule': 'src/motion_web/motion_schedule',
    'motion_coordination': 'src/motion_coordination',
    'motion_studio': 'src/motion_control_studio/motion_studio',
    'motion_runtime': 'src/motion_control_studio/motion_control/motion_runtime',
    'motion_supervisor': 'src/motion_control_studio/motion_control/motion_supervisor',
    'midi_control': 'src/motion_control_studio/motion_control/midi_control',
    'motion_state_monitor': 'src/motion_control_studio/motion_control/motion_state_monitor',
}


def _source_files(root: Path):
    for path in root.rglob('*.py'):
        parts = path.parts
        if 'build' in parts or 'install' in parts or '__pycache__' in parts:
            continue
        if 'test' in parts or path.name.startswith('test_'):
            continue
        yield path


def _imported_top_levels(path: Path):
    try:
        tree = ast.parse(path.read_text(encoding='utf-8'))
    except (OSError, SyntaxError):
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            # level > 0 은 패키지 내부 상대 import · 경계 위반이 아니다
            if node.module and node.level == 0:
                yield node.module.split('.')[0], node.lineno
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split('.')[0], node.lineno


@pytest.mark.parametrize('package', sorted(PACKAGES))
def test_package_does_not_import_another_package_directly(package):
    root = WORKSPACE / PACKAGES[package]
    if not root.is_dir():
        pytest.skip(f'{package} 소스를 찾을 수 없음')

    others = set(PACKAGES) - {package}
    offenders = []
    for path in _source_files(root):
        for imported, lineno in _imported_top_levels(path):
            if imported in others:
                offenders.append(
                    f'{path.relative_to(WORKSPACE)}:{lineno} → {imported}'
                )
    assert not offenders, (
        f'{package}가 다른 패키지를 직접 import한다 · '
        f'motion_common으로 옮기거나 토픽·서비스를 쓸 것:\n' + '\n'.join(offenders)
    )


def test_motion_common_depends_on_no_ros_package():
    """공용 커널은 rclpy와 다른 패키지에 의존하지 않는다 · 노드 없이 테스트 가능해야 한다."""
    root = WORKSPACE / 'src/motion_common/motion_common'
    forbidden = set(PACKAGES) | {'rclpy', 'std_msgs', 'motion_control_msgs'}
    offenders = []
    for path in _source_files(root):
        for imported, lineno in _imported_top_levels(path):
            if imported in forbidden:
                offenders.append(f'{path.name}:{lineno} → {imported}')
    assert not offenders, 'motion_common이 ROS에 의존한다:\n' + '\n'.join(offenders)
