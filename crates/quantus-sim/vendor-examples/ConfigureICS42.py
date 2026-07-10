# QServer introduction to Python: How to configure Module and Channels.
# For this example an ICS42 Module is used. Please ensure that the ICS42 is installed in the system.

import json
import requests

# Specify your server's IP address, port 8080 is the default port for the QServer.
ipAddress = "192.168.100.47"
url = "http://" + ipAddress + ":8080"

# First we can check if the system is online by sending a ping request
response = requests.get(url + "/info/ping/")
if response.status_code == 200:
    print("Server is online")
else:
    print("Server is offline")
    exit()

# Now we can request the item list from the server.
# The item list will be used to find the ICS42 Module and configure it.
response = requests.get(url + "/item/list/")
if response.status_code != 200:
    print("Failed to receive item list")
    exit()

item_list = response.json()
module_id = None
channel_list = []
for item in item_list:
    # Item names are unique, hence we can use them to identify the ICS42 Module and Channels.
    # You can also use the ItemNameIdentifier to identify the items, which you'll fine the values of in the manual.
    if item["ItemName"] == "ICS421" or item["ItemName"] == "ICS425":
        if item["ItemType"] == "Module":
            module_id = item["ItemId"]
        elif item["ItemType"] == "Channel":
            channel_list.append(item["ItemId"])
    
    # Check if the ICS42 Module and all 6 Channels are found, and exit the loop once we have them.
    if module_id is not None and len(channel_list) == 6:
        print("Module found with ItemId:", module_id)
        print("Channels found with ItemId's:", channel_list)
        break    

if module_id is None or len(channel_list) != 6:
    print("Not all Items were found")
    exit()

# Now we can configure the ICS42 Module.
# The first step would be to retrieve the Item Operation Mode. This will give us an overview of the available settings.
# Here we need to supply QServer with the ItemId of the ICS42 Module.
response = requests.get(url + "/item/operationMode/", params={"itemId": module_id})
if response.status_code != 200:
    print("Failed to receive item operation mode")
    exit()

# Lets print the response to see the operation mode of the ICS42 Module.
operation_mode = response.json()
print(json.dumps(operation_mode, indent=4))

# The operation mode contains the following fields:
# - ItemId: The unique identifier of the item.
# - ItemName: The name of the item.
# - ItemNameIdentifier: A unique ID assigned for the specific Name.
# - ItemType: A human readable name for the Type. Item Types include Controller, Signal Conditioner, Module and Channel.
# - ItemTypeIdentifier: A unique ID assigned for the Type.
# - Info: A list of additional information about the item. For a Module, this includes the serial number.
# - SettingsApplied: A boolean value indicating if the settings have been applied.
# - Settings: A list of settings that can be configured. Each setting contains the following fields:
#  - Name: The name of the setting.
#  - Type: The data type of the setting. This can be Integer, Float, String, Enumeration, or Array.
#  - SupportedValues: A list of supported values for the setting given with an Id and Description pair. This is only available for Enumerations.
#  - Value: The current value of the setting.

# To change the operation mode you need to change the Value field to the ID of the supported value you which to select.
# For the operation mode endpoint you'll only receive one item in the Setting list, hence we can assume 0 index.
# Let also assume the ID of the supported value you want to select is 1.
operation_mode["Settings"][0]["Value"] = 1

# Now the new settings can be sent to QServer.
response = requests.put(url + "/item/operationMode/", params={"itemId": module_id}, json=operation_mode)
if response.status_code != 200:
    print("Failed to set item operation mode")
    exit()

# The Module will control the sample rate of it's Channels, hence we need to change this value in the Module settings.
# Let's retrieve the settings of the Module.
response = requests.get(url + "/item/settings/", params={"itemId": module_id})
if response.status_code != 200:
    print("Failed to receive item settings")
    exit()

module_settings = response.json()
for setting in module_settings["Settings"]:
    if "Sample Rate" in setting["Name"]:
        setting["Value"] = 0

# Now the new settings can be sent to QServer.
response = requests.put(url + "/item/settings/", params={"itemId": module_id}, json=module_settings)
if response.status_code != 200:
    print("Failed to set item settings")
    exit()

# The operation mode has been set for the Module, now we can configure the Channels.
# Please note that changing a setting does not reconfigure the item, this will happen later with an Apply request.

# First we need to retrieve the operation mode of the Channels.
for channel_id in channel_list:
    response = requests.get(url + "/item/operationMode/", params={"itemId": channel_id})
    if response.status_code != 200:
        print("Failed to receive item operation mode for Channel with ItemId:", channel_id)
        exit()

    # The ICS42 Channels have the ability to power ICP sensors, hence the operation mode will have a setting for that.
    # Let's use the "SupportedValues" field to find a setting that is related to powering ICP sensors.
    channel_operation_mode = response.json()
    icp_setting = None
    for setting in channel_operation_mode["Settings"][0]["SupportedValues"]:
        if "ICP" in setting["Description"]:
            icp_setting = setting["Id"]
            break
    
    # Now we can change the value of the ICP-related setting.
    channel_operation_mode["Settings"][0]["Value"] = icp_setting

    # Now the new settings can be sent to QServer.
    response = requests.put(url + "/item/operationMode/", params={"itemId": channel_id}, json=channel_operation_mode)
    if response.status_code != 200:
        print("Failed to set item operation mode for Channel with ItemId:", channel_id)
        exit()

    # Now that the operation mode has been set, we can request the settings of the Channel.
    # Please note that the Channel settings will change depending on the operation mode, hence it is important to set the operation mode first.
    response = requests.get(url + "/item/settings/", params={"itemId": channel_id})
    if response.status_code != 200:
        print("Failed to receive item settings for Channel with ItemId:", channel_id)
        exit()

    channel_settings = response.json()

    # The response of the item setting request will be similar to that of the operation mode response.
    # Hence lets find the settings we want to change and set them.
    for setting in channel_settings["Settings"]:
        if "Voltage Range" in setting["Name"]:
            setting["Value"] = 1
        elif "Coupling" in setting["Name"]:
            setting["Value"] = 0

    # It is important to ensure the Channel is enabled to stream data if you wish to receive data from it.
    # This can be done through the Data field, which will only be available on Items which can stream data.
    data_setting = channel_settings["Data"]

    # Two entries will exist depending on the Channel capabilities, one for Streaming and one for Local Storage.
    # Let's enable Streaming and disable Local Storage.
    for setting in data_setting:
        if "Streaming" in setting["Name"]:
            setting["Value"] = 1
        elif "Local Storage" in setting["Name"]:
            setting["Value"] = 0

    channel_settings["Data"] = data_setting

    # Now the new settings can be sent to QServer.
    response = requests.put(url + "/item/settings/", params={"itemId": channel_id}, json=channel_settings)
    if response.status_code != 200:
        print("Failed to set item settings for Channel with ItemId:", channel_id)
        exit()


# Now that the operation mode and settings have been set for the ICS42 Module and Channels, we can apply the settings.
# This will reconfigure the ICS42 Module and Channels with the new settings.
response = requests.put(url + "/system/settings/apply")
if response.status_code != 200:
    print("Failed to apply settings")
    exit()

print("Settings applied successfully")

# That's it! The ICS42 Module and Channels have been configured and the system is ready to stream data.
# All of the setting values used in this example can be found in the QuantusSoftware manual available on GitHub.
# Alternatively you can use the "SupportedValues" field to find the available values for each setting making it easy to build a GUI.

# Next you might want to steam data, please refer to the PythonBasicsStreamData example for more information.