"""화면 셸에 조각을 끼워 넣는다 · §6-192

**길목 파일 안에 조립기가 있었다.**

`system_routes.py` 가 304줄에 길 19개로 길당 16줄이었다 · 그 안에 이
조립기가 통째로 들어 있었고, 시험 두 개가 `routes.system_routes` 에서
이것을 꺼내 썼다 · 화면을 짜 맞추는 일은 HTTP 와 상관이 없다.

조각으로 나누되 **끼우는 일은 서버가 한다** (§6-44) · 브라우저에서 끼우면
`main.js` 가 모듈 적재 시점에 DOM 을 찾지 못하고, 빌드 때 끼우면 런타임이
소스를 서빙하므로 쓰이지 않는다 (§6-42 의 함정).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

#: `<!--#include 경로 -->` · 줄 하나가 통째로 조각 내용으로 바뀐다
_INCLUDE = re.compile(r'^[ \t]*<!--#include ([A-Za-z0-9_./-]+) -->[ \t]*\r?\n', re.M)

#: 조각 첫머리의 출처 주석 · 소스에만 두고 화면으로는 내보내지 않는다
_PROVENANCE = re.compile(r'\A<!--.*?-->\r?\n', re.S)


class IndexComposer:
    """`index.html` 셸에 조각을 끼워 넣는다 · §6-44.

    화면 셸이 2,004줄짜리 한 덩이였다. 조각으로 나누되 **끼우는 일은 서버가
    한다** · 브라우저에서 끼우면 `main.js`가 모듈 적재 시점에 DOM을 찾지 못하고,
    빌드 때 끼우면 런타임이 소스를 서빙하므로 쓰이지 않는다(§6-42의 함정).

    조각 파일의 `mtime`이 하나라도 바뀌면 다시 조립한다 · `ETag`는 조립 결과에서
    낸다 · 셸만 보고 만들면 조각을 고쳐도 브라우저가 옛 화면을 쓴다.
    """

    def __init__(self, index_path: Path) -> None:
        self.index_path = index_path
        self._cached_key = None
        self._cached_html = ''
        self._cached_etag = ''

    def _parts(self):
        shell = self.index_path.read_text(encoding='utf-8')
        names = _INCLUDE.findall(shell)
        return shell, [self.index_path.parent / name for name in names]

    def _stamp(self, paths):
        return tuple(
            (str(path), path.stat().st_mtime_ns, path.stat().st_size)
            for path in paths
        )

    def compose(self):
        """조립한 HTML과 그 `ETag`를 돌려준다."""
        shell, part_paths = self._parts()
        key = self._stamp([self.index_path, *part_paths])
        if key == self._cached_key:
            return self._cached_html, self._cached_etag

        def replace(match: 're.Match[str]') -> str:
            part = self.index_path.parent / match.group(1)
            return _PROVENANCE.sub('', part.read_text(encoding='utf-8'), count=1)

        html = _INCLUDE.sub(replace, shell)
        etag = hashlib.md5(html.encode('utf-8'), usedforsecurity=False).hexdigest()
        self._cached_key, self._cached_html, self._cached_etag = key, html, etag
        return html, etag
