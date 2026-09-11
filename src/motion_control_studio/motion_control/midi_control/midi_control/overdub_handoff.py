"""추가 녹화 중 SELECT 이어받기 · §6-86

추가 녹화는 녹화된 축을 재생하면서 그 위에 얹는다. 재생이 그 축을 쥐고 있는
동안 MIDI 는 막히고, 구간이 끝나면 MIDI 차례가 된다 · 축 × 시간.

**문제는 그 넘어가는 순간이었다.**

전에는 실행 노드가 끝나는 순간 MIDI 가 모든 SELECT 를 껐다 · 하필 사용자가
이어서 녹화하려는 바로 그 지점이다. 다시 눌러야 했다.

그렇다고 그냥 켜 두면 위험하다 · 페이더는 사용자가 놓아둔 자리에 있고 모터는
재생을 따라 저만치 가 있다. 구간이 끝나 MIDI 명령이 통하는 순간 모터가 페이더
자리로 **튄다**.

그래서 켜 둔 채로, 재생이 쥔 **구간 동안 내내 Pickup 을 걸어 둔다** · 페이더가
모터를 따라 움직이고 그동안 명령은 막힌다. 구간이 끝나면 페이더는 이미 모터
자리에 있으니 그대로 이어서 녹화된다 · SELECT 는 켜진 채고 사용자는 아무것도
안 해도 된다.

소유 판정은 스튜디오와 **같은 규칙**이어야 한다 · 한쪽이 "재생 소유" 라고 보고
다른 쪽이 "MIDI 차례" 라고 보면 그 축은 두 주인이 동시에 밀거나 아무도 안
민다 · `motion_studio.timeline.owned_at` 과 같은 식이다.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence


def owned_at(spans: Iterable[Sequence[float]], time_sec: float) -> bool:
    """이 시각이 소유 구간 안인가 · 스튜디오와 같은 판정."""
    for span in spans or ():
        try:
            start = float(span[0])
            end = float(span[1])
        except (TypeError, ValueError, IndexError):
            continue
        if start - 1e-9 <= time_sec <= end + 1e-9:
            return True
    return False


class OverdubHandoff:
    """축의 소유 구간이 끝나는 순간을 잡아 낸다.

    상태는 축마다 하나 · "직전에 재생이 쥐고 있었나" 뿐이다.
    """

    def __init__(self) -> None:
        self.active = False
        self.spans: Dict[str, List[Sequence[float]]] = {}
        self.elapsed_sec = 0.0

    def update(self, payload: Mapping[str, Any]) -> None:
        """스튜디오 상태를 받아 둔다 · 추가 녹화가 아니면 모두 비운다."""
        recording = str(payload.get('state') or '') == 'recording'
        overdub = str(payload.get('record_mode') or '') == 'overdub'
        spans = payload.get('overdub_spans')
        self.active = bool(recording and overdub and isinstance(spans, dict) and spans)
        if not self.active:
            self.spans = {}
            self.elapsed_sec = 0.0
            return
        self.spans = {
            str(motion_id): list(value)
            for motion_id, value in spans.items()
            if isinstance(value, (list, tuple)) and value
        }
        try:
            self.elapsed_sec = max(0.0, float(payload.get('elapsed_sec') or 0.0))
        except (TypeError, ValueError):
            self.elapsed_sec = 0.0

    def owned_now(self, motion_id: str) -> bool:
        if not self.active:
            return False
        return owned_at(self.spans.get(str(motion_id), ()), self.elapsed_sec)

    def owned_channels(self, motion_ids_by_channel: Sequence[Iterable[str]]) -> List[int]:
        """지금 재생이 쥔 축을 담당하는 라인 · 이 라인들은 붙잡아 둔다.

        "풀린 순간" 을 잡아 그때 한 번 손보는 방법도 있었지만, 스튜디오 상태는
        0.5초마다 온다 · 풀린 것을 최대 0.5초 늦게 알게 되고 그 사이 MIDI 명령이
        통해 모터가 튄다. 구간 **동안 내내** 붙잡으면 그 구멍이 없다.

        상태가 늦게 오면 판정도 늦는데, 늦는 쪽은 "아직 재생 중" 이라 안전하다.
        """
        if not self.active:
            return []
        return [
            channel
            for channel, motion_ids in enumerate(motion_ids_by_channel)
            if any(self.owned_now(motion_id) for motion_id in motion_ids)
        ]

    def forget(self) -> None:
        """테이크가 끝났다 · 다음 테이크가 지난 기억을 물려받지 않게 한다."""
        self.active = False
        self.spans = {}
        self.elapsed_sec = 0.0
