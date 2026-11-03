---
myst:
  html_meta:
    description: "instro Python example: ModbusDevice feature showcase. Demonstrates all config features against the sim server: - Connection separation (connection passed to constructor, not in config) - Timing config (poll_interval + write_delay_ms) - Read groups (batched register reads) - Write limits (write_min / write_max) - Write value maps (string-to-number mapping) - Scaled registers (linear gain + offset) - Bitmap extraction (individual bit channels) - All register types (holding, input, coil, discrete)"
---

# ModbusDevice feature showcase

```{literalinclude} /../examples/modbus/modbus_example.py
:caption: modbus_example.py
:language: python
```

Source: [`examples/modbus/modbus_example.py`](https://github.com/nominal-io/instro/blob/main/examples/modbus/modbus_example.py)
