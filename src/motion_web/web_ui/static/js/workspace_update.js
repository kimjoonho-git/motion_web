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
//: 끝난 것을 읽을 시간은 주고, 새 화면으로 바꾼다
const RELOAD_DELAY_MS = 4000;

function shortCommit(value) {
  return String(value || '').slice(0, 7) || '-';
}

function setText(element, value) {
  if (element) element.textContent = value;
}

/** 시:분:초 · 언제 시작했고 언제 끝났는지 못 박는다. */
function clockText(epochSeconds) {
  const stamp = Number(epochSeconds);
  if (!Number.isFinite(stamp) || stamp <= 0) return '';
  const when = new Date(stamp * 1000);
  const two = (value) => String(value).padStart(2, '0');
  return `${two(when.getHours())}:${two(when.getMinutes())}:${two(when.getSeconds())}`;
}

/** 얼마나 지났나 · `1분 12초` */
function elapsedText(seconds) {
  const whole = Math.max(0, Math.round(seconds));
  if (whole < 60) return `${whole}초`;
  return `${Math.floor(whole / 60)}분 ${String(whole % 60).padStart(2, '0')}초`;
}

/** 언제 적힌 상태인가 · 옛 기록을 지금 것으로 오해하지 않게 한다. */
function ageText(updatedAt, now) {
  const stamp = Number(updatedAt);
  if (!Number.isFinite(stamp) || stamp <= 0) return '';
  const seconds = Math.max(0, Math.round(now / 1000 - stamp));
  if (seconds < 60) return ` · ${seconds}초 전`;
  const minutes = Math.round(seconds / 60);
  return minutes < 60 ? ` · ${minutes}분 전` : ' · 오래 전';
}

export function createWorkspaceUpdateController({
  elements = {},
  api,
  confirm,
  alert,
  onFinished = () => {},
  timers = { setInterval, clearInterval, setTimeout },
  now = () => Date.now(),
  reload = () => window.location.reload(),
} = {}) {
  let pollTimer = null;
  //: 빌드 동안에는 웹 서버 자신이 멈춰 화면이 아무것도 못 받는다 · 그 구간은
  //: 누른 시각에서 흐른 시간으로만 셀 수 있다 · 없으면 "무기한 대기" 로 보인다
  let startedAt = 0;
  //: 마지막으로 그린 것 · 1초 시계가 이것을 다시 그려 경과를 올린다
  let lastPainted = null;
  let tickTimer = null;

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
    lastPainted = { status, disconnected };
    const state = String(status.status || 'idle');
    const running = state === 'running';
    const label = UPDATE_PHASE_LABEL[String(status.phase || '')]
      || String(status.phase || '') || '-';
    // 시작 시각을 글에 박는다 · 그러면 중간에 멈춰도 언제 것인지 알 수 있다
    const began = clockText(status.started_at || startedAt);
    const since = began ? ` · 시작 ${began}` : '';
    const waiting = startedAt
      ? ` · ${elapsedText(now() / 1000 - startedAt)} 경과`
      : '';
    const ended = running ? '' : ` · ${clockText(status.updated_at)}`;
    setText(
      elements.phase,
      disconnected
        ? `서비스 재시작 중${since}${waiting}`
        : `${label}${since}${ended || ageText(status.updated_at, now())}`,
    );
    if (state !== 'idle') {
      setText(
        elements.summary,
        disconnected
          ? '빌드 중입니다 · 웹이 다시 뜨면 저절로 돌아옵니다 (보통 1~3분)'
          : String(status.message || ''),
      );
      elements.summary?.classList?.toggle('warning-text', state === 'failure');
    }
    if (elements.log && !disconnected) {
      const tail = String(status.log_tail || '');
      elements.log.hidden = !tail;
      elements.log.textContent = tail;
      if (elements.logCaption) {
        // 기록만 보고는 지금 것인지 지난 것인지 알 수 없다 · 언제 것인지 적는다
        elements.logCaption.hidden = !tail;
        elements.logCaption.textContent = tail
          ? `${running ? '진행 중 기록' : '지난 기록'}${ageText(status.updated_at, now())}`
          : '';
        elements.logCaption.classList?.toggle('warning-text', !running);
      }
    }
    if (running && elements.startButton) elements.startButton.disabled = true;
    if (elements.checkButton) elements.checkButton.disabled = running;
    return running;
  }

  function stop() {
    if (pollTimer !== null) {
      timers.clearInterval(pollTimer);
      pollTimer = null;
    }
    if (tickTimer !== null) {
      timers.clearInterval(tickTimer);
      tickTimer = null;
    }
  }

  /** 1초마다 화면만 다시 그린다 · §6-97
   *
   * 서버에서 오는 것과 **무관하게** 경과 시간이 올라가야 한다 · 확인 응답이
   * 늦거나 웹이 멈춘 구간에는 화면이 "0초 전" 에서 굳어 보인다 · 실제로
   * 그렇게 보였고, 사용자는 멈춘 줄 안다.
   */
  function tick() {
    if (!lastPainted) return;
    renderProgress(lastPainted.status, { disconnected: lastPainted.disconnected });
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
    const finishedIn = startedAt ? elapsedText(now() / 1000 - startedAt) : '';
    const done = String(status.status || '') === 'success';
    startedAt = 0;
    onFinished(status);
    // 확인을 먼저 하고 **그 뒤에** 끝난 것을 적는다 · 순서가 바뀌면 "최신입니다"
    // 가 완료 문구를 덮어 사용자는 끝난 줄 모른다
    await refresh();
    if (!done) return;
    setText(
      elements.summary,
      `업데이트 완료${finishedIn ? ` · ${finishedIn} 걸렸습니다` : ''}`
      + ' · 잠시 뒤 화면을 새로 불러옵니다',
    );
    elements.summary?.classList?.toggle('warning-text', false);
    // 새 화면 코드는 다시 불러와야 뜬다 · 사용자가 F5 를 기억하지 않아도 되게
    timers.setTimeout(reload, RELOAD_DELAY_MS);
  }

  function watch() {
    stop();
    tickTimer = timers.setInterval(tick, 1000);
    pollTimer = timers.setInterval(poll, POLL_INTERVAL_MS);
    // 첫 확인을 2초 뒤로 미루면 그동안 **지난 기록**이 화면에 남는다 ·
    // 실패로 끝났던 기록이면 누르자마자 "실패" 로 보인다
    poll();
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
    startedAt = now() / 1000;
    // 지난 기록을 먼저 지운다 · 옛 실패 글과 옛 기록이 새 작업의 것처럼 보인다
    renderProgress({
      status: 'running', phase: 'starting', message: '업데이트를 시작합니다',
      log_tail: '', updated_at: now() / 1000,
    });
    // **요청을 기다리지 않고 곧바로 지켜본다** · 시작 요청은 원격을 한 번
    // 물어보므로(fetch) 몇 초 걸릴 수 있다 · 그때까지 기다렸다 지켜보기
    // 시작하면 화면이 "시작하는 중 · 0초 전" 에서 멈춘 것처럼 보인다 ·
    // 실제로 그렇게 보였다.
    watch();
    try {
      await api.start();
    } catch (error) {
      stop();
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
