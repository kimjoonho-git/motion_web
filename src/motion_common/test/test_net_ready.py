"""랜 주소 대기 검증 · 전원을 껐다 켜면 연동이 죽던 일의 근거 · §6-96

DDS 는 참가자를 만드는 순간의 랜카드만 본다 · 주소가 없을 때 뜨면 루프백만
광고하고 다른 PC 를 영영 못 본다 · 그래서 "기다린다"와 "그래도 언젠가는 뜬다"
둘 다 지켜져야 한다 · 한쪽만 지키면 연동이 죽거나 모션 전체가 안 뜬다.
"""

from motion_common import net_ready


def test_loopback_is_never_a_lan_address():
    """루프백은 랜이 아니다 · 이걸 랜으로 세면 기다림이 그냥 통과한다."""
    assert not [a for a in net_ready.lan_addresses() if a.startswith('127.')]


def test_addresses_are_sorted_and_unique():
    """견주는 값이므로 순서가 흔들리면 안 된다 · 매번 바뀐 것처럼 보인다."""
    addresses = net_ready.lan_addresses()
    assert list(addresses) == sorted(set(addresses))


def test_returns_at_once_when_the_lan_is_already_up(monkeypatch):
    """이미 주소가 있으면 한 번도 자지 않는다 · 부팅을 늦출 이유가 없다."""
    monkeypatch.setattr(net_ready, 'lan_addresses', lambda: ('172.16.21.10',))

    def never(_seconds):
        raise AssertionError('이미 랜이 있는데 기다렸다')

    monkeypatch.setattr(net_ready.time, 'sleep', never)
    assert net_ready.wait_for_lan(report=lambda _message: None) == ('172.16.21.10',)


def test_waits_until_the_lan_shows_up(monkeypatch):
    """늦게 올라오는 랜을 기다린다 · 이것이 이 파일이 있는 이유다."""
    calls = {'count': 0}

    def appearing():
        calls['count'] += 1
        return ('172.16.21.10',) if calls['count'] > 3 else ()

    monkeypatch.setattr(net_ready, 'lan_addresses', appearing)
    monkeypatch.setattr(net_ready.time, 'sleep', lambda _seconds: None)
    assert net_ready.wait_for_lan(report=lambda _message: None) == ('172.16.21.10',)
    assert calls['count'] == 4


def test_gives_up_instead_of_blocking_forever(monkeypatch):
    """랜이 영영 없는 PC 도 떠야 한다 · 연동만 못 할 뿐 모션은 돌아야 한다."""
    monkeypatch.setattr(net_ready, 'lan_addresses', tuple)
    monkeypatch.setattr(net_ready.time, 'sleep', lambda _seconds: None)
    assert net_ready.wait_for_lan(
        timeout_sec=0.0, report=lambda _message: None,
    ) == ()


def test_the_helper_never_fails_the_service(monkeypatch):
    """여기서 죽으면 랜 문제가 모션 전체를 멈추는 더 큰 고장이 된다."""
    def explode():
        raise OSError('랜카드를 못 읽음')

    monkeypatch.setattr(net_ready, 'lan_addresses', explode)
    assert net_ready.main() == 0


def test_the_wait_can_be_tuned_per_site():
    """DHCP 가 느린 현장은 늘리고, 랜 안 쓰는 PC 는 0 으로 꺼 둔다."""
    assert net_ready.configured_timeout_sec({}) == net_ready.DEFAULT_TIMEOUT_SEC
    assert net_ready.configured_timeout_sec({net_ready.TIMEOUT_ENV: '5'}) == 5.0
    assert net_ready.configured_timeout_sec({net_ready.TIMEOUT_ENV: '0'}) == 0.0


def test_a_broken_knob_falls_back_instead_of_crashing():
    """오타 하나로 서비스가 안 뜨면 안 된다 · 기본값으로 물러난다."""
    for broken in ('', '   ', '나중에', None):
        assert net_ready.configured_timeout_sec(
            {net_ready.TIMEOUT_ENV: broken} if broken is not None else {},
        ) == net_ready.DEFAULT_TIMEOUT_SEC


def test_a_negative_knob_means_do_not_wait():
    """음수를 넣어도 시간을 거슬러 가지 않는다 · 그냥 안 기다린다."""
    assert net_ready.configured_timeout_sec({net_ready.TIMEOUT_ENV: '-5'}) == 0.0
