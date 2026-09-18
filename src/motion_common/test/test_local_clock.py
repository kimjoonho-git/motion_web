"""이 PC 가 몇 시라고 믿는가 · §6-147

시간대는 NTP 가 못 고친다 · PC 를 들고 다른 나라에 가서 네트워크에 붙여도
`Asia/Seoul` 그대로다 · 시계는 정확한데 현지 시각만 틀린 상태가 되고, 한국에서
만든 09:17 스케줄이 파리 현지 02:17 에 돈다 · 하루가 지나야 안다.

막을 방법은 **화면이 늘 시간대를 보여주는 것** 하나다 · 그 한 줄을 만드는
자리라서, 여기서 조용히 틀리면 화면도 같이 조용히 틀린다.
"""

from pathlib import Path

import pytest

from motion_common import local_clock


@pytest.fixture(autouse=True)
def _forget_cache():
    """검사끼리 캐시를 물려주지 않는다."""
    local_clock._ntp_cache = (0.0, None)
    yield
    local_clock._ntp_cache = (0.0, None)


def test_the_snapshot_says_which_timezone():
    """시각만 보여주면 소용없다 · **어느 시간대인지**가 걸러내는 값이다."""
    now = local_clock.snapshot()
    assert now['timezone'], '시간대 이름이 비어 있으면 화면이 말할 게 없다'
    assert now['local_time']
    assert now['utc_offset']


def test_the_offset_is_a_real_offset():
    """`+0900` 모양이어야 화면이 벽시계를 계산할 수 있다."""
    offset = local_clock.snapshot()['utc_offset']
    assert len(offset) == 5 and offset[0] in '+-' and offset[1:].isdigit()


def test_unknown_is_not_the_same_as_not_synced():
    """모른다(None)를 아니다(False)로 적으면 없는 문제를 만든다."""
    assert local_clock.ntp_synced() in (True, False, None)


def test_the_region_name_wins_over_the_abbreviation(monkeypatch, tmp_path):
    """`Asia/Seoul` 이라야 한다 · `KST` 로는 서머타임 여부를 알 수 없다."""
    path = tmp_path / 'timezone'
    path.write_text('Europe/Paris\n', encoding='utf-8')
    monkeypatch.setattr(local_clock, '_TIMEZONE_FILE', path)
    assert local_clock.timezone_name() == 'Europe/Paris'


def test_a_missing_file_still_answers(monkeypatch, tmp_path):
    """파일이 없는 배포판도 있다 · 화면이 빈칸이 되면 안 된다."""
    monkeypatch.setattr(local_clock, '_TIMEZONE_FILE', tmp_path / '없음')
    assert local_clock.timezone_name(), '어떤 이름이든 돌려줘야 한다'


def test_asking_often_does_not_spawn_a_process_every_time(monkeypatch):
    """상태 조회는 1초에도 여러 번 온다 · 그때마다 프로세스를 띄우면 §6-146 재현이다."""
    calls = {'count': 0}

    def counted(*_args, **_kwargs):
        calls['count'] += 1
        return type('R', (), {'stdout': 'yes'})()

    monkeypatch.setattr(local_clock.subprocess, 'run', counted)
    for _ in range(50):
        local_clock.ntp_synced()
    assert calls['count'] == 1, f'{calls["count"]}번이나 띄웠다'


def test_a_broken_timedatectl_does_not_crash_the_status(monkeypatch):
    """시각을 보여주려다 상태 조회 전체를 죽이면 안 된다."""
    def explode(*_args, **_kwargs):
        raise OSError('timedatectl 없음')

    monkeypatch.setattr(local_clock.subprocess, 'run', explode)
    assert local_clock.ntp_synced() is None
    assert local_clock.snapshot()['timezone']
