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
  const calls = { start: 0, finished: 0, alerts: [], intervals: [], cleared: 0 };
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
    },
    now: () => 1_000_000_000_000,
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

  assert.equal(elements.phase.textContent, '서비스 재시작 중');
  assert.equal(elements.summary.textContent, '웹이 다시 뜨기를 기다립니다');
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
  assert.equal(checked, 1, '막힌 뒤 화면이 현재 상태를 다시 읽어야 한다');
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
