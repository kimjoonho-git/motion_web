/**
 * Motion Schedule Management Module
 * Connects with /api/schedule REST endpoints and handles Schedule Modal UI
 */
import { motionLocalDateText } from './local_time.js';
import {
    motionScheduleBadgeState,
    motionScheduleScopeNote,
    motionScheduleTimezoneDrift,
} from './schedule_scope.js';

/** 그 PC 의 벽시계 글자 · 브라우저 시간대로 옮기지 않는다 · §6-147 */
function wallClockText(epochMs, offsetText) {
    const parsed = /^([+-])(\d{2})(\d{2})$/.exec(offsetText || '');
    if (!parsed) return new Date(epochMs).toISOString().slice(11, 19);
    const sign = parsed[1] === '-' ? -1 : 1;
    const minutes = sign * (Number(parsed[2]) * 60 + Number(parsed[3]));
    return new Date(epochMs + minutes * 60000).toISOString().slice(11, 19);
}

const ScheduleManager = {
    schedules: [],
    status: null,
    clockTimer: null,
    statusTimer: null,
    // 서버가 말한 시각과 그걸 받은 순간 · 사이 시간은 여기서 흘린다
    clockAnchor: null,

    async init() {
        this.bindEvents();
        await this.loadStatus();
        this.startClockTimer();
    },

    startClockTimer() {
        if (this.clockTimer) clearInterval(this.clockTimer);
        this.updateClock();
        this.clockTimer = setInterval(() => this.updateClock(), 1000);
        // 상태도 스스로 다시 읽는다 · §6-147
        //
        // 전에는 처음 한 번과 사람이 무엇을 누를 때만 읽었다 · 그래서 시각이
        // 되어 거부당해도 화면은 아무 일 없는 얼굴로 그대로 있었다 · 배지가
        // 빨개지려면 누가 보고 있지 않아도 다시 읽어야 한다.
        //
        // 5초인 이유 · 이 조회는 파일과 연동 노드를 건드린다 · 더 자주 두드리면
        // 보여주려던 화면이 서버를 느리게 만든다 · §6-146 에서 겪었다.
        if (this.statusTimer) clearInterval(this.statusTimer);
        this.statusTimer = setInterval(() => this.loadStatus(), 5000);
    },

    /** 그 PC 가 몇 시라고 믿는가 · §6-147
     *
     * 전에는 `new Date()` 였다 · 이름은 「PC 시각」인데 실제로는 **보는 사람의
     * 브라우저 시각**이었다 · 같은 방에서는 같으니 아무도 몰랐다.
     *
     * 해외에 설치하고 한국에서 원격으로 보면 갈린다 · 파리 PC 를 보면서
     * 한국 시각을 보고 09:17 을 넣게 된다 · NTP 는 UTC 만 맞추고 시간대는
     * 사람이 정하는 값이라, 네트워크에 붙여도 안 바뀐다.
     *
     * 그래서 서버가 준 값을 쓴다 · 시간대 이름을 같이 띄워야 「Asia/Seoul
     * 인데 여기 파리잖아」를 그 자리에서 알아챈다.
     */
    updateClock() {
        const timeEl = document.getElementById('schedulePcCurrentTime');
        if (!timeEl) return;
        const clock = this.status?.clock;
        if (!clock || !this.clockAnchor) {
            timeEl.textContent = '🕒 PC 시각: 확인 중';
            timeEl.title = '';
            return;
        }
        // 받은 시각에 그 뒤 흐른 만큼을 더한다 · 5초마다 툭툭 뛰지 않게
        const epoch = this.clockAnchor.at + (Date.now() - this.clockAnchor.received);
        const shown = wallClockText(epoch, this.clockAnchor.offset);
        const zone = clock.timezone || clock.abbreviation || '시간대 미상';
        const ntp = clock.ntp_synced === false ? ' · ⚠️ 시계 미동기' : '';
        timeEl.textContent = `🕒 PC 시각: ${shown} · ${zone}${ntp}`;
        timeEl.title = clock.ntp_synced === false
            ? '이 PC 는 시각을 맞춰주는 서버에 붙지 못했습니다 · 시계가 틀어져 있을 수 있습니다'
            : `이 PC 기준입니다 · 브라우저 시각이 아닙니다 · UTC${clock.utc_offset || ''}`;
    },

    bindEvents() {
        const scheduleBtn = document.getElementById('btnScheduleModal');
        if (scheduleBtn) {
            scheduleBtn.addEventListener('click', () => this.openScheduleModal());
        }
        const closeBtn = document.getElementById('btnCloseScheduleModal');
        if (closeBtn) {
            closeBtn.addEventListener('click', () => this.closeScheduleModal());
        }

        const modeSelect = document.getElementById('scheduleRunMode');
        if (modeSelect) {
            modeSelect.addEventListener('change', () => this.saveRunMode(modeSelect.value));
        }

        const addBtn = document.getElementById('btnAddSchedule');
        if (addBtn) {
            addBtn.addEventListener('click', () => this.openEditModal());
        }

        const saveBtn = document.getElementById('btnSaveScheduleForm');
        if (saveBtn) {
            saveBtn.addEventListener('click', () => this.saveScheduleForm());
        }

        const cancelFormBtn = document.getElementById('btnCancelScheduleForm');
        if (cancelFormBtn) {
            cancelFormBtn.addEventListener('click', () => this.closeEditModal());
        }

        const repeatTypeSelect = document.getElementById('schedRepeatType');
        if (repeatTypeSelect) {
            repeatTypeSelect.addEventListener('change', () => this.onRepeatTypeChange());
        }
    },

    async saveRunMode(mode) {
        try {
            const res = await fetch('/api/schedule/mode', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ run_mode: mode }),
            });
            if (!res.ok) {
                const detail = await res.json().catch(() => ({}));
                alert(`실행 관리 변경 실패: ${detail.detail || res.status}`);
            }
        } catch (err) {
            console.error('[ScheduleManager] Failed to save run mode:', err);
            alert('실행 관리 변경 중 통신 오류가 발생했습니다.');
        }
        await this.loadStatus();
    },

    async loadStatus() {
        try {
            const res = await fetch('/api/schedule/status');
            if (res.ok) {
                this.status = await res.json();
                const stamp = Date.parse(this.status?.clock?.local_time || '');
                this.clockAnchor = Number.isNaN(stamp) ? null : {
                    at: stamp,
                    received: Date.now(),
                    offset: this.status.clock.utc_offset || '',
                };
                this.updateStatusBadge();
                this.updateClock();
                this.renderTimezoneDrift();
            }
        } catch (err) {
            console.warn('[ScheduleManager] Failed to load schedule status:', err);
        }
    },

    updateStatusBadge() {
        // 네 가지 상태를 가른다 · 연동 안 씀 · 마스터 · 마스터인데 빠짐 ·
        // 슬레이브 · 판단은 `schedule_scope.js` 가 한다 · §6-133
        const state = motionScheduleBadgeState(this.status);
        // 전에는 `badge bg-success` 처럼 부트스트랩 이름을 썼다 · 이 프로젝트에는
        // 부트스트랩이 없고 인라인 배경색이 클래스를 이겨서, 네 가지로 갈라
        // 놓고도 **배지는 늘 회색**이었다 · §6-147
        const TONE_CLASS = {
            ok: 'schedule-badge tone-ok',
            warn: 'schedule-badge tone-warn',
            bad: 'schedule-badge tone-bad',
            muted: 'schedule-badge tone-muted',
        };

        const button = document.getElementById('btnScheduleModal');
        if (button) {
            button.disabled = !state.canEdit;
            button.title = state.blockedReason || state.warning || '';
        }

        const badge = document.getElementById('scheduleStatusBadge');
        if (badge) {
            badge.className = TONE_CLASS[state.tone] || TONE_CLASS.muted;
            badge.textContent = state.text;
        }

        // 발화해도 실행되지 않는 상태는 목록 위에 띄운다 · 전에는 로그에만
        // 남아서 "스케줄이 발화했는데 아무 일도 안 났다" 가 됐다 · §6-68
        // 슬레이브에서는 실행 관리가 아무 일도 하지 않는다 · 잠근다 · §6-143
        //
        // 슬레이브는 스케줄 자체가 안 돈다 · 마스터가 보내는 그룹 실행만
        // 이 PC 를 움직인다 · 여기서 수동으로 바꿔 두면 막힌 줄 알게 된다 ·
        // 실제로 막으려면 「지금 빠지기」로 그룹에서 나가야 한다.
        const modeSelect = document.getElementById('scheduleRunMode');
        if (modeSelect) {
            modeSelect.disabled = !state.canEdit;
            modeSelect.title = state.blockedReason || '';
            if (!modeSelect.matches(':focus')) {
                modeSelect.value = String(this.status?.run_mode || 'schedule');
            }
        }

        const notice = document.getElementById('scheduleScopeNotice');
        if (notice) {
            const message = state.warning || state.blockedReason;
            notice.textContent = message || motionScheduleScopeNote(this.status);
            notice.classList.toggle('schedule-scope-warning', Boolean(state.warning));
            notice.classList.toggle('schedule-scope-blocked', Boolean(state.blockedReason));
        }
    },

    async openScheduleModal() {
        const modalEl = document.getElementById('scheduleModal');
        if (modalEl) {
            modalEl.style.display = 'block';
            await this.loadSchedules();
        }
    },

    closeScheduleModal() {
        const modalEl = document.getElementById('scheduleModal');
        if (modalEl) {
            modalEl.style.display = 'none';
        }
    },

    async loadSchedules() {
        try {
            const res = await fetch('/api/schedule/list');
            if (res.ok) {
                this.schedules = await res.json();
                this.renderScheduleList();
                this.renderTimezoneDrift();
            }
        } catch (err) {
            console.error('[ScheduleManager] Failed to fetch schedules:', err);
        }
    },

    /** 들고 나갔는데 시간대만 안 바뀌었는가 · §6-150 */
    renderTimezoneDrift() {
        const box = document.getElementById('scheduleTimezoneDrift');
        if (!box) return;
        box.textContent = motionScheduleTimezoneDrift(
            this.schedules, this.status?.clock?.timezone,
        );
    },

    renderScheduleList() {
        const container = document.getElementById('scheduleListContainer');
        if (!container) return;

        if (!this.schedules || this.schedules.length === 0) {
            container.innerHTML = `
                <div class="text-center text-muted py-4">
                    등록된 모션 스케줄이 없습니다. [+ 신규 스케줄 추가] 버튼을 눌러 생성하세요.
                </div>
            `;
            return;
        }

        let html = '<div class="list-group">';
        this.schedules.forEach(item => {
            const enabledBadge = item.enabled
                ? '<span class="badge bg-success" style="background-color: #38a169; color: #fff; padding: 2px 6px; border-radius: 4px; font-size: 11px;">활성</span>'
                : '<span class="badge bg-secondary" style="background-color: #718096; color: #fff; padding: 2px 6px; border-radius: 4px; font-size: 11px;">비활성</span>';

            const stopInfo = `종료시각: ${item.stop_time || '설정안됨'}`;

            const daysInfo = item.repeat_type === 'weekly'
                ? `반복: 매주 [${(item.repeat_days || []).join(', ')}]`
                : (item.repeat_type === 'daily' ? '반복: 매일' : `1회: ${item.run_date || ''}`);

            html += `
                <div class="list-group-item" style="display: flex; justify-content: space-between; align-items: center; padding: 12px; margin-bottom: 8px; background: #1a202c; border: 1px solid #4a5568; border-radius: 6px;">
                    <div>
                        <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 4px;">
                            <strong style="font-size: 15px; color: #fff;">${this.escapeHtml(item.schedule_name)}</strong>
                            ${enabledBadge}
                        </div>
                        <div style="font-size: 12px; color: #a0aec0; display: flex; gap: 12px;">
                            <span>🕒 시작: <strong style="color: #63b3ed;">${item.start_time}</strong></span>
                            <span>🛑 ${stopInfo}</span>
                            <span>📅 ${daysInfo}</span>
                        </div>
                    </div>
                    <div style="display: flex; gap: 8px;">
                        <button type="button" style="background: ${item.enabled ? '#dd6b20' : '#38a169'}; color: #fff; border: none; padding: 6px 12px; border-radius: 4px; cursor: pointer; font-size: 12px; font-weight: bold;" 
                                onclick="window.ScheduleManager.toggleEnable('${item.schedule_id}', ${!item.enabled})">
                            ${item.enabled ? '비활성화' : '활성화'}
                        </button>
                        <button type="button" style="background: #e53e3e; color: #fff; border: none; padding: 6px 12px; border-radius: 4px; cursor: pointer; font-size: 12px; font-weight: bold;" 
                                onclick="window.ScheduleManager.deleteSchedule('${item.schedule_id}')">
                            삭제
                        </button>
                    </div>
                </div>
            `;
        });
        html += '</div>';
        container.innerHTML = html;
    },

    openEditModal(scheduleItem = null) {
        const modal = document.getElementById('scheduleEditModal');
        if (!modal) return;

        document.getElementById('schedEditId').value = scheduleItem ? scheduleItem.schedule_id : '';
        document.getElementById('schedName').value = scheduleItem ? scheduleItem.schedule_name : '새 연동 스케줄';
        document.getElementById('schedStartTime').value = scheduleItem ? scheduleItem.start_time : '09:00:00';
        document.getElementById('schedStopTime').value = scheduleItem ? (scheduleItem.stop_time || '18:00:00') : '18:00:00';
        document.getElementById('schedRepeatType').value = scheduleItem ? scheduleItem.repeat_type : 'daily';

        // Set date
        // 이 PC 시각의 오늘 · §6-144
        //
        // `toISOString()` 은 UTC 라, 자정부터 오전 9시 사이에 「1회」 스케줄을
        // 만들면 **어제 날짜**가 박혔다 · 「1회」는 그 날짜에만 도니까 영영
        // 안 돌았다 · 오후에 만들면 멀쩡해서 한참 몰랐다.
        const todayStr = motionLocalDateText();
        document.getElementById('schedRunDate').value = scheduleItem ? (scheduleItem.run_date || todayStr) : todayStr;

        // Set days checkboxes
        const days = (scheduleItem && Array.isArray(scheduleItem.repeat_days))
            ? scheduleItem.repeat_days
            : ["MON", "TUE", "WED", "THU", "FRI"];
        document.querySelectorAll('.sched-day-check').forEach(chk => {
            chk.checked = days.includes(chk.value);
        });

        this.onRepeatTypeChange();
        modal.style.display = 'block';
    },

    closeEditModal() {
        const modal = document.getElementById('scheduleEditModal');
        if (modal) {
            modal.style.display = 'none';
        }
    },


    onRepeatTypeChange() {
        const type = document.getElementById('schedRepeatType').value;
        const weeklyGroup = document.getElementById('schedWeeklyDaysGroup');
        const onceGroup = document.getElementById('schedOnceDateGroup');

        if (type === 'weekly') {
            if (weeklyGroup) weeklyGroup.style.display = 'block';
            if (onceGroup) onceGroup.style.display = 'none';
        } else if (type === 'once') {
            if (weeklyGroup) weeklyGroup.style.display = 'none';
            if (onceGroup) onceGroup.style.display = 'block';
        } else {
            if (weeklyGroup) weeklyGroup.style.display = 'none';
            if (onceGroup) onceGroup.style.display = 'none';
        }
    },

    async saveScheduleForm() {
        const id = document.getElementById('schedEditId').value;
        const name = document.getElementById('schedName').value.trim() || '새 스케줄';
        const startTime = document.getElementById('schedStartTime').value.trim() || '09:00:00';
        const stopTime = document.getElementById('schedStopTime').value.trim() || '18:00:00';
        const repeatType = document.getElementById('schedRepeatType').value;

        // Selected days
        const selectedDays = [];
        document.querySelectorAll('.sched-day-check:checked').forEach(chk => {
            selectedDays.push(chk.value);
        });

        const runDate = document.getElementById('schedRunDate').value || null;

        const payload = {
            schedule_name: name,
            start_time: startTime,
            // 정지 방식은 「지정 시각」 하나다 · §6-137
            stop_mode: 'time',
            stop_time: stopTime,
            duration_sec: null,
            repeat_type: repeatType,
            repeat_days: repeatType === 'weekly' ? selectedDays : ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"],
            run_date: repeatType === 'once' ? runDate : null,
            enabled: true
        };

        if (id) {
            payload.schedule_id = id;
        }

        try {
            const res = await fetch('/api/schedule/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            if (res.ok) {
                this.closeEditModal();
                await this.loadSchedules();
                await this.loadStatus();
            } else {
                const errData = await res.json().catch(() => ({}));
                const msg = errData.detail || '스케줄 저장에 실패했습니다.';
                alert(`스케줄 저장 실패: ${msg}`);
            }
        } catch (err) {
            console.error('[ScheduleManager] Save error:', err);
            alert('스케줄 저장 중 통신 오류가 발생했습니다.');
        }
    },

    async toggleEnable(scheduleId, targetEnable) {
        const endpoint = targetEnable ? `/api/schedule/${scheduleId}/enable` : `/api/schedule/${scheduleId}/disable`;
        try {
            const res = await fetch(endpoint, { method: 'POST' });
            if (res.ok) {
                await this.loadSchedules();
                await this.loadStatus();
            } else {
                const errData = await res.json().catch(() => ({}));
                alert(`상태 변경 실패: ${errData.detail || '오류 발생'}`);
            }
        } catch (err) {
            console.error('[ScheduleManager] Toggle enable error:', err);
            alert('상태 변경 중 통신 오류가 발생했습니다.');
        }
    },

    async deleteSchedule(scheduleId) {
        if (!confirm('이 스케줄을 삭제하시겠습니까?')) return;

        try {
            const res = await fetch(`/api/schedule/${scheduleId}`, { method: 'DELETE' });
            if (res.ok) {
                await this.loadSchedules();
                await this.loadStatus();
            } else {
                const errData = await res.json().catch(() => ({}));
                alert(`스케줄 삭제 실패: ${errData.detail || '오류 발생'}`);
            }
        } catch (err) {
            console.error('[ScheduleManager] Delete schedule error:', err);
            alert('스케줄 삭제 중 통신 오류가 발생했습니다.');
        }
    },

    escapeHtml(str) {
        return String(str).replace(/[&<>"']/g, m => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        })[m]);
    }
};

window.ScheduleManager = ScheduleManager;

document.addEventListener('DOMContentLoaded', () => {
    ScheduleManager.init();
});
