import pytest

from bdx_slow_control.runtime import RuntimeSettings


def test_runtime_update_period_and_frequency():
    runtime = RuntimeSettings(initial_update_period=5.0, minimum_update_period=2.0)
    assert runtime.update_frequency == pytest.approx(0.2)
    runtime.set_update_period(10.0)
    assert runtime.update_frequency == pytest.approx(0.1)


def test_runtime_rejects_one_hz_or_faster_for_prototype():
    runtime = RuntimeSettings(initial_update_period=5.0, minimum_update_period=2.0)
    with pytest.raises(ValueError):
        runtime.set_update_period(1.0)
