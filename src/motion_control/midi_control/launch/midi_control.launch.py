import os
from pathlib import Path

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    workspace = Path(os.environ.get('MOTION_WORKSPACE', Path.cwd())).expanduser()
    return LaunchDescription([
        DeclareLaunchArgument(
            'motor_config_file',
            default_value=str(workspace / 'config/bootstrap_motor_config.yaml'),
            description='Legacy compatibility argument; MIDI banks use the selected mapping YAML.',
        ),
        DeclareLaunchArgument(
            'motion_projects_dir',
            default_value=str(workspace / 'motion_projects'),
        ),
        Node(
            package='midi_input_bridge',
            executable='midi_input_node',
            name='midi_input_node',
            output='screen',
            parameters=[{
                'midi_topic': '/xtouch/midi',
                'feedback_topic': '/xtouch/feedback',
                'input_state_topic': '/xtouch/input_state',
                'connection_command_topic': '/xtouch/connection/command',
                'connection_state_topic': '/xtouch/connection/state',
                'publish_period_ms': 5,
                # Keep manual-control state latched through brief pauses and
                # noisy capacitive touch-OFF events.
                'movement_release_delay_ms': 300,
                # X-Touch does not echo host-driven fader positions. Treat a
                # command as settled only after this quiet interval; any real
                # touch or pitch-bend input cancels the estimate.
                'fader_command_settle_ms': 1000,
                # Send the final hand position once on release. The input
                # bridge suppresses the motor-driven MIDI echo.
                'hold_fader_on_release': True,
            }],
        ),
        Node(
            package='midi_control',
            executable='midi_control_node',
            name='midi_control_node',
            output='screen',
            parameters=[{
                'input_topic': '/xtouch/midi',
                'state_topic': '/motion_web/midi_monitor/state',
                'request_topic': '/motion_web/midi_monitor/request',
                'response_topic': '/motion_web/midi_monitor/response',
                'feedback_topic': '/xtouch/feedback',
                'input_state_topic': '/xtouch/input_state',
                'connection_command_topic': '/xtouch/connection/command',
                'connection_state_topic': '/xtouch/connection/state',
                'motion_state_topic': '/motion_control/motion_state',
                'motion_run_status_topic': '/motion_control/motion_run_status',
                'motion_mapping_response_topic': '/motion_control/motion_mapping_response',
                'motor_request_topic': '/motion_control/midi_position_request',
                'motor_result_topic': '/motion_control/midi_position_result',
                'motion_projects_dir': LaunchConfiguration('motion_projects_dir'),
                'publish_hz': 50.0,
                'stale_timeout_sec': 0.5,
            }],
        ),
    ])
