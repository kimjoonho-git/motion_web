from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument(
            'motor_config_file',
            default_value='/home/joonho_test/ros2_ws/config/active_motor_config.yaml',
            description='Legacy compatibility argument; MIDI banks use the selected mapping YAML.',
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
                'motion_data_dir': '/home/joonho_test/ros2_ws/motion_data',
                'publish_hz': 20.0,
                'stale_timeout_sec': 0.5,
            }],
        ),
    ])
