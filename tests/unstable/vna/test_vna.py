"""Higher-level tests for the VNA wrapper using a simple in-memory driver."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from instro.lib import InstroError
from instro.unstable.vna.drivers.nanovna_v2clone import NanoVNAv2Clone
from instro.unstable.vna.storage import DiskStorage
from instro.unstable.vna.types import NetworkFileFormat
from instro.unstable.vna.vna import InstroVNA
from tests.unstable.vna.simulated import DEFAULT_NPOINTS, DEFAULT_NPORTS, SimulatedVNA


@pytest.fixture
def vna() -> InstroVNA:
    driver = SimulatedVNA(npoints=DEFAULT_NPOINTS, nports=DEFAULT_NPORTS)
    return InstroVNA(name="ut", driver=driver, storage=DiskStorage())


def test_instro_vna_get_freq_start_wraps_numeric_getter(vna: InstroVNA) -> None:
    measurement = vna.get_freq_start(ch=1)
    assert measurement is not None
    assert "ut.chget_freq_start" in measurement.channel_data
    assert measurement.channel_data["ut.chget_freq_start"] == pytest.approx([1_000_000_000.0])


def test_instro_vna_get_network_and_get_s_build_expected_shapes(vna: InstroVNA) -> None:
    network = vna.get_network(ports=list(range(DEFAULT_NPORTS)), ch=1)
    assert network.s.shape == (DEFAULT_NPOINTS, DEFAULT_NPORTS, DEFAULT_NPORTS)
    assert network.nports == DEFAULT_NPORTS

    s11 = vna.get_s(0, 0, ch=1)
    assert s11.s.shape == (DEFAULT_NPOINTS, 1, 1)


def test_instro_vna_save_network_writes_touchstone_file(vna: InstroVNA, tmp_path: Path) -> None:
    vna._storage = DiskStorage(path=str(tmp_path))

    saved_path = vna.save_network(name="test_network", ports=[0, 1], ch=1, format=NetworkFileFormat.SNP)

    assert saved_path.name.endswith(".s2p")
    assert saved_path.exists()


def test_instro_vna_open_and_close_delegate_to_driver(vna: InstroVNA) -> None:
    with mock.patch.object(vna.driver, "open") as driver_open, mock.patch.object(vna.driver, "close") as driver_close:
        vna.open()
        vna.close()

    driver_open.assert_called_once()
    driver_close.assert_called_once()


def test_nanovna_get_f_raises_on_empty_reply() -> None:
    with (
        mock.patch("instro.unstable.vna.drivers.nanovna_v2clone.serial.Serial", autospec=True),
        mock.patch("instro.unstable.vna.drivers.nanovna_v2clone.time.sleep"),
    ):
        driver = NanoVNAv2Clone(port="loop://")
        with mock.patch.object(driver, "_read_lines", return_value=[]):
            with pytest.raises(InstroError, match="no frequency data"):
                driver.get_f()


def test_instro_vna_set_freq_start_programs_driver_setter(vna: InstroVNA) -> None:
    vna.set_freq_start(1_500_000_000.0, ch=1)

    assert vna.driver.get_freq_start() == 1_500_000_000.0


def test_instro_vna_get_frequency_validates_unit_and_coerces_sweep_type(vna: InstroVNA) -> None:
    with pytest.raises(ValueError, match="invalid frequency unit"):
        vna.driver.get_frequency(unit="gigahurts")

    frequency = vna.driver.get_frequency(sweep_type="LIN")
    assert frequency.unit == "Hz"
