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

`InstroSDR` provides a unified interface for software defined radios. This class is initialized with a vendor-specific driver (`HackRFOne`, …), and provides the vendor-agnostic API (`set_center_freq`, `set_sample_rate`, `set_gain`, `measure_iq`, `measure_spectrum`, …).

## Creating an InstroSDR

```python
from instro.unstable.sdr import InstroSDR

sdr = InstroSDR(
    name="sdr",
    driver=MyVendorSDR(...),  # any concrete SDRDriverBase
)
```

| Parameter | Type | Description |
|---|---|---|
| `name` | `str` | Channel-name prefix for published data |
| `driver` | `SDRDriverBase` | Concrete vendor driver |
| `publishers` | `list[Publisher]` | Optional; publishers receiving emitted data |
| `**kwargs` | | Default tags; `dataset_rid=` attaches a `NominalCorePublisher` |

## Supported Vendors

:::{driver-cards} sdr
:::

If your vendor or model is not listed, see [Custom Driver Development](/library/custom-instruments.md#software-defined-radio-sdr), or open a [Driver Request](https://github.com/nominal-io/instro/issues) issue on GitHub.

## Details

The following presents details about the `InstroSDR`. Specific driver details can be found on [their pages](#supported-vendors).

### Driver Composition

An `InstroSDR` is built from a concrete driver:

```python
InstroSDR("name", driver=MyVendorSDR(...))
```

- The **vendor driver** owns the connection setup and the vendor SDK.
- **`InstroSDR`** owns the category-level workflow: IQ and spectrum measurements, commands, publishers, the background daemon.

### Lifecycle

The driver captures connection settings on construction and takes the USB handle in `open()`. `close()` releases it, and a closed driver can be reopened:

```python
sdr = InstroSDR("sdr", driver=MyVendorSDR(...))
sdr.open()
try:
    ...
finally:
    sdr.close()
```

Using an unopened driver raises `RuntimeError` rather than reaching into a closed handle.

### IQ blocks and their timebase

`measure_iq(n_samples=N)` returns one `Measurement` holding `N` samples across paired `.i` and `.q` channels, not one `Measurement` per sample. The two channels share a timestamp vector whose spacing comes from the rate the **device** reports, not from the host clock, so an IQ block carries the radio's own notion of elapsed time.

Blocks are *backstamped*: the read returns after the samples were captured, so the block is placed to **end** at the moment the read completed. A block sometimes overlaps its predecessor, meaning the device had samples buffered and the read returned faster than the block's own duration. Such a block continues the previous block's timeline instead. A wider gap re-anchors to the wall clock, so genuine dropouts remain visible as gaps.

:::{note}
`measure_iq` acquires one block per call. Samples arriving between calls are lost, and that loss shows up as a gap in the timestamps. Use `start()` and `fetch_iq()` for gapless capture.
:::

### Streaming

`measure_iq` is a one-shot read: the radio is idle between calls, so whatever it would have produced in that window is gone. For continuous capture, start a stream and fetch from it:

```python
sdr.start()
try:
    while True:
        block = sdr.fetch_iq(n_samples=262144)
finally:
    sdr.stop()
```

Consecutive fetches are contiguous. Successive blocks are exactly one sample period apart, so a stream can be concatenated into one unbroken signal.

A stream covers **one direction and a set of channels**, chosen when it starts:

```python
sdr.start(direction=Direction.RX, channels=("0", "1"))
block = sdr.fetch_iq(n_samples=65536)   # both channels, time-aligned
sdr.stop()
```

`fetch_iq` and `stop` take no channel argument: the stream already knows which channels it covers, the same way SoapySDR's `activateStream` acts on a stream handle and UHD's stream args fix the channel list up front. Receive and transmit are always separate streams, so starting a receive stream never keys a transmitter.

### One-shot reads during a stream

While a stream is running, `measure_iq`, `measure_spectrum` and `compute_psd` are served from that stream rather than from the device. Reading the device directly would mean two concurrent reads on one handle, which most vendor libraries do not support and which would quietly cost the stream the samples the one-shot read consumed.

Consequences worth knowing:

- A one-shot read **consumes** from the stream, so it slots into the fetch sequence rather than duplicating or skipping data. Interleaving `measure_iq` with `fetch_iq` still yields one unbroken timeline.
- A stream block is consumed whole, so `channels` defaults to every channel the stream covers. Naming a subset still works and still publishes only that subset, but the rest of that block is consumed with it and cannot be fetched again.
- It publishes `backlog` and `overflow` exactly as a fetch does, so a dropout is never hidden by having asked for the block through the one-shot call.
- It returns buffered samples, which can be as old as `get_backlog()` reports. For the most recent samples, drain the backlog or keep the consumer ahead of the radio.

Requesting a channel the stream does not cover raises rather than returning a different path's data. `is_streaming(direction=...)` reports whether routing is in effect.

Calling `read_iq` straight on a driver during its own stream raises: only the HAL routes.

Each fetch also publishes stream health on two channels:

| Channel | Meaning |
|---|---|
| `backlog` | Samples already captured and waiting to be fetched |
| `overflow` | `1` when the device dropped samples before this block, `0` otherwise |

A consumer slower than the radio eventually fills the buffer, at which point the oldest samples are dropped, `overflow` goes high for that block, and a warning is logged. The flag is per fetch: the next clean block reports `0` again. Watch `backlog` to see it coming.

`n_samples` cannot exceed what the stream buffers, since the reader evicts to stay inside that bound and the request could never be filled. Asking for more raises `ValueError` immediately rather than waiting out the fetch timeout while samples are discarded. Each driver sets the bound as `STREAM_BUFFER_SAMPLES`.

A dropout also opens a real gap in the timestamps. The driver counts what it discarded, so the block after a dropout is placed that many sample periods later rather than being chained on as though nothing was lost. Concatenating blocks across a dropout therefore splices two genuinely separate stretches of signal, and the timestamps say so.

### Streaming in the background

`start(background=True)` hands the fetch loop to the instrument's daemon, so blocks publish continuously without a loop of your own:

```python
sdr.start(background=True, n_samples=262144, publish_spectrum=True)
```

The daemon calls `fetch_iq` with that block size. Because `fetch_iq` blocks until the radio has the samples, it paces the loop itself, exactly as `InstroDAQ` does for hardware-timed reads. `background_interval` is therefore forced to `0` and refuses to be set: any interval would sit *between* blocks and punch gaps into an otherwise gapless stream.

:::{warning}
Do not call `measure_spectrum` in a `fetch_iq` loop. While streaming it is served from the stream too, so it consumes a whole block and publishes only the four scalars from it, and the IQ in that block never reaches your publishers. A loop alternating the two publishes roughly half the samples, in blocks the length of `n_samples` separated by gaps of the same length. Pass `publish_spectrum=True` to `fetch_iq` instead and both come from one block.
:::

:::{note}
`stop()` always stops both the daemon and the hardware stream, and `close()` routes through it, so a stream cannot outlive the device handle.
:::

### Reassembling the complex signal

`.i` is the real part and `.q` the imaginary part:

```python
z = np.asarray(iq.channel_data["sdr.rx0.i"]) + 1j * np.asarray(iq.channel_data["sdr.rx0.q"])
```

Swapping them conjugates the signal, which mirrors the spectrum about the center frequency.

### Spectrum

`measure_spectrum()` publishes four **scalar** channels summarizing a Hann-windowed FFT of one block. It does not publish the spectrum array: a power spectrum is a function of frequency at a single instant, and `Measurement` describes channels over time.

For the full array, `compute_psd()` returns `(frequencies_hz, power_db)` to the caller and publishes nothing:

```python
freqs, power_db = sdr.compute_psd(n_samples=16384)
peak = freqs[int(np.argmax(power_db))]
```

:::{note}
A direct-conversion receiver leaks its local oscillator into the DC bin, which lands on the center frequency. Mask a few kHz around the center before searching for a peak, or the strongest bin will always be that spike.
:::

### Published channels

Every measurement/command call produces a channel keyed under `{name}.{path}.{descriptor}`, where `{name}` is the constructor argument and `{path}` names the signal path the call applied to (`rx0` by default). A two-channel transceiver publishes `rx0`, `rx1`, `tx0` and `tx1` independently.

| Method | Descriptor | Type |
|--------|------------|------|
| `set_center_freq()` | `center_freq.cmd` | command |
| `set_sample_rate()` | `sample_rate.cmd` | command |
| `set_gain()` | `gain.cmd` | command |
| `set_gain_mode()` | `gain_mode.cmd` | command |
| `set_bandwidth()` | `bandwidth.cmd` | command |
| `set_freq_correction()` | `freq_correction.cmd` | command |
| `set_antenna()` | `antenna.cmd` | command |
| `get_center_freq()` | `center_freq` | telemetry |
| `get_sample_rate()` | `sample_rate` | telemetry |
| `get_gain()` | `gain` | telemetry |
| `get_gain_mode()` | `gain_mode` | telemetry |
| `get_bandwidth()` | `bandwidth` | telemetry |
| `get_freq_correction()` | `freq_correction` | telemetry |
| `get_antenna()` | `antenna` | telemetry |
| `measure_iq()` | `i`, `q` | telemetry (one value per sample) |
| `measure_spectrum()` | `spectrum.peak_power_db` | telemetry |
| `measure_spectrum()` | `spectrum.peak_freq_hz` | telemetry |
| `measure_spectrum()` | `spectrum.mean_power_db` | telemetry |
| `measure_spectrum()` | `spectrum.occupied_bw_hz` | telemetry |
| `fetch_iq()` | `i`, `q` | telemetry (one value per sample) |
| `fetch_iq()` | `backlog`, `overflow` | telemetry |

So an SDR named `sdr` publishes `sdr.rx0.i`, `sdr.rx0.center_freq.cmd`, and so on.

:::{note}
`compute_psd()` returns `(frequencies_hz, power_db)` as numpy arrays directly and does not publish. Every other readback listed above publishes a `Measurement` on the descriptor shown.

`occupied_bw_hz` is the width of the band holding 99% of total power. Over a span that is mostly noise it approaches the full sample rate; it is only meaningful on a clean signal.
:::

### Method reference

| Method | Returns | Description |
|---|---|---|
| `open()` | `None` | Open the device |
| `close()` | `None` | Close the device, stop the daemon, close publishers |
| `set_center_freq(frequency_hz)` | `Command` | Tune the RF center frequency |
| `get_center_freq()` | `Measurement` | Center frequency the device accepted |
| `set_sample_rate(sample_rate_hz)` | `Command` | Set the IQ sample rate |
| `get_sample_rate()` | `Measurement` | Sample rate the device accepted |
| `set_gain(gain_db)` | `Command` | Set receive gain |
| `get_gain()` | `Measurement` | Current gain |
| `set_bandwidth(bandwidth_hz)` | `Command` | Set IF/filter bandwidth (`0` means auto) |
| `get_bandwidth()` | `Measurement` | Current bandwidth |
| `start(channels, background, n_samples, publish_spectrum)` | `None` | Begin continuous acquisition over a channel set |
| `stop()` | `None` | Stop every running stream and the background daemon |
| `measure_iq(n_samples, channels)` | `Measurement \| None` | One aligned IQ block as paired `.i`/`.q` channels |
| `fetch_iq(n_samples, publish_spectrum)` | `Measurement \| None` | Next contiguous block; optionally publishes its spectrum too |
| `get_backlog()` | `int` | Samples per channel waiting to be fetched |
| `measure_spectrum(n_samples)` | `Measurement \| None` | Four scalar spectrum features |
| `compute_psd(n_samples)` | `tuple[ndarray, ndarray] \| None` | `(frequencies_hz, power_db)`; publishes nothing |
| `driver` | `SDRDriverBase` | The underlying driver |

:::{note}
Tuners quantize both frequency and sample rate, so the value a getter reports can differ from what was requested. Read back rather than assuming the request took effect exactly.
:::

## Custom Driver Development

For more information on writing a custom driver, see [Custom Driver Development](/library/custom-instruments.md#software-defined-radio-sdr).
