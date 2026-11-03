---
myst:
  html_meta:
    description: "Using InstroSDR to capture IQ samples and spectrum summaries"
---

# Software Defined Radio (SDR)

{.lead}
Using InstroSDR to capture IQ samples and spectrum summaries

:::{warning}
This Instrument category is new and is currently available only in the Unstable package. Its API is not settled and may change without notice between releases. See [Additional Packages](/installation.md#additional-packages).
:::

{py:class}`~instro.unstable.sdr.InstroSDR` provides a unified interface for software defined radios. This class is initialized with a vendor-specific driver (a {py:class}`~instro.unstable.sdr.SDRDriverBase` subclass), and provides the vendor-agnostic API (`set_center_freq`, `set_sample_rate`, `set_gain`, `measure_iq`, `measure_spectrum`, …).

## Creating an InstroSDR

```python
from instro.unstable.sdr import InstroSDR

sdr = InstroSDR(
    name="sdr",
    driver=MyVendorSDR(...),  # any concrete SDRDriverBase
)
```

Arguments, with types and defaults, are in the {py:class}`InstroSDR reference <instro.unstable.sdr.InstroSDR>`.

## Supported Vendors

No vendor drivers ship in this release yet. To use a radio now, see [Custom Driver Development](/library/custom-instruments.md#software-defined-radio-sdr), or open a [Driver Request](https://github.com/nominal-io/instro/issues) issue on GitHub.

## Details


### Setup

`InstroSDR` wraps a vendor driver: the driver owns the radio and its SDK, and `InstroSDR` adds measurements, commands, publishing, and the background daemon. The driver takes the device handle in `open()` and releases it in `close()`. A closed driver can be reopened, and using one before `open()` raises `RuntimeError`.

```python
sdr = InstroSDR("sdr", driver=MyVendorSDR(...))
sdr.open()
try:
    sdr.set_center_freq(100e6)
    sdr.set_sample_rate(2.4e6)
    iq = sdr.measure_iq(n_samples=4096)  # one block, published to the instrument's publishers
    z = np.asarray(iq.channel_data["sdr.rx0.i"]) + 1j * np.asarray(iq.channel_data["sdr.rx0.q"])
finally:
    sdr.close()
```

Tuners quantize frequency and sample rate, so read a setting back (`get_center_freq()`, `get_sample_rate()`) rather than assuming the request took effect exactly.

### Capturing IQ

`measure_iq(n_samples=N)` captures one block and returns it as a single `Measurement`: `N` samples on paired `.i` and `.q` channels that share one timestamp vector. The timestamps are spaced by the sample rate the device reports, and the block is placed to end when the read completed. A block that would overlap the previous one (the device had samples buffered) continues the previous block's timeline instead.

The radio is idle between calls, so samples between them are lost and show up as a gap in the timestamps. For gapless capture, use a [stream](#streaming).

`.i` is the real part and `.q` the imaginary part, as in `z` in the [Setup](#setup) example. Swapping them conjugates the signal, which mirrors the spectrum about the center frequency.

To plot both against the block's timestamps (integer nanoseconds since the Unix epoch):

```python
import matplotlib.pyplot as plt

t_ms = (np.asarray(iq.timestamps) - iq.timestamps[0]) / 1e6
plt.plot(t_ms, z.real, label="I (real)")
plt.plot(t_ms, z.imag, label="Q (imaginary)")
plt.xlabel("Time (ms)")
plt.legend()
plt.show()
```

### Streaming

A stream captures continuously, and each `fetch_iq` returns the next block, exactly one sample period after the last, so the blocks concatenate into one unbroken signal:

```python
sdr.start()
try:
    while True:
        block = sdr.fetch_iq(n_samples=262144)
finally:
    sdr.stop()
```

A stream covers one direction and a set of channels, fixed when it starts, so `fetch_iq` and `stop` take no channel argument. Receive and transmit are always separate streams, so starting a receive stream never keys a transmitter.

```python
sdr.start(direction=Direction.RX, channels=("0", "1"))
block = sdr.fetch_iq(n_samples=65536)   # both channels, time-aligned
```

#### Stream health and dropouts

Each fetch also publishes `backlog`, the samples waiting to be fetched, and `overflow`, `1` when the device dropped samples before this block. A consumer slower than the radio fills the buffer. The oldest samples are then dropped, `overflow` goes high for that block, a warning is logged, and the timestamps jump by the dropped samples, so blocks on either side of a dropout never look contiguous. Watch `backlog` to see it coming.

`n_samples` can't exceed the driver's buffer, `STREAM_BUFFER_SAMPLES`. Asking for more raises `ValueError` immediately.

#### One-shot reads during a stream

While a stream runs, `measure_iq`, `measure_spectrum`, and `compute_psd` read from it instead of the device, since most vendor libraries can't serve two reads on one handle. So a one-shot read:

- consumes the stream's next block, staying in its timeline;
- takes that block whole: `channels` defaults to every streamed channel, and any you leave out are discarded with it;
- publishes `backlog` and `overflow`, like a fetch;
- returns buffered samples, up to `get_backlog()` old.

Asking for a channel the stream doesn't cover raises, as does calling the driver's own `read_iq` during a stream. `is_streaming()` reports whether a stream is running.

#### In the background

`start(background=True)` hands the fetch loop to the instrument's daemon, so blocks publish without a loop of your own:

```python
sdr.start(background=True, n_samples=262144, publish_spectrum=True)
```

The blocking fetch paces the loop, so there is no interval to set (see {py:attr}`~instro.unstable.sdr.InstroSDR.background_interval`).

:::{warning}
Don't alternate `measure_spectrum` with `fetch_iq`. Each spectrum call consumes a block whose IQ is never published, so the loop loses about half the signal. Pass `publish_spectrum=True` to `fetch_iq` to get both from one block.
:::

### Spectrum

`measure_spectrum()` publishes four scalar features of one block, not the spectrum array: a spectrum is a function of frequency at one instant, while a `Measurement` holds channels over time. For the array, `compute_psd()` returns it without publishing. The {py:meth}`~instro.unstable.sdr.InstroSDR.measure_spectrum` and {py:meth}`~instro.unstable.sdr.InstroSDR.compute_psd` references define the features and return values.

```python
freqs, power_db = sdr.compute_psd(n_samples=16384)
peak = freqs[int(np.argmax(power_db))]
```

:::{note}
A direct-conversion receiver leaks its local oscillator into the bin at the center frequency. Mask a few kHz around the center before searching for a peak, or that spike always wins. Likewise, `occupied_bw_hz` is only meaningful on a clean signal: over mostly noise it approaches the full sample rate.
:::

### Published channels

Every call publishes under `{name}.{path}.{descriptor}`, where `{path}` is the signal path it applied to (`rx0` by default). A two-channel transceiver publishes `rx0`, `rx1`, `tx0`, and `tx1` independently, so an SDR named `sdr` publishes `sdr.rx0.i`, `sdr.rx0.center_freq.cmd`, and so on.

| Method | Descriptor | Type |
|---|---|---|
| `set_<setting>()` | `<setting>.cmd` | command |
| `get_<setting>()` | `<setting>` | telemetry |
| `measure_iq()`, `fetch_iq()` | `i`, `q` | telemetry (one value per sample) |
| `fetch_iq()`, and one-shot reads during a stream | `backlog`, `overflow` | telemetry |
| `measure_spectrum()` | `spectrum.peak_power_db`, `spectrum.peak_freq_hz`, `spectrum.mean_power_db`, `spectrum.occupied_bw_hz` | telemetry |

Settings: `center_freq`, `sample_rate`, `gain`, `gain_mode`, `bandwidth`, `freq_correction`, `antenna`.

## Custom Driver Development

For more information on writing a custom driver, see [Custom Driver Development](/library/custom-instruments.md#software-defined-radio-sdr).
