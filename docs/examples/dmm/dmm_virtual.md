---
myst:
  html_meta:
    description: "instro Python example: Read a virtual DMM, with no hardware or server. VirtualDMM simulates readings in-process, so this runs as-is. Swap in a real driver (Agilent34401A, Keysight34461A, ...) and the rest is unchanged."
---

# Read a virtual DMM, with no hardware or server

```{literalinclude} /../examples/dmm/dmm_virtual.py
:caption: dmm_virtual.py
:language: python
```

Source: [`examples/dmm/dmm_virtual.py`](https://github.com/nominal-io/instro/blob/main/examples/dmm/dmm_virtual.py)
