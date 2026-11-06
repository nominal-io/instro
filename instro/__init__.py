"""Control test-and-measurement instruments from Python, with one API per instrument category.

Build an instrument from a category class and a driver, then read and command it::

    from instro import InstroPSU
    from instro.drivers import VirtualPSU

    with InstroPSU(name="myPSU", driver=VirtualPSU(num_channels=1), num_channels=1) as psu:
        psu.apply(current_limit=1.0, voltage=5.0, enable=True, channel=1)
        print(psu.get_voltage(channel=1).latest)

Swap ``VirtualPSU`` for a real driver (``BK9115``, ``RigolDP800``, ...) and the rest stays the same.

Where to import from:

``instro``
    Instrument classes (``InstroPSU``, ``InstroDMM``, ``InstroDAQ``, ...), their types and enums
    (``MeasurementFunction``, ``LoadMode``, ...), and the ``*DriverBase`` classes for writing a driver.
``instro.drivers``
    Every driver, including the ones in vendor packages (``NIDAQDriver``, ``Aardvark``, ...).
``instro.lib``
    Shared tools: publishers that send data to Nominal or files (``NominalCorePublisher``,
    ``FilePublisher``), transports and their settings (``VisaConfig``), ``Measurement``/``Command``, and
    exceptions.

Instrument categories, each also importable on its own (``from instro.psu import InstroPSU``):

``instro.psu``
    Power supplies (``InstroPSU``).
``instro.dmm``
    Digital multimeters (``InstroDMM``).
``instro.eload``
    Electronic loads (``InstroELoad``).
``instro.scope``
    Oscilloscopes (``InstroScope``).
``instro.awg``
    Arbitrary waveform generators (``InstroAWG``).
``instro.daq``
    Data acquisition (``InstroDAQ``); ``instro.daq.scaling`` converts raw readings to engineering units.
``instro.i2c``
    I2C buses (``I2CInterface``).
``instro.modbus``
    Modbus devices (``ModbusDevice``).

Optional packages add ``instro.contrib`` (community drivers, ``pip install 'instro[contrib]'``),
``instro.ethernetip`` (EtherNet/IP devices), and ``instro.unstable`` (in-development categories).
"""

# The TYPE_CHECKING block below uses grouped ``X as X`` re-exports (explicit for mypy --strict users);
# isort would split each into its own statement, so import sorting is off for this file.
# ruff: noqa: I001

from __future__ import annotations

import importlib
from pkgutil import extend_path
from typing import TYPE_CHECKING, Any

# instro is shared with workspace packages (instro-contrib, instro-unstable, instro-ethernetip, ...) that
# ship their own instro.* subpackages; extend_path keeps those importable now that instro is a regular package.
__path__ = extend_path(__path__, __name__)

if TYPE_CHECKING:
    from instro.awg import (
        AmplitudeMeasurementUnit as AmplitudeMeasurementUnit,
        Arbitrary as Arbitrary,
        AWGConfig as AWGConfig,
        AWGDriverBase as AWGDriverBase,
        BurstTriggerSource as BurstTriggerSource,
        BurstType as BurstType,
        convert_amplitude as convert_amplitude,
        GatePolarity as GatePolarity,
        InstroAWG as InstroAWG,
        ModulationType as ModulationType,
        Pulse as Pulse,
        Sawtooth as Sawtooth,
        Sine as Sine,
        Square as Square,
        StaticValue as StaticValue,
        SweepTriggerSource as SweepTriggerSource,
        SweepType as SweepType,
        Triangle as Triangle,
        Waveform as Waveform,
    )
    from instro.daq import (
        AnalogChannel as AnalogChannel,
        AnalogCurrentChannel as AnalogCurrentChannel,
        AnalogThermocoupleChannel as AnalogThermocoupleChannel,
        AnalogVoltageChannel as AnalogVoltageChannel,
        ChannelType as ChannelType,
        CJCSource as CJCSource,
        DAQChannel as DAQChannel,
        DAQDriverBase as DAQDriverBase,
        DAQVendor as DAQVendor,
        DigitalChannel as DigitalChannel,
        DigitalLineChannel as DigitalLineChannel,
        DigitalPortChannel as DigitalPortChannel,
        DigitalPortWidth as DigitalPortWidth,
        Direction as Direction,
        HWTimestamper as HWTimestamper,
        HWTimingConfig as HWTimingConfig,
        HWTimingException as HWTimingException,
        InstroDAQ as InstroDAQ,
        Logic as Logic,
        RelayChannel as RelayChannel,
        TerminalConfig as TerminalConfig,
        TimingConfigException as TimingConfigException,
    )
    from instro.daq.scaling import (
        DevicePassthrough as DevicePassthrough,
        InverseThermocoupleSensor as InverseThermocoupleSensor,
        kelvin_to_unit as kelvin_to_unit,
        LinearScaler as LinearScaler,
        ReverseLinearScaler as ReverseLinearScaler,
        Scaler as Scaler,
        ScalerPipeline as ScalerPipeline,
        TC_TYPE as TC_TYPE,
        TC_UNIT as TC_UNIT,
        ThermocoupleSensor as ThermocoupleSensor,
        unit_to_kelvin as unit_to_kelvin,
    )
    from instro.dmm import (
        DMMConfig as DMMConfig,
        DMMDriverBase as DMMDriverBase,
        DMMMeasurementConfig as DMMMeasurementConfig,
        InstroDMM as InstroDMM,
        MeasurementFunction as MeasurementFunction,
    )
    from instro.eload import (
        ELoadConfig as ELoadConfig,
        ELoadDriverBase as ELoadDriverBase,
        InstroELoad as InstroELoad,
        LoadMode as LoadMode,
        SlewRateDirection as SlewRateDirection,
    )
    from instro.i2c import (
        CommandDef as CommandDef,
        CommandDevice as CommandDevice,
        CustomScaling as CustomScaling,
        DataFormat as DataFormat,
        FieldDef as FieldDef,
        I2CDriverBase as I2CDriverBase,
        I2CInterface as I2CInterface,
        LinearScaling as LinearScaling,
        RegisterDevice as RegisterDevice,
        ScalingFunction as ScalingFunction,
        SystemDefinition as SystemDefinition,
    )
    from instro.modbus import (
        BitDef as BitDef,
        ModbusConfig as ModbusConfig,
        ModbusDevice as ModbusDevice,
        RegisterDef as RegisterDef,
    )
    from instro.psu import (
        InstroPSU as InstroPSU,
        OperatingMode as OperatingMode,
        PSUConfig as PSUConfig,
        PSUDriverBase as PSUDriverBase,
    )
    from instro.scope import (
        AcquisitionConfig as AcquisitionConfig,
        AcquisitionMode as AcquisitionMode,
        AcquisitionState as AcquisitionState,
        ChannelConfig as ChannelConfig,
        ChannelState as ChannelState,
        Coupling as Coupling,
        InstroScope as InstroScope,
        ScopeConfig as ScopeConfig,
        ScopeDriverBase as ScopeDriverBase,
        ScopeMeasurementType as ScopeMeasurementType,
        ScopeState as ScopeState,
        TriggerConfig as TriggerConfig,
        TriggerMode as TriggerMode,
        TriggerSlope as TriggerSlope,
        TriggerState as TriggerState,
        TriggerStatus as TriggerStatus,
        TriggerType as TriggerType,
        VisaDriverConfig as VisaDriverConfig,
        WaveformData as WaveformData,
    )

# Public name -> module that defines it. Names that would be ambiguous at the top level keep their category path:
# each protocol has its own ``TimingConfig`` (``instro.modbus``), and ``RegisterDef`` exists for both Modbus
# (exported here) and I2C (``instro.i2c.RegisterDef``).
_EXPORTS: dict[str, str] = {
    # instro.awg
    "AmplitudeMeasurementUnit": "instro.awg",
    "Arbitrary": "instro.awg",
    "AWGConfig": "instro.awg",
    "AWGDriverBase": "instro.awg",
    "BurstTriggerSource": "instro.awg",
    "BurstType": "instro.awg",
    "convert_amplitude": "instro.awg",
    "GatePolarity": "instro.awg",
    "InstroAWG": "instro.awg",
    "ModulationType": "instro.awg",
    "Pulse": "instro.awg",
    "Sawtooth": "instro.awg",
    "Sine": "instro.awg",
    "Square": "instro.awg",
    "StaticValue": "instro.awg",
    "SweepTriggerSource": "instro.awg",
    "SweepType": "instro.awg",
    "Triangle": "instro.awg",
    "Waveform": "instro.awg",
    # instro.daq
    "AnalogChannel": "instro.daq",
    "AnalogCurrentChannel": "instro.daq",
    "AnalogThermocoupleChannel": "instro.daq",
    "AnalogVoltageChannel": "instro.daq",
    "ChannelType": "instro.daq",
    "CJCSource": "instro.daq",
    "DAQChannel": "instro.daq",
    "DAQDriverBase": "instro.daq",
    "DAQVendor": "instro.daq",
    "DigitalChannel": "instro.daq",
    "DigitalLineChannel": "instro.daq",
    "DigitalPortChannel": "instro.daq",
    "DigitalPortWidth": "instro.daq",
    "Direction": "instro.daq",
    "HWTimestamper": "instro.daq",
    "HWTimingConfig": "instro.daq",
    "HWTimingException": "instro.daq",
    "InstroDAQ": "instro.daq",
    "Logic": "instro.daq",
    "RelayChannel": "instro.daq",
    "TerminalConfig": "instro.daq",
    "TimingConfigException": "instro.daq",
    # instro.daq.scaling
    "DevicePassthrough": "instro.daq.scaling",
    "InverseThermocoupleSensor": "instro.daq.scaling",
    "kelvin_to_unit": "instro.daq.scaling",
    "LinearScaler": "instro.daq.scaling",
    "ReverseLinearScaler": "instro.daq.scaling",
    "Scaler": "instro.daq.scaling",
    "ScalerPipeline": "instro.daq.scaling",
    "TC_TYPE": "instro.daq.scaling",
    "TC_UNIT": "instro.daq.scaling",
    "ThermocoupleSensor": "instro.daq.scaling",
    "unit_to_kelvin": "instro.daq.scaling",
    # instro.dmm
    "DMMConfig": "instro.dmm",
    "DMMDriverBase": "instro.dmm",
    "DMMMeasurementConfig": "instro.dmm",
    "InstroDMM": "instro.dmm",
    "MeasurementFunction": "instro.dmm",
    # instro.eload
    "ELoadConfig": "instro.eload",
    "ELoadDriverBase": "instro.eload",
    "InstroELoad": "instro.eload",
    "LoadMode": "instro.eload",
    "SlewRateDirection": "instro.eload",
    # instro.i2c
    "CommandDef": "instro.i2c",
    "CommandDevice": "instro.i2c",
    "CustomScaling": "instro.i2c",
    "DataFormat": "instro.i2c",
    "FieldDef": "instro.i2c",
    "I2CDriverBase": "instro.i2c",
    "I2CInterface": "instro.i2c",
    "LinearScaling": "instro.i2c",
    "RegisterDevice": "instro.i2c",
    "ScalingFunction": "instro.i2c",
    "SystemDefinition": "instro.i2c",
    # instro.modbus
    "BitDef": "instro.modbus",
    "ModbusConfig": "instro.modbus",
    "ModbusDevice": "instro.modbus",
    "RegisterDef": "instro.modbus",
    # instro.psu
    "InstroPSU": "instro.psu",
    "OperatingMode": "instro.psu",
    "PSUConfig": "instro.psu",
    "PSUDriverBase": "instro.psu",
    # instro.scope
    "AcquisitionConfig": "instro.scope",
    "AcquisitionMode": "instro.scope",
    "AcquisitionState": "instro.scope",
    "ChannelConfig": "instro.scope",
    "ChannelState": "instro.scope",
    "Coupling": "instro.scope",
    "InstroScope": "instro.scope",
    "ScopeConfig": "instro.scope",
    "ScopeDriverBase": "instro.scope",
    "ScopeMeasurementType": "instro.scope",
    "ScopeState": "instro.scope",
    "TriggerConfig": "instro.scope",
    "TriggerMode": "instro.scope",
    "TriggerSlope": "instro.scope",
    "TriggerState": "instro.scope",
    "TriggerStatus": "instro.scope",
    "TriggerType": "instro.scope",
    "VisaDriverConfig": "instro.scope",
    "WaveformData": "instro.scope",
}

# Subpackages reachable as attributes (``instro.psu``) without an explicit ``import instro.psu``.
_SUBMODULES: frozenset[str] = frozenset(
    {"awg", "cli", "daq", "dmm", "drivers", "eload", "i2c", "lib", "modbus", "psu", "scope"}
)

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is not None:
        value = getattr(importlib.import_module(module), name)
    elif name in _SUBMODULES:
        value = importlib.import_module(f"{__name__}.{name}")
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    # Only the public API: helper imports (importlib, Any, ...) would clutter tab completion.
    return sorted({*_EXPORTS, *_SUBMODULES, *(n for n in globals() if n.startswith("__"))})
