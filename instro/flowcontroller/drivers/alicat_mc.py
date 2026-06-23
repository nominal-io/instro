"""Alicat MC-series mass-flow controller driver.

Hardware protocol: ASCII polling over RS-232 (default 19200 baud, 8N1).
Reference: Alicat Gas Flow Controller Manual, pp. 42–50.
https://documents.alicat.com/manuals/Gas_Flow_Controller_Manual.pdf
"""


# Driver features expected:
# Tare
# [unitid]v\r
# Barometer tare
# [unitid]pc\r
# collect single sample
# [unitid]\r
# returns:
# UnitID AbsPressure Temp VolumetricFlow StandardMassFlow Setpoint GasTypeString
# Units for the above are in pre-selected EU and may be diff from the display
# Request data frame description
# [unitid]??d*\r
# same format as above, may also return status messages to the right of the gas column

# setpoints
# confirm that its setpoint source is set to Serial/Front Panel by selecting
# MENU → CONTROL → ADV CONTROL → SETPT SOURCE
# [unitid]s[setpoint as float]\r
# ex: as15.44\r or as-15.44\r
# can also sent as integers using equation:
# 64000 * setpoint/full-scale
# ex: 64000 * 15.44/20 = 49408
# command is thus:
# a[integer setpoint]\r
# ex: [unitid]49408\r
# If bidirectional, full scale includes +/-
# so if its +/-20, 0=-20, 64000=+20
# so -15.44 over 40, in this example is [(-15.44/40) - (-20/40)]*64000 = 64000*(20-15.44)/40

# gas select
# [unitid]g[gasnumber]\r
# defining a gas mix is:
# [unitid]gm [mix name - 6az] [mix number 236-255, 0=next] [gas1%2d] [gas1number] ... [2-5 gas types]\r
# gas list info = a??g*\r

# hold valve
# at pos: [unitid]hp\r
# at close: [unitid]hc\r
# unhold: [unitid]c\r
from __future__ import annotations

import logging
import re
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal

from pyvisa import VisaIOError

from instro.flowcontroller import FlowControllerDriverBase
from instro.flowcontroller.types import FlowData
from instro.lib.transports.visa import SerialConfig, TerminatorConfig, VisaConfig, VisaDriver

logger = logging.getLogger(__name__)


def _default_alicat_config(visa_resource: str) -> VisaConfig:
    return VisaConfig(
        visa_resource=visa_resource,
        serial_config=SerialConfig(baud_rate=19200),
        terminator=TerminatorConfig(read="\r", write="\r"),
    )


@dataclass
class GasMixEntry:
    gas_percentage: Decimal  # valid to 2 digits of precision
    gas_number: int  # fetch using list_gas_types

    @property
    def serialized_gas_percentage(self) -> str:
        return f"{self.gas_percentage:.2f}"

    @staticmethod
    def sum_mixture_percentages(entries: list[GasMixEntry]) -> Decimal:
        sum = Decimal("0.00")
        for entry in entries:
            if isinstance(entry.gas_percentage, Decimal):
                sum = sum + entry.gas_percentage
            else:
                sum = sum + Decimal(entry.serialized_gas_percentage)
        return sum


@dataclass
class GasTypeEntry:
    identifier: int
    name: str

    @staticmethod
    def parse(line: str) -> GasTypeEntry:
        # Format: '{unit_id} G{number} {name}', e.g. 'M G02      CH4 '
        fields = line.split()
        if len(fields) < 3:
            raise RuntimeError(f"AlicatMC: unexpected gas type line: {line!r}")
        return GasTypeEntry(identifier=int(fields[1][1:]), name=fields[2])


@dataclass
class MeasurementHeaderEntry:
    index: int
    identifier: int
    name: str
    data_type: str
    width: str
    notes: str

    @staticmethod
    def parse_column_spans(header_line: str) -> tuple[int, int, int, int]:
        # Header: 'M D00 ID_ NAME______ TYPE_______ WIDTH NOTES___'
        # Tokens 0-2 are fixed prefix fields; 3-6 are the variable-width columns.
        tokens = list(re.finditer(r"\S+", header_line))
        if len(tokens) < 7:
            raise RuntimeError(f"AlicatMC: cannot parse measurement column header: {header_line!r}")
        return (tokens[3].start(), tokens[4].start(), tokens[5].start(), tokens[6].start())

    @staticmethod
    def parse(line: str, col_spans: tuple[int, int, int, int]) -> MeasurementHeaderEntry:
        # Format: 'M D{nn} {id} {name...}{type...}{width...}{notes...}'
        # Example: 'M D02 002 Abs Press        s decimal   7/2 010 02 PSIA'
        slot = line[2:5]  # 'D01', 'D02', ...
        raw_id = line[6:9]  # '700', '002'; 'ID_' on the D00 header row raises below
        try:
            index = int(slot[1:])
            identifier = int(raw_id)
        except ValueError as exc:
            raise RuntimeError(f"AlicatMC: unexpected measurement header line: {line!r}") from exc
        name_s, type_s, width_s, notes_s = col_spans
        return MeasurementHeaderEntry(
            index=index,
            identifier=identifier,
            name=line[name_s:type_s].strip(),
            data_type=line[type_s:width_s].strip(),
            width=line[width_s:notes_s].strip(),
            notes=line[notes_s:].strip(),
        )


class AlicatMC(FlowControllerDriverBase):
    """Alicat MC-series mass-flow controller (MC-100SCCM and related MC models).

    Communicates in RS-232 polling mode. ``device_id`` is the single-letter
    address (A–Z) configured on the device; default is ``"A"``.
    Default baud is 19200 with 8data-1stop-none_parity-none_flow
    Termination is always carriage return.
    """

    unit_id: str
    _visa: VisaDriver
    known_gas_types: list[GasTypeEntry]
    measurement_headings: list[MeasurementHeaderEntry]

    def __init__(self, visa_resource: str | VisaConfig, device_id: str = "A") -> None:
        self.unit_id = device_id
        if isinstance(visa_resource, str):
            visa_resource = _default_alicat_config(visa_resource)
        self._visa = VisaDriver(visa_resource)
        self.known_gas_types = []
        self.measurement_headings = []

    def open(self) -> None:
        self._visa.open()

    def close(self) -> None:
        self._visa.close()

    def _query_checked(self, command: str) -> str:
        response = self._visa.query(command)
        if response == "?":
            raise RuntimeError(f"Error running command {command}, device returned ?")
        return response

    ###TARE
    def tare_flow(self) -> FlowData:
        try:
            response = self._query_checked(f"{self.unit_id}v")
        except RuntimeError as e:
            raise NotImplementedError(
                f"The currently selected device with unit ID={self.unit_id} does not support flow rate tare-ing"
            )
        return self._parse_flowdata(response)

    def tare_barometer(self) -> FlowData:
        try:
            response = self._query_checked(f"{self.unit_id}pc")
        except RuntimeError as e:
            raise NotImplementedError(
                f"The currently selected device with unit ID={self.unit_id} does not support barometer tare-ing"
            )
        return self._parse_flowdata(response)

    ###GAS TYPES
    def list_gas_types(self, refresh=False) -> list[GasTypeEntry]:
        if self.known_gas_types is None or len(self.known_gas_types) == 0 or refresh:
            gas_types_list: list[GasTypeEntry] = []
            with self._visa.temporary_timeout(250):
                with self._visa.lock():
                    self._visa.write(f"{self.unit_id}??g*")
                    for _ in range(256):
                        gas_type_line = None
                        try:
                            gas_type_line = self._visa.read()
                        except VisaIOError as e:  # we expect a timeout when we run out of items to read
                            if e.abbreviation == "VI_ERROR_TMO":
                                break
                            else:
                                raise
                        if gas_type_line:
                            try:
                                gas_type = GasTypeEntry.parse(gas_type_line)
                                if gas_type is not None:
                                    gas_types_list.append(gas_type)
                            except Exception as e:  # less clear why this might happen, log
                                logger.error(f"Exception {e} occurred while parsing gas type entries", exc_info=True)
            if gas_types_list:
                self.known_gas_types = gas_types_list
        gas_types_list = deepcopy(self.known_gas_types)  # since we cache this, don't give users access to the real list
        return gas_types_list

    def select_gas(self, gas_name: str) -> str:
        if not self.known_gas_types:
            self.list_gas_types()
        gas_number = None
        for known_gas_type in self.known_gas_types:
            if gas_name.lower() == known_gas_type.name.lower():
                gas_number = known_gas_type.identifier
                break
        if gas_number is None:
            raise ValueError(
                f"Unable to locate {gas_name} in list of all known gas types: "
                f"{[kgt.name for kgt in self.known_gas_types]}"
            )
        response = self._query_checked(f"{self.unit_id}g{gas_number}")
        measurement = self._parse_flowdata(response)
        return measurement.gas

    def define_gas_mixture(self, mix_name: str, mixture: list[GasMixEntry], gas_id: int = 0) -> GasTypeEntry:
        """Allows defining an arbitrary gas mixture of 2-5 components.

        `mix_name` is an alias for the mixture. Use a maximum of 6 letters (upper and/or lower case),
        numbers and symbols (space, period or hyphen only).

        `gas_id` is a number from 236-255, selecting 0 will get the next available ID

        Returns selected gas_id. If `gas_id` is not 0, it should return `gas_id`.
        If `gas_id` is 0, it should return an integer from 236-255.
        """
        # [unitid]gm [mix name - 6az] [mix number 236-255, 0=next] [gas1%2d] [gas1number] ... [2-5 gas types]\r
        if mix_name is None or len(mix_name) == 0 or len(mix_name) > 6:
            raise ValueError(f"Gas mixture name must be between 1 and 6 chars")
        if len(mixture) < 2 or len(mixture) > 5:
            raise ValueError(f"Gas mixture must have between 2 and 5 components")
        sum = GasMixEntry.sum_mixture_percentages(mixture)
        if sum != 100:
            raise ValueError(f"Gas mixture percentages to 2 decimal places must sum to 100, instead got {sum}")
        mixture_strings = " ".join(
            [f"{gas_entry.serialized_gas_percentage} {gas_entry.gas_number}" for gas_entry in mixture]
        )
        response = self._query_checked(f"{self.unit_id}gm {mix_name} {gas_id} {mixture_strings}")
        response_cols = response.split()
        mixture_identifier = GasTypeEntry(int(response_cols[1]), mix_name)
        return mixture_identifier

    ###Normal Operation
    def get_flow_data(self) -> FlowData:
        response = self._query_checked(self.unit_id)
        return self._parse_flowdata(response)

    def get_flow_sample_metadata(self, refresh=False) -> list[MeasurementHeaderEntry]:
        if self.measurement_headings is None or len(self.measurement_headings) == 0 or refresh:
            headings_list: list[MeasurementHeaderEntry] = []
            with self._visa.temporary_timeout(250):
                with self._visa.lock():
                    self._visa.write(f"{self.unit_id}??d*")
                    heading_col_widths = None
                    for i in range(50):
                        heading_line = None
                        try:
                            heading_line = self._visa.read()
                        except VisaIOError as e:  # we expect a timeout when we run out of items to read
                            if e.abbreviation == "VI_ERROR_TMO":
                                break
                            else:
                                raise
                        if heading_line:
                            if heading_col_widths is None:
                                heading_col_widths = MeasurementHeaderEntry.parse_column_spans(heading_line)
                            else:
                                try:
                                    measurement_header = MeasurementHeaderEntry.parse(heading_line, heading_col_widths)
                                    if measurement_header is not None:
                                        headings_list.append(measurement_header)
                                except Exception as e:  # less clear why this might happen, log
                                    logger.error(
                                        f"Exception {e} occurred while parsing gas type entries", exc_info=True
                                    )
            if headings_list is not None and len(headings_list) > 0:
                self.measurement_headings = headings_list

        headings_list = deepcopy(self.measurement_headings)
        return headings_list

    def set_setpoint(self, setpt: float) -> float:
        # Device echoes an updated data frame; consume it to keep the buffer clean.
        response = self._query_checked(f"{self.unit_id}s{setpt}")
        flowdata = self._parse_flowdata(response)
        return flowdata.setpoint

    def set_setpoint_int(self, setpt: float, full_scale_range: float, range_minimum: float) -> float:
        # Device echoes an updated data frame; consume it to keep the buffer clean.
        setpoint_integer = int(64000 * ((setpt / full_scale_range) - (range_minimum / full_scale_range)))
        response = self._query_checked(f"{self.unit_id}{setpoint_integer}")
        flowdata = self._parse_flowdata(response)
        return flowdata.setpoint

    ###Hold commands
    def hold_valve_at_position(self) -> FlowData:
        response = self._query_checked(f"{self.unit_id}hp")
        return self._parse_flowdata(response)

    def hold_valve_closed(self) -> FlowData:
        response = self._query_checked(f"{self.unit_id}hc")
        return self._parse_flowdata(response)

    def cancel_valve_hold(self) -> FlowData:
        response = self._query_checked(f"{self.unit_id}c")
        return self._parse_flowdata(response)

    def _parse_flowdata(self, response: str) -> FlowData:
        # order: Unit[0], Abs press[1], flow temp[2], volu flow[3], mass flow[4], mass flow setpt[5], gas[6], status[7+]
        # area for improvement: use get_flow_sample_metadata to dynamically determine order
        fields = response.split()
        if len(fields) < 7:
            raise RuntimeError(f"AlicatMC: short response from {self.unit_id!r}: {response!r}")
        if fields[0].upper() != self.unit_id.upper():
            raise RuntimeError(f"AlicatMC: response ID {fields[0]!r} does not match device ID {self.unit_id!r}")
        return FlowData(
            pressure=float(fields[1]),
            temperature=float(fields[2]),
            vol_flow=float(fields[3]),
            mass_flow=float(fields[4]),
            setpoint=float(fields[5]),
            gas=fields[6],
            status_flags=set(fields[7:]),
        )
