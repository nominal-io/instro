"""Keysight N5700 compatibility driver."""

from instro.psu.drivers.tdk_lambda_genesys import TDKLambdaGenesys


class KeysightN5700(TDKLambdaGenesys):
    """Keysight N5700-series PSU via the TDK Lambda Genesys SCPI interface."""

    FRIENDLY_NAME = "Keysight N5700-series PSU using the TDK Lambda Genesys SCPI interface"
