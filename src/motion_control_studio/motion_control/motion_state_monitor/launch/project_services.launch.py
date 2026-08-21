"""Restart project-owned services without touching motor driver processes."""

import os
from pathlib import Path

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from motion_common import topics


WORKSPACE = Path(os.environ.get('MOTION_WORKSPACE', Path.cwd())).expanduser()


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument(
            'config_file',
            default_value=str(WORKSPACE / 'config' / 'bootstrap_motor_config.yaml'),
        ),
        DeclareLaunchArgument(
            'motion_projects_dir',
            default_value=str(WORKSPACE / 'motion_projects'),
        ),
        DeclareLaunchArgument('host', default_value='0.0.0.0'),
        DeclareLaunchArgument('port', default_value='8000'),
        DeclareLaunchArgument('motion_state_topic', default_value=topics.MOTION_STATE),
        DeclareLaunchArgument(
            'safety_request_topic',
            default_value=topics.SAFETY_REQUEST,
        ),
        DeclareLaunchArgument('publish_hz', default_value='10.0'),
        DeclareLaunchArgument('max_jog_delta_deg', default_value='360.0'),
        DeclareLaunchArgument('start_midi_control', default_value='true'),
        DeclareLaunchArgument(
            'motion_studio_request_topic',
            default_value=topics.STUDIO_REQUEST,
        ),
        DeclareLaunchArgument(
            'motion_studio_response_topic',
            default_value=topics.STUDIO_RESPONSE,
        ),
        DeclareLaunchArgument(
            'motion_studio_editor_request_topic',
            default_value=topics.STUDIO_EDITOR_REQUEST,
        ),
        DeclareLaunchArgument(
            'motion_studio_editor_response_topic',
            default_value=topics.STUDIO_EDITOR_RESPONSE,
        ),
        Node(
            package='motion_runtime',
            executable='motion_mapping_manager',
            name='motion_mapping_manager',
            output='screen',
            parameters=[{
                'motion_state_topic': LaunchConfiguration('motion_state_topic'),
                'motion_projects_dir': LaunchConfiguration('motion_projects_dir'),
            }],
        ),
        Node(
            package='motion_runtime',
            executable='motion_run_manager',
            name='motion_run_manager',
            output='screen',
            parameters=[{
                'motion_projects_dir': LaunchConfiguration('motion_projects_dir'),
            }],
        ),
        Node(
            package='motion_studio',
            executable='motion_studio_node',
            name='motion_studio_node',
            output='screen',
            parameters=[{
                'motion_projects_dir': LaunchConfiguration('motion_projects_dir'),
                'request_topic': LaunchConfiguration('motion_studio_request_topic'),
                'response_topic': LaunchConfiguration('motion_studio_response_topic'),
            }],
        ),
        Node(
            package='motion_studio',
            executable='motion_studio_editor_node',
            name='motion_studio_editor_node',
            output='screen',
            parameters=[{
                'request_topic': LaunchConfiguration(
                    'motion_studio_editor_request_topic'
                ),
                'response_topic': LaunchConfiguration(
                    'motion_studio_editor_response_topic'
                ),
            }],
        ),
        Node(
            package='midi_control',
            executable='midi_control_node',
            name='midi_control_node',
            output='screen',
            condition=IfCondition(LaunchConfiguration('start_midi_control')),
            parameters=[{
                'input_topic': topics.XTOUCH_MIDI,
                'state_topic': topics.MIDI_MONITOR_STATE,
                'request_topic': topics.MIDI_MONITOR_REQUEST,
                'response_topic': topics.MIDI_MONITOR_RESPONSE,
                'feedback_topic': topics.XTOUCH_FEEDBACK,
                'input_state_topic': topics.XTOUCH_INPUT_STATE,
                'connection_command_topic': topics.XTOUCH_CONNECTION_COMMAND,
                'connection_state_topic': topics.XTOUCH_CONNECTION_STATE,
                'motion_state_topic': LaunchConfiguration('motion_state_topic'),
                'motion_run_status_topic': topics.MOTION_RUN_STATUS,
                'motion_mapping_response_topic': topics.MOTION_MAPPING_RESPONSE,
                'motor_request_topic': topics.MIDI_POSITION_REQUEST,
                'motor_result_topic': topics.MIDI_POSITION_RESULT,
                'motion_projects_dir': LaunchConfiguration('motion_projects_dir'),
                'publish_hz': 50.0,
                'stale_timeout_sec': 0.5,
            }],
        ),
        Node(
            package='motion_schedule',
            executable='motion_schedule_node',
            name='motion_schedule_node',
            output='screen',
        ),
        Node(
            package='motion_web_bridge',
            executable='motion_web_bridge',
            name='motion_web_bridge',
            output='screen',
            parameters=[{
                'motion_state_topic': LaunchConfiguration('motion_state_topic'),
                'safety_request_topic': LaunchConfiguration('safety_request_topic'),
                'max_jog_delta_deg': LaunchConfiguration('max_jog_delta_deg'),
                'host': LaunchConfiguration('host'),
                'port': LaunchConfiguration('port'),
                'web_publish_hz': LaunchConfiguration('publish_hz'),
                'motor_config_file': LaunchConfiguration('config_file'),
                'motion_projects_dir': LaunchConfiguration('motion_projects_dir'),
                'motion_studio_request_topic': LaunchConfiguration(
                    'motion_studio_request_topic'
                ),
                'motion_studio_response_topic': LaunchConfiguration(
                    'motion_studio_response_topic'
                ),
                'motion_studio_editor_request_topic': LaunchConfiguration(
                    'motion_studio_editor_request_topic'
                ),
                'motion_studio_editor_response_topic': LaunchConfiguration(
                    'motion_studio_editor_response_topic'
                ),
            }],
        ),
    ])
