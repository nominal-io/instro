"""Backend diagnostics: which VISA backend is active and which pyvisa-py interfaces it cannot serve."""

from unittest.mock import MagicMock, patch

from instro.lib.transports.visa import (
    BackendDiagnostics,
    DegradedInterface,
    _open_resource_manager,
    backend_diagnostics,
    degraded_interfaces,
    open_resource_manager,
)

_DEBUG_INFO = {
    "Version": "0.8.1",
    "ASRL INSTR": "Available via PySerial (3.5)",
    "USB INSTR": "Please install PyUSB to use this resource type.\nNo module named 'usb'",
    "USB RAW": "Please install PyUSB to use this resource type.",
    "GPIB INSTR": "Available ",
    "GPIB INTFC": [
        "gpib_ctypes is installed but could not locate the gpib library.",
        "Please manually load it using:",
        "  gpib_ctypes.gpib.gpib._load_lib(filename)",
    ],
}


def test_degraded_interfaces_groups_by_family_and_attaches_hints() -> None:
    rm = MagicMock()
    rm.visalib.get_debug_info.return_value = _DEBUG_INFO
    assert degraded_interfaces(rm) == (
        DegradedInterface(
            "GPIB",
            "gpib_ctypes is installed but could not locate the gpib library",
            "install NI-488.2 or linux-gpib",
        ),
        DegradedInterface("USB", "Please install PyUSB to use this resource type", "install libusb"),
    )
    assert degraded_interfaces(rm)[1].describe() == (
        "USB: unavailable — Please install PyUSB to use this resource type (install libusb)"
    )


def test_degraded_interfaces_empty_without_pyvisa_py_debug_info() -> None:
    rm = MagicMock(spec=["visalib"])
    rm.visalib = MagicMock(spec=[])  # an IVI visalib has no get_debug_info
    assert degraded_interfaces(rm) == ()


def test_backend_diagnostics_reports_py_fallback_and_skips_ivi_inspection() -> None:
    rm = MagicMock()
    rm.visalib.get_debug_info.return_value = _DEBUG_INFO
    with patch("instro.lib.transports.visa.pyvisa.ResourceManager", side_effect=[OSError("no IVI"), rm]):
        fallback = backend_diagnostics()
    assert fallback == BackendDiagnostics("@py", True, degraded_interfaces(rm))
    assert fallback.label == "@py (pyvisa-py — no IVI VISA found)"

    with patch("instro.lib.transports.visa.pyvisa.ResourceManager", return_value=rm):
        ivi = backend_diagnostics()
        explicit_py = backend_diagnostics("@py")
    assert ivi == BackendDiagnostics("@ivi", False, ())
    assert ivi.label == "@ivi (system IVI VISA)"
    assert explicit_py.label == "@py (pyvisa-py)"
    assert explicit_py.degraded


def test_open_resource_manager_keeps_private_alias() -> None:
    assert _open_resource_manager is open_resource_manager
