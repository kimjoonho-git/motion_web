import os
from pathlib import Path

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from motion_common import topics


def generate_launch_description():
    workspace = Path(os.environ.get('MOTION_WORKSPACE', Path.cwd())).expanduser()
    return LaunchDescription([
        DeclareLaunchArgument(
            'motion_projects_dir',
            default_value=str(workspace / 'motion_projects'),
        ),
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
    ])
