---
myst:
  html_meta:
    description: "instro Python example: EtherNet/IP feature showcase. Demonstrates config-driven EtherNet/IP features against a PLC: - Connection separation (connection passed to constructor, not in config) - Optional backplane route path from environment configuration - Timing config for background polling - Typed scalar tags (bool, integers, floats) - Write limits (write_min / write_max) - Background daemon polling via get_channel()"
---

# EtherNet/IP feature showcase

```{literalinclude} /../examples/ethernetip/ethernetip_example.py
:caption: ethernetip_example.py
:language: python
```

Source: [`examples/ethernetip/ethernetip_example.py`](https://github.com/nominal-io/instro/blob/main/examples/ethernetip/ethernetip_example.py)
