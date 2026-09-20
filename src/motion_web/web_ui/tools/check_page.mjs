// 화면을 **진짜 띄워서** 눌러 본다 · §6-222
//
// 그동안 화면 검사는 소스 글자를 훑기만 했다 · 그래서 함수 하나가 없어도
// 시험이 전부 통과했고, 사람이 버튼을 누르고 나서야 오류를 봤다.
//
//     전체 모터 검색 실패: options.nextAvailableAxis is not a function
//     전체 모터 검색 실패: scanAdoptedMessage is not defined
//
// 두 번 다 그랬다 · 여기서는 Chrome 을 띄워 실제 페이지를 열고, 버튼을
// 눌러 보고, 콘솔 오류를 그대로 받아 온다.
//
//     node --experimental-websocket tools/check_page.mjs [주소] [누를 버튼 id...]

const BASE = process.argv[2] || 'http://127.0.0.1:8000/';
const CLICKS = process.argv.slice(3);
const PORT = 9333;

const { spawn } = await import('node:child_process');
const chrome = spawn('chromium-browser', [
  '--headless', '--disable-gpu', '--no-sandbox',
  `--remote-debugging-port=${PORT}`, 'about:blank',
], { stdio: 'ignore' });

const sleep = (ms) => new Promise((done) => setTimeout(done, ms));

async function endpoint() {
  for (let attempt = 0; attempt < 40; attempt += 1) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
      const page = list.find((item) => item.type === 'page');
      if (page) return page.webSocketDebuggerUrl;
    } catch { /* 아직 안 떴다 */ }
    await sleep(250);
  }
  throw new Error('Chrome 이 뜨지 않았습니다');
}

const socket = new WebSocket(await endpoint());
await new Promise((ok) => socket.addEventListener('open', ok, { once: true }));

let nextId = 0;
const waiting = new Map();
const events = [];
socket.addEventListener('message', (event) => {
  const message = JSON.parse(event.data);
  if (message.id !== undefined) waiting.get(message.id)?.(message);
  else events.push(message);
});
const send = (method, params = {}) => new Promise((ok) => {
  const id = nextId += 1;
  waiting.set(id, ok);
  socket.send(JSON.stringify({ id, method, params }));
});

const problems = [];
await send('Runtime.enable');
await send('Network.enable');
await send('Log.enable');
await send('Page.enable');
await send('Page.navigate', { url: BASE });
await sleep(6000);

async function click(id) {
  const result = await send('Runtime.evaluate', {
    expression: `(() => {
      const el = document.getElementById(${JSON.stringify(id)});
      if (!el) return 'NOT_FOUND';
      if (el.disabled) return 'DISABLED';
      el.click();
      return 'CLICKED';
    })()`,
    awaitPromise: true,
  });
  return result.result?.result?.value;
}

for (const id of CLICKS) {
  const outcome = await click(id);
  console.log(`클릭 ${id}: ${outcome}`);
  if (outcome !== 'CLICKED') problems.push(`${id} → ${outcome}`);
  // 확인창이 뜨면 「확인」을 누른다 · 사람이 하듯이
  await sleep(1500);
  const confirmed = await click('appDialogConfirmButton');
  if (confirmed === 'CLICKED') console.log('  확인창 확인');
  await sleep(/scan/i.test(id) ? 40000 : 60000);
}

for (const event of events) {
  if (event.method === 'Runtime.exceptionThrown') {
    const detail = event.params.exceptionDetails;
    problems.push(`예외: ${detail.exception?.description || detail.text}`);
  }
  if (event.method === 'Network.responseReceived' && event.params.response.status >= 400) {
    problems.push(`HTTP ${event.params.response.status}: ${event.params.response.url}`);
  }
  if (event.method === 'Log.entryAdded' && event.params.entry.level === 'error') {
    problems.push(`콘솔 오류: ${event.params.entry.text}`);
  }
  if (event.method === 'Runtime.consoleAPICalled' && event.params.type === 'error') {
    problems.push(`console.error: ${event.params.args.map((a) => a.value ?? a.description).join(' ')}`);
  }
}

const shown = await send('Runtime.evaluate', {
  expression: `JSON.stringify({
    rows: [...document.querySelectorAll('#axisRows tr[data-axis-row]')].length,
    empty: document.querySelector('#axisRows .empty')?.textContent || '',
    message: document.getElementById('axisActionMessage')?.textContent || '',
    scanResult: document.getElementById('scanAllResult')?.textContent || '',
    dialog: document.getElementById('appDialogBody')?.textContent
      || document.querySelector('[id*=Dialog] [id*=Message], [id*=dialog] p')?.textContent || '',
  })`,
});
console.log('화면 상태:', shown.result?.result?.value);

socket.close();
chrome.kill();

if (problems.length > 0) {
  console.log('\n문제:');
  for (const line of problems) console.log(' -', line);
  process.exit(1);
}
console.log('\n오류 없음');
