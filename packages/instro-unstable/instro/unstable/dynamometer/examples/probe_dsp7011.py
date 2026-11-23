"""Find which commands a Magtrol DSP7011 rejects. Run with the motor stopped.

./.venv/Scripts/python.exe packages/instro-unstable/instro/unstable/dynamometer/examples/probe_dsp7011.py ASRL3::INSTR
"""

import sys

import pyvisa

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
    "I1",
    "N1",
    "Q1",
    "I",
    "N",
    "Q",  # brake off, both forms
]

rm = pyvisa.ResourceManager()
inst = rm.open_resource(RESOURCE)
inst.baud_rate = 9600
inst.write_termination = "\r\n"
inst.read_termination = "\n"
inst.timeout = 500
inst.clear()

for command in COMMANDS:
    inst.write(command)
    replies = []
    try:
        while True:
            replies.append(inst.read().strip())
    except pyvisa.errors.VisaIOError:
        pass
    print(f"{command:<14} -> {replies or '(silent)'}")

inst.close()
