import {
  fetchServoAlarmPolicy,
  saveServoAlarmPolicy,
} from './api.js?v=20260728-servo-alarm-2';

const FALLBACK_GRADE_LABELS = Object.freeze({
  1: '1등급 · 해당 에러축 정지',
  2: '2등급 · 전체 모션 종료',
  3: '3등급 · 전체 모터 제어 차단',
});

function normalizedCode(value) {
  const code = Number(value);
  return Number.isInteger(code) && code > 0 && code !== 0xFFFF ? code : 0;
}

export function createServoAlarmController({ el, getLatestState }) {
  let catalog = [];
  let overrides = {};
  let projectId = '';
  let policyRevision = '';
  let gradeDefinitions = {};
  let dirty = false;
  let loading = false;

  function entryForCode(value) {
    const code = normalizedCode(value);
    const entry = catalog.find((candidate) => Number(candidate.code) === code) || null;
    return entry ? { ...entry, effective_grade: effectiveGrade(entry) } : null;
  }

  function effectiveGrade(entry) {
    const configured = Number(overrides[String(entry.code)]);
    return [1, 2, 3].includes(configured) ? configured : Number(entry.default_grade);
  }

  function gradeAction(grade) {
    return gradeDefinitions?.[String(grade)]?.action
      || FALLBACK_GRADE_LABELS[grade]?.split(' · ')[1]
      || '동작 제한';
  }

  function gradeOptionLabel(grade) {
    const definition = gradeDefinitions?.[String(grade)] || {};
    return `${definition.label || `${grade}등급`} · ${definition.action || gradeAction(grade)}`;
  }

  function counts() {
    const result = { 1: 0, 2: 0, 3: 0, modified: 0 };
    catalog.forEach((entry) => {
      result[effectiveGrade(entry)] += 1;
      if (Object.hasOwn(overrides, String(entry.code))) result.modified += 1;
    });
    return result;
  }

  function setStatus(text, tone = '') {
    if (!el.servoAlarmStatus) return;
    el.servoAlarmStatus.textContent = text;
    el.servoAlarmStatus.dataset.tone = tone;
  }

  function renderSummary() {
    const current = counts();
    if (el.servoAlarmGrade1Count) el.servoAlarmGrade1Count.textContent = `${current[1]}개`;
    if (el.servoAlarmGrade2Count) el.servoAlarmGrade2Count.textContent = `${current[2]}개`;
    if (el.servoAlarmGrade3Count) el.servoAlarmGrade3Count.textContent = `${current[3]}개`;
    if (el.servoAlarmModifiedCount) {
      el.servoAlarmModifiedCount.textContent = `${current.modified}개`;
    }
    if (el.servoAlarmProjectName) {
      el.servoAlarmProjectName.textContent = projectId || '프로젝트 선택 필요';
    }
    if (el.servoAlarmSaveButton) {
      el.servoAlarmSaveButton.disabled = loading || !dirty || !projectId;
    }
    if (el.servoAlarmResetAllButton) {
      el.servoAlarmResetAllButton.disabled = loading || current.modified === 0;
    }
  }

  function renderCatalog() {
    if (!el.servoAlarmRows) return;
    el.servoAlarmRows.replaceChildren();
    if (!catalog.length) {
      const row = document.createElement('tr');
      const cell = document.createElement('td');
      cell.colSpan = 6;
      cell.className = 'empty';
      cell.textContent = projectId
        ? '서보 에러 목록이 없습니다'
        : '프로젝트를 선택하세요';
      row.appendChild(cell);
      el.servoAlarmRows.appendChild(row);
      renderSummary();
      return;
    }

    catalog.forEach((entry) => {
      const grade = effectiveGrade(entry);
      const row = document.createElement('tr');
      row.dataset.grade = String(grade);
      row.dataset.code = String(entry.code);

      const code = document.createElement('td');
      const codeLabel = document.createElement('strong');
      codeLabel.textContent = entry.code_label;
      code.appendChild(codeLabel);
      if (entry.ethercat_related) {
        const mark = document.createElement('small');
        mark.textContent = 'EtherCAT';
        code.appendChild(mark);
      }

      const name = document.createElement('td');
      name.textContent = entry.name;

      const defaultGrade = document.createElement('td');
      const defaultBadge = document.createElement('span');
      defaultBadge.className = `servo-alarm-grade grade-${entry.default_grade}`;
      defaultBadge.textContent = `${entry.default_grade}등급`;
      defaultGrade.appendChild(defaultBadge);

      const projectGrade = document.createElement('td');
      const select = document.createElement('select');
      select.className = 'servo-alarm-grade-select';
      select.dataset.servoAlarmCode = String(entry.code);
      [
        ['', `기본값 (${entry.default_grade}등급)`],
        ['1', gradeOptionLabel(1)],
        ['2', gradeOptionLabel(2)],
        ['3', gradeOptionLabel(3)],
      ].forEach(([value, label]) => {
        const option = document.createElement('option');
        option.value = value;
        option.textContent = label;
        select.appendChild(option);
      });
      select.value = Object.hasOwn(overrides, String(entry.code))
        ? String(overrides[String(entry.code)])
        : '';
      projectGrade.appendChild(select);

      const action = document.createElement('td');
      action.textContent = entry.action;
      const guidance = document.createElement('td');
      guidance.textContent = entry.guidance;

      row.append(code, name, defaultGrade, projectGrade, action, guidance);
      el.servoAlarmRows.appendChild(row);
    });
    renderSummary();
  }

  function applyPayload(payload) {
    projectId = String(payload?.project_id || '');
    policyRevision = String(payload?.policy_revision || '');
    gradeDefinitions = { ...(payload?.grade_definitions || {}) };
    catalog = Array.isArray(payload?.catalog) ? payload.catalog : [];
    overrides = { ...(payload?.overrides || {}) };
    dirty = false;
    renderCatalog();
    setStatus(
      projectId
        ? `${catalog.length}개 에러군 · 현재 프로젝트 정책`
        : '프로젝트 선택 필요',
      projectId ? 'ready' : 'warning',
    );
  }

  async function refresh() {
    if (loading) return;
    loading = true;
    renderSummary();
    setStatus('프로젝트 정책 불러오는 중');
    try {
      applyPayload(await fetchServoAlarmPolicy());
    } catch (error) {
      setStatus(`불러오기 실패 · ${error?.message || error}`, 'danger');
    } finally {
      loading = false;
      renderSummary();
    }
  }

  async function save() {
    if (loading || !dirty || !projectId) return;
    loading = true;
    renderSummary();
    setStatus('프로젝트 등급 저장 중');
    try {
      const payload = await saveServoAlarmPolicy(overrides);
      applyPayload(payload);
      setStatus(
        payload?.supervisor_applied
          ? '저장 완료 · Supervisor 적용 확인'
          : `저장 완료 · Supervisor 적용 미확인${payload?.supervisor_message ? ` · ${payload.supervisor_message}` : ''}`,
        payload?.supervisor_applied ? 'ready' : 'warning',
      );
    } catch (error) {
      setStatus(`저장 실패 · ${error?.message || error}`, 'danger');
    } finally {
      loading = false;
      renderSummary();
    }
  }

  function renderRuntimeState() {
    if (!el.servoAlarmRuntimeBanner) return;
    const state = getLatestState?.() || {};
    const safety = state.safety_status || {};
    const active = Array.isArray(safety.servo_alarm_active)
      ? safety.servo_alarm_active
      : [];
    const grade = Number(safety.servo_alarm_grade) || 0;
    const latched = Boolean(safety.servo_alarm_grade3_latched);
    const banner = el.servoAlarmRuntimeBanner;
    const appliedProjectId = String(safety.servo_alarm_policy_project_id || '');
    const appliedRevision = String(safety.servo_alarm_policy_revision || '');
    if (
      projectId
      && policyRevision
      && (
        appliedProjectId !== projectId
        || appliedRevision !== policyRevision
      )
    ) {
      banner.classList.remove('ready', 'grade-1', 'grade-3');
      banner.classList.add('grade-2');
      const title = banner.querySelector('strong');
      const detail = banner.querySelector('span');
      if (title) title.textContent = '서보 에러 정책 적용 확인 필요';
      if (detail) {
        detail.textContent = appliedProjectId
          ? '저장된 프로젝트 등급과 Supervisor 적용 등급이 일치하지 않습니다.'
          : 'Supervisor의 프로젝트 정책 응답을 기다리고 있습니다.';
      }
      return;
    }
    banner.classList.toggle('ready', grade === 0 && !latched);
    banner.classList.toggle('grade-1', grade === 1);
    banner.classList.toggle('grade-2', grade === 2);
    banner.classList.toggle('grade-3', grade === 3 || latched);
    const title = banner.querySelector('strong');
    const detail = banner.querySelector('span');
    if (latched) {
      if (title) title.textContent = '3등급 차단 유지 · 프로그램 재시작 필요';
      if (detail) {
        detail.textContent = active.length
          ? active.map((item) => `축 ${item.axis} Err${item.code}.*`).join(' · ')
          : '실제 에러가 해제됐더라도 프로그램 재시작 전까지 모터 제어를 차단합니다.';
      }
      return;
    }
    if (!active.length) {
      const heldAxes = Array.isArray(safety.servo_alarm_recovery_hold_axes)
        ? safety.servo_alarm_recovery_hold_axes
        : [];
      if (grade === 1 && heldAxes.length) {
        if (title) title.textContent = '1등급 에러 해제 확인 · 현재 재생에서는 해당 축 유지 차단';
        if (detail) detail.textContent = `${heldAxes.map((axis) => `축 ${axis}`).join(', ')} · 다음 동작부터 정상 제어`;
      } else {
        if (title) title.textContent = '현재 서보 에러 없음';
        if (detail) detail.textContent = '실시간 모터 상태 기준';
      }
      return;
    }
    if (title) title.textContent = `${grade}등급 서보 에러 · ${gradeAction(grade)}`;
    if (detail) {
      detail.textContent = active.map((item) => {
        const entry = entryForCode(item.code);
        return `축 ${item.axis} ${entry?.code_label || `Err${item.code}.*`} ${entry?.name || ''}`.trim();
      }).join(' · ');
    }
  }

  function bindEvents() {
    el.servoAlarmReloadButton?.addEventListener('click', () => refresh());
    el.servoAlarmSaveButton?.addEventListener('click', () => save());
    el.servoAlarmResetAllButton?.addEventListener('click', () => {
      overrides = {};
      dirty = true;
      renderCatalog();
      setStatus('전체 항목을 기본 등급으로 변경 · 저장 필요', 'warning');
    });
    el.servoAlarmRows?.addEventListener('change', (event) => {
      const select = event.target.closest('select[data-servo-alarm-code]');
      if (!select) return;
      const code = String(select.dataset.servoAlarmCode || '');
      if (select.value === '') delete overrides[code];
      else overrides[code] = Number(select.value);
      dirty = true;
      renderCatalog();
      setStatus('프로젝트 등급 변경 있음 · 저장 필요', 'warning');
    });
  }

  function resetProjectState() {
    catalog = [];
    overrides = {};
    projectId = '';
    policyRevision = '';
    gradeDefinitions = {};
    dirty = false;
    renderCatalog();
    setStatus('프로젝트 정책을 다시 불러오는 중');
  }

  return {
    bindEvents,
    entryForCode,
    refresh,
    renderRuntimeState,
    resetProjectState,
  };
}
