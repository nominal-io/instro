---
orphan: true
card: HackRF One
image: HackRFOne.png
myst:
  html_meta:
    description: "A driver for InstroSDR"
---

# HackRFOne

{.lead}
A driver for [`InstroSDR`](/sdr.md)

![HackRF One](HackRFOne.png){.driver-image}

The {py:obj}`HackRFOne <instro.unstable.sdr.drivers.hackrf_one.HackRFOne>` driver supports the HackRF One through the [`python_hackrf`](https://github.com/GvozdevLeonid/python_hackrf) bindings over `libhackrf`, and can be used to instantiate an [InstroSDR](/sdr.md).

## Creating an [`InstroSDR`](/sdr.md) with {py:obj}`HackRFOne <instro.unstable.sdr.drivers.hackrf_one.HackRFOne>`

Install the `hackrf` extra, after the system library described in [libhackrf](/installation.md#libhackrf):

```bash
pip install "instro[unstable,hackrf]"
```

```python
from instro.unstable.sdr.drivers import HackRFOne
from instro.unstable.sdr import InstroSDR

sdr = InstroSDR(
    name="hackrf",
    driver=HackRFOne(device_index=0),
)
```

`device_index` selects among connected radios. `HackRFOne(serial_number="...")` addresses one directly, regardless of enumeration order.

Three behaviors set it apart from most SDR drivers.

## Readback comes from the driver, not the radio

`libhackrf` exposes no getters. Nothing about the radio's state can be read back over USB: not frequency, sample rate, bandwidth, or any gain. The driver caches every value it writes and serves `get_center_freq()`, `get_sample_rate()`, `get_bandwidth()`, and `get_gain()` from that cache.

Two consequences are worth knowing. `open()` programs a known state onto the radio (`DEFAULT_CENTER_FREQ_HZ`, `DEFAULT_SAMPLE_RATE_HZ`, and the default gains) because the hardware keeps whatever the previous user left behind. And tuning resolution is roughly 50 Hz, so the true tuned frequency can differ slightly from the value read back.

Setting the sample rate resets the baseband filter to `0.75 * sample_rate` in hardware. The cached bandwidth follows it, so set the sample rate first and the bandwidth second:

```python
sdr.set_sample_rate(8e6)      # filter becomes 6 MHz
sdr.set_bandwidth(5e6)        # now narrow it
```

`set_bandwidth()` snaps to one of the 16 widths the filter actually has, and `get_bandwidth()` returns the snapped value.

## Gain is three stages, with no AGC

HackRF has no single gain register and no automatic gain control, so `set_gain_mode()` raises `NotImplementedError`. Gain is an RX LNA stage (0 to 40 dB in 8 dB steps), an RX VGA stage (0 to 62 dB in 2 dB steps), and a separate on/off RF amplifier of roughly 11 dB.

`set_gain()` distributes a single figure across the two RX stages: it fills the LNA first, then the VGA, for a 0 to 102 dB range. Because the stages are quantized, `get_gain()` reports what the hardware landed on rather than what was requested, and a request of 7 dB becomes 6 dB. Values outside the range raise `ValueError` instead of being silently clamped.

Set the stages individually when the split matters:

```python
sdr.driver.set_lna_gain(24)
sdr.driver.set_vga_gain(20)
sdr.driver.set_amp_enable(True)    # the ~11 dB amplifier
sdr.driver.set_bias_tee(True)      # 3.3V antenna port power
```

`set_gain(..., direction=Direction.TX)` sets the transmit VGA (0 to 47 dB in 1 dB steps). The radio is half duplex, so receive and transmit never run at once, and the driver acquires on the receive path only.

The RF amplifier is not counted in `get_gain()`, and the firmware drops the bias tee whenever the radio returns to idle, so re-assert it after each stream.

## Every read is a stream

`libhackrf` has no synchronous read. Samples arrive only through a callback on its USB thread. `read_iq()` therefore starts the stream, collects blocks until it has enough, and stops again, so a one-shot read costs a stream start and stop. Prefer `start()` and `fetch_iq()` when reading repeatedly:

```python
sdr.start(background=True, n_samples=16384)
```

Samples arrive as interleaved signed 8-bit pairs and are normalized to complex values in [-1.0, 1.0). A block that ends on an odd byte holds its stray byte back for the next block, so IQ pairs never split across blocks. Any positive `n_samples` up to `HackRFOne.STREAM_BUFFER_SAMPLES` is accepted.

## Unsupported capabilities

Capabilities the radio does not have raise `NotImplementedError`: `set_gain_mode()`, `get_gain_mode()`, `set_freq_correction()`, `get_freq_correction()`, and the antenna selection methods. `set_antenna_enable` in the vendor API is bias tee power, not antenna selection, so it is exposed as `set_bias_tee()`.

Parameters and methods specific to {py:obj}`HackRFOne <instro.unstable.sdr.drivers.hackrf_one.HackRFOne>` can be found in the [SDK](/sdk/index.md).
