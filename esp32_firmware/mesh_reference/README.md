# ESP-WIFI-MESH integration reference

For large installations, ESP-WIFI-MESH can carry leaf-node data to a root node. The intended architecture is:

1. Every node captures CSI and pushes a compact record to a local queue.
2. Leaf nodes use `esp_mesh_send(NULL, ...)` to send records toward the root.
3. The root drains the mesh RX queue with `esp_mesh_recv()` / the appropriate to-DS receive API for the ESP-IDF version.
4. Only the root maintains the TCP connection to WiFiSense Ultra and forwards each record with the original node ID.
5. Records must stay below ESP-MESH MTU/MPS limits or use application-level fragmentation.

This is kept as a reference rather than pretending it is production-ready. ESP-WIFI-MESH APIs and root-to-external-network forwarding behavior vary by ESP-IDF generation and need hardware testing on the exact target. Start with the direct TCP firmware, validate CSI sensing, then introduce mesh as a separate deployment phase.
