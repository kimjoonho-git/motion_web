"""Read MIDI-bank settings from the file owned by motion_mapping_manager."""

from pathlib import Path
from typing import Any, Dict, Optional

import yaml


def load_midi_banks(mapping_file: Path) -> Optional[Dict[str, Any]]:
    if not mapping_file.is_file():
        return None
    root = yaml.safe_load(mapping_file.read_text(encoding='utf-8')) or {}
    if not isinstance(root, dict):
        raise ValueError('motion-axis mapping YAML root must be an object')
    section = root.get('midi_banks')
    if section is None:
        return None
    if not isinstance(section, dict):
        raise ValueError('midi_banks must be an object')
    return section
