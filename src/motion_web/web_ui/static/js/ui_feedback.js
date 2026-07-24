const FEEDBACK_SELECTOR = '.registry-message, [data-ui-feedback]';

const ERROR_PATTERN = /(실패|오류|거부|잘못|충돌|중단됨|불일치)/;
const WARNING_PATTERN = /(주의|경고|확인 불가|저장되지 않|변경 있음|재시작 필요)/;
const BUSY_PATTERN = /(처리 중|확인 중|불러오는 중|갱신 중|저장 중|업로드 중|삭제 중|검색 중|연결 중|요청 중|수신 대기)/;
const SUCCESS_PATTERN = /(완료|성공|저장됨|연결됨|적용됨|선택됨|추가했습니다|삭제했습니다|갱신했습니다|불러왔습니다)/;

export function feedbackState(message, { error = false } = {}) {
  const text = String(message || '').trim();
  if (error || ERROR_PATTERN.test(text)) return 'error';
  if (WARNING_PATTERN.test(text)) return 'warning';
  if (BUSY_PATTERN.test(text)) return 'busy';
  if (SUCCESS_PATTERN.test(text)) return 'success';
  return 'neutral';
}

export function applyFeedbackState(element) {
  if (!element) return 'neutral';
  const explicitError = element.classList?.contains('error-text')
    || element.dataset?.state === 'error';
  const state = feedbackState(element.textContent, { error: explicitError });
  element.dataset.feedbackState = state;
  if (!element.hasAttribute?.('aria-live')) element.setAttribute?.('aria-live', 'polite');
  if (!element.hasAttribute?.('role')) element.setAttribute?.('role', 'status');
  return state;
}

function updateFeedbackTree(root) {
  if (!root) return;
  if (root.matches?.(FEEDBACK_SELECTOR)) applyFeedbackState(root);
  root.querySelectorAll?.(FEEDBACK_SELECTOR).forEach(applyFeedbackState);
}

export function installFeedbackPresentation(root = document) {
  updateFeedbackTree(root);
  if (typeof MutationObserver === 'undefined') return () => {};
  const observer = new MutationObserver((records) => {
    records.forEach((record) => {
      const target = record.type === 'characterData' ? record.target.parentElement : record.target;
      const feedback = target?.closest?.(FEEDBACK_SELECTOR);
      if (feedback) applyFeedbackState(feedback);
      record.addedNodes?.forEach(updateFeedbackTree);
    });
  });
  observer.observe(root, { childList: true, characterData: true, subtree: true });
  return () => observer.disconnect();
}
