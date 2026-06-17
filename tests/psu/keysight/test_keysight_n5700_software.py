"""Software tests for the Keysight N5700 compatibility driver."""

from instro.psu.drivers import KeysightN5700, TDKLambdaGenesys


def test_keysight_n5700_instantiates_as_tdk_genesys_type() -> None:
    keysight_n5700 = KeysightN5700("TCPIP0::keysight-n5700::INSTR")

    assert isinstance(keysight_n5700, TDKLambdaGenesys)
