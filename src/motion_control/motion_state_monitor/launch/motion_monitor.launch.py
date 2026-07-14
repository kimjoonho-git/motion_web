from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


DEFAULT_CONFIG = '/home/joonho_test/ros2_ws/config/active_motor_config.yaml'


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
            'motion_data_dir',
            default_value='/home/joonho_test/ros2_ws/motion_data',
            description='Directory for uploaded motion files and motion axis mapping YAML files.',
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
                'max_jog_delta_deg': LaunchConfiguration('max_jog_delta_deg'),
                'config_file': LaunchConfiguration('config_file'),
            }],
        ),
        Node(
            package='motion_runtime',
            executable='motion_mapping_manager',
            name='motion_mapping_manager',
            output='screen',
            parameters=[{
                'motion_data_dir': LaunchConfiguration('motion_data_dir'),
                'request_topic': '/motion_control/motion_mapping_request',
                'response_topic': '/motion_control/motion_mapping_response',
            }],
        ),
        Node(
            package='motion_runtime',
            executable='motion_run_manager',
            name='motion_run_manager',
            output='screen',
            parameters=[{
                'motion_state_topic': LaunchConfiguration('motion_state_topic'),
                'motor_command_topic': '/motion_control/motion_run_command',
                'motion_data_dir': LaunchConfiguration('motion_data_dir'),
                'config_file': LaunchConfiguration('config_file'),
                'request_topic': '/motion_control/motion_run_request',
                'response_topic': '/motion_control/motion_run_response',
                'status_topic': '/motion_control/motion_run_status',
            }],
        ),
        Node(
            package='motion_web_bridge',
            executable='motion_web_bridge',
            name='motion_web_bridge',
            output='screen',
            parameters=[{
                'motion_state_topic': LaunchConfiguration('motion_state_topic'),
                'monitoring_service': '/set_monitoring',
                'scan_service': '/scan_motors',
                'scan_ac_servo_service': '/scan_ac_servo_motors',
                'scan_dynamixel_service': '/scan_dynamixel_motors',
                'jog_request_topic': '/motion_control/manual_jog_request',
                'jog_result_topic': '/motion_control/manual_jog_result',
                'action_request_topic': '/motion_control/manual_action_request',
                'action_result_topic': '/motion_control/manual_action_result',
                'max_jog_delta_deg': LaunchConfiguration('max_jog_delta_deg'),
                'host': LaunchConfiguration('host'),
                'port': LaunchConfiguration('port'),
                'web_publish_hz': LaunchConfiguration('publish_hz'),
                'motor_config_file': LaunchConfiguration('config_file'),
                'motion_data_dir': LaunchConfiguration('motion_data_dir'),
                'motion_mapping_request_topic': '/motion_control/motion_mapping_request',
                'motion_mapping_response_topic': '/motion_control/motion_mapping_response',
                'motion_run_request_topic': '/motion_control/motion_run_request',
                'motion_run_response_topic': '/motion_control/motion_run_response',
                'motion_run_status_topic': '/motion_control/motion_run_status',
            }],
        ),
    ])
