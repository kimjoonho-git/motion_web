"""프로젝트 생성·전환·삭제와 프로젝트 파일 조작.

`MotionWebBridge`에서 떼어냈다 · §5 분해 목표안의 `ProjectService` · §6-23
이것으로 §5가 적어둔 서비스 6개가 모두 섰다.

노드에 남긴 것 · 프로젝트 **세대 번호**(`current_project_generation`) ·
세대는 노드 전역 개념이라 §6-20에서 남기기로 정했고 여기서도 같다.

**브리지 속살을 더는 만지지 않는다** · §6-183

전에는 이 파일이 브리지의 밑줄 붙은 칸 다섯을 직접 열었다 (43회).

    _execution_context        12회  ← 남의 자물쇠를 직접 잡았다 놓았다
    _motor_config             11회  ← 남의 칸에 직접 값을 적었다
    _current_project_generation 9회
    _ensure_project_mutation_allowed 7회  ← 브리지를 돌아 제자리로 왔다
    _advance_project_generation 3회

지금은 공개된 동사만 부른다.

    bridge.changing_project()            프로젝트가 바뀐다 (세대·무효화·자물쇠)
    bridge.select_motor_axes_file(path)  이 모터 축 파일을 쓴다
    bridge.mark_project_selected(id)     골랐지만 아직 적용 전이다
    bridge.reconcile_execution_context() 실행 컨텍스트를 맞춘다
    bridge.current_project_generation()  지금 몇 세대인가
    self.ensure_mutation_allowed(id)     제 검사는 제가 부른다
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any, Dict

import yaml

from motion_web_bridge import motion_studio_session, motor_config_rules


class ProjectService:
    def __init__(
        self,
        bridge: Any,
        *,
        repository: Any,
        motion_projects_dir: Path,
    ) -> None:
        self.bridge = bridge
        self.repository = repository
        self.motion_projects_dir = motion_projects_dir

    def ensure_selected(self, project_id: Any) -> None:
        if str(project_id or '') != self.repository.selected_project_id():
            raise ValueError('현재 선택한 프로젝트 파일만 사용할 수 있습니다')

    def ensure_change_allowed(self) -> None:
        blocker = self.change_blocker()
        if blocker:
            raise ValueError(blocker)

    def ensure_mutation_allowed(self, project_id: Any) -> None:
        """고른 프로젝트인가 · 지금 바꿔도 되나 · §6-183

        **제 검사는 제가 부른다** · 전에는 `bridge.ensure_project_mutation_allowed()`
        를 거쳤는데, 그 메서드가 하는 일은 `self._project` — 곧 이 객체 — 의
        같은 두 메서드를 부르는 것이었다 · 일곱 자리에서 브리지를 한 바퀴 돌아
        제자리로 왔다.

        돌아오는 길이 있으면 「누가 검사의 주인인가」가 흐려진다 · 브리지에
        있는 것처럼 보이지만 실은 여기 있다.
        """
        self.ensure_selected(project_id)
        self.ensure_change_allowed()

    def change_blocker(
        self,
        *,
        ignore_motor_lifecycle: bool = False,
        allow_run_stopping: bool = False,
        allow_studio_stopping: bool = False,
    ) -> str:
        lifecycle_lock = getattr(self.bridge, '_motor_lifecycle_lock', None)
        if (
            not ignore_motor_lifecycle
            and lifecycle_lock is not None
            and lifecycle_lock.locked()
        ):
            return '모터 설정·검색·재시작 작업이 진행 중이므로 프로젝트를 변경할 수 없습니다'
        repository = getattr(self, 'repository', None)
        if (
            not ignore_motor_lifecycle
            and repository is not None
            and hasattr(getattr(repository, 'runtime', None), 'motor_operation_status')
        ):
            operation = repository.runtime.motor_operation_status()
            if operation.get('status') == 'running':
                return '모터 설정·검색·재시작 작업이 진행 중이므로 프로젝트를 변경할 수 없습니다'
        run_lock = getattr(self.bridge, '_motion_run_lock', None)
        if run_lock is None:
            run_status = getattr(self.bridge, '_motion_run_status', {})
        else:
            with run_lock:
                run_status = dict(getattr(self.bridge, '_motion_run_status', {}) or {})
        studio_session = motion_studio_session.session_of(self.bridge)
        studio_status = (
            studio_session.snapshot_status() if studio_session is not None else {}
        )
        run_state = str((run_status or {}).get('state') or 'idle')
        studio_state = str((studio_status or {}).get('state') or 'idle')
        blocked_run_states = {
            'initializing',
            'initialized',
            'running',
            'waiting',
            'verifying',
            'stopping',
        }
        if allow_run_stopping:
            blocked_run_states.discard('stopping')
        if run_state in blocked_run_states:
            return f'모션 동작 상태가 {run_state}이므로 프로젝트를 변경할 수 없습니다'
        blocked_studio_states = {
            'initializing', 'countdown', 'recording', 'playing', 'stopping',
        }
        if allow_studio_stopping:
            blocked_studio_states.discard('stopping')
        if studio_state in blocked_studio_states:
            return f'모션 스튜디오 상태가 {studio_state}이므로 프로젝트를 변경할 수 없습니다'
        return ''

    def payload_matches_selected(
        self, payload: Any, *, require_generation: bool = True
    ) -> bool:
        """Reject status belonging to any project other than the selected one."""
        if not isinstance(payload, dict):
            return False
        nested = payload.get('execution_context')
        if not isinstance(nested, dict):
            nested = {}
        project_id = str(
            payload.get('project_id')
            or payload.get('workspace_project_id')
            or nested.get('project_id')
            or ''
        ).strip()
        selected = self.repository.selected_project_id()
        generation = payload.get('project_generation')
        if generation is None:
            generation = nested.get('project_generation')
        if not require_generation:
            generation_matches = True
        else:
            try:
                generation_matches = int(generation) == self.bridge.current_project_generation()
            except (TypeError, ValueError):
                generation_matches = False
        return bool(
            selected and project_id and project_id == selected and generation_matches
        )

    def runtime_project_id(self) -> str:
        project_id = self.runtime_project_id_from_path()
        if not project_id:
            return ''
        try:
            self.repository.get_project(project_id)
        except (OSError, ValueError, json.JSONDecodeError):
            return ''
        return project_id

    def runtime_project_id_from_path(self) -> str:
        """지금 모터에 적용된 설정이 **어느 프로젝트 것인가** · §6-197

        적용된 모터축 파일의 경로에서 읽는다 · 프로젝트 YAML 을 파싱하지
        않으므로 상태 조회처럼 자주 부르는 길에서 쓴다.

        **인자를 받지 않는다** · 전에는 `selected_project_id` 를 받았는데
        본문에서 쓰지 않았다 · 두 곳이 그 값을 넘기면서 「이 프로젝트 기준으로
        본다」고 믿고 있었다 · 답은 고른 프로젝트와 무관하다.
        """
        try:
            relative = self.bridge.applied_motor_axes_file().relative_to(
                self.motion_projects_dir.resolve()
            )
        except (AttributeError, ValueError):
            return ''
        parts = relative.parts
        if len(parts) < 2:
            return ''
        project_id = str(parts[0])
        return project_id

    def selected_owns_runtime(self) -> bool:
        selected = self.repository.selected_project_id()
        return bool(
            selected
            and selected == self.runtime_project_id_from_path()
        )

    def bind_selected_sources(self) -> None:
        project_id = self.repository.selected_project_id()
        if not project_id:
            self.bridge.select_motor_axes_file()
            return
        try:
            detail = self.repository.get_project(project_id)
            active = detail.get('project', {}).get('active_files') or {}
            motor_name = str(active.get('motor_axes') or '')
            if motor_name:
                self.bridge.select_motor_axes_file(self.repository.export_path(
                    project_id, 'motor_axes', motor_name
                ))
            else:
                self.bridge.select_motor_axes_file()
        except (OSError, ValueError, json.JSONDecodeError):
            self.bridge.select_motor_axes_file()
            return

    def sync_file(
        self, result: Dict[str, Any], category: str, path: Path
    ) -> Dict[str, Any]:
        repository = getattr(self, 'repository', None)
        if repository is None:
            return result
        try:
            sync = repository.sync_project_file(category, path)
        except (OSError, UnicodeDecodeError, ValueError, yaml.YAMLError) as exc:
            result['project_sync_warning'] = str(exc)
            return result
        result['project_sync'] = sync
        return result

    def clear_scoped_memory(self) -> None:
        """옛 프로젝트의 기억을 버리라고 알린다 · §6-170

        **무엇을 어떻게 버릴지는 가진 쪽이 안다** · 여기서는 알 필요가 없다 ·
        전에는 이 메서드가 브리지의 13가지 속살을 직접 만졌고, 그래서
        프로젝트를 건드릴 때마다 MIDI·모터·스튜디오가 딸려 왔다.
        """
        self.bridge.forget_project_memory()

    def initialize_selected_context(self) -> None:
        self.bridge.reconcile_execution_context()

    def list_projects(self) -> Dict[str, Any]:
        result = self.repository.list_projects()
        result['project_generation'] = self.bridge.current_project_generation()
        runtime_project_id = self.runtime_project_id()
        result['runtime_project_id'] = runtime_project_id
        for project in result.get('projects') or []:
            project['runtime_active'] = project.get('project_id') == runtime_project_id
        return result

    def load_project(self, project_id: Any) -> Dict[str, Any]:
        result = self.repository.get_project(project_id)
        result['project_generation'] = self.bridge.current_project_generation()
        return result

    def create_project(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.ensure_change_allowed()
        previous_generation = self.bridge.current_project_generation()
        with self.bridge.changing_project():
            created = self.repository.create_project(payload.get('name'))
        result = self.select_project(created['project']['project_id'])
        result['previous_project_generation'] = previous_generation
        result['project_generation'] = self.bridge.current_project_generation()
        return result

    def update_project(self, project_id: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.ensure_selected(project_id)
        return self.repository.update_project_memo(project_id, payload.get('memo'))

    def select_project(self, project_id: Any) -> Dict[str, Any]:
        previous_generation = self.bridge.current_project_generation()
        changing_project = (
            str(project_id or '') != self.repository.selected_project_id()
        )
        # 같은 프로젝트를 다시 고르는 것은 「바뀜」이 아니다 · 세대를 올리면
        # 화면들이 통째로 새로고침된다 · §6-183
        with contextlib.ExitStack() as stack:
            if changing_project:
                self.ensure_change_allowed()
                stack.enter_context(self.bridge.changing_project())
            result = self.repository.select_project(project_id)
            result['previous_project_generation'] = previous_generation
            result['project_generation'] = self.bridge.current_project_generation()
            active = result.get('project', {}).get('active_files') or {}
            motor_name = str(active.get('motor_axes') or '')
            self.bridge.select_motor_axes_file(
                self.repository.export_path(project_id, 'motor_axes', motor_name)
                if motor_name else None
            )
            self.bridge.mark_project_selected(project_id)
        policy_result = self.bridge.publish_servo_alarm_policy()
        if policy_result.get('success') is not True:
            raise ValueError(
                '선택 프로젝트의 서보 에러 정책을 적용하지 못했습니다: '
                f'{policy_result.get("message") or "응답 없음"}'
            )
        result['execution_context'] = self.bridge.reconcile_execution_context()
        return result

    def delete_project(self, project_id: Any) -> Dict[str, Any]:
        self.ensure_mutation_allowed(project_id)
        if str(project_id or '') == self.runtime_project_id():
            raise ValueError(
                '현재 모터에 적용된 프로젝트는 삭제할 수 없습니다. '
                '「전체 동작 정지」 후 「실행 적용 해제」를 실행하거나, '
                '다른 프로젝트를 적용한 뒤 삭제하세요'
            )
        previous_generation = self.bridge.current_project_generation()
        with self.bridge.changing_project():
            result = self.repository.delete_project(project_id)
        result['previous_project_generation'] = previous_generation
        result['project_generation'] = self.bridge.current_project_generation()
        if not self.repository.selected_project_id():
            self.bridge.select_motor_axes_file()
        return result

    def load_file(
        self, project_id: Any, category: Any, file_name: Any
    ) -> Dict[str, Any]:
        self.ensure_selected(project_id)
        return self.repository.read_file(project_id, category, file_name)

    def load_read_only_file(
        self, project_id: Any, relative_path: Any
    ) -> Dict[str, Any]:
        self.ensure_selected(project_id)
        return self.repository.read_read_only_file(project_id, relative_path)

    def download_file(
        self, project_id: Any, category: Any, file_name: Any
    ) -> Path:
        self.ensure_selected(project_id)
        return self.repository.export_path(project_id, category, file_name)

    def save_file(
        self, project_id: Any, category: Any, file_name: Any, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        self.ensure_mutation_allowed(project_id)
        return self.repository.save_file(
            project_id, category, file_name, payload.get('content')
        )

    def rename_file(
        self, project_id: Any, category: Any, file_name: Any, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        self.ensure_mutation_allowed(project_id)
        return self.repository.rename_file(
            project_id, category, file_name, payload.get('new_name')
        )

    def import_file(
        self, project_id: Any, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        self.ensure_mutation_allowed(project_id)
        # 밖에서 들어올 수 있는 것은 모션 파일 하나뿐이다.
        #
        # 모터축·모션축 설정은 그 PC 의 하드웨어 배선에 매인 값이라 옮기면
        # 꼬인다 · 레이어는 스튜디오가 제 프로젝트 안에서만 다룬다. 남의 PC
        # 값을 끌어오는 길을 열어 두면 화면에서 지워도 언젠가 다시 새어 든다.
        if str(payload.get('category') or '').strip() != 'motions':
            raise ValueError(
                '프로젝트로 가져올 수 있는 것은 모션 파일뿐입니다. '
                '모터축·모션축 설정은 이 PC 에서 직접 만드세요'
            )
        self._ensure_motion_import_target(project_id)
        return self.repository.import_text(
            project_id,
            payload.get('category'),
            payload.get('file_name'),
            payload.get('content'),
        )

    def _ensure_motion_import_target(self, project_id: Any) -> None:
        """모션축 설정이 없는 프로젝트는 모션 파일을 받지 않는다.

        모션 파일 혼자서는 아무것도 못 한다 · 재생 등록도, 실행도,
        스튜디오로 보내기도 모션축 설정을 먼저 요구한다. 넣어 봐야 파일만
        놓이고, 그 사이 `import_text`가 비어 있던 `active_files`에 그 파일을
        말없이 꽂는다. 문 앞에서 막는 편이 낫다.
        """
        summary = self.repository.get_project(project_id)['project']
        counts = summary.get('counts') if isinstance(summary.get('counts'), dict) else {}
        if not int(counts.get('motion_axis_matching') or 0):
            raise ValueError(
                '모션축 설정이 없는 프로젝트에는 모션 파일을 넣을 수 없습니다. '
                '모션축 설정을 먼저 만드세요'
            )

    def activate_file(
        self, project_id: Any, category: Any, file_name: Any
    ) -> Dict[str, Any]:
        self.ensure_mutation_allowed(project_id)
        # This only selects a file in project metadata.  It does not apply a
        # motor configuration or publish a motion command.
        result = self.repository.set_active(project_id, category, file_name)
        if str(category) in {'motor_axes', 'motion_axis_matching', 'motions'}:
            result['editor_link'] = self.open_file_for_editing(
                project_id, category, file_name
            )
        return result

    def delete_file(
        self, project_id: Any, category: Any, file_name: Any
    ) -> Dict[str, Any]:
        self.ensure_mutation_allowed(project_id)
        result = self.repository.delete_file(project_id, category, file_name)
        if self.repository.selected_project_id() == str(project_id):
            replacement = str(result.get('replacement_active_file') or '')
            if str(category) == 'motor_axes':
                if replacement:
                    self.open_file_for_editing(
                        project_id, category, replacement
                    )
                    motor_config_rules.write_motor_config_selection(
                        self.repository, self.bridge.selected_motor_axes_file()
                    )
                else:
                    self.bridge.select_motor_axes_file()
                    motor_config_rules.clear_motor_config_selection(self.repository)
        return result

    def open_file_for_editing(
        self, project_id: Any, category: Any, file_name: Any
    ) -> Dict[str, Any]:
        self.ensure_mutation_allowed(project_id)
        path = self.repository.export_path(project_id, category, file_name)
        category_text = str(category)
        if category_text == 'motor_axes':
            self.bridge.select_motor_axes_file(path)
            return {
                'success': True,
                'workspace': 'config',
                'category': category_text,
                'file_name': path.name,
                'message': '모터축 설정 편집기에 연결했습니다 · 설정 적용은 실행하지 않았습니다',
            }
        if category_text in {'motion_axis_matching', 'motions'}:
            return {
                'success': True,
                'workspace': 'project',
                'category': category_text,
                'motion_tab': 'mapping' if category_text == 'motion_axis_matching' else 'files',
                'file_name': path.name,
                'path': str(path),
                'message': '현재 프로젝트 파일을 기능 탭에서 직접 사용합니다 · 모션 실행은 시작하지 않았습니다',
            }
        return {
            'success': True,
            'workspace': 'studio',
            'category': category_text,
            'file_name': path.name,
            'message': '레이어는 왼쪽 프로젝트 파일에서 관리하고 모션 스튜디오에서 합성합니다',
        }
