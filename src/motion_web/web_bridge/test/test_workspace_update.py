"""화면에서 누르는 업데이트 · §6-97

여기서 보는 것은 **확인하고 · 띄우고 · 읽는** 부분이다 · 실제로 받고 빌드하는
일은 `deploy/update_workspace.sh` 가 한다.

가장 중요한 것 둘 ·

  · 막을 이유가 하나라도 있으면 **시작하지 않는다** · 모터가 도는 중에
    서비스를 멈추면 도는 채로 제어가 사라진다
  · 작업은 서비스 **바깥에서** 돈다 · 자식으로 띄우면 `systemctl stop
    motion-control` 이 업데이트 자신을 죽인다
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from motion_web_bridge.workspace_update import BRANCH, UNIT_NAME, WorkspaceUpdate


class FakeRun:
    """`git ...` 과 `systemd-run ...` 을 흉내 낸다."""

    def __init__(self, answers=None, failures=()):
        self.answers = {
            'rev-parse --abbrev-ref HEAD': BRANCH,
            'status --porcelain --untracked-files=no': '',
            'rev-parse HEAD': 'a' * 40,
            'fetch origin main': '',
            f'rev-parse origin/{BRANCH}': 'b' * 40,
            f'rev-list --count HEAD..origin/{BRANCH}': '3',
            f'log -1 --format=%s origin/{BRANCH}': '중계 배선',
            **(answers or {}),
        }
        self.failures = set(failures)
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))
        key = ' '.join(args[1:]) if args and args[0] == 'git' else args[0]
        if key in self.failures:
            return SimpleNamespace(returncode=1, stdout='', stderr=f'{key} 실패')
        return SimpleNamespace(
            returncode=0, stdout=self.answers.get(key, ''), stderr=''
        )


@pytest.fixture
def updater(tmp_path):
    def build(run):
        return WorkspaceUpdate(tmp_path / 'ws', state_dir=tmp_path / 'state', run=run)
    return build


def _workspace_with_script(tmp_path):
    script = (
        tmp_path / 'ws/src/motion_web/web_bridge/deploy/update_workspace.sh'
    )
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text('#!/usr/bin/env bash\n', encoding='utf-8')
    return script


# --------------------------------------------------------------------- #
# 무엇을 받을지 확인한다
# --------------------------------------------------------------------- #

def test_it_reports_what_would_come_in(updater):
    state = updater(FakeRun()).check()

    assert state['behind'] == 3
    assert state['target'] == 'b' * 40
    assert state['up_to_date'] is False
    assert state['blocked_reason'] == ''


def test_nothing_to_do_is_not_an_error(updater):
    run = FakeRun({f'rev-list --count HEAD..origin/{BRANCH}': '0'})
    state = updater(run).check()

    assert state['up_to_date'] is True


def test_only_main_is_updated(updater):
    """PC 마다 다른 브랜치가 조용히 도는 것이 제일 위험하다."""
    run = FakeRun({'rev-parse --abbrev-ref HEAD': 'wip-next'})
    state = updater(run).check()

    assert BRANCH in state['blocked_reason']
    assert 'wip-next' in state['blocked_reason']


def test_local_edits_stop_the_update(updater):
    """합치다가 사용자의 수정을 덮거나 충돌 난 채로 빌드하는 것이 제일 나쁘다."""
    run = FakeRun({'status --porcelain --untracked-files=no': ' M src/a.py'})
    state = updater(run).check()

    assert state['dirty'] is True
    assert '고친 파일' in state['blocked_reason']


def test_a_dead_network_does_not_break_the_page(updater):
    """이 값은 시스템 정보 화면이 같이 읽는다 · 여기서 터지면 화면이 통째로
    비어 버린다."""
    state = updater(FakeRun(failures={'fetch origin main'})).check()

    assert state['fetched'] is False
    assert state['message']
    assert state['current'] == 'a' * 40


def test_a_broken_repository_is_reported_not_raised(updater):
    state = updater(FakeRun(failures={'rev-parse --abbrev-ref HEAD'})).check()

    assert state['blocked_reason']


# --------------------------------------------------------------------- #
# 시작 · 막을 이유가 있으면 시작하지 않는다
# --------------------------------------------------------------------- #

def test_a_reason_from_outside_stops_it(updater, tmp_path):
    """모터가 도는 중인지는 웹 브리지가 안다 · 이 객체는 모른다."""
    _workspace_with_script(tmp_path)
    run = FakeRun()
    with pytest.raises(ValueError, match='모터'):
        updater(run).start(blocked_reason='모터 동작 중입니다')

    assert not any(call[0] == 'systemd-run' for call in run.calls)


def test_it_does_not_start_twice(updater, tmp_path):
    _workspace_with_script(tmp_path)
    instance = updater(FakeRun())
    instance._write_state({'status': 'running', 'phase': 'building'})

    with pytest.raises(ValueError, match='이미 진행'):
        instance.start()


def test_it_does_not_start_when_there_is_nothing_to_get(updater, tmp_path):
    _workspace_with_script(tmp_path)
    run = FakeRun({f'rev-list --count HEAD..origin/{BRANCH}': '0'})

    with pytest.raises(ValueError, match='최신'):
        updater(run).start()


def test_it_runs_outside_the_service(updater, tmp_path):
    """자식으로 띄우면 `systemctl stop motion-control` 이 같은 cgroup 인
    업데이트 자신을 죽인다 · 그래서 `systemd-run` 으로 따로 띄운다."""
    _workspace_with_script(tmp_path)
    run = FakeRun()
    result = updater(run).start()

    spawned = [call for call in run.calls if call[0] == 'systemd-run']
    assert len(spawned) == 1
    command = spawned[0]
    assert '--user' in command and f'--unit={UNIT_NAME}' in command
    assert any(item.startswith('--setenv=MOTION_WORKSPACE=') for item in command)
    assert any(
        item.startswith('--setenv=MOTION_UPDATE_OPERATION_ID=') for item in command
    )
    assert command[-1].endswith('update_workspace.sh')
    assert result['success'] is True


def test_the_screen_sees_it_started_right_away(updater, tmp_path):
    """스크립트가 첫 상태를 쓰기까지의 사이에 화면이 가만히 있으면 사용자가
    다시 누른다."""
    _workspace_with_script(tmp_path)
    instance = updater(FakeRun())
    instance.start()

    assert instance.status()['status'] == 'running'


def test_a_failed_spawn_is_written_down(updater, tmp_path):
    _workspace_with_script(tmp_path)
    instance = updater(FakeRun(failures={'systemd-run'}))

    with pytest.raises(RuntimeError):
        instance.start()

    assert instance.status()['status'] == 'failure'


# --------------------------------------------------------------------- #
# 읽기 · 진행 상황은 디스크에 있다
# --------------------------------------------------------------------- #

def test_never_run_is_idle(updater):
    assert updater(FakeRun()).status()['status'] == 'idle'


def test_it_reads_the_state_the_script_wrote(updater, tmp_path):
    """웹 브리지는 이 일을 하는 중에 멈췄다 다시 뜬다 · 기억이 아니라
    디스크에서 읽어야 결과를 보여 줄 수 있다."""
    instance = updater(FakeRun())
    state_dir = tmp_path / 'state'
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / 'state.json').write_text(
        json.dumps({'status': 'success', 'phase': 'done'}), encoding='utf-8'
    )
    (state_dir / 'update.log').write_text('· 빌드\n· 끝\n', encoding='utf-8')

    status = instance.status()

    assert status['status'] == 'success'
    assert '끝' in status['log_tail']


def test_a_half_written_state_is_not_a_crash(updater, tmp_path):
    instance = updater(FakeRun())
    state_dir = tmp_path / 'state'
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / 'state.json').write_text('{"status": "run', encoding='utf-8')

    assert instance.status()['status'] == 'idle'


# --------------------------------------------------------------------- #
# 스크립트와 갈리지 않는다
# --------------------------------------------------------------------- #

SCRIPT = (
    Path(__file__).resolve().parents[1] / 'deploy/update_workspace.sh'
).read_text(encoding='utf-8')


def test_the_script_and_this_module_mean_the_same_branch():
    """둘 중 하나만 고치면 화면은 main 을 말하고 스크립트는 다른 것을 받는다."""
    assert f'BRANCH="{BRANCH}"' in SCRIPT


def test_the_script_only_fast_forwards():
    """여기서 합치기가 생기면 PC 마다 다른 역사가 된다."""
    assert '--ff-only' in SCRIPT
    assert 'git pull' not in SCRIPT


def test_the_script_can_put_it_back():
    """빌드가 깨지면 반쯤 덮인 채로 남는다 · 직전 커밋으로 되돌리고 다시
    빌드해서 도는 상태로 끝나야 한다."""
    assert 'git -C "${WORKSPACE}" reset --hard "${FROM_COMMIT}"' in SCRIPT
    assert 'roll_back' in SCRIPT
    assert 'trap on_error ERR' in SCRIPT


def test_the_script_starts_the_services_again_even_when_it_fails():
    """실패했는데 서비스가 꺼진 채로 끝나면 장비가 멈춘 채 남는다."""
    failure_path = SCRIPT[SCRIPT.index('on_error()'):SCRIPT.index('trap on_error ERR')]
    assert 'start_services' in failure_path


def test_the_script_renders_the_units_again():
    """오늘처럼 유닛 템플릿이 바뀌면 빌드만으로는 옛 유닛이 그대로 남는다."""
    assert 'install_user_service.sh' in SCRIPT
