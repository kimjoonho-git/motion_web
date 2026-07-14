import { fetchMidiMonitor, saveMidiMapping } from './api.js?v=20260714-midi-monitor';

const MIDI_MAX = 16383;
const CHANNEL_COUNT = 8;

function numberValue(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function defaultMapping(channel) {
  return {
    channel,
    enabled: true,
    motion_id: String(channel + 1),
    min_deg: -180,
    max_deg: 180,
    reversed: false,
  };
}

function escapeAttribute(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('"', '&quot;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;');
}

export function createMidiMonitorController({ el }) {
  let status = null;
  let mappingDraft = Array.from({ length: CHANNEL_COUNT }, (_, channel) => defaultMapping(channel));
  let mappingLoaded = false;
  let loading = false;

  function channelsOf(nextStatus = status) {
    return Array.isArray(nextStatus?.channels) ? nextStatus.channels : [];
  }

  function setStatus(nextStatus, { updateMapping = false } = {}) {
    if (!nextStatus || typeof nextStatus !== 'object') return;
    status = nextStatus;
    const channels = channelsOf(nextStatus);
    if ((updateMapping || !mappingLoaded) && channels.length) {
      mappingDraft = Array.from({ length: CHANNEL_COUNT }, (_, channel) => {
        const item = channels.find((entry) => Number(entry?.channel) === channel) || defaultMapping(channel);
        return {
          channel,
          enabled: item.enabled !== false,
          motion_id: String(item.motion_id ?? channel + 1),
          min_deg: numberValue(item.min_deg, -180),
          max_deg: numberValue(item.max_deg, 180),
          reversed: Boolean(item.reversed),
        };
      });
      mappingLoaded = true;
      renderRows(true);
    }
    render();
  }

  function rowHtml(item) {
    const channel = item.channel;
    return `<tr data-midi-channel="${channel}">
      <td><input type="checkbox" data-midi-field="enabled" ${item.enabled ? 'checked' : ''}></td>
      <td>${channel + 1}</td>
      <td><input class="midi-motion-id-input" type="text" data-midi-field="motion_id" value="${escapeAttribute(item.motion_id)}"></td>
      <td class="midi-live-value" data-midi-output="raw">0</td>
      <td class="midi-live-value" data-midi-output="ratio">0.00%</td>
      <td><input type="number" inputmode="decimal" step="0.1" data-midi-field="min_deg" value="${item.min_deg}"></td>
      <td><input type="number" inputmode="decimal" step="0.1" data-midi-field="max_deg" value="${item.max_deg}"></td>
      <td><input type="checkbox" data-midi-field="reversed" ${item.reversed ? 'checked' : ''}></td>
      <td class="midi-live-value midi-motion-value" data-midi-output="motion_deg">-</td>
      <td data-midi-output="touch">-</td>
    </tr>`;
  }

  function renderRows(force = false) {
    if (!el.midiMonitorRows) return;
    if (force || !el.midiMonitorRows.querySelector('[data-midi-channel]')) {
      el.midiMonitorRows.innerHTML = mappingDraft.map(rowHtml).join('');
    }
    const channels = channelsOf();
    mappingDraft.forEach((mapping) => {
      const row = el.midiMonitorRows.querySelector(`[data-midi-channel="${mapping.channel}"]`);
      if (!row) return;
      const live = channels.find((item) => Number(item?.channel) === mapping.channel);
      const raw = Math.max(0, Math.min(MIDI_MAX, numberValue(live?.raw_value, 0)));
      const confirmed = live?.value_confirmed !== false;
      const rawRatio = raw / MIDI_MAX;
      const ratio = mapping.reversed ? 1 - rawRatio : rawRatio;
      const converted = mapping.enabled
        ? mapping.min_deg + ((mapping.max_deg - mapping.min_deg) * ratio)
        : null;
      const rawCell = row.querySelector('[data-midi-output="raw"]');
      const ratioCell = row.querySelector('[data-midi-output="ratio"]');
      const motionCell = row.querySelector('[data-midi-output="motion_deg"]');
      const touchCell = row.querySelector('[data-midi-output="touch"]');
      if (rawCell) rawCell.textContent = Math.round(raw).toLocaleString('ko-KR');
      if (ratioCell) ratioCell.textContent = `${(rawRatio * 100).toFixed(2)}%`;
      if (motionCell) {
        motionCell.textContent = !confirmed
          ? '슬라이더 이동 필요'
          : converted === null ? '사용 안 함' : `${converted.toFixed(3)}°`;
      }
      if (touchCell) {
        const touched = Boolean(live?.touch);
        touchCell.textContent = touched ? '터치' : '-';
        touchCell.classList.toggle('midi-touch-active', touched);
      }
    });
  }

  function render() {
    const connected = Boolean(status?.connected);
    if (el.midiConnectionState) {
      el.midiConnectionState.textContent = connected ? '연결됨' : '연결 대기';
      el.midiConnectionState.classList.toggle('status-ok', connected);
      el.midiConnectionState.classList.toggle('status-bad', !connected);
    }
    if (el.midiMotorOutputState) {
      el.midiMotorOutputState.textContent = status?.motor_output_enabled ? '활성' : '사용 안 함';
    }
    if (el.midiMonitorMessage) {
      el.midiMonitorMessage.textContent = loading
        ? 'MIDI 상태 확인 중'
        : status?.message || 'MIDI 모니터 노드 상태 수신 대기';
    }
    if (el.midiMappingPath) {
      el.midiMappingPath.textContent = status?.mapping_file
        ? `변환 설정: ${status.mapping_file}`
        : '변환 설정 파일은 MIDI 모니터 노드에서 관리합니다';
    }
    if (el.refreshMidiMonitorButton) el.refreshMidiMonitorButton.disabled = loading;
    if (el.saveMidiMappingButton) el.saveMidiMappingButton.disabled = loading;
    renderRows();
  }

  function updateDraftFromRow(target) {
    const row = target.closest('[data-midi-channel]');
    const field = target.dataset.midiField;
    if (!row || !field) return;
    const channel = Number(row.dataset.midiChannel);
    const item = mappingDraft[channel];
    if (!item) return;
    if (target.type === 'checkbox') {
      item[field] = target.checked;
    } else if (field === 'min_deg' || field === 'max_deg') {
      item[field] = numberValue(target.value, item[field]);
    } else {
      item[field] = target.value;
    }
    renderRows();
  }

  async function refresh() {
    loading = true;
    render();
    try {
      setStatus(await fetchMidiMonitor(), { updateMapping: !mappingLoaded });
    } catch (error) {
      status = {
        connected: false,
        motor_output_enabled: false,
        message: `MIDI 상태 확인 실패: ${error?.message || error}`,
      };
    } finally {
      loading = false;
      render();
    }
  }

  async function saveMapping() {
    const invalid = mappingDraft.find((item) => (
      !String(item.motion_id || '').trim()
      || !Number.isFinite(Number(item.min_deg))
      || !Number.isFinite(Number(item.max_deg))
      || Math.abs(Number(item.max_deg) - Number(item.min_deg)) < 1e-9
    ));
    if (invalid) {
      status = {
        ...(status || {}),
        message: `채널 ${invalid.channel + 1}: Motion ID와 서로 다른 Min/Max 각도를 확인하세요`,
      };
      render();
      return;
    }
    loading = true;
    render();
    try {
      const payload = await saveMidiMapping({ mappings: mappingDraft });
      setStatus(payload, { updateMapping: true });
    } catch (error) {
      status = {
        ...(status || {}),
        message: `변환 설정 저장 실패: ${error?.message || error}`,
      };
    } finally {
      loading = false;
      render();
    }
  }

  function bindEvents() {
    el.midiMonitorRows?.addEventListener('input', (event) => updateDraftFromRow(event.target));
    el.midiMonitorRows?.addEventListener('change', (event) => updateDraftFromRow(event.target));
    el.refreshMidiMonitorButton?.addEventListener('click', refresh);
    el.saveMidiMappingButton?.addEventListener('click', saveMapping);
  }

  bindEvents();
  renderRows(true);
  render();

  return {
    refresh,
    renderSnapshot: (payload) => setStatus(payload),
  };
}
