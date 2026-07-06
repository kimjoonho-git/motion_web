from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument('input_topic', default_value='/motion_control/motor_status'),
        DeclareLaunchArgument('input_type', default_value='motor_status'),
        DeclareLaunchArgument('ethercat_status_topic', default_value='/ethercat_status'),
        DeclareLaunchArgument(
            'motor_config_file',
            default_value='/home/joonho_test/ros2_ws/config/active_motor_config.yaml',
        ),
        DeclareLaunchArgument('output_topic', default_value='/motion_control/motion_state'),
        DeclareLaunchArgument('publish_hz', default_value='10.0'),
        DeclareLaunchArgument('max_motors', default_value='50'),
        Node(
            package='motion_state_monitor',
            executable='motion_state_monitor',
            name='motion_state_monitor',
            output='screen',
            parameters=[{
                'input_topic': LaunchConfiguration('input_topic'),
                'input_type': LaunchConfiguration('input_type'),
                'ethercat_status_topic': LaunchConfiguration('ethercat_status_topic'),
                'motor_config_file': LaunchConfiguration('motor_config_file'),
                'output_topic': LaunchConfiguration('output_topic'),
                'publish_hz': LaunchConfiguration('publish_hz'),
                'max_motors': LaunchConfiguration('max_motors'),
                'dynamixel_scan_id_fallback': True,
            }],
        ),
    ])
