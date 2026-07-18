from motion_web_bridge.bridge_node import MotionWebBridge


def test_shared_driver_profiles_are_cloned_per_ac_and_dynamixel_axis():
    config = {
        'masters': [
            {
                'id': 0,
                'type': 'ethercat',
                'slaves': [
                    {'controller_index': 0, 'driver_id': 0, 'alias': 101},
                    {'controller_index': 1, 'driver_id': 0, 'alias': 403},
                ],
            },
            {
                'id': 1,
                'type': 'serial',
                'slaves': [
                    {'controller_index': 2, 'driver_id': 1, 'bus_id': 3},
                    {'controller_index': 3, 'driver_id': 1, 'bus_id': 5},
                ],
            },
        ],
        'drivers': [
            {'id': 0, 'type': 'minas', 'lower': -27000.0, 'upper': 27000.0},
            {'id': 1, 'type': 'dynamixel', 'lower': -180.0, 'upper': 180.0},
        ],
    }

    expanded = MotionWebBridge._expand_shared_driver_profiles(config)
    slaves = [slave for master in expanded['masters'] for slave in master['slaves']]
    driver_ids = [slave['driver_id'] for slave in slaves]

    assert len(set(driver_ids)) == 4
    assert len(expanded['drivers']) == 4
    assert config['masters'][0]['slaves'][1]['driver_id'] == 0
    assert config['masters'][1]['slaves'][1]['driver_id'] == 1

    drivers = {driver['id']: driver for driver in expanded['drivers']}
    ac_first, ac_second, dx_first, dx_second = driver_ids
    drivers[ac_first]['lower'] = -90.0
    drivers[dx_first]['lower'] = -45.0

    assert drivers[ac_second]['lower'] == -27000.0
    assert drivers[dx_second]['lower'] == -180.0


def test_already_unique_driver_profiles_are_not_duplicated():
    config = {
        'masters': [{
            'id': 0,
            'type': 'ethercat',
            'slaves': [
                {'controller_index': 0, 'driver_id': 0},
                {'controller_index': 1, 'driver_id': 2},
            ],
        }],
        'drivers': [
            {'id': 0, 'type': 'minas', 'lower': -90.0},
            {'id': 2, 'type': 'minas', 'lower': -180.0},
        ],
    }

    expanded = MotionWebBridge._expand_shared_driver_profiles(config)

    assert expanded == config


def test_motor_model_defaults_match_verified_development_config(tmp_path):
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.workspace_root = tmp_path

    ac = bridge._default_motor_config()['drivers'][0]
    assert ac['lower'] == -36000.0
    assert ac['upper'] == 36000.0
    assert ac['speed'] == 2000000.0
    assert ac['acceleration'] == 180000.0
    assert ac['deceleration'] == 180000.0
    assert ac['profile_velocity'] == 18000.0
    assert ac['profile_acceleration'] == 180000.0
    assert ac['profile_deceleration'] == 180000.0

    w150 = bridge._default_dynamixel_driver('XM540-W150')
    assert w150['driver_model'] == 'XM540-W150'
    assert w150['rated_speed_rpm'] == 66
    assert w150['speed'] == 396.0
    assert w150['profile_velocity'] == 396.0
    assert w150['profile_acceleration'] == 703104.5
    assert w150['param_file'].endswith('dynamixel_xm540_w150.yaml')

    w270 = bridge._default_dynamixel_driver('XM540-W270')
    assert w270['driver_model'] == 'XM540-W270-R'
    assert w270['rated_speed_rpm'] == 37
    assert w270['speed'] == 222.0
    assert w270['profile_velocity'] == 222.0
    assert w270['profile_acceleration'] == 703104.5
    assert w270['param_file'].endswith('dynamixel_xm540_w270.yaml')


def test_dynamixel_defaults_do_not_depend_on_scan_order(tmp_path):
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.workspace_root = tmp_path
    drivers = []

    w150_id = bridge._append_driver_for_registry_motor(
        'dynamixel', 'XM540-W150', drivers
    )
    w270_id = bridge._append_driver_for_registry_motor(
        'dynamixel', 'XM540-W270', drivers
    )
    by_id = {driver['id']: driver for driver in drivers}

    assert by_id[w150_id]['speed'] == 396.0
    assert by_id[w150_id]['driver_model'] == 'XM540-W150'
    assert by_id[w270_id]['speed'] == 222.0
    assert by_id[w270_id]['driver_model'] == 'XM540-W270-R'
