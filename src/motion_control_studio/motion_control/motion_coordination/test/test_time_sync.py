from types import SimpleNamespace

from motion_coordination.time_sync import inspect_time_sync


def test_chrony_offset_is_reduced_to_safe_summary():
    def runner(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout='Last offset     : +0.000007500 seconds\nLeap status     : Normal\n',
        )

    assert inspect_time_sync(runner) == {
        'clock_sync_state': 'ready',
        'clock_offset_ms': 0.0075,
        'clock_source': 'chrony',
    }


def test_chrony_out_of_tolerance_blocks_synchronized_start():
    def runner(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout='Last offset     : -0.025 seconds\nLeap status     : Normal\n',
        )

    assert inspect_time_sync(runner)['clock_sync_state'] == 'out_of_tolerance'
