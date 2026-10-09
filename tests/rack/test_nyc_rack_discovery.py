"""NYC test rack: instro's discovery layers and the ``instro discover`` CLI against real instruments.

Exercises each public layer of ``instro.lib.discover`` (enumerate, identify, match, providers,
orchestrate) and the CLI on top of it. Expectations come from what the session's discovery found, so
the checks hold for whichever supported instruments are on the rack. Nothing here opens the rack
fixture or energizes anything: every probe is OS port enumeration or an ``*IDN?`` query.

Run:
    uv run python tests/rack/test_nyc_rack_discovery.py
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest
from discovery import CATEGORIES
from rack_support import RACK_DIR, logger, run_pytest

from instro.lib.discover import (
    DiscoveredInstrument,
    DiscoveryEvent,
    DiscoveryReport,
    IdentifyError,
    discover,
    enumerate_candidates,
    identify,
    list_serial_ports,
    match_idn,
    providers_for,
    scan_visa_resources,
)
from instro.lib.registry import driver_registry
from instro.lib.transports import SerialConfig
from instro.lib.transports.visa import VisaDriver

pytestmark = pytest.mark.hardware

# Fails to open immediately (no such USB device), unlike a LAN address that waits out a connect timeout.
UNREACHABLE_RESOURCE = "USB0::0x0000::0x0000::NO_SUCH_INSTRUMENT::INSTR"
WRONG_BAUD = 19200
# A probe at the wrong baud leaves garbage in the B&K 8500B's input buffer, so it misses the next
# correctly-framed *IDN? and answers the one after; give it a few tries to come back.
RECOVERY_ATTEMPTS = 3


def _serial(instruments: dict[str, DiscoveredInstrument]) -> DiscoveredInstrument:
    found = next((i for i in instruments.values() if i.serial_config is not None), None)
    if found is None:
        pytest.skip("no rack instrument is on a serial port")
    return found


def _restore_serial(serial: DiscoveredInstrument) -> None:
    """Bring the instrument back after a wrong-baud probe: wait for it to answer, then clear its error queue.

    The garbage it received is queued as invalid-command errors that outlive the session, and drivers
    that check ``SYST:ERR?`` on open (BK85XXB does) would trip over them. ``*CLS`` is the IEEE 488.2
    common command that clears the queue.
    """
    for attempt in range(1, RECOVERY_ATTEMPTS + 1):
        try:
            identify(serial.resource, serial_config=serial.serial_config, timeout=1)
        except Exception as exc:
            logger.info("  recovering %s: attempt %d failed (%s)", serial.resource, attempt, type(exc).__name__)
            continue
        logger.info("  %s answering again after %d attempt(s)", serial.resource, attempt)
        break
    else:
        raise AssertionError(f"{serial.resource} did not answer at its settings after {RECOVERY_ATTEMPTS} attempts")
    visa = VisaDriver(serial.visa_config())
    visa.open()
    try:
        visa.write("*CLS")
    finally:
        visa.close()


def test_list_serial_ports_sees_the_serial_instrument(instruments: dict[str, DiscoveredInstrument]) -> None:
    """The OS lists the serial instrument's port, maps it to its VISA resource, and flags its adapter as USB."""
    serial = _serial(instruments)
    ports = list_serial_ports()
    for p in ports:
        logger.info("  %-6s %-14s usb=%-5s %s", p.device, p.resource, p.is_usb, p.description)
    port = next((p for p in ports if p.resource == serial.resource), None)
    assert port is not None, f"{serial.resource} not among the OS serial ports"
    assert port.is_usb, f"{port.device} should be a USB adapter (discovery only probes those by default)"


def test_enumerate_candidates_merges_sources(instruments: dict[str, DiscoveredInstrument]) -> None:
    """VISA resources, OS serial ports and extra resources merge into one de-duplicated list; nothing is sent."""
    psu = instruments["psu"].resource
    candidates = enumerate_candidates(extra_resources=[UNREACHABLE_RESOURCE, psu])
    for c in candidates:
        logger.info("  %-6s %-50s port=%s", c.source, c.resource, c.port.device if c.port else "-")
    resources = [c.resource for c in candidates]
    assert len(resources) == len(set(resources)), f"duplicate candidates: {resources}"
    assert {i.resource for i in instruments.values()} <= set(resources), "every rack instrument is a candidate"
    by_resource = {c.resource: c for c in candidates}
    assert by_resource[UNREACHABLE_RESOURCE].source == "extra"
    assert by_resource[psu].source == "visa", "an extra resource VISA already lists keeps its VISA source"


def test_identify_each_rack_instrument(instruments: dict[str, DiscoveredInstrument]) -> None:
    """identify() returns each instrument's reply; serial ports also return the settings that answered."""
    for category in CATEGORIES:
        found = instruments[category]
        identity = identify(found.resource)
        logger.info("  %-5s %-45s %s  serial=%s", category, found.resource, identity.idn, identity.serial_config)
        assert identity.idn == found.idn
        match = match_idn(identity.idn)
        assert match is not None and (match.category, match.driver_name) == (category, found.driver_name)
        if found.serial_config is None:
            assert identity.serial_config is None
        else:
            assert identity.serial_config == SerialConfig(), "probed once, at the SerialConfig defaults"


def test_identify_serial_at_wrong_baud_fails(instruments: dict[str, DiscoveredInstrument]) -> None:
    """A serial instrument probed at the wrong baud doesn't produce an identity."""
    serial = _serial(instruments)
    try:
        with pytest.raises(Exception) as exc:
            identify(serial.resource, serial_config=SerialConfig(baud_rate=WRONG_BAUD), timeout=1)
    finally:
        _restore_serial(serial)
    logger.info("  %s @ %d baud -> %s: %s", serial.resource, WRONG_BAUD, type(exc.value).__name__, exc.value)
    assert isinstance(exc.value, IdentifyError) or "TMO" in str(exc.value), "expected garbage or a timeout"


def test_discover_finds_the_rack(discovery: DiscoveryReport) -> None:
    """The default discover() (VISA plus USB serial) finds every rack category, serial settings included."""
    for category in CATEGORIES:
        assert discovery.by_category(category), f"no {category} discovered"
    serial_records = [i for i in discovery.instruments if i.serial_config is not None]
    for record in serial_records:
        assert record.serial_config is not None
        logger.info("  serial: %s %s at %s", record.resource, record.driver_name, record.serial_config)
        assert record.config_block()["visa"]["serial_config"]["baud_rate"] == record.serial_config.baud_rate
    non_usb = [p for p in discovery.serial_ports if not p.is_usb]
    skipped = {s.resource: s.reason for s in discovery.skipped}
    for port in non_usb:
        assert "not a USB serial port" in skipped.get(port.resource, ""), f"{port.device} should be skipped"


def test_serial_probe_policy_none(instruments: dict[str, DiscoveredInstrument]) -> None:
    """probe_serial='none' skips every serial port, so the serial instrument isn't found."""
    serial = _serial(instruments)
    report = discover(probe_serial="none")
    assert serial.resource not in {i.resource for i in report.instruments}
    assert {s.resource: s.reason for s in report.skipped}.get(serial.resource) == "serial probing disabled"
    for category, found in instruments.items():
        if found.serial_config is None:
            assert any(i.resource == found.resource for i in report.instruments), f"{category} should still be found"


def test_exclude_by_resource_and_by_device(instruments: dict[str, DiscoveredInstrument]) -> None:
    serial = _serial(instruments)
    device = next(p.device for p in list_serial_ports() if p.resource == serial.resource)
    for excluded in (serial.resource, device.lower()):
        report = discover(exclude=[excluded])
        logger.info("  exclude=%r -> skipped %s", excluded, [(s.resource, s.reason) for s in report.skipped])
        assert serial.resource not in {i.resource for i in report.instruments}
        assert {s.resource: s.reason for s in report.skipped}.get(serial.resource) == "excluded"


def test_wrong_serial_config_reports_a_manual_settings_hint(instruments: dict[str, DiscoveredInstrument]) -> None:
    """A serial instrument that doesn't answer at the probed settings is an error that says how to fix it."""
    serial = _serial(instruments)
    try:
        report = discover(serial_config=SerialConfig(baud_rate=WRONG_BAUD), timeout=1)
    finally:
        _restore_serial(serial)
    errors = {e.resource: e for e in report.errors}
    assert serial.resource in errors, f"{serial.resource} should be an error at {WRONG_BAUD} baud"
    logger.info("  %s -> %s", serial.resource, errors[serial.resource].hint)
    assert f"{WRONG_BAUD} baud" in (errors[serial.resource].hint or "")
    assert "serial_config manually" in (errors[serial.resource].hint or "")


def test_extra_resource_that_cannot_be_reached_is_an_error() -> None:
    report = discover(extra_resources=[UNREACHABLE_RESOURCE], probe_serial="none")
    errors = {e.resource: e.message for e in report.errors}
    logger.info("  %s -> %s", UNREACHABLE_RESOURCE, errors.get(UNREACHABLE_RESOURCE))
    assert UNREACHABLE_RESOURCE in errors


def test_events_announce_each_probe_before_its_outcome(discovery: DiscoveryReport) -> None:
    events: list[DiscoveryEvent] = []
    report = discover(on_event=events.append)
    for found in report.instruments:
        statuses = [e.status for e in events if e.resource == found.resource]
        logger.info("  %-45s %s", found.resource, statuses)
        assert statuses == ["probing", "found"], f"{found.resource}: {statuses}"
    assert {i.resource for i in report.instruments} == {i.resource for i in discovery.instruments}, "repeatable"


def test_scan_visa_resources_wrapper(discovery: DiscoveryReport) -> None:
    """The pre-provider API still works: VISA only, serial ports skipped."""
    report = scan_visa_resources()
    assert not any(i.resource.upper().startswith("ASRL") for i in report.instruments)
    usb = {i.resource for i in discovery.instruments if i.serial_config is None}
    assert {i.resource for i in report.instruments} == usb


def test_sources_and_providers() -> None:
    assert [p.name for p in providers_for(["visa", "visa"])] == ["visa"], "duplicates collapse"
    assert [p.name for p in providers_for(["all"])], "'all' expands to every registered source"
    with pytest.raises(ValueError, match="unknown discovery source"):
        providers_for(["no-such-source"])
    report = discover(sources=["all"], probe_serial="none")
    assert report.instruments, "sources=['all'] still discovers the VISA instruments"


def test_daq_registry_resolves_scpi_daq_identity() -> None:
    """#622 registers DAQ drivers; the SCPI one is matchable by *IDN? (no DAQ on this rack, so identity only)."""
    match = match_idn("Keysight Technologies,34980A,MY12345678,2.43-2.43-0.00-0.00")
    assert match is not None and (match.category, match.driver_name) == ("daq", "Keysight34980A")
    assert {"Keysight34980A", "NIDAQDriver", "LabJackTSeriesDriver", "MCCDriver"} <= set(driver_registry("daq"))


def test_cli_discover_lists_the_rack(instruments: dict[str, DiscoveredInstrument]) -> None:
    """`instro discover`, run as a real process, prints every rack instrument in its RECOGNIZED table."""
    env = {**os.environ, "COLUMNS": "240", "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run(
        [sys.executable, "-m", "instro.cli.main", "discover"],
        cwd=RACK_DIR.parent.parent,
        env=env,
        capture_output=True,
        encoding="utf-8",
        timeout=120,
    )
    logger.info("instro discover output:\n%s", result.stdout)
    assert result.returncode == 0, result.stderr
    assert "RECOGNIZED DEVICES" in result.stdout
    for category, found in instruments.items():
        assert found.driver_name in result.stdout, f"{category} {found.driver_name} missing from CLI output"
    if any(i.serial_config is not None for i in instruments.values()):
        assert "9600 baud, 8N1" in result.stdout, "serial instruments show the settings they answered at"


if __name__ == "__main__":
    sys.exit(run_pytest(__file__))
