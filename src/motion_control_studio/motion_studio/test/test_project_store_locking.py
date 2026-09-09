"""스튜디오 파일 기록의 락 계약 · §6-48.

`<프로젝트>/motions/`는 세 프로세스가 쓴다 · 웹 브리지 · 매핑 관리자 · 스튜디오.
앞의 둘은 `store.locked_update`에서 만나는데 스튜디오만 빠져 있었다.

**한쪽만 잡는 락은 아무것도 막지 못한다.** 그래서 이 시험은 "락을 잡는가"를
직접 본다 · 기록이 원자적인지만 보면 이 결함을 놓친다.
"""

from pathlib import Path

from motion_common import store as common_store
from motion_studio.project_store import ProjectStore


def _record_locked_paths(monkeypatch):
    taken = []
    real = common_store.locked_update

    def spy(path, *args, **kwargs):
        taken.append(Path(path))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(common_store, 'locked_update', spy)
    return taken


def test_motion_file_write_takes_the_shared_lock(tmp_path, monkeypatch):
    store = ProjectStore(tmp_path)
    taken = _record_locked_paths(monkeypatch)

    name = store.write_motion_file('demo', '{"frames": []}\n')

    written = store.files_dir / name
    assert written.is_file()
    assert written in taken, '기록 경로의 락을 잡지 않았다'


def test_project_save_takes_the_shared_lock(tmp_path, monkeypatch):
    mapping = tmp_path / 'motion_axis_matching' / 'face.yaml'
    mapping.parent.mkdir(parents=True, exist_ok=True)
    mapping.write_text('rows:\n- motion_id: 1-1\n  motor_axis: 1\n', encoding='utf-8')
    store = ProjectStore(tmp_path)
    saved = store.save_project(store.create_project('잠금 시험', 'face.yaml'))
    taken = _record_locked_paths(monkeypatch)

    store.save_project(saved)

    assert taken, '프로젝트 저장이 락을 하나도 잡지 않았다'
    assert all(path.suffix in ('.json',) for path in taken)


def test_lock_path_matches_the_web_side(tmp_path):
    """두 저장소가 같은 파일을 쓰면 같은 락 파일에서 만나야 한다."""
    target = tmp_path / 'motions' / 'shared.json'
    assert common_store.lock_path_for(target).name == '.shared.json.lock'
