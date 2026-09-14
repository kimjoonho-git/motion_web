"""그룹 설정에서 실행 환경을 뽑는다 · §6-96

"이 PC 는 누구인가"와 "어느 DDS 망에 있는가"는 그룹 설정이 주인이다 ·
`config/motion_coordination.yaml` 의 `pc_id` 와 `dds_domain_id`.

호스트 이름을 따로 읽으면 주인이 둘이 된다 · 설정에서 `pc_id` 를 바꿨는데
토픽 이름은 호스트 이름을 따라가면, 같은 PC 가 두 이름으로 불린다.

실행 스크립트가 ROS 를 켜기 **전에** 부르므로 표준 라이브러리만 쓴다.

    eval "$(python3 .../group_env.py)"
"""

from __future__ import annotations

import os
import re
import socket
import sys
from pathlib import Path

# 이름을 토픽에 쓸 수 있는 모양으로 바꾸는 규칙은 `topics.py` 가 주인이다 ·
# 여기서 따로 적으면 둘이 갈린다 · 실제로 `pc_id` 에 하이픈이 있으면
# (`pc-a`, `floating3-Ecolite-Series`) 파이썬 노드는 `pc_a` 를 열고 모터
# 노드는 `/pc-a` 를 받아 **아예 뜨지도 못한다**.
#
# ROS 를 켜기 전이라 경로를 직접 넣는다 · `topics.py` 는 표준 라이브러리만 쓴다.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from motion_common.topics import sanitize_namespace  # noqa: E402

#: 설정을 못 읽어도 시스템은 떠야 한다 · 그룹 기본값과 같은 값
FALLBACK_DOMAIN_ID = 21


def _read_scalar(text: str, key: str) -> str:
    """아주 단순한 YAML 한 줄 읽기 · ROS 를 켜기 전이라 pyyaml 이 없다."""
    match = re.search(rf'^{re.escape(key)}\s*:\s*(.+?)\s*$', text, re.M)
    if not match:
        return ''
    return match.group(1).strip().strip('"').strip("'")


def resolve(config_path: Path) -> dict:
    """이름공간과 도메인 · 설정이 없거나 깨져도 쓸 수 있는 값을 돌려준다.

    이름은 토픽에 쓸 수 있는 모양으로 돌려준다 · 여기서 정리해 두어야 모터
    노드에 넘기는 `__ns` 와 파이썬 노드가 여는 토픽이 **같은 이름**이 된다.
    """
    pc_id = ''
    domain = ''
    try:
        text = config_path.read_text(encoding='utf-8')
    except OSError:
        text = ''
    if text:
        pc_id = _read_scalar(text, 'pc_id')
        domain = _read_scalar(text, 'dds_domain_id')

    namespace = sanitize_namespace(pc_id or socket.gethostname())
    try:
        domain_id = int(domain)
    except (TypeError, ValueError):
        domain_id = FALLBACK_DOMAIN_ID
    if not 0 <= domain_id <= 101:
        domain_id = FALLBACK_DOMAIN_ID
    return {'namespace': namespace, 'domain_id': domain_id}


def exports(resolved: dict, environ) -> list:
    """셸이 그대로 삼킬 두 줄.

    **바깥에서 이미 정했으면 그것을 존중한다** · 되돌릴 수 있어야 한다 ·
    빈 값으로 둔 것도 뜻이 있다(`MOTION_PC_NAMESPACE=` · 이름표 끄기) ·
    그래서 "안 정했다"와 "비워 뒀다"를 가른다.

    덮어쓴 값도 **같은 규칙을 지난다** · 손으로 `pc-a` 를 넣어도 모터 노드가
    받는 이름과 노드가 여는 토픽이 갈리지 않아야 한다.
    """
    given = environ.get('MOTION_PC_NAMESPACE')
    namespace = (
        resolved['namespace'] if given is None else sanitize_namespace(given)
    )
    domain = (environ.get('ROS_DOMAIN_ID') or '').strip()
    return [
        f'export MOTION_PC_NAMESPACE="{namespace}"',
        f'export ROS_DOMAIN_ID="{domain or resolved["domain_id"]}"',
    ]


def main() -> int:
    workspace = Path(
        os.environ.get('MOTION_WORKSPACE') or Path.cwd()
    ).resolve()
    config_path = Path(
        os.environ.get('MOTION_COORDINATION_CONFIG')
        or workspace / 'config/motion_coordination.yaml'
    ).expanduser()
    for line in exports(resolve(config_path), os.environ):
        print(line)
    return 0


if __name__ == '__main__':
    sys.exit(main())
