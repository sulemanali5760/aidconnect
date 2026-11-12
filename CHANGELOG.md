# Changelog

All notable changes to this project are listed here. Versions follow [Semantic Versioning](https://semver.org).

## 0.1.1 - 2026-11-12

- Releases carry a wheel and offline bundles (tool plus all dependencies, including the BaSyx server for the REST side) for Linux and Windows.

## 0.1.0 - 2026-11-12

First public version.

- `read_interface`: endpoint, security and datapoints from the Asset Interfaces Description (IDTA 02017) of an AASX file.
- `connect`: OPC UA session configured as the AAS demands; `Session.read()` returns typed values.
- `Gateway`: live values as submodel `OperationalData` on the AAS Part 2 REST API, reconnect with back-off.
- Command line: `python -m aidconnect inspect | serve`.
- Remote AAS files only over https with a pinned SHA-256; server certificate pinned on secure connections.
