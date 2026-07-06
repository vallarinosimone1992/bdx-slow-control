import pytest

from bdx_slow_control.runtime import RuntimeSettings


def test_runtime_update_period_and_frequency():
    runtime = RuntimeSettings(initial_update_period=1.0, minimum_update_period=1.0)
    assert runtime.update_frequency == pytest.approx(1.0)
    runtime.set_update_period(10.0)
    assert runtime.update_frequency == pytest.approx(0.1)


def test_runtime_rejects_faster_than_one_hz_for_prototype():
    runtime = RuntimeSettings(initial_update_period=1.0, minimum_update_period=1.0)
    with pytest.raises(ValueError):
        runtime.set_update_period(0.5)
