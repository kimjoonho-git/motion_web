from motion_web_bridge.bridge_node import motor_activity_snapshot


def test_automatic_motion_has_priority_over_playback_owner():
    result = motor_activity_snapshot(
        {'state': 'running', 'automation_run': True},
        {},
        {'command_owner': 'playback'},
    )

    assert result == {
        'active': True,
        'kind': 'automation',
        'label': '자동 반복 모션 동작 중',
        'source': 'motion_run',
        'warning': False,
    }


def test_repeat_dwell_is_not_reported_as_motor_activity():
    result = motor_activity_snapshot(
        {'state': 'waiting', 'phase': 'repeat_waiting'},
        {},
        {'command_owner': 'playback'},
    )

    assert result['active'] is False
    assert result['kind'] == 'repeat_waiting'


def test_runtime_countdown_is_not_reported_as_initial_position_motion():
    result = motor_activity_snapshot(
        {'state': 'countdown', 'phase': 'countdown'},
        {'state': 'initializing'},
        {'command_owner': 'playback'},
    )

    assert result['active'] is False
    assert result['kind'] == 'countdown'


def test_completed_initial_position_is_not_reported_as_moving():
    result = motor_activity_snapshot(
        {'state': 'initialized', 'phase': 'initialized'},
        {'state': 'initializing'},
        {'command_owner': 'playback'},
    )

    assert result['active'] is False
    assert result['kind'] == 'initialized'


def test_manual_activity_distinguishes_action_from_jog():
    action = motor_activity_snapshot(
        {}, {}, {
            'command_owner': 'manual',
            'manual_activity_modes': ['jog', 'action'],
        },
    )
    jog = motor_activity_snapshot(
        {}, {}, {
            'command_owner': 'manual',
            'manual_activity_modes': ['jog'],
        },
    )

    assert action['kind'] == 'action'
    assert jog['kind'] == 'jog'


def test_unmatched_active_owner_is_visible_as_warning():
    result = motor_activity_snapshot(
        {}, {}, {'command_owner': 'playback'},
    )

    assert result['active'] is True
    assert result['warning'] is True
    assert result['kind'] == 'unknown'
