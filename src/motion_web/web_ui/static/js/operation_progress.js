const OUTCOMES = new Set(['success', 'partial', 'failure', 'timeout', 'cancelled']);

export function createOperationProgressManager({
  el,
  now = () => Date.now(),
  setTimer = (callback, delay) => window.setInterval(callback, delay),
  clearTimer = (timer) => window.clearInterval(timer),
} = {}) {
  let active = null;
  let elapsedTimer = null;

  function stopElapsedTimer() {
    if (elapsedTimer !== null) {
      clearTimer(elapsedTimer);
      elapsedTimer = null;
    }
  }

  function renderElapsed() {
    if (!el?.operationProgressElapsed || !active) return;
    const elapsedSec = Math.max(now() - active.startedAt, 0) / 1000;
    el.operationProgressElapsed.textContent = `경과 ${elapsedSec.toFixed(1)}초`;
  }

  function setVisible(visible) {
    el?.operationProgressModal?.classList.toggle('hidden', !visible);
    document.body.classList.toggle('operation-modal-open', visible);
  }

  function setLogVisible(visible) {
    el?.operationProgressLog?.classList.toggle('hidden', !visible);
    el?.operationProgressClearButton?.classList.toggle('hidden', !visible);
  }

  function setRunning(running) {
    el?.operationProgressSpinner?.classList.toggle('hidden', !running);
    if (el?.operationProgressCloseButton) {
      const cancelable = running && Boolean(active?.cancelable);
      el.operationProgressCloseButton.disabled = running && !cancelable;
      el.operationProgressCloseButton.textContent = running
        ? (cancelable ? '확인 중단' : '진행 중')
        : '완료';
    }
    if (el?.operationProgressClearButton) el.operationProgressClearButton.disabled = running;
  }

  function update({
    title,
    message,
    detail,
    phase,
  } = {}) {
    if (!active) return false;
    if (title && el?.operationProgressTitle) el.operationProgressTitle.textContent = title;
    if (message && el?.operationProgressMessage) el.operationProgressMessage.textContent = message;
    if (detail && el?.operationProgressDetail) el.operationProgressDetail.textContent = detail;
    if (phase && el?.operationProgressState) {
      el.operationProgressState.textContent = phase;
      el.operationProgressState.dataset.state = active.status;
    }
    renderElapsed();
    return true;
  }

  function clearLog({ force = false } = {}) {
    if (active?.running && !force) return false;
    el?.operationProgressLog?.replaceChildren();
    return true;
  }

  function begin({
    id,
    title,
    message = '작업을 시작하고 있습니다.',
    detail = '요청 준비 중',
    phase = '진행 중',
    mode = 'standard',
    cancelable = false,
    onCancel = null,
  }) {
    const operationId = String(id || '').trim();
    if (!operationId) throw new Error('operation progress id is required');
    if (active?.running && active.id !== operationId) return false;
    active = {
      id: operationId,
      mode,
      status: 'running',
      running: true,
      startedAt: now(),
      cancelable: Boolean(cancelable),
      onCancel: typeof onCancel === 'function' ? onCancel : null,
    };
    clearLog({ force: true });
    setLogVisible(mode === 'log');
    setVisible(true);
    setRunning(true);
    if (el?.operationProgressState) {
      el.operationProgressState.dataset.state = 'running';
    }
    update({ title, message, detail, phase });
    stopElapsedTimer();
    elapsedTimer = setTimer(renderElapsed, 200);
    return true;
  }

  function appendLog(message, state = 'running') {
    if (!active || active.mode !== 'log' || !el?.operationProgressLog) return false;
    const line = document.createElement('div');
    line.className = `operation-progress-line is-${state}`;
    line.textContent = String(message || '');
    el.operationProgressLog.appendChild(line);
    el.operationProgressLog.scrollTop = el.operationProgressLog.scrollHeight;
    return true;
  }

  function finish({
    outcome = 'success',
    title,
    message,
    detail,
    phase,
  } = {}) {
    if (!active) return false;
    const normalizedOutcome = OUTCOMES.has(outcome) ? outcome : 'failure';
    active.status = normalizedOutcome;
    active.running = false;
    stopElapsedTimer();
    setRunning(false);
    if (el?.operationProgressState) {
      el.operationProgressState.dataset.state = normalizedOutcome;
    }
    update({
      title,
      message,
      detail,
      phase: phase || {
        success: '완료',
        partial: '부분 완료',
        failure: '실패',
        timeout: '시간 초과',
        cancelled: '취소',
      }[normalizedOutcome],
    });
    return true;
  }

  function close({ force = false } = {}) {
    if (active?.running && !force && !active.cancelable) return false;
    const onCancel = active?.running && !force ? active.onCancel : null;
    stopElapsedTimer();
    active = null;
    setVisible(false);
    onCancel?.();
    return true;
  }

  function activeId() {
    return active?.id || '';
  }

  function isRunning() {
    return Boolean(active?.running);
  }

  el?.operationProgressCloseButton?.addEventListener('click', () => close());
  el?.operationProgressClearButton?.addEventListener('click', () => clearLog());
  setVisible(false);
  setLogVisible(false);

  return {
    begin,
    update,
    appendLog,
    finish,
    close,
    clearLog,
    activeId,
    isRunning,
  };
}
