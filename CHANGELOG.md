# Changelog

All notable changes to this project are listed here. Versions follow [Semantic Versioning](https://semver.org).

## 0.2.0 - 2026-11-14

- Asset Interfaces Mapping Configuration (IDTA 02027, AIMC 1.0): the gateway writes each mapped datapoint into the
  Property the mapping names. Mappings are checked when the file is loaded.
- `Datapoint.element` and `Interface.element`: the AID elements behind them, for references.

## 0.1.1 - 2026-11-12

- Releases carry a wheel and offline bundles (tool plus all dependencies, including the BaSyx server for the REST side) for Linux and Windows.

## 0.1.0 - 2026-11-12

First public version.

- `read_interface`: endpoint, security and datapoints from the Asset Interfaces Description (IDTA 02017) of an AASX file.
- `connect`: OPC UA session configured as the AAS demands; `Session.read()` returns typed values.
- `Gateway`: live values as submodel `OperationalData` on the AAS Part 2 REST API, reconnect with back-off.
- Command line: `python -m aidconnect inspect | serve`.
- Remote AAS files only over https with a pinned SHA-256; server certificate pinned on secure connections.
