from motion_coordination.trigger_sync import (
    TriggerSyncEstimator,
    coordinator_to_local_ns,
    local_to_coordinator_ns,
)


def test_best_round_trip_samples_estimate_relative_monotonic_offset():
    estimator = TriggerSyncEstimator(keep_best=3)
    # Participant monotonic clock is 25 ms ahead of the coordinator.
    assert estimator.add_exchange(
        t1_ns=1_000_000_000, t2_ns=1_027_000_000,
        t3_ns=1_027_500_000, t4_ns=1_004_500_000,
    )
    assert estimator.add_exchange(
        t1_ns=2_000_000_000, t2_ns=2_026_000_000,
        t3_ns=2_026_500_000, t4_ns=2_003_500_000,
    )
    assert estimator.add_exchange(
        t1_ns=3_000_000_000, t2_ns=3_026_500_000,
        t3_ns=3_027_000_000, t4_ns=3_004_000_000,
    )
    # Large Wi-Fi delay outlier must not move the selected estimate.
    assert estimator.add_exchange(
        t1_ns=4_000_000_000, t2_ns=4_080_000_000,
        t3_ns=4_080_500_000, t4_ns=4_081_000_000,
    )
    estimate = estimator.estimate()
    assert abs(estimate.offset_ns - 25_000_000) <= 500_000
    assert estimate.uncertainty_ms <= 2.5
    assert estimate.sample_count == 4


def test_invalid_exchange_is_rejected():
    estimator = TriggerSyncEstimator()
    assert not estimator.add_exchange(t1_ns=10, t2_ns=20, t3_ns=19, t4_ns=30)
    assert not estimator.add_exchange(t1_ns=30, t2_ns=20, t3_ns=21, t4_ns=29)


def test_deadline_and_trigger_mapping_are_inverse():
    local = coordinator_to_local_ns(2_000_000_000, 25_000_000)
    assert local == 2_025_000_000
    assert local_to_coordinator_ns(local, 25_000_000) == 2_000_000_000
