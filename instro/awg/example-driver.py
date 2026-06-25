"""
Copyright (c) 2025 ABB Stotz Kontakt GmbH <daniel.koepping@de.abb.com>

SPDX-License-Identifier: MIT
"""

import pyvisa
import time
import logging

class RigolDG:
    """
    Module to control Rigol DGx Function Generators.
    Tested with DG1022, DG1062Z, DG4102
    Supports USB or LAN connections.
    """

    # Define the number of decimal places for each parameter
    FREQ_DECIMALS = 6
    AMPL_DECIMALS = 5
    OFFSET_DECIMALS = 5
    PHASE_DECIMALS = 4

    _resource_manager = None

    def __init__(
        self,
        resource_string=None,
        ip_address=None,
        timeout=5000,
        verbose=False,
        max_retries=3,
        retry_delay=1,
    ):
        """
        Initialize the connection to the Rigol DGx.

        :param resource_string: VISA resource string. If None, attempts to find the device automatically.
        :param ip_address: IP address of the device for network connection.
        :param timeout: Timeout for VISA operations in milliseconds.
        :param verbose: If True, enables verbose logging.
        :param max_retries: Maximum number of connection attempts.
        :param retry_delay: Delay between retries in seconds.
        """
        self.inst = None
        self.verbose = verbose

        # Set up logging
        self.logger = logging.getLogger(__name__)
        if not self.logger.handlers:
            logging.basicConfig(level=logging.INFO if verbose else logging.WARNING)

        try:
            if RigolDG._resource_manager is None:
                RigolDG._resource_manager = pyvisa.ResourceManager()

            self.rm = RigolDG._resource_manager

            if resource_string is None:
                if ip_address is not None:
                    resource_string = f"TCPIP0::{ip_address}::INSTR"
                    self.logger.info(f"Using network resource string: {resource_string}")
                else:
                    resource_string = self._find_device()

            for attempt in range(1, max_retries + 1):
                try:
                    self.inst = self.rm.open_resource(resource_string)
                    self.logger.info(f"Successfully opened resource: {resource_string}")
                    break  # Successful connection
                except pyvisa.errors.VisaIOError as e:
                    self.logger.warning(f"Attempt {attempt} failed: {str(e)}")
                    if attempt < max_retries:
                        time.sleep(retry_delay)
                    else:
                        raise
            else:
                raise Exception(f"Failed to connect to device after {max_retries} attempts")

            # Verify device identity
            # Decrease timeout for this operation
            self.inst.timeout = 100
            idn = self._query("*IDN?")
            self.inst.timeout = timeout
            if "Rigol Technologies,DG" not in idn:
                raise ValueError(f"Connected device is not a Rigol DG: {idn}")

            self.logger.info(f"Successfully connected to Rigol DG: {idn}")

        except Exception as e:
            self.logger.error(f"Failed to initialize Rigol DGx: {str(e)}")
            self.close()
            raise

    def __enter__(self):
        return self


    def close(self):
        """Safely close the connection to the device."""
        if self.inst:
            try:
                self.inst.close()
                self.logger.info("Closed connection to the instrument")
            except Exception as e:
                self.logger.warning(f"Error closing instrument connection: {str(e)}")
            finally:
                self.inst = None

    def _send_command(self, command, check_errors=False, delay=0.01, max_retries=3):
        """
        Send a command to the device and optionally check for errors with retry logic.

        :param command: The command string to send.
        :param check_errors: Whether to check for errors after sending the command.
        :param delay: Delay in seconds after sending the command (default: 0.01 seconds).
        :param max_retries: Maximum number of retry attempts.
        :raises IOError: If there's a communication error.
        :raises ValueError: If the device returns an error.
        """
        if self.inst is None:
            self._reconnect()

        for attempt in range(max_retries):
            try:
                self.logger.debug(f"Sending command: {command}")
                self.inst.write(command)
                time.sleep(delay)  # Introduce a delay to avoid overloading communication
                if check_errors:
                    error = self._query("SYST:ERR?")
                    if not error.startswith("0,"):
                        raise ValueError(f"Error after command '{command}': {error}")
                self.logger.debug("Command sent successfully")
                return
            except pyvisa.errors.VisaIOError as e:
                self.logger.warning(f"Attempt {attempt+1}: Communication error while sending command '{command}': {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(delay * 10)  # Longer delay for retry
                    self._reconnect()
                else:
                    self.logger.error(f"Failed after {max_retries} attempts: {str(e)}")
                    raise
            except Exception as e:
                self.logger.error(f"Unexpected error while sending command '{command}': {str(e)}")
                raise

    def _query(self, query, max_retries=3, retry_delay=0.5):
        """
        Send a query to the device and return the result with retry logic.

        :param query: The query string to send.
        :param max_retries: Maximum number of retry attempts.
        :param retry_delay: Delay between retries in seconds.
        :return: The response from the device.
        """
        if self.inst is None:
            self._reconnect()

        for attempt in range(max_retries):
            try:
                self.logger.debug(f"Sending query: {query}")
                response = self.inst.query(query).strip()
                self.logger.debug(f"Received response: {response}")
                return response
            except pyvisa.errors.VisaIOError as e:
                self.logger.warning(f"Attempt {attempt+1}: Communication error while querying '{query}': {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    self._reconnect()
                else:
                    self.logger.error(f"Failed after {max_retries} attempts: {str(e)}")
                    raise
            except Exception as e:
                self.logger.error(f"Unexpected error while querying '{query}': {str(e)}")
                raise

    def _reconnect(self, max_retries=3):
        """
        Attempt to reconnect to the device if the connection was lost.

        :param max_retries: Maximum number of reconnection attempts.
        """
        self.logger.info("Attempting to reconnect to the device...")

        # Close the current connection if it exists
        if self.inst:
            try:
                self.inst.close()
            except:
                pass
            self.inst = None

        # Get the current resource string if available
        current_resource = getattr(self, 'current_resource', None)

        # Try to reconnect
        for attempt in range(max_retries):
            try:
                if current_resource:
                    self.inst = self.rm.open_resource(current_resource)
                    self.logger.info(f"Successfully reconnected to resource: {current_resource}")

                    # Verify device identity
                    try:
                        orig_timeout = self.inst.timeout
                        self.inst.timeout = 1000
                        idn = self.inst.query("*IDN?").strip()
                        self.inst.timeout = orig_timeout

                        if "Rigol Technologies,DG" not in idn:
                            raise ValueError(f"Connected device is not a Rigol DGx: {idn}")

                        return  # Successful reconnection
                    except Exception:
                        # If identity verification fails, try to find the device again
                        self.inst.close()
                        self.inst = None
                        raise

                # If we don't have a resource string or verification failed, try to find the device
                resources = self.rm.list_resources()
                for res in resources:
                    if 'USB' in res or 'ASRL' in res or ('TCPIP' in res and 'DG' in res):
                        try:
                            self.logger.info(f"Trying resource: {res}")
                            self.inst = self.rm.open_resource(res)
                            self.inst.timeout = 1000
                            idn = self.inst.query("*IDN?").strip()

                            if "Rigol Technologies,DG" in idn:
                                self.logger.info(f"Found Rigol DGx at: {res}")
                                self.current_resource = res
                                return  # Successfully found and connected

                            # Not the right device, close and continue
                            self.inst.close()
                            self.inst = None
                        except:
                            # If there's an error trying this resource, close and continue
                            if self.inst:
                                try:
                                    self.inst.close()
                                except:
                                    pass
                                self.inst = None

                # If we reach here, we couldn't find the device
                self.logger.warning(f"Attempt {attempt+1}: Could not find Rigol DGx")
                time.sleep(1)

            except Exception as e:
                self.logger.warning(f"Reconnection attempt {attempt+1} failed: {str(e)}")
                time.sleep(1)

        # If we reach here, all reconnection attempts failed
        raise IOError("Failed to reconnect to Rigol DGx")

    def _find_device(self):
        """
        Attempt to find the Rigol DG device automatically.
        """
        resources = self.rm.list_resources()
        for res in resources:
            if 'DG' in res:
                self.logger.info(f"Found potential Rigol device: {res}")
                return res
        raise ValueError("Could not find Rigol DGx device")

    def _format_value(self, value, decimals):
        """
        Format a float value to the specified number of decimal places.

        :param value: The float value to format.
        :param decimals: Number of decimal places.
        :return: Formatted string.
        """
        format_string = f"{{:.{decimals}f}}"
        formatted = format_string.format(value)
        self.logger.debug(f"Formatted value: {formatted} (Original: {value}, Decimals: {decimals})")
        return formatted

    def _round_value(self, value, decimals):
        """
        Round a float value to the specified number of decimal places.

        :param value: The float value to round.
        :param decimals: Number of decimal places.
        :return: Rounded float.
        """
        rounded = round(value, decimals)
        self.logger.debug(f"Rounded value: {rounded} (Original: {value}, Decimals: {decimals})")
        return rounded

    def set_voltage_unit(self, channel, unit):
        """
        Set the voltage unit for a channel.

        :param channel: The channel number (1 or 2).
        :param unit: The voltage unit, one of 'VPP', 'VRMS', or 'DBM'.
        """
        if unit.upper() not in ['VPP', 'VRMS', 'DBM']:
            raise ValueError("Unit must be 'VPP', 'VRMS', or 'DBM'.")
        command = f"SOUR{channel}:VOLT:UNIT {unit.upper()}"
        self._send_command(command)
        self.logger.info(f"Set voltage unit for channel {channel} to {unit.upper()}")

    def set_waveform(self, channel, waveform):
        """
        Set the waveform type for a channel without altering frequency, amplitude, or offset.

        :param channel: The channel number (1 or 2).
        :param waveform: The waveform type (e.g., "SIN", "SQU", "RAMP", "PULS", "NOIS", "DC", "USER").
        """
        command = f":SOUR{channel}:FUNC {waveform}"
        self._send_command(command)
        self.logger.info(f"Set waveform type for channel {channel}: {waveform}")

    def set_output(self, channel, state):
        """
        Set the output state for a channel.

        :param channel: The channel number (1 or 2).
        :param state: Boolean indicating whether to turn the output on (True) or off (False).
        """
        command = f":OUTP{channel} {'ON' if state else 'OFF'}"
        self._send_command(command)
        self.logger.info(f"Set output for channel {channel} to {'ON' if state else 'OFF'}")

    def set_frequency(self, channel, frequency):
        """
        Set the frequency for a channel.

        :param channel: The channel number (1 or 2).
        :param frequency: The frequency in Hz.
        """
        formatted_freq = self._format_value(frequency, self.FREQ_DECIMALS)
        command = f":SOUR{channel}:FREQ {formatted_freq}"
        self._send_command(command)
        self.logger.info(f"Set frequency for channel {channel} to {formatted_freq} Hz")

    def set_amplitude(self, channel, amplitude):
        """
        Set the amplitude for a channel.

        :param channel: The channel number (1 or 2).
        :param amplitude: The amplitude in Vpp.
        """
        formatted_ampl = self._format_value(amplitude, self.AMPL_DECIMALS)
        command = f":SOUR{channel}:VOLT {formatted_ampl}"
        self._send_command(command)
        self.logger.info(f"Set amplitude for channel {channel} to {formatted_ampl} Vpp")

    def set_offset(self, channel, offset):
        """
        Set the DC offset for a channel.

        :param channel: The channel number (1 or 2).
        :param offset: The DC offset in V.
        """
        formatted_offset = self._format_value(offset, self.OFFSET_DECIMALS)
        command = f":SOUR{channel}:VOLT:OFFS {formatted_offset}"
        self._send_command(command)
        self.logger.info(f"Set DC offset for channel {channel} to {formatted_offset} V")

    def set_phase(self, channel, phase):
        """
        Set the phase for a channel.

        :param channel: The channel number (1 or 2).
        :param phase: The phase in degrees.
        """
        formatted_phase = self._format_value(phase, self.PHASE_DECIMALS)
        command = f":SOUR{channel}:PHAS {formatted_phase}"
        self._send_command(command)
        self.logger.info(f"Set phase for channel {channel} to {formatted_phase} degrees")

    def set_impedance(self, channel=1, impedance=50):
        """
        Set the output impedance for the specified channel.

        :param channel: Channel number (1 or 2). Default is 1.
        :param impedance: Impedance in Ohms (1 to 10000). Sets to MIN or MAX if out of range.
        """
        if impedance < 1:
            impedance_cmd = "MIN"
        elif impedance > 10000:
            impedance_cmd = "MAX"
        else:
            impedance_cmd = str(int(impedance))
        command = f":OUTP{channel}:IMP {impedance_cmd}"
        self._send_command(command)
        self.logger.info(f"Set output impedance for channel {channel} to {impedance_cmd}")

    def get_impedance(self, channel=1):
        """
        Get the output impedance for the specified channel.

        :param channel: Channel number (1 or 2). Default is 1.
        :return: Impedance in Ohms. Returns 1 for MIN, 10000 for MAX, or the integer value.
        """
        response = self._query(f":OUTP{channel}:IMP?")
        if response.upper() == "MIN":
            return 1
        elif response.upper() == "MAX":
            return 10000
        else:
            return int(float(response))

    def set_load(self, channel=1, load=50):
        """
        Set the load for the specified channel.

        :param channel: Channel number (1 or 2). Default is 1.
        :param load: Load in Ohms (1 to 10000). Sets to MIN or MAX if out of range.
        """
        if load < 1:
            load_cmd = "MIN"
        elif load > 10000:
            load_cmd = "MAX"
        else:
            load_cmd = str(int(load))
        command = f":OUTP{channel}:LOAD {load_cmd}"
        self._send_command(command)
        self.logger.info(f"Set load for channel {channel} to {load_cmd}")

    def get_load(self, channel=1):
        """
        Get the load for the specified channel.

        :param channel: Channel number (1 or 2). Default is 1.
        :return: Load in Ohms. Returns 1 for MIN, 10000 for MAX, or the integer value.
        """
        response = self._query(f":OUTP{channel}:LOAD?")
        if response.upper() == "MIN":
            return 1
        elif response.upper() == "MAX":
            return 10000
        else:
            return int(float(response))

    def get_waveform(self, channel):
        """
        Get the current waveform type for a channel.

        :param channel: The channel number (1 or 2).
        :return: The waveform type as a string.
        """
        waveform = self._query(f":SOUR{channel}:FUNC?")
        self.logger.info(f"Current waveform for channel {channel}: {waveform}")
        return waveform

    def get_frequency(self, channel):
        """
        Get the current frequency for a channel.

        :param channel: The channel number (1 or 2).
        :return: The frequency in Hz.
        """
        frequency = float(self._query(f":SOUR{channel}:FREQ?"))
        rounded_freq = self._round_value(frequency, self.FREQ_DECIMALS)
        self.logger.info(f"Current frequency for channel {channel}: {rounded_freq} Hz")
        return rounded_freq

    def get_amplitude(self, channel):
        """
        Get the current amplitude for a channel.

        :param channel: The channel number (1 or 2).
        :return: The amplitude in Vpp.
        """
        amplitude = float(self._query(f":SOUR{channel}:VOLT?"))
        rounded_ampl = self._round_value(amplitude, self.AMPL_DECIMALS)
        self.logger.info(f"Current amplitude for channel {channel}: {rounded_ampl} Vpp")
        return rounded_ampl

    def get_offset(self, channel):
        """
        Get the current DC offset for a channel.

        :param channel: The channel number (1 or 2).
        :return: The DC offset in V.
        """
        offset = float(self._query(f":SOUR{channel}:VOLT:OFFS?"))
        rounded_offset = self._round_value(offset, self.OFFSET_DECIMALS)
        self.logger.info(f"Current DC offset for channel {channel}: {rounded_offset} V")
        return rounded_offset

    def get_phase(self, channel):
        """
        Get the current phase for a channel.

        :param channel: The channel number (1 or 2).
        :return: The phase in degrees.
        """
        phase = float(self._query(f":SOUR{channel}:PHAS?"))
        rounded_phase = self._round_value(phase, self.PHASE_DECIMALS)
        self.logger.info(f"Current phase for channel {channel}: {rounded_phase} degrees")
        return rounded_phase

    def get_output_state(self, channel):
        """
        Get the current output state for a channel.

        :param channel: The channel number (1 or 2).
        :return: Boolean indicating whether the output is on (True) or off (False).
        """
        state_str = self._query(f":OUTP{channel}?").strip()
        state = state_str.upper() == "ON"
        self.logger.info(f"Current output state for channel {channel}: {'ON' if state else 'OFF'}")
        return state

    def get_all_parameters(self, channel):
        """
        Get all current parameters for a channel.

        :param channel: The channel number (1 or 2).
        :return: A dictionary containing all parameter values.
        """
        params = {
            'waveform': self.get_waveform(channel),
            'frequency': self.get_frequency(channel),
            'amplitude': self.get_amplitude(channel),
            'offset': self.get_offset(channel),
            'phase': self.get_phase(channel),
            'output_state': self.get_output_state(channel)
        }
        self.logger.info(f"All parameters for channel {channel}: {params}")
        return params

    def reset(self):
        """
        Reset the device to its default state.
        """
        self._send_command("*RST")
        self.logger.info("Device reset to default state")

    def self_test(self):
        """
        Perform a self-test of the device.

        :return: The result of the self-test.
        """
        result = self._query("*TST?")
        self.logger.info(f"Self-test result: {result}")
        return result

    def get_error(self):
        """
        Get the latest error message from the device.

        :return: The error message as a string.
        """
        error = self._query("SYST:ERR?")
        self.logger.info(f"Latest error: {error}")
        return error

    def clear_error_queue(self):
        """
        Clear the error queue of the device.
        """
        while True:
            error = self._query("SYST:ERR?")
            if error.startswith("0,"):
                break
        self.logger.info("Error queue cleared")

    def set_coupling_state(self, state):
        """
        Enable or disable coupling function.

        :param state: Boolean, True to enable coupling, False to disable.
        """
        command = f":COUP:STAT {'ON' if state else 'OFF'}"
        self._send_command(command)
        self.logger.info(f"Set coupling state to {'ON' if state else 'OFF'}")

    def get_coupling_state(self):
        """
        Query the coupling state.

        :return: Boolean, True if coupling is enabled, False if disabled.
        """
        response = self._query(":COUP:STAT?")
        state = response.strip().upper() == "ON"
        self.logger.info(f"Coupling state is {'ON' if state else 'OFF'}")
        return state

    def set_coupling_phase_state(self, state):
        """
        Enable or disable phase coupling.

        :param state: Boolean, True to enable coupling, False to disable.
        """
        command = f":COUP:PHAS {'ON' if state else 'OFF'}"
        self._send_command(command)
        self.logger.info(f"Set coupling state to {'ON' if state else 'OFF'}")

    def get_coupling_phase_state(self):
        """
        Query the phase coupling state.

        :return: Boolean, True if coupling is enabled, False if disabled.
        """
        response = self._query(":COUP:PHAS?")
        state = response.strip().upper() == "ON"
        self.logger.info(f"Coupling state is {'ON' if state else 'OFF'}")
        return state

    def set_coupling_phase_deviation(self, value):
        """
        Set the phase deviation of channel coupling.

        :param value: Phase deviation in degrees
        """
        if not 0 <= value <= 360:
            raise ValueError("Phase deviation must be between -180 and 180 degrees.")
        command = f":COUP:PHAS:DEV {value}"
        self._send_command(command)
        self.logger.info(f"Set coupling phase deviation to {value} degrees")

    def get_coupling_phase_deviation(self):
        """
        Query the phase deviation.

        :return: Phase deviation in degrees.
        """
        response = self._query(":COUP:PHAS:DEV?")
        value = float(response.strip())
        self.logger.info(f"Coupling phase deviation is {value} degrees")
        return value

    def set_coupling_frequency_deviation(self, value):
        """
        Set the frequency deviation of channel coupling.

        :param value: Frequency deviation in Hz
        """
        command = f":COUP:FREQ:DEV {value}"
        self._send_command(command)
        self.logger.info(f"Set coupling frequency deviation to {value} Hz")

    def get_coupling_frequency_deviation(self):
        """
        Query the frequency deviation.

        :return: Frequency deviation in Hz.
        """
        response = self._query(":COUP:FREQ:DEV?")
        value = float(response.strip())
        self.logger.info(f"Coupling frequency deviation is {value} Hz")
        return value


    def set_burst_mode(self, channel, mode):
        """
        Set the burst mode for the specified channel.

        :param channel: The channel number (1 or 2).
        :param mode: String 'TRIG' for triggered or 'GAT' for gated.
        """
        if mode.upper() not in ['TRIG', 'GAT']:
            raise ValueError("Mode must be 'TRIG' or 'GAT'.")
        command = f":SOUR{channel}:BURS:MODE {mode.upper()}"
        self._send_command(command)
        self.logger.info(f"Set burst mode to {mode.upper()} for channel {channel}")

    def get_burst_mode(self, channel):
        """
        Query the burst mode.

        :param channel: The channel number (1 or 2)
        :return: String 'TRIG' or 'GAT'
        """
        response = self._query(f":SOUR{channel}:BURS:MODE?")
        mode = response.strip().upper()
        self.logger.info(f"Burst mode for channel {channel} is {mode}")
        return mode

    def set_burst_ncycles(self, channel, cycles):
        """
        Set the number of cycles for burst mode.

        :param channel: The channel number (1 or 2)
        :param cycles: Number of cycles (integer between 1 and 50000)
        """
        try:
            cycles = int(cycles)
            if not 1 <= cycles <= 50000:
                raise ValueError
        except:
            raise ValueError("Cycles must be an integer between 1 and 50000.")
        command = f":SOUR{channel}:BURS:NCYC {cycles}"
        self._send_command(command)
        self.logger.info(f"Set burst cycles to {cycles} for channel {channel}")

    def get_burst_ncycles(self, channel):
        """
        Query the number of cycles for burst mode.

        :param channel: The channel number (1 or 2)
        :return: Number of cycles (integer)
        """
        response = self._query(f":SOUR{channel}:BURS:NCYC?")
        cycles = int(float(response.strip()))
        self.logger.info(f"Burst cycles for channel {channel} is {cycles}")
        return cycles

    def set_burst_internal_period(self, channel, period):
        """
        Set the burst period in internal trigger mode.

        :param channel: The channel number (1 or 2).
        :param period: Burst period in seconds (float between 1e-6 and 500)
        """
        if not 1e-6 <= period <= 500:
            raise ValueError("Burst period must be between 1e-6 and 500 seconds.")
        command = f":SOUR{channel}:BURS:INT:PER {period}"
        self._send_command(command)
        self.logger.info(f"Set burst internal period to {period} seconds for channel {channel}")

    def get_burst_internal_period(self, channel):
        """
        Query the burst period in internal trigger mode.

        :param channel: The channel number (1 or 2)
        :return: Burst period in seconds.
        """
        response = self._query(f":SOUR{channel}:BURS:INT:PER?")
        period = float(response.strip())
        self.logger.info(f"Burst internal period for channel {channel} is {period} seconds")
        return period

    def set_burst_phase(self, channel, angle):
        """
        Set the initial phase of burst.

        :param channel: The channel number (1 or 2)
        :param angle: Phase angle in degrees (-360 to 360)
        """
        if not -360 <= angle <= 360:
            raise ValueError("Burst phase must be between -360 and 360 degrees.")
        command = f":SOUR{channel}:BURS:PHAS {angle}"
        self._send_command(command)
        self.logger.info(f"Set burst phase to {angle} degrees for channel {channel}")

    def get_burst_phase(self, channel):
        """
        Query the initial phase of burst.

        :param channel: The channel number (1 or 2).
        :return: Burst phase angle in degrees.
        """
        response = self._query(f":SOUR{channel}:BURS:PHAS?")
        angle = float(response.strip())
        self.logger.info(f"Burst phase for channel {channel} is {angle} degrees")
        return angle

    def set_burst_state(self, channel, state):
        """
        Enable or disable burst mode.

        :param channel: The channel number (1 or 2).
        :param state: Boolean, True to enable burst mode, False to disable.
        """
        command = f":SOUR{channel}:BURS:STAT {'ON' if state else 'OFF'}"
        self._send_command(command)
        self.logger.info(f"Set burst state to {'ON' if state else 'OFF'} for channel {channel}")

    def get_burst_state(self, channel):
        """
        Query the burst mode state.

        :param channel: The channel number (1 or 2).
        :return: Boolean, True if burst mode is enabled, False otherwise.
        """
        response = self._query(f":SOUR{channel}:BURS:STAT?")
        state = response.strip().upper() == "ON"
        self.logger.info(f"Burst state for channel {channel} is {'ON' if state else 'OFF'}")
        return state

    def set_burst_gate_polarity(self, channel, polarity):
        """
        Set the polarity of external gated signal.

        :param channel: The channel number (1 or 2)
        :param polarity: String 'NORM' or 'INV'
        """
        if polarity.upper() not in ['NORM', 'INV']:
            raise ValueError("Polarity must be 'NORM' or 'INV'.")
        command = f":SOUR{channel}:BURS:GATE:POL {polarity.upper()}"
        self._send_command(command)
        self.logger.info(f"Set burst gate polarity to {polarity.upper()} for channel {channel}")

    def get_burst_gate_polarity(self, channel):
        """
        Query the polarity of external gated signal.

        :param channel: The channel number (1 or 2)
        :return: String 'NORM' or 'INV'
        """
        response = self._query(f":SOUR{channel}:BURS:GATE:POL?")
        polarity = response.strip().upper()
        self.logger.info(f"Burst gate polarity for channel {channel} is {polarity}")
        return polarity

    def set_sweep_spacing(self, channel, spacing):
        """
        Select linear or logarithmic spacing for the sweep.

        :param channel: The channel number (1 or 2)
        :param spacing: String 'LIN' for linear or 'LOG' for logarithmic.
        """
        if spacing.upper() not in ['LIN', 'LOG']:
            raise ValueError("Spacing must be 'LIN' or 'LOG'.")
        command = f":SOUR{channel}:SWE:SPAC {spacing.upper()}"
        self._send_command(command)
        self.logger.info(f"Set sweep spacing to {spacing.upper()} for channel {channel}")

    def get_sweep_spacing(self, channel):
        """
        Query the current sweep spacing.

        :param channel: The channel number (1 or 2)
        :return: String 'LIN' or 'LOG'
        """
        response = self._query(f":SOUR{channel}:SWE:SPAC?")
        spacing = response.strip().upper()
        self.logger.info(f"Sweep spacing for channel {channel} is {spacing}")
        return spacing

    def set_sweep_time(self, channel, time):
        """
        Set the sweep time.

        :param channel: The channel number (1 or 2)
        :param time: Sweep time in seconds (float between 0.001 and 500)
        """
        if not 0.001 <= time <= 500:
            raise ValueError("Sweep time must be between 0.001 and 500 seconds.")
        command = f":SOUR{channel}:SWE:TIME {time}"
        self._send_command(command)
        self.logger.info(f"Set sweep time to {time} seconds for channel {channel}")

    def get_sweep_time(self, channel):
        """
        Query the sweep time.

        :param channel: The channel number (1 or 2)
        :return: Sweep time in seconds.
        """
        response = self._query(f":SOUR{channel}:SWE:TIME?")
        time = float(response.strip())
        self.logger.info(f"Sweep time for channel {channel} is {time} seconds")
        return time

    def set_sweep_state(self, channel, state):
        """
        Enable or disable sweep mode.

        :param channel: The channel number (1 or 2)
        :param state: Boolean, True to enable sweep mode, False to disable.
        """
        command = f":SOUR{channel}:SWE:STAT {'ON' if state else 'OFF'}"
        self._send_command(command)
        self.logger.info(f"Set sweep state to {'ON' if state else 'OFF'} for channel {channel}")

    def get_sweep_state(self, channel):
        """
        Query the sweep state.

        :param channel: The channel number (1 or 2)
        :return: Boolean, True if sweep mode is enabled, False otherwise.
        """
        response = self._query(f":SOUR{channel}:SWE:STAT?")
        state = response.strip().upper() == "ON"
        self.logger.info(f"Sweep state for channel {channel} is {'ON' if state else 'OFF'}")
        return state

    def set_sweep_start_frequency(self, channel, frequency):
        """
        Set the sweep start frequency for a channel.

        :param channel: The channel number (1 or 2).
        :param frequency: The start frequency in Hz.
        """
        formatted_freq = self._format_value(frequency, self.FREQ_DECIMALS)
        command = f":SOUR{channel}:FREQ:STAR {formatted_freq}"
        self._send_command(command)
        self.logger.info(f"Set sweep start frequency for channel {channel} to {formatted_freq} Hz")

    def get_sweep_start_frequency(self, channel):
        """
        Get the sweep start frequency for a channel.

        :param channel: The channel number (1 or 2).
        :return: The start frequency in Hz.
        """
        response = self._query(f":SOUR{channel}:FREQ:STAR?")
        frequency = float(response.strip())
        rounded_freq = self._round_value(frequency, self.FREQ_DECIMALS)
        self.logger.info(f"Sweep start frequency for channel {channel} is {rounded_freq} Hz")
        return rounded_freq

    def set_sweep_stop_frequency(self, channel, frequency):
        """
        Set the sweep stop frequency for a channel.

        :param channel: The channel number (1 or 2).
        :param frequency: The stop frequency in Hz.
        """
        formatted_freq = self._format_value(frequency, self.FREQ_DECIMALS)
        command = f":SOUR{channel}:FREQ:STOP {formatted_freq}"
        self._send_command(command)
        self.logger.info(f"Set sweep stop frequency for channel {channel} to {formatted_freq} Hz")

    def get_sweep_stop_frequency(self, channel):
        """
        Get the sweep stop frequency for a channel.

        :param channel: The channel number (1 or 2).
        :return: The stop frequency in Hz.
        """
        response = self._query(f":SOUR{channel}:FREQ:STOP?")
        frequency = float(response.strip())
        rounded_freq = self._round_value(frequency, self.FREQ_DECIMALS)
        self.logger.info(f"Sweep stop frequency for channel {channel} is {rounded_freq} Hz")
        return rounded_freq

    def set_sweep_trigger_source(self, channel, source):
        """
        Set the sweep trigger source.

        :param channel: The channel number (1 or 2).
        :param source: String 'IMM', 'EXT', or 'MAN'.
        """
        if source.upper() not in ['IMM', 'EXT', 'MAN']:
            raise ValueError("Trigger source must be 'IMM', 'EXT', or 'MAN'.")
        command = f":SOUR{channel}:SWE:TRIG:SOUR {source.upper()}"
        self._send_command(command)
        self.logger.info(f"Set sweep trigger source to {source.upper()} for channel {channel}")

    def get_sweep_trigger_source(self, channel):
        """
        Query the sweep trigger source.

        :param channel: The channel number (1 or 2).
        :return: String 'IMM', 'EXT', or 'MAN'
        """
        response = self._query(f":SOUR{channel}:SWE:TRIG:SOUR?")
        source = response.strip().upper()
        self.logger.info(f"Sweep trigger source for channel {channel} is {source}")
        return source

    def align_phase(self):
        """
        Enable phase alignment of dual channels.
        """
        self._send_command(":PHAS:SYNC")
        self.logger.info("Enabled phase alignment of dual channels")

    def apply_waveform(self, channel, waveform, frequency=None, amplitude=None, offset=None, phase=None):
        """
        Apply a specified waveform with optional frequency, amplitude, and offset using the APPL command.

        Note: For DC, some instruments require placeholders for frequency and amplitude.

        :param channel: The channel number (1 or 2).
        :param waveform: The waveform type (e.g., "SIN", "SQU", "RAMP", "PULS", "NOIS", "DC", "USER").
        :param frequency: (Optional) Frequency in Hz.
        :param amplitude: (Optional) Amplitude in Vpp.
        :param offset: (Optional) DC offset in V.
        :param phase: (Optional) Phase in degrees.
        """
        waveform_map = {
            "SIN": "SIN",
            "SQU": "SQU",
            "RAMP": "RAMP",
            "PULS": "PULS",
            "NOIS": "NOIS",
            "DC": "DC",
            "USER": "USER"
        }

        if waveform.upper() not in waveform_map:
            raise ValueError(f"Unsupported waveform type: {waveform}")

        appl_command = f":SOUR{channel}:APPL:{waveform_map[waveform.upper()]}"
        params = []
        if frequency is not None:
            params.append(self._format_value(frequency, self.FREQ_DECIMALS))
        if amplitude is not None:
            params.append(self._format_value(amplitude, self.AMPL_DECIMALS))
        if offset is not None:
            params.append(self._format_value(offset, self.OFFSET_DECIMALS))
        if phase is not None:
            params.append(self._format_value(phase, self.PHASE_DECIMALS))

        if params:
            appl_command += " " + ",".join(params)

        self._send_command(appl_command)
        self.logger.info(f"Applied waveform '{waveform}' to channel {channel} with parameters: frequency={frequency}, amplitude={amplitude}, offset={offset}")

    def apply_sinusoid(self, channel, frequency, amplitude, offset):
        """
        Apply a sine waveform to the specified channel using the APPL:SINusoid command.

        :param channel: The channel number (1 or 2).
        :param frequency: Frequency in Hz.
        :param amplitude: Amplitude in Vpp.
        :param offset: DC offset in V.
        """
        self.apply_waveform(channel, "SIN", frequency, amplitude, offset)

    def apply_square(self, channel, frequency, amplitude, offset):
        """
        Apply a square waveform to the specified channel using the APPL:SQUare command.

        :param channel: The channel number (1 or 2).
        :param frequency: Frequency in Hz.
        :param amplitude: Amplitude in Vpp.
        :param offset: DC offset in V.
        """
        self.apply_waveform(channel, "SQU", frequency, amplitude, offset)

    def apply_ramp(self, channel, frequency, amplitude, offset):
        """
        Apply a ramp waveform to the specified channel using the APPL:RAMP command.

        :param channel: The channel number (1 or 2).
        :param frequency: Frequency in Hz.
        :param amplitude: Amplitude in Vpp.
        :param offset: DC offset in V.
        """
        self.apply_waveform(channel, "RAMP", frequency, amplitude, offset)

    def apply_pulse(self, channel, frequency, amplitude, offset):
        """
        Apply a pulse waveform to the specified channel using the APPL:PULSe command.

        :param channel: The channel number (1 or 2).
        :param frequency: Frequency in Hz.
        :param amplitude: Amplitude in Vpp.
        :param offset: DC offset in V.
        """
        self.apply_waveform(channel, "PULS", frequency, amplitude, offset)

    def apply_noise(self, channel, amplitude, offset, frequency="DEF"):
        """
        Apply Gaussian noise to the specified channel using the APPL:NOISe command.

        :param channel: The channel number (1 or 2).
        :param amplitude: Amplitude in Vpp.
        :param offset: DC offset in V.
        :param frequency: Must be "DEF" as frequency parameter is not used.
        """
        if frequency != "DEF":
            frequency = "DEF"
        self.apply_waveform(channel, "NOIS", frequency, amplitude, offset)

    def apply_dc(self, channel, offset, frequency="DEF", amplitude="DEF"):
        """
        Apply a DC level to the specified channel using the APPL:DC command.

        :param channel: The channel number (1 or 2).
        :param offset: DC offset in V.
        :param frequency: Must be "DEF" as frequency parameter is not used.
        :param amplitude: Must be "DEF" as amplitude parameter is not used.
        """
        frequency = "DEF"
        amplitude = "DEF"
        self.apply_waveform(channel, "DC", frequency, amplitude, offset)

    def apply_user_waveform(self, channel, frequency, amplitude, offset):
        """
        Apply a user-defined arbitrary waveform to the specified channel using the APPL:USER command.

        :param channel: The channel number (1 or 2).
        :param frequency: Frequency in Hz.
        :param amplitude: Amplitude in Vpp.
        :param offset: DC offset in V.
        """
        self.apply_waveform(channel, "USER", frequency, amplitude, offset)

    def query_appl(self, channel=1):
        """
        Query the current APPL settings for the specified channel.

        :param channel: The channel number (1 or 2).
        :return: Dictionary containing waveform type, frequency, amplitude, and offset.
        """

        response = self._query(f"SOUR{channel}:APPL?")

        try:
            waveform, freq, ampl, offs = response.strip('"').split(',')
            params = {
                'waveform': waveform,
                'frequency': float(freq),
                'amplitude': float(ampl),
                'offset': float(offs)
            }
            self.logger.info(f"Queried APPL settings for channel {channel}: {params}")
            return params
        except Exception as e:
            self.logger.error(f"Failed to parse APPL query response '{response}': {e}")
            raise

    def get_system_error(self):
        """
        Read and clear an error from the error queue.

        :return: The latest error message as a string.
        """
        error = self._query("SYST:ERR?")
        self.logger.info(f"System Error: {error}")
        return error

    def get_version(self):
        """
        Query the current edition number of the instrument.

        :return: Version string of the instrument.
        """
        version = self._query("SYST:VERS?")
        self.logger.info(f"System Version: {version}")
        return version

    def set_beeper_state(self, state):
        """
        Enable or disable the beeper when an error occurs.

        :param state: Boolean, True to turn the beeper ON, False to turn it OFF.
        """
        command = f"SYST:BEEP:STAT {'ON' if state else 'OFF'}"
        self._send_command(command)
        self.logger.info(f"Set beeper state to {'ON' if state else 'OFF'}")

    def get_beeper_state(self):
        """
        Query the current state of the beeper.

        :return: Boolean, True if the beeper is ON, False if OFF.
        """
        response = self._query("SYST:BEEP:STAT?")
        state = response.strip() == "1"
        self.logger.info(f"Beeper state is {'ON' if state else 'OFF'}")
        return state

    def set_clock_source(self, source):
        """
        Select the system clock source as internal or external.

        :param source: String, either 'INT' for internal or 'EXT' for external.
        """
        if source.upper() not in ['INT', 'EXT']:
            raise ValueError("Clock source must be 'INT' or 'EXT'.")
        command = f"ROSC:SOUR {source.upper()}"
        self._send_command(command)
        self.logger.info(f"Set clock source to {source.upper()}")

    def perform_self_test(self):
        """
        Perform a self-test of the device.

        :return: The result of the self-test as a string.
        """
        result = self.self_test()
        self.logger.info(f"Self-test result: {result}")
        return result

    def clear_error_queue(self):
        """
        Clear all errors from the device's error queue.
        """
        while True:
            error = self.get_system_error()
            if error.startswith("0,"):
                break
        self.logger.info("Cleared all errors from the error queue")

    def get_idn(self):
        """
        Query the device identification string.

        :return: Identification string of the device.
        """
        idn = self._query("*IDN?")
        self.logger.info(f"Device Identification: {idn}")
        return idn
