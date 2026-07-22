from motion_web_bridge.bridge_node import add_monitoring_motion_values


def test_monitoring_uses_received_control_motion_value_not_motor_position():
    state = {
        'motors': [{
            'controller_index': 1,
            'position_deg': 9999.0,
        }],
    }
    rows = [{'motion_id': '2-1', 'motor_axis': 1, 'gear_ratio': 100.0}]
    topic_state = {
        'values': {'2-1': 2.125},
        'sources': {'2-1': 'midi'},
    }

    add_monitoring_motion_values(state, rows, topic_state)

    motor = state['motors'][0]
    assert motor['motion_axis_configured'] is True
    assert motor['motion_id'] == '2-1'
    assert motor['motion_value_deg'] == 2.125
    assert motor['motion_value_status'] == 'received'
    assert motor['motion_value_source'] == 'midi'


def test_monitoring_distinguishes_unmapped_from_motion_value_not_received():
    state = {
        'motors': [
            {'controller_index': 0, 'position_deg': 0.0},
            {'controller_index': 1, 'position_deg': 100.0},
        ],
    }
    rows = [{'motion_id': '2-1', 'motor_axis': 1}]

    add_monitoring_motion_values(state, rows, {})

    assert state['motors'][0]['motion_value_status'] == 'unmapped'
    assert state['motors'][0]['motion_value_message'] == '모션축 미설정'
    assert state['motors'][1]['motion_value_status'] == 'missing'
    assert state['motors'][1]['motion_value_message'] == '모션값 토픽 미수신'
    assert state['motors'][1]['motion_value_deg'] is None


def test_monitoring_resolves_mapping_motor_ref_for_topic_value():
    state = {
        'motors': [{
            'controller_index': 4,
            'motor_type': 'ac_servo',
            'alias': 104,
            'position_deg': -500.0,
        }],
    }
    rows = [{'motion_id': '3-1', 'motor_ref': 'ac_servo:alias:104'}]

    add_monitoring_motion_values(
        state,
        rows,
        {'values': {'3-1': -7.5}, 'sources': {'3-1': 'motion_run'}},
    )

    motor = state['motors'][0]
    assert motor['motion_value_deg'] == -7.5
    assert motor['motion_value_message'] == '모션 실행 제어 모션값 수신'


def test_monitoring_does_not_calculate_when_topic_value_is_invalid():
    state = {'motors': [{'controller_index': 2, 'position_deg': 20.0}]}
    rows = [{'motion_id': '1-1', 'motor_axis': 2}]

    add_monitoring_motion_values(state, rows, {'values': {'1-1': 'not-a-number'}})

    motor = state['motors'][0]
    assert motor['motion_value_status'] == 'missing'
    assert motor['motion_value_deg'] is None
