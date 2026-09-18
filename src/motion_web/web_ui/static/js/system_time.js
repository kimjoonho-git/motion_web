import { fetchSystemTime } from './api.js';

/** 시간대 바꾸기 · §6-150
 *
 * 해외 설치에서 사람이 직접 해야 하는 유일한 일이다 · NTP 는 절대 시각(UTC)만
 * 맞추고 **시간대는 안 바꾼다** · PC 를 파리에 들고 가서 네트워크에 붙여도
 * `Asia/Seoul` 그대로고, 그러면 09:17 스케줄이 현지 02:17 에 돈다.
 *
 * 여기서 직접 바꾸지 않는다 · `timedatectl` 은 root 권한이 필요하고, 그 권한을
 * 웹 서비스에 주는 것이 시간대를 잘못 잡는 것보다 위험하다 · 그래서 **칠 명령을
 * 만들어 주기만** 하고, 치는 일은 사람이 아래 터미널에서 한다.
 *
 * 자동 감지도 하지 않는다 · 위치 기반 자동 시간대는 전시장 네트워크에서 자주
 * 틀리고, 더 나쁜 건 전시 중에 저절로 바뀔 수 있다는 것이다 · 틀려도 모르는
 * 것이 가장 나쁘다.
 */

const COMMAND_PLACEHOLDER = '시간대를 고르면 칠 명령이 여기 나옵니다';

function browserTimezone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || '';
  } catch (error) {
    return '';
  }
}

/** 복사 · 이 화면은 http 로 열린다 · §6-150
 *
 * `navigator.clipboard` 는 https 이거나 localhost 일 때만 있다 · 현장에서는
 * `http://172.16.x.x:8000` 으로 여니 **없다** · 그대로 부르면 조용히 아무 일도
 * 안 나고, 사람은 눌렀는데 안 붙는 이유를 모른다 · 옛 방법을 함께 둔다.
 */
async function copyText(text) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (error) {
    // 아래 옛 방법으로 넘어간다
  }
  try {
    const box = document.createElement('textarea');
    box.value = text;
    box.setAttribute('readonly', '');
    box.style.position = 'fixed';
    box.style.opacity = '0';
    document.body.appendChild(box);
    box.select();
    const done = document.execCommand('copy');
    document.body.removeChild(box);
    return done;
  } catch (error) {
    return false;
  }
}

/** 어떤 명령을 쳐야 하는가 · 규칙은 여기 하나다 · §6-150
 *
 * DOM 밖으로 빼 둔다 · 화면을 띄우지 않고도 검사할 수 있어야 한다 ·
 * 이 규칙이 틀리면 사람이 엉뚱한 명령을 치고, 왜 안 되는지 모른 채 시간이 간다.
 *
 * 돌려주는 값 · `{ command, note }`
 *   - `command` 가 비면 칠 것이 없다는 뜻이다
 *   - `note` 는 왜 비었는지 또는 무엇을 알아야 하는지
 */
export function timezoneCommand({ zones = [], chosen = '', current = '' } = {}) {
  const zone = String(chosen || '').trim();
  if (!zone) return { command: '', note: '' };
  // 목록에 없는 이름이면 명령을 만들지 않는다 · `Europe/Pari` 처럼 한 글자만
  // 틀려도 `timedatectl` 이 거부하는데, 거부 이유는 터미널에만 남는다.
  if (zones.length && !zones.includes(zone)) {
    return { command: '', note: `목록에 없는 이름입니다 · ${zone}` };
  }
  const command = `sudo timedatectl set-timezone ${zone}`;
  return {
    command,
    note: zone === current ? '이미 이 시간대입니다' : '',
  };
}

const SystemTime = {
  zones: [],
  current: '',

  async init() {
    const pick = document.getElementById('systemTimezonePick');
    if (!pick) return;                     // 이 화면이 없는 배포도 있다
    pick.addEventListener('input', () => this.renderCommand());
    document.getElementById('btnCopyTimezoneCommand')
      ?.addEventListener('click', () => this.copyCommand());
    await this.load();
  },

  async load() {
    try {
      // 서버를 부르는 길은 `api.js` 하나다 · §6-181
      const payload = await fetchSystemTime();
      this.zones = Array.isArray(payload.timezones) ? payload.timezones : [];
      this.current = payload.clock?.timezone || '';
      this.render(payload.clock || {});
    } catch (error) {
      console.warn('[SystemTime] 시간대 정보를 못 읽었습니다:', error);
    }
  },

  render(clock) {
    const now = document.getElementById('systemTimezoneNow');
    if (now) {
      const ntp = clock.ntp_synced === false
        ? ' · ⚠️ 시계가 맞춰지지 않았습니다' : '';
      now.textContent = `${this.current || '알 수 없음'} (UTC${clock.utc_offset || ''})${ntp}`;
    }

    const list = document.getElementById('systemTimezoneList');
    if (list) {
      list.innerHTML = this.zones
        .map((zone) => `<option value="${zone}"></option>`).join('');
    }

    // 브라우저 시간대를 **기본 선택값으로만** 제안한다 · 적용하지 않는다 ·
    // 현장 노트북으로 접속했다면 대개 맞고, 원격이면 사람이 고쳐 넣으면 된다.
    const pick = document.getElementById('systemTimezonePick');
    const guess = browserTimezone();
    if (pick && !pick.value && guess && this.zones.includes(guess)) {
      pick.value = guess;
    }

    const mismatch = document.getElementById('systemTimezoneMismatch');
    if (mismatch) {
      mismatch.textContent = guess && this.current && guess !== this.current
        ? `⚠️ 보고 계신 기기는 ${guess} 인데 이 PC 는 ${this.current} 입니다 · 다른 곳에서 보고 있다면 정상입니다`
        : '';
    }

    this.renderCommand();
  },

  chosen() {
    return String(document.getElementById('systemTimezonePick')?.value || '').trim();
  },

  decide() {
    return timezoneCommand({
      zones: this.zones, chosen: this.chosen(), current: this.current,
    });
  },

  renderCommand() {
    const box = document.getElementById('systemTimezoneCommand');
    if (!box) return;
    const { command, note } = this.decide();
    if (command) {
      box.textContent = note ? `${command}   # ${note}` : command;
    } else {
      box.textContent = note || COMMAND_PLACEHOLDER;
    }
    const said = document.getElementById('systemTimezoneCopied');
    if (said) said.textContent = '';
  },

  async copyCommand() {
    const said = document.getElementById('systemTimezoneCopied');
    const { command } = this.decide();
    if (!command) {
      if (said) said.textContent = '먼저 설치할 곳을 고르세요';
      return;
    }
    const done = await copyText(command);
    if (said) {
      said.textContent = done
        ? '복사했습니다 · 아래 터미널에 붙여넣으세요'
        : '복사가 막혀 있습니다 · 위 명령을 직접 끌어서 복사하세요';
    }
  },
};

// 화면이 있을 때만 붙는다 · 검사는 DOM 없이 규칙만 불러 본다
if (typeof window !== 'undefined' && typeof document !== 'undefined') {
  window.SystemTime = SystemTime;
  document.addEventListener('DOMContentLoaded', () => {
    SystemTime.init();
  });
}

export { SystemTime, browserTimezone, copyText };
