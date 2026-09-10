import numpy as np 
import xarray as xr
from scipy.signal import stft
from instro.unstable.sdr import InstroSDR
from instro.unstable.sdr.drivers import RTLSDR

# params
khz ,mhz = 1e3,1e6
f_center = 89.7*mhz
f_bw     = 2*mhz

# sdr 
sdr = InstroSDR(
    name='billy',
    driver = RTLSDR())

# measure
sdr.set_center_freq(f_center)
sdr.set_sample_rate(f_bw)
fs = sdr.get_sample_rate().latest
iq = sdr.measure_iq(n_samples = 1048576 )
sdr.close()

# process
## convert IQ to complex array
z = np.array(iq.channel_data[f'{sdr.name}.i'])*1j +\
    np.array(iq.channel_data[f'{sdr.name}.q'])

## compute stft 
f, times, Z = stft(z, fs=fs, return_onesided=False, nperseg=256, noverlap=128)
f = np.fft.fftshift(f)+ f_center
f_mhz       = f/mhz
Z           = np.fft.fftshift(Z, axes=0)
Z_db        = np.log10(np.abs(Z))

## store stft in dataarray 
stft_da = xr.DataArray(
    data=Z_db,
    dims=["f_mhz", "time"],
    coords={"f_mhz": f_mhz, "time": times},
    name="stft",
    attrs={"sampling_rate_hz": fs, "units": "V/rtHz"},
)
## view  
%config InlineBackend.figure_format = 'retina'
stft_da.plot(size=2, aspect=8)