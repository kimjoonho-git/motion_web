import stat
from types import SimpleNamespace

from motion_web_bridge.bridge_node import MotionWebBridge


LAUNCHER = (
    '[Desktop Entry]\n'
    'Version=1.0\n'
    'Type=Application\n'
    'Name=모션 프로그램 열기\n'
    'Exec=xdg-open http://localhost:8000\n'
    'Terminal=false\n'
)


def test_desktop_shortcut_is_fixed_idempotent_and_executable(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    desktop = home / 'Desktop'
    desktop.mkdir(parents=True)
    share = tmp_path / 'share' / 'motion_web_bridge'
    source = share / 'deploy' / 'motion-program.desktop'
    source.parent.mkdir(parents=True)
    source.write_text(LAUNCHER, encoding='utf-8')
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if command == ['xdg-user-dir', 'DESKTOP']:
            return SimpleNamespace(returncode=0, stdout=f'{desktop}\n', stderr='')
        if command[:2] == ['gio', 'set']:
            return SimpleNamespace(returncode=0, stdout='', stderr='')
        raise AssertionError(f'unexpected command: {command}')

    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setattr(
        'motion_web_bridge.bridge_node.get_package_share_directory',
        lambda package: str(share),
    )
    monkeypatch.setattr('motion_web_bridge.bridge_node.subprocess.run', run)
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.workspace_root = tmp_path

    created = bridge.create_desktop_shortcut()
    installed = desktop / '모션 프로그램 열기.desktop'

    assert created['success'] is True
    assert created['status'] == 'created'
    assert created['path'] == str(installed)
    assert created['trusted'] is True
    assert installed.read_text(encoding='utf-8') == LAUNCHER
    assert installed.stat().st_mode & stat.S_IXUSR
    assert calls[0][0] == ['xdg-user-dir', 'DESKTOP']
    assert calls[1][0] == [
        'gio', 'set', str(installed), 'metadata::trusted', 'true',
    ]
    assert all(call[1].get('shell') is None for call in calls)

    repeated = bridge.create_desktop_shortcut()

    assert repeated['success'] is True
    assert repeated['status'] == 'already_installed'
    assert installed.read_text(encoding='utf-8') == LAUNCHER


def test_desktop_shortcut_rejects_home_as_disabled_desktop(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setattr(
        'motion_web_bridge.bridge_node.subprocess.run',
        lambda command, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=f'{home}\n',
            stderr='',
        ),
    )
    bridge = MotionWebBridge.__new__(MotionWebBridge)

    result = bridge.create_desktop_shortcut()

    assert result['success'] is False
    assert '바탕화면 폴더' in result['message']
    assert not (home / '모션 프로그램 열기.desktop').exists()
