# /// script
# requires-python = ">=3.10"
# dependencies = ["pywin32>=306"]
# ///
"""Experiment: print live channel data from a running DewesoftX instance over DCOM."""

# Run with: uv run dewesoftx.py
import time

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

# 3. Poll each ring buffer; print every new sample as (value, timestamp), oldest unread first
dt = [ch.SRDiv / dw.Data.SampleRate for ch in chans]  # sync channels: implicit time axis, index * dt, t=0 at attach
buf = [ch.DBBufSize for ch in chans]
pos = [ch.DBPos for ch in chans]
total = [0] * len(chans)
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
        chunk = ", ".join(f"({v:.6g}, {(total[i] + k) * dt[i]:.6f})" for k, v in enumerate(vals))
        print(f"{ch.Name}: [{chunk}]")
        total[i] += new
        pos[i] = (pos[i] + new) % buf[i]
