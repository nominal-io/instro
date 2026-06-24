"""Hardware validation for the Total Phase Aardvark I2C driver.

Three phases against one adapter session:

  Phase A - raw driver contract. Exercises every method the ``Aardvark`` driver
    implements (``packages/instro-i2c-aardvark/instro/i2c/drivers/totalphase/aardvark.py``):
    ``open``, ``close``, ``set_bitrate``, ``set_pullups``, ``set_power_enable``,
    ``write``, ``read``, ``write_read``.

  Phase B - SystemDefinition + I2CInterface. Drives the same adapter through the
    public HAL with a real ``SystemDefinition`` (the way users actually use the
    driver): register read/write roundtrips, field-level read-modify-write
    (lights the board LEDs), and ``reset_reg``.

  Phase C - dense streaming. Runs the background daemon at a fixed rate, driving
    the PCA9554 output register with a sine waveform and reading it back, so the
    test produces a dense (non-sparse) time series rather than a few scattered
    points.

Optional Nominal Core publishing:
    Set the ``DATASET_RID`` constant below to a dataset RID to stream this test's
    measurements/commands to Nominal Core (credentials come from the on-disk
    ``default`` profile). Left as ``None``, no publisher is attached and the test
    still runs and validates fully. With it set, Phase C streams a clean
    commanded-vs-read-back sine you can plot in Core.

Target hardware:
    Total Phase Aardvark I2C/SPI host adapter
    + Total Phase I2C/SPI Activity Board (https://www.totalphase.com/products/activity-board/)

    Onboard I2C targets used here:
      - AT24C02 EEPROM         @ 0x50  (256 B, 1-byte register addressing, 8-byte pages)
      - PCA9554 I/O expander   @ 0x38  (drives the board LEDs)

    The Activity Board is powered from the Aardvark target-power line, so the
    script enables target power (and the internal pull-ups) before talking to
    the onboard devices, and turns target power back off in ``finally``.

Run:
    uv run --extra i2c python tests/i2c/totalphase/test_aardvark_hardware.py

Recorded firmware/hardware versions of the tested unit are printed at the top of
the run and asserted non-empty; see the PR description for the values observed.
"""

from __future__ import annotations

import math
import sys
import time

import pytest

from instro.i2c import I2CInterface, RegisterDevice, SystemDefinition
from instro.i2c.drivers.totalphase import Aardvark
from instro.i2c.types import DataFormat, FieldDef, RegisterDef
from instro.lib.publishers import NominalCorePublisher

# --- HARDWARE TEST SETUP - EDIT THESE VALUES BEFORE RUNNING ------------------
# None selects the first available adapter; set to a serial (e.g. "2239-764425")
# to pin a specific unit.
SERIAL_NUMBER: str | None = None

# Optionally publish to Nominal Core: set this to a dataset RID to stream this
# test's data (including the Phase C dense sine) to that dataset; leave it None to
# run without any publisher.
DATASET_RID: str | None = None

NAME = "hw_validate"  # Channel-name prefix used by the I2CInterface.
BITRATE_KHZ = 100  # Aardvark snaps to the nearest supported rate.
ENABLE_PULLUPS = True
ENABLE_TARGET_POWER = True  # Activity Board is powered from the Aardvark.

# Phase C streaming parameters.
STREAM_RATE_HZ = 20  # Background-daemon publish rate; high enough for dense plots.
STREAM_WAVEFORM_HZ = 0.5  # Sine period 2 s, so a run shows several clean cycles.
STREAM_DURATION_S = 15  # How long the dense streaming phase runs.

# AT24C02 EEPROM on the Activity Board.
EEPROM_ADDRESS = 0x50
EEPROM_SCRATCH_REG = 0x10  # Byte offset used for the R/W roundtrip; restored after.
EEPROM_WRITE_CYCLE_S = 0.01  # AT24C02 self-timed write cycle (~5 ms max).

# PCA9554 I/O expander on the Activity Board (standard register map).
PCA9554_ADDRESS = 0x38
PCA9554_INPUT_REG = 0x00
PCA9554_OUTPUT_REG = 0x01  # Power-on default 0xFF; readback returns last written value.
PCA9554_POLARITY_REG = 0x02  # Pure R/W register, default 0x00, no electrical effect.
PCA9554_CONFIG_REG = 0x03  # 1 = input (default 0xFF), 0 = output.
# -----------------------------------------------------------------------------


def activity_board_system() -> SystemDefinition:
    """SystemDefinition describing the two onboard I2C devices used by Phases B and C."""
    eeprom = RegisterDevice(
        name="eeprom",
        address=EEPROM_ADDRESS,
        addr_width_bytes=1,
        registers={
            "scratch": RegisterDef(
                alias="scratch",
                register=EEPROM_SCRATCH_REG,
                format=DataFormat(transfer_bits=8),
            ),
        },
    )

    led0 = FieldDef(name="led0", lsb=0, width_bits=1)
    led1 = FieldDef(name="led1", lsb=1, width_bits=1)
    pca9554 = RegisterDevice(
        name="pca9554",
        address=PCA9554_ADDRESS,
        addr_width_bytes=1,
        registers={
            "config": RegisterDef(
                alias="config", register=PCA9554_CONFIG_REG, default_value=0xFF, format=DataFormat(transfer_bits=8)
            ),
            "output": RegisterDef(
                alias="output",
                register=PCA9554_OUTPUT_REG,
                default_value=0xFF,
                format=DataFormat(transfer_bits=8),
                fields={led0.name: led0, led1.name: led1},
            ),
            "polarity": RegisterDef(
                alias="polarity", register=PCA9554_POLARITY_REG, default_value=0x00, format=DataFormat(transfer_bits=8)
            ),
        },
    )

    return SystemDefinition(devices={eeprom.name: eeprom, pca9554.name: pca9554})


def _make_i2c() -> I2CInterface:
    """Construct the I2CInterface, optionally attaching a NominalCorePublisher."""
    i2c = I2CInterface(
        name=NAME,
        driver=Aardvark(serial_number=SERIAL_NUMBER),
        system_definition=activity_board_system(),
        publishers=None,
    )
    if DATASET_RID:
        i2c.add_publisher(NominalCorePublisher(dataset_rid=DATASET_RID))
    return i2c


def _run(name: str, fn, failures: list) -> None:
    """Run one check, print OK/FAIL, and accumulate failures instead of aborting."""
    try:
        fn()
        print(f"  [OK]   {name}")
    except Exception as exc:  # noqa: BLE001 - report, don't abort
        print(f"  [FAIL] {name}: {exc}")
        failures.append((name, exc))


def _hal_value(measurement, channel: str) -> int:
    """Unwrap the first sample for ``channel`` from a Measurement as an int."""
    return int(measurement.channel_data[channel][0])


# --- Phase A: raw driver contract -------------------------------------------


def _assert_identity(device) -> None:
    assert device.firmware_version, "firmware_version is empty"
    assert device.hardware_revision, "hardware_revision is empty"
    assert device.unique_id_str(), "unique_id_str is empty"


def _assert_bitrate(driver: Aardvark, device) -> None:
    driver.set_bitrate(BITRATE_KHZ)
    actual = device.i2c_bitrate  # kHz, snapped to the nearest supported rate.
    assert abs(actual - BITRATE_KHZ) <= 25, f"requested {BITRATE_KHZ} kHz, adapter reports {actual} kHz"


def _eeprom_byte_roundtrip(driver: Aardvark, value: int) -> None:
    """write() a scratch byte and read it back via write_read()."""
    driver.write(EEPROM_ADDRESS, bytes([EEPROM_SCRATCH_REG, value]))
    time.sleep(EEPROM_WRITE_CYCLE_S)
    read_back = driver.write_read(EEPROM_ADDRESS, bytes([EEPROM_SCRATCH_REG]), 1)
    if read_back != bytes([value]):
        raise AssertionError(f"wrote 0x{value:02X}, read back {read_back!r}")


def _assert_plain_read(driver: Aardvark) -> None:
    # Seed a known value, set the address pointer with a bare write, then read().
    driver.write(EEPROM_ADDRESS, bytes([EEPROM_SCRATCH_REG, 0x3C]))
    time.sleep(EEPROM_WRITE_CYCLE_S)
    driver.write(EEPROM_ADDRESS, bytes([EEPROM_SCRATCH_REG]))  # set internal address pointer
    read_back = driver.read(EEPROM_ADDRESS, 1)
    assert read_back == bytes([0x3C]), f"read() returned {read_back!r}, expected 0x3C"


def _assert_pca9554_roundtrip(driver: Aardvark) -> None:
    original = driver.write_read(PCA9554_ADDRESS, bytes([PCA9554_POLARITY_REG]), 1)
    try:
        for value in (0xAA, 0x55):
            driver.write(PCA9554_ADDRESS, bytes([PCA9554_POLARITY_REG, value]))
            read_back = driver.write_read(PCA9554_ADDRESS, bytes([PCA9554_POLARITY_REG]), 1)
            assert read_back == bytes([value]), f"wrote 0x{value:02X}, read back {read_back!r}"
    finally:
        driver.write(PCA9554_ADDRESS, bytes([PCA9554_POLARITY_REG]) + original)


# --- Phase B: SystemDefinition + I2CInterface --------------------------------


def _hal_eeprom_roundtrip(i2c: I2CInterface, value: int) -> None:
    i2c.write("eeprom", "scratch", value)
    time.sleep(EEPROM_WRITE_CYCLE_S)
    got = _hal_value(i2c.read("eeprom", "scratch"), f"{NAME}.eeprom.scratch")
    if got != value:
        raise AssertionError(f"HAL wrote {value}, read back {got}")


def _hal_pca_polarity_roundtrip(i2c: I2CInterface) -> None:
    channel = f"{NAME}.pca9554.polarity"
    original = _hal_value(i2c.read("pca9554", "polarity"), channel)
    try:
        for value in (0xAA, 0x55):
            i2c.write("pca9554", "polarity", value)
            got = _hal_value(i2c.read("pca9554", "polarity"), channel)
            if got != value:
                raise AssertionError(f"HAL wrote 0x{value:02X}, read back 0x{got:02X}")
    finally:
        i2c.write("pca9554", "polarity", original)


def _hal_pca_field_and_reset(i2c: I2CInterface) -> None:
    """Field-level read-modify-write through the HAL (lights LEDs), plus reset_reg."""
    output_ch = f"{NAME}.pca9554.output"
    i2c.write("pca9554", "config", 0x00)  # all lines outputs so the LEDs respond
    try:
        i2c.write("pca9554", "output", 0xFF)  # known baseline (all bits high)

        # Clear led0 via a field RMW; only bit 0 should change (0xFF -> 0xFE).
        i2c.write("pca9554", "output", 0, field="led0")
        full = _hal_value(i2c.read("pca9554", "output"), output_ch)
        if full != 0xFE:
            raise AssertionError(f"after led0=0 expected 0xFE, got 0x{full:02X}")
        bit = _hal_value(i2c.read("pca9554", "output", field="led0"), f"{output_ch}.led0")
        if bit != 0:
            raise AssertionError(f"led0 field read expected 0, got {bit}")

        # Clear led1 too (0xFE -> 0xFC); proves RMW preserves the other cleared bit.
        i2c.write("pca9554", "output", 0, field="led1")
        full = _hal_value(i2c.read("pca9554", "output"), output_ch)
        if full != 0xFC:
            raise AssertionError(f"after led1=0 expected 0xFC, got 0x{full:02X}")

        # reset_reg writes the register's defined default (0xFF) back to hardware.
        i2c.reset_reg("pca9554", "output")
        full = _hal_value(i2c.read("pca9554", "output"), output_ch)
        if full != 0xFF:
            raise AssertionError(f"reset_reg expected 0xFF, got 0x{full:02X}")
    finally:
        i2c.write("pca9554", "output", 0xFF)
        i2c.write("pca9554", "config", 0xFF)  # restore power-on default (all inputs)


# --- Phase C: dense streaming via the background daemon ----------------------


def _stream_tick(i2c: I2CInterface) -> None:
    """One daemon tick: command a sine sample, write it, read it back (both publish)."""
    value = round(127.5 + 127.5 * math.sin(2 * math.pi * STREAM_WAVEFORM_HZ * time.time()))
    value = max(0, min(255, value))
    i2c.write("pca9554", "output", value)  # publishes pca9554.output.cmd
    i2c.read("pca9554", "output")  # publishes pca9554.output (read-back)


def _stream_and_verify(i2c: I2CInterface) -> None:
    """Run a fixed-rate sine stream and confirm the data is dense (non-sparse) and varying."""
    output_ch = f"{NAME}.pca9554.output"
    i2c.write("pca9554", "config", 0x00)  # all lines outputs so the LEDs animate
    i2c.background_interval = 1.0 / STREAM_RATE_HZ
    i2c.add_background_daemon_function(_stream_tick, i2c)
    i2c.start()
    try:
        time.sleep(STREAM_DURATION_S)
        # Read the most recent buffered samples while the daemon is still running.
        samples = i2c.get_channel(output_ch, length=10).channel_data[output_ch]
        if len(samples) < 10:
            raise AssertionError(f"expected a dense buffer, only got {len(samples)} samples")
        if len(set(samples)) <= 1:
            raise AssertionError(f"expected a varying waveform, samples are flat: {samples}")
    finally:
        i2c.stop()
        i2c.write("pca9554", "output", 0xFF)
        i2c.write("pca9554", "config", 0xFF)


def run_all() -> list:
    i2c = _make_i2c()
    failures: list = []

    # I2CInterface.open() opens the underlying Aardvark driver.
    i2c.open()
    driver: Aardvark = i2c._driver  # type: ignore[assignment]
    try:
        device = driver._device  # pyaardvark handle; exposes version/identity strings.

        print("Connected to Aardvark adapter:")
        print(f"  unique id          = {device.unique_id_str()}")
        print(f"  firmware version   = {device.firmware_version}")
        print(f"  hardware revision  = {device.hardware_revision}")
        print(f"  api (sw) version   = {device.api_version}")
        print(f"  publishing to Core = {'yes (' + DATASET_RID + ')' if DATASET_RID else 'no (DATASET_RID is None)'}")
        print()

        # --- Phase A: raw driver contract ---
        print("Phase A - raw driver contract:")
        _run("open() + firmware/hardware identity reported", lambda: _assert_identity(device), failures)
        _run(f"set_bitrate({BITRATE_KHZ}) snaps near requested rate", lambda: _assert_bitrate(driver, device), failures)
        _run(f"set_pullups({ENABLE_PULLUPS})", lambda: driver.set_pullups(ENABLE_PULLUPS), failures)
        _run(f"set_power_enable({ENABLE_TARGET_POWER})", lambda: driver.set_power_enable(ENABLE_TARGET_POWER), failures)

        # Give the Activity Board a moment to power up before addressing it.
        if ENABLE_TARGET_POWER:
            time.sleep(0.2)

        original = driver.write_read(EEPROM_ADDRESS, bytes([EEPROM_SCRATCH_REG]), 1)
        print(f"  (EEPROM[0x{EEPROM_SCRATCH_REG:02X}] original = {original!r}, restored at end)")
        _run("write() + write_read() EEPROM roundtrip (0x5A)", lambda: _eeprom_byte_roundtrip(driver, 0x5A), failures)
        _run("write() + write_read() EEPROM roundtrip (0xA5)", lambda: _eeprom_byte_roundtrip(driver, 0xA5), failures)
        _run("read() sequential read after pointer set", lambda: _assert_plain_read(driver), failures)
        _run("write() + write_read() PCA9554 register roundtrip", lambda: _assert_pca9554_roundtrip(driver), failures)

        # Restore the EEPROM scratch byte to its original value.
        driver.write(EEPROM_ADDRESS, bytes([EEPROM_SCRATCH_REG]) + original)
        time.sleep(EEPROM_WRITE_CYCLE_S)

        # --- Phase B: SystemDefinition / I2CInterface ---
        print("\nPhase B - SystemDefinition + I2CInterface:")
        hal_original = _hal_value(i2c.read("eeprom", "scratch"), f"{NAME}.eeprom.scratch")
        _run("I2CInterface EEPROM register roundtrip (write/read)", lambda: _hal_eeprom_roundtrip(i2c, 0x42), failures)
        i2c.write("eeprom", "scratch", hal_original)  # restore
        time.sleep(EEPROM_WRITE_CYCLE_S)
        _run("I2CInterface PCA9554 register roundtrip (write/read)", lambda: _hal_pca_polarity_roundtrip(i2c), failures)
        _run("I2CInterface field RMW (led0/led1) + reset_reg", lambda: _hal_pca_field_and_reset(i2c), failures)

        # --- Phase C: dense streaming ---
        print(f"\nPhase C - dense streaming ({STREAM_RATE_HZ} Hz sine for {STREAM_DURATION_S}s):")
        _run("background-daemon sine stream is dense and varying", lambda: _stream_and_verify(i2c), failures)

    finally:
        # Restore a safe state: target power off, then close the interface (and driver).
        try:
            driver.set_power_enable(False)
        except Exception as exc:  # noqa: BLE001
            print(f"  [WARN] could not disable target power: {exc}")
        i2c.close()

    return failures


@pytest.mark.hardware
def test_aardvark_hardware():
    failures = run_all()
    assert not failures, f"{len(failures)} hardware check(s) failed: {failures}"


def main() -> int:
    failures = run_all()
    print(f"\n{'PASSED' if not failures else f'FAILED ({len(failures)})'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
