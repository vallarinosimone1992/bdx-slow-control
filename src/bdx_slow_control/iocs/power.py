"""Power-supply and high-voltage IOC groups."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
import time

from caproto import ChannelType
from caproto.server import pvproperty

from .common import ManagedIOC

PSU_FLOAT_PRECISION = 3
CURRENT_CHANGE_REFERENCE_FLOOR_A = 0.010
CURRENT_CHANGE_MIN_SIGNIFICANT_A = 0.030
DIAGNOSTIC_HISTORY_SECONDS = 35.0
DIAGNOSTIC_CONTROL_GRACE_SECONDS = 5.0
OCP_INTERLOCK_SECONDS = 20.0


@dataclass(frozen=True)
class PowerChannelLimits:
    """Software limits applied before low-voltage PSU writes."""

    minimum_voltage: float = 0.0
    maximum_voltage: float = 60.0
    minimum_current_limit: float = 0.0
    maximum_current_limit: float = 20.0
    maximum_power: float = 420.0

    def validate(self, voltage: float, current_limit: float) -> None:
        if voltage < self.minimum_voltage or voltage > self.maximum_voltage:
            raise ValueError(
                "Requested voltage is outside the configured limits "
                f"({self.minimum_voltage:g} to {self.maximum_voltage:g} V)"
            )
        if (
            current_limit < self.minimum_current_limit
            or current_limit > self.maximum_current_limit
        ):
            raise ValueError(
                "Requested current limit is outside the configured limits "
                f"({self.minimum_current_limit:g} to {self.maximum_current_limit:g} A)"
            )
        if voltage * current_limit > self.maximum_power:
            raise ValueError(
                "Requested voltage-current product exceeds the configured limit "
                f"({self.maximum_power:g} W)"
            )


class PowerDeviceIOC(ManagedIOC):
    """Device-level power-supply commands and status."""

    ALLOFF_CMD = pvproperty(value=False, dtype=bool)
    ALL_OUTPUTS_OFF = pvproperty(value=True, dtype=bool, read_only=True)

    async def poll_device(self) -> None:
        await self.ALL_OUTPUTS_OFF.write(value=self.driver.all_outputs_off())

    @ALLOFF_CMD.putter
    async def ALLOFF_CMD(self, instance, value):
        if value:
            try:
                self.driver.all_off()
                await self.ALL_OUTPUTS_OFF.write(value=True)
                await self.ERROR_CODE.write(value=0)
                await self.ERROR_MESSAGE.write(value="")
            except Exception as exc:
                await self.mark_failure(exc)
                raise
        return False


class PowerChannelIOC(ManagedIOC):
    """Single-channel setpoint and readback group."""

    VOLTAGE_SET = pvproperty(value=0.0, dtype=float, precision=PSU_FLOAT_PRECISION)
    VOLTAGE_RBV = pvproperty(
        value=0.0,
        dtype=float,
        read_only=True,
        precision=PSU_FLOAT_PRECISION,
    )
    CURRENT_LIMIT_SET = pvproperty(value=0.0, dtype=float, precision=PSU_FLOAT_PRECISION)
    CURRENT_LIMIT_RBV = pvproperty(
        value=0.0,
        dtype=float,
        read_only=True,
        precision=PSU_FLOAT_PRECISION,
    )
    CURRENT_RBV = pvproperty(
        value=0.0,
        dtype=float,
        read_only=True,
        precision=PSU_FLOAT_PRECISION,
    )
    OUTPUT_SET = pvproperty(value=False, dtype=bool)
    OUTPUT_RBV = pvproperty(value=False, dtype=bool, read_only=True)
    OUTPUT_MONITOR_READY = pvproperty(value=False, dtype=bool, read_only=True)
    OUTPUT_COMMAND_PENDING = pvproperty(value=False, dtype=bool, read_only=True)
    OVP_SET = pvproperty(value=0.0, dtype=float, precision=PSU_FLOAT_PRECISION)
    OVP_RBV = pvproperty(
        value=0.0,
        dtype=float,
        read_only=True,
        precision=PSU_FLOAT_PRECISION,
    )
    OCP_SET = pvproperty(value=0.0, dtype=float, precision=PSU_FLOAT_PRECISION)
    OCP_RBV = pvproperty(
        value=0.0,
        dtype=float,
        read_only=True,
        precision=PSU_FLOAT_PRECISION,
    )
    OVP_WARNING = pvproperty(value=False, dtype=bool, read_only=True)
    OVP_ALARM = pvproperty(value=False, dtype=bool, read_only=True)
    OCP_WARNING = pvproperty(value=False, dtype=bool, read_only=True)
    OCP_ALARM = pvproperty(value=False, dtype=bool, read_only=True)
    TRIP_ACTIVE = pvproperty(value=False, dtype=bool, read_only=True)
    OCP_TRIPPED = pvproperty(value=False, dtype=bool, read_only=True)
    OVP_TRIPPED = pvproperty(value=False, dtype=bool, read_only=True)
    UNREGULATED = pvproperty(value=False, dtype=bool, read_only=True)
    CONSTANT_CURRENT = pvproperty(value=False, dtype=bool, read_only=True)
    CONSTANT_VOLTAGE = pvproperty(value=False, dtype=bool, read_only=True)
    SIM_CURRENT_SET = pvproperty(value=math.nan, dtype=float)
    SIM_OUTPUT_MISMATCH_SET = pvproperty(value=False, dtype=bool)
    SIM_OCP_TRIP_SET = pvproperty(value=False, dtype=bool)

    def __init__(self, *args, channel: int, **kwargs) -> None:
        self.channel = int(channel)
        self._output_setting_initialized = False
        self._output_monitor_pending_poll = False
        super().__init__(*args, **kwargs)

    async def poll_device(self) -> None:
        state = self.driver.read_channel(self.channel)
        await self._initialize_output_setting(state)
        await self.VOLTAGE_RBV.write(value=state.voltage)
        await self.CURRENT_LIMIT_RBV.write(value=state.current_limit)
        await self.CURRENT_RBV.write(value=state.current)
        await self.OUTPUT_RBV.write(value=state.output_enabled)
        await self._update_output_monitor_ready(state.output_enabled)
        await self.OVP_RBV.write(value=state.ovp)
        await self.OCP_RBV.write(value=state.ocp)
        await self._write_protection_status(state)

    async def _initialize_output_setting(self, state) -> None:
        if not self._output_setting_initialized:
            # Reconcile the command PV with hardware state after an IOC restart.
            # verify_value=False bypasses the putter, so this never writes hardware.
            await self.OUTPUT_SET.write(
                value=state.output_enabled,
                verify_value=False,
            )
            self._output_setting_initialized = True
            await self.OUTPUT_COMMAND_PENDING.write(value=False)
            await self.OUTPUT_MONITOR_READY.write(value=True)

    async def _update_output_monitor_ready(self, output_enabled: bool) -> None:
        if not self._output_monitor_pending_poll:
            return
        if bool(output_enabled) != bool(self.OUTPUT_SET.value):
            # The hardware has not acknowledged the requested transition yet.
            # Keep mismatch monitoring disarmed until the readback confirms it.
            await self.OUTPUT_COMMAND_PENDING.write(value=True)
            await self.OUTPUT_MONITOR_READY.write(value=False)
            return
        self._output_monitor_pending_poll = False
        await self.OUTPUT_COMMAND_PENDING.write(value=False)
        await self.OUTPUT_MONITOR_READY.write(value=True)

    async def _write_protection_status(self, state) -> None:
        voltage = abs(float(state.voltage))
        current = abs(float(state.current))
        ovp = float(state.ovp)
        ocp = float(state.ocp)
        await self.OVP_WARNING.write(value=ovp > 0 and voltage >= 0.95 * ovp)
        await self.OVP_ALARM.write(value=ovp > 0 and voltage >= ovp)
        await self.OCP_WARNING.write(value=ocp > 0 and current >= 0.95 * ocp)
        await self.OCP_ALARM.write(value=ocp > 0 and current >= ocp)
        await self.TRIP_ACTIVE.write(value=bool(state.trip_active))
        await self.OCP_TRIPPED.write(value=bool(state.ocp_tripped))
        await self.OVP_TRIPPED.write(value=bool(state.ovp_tripped))
        await self.UNREGULATED.write(value=bool(state.unregulated))
        await self.CONSTANT_CURRENT.write(value=bool(state.constant_current))
        await self.CONSTANT_VOLTAGE.write(value=bool(state.constant_voltage))

    @VOLTAGE_SET.putter
    async def VOLTAGE_SET(self, instance, value):
        try:
            self.driver.set_voltage(self.channel, float(value))
        except Exception as exc:
            await self.mark_failure(exc)
            raise
        return float(value)

    @CURRENT_LIMIT_SET.putter
    async def CURRENT_LIMIT_SET(self, instance, value):
        try:
            self.driver.set_current_limit(self.channel, float(value))
        except Exception as exc:
            await self.mark_failure(exc)
            raise
        return float(value)

    @OUTPUT_SET.putter
    async def OUTPUT_SET(self, instance, value):
        await self.OUTPUT_MONITOR_READY.write(value=False)
        await self.OUTPUT_COMMAND_PENDING.write(value=True)
        self._output_monitor_pending_poll = True
        try:
            self.driver.set_output(self.channel, bool(value))
        except Exception as exc:
            self._output_monitor_pending_poll = False
            await self.OUTPUT_COMMAND_PENDING.write(value=False)
            await self.OUTPUT_MONITOR_READY.write(value=True)
            await self.mark_failure(exc)
            raise
        return bool(value)

    @OVP_SET.putter
    async def OVP_SET(self, instance, value):
        try:
            self.driver.set_ovp(self.channel, float(value))
        except Exception as exc:
            await self.mark_failure(exc)
            raise
        return float(value)

    @OCP_SET.putter
    async def OCP_SET(self, instance, value):
        try:
            self.driver.set_ocp(self.channel, float(value))
        except Exception as exc:
            await self.mark_failure(exc)
            raise
        return float(value)

    @SIM_CURRENT_SET.putter
    async def SIM_CURRENT_SET(self, instance, value):
        if not bool(getattr(self.driver, "simulation", False)):
            raise ValueError("Current injection is simulation-only")
        numeric = float(value)
        self.driver.set_simulated_current(
            self.channel,
            None if math.isnan(numeric) else numeric,
        )
        return numeric

    @SIM_OUTPUT_MISMATCH_SET.putter
    async def SIM_OUTPUT_MISMATCH_SET(self, instance, value):
        if not bool(getattr(self.driver, "simulation", False)):
            raise ValueError("Output mismatch injection is simulation-only")
        self.driver.set_simulated_output_readback(
            self.channel,
            not bool(self.OUTPUT_SET.value) if value else None,
        )
        return bool(value)

    @SIM_OCP_TRIP_SET.putter
    async def SIM_OCP_TRIP_SET(self, instance, value):
        if not bool(getattr(self.driver, "simulation", False)):
            raise ValueError("OCP trip injection is simulation-only")
        self.driver.set_simulated_ocp_trip(self.channel, bool(value))
        return bool(value)


class LowVoltagePowerChannelIOC(PowerChannelIOC):
    """Low-voltage PSU channel with staged operator setpoints."""

    VOLTAGE_SET_RBV = pvproperty(
        value=0.0,
        dtype=float,
        read_only=True,
        precision=PSU_FLOAT_PRECISION,
    )
    OUTPUT_STATE = pvproperty(value="OFF", dtype=ChannelType.STRING, read_only=True)
    VOLTAGE_REQUEST = pvproperty(value=0.0, dtype=float, precision=PSU_FLOAT_PRECISION)
    CURRENT_LIMIT_REQUEST = pvproperty(
        value=0.0,
        dtype=float,
        precision=PSU_FLOAT_PRECISION,
    )
    APPLY_CMD = pvproperty(value=False, dtype=bool)
    APPLY_STATUS = pvproperty(value="IDLE", dtype=ChannelType.STRING, read_only=True)
    APPLY_MESSAGE = pvproperty(value="", dtype=ChannelType.STRING, read_only=True)
    CURRENT_CHANGE_2S_PERCENT = pvproperty(
        value=math.nan, dtype=float, read_only=True, precision=2
    )
    CURRENT_CHANGE_10S_PERCENT = pvproperty(
        value=math.nan, dtype=float, read_only=True, precision=2
    )
    VOLTAGE_SPAN_30S = pvproperty(
        value=math.nan, dtype=float, read_only=True, precision=PSU_FLOAT_PRECISION
    )
    CURRENT_SPAN_30S = pvproperty(
        value=math.nan, dtype=float, read_only=True, precision=PSU_FLOAT_PRECISION
    )
    OCP_INTERLOCK_ACTIVE = pvproperty(value=False, dtype=bool, read_only=True)

    def __init__(
        self,
        *args,
        limits: PowerChannelLimits | None = None,
        **kwargs,
    ) -> None:
        self.limits = limits or PowerChannelLimits()
        self._requests_initialized = False
        self._diagnostic_history = deque()
        self._diagnostic_control_state: tuple[bool, float, float] | None = None
        self._diagnostic_grace_until: float | None = None
        self._ocp_trip_started_at: float | None = None
        self._ocp_interlock_active = False
        super().__init__(*args, **kwargs)

    async def poll_device(self) -> None:
        state = self.driver.read_channel(self.channel)
        await self._initialize_output_setting(state)
        await self.VOLTAGE_RBV.write(value=state.voltage)
        await self.VOLTAGE_SET_RBV.write(value=state.voltage_setpoint)
        await self.CURRENT_LIMIT_RBV.write(value=state.current_limit)
        await self.CURRENT_RBV.write(value=state.current)
        await self.OUTPUT_RBV.write(value=state.output_enabled)
        await self._update_output_monitor_ready(state.output_enabled)
        await self.OUTPUT_STATE.write(value="ON" if state.output_enabled else "OFF")
        await self.OVP_RBV.write(value=state.ovp)
        await self.OCP_RBV.write(value=state.ocp)
        await self._write_protection_status(state)
        await self._write_diagnostics(state)
        await self._update_ocp_interlock(state)
        if not self._requests_initialized:
            await self.VOLTAGE_REQUEST.write(value=state.voltage_setpoint)
            await self.CURRENT_LIMIT_REQUEST.write(value=state.current_limit)
            self._requests_initialized = True

    @staticmethod
    def _percent_change(current: float, reference: float) -> float:
        delta = abs(float(current) - float(reference))
        if delta < CURRENT_CHANGE_MIN_SIGNIFICANT_A:
            return 0.0
        denominator = max(
            abs(float(current)),
            abs(float(reference)),
            CURRENT_CHANGE_REFERENCE_FLOOR_A,
        )
        return delta / denominator * 100.0

    def _sample_at_or_before(self, target: float):
        for sample in reversed(self._diagnostic_history):
            if sample[0] <= target:
                return sample
        return None

    @staticmethod
    def _settings_stable(samples) -> bool:
        if not samples:
            return False
        voltages = [sample[3] for sample in samples]
        currents = [sample[4] for sample in samples]
        return (
            max(voltages) - min(voltages) < 1e-9
            and max(currents) - min(currents) < 1e-9
        )

    async def _write_diagnostics(self, state) -> None:
        now = time.monotonic()
        control_state = (
            bool(state.output_enabled),
            float(state.voltage_setpoint),
            float(state.current_limit),
        )
        control_changed = (
            self._diagnostic_control_state is not None
            and control_state != self._diagnostic_control_state
        )
        first_active_sample = (
            self._diagnostic_control_state is None
            and bool(state.output_enabled)
        )
        self._diagnostic_control_state = control_state

        if control_changed or first_active_sample:
            # Operator-commanded changes naturally produce current/voltage
            # transients.  Drop the previous rolling baseline and allow the
            # hardware a short settling period before rebuilding diagnostics.
            self._diagnostic_history.clear()
            self._diagnostic_grace_until = now + DIAGNOSTIC_CONTROL_GRACE_SECONDS

        if not bool(state.output_enabled):
            self._diagnostic_history.clear()
            self._diagnostic_grace_until = None
            await self.CURRENT_CHANGE_2S_PERCENT.write(value=math.nan)
            await self.CURRENT_CHANGE_10S_PERCENT.write(value=math.nan)
            await self.VOLTAGE_SPAN_30S.write(value=math.nan)
            await self.CURRENT_SPAN_30S.write(value=math.nan)
            return

        if (
            self._diagnostic_grace_until is not None
            and now < self._diagnostic_grace_until
        ):
            self._diagnostic_history.clear()
            await self.CURRENT_CHANGE_2S_PERCENT.write(value=math.nan)
            await self.CURRENT_CHANGE_10S_PERCENT.write(value=math.nan)
            await self.VOLTAGE_SPAN_30S.write(value=math.nan)
            await self.CURRENT_SPAN_30S.write(value=math.nan)
            return
        self._diagnostic_grace_until = None

        sample = (
            now,
            float(state.voltage),
            float(state.current),
            float(state.voltage_setpoint),
            float(state.current_limit),
        )
        self._diagnostic_history.append(sample)
        cutoff = now - DIAGNOSTIC_HISTORY_SECONDS
        while self._diagnostic_history and self._diagnostic_history[0][0] < cutoff:
            self._diagnostic_history.popleft()

        for seconds, pv in (
            (2.0, self.CURRENT_CHANGE_2S_PERCENT),
            (10.0, self.CURRENT_CHANGE_10S_PERCENT),
        ):
            reference = self._sample_at_or_before(now - seconds)
            relevant = [
                item for item in self._diagnostic_history
                if item[0] >= now - seconds
            ]
            if reference is None or not self._settings_stable(relevant + [reference]):
                value = math.nan
            else:
                value = self._percent_change(float(state.current), reference[2])
            await pv.write(value=value)

        window = [
            item for item in self._diagnostic_history
            if item[0] >= now - 30.0
        ]
        covers_window = bool(window) and window[0][0] <= now - 29.0
        if covers_window and self._settings_stable(window):
            voltage_span = max(item[1] for item in window) - min(item[1] for item in window)
            current_span = max(item[2] for item in window) - min(item[2] for item in window)
        else:
            voltage_span = math.nan
            current_span = math.nan
        await self.VOLTAGE_SPAN_30S.write(value=voltage_span)
        await self.CURRENT_SPAN_30S.write(value=current_span)

    async def _update_ocp_interlock(self, state) -> None:
        now = time.monotonic()
        if bool(state.ocp_tripped):
            if self._ocp_trip_started_at is None:
                self._ocp_trip_started_at = now
            if (
                not self._ocp_interlock_active
                and now - self._ocp_trip_started_at >= OCP_INTERLOCK_SECONDS
            ):
                # The CPX400DP hardware OCP trip already switches the output off.
                # This explicit command keeps the slow-control interlock action
                # deterministic for simulation and compatible drivers.
                self.driver.set_output(self.channel, False)
                self._ocp_interlock_active = True
        else:
            self._ocp_trip_started_at = None
            self._ocp_interlock_active = False
        await self.OCP_INTERLOCK_ACTIVE.write(value=self._ocp_interlock_active)

    @APPLY_CMD.putter
    async def APPLY_CMD(self, instance, value):
        if not value:
            return False

        voltage = float(self.VOLTAGE_REQUEST.value)
        current_limit = float(self.CURRENT_LIMIT_REQUEST.value)
        try:
            self.limits.validate(voltage, current_limit)
        except ValueError as exc:
            await self.APPLY_STATUS.write(value="REJECTED")
            await self.APPLY_MESSAGE.write(value=str(exc))
            return False

        try:
            self.driver.set_voltage_and_current_limit(
                self.channel,
                voltage,
                current_limit,
            )
            state = self.driver.read_channel(self.channel)
        except Exception as exc:
            message = (
                f"Apply failed; hardware may have accepted only part of the request: {exc}"
            )
            await self.APPLY_STATUS.write(value="FAILED")
            await self.APPLY_MESSAGE.write(value=message)
            try:
                state = self.driver.read_channel(self.channel)
            except Exception:
                await self.mark_failure(exc)
                return False
            await self._write_readbacks(state)
            await self.mark_failure(exc)
            return False

        await self._write_readbacks(state)
        await self.APPLY_STATUS.write(value="APPLIED")
        await self.APPLY_MESSAGE.write(value="Request applied")
        await self.ERROR_CODE.write(value=0)
        await self.ERROR_MESSAGE.write(value="")
        return False

    async def _write_readbacks(self, state) -> None:
        await self.VOLTAGE_RBV.write(value=state.voltage)
        await self.VOLTAGE_SET_RBV.write(value=state.voltage_setpoint)
        await self.CURRENT_LIMIT_RBV.write(value=state.current_limit)
        await self.CURRENT_RBV.write(value=state.current)
        await self.OUTPUT_RBV.write(value=state.output_enabled)
        await self._update_output_monitor_ready()
        await self.OUTPUT_STATE.write(value="ON" if state.output_enabled else "OFF")
        await self.OVP_RBV.write(value=state.ovp)
        await self.OCP_RBV.write(value=state.ocp)
        await self._write_protection_status(state)
        await self._write_diagnostics(state)
        await self._update_ocp_interlock(state)
