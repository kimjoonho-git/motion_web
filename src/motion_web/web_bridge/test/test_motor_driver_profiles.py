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


def _registry_motor(axis, *, transport, master_index=0):
    if transport == 'ethercat':
        return {
            'enabled': True,
            'deleted': False,
            'transport': 'ethercat',
            'motor_type': 'ac_servo',
            'driver_family': 'minas',
            'name': f'AC {axis}',
            'axis': axis,
            'identity': {},
            'profile': {
                'driver_model': 'MADLN05BE',
                'model_confirmed': True,
                'model_source': 'user_nameplate',
            },
            'config': {
                'controller_index': axis,
                'ethercat_master_index': master_index,
                'driver_id': 0,
                'alias': 100 + axis,
                'position': 0,
                'vendor_id': 1647,
                'product_id': 1614282756,
                'profile_mode': 0,
            },
        }
    return {
        'enabled': True,
        'deleted': False,
        'transport': 'serial',
        'motor_type': 'dynamixel',
        'driver_family': 'dynamixel',
        'name': f'Dynamixel {axis}',
        'axis': axis,
        'identity': {
            'driver_model': 'XM540-W150',
            'bus_id': axis + 1,
            'serial_port': '/dev/test-dynamixel',
            'serial_baudrate': 1000000,
        },
        'config': {
            'controller_index': axis,
            'driver_id': 1,
            'bus_id': axis + 1,
            'serial_port': '/dev/test-dynamixel',
            'serial_baudrate': 1000000,
            'profile_mode': 0,
        },
    }


def test_dynamixel_only_config_does_not_keep_empty_ethercat_master(tmp_path):
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.workspace_root = tmp_path
    registry = {'motors': [_registry_motor(axis, transport='serial') for axis in range(30)]}

    config = bridge._motor_config_from_registry(registry, bridge._default_motor_config())

    assert [(master['type'], master['number_of_slaves']) for master in config['masters']] == [
        ('serial', 30),
    ]
    assert len(config['drivers']) == 30


def test_ac_and_mixed_configs_keep_ethercat_only_when_needed(tmp_path):
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.workspace_root = tmp_path

    ac_config = bridge._motor_config_from_registry(
        {'motors': [_registry_motor(axis, transport='ethercat') for axis in range(30)]},
        bridge._default_motor_config(),
    )
    assert [(master['type'], master['number_of_slaves']) for master in ac_config['masters']] == [
        ('ethercat', 30),
    ]

    mixed_config = bridge._motor_config_from_registry(
        {
            'motors': [
                *[_registry_motor(axis, transport='ethercat') for axis in range(15)],
                *[_registry_motor(axis, transport='serial') for axis in range(15, 30)],
            ],
        },
        bridge._default_motor_config(),
    )
    assert [(master['type'], master['number_of_slaves']) for master in mixed_config['masters']] == [
        ('ethercat', 15),
        ('serial', 15),
    ]


def test_ac_identity_metadata_round_trips_in_project_config(tmp_path):
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.workspace_root = tmp_path
    motor = _registry_motor(0, transport='ethercat')
    motor['identity'].update({
        'ethercat_alias': 403,
        'rotary_alias': 3,
        'slave_position': 1,
        'identity_source': 'physical_sii',
        'revision_number': 65536,
        'serial_number': 123456,
        'sii_order_number': 'SII-ORDER',
        'sii_device_name': 'SII-DEVICE',
    })

    config = bridge._motor_config_from_registry(
        {'motors': [motor]}, bridge._default_motor_config()
    )
    restored = bridge._registry_from_motor_config(config)['motors'][0]

    assert config['masters'][0]['slaves'][0]['alias'] == 403
    assert config['masters'][0]['slaves'][0]['position'] == 1
    assert config['web_axis_identities'] == [{
        'controller_index': 0,
        'ethercat_master_index': 0,
        'eeprom_alias': 403,
        'rotary_alias': 3,
        'slave_position': 1,
        'vendor_id': 1647,
        'product_id': 1614282756,
        'revision_number': 65536,
        'serial_number': 123456,
        'identity_source': 'physical_sii',
        'sii_order_number': 'SII-ORDER',
        'sii_device_name': 'SII-DEVICE',
    }]
    assert config['web_axis_profiles'] == [{
        'controller_index': 0,
        'driver_model': 'MADLN05BE',
        'model_confirmed': True,
        'model_source': 'user_nameplate',
    }]
    assert restored['identity']['ethercat_alias'] == 403
    assert restored['identity']['ethercat_master_index'] == 0
    assert restored['identity']['rotary_alias'] == 3
    assert restored['identity']['slave_position'] == 1
    assert restored['identity']['revision_number'] == 65536
    assert restored['identity']['serial_number'] == 123456
    assert restored['identity']['vendor_id'] == 1647
    assert restored['identity']['product_code'] == 1614282756
    assert restored['identity']['sii_order_number'] == 'SII-ORDER'
    assert restored['identity']['sii_device_name'] == 'SII-DEVICE'
    assert 'driver_model' not in restored['identity']
    assert restored['profile']['driver_model'] == 'MADLN05BE'
    assert restored['profile']['model_confirmed'] is True


def test_two_ethercat_masters_round_trip_without_merging_slave_positions(tmp_path):
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.workspace_root = tmp_path
    motors = []
    for master_index in (0, 1):
        for offset in range(2):
            axis = master_index * 2 + offset
            motor = _registry_motor(
                axis,
                transport='ethercat',
                master_index=master_index,
            )
            motor['identity'].update({
                'ethercat_master_index': master_index,
                'ethercat_alias': 0,
                'rotary_alias': 0,
                'slave_position': offset,
            })
            motor['config'].update({
                'alias': 0,
                'position': offset,
            })
            motors.append(motor)

    config = bridge._motor_config_from_registry(
        {'motors': motors}, bridge._default_motor_config()
    )
    restored = bridge._registry_from_motor_config(config)['motors']

    assert [
        (master['ethercat_master_index'], master['number_of_slaves'])
        for master in config['masters']
    ] == [(0, 2), (1, 2)]
    assert [
        [slave['controller_index'] for slave in master['slaves']]
        for master in config['masters']
    ] == [[0, 1], [2, 3]]
    assert [
        motor['config']['ethercat_master_index'] for motor in restored
    ] == [0, 0, 1, 1]
    assert len({motor['id'] for motor in restored}) == 4


def test_zero_alias_ac_axes_round_trip_with_unique_slave_ids(tmp_path):
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.workspace_root = tmp_path
    motors = []
    for axis in range(5):
        motor = _registry_motor(axis, transport='ethercat')
        motor['identity'].update({
            'ethercat_alias': None,
            'rotary_alias': 0,
            'slave_position': axis,
        })
        motor['config']['alias'] = None
        motor['config']['position'] = axis
        motors.append(motor)

    config = bridge._motor_config_from_registry(
        {'motors': motors}, bridge._default_motor_config()
    )
    restored = bridge._registry_from_motor_config(config)['motors']

    assert [slave['alias'] for slave in config['masters'][0]['slaves']] == [0] * 5
    assert [slave['position'] for slave in config['masters'][0]['slaves']] == list(range(5))
    assert [motor['id'] for motor in restored] == [
        f'ac_servo_ethercat_master_0_slave_{axis}' for axis in range(5)
    ]
    assert len({motor['id'] for motor in restored}) == 5
