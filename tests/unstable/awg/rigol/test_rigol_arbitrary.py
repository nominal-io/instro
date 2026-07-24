"""Standalone hardware test: reproduce the DG1000Z manual's arbitrary waveform example verbatim.

Manual: "To Output Arbitrary Waveform", DG1000Z Programming Guide p.3-2. Bypasses
RigolDG1022Z.set_waveform's own command sequence entirely, and is isolated from
test_rigol_dg1022z_hardware.py so it can be run alone without commenting out other
tests. See INSTRO-410 for the investigation history: this exact sequence, run via a
bare VISA probe, hung the firmware on the very first download over USB. Confirm or
refute that here through the driver's own transport, and try other resource strings
(e.g. LAN) by editing VISA_RESOURCE.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest

from instro.lib.transports import VisaConfig
from instro.unstable.awg.drivers import RigolDG1022Z

pytestmark = pytest.mark.hardware

# HARDWARE TEST SETUP - EDIT THESE VALUES BEFORE RUNNING THIS FILE.
# Set VISA_RESOURCE to the bench unit's VISA resource string. Set VISA_BACKEND to
# "@ivi" or "" for the system VISA library, or "@py" for pyvisa-py.
VISA_RESOURCE = "USB0::6833::1602::DG1ZA000000000::0::INSTR"
VISA_BACKEND = "@py"

# DG1000Z Programming Guide, "To Output Arbitrary Waveform" example (p.3-2):
# sample rate output mode, frequency/sample-rate 500, and a 10-point waveform.
ARB_SAMPLE_RATE = 500
ARB_SAMPLES = (-0.6, -0.4, -0.3, -0.1, 0.0, 0.1, 0.2, 0.3, 0.5, 0.7)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _shutdown_output(driver: RigolDG1022Z) -> None:
    driver.output_enable(1, False)


def _reset_driver(driver: RigolDG1022Z) -> None:
    driver._visa.write("*CLS")
    driver._visa.write("*RST")
    time.sleep(0.5)
    driver._visa.write("*CLS")
    driver.check_errors()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def driver() -> Iterator[RigolDG1022Z]:
    awg_driver = RigolDG1022Z(
        VisaConfig(
            visa_resource=VISA_RESOURCE,
            visa_backend=VISA_BACKEND,
        )
    )
    opened = False
    try:
        awg_driver.open()
        opened = True
        yield awg_driver
    finally:
        if opened:
            _shutdown_output(awg_driver)
        awg_driver.close()


@pytest.fixture(autouse=True)
def reset_before_each_test(driver: RigolDG1022Z) -> None:
    _reset_driver(driver)


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


def test_01_arbitrary_waveform_per_manual_example(driver: RigolDG1022Z) -> None:
    """Send the manual's 4-step example verbatim, checking errors after each step."""
    idn = driver._visa.query("*IDN?")
    print(f"\nIDN: {idn.strip()}")

    breakpoint()

    print(f"> :SOUR1:APPL:ARB {ARB_SAMPLE_RATE}")
    driver._visa.write(f":SOUR1:APPL:ARB {ARB_SAMPLE_RATE}")
    driver.check_errors()

    data = ",".join(str(sample) for sample in ARB_SAMPLES)
    print(f"> :SOUR1:DATA VOLATILE,{data}")
    driver._visa.write(f":SOUR1:DATA VOLATILE,{data}")
    driver.check_errors()

    print("> :OUTP1 ON")
    driver._visa.write(":OUTP1 ON")
    driver.check_errors()

    points = driver._visa.query(":SOUR1:TRAC:DATA:POIN? VOLATILE").strip()
    print(f"POINts? -> {points}")
    assert points == str(len(ARB_SAMPLES))
