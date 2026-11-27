---
myst:
  html_meta:
    description: "Drivers Nominal accepts on contributor verification rather than direct testing"
---

# Contrib drivers

{.lead}
Drivers Nominal accepts on contributor verification rather than direct testing

[`instro-contrib`](https://github.com/nominal-io/instro/tree/main/packages/instro-contrib) is a separate package containing drivers that have passed code review but were **verified on real hardware by the contributor**, not by Nominal. They typically come from one of two sources:

- **Community contributions**: drivers submitted by users who own the hardware and tested locally.
- **Staff drops**: drivers Nominal has written for a device it doesn't have on hand.

If a driver is in `instro-contrib`, that means: it type-checks, it has a unit test against a mocked transport, and a Nominal reviewer has read it. It does **not** mean Nominal has personally run it on the device.

## Install

`instro-contrib` is published on PyPI and installed via the `contrib` extra:

```bash
$ uv add "instro[contrib]"
```

or with pip:

```bash
(.venv) $ pip install "instro[contrib]"
```

It's also included in the `all` extra. See [Installation](/installation.md#additional-packages) for the full list of extras.

## Import path

Import contrib drivers from `instro.contrib.drivers`:

```python
from instro import InstroPSU
from instro.contrib.drivers import SomeVendorPSU

psu = InstroPSU(
    name="myPSU",
    driver=SomeVendorPSU(visa_resource="USB0::..."),
    num_channels=1,
)
```

The category-level class (`InstroPSU`, `InstroDMM`, etc.) is always imported from core `instro`. Only the concrete driver lives in `instro.contrib`.

## Stability expectations

- **API may change between releases.** A contrib driver's constructor or behavior may be revised based on user feedback or graduation. Pin to a specific commit if you need a frozen target.
- **No regression verification by Nominal.** A core dependency change (e.g. a `VisaDriver` refactor) might break a contrib driver between releases. The smoke test catches import-level breaks. Behavioral breaks may not surface until you run it.
- **Bugs are accepted as PRs.** If you hit a problem with a contrib driver, the fastest path to a fix is usually contributing one: the original author may not be reachable.

## Available drivers

This is the complete set of `instro-contrib` drivers as of the current release.

### Matrix WPS300S-series PSU

**Class**: `MatrixWPS300S`  
**Import**: `from instro.contrib.drivers import MatrixWPS300S`  
**Tested against**: WPS300S-150-5 (0–150 V, 0–5 A, 300 W)  
**Protocol**: SCPI over RS-232, 9600 baud 8-N-1

### Siglent SDS3034X HD oscilloscope

Use the four-channel `SiglentSDS3034XHD` driver with a VXI-11 Ethernet resource.
The driver was tested on firmware `4.8.9.1.0.3.9`.
With the compensation signal on channel 3, enable that input and set an edge trigger on it.
Replace `scope.local` with your scope's hostname or IP address.

```python
from instro import InstroScope, TriggerSlope, TriggerType
from instro.contrib.drivers import SiglentSDS3034XHD

scope = InstroScope("bench", driver=SiglentSDS3034XHD("TCPIP0::scope.local::inst0::INSTR"), num_channels=4)
scope.open()
try:
    scope.set_trigger_type(TriggerType.EDGE)
    scope.set_trigger_source(3)
    scope.set_trigger_slope(TriggerSlope.RISING)
    scope.set_trigger_level(1.5)
    scope.single()
    trace = scope.fetch_waveform(channel=3, timeout=5)
    print(len(trace.values), max(trace.values) - min(trace.values))
finally:
    scope.close()
```

Waveform reads use the scope's signed WORD transfer, the probe factor from the captured record,
and timestamps rounded to integer nanoseconds. Fetch a single acquisition as above, or call
`stop_acquisition()` before reading the current record. Segmented acquisitions are unsupported.
The driver also supports built-in measurements, trigger and acquisition controls, PNG screenshots,
and setup save and recall.
Use AC or DC coupling on each channel when calling `sync_configuration()`, since the
shared scope API only represents those two coupling modes.
When this firmware recalls a setup with disabled inputs, it can reassign auxiliary sources
and recalculate decoder thresholds. Check these settings if your workflow uses them.

Drivers should be added to this list as part of the merge which brings them into the main branch. For pre-release drivers browse [`packages/instro-contrib/instro/contrib/`](https://github.com/nominal-io/instro/tree/main/packages/instro-contrib/instro/contrib) or [this file](https://github.com/nominal-io/instro/blob/main/docs/guides/library/contrib.mdx) in the source GitHub repository.

## Graduation to core

When Nominal acquires the hardware and can verify the driver directly, it moves into core `instro`. The import path drops `.contrib`:

```python
# Before (contrib)
from instro.contrib.psu.drivers import SomeVendorPSU

# After graduation (core)
from instro.psu.drivers import SomeVendorPSU
```

Graduations are noted in `CHANGELOG.md`. The old `instro.contrib` import path is a hard cutover. There is no compatibility shim: old code stays broken until the import is updated.

For one release after the graduation, a stub at the old path raises an `ImportError` that names the destination:

```python
from instro.contrib.psu.drivers import SomeVendorPSU
# ImportError: SomeVendorPSU graduated to core in v1.2. Import it from instro.psu.drivers.
```

The stub is deleted in the following release, after which the old import fails with a plain `ImportError`.

## Contributing a driver

See the **Contrib Package** section of [`CONTRIBUTING.md`](https://github.com/nominal-io/instro/blob/main/CONTRIBUTING.md) for the contribution bar and review process.
