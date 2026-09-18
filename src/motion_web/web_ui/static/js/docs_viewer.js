/** 사용법·설치법 보기 · §6-157
 *
 * **문서가 저장소 안에만 있으면 아무도 안 읽는다.** 사용법과 설치법은 `.md`
 * 라서 터미널에서 `cat` 하거나 깃허브에 올려야 읽혔다 · 정작 이 프로그램을
 * 쓰는 사람은 웹 화면 앞에 앉아 있다.
 *
 * 탭을 눌렀을 때 읽는다 · 첫 화면 뜨는 속도에 30KB 짜리 문서 둘을 얹지 않는다.
 * 문서는 `install.sh` 로 갱신되므로, 화면을 띄워 둔 채 갱신했을 때를 위해
 * `다시 읽기` 를 둔다.
 */

import { fetchDocument, fetchDocumentList } from './api.js';
import { renderMarkdown } from './markdown.js';

const panel = document.querySelector('[data-workspace-panel="docs"]');

if (panel) {
  const picker = document.getElementById('docsPicker');
  const outline = document.getElementById('docsOutline');
  const article = document.getElementById('docsArticle');
  const subtitle = document.getElementById('docsSubtitle');
  const sourceLabel = document.getElementById('docsSourceLabel');
  const searchInput = document.getElementById('docsSearchInput');
  const reloadButton = document.getElementById('docsReloadButton');

  const state = {
    documents: [],
    activeId: '',
    loadedOnce: false,
    headings: [],
  };

  function setArticleMessage(text, kind = 'docs-placeholder') {
    article.innerHTML = '';
    const paragraph = document.createElement('p');
    paragraph.className = kind;
    paragraph.textContent = text;
    article.appendChild(paragraph);
    outline.innerHTML = '';
  }

  function renderPicker() {
    picker.innerHTML = '';
    state.documents.forEach((document_) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.setAttribute('role', 'tab');
      button.textContent = document_.title;
      button.className = [
        document_.id === state.activeId ? 'active' : '',
        document_.available ? '' : 'missing',
      ].filter(Boolean).join(' ');
      button.setAttribute('aria-selected', document_.id === state.activeId ? 'true' : 'false');
      if (!document_.available) button.title = `${document_.source} 파일이 이 PC 에 없습니다`;
      button.addEventListener('click', () => selectDocument(document_.id));
      picker.appendChild(button);
    });
  }

  /** 차례 · `##`·`###` 만 올린다 · `#` 은 문서 제목 하나뿐이고 줄마다 넣으면 길어진다 */
  function renderOutline(headings) {
    outline.innerHTML = '';
    const usable = headings.filter((heading) => heading.level === 2 || heading.level === 3);
    if (!usable.length) {
      const empty = document.createElement('span');
      empty.className = 'docs-outline-empty';
      empty.textContent = '차례 없음';
      outline.appendChild(empty);
      return;
    }
    usable.forEach((heading) => {
      const link = document.createElement('a');
      link.href = `#${heading.id}`;
      link.dataset.level = String(heading.level);
      link.dataset.target = heading.id;
      link.textContent = heading.text;
      link.title = heading.text;
      outline.appendChild(link);
    });
  }

  /** 문서 안에서만 뛴다 · 주소창을 건드리면 화면 전체 경로가 흔들린다 */
  function jumpTo(id) {
    const target = article.querySelector(`[id="${CSS.escape(id)}"]`);
    if (!target) return false;
    target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    markActiveOutline(id);
    return true;
  }

  function markActiveOutline(id) {
    outline.querySelectorAll('a').forEach((link) => {
      link.classList.toggle('active', link.dataset.target === id);
    });
  }

  /** 읽는 중인 곳을 차례에 표시 · 화면 위쪽에 가장 가까운 제목을 고른다 */
  function trackScroll() {
    const anchors = state.headings
      .filter((heading) => heading.level === 2 || heading.level === 3)
      .map((heading) => article.querySelector(`[id="${CSS.escape(heading.id)}"]`))
      .filter(Boolean);
    if (!anchors.length) return;
    let current = anchors[0];
    anchors.forEach((element) => {
      if (element.getBoundingClientRect().top <= 120) current = element;
    });
    markActiveOutline(current.id);
  }

  async function selectDocument(docId) {
    state.activeId = docId;
    renderPicker();
    setArticleMessage('문서를 읽는 중입니다');
    try {
      const payload = await fetchDocument(docId);
      if (!payload.success) {
        setArticleMessage(payload.message || '문서를 읽지 못했습니다', 'docs-error');
        if (sourceLabel) sourceLabel.textContent = payload.source || '';
        return;
      }
      const { html, headings } = renderMarkdown(payload.markdown);
      state.headings = headings;
      article.innerHTML = html;
      renderOutline(headings);
      if (subtitle) subtitle.textContent = payload.subtitle || '';
      if (sourceLabel) {
        const when = payload.modified
          ? new Date(payload.modified * 1000).toLocaleString('ko-KR')
          : '';
        sourceLabel.textContent = when
          ? `${payload.source} · 마지막 수정 ${when}`
          : payload.source;
      }
      article.scrollTop = 0;
      window.scrollTo({ top: 0 });
      if (searchInput?.value) applySearch(searchInput.value);
      trackScroll();
    } catch (error) {
      setArticleMessage(`문서를 읽지 못했습니다 · ${error?.message || error}`, 'docs-error');
    }
  }

  async function load(force = false) {
    if (state.loadedOnce && !force) return;
    try {
      const payload = await fetchDocumentList();
      state.documents = payload.documents || [];
      state.loadedOnce = true;
      const first = state.documents.find((item) => item.available) || state.documents[0];
      if (!first) {
        setArticleMessage('읽을 문서가 없습니다', 'docs-error');
        return;
      }
      await selectDocument(force && state.activeId ? state.activeId : first.id);
    } catch (error) {
      setArticleMessage(`문서 목록을 읽지 못했습니다 · ${error?.message || error}`, 'docs-error');
    }
  }

  /** 찾기 · 글자만 칠한다 · 태그 안은 건드리지 않는다 (`<img src=...>` 가 깨진다) */
  function applySearch(term) {
    article.querySelectorAll('mark').forEach((mark) => {
      const parent = mark.parentNode;
      parent.replaceChild(document.createTextNode(mark.textContent), mark);
      parent.normalize();
    });
    const needle = String(term || '').trim();
    if (needle.length < 1) return;

    const walker = document.createTreeWalker(article, NodeFilter.SHOW_TEXT);
    const targets = [];
    while (walker.nextNode()) {
      const node = walker.currentNode;
      if (node.nodeValue.toLowerCase().includes(needle.toLowerCase())) targets.push(node);
    }
    let first = null;
    targets.forEach((node) => {
      const fragment = document.createDocumentFragment();
      const text = node.nodeValue;
      const lower = text.toLowerCase();
      const lowerNeedle = needle.toLowerCase();
      let cursor = 0;
      let found = lower.indexOf(lowerNeedle, cursor);
      while (found !== -1) {
        fragment.appendChild(document.createTextNode(text.slice(cursor, found)));
        const mark = document.createElement('mark');
        mark.textContent = text.slice(found, found + needle.length);
        fragment.appendChild(mark);
        if (!first) first = mark;
        cursor = found + needle.length;
        found = lower.indexOf(lowerNeedle, cursor);
      }
      fragment.appendChild(document.createTextNode(text.slice(cursor)));
      node.parentNode.replaceChild(fragment, node);
    });
    if (first) first.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }

  outline.addEventListener('click', (event) => {
    const link = event.target.closest('a[data-target]');
    if (!link) return;
    event.preventDefault();
    jumpTo(link.dataset.target);
  });

  // 문서 안의 목차 링크 · `[9](#9-스케줄-걸기)` 같은 것들
  article.addEventListener('click', (event) => {
    const link = event.target.closest('a[data-doc-anchor]');
    if (!link) return;
    event.preventDefault();
    jumpTo(decodeURIComponent(link.getAttribute('href').slice(1)));
  });

  let searchTimer = 0;
  searchInput?.addEventListener('input', () => {
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => applySearch(searchInput.value), 220);
  });

  reloadButton?.addEventListener('click', () => load(true));

  window.addEventListener('scroll', () => {
    if (!panel.classList.contains('hidden')) trackScroll();
  }, { passive: true });

  // 탭을 눌러 이 화면이 보일 때 읽는다 · 패널의 `hidden` 이 곧 신호다
  new MutationObserver(() => {
    if (!panel.classList.contains('hidden')) load(false);
  }).observe(panel, { attributes: true, attributeFilter: ['class'] });

  if (!panel.classList.contains('hidden')) load(false);
}
