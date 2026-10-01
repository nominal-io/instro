"""Example: scanning a band with InstroSDR.

Steps an RTL-SDR across the FM broadcast band and reports the strongest signal in
each window. Uses ``compute_psd``, which returns the full spectrum to the caller
without publishing it; only the per-window summary is published.
"""

import numpy as np

from instro.unstable.sdr import InstroSDR
from instro.unstable.sdr.drivers import RTLSDR

BAND_START_HZ = 88.1e6
BAND_STOP_HZ = 107.9e6
SAMPLE_RATE_HZ = 2.4e6
GAIN_DB = 30.0
N_SAMPLES = 16384  # must be a multiple of RTLSDR.READ_GRANULARITY (256)
DC_GUARD_HZ = 20e3  # width of the LO-leakage spike to ignore at each window's center

sdr = InstroSDR(name="rtl", driver=RTLSDR(device_index=0))

sdr.open()
try:
    sdr.set_sample_rate(SAMPLE_RATE_HZ)
    sdr.set_gain(GAIN_DB)

    # Retune by one full span each step so the windows tile the band without overlap.
    span_hz = float(sdr.get_sample_rate().latest)
    centers = np.arange(BAND_START_HZ + span_hz / 2, BAND_STOP_HZ, span_hz)

    print(f"{'window (MHz)':>16}  {'peak (MHz)':>12}  {'peak (dB)':>10}  {'noise (dB)':>11}")
    for center_hz in centers:
        sdr.set_center_freq(float(center_hz))

        # The first block after a retune can still hold samples from the old frequency,
        # so discard one before measuring.
        sdr.measure_iq(n_samples=N_SAMPLES)

        psd = sdr.compute_psd(n_samples=N_SAMPLES)
        assert psd is not None  # the dongle always returns samples once open
        freqs, power_db = psd

        # A direct-conversion receiver leaks its local oscillator into bin 0, which lands on
        # the center frequency. Without masking it out, every window's "peak" is that spike.
        searchable = power_db.copy()
        searchable[np.abs(freqs - center_hz) < DC_GUARD_HZ] = -np.inf

        peak = int(np.argmax(searchable))
        noise_floor_db = float(np.median(power_db))
        print(f"{center_hz / 1e6:>16.1f}  {freqs[peak] / 1e6:>12.3f}  {power_db[peak]:>10.1f}  {noise_floor_db:>11.1f}")

        # Publishes the four scalar spectrum channels for this window.
        sdr.measure_spectrum(n_samples=N_SAMPLES)
finally:
    sdr.close()
