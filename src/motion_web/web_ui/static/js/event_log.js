import {
  clearMotorEvents,
  deleteMotorEventLogFile,
  fetchMotorEvents,
} from './api.js?v=20260722-motor-config-delete';

const CATEGORY_LABELS = {
  error: '모터 에러',
  initial_position: '초기 위치 이동',
  motion: '모션 시작',
  system: '시스템 설정',
};

const EVENT_TYPE_LABELS = {
  single_motion_started: '1회 모션 시작',
  continuous_motion_started: '연속 모션 시작',
  motion_started: '모션 시작',
  ethercat_alias_written: 'EEPROM Alias 변경',
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
  let activeFile = 'all';
  let loading = false;
  let lastLoadedAt = 0;
  let logFiles = [];

  function formatBytes(value) {
    const bytes = Number(value) || 0;
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  }

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

  function renderLogFiles() {
    if (el.motorEventLogFileSelect) {
      const available = new Set(logFiles.map((file) => file.name));
      if (activeFile !== 'all' && !available.has(activeFile)) activeFile = 'all';
      el.motorEventLogFileSelect.innerHTML = '<option value="all">전체 날짜</option>'
        + logFiles.map((file) => (
          `<option value="${String(file.name).replaceAll('&', '&amp;').replaceAll('"', '&quot;')}">`
          + `${file.name} · ${Number(file.record_count) || 0}건 · ${formatBytes(file.size)}</option>`
        )).join('');
      el.motorEventLogFileSelect.value = activeFile;
      el.motorEventLogFileSelect.disabled = loading || logFiles.length === 0;
    }
    if (el.deleteMotorEventLogFileButton) {
      el.deleteMotorEventLogFileButton.disabled = loading || activeFile === 'all';
    }
    if (el.clearMotorEventLogButton) {
      el.clearMotorEventLogButton.disabled = loading || logFiles.length === 0;
    }
  }

  async function refresh(force = false) {
    if (loading || (!force && Date.now() - lastLoadedAt < 1500)) return;
    loading = true;
    if (el.refreshMotorEventLogButton) el.refreshMotorEventLogButton.disabled = true;
    if (el.motorEventLogSummary && !lastLoadedAt) {
      el.motorEventLogSummary.textContent = '로그를 불러오는 중';
    }
    try {
      const payload = await fetchMotorEvents(activeFilter, 300, activeFile);
      const events = Array.isArray(payload.events) ? payload.events : [];
      logFiles = Array.isArray(payload.files) ? payload.files : [];
      renderLogFiles();
      renderRows(events);
      lastLoadedAt = Date.now();
      if (el.motorEventLogSummary) {
        const filterLabel = activeFilter === 'all' ? '전체' : CATEGORY_LABELS[activeFilter];
        el.motorEventLogSummary.textContent = (
          `${payload.project_name || '프로젝트 미선택'} · ${filterLabel} ${events.length}건 · 최신순`
        );
      }
      if (el.motorEventLogStorage) {
        const maxMb = Math.round(Number(payload.max_bytes || 0) / (1024 * 1024));
        const totalRecords = logFiles.reduce(
          (sum, file) => sum + (Number(file.record_count) || 0), 0,
        );
        el.motorEventLogStorage.textContent = (
          `${logFiles.length}/${Number(payload.max_files) || 0}개 파일 · ${totalRecords}/${Number(payload.max_records) || 0}건`
          + ` · ${Number(payload.retention_days) || 0}일 · 최대 ${maxMb}MB`
        );
      }
    } catch (error) {
      if (el.motorEventLogSummary) {
        el.motorEventLogSummary.textContent = `로그 불러오기 실패: ${error?.message || error}`;
      }
    } finally {
      loading = false;
      if (el.refreshMotorEventLogButton) el.refreshMotorEventLogButton.disabled = false;
      renderLogFiles();
    }
  }

  function activate() {
    renderFilters();
    refresh(true);
  }

  function resetProjectState() {
    activeFile = 'all';
    loading = false;
    lastLoadedAt = 0;
    logFiles = [];
    el.motorEventLogRows?.replaceChildren();
    if (el.motorEventLogSummary) el.motorEventLogSummary.textContent = '';
    renderLogFiles();
  }

  async function clearAll() {
    const confirmed = window.confirm(
      '현재 프로젝트에 저장된 모터 동작 로그를 모두 삭제합니다.\n삭제한 로그는 복구할 수 없습니다.'
    );
    if (!confirmed) return;
    if (el.clearMotorEventLogButton) el.clearMotorEventLogButton.disabled = true;
    try {
      await clearMotorEvents();
      lastLoadedAt = 0;
      await refresh(true);
      window.dispatchEvent(new CustomEvent('motion-project-files-changed'));
    } catch (error) {
      if (el.motorEventLogSummary) {
        el.motorEventLogSummary.textContent = `로그 삭제 실패: ${error?.message || error}`;
      }
    } finally {
      renderLogFiles();
    }
  }

  async function deleteSelectedFile() {
    if (activeFile === 'all') return;
    const fileName = activeFile;
    if (!window.confirm(`${fileName} 로그 파일을 삭제할까요?\n삭제한 로그는 복구할 수 없습니다.`)) return;
    if (el.deleteMotorEventLogFileButton) el.deleteMotorEventLogFileButton.disabled = true;
    try {
      await deleteMotorEventLogFile(fileName);
      activeFile = 'all';
      lastLoadedAt = 0;
      await refresh(true);
      window.dispatchEvent(new CustomEvent('motion-project-files-changed'));
    } catch (error) {
      if (el.motorEventLogSummary) {
        el.motorEventLogSummary.textContent = `로그 파일 삭제 실패: ${error?.message || error}`;
      }
    } finally {
      renderLogFiles();
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
    if (el.motorEventLogFileSelect) {
      el.motorEventLogFileSelect.addEventListener('change', () => {
        activeFile = el.motorEventLogFileSelect.value || 'all';
        lastLoadedAt = 0;
        refresh(true);
      });
    }
    if (el.deleteMotorEventLogFileButton) {
      el.deleteMotorEventLogFileButton.addEventListener('click', deleteSelectedFile);
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
    resetProjectState,
  };
}
