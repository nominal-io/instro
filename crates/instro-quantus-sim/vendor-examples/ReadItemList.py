# QServer introduction to Python: How to read and interpret the item list.

import json
import requests

# Specify your server's IP address, port 8080 is the default port for the QServer.
ipAddress = "192.168.100.47"
url = "http://" + ipAddress + ":8080"

# First we can check if the system is online by sending a ping request
response = requests.get(url + "/info/ping/")
if response.status_code == 200:
    print("Server is online")
    # Since we know the server is online, list have a look at the response of the query.

    print(response.text)
    # Code: This field indicates the status of the request. It is QServer specific error codes which can be found in
    #       the QServer documentation.
    # Message: This field contains a human-readable message which can be used to understand the response.
else:
    print("Server is offline")
    exit()

# Now we can request the item list from the server.
response = requests.get(url + "/item/list/")
if response.status_code == 200:
    print("Item list received:")

    # Let's print the response to see the item list.
    # It is formatted in JSON, hence we can print it in a more readable way.
    item_list = response.json()
    print(json.dumps(item_list, indent=4))

    # Depending on the system configuration, the item list can look a little different.
    # The item list contains the following fields:
    # - ItemId: The unique identifier of the item.
    # - ItemName: The name of the item.
    # - ItemNameIdentifier: A unique ID assigned for the specific Name.
    # - ItemType: A human readable name for the Type. Item Types include Controller, Signal Conditioner, Module and
    #             Channel.
    # - ItemTypeIdentifier: A unique ID assigned for the Type.

    # The first item in the list is the controller. It is then followed by the signal conditioners, modules and channels.
    # The Item List is useful to understand the structure of the system and to identify the items that are available.

    # ID's for the items are assigned in a tree like structure, where the controller is the root, followed by signal
    # conditioners, modules and lastly channels. Please note that only channels can stream data. Hence it is important
    # to index these ID's to access the data later.
else: 
    print("Failed to receive item list")
    exit()

# That is it for this example. Find more examples in the Examples folders or read the QServer documentation for more.