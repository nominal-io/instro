from instro.sig_gen.drivers.rigol_dg1022 import RigolDG1022
from instro.lib.transports.visa import TerminatorConfig, VisaConfig
from instro.sig_gen.types import Channel, WaveformType

driver = RigolDG1022(
    VisaConfig(
        visa_resource="TCPIP0::127.0.0.1::5026::SOCKET",
        terminator=TerminatorConfig(write="\n", read="\n"),
    )
)
driver.open()
driver.set_frequency(Channel.CH1, 1000)
driver.set_amplitude(Channel.CH1, 5)
driver.set_offset(Channel.CH1, 0)