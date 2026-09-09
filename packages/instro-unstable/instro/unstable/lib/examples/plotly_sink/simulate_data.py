import subprocess
from time import sleep

from instro.dmm import InstroDMM, MeasurementFunction
from instro.dmm.drivers import SimulatedDMM
from instro.lib.publishers import FilePublisher

# Pass your terminal command as a list of strings
cmd = ["python", "-m", "instro.dmm.scpi_sim_server", "--dc-voltage", "1", "--dc-current", "1", "--ac-current", "1.02"]
server = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

filepub = FilePublisher(format="jsonl", directory="/tmp/instro/", custom_file_name="meas")
filepub.file_path.write_text("")  # clear file
sleep(1)

with InstroDMM(name="bench_dmm", driver=SimulatedDMM("TCPIP0::127.0.0.1::5026::SOCKET"), publishers=[filepub]) as dmm:
    for k in range(1000):
        dmm.set_measurement_function(MeasurementFunction.DC_VOLTAGE)
        dmm.read()
        sleep(0.1)
        if k > 80:
            dmm.set_measurement_function(MeasurementFunction.DC_CURRENT)
            dmm.read()

        if k > 120:
            dmm.set_measurement_function(MeasurementFunction.AC_CURRENT)
            dmm.read()
