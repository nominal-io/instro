"""VISA instrument discovery: scan resources, query identity, match to known drivers."""

# the difficult thing is that this only works reliably for VISA instruments atm (sometimes)
# we will need to eventually expand this to cover non-visa instruments!
from __future__ import annotations

import dataclasses
import warnings
import pyvisa
from instro.lib.transports.visa import TimeoutConfig, VisaConfig, VisaDriver


@dataclasses.dataclass
class VisaInstrumentInfo:
    resource: str
    idn: str
    category: str
    driver_class_name: str
    vendor_key: str | None
    num_channels: int | None


@dataclasses.dataclass
class VisaScanError:
    resource: str
    message: str


@dataclasses.dataclass
class VisaUnrecognizedInstrument:
    resource: str
    idn: str


@dataclasses.dataclass
class VisaScanResult:
    instruments: list[VisaInstrumentInfo]
    unrecognized: list[VisaUnrecognizedInstrument]
    errors: list[VisaScanError]


_IDN_MAP: dict[tuple[str, str], tuple[str, str, str | None, int | None]] = {
    ("AGILENT TECHNOLOGIES", "34401A"): ("dmm", "Agilent34401A", None, None),
    ("HEWLETT-PACKARD", "34401A"): ("dmm", "Agilent34401A", None, None),
    ("KEITHLEY INSTRUMENTS", "2400"): ("dmm", "Keithley2400", None, None),
    ("B&K PRECISION", "9115"): ("psu", "BK9115", "bk_9115", 1),
    ("B&K PRECISION", "9140"): ("psu", "BK914X", "bk_914x", 3),
    ("RIGOL TECHNOLOGIES", "DP811"): ("psu", "RigolDP800", "rigol_dp800", 1),
    ("RIGOL TECHNOLOGIES", "DP821"): ("psu", "RigolDP800", "rigol_dp800", 2),
    ("RIGOL TECHNOLOGIES", "DP831"): ("psu", "RigolDP800", "rigol_dp800", 3),
    ("RIGOL TECHNOLOGIES", "DP832"): ("psu", "RigolDP800", "rigol_dp800", 3),
    ("SIGLENT TECHNOLOGIES", "SPD3303"): ("psu", "SiglentSPD3303", "siglent_spd3303", 3),
    ("GENESYS", "GEN"): ("psu", "TDKLambdaGenesys", "tdk_lambda_genesys", 1),
    ("B&K PRECISION", "BK85"): ("eload", "BK85XXB", None, None),
}


def scan_visa_resources(
    backend: str | None = None,
    timeout: int = 2,
) -> VisaScanResult:
    """Scan VISA resources, query each for identity, and return matched instruments."""
    if backend is not None:
        rm = pyvisa.ResourceManager(backend)
        active_backend = backend
    else:
        try:
            rm = pyvisa.ResourceManager("@ivi")
            active_backend = "@ivi"
        except Exception:
            rm = pyvisa.ResourceManager("@py")
            active_backend = "@py"

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        resources = rm.list_resources()

    instruments: list[VisaInstrumentInfo] = []
    unrecognized: list[VisaUnrecognizedInstrument] = []
    errors: list[VisaScanError] = []

    for resource in resources:
        if resource.startswith("ASRL"):
            continue

        driver = VisaDriver(
            VisaConfig(visa_resource=resource, timeout=TimeoutConfig(recv=timeout), visa_backend=active_backend),
        )
        try:
            driver.open()
            idn = driver.query("*IDN?").strip()
            parts = [p.strip().lower() for p in idn.split(",")]
            vendor = parts[0] if len(parts) > 0 else ""
            model = parts[1] if len(parts) > 1 else ""

            match = next(
                (
                    v
                    for (k_vendor, k_model), v in _IDN_MAP.items()
                    if k_vendor.lower() in vendor and k_model.lower() in model
                ),
                None,
            )
            if match is None:
                unrecognized.append(VisaUnrecognizedInstrument(resource=resource, idn=idn))
            else:
                category, driver_class_name, vendor_key, num_channels = match
                instruments.append(
                    VisaInstrumentInfo(
                        resource=resource,
                        idn=idn,
                        category=category,
                        driver_class_name=driver_class_name,
                        vendor_key=vendor_key,
                        num_channels=num_channels,
                    )
                )
        except Exception as e:
            errors.append(VisaScanError(resource=resource, message=str(e)))
        finally:
            driver.close()

        # return a nice lil class that has instro, unrec, and errors, the PSU will only parse instro for now
    return VisaScanResult(instruments=instruments, unrecognized=unrecognized, errors=errors)
