import test from 'node:test';
import assert from 'node:assert/strict';

import { createWorkspaceUpdateController } from '../static/js/workspace_update.js';

function element() {
  const classes = new Set();
  return {
    textContent: '', disabled: false, title: '', hidden: false,
    classList: {
      toggle(name, enabled) {
        if (enabled) classes.add(name);
        else classes.delete(name);
      },
      contains(name) { return classes.has(name); },
    },
  };
}

function fixture({ check, status, start, confirmed = true } = {}) {
  const elements = {
    summary: element(), current: element(), target: element(), phase: element(),
    checkButton: element(), startButton: element(), log: element(),
    logCaption: element(),
  };
  const calls = {
    start: 0, finished: 0, alerts: [], intervals: [], cleared: 0,
    reloaded: 0, timeouts: [],
  };
  const controller = createWorkspaceUpdateController({
    elements,
    api: {
      check: check || (async () => ({ fetched: true, up_to_date: true })),
      status: status || (async () => ({ status: 'idle' })),
      start: start || (async () => { calls.start += 1; return { success: true }; }),
    },
    confirm: async () => confirmed,
    alert: (message) => calls.alerts.push(message),
    onFinished: () => { calls.finished += 1; },
    timers: {
      setInterval: (fn) => { calls.intervals.push(fn); return calls.intervals.length; },
      clearInterval: () => { calls.cleared += 1; },
      setTimeout: (fn) => { calls.timeouts.push(fn); return calls.timeouts.length; },
    },
    now: () => 1_000_000_000_000,
    reload: () => { calls.reloaded += 1; },
  });
  return { controller, elements, calls };
}

const BEHIND = {
  fetched: true, up_to_date: false, behind: 3, current: 'aaaaaaaaaa',
  target: 'bbbbbbbbbb', message: '중계 배선', blocked_reason: '',
};

// --------------------------------------------------------------------- //
// 무엇을 받을지
// --------------------------------------------------------------------- //

test('뒤처져 있으면 업데이트를 시작할 수 있다', async () => {
  const { controller, elements } = fixture({ check: async () => BEHIND });
  await controller.refresh();

  assert.equal(elements.current.textContent, 'aaaaaaa');
  assert.equal(elements.target.textContent, 'bbbbbbb');
  assert.match(elements.summary.textContent, /3개 뒤처짐/);
  assert.equal(elements.startButton.disabled, false);
});

test('최신이면 시작 버튼이 꺼진다', async () => {
  const { controller, elements } = fixture({
    check: async () => ({ ...BEHIND, up_to_date: true, behind: 0 }),
  });
  await controller.refresh();

  assert.equal(elements.summary.textContent, '최신입니다');
  assert.equal(elements.startButton.disabled, true);
});

test('막힌 이유가 있으면 그 이유를 그대로 보여 주고 못 누르게 한다', async () => {
  const { controller, elements } = fixture({
    check: async () => ({ ...BEHIND, blocked_reason: '모션 재생 중에는 업데이트할 수 없습니다' }),
  });
  await controller.refresh();

  assert.equal(elements.summary.textContent, '모션 재생 중에는 업데이트할 수 없습니다');
  assert.equal(elements.startButton.disabled, true);
  assert.equal(elements.summary.classList.contains('warning-text'), true);
});

// --------------------------------------------------------------------- //
// 진행 · 웹이 멈췄다 돌아오는 구간이 있다
// --------------------------------------------------------------------- //

test('단계를 사람 말로 보여 준다', () => {
  const { controller, elements } = fixture();
  const running = controller.renderProgress({
    status: 'running', phase: 'building', message: '빌드 중 · 몇 분 걸립니다',
  });

  assert.equal(running, true);
  assert.equal(elements.phase.textContent, '빌드 중');
  assert.equal(elements.checkButton.disabled, true);
});

test('응답이 없는 구간은 오류가 아니라 재시작 중이다', async () => {
  /** 빌드하는 동안 웹 브리지 자신이 멈춘다 · 여기서 오류를 띄우면 사용자가
   * 새로 고치거나 다시 누른다 · 폴링도 멈추면 안 된다. */
  const { controller, elements, calls } = fixture({
    status: async () => { throw new Error('Failed to fetch'); },
  });

  await controller.poll();

  assert.match(elements.phase.textContent, /서비스 재시작 중/);
  assert.match(elements.summary.textContent, /빌드 중입니다/);
  assert.equal(calls.cleared, 0, '폴링을 멈추면 돌아온 뒤를 못 본다');
});

test('끝나면 폴링을 멈추고 버전을 다시 읽는다', async () => {
  let finished = false;
  const { controller, calls } = fixture({
    status: async () => (finished
      ? { status: 'success', phase: 'done', message: '완료' }
      : { status: 'running', phase: 'building', message: '빌드 중' }),
  });

  await controller.init();
  assert.equal(calls.intervals.length, 1, '진행 중이면 지켜봐야 한다');
  finished = true;
  await calls.intervals[0]();

  assert.equal(calls.finished, 1);
  assert.ok(calls.cleared > 0, '끝났는데 계속 물어보면 안 된다');
});

test('실패는 눈에 띄게 남는다', () => {
  const { controller, elements } = fixture();
  controller.renderProgress({
    status: 'failure', phase: 'failed', message: '업데이트 실패 · 기록을 확인하세요',
    log_tail: '· 10:00:00 빌드\nerror: 뭔가 깨졌다',
  });

  assert.equal(elements.summary.classList.contains('warning-text'), true);
  assert.equal(elements.log.hidden, false);
  assert.match(elements.log.textContent, /error/);
});

// --------------------------------------------------------------------- //
// 시작 · 사람이 확인해야 한다
// --------------------------------------------------------------------- //

test('확인 창에서 취소하면 아무 일도 안 한다', async () => {
  const { controller, calls } = fixture({ confirmed: false });
  await controller.start();

  assert.equal(calls.start, 0);
  assert.equal(calls.intervals.length, 0);
});

test('거절당하면 그 말을 보여 주고 다시 확인한다', async () => {
  let checked = 0;
  const { controller, calls } = fixture({
    check: async () => { checked += 1; return BEHIND; },
    start: async () => { throw new Error('모터 동작 중에는 업데이트할 수 없습니다'); },
  });

  await controller.start();

  assert.deepEqual(calls.alerts, ['모터 동작 중에는 업데이트할 수 없습니다']);
  assert.ok(checked >= 1, '막힌 뒤 화면이 현재 상태를 다시 읽어야 한다');
});

test('화면을 새로 열어도 하던 업데이트를 이어서 본다', async () => {
  const { controller, elements, calls } = fixture({
    status: async () => ({ status: 'running', phase: 'pulling', message: '코드 받는 중' }),
  });

  await controller.init();

  assert.equal(elements.phase.textContent, '코드 받는 중');
  assert.equal(calls.intervals.length, 1, '이어서 지켜봐야 한다');
});


// --------------------------------------------------------------------- //
// 지난 기록이 지금 것처럼 보이면 안 된다 · §6-97
// --------------------------------------------------------------------- //

test('누르는 순간 지난 기록을 지운다', async () => {
  /** 실패로 끝났던 기록이 남아 있으면 누르자마자 "실패" 로 보인다 ·
   * 실제로 그렇게 보였다. */
  const { controller, elements, calls } = fixture({
    status: async () => ({ status: 'running', phase: 'building', message: '빌드 중' }),
  });
  controller.renderProgress({
    status: 'failure', phase: 'failed', message: '업데이트 실패',
    log_tail: '지난 기록',
  });

  await controller.start();

  assert.notEqual(elements.summary.textContent, '업데이트 실패');
  assert.equal(elements.summary.classList.contains('warning-text'), false);
  assert.ok(!/지난 기록/.test(elements.log.textContent), '옛 기록이 남았다');
  assert.ok(calls.intervals.length > 0, '지켜보기를 시작하지 않았다');
});

test('시작하면 곧바로 한 번 확인한다', async () => {
  let asked = 0;
  const { controller } = fixture({
    status: async () => { asked += 1; return { status: 'running', phase: 'pulling' }; },
  });

  await controller.start();

  assert.equal(asked, 1, '첫 확인을 2초 뒤로 미뤘다');
});

test('언제 적힌 상태인지 함께 보여 준다', () => {
  const { controller, elements } = fixture();
  controller.renderProgress({
    status: 'running', phase: 'building', updated_at: 1_000_000_000 - 5,
  });

  assert.equal(elements.phase.textContent, '빌드 중 · 5초 전');
});

test('오래된 기록은 오래됐다고 보인다', () => {
  const { controller, elements } = fixture();
  controller.renderProgress({
    status: 'failure', phase: 'failed', updated_at: 1_000_000_000 - 600,
  });

  assert.match(elements.phase.textContent, /10분 전/);
});


test('기록이 지금 것인지 지난 것인지 상자에 적는다', () => {
  /** 기록만 보고는 알 수 없다 · "이 로그가 최신인지 확신이 없다" 는 말이
   * 그래서 나왔다. */
  const { controller, elements } = fixture();

  controller.renderProgress({
    status: 'running', phase: 'building', log_tail: '빌드 중',
    updated_at: 1_000_000_000 - 3,
  });
  assert.equal(elements.logCaption.textContent, '진행 중 기록 · 3초 전');

  controller.renderProgress({
    status: 'failure', phase: 'failed', log_tail: '오류',
    updated_at: 1_000_000_000 - 720,
  });
  assert.match(elements.logCaption.textContent, /지난 기록 · 12분 전/);
});


test('요청이 늦어도 화면은 곧바로 움직인다', async () => {
  /** 시작 요청은 원격을 한 번 물어보므로 몇 초 걸린다 · 그때까지 기다렸다
   * 지켜보기 시작하면 "시작하는 중 · 0초 전" 에서 멈춘 것처럼 보인다 ·
   * 실제로 그렇게 보였다. */
  let released;
  const slow = new Promise((resolve) => { released = resolve; });
  const { controller, calls } = fixture({ start: async () => { await slow; } });

  const pending = controller.start();
  await Promise.resolve();

  assert.ok(calls.intervals.length > 0, '요청을 기다리느라 지켜보지 않는다');
  released();
  await pending;
});

test('요청이 거절되면 지켜보기를 멈춘다', async () => {
  const { controller, calls } = fixture({
    start: async () => { throw new Error('모터 동작 중입니다'); },
  });

  await controller.start();

  assert.ok(calls.cleared > 0, '거절됐는데 계속 물어본다');
  assert.deepEqual(calls.alerts, ['모터 동작 중입니다']);
});


// --------------------------------------------------------------------- //
// 기다리는 동안과 끝난 뒤 · §6-97
// --------------------------------------------------------------------- //

test('웹이 멈춘 구간에는 경과 시간을 보여 준다', () => {
  /** 빌드 동안에는 웹 서버 자신이 멈춰 화면이 아무것도 못 받는다 ·
   * 아무 표시가 없으면 무기한 대기로 보인다. */
  const { controller, elements } = fixture();

  controller.renderProgress({ status: 'running' }, { disconnected: true });

  assert.match(elements.phase.textContent, /서비스 재시작 중/);
  assert.match(elements.summary.textContent, /빌드 중입니다 · 웹이 다시 뜨면/);
});

test('끝나면 끝났다고 말하고 화면을 새로 불러온다', async () => {
  let finished = false;
  const { controller, elements, calls } = fixture({
    status: async () => (finished
      ? { status: 'success', phase: 'done', message: '업데이트 완료' }
      : { status: 'running', phase: 'building' }),
    check: async () => ({ fetched: true, up_to_date: true, behind: 0 }),
  });

  await controller.start();
  finished = true;
  await calls.intervals[0]();

  assert.match(elements.summary.textContent, /업데이트 완료/);
  assert.match(elements.summary.textContent, /새로 불러옵니다/);
  assert.equal(calls.timeouts.length, 1, '새로 불러오기를 걸지 않았다');
  calls.timeouts[0]();
  assert.equal(calls.reloaded, 1);
});

test('완료 문구가 "최신입니다" 에 덮이지 않는다', async () => {
  /** 확인을 나중에 하면 완료 문구가 덮여 사용자는 끝난 줄 모른다. */
  let finished = false;
  const { controller, elements } = fixture({
    status: async () => (finished
      ? { status: 'success', phase: 'done' }
      : { status: 'running', phase: 'building' }),
    check: async () => ({ fetched: true, up_to_date: true, behind: 0 }),
  });

  await controller.start();
  finished = true;
  await controller.poll();

  assert.notEqual(elements.summary.textContent, '최신입니다');
});
