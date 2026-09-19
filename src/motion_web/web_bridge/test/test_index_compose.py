"""`index.html` 서버 조립 계약 · §6-44.

셸에 조각을 끼워 돌려준다. **조립 결과가 원본과 한 글자도 달라지면 안 되고**,
조각을 고치면 `ETag`가 따라 바뀌어야 한다. 둘 다 틀리면 화면이 조용히 어긋난다.
"""

from pathlib import Path

from motion_web_bridge.index_composer import IndexComposer

STATIC = (
    Path(__file__).resolve().parents[2] / 'web_ui' / 'static'
)


def _composer():
    return IndexComposer(STATIC / 'index.html')


def test_every_include_marker_resolves_to_a_file():
    composer = _composer()
    _shell, parts = composer._parts()
    assert parts, '자리표시자가 하나도 없다'
    for part in parts:
        assert part.is_file(), f'{part} 없음'


def test_composed_html_leaves_no_marker_behind():
    html, _etag = _composer().compose()
    assert '#include' not in html


def test_provenance_comments_stay_in_the_source():
    """조각 머리말은 소스용이다 · 화면으로 내보내면 원본과 바이트가 달라진다."""
    html, _etag = _composer().compose()
    assert '§6-44' not in html
    assert '떼어낸 조각' not in html


def test_composed_html_contains_every_panel():
    # 연동은 별도 패널이 아니다 · 모션 실행 화면에서 "연동" 범위를 고르면
    # 나온다 · 실행과 연동이 갈라져 있으면 어느 쪽으로 나가는지 두 화면을
    # 오가며 맞춰야 했다 · §6-66
    html, _etag = _composer().compose()
    for panel in (
        'data-workspace-panel="system"',
        'data-workspace-panel="monitoring"',
        'data-workspace-panel="motion"',
        'data-workspace-panel="studio"',
        'data-workspace-panel="log"',
        'id="scheduleEditModal"',
        'id="workspaceTabs"',
    ):
        assert panel in html, f'{panel} 누락'


def test_etag_follows_the_parts_not_only_the_shell(tmp_path):
    """조각만 고쳐도 ETag가 바뀌어야 한다 · 셸만 보면 옛 화면이 남는다."""
    composer = _composer()
    _html, first = composer.compose()
    _shell, parts = composer._parts()

    target = parts[0]
    original = target.read_bytes()
    try:
        target.write_bytes(original + '<!-- 시험용 한 줄 -->\n'.encode('utf-8'))
        _again, second = composer.compose()
    finally:
        target.write_bytes(original)

    assert second != first
    _restored, third = composer.compose()
    assert third == first


def test_repeated_compose_reuses_the_cache():
    composer = _composer()
    first_html, first_etag = composer.compose()
    second_html, second_etag = composer.compose()
    assert second_html is first_html
    assert second_etag == first_etag


def test_javascript_helper_uses_the_same_rules():
    """시험용 조립기와 서버 조립기가 갈라지면, 시험이 보는 화면과 실제가 달라진다.

    두 곳의 정규식을 문자로 맞춰 둔다 · 완전한 동치 검사는 아니지만
    한쪽만 고치는 흔한 실수는 잡는다.
    """
    helper = (
        STATIC.parent / 'tools' / 'index_html.mjs'
    ).read_text(encoding='utf-8')

    assert '<!--#include ([A-Za-z0-9_./-]+) -->' in helper
    assert 'composeStylesCss' in helper
    assert 'static' in helper and 'css' in helper
    # 출처 주석 제거를 양쪽 모두 한다
    assert 'PROVENANCE' in helper

