"""CSS 조각의 순서 계약 · §6-43.

`styles.css` 7,415줄을 순서 그대로 잘랐다. **연결 순서가 겹치기(cascade)를
정하므로** 순서가 어긋나면 화면이 조용히 달라진다. 시험이 그것을 잡는다.
"""

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / 'static'
INDEX = STATIC / 'index.html'
CSS_DIR = STATIC / 'css'

LINK = re.compile(r'<link rel="stylesheet" href="/static/css/([0-9a-z-]+\.css)')


def _linked_names():
    return LINK.findall(INDEX.read_text(encoding='utf-8'))


def test_index_links_every_css_part_exactly_once():
    linked = _linked_names()
    on_disk = sorted(p.name for p in CSS_DIR.glob('*.css'))
    assert sorted(linked) == on_disk
    assert len(linked) == len(set(linked))


def test_links_follow_the_numeric_prefix_order():
    """번호 접두가 곧 겹치기 순서다 · 사람이 순서를 지키게 하는 장치다."""
    linked = _linked_names()
    assert linked == sorted(linked)


def test_no_part_is_empty_and_each_declares_its_origin():
    for name in _linked_names():
        text = (CSS_DIR / name).read_text(encoding='utf-8')
        assert '§6-43' in text, f'{name}: 출처 주석이 없다'
        body = text.split('*/', 1)[1]
        assert body.strip(), f'{name}: 내용이 없다'


def test_old_single_stylesheet_is_gone():
    """남겨두면 둘 중 어느 것이 진짜인지 알 수 없게 된다."""
    assert not (STATIC / 'styles.css').exists()
