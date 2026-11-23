"""Show the raw bytes a Magtrol DSP7011 sends after each command. Run with the motor stopped.

Sends only commands documented in the DSP7010 manual (Aug 2026), channel 1. Changes torque
units to Nm, the channel 1 alarm limits, and disables channel 1 alarms; ends with the brake off.

    ./.venv/Scripts/python.exe packages/instro-unstable/instro/unstable/dynamometer/examples/probe_dsp7011.py ASRL3::INSTR
"""

import sys

import pyvisa

from instro.lib.transports.visa import VisaDriver

RESOURCE = sys.argv[1] if len(sys.argv) > 1 else "ASRL3::INSTR"
COMMANDS = [
    "*IDN?",  # p.68
    "OD1",  # p.68
    "STAT1",  # p.68
    "UR1,8",  # p.72
    "ALS1,4000.0",  # p.67
    "ALT1,2.000",  # p.67
    "ALP1,0.500",  # p.67
    "ALL1,0",  # p.67
    "OD1",
    "Q1,0.000",  # p.73: brake on at zero torque
    "OD1",
    "I1",  # p.74
    "N1",  # p.72: brake off
    "Q1",  # p.73: brake off
    "OD1",
]


def drain(visa: VisaDriver) -> list[bytes]:
    replies = []
    with visa.temporary_timeout(1000):  # manual p.66: at least one second
        while True:
            try:
                replies.append(visa.read_raw())
            except pyvisa.errors.VisaIOError:
                return replies


visa = VisaDriver(RESOURCE)
visa.open()
try:
    print(f"{'(pending)':<14} -> {drain(visa) or '(none)'}")
    for command in COMMANDS:
        try:
            visa.write(command)
            print(f"{command:<14} -> {drain(visa) or '(silent)'}")
        except pyvisa.errors.VisaIOError as exc:
            print(f"{command:<14} -> VISA error: {exc}")
finally:
    visa.close()
