import {
  connectMidiDevice,
  createMidiBank,
  deleteMidiBank,
  disconnectMidiDevice,
  fetchMidiMonitor,
  loadMidiBanksFromFile,
  resetMidiRuntimeValues,
  selectMidiBank,
  updateMidiBank,
} from './api.js?v=20260722-motor-config-delete';

const MIDI_MAX = 16383;
const CHANNEL_COUNT = 8;
const FILTER_LEVEL_MAX = 13;
const MOTION_ID_PATTERN = /^[1-9]\d*-[1-9]\d*$/;

function numberValue(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function pathBasename(value) {
  return String(value || '').split(/[\\/]/).filter(Boolean).pop() || '모션축 설정 YAML';
}

function linkedMotionIdDraft(values) {
  const result = Array.isArray(values) ? values.slice(0, 2).map((value) => String(value || '')) : [];
  while (result.length < 2) result.push('');
  return result;
}

function defaultMapping(channel) {
  return {
    channel,
    enabled: true,
    motion_id: `1-${channel + 1}`,
    linked_motion_ids: ['', ''],
    min_percent: 0,
    max_percent: 100,
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

function invalidMappingItem(item) {
  const motionIds = [item.motion_id, ...(Array.isArray(item.linked_motion_ids) ? item.linked_motion_ids : [])]
    .map((value) => String(value || '').trim())
    .filter(Boolean);
  return motionIds.length < 1
    || motionIds.length > 3
    || motionIds.some((motionId) => !MOTION_ID_PATTERN.test(motionId))
    || new Set(motionIds).size !== motionIds.length
    || !Number.isFinite(Number(item.min_percent))
    || !Number.isFinite(Number(item.max_percent))
    || Number(item.max_percent) <= 0
    || Number(item.max_percent) > 200
    || (Number(item.max_percent) <= 100 && (
      Number(item.min_percent) < 0
      || Number(item.min_percent) >= Number(item.max_percent)
    ))
    || !Number.isInteger(Number(item.filter_level))
    || Number(item.filter_level) < 0
    || Number(item.filter_level) > FILTER_LEVEL_MAX;
}

function filterLevelOptions(selectedLevel) {
  return Array.from({ length: FILTER_LEVEL_MAX + 1 }, (_, level) => (
    `<option value="${level}" ${Number(selectedLevel) === level ? 'selected' : ''}>${level}단계</option>`
  )).join('');
}

function mappedOutput14bit(filteredValue, mapping) {
  let normalized = Math.max(0, Math.min(1, numberValue(filteredValue, 0) / MIDI_MAX));
  if (mapping.reversed) normalized = 1 - normalized;
  const minPercent = numberValue(mapping.min_percent, 0);
  const maxPercent = numberValue(mapping.max_percent, 100);
  const outputPercent = minPercent + ((maxPercent - minPercent) * normalized);
  return MIDI_MAX * Math.max(0, Math.min(100, outputPercent)) / 100;
}

export function createMidiMonitorController({ el }) {
  let status = null;
  let mappingDraft = Array.from({ length: CHANNEL_COUNT }, (_, channel) => defaultMapping(channel));
  let mappingLoaded = false;
  let loading = false;
  let activeBankId = '';
  let bankNameDraft = 'Bank 1';
  let banks = [];
  const dirtyFields = new Set();
  let editSafetyResetTimer = null;
  let editSafetyResetRunning = false;
  let editSafetyResetDone = false;

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
          motion_id: String(item.motion_id ?? `1-${channel + 1}`),
          linked_motion_ids: linkedMotionIdDraft(item.linked_motion_ids),
          min_percent: numberValue(item.min_percent, 0),
          max_percent: numberValue(item.max_percent, 100),
          reversed: Boolean(item.reversed),
          filter_level: Math.round(numberValue(item.filter_level, 0)),
        };
      });
      bankNameDraft = String(nextStatus?.active_bank?.name || activeBankId || 'Bank 1');
      mappingLoaded = true;
      dirtyFields.clear();
      editSafetyResetDone = false;
      renderRows(true);
    } else if (activeMappings.length) {
      // A physical encoder changes filter_level directly in the MIDI node.
      // Keep unrelated unsaved web edits, but mirror hardware filter changes.
      activeMappings.forEach((item) => {
        const channel = Number(item?.channel);
        if (!Number.isInteger(channel) || channel < 0 || channel >= CHANNEL_COUNT) return;
        if (!dirtyFields.has(`${channel}:filter_level`)) {
          mappingDraft[channel].filter_level = Math.round(numberValue(item.filter_level, 0));
        }
      });
    }
    render();
  }

  function rowHtml(item) {
    const channel = item.channel;
    const linked = Array.isArray(item.linked_motion_ids) ? item.linked_motion_ids : [];
    return `<tr data-midi-channel="${channel}">
      <td><input type="checkbox" data-midi-field="enabled" ${item.enabled ? 'checked' : ''}></td>
      <td>${channel + 1}</td>
      <td data-midi-output="select">비활성</td>
      <td><div class="midi-motion-id-group">
        <input class="midi-motion-id-input" type="text" pattern="[1-9]\\d*-[1-9]\\d*" title="기본 Motion ID · 예: 1-1" aria-label="기본 Motion ID" data-midi-field="motion_id" value="${escapeAttribute(item.motion_id)}">
        <input class="midi-motion-id-input" type="text" pattern="[1-9]\\d*-[1-9]\\d*" title="연동 Motion ID 2 · 비워둘 수 있음" aria-label="연동 Motion ID 2" data-midi-field="linked_motion_id_0" value="${escapeAttribute(linked[0] || '')}" placeholder="연동 2">
        <input class="midi-motion-id-input" type="text" pattern="[1-9]\\d*-[1-9]\\d*" title="연동 Motion ID 3 · 비워둘 수 있음" aria-label="연동 Motion ID 3" data-midi-field="linked_motion_id_1" value="${escapeAttribute(linked[1] || '')}" placeholder="연동 3">
      </div></td>
      <td class="midi-live-value" data-midi-output="raw">0</td>
      <td class="midi-live-value" data-midi-output="filtered">0</td>
      <td><input type="number" inputmode="decimal" min="0" max="100" step="1" data-midi-field="min_percent" value="${item.min_percent}"></td>
      <td><input type="number" inputmode="decimal" min="0.1" max="200" step="1" data-midi-field="max_percent" value="${item.max_percent}"></td>
      <td><input type="checkbox" data-midi-field="reversed" ${item.reversed ? 'checked' : ''}></td>
      <td><select data-midi-field="filter_level" aria-label="필터 단계">${filterLevelOptions(item.filter_level)}</select></td>
      <td class="midi-live-value" data-midi-output="final">0</td>
      <td class="midi-live-value" data-midi-output="ratio">0.00%</td>
      <td class="midi-live-value" data-midi-output="motion-deg">-</td>
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
      const filtered = Math.max(0, Math.min(MIDI_MAX, numberValue(live?.filtered_value, raw)));
      const sensitivityMode = Number(mapping.max_percent) > 100;
      if (sensitivityMode) mapping.min_percent = 0;
      const groupValid = live?.motion_group_valid !== false;
      const faderParking = Boolean(live?.fader_parking);
      const selected = Boolean(live?.motion_axis_matched) && groupValid && Boolean(live?.control_enabled);
      const activationRejected = live?.motor_command_state === 'activation_rejected';
      const activationMessage = String(
        live?.motor_command_message
        || live?.motion_group_message
        || '활성화할 수 없습니다'
      );
      const finalOutput = selected
        ? mappedOutput14bit(filtered, mapping)
        : Math.max(0, Math.min(MIDI_MAX, numberValue(live?.final_output_value, 0)));
      const finalRatio = finalOutput / MIDI_MAX;
      const rawCell = row.querySelector('[data-midi-output="raw"]');
      const filteredCell = row.querySelector('[data-midi-output="filtered"]');
      const finalCell = row.querySelector('[data-midi-output="final"]');
      const ratioCell = row.querySelector('[data-midi-output="ratio"]');
      const motionDegCell = row.querySelector('[data-midi-output="motion-deg"]');
      const touchCell = row.querySelector('[data-midi-output="touch"]');
      const selectCell = row.querySelector('[data-midi-output="select"]');
      const motionIdInputs = row.querySelectorAll('.midi-motion-id-input');
      const minPercentInput = row.querySelector('[data-midi-field="min_percent"]');
      const reversedInput = row.querySelector('[data-midi-field="reversed"]');
      const filterLevelInput = row.querySelector('[data-midi-field="filter_level"]');
      if (rawCell) rawCell.textContent = Math.round(raw).toLocaleString('ko-KR');
      if (filteredCell) filteredCell.textContent = Math.round(filtered).toLocaleString('ko-KR');
      if (finalCell) finalCell.textContent = Math.round(finalOutput).toLocaleString('ko-KR');
      if (ratioCell) ratioCell.textContent = `${(finalRatio * 100).toFixed(2)}%`;
      if (motionDegCell) {
        const motionDeg = Number(live?.motion_value_deg);
        motionDegCell.textContent = Number.isFinite(motionDeg) ? motionDeg.toFixed(2) : '-';
      }
      if (selectCell) {
        const matched = Boolean(live?.motion_axis_matched);
        selectCell.textContent = faderParking
          ? '0 복귀 중 · SELECT 대기'
          : (!matched
          ? '매칭 없음'
          : (!groupValid || activationRejected
            ? `활성 불가 · ${activationMessage}`
            : (selected ? '활성' : '비활성')));
        selectCell.title = matched && (!groupValid || activationRejected) ? activationMessage : '';
        selectCell.classList.toggle('midi-select-active', selected);
        selectCell.classList.toggle('midi-select-unmatched', !matched);
        selectCell.classList.toggle('midi-select-rejected', matched && (!groupValid || activationRejected));
      }
      if (minPercentInput) {
        minPercentInput.disabled = sensitivityMode;
        minPercentInput.title = sensitivityMode ? '입력 감도 확대 시 Min은 0%로 고정됩니다' : '';
        if (document.activeElement !== minPercentInput) {
          minPercentInput.value = mapping.min_percent;
        }
      }
      if (reversedInput) {
        reversedInput.disabled = selected;
        reversedInput.title = selected
          ? '안전을 위해 셀렉트 비활성 상태에서만 반전을 변경할 수 있습니다'
          : '';
      }
      if (touchCell) {
        const physicalTouch = Boolean(live?.physical_touch);
        const faderMoving = Boolean(live?.fader_moving);
        const faderSyncing = Boolean(live?.fader_syncing);
        const faderParking = Boolean(live?.fader_parking);
        const inputValid = Boolean(live?.input_valid ?? live?.touch);
        touchCell.textContent = faderParking
          ? '0 복귀 중'
          : physicalTouch
          ? '물리 터치'
          : (faderMoving ? '움직임' : (faderSyncing ? '동기화' : '-'));
        touchCell.title = (
          `물리 터치: ${physicalTouch ? '감지' : '없음'} · `
          + `움직임: ${faderMoving ? '감지' : '없음'} · `
          + `입력 인정: ${inputValid ? '사용' : '미사용'}`
        );
        touchCell.classList.toggle('midi-touch-active', physicalTouch || faderMoving);
      }
      if (filterLevelInput && document.activeElement !== filterLevelInput) {
        filterLevelInput.value = String(mapping.filter_level);
      }
      motionIdInputs.forEach((motionIdInput, index) => {
        const value = String(motionIdInput.value || '').trim();
        const duplicate = Boolean(value) && Array.from(motionIdInputs).filter(
          (input) => String(input.value || '').trim() === value
        ).length > 1;
        const invalid = (index === 0 && !value)
          || (Boolean(value) && !MOTION_ID_PATTERN.test(value))
          || duplicate;
        motionIdInput.classList.toggle('input-invalid', invalid);
        motionIdInput.setAttribute('aria-invalid', invalid ? 'true' : 'false');
      });
    });
  }

  function render() {
    const deviceConnected = Boolean(status?.device_connected);
    const inputActive = Boolean(status?.connected);
    if (el.midiConnectionState) {
      el.midiConnectionState.textContent = deviceConnected ? '연결됨' : '연결 대기';
      el.midiConnectionState.classList.toggle('status-ok', deviceConnected);
      el.midiConnectionState.classList.toggle('status-bad', !deviceConnected);
    }
    if (el.midiInputState) {
      el.midiInputState.textContent = inputActive ? '수신 중' : '입력 대기';
      el.midiInputState.classList.toggle('status-ok', inputActive);
      el.midiInputState.classList.toggle('status-bad', !inputActive);
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
      const configPath = String(status?.bank_config_file || '').trim();
      const configName = configPath || pathBasename(status?.bank_config_file);
      const saveState = status?.bank_persistent
        ? '현재 노드값과 파일 일치'
        : '현재 노드값이 파일에 저장되지 않음';
      el.midiMappingPath.textContent = (
        `모션축 설정: ${configName} · midi_banks · 저장된 뱅크: ${count}개`
        + ` (최대 ${status?.max_banks || 8}개) · ${saveState}`
      );
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
    if (el.connectMidiDeviceButton) {
      // A USB power cycle can leave the old RtMidi handle looking open even
      // though it no longer receives the re-enumerated device. Keep this
      // action available so the user can always force a fresh port search.
      el.connectMidiDeviceButton.disabled = loading;
      el.connectMidiDeviceButton.textContent = status?.device_connected
        ? 'MIDI 재연결'
        : 'MIDI 연결';
    }
    if (el.disconnectMidiDeviceButton) {
      el.disconnectMidiDeviceButton.disabled = loading || !Boolean(status?.device_connected);
    }
    if (el.resetMidiRuntimeButton) el.resetMidiRuntimeButton.disabled = loading;
    if (el.loadMidiBanksFileButton) el.loadMidiBanksFileButton.disabled = loading;
    if (el.saveMidiMappingButton) {
      el.saveMidiMappingButton.disabled = loading
        || !activeBankId
        || mappingDraft.some(invalidMappingItem);
    }
    renderRows();
  }

  function updateDraftFromRow(target) {
    const row = target.closest('[data-midi-channel]');
    const field = target.dataset.midiField;
    if (!row || !field) return;
    const channel = Number(row.dataset.midiChannel);
    const item = mappingDraft[channel];
    if (!item) return;
    dirtyFields.add(`${channel}:${field}`);
    if (target.type === 'checkbox') {
      item[field] = target.checked;
    } else if (field.startsWith('linked_motion_id_')) {
      const index = Number(field.slice(-1));
      const linked = Array.isArray(item.linked_motion_ids)
        ? [...item.linked_motion_ids]
        : ['', ''];
      linked[index] = target.value;
      item.linked_motion_ids = linkedMotionIdDraft(linked);
    } else if (field === 'filter_level') {
      item.filter_level = Math.round(numberValue(target.value, item.filter_level));
    } else if (field === 'min_percent' || field === 'max_percent') {
      item[field] = numberValue(target.value, item[field]);
      if (field === 'max_percent' && item.max_percent > 100) {
        item.min_percent = 0;
      }
    } else {
      item[field] = target.value;
    }
    status = {
      ...(status || {}),
      message: '뱅크 설정 변경됨 · 뱅크 설정 적용/저장을 눌러야 파일과 노드에 반영됩니다',
    };
    if (field !== 'filter_level') scheduleEditSafetyReset();
    render();
  }

  function scheduleEditSafetyReset() {
    if (editSafetyResetDone) return;
    if (editSafetyResetTimer !== null) window.clearTimeout(editSafetyResetTimer);
    editSafetyResetTimer = window.setTimeout(async () => {
      editSafetyResetTimer = null;
      if (editSafetyResetRunning) return;
      editSafetyResetRunning = true;
      editSafetyResetDone = true;
      try {
        const payload = requireSuccess(await resetMidiRuntimeValues());
        setStatus({
          ...payload,
          message: 'MIDI 설정 편집 중 · SELECT 전체 해제 · 페이더 0 이동 · 저장 필요',
        });
      } catch (error) {
        status = {
          ...(status || {}),
          message: `MIDI 설정 편집 안전 초기화 실패: ${error?.message || error}`,
        };
        render();
      } finally {
        editSafetyResetRunning = false;
      }
    }, 80);
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

  async function applySaveAndVerify() {
    const saved = requireSuccess(await updateMidiBank(activeBankId, {
      name: bankNameDraft,
      mappings: mappingDraft.map((mapping) => ({
        ...mapping,
        linked_motion_ids: linkedMotionIdDraft(mapping.linked_motion_ids)
          .map((value) => value.trim())
          .filter(Boolean),
      })),
    }));
    const verified = requireSuccess(await loadMidiBanksFromFile());
    return {
      ...verified,
      message: `${saved.message || 'MIDI 뱅크 파일 저장 완료'} · 재불러오기 검증 완료`,
    };
  }

  async function saveMapping() {
    const invalid = mappingDraft.find(invalidMappingItem);
    if (invalid) {
      status = {
        ...(status || {}),
        message: `채널 ${invalid.channel + 1}: 모션 ID 형식, 최솟값/최댓값 퍼센트와 필터 0~13단계를 확인하세요`,
      };
      render();
      return;
    }
    loading = true;
    render();
    try {
      const payload = await applySaveAndVerify();
      dirtyFields.clear();
      editSafetyResetDone = false;
      setStatus(payload, { updateMapping: true });
      window.dispatchEvent(new CustomEvent('motion-project-files-changed'));
    } catch (error) {
      status = {
        ...(status || {}),
        message: `뱅크 설정 적용/파일 저장 실패: ${error?.message || error}`,
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

  async function loadBanksFile() {
    if (!window.confirm('현재 메모리의 MIDI 뱅크를 파일에 저장된 내용으로 바꿀까요?')) return;
    loading = true;
    render();
    try {
      setStatus(requireSuccess(await loadMidiBanksFromFile()), { updateMapping: true });
    } catch (error) {
      status = { ...(status || {}), message: `MIDI 뱅크 파일 불러오기 실패: ${error?.message || error}` };
    } finally {
      loading = false;
      render();
    }
  }

  async function resetRuntimeValues() {
    if (!window.confirm('MIDI 실시간 값과 셀렉트를 초기화하고 전동 페이더를 0으로 이동할까요? 저장 파일은 변경되지 않습니다.')) return;
    loading = true;
    render();
    try {
      setStatus(requireSuccess(await resetMidiRuntimeValues()));
    } catch (error) {
      status = { ...(status || {}), message: `MIDI 실시간 값 초기화 실패: ${error?.message || error}` };
    } finally {
      loading = false;
      render();
    }
  }

  async function setDeviceConnection(connect) {
    loading = true;
    render();
    try {
      const requested = requireSuccess(await (
        connect ? connectMidiDevice() : disconnectMidiDevice()
      ));
      setStatus(requested);
      // The hardware bridge responds asynchronously. A short status refresh
      // shows the actual port-open result rather than only the request result.
      window.setTimeout(() => refresh(), 350);
    } catch (error) {
      status = {
        ...(status || {}),
        message: `MIDI ${connect ? '연결' : '연결 해제'} 실패: ${error?.message || error}`,
      };
    } finally {
      loading = false;
      render();
    }
  }

  function resetProjectState() {
    status = null;
    mappingDraft = Array.from(
      { length: CHANNEL_COUNT }, (_, channel) => defaultMapping(channel)
    );
    mappingLoaded = false;
    loading = false;
    activeBankId = '';
    bankNameDraft = 'Bank 1';
    banks = [];
    dirtyFields.clear();
    if (editSafetyResetTimer !== null) window.clearTimeout(editSafetyResetTimer);
    editSafetyResetTimer = null;
    editSafetyResetRunning = false;
    editSafetyResetDone = false;
    renderRows(true);
    render();
  }

  function bindEvents() {
    el.midiMonitorRows?.addEventListener('input', (event) => updateDraftFromRow(event.target));
    el.midiMonitorRows?.addEventListener('change', (event) => updateDraftFromRow(event.target));
    el.refreshMidiMonitorButton?.addEventListener('click', refresh);
    el.connectMidiDeviceButton?.addEventListener('click', () => setDeviceConnection(true));
    el.disconnectMidiDeviceButton?.addEventListener('click', () => setDeviceConnection(false));
    el.resetMidiRuntimeButton?.addEventListener('click', resetRuntimeValues);
    el.saveMidiMappingButton?.addEventListener('click', saveMapping);
    el.midiBankSelect?.addEventListener('change', changeBank);
    el.midiBankName?.addEventListener('input', (event) => {
      bankNameDraft = event.target.value;
    });
    el.addMidiBankButton?.addEventListener('click', addBank);
    el.deleteMidiBankButton?.addEventListener('click', removeBank);
    el.loadMidiBanksFileButton?.addEventListener('click', loadBanksFile);
  }

  bindEvents();
  renderRows(true);
  render();

  return {
    refresh,
    resetProjectState,
    renderSnapshot: (payload) => setStatus(payload),
  };
}
