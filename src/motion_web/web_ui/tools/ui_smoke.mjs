#!/usr/bin/env node
/**
 * 화면 회귀 검사 · 실제로 그려 보고 확인한다.
 *
 * `test/*.test.mjs` 는 소스 문자열을 정규식으로 대조한다 · 계약이 바뀐 것은
 * 잡지만 **그려진 결과**는 못 잡는다. 실제로 이런 것들을 놓쳤다.
 *
 *   - 파일명이 잘려 `테스트_비...` 로 보이던 것
 *   - 라벨이 "초기 이동 시 / 간" 으로 끊기던 것
 *   - `.hidden` 이 순서에서 져 안내줄이 안 숨던 것
 *
 * 이 도구는 그 빈 곳만 본다 · 콘솔 오류 · 가로 스크롤 · 잘린 글자 ·
 * 화면마다 있어야 할 요소.
 *
 * 사용 · 웹 브리지가 떠 있어야 한다.
 *   node tools/ui_smoke.mjs
 *   node tools/ui_smoke.mjs --widths 1680,1280 --shots /tmp/shots
 */
import { spawn } from 'node:child_process';
import { mkdtempSync, mkdirSync, writeFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { connect, fetchJson } from './cdp_client.mjs';

const args = process.argv.slice(2);
const opt = (name, fallback) => {
  const i = args.indexOf(name);
  return i >= 0 && args[i + 1] ? args[i + 1] : fallback;
};
const BASE = opt('--url', 'http://127.0.0.1:8000/');
const WIDTHS = opt('--widths', '1680,1280').split(',').map(Number);
const SHOTS = opt('--shots', '');
const PORT = Number(opt('--port', '9333'));

const CHROME = ['chromium', 'chromium-browser', 'google-chrome']
  .map((n) => ['/snap/bin/' + n, '/usr/bin/' + n]).flat().find((p) => existsSync(p));

/** 화면마다 반드시 있어야 할 요소 · 통합·이름변경으로 사라지면 여기서 걸린다. */
const REQUIRED = {
  'motion-run': ['#motionRunSummary', '#motionFileRows', '#motionRunStatus',
    '#registerMotionFileButton', '#motionRunStartButton', '#motionRunGraphCanvas'],
  'motion-mapping': ['#motionMappingSelect', '#saveMotionMappingButton'],
  'motion-midi': ['#midiBankSelect', '#saveMidiMappingButton'],
  studio: ['#studioRecordButton', '#studioExportButton'],
  manual: ['#motionTestPanel, [data-workspace-panel="manual"]'],
  monitoring: ['[data-workspace-panel="monitoring"]'],
};

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  if (!CHROME) throw new Error('크롬을 찾지 못했습니다 (chromium · google-chrome)');
  const profile = mkdtempSync(join(tmpdir(), 'ui-smoke-'));
  const chrome = spawn(CHROME, [
    '--headless=new', `--remote-debugging-port=${PORT}`, '--disable-gpu',
    '--hide-scrollbars', '--no-first-run', `--user-data-dir=${profile}`, 'about:blank',
  ], { stdio: 'ignore' });

  let cdp;
  const failures = [];
  try {
    for (let i = 0; i < 60; i += 1) {
      try { await fetchJson(PORT, '/json/version'); break; } catch { await sleep(250); }
    }
    const target = (await fetchJson(PORT, '/json/list')).find((t) => t.type === 'page');
    cdp = await connect(target.webSocketDebuggerUrl);
    await cdp.send('Page.enable');
    await cdp.send('Runtime.enable');
    await cdp.send('Log.enable');

    for (const width of WIDTHS) {
      await cdp.send('Emulation.setDeviceMetricsOverride',
        { width, height: 1000, deviceScaleFactor: 1, mobile: false });
      await cdp.send('Page.navigate', { url: BASE });
      await sleep(6000);
      cdp.drainEvents();

      const routes = (await evaluate(cdp,
        `[...document.querySelectorAll('[data-workspace-tab]')].map(b=>b.dataset.workspaceTab)`));
      for (const route of routes) {
        if (route === 'btop') continue;              // 외부 iframe · 검사 대상 아님
        await evaluate(cdp, `(()=>{const t=document.querySelector('[data-workspace-tab="${route}"]');`
          + `const g=t.closest('[data-workspace-group-panel]')?.dataset.workspaceGroupPanel;`
          + `if(g) document.querySelector('[data-workspace-group="'+g+'"]').click();`
          + `t.click(); return 1;})()`);
        await sleep(900);
        for (const problem of await inspect(cdp, route)) {
          failures.push(`${width}px · ${route} · ${problem}`);
        }
        if (SHOTS) {
          mkdirSync(SHOTS, { recursive: true });
          const shot = await cdp.send('Page.captureScreenshot',
            { format: 'png', captureBeyondViewport: true });
          writeFileSync(join(SHOTS, `${width}-${route}.png`), Buffer.from(shot.data, 'base64'));
        }
      }
      for (const problem of consoleProblems(cdp)) failures.push(`${width}px · ${problem}`);
    }
  } finally {
    cdp?.close();
    chrome.kill('SIGKILL');
  }

  if (failures.length) {
    console.error(`화면 회귀 ${failures.length}건`);
    for (const f of failures) console.error('  ✗ ' + f);
    process.exit(1);
  }
  console.log(`화면 회귀 없음 · 폭 ${WIDTHS.join(', ')}px`);
}

async function evaluate(cdp, expression) {
  const result = await cdp.send('Runtime.evaluate', { expression, returnByValue: true });
  if (result.exceptionDetails) {
    throw new Error(result.exceptionDetails.exception?.description || '평가 실패');
  }
  return result.result.value;
}

function consoleProblems(cdp) {
  const out = [];
  for (const e of cdp.drainEvents('Runtime.exceptionThrown')) {
    out.push(`예외 · ${e.params.exceptionDetails?.exception?.description?.split('\n')[0]}`);
  }
  for (const e of cdp.drainEvents('Runtime.consoleAPICalled')) {
    if (e.params.type !== 'error') continue;
    out.push(`콘솔 오류 · ${e.params.args.map((a) => a.value ?? a.description).join(' ').slice(0, 120)}`);
  }
  return out;
}

async function inspect(cdp, route) {
  const required = JSON.stringify(REQUIRED[route] || []);
  return evaluate(cdp, `(()=>{
    const problems = [];
    for (const sel of ${required}) {
      const node = document.querySelector(sel);
      if (!node) { problems.push('요소 없음 ' + sel); continue; }
      if (!node.getClientRects().length) problems.push('요소가 보이지 않음 ' + sel);
    }
    if (document.documentElement.scrollWidth > document.documentElement.clientWidth + 1) {
      problems.push('가로 스크롤 발생');
    }
    const panel = document.querySelector('[data-workspace-panel]:not(.hidden)');
    for (const node of (panel ? panel.querySelectorAll('*') : [])) {
      if (node.children.length || !node.textContent.trim()) continue;
      if (!node.getClientRects().length) continue;
      const style = getComputedStyle(node);
      // 말줄임(…)과 스크롤 상자는 넘치는 것이 정상이다
      if (style.textOverflow === 'ellipsis') continue;
      if (style.overflowX === 'auto' || style.overflowX === 'scroll') continue;
      if (node.closest('[style*="overflow"], .table-wrap, pre, canvas')) continue;
      if (node.scrollWidth > node.clientWidth + 1) {
        problems.push('글자 잘림 · ' + node.textContent.trim().slice(0, 28));
      }
    }
    return problems;
  })()`);
}

main().catch((error) => { console.error('검사 실패 ·', error.message); process.exit(2); });
