import { clearMotorEvents, fetchMotorEvents } from './api.js?v=20260714-motor-event-log-clear';

const CATEGORY_LABELS = {
  error: '모터 에러',
  initial_position: '초기 위치 이동',
  motion: '모션 시작',
};

const EVENT_TYPE_LABELS = {
  single_motion_started: '1회 모션 시작',
  continuous_motion_started: '연속 모션 시작',
  motion_started: '모션 시작',
};

function eventTimeText(event) {
  const value = String(event?.timestamp_text || '');
  if (!value) return '-';
  return value.replace('T', ' ').slice(0, 19);
}

function appendTextCell(row, text, className = '') {
  const cell = document.createElement('td');
  if (className) cell.className = className;
  cell.textContent = String(text ?? '-');
  row.appendChild(cell);
  return cell;
}

export function createMotorEventLogController({ el }) {
  let activeFilter = 'all';
  let loading = false;
  let lastLoadedAt = 0;

  function isPanelActive() {
    const panel = el.motorEventLogRows?.closest('[data-workspace-panel="log"]');
    return Boolean(panel && !panel.classList.contains('hidden'));
  }

  function renderFilters() {
    if (!el.motorEventLogFilters) return;
    el.motorEventLogFilters.querySelectorAll('[data-event-log-filter]').forEach((button) => {
      const active = button.dataset.eventLogFilter === activeFilter;
      button.classList.toggle('active', active);
      button.setAttribute('aria-selected', active ? 'true' : 'false');
    });
  }

  function renderRows(events) {
    if (!el.motorEventLogRows) return;
    el.motorEventLogRows.replaceChildren();
    if (!events.length) {
      const row = document.createElement('tr');
      const cell = appendTextCell(row, '기록된 모터 동작 로그가 없습니다', 'empty');
      cell.colSpan = 4;
      el.motorEventLogRows.appendChild(row);
      return;
    }

    events.forEach((event) => {
      const row = document.createElement('tr');
      appendTextCell(row, eventTimeText(event), 'mono');
      const kindCell = document.createElement('td');
      const kind = document.createElement('span');
      const category = String(event.category || '');
      kind.className = `event-log-kind ${category}`;
      kind.textContent = EVENT_TYPE_LABELS[event.event_type]
        || CATEGORY_LABELS[category]
        || category
        || '-';
      kindCell.appendChild(kind);
      row.appendChild(kindCell);
      appendTextCell(row, event.target || '-');
      appendTextCell(row, event.content || '-');
      el.motorEventLogRows.appendChild(row);
    });
  }

  async function refresh(force = false) {
    if (loading || (!force && Date.now() - lastLoadedAt < 1500)) return;
    loading = true;
    if (el.refreshMotorEventLogButton) el.refreshMotorEventLogButton.disabled = true;
    if (el.motorEventLogSummary && !lastLoadedAt) {
      el.motorEventLogSummary.textContent = '로그를 불러오는 중';
    }
    try {
      const payload = await fetchMotorEvents(activeFilter, 300);
      const events = Array.isArray(payload.events) ? payload.events : [];
      renderRows(events);
      lastLoadedAt = Date.now();
      if (el.motorEventLogSummary) {
        const filterLabel = activeFilter === 'all' ? '전체' : CATEGORY_LABELS[activeFilter];
        const retentionDays = Number(payload.retention_days || 30);
        const maxMb = Math.round(Number(payload.max_bytes || 0) / (1024 * 1024));
        el.motorEventLogSummary.textContent = (
          `${filterLabel} ${events.length}건 · 최신순 · ${retentionDays}일 / 최대 ${maxMb}MB 보관`
        );
      }
    } catch (error) {
      if (el.motorEventLogSummary) {
        el.motorEventLogSummary.textContent = `로그 불러오기 실패: ${error?.message || error}`;
      }
    } finally {
      loading = false;
      if (el.refreshMotorEventLogButton) el.refreshMotorEventLogButton.disabled = false;
    }
  }

  function activate() {
    renderFilters();
    refresh(true);
  }

  async function clearAll() {
    const confirmed = window.confirm(
      '저장된 모터 동작 로그를 모두 삭제합니다.\n삭제한 로그는 복구할 수 없습니다.'
    );
    if (!confirmed) return;
    if (el.clearMotorEventLogButton) el.clearMotorEventLogButton.disabled = true;
    try {
      await clearMotorEvents();
      lastLoadedAt = 0;
      await refresh(true);
    } catch (error) {
      if (el.motorEventLogSummary) {
        el.motorEventLogSummary.textContent = `로그 삭제 실패: ${error?.message || error}`;
      }
    } finally {
      if (el.clearMotorEventLogButton) el.clearMotorEventLogButton.disabled = false;
    }
  }

  function bindEvents() {
    if (el.motorEventLogFilters) {
      el.motorEventLogFilters.addEventListener('click', (event) => {
        const button = event.target.closest('button[data-event-log-filter]');
        if (!button) return;
        activeFilter = button.dataset.eventLogFilter || 'all';
        lastLoadedAt = 0;
        renderFilters();
        refresh(true);
      });
    }
    if (el.refreshMotorEventLogButton) {
      el.refreshMotorEventLogButton.addEventListener('click', () => refresh(true));
    }
    if (el.clearMotorEventLogButton) {
      el.clearMotorEventLogButton.addEventListener('click', clearAll);
    }
    window.setInterval(() => {
      if (isPanelActive()) refresh();
    }, 2000);
  }

  return {
    activate,
    bindEvents,
    refresh,
  };
}
