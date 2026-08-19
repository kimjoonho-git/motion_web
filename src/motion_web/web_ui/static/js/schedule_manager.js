/**
 * Motion Schedule Management Module
 * Connects with /api/schedule REST endpoints and handles Schedule Modal UI
 */

const ScheduleManager = {
    schedules: [],
    status: null,

    async init() {
        this.bindEvents();
        await this.loadStatus();
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
                    등록된 모션 스케줄이 없습니다. [스케줄 추가] 버튼을 눌러 생성하세요.
                </div>
            `;
            return;
        }

        let html = '<div class="list-group">';
        this.schedules.forEach(item => {
            const enabledBadge = item.enabled
                ? '<span class="badge bg-success">활성</span>'
                : '<span class="badge bg-secondary">비활성</span>';

            const stopInfo = item.stop_mode === 'duration'
                ? `유지시간: ${item.duration_sec || 0}초`
                : `종료시각: ${item.stop_time || '설정안됨'}`;

            const daysInfo = item.repeat_type === 'weekly'
                ? `반복: 매주 [${(item.repeat_days || []).join(', ')}]`
                : (item.repeat_type === 'daily' ? '반복: 매일' : `1회: ${item.run_date || ''}`);

            html += `
                <div class="list-group-item list-group-item-action d-flex justify-content-between align-items-center p-3 mb-2 rounded border">
                    <div>
                        <div class="d-flex align-items-center gap-2 mb-1">
                            <h6 class="mb-0 fw-bold">${this.escapeHtml(item.schedule_name)}</h6>
                            ${enabledBadge}
                        </div>
                        <div class="small text-muted">
                            <span class="me-3">🕒 시작: <strong>${item.start_time}</strong></span>
                            <span class="me-3">🛑 ${stopInfo} (회차 완주 정지)</span>
                            <span>📅 ${daysInfo}</span>
                        </div>
                    </div>
                    <div class="d-flex align-items-center gap-2">
                        <button class="btn btn-sm ${item.enabled ? 'btn-outline-warning' : 'btn-outline-success'}" 
                                onclick="ScheduleManager.toggleEnable('${item.schedule_id}', ${!item.enabled})">
                            ${item.enabled ? '비활성화' : '활성화'}
                        </button>
                        <button class="btn btn-sm btn-outline-danger" 
                                onclick="ScheduleManager.deleteSchedule('${item.schedule_id}')">
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

        this.onStopModeChange();
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

    async saveScheduleForm() {
        const id = document.getElementById('schedEditId').value;
        const name = document.getElementById('schedName').value.trim() || '새 스케줄';
        const startTime = document.getElementById('schedStartTime').value.trim() || '09:00:00';
        const stopMode = document.getElementById('schedStopMode').value;
        const stopTime = document.getElementById('schedStopTime').value.trim() || '18:00:00';
        const durationSec = parseInt(document.getElementById('schedDurationSec').value, 10) || 3600;
        const repeatType = document.getElementById('schedRepeatType').value;

        const payload = {
            schedule_name: name,
            start_time: startTime,
            stop_mode: stopMode,
            stop_time: stopMode === 'time' ? stopTime : null,
            duration_sec: stopMode === 'duration' ? durationSec : null,
            repeat_type: repeatType,
            repeat_days: ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"],
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
                alert('스케줄 저장에 실패했습니다.');
            }
        } catch (err) {
            console.error('[ScheduleManager] Save error:', err);
            alert('스케줄 저장 오류 발생');
        }
    },

    async toggleEnable(scheduleId, targetEnable) {
        const endpoint = targetEnable ? `/api/schedule/${scheduleId}/enable` : `/api/schedule/${scheduleId}/disable`;
        try {
            const res = await fetch(endpoint, { method: 'POST' });
            if (res.ok) {
                await this.loadSchedules();
                await this.loadStatus();
            }
        } catch (err) {
            console.error('[ScheduleManager] Toggle enable error:', err);
        }
    },

    async deleteSchedule(scheduleId) {
        if (!confirm('이 스케줄을 삭제하시겠습니까?')) return;

        try {
            const res = await fetch(`/api/schedule/${scheduleId}`, { method: 'DELETE' });
            if (res.ok) {
                await this.loadSchedules();
                await this.loadStatus();
            }
        } catch (err) {
            console.error('[ScheduleManager] Delete schedule error:', err);
        }
    },

    escapeHtml(str) {
        return String(str).replace(/[&<>"']/g, m => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        })[m]);
    }
};

document.addEventListener('DOMContentLoaded', () => {
    ScheduleManager.init();
});
