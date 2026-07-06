from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument('motion_state_topic', default_value='/motion_control/motion_state'),
        DeclareLaunchArgument('monitoring_service', default_value='/set_monitoring'),
        DeclareLaunchArgument('scan_service', default_value='/scan_motors'),
        DeclareLaunchArgument('scan_ac_servo_service', default_value='/scan_ac_servo_motors'),
        DeclareLaunchArgument('scan_dynamixel_service', default_value='/scan_dynamixel_motors'),
        DeclareLaunchArgument('host', default_value='0.0.0.0'),
        DeclareLaunchArgument('port', default_value='8000'),
        DeclareLaunchArgument('web_publish_hz', default_value='10.0'),
        DeclareLaunchArgument(
            'motor_config_file',
            default_value='/home/joonho_test/ros2_ws/config/active_motor_config.yaml',
        ),
        Node(
            package='motion_web_bridge',
            executable='motion_web_bridge',
            name='motion_web_bridge',
            output='screen',
            parameters=[{
                'motion_state_topic': LaunchConfiguration('motion_state_topic'),
                'monitoring_service': LaunchConfiguration('monitoring_service'),
                'scan_service': LaunchConfiguration('scan_service'),
                'scan_ac_servo_service': LaunchConfiguration('scan_ac_servo_service'),
                'scan_dynamixel_service': LaunchConfiguration('scan_dynamixel_service'),
                'host': LaunchConfiguration('host'),
                'port': LaunchConfiguration('port'),
                'web_publish_hz': LaunchConfiguration('web_publish_hz'),
                'motor_config_file': LaunchConfiguration('motor_config_file'),
            }],
        ),
    ])
