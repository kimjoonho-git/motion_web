"""Restart project-owned services without touching motor driver processes."""

import os
from pathlib import Path

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


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
        DeclareLaunchArgument('motion_state_topic', default_value='/motion_control/motion_state'),
        DeclareLaunchArgument(
            'safety_request_topic',
            default_value='/motion_control/safety_request',
        ),
        DeclareLaunchArgument('publish_hz', default_value='10.0'),
        DeclareLaunchArgument('max_jog_delta_deg', default_value='360.0'),
        DeclareLaunchArgument('start_midi_control', default_value='true'),
        DeclareLaunchArgument(
            'motion_studio_request_topic',
            default_value='/motion_studio/request',
        ),
        DeclareLaunchArgument(
            'motion_studio_response_topic',
            default_value='/motion_studio/response',
        ),
        DeclareLaunchArgument(
            'motion_studio_editor_request_topic',
            default_value='/motion_studio/editor/request',
        ),
        DeclareLaunchArgument(
            'motion_studio_editor_response_topic',
            default_value='/motion_studio/editor/response',
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
                'input_topic': '/xtouch/midi',
                'state_topic': '/motion_web/midi_monitor/state',
                'request_topic': '/motion_web/midi_monitor/request',
                'response_topic': '/motion_web/midi_monitor/response',
                'feedback_topic': '/xtouch/feedback',
                'input_state_topic': '/xtouch/input_state',
                'connection_command_topic': '/xtouch/connection/command',
                'connection_state_topic': '/xtouch/connection/state',
                'motion_state_topic': LaunchConfiguration('motion_state_topic'),
                'motion_run_status_topic': '/motion_control/motion_run_status',
                'motion_mapping_response_topic': '/motion_control/motion_mapping_response',
                'motor_request_topic': '/motion_control/midi_position_request',
                'motor_result_topic': '/motion_control/midi_position_result',
                'motion_projects_dir': LaunchConfiguration('motion_projects_dir'),
                'publish_hz': 50.0,
                'stale_timeout_sec': 0.5,
            }],
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
