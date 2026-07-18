"""Read the currently relevant motion-axis mapping without owning motion runtime."""

from pathlib import Path
from typing import Any, Dict, Optional

import yaml


class MotionAxisRegistry:
    """Resolve Motion IDs to configured motor axes from mapping YAML files.

    A mapping selected by motion runtime takes precedence. Before a run/check
    selects one, the newest mapping YAML is used, matching the web UI's default
    ordering.
    """

    def __init__(self, mappings_dir: Path) -> None:
        self.mappings_dir = Path(mappings_dir).expanduser()
        self.file_id = ''
        self.axes: Dict[str, int] = {}
        self.rows: Dict[str, Dict[str, Any]] = {}

    def refresh(
        self,
        preferred_file_id: Any = '',
        motion_state: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, int]:
        path = self._mapping_path(preferred_file_id)
        if path is None:
            self.file_id = ''
            self.axes = {}
            self.rows = {}
            return {}
        try:
            root = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
        except (OSError, yaml.YAMLError):
            self.file_id = path.name
            self.axes = {}
            self.rows = {}
            return {}
        rows = root.get('mappings') if isinstance(root, dict) else None
        axes: Dict[str, int] = {}
        mapped_rows: Dict[str, Dict[str, Any]] = {}
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict) or row.get('enabled') is False:
                    continue
                motion_id = str(row.get('motion_id') or '').strip()
                motor_ref = str(row.get('motor_ref') or '').strip()
                motor_axis = self._axis_for_ref(motor_ref, motion_state)
                if motor_axis is None and not motor_ref:
                    try:
                        motor_axis = int(row.get('motor_axis'))
                    except (TypeError, ValueError):
                        continue
                if motion_id and motor_axis is not None and motor_axis >= 0:
                    axes[motion_id] = motor_axis
                    mapped_rows[motion_id] = dict(row)
        self.file_id = path.name
        self.axes = axes
        self.rows = mapped_rows
        return dict(axes)

    @classmethod
    def _axis_for_ref(
        cls,
        motor_ref: str,
        motion_state: Optional[Dict[str, Any]],
    ) -> Optional[int]:
        if not motor_ref or not isinstance(motion_state, dict):
            return None
        motors = motion_state.get('motors')
        if not isinstance(motors, list):
            return None
        matches = [
            motor for motor in motors
            if isinstance(motor, dict)
            and cls._motor_ref(motor).lower() == motor_ref.lower()
        ]
        if len(matches) != 1:
            return None
        try:
            return int(matches[0].get('controller_index'))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _motor_ref(motor: Dict[str, Any]) -> str:
        text = ' '.join(str(motor.get(key) or '').lower() for key in (
            'motor_type', 'motor_type_label', 'driver_model', 'transport'
        ))
        if 'dynamixel' in text:
            value = motor.get('bus_id', motor.get('node_id'))
            try:
                return f'dynamixel:id:{int(value)}'
            except (TypeError, ValueError):
                return ''
        if 'minas' in text or 'ac servo' in text or 'ac_servo' in text:
            value = motor.get('alias', motor.get('ethercat_alias'))
            try:
                return f'ac_servo:alias:{int(value)}'
            except (TypeError, ValueError):
                return ''
        return ''

    def motor_axis(self, motion_id: Any) -> Optional[int]:
        return self.axes.get(str(motion_id or '').strip())

    def mapping(self, motion_id: Any) -> Optional[Dict[str, Any]]:
        row = self.rows.get(str(motion_id or '').strip())
        return dict(row) if row is not None else None

    def _mapping_path(self, preferred_file_id: Any) -> Optional[Path]:
        preferred = str(preferred_file_id or '').strip()
        if preferred and preferred == Path(preferred).name:
            path = self.mappings_dir / preferred
            if path.is_file() and path.suffix.lower() in ('.yaml', '.yml'):
                return path
        try:
            candidates = [
                path for path in self.mappings_dir.iterdir()
                if path.is_file() and path.suffix.lower() in ('.yaml', '.yml')
            ]
        except OSError:
            return None
        return max(candidates, key=lambda path: path.stat().st_mtime, default=None)
