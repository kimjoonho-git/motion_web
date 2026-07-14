import {
  createMidiBank,
  deleteMidiBank,
  fetchMidiMonitor,
  selectMidiBank,
  updateMidiBank,
} from './api.js?v=20260714-midi-banks';

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
    filter_level: 0,
  };
}

function escapeAttribute(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('"', '&quot;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;');
}

function requireSuccess(payload) {
  if (payload?.success === false) {
    throw new Error(payload.message || 'MIDI 뱅크 요청 실패');
  }
  return payload;
}

export function createMidiMonitorController({ el }) {
  let status = null;
  let mappingDraft = Array.from({ length: CHANNEL_COUNT }, (_, channel) => defaultMapping(channel));
  let mappingLoaded = false;
  let loading = false;
  let activeBankId = '';
  let bankNameDraft = 'Bank 1';
  let banks = [];

  function channelsOf(nextStatus = status) {
    return Array.isArray(nextStatus?.channels) ? nextStatus.channels : [];
  }

  function setStatus(nextStatus, { updateMapping = false } = {}) {
    if (!nextStatus || typeof nextStatus !== 'object') return;
    const nextBankId = String(nextStatus.active_bank_id || '');
    const bankChanged = Boolean(nextBankId && nextBankId !== activeBankId);
    status = nextStatus;
    banks = Array.isArray(nextStatus.banks) ? nextStatus.banks : [];
    activeBankId = nextBankId || activeBankId;
    const channels = channelsOf(nextStatus);
    const activeMappings = Array.isArray(nextStatus?.active_bank?.mappings)
      ? nextStatus.active_bank.mappings
      : channels;
    if ((updateMapping || bankChanged || !mappingLoaded) && activeMappings.length) {
      mappingDraft = Array.from({ length: CHANNEL_COUNT }, (_, channel) => {
        const item = activeMappings.find((entry) => Number(entry?.channel) === channel) || defaultMapping(channel);
        return {
          channel,
          enabled: item.enabled !== false,
          motion_id: String(item.motion_id ?? channel + 1),
          min_deg: numberValue(item.min_deg, -180),
          max_deg: numberValue(item.max_deg, 180),
          reversed: Boolean(item.reversed),
          filter_level: numberValue(item.filter_level, 0),
        };
      });
      bankNameDraft = String(nextStatus?.active_bank?.name || activeBankId || 'Bank 1');
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
      <td><input type="number" inputmode="decimal" min="0" max="1" step="0.05" data-midi-field="filter_level" value="${item.filter_level}"></td>
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
      const count = banks.length || 1;
      el.midiMappingPath.textContent = `메모리 전용 뱅크 ${count}/${status?.max_banks || 8} · 노드 재시작 시 초기화 · 파일에 저장하지 않음`;
    }
    if (el.midiBankSelect) {
      const optionsKey = banks.map((bank) => `${bank.bank_id}:${bank.name}`).join('|');
      if (el.midiBankSelect.dataset.optionsKey !== optionsKey) {
        el.midiBankSelect.innerHTML = banks.map((bank) => (
          `<option value="${escapeAttribute(bank.bank_id)}">${escapeAttribute(bank.name)}</option>`
        )).join('');
        el.midiBankSelect.dataset.optionsKey = optionsKey;
      }
      el.midiBankSelect.value = activeBankId;
      el.midiBankSelect.disabled = loading || banks.length === 0;
    }
    if (el.midiBankName && document.activeElement !== el.midiBankName) {
      el.midiBankName.value = bankNameDraft;
    }
    if (el.midiBankName) el.midiBankName.disabled = loading;
    if (el.addMidiBankButton) el.addMidiBankButton.disabled = loading || banks.length >= (status?.max_banks || 8);
    if (el.deleteMidiBankButton) el.deleteMidiBankButton.disabled = loading || banks.length <= 1;
    if (el.refreshMidiMonitorButton) el.refreshMidiMonitorButton.disabled = loading;
    if (el.saveMidiMappingButton) el.saveMidiMappingButton.disabled = loading || !activeBankId;
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
    } else if (field === 'min_deg' || field === 'max_deg' || field === 'filter_level') {
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
      setStatus(requireSuccess(await fetchMidiMonitor()), { updateMapping: !mappingLoaded });
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
      || !Number.isFinite(Number(item.filter_level))
      || Number(item.filter_level) < 0
      || Number(item.filter_level) > 1
    ));
    if (invalid) {
      status = {
        ...(status || {}),
        message: `채널 ${invalid.channel + 1}: Motion ID, 서로 다른 Min/Max, 필터 0~1을 확인하세요`,
      };
      render();
      return;
    }
    loading = true;
    render();
    try {
      const payload = requireSuccess(await updateMidiBank(activeBankId, {
        name: bankNameDraft,
        mappings: mappingDraft,
      }));
      setStatus(payload, { updateMapping: true });
    } catch (error) {
      status = {
        ...(status || {}),
        message: `뱅크 설정 적용 실패: ${error?.message || error}`,
      };
    } finally {
      loading = false;
      render();
    }
  }

  async function changeBank() {
    const bankId = String(el.midiBankSelect?.value || '');
    if (!bankId || bankId === activeBankId) return;
    loading = true;
    render();
    try {
      setStatus(requireSuccess(await selectMidiBank(bankId)), { updateMapping: true });
    } catch (error) {
      status = { ...(status || {}), message: `뱅크 전환 실패: ${error?.message || error}` };
    } finally {
      loading = false;
      render();
    }
  }

  async function addBank() {
    loading = true;
    render();
    try {
      const name = `Bank ${banks.length + 1}`;
      setStatus(requireSuccess(await createMidiBank({ name })), { updateMapping: true });
    } catch (error) {
      status = { ...(status || {}), message: `뱅크 추가 실패: ${error?.message || error}` };
    } finally {
      loading = false;
      render();
    }
  }

  async function removeBank() {
    if (!activeBankId || banks.length <= 1) return;
    const bankName = bankNameDraft || activeBankId;
    if (!window.confirm(`'${bankName}' 뱅크를 삭제할까요?`)) return;
    loading = true;
    render();
    try {
      setStatus(requireSuccess(await deleteMidiBank(activeBankId)), { updateMapping: true });
    } catch (error) {
      status = { ...(status || {}), message: `뱅크 삭제 실패: ${error?.message || error}` };
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
    el.midiBankSelect?.addEventListener('change', changeBank);
    el.midiBankName?.addEventListener('input', (event) => {
      bankNameDraft = event.target.value;
    });
    el.addMidiBankButton?.addEventListener('click', addBank);
    el.deleteMidiBankButton?.addEventListener('click', removeBank);
  }

  bindEvents();
  renderRows(true);
  render();

  return {
    refresh,
    renderSnapshot: (payload) => setStatus(payload),
  };
}
