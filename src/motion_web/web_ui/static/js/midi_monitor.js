import {
  connectMidiDevice,
  createMidiBank,
  deleteMidiBank,
  fetchMidiMonitor,
  loadMidiBanksFromFile,
  resetMidiRuntimeValues,
  selectMidiBank,
  updateMidiBank,
} from './api.js';
import { showConfirm } from './ui_dialogs.js';

const MIDI_MAX = 16383;
const CHANNEL_COUNT = 8;
const FILTER_LEVEL_MAX = 13;

// 새 채널의 필터 기본값 · 파이썬 쪽 bank_manager.FILTER_LEVEL_DEFAULT 와 같은 값
//
// 0 이면 페이더 값이 그대로 나가 포인트가 많아지고 모터도 급하게 따라간다 ·
// 실제 녹화 18.7초 한 축이 0단계 218개, 7단계 95개(0.5° 기준)다 · 대신 약
// 0.4초 뒤처진다.
const FILTER_LEVEL_DEFAULT = 7;
const MOTION_ID_PATTERN = /^[1-9]\d*-[1-9]\d*$/;

function numberValue(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function pathBasename(value) {
  return String(value || '').split(/[\\/]/).filter(Boolean).pop() || '모션축 설정 YAML';
}

function timestampText(value, fallback) {
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds <= 0) return fallback;
  return new Date(seconds * 1000).toLocaleString('ko-KR', { hour12: false });
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
    filter_level: FILTER_LEVEL_DEFAULT,
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

/** 필터 출력 → 최종 출력 · 서버와 **같은 식**이어야 한다 · §6-245
 *
 * 서버에도 같은 계산이 있다 (`midi_control_node._filtered_output_14bit`) ·
 * 화면이 굳이 또 계산하는 이유는 하나다 — **저장 전 편집을 미리 보여주려고** ·
 * 서버는 아직 저장되지 않은 최소값·최대값·반전을 모른다.
 *
 * 그래서 없앨 수는 없고, 대신 **갈라지면 잡히게** 해 둔다 · 양쪽 시험이
 * 똑같은 표본으로 똑같은 답을 요구한다.
 *
 *     화면   test/midi_output_matches_the_node.test.mjs
 *     서버   test_midi_output_matches_the_screen.py
 *
 * 한쪽만 고치면 그쪽 시험이 깨진다 · 모델값 때처럼 화면 숫자와 실제 출력이
 * 말없이 어긋나는 일을 막는다.
 */
export function mappedOutput14bit(filteredValue, mapping) {
  let normalized = Math.max(0, Math.min(1, numberValue(filteredValue, 0) / MIDI_MAX));
  if (mapping.reversed) normalized = 1 - normalized;
  const minPercent = numberValue(mapping.min_percent, 0);
  const maxPercent = numberValue(mapping.max_percent, 100);
  const outputPercent = minPercent + ((maxPercent - minPercent) * normalized);
  return MIDI_MAX * Math.max(0, Math.min(100, outputPercent)) / 100;
}

export function createMidiMonitorController({ el, onMappingFileSaved }) {
  let status = null;
  let mappingDraft = Array.from({ length: CHANNEL_COUNT }, (_, channel) => defaultMapping(channel));
  let mappingLoaded = false;
  let loading = false;
  let activeBankId = '';
  let bankNameDraft = 'Bank 1';
  let banks = [];
  const dirtyFields = new Set();
  /** 사람에게 하는 말은 노드 상태와 자리를 나눠 쓴다 · §6-245
   *
   * 상태 문구는 **초당 열 번** 서버 값으로 덮어써진다(`renderSnapshot` →
   * `setStatus`) · 거기에 편집 안내를 적어 두었더니 100밀리초도 못 버티고
   * 지워졌다 · 「뱅크 저장을 눌러야 반영됩니다」 도, 「최소값을 0%로
   * 맞췄습니다」 도 사람 눈에는 **한 번도 뜨지 않았다**.
   *
   * 시간으로 지우지 않는다 · 저장하거나 뱅크를 다시 읽을 때 지운다.
   */
  let editNotice = '';
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
      // **그리면서 설정을 고치지 않는다** · §6-245
      //
      // 전에는 여기서 `mapping.min_percent = 0` 을 했다 · 화면을 다시 그릴
      // 때마다 사람이 적어 둔 최소값이 말없이 지워졌다 · 알림도 없었다.
      //
      // 같은 규칙이 **편집 처리부에 이미 있다**(`updateDraftFromRow`) · 거기서
      // 한 번 하면 되는 일을, 그리는 자리에서 한 번 더 하고 있었다.
      const sensitivityMode = Number(mapping.max_percent) > 100;
      const groupValid = live?.motion_group_valid !== false;
      const faderParking = Boolean(live?.fader_parking);
      const zeroReturnFailed = live?.motor_command_state === 'fader_park_failed';
      const selected = Boolean(live?.motion_axis_matched)
        && groupValid
        && Boolean(live?.select_enabled ?? live?.control_enabled);
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
        const displayedMotionDeg = live?.displayed_motion_value_deg;
        const motionDeg = Number(
          displayedMotionDeg === null || displayedMotionDeg === undefined
            ? live?.motion_value_deg
            : displayedMotionDeg,
        );
        motionDegCell.textContent = Number.isFinite(motionDeg) ? motionDeg.toFixed(2) : '-';
      }
      if (selectCell) {
        const matched = Boolean(live?.motion_axis_matched);
        const commandMessage = String(live?.motor_command_message || '');
        const zeroArrivalUnverified = !selected
          && commandMessage.includes('물리 도착 피드백 없음');
        selectCell.textContent = faderParking
          ? '0 복귀 중 · SELECT 대기'
          : (zeroReturnFailed
          ? '0 복귀 실패'
          : (!matched
          ? '매칭 없음'
          : (!groupValid || activationRejected
            ? `활성 불가 · ${activationMessage}`
            : (selected ? '활성' : (
              zeroArrivalUnverified ? '비활성 · 0 명령 전송' : '비활성'
            )))));
        selectCell.title = matched && (!groupValid || activationRejected)
          ? activationMessage
          : (zeroReturnFailed || zeroArrivalUnverified ? commandMessage : '');
        selectCell.classList.toggle('midi-select-active', selected);
        selectCell.classList.toggle('midi-select-unmatched', !matched);
        selectCell.classList.toggle(
          'midi-select-rejected',
          zeroReturnFailed || (matched && (!groupValid || activationRejected)),
        );
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
    // 다른 PC 가 쓰는 중인 것과 장치가 빠진 것은 **다른 일이다** · §6-94
    // 둘 다 '연결 대기' 라고 하면 무엇을 고쳐야 할지 알 수 없다
    const surfaceOwned = status?.surface_owned !== false;
    if (el.midiConnectionState) {
      el.midiConnectionState.textContent = surfaceOwned
        ? (deviceConnected ? '연결됨' : '연결 대기')
        : (status?.device_connection_message || '다른 PC 가 사용 중');
      el.midiConnectionState.classList.toggle('status-ok', deviceConnected);
      el.midiConnectionState.classList.toggle('status-bad', !deviceConnected);
    }
    if (el.midiInputState) {
      el.midiInputState.textContent = inputActive ? '최근 입력 정상' : '현재 입력 없음';
      el.midiInputState.classList.toggle('status-ok', inputActive);
      el.midiInputState.classList.toggle('status-bad', !deviceConnected);
    }
    if (el.midiLastInputState) {
      el.midiLastInputState.textContent = timestampText(
        status?.last_received_at,
        '입력 기록 없음',
      );
    }
    if (el.midiPowerReconnectState) {
      el.midiPowerReconnectState.textContent = timestampText(
        status?.device_last_power_reconnected_at,
        '감지 기록 없음',
      );
      el.midiPowerReconnectState.title = (
        `연결 ${Number(status?.device_connection_count || 0)}회 · `
        + `전원 재연결 ${Number(status?.device_power_reconnect_count || 0)}회`
      );
    }
    if (el.midiMotorOutputState) {
      el.midiMotorOutputState.textContent = status?.motor_output_enabled ? '활성' : '사용 안 함';
    }
    if (el.midiMonitorMessage) {
      // 뱅크가 안 실리면 **기본값(1-1~1-8)** 으로 돈다 · §6-94
      //
      // 빈 설정이 아니라 **채워진 기본 설정**이라 화면이 멀쩡해 보인다 ·
      // 꺼 놓은 축도 살아 있는 것처럼 보이고, 이름도 그럴듯하다 · 조용히
      // 두면 왜 내 설정대로 안 되는지 알 길이 없다.
      const bankMissing = status && status.ready === false;
      el.midiMonitorMessage.textContent = loading
        ? 'MIDI 상태 확인 중'
        : bankMissing
          ? '⚠ 뱅크가 실리지 않았습니다 · 지금 보이는 1-1~1-8 은 기본값입니다'
            + ' · 프로젝트 관리 탭에서 프로젝트를 먼저 띄우세요'
          : editNotice || status?.message || 'MIDI 모니터 노드 상태 수신 대기';
      el.midiMonitorMessage.classList.toggle('status-bad', Boolean(bankMissing));
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
      // 표면이 내 것이 아니면 장치를 건드릴 수 없다 · 눌러도 거절당한다
      el.connectMidiDeviceButton.disabled = loading || !surfaceOwned;
      el.connectMidiDeviceButton.textContent = status?.device_connected
        ? 'MIDI 재연결'
        : 'MIDI 연결';
    }
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
    let minPercentForced = 0;
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
      if (field === 'max_percent' && item.max_percent > 100 && item.min_percent !== 0) {
        // 서버 규칙이다 · 최대값이 100 을 넘으면 최소값은 0 으로 못박힌다
        // (`bank_manager.py` · max_percent > 100 이면 min_percent = 0)
        // **말없이 지우지 않는다** · 사람이 적어 둔 값이 사라지는 일이다.
        item.min_percent = 0;
        minPercentForced = channel + 1;
      }
    } else {
      item[field] = target.value;
    }
    editNotice = minPercentForced
      ? `채널 ${minPercentForced}: 최대값이 100%를 넘어 최소값을 0%로 맞췄습니다`
        + ' · 뱅크 저장을 눌러야 파일과 노드에 반영됩니다'
      : '뱅크 설정 변경됨 · 뱅크 저장을 눌러야 파일과 노드에 반영됩니다';
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
        message: `채널 ${invalid.channel + 1}: 모션 ID 형식, 최소값/최대값 퍼센트와 필터 0~13단계를 확인하세요`,
      };
      render();
      return;
    }
    loading = true;
    render();
    try {
      const payload = await applySaveAndVerify();
      dirtyFields.clear();
      editNotice = '';
      editSafetyResetDone = false;
      setStatus(payload, { updateMapping: true });
      onMappingFileSaved?.(payload.file);
      window.dispatchEvent(new CustomEvent('motion-project-files-changed'));
    } catch (error) {
      status = {
        ...(status || {}),
        message: `MIDI 뱅크 설정 저장 실패: ${error?.message || error}`,
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
      editNotice = '';
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
    if (!await showConfirm(
      `'${bankName}' 뱅크를 삭제할까요?`,
      { title: 'MIDI 뱅크 삭제', confirmLabel: '삭제', tone: 'danger' },
    )) return;
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
    if (!await showConfirm(
      '현재 메모리의 MIDI 뱅크를 파일에 저장된 내용으로 바꿀까요?',
      { title: 'MIDI 설정 불러오기', confirmLabel: '불러오기' },
    )) return;
    loading = true;
    render();
    try {
      setStatus(requireSuccess(await loadMidiBanksFromFile()), { updateMapping: true });
      editNotice = '';
    } catch (error) {
      status = { ...(status || {}), message: `MIDI 뱅크 파일 불러오기 실패: ${error?.message || error}` };
    } finally {
      loading = false;
      render();
    }
  }

  /** 장치를 다시 연다 · **끊는 길은 두지 않는다** · §6-246
   *
   * 전에는 「연결 해제」와 「페이더 0으로 되돌리기」가 함께 있었다 · 그중
   * 「연결 해제」는 **돌아올 수 없는 길**이었다.
   *
   *     연결 해제 → 입력 브리지가 auto_reconnect 를 끄고 포트를 닫는다
   *              → 장치에서 값이 끊긴다
   *              → 「장치 주인」이 이 PC 가 아닌 것으로 바뀐다
   *                 (midi_relay_bridge · device_pc_id 를 남에게 넘김)
   *              → holds_device false → owns_surface false
   *              → surface_owned false 로 방송
   *              → 「MIDI 연결」 단추까지 꺼진다 · 다시 붙일 방법이 없다
   *
   * **꽂혀 있나** 를 **값이 들어오나** 로 판단하기 때문이다 · 사람이 일부러
   * 닫은 것과 장치가 빠진 것을 구분하지 못한다.
   *
   * USB 를 뽑았다 꽂는 것은 원래 문제가 없다 · 입력 브리지가 0.25초마다
   * 포트를 보고 저절로 다시 연다 · 막히는 길은 사람이 누르는 「연결 해제」
   * 하나뿐이었고, 그래서 그 단추를 없앤다.
   */
  async function connectDevice() {
    loading = true;
    render();
    try {
      setStatus(requireSuccess(await connectMidiDevice()));
      // 하드웨어 브리지는 나중에 답한다 · 실제로 포트가 열렸는지 한 번 더 본다
      window.setTimeout(() => refresh(), 350);
    } catch (error) {
      status = {
        ...(status || {}),
        message: `MIDI 연결 실패: ${error?.message || error}`,
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
    el.connectMidiDeviceButton?.addEventListener('click', connectDevice);
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
