"""실행 컨텍스트를 받아들일지 판정한다 · 한 곳에서 · §6-195

**노드 넷이 같은 판정을 따로 들고 있었다.**

브리지가 「이 설정으로 일해라」를 네 노드에 쏘면, 각 노드는 두 가지를 본다.

    1  받은 모션축 설정 파일이 정말 그 파일인가   sha256 지문
    2  확인하려는 컨텍스트가 지금 적용된 것인가   context_id

둘 다 세 벌씩 복사돼 있었고, **1번은 한 곳이 달랐다.**

    스튜디오      if actual != expected:                 항상 검사
    모션축 매핑    expected 없으면 「필요합니다」로 거부      항상 검사
    MIDI         if expected and actual != expected:    **비면 건너뜀**

MIDI 만 지문이 비어 오면 그냥 통과했다 · 게다가 매핑 파일이 아예 없으면
`actual` 도 빈 문자열이라, **파일 없이도 「확인 완료」로 답했다.**

**지금 브리지는 항상 지문을 보낸다** · 그래서 아직 안 터졌다 · 그러나
노드는 보내는 쪽을 믿으면 안 된다 · ROS 토픽은 누구나 쏠 수 있고
(`ros2 topic pub`, 디버그 스크립트, 다른 노드), 지문이 한 번이라도 비어
오면 **MIDI 만 조용히 허용**하고 나머지는 거부한다 · 반쪽만 도는 상태다 ·
매핑이 틀리면 엉뚱한 축이 움직인다.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Optional

#: 지문 없이 왔다 · 보내는 쪽을 믿지 않는다
MAPPING_FINGERPRINT_REQUIRED = '모션축 설정 파일 버전(지문)이 필요합니다'

#: 지문은 왔는데 그 파일이 없다
MAPPING_FILE_MISSING = '현재 프로젝트의 모션축 설정 파일을 찾을 수 없습니다'

#: 파일은 있는데 내용이 다르다 · 누가 고쳤거나 다른 프로젝트의 것이다
MAPPING_FINGERPRINT_MISMATCH = '모션축 설정 파일 버전이 실행 컨텍스트와 다릅니다'

#: 확인하려는 컨텍스트가 지금 적용된 것과 다르다
CONTEXT_MISMATCH = '확인하려는 실행 컨텍스트가 적용된 설정과 다릅니다'


def verify_mapping_fingerprint(path: Optional[Path], expected_sha: Any) -> str:
    """이 파일이 정말 그 파일인가 · 맞으면 그 지문을 돌려준다 · §6-195

    **비어 있으면 통과가 아니라 거부다** · 그것이 MIDI 만 달랐던 부분이다 ·
    지문이 없다는 것은 「검사하지 말라」가 아니라 「보낸 쪽이 무엇을 주는지
    모른다」는 뜻이다.
    """
    expected = str(expected_sha or '').strip()
    if not expected:
        raise ValueError(MAPPING_FINGERPRINT_REQUIRED)
    if path is None or not Path(path).is_file():
        raise ValueError(MAPPING_FILE_MISSING)
    actual = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError(MAPPING_FINGERPRINT_MISMATCH)
    return actual


def confirm_context_id(applied: Any, payload: Any) -> str:
    """확인하려는 컨텍스트가 지금 적용된 것인가 · §6-195

    브리지는 설정을 적용한 뒤 한 번 더 「그거 맞지?」를 묻는다 · 그 사이에
    프로젝트가 바뀌었을 수 있기 때문이다 · 세 노드가 이 판정을 따로 적고
    있었다 · 지금은 셋 다 같았지만, 세 벌인 이상 언젠가 갈린다.
    """
    context_id = str((payload or {}).get('context_id') or '').strip()
    if not context_id or context_id != (applied or {}).get('context_id'):
        raise ValueError(CONTEXT_MISMATCH)
    return context_id
