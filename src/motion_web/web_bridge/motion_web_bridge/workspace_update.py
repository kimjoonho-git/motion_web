"""작업공간을 최신 main 으로 올린다 · §6-97

화면에서 사용자가 눌러 코드를 받고 다시 빌드한다 · 실제 일은
`deploy/update_workspace.sh` 가 하고, 여기서는 **확인하고 · 띄우고 · 읽는다**.

이 일을 하는 동안 **웹 브리지 자신이 멈췄다 다시 뜬다** · 그래서 둘을 지킨다 ·

  · 작업은 `systemd-run` 으로 서비스 **바깥에** 띄운다 · 자식으로 띄우면
    `systemctl stop motion-control` 이 같은 cgroup 인 작업까지 죽인다
  · 진행 상황은 기억이 아니라 **디스크**에 둔다 · 다시 뜬 뒤에도 결과를
    화면에 보여줄 수 있어야 한다

브랜치는 main 고정이다 · PC 마다 다른 브랜치가 조용히 도는 것이 제일 위험하다.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional

#: 하나뿐인 브랜치 · 스크립트와 **같은 값**이어야 한다
BRANCH = 'main'

#: 작업 하나만 돈다 · 이름이 고정이라 두 번째 요청은 systemd 가 거절한다
UNIT_NAME = 'motion-update'

#: 화면에 보여 줄 기록 줄 수
LOG_TAIL_LINES = 60

GIT_TIMEOUT_SEC = 60.0

#: 정말로 도는 중인 상태는 이것 하나다 · `motor_runtime_store` 가 그렇게 정한다
#:
#: 끝난 작업 기록은 지워지지 않고 남는다(`partial`·`timeout`·`success`…) ·
#: 그것을 진행 중으로 보면 업데이트 버튼이 **영원히** 꺼진다 · 실제로 며칠 전
#: 끝난 모터 검색 기록 하나에 막혔다.
RUNNING_OPERATION_STATUS = 'running'


def blocking_reason(snapshot: Mapping[str, Any]) -> str:
    """지금 업데이트하면 안 되는 이유 · 없으면 빈 문자열.

    업데이트는 서비스를 멈추고 몇 분 동안 빌드한다 · 모터가 움직이는 중에 그
    일을 시작하면 도는 채로 제어가 사라진다.

    무엇이 위험한지는 **웹 브리지의 상태**가 안다 · 판단은 여기 한 곳에서
    하고, 화면과 API 는 이유를 받아 쓴다.
    """
    payload = snapshot if isinstance(snapshot, Mapping) else {}
    activity = payload.get('motor_activity')
    if isinstance(activity, Mapping) and activity.get('active'):
        label = str(activity.get('label') or '모터 동작')
        return f'{label} 중에는 업데이트할 수 없습니다'
    operation = payload.get('motor_operation')
    if (
        isinstance(operation, Mapping)
        and str(operation.get('status') or '') == RUNNING_OPERATION_STATUS
    ):
        return '모터 작업이 진행 중입니다'
    return ''


def _state_dir() -> Path:
    given = os.environ.get('MOTION_UPDATE_STATE_DIR')
    if given:
        return Path(given).expanduser()
    base = os.environ.get('XDG_STATE_HOME') or (Path.home() / '.local/state')
    return Path(base).expanduser() / 'motion-update'


class WorkspaceUpdate:
    def __init__(
        self,
        workspace: Path,
        *,
        state_dir: Optional[Path] = None,
        run: Callable[..., Any] = subprocess.run,
    ) -> None:
        self.workspace = Path(workspace)
        self.state_dir = Path(state_dir) if state_dir else _state_dir()
        self._run = run

    # ----------------------------------------------------------------- #
    # 확인
    # ----------------------------------------------------------------- #

    def check(self) -> Dict[str, Any]:
        """무엇이 받아질지 · 지금 받아도 되는지.

        원격을 한 번 물어본다(fetch) · 네트워크가 없으면 그 사실을 돌려준다 ·
        여기서 예외를 던지면 화면의 시스템 정보 전체가 같이 죽는다.
        """
        try:
            branch = self._git('rev-parse', '--abbrev-ref', 'HEAD')
            current = self._git('rev-parse', 'HEAD')
            dirty = bool(
                self._git('status', '--porcelain', '--untracked-files=no')
            )
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            return self._unknown(f'git 정보를 읽지 못했습니다 · {exc}')

        result: Dict[str, Any] = {
            'branch': branch,
            'current': current,
            'target': '',
            'behind': 0,
            'up_to_date': False,
            'dirty': dirty,
            'fetched': False,
            'blocked_reason': '',
            'message': '',
        }
        if branch != BRANCH:
            result['blocked_reason'] = (
                f'{BRANCH} 브랜치에서만 업데이트합니다 · 지금은 {branch}'
            )
        elif dirty:
            result['blocked_reason'] = '고친 파일이 있습니다 · 먼저 정리하세요'

        try:
            self._git('fetch', 'origin', BRANCH)
            target = self._git('rev-parse', f'origin/{BRANCH}')
            behind = int(
                self._git('rev-list', '--count', f'HEAD..origin/{BRANCH}') or 0
            )
            message = self._git('log', '-1', '--format=%s', f'origin/{BRANCH}')
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            result['message'] = f'원격을 확인하지 못했습니다 · {exc}'
            return result

        result.update({
            'target': target,
            'behind': behind,
            'up_to_date': behind == 0,
            'fetched': True,
            'message': message,
        })
        return result

    def _unknown(self, message: str) -> Dict[str, Any]:
        return {
            'branch': 'unknown', 'current': '', 'target': '', 'behind': 0,
            'up_to_date': False, 'dirty': False, 'fetched': False,
            'blocked_reason': message, 'message': message,
        }

    # ----------------------------------------------------------------- #
    # 시작
    # ----------------------------------------------------------------- #

    def start(self, *, blocked_reason: str = '') -> Dict[str, Any]:
        """업데이트를 띄운다 · 막을 이유가 하나라도 있으면 시작하지 않는다.

        `blocked_reason` 은 부르는 쪽이 넘긴다(모터가 도는 중 등) · 무엇이
        위험한지는 웹 브리지가 알고, 이 객체는 모른다.
        """
        if blocked_reason:
            raise ValueError(blocked_reason)
        running = self.status()
        if running.get('status') == 'running':
            raise ValueError('업데이트가 이미 진행 중입니다')

        state = self.check()
        if state['blocked_reason']:
            raise ValueError(state['blocked_reason'])
        if not state['fetched']:
            raise ValueError(state['message'] or '원격을 확인하지 못했습니다')
        if state['up_to_date']:
            raise ValueError('이미 최신입니다')

        operation_id = f'update-{int(time.time())}'
        script = (
            self.workspace
            / 'src/motion_web/web_bridge/deploy/update_workspace.sh'
        )
        if not script.is_file():
            raise ValueError(f'업데이트 스크립트가 없습니다 · {script}')

        # 화면이 곧바로 "시작했다"를 볼 수 있어야 한다 · 스크립트가 첫 상태를
        # 쓰기까지의 짧은 사이에 화면이 "가만히 있다"로 보이면 사용자가 다시
        # 누른다
        self._write_started(operation_id, state)
        completed = self._run(
            [
                'systemd-run', '--user', f'--unit={UNIT_NAME}', '--collect',
                f'--setenv=MOTION_WORKSPACE={self.workspace}',
                f'--setenv=MOTION_UPDATE_STATE_DIR={self.state_dir}',
                f'--setenv=MOTION_UPDATE_OPERATION_ID={operation_id}',
                '/bin/bash', str(script),
            ],
            capture_output=True, text=True, timeout=20,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or '').strip()
            self._write_state({
                'operation_id': operation_id, 'status': 'failure',
                'phase': 'spawn', 'message': f'작업을 띄우지 못했습니다 · {detail}',
                'from_commit': state['current'], 'to_commit': state['target'],
                'rolled_back': False, 'updated_at': time.time(),
            })
            raise RuntimeError(f'업데이트를 시작하지 못했습니다 · {detail}')
        return {'success': True, 'operation_id': operation_id, **state}

    # ----------------------------------------------------------------- #
    # 읽기
    # ----------------------------------------------------------------- #

    def status(self) -> Dict[str, Any]:
        """디스크에 적힌 진행 상황 · 한 번도 안 했으면 `idle`."""
        try:
            payload = json.loads(
                (self.state_dir / 'state.json').read_text(encoding='utf-8')
            )
        except (OSError, ValueError):
            return {'status': 'idle', 'phase': '', 'message': '', 'log_tail': ''}
        if not isinstance(payload, dict):
            return {'status': 'idle', 'phase': '', 'message': '', 'log_tail': ''}
        payload['log_tail'] = self._log_tail()
        return payload

    def _log_tail(self) -> str:
        try:
            lines = (
                (self.state_dir / 'update.log')
                .read_text(encoding='utf-8', errors='replace')
                .splitlines()
            )
        except OSError:
            return ''
        return '\n'.join(lines[-LOG_TAIL_LINES:])

    # ----------------------------------------------------------------- #
    # 내부
    # ----------------------------------------------------------------- #

    def _git(self, *args: str) -> str:
        completed = self._run(
            ['git', *args], cwd=str(self.workspace),
            capture_output=True, text=True, timeout=GIT_TIMEOUT_SEC,
        )
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or '').strip() or 'git 실패')
        return (completed.stdout or '').strip()

    def _write_started(self, operation_id: str, state: Dict[str, Any]) -> None:
        self._write_state({
            'operation_id': operation_id,
            'status': 'running',
            'phase': 'starting',
            'message': '업데이트를 시작합니다',
            'from_commit': state['current'],
            'to_commit': state['target'],
            'rolled_back': False,
            'updated_at': time.time(),
            'started_at': time.time(),
        })

    def _write_state(self, payload: Dict[str, Any]) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        path = self.state_dir / 'state.json'
        temporary = path.with_suffix('.json.tmp')
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False), encoding='utf-8'
        )
        os.replace(temporary, path)
