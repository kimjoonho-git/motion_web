"""Pure motion-layer editing operations.

This module never sends motor commands and never writes project files.  It
transforms a temporary layer supplied by the editor node; the studio node is
the sole owner of final project persistence.
"""

from __future__ import annotations

import copy
import heapq
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .axis_operations import apply_axis_operation
from .constants import DEFAULT_PERIOD_SEC
from .layer_validation import point_curve_frame_mismatches
from .curve_engine import (
    EPSILON,
    MAX_TIME_SCALE,
    finite as _finite,
    frame_time as _time,
    point_curve_order,
    render_point_curve,
)
from .point_curve_operations import (
    transform_point_curve,
    validate_point_curve_overlaps,
)
from .motion_model import normalize_layer, point_curve_bounds, unique_motion_ids
from .timeline import layer_conflicts, owned_at, playback_ownership, render_project


MAX_EDIT_FRAMES = 500_000
#: 포인트 상한 · §6-111
#:
#: 200 이던 시절에는 5분짜리 녹화가 「전체 포인트 생성」 자체가 되지 않았다 ·
#: 그때는 포인트 하나를 고를 때마다 표본 전체를 다시 훑느라 2,000개를 고르는
#: 데 54초가 걸려 상한을 올릴 수가 없었다.
#:
#: 이제 구간 캐시로 고르고 나쁜 구간을 한 번에 채운다 · 20분(60,000표본)에
#: 5,000 포인트도 1.3초다 · 상한은 계산이 아니라 **편집 화면이 감당할 양**으로
#: 정한다.
MAX_APPROXIMATION_POINTS = 5000

#: 1차 통과를 얼마나 느슨하게 잡을지 · §6-112
#:
#: 1차 통과는 **직선**으로 재는데 사용자가 실제로 편집할 곡선은 3·5차다 ·
#: 완만한 구간은 직선보다 훨씬 잘 맞으므로 직선 기준으로 재면 필요 없는
#: 포인트까지 잡는다 · 여기서 느슨하게 잡고, 실제 곡선을 그려 보는 2차 통과가
#: 모자란 자리만 채운다.
#:
#: 실제 녹화(18.7초)에서 0.5° 288→247개, 1° 188→169개 · 최대 오차는 그대로
#: 허용치 안이다 · 더 느슨하게(×3 이상) 잡으면 2차 통과가 도로 채워 이득이 없다.
LINEAR_PASS_RELAXATION = 2.0

#: 값을 풀어 줄 때 한 번에 함께 움직이는 이웃 포인트 수 · §6-117
FREE_VALUE_WINDOW = 4

#: 한 바퀴에 시도해 볼 후보 수 · 가장 덜 중요한 것부터
FREE_VALUE_TRIES = 8

#: 최소제곱을 다시 푸는 횟수 · 큰 오차에 무게를 실어 최대 오차 기준에 다가간다
FREE_VALUE_SWEEPS = 3

#: 한 번에 들여다보는 표본 수의 상한 · §6-117
#:
#: 포인트가 줄어들수록 이웃 여덟 개가 덮는 시간이 길어진다 · 상한이 없으면
#: 끝물에 창이 수천 표본까지 벌어져 계산이 폭증한다(60초 신호에서 29초).
#: 창이 넓어지면 함께 움직이는 이웃 수를 줄여 맞춘다.
FREE_VALUE_MAX_SAMPLES = 240


def _selected_ids(layer: Dict[str, Any], values: Iterable[Any]) -> List[str]:
    available = {
        str(motion_id)
        for frame in layer.get('frames') or []
        for motion_id in (frame.get('values') or {})
    }
    selected = unique_motion_ids(values)
    missing = [motion_id for motion_id in selected if motion_id not in available]
    if missing:
        raise ValueError('레이어에 없는 Motion ID: ' + ', '.join(missing))
    if not selected:
        raise ValueError('편집할 Motion ID를 선택하세요')
    return selected


def _tracks(layer: Dict[str, Any]) -> Dict[str, List[tuple[float, float]]]:
    tracks: Dict[str, List[tuple[float, float]]] = {}
    for frame in layer.get('frames') or []:
        time_sec = _finite(frame.get('time_sec'), '프레임 시간')
        for motion_id, value in (frame.get('values') or {}).items():
            tracks.setdefault(str(motion_id), []).append(
                (round(time_sec, 9), _finite(value, f'{motion_id} 모션'))
            )
    for points in tracks.values():
        points.sort(key=lambda item: item[0])
    return tracks


def _frames(tracks: Mapping[str, Sequence[tuple[float, float]]]) -> List[Dict[str, Any]]:
    by_time: Dict[float, Dict[str, float]] = {}
    occupied: Dict[tuple[str, float], float] = {}
    for motion_id, points in tracks.items():
        for raw_time, raw_value in points:
            time_sec = round(_time(raw_time), 9)
            key = (motion_id, time_sec)
            if key in occupied:
                raise ValueError(f'{motion_id}의 {time_sec:.3f}초 데이터가 겹칩니다')
            occupied[key] = raw_value
            by_time.setdefault(time_sec, {})[motion_id] = float(raw_value)
    return [
        {'frame': index, 'time_sec': time_sec, 'values': by_time[time_sec]}
        for index, time_sec in enumerate(sorted(by_time), start=1)
        if by_time[time_sec]
    ]


def _inside(time_sec: float, start_sec: float, end_sec: float) -> bool:
    return start_sec - EPSILON <= time_sec <= end_sec + EPSILON


def _overlapping_curves(
    layer: Dict[str, Any], motion_ids: Iterable[str], start_sec: float, end_sec: float,
    *, excluding_curve_id: str = '',
) -> List[Dict[str, Any]]:
    selected = set(motion_ids)
    result = []
    for curve in layer.get('point_curves') or []:
        if (
            curve.get('motion_id') not in selected
            or curve.get('curve_id') == excluding_curve_id
        ):
            continue
        curve_start, curve_end = point_curve_bounds(curve)
        if curve_start <= end_sec + EPSILON and curve_end >= start_sec - EPSILON:
            result.append(curve)
    return result


#: 포인트를 고르는 네 단계가 함께 보는 사실 · §6-128
#:
#: 전에는 이 값들이 560줄짜리 함수의 지역 변수였고, 중첩 함수 열다섯 개가
#: 그냥 집어 썼다 · 단계 하나만 떼어 시험할 수 없었고, 한 단계를 고치면 다른
#: 단계가 같은 변수를 어떻게 쓰는지 일일이 봐야 했다.
@dataclass
class _FitContext:
    ordered: List[tuple[float, float]]
    tolerance: float
    curve_order: int
    point_limit: int
    #: 솎아내기에 쓸 시도 횟수 · 한 칸짜리 목록인 것은 안에서 줄여야 하기 때문이다
    prune_attempts: List[int]

    @property
    def tangent_mode(self) -> str:
        return 'linear' if self.curve_order == 1 else 'auto'

    def chord_error(self, index: int, left: int, right: int) -> float:
        """직선으로 이었을 때 이 표본이 얼마나 벗어나는가."""
        time_sec, value = self.ordered[index]
        left_time, left_value = self.ordered[left]
        right_time, right_value = self.ordered[right]
        span = right_time - left_time
        ratio = (time_sec - left_time) / span if span > EPSILON else 0.0
        expected = left_value + ((right_value - left_value) * ratio)
        return abs(value - expected)

    def points_at(
        self, indices: Sequence[int], stable_ids: bool = False
    ) -> List[Dict[str, Any]]:
        return [
            {
                'point_id': (
                    f'point_{uuid.uuid4().hex[:8]}' if stable_ids else f'fit_{index}'
                ),
                'time_sec': self.ordered[index][0],
                'value_deg': self.ordered[index][1],
                'tangent_mode': self.tangent_mode,
            }
            for index in indices
        ]

    def errors_of(self, points: Sequence[Dict[str, Any]]) -> List[float]:
        """이 포인트들로 그린 곡선이 원래 표본에서 벗어나는 정도."""
        _normalized, rendered = render_point_curve(list(points), self.curve_order)
        rendered_by_time = {
            round(time_sec, 9): float(value) for time_sec, value in rendered
        }
        return [
            abs(value - rendered_by_time[round(time_sec, 9)])
            for time_sec, value in self.ordered
            if round(time_sec, 9) in rendered_by_time
        ]


def _select_by_chords(context: _FitContext) -> set:
    """1단계 · 직선으로 재어 뼈대를 잡는다 · §6-111 §6-112 §6-128

    포인트 두 개를 **직선**으로 이었을 때 가장 벗어난 표본에 포인트를 하나
    더한다 · 쪼갠 구간만 다시 재므로 표본이 많아도 빠르다.

    멈추는 기준은 허용 오차의 몇 배다 · 실제 곡선은 3·5차라 직선보다 훨씬 잘
    맞는다 · 직선 기준으로 빡빡하게 재면 필요 없는 포인트까지 잡는다.
    """
    ordered = context.ordered
    selected = {0, len(ordered) - 1}
    if len(ordered) > 2:
        selected.add(len(ordered) // 2)

    def worst_inside(left: int, right: int) -> tuple[float, int]:
        """이 구간에서 가장 어긋난 표본 하나 · 같으면 앞쪽이 이긴다."""
        candidate = -1
        worst = -1.0
        for index in range(left + 1, right):
            error = context.chord_error(index, left, right)
            if error > worst:
                candidate = index
                worst = error
        return worst, candidate

    linear_threshold = context.tolerance * (
        1.0 if context.curve_order == 1 else LINEAR_PASS_RELAXATION
    )
    segment_end: Dict[int, int] = {}
    queue: List[tuple[float, int, int, int]] = []
    start_indices = sorted(selected)
    for left, right in zip(start_indices, start_indices[1:]):
        segment_end[left] = right
        worst, candidate = worst_inside(left, right)
        if candidate >= 0:
            heapq.heappush(queue, (-worst, left, right, candidate))

    while len(selected) < min(context.point_limit, len(ordered)):
        head = None
        while queue:
            negative_error, left, right, candidate = queue[0]
            if segment_end.get(left) != right:
                heapq.heappop(queue)      # 이미 쪼개진 구간 · 버린다
                continue
            head = (-negative_error, left, right, candidate)
            break
        if head is None:
            break
        maximum_error, left, right, candidate = head
        if maximum_error <= linear_threshold:
            break
        heapq.heappop(queue)
        selected.add(candidate)
        segment_end[left] = candidate
        segment_end[candidate] = right
        for piece_left, piece_right in ((left, candidate), (candidate, right)):
            worst, next_candidate = worst_inside(piece_left, piece_right)
            if next_candidate >= 0:
                heapq.heappush(
                    queue, (-worst, piece_left, piece_right, next_candidate)
                )
    return selected


def approximate_motion_points(
    samples: Sequence[tuple[float, float]],
    tolerance_deg: Any = 0.1,
    maximum_points: Any = 50,
    interpolation_order: Any = 1,
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Approximate samples, then validate them with the requested final curve."""
    if len(samples) < 2:
        raise ValueError('포인트 변환에는 일반 모션점이 두 개 이상 필요합니다')
    tolerance = _finite(tolerance_deg, '근사 허용 오차')
    if tolerance <= 0.0:
        raise ValueError('근사 허용 오차는 0보다 커야 합니다')
    try:
        curve_order = int(interpolation_order)
    except (TypeError, ValueError) as exc:
        raise ValueError('근사 곡선은 1차, 3차, 5차 중 하나여야 합니다') from exc
    if curve_order not in {1, 3, 5}:
        raise ValueError('근사 곡선은 1차, 3차, 5차 중 하나여야 합니다')
    try:
        point_limit = int(maximum_points)
    except (TypeError, ValueError) as exc:
        raise ValueError('최대 포인트 수가 올바르지 않습니다') from exc
    point_limit = max(3, min(MAX_APPROXIMATION_POINTS, point_limit))
    ordered = sorted(
        (round(_time(time_sec), 9), float(value))
        for time_sec, value in samples
    )
    context = _FitContext(
        ordered=ordered,
        tolerance=tolerance,
        curve_order=curve_order,
        point_limit=point_limit,
        prune_attempts=[max(400, 6_000_000 // max(1, len(ordered)))],
    )
    selected_indices = _select_by_chords(context)
    interpolation_error = context.chord_error
    initial_point_count = len(selected_indices)

    # 문맥이 들고 있는 것을 그대로 쓴다 · 같은 절차를 두 곳에 적지 않는다 · §6-128
    curve_points = context.points_at
    final_curve_errors_for = context.errors_of

    def final_curve_errors(indices: Sequence[int]) -> List[float]:
        _normalized, rendered = render_point_curve(
            curve_points(indices), curve_order
        )
        rendered_by_time = {
            round(time_sec, 9): float(value) for time_sec, value in rendered
        }
        return [
            abs(value - rendered_by_time[round(time_sec, 9)])
            for time_sec, value in ordered
        ]

    def worst_per_gap(errors: Sequence[float]) -> List[int]:
        """아직 오차가 큰 구간마다 가장 어긋난 표본 하나씩 · §6-111

        구간 하나를 채울 때마다 곡선을 통째로 다시 그리면, 5분짜리 녹화에서
        포인트 2,000개를 넣는 데 54초가 걸렸다 · 나쁜 구간을 **한꺼번에** 채우면
        다시 그리는 횟수가 열 번 남짓으로 줄어든다.

        고르는 자리는 그대로다 · 실제로 사용자가 편집할 곡선의 오차가 가장 큰
        표본이다 · 다만 구간마다 하나씩 동시에 고른다.
        """
        picked: List[int] = []
        indices = sorted(selected_indices)
        for left, right in zip(indices, indices[1:]):
            candidate = -1
            worst = tolerance
            for index in range(left + 1, right):
                if errors[index] > worst:
                    candidate = index
                    worst = errors[index]
            if candidate >= 0:
                picked.append(candidate)
        return picked

    # 1차 통과는 직선 추적으로 후보를 고른다 · 2차 통과는 **사용자가 실제로
    # 편집할 곡선**을 그려 보고 아직 어긋난 자리를 채운다.
    errors = final_curve_errors(sorted(selected_indices))
    room = min(point_limit, len(ordered))
    while max(errors, default=0.0) > tolerance and len(selected_indices) < room:
        candidates = worst_per_gap(errors)
        if not candidates:
            break
        for candidate in candidates:
            if len(selected_indices) >= room:
                break
            selected_indices.add(candidate)
        errors = final_curve_errors(sorted(selected_indices))

    sample_step = {
        round(time_sec, 9): step for step, (time_sec, _value) in enumerate(ordered)
    }

    #: 포인트를 빼면 곡선이 달라지는 범위 · 기울기는 바로 옆 포인트만 보므로
    #: ±2 면 충분하다 · 5차는 가속도까지 보므로 한 칸 더 넉넉히 잡는다.
    AFFECTED_NEIGHBOURS = 3

    #: 솎아내기에 쓸 시도 횟수 · §6-113
    #:
    #: 한 번 시도할 때마다 조각을 다시 그린다 · 표본이 많을수록 조각도 크므로,
    #: **표본 수에 반비례**하게 잡아 어떤 길이든 비슷한 시간에 끝나게 한다 ·
    #: 18초짜리는 예산이 남아 완전히 최소까지 가고, 5분짜리는 예산 안에서
    #: 최대한 줄인다 · 예산이 다해도 결과는 언제나 허용 오차 안이다.
    prune_attempts = context.prune_attempts

    #: 조각만 그릴 때 양옆에 더 붙이는 여백 · 조각의 끝점은 기울기가 0 이 되어
    #: 값이 달라진다 · 여백을 두면 그 영향이 정작 볼 구간까지 닿지 않는다.
    SLICE_MARGIN = 4

    def changed_slots(
        trial_length: int, dropped_slots: Sequence[int]
    ) -> List[tuple[int, int]]:
        """뺀 자리 주변에서 곡선이 달라지는 포인트 범위 · 겹치면 합친다."""
        spans = sorted(
            (
                max(0, slot - AFFECTED_NEIGHBOURS),
                min(trial_length - 1, slot + AFFECTED_NEIGHBOURS - 1),
            )
            for slot in dropped_slots
        )
        merged: List[tuple[int, int]] = []
        for span in spans:
            if merged and span[0] <= merged[-1][1] + 1:
                merged[-1] = (merged[-1][0], max(merged[-1][1], span[1]))
            else:
                merged.append(span)
        return merged

    def within_tolerance(
        trial: Sequence[int], dropped_slots: Optional[Sequence[int]] = None
    ) -> bool:
        """이 포인트 집합이 허용 오차 안인가 · §6-113

        `dropped_slots` 를 주면 **바뀐 자리만** 본다 · 빼기 전이 이미 허용 오차
        안이었으므로 곡선이 그대로인 자리는 다시 볼 이유가 없다.

        바뀐 자리도 **조각만** 그린다 · 전체 포인트로 기울기를 구하면 포인트가
        많을수록 한 번 시험할 때마다 그만큼 든다 · 조각 양옆에 여백을 붙이면
        전체로 그린 것과 값이 같다(끝점 기울기의 영향이 안 닿는다).
        """
        if dropped_slots is None:
            return max(final_curve_errors(trial), default=0.0) <= tolerance
        last = len(trial) - 1
        for low, high in changed_slots(len(trial), dropped_slots):
            slice_low = max(0, low - SLICE_MARGIN)
            slice_high = min(last, high + SLICE_MARGIN)
            piece = curve_points(trial[slice_low:slice_high + 1])
            if len(piece) < 2:
                return max(final_curve_errors(trial), default=0.0) <= tolerance
            _normalized, rendered = render_point_curve(piece, curve_order)
            window_start = ordered[trial[low]][0] - EPSILON
            window_end = ordered[trial[high]][0] + EPSILON
            for time_sec, value in rendered:
                if time_sec < window_start or time_sec > window_end:
                    continue
                step = sample_step.get(round(time_sec, 9))
                if step is None:
                    continue
                if abs(ordered[step][1] - value) > tolerance:
                    return False
        return True

    def drop_group(kept: List[int], group: Sequence[int]) -> Optional[List[int]]:
        """이 묶음을 빼 본다 · 안 되면 반으로 나눠 각각 다시 · 되는 것만 뺀다.

        하나씩 빼 보고 곡선을 다시 그리면 5분짜리에서 수십 초가 걸린다 ·
        한 번에 빼 보고 실패할 때만 쪼개면 다시 그리는 횟수가 확 준다 ·
        실패한 묶음을 버리지 않고 **양쪽 다** 다시 보는 것이 핵심이다 ·
        한쪽만 보면 뺄 수 있는 것을 놓친다.
        """
        if prune_attempts[0] <= 0:
            return None
        prune_attempts[0] -= 1
        dropping = set(group)
        trial: List[int] = []
        dropped_slots: List[int] = []
        for index in kept:
            if index in dropping:
                dropped_slots.append(len(trial))
                continue
            trial.append(index)
        if within_tolerance(trial, dropped_slots):
            return trial
        if len(group) <= 1:
            return None
        middle = len(group) // 2
        first = drop_group(kept, group[:middle])
        base = kept if first is None else first
        second = drop_group(base, group[middle:])
        if second is not None:
            return second
        return first

    def prune_redundant(indices: List[int]) -> List[int]:
        """허용 오차를 지키면서 뺄 수 있는 포인트는 뺀다 · §6-113

        앞에서부터 욕심내어 넣기 때문에, 나중에 넣은 포인트가 앞서 넣은 것을
        필요 없게 만든다 · 그대로 두면 「정밀도 안에서 최소」가 아니다.

        뺐을 때 이웃 사이에서 가장 덜 벗어나는 것부터, 서로 붙어 있지 않은
        것끼리 묶어 시도한다 · 넘으면 되돌리므로 결과는 **언제나 허용 오차
        안**이다.
        """
        kept = list(indices)
        while len(kept) > 2:
            ranked = sorted(
                (
                    interpolation_error(kept[slot], kept[slot - 1], kept[slot + 1]),
                    kept[slot],
                )
                for slot in range(1, len(kept) - 1)
            )
            group: List[int] = []
            blocked: set = set()
            for _cost, index in ranked:
                slot = kept.index(index)
                if slot in blocked:
                    continue
                group.append(index)
                blocked.update((slot - 1, slot, slot + 1))
            reduced = drop_group(kept, group)
            if reduced is not None and len(reduced) < len(kept):
                kept = reduced
                continue
            # 묶음으로는 더 못 뺀다 · 이웃끼리 겹쳐 빠진 후보가 남아 있으므로
            # 마지막에 하나씩 훑는다 · 여기서 아무것도 안 빠져야 「최소」다.
            removed_alone = False
            slot = 1
            while slot < len(kept) - 1 and prune_attempts[0] > 0:
                prune_attempts[0] -= 1
                trial = kept[:slot] + kept[slot + 1:]
                if within_tolerance(trial, [slot]):
                    kept = trial
                    removed_alone = True
                else:
                    slot += 1
            if not removed_alone or prune_attempts[0] <= 0:
                break
        return kept

    def free_points(
        times: Sequence[float], values: Sequence[float], stable_ids: bool = False
    ) -> List[Dict[str, Any]]:
        tangent_mode = 'linear' if curve_order == 1 else 'auto'
        return [
            {
                'point_id': (
                    f'point_{uuid.uuid4().hex[:8]}' if stable_ids else f'free_{slot}'
                ),
                'time_sec': float(time_sec),
                'value_deg': float(value),
                'tangent_mode': tangent_mode,
            }
            for slot, (time_sec, value) in enumerate(zip(times, values))
        ]

    def free_curve(
        times: Sequence[float], values: Sequence[float],
        low: int, high: int,
    ) -> Dict[float, float]:
        """조각만 그린다 · 양옆 여백을 붙여 전체로 그린 것과 값이 같게 한다."""
        slice_low = max(0, low - SLICE_MARGIN)
        slice_high = min(len(times) - 1, high + SLICE_MARGIN)
        piece = free_points(
            times[slice_low:slice_high + 1], values[slice_low:slice_high + 1]
        )
        if len(piece) < 2:
            piece = free_points(times, values)
        _normalized, rendered = render_point_curve(piece, curve_order)
        return {round(float(t), 9): float(v) for t, v in rendered}

    def solve_normal_equations(
        matrix: List[List[float]], vector: List[float]
    ) -> Optional[List[float]]:
        """작은 연립방정식 하나 · 자유 포인트가 여덟 남짓이라 이걸로 충분하다."""
        size = len(vector)
        rows = [list(matrix[index]) + [vector[index]] for index in range(size)]
        for column in range(size):
            pivot = max(range(column, size), key=lambda row: abs(rows[row][column]))
            if abs(rows[pivot][column]) < 1e-12:
                return None
            rows[column], rows[pivot] = rows[pivot], rows[column]
            head = rows[column][column]
            for row in range(column + 1, size):
                factor = rows[row][column] / head
                if factor:
                    for cell in range(column, size + 1):
                        rows[row][cell] -= factor * rows[column][cell]
        answer = [0.0] * size
        for row in range(size - 1, -1, -1):
            total = rows[row][size] - sum(
                rows[row][cell] * answer[cell] for cell in range(row + 1, size)
            )
            answer[row] = total / rows[row][row]
        return answer

    def fit_free_values(
        times: Sequence[float], values: Sequence[float], free: Sequence[int],
    ) -> tuple[Optional[List[float]], float]:
        """자유 포인트의 값을 움직여 그 언저리의 **최대 오차**를 줄인다 · §6-117

        포인트를 녹화 표본 값에 묶어 두면, 곡선이 그 점을 반드시 지나야 해서
        주변에 포인트가 더 필요하다 · 허용 오차 안에서 값을 조금 옮길 수 있게
        하면 같은 모양을 더 적은 포인트로 낸다 · 실측 14~19% 감소.

        곡선은 포인트 값에 대해 **선형**이다(1·3·5차 모두) · 그래서 값 하나씩
        1 만큼 밀어 본 결과를 모으면 그것이 그대로 계수표가 된다 · 최소제곱으로
        풀고, 큰 오차에 무게를 더 실어 몇 번 되풀면 최대 오차 기준에 가까워진다.
        """
        low = max(0, min(free) - AFFECTED_NEIGHBOURS)
        high = min(len(times) - 1, max(free) + AFFECTED_NEIGHBOURS)
        base = list(values)
        zero = free_curve(times, base, low, high)
        # 여백 구간은 세지 않는다 · 조각의 끝점은 기울기가 0 이 되어 값이
        # 다르다 · 여백을 오차로 세면 멀쩡한 후보가 죄다 퇴짜를 맞는다.
        window_start = times[low] - EPSILON
        window_end = times[high] + EPSILON
        window = [
            step for step in range(len(ordered))
            if round(ordered[step][0], 9) in zero
            and window_start <= ordered[step][0] <= window_end
        ]
        if not window:
            return None, float('inf')
        residual = [
            ordered[step][1] - zero[round(ordered[step][0], 9)] for step in window
        ]
        columns: List[List[float]] = []
        for slot in free:
            bumped = list(base)
            bumped[slot] += 1.0
            moved = free_curve(times, bumped, low, high)
            columns.append([
                moved[round(ordered[step][0], 9)] - zero[round(ordered[step][0], 9)]
                for step in window
            ])
        weights = [1.0] * len(window)
        best: Optional[List[float]] = None
        best_error = float('inf')
        for _sweep in range(FREE_VALUE_SWEEPS):
            size = len(free)
            normal = [
                [
                    sum(
                        weights[k] * columns[i][k] * columns[j][k]
                        for k in range(len(window))
                    )
                    for j in range(size)
                ]
                for i in range(size)
            ]
            for index in range(size):
                normal[index][index] += 1e-9
            right = [
                sum(weights[k] * columns[i][k] * residual[k] for k in range(len(window)))
                for i in range(size)
            ]
            delta = solve_normal_equations(normal, right)
            if delta is None:
                break
            trial = list(base)
            for index, slot in enumerate(free):
                trial[slot] += delta[index]
            got = free_curve(times, trial, low, high)
            errors_here = [
                abs(ordered[step][1] - got[round(ordered[step][0], 9)])
                for step in window
                if round(ordered[step][0], 9) in got
            ]
            if not errors_here:
                break
            worst = max(errors_here)
            if worst < best_error:
                best, best_error = trial, worst
            peak = worst or 1.0
            weights = [
                weight * (0.2 + 0.8 * (error / peak) ** 2)
                for weight, error in zip(weights, errors_here)
            ]
        return best, best_error

    def free_value_pass(
        indices: Sequence[int]
    ) -> tuple[List[float], List[float]]:
        """값을 허용 오차 안에서 풀어 주고 포인트를 더 뺀다 · §6-117"""
        times = [ordered[index][0] for index in indices]
        values = [ordered[index][1] for index in indices]
        while len(times) > 3 and prune_attempts[0] > 0:
            ranked = sorted(
                range(1, len(times) - 1),
                key=lambda slot: abs(
                    values[slot] - (
                        values[slot - 1]
                        + (values[slot + 1] - values[slot - 1])
                        * ((times[slot] - times[slot - 1])
                           / max(times[slot + 1] - times[slot - 1], EPSILON))
                    )
                ),
            )
            progressed = False
            for slot in ranked[:FREE_VALUE_TRIES]:
                if prune_attempts[0] <= 0:
                    break
                prune_attempts[0] -= 1
                trial_times = times[:slot] + times[slot + 1:]
                trial_values = values[:slot] + values[slot + 1:]
                free = list(range(
                    max(1, slot - FREE_VALUE_WINDOW),
                    min(len(trial_times) - 1, slot + FREE_VALUE_WINDOW),
                ))
                # 창이 너무 벌어지면 이웃을 줄인다 · 끝물에 포인트가 성길수록
                # 같은 이웃 수가 훨씬 긴 시간을 덮는다
                while len(free) > 2 and (
                    trial_times[min(len(trial_times) - 1, max(free) + AFFECTED_NEIGHBOURS)]
                    - trial_times[max(0, min(free) - AFFECTED_NEIGHBOURS)]
                ) > FREE_VALUE_MAX_SAMPLES * DEFAULT_PERIOD_SEC:
                    if max(free) - slot >= slot - min(free):
                        free.pop()
                    else:
                        free.pop(0)
                if not free:
                    continue
                tuned, worst = fit_free_values(trial_times, trial_values, free)
                if tuned is None or worst > tolerance:
                    continue
                times, values = trial_times, tuned
                progressed = True
                break
            if not progressed:
                break
        return times, values

    indices = sorted(selected_indices)
    free_times: Optional[List[float]] = None
    free_values: Optional[List[float]] = None
    if max(errors, default=0.0) <= tolerance:
        # 허용 오차를 못 맞춘 채 상한에 걸린 경우는 뺄 여유가 없다
        indices = prune_redundant(indices)
        free_times, free_values = free_value_pass(indices)
        if len(free_times) >= len(indices):
            free_times = free_values = None
    if free_times is not None and free_values is not None:
        points = free_points(free_times, free_values, stable_ids=True)
        errors = final_curve_errors_for(points)
        if max(errors, default=0.0) > tolerance:
            # 값을 푼 결과가 허용 오차를 넘으면 쓰지 않는다 · 전체로 다시 확인한다
            points = curve_points(indices, stable_ids=True)
            errors = final_curve_errors(indices)
    else:
        points = curve_points(indices, stable_ids=True)
        errors = final_curve_errors(indices)
    return points, {
        'operation': 'create_axis_point_curve',
        'interpolation_order': curve_order,
        'initial_point_count': initial_point_count,
        'point_count': len(points),
        'source_sample_count': len(ordered),
        'tolerance_deg': tolerance,
        'maximum_error_deg': max(errors, default=0.0),
        'average_error_deg': (
            sum(errors) / len(errors) if errors else 0.0
        ),
        'point_limit_reached': (
            len(points) >= point_limit and max(errors, default=0.0) > tolerance
        ),
    }


def _unique_point_ids(points: Sequence[Any]) -> List[Dict[str, Any]]:
    """한 곡선 안에서 포인트 id 는 겹치면 안 된다 · §6-280

    곡선 둘을 하나로 합치면 각자 쓰던 id 가 한자리에 모인다 · 서로 다른
    곡선에서 만들어진 id 는 겹칠 수 있다 · 겹치는 것만 새로 준다.
    """
    seen: set[str] = set()
    result: List[Dict[str, Any]] = []
    for point in points:
        item = dict(point)
        point_id = str(item.get('point_id') or '')
        if not point_id or point_id in seen:
            point_id = f'point_{uuid.uuid4().hex[:8]}'
        seen.add(point_id)
        item['point_id'] = point_id
        result.append(item)
    return result


def _near(time_sec: float, start_sec: float, end_sec: float) -> bool:
    """붙인 구간이거나 그 양옆 한 칸인가 · §6-280

    곡선의 포인트는 서로 20ms 이상 떨어져야 한다 · 붙인 포인트 바로 옆에 옛
    포인트가 남으면 그 규칙을 어겨 곡선을 그릴 수 없다 · 한 칸 여유를 두고
    비운다.
    """
    return (
        start_sec - DEFAULT_PERIOD_SEC + EPSILON
        <= time_sec
        <= end_sec + DEFAULT_PERIOD_SEC - EPSILON
    )


def _remove_curve(
    working: Dict[str, Any],
    tracks: Dict[str, List[tuple[float, float]]],
    curve: Mapping[str, Any],
) -> None:
    """곡선 하나를 그 구간의 프레임까지 함께 없앤다 · §6-279

    `_rewrite_curve` 의 앞쪽 절반과 같은 일을 한다 · 새 포인트로 갈아 끼우는
    대신 아무것도 넣지 않는다.
    """
    curve_id = str(curve.get('curve_id') or '')
    motion_id = str(curve.get('motion_id') or '')
    start_sec, end_sec = point_curve_bounds(curve)
    tracks[motion_id] = [
        point for point in tracks.get(motion_id, [])
        if not _inside(point[0], start_sec, end_sec)
    ]
    working['point_curves'] = [
        item for item in working.get('point_curves') or []
        if str(item.get('curve_id') or '') != curve_id
    ]


def _rewrite_curve(
    working: Dict[str, Any],
    tracks: Dict[str, List[tuple[float, float]]],
    curve_id: str,
    motion_id: str,
    interpolation_order: int,
    points: Sequence[Any],
) -> None:
    """곡선 하나를 새 포인트로 갈아 끼우고 20ms 프레임을 맞춘다 · §6-122

    `point_curve` 편집이 하던 일을 그대로 떼어낸 것이다 · 여러 축을 한 번에
    고치는 편집이 같은 일을 축마다 되풀이해야 해서, 두 곳에 같은 절차를 적지
    않으려고 함수로 뺐다.
    """
    normalized_points, rendered = render_point_curve(points, interpolation_order)
    start_sec, end_sec = rendered[0][0], rendered[-1][0]
    previous_curve = next((
        curve for curve in working.get('point_curves') or []
        if str(curve.get('curve_id') or '') == curve_id
    ), None)
    if previous_curve is not None:
        previous_motion_id = str(previous_curve['motion_id'])
        previous_start, previous_end = point_curve_bounds(previous_curve)
        tracks[previous_motion_id] = [
            point for point in tracks.get(previous_motion_id, [])
            if not _inside(point[0], previous_start, previous_end)
        ]
    existing = tracks.get(motion_id, [])
    tracks[motion_id] = [
        point for point in existing if not _inside(point[0], start_sec, end_sec)
    ] + rendered
    working['point_curves'] = [
        curve for curve in working.get('point_curves') or []
        if str(curve.get('curve_id') or '') != curve_id
    ] + [{
        'curve_id': curve_id,
        'motion_id': motion_id,
        'interpolation_order': interpolation_order,
        'points': normalized_points,
    }]


def one_curve_per_axis(layer: Dict[str, Any]) -> Dict[str, Any]:
    """한 축의 그래프는 **하나다** · §6-281

    포인트 곡선은 한 축에 여러 개 있을 수 있게 만들어져 있었다(겹치지만
    않으면 된다) · 그러다 보니 편집할 때마다 조각이 늘었다 ·

        구간 복사   붙인 자리에 곡선이 없으면 새로 만들었다
        합치기      원본 레이어의 곡선을 그대로 가져왔다
        빈 구간     그 자리를 메우는 곡선을 따로 만들었다

    조각이 나면 사람 눈에 드러난다 · 파란 배경 사이에 흰 줄이 생기고, 구간
    지우기가 조각마다 따로 놀고, 한 조각만 고쳐도 옆이 안 따라온다.

    그래서 **편집이 끝나면 축마다 곡선 하나로 모은다** · 조각 사이는 이어지며
    메워진다 · 보간 차수는 포인트가 가장 많은 조각의 것을 따른다.
    """
    curves = list(layer.get('point_curves') or [])
    by_axis: Dict[str, List[Dict[str, Any]]] = {}
    for curve in curves:
        by_axis.setdefault(str(curve.get('motion_id') or ''), []).append(curve)
    if all(len(pieces) < 2 for pieces in by_axis.values()):
        return layer
    working = normalize_layer(copy.deepcopy(layer))
    tracks = _tracks(working)
    for motion_id, pieces in by_axis.items():
        if len(pieces) < 2:
            continue
        pieces = sorted(pieces, key=lambda item: point_curve_bounds(item)[0])
        order = point_curve_order(
            max(pieces, key=lambda item: len(item.get('points') or []))
        )
        keep_id = str(pieces[0].get('curve_id') or '')
        points = _unique_point_ids(sorted(
            (point for piece in pieces for point in piece.get('points') or []),
            key=lambda item: _finite(item.get('time_sec'), '포인트 시간'),
        ))
        for piece in pieces[1:]:
            _remove_curve(working, tracks, piece)
        _rewrite_curve(working, tracks, keep_id, motion_id, order, points)
    working['frames'] = _frames(tracks)
    return normalize_layer(working)


def edit_layer(layer: Dict[str, Any], request: Dict[str, Any]) -> Dict[str, Any]:
    """Apply one operation to a temporary layer and return a new layer.

    **어떤 편집이든 끝나면 축마다 곡선 하나다** · §6-281
    """
    return one_curve_per_axis(_edit_layer_once(layer, request))


def _edit_layer_once(layer: Dict[str, Any], request: Dict[str, Any]) -> Dict[str, Any]:
    working = normalize_layer(copy.deepcopy(layer))
    if working.get('locked'):
        raise ValueError('잠긴 레이어는 편집할 수 없습니다')
    operation = str(request.get('operation') or '').strip()
    tracks = _tracks(working)

    if operation == 'resolve_point_curve_consistency':
        strategy = str(request.get('strategy') or '')
        if strategy != 'points':
            raise ValueError('포인트 기준 재계산만 지원합니다')
        selected_curve_ids = {
            str(value) for value in request.get('curve_ids') or [] if str(value)
        }
        curves = [
            curve for curve in working.get('point_curves') or []
            if not selected_curve_ids or str(curve.get('curve_id') or '') in selected_curve_ids
        ]
        if not curves:
            raise ValueError('정리할 포인트 곡선을 찾을 수 없습니다')
        normalized_by_id = {}
        for curve in curves:
            motion_id = str(curve.get('motion_id') or '')
            normalized_points, rendered = render_point_curve(
                curve.get('points') or [], point_curve_order(curve)
            )
            start_sec, end_sec = rendered[0][0], rendered[-1][0]
            tracks[motion_id] = [
                point for point in tracks.get(motion_id, [])
                if not _inside(point[0], start_sec, end_sec)
            ] + rendered
            normalized_by_id[str(curve.get('curve_id') or '')] = normalized_points
        for curve in working.get('point_curves') or []:
            curve_id = str(curve.get('curve_id') or '')
            if curve_id in normalized_by_id:
                curve['points'] = normalized_by_id[curve_id]
        working['frames'] = _frames(tracks)
        working['edit_revision'] = int(working.get('edit_revision') or 0) + 1
        return normalize_layer(working)

    if apply_axis_operation(operation, working, tracks, request):
        working['frames'] = _frames(tracks)
        if len(working['frames']) > MAX_EDIT_FRAMES:
            raise ValueError(f'편집 결과가 최대 {MAX_EDIT_FRAMES:,}프레임을 초과합니다')
        working['edit_revision'] = int(working.get('edit_revision') or 0) + 1
        return normalize_layer(working)

    if operation not in {
        'point_curve',
        'create_axis_point_curve',
        'copy_point_range',
        'delete_point_range',
        'time_shift',
        'time_scale',
        'value_offset',
        'value_scale',
    }:
        raise ValueError('지원하지 않는 레이어 편집 기능입니다')

    if operation in {'copy_point_range', 'delete_point_range'}:
        # 고른 축 **전부**의 그 시간대 포인트를 한 번에 다룬다 · §6-122
        #
        # 전에는 구간 복사·삭제가 곡선 하나만 봤다 · 시작·종료 포인트가 같은
        # 축·같은 곡선이어야 했고, 축 셋을 골라도 한 축만 바뀌었다 · 시간
        # 이동·배율은 이미 고른 축 전부를 처리하고 있었는데 여기만 달랐다.
        #
        # 그 시간대에 포인트가 없는 축은 **조용히 건너뛴다** · 오류로 막으면
        # 나머지 축까지 못 바꾼다.
        selected = unique_motion_ids(request.get('motion_ids') or [])
        if not selected:
            raise ValueError('편집할 Motion ID를 선택하세요')
        start_sec = _finite(request.get('start_sec'), '구간 시작')
        end_sec = _finite(request.get('end_sec'), '구간 끝')
        if end_sec < start_sec:
            start_sec, end_sec = end_sec, start_sec
        copying = operation == 'copy_point_range'
        target_start = (
            _finite(request.get('target_start_sec'), '붙일 시간') if copying else 0.0
        )
        if copying and target_start < 0.0:
            raise ValueError('붙일 시간은 0초 이상이어야 합니다')
        changed: List[str] = []
        if copying:
            # 붙인 포인트는 **붙인 자리의 곡선**에 들어간다 · §6-256
            #
            # 전에는 포인트를 떠 온 곡선에 그대로 도로 넣었다 · 한 축에 곡선이
            # 하나뿐이면 같은 말이지만, 여럿이면 떠 온 곡선이 붙인 자리까지
            # 늘어나 **옆 곡선을 통째로 덮었다** · 덮인 곡선은 그대로 남고
            # 프레임만 사라져서, 미리보기·반영은 멀쩡하다가 저장할 때
            # 「포인트 곡선과 20ms 프레임이 다릅니다」로 터졌다.
            for motion_id in selected:
                curves = [
                    curve for curve in working.get('point_curves') or []
                    if str(curve.get('motion_id') or '') == motion_id
                ]
                taken = sorted(
                    ((curve, point) for curve in curves
                     for point in curve.get('points') or []
                     if _inside(_finite(point.get('time_sec'), '포인트 시간'),
                                start_sec, end_sec)),
                    key=lambda item: _finite(item[1].get('time_sec'), '포인트 시간'),
                )
                if len(taken) < 2:
                    continue
                inside = [point for _curve, point in taken]
                offset = round(
                    target_start - _finite(inside[0].get('time_sec'), '포인트 시간'), 9
                )
                moved = []
                for point in inside:
                    copy_point = copy.deepcopy(point)
                    copy_point['point_id'] = f'point_{uuid.uuid4().hex[:8]}'
                    copy_point['time_sec'] = round(
                        _finite(point.get('time_sec'), '포인트 시간') + offset, 9
                    )
                    moved.append(copy_point)
                paste_start = _finite(moved[0].get('time_sec'), '포인트 시간')
                paste_end = _finite(moved[-1].get('time_sec'), '포인트 시간')
                # 붙이고 나면 **그 축은 곡선 하나**다 · §6-280
                #
                # 전에는 붙일 자리에 곡선이 없으면 새로 만들었다 · 그래서 끝에
                # 이어 붙일 때마다 곡선이 하나씩 늘었고, 기존 데이터 끝과 붙인
                # 자리 사이는 **곡선도 프레임도 없는 빈 구간**으로 남았다 ·
                # 화면에서는 파란 배경 사이의 흰 줄로 보였고, 그 조각난 곡선
                # 때문에 구간 지우기까지 어긋났다.
                #
                # 나눌 이유가 없다 · 한 축의 곡선을 전부 모아 하나로 쓴다 ·
                # 빈 구간은 이어지면서 자연히 메워진다.
                destination_id = str(taken[0][0].get('curve_id') or '')
                order = point_curve_order(taken[0][0])
                kept = [
                    point
                    for curve in curves
                    for point in curve.get('points') or []
                    if not _near(
                        _finite(point.get('time_sec'), '포인트 시간'),
                        paste_start, paste_end,
                    )
                ]
                next_points = _unique_point_ids(sorted(
                    kept + moved,
                    key=lambda item: _finite(item.get('time_sec'), '포인트 시간'),
                ))
                for curve in curves:
                    if str(curve.get('curve_id') or '') != destination_id:
                        _remove_curve(working, tracks, curve)
                _rewrite_curve(
                    working, tracks, destination_id, motion_id, order, next_points,
                )
                changed.append(motion_id)
        else:
            for curve in list(working.get('point_curves') or []):
                motion_id = str(curve.get('motion_id') or '')
                if motion_id not in selected:
                    continue
                points = list(curve.get('points') or [])
                inside = [
                    point for point in points
                    if _inside(_finite(point.get('time_sec'), '포인트 시간'),
                               start_sec, end_sec)
                ]
                if not inside:
                    continue
                next_points = [
                    point for point in points
                    if not _inside(_finite(point.get('time_sec'), '포인트 시간'),
                                   start_sec, end_sec)
                ]
                if len(next_points) < 2:
                    # 남는 게 한 점 이하면 **곡선째 지운다** · §6-279
                    #
                    # 전에는 그냥 건너뛰었다 · 곡선은 포인트가 둘 이상이어야
                    # 하니 고칠 수 없다는 뜻이었는데, 결과가 "그 포인트들만
                    # 안 지워진다" 였다 · 합친 레이어에는 2~3점짜리 작은
                    # 곡선이 여럿 생겨서 구간을 지워도 그것들이 그대로 남았다 ·
                    # 사람 눈에는 "지워지지 않는 포인트" 로만 보인다.
                    #
                    # 지우라고 했으면 지운다 · 곡선과 그 구간의 프레임을 함께
                    # 없앤다.
                    _remove_curve(working, tracks, curve)
                    changed.append(motion_id)
                    continue
                _rewrite_curve(
                    working, tracks, str(curve.get('curve_id') or ''), motion_id,
                    point_curve_order(curve), next_points,
                )
                changed.append(motion_id)
        if not changed:
            raise ValueError(
                '선택한 구간에서 다룰 포인트가 없습니다 · 축과 구간을 다시 보세요'
            )
        working['frames'] = _frames(tracks)
        if len(working['frames']) > MAX_EDIT_FRAMES:
            raise ValueError(f'편집 결과가 최대 {MAX_EDIT_FRAMES:,}프레임을 초과합니다')
        working['edit_revision'] = int(working.get('edit_revision') or 0) + 1
        return normalize_layer(working)

    if operation == 'point_curve':
        selected = unique_motion_ids(request.get('motion_ids') or [])
        if len(selected) != 1:
            raise ValueError('포인트 곡선을 만들 Motion ID를 하나만 선택하세요')
        motion_id = selected[0]
        interpolation_order = point_curve_order(request)
        normalized_points, rendered = render_point_curve(
            request.get('points') or [], interpolation_order
        )
        start_sec, end_sec = rendered[0][0], rendered[-1][0]
        curve_id = str(request.get('curve_id') or f'curve_{uuid.uuid4().hex[:8]}')
        if _overlapping_curves(
            working, selected, start_sec, end_sec, excluding_curve_id=curve_id
        ):
            raise ValueError('같은 Motion ID의 포인트 곡선 구간이 서로 겹칩니다')
        _rewrite_curve(
            working, tracks, curve_id, motion_id, interpolation_order,
            request.get('points') or [],
        )
        working['frames'] = _frames(tracks)
        if len(working['frames']) > MAX_EDIT_FRAMES:
            raise ValueError(f'편집 결과가 최대 {MAX_EDIT_FRAMES:,}프레임을 초과합니다')
        working['edit_revision'] = int(working.get('edit_revision') or 0) + 1
        return normalize_layer(working)

    selected = _selected_ids(working, request.get('motion_ids') or [])
    if operation == 'create_axis_point_curve':
        if len(selected) != 1:
            raise ValueError('포인트를 생성할 Motion ID를 하나만 선택하세요')
        motion_id = selected[0]
        samples = list(tracks[motion_id])
        points, report = approximate_motion_points(
            samples,
            request.get('approximation_tolerance_deg', 0.1),
            request.get('approximation_maximum_points', 50),
            request.get('approximation_interpolation_order', 1),
        )
        if report['point_limit_reached']:
            raise ValueError(
                f"최대 {int(request.get('approximation_maximum_points') or 50)}개 "
                '포인트로 허용 오차를 만족하지 못했습니다'
            )
        interpolation_order = int(report['interpolation_order'])
        normalized_points, rendered = render_point_curve(
            points, interpolation_order
        )
        curve_id = str(
            request.get('curve_id') or f'curve_{uuid.uuid4().hex[:8]}'
        )
        working['point_curves'] = [
            curve for curve in working.get('point_curves') or []
            if str(curve.get('motion_id') or '') != motion_id
        ] + [{
            'curve_id': curve_id,
            'motion_id': motion_id,
            'interpolation_order': interpolation_order,
            'points': normalized_points,
        }]
        tracks[motion_id] = rendered
        validate_point_curve_overlaps(working['point_curves'])
        working['frames'] = _frames(tracks)
        working['edit_revision'] = int(working.get('edit_revision') or 0) + 1
        return normalize_layer(working)

    start_sec = _time(request.get('start_sec', 0.0))
    end_sec = _time(request.get('end_sec', start_sec))
    if end_sec < start_sec:
        raise ValueError('편집 종료 시간은 시작 시간보다 빠를 수 없습니다')
    if not any(
        _inside(time_sec, start_sec, end_sec)
        for motion_id in selected
        for time_sec, _value in tracks[motion_id]
    ):
        raise ValueError('선택한 축의 편집 구간에 모션 데이터가 없습니다')

    overlapping_curves = _overlapping_curves(
        working, selected, start_sec, end_sec
    )
    if not overlapping_curves:
        raise ValueError(
            '포인트가 없는 모션은 편집할 수 없습니다. '
            '선택 축 전체에 포인트를 먼저 생성하세요'
        )
    curves_with_selected_points = [
        curve for curve in overlapping_curves
        if any(
            _inside(float(point['time_sec']), start_sec, end_sec)
            for point in curve.get('points') or []
        )
    ]
    motions_with_selected_points = {
        str(curve.get('motion_id') or '')
        for curve in curves_with_selected_points
    }
    missing_range_points = [
        motion_id for motion_id in selected
        if motion_id not in motions_with_selected_points
    ]
    if missing_range_points:
        raise ValueError(
            '선택영역에 편집할 포인트가 없는 Motion ID: '
            + ', '.join(missing_range_points)
        )
    overlapping_curves = curves_with_selected_points
    point_times = {
        round(float(point['time_sec']), 9)
        for curve in overlapping_curves
        for point in curve.get('points') or []
    }
    if (
        round(start_sec, 9) not in point_times
        or round(end_sec, 9) not in point_times
    ):
        raise ValueError(
            '공통 시간영역은 선택된 축의 포인트 두 개로 지정하세요'
        )
    single_point_selection = abs(end_sec - start_sec) <= EPSILON
    time_scale_pivot = 0.0 if single_point_selection else start_sec
    value_scale_pivots: Dict[str, float] = {}
    if operation == 'value_scale':
        for motion_id in selected:
            if single_point_selection:
                value_scale_pivots[motion_id] = 0.0
                continue
            selected_points = [
                point for point in tracks[motion_id]
                if _inside(point[0], start_sec, end_sec)
            ]
            if selected_points:
                value_scale_pivots[motion_id] = selected_points[0][1]

    # Linked point curves are regenerated from transformed point metadata.
    # Remove their old samples first so moved/scaled curves cannot silently
    # overwrite unrelated data at the destination.
    for curve in overlapping_curves:
        curve_start, curve_end = point_curve_bounds(curve)
        motion_id = str(curve['motion_id'])
        tracks[motion_id] = [
            point for point in tracks[motion_id]
            if not _inside(point[0], curve_start, curve_end)
        ]

    if operation == 'time_shift':
        _finite(request.get('delta_sec'), '이동 시간')
    elif operation == 'value_offset':
        _finite(request.get('offset_deg'), '각도 오프셋')
    elif operation == 'value_scale':
        factor = _finite(request.get('factor'), '동작 배율')
        if factor == 0.0:
            raise ValueError('동작 배율은 0을 제외한 값이어야 합니다')
    elif operation == 'time_scale':
        factor = _finite(request.get('factor'), '시간 배율')
        if factor <= 0.0 or factor > MAX_TIME_SCALE:
            raise ValueError(f'시간 배율은 0보다 크고 {MAX_TIME_SCALE:g} 이하여야 합니다')
    if overlapping_curves:
        delta_sec = _finite(request.get('delta_sec', 0.0), '이동 시간')
        factor = _finite(request.get('factor', 1.0), '배율')
        offset_deg = _finite(request.get('offset_deg', 0.0), '각도 오프셋')
        transformed_by_id: Dict[str, tuple[Dict[str, Any], List[tuple[float, float]]]] = {}
        for curve in overlapping_curves:
            motion_id = str(curve['motion_id'])
            transformed_by_id[str(curve['curve_id'])] = transform_point_curve(
                curve,
                operation,
                start_sec=start_sec,
                end_sec=end_sec,
                delta_sec=delta_sec,
                factor=factor,
                time_pivot=time_scale_pivot,
                offset_deg=offset_deg,
                value_pivot=value_scale_pivots.get(motion_id, 0.0),
            )
        updated_curves = []
        for curve in working.get('point_curves') or []:
            transformed = transformed_by_id.get(str(curve.get('curve_id') or ''))
            updated_curves.append(transformed[0] if transformed else curve)
        validate_point_curve_overlaps(updated_curves)
        working['point_curves'] = updated_curves
        for transformed_curve, rendered in transformed_by_id.values():
            tracks.setdefault(str(transformed_curve['motion_id']), []).extend(rendered)
    working['frames'] = _frames(tracks)
    if len(working['frames']) > MAX_EDIT_FRAMES:
        raise ValueError(f'편집 결과가 최대 {MAX_EDIT_FRAMES:,}프레임을 초과합니다')
    working['edit_revision'] = int(working.get('edit_revision') or 0) + 1
    return normalize_layer(working)


def extend_point_curves_to_frames(layer: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """합친 레이어의 빈 구간에 **곡선을 따로 만든다** · §6-252

    합치기는 둘을 따로 만든다.

        frames = render_project(...)              전체 길이를 꽉 채운다
        point_curves = 원본에서 그대로 복사        원본이 덮던 구간만

    그래서 합친 뒤에는 곡선 밖에 프레임만 있는 구간이 남았다 · 그래프에는
    앞뒤로 평평한 선이 그어지는데 **집을 포인트가 없어** 그 구간은 편집할 수
    없었다 · 녹화만 한 레이어는 둘이 같은 구간이라 이런 일이 없어서,
    「어떨 땐 있고 없을 때도 있다」로 보였다.

    **원래 곡선에 포인트를 이어 붙이지 않는다.** 붙이면 이웃이 생겨 원래
    곡선의 접선이 달라지고, 그린 결과가 프레임과 어긋난다(실측 83개 표본이
    최대 3.65° 벗어났다) · 한 축에 곡선을 여럿 둘 수 있으므로(겹치지만
    않으면 된다) **빈 구간에 새 곡선을 만든다** · 원래 곡선은 손대지 않는다.

    채운 뒤 **스스로 검사**한다 · 그린 결과가 프레임과 다르면 그 구간은
    포기한다(맞추지 못했다고 망가뜨리지는 않는다).
    """
    normalized = normalize_layer(copy.deepcopy(dict(layer)))
    frames_by_id = _tracks(normalized)
    curves = copy.deepcopy(list(layer.get('point_curves') or []))
    if not curves or not frames_by_id:
        return curves

    covered: Dict[str, List[tuple[float, float]]] = {}
    for curve in curves:
        points = list(curve.get('points') or [])
        if len(points) < 2:
            continue
        covered.setdefault(str(curve.get('motion_id') or ''), []).append((
            round(float(points[0].get('time_sec')), 9),
            round(float(points[-1].get('time_sec')), 9),
        ))

    filled: List[Dict[str, Any]] = []
    for motion_id, samples in frames_by_id.items():
        spans = sorted(covered.get(motion_id) or [])
        if not spans or len(samples) < 2:
            continue
        for gap in _uncovered_gaps(samples, spans):
            piece = _curve_from_samples(motion_id, gap)
            if piece is None:
                continue
            probe = {'frames': list(layer.get('frames') or []), 'point_curves': [piece]}
            if not point_curve_frame_mismatches(probe):
                filled.append(piece)

    if not filled:
        return curves
    merged = curves + filled
    try:
        validate_point_curve_overlaps(merged)
    except ValueError:
        return curves
    return merged


def _uncovered_gaps(
    samples: Sequence[tuple[float, float]],
    spans: Sequence[tuple[float, float]],
) -> List[List[tuple[float, float]]]:
    """곡선이 덮지 않은 프레임 묶음들 · 곡선 사이 20ms 는 비워 둔다."""
    gaps: List[List[tuple[float, float]]] = []
    current: List[tuple[float, float]] = []
    for time_sec, value in samples:
        inside = any(
            start - EPSILON <= time_sec <= end + EPSILON for start, end in spans
        )
        touching = any(
            abs(time_sec - start) < DEFAULT_PERIOD_SEC - EPSILON
            or abs(time_sec - end) < DEFAULT_PERIOD_SEC - EPSILON
            for start, end in spans
        )
        if inside or touching:
            if len(current) >= 2:
                gaps.append(current)
            current = []
            continue
        current.append((time_sec, value))
    if len(current) >= 2:
        gaps.append(current)
    return gaps


def _curve_from_samples(
    motion_id: str, samples: Sequence[tuple[float, float]]
) -> Optional[Dict[str, Any]]:
    """프레임 묶음을 곡선 하나로 · 값이 바뀌는 자리만 포인트로 남긴다."""
    if len(samples) < 2:
        return None
    chosen: List[tuple[float, float]] = []
    previous = None
    for index, (time_sec, value) in enumerate(samples):
        if index in (0, len(samples) - 1) or previous is None or abs(value - previous) > 1e-9:
            chosen.append((time_sec, value))
        previous = value
    guarded: List[tuple[float, float]] = []
    for time_sec, value in chosen:
        if guarded and time_sec - guarded[-1][0] < DEFAULT_PERIOD_SEC - EPSILON:
            continue
        guarded.append((time_sec, value))
    if len(guarded) < 2:
        return None
    return {
        'curve_id': f'curve_{uuid.uuid4().hex[:8]}',
        'motion_id': motion_id,
        'interpolation_order': 1,
        'points': [
            {
                'point_id': f'point_{uuid.uuid4().hex[:8]}',
                'time_sec': round(float(time_sec), 9),
                'value_deg': float(value),
                'tangent_mode': 'linear',
            }
            for time_sec, value in guarded
        ],
    }


def collect_merged_point_curves(
    layers: Iterable[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """Copy point curves from source layers without allowing ID collisions."""
    merged_point_curves = []
    used_curve_ids = set()
    used_point_ids = set()
    for layer in layers:
        for source_curve in layer.get('point_curves') or []:
            curve = copy.deepcopy(source_curve)
            curve_id = str(curve.get('curve_id') or '')
            if not curve_id or curve_id in used_curve_ids:
                curve_id = f'curve_{uuid.uuid4().hex[:8]}'
                while curve_id in used_curve_ids:
                    curve_id = f'curve_{uuid.uuid4().hex[:8]}'
            curve['curve_id'] = curve_id
            used_curve_ids.add(curve_id)
            for point in curve.get('points') or []:
                point_id = str(point.get('point_id') or '')
                if not point_id or point_id in used_point_ids:
                    point_id = f'point_{uuid.uuid4().hex[:8]}'
                    while point_id in used_point_ids:
                        point_id = f'point_{uuid.uuid4().hex[:8]}'
                point['point_id'] = point_id
                used_point_ids.add(point_id)
            merged_point_curves.append(curve)
    validate_point_curve_overlaps(merged_point_curves)
    return merged_point_curves


def frames_are_curve_derived(layer: Mapping[str, Any]) -> bool:
    """이 레이어의 프레임은 **곡선에서 다시 그릴 수 있는가** · §6-292

    그렇다면 프레임은 파생물이다 · 곡선만 있으면 언제든 같은 값이 나온다 ·
    화면이 「작업본 반영」을 보낼 때 프레임을 뺄 수 있다는 뜻이다 · 10분짜리
    레이어에서 3.7 MB 가 60 KB 로 준다.

    판정은 싸게 한다 · 곡선을 다시 그려 보지 않고 **구간만** 본다 ·
    `render_point_curve` 는 첫 포인트부터 마지막 포인트까지 20ms 격자를 빠짐없이
    채우므로, 그 축의 모든 프레임 시각이 어느 곡선 구간 안에 들면 덮인 것이다.

    녹화만 한 축(곡선 없음)이 하나라도 있으면 거짓이다 · 그 값은 곡선에서
    나오지 않으므로 프레임이 원본이다.
    """
    spans: Dict[str, List[tuple[float, float]]] = {}
    for curve in layer.get('point_curves') or []:
        points = list(curve.get('points') or [])
        if len(points) < 2:
            continue
        spans.setdefault(str(curve.get('motion_id') or ''), []).append(
            point_curve_bounds(curve)
        )
    if not spans:
        return False
    for frame in layer.get('frames') or []:
        if not isinstance(frame, Mapping):
            continue
        try:
            time_sec = _finite(frame.get('time_sec'), '프레임 시간')
        except ValueError:
            return False
        for motion_id in (frame.get('values') or {}):
            ranges = spans.get(str(motion_id))
            if not ranges:
                return False
            if not any(
                start - EPSILON <= time_sec <= end + EPSILON
                for start, end in ranges
            ):
                return False
    return True


def frames_from_point_curves(layer: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """곡선만 받아 **프레임을 다시 그린다** · §6-292

    화면이 프레임을 빼고 보냈을 때 서버가 이것으로 채운다 · 같은
    `render_point_curve` 로 그리므로 화면이 미리보기에서 본 값과 같다 ·
    그 프레임도 원래 서버가 만들어 보낸 것이다.
    """
    tracks: Dict[str, List[tuple[float, float]]] = {}
    for curve in layer.get('point_curves') or []:
        points = list(curve.get('points') or [])
        if len(points) < 2:
            continue
        _normalized, rendered = render_point_curve(
            points, point_curve_order(curve)
        )
        tracks.setdefault(str(curve.get('motion_id') or ''), []).extend(rendered)
    if not tracks:
        raise ValueError('포인트 곡선이 없어 프레임을 다시 그릴 수 없습니다')
    return _frames({
        motion_id: sorted(samples) for motion_id, samples in tracks.items()
    })


def layer_point_coverage_issues(layer: Mapping[str, Any]) -> List[str]:
    """포인트 곡선으로 덮이지 **않은** 축 · 편집기가 묻는 질문이다 · §6-90

    "이 축을 포인트로 편집할 수 있나" 를 판정한다 · 덮여 있지 않다면 먼저
    포인트를 만들어야 한다.

    **합치기의 판정이 아니다.** 한때 합치기가 이것을 썼고, 그래서 곡선이 없는
    녹화 레이어는 영영 합칠 수 없었다 · 스튜디오가 만들어 내는 것이 바로 그
    레이어인데도. 합치기·재생·내보내기가 지키는 불변식은 따로 있다 ·
    `point_curve_frame_mismatches` · "곡선이 **있으면** 프레임과 맞아야 한다".
    """
    normalized = normalize_layer(copy.deepcopy(dict(layer)))
    tracks = _tracks(normalized)
    if not tracks:
        return ['모션 데이터 없음']
    curve_times: Dict[str, set[float]] = {}
    for curve in normalized.get('point_curves') or []:
        motion_id = str(curve.get('motion_id') or '')
        _points, rendered = render_point_curve(
            curve.get('points') or [], point_curve_order(curve)
        )
        curve_times.setdefault(motion_id, set()).update(
            round(float(time_sec), 9) for time_sec, _value in rendered
        )
    return [
        motion_id
        for motion_id, samples in sorted(tracks.items())
        if {
            round(float(time_sec), 9) for time_sec, _value in samples
        } != curve_times.get(motion_id, set())
    ]


def _layer_time_bounds(layer: Mapping[str, Any]) -> tuple[float, float]:
    times = [
        _finite(frame.get('time_sec'), '프레임 시간')
        for frame in layer.get('frames') or []
        if isinstance(frame, Mapping)
    ]
    if not times:
        raise ValueError(
            f"레이어 합치기 중단 · '{layer.get('name') or layer.get('layer_id')}'의 "
            '모션 데이터가 없습니다'
        )
    return min(times), max(times)


def _shift_layer_time(layer: Mapping[str, Any], offset_sec: float) -> Dict[str, Any]:
    shifted = copy.deepcopy(dict(layer))
    offset = round(max(0.0, float(offset_sec)), 9)
    if offset <= EPSILON:
        return shifted
    for frame in shifted.get('frames') or []:
        frame['time_sec'] = round(
            _finite(frame.get('time_sec'), '프레임 시간') + offset, 9
        )
    for curve in shifted.get('point_curves') or []:
        for point in curve.get('points') or []:
            point['time_sec'] = round(
                _finite(point.get('time_sec'), '포인트 시간') + offset, 9
            )
    return shifted


def _only_recorded_values(
    frames: List[Dict[str, Any]],
    spans: Mapping[str, List[tuple[float, float]]],
) -> List[Dict[str, Any]]:
    """합치기는 **없던 값을 만들지 않는다** · §6-278

    합성(`render_project`)은 재생을 위해 모든 축을 매 순간 채운다 · 값이 없는
    시간은 마지막 값을 그대로 물고 간다 · 모터는 언제나 갈 자리가 있어야 하니
    재생에는 그게 맞다.

    그런데 합친 **레이어**에 그대로 넣으면, 25초짜리 축이 654초까지 늘어난
    채로 저장된다 · 그 뒤 `extend_point_curves_to_frames` 가 그 빈 구간마다
    2점짜리 곡선을 만들고, 화면에는 끝까지 평평한 선과 집을 수 없는 끝점이
    남는다 · 실측으로 1-4(3.16~38.04초)가 0.02~654.40초로 저장됐고 그 끝점은
    선택도 삭제도 되지 않았다.

    그래서 **원래 값이 있던 시간만** 남긴다 · 판정은 `owned_at` 하나뿐이라
    재생·녹화·MIDI 와 같은 답을 쓴다 · 빠진 시간은 재생할 때 합성이 그대로
    물고 가므로 **모터가 도는 모양은 달라지지 않는다**.
    """
    if not spans:
        return frames
    trimmed = []
    for frame in frames:
        time_sec = float(frame.get('time_sec') or 0.0)
        values = {
            motion_id: value
            for motion_id, value in (frame.get('values') or {}).items()
            if str(motion_id) not in spans
            or owned_at(spans[str(motion_id)], time_sec)
        }
        trimmed.append({**frame, 'values': values})
    return trimmed


def merge_layers(
    project: Dict[str, Any],
    layer_ids: Iterable[Any],
    *,
    name: Any = '합친 레이어',
    append_layer_id: Any = '',
    initial_motion_values_deg: Mapping[str, float] | None = None,
) -> Dict[str, Any]:
    selected_ids = {str(value) for value in layer_ids if str(value)}
    if len(selected_ids) < 2:
        raise ValueError('합칠 레이어를 두 개 이상 선택하세요')
    selected_layers = [
        copy.deepcopy(layer)
        for layer in project.get('layers') or []
        if str(layer.get('layer_id') or '') in selected_ids
    ]
    if len(selected_layers) != len(selected_ids):
        raise ValueError('선택한 레이어 일부를 찾을 수 없습니다')
    for layer in selected_layers:
        layer['enabled'] = True
        # 나머지 시스템과 **같은 규칙**을 쓴다 · §6-90
        #
        # 전에는 "모든 축이 포인트 곡선으로 덮여 있어야 한다" 고 요구했다 ·
        # 그런데 녹화 레이어는 20ms 프레임만 있고 곡선이 없다 · 스튜디오가
        # 만들어 내는 것이 바로 그것인데, 그래서 **녹화한 레이어는 영영 합칠
        # 수 없었다**.
        #
        # 재생·내보내기·초기 위치가 지키는 불변식은 그게 아니다 · "곡선이
        # **있으면** 프레임과 맞아야 한다" 이다. 합치기만 혼자 더 센 규칙을
        # 만들어 쓰고 있었다.
        if not _tracks(normalize_layer(copy.deepcopy(layer))):
            raise ValueError(
                f"레이어 합치기 중단 · '{layer.get('name') or layer.get('layer_id')}'에 "
                '모션 데이터 없음'
            )
        mismatches = point_curve_frame_mismatches(layer)
        if mismatches:
            axes = ', '.join(sorted({
                str(item.get('motion_id') or '') for item in mismatches
            }))
            raise ValueError(
                f"레이어 합치기 중단 · '{layer.get('name') or layer.get('layer_id')}'의 "
                f'포인트 곡선이 20ms 프레임과 어긋납니다: {axes}'
            )
    # 한 축이 한쪽에만 포인트로 덮여 있으면 합치지 않는다 · §6-116
    #
    # 합치면 그 축은 **반쪽**이 된다 · 덮인 구간은 포인트로 편집되고 나머지는
    # "포인트가 없는 모션은 편집할 수 없습니다" 로 막힌다 · 거기서 「전체 포인트
    # 생성」을 누르면 축 전체를 새로 맞춘 곡선 하나로 덮어써, 앞쪽에서 손으로
    # 다듬어 둔 포인트가 사라진다 · 합치고 나서는 되돌릴 방법이 없다.
    #
    # 축이 서로 다른 경우는 막지 않는다 · 그때는 축마다 상태가 한결같아서
    # "이 축은 포인트로 편집, 저 축은 아직" 이 그대로 성립한다.
    covered_by: Dict[str, List[str]] = {}
    uncovered_by: Dict[str, List[str]] = {}
    for layer in selected_layers:
        normalized = normalize_layer(copy.deepcopy(layer))
        gaps = set(layer_point_coverage_issues(normalized))
        label = str(layer.get('name') or layer.get('layer_id') or '')
        for motion_id in _tracks(normalized):
            table = uncovered_by if motion_id in gaps else covered_by
            table.setdefault(motion_id, []).append(label)
    mixed_axes = sorted(set(covered_by) & set(uncovered_by))
    if mixed_axes:
        motion_id = mixed_axes[0]
        with_points = ', '.join(covered_by[motion_id])
        without_points = ', '.join(uncovered_by[motion_id])
        raise ValueError(
            f'레이어 합치기 중단 · 축 {motion_id} 은 한쪽에만 포인트가 있습니다 · '
            f'포인트 있음: {with_points} · 포인트 없음: {without_points} · '
            '이대로 합치면 그 축은 앞부분만 포인트로 편집되고 나머지는 편집할 수 '
            '없습니다 · 합치기 전에 포인트 없는 레이어의 해당 축에 포인트를 '
            '만들거나, 양쪽 모두 포인트 없이 합치세요'
        )

    append_id = str(append_layer_id or '')
    append_offset_sec = 0.0
    append_seam_sec = 0.0
    if append_id:
        if append_id not in selected_ids:
            raise ValueError('뒤로 이동할 레이어가 합치기 대상에 포함되지 않았습니다')
        append_index = next(
            index for index, layer in enumerate(selected_layers)
            if str(layer.get('layer_id') or '') == append_id
        )
        stationary_layers = [
            layer for index, layer in enumerate(selected_layers)
            if index != append_index
        ]
        stationary_end = max(
            _layer_time_bounds(layer)[1] for layer in stationary_layers
        )
        append_start, _append_end = _layer_time_bounds(selected_layers[append_index])
        period_sec = _finite(
            project.get('period_sec') or DEFAULT_PERIOD_SEC, '모션 주기'
        )
        if period_sec <= 0.0:
            raise ValueError('모션 주기는 0보다 커야 합니다')
        append_offset_sec = round(max(
            0.0, stationary_end + period_sec - append_start
        ), 9)
        selected_layers[append_index] = _shift_layer_time(
            selected_layers[append_index], append_offset_sec
        )
        # 이음매는 **뒤 레이어의 첫 프레임**이다 · 옮긴 양이 아니다
        append_seam_sec = _layer_time_bounds(selected_layers[append_index])[0]
    temporary = {
        'period_sec': DEFAULT_PERIOD_SEC,
        'layers': selected_layers,
    }
    conflicts = layer_conflicts(temporary)
    if conflicts:
        first = conflicts[0]
        raise ValueError(
            '레이어 합치기 중단 · 시간 충돌: '
            f"{first['motion_id']} · {first['first_layer_name']} / "
            f"{first['second_layer_name']} · "
            f"{first['start_sec']:.3f}~{first['end_sec']:.3f}초"
        )
    frames = _only_recorded_values(
        render_project(
            temporary,
            initial_motion_values_deg=initial_motion_values_deg,
        ),
        playback_ownership(temporary),
    )
    merged_point_curves = collect_merged_point_curves(selected_layers)
    merged = normalize_layer({
        'layer_id': f'merged_{uuid.uuid4().hex[:8]}',
        'name': str(name or '합친 레이어').strip()[:40] or '합친 레이어',
        'enabled': True,
        'locked': False,
        'source_layer_ids': sorted(selected_ids),
        'frames': frames,
        'point_curves': merged_point_curves,
    })
    # 곡선을 프레임 전체로 늘린다 · 편집할 수 없는 구간을 남기지 않는다 · §6-252
    merged['point_curves'] = extend_point_curves_to_frames(merged)
    # 합쳐도 **축마다 곡선 하나**다 · §6-281
    merged = one_curve_per_axis(normalize_layer(merged))
    merged['merge_report'] = {
        'mode': 'append' if append_id else 'preserve',
        'append_layer_id': append_id,
        'append_offset_sec': append_offset_sec,
        'append_seam': _append_seam_steps(merged, append_seam_sec)
        if append_id else [],
    }
    return merged


def _append_seam_steps(
    merged: Mapping[str, Any], seam_sec: float
) -> List[Dict[str, Any]]:
    """이어 붙인 자리에서 값이 얼마나 튀는가 · §6-116

    앞 레이어의 마지막 값과 뒤 레이어의 첫 값 사이에는 아무것도 없다 · 섞어
    주지 않으므로 20ms 한 칸에 그 차이만큼 건너뛴다 · 실제로 40° 가 튄 적이
    있다(다른 칸은 최대 1.5°) · 모터에 계단 명령이 그대로 나간다.

    **막지는 않는다** · 사용자가 보고 판단할 일이다 · 다만 말은 해 준다.
    """
    frames = list(merged.get('frames') or [])
    if len(frames) < 2 or seam_sec <= EPSILON:
        return []
    seam_index = next(
        (
            index for index, frame in enumerate(frames)
            if _finite(frame.get('time_sec'), '프레임 시간') >= seam_sec - EPSILON
        ),
        None,
    )
    if not seam_index:
        return []
    before = frames[seam_index - 1].get('values') or {}
    after = frames[seam_index].get('values') or {}
    steps = []
    for motion_id in sorted(set(before) & set(after)):
        delta = abs(float(after[motion_id]) - float(before[motion_id]))
        steps.append({
            'motion_id': motion_id,
            'time_sec': round(
                _finite(frames[seam_index].get('time_sec'), '프레임 시간'), 9
            ),
            'step_deg': round(delta, 4),
        })
    steps.sort(key=lambda item: -item['step_deg'])
    return steps
