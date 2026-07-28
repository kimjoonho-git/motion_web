"""Thread-safe Panasonic servo-alarm policy and runtime state machine."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, Optional


@dataclass(frozen=True)
class AlarmEvaluation:
    changed: bool
    stop_required: bool


def policy_revision(grades: Dict[str, int], catalog_version: int) -> str:
    canonical = json.dumps(
        {
            'catalog_version': int(catalog_version),
            'grades': {
                str(key): int(value)
                for key, value in sorted(
                    grades.items(),
                    key=lambda item: int(item[0]),
                )
            },
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(',', ':'),
    )
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


class ServoAlarmGuard:
    """Own all mutable alarm-policy and latch state behind one lock."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._grades: Dict[str, int] = {}
        self._policy_project_id = ''
        self._policy_version = 0
        self._policy_revision = ''
        self._grade3_latched = False
        self._active_grade = 0
        self._active: list[Dict[str, Any]] = []
        self._blocked_axes: set[int] = set()
        self._recovery_hold_axes: set[int] = set()
        self._clear_since: Optional[float] = None
        self._stop_signature = ''

    def apply_policy(
        self,
        grades: Dict[str, int],
        *,
        project_id: str,
        catalog_version: int,
        revision: str,
    ) -> tuple[bool, str]:
        normalized: Dict[str, int] = {}
        for key, value in grades.items():
            try:
                code = int(str(key).split('.', 1)[0])
                grade = int(value)
            except (TypeError, ValueError):
                return False, f'서보 에러 등급 값이 올바르지 않습니다: {key}'
            if code <= 0 or grade not in (1, 2, 3):
                return False, f'서보 에러 등급 값이 올바르지 않습니다: {key}'
            normalized[str(code)] = grade
        expected_revision = policy_revision(normalized, catalog_version)
        if not revision or revision != expected_revision:
            return False, '서보 에러 정책 revision이 등급 내용과 일치하지 않습니다'
        with self._lock:
            self._grades = normalized
            self._policy_project_id = str(project_id or '')
            self._policy_version = int(catalog_version)
            self._policy_revision = expected_revision
        return True, (
            f'서보 에러 정책 적용 · {len(normalized)}개 코드 · '
            f'프로젝트 {project_id or "없음"} · revision {expected_revision[:12]}'
        )

    def reset_project_policy(self) -> None:
        with self._lock:
            self._grades = {}
            self._policy_project_id = ''
            self._policy_version = 0
            self._policy_revision = ''
            self._recovery_hold_axes.clear()

    def evaluate(
        self,
        motors: Iterable[Dict[str, Any]],
        *,
        is_ac_servo: Callable[[Dict[str, Any]], bool],
        axis_value: Callable[[Any], Optional[int]],
        playback_active: bool,
        now: Optional[float] = None,
    ) -> AlarmEvaluation:
        current_time = time.monotonic() if now is None else float(now)
        with self._lock:
            entries: list[Dict[str, Any]] = []
            blocked_axes: set[int] = set()
            max_grade = 0
            for motor in motors:
                if not isinstance(motor, dict) or not is_ac_servo(motor):
                    continue
                code = self.alarm_code(motor)
                if code < 0:
                    continue
                fault = bool(motor.get('fault', False))
                if code == 0 and not fault:
                    continue
                axis = axis_value(motor.get('controller_index'))
                if axis is None:
                    continue
                grade = int(self._grades.get(str(code), 2))
                grade = grade if grade in (1, 2, 3) else 2
                entries.append({
                    'axis': axis,
                    'code': code,
                    'grade': grade,
                    'error_text': str(motor.get('error_text') or ''),
                    'errorcode_hex': str(motor.get('errorcode_hex') or ''),
                })
                max_grade = max(max_grade, grade)
                if grade == 1:
                    blocked_axes.add(axis)

            previous_grade = self._active_grade
            previous_grade1_axes = {
                int(item['axis'])
                for item in self._active
                if int(item.get('grade') or 0) == 1
            }
            if playback_active:
                self._recovery_hold_axes.update(
                    previous_grade1_axes - blocked_axes
                )
            else:
                self._recovery_hold_axes.clear()
            blocked_axes.update(self._recovery_hold_axes)
            if blocked_axes:
                max_grade = max(max_grade, 1)

            previous_axes = set(self._blocked_axes)
            changed = entries != self._active or blocked_axes != previous_axes
            self._active = entries
            self._blocked_axes = blocked_axes

            if max_grade >= 2:
                self._clear_since = None
                self._active_grade = max_grade
            elif previous_grade >= 2 and not self._grade3_latched:
                if self._clear_since is None:
                    self._clear_since = current_time
                if current_time - self._clear_since >= 0.5:
                    self._active_grade = max_grade
                    self._stop_signature = ''
                else:
                    self._active_grade = previous_grade
            else:
                self._clear_since = None
                self._active_grade = 3 if self._grade3_latched else max_grade

            stop_required = False
            if max_grade >= 2:
                signature = '|'.join(
                    f"{item['axis']}:{item['code']}:{item['grade']}"
                    for item in entries if int(item['grade']) >= 2
                )
                if signature and signature != self._stop_signature:
                    self._stop_signature = signature
                    if max_grade >= 3:
                        self._grade3_latched = True
                        self._active_grade = 3
                    stop_required = True
                    changed = True

            return AlarmEvaluation(
                changed=changed or previous_grade != self._active_grade,
                stop_required=stop_required,
            )

    @staticmethod
    def alarm_code(motor: Dict[str, Any]) -> int:
        try:
            raw = int(motor.get('errorcode_raw') or 0)
        except (TypeError, ValueError):
            raw = 0
        if raw == 0xFFFF:
            return -1
        try:
            return int(motor.get('errorcode') or 0)
        except (TypeError, ValueError):
            return 0

    def block_reason(self, axis: Optional[int] = None) -> str:
        with self._lock:
            if self._grade3_latched:
                return '3등급 서보 에러 · 프로그램 재시작 전까지 전체 모터 제어 차단'
            if self._active_grade >= 2:
                return '2등급 서보 에러 · 정상 상태 확인 전까지 전체 모터 동작 차단'
            if axis is not None and axis in self._blocked_axes:
                return f'1등급 서보 에러 · 축 {axis} 모터 동작 차단'
            return ''

    def blocked_slots(self, controller_indexes: Iterable[Any]) -> set[int]:
        with self._lock:
            blocked = set(self._blocked_axes)
        return {
            slot
            for slot, value in enumerate(controller_indexes)
            if int(value) in blocked
        }

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                'grade': int(self._active_grade),
                'grade3_latched': bool(self._grade3_latched),
                'blocked_axes': sorted(self._blocked_axes),
                'recovery_hold_axes': sorted(self._recovery_hold_axes),
                'active': [dict(item) for item in self._active],
                'policy_project_id': self._policy_project_id,
                'policy_version': int(self._policy_version),
                'policy_revision': self._policy_revision,
            }
