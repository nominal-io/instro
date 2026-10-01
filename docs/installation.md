---
myst:
  html_meta:
    description: "Installing instro and Driver Dependencies"
---

# Installation

{.lead}
Installing instro and Driver Dependencies

## Basic Install

`instro` is published on [PyPI](https://pypi.org/project/instro/), and can be installed using either [`uv`](https://docs.astral.sh/uv/) or `pip`. `instro` supports Python 3.10-3.14.

<div style="display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; align-items: start">

```bash
uv add instro
```

```bash
pip install instro
```

</div>

## Additional Packages

`instro` has additional packages for utilizing vendor instruments that require their own dependencies.  These are grouped by individual vendors and by instrument type where useful.

<div style="display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; align-items: start">

```bash
# install ni-daq
uv add "instro[nidaq]"
# or install all DAQs
uv add "instro[daq]"
# install everything
uv add "instro[all]"
```

```bash
# install ni-daq
pip install "instro[nidaq]"
# or install all DAQs
pip install "instro[daq]"
# install everything
pip install "instro[all]"
```

</div>

:::{note}
These packages supply Python dependencies. See [Driver Dependencies](#driver-dependencies) for external installers required for some instruments.
:::

Here is a table of available packages:

| Instrument Type | Package | Contents |
| --- | --- | --- |
| **All** | `all` | All drivers in this table|
| **DAQ** | `daq` | All DAQ drivers |
|  | `nidaq` | NI DAQ package |
|  | `mccdaq` | Measurement Computing (MCC) package |
|  | `labjack` | LabJack package |
| **I2C** | `i2c` | All I2C packages |
|  | `aardvark` | TotalPhase Aardvark package |
| **EtherNet** | `ethernetip` | EtherNet/IP support |
| **Other** | `contrib` | Community-contributed drivers |
|  | `unstable` | Experimental unstable modules |
| **SDR** | `hackrf` | HackRF One bindings (`python-hackrf`). Not part of `all`: it builds against a system `libhackrf` ([libhackrf](#libhackrf)) |

## Using Nominal Connect

If you're a Nominal Connect user, you don't need to install `instro` yourself. Just add it to your packages list in your `app.connect` file, and Nominal Connect will install it into your Python environment automatically. Use any of the [additional packages](#additional-packages) above to pull in more support.

```yaml
python:
  packages:
    - instro[daq]
```

## Driver Dependencies

`instro` depends on external software to be installed for various hardware interactions to be successful.

:::{note}
**Understanding the distinction**: These vendor-installable drivers are **different from** the driver classes that an `Instrument` is composed with:

- **Vendor-installable drivers**: External libraries/drivers developed by instrument vendors that must be installed to your system operating system.
- **`instro` driver classes**: The vendor/model-specific Python code that `Instrument` uses to interact with the external software drivers.
:::

The table below shows which vendor drivers are required for which `instro` instrument types:

| Vendor Driver | Required For | Used By instro Drivers |
|--------------|--------------|------------------------|
| **VISA** | SCPI/VISA-based instruments (PSUs, ELoads, Keysight DAQ) | All VISA-based driver implementations |
| **NI-DAQmx** | National Instruments DAQ devices | `NIDAQDriver` |
| **LJM (LabJack Module)** | LabJack T-Series devices | `LabJackTSeriesDriver` |
| **MCC Universal Library (`mcculw`)** | Measurement Computing DAQ devices | `MCCDriver` |
| **Total Phase USB Drivers** | Total Phase Aardvark I2C/SPI Host Adapter | `Aardvark` |
| **libhackrf** | HackRF One software defined radio | `HackRFOne` |

## VISA
### Used By

VISA (Virtual Instrument Software Architecture) is used by `instro` for all SCPI/VISA based instrument connections. Some support is provided when you install `instro`, but you may need additional drivers.

### Instro installs

Installing `instro` also installs `pyvisa`, `pyvisa-py`, and the full set of `pyvisa-py` backend libraries (`pyusb`, bundled libusb via `libusb-package`, `pyserial`, `psutil`, `zeroconf`, `pyvicp`, and `gpib-ctypes`). TCP/IP, USB-TMC, serial, HiSLIP, and VICP resources work with no extra setup.

- **pyvisa** (Python VISA library)
- **pyvisa-py** (default Python backend)
- **pyusb + libusb** (USB-TMC backend; libusb ships bundled via `libusb-package`)
- **pyserial** (ASRL/serial backend)
- **psutil, zeroconf, pyvicp** (TCPIP discovery, HiSLIP discovery, and VICP support)

### You may need

- **Native GPIB driver**: NI-488.2 for NI GPIB interfaces, or linux-gpib on Linux.
- **Vendor VISA runtimes** (optional; required for PXI/VXI or vendor-specific transports)

## NI-DAQmx

NI-DAQmx is the National Instruments driver for their DAQ devices. It must be installed before using National Instruments DAQ devices with `instro`.

### Used By

- **`InstroDAQ`**: National Instruments DAQ devices (via `NIDAQDriver`)

### Installation

1. Download and install NI-DAQmx from the [National Instruments website](https://www.ni.com/en/support/downloads/drivers/download.ni-daq-mx.html)
2. Verify installation by opening NI-MAX (Measurement & Automation Explorer) and confirming your device appears

### Alternative Installation (Python)

You can also install NI-DAQmx via the `nidaqmx` Python package's driver installer:
```bash
python -m nidaqmx installdriver
```

## LJM (LabJack Module)

LJM is the LabJack Module library required to communicate with LabJack T-Series devices (T4, T7, T8).

### Used By

- **`InstroDAQ`**: LabJack T-Series devices (via `LabJackTSeriesDriver`)

### Installation

1. Download LJM from the [LabJack website](https://labjack.com/support/software/installers/ljm)
2. Install the LJM package for your operating system
3. The Python bindings (`labjack.ljm`) are typically included with the LJM installation

## MCC Universal Library (`mcculw`)

The MCC Universal Library is the Measurement Computing driver stack for their USB and Ethernet DAQ devices. It must be installed before using MCC devices with `instro`.

### Used By

- **`InstroDAQ`**: Measurement Computing DAQ devices (via `MCCDriver`)

### Installation

1. Download and install the MCC DAQ Software (includes InstaCal and the Universal Library) from the [Measurement Computing website](https://digilent.com/reference/software/instacal/start).
2. Open **InstaCal** and confirm your device appears in the board list. Note the device's unique ID. You pass this as the `device_id` argument to `MCCDriver()` when constructing the driver handed to `InstroDAQ`.
3. The Python bindings (`mcculw`) are installed automatically when you install the `instro[mccdaq]` or `instro[daq]` extras.

:::{note}
The MCC Universal Library is currently only available on Windows. MCC DAQ support in `instro` is therefore limited to Windows hosts.
:::

## Total Phase USB Drivers

Total Phase USB Drivers are required to communicate with Total Phase Aardvark I2C/SPI Host Adapter devices.

### Used By

- **`I2CInterface`**: Total Phase Aardvark I2C/SPI Host Adapter (via `Aardvark`)

### Installation

1. Download the USB Drivers from the [Total Phase website](https://www.totalphase.com/products/usb-drivers/)

## libhackrf

`libhackrf` is the Great Scott Gadgets host library required to communicate with a HackRF One. The `python-hackrf` bindings ship as a source distribution and build against it at install time.

### Used By

- **`InstroSDR`**: HackRF One (via `HackRFOne`)

### Installation

1. Install `libhackrf` 2024.02.1 or newer: `brew install hackrf` on macOS, or `libhackrf-dev` on Debian and Ubuntu.
2. Make sure `pkg-config` and a C compiler are available, since the bindings compile against the library.
3. Install the `instro[unstable,hackrf]` extras, which build the Python bindings (`python-hackrf`).
4. Run `hackrf_info` to confirm the radio enumerates. A HackRF that shows its **3V3** LED but not **USB** has power but no data connection, which usually means a charge-only cable.

:::{note}
The `hackrf` extra is not part of `instro[all]`, because building it needs the system library above.
:::
