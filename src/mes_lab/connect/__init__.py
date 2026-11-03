"""Shop-floor connectivity: the machines as an OPC UA server and the Unified Namespace in MQTT.

* `shopfloor` - what the machines show right now (state, piece, counters, the last process values), one view
  for both protocols, and the names in the ISA-95 hierarchy: enterprise / site / area / line / station;
* `uns`       - MQTT: the edge gateway publishes every change under acme/plant1/<area>/<line>/<station>/...
  (retained, so a newcomer gets the present state at once), the MES adds its own data and mirrors the
  whole tree for its web page;
* `opcua`     - OPC UA: the plant is a server with an object per area, line and station and a variable per
  value (the way a machine or a PLC exposes its data); the MES browses and reads it.

Both are off unless configured: MES_MQTT_URL (mqtt://host:port) and MES_OPCUA_ENDPOINT / MES_OPCUA_URL.
"""
