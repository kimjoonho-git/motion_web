import os
from pathlib import Path

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from motion_common import topics


DEFAULT_CONFIG = str(
    Path(os.environ.get('MOTION_WORKSPACE', Path.cwd())).expanduser()
    / 'config'
    / 'bootstrap_motor_config.yaml'
)


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument(
            'config_file',
            default_value=DEFAULT_CONFIG,
            description='Absolute path to active motor YAML.',
        ),
        DeclareLaunchArgument('motion_state_topic', default_value=topics.MOTION_STATE),
        DeclareLaunchArgument('jog_request_topic', default_value=topics.MANUAL_JOG_REQUEST),
        DeclareLaunchArgument('jog_result_topic', default_value=topics.MANUAL_JOG_RESULT),
        DeclareLaunchArgument('safety_request_topic', default_value=topics.SAFETY_REQUEST),
        DeclareLaunchArgument('action_request_topic', default_value=topics.MANUAL_ACTION_REQUEST),
        DeclareLaunchArgument('action_result_topic', default_value=topics.MANUAL_ACTION_RESULT),
        DeclareLaunchArgument(
            'motion_run_command_topic',
            default_value=topics.MOTION_RUN_COMMAND,
        ),
        DeclareLaunchArgument('motor_command_topic', default_value=topics.MOTOR_COMMAND),
        DeclareLaunchArgument(
            'midi_position_request_topic',
            default_value=topics.MIDI_POSITION_REQUEST,
        ),
        DeclareLaunchArgument(
            'midi_position_result_topic',
            default_value=topics.MIDI_POSITION_RESULT,
        ),
        Node(
            package='motion_supervisor',
            executable='motion_supervisor',
            name='motion_supervisor',
            output='screen',
            parameters=[{
                'motion_state_topic': LaunchConfiguration('motion_state_topic'),
                'jog_request_topic': LaunchConfiguration('jog_request_topic'),
                'jog_result_topic': LaunchConfiguration('jog_result_topic'),
                'safety_request_topic': LaunchConfiguration('safety_request_topic'),
                'action_request_topic': LaunchConfiguration('action_request_topic'),
                'action_result_topic': LaunchConfiguration('action_result_topic'),
                'motion_run_command_topic': LaunchConfiguration('motion_run_command_topic'),
                'motor_command_topic': LaunchConfiguration('motor_command_topic'),
                'midi_position_request_topic': LaunchConfiguration('midi_position_request_topic'),
                'midi_position_result_topic': LaunchConfiguration('midi_position_result_topic'),
                'config_file': LaunchConfiguration('config_file'),
            }],
        ),
    ])
