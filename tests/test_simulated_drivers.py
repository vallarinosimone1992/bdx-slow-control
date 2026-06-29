import pytest

from bdx_slow_control.drivers.simulated import (
    SimulatedDaqCrateDriver,
    SimulatedPowerSupplyDriver,
)


def test_power_supply_setpoint_and_readback():
    driver = SimulatedPowerSupplyDriver(channels=[1])
    driver.set_voltage(1, 5.0)
    driver.set_output(1, True)
    state = driver.read_channel(1)
    assert state.output_enabled is True
    assert state.voltage == pytest.approx(5.0)


def test_power_supply_all_off():
    driver = SimulatedPowerSupplyDriver(channels=[1, 2])
    driver.set_output(1, True)
    driver.set_output(2, True)
    driver.all_off()
    assert driver.all_outputs_off() is True


def test_daq_configuration():
    driver = SimulatedDaqCrateDriver("initial")
    driver.apply_configuration("prototype")
    assert driver.read_state().configuration_applied == "prototype"
