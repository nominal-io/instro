# /// script
# requires-python = ">=3.10"
# dependencies = ["pywin32>=306"]
# ///
"""Experiment: print live channel data from a running DewesoftX instance over DCOM."""

# Run with: uv run dewesoftx.py
import time
from datetime import timezone

import win32com.client

POLL_FREQ_HZ = 2

# 1. Connect — DewesoftX is not in the ROT, so Dispatch attaches to a running instance or starts one
dw = win32com.client.Dispatch("Dewesoft.App")
dw.Init()

# 2. List the used channels
channels = dw.Data.UsedChannels
chans = [ch for i in range(channels.Count) if (ch := channels.Item(i)).DBBufSize > 0]
print(f"Sample rate: {dw.Data.SampleRate} Hz")
for ch in chans:
    print(f"  {ch.Name} [{ch.Unit_}]")

# 3. Poll each ring buffer; print every new sample as (value, unix_timestamp), oldest unread first
dt = [ch.SRDiv / dw.Data.SampleRate for ch in chans]  # sync channels: implicit time axis, index * dt
t0 = dw.Data.StartStoreTimeUTC.replace(tzinfo=timezone.utc).timestamp()  # OLE zero date when not storing
print(f"Store start: {dw.Data.StartStoreTimeUTC} UTC")
buf = [ch.DBBufSize for ch in chans]
pos = [ch.DBPos for ch in chans]
total = list(pos)  # DBPos at attach = samples since buffer start, valid until the first wrap
while True:
    time.sleep(1 / POLL_FREQ_HZ)
    for i, ch in enumerate(chans):
        new = (ch.DBPos - pos[i]) % buf[i]
        if new == 0:
            continue
        head = min(new, buf[i] - pos[i])  # split the bulk read in two where the ring buffer wraps
        vals = ch.GetScaledDataEx(pos[i], head)
        if new > head:
            vals += ch.GetScaledDataEx(0, new - head)
        timestamps = [t0 + (total[i] + k) * dt[i] for k in range(new)]  # derived: sync channels have no TS buffer
        chunk = ", ".join(f"({v:.6g}, {t:.6f})" for v, t in zip(vals, timestamps))
        print(f"{ch.Name}: [{chunk}]")
        total[i] += new
        pos[i] = (pos[i] + new) % buf[i]
