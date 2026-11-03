InstroDAQ
=========

.. currentmodule:: instro.daq



.. autoclass:: InstroDAQ

   .. rubric:: Attributes

   .. autosummary::
      :nosignatures:

      ~InstroDAQ.DEFAULT_SW_SAMPLE_RATE
      ~InstroDAQ.ai_channels
      ~InstroDAQ.ai_hw_timing_config
      ~InstroDAQ.ao_channels
      ~InstroDAQ.ao_hw_timing_config
      ~InstroDAQ.background_interval
      ~InstroDAQ.channel_names
      ~InstroDAQ.channels
      ~InstroDAQ.di_channels
      ~InstroDAQ.di_hw_timing_config
      ~InstroDAQ.do_channels
      ~InstroDAQ.do_hw_timing_config
      ~InstroDAQ.driver
      ~InstroDAQ.is_hw_timing_configured
      ~InstroDAQ.is_sw_timing_configured
      ~InstroDAQ.relay_channels


   .. rubric:: Methods

   .. autosummary::
      :nosignatures:

      ~InstroDAQ.add_background_daemon_function
      ~InstroDAQ.add_publisher
      ~InstroDAQ.close
      ~InstroDAQ.close_relay
      ~InstroDAQ.configure_ai_hw_sample_rate
      ~InstroDAQ.configure_ai_sample_rate
      ~InstroDAQ.configure_ai_sw_sample_rate
      ~InstroDAQ.configure_analog_channel
      ~InstroDAQ.configure_current_input
      ~InstroDAQ.configure_current_output
      ~InstroDAQ.configure_digital_input
      ~InstroDAQ.configure_digital_line
      ~InstroDAQ.configure_digital_output
      ~InstroDAQ.configure_digital_port
      ~InstroDAQ.configure_relay_channel
      ~InstroDAQ.configure_thermocouple_input
      ~InstroDAQ.configure_voltage_input
      ~InstroDAQ.configure_voltage_output
      ~InstroDAQ.define_background_daemon
      ~InstroDAQ.get_actual_sample_rate
      ~InstroDAQ.get_channel
      ~InstroDAQ.get_points_in_buffer
      ~InstroDAQ.get_single_channel_value
      ~InstroDAQ.open
      ~InstroDAQ.open_relay
      ~InstroDAQ.publish
      ~InstroDAQ.read
      ~InstroDAQ.read_analog
      ~InstroDAQ.read_batch
      ~InstroDAQ.read_digital_line
      ~InstroDAQ.read_digital_port
      ~InstroDAQ.start
      ~InstroDAQ.stop
      ~InstroDAQ.write
      ~InstroDAQ.write_analog_value
      ~InstroDAQ.write_batch
      ~InstroDAQ.write_digital_line
      ~InstroDAQ.write_digital_port

