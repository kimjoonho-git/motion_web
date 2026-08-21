"""워크스페이스 경로 해석 검증 · 절대경로 하드코딩 제거의 근거."""

from pathlib import Path

from motion_common import paths


def test_environment_variable_wins(monkeypatch, tmp_path):
    monkeypatch.setenv(paths.WORKSPACE_ENV, str(tmp_path))
    assert paths.workspace_root() == tmp_path.resolve()


def test_environment_variable_expands_user(monkeypatch):
    monkeypatch.setenv(paths.WORKSPACE_ENV, '~/some_ws')
    assert paths.workspace_root() == (Path.home() / 'some_ws').resolve()


def test_blank_environment_variable_is_ignored(monkeypatch, tmp_path):
    monkeypatch.setenv(paths.WORKSPACE_ENV, '   ')
    monkeypatch.chdir(tmp_path)
    # 공백 값은 미설정과 같게 다뤄 탐색으로 넘어간다
    resolved = paths.workspace_root()
    assert resolved.is_absolute()
    assert resolved != Path('   ').resolve()


def test_cwd_is_the_last_resort(monkeypatch, tmp_path):
    monkeypatch.delenv(paths.WORKSPACE_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    # 어느 후보에서도 표식을 못 찾으면 현재 작업 디렉터리로 물러난다
    monkeypatch.setattr(paths, '_walk_up', lambda candidate: None)
    assert paths.workspace_root() == tmp_path.resolve()


def test_source_tree_is_detected_by_src_and_scripts(monkeypatch, tmp_path):
    monkeypatch.delenv(paths.WORKSPACE_ENV, raising=False)
    workspace = tmp_path / 'ros2_ws'
    (workspace / 'src' / 'pkg').mkdir(parents=True)
    (workspace / 'scripts').mkdir()
    monkeypatch.chdir(workspace / 'src' / 'pkg')
    assert paths.workspace_root() == workspace.resolve()


def test_install_tree_resolves_to_its_parent(monkeypatch, tmp_path):
    monkeypatch.delenv(paths.WORKSPACE_ENV, raising=False)
    workspace = tmp_path / 'ros2_ws'
    share = workspace / 'install' / 'pkg' / 'share' / 'pkg'
    share.mkdir(parents=True)
    monkeypatch.chdir(share)
    assert paths.workspace_root() == workspace.resolve()


def test_derived_paths_hang_off_workspace_root(monkeypatch, tmp_path):
    monkeypatch.setenv(paths.WORKSPACE_ENV, str(tmp_path))
    root = tmp_path.resolve()
    assert paths.motion_projects_dir() == root / 'motion_projects'
    assert paths.config_dir() == root / 'config'
    assert paths.config_file('coordination_settings.yaml') == (
        root / 'config' / 'coordination_settings.yaml'
    )
    assert paths.scripts_dir() == root / 'scripts'
    assert paths.log_dir() == root / 'log'


def test_no_absolute_path_literals_in_module():
    source = Path(paths.__file__).read_text(encoding='utf-8')
    assert '/home/' not in source
