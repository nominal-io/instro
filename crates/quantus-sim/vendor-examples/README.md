# Vendor referee scripts

Unmodified copies of Mecalc's official Python examples
(https://github.com/Mecalc/ExamplesPython, MIT license). These are the
**independent referee** for the simulator (PLAN.md testing strategy #2): they
were written by the vendor, so they encode the vendor's reading of the
protocol. Running them against `quantus-sim` validates sim fidelity without
sharing any code with our implementations.

Run against a local sim (only the address may be patched — never the logic):

```sh
cargo run -p quantus-sim -- fixtures/sim/microq_demo.toml
# in the scripts, set ipAddress to 127.0.0.1 (and the port if not 8080)
python tools/vendor-examples/ReadItemList.py
python tools/vendor-examples/ConfigureICS42.py
```

Verified passing against the Phase 1 REST plane (ReadItemList, ConfigureICS42).
StreamData.py becomes runnable when the Phase 3 stream plane lands.
