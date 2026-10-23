"""Hardware validation for Keysight EL34243A (KeysightEL30000) via InstroELoad. Self-contained; no publishers.

Exercises every method KeysightEL30000 implements through the InstroELoad public API: mode select
(CC/CV/CP/CR), level/range/slew writes with device readback, input enable, current/voltage measurement
against the known source, and SYST:ERR? error propagation.

Wiring / stimulus:
    A 5 V / 1 A DC supply is connected to the INPUT terminals of CHANNEL (2 on the tested unit) through a relay
    on the positive bus. The relay is driven by a LabJack T7 Pro on FIO0 and closes when FIO0 is LOW (active-low).
    The 5 V supply is a Rigol DP832A (CH1, limited to 1 A) driven through InstroPSU, which lets the script cross-check
    the load against the supply's own readback and run CV with the supply in current limit.
    All levels below draw at most ~0.6 A. The script connects the supply for the source checks, then opens the
    relay and bleeds the input down before short_output, and leaves the supply disconnected on exit.

Tested unit: Keysight EL34243A, firmware 1.2.5-1.0.6-17-114.

Run:
    uv run pytest tests/eload/keysight/test_keysight_el30000_hardware.py -m hardware -s
    uv run python tests/eload/keysight/test_keysight_el30000_hardware.py
"""

from __future__ import annotations

import math
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass

import pytest

from instro.daq import InstroDAQ
from instro.daq.types import Logic
from instro.eload import InstroELoad
from instro.eload.drivers.keysight_el30000 import KeysightEL30000
from instro.eload.types import LoadMode, SlewRateDirection
from instro.psu import InstroPSU
from instro.psu.drivers import RigolDP800

pytestmark = pytest.mark.hardware

# --- Configuration — edit before running -----------------------------------
RESOURCE = "USB0::0x2A8D::0x3902::MY61001220::INSTR"  # <-- edit to your unit's VISA resource string
CHANNEL = 2  # <-- channel the supply is wired to

RELAY_LINE = "FIO0"  # <-- LabJack line driving the supply relay (closes when LOW)
LABJACK_ID = "ANY"  # <-- LabJack serial number, or "ANY"

PSU_RESOURCE = "USB0::0x1AB1::0x0E11::DP8B26AM00234::INSTR"  # <-- Rigol DP832A feeding the relay
PSU_CHANNEL = 1
CROSS_CHECK_VOLTS = (3.0, 5.0, 6.0)  # keep below the supply's OVP level (7 V on the tested unit)
HV_CROSS_CHECK_VOLTS = (10.0, 20.0, 30.0)  # high-voltage steps; power stays <= ~3 W
HV_OVP_V = 31.0  # supply OVP is raised to this only for the high-voltage steps, then restored
HV_CC_LEVEL_A = 0.1
HV_CR_LEVEL_OHM = 300.0  # 0.1 A at 30 V
HV_CP_LEVEL_W = 2.5  # 0.083 A at 30 V; 2 W is the load's minimum CP level
HV_CV_LEVEL_V = 20.0
HV_CV_SOURCE_LIMIT_A = 0.1
HV_CV_RANGE_V = 153.0
PSU_CURRENT_LIMIT_A = 1.0  # never raised above this; protects the load wiring and the source

SOURCE_VOLTAGE_V = 5.0
SOURCE_CURRENT_LIMIT_A = 1.0

# Levels that stay well inside the 5 V / 1 A source.
CC_LEVEL_A = 0.2
CR_LEVEL_OHM = 25.0  # ~0.2 A at 5 V
CP_LEVEL_W = 2.5  # ~0.5 A at 5 V; the EL34243A minimum CP level is 2 W
CV_LEVEL_V = 4.5
CV_CURR_LIMIT_A = 0.3
CC_RANGE_A = 1.0
CV_RANGE_V = 15.0
CP_RANGE_W = 5.0
CR_RANGE_OHM = 1250.0  # RES:RANG takes a range maximum (30, 1250, 4000, 100000 ohm)
SLEW_A_PER_US = 0.01  # 10 kA/s

VOLTAGE_REL_TOL = 0.1
CURRENT_ABS_TOL_A = 0.05
SETTLE_S = 0.5
SUPPLY_SETTLE_S = 1.5  # the supply's own readback lags a load step
SHORT_MAX_SOURCE_V = 0.5  # short_output only runs when the input is below this, i.e. no source connected
BLEED_TIMEOUT_S = 15.0
BLEED_OHM = 25.0  # resistance used to discharge the input capacitance once the relay opens

MODE_FUNCTION = {LoadMode.CC: "CURR", LoadMode.CV: "VOLT", LoadMode.CP: "POW", LoadMode.CR: "RES"}


@dataclass
class Bench:
    eload: InstroELoad
    psu: InstroPSU
    relay: InstroDAQ
    source_connected: bool = False

    @property
    def visa(self):  # type: ignore[no-untyped-def]
        return self.eload._driver._visa  # type: ignore[attr-defined]

    def set_source(self, voltage: float, current_limit: float = PSU_CURRENT_LIMIT_A) -> None:
        assert current_limit <= PSU_CURRENT_LIMIT_A
        self.psu.set_current_limit(current_limit, channel=PSU_CHANNEL)
        self.psu.set_voltage(voltage, channel=PSU_CHANNEL)
        self.psu.output_enable(True, channel=PSU_CHANNEL)
        time.sleep(SETTLE_S)

    def connect_source(self) -> None:
        self.relay.write_digital_line("supply_relay", 1)
        self.source_connected = True
        time.sleep(SETTLE_S * 2)

    def disconnect_source(self) -> None:
        """Open the relay and bleed the input capacitance down through the load."""
        self.psu.output_enable(False, channel=PSU_CHANNEL)
        self.relay.write_digital_line("supply_relay", 0)
        self.source_connected = False
        self.eload.set_mode(LoadMode.CR, channel=CHANNEL)
        self.eload.set_level(value=BLEED_OHM, channel=CHANNEL)
        self.eload.output_enable(True, channel=CHANNEL)
        try:
            deadline = time.monotonic() + BLEED_TIMEOUT_S
            while self.eload.get_voltage(channel=CHANNEL).latest > SHORT_MAX_SOURCE_V:
                assert time.monotonic() < deadline, "input voltage did not fall after the relay opened"
                time.sleep(0.2)
        finally:
            self.eload.output_enable(False, channel=CHANNEL)

    def sink(self, mode: LoadMode, level: float, curr_limit: float | None = None) -> tuple[float, float, float, float]:
        """Sink briefly and return (load V, load I, supply V, supply I)."""
        self.eload.set_mode(mode, channel=CHANNEL)
        self.eload.set_level(value=level, channel=CHANNEL, curr_limit=curr_limit)
        self.eload.output_enable(True, channel=CHANNEL)
        try:
            time.sleep(SUPPLY_SETTLE_S)
            readings = (
                self.eload.get_voltage(channel=CHANNEL).latest,
                self.eload.get_current(channel=CHANNEL).latest,
                self.psu.get_voltage(channel=PSU_CHANNEL).latest,
                self.psu.get_current(channel=PSU_CHANNEL).latest,
            )
            supply_mode = self.psu.get_operating_mode(channel=PSU_CHANNEL).latest
        finally:
            self.eload.output_enable(False, channel=CHANNEL)
        print(
            f"         {mode.value} {level}: load {readings[0]:.3f} V {readings[1]:.4f} A"
            f" | supply {readings[2]:.3f} V {readings[3]:.4f} A ({supply_mode})"
        )
        return readings


@pytest.fixture(scope="module")
def bench() -> Iterator[Bench]:
    # Imported here: the LabJack package needs the optional LJM SDK, which CI collection must not require.
    from instro.daq.drivers.labjack import LabJackTSeriesDriver

    eload = InstroELoad(name="hw_validate", driver=KeysightEL30000(RESOURCE), publishers=None)
    psu = InstroPSU(name="hw_psu", driver=RigolDP800(PSU_RESOURCE), num_channels=3, publishers=None)
    relay = InstroDAQ(name="hw_relay", driver=LabJackTSeriesDriver(LABJACK_ID), publishers=None)
    opened: list[InstroELoad | InstroPSU | InstroDAQ] = []
    try:
        for device in (eload, psu, relay):
            device.open()
            opened.append(device)
        relay.configure_digital_output(RELAY_LINE, logic=Logic.LOW, alias="supply_relay")
        psu.clear_protection(channel=PSU_CHANNEL)  # a trip from an earlier run leaves the output off
        yield Bench(eload, psu, relay)
    finally:
        # Safe state for whatever opened: load input off, supply off, relay open. Each step is attempted regardless.
        safe_states = {
            id(eload): lambda: eload.output_enable(False, channel=CHANNEL),
            id(psu): lambda: psu.output_enable(False, channel=PSU_CHANNEL),
            id(relay): lambda: relay.write_digital_line("supply_relay", 0),
        }
        for device in opened:
            try:
                safe_states[id(device)]()
            except Exception:  # noqa: BLE001 - best-effort safe state
                pass
        for device in reversed(opened):
            device.close()


@pytest.fixture(autouse=True)
def baseline(bench: Bench) -> None:
    """Every test starts with the load input off and the 5 V supply connected."""
    bench.eload.output_enable(False, channel=CHANNEL)
    bench.set_source(SOURCE_VOLTAGE_V)
    if not bench.source_connected:
        bench.connect_source()


@pytest.fixture
def high_voltage_ovp(bench: Bench) -> Iterator[None]:
    """Raise the supply OVP for one test, then restore it with the supply back at 5 V."""
    original_ovp = bench.psu.get_overvoltage_protection_level(channel=PSU_CHANNEL).latest
    bench.psu.output_enable(False, channel=PSU_CHANNEL)
    bench.psu.set_overvoltage_protection_level(HV_OVP_V, channel=PSU_CHANNEL)
    try:
        yield
    finally:
        bench.psu.output_enable(False, channel=PSU_CHANNEL)
        bench.psu.set_voltage(SOURCE_VOLTAGE_V, channel=PSU_CHANNEL)
        bench.psu.set_overvoltage_protection_level(original_ovp, channel=PSU_CHANNEL)


def test_identity(bench: Bench) -> None:
    idn = bench.visa.query("*IDN?").strip()
    print(f"         *IDN? -> {idn}")
    assert "Keysight" in idn and "EL3" in idn, f"unexpected identity: {idn!r}"


def test_set_mode_reads_back_each_mode(bench: Bench) -> None:
    for mode in (LoadMode.CC, LoadMode.CV, LoadMode.CP, LoadMode.CR):
        bench.eload.set_mode(mode, channel=CHANNEL)
        active = bench.visa.query(f"FUNC? (@{CHANNEL})").strip().upper()
        assert active.startswith(MODE_FUNCTION[mode]), f"{mode}: device reports {active!r}"


def test_set_level_reads_back_each_mode(bench: Bench) -> None:
    cases = [
        (LoadMode.CC, CC_LEVEL_A, "CURR", None),
        (LoadMode.CV, CV_LEVEL_V, "VOLT", CV_CURR_LIMIT_A),
        (LoadMode.CP, CP_LEVEL_W, "POW", None),
        (LoadMode.CR, CR_LEVEL_OHM, "RES", None),
    ]
    for mode, value, fn, curr_limit in cases:
        bench.eload.set_mode(mode, channel=CHANNEL)
        bench.eload.set_level(value=value, channel=CHANNEL, curr_limit=curr_limit)
        got = float(bench.visa.query(f"{fn}? (@{CHANNEL})"))
        assert math.isclose(got, value, rel_tol=1e-3), f"{mode}: set {value}, device reports {got}"


def test_set_level_cv_current_limit_reads_back(bench: Bench) -> None:
    bench.eload.set_mode(LoadMode.CV, channel=CHANNEL)
    bench.eload.set_level(value=CV_LEVEL_V, channel=CHANNEL, curr_limit=CV_CURR_LIMIT_A)
    limit = float(bench.visa.query(f"CURR:LIM? (@{CHANNEL})"))
    print(f"         CURR:LIM? -> {limit} A (sent {CV_CURR_LIMIT_A})")
    assert math.isclose(limit, CV_CURR_LIMIT_A, rel_tol=1e-3), f"limit reads back {limit}"


def test_set_range_reads_back_each_mode(bench: Bench) -> None:
    # The instrument rejects a range that excludes the present level, so set an in-range level first.
    cases = [
        (LoadMode.CC, CC_RANGE_A, "CURR", CC_LEVEL_A),
        (LoadMode.CV, CV_RANGE_V, "VOLT", CV_LEVEL_V),
        (LoadMode.CP, CP_RANGE_W, "POW", CP_LEVEL_W),
        (LoadMode.CR, CR_RANGE_OHM, "RES", CR_LEVEL_OHM),
    ]
    for mode, value, fn, level in cases:
        bench.eload.set_mode(mode, channel=CHANNEL)
        bench.eload.set_level(value=level, channel=CHANNEL)
        bench.eload.set_range(value=value, channel=CHANNEL)
        got = float(bench.visa.query(f"{fn}:RANG? (@{CHANNEL})"))
        print(f"         {mode.value} range: requested {value}, device range max {got}")
        assert got >= value, f"{mode}: range max {got} does not cover {value}"


def test_set_range_excluding_present_level_raises_clear_error(bench: Bench) -> None:
    # Input stays off: a 3 A setpoint is only programmed, never drawn.
    bench.eload.set_mode(LoadMode.CC, channel=CHANNEL)
    bench.eload.set_range(value=CC_RANGE_A * 10, channel=CHANNEL)
    bench.eload.set_level(value=3.0, channel=CHANNEL)
    range_before = float(bench.visa.query(f"CURR:RANG? (@{CHANNEL})"))
    with pytest.raises(RuntimeError, match=r"rejected CC range 0\.5.*present CC level") as excinfo:
        bench.eload.set_range(value=0.5, channel=CHANNEL)  # snaps to the 0.612 A range, which excludes 3 A
    print(f"         raised: {excinfo.value}")
    assert float(bench.visa.query(f"CURR:RANG? (@{CHANNEL})")) == range_before, "range changed despite the error"


def test_set_slewrate_converts_a_per_us_to_a_per_s(bench: Bench) -> None:
    bench.eload.set_mode(LoadMode.CC, channel=CHANNEL)  # the instrument only accepts CURR:SLEW in CC mode
    expected_a_per_s = SLEW_A_PER_US * 1e6
    for direction, query in (
        (SlewRateDirection.RISE, f"CURR:SLEW? (@{CHANNEL})"),
        (SlewRateDirection.FALL, f"CURR:SLEW:NEG? (@{CHANNEL})"),
    ):
        bench.eload.set_slewrate(direction, rate=SLEW_A_PER_US, channel=CHANNEL)
        got = float(bench.visa.query(query))
        assert math.isclose(got, expected_a_per_s, rel_tol=0.05), f"{direction}: {got} A/s vs {expected_a_per_s}"
    bench.eload.set_slewrate(SlewRateDirection.BOTH, rate=SLEW_A_PER_US, channel=CHANNEL)


def test_output_enable_reads_back(bench: Bench) -> None:
    bench.eload.set_mode(LoadMode.CC, channel=CHANNEL)
    bench.eload.set_level(value=CC_LEVEL_A, channel=CHANNEL)
    bench.eload.output_enable(True, channel=CHANNEL)
    assert bench.visa.query(f"INP? (@{CHANNEL})").strip() == "1", "input did not turn on"
    bench.eload.output_enable(False, channel=CHANNEL)
    assert bench.visa.query(f"INP? (@{CHANNEL})").strip() == "0", "input did not turn off"


def test_measure_input_off_with_source_present(bench: Bench) -> None:
    time.sleep(SETTLE_S)  # the readback is averaged; let the prior input-off settle
    voltage = bench.eload.get_voltage(channel=CHANNEL).latest
    current = bench.eload.get_current(channel=CHANNEL).latest
    print(f"         input off: V = {voltage} V, I = {current} A")
    assert math.isclose(voltage, SOURCE_VOLTAGE_V, rel_tol=VOLTAGE_REL_TOL), f"{voltage} V vs source"
    assert abs(current) < CURRENT_ABS_TOL_A, f"current {current} A with input off"


@pytest.mark.parametrize(
    "mode,level,expected_a",
    [(LoadMode.CC, CC_LEVEL_A, 0.2), (LoadMode.CR, CR_LEVEL_OHM, 0.2), (LoadMode.CP, CP_LEVEL_W, 0.5)],
    ids=["CC", "CR", "CP"],
)
def test_loaded_measurement_is_sink_positive(bench: Bench, mode: LoadMode, level: float, expected_a: float) -> None:
    bench.eload.set_mode(mode, channel=CHANNEL)
    bench.eload.set_level(value=level, channel=CHANNEL)
    bench.eload.output_enable(True, channel=CHANNEL)
    try:
        time.sleep(SETTLE_S)
        voltage = bench.eload.get_voltage(channel=CHANNEL).latest
        current = bench.eload.get_current(channel=CHANNEL).latest
    finally:
        bench.eload.output_enable(False, channel=CHANNEL)
    print(f"         {mode.value} {level}: V = {voltage} V, I = {current} A")
    assert current > 0, f"current {current} A is not positive while sinking; sign convention needs negating"
    assert math.isclose(voltage, SOURCE_VOLTAGE_V, rel_tol=VOLTAGE_REL_TOL), f"{voltage} V vs source"
    assert math.isclose(current, expected_a, abs_tol=CURRENT_ABS_TOL_A), f"{current} A vs ~{expected_a} A"


def test_cross_check_against_supply(bench: Bench) -> None:
    for volts in CROSS_CHECK_VOLTS:
        bench.set_source(volts)
        load_v, load_i, psu_v, psu_i = bench.sink(LoadMode.CC, CC_LEVEL_A)
        assert math.isclose(load_i, psu_i, abs_tol=0.01), f"{volts} V: load {load_i} A vs supply {psu_i} A"
        assert math.isclose(load_v, psu_v, abs_tol=0.15), f"{volts} V: load {load_v} V vs supply {psu_v} V"
        assert math.isclose(load_i, CC_LEVEL_A, abs_tol=CURRENT_ABS_TOL_A), f"{load_i} A vs {CC_LEVEL_A} A"


def test_cv_current_limit_governs_sunk_current(bench: Bench) -> None:
    setpoint = CV_LEVEL_V - 1.5
    for limit in (0.3, 0.6):
        load_v, load_i, _, _ = bench.sink(LoadMode.CV, setpoint, curr_limit=limit)
        assert math.isclose(load_i, limit, abs_tol=CURRENT_ABS_TOL_A), f"limit {limit} A, sank {load_i} A"
        assert load_v > setpoint, f"{load_v} V: the load held its setpoint, so the limit did not govern"


def test_cv_regulates_to_setpoint_with_supply_in_current_limit(bench: Bench) -> None:
    bench.set_source(SOURCE_VOLTAGE_V, current_limit=0.3)
    load_v, load_i, _, psu_i = bench.sink(LoadMode.CV, 3.0, curr_limit=0.5)
    assert math.isclose(load_v, 3.0, abs_tol=0.1), f"CV held {load_v} V, expected 3.0 V"
    assert math.isclose(load_i, psu_i, abs_tol=0.01), f"load {load_i} A vs supply {psu_i} A"
    assert math.isclose(load_i, 0.3, abs_tol=0.03), f"{load_i} A vs the supply's 0.3 A limit"


@pytest.mark.usefixtures("high_voltage_ovp")
def test_hv_cross_check_against_supply(bench: Bench) -> None:
    for volts in HV_CROSS_CHECK_VOLTS:
        bench.set_source(volts)
        load_v, load_i, psu_v, psu_i = bench.sink(LoadMode.CC, HV_CC_LEVEL_A)
        assert math.isclose(load_i, psu_i, abs_tol=0.005), f"{volts} V: load {load_i} A vs supply {psu_i} A"
        assert math.isclose(load_v, psu_v, abs_tol=0.3), f"{volts} V: load {load_v} V vs supply {psu_v} V"
        assert math.isclose(load_i, HV_CC_LEVEL_A, abs_tol=0.01), f"{load_i} A vs {HV_CC_LEVEL_A} A"


@pytest.mark.usefixtures("high_voltage_ovp")
def test_hv_cr_and_cp_at_30v(bench: Bench) -> None:
    bench.set_source(30.0)
    _, cr_i, _, psu_i = bench.sink(LoadMode.CR, HV_CR_LEVEL_OHM)
    assert math.isclose(cr_i, 30.0 / HV_CR_LEVEL_OHM, abs_tol=0.01), f"CR drew {cr_i} A"
    assert math.isclose(cr_i, psu_i, abs_tol=0.005), f"CR: load {cr_i} A vs supply {psu_i} A"
    _, cp_i, _, psu_i = bench.sink(LoadMode.CP, HV_CP_LEVEL_W)
    assert math.isclose(cp_i, HV_CP_LEVEL_W / 30.0, abs_tol=0.01), f"CP drew {cp_i} A"
    assert math.isclose(cp_i, psu_i, abs_tol=0.005), f"CP: load {cp_i} A vs supply {psu_i} A"


@pytest.mark.usefixtures("high_voltage_ovp")
def test_hv_cv_regulates_with_supply_in_current_limit(bench: Bench) -> None:
    bench.set_source(30.0, current_limit=HV_CV_SOURCE_LIMIT_A)
    bench.eload.set_mode(LoadMode.CV, channel=CHANNEL)
    bench.eload.set_level(value=HV_CV_LEVEL_V, channel=CHANNEL)
    bench.eload.set_range(value=HV_CV_RANGE_V, channel=CHANNEL)
    range_max = float(bench.visa.query(f"VOLT:RANG? (@{CHANNEL})"))
    assert range_max >= HV_CV_RANGE_V, f"CV range max {range_max} V"
    load_v, load_i, _, psu_i = bench.sink(LoadMode.CV, HV_CV_LEVEL_V, curr_limit=0.15)
    assert math.isclose(load_v, HV_CV_LEVEL_V, abs_tol=0.3), f"CV held {load_v} V"
    assert math.isclose(load_i, psu_i, abs_tol=0.005), f"load {load_i} A vs supply {psu_i} A"
    assert math.isclose(load_i, HV_CV_SOURCE_LIMIT_A, abs_tol=0.01), f"{load_i} A vs supply limit"


def test_short_output_with_source_relay_open(bench: Bench) -> None:
    bench.disconnect_source()
    voltage = bench.eload.get_voltage(channel=CHANNEL).latest
    assert voltage <= SHORT_MAX_SOURCE_V, f"{voltage:.2f} V still present; refusing to short"
    bench.eload.short_output(True, channel=CHANNEL)
    try:
        assert int(bench.visa.query("INST:NSEL?")) == CHANNEL, "channel was not selected"
        assert bench.visa.query("INP:SHOR?").strip() == "1", "short did not engage"
    finally:
        bench.eload.short_output(False, channel=CHANNEL)
    assert bench.visa.query("INP:SHOR?").strip() == "0", "short did not release"


def test_bad_command_surfaces_as_error(bench: Bench) -> None:
    bench.visa.write("BOGUS:COMMAND")
    with pytest.raises(RuntimeError, match="KeysightEL30000 reported error"):
        bench.eload.get_voltage(channel=CHANNEL)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-m", "hardware", "-s", "-q"]))
