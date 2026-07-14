from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        Node(
            package='midi_input_bridge',
            executable='midi_input_node',
            name='midi_input_node',
            output='screen',
            parameters=[{
                'midi_topic': '/xtouch/midi',
                'publish_period_ms': 5,
                # Keep manual-control state latched through brief pauses and
                # noisy capacitive touch-OFF events.
                'movement_release_delay_ms': 300,
                # Do not output while touched. Send the final physical value
                # once on release so the fader holds without hand resistance.
                # This does not publish any robot/servo motor command.
                'hold_fader_on_release': True,
            }],
        ),
        Node(
            package='motion_web_bridge',
            executable='midi_monitor_node',
            name='midi_monitor_node',
            output='screen',
            parameters=[{
                'input_topic': '/xtouch/midi',
                'state_topic': '/motion_web/midi_monitor/state',
                'request_topic': '/motion_web/midi_monitor/request',
                'response_topic': '/motion_web/midi_monitor/response',
                'mapping_file': '/home/joonho_test/ros2_ws/motion_data/midi_mappings/default.json',
                'publish_hz': 10.0,
                'stale_timeout_sec': 0.5,
            }],
        ),
    ])
