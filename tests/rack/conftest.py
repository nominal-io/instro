"""Fixtures shared by the NYC test rack hardware suites; see rack_support.py for the wiring."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest
from discovery import CATEGORIES, discover_rack, model_of, pinned_resource, to_json
from rack_support import CAPTURE_ROOT, Rack, arm_protection, logger

from instro.dmm import InstroDMM
from instro.eload import InstroELoad
from instro.eload.types import LoadMode
from instro.lib import Instrument
from instro.lib.discover import DiscoveredInstrument, DiscoveryReport
from instro.lib.publishers import FilePublisher, SharedPublisher
from instro.psu import InstroPSU


@pytest.fixture(scope="session")
def run_dir() -> Path:
    """Before each session, define a run directory for the session's captures and discovery report."""
    run_id = os.environ.get("RACK_RUN_ID") or datetime.now().strftime("%Y%m%d-%H%M%S")
    path = CAPTURE_ROOT / run_id
    path.mkdir(parents=True, exist_ok=True)
    logger.info("Run directory: %s", path)
    return path


@pytest.fixture(scope="session")
def discovery(run_dir: Path) -> DiscoveryReport:
    """Before each session, discover the instruments in the rack and write a report to the run directory."""
    logger.info("=== Instrument discovery ===")
    report = discover_rack()
    (run_dir / "discovery.json").write_text(to_json(report))
    return report


@pytest.fixture(scope="session")
def instruments(discovery: DiscoveryReport) -> dict[str, DiscoveredInstrument]:
    """Before each session, select the instruments the rack uses.

    The first discovered instrument per category or the one pinned by RACK_<CATEGORY>_RESOURCE.
    """
    chosen: dict[str, DiscoveredInstrument] = {}
    for category in CATEGORIES:
        candidates = discovery.by_category(category)
        if pin := pinned_resource(category):
            candidates = [i for i in candidates if i.resource == pin]
        if not candidates:
            continue
        if len(candidates) > 1:
            logger.warning(
                "  %d %ss found, using %s; pin one with RACK_%s_RESOURCE",
                len(candidates),
                category,
                candidates[0].resource,
                category.upper(),
            )
        chosen[category] = candidates[0]
        logger.info(
            "  rack %-5s -> %s via %s (%s)",
            category,
            model_of(chosen[category]),
            chosen[category].driver_name,
            chosen[category].resource,
        )
    missing = [c for c in CATEGORIES if c not in chosen]
    if missing:
        pytest.fail(f"no supported {', '.join(missing)} discovered; see discovery.json in the run directory")
    return chosen


@pytest.fixture(scope="module")
def rack(instruments: dict[str, DiscoveredInstrument], run_dir: Path, request: pytest.FixtureRequest) -> Iterator[Rack]:
    """For each module, open the instruments and put them in a safe state.

    At the end of the module, return them to a safe state and close them.
    """
    suite = request.module.__name__.rsplit(".", 1)[-1].removeprefix("test_nyc_rack_")
    capture = FilePublisher(directory=run_dir, format="jsonl", custom_file_name=suite)
    shared = SharedPublisher(capture)
    run_id = run_dir.name
    psu_info, dmm_info, eload_info = instruments["psu"], instruments["dmm"], instruments["eload"]
    assert psu_info.num_channels is not None

    logger.info("=== Constructing instro instruments for %s ===", suite)
    psu = InstroPSU(
        name="psu",
        driver=psu_info.make_driver(),
        num_channels=psu_info.num_channels,
        publishers=[shared.clone()],
        rack="nyc",
        run_id=run_id,
        suite=suite,
    )
    dmm = InstroDMM(
        name="dmm",
        driver=dmm_info.make_driver(),
        publishers=[shared.clone()],
        rack="nyc",
        run_id=run_id,
        suite=suite,
    )
    eload = InstroELoad(
        name="eload",
        driver=eload_info.make_driver(),
        publishers=[shared.clone()],
        rack="nyc",
        run_id=run_id,
        suite=suite,
    )
    rack = Rack(psu=psu, dmm=dmm, eload=eload, capture_path=capture.file_path, found=instruments)

    opened: list[Instrument] = []
    try:
        for instrument, info in zip(rack.instruments, (psu_info, dmm_info, eload_info)):
            logger.info("Opening %s: %s via %s on %s", instrument.name, model_of(info), info.driver_name, info.resource)
            instrument.open()
            opened.append(instrument)
        rack.safe_state()
        arm_protection(psu, rack.psu_channels)
        eload.set_mode(LoadMode.CC)
        eload.set_level(0.0)
        logger.info("Rack ready; publishing to %s", rack.capture_path)
        yield rack
    finally:
        logger.info("=== Rack teardown (%s) ===", suite)
        rack.safe_state()
        for instrument in reversed(opened):
            try:
                instrument.close()
            except Exception:
                logger.exception("failed to close %s", instrument.name)
        shared.close()
        logger.info("Capture written to %s", rack.capture_path)


@pytest.fixture(autouse=True)
def _per_check(request: pytest.FixtureRequest) -> Iterator[None]:
    """Tag published data with the check name and return the rack to a safe state after each check."""
    logger.info("=== %s ===", request.node.name)
    if "rack" not in request.fixturenames:
        yield
        return
    rack: Rack = request.getfixturevalue("rack")
    for instrument in rack.instruments:
        instrument.default_tags["check"] = request.node.name
    try:
        yield
    finally:
        rack.safe_state()
        for instrument in rack.instruments:
            instrument.default_tags.pop("check", None)
