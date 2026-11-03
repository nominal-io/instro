---
myst:
  html_meta:
    description: "instro Python example: One hardware-timed and one software-timed acquisition with two InstroDAQ instances. Hardware and software timing are mutually exclusive per instance. Software timing paces the daemon loop at 1 / sample_rate and timestamps each sample when the read returns, so the configured rate is a ceiling, not a guarantee: a conversion slower than the loop period simply slows the loop."
---

# One hardware-timed and one software-timed acquisition with two InstroDAQ instances

```{literalinclude} /../examples/daq/daq_hw_and_sw_timed_ni.py
:caption: daq_hw_and_sw_timed_ni.py
:language: python
```

Source: [`examples/daq/daq_hw_and_sw_timed_ni.py`](https://github.com/nominal-io/instro/blob/main/examples/daq/daq_hw_and_sw_timed_ni.py)
