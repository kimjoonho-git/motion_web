import os
from pathlib import Path

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


WORKSPACE = Path(os.environ.get('MOTION_WORKSPACE', Path.cwd())).expanduser()
DEFAULT_CONFIG = str(WORKSPACE / 'config/bootstrap_motor_config.yaml')


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument(
            'config_file',
            default_value=DEFAULT_CONFIG,
            description='Absolute path to active motor YAML.',
        ),
        DeclareLaunchArgument('motion_state_topic', default_value='/motion_control/motion_state'),
        DeclareLaunchArgument('motor_status_topic', default_value='/motion_control/motor_status'),
        DeclareLaunchArgument('ethercat_status_topic', default_value='/ethercat_status'),
        DeclareLaunchArgument('publish_hz', default_value='10.0'),
        DeclareLaunchArgument('max_motors', default_value='50'),
        DeclareLaunchArgument(
            'max_jog_delta_deg',
            default_value='360.0',
            description='Maximum single jog move in degrees.',
        ),
        DeclareLaunchArgument(
            'start_motor_manager',
            default_value='false',
            description='Start motor_manager_node. false keeps servo drivers untouched.',
        ),
        DeclareLaunchArgument(
            'start_motion_supervisor',
            default_value='true',
            description='Start motion_supervisor for upper-level command publishing.',
        ),
        DeclareLaunchArgument('host', default_value='0.0.0.0'),
        DeclareLaunchArgument('port', default_value='8000'),
        DeclareLaunchArgument(
            'motion_projects_dir',
            default_value=str(WORKSPACE / 'motion_projects'),
            description='Root containing isolated integrated projects.',
        ),
        Node(
            package='motion_control_bridge',
            executable='motor_manager_node',
            name='motor_manager_node',
            output='screen',
            condition=IfCondition(LaunchConfiguration('start_motor_manager')),
            parameters=[{
                'config_file': LaunchConfiguration('config_file'),
            }],
        ),
        Node(
            package='motion_state_monitor',
            executable='motion_state_monitor',
            name='motion_state_monitor',
            output='screen',
            parameters=[{
                'input_topic': LaunchConfiguration('motor_status_topic'),
                'input_type': 'motor_status',
                'ethercat_status_topic': LaunchConfiguration('ethercat_status_topic'),
                'motor_config_file': LaunchConfiguration('config_file'),
                'output_topic': LaunchConfiguration('motion_state_topic'),
                'publish_hz': LaunchConfiguration('publish_hz'),
                'max_motors': LaunchConfiguration('max_motors'),
                'dynamixel_scan_id_fallback': True,
            }],
        ),
        Node(
            package='motion_supervisor',
            executable='motion_supervisor',
            name='motion_supervisor',
            output='screen',
            condition=IfCondition(LaunchConfiguration('start_motion_supervisor')),
            parameters=[{
                'motion_state_topic': LaunchConfiguration('motion_state_topic'),
                'jog_request_topic': '/motion_control/manual_jog_request',
                'jog_result_topic': '/motion_control/manual_jog_result',
                'action_request_topic': '/motion_control/manual_action_request',
                'action_result_topic': '/motion_control/manual_action_result',
                'motion_run_command_topic': '/motion_control/motion_run_command',
                'motor_command_topic': '/motion_control/motor_command',
                'midi_position_request_topic': '/motion_control/midi_position_request',
                'midi_position_result_topic': '/motion_control/midi_position_result',
                'max_jog_delta_deg': LaunchConfiguration('max_jog_delta_deg'),
                'config_file': LaunchConfiguration('config_file'),
            }],
        ),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(
                str(Path(__file__).with_name('project_services.launch.py'))
            ),
            launch_arguments={
                'config_file': LaunchConfiguration('config_file'),
                'motion_projects_dir': LaunchConfiguration('motion_projects_dir'),
                'motion_state_topic': LaunchConfiguration('motion_state_topic'),
                'publish_hz': LaunchConfiguration('publish_hz'),
                'max_jog_delta_deg': LaunchConfiguration('max_jog_delta_deg'),
                'host': LaunchConfiguration('host'),
                'port': LaunchConfiguration('port'),
                # The normal restart script launches midi_control.launch.py so
                # the physical MIDI input bridge and controller start together.
                'start_midi_control': 'false',
            }.items(),
        ),
    ])
