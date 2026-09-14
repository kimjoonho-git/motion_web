/**
 * 소프트웨어 업데이트 화면 · §6-97
 *
 * 빌드하는 동안 **웹 브리지 자신이 멈춘다** · 그래서 이 화면에는 다른 화면에
 * 없는 두 가지가 있다 ·
 *
 *   · 요청이 실패하는 것이 **정상인 구간**이 있다 · 그때는 "서비스 재시작 중"
 *     으로 보여 주고 계속 물어본다 · 오류로 보여 주면 사용자가 새로 고치거나
 *     다시 누른다
 *   · 진행 상황은 서버의 기억이 아니라 디스크에서 온다 · 그래서 화면을 새로
 *     열어도 **하던 일을 이어서** 보여 줄 수 있다
 */

const UPDATE_PHASE_LABEL = {
  starting: '시작하는 중',
  checking: '확인 중',
  stopping: '서비스 정지',
  pulling: '코드 받는 중',
  building: '빌드 중',
  installing: '서비스 시작',
  rollback: '되돌리는 중',
  done: '완료',
  needs_attention: '설치만 남음',
  failed: '실패',
  blocked: '시작하지 못함',
  spawn: '시작하지 못함',
};

const UPDATE_CONFIRM_MESSAGE = '최신 main 을 받아 다시 빌드합니다.\n\n'
  + '웹·모터 제어·PC 연동 서비스가 모두 멈췄다가 빌드가 끝난 뒤 다시 시작됩니다.\n'
  + '빌드 동안 화면이 끊겼다가 저절로 돌아옵니다.\n'
  + '모든 모션이 정지됐고 장비가 안전한지 확인했습니까?';

const POLL_INTERVAL_MS = 2000;

function shortCommit(value) {
  return String(value || '').slice(0, 7) || '-';
}

function setText(element, value) {
  if (element) element.textContent = value;
}

export function createWorkspaceUpdateController({
  elements = {},
  api,
  confirm,
  alert,
  onFinished = () => {},
  timers = { setInterval, clearInterval },
} = {}) {
  let pollTimer = null;

  function renderCheck(check = {}) {
    const blocked = String(check.blocked_reason || '');
    const behind = Number(check.behind || 0);
    setText(elements.current, shortCommit(check.current));
    setText(elements.target, shortCommit(check.target));
    setText(
      elements.summary,
      blocked || (check.up_to_date
        ? '최신입니다'
        : `${behind}개 뒤처짐 · ${String(check.message || '')}`),
    );
    elements.summary?.classList?.toggle('warning-text', Boolean(blocked));
    if (elements.startButton) {
      elements.startButton.disabled = Boolean(blocked)
        || !check.fetched
        || Boolean(check.up_to_date);
      elements.startButton.title = blocked
        || (check.up_to_date ? '받을 것이 없습니다' : '최신 main 으로 올리고 다시 빌드합니다');
    }
  }

  function renderProgress(status = {}, { disconnected = false } = {}) {
    const state = String(status.status || 'idle');
    const running = state === 'running';
    setText(
      elements.phase,
      disconnected
        ? '서비스 재시작 중'
        : (UPDATE_PHASE_LABEL[String(status.phase || '')] || String(status.phase || '') || '-'),
    );
    if (state !== 'idle') {
      setText(
        elements.summary,
        disconnected ? '웹이 다시 뜨기를 기다립니다' : String(status.message || ''),
      );
      elements.summary?.classList?.toggle('warning-text', state === 'failure');
    }
    if (elements.log && !disconnected) {
      const tail = String(status.log_tail || '');
      elements.log.hidden = !tail;
      elements.log.textContent = tail;
    }
    if (running && elements.startButton) elements.startButton.disabled = true;
    if (elements.checkButton) elements.checkButton.disabled = running;
    return running;
  }

  function stop() {
    if (pollTimer === null) return;
    timers.clearInterval(pollTimer);
    pollTimer = null;
  }

  async function poll() {
    let status = null;
    try {
      status = await api.status();
    } catch (error) {
      // 빌드·재시작 구간에는 웹 브리지가 없다 · 이 실패는 정상이다
      renderProgress({ status: 'running' }, { disconnected: true });
      return;
    }
    if (renderProgress(status)) return;
    stop();
    onFinished(status);
    await refresh();
  }

  function watch() {
    stop();
    pollTimer = timers.setInterval(poll, POLL_INTERVAL_MS);
  }

  async function refresh() {
    if (elements.checkButton) elements.checkButton.disabled = true;
    try {
      renderCheck(await api.check());
    } catch (error) {
      setText(elements.summary, error?.message || String(error));
    } finally {
      if (elements.checkButton) elements.checkButton.disabled = false;
    }
  }

  async function start() {
    const confirmed = await confirm(UPDATE_CONFIRM_MESSAGE, {
      title: '업데이트 및 재빌드',
      confirmLabel: '업데이트',
      tone: 'danger',
    });
    if (!confirmed) return;
    if (elements.startButton) elements.startButton.disabled = true;
    try {
      await api.start();
      watch();
    } catch (error) {
      alert(error?.message || String(error));
      await refresh();
    }
  }

  async function init() {
    // 화면을 새로 열어도 하던 일을 이어서 보여 준다 · 빌드 도중에 웹이 다시
    // 뜨면 사용자는 화면을 새로 열게 된다
    try {
      if (renderProgress(await api.status())) {
        watch();
        return;
      }
    } catch (error) {
      // 한 번도 안 했으면 상태가 없다 · 그냥 확인으로 넘어간다
    }
    await refresh();
  }

  return { init, refresh, start, stop, renderCheck, renderProgress, poll };
}
