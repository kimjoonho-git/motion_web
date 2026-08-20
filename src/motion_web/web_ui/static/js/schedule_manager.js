/**
 * Motion Schedule Management Module
 * Connects with /api/schedule REST endpoints and handles Schedule Modal UI
 */

const ScheduleManager = {
    schedules: [],
    status: null,
    clockTimer: null,

    async init() {
        this.bindEvents();
        await this.loadStatus();
        this.startClockTimer();
    },

    startClockTimer() {
        if (this.clockTimer) clearInterval(this.clockTimer);
        this.updateClock();
        this.clockTimer = setInterval(() => this.updateClock(), 1000);
    },

    updateClock() {
        const timeEl = document.getElementById('schedulePcCurrentTime');
        if (timeEl) {
            const now = new Date();
            const timeStr = now.toTimeString().split(' ')[0];
            timeEl.textContent = `🕒 PC 시각: ${timeStr}`;
        }
    },

    bindEvents() {
        const scheduleBtn = document.getElementById('btnScheduleModal');
        if (scheduleBtn) {
            scheduleBtn.addEventListener('click', () => this.openScheduleModal());
        }
        const scheduleBtnCoord = document.getElementById('btnScheduleModalCoord');
        if (scheduleBtnCoord) {
            scheduleBtnCoord.addEventListener('click', () => this.openScheduleModal());
        }

        const closeBtn = document.getElementById('btnCloseScheduleModal');
        if (closeBtn) {
            closeBtn.addEventListener('click', () => this.closeScheduleModal());
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

        const stopModeSelect = document.getElementById('schedStopMode');
        if (stopModeSelect) {
            stopModeSelect.addEventListener('change', () => this.onStopModeChange());
        }

        const repeatTypeSelect = document.getElementById('schedRepeatType');
        if (repeatTypeSelect) {
            repeatTypeSelect.addEventListener('change', () => this.onRepeatTypeChange());
        }
    },

    async loadStatus() {
        try {
            const res = await fetch('/api/schedule/status');
            if (res.ok) {
                this.status = await res.json();
                this.updateStatusBadge();
            }
        } catch (err) {
            console.warn('[ScheduleManager] Failed to load schedule status:', err);
        }
    },

    updateStatusBadge() {
        const badge = document.getElementById('scheduleStatusBadge');
        const badgeCoord = document.getElementById('scheduleStatusBadgeCoord');
        if (this.status) {
            const text = this.status.is_master
                ? `스케줄러: 마스터 (${this.status.schedule_count || 0}개 등록)`
                : '스케줄러: 슬레이브 대기';
            const cls = this.status.is_master ? 'badge bg-success me-2' : 'badge bg-secondary me-2';

            if (badge) {
                badge.className = cls;
                badge.textContent = text;
            }
            if (badgeCoord) {
                badgeCoord.className = cls;
                badgeCoord.textContent = text;
            }
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
            }
        } catch (err) {
            console.error('[ScheduleManager] Failed to fetch schedules:', err);
        }
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

            const stopInfo = item.stop_mode === 'duration'
                ? `유지시간: ${item.duration_sec || 0}초`
                : `종료시각: ${item.stop_time || '설정안됨'}`;

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
        document.getElementById('schedStopMode').value = scheduleItem ? scheduleItem.stop_mode : 'time';
        document.getElementById('schedStopTime').value = scheduleItem ? (scheduleItem.stop_time || '18:00:00') : '18:00:00';
        document.getElementById('schedDurationSec').value = scheduleItem ? (scheduleItem.duration_sec || 3600) : 3600;
        document.getElementById('schedRepeatType').value = scheduleItem ? scheduleItem.repeat_type : 'daily';

        // Set date
        const todayStr = new Date().toISOString().split('T')[0];
        document.getElementById('schedRunDate').value = scheduleItem ? (scheduleItem.run_date || todayStr) : todayStr;

        // Set days checkboxes
        const days = (scheduleItem && Array.isArray(scheduleItem.repeat_days))
            ? scheduleItem.repeat_days
            : ["MON", "TUE", "WED", "THU", "FRI"];
        document.querySelectorAll('.sched-day-check').forEach(chk => {
            chk.checked = days.includes(chk.value);
        });

        this.onStopModeChange();
        this.onRepeatTypeChange();
        modal.style.display = 'block';
    },

    closeEditModal() {
        const modal = document.getElementById('scheduleEditModal');
        if (modal) {
            modal.style.display = 'none';
        }
    },

    onStopModeChange() {
        const mode = document.getElementById('schedStopMode').value;
        const timeGroup = document.getElementById('schedStopTimeGroup');
        const durationGroup = document.getElementById('schedDurationGroup');

        if (mode === 'duration') {
            if (timeGroup) timeGroup.style.display = 'none';
            if (durationGroup) durationGroup.style.display = 'block';
        } else {
            if (timeGroup) timeGroup.style.display = 'block';
            if (durationGroup) durationGroup.style.display = 'none';
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
        const stopMode = document.getElementById('schedStopMode').value;
        const stopTime = document.getElementById('schedStopTime').value.trim() || '18:00:00';
        const durationSec = parseInt(document.getElementById('schedDurationSec').value, 10) || 3600;
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
            stop_mode: stopMode,
            stop_time: stopMode === 'time' ? stopTime : null,
            duration_sec: stopMode === 'duration' ? durationSec : null,
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
