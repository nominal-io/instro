---
myst:
  html_meta:
    description: "Protocol-based instrument communication for industrial protocols"
---

# Protocols

{.lead}
Protocol-based instrument communication for industrial protocols

:::{note}
The Nominal Instrumentation Library is in alpha testing. To request access, open the [Support Portal](https://portal.usepylon.com/nominal).
:::

Protocol clients complement instrument drivers: drivers expose purpose-built APIs for specific instrument categories, while protocol clients expose protocol-level operations. Modbus and EtherNet/IP use JSON config files to describe register or tag interfaces. The OPC UA client provides direct browsing, typed attribute reads, and subscription iteration.

## When to use protocol clients

Use a config-driven Modbus or EtherNet/IP client when a config file can name the addresses, tags, and polling interval. The config becomes the portable definition of how Nominal reads the device; teams can version it with the test setup and change polling without editing Python. Use the OPC UA client to browse an address space and read or monitor selected attributes directly.

Use protocol clients for industrial devices that do not use SCPI or VISA transports, such as PLCs, sensors, motor controllers, and environmental monitors. The Modbus and EtherNet/IP clients send data through the same [publisher system](/library/publishers.md) and background worker infrastructure as instrument drivers.

## Supported protocols

- **[Modbus](/library/protocols/modbus.md)**: Modbus TCP and RTU client with config-driven register access, automatic polling, and publisher support.
- **[EtherNet/IP](/library/protocols/ethernetip.md)**: Allen-Bradley PLC tag access with config-driven scalar reads, writes, and batched background collection.
- **[OPC UA](/library/protocols/opcua.md)**: Direct synchronous client with browsing from RootFolder, typed attributes, and iterable subscriptions, provided by `instro-opcua`.
