from motion_supervisor.servo_alarm_guard import ServoAlarmGuard, policy_revision


def ac_alarm(axis, code):
    return {
        'controller_index': axis,
        'motor_type': 'ac_servo',
        'errorcode': code,
        'errorcode_raw': code,
        'fault': True,
    }


def is_ac_servo(motor):
    return motor.get('motor_type') == 'ac_servo'


def axis_value(value):
    return int(value)


def test_policy_revision_rejects_mismatched_grade_content():
    guard = ServoAlarmGuard()
    grades = {'16': 1, '24': 2}

    success, message = guard.apply_policy(
        grades,
        project_id='project-a',
        catalog_version=1,
        revision=policy_revision({'16': 3, '24': 2}, 1),
    )

    assert success is False
    assert 'revision' in message
    assert guard.snapshot()['policy_revision'] == ''


def test_grade1_blocked_slots_follow_controller_index_values():
    guard = ServoAlarmGuard()
    grades = {'16': 1}
    assert guard.apply_policy(
        grades,
        project_id='project-a',
        catalog_version=1,
        revision=policy_revision(grades, 1),
    )[0]
    guard.evaluate(
        [ac_alarm(5, 16)],
        is_ac_servo=is_ac_servo,
        axis_value=axis_value,
        playback_active=False,
    )

    assert guard.blocked_slots([2, 5]) == {1}
    assert guard.blocked_slots([5]) == {0}
    assert guard.blocked_slots([0, 2]) == set()


def test_project_boundary_resets_policy_identity_but_keeps_grade3_latch():
    guard = ServoAlarmGuard()
    grades = {'98': 3}
    assert guard.apply_policy(
        grades,
        project_id='project-a',
        catalog_version=1,
        revision=policy_revision(grades, 1),
    )[0]
    guard.evaluate(
        [ac_alarm(0, 98)],
        is_ac_servo=is_ac_servo,
        axis_value=axis_value,
        playback_active=False,
    )

    guard.reset_project_policy()
    state = guard.snapshot()

    assert state['grade3_latched'] is True
    assert state['grade'] == 3
    assert state['policy_project_id'] == ''
    assert state['policy_revision'] == ''

