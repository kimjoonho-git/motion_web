"""Panasonic MINAS A6B servo-alarm catalog and project policy helpers."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable


CATALOG_VERSION = 1
UNKNOWN_ALARM_GRADE = 2

GRADE_DEFINITIONS = {
    '1': {
        'label': '1등급',
        'action': '해당 에러축 정지',
        'active_block': '해당 에러축만 동작 차단',
    },
    '2': {
        'label': '2등급',
        'action': '전체 모션 종료',
        'active_block': '전체 모터 동작 차단',
    },
    '3': {
        'label': '3등급',
        'action': '전체 모터 제어 차단',
        'active_block': '프로그램 재시작 전까지 전체 모터 제어 차단',
    },
}


def grade_action(grade: Any) -> str:
    return str(GRADE_DEFINITIONS.get(str(int(grade)), GRADE_DEFINITIONS['2'])['action'])


def _entry(
    code: int,
    name: str,
    grade: int,
    _legacy_action: str,
    guidance: str,
    *,
    ethercat_related: bool = False,
) -> Dict[str, Any]:
    return {
        'code': int(code),
        'code_label': f'Err{int(code)}.*',
        'name': name,
        'default_grade': int(grade),
        'default_action': grade_action(grade),
        'guidance': guidance,
        'ethercat_related': bool(ethercat_related),
    }


SERVO_ALARM_CATALOG = (
    _entry(11, '제어전원 저전압', 2, '전체 모션 종료', '제어전원과 배선을 확인한 후 드라이버 전원을 다시 켜세요.'),
    _entry(12, '과전압', 3, '전체 모터 제어 차단', '전원과 회생 조건을 점검한 후 드라이버 전원을 다시 켜세요.'),
    _entry(13, '주전원 저전압', 2, '전체 모션 종료', '주전원과 차단기 상태를 확인한 후 드라이버 전원을 다시 켜세요.'),
    _entry(14, '과전류·IPM 오류', 3, '전체 모터 제어 차단', '모터선과 단락 여부를 점검하고 원인을 확인하기 전에는 다시 구동하지 마세요.'),
    _entry(15, '드라이버·엔코더 과열', 3, '전체 모터 제어 차단', '온도와 냉각 상태를 점검하고 충분히 식힌 후 전원을 다시 켜세요.'),
    _entry(16, '과부하·토크 포화', 1, '해당 에러축 정지', '부하와 걸림 상태를 확인하고 10초 이상 기다린 후 필요하면 전원을 다시 켜세요.'),
    _entry(18, '회생 부하·회생 트랜지스터 오류', 3, '전체 모터 제어 차단', '회생저항과 감속 조건을 점검한 후 드라이버 전원을 다시 켜세요.'),
    _entry(21, '엔코더 통신 단절·통신 오류', 2, '전체 모션 종료', '엔코더 케이블과 커넥터를 확인한 후 드라이버 전원을 다시 켜세요.'),
    _entry(23, '엔코더 통신 데이터 오류', 2, '전체 모션 종료', '엔코더 배선과 노이즈 상태를 확인한 후 드라이버 전원을 다시 켜세요.'),
    _entry(24, '위치·속도 편차 과대', 2, '전체 모션 종료', '기구 걸림과 명령 조건을 확인한 후 현재 위치를 확인하고 다시 시작하세요.'),
    _entry(25, '하이브리드 편차 과대', 2, '전체 모션 종료', '외부 스케일과 기구 상태를 확인한 후 전원을 다시 켜세요.'),
    _entry(26, '과속', 2, '전체 모션 종료', '속도 설정과 기구 상태를 확인한 후 드라이버 전원을 다시 켜세요.'),
    _entry(27, '절대위치·위치명령·운전명령 오류', 1, '해당 에러축 정지', '명령값과 절대위치 상태를 확인한 후 해당 동작을 다시 실행하세요.'),
    _entry(28, '펄스 재생 제한', 2, '전체 모션 종료', '명령 주기와 속도 설정을 확인한 후 모션을 다시 실행하세요.'),
    _entry(29, '카운터 오버플로', 2, '전체 모션 종료', '위치 범위와 명령값을 확인한 후 드라이버 전원을 다시 켜세요.'),
    _entry(31, '안전기능 오류', 3, '전체 모터 제어 차단', '안전회로와 STO 입력을 점검하고 원인을 확인하기 전에는 구동하지 마세요.'),
    _entry(33, '입출력 기능 할당 오류', 2, '전체 모션 종료', '드라이버 입출력 기능 설정을 확인한 후 전원을 다시 켜세요.'),
    _entry(34, '소프트웨어 리미트·절대위치 범위 오류', 1, '해당 에러축 정지', '축 위치와 제한범위를 확인한 후 안전한 방향으로 조정하세요.'),
    _entry(36, 'EEPROM 파라미터 오류', 3, '전체 모터 제어 차단', '파라미터와 드라이버 상태를 점검하고 전원을 다시 켜세요.'),
    _entry(37, 'EEPROM 체크 코드 오류', 3, '전체 모터 제어 차단', '드라이버 파라미터와 하드웨어를 점검하세요.'),
    _entry(38, '오버트래블 입력', 1, '해당 에러축 정지', '리미트 입력과 축 위치를 확인하고 안전한 방향으로 이동하세요.'),
    _entry(40, '절대 엔코더 시스템 다운', 2, '전체 모션 종료', '절대 엔코더 상태를 확인하고 필요한 초기화를 수행하세요.'),
    _entry(41, '절대 엔코더 카운터 초과', 2, '전체 모션 종료', '절대위치 범위와 엔코더 상태를 확인하세요.'),
    _entry(42, '절대 엔코더 과속', 2, '전체 모션 종료', '절대 엔코더 초기화와 기구 상태를 확인하세요.'),
    _entry(44, '절대 단일회전 카운터 오류', 2, '전체 모션 종료', '엔코더 상태와 현재 위치를 확인하세요.'),
    _entry(45, '절대 다회전 카운터 오류', 2, '전체 모션 종료', '엔코더 배터리와 다회전 데이터를 확인하세요.'),
    _entry(47, '절대 엔코더 상태 오류', 2, '전체 모션 종료', '엔코더 상태와 배선을 확인한 후 전원을 다시 켜세요.'),
    _entry(50, '외부 스케일 연결·통신 오류', 2, '전체 모션 종료', '외부 스케일 케이블과 연결 상태를 확인하세요.'),
    _entry(51, '외부 스케일 상태 오류', 2, '전체 모션 종료', '외부 스케일 상태와 설정을 확인하세요.'),
    _entry(55, '외부 스케일 A/B/Z상 연결 오류', 2, '전체 모션 종료', '외부 스케일 신호 배선을 확인하세요.'),
    _entry(60, '모터 설정 오류', 2, '전체 모션 종료', '모터 모델과 드라이버 설정을 확인한 후 전원을 다시 켜세요.'),
    _entry(70, '전류 검출기 오류', 3, '전체 모터 제어 차단', '드라이버 하드웨어를 점검하고 원인을 확인하기 전에는 구동하지 마세요.'),
    _entry(72, '열 관련 오류', 3, '전체 모터 제어 차단', '드라이버 온도와 냉각 상태를 점검하세요.'),
    _entry(80, 'EtherCAT ESM·동기·PDO 오류', 2, '전체 모션 종료', '재시작 또는 검색이 끝난 뒤에도 지속되면 EtherCAT 상태를 확인하세요.', ethercat_related=True),
    _entry(81, 'EtherCAT 주기·Mailbox·DC 오류', 2, '전체 모션 종료', 'EtherCAT 주기와 통신 설정을 확인하세요.', ethercat_related=True),
    _entry(84, '동기 확립 초기화 오류', 2, '전체 모션 종료', 'EtherCAT 동기 상태를 확인한 후 모터 제어를 다시 시작하세요.', ethercat_related=True),
    _entry(85, 'PDO 할당·링크·SII 오류', 2, '전체 모션 종료', 'EtherCAT 링크와 PDO 설정을 확인하세요.', ethercat_related=True),
    _entry(87, '강제 알람·복귀 동작 오류', 1, '해당 에러축 정지', '강제 알람 입력과 복귀 동작 결과를 확인하세요.'),
    _entry(88, '전원·제어모드·ESM 운전 오류', 2, '전체 모션 종료', '전원과 EtherCAT 운전상태를 확인한 후 다시 시작하세요.', ethercat_related=True),
    _entry(91, '명령 오류', 1, '해당 에러축 정지', '명령값과 실행 순서를 확인한 후 해당 동작을 다시 실행하세요.'),
    _entry(92, '엔코더·외부 스케일 데이터 복구 오류', 2, '전체 모션 종료', '엔코더와 외부 스케일 데이터를 확인하세요.'),
    _entry(93, '파라미터 설정 오류', 2, '전체 모션 종료', '드라이버 파라미터를 확인한 후 전원을 다시 켜세요.'),
    _entry(94, '원점복귀 오류', 1, '해당 에러축 정지', '원점 센서와 원점복귀 조건을 확인한 후 다시 실행하세요.'),
    _entry(95, '모터 자동인식 오류', 2, '전체 모션 종료', '모터 연결과 모델 조합을 확인한 후 전원을 다시 켜세요.'),
    _entry(96, '내부 제어부 오류', 3, '전체 모터 제어 차단', '드라이버 전원을 차단하고 하드웨어를 점검하세요.'),
    _entry(98, '통신 하드웨어 오류', 3, '전체 모터 제어 차단', 'EtherCAT 하드웨어와 드라이버 상태를 점검하세요.'),
)

_CATALOG_BY_CODE = {entry['code']: entry for entry in SERVO_ALARM_CATALOG}


def normalize_overrides(value: Any) -> Dict[str, int]:
    if not isinstance(value, dict):
        return {}
    normalized: Dict[str, int] = {}
    for key, grade in value.items():
        try:
            code = int(str(key).split('.', 1)[0])
            parsed_grade = int(grade)
        except (TypeError, ValueError):
            continue
        if code not in _CATALOG_BY_CODE or parsed_grade not in (1, 2, 3):
            continue
        normalized[str(code)] = parsed_grade
    return normalized


def effective_grade_map(overrides: Any = None) -> Dict[str, int]:
    normalized = normalize_overrides(overrides)
    return {
        str(entry['code']): int(normalized.get(str(entry['code']), entry['default_grade']))
        for entry in SERVO_ALARM_CATALOG
    }


def policy_revision(
    effective_grades: Dict[str, int],
    catalog_version: int = CATALOG_VERSION,
) -> str:
    canonical = json.dumps(
        {
            'catalog_version': int(catalog_version),
            'grades': {
                str(key): int(value)
                for key, value in sorted(
                    effective_grades.items(),
                    key=lambda item: int(item[0]),
                )
            },
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(',', ':'),
    )
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def catalog_payload(overrides: Any = None) -> list[Dict[str, Any]]:
    normalized = normalize_overrides(overrides)
    rows = []
    for entry in SERVO_ALARM_CATALOG:
        key = str(entry['code'])
        project_grade = normalized.get(key)
        rows.append({
            **entry,
            'project_grade': project_grade,
            'effective_grade': int(project_grade or entry['default_grade']),
            'action': grade_action(project_grade or entry['default_grade']),
            'modified': project_grade is not None,
        })
    return rows


def configured_counts(entries: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    counts = {'1': 0, '2': 0, '3': 0, 'modified': 0}
    for entry in entries:
        grade = str(int(entry.get('effective_grade') or UNKNOWN_ALARM_GRADE))
        counts[grade] = counts.get(grade, 0) + 1
        if entry.get('modified'):
            counts['modified'] += 1
    return counts
