"""Fluke 8845A DMM driver: the Agilent 34401A driver, run against the 8845A's 34401A-compatible command set."""

from __future__ import annotations

from instro.dmm.drivers import Agilent34401A
from instro.lib.transports.visa import VisaConfig


class Fluke8845A(Agilent34401A):
    """Fluke 8845A 6.5-digit DMM, driven through its Agilent 34401A-compatible ``8845`` command set.

    The meter's default remote command set (``8845``) is compatible with the Agilent 34401A, so
    every command comes from `Agilent34401A` and this class adds none. Range and resolution are
    sent in short form (``10,1e-05``): over RS-232 the meter intermittently withholds replies to
    the ``1.000000e+01,1.000000e-05`` form at 5½ and 6½ digits. Two front-panel settings
    must be made before use; neither can be changed over the remote interface, and both are
    non-volatile:

    * **Command set** ``8845`` (INSTR SETUP → COMMANDS → 8845). The Fluke 45 emulation command set
      is not supported.
    * **RS-232 mode** ``COMPUTER`` (INSTR SETUP → PORT IF → RS232 → COMP/TERM ECHO → COMPUTER).
      ``TERMINAL`` mode echoes each command and prints a ``NNN>`` prompt, which the driver reads
      back as the reply; ``open`` then fails with an error such as
      ``Fluke 8845A reported error: *CLS``.

    Over RS-232, also match the meter's line settings to the ``VisaConfig`` defaults: 9600 baud,
    8 data bits, no parity, 1 stop bit, no flow control, and EOL ``CR LF``.
    """

    def __init__(self, visa_resource: str | VisaConfig) -> None:
        """Create the driver; the connection opens on ``open``.

        Args:
            visa_resource (str | VisaConfig): VISA resource string, e.g. ``"ASRL6::INSTR"`` for
                RS-232 on COM6, or a `VisaConfig` to customize the serial line or backend.

        Example::

            from instro.dmm import InstroDMM, MeasurementFunction
            from instro.unstable.dmm.drivers import Fluke8845A

            dmm = InstroDMM(name="fluke", driver=Fluke8845A("ASRL6::INSTR"))
            dmm.open()
            dmm.set_measurement_function(MeasurementFunction.DC_VOLTAGE)
            volts = dmm.read().latest
            dmm.close()
        """
        super().__init__(visa_resource)

    def _build_meas_cmd(self, base_cmd: str) -> str:
        if self._range is None:
            return f"{base_cmd}?"
        if self._resolution is None:
            return f"{base_cmd}? {self._range:.7g}"
        return f"{base_cmd}? {self._range:.7g},{self._resolution:.7g}"

    def _check_errors(self) -> None:
        err = self._visa.query("SYST:ERR?")
        parts = err.strip().split(",", 1)
        code_str = parts[0] if parts else ""
        code_val = int(code_str) if code_str.lstrip("-+").isdigit() else -1
        if code_val != 0:
            raise RuntimeError(f"Fluke 8845A reported error: {err.strip()}")
