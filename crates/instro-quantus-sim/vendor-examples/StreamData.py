# QServer introduction to Python: How to stream data from TCP socket.
# This example is primarily for demonstration purposes and does not handle all possible data payload types.
# Please refer to the QServer Software Manual for a complete list of data payload types and how to handle them.

import socket
import struct
import requests
from ctypes import c_long

ip = "192.168.100.47"
url = "http://" + ip + ":8080"

# First we can check if the system is online by sending a /info/ping/ request and checking the response status code.
response = requests.get(url + "/info/ping/")
if response.status_code == 200:
    print("Server is online")
else:  
    print("Server is offline")
    exit()

# Normally you would configure the Items here before you start streaming data.
# However, for this example we assume the configuration is already done, and you have at least a few channels enabled for streaming.
# If that is not the case, then please ensure you have a Module installed and configured with at least one Channel enabled.

# First thing we need to do is to request which port is available for streaming.
# We can do this by requesting the /datastream/setup/ endpoint.
response = requests.get(url + "/datastream/setup/")
if response.status_code != 200:
    print("Failed to receive datastream setup")
    exit()

datastream_setup = response.json()
streaming_port = datastream_setup["TCPPort"]

# Next we need to open a TCP port to start streaming data.
client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
client_socket.connect((ip, streaming_port))

# Lets loop for a while and read the data from the client socket.
analog_channel_data = {}
for loop_count in range(500):
    print("Loop count:", loop_count + 1)

    # The first 32 bytes of the data stream contains the header information.
    # The header information contains the following fields:
    # - SequenceNumber: The sequence number of the data packet; 64-bit unsigned integer
    # - TransmitTimestamp: The system timestamp for when the data packet was sent; 64-bit floating point
    # - BufferLevel: The buffer level of the controller; 32-bit floating point
    # - PayloadSize: The size of the data payload; 32-bit unsigned integer
    # - ByteOrderMarker: The byte order marker; 32-bit unsigned integer
    # - PayloadType: The type of the payload data; 32-bit unsigned integer
    data = client_socket.recv(32)
    sequence_number = struct.unpack('<Q', data[0:8])[0]
    transmit_timestamp = struct.unpack('<d', data[8:16])[0]
    buffer_level = struct.unpack('<f', data[16:20])[0]
    payload_size = struct.unpack('<I', data[20:24])[0]
    byte_order_marker = struct.unpack('<I', data[24:28])[0]
    payload_type = struct.unpack('<I', data[28:32])[0]

    # This example will only deal with payload_type 0, which is the data payload.
    # If another type is delivered, then the data will be discarded.
    # we'll read the entire payload data in one go, then parse the bytes from this local buffer.
    index = 0
    payload_data = client_socket.recv(payload_size)
    while len(payload_data) < payload_size:
        payload_data += client_socket.recv(payload_size - len(payload_data))

    received_payload_size = len(payload_data)
    if payload_type != 0:
        continue

    # You can also check the byte order marker here, if it is not 0xfffe then the endianness is different.
    if byte_order_marker != 0xfffe:
        print("Unknown byte order marker")
        exit()

    # The payload structure consists of Generic Channel Headers for all channels that are enabled for streaming.
    # Following the Generic Channel Headers will be Specific Channel Headers followed by the actual sampled data.
    # Let's loop through the payload and see how we can parse the data.
    while index < payload_size:
        # First read a Generic Channel Header:
        # - ChannelId: The Channel identifier; 32-bit signed integer
        # - SampleType: The type of the sample data; 32-bit signed integer
        # - ChannelType: The type of the Channel; 32-bit unsigned integer
        # - ChannelDataSize: The size of the Channel data; 32-bit unsigned integer
        # - Timestamp: The timestamp of the sample data; 64-bit unsigned integer
        data = payload_data[index:index + 24]
        index += 24

        channel_id = struct.unpack('i', data[0:4])[0]
        sample_type = struct.unpack('i', data[4:8])[0]
        channel_type = struct.unpack('I', data[8:12])[0]
        channel_data_size = struct.unpack('I', data[12:16])[0]
        timestamp = struct.unpack('Q', data[16:24])[0]

        # Next read the Specific Channel Header based on the ChannelType.
        if channel_type == 0:
            # The Analog Channel Header contains the following fields:
            # - ChannelIntegrity: The integrity of the data; 32-bit signed integer
            # - LevelCrossingOccurred: The level crossing occurred; 32-bit signed integer
            # - Level: The level of the data in this packet; 32-bit floating point
            # - Min: The minimum value of the data in this packet; 32-bit floating point
            # - Max: The maximum value of the data in this packet; 32-bit floating point
            specific_data = payload_data[index:index + 20]
            index += 20

            analog_channel_integrity = struct.unpack('i', specific_data[0:4])[0]
            level_crossing_occurred = struct.unpack('i', specific_data[4:8])[0]
            level = struct.unpack('f', specific_data[8:12])[0]
            min_value = struct.unpack('f', specific_data[12:16])[0]
            max_value = struct.unpack('f', specific_data[16:20])[0]

            # Depending on the SampleType, as specified in the Generic Channel Header, there might be an additional field and the data format might change.
            # Hence we need to read the data based on the SampleType.
            if sample_type == 0:
                # The SampleType 0 is for 32-bit floating point data.
                specific_data = payload_data[index:index + channel_data_size]
                index += channel_data_size
                sampled_data = struct.unpack('f' * (channel_data_size // 4), specific_data)
            
            # The following sample types will occur only when you configure the controller to deliver Raw Data.
            elif sample_type == 1:
                # The SampleType 1 is for 16-bit signed integer data.
                # You'll have to read one additional field for the scaling factor, then read the data scaling it to a float yourself.
                scaling_factor = struct.unpack('f', payload_data[index:index + 4])[0]
                index += 4

                specific_data = payload_data[index:index + channel_data_size]
                index += channel_data_size

                sampled_data = [scaling_factor * struct.unpack('h', specific_data[i:i + 2])[0] for i in range(0, len(specific_data), 2)]

            elif sample_type == 2:
                # The SampleType 2 is for 24-bit signed integer data.
                # You'll have to read one additional field for the scaling factor, then read the data scaling it to a float yourself.
                scaling_factor = struct.unpack('f', payload_data[index:index + 4])[0]
                index += 4

                specific_data = payload_data[index:index + channel_data_size]
                index += channel_data_size

                # Since python does not have a 24-bit integer type, we'll have to read the 24-bit data as a fixed length 32-bit signed integer and then scale it to a float.
                # The ctypes library was used here to handle the 32-bit signed integer data.
                sampled_data = []
                for i in range(0, len(specific_data), 3):
                    value = c_long((specific_data[i + 2] << 24) | (specific_data[i + 1] << 16) | (specific_data[i] << 8))
                    sampled_data.append(scaling_factor * float(value.value))

            elif sample_type == 3:
                # The SampleType 3 is for 32-bit signed integer data.
                # You'll have to read one additional field for the scaling factor, then read the data scaling it to a float yourself.
                scaling_factor = struct.unpack('f', payload_data[index:index + 4])[0]
                index += 4

                specific_data = payload_data[index:index + channel_data_size]
                index += channel_data_size

                sampled_data = [scaling_factor * struct.unpack('i', specific_data[i:i + 4])[0] for i in range(0, len(specific_data), 4)]
        
        elif channel_type == 1:
            # The Counter Channels (Tacho) does not have a specific header, hence we can directly read the data.
            specific_data = payload_data[index:index + channel_data_size]
            index += channel_data_size

            sampled_data = struct.unpack('d' * (channel_data_size // 8), specific_data)

        elif channel_type == 2:
            # The CAN Bus Channel Header reserves 24 bytes for future use.
            specific_data = payload_data[index:index + 24]
            index += 24

            # The CAN Channel Data is a list of messages, where each message contains the following fields:
            # - Timestamp: The timestamp of the message; 64-bit floating point
            # - ID: The identifier of the message; 32-bit unsigned integer
            # - Header: The header of the message; 8-bit unsigned integer
            # - Frame Format: The frame format of the message; 8-bit unsigned integer
            # - Frame Type: The frame type of the message; 8-bit unsigned integer
            # - DLC: The data length code of the message; 8-bit unsigned integer
            # - Data: The data of the message; list of up to 64 bytes
            message_list = []
            message_end = index + channel_data_size
            while index < message_end:
                timestamp = struct.unpack('d', payload_data[index:index + 8])[0]
                index += 8
                message_id = struct.unpack('I', payload_data[index:index + 4])[0]
                index += 4
                header = struct.unpack('B', payload_data[index:index + 1])[0]
                index += 1
                frame_format = struct.unpack('B', payload_data[index:index + 1])[0]
                index += 1
                frame_type = struct.unpack('B', payload_data[index:index + 1])[0]
                index += 1
                dlc = struct.unpack('B', payload_data[index:index + 1])[0]
                index += 1
                data = list(payload_data[index:index + dlc])
                index += dlc

                message = {
                    "Timestamp": timestamp,
                    "ID": message_id,
                    "Header": header,
                    "Frame Format": frame_format,
                    "Frame Type": frame_type,
                    "DLC": dlc,
                    "Data": data
                }

                message_list.append(message)

            index += channel_data_size

        elif channel_type == 3:
            # NOTE: GPS channel is still in Beta, use at own risk.
            # The GPS Bus Channel Header reserves 12 bytes.
            specific_data = payload_data[index:index + 12]
            index += 12

            timestamp = struct.unpack('Q', specific_data[0:8])[0]
            accuracyInNanoSeconds = struct.unpack('H', specific_data[8:10])[0]
            isLeapSecondsValid = struct.unpack('B', specific_data[10:11])[0]
            leapSeconds = struct.unpack('B', specific_data[11:12])[0]

            # Now read the GPS message, it formatted in ASCII and always end with a /r/n, 
            gpsMessage = payload_data[index:index + channel_data_size].decode('ascii')
            index += channel_data_size
            print(gpsMessage)
        else:
            print("Unknown Channel Type: ", channel_type)
            exit()

        # Store the sampled data in a dictionary with channel_id as the key
        if channel_id not in analog_channel_data:
            analog_channel_data[channel_id] = []

        analog_channel_data[channel_id].append(sampled_data)

client_socket.close()

# Thats it for this example. You can now process the data further as needed.

# Please note that this example only deals with the data payload type 0, and does not handle other payload types.
# Refer to the QServer API documentation for more information on the data payload types and how to handle them.