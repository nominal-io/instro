---
myst:
  html_meta:
    description: "instro Python example: Continuous batch write to all NI 9263 AO channels. Change the value of CONTINUE_ON_FAILED_WRITE and unplug cDAQ to force a failed write. Writes should continue to be attempted when CONTINUE_ON_FAILED_WRITE = True. The whole script should error out when CONTINUE_ON_FAILED_WRITE = False."
---

# Continuous batch write to all NI 9263 AO channels

```{literalinclude} /../examples/daq/daq_write_batch_continuous_ni.py
:caption: daq_write_batch_continuous_ni.py
:language: python
```

Source: [`examples/daq/daq_write_batch_continuous_ni.py`](https://github.com/nominal-io/instro/blob/main/examples/daq/daq_write_batch_continuous_ni.py)
