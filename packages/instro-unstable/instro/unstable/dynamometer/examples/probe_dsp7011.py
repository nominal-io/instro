"""Find which commands a Magtrol DSP7011 rejects, showing every raw reply byte. Run with the motor stopped.

./.venv/Scripts/python.exe packages/instro-unstable/instro/unstable/dynamometer/examples/probe_dsp7011.py ASRL3::INSTR
"""

import sys

import pyvisa

from instro.lib.transports.visa import VisaDriver

RESOURCE = sys.argv[1] if len(sys.argv) > 1 else "ASRL3::INSTR"
COMMANDS = [
    "*IDN?",
    "OD1",
    "OD",
    "STAT1",
    "STAT",
    "UR1,8",
    "UR8",
    "ALS1,4000.0",
    "ALT1,2.000",
    "ALP1,0.500",
    "ALL1,0",
    "ALS4000.0",
    "ALL0",
    "Q1,0.000",
    "Q0.000",
    "I1",  # brake off, both forms
    "N1",
    "Q1",
    "I",
    "N",
    "Q",
]


def drain(visa: VisaDriver) -> list[bytes]:
    replies = []
    with visa.temporary_timeout(700):
        while True:
            try:
                replies.append(visa.read_raw())
            except pyvisa.errors.VisaIOError:
                return replies


visa = VisaDriver(RESOURCE)
visa.open()
try:
    print(f"{'(stale)':<14} -> {drain(visa) or '(none)'}")
    for command in COMMANDS:
        try:
            visa.write(command)
            print(f"{command:<14} -> {drain(visa) or '(silent)'}")
        except pyvisa.errors.VisaIOError as exc:
            print(f"{command:<14} -> VISA error: {exc}")
finally:
    visa.close()
