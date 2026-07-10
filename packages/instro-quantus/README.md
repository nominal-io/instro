# instro-quantus

Mecalc QuantusSeries DAQ mainframe support for instro: a config-driven
`QuantusDevice` in the spirit of `EtherNetIPDevice`, backed by the Rust
`quantus` wheel (REST configuration + binary stream engine).

**Status: draft.** Blocked on publishing the `quantus` wheel; see the quantus
repo's PLAN.md Phase 5. Not yet registered in the uv workspace, README device
table, or docs site — those land with the real PR.

```python
from instro.quantus import QuantusDevice

daq = QuantusDevice(
    config="rack.json",          # or a dict; see quantus repo fixtures/rack/
    name="quantus",
    dbc={"vehicle_bus": "vehicle.dbc"},   # optional: decode CAN by alias
    publishers=[...],
)
daq.open()
report = daq.reconcile()          # writes settings, applies once, snaps rates
daq.start()                       # stream -> Measurements on {name}.{alias}
...
daq.close()
```

Analog channels publish sampled batches; tacho channels publish RPM computed
from edge intervals; CAN channels publish DBC-decoded signals as
`{name}.{alias}.{signal}` (frames with unknown ids are counted on
`{name}.{alias}.unknown_frames`). Stream health (gaps, buffer level) publishes
on `{name}.stream`.
