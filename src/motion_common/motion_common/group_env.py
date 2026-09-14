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

#: 설정을 못 읽어도 시스템은 떠야 한다 · 그룹 기본값과 같은 값
FALLBACK_DOMAIN_ID = 21


def _read_scalar(text: str, key: str) -> str:
    """아주 단순한 YAML 한 줄 읽기 · ROS 를 켜기 전이라 pyyaml 이 없다."""
    match = re.search(rf'^{re.escape(key)}\s*:\s*(.+?)\s*$', text, re.M)
    if not match:
        return ''
    return match.group(1).strip().strip('"').strip("'")


def resolve(config_path: Path) -> dict:
    """이름공간과 도메인 · 설정이 없거나 깨져도 쓸 수 있는 값을 돌려준다."""
    pc_id = ''
    domain = ''
    try:
        text = config_path.read_text(encoding='utf-8')
    except OSError:
        text = ''
    if text:
        pc_id = _read_scalar(text, 'pc_id')
        domain = _read_scalar(text, 'dds_domain_id')

    namespace = pc_id or socket.gethostname()
    try:
        domain_id = int(domain)
    except (TypeError, ValueError):
        domain_id = FALLBACK_DOMAIN_ID
    if not 0 <= domain_id <= 101:
        domain_id = FALLBACK_DOMAIN_ID
    return {'namespace': namespace, 'domain_id': domain_id}


def main() -> int:
    workspace = Path(
        os.environ.get('MOTION_WORKSPACE') or Path.cwd()
    ).resolve()
    config_path = Path(
        os.environ.get('MOTION_COORDINATION_CONFIG')
        or workspace / 'config/motion_coordination.yaml'
    ).expanduser()
    resolved = resolve(config_path)
    # 바깥에서 이미 정했으면 그것을 존중한다 · 되돌릴 수 있어야 한다
    print(f'export MOTION_PC_NAMESPACE="${{MOTION_PC_NAMESPACE:-{resolved["namespace"]}}}"')
    print(f'export ROS_DOMAIN_ID="${{ROS_DOMAIN_ID:-{resolved["domain_id"]}}}"')
    return 0


if __name__ == '__main__':
    sys.exit(main())
