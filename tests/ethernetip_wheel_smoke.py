# Used by the `just eip-wheel-smoke-test` recipe.

from instro.unstable._ethernetip import EtherNetIpSession, PlcKind, StructuredValue


def main() -> None:
    assert EtherNetIpSession.__name__ == "EtherNetIpSession"
    assert PlcKind.__name__ == "PlcKind"
    assert StructuredValue.__name__ == "StructuredValue"
    print("PASS: local EtherNet/IP native module imports successfully")


if __name__ == "__main__":
    main()
