from .project_routes import register_project_routes
from .motor_routes import register_motor_routes
from .motion_run_routes import register_motion_run_routes
from .midi_routes import register_midi_routes
from .safety_routes import register_safety_routes
from .system_routes import register_system_routes

__all__ = [
    'register_project_routes',
    'register_motor_routes',
    'register_motion_run_routes',
    'register_midi_routes',
    'register_safety_routes',
    'register_system_routes',
]
