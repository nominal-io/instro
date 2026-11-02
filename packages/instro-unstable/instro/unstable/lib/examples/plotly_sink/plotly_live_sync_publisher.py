from instro.dmm import InstroDMM, MeasurementFunction
from instro.dmm.drivers import SimulatedDMM
from instro.unstable.lib.publishers import PlotlyLivePublisher
from time import sleep 
import subprocess

# Pass your terminal command as a list of strings
cmd = ["python", "-m", "instro.dmm.scpi_sim_server", "--dc-voltage", "1", "--dc-current", "1","--ac-current","1.02"]
server = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

pub = PlotlyLivePublisher()
dmm =  InstroDMM(
    name="bench_dmm",
    driver=SimulatedDMM("TCPIP0::127.0.0.1::5026::SOCKET"),
    publishers=[pub])

pub.display() # displays the output

## then in new cell   you can generate data 
dmm.open()
for k in range(1000):
    dmm.set_measurement_function(MeasurementFunction.DC_VOLTAGE)
    dmm.read()
    sleep(.1)
    if k>80 :
        dmm.set_measurement_function(MeasurementFunction.DC_CURRENT)
        dmm.read()
    
    if k>120 :
        dmm.set_measurement_function(MeasurementFunction.AC_CURRENT)
        dmm.read()     