---
orphan: true
card: RTL-SDR
image: RTLSDR.png
myst:
  html_meta:
    description: "A driver for InstroSDR"
---

# RTLSDR

{.lead}
A driver for [`InstroSDR`](/sdr.md)

![RTL-SDR](RTLSDR.png){.driver-image}

The {py:obj}`RTLSDR <instro.unstable.sdr.drivers.rtl_sdr.RTLSDR>` driver supports any `librtlsdr`-compatible dongle through `pyrtlsdr`, and can be used to instantiate an [InstroSDR](/sdr.md).

## Creating an [`InstroSDR`](/sdr.md) with {py:obj}`RTLSDR <instro.unstable.sdr.drivers.rtl_sdr.RTLSDR>`

```python
from instro.unstable.sdr.drivers import RTLSDR
from instro.unstable.sdr import InstroSDR

sdr = InstroSDR(
    name="rtl",
    driver=RTLSDR(device_index=0),
)
```

`device_index` selects among multiple connected dongles. Any additional keyword arguments pass through to `rtlsdr.RtlSdr`, so `RTLSDR(serial_number="00000001")` addresses a specific device regardless of enumeration order.

## Sample-count granularity

`librtlsdr` transfers whole 512-byte USB blocks and an IQ sample is two bytes, so `n_samples` must be a positive multiple of `RTLSDR.READ_GRANULARITY` (256). A request that is not raises `ValueError` before the device is touched. A short read makes the vendor library close the device.

## Signal paths

An RTL-SDR is receive-only with a single path. `RTLSDR` rejects anything but `rx` channel `"0"` rather than silently acting on the path it does have. A stream buffers up to `RTLSDR.STREAM_BUFFER_SAMPLES` samples.

Parameters and methods specific to {py:obj}`RTLSDR <instro.unstable.sdr.drivers.rtl_sdr.RTLSDR>` can be found in the [SDK](/sdk/index.md).
