"""축의 주인은 **축 × 시간**으로 정해진다 · §6-275

추가 녹화(오버더빙)는 녹화된 축을 재생하면서 그 위에 얹는다 · 그래서 같은
순간에도 축마다 주인이 다르고, **같은 축이라도 시각에 따라 주인이 바뀐다**.

    축 1-4  ├─ 3.2~25.1초 재생 ─┤   ├─ 27.8~45.4초 재생 ─┤
            └─ 그 밖의 시간은 사람 손 · MIDI 로 녹화 ─────┘

이 사실은 스튜디오가 한 번 계산해(`playback_ownership`) 네 곳에 나눠 준다.

    ① 녹화에서 버리기   재생이 쥔 시간의 값은 기록하지 않는다
    ② 모터 발행         재생이 쥔 축만 명령한다
    ③ 재생 계속 판정    내 축을 남이 쥐었으면 멈춘다
    ④ MIDI SELECT       재생이 쥔 축은 추종, 나머지는 조종

**네 곳이 각자 판정을 들고 있었고, 그중 둘이 시간을 빠뜨렸다.**

    ③ 은 "이 축에 구간이 있느냐" 만 봤다 · 26초에 사람이 1-4 를 잡으면
       "MIDI 가 축 3 을 쓴다" 며 **실행 전체를 오류로 끝냈다** · 녹화는
       계속되는데 1-1·1-2·1-3 이 통째로 멈췄다.
    ④ 는 축 이름만 받았다 · 한 번 녹화된 축은 그 뒤로 영영 재생 것이 되어
       25초가 지나도 페이더가 그 축을 잡지 못했다.

그래서 판정을 **여기 하나**로 모은다 · 네 곳은 이 함수만 부른다 · 시간을
빠뜨리려면 이 파일을 고쳐야 하고, 그러면 네 곳이 같이 바뀐다.
"""

from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

#: 부동소수 비교의 여유 · 20ms 프레임에 견주면 무시할 만하다
EPSILON = 1e-9

Span = Tuple[float, float]


def owned_at(spans: Iterable[Span] | None, time_sec: float) -> bool:
    """이 시각이 소유 구간 안인가 · **이것이 유일한 판정이다**

    구간이 없거나 비어 있으면 거짓 · "이 축은 재생이 한 번도 쥐지 않는다".
    경계는 양쪽 다 포함한다 · 끝 시각에 잠깐 주인이 없어지면 모터가 재생과
    MIDI 사이에서 떤다.
    """
    if not spans:
        return False
    for span in spans:
        try:
            start, end = span
        except (TypeError, ValueError):
            continue
        if float(start) - EPSILON <= time_sec <= float(end) + EPSILON:
            return True
    return False


def playback_owns(
    table: Mapping[Any, Sequence[Span]] | None,
    key: Any,
    time_sec: float,
    *,
    missing_is_playbacks: bool,
) -> bool:
    """표에서 이 축을 찾아 그 시각의 주인을 묻는다 · §6-275

    `missing_is_playbacks` 는 **표에 없는 축**을 어느 쪽으로 볼지다 · 부르는
    곳마다 뜻이 다르므로 숨기지 않고 매번 적는다.

        발행·재생 판정   표는 녹화 대상 축을 전부 담는다 · 빠진 축은
                         표를 못 받은 것이므로 재생 것으로 본다 (참)
        MIDI SELECT      표에 없으면 재생이 안 쥔 축이다 (거짓)
    """
    if not table or key not in table:
        return missing_is_playbacks
    return owned_at(table.get(key), time_sec)


def parse_spans(raw: Any, *, key: Any = str) -> Dict[Any, List[Span]]:
    """오간 값을 구간 표로 읽는다 · 숫자가 아니거나 뒤집힌 구간은 버린다

    빈 목록은 **버리지 않는다** · "이 축은 재생이 한 번도 쥐지 않는다" 라는
    뜻이 있어서, 없애면 표에 없는 축과 구별이 사라진다 · §6-87
    """
    if not isinstance(raw, Mapping):
        return {}
    table: Dict[Any, List[Span]] = {}
    for raw_key, spans in raw.items():
        try:
            clean_key = key(raw_key)
        except (TypeError, ValueError):
            continue
        if isinstance(clean_key, str):
            clean_key = clean_key.strip()
            if not clean_key:
                continue
        if not isinstance(spans, (list, tuple)):
            continue
        parsed: List[Span] = []
        for span in spans:
            if not isinstance(span, (list, tuple)) or len(span) != 2:
                continue
            try:
                start = float(span[0])
                end = float(span[1])
            except (TypeError, ValueError):
                continue
            if not (start == start and end == end):  # NaN
                continue
            if end < start:
                continue
            parsed.append((start, end))
        table[clean_key] = parsed
    return table
