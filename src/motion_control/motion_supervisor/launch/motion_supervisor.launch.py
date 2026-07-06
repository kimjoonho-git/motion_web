from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
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
        DeclareLaunchArgument('jog_request_topic', default_value='/motion_control/manual_jog_request'),
        DeclareLaunchArgument('jog_result_topic', default_value='/motion_control/manual_jog_result'),
        DeclareLaunchArgument('action_request_topic', default_value='/motion_control/manual_action_request'),
        DeclareLaunchArgument('action_result_topic', default_value='/motion_control/manual_action_result'),
        DeclareLaunchArgument('motor_command_topic', default_value='/motion_control/motor_command'),
        Node(
            package='motion_supervisor',
            executable='motion_supervisor',
            name='motion_supervisor',
            output='screen',
            parameters=[{
                'motion_state_topic': LaunchConfiguration('motion_state_topic'),
                'jog_request_topic': LaunchConfiguration('jog_request_topic'),
                'jog_result_topic': LaunchConfiguration('jog_result_topic'),
                'action_request_topic': LaunchConfiguration('action_request_topic'),
                'action_result_topic': LaunchConfiguration('action_result_topic'),
                'motor_command_topic': LaunchConfiguration('motor_command_topic'),
                'config_file': LaunchConfiguration('config_file'),
            }],
        ),
    ])
