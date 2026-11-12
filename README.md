# aidconnect

[![ci](https://github.com/sulemanali5760/aidconnect/actions/workflows/ci.yml/badge.svg)](https://github.com/sulemanali5760/aidconnect/actions/workflows/ci.yml)

Connect to an industrial asset from its **Asset Administration Shell**. The AAS file says where the asset's OPC UA server is, which security it wants and which datapoints it has; `aidconnect` reads that and connects. No endpoint, tag list or security setting in your code.

```python
import asyncio
import aidconnect

async def main():
    async with aidconnect.connect("tank.aasx") as session:
        print(await session.read())   # {'Level': 2.5, 'PumpOn': True, 'Cycles': 7, 'Name': 'B101'}

asyncio.run(main())
```

The information comes from the **Asset Interfaces Description** submodel (IDTA 02017) inside the AAS. Swap the file and the same code talks to a different machine.

## Install

With internet:

```bash
pip install https://github.com/sulemanali5760/aidconnect/releases/download/v0.1.1/aidconnect-0.1.1-py3-none-any.whl
```

Without internet (a plant PC, a commissioning laptop on the machine network): download the offline bundle
for your system from the [latest release](https://github.com/sulemanali5760/aidconnect/releases/latest), carry
it over on a USB stick, unzip it and run inside the folder:

```bash
python -m pip install --no-index --find-links . aidconnect
```

The bundle holds this tool and every package it needs; nothing is downloaded. Each bundle is installed in a
clean environment and started once before it is published. Python 3.11 or newer. Not on PyPI yet.

The REST side of the gateway needs the Eclipse BaSyx Python server. The offline bundle includes it; with the wheel alone, install it with the pinned line in `requirements-dev.txt`.

## What it does

| | |
|---|---|
| `aidconnect.read_interface(file)` | Endpoint, security and datapoints (name, type, unit, range, node reference) as plain Python objects. Opens no connection. |
| `aidconnect.connect(file, ...)` | A session configured exactly as the AAS demands. `await session.read()` returns all datapoints in one request, typed (`float`, `int`, `bool`, `str`). |
| `aidconnect.Gateway(file, ...)` | Keeps reading, reconnects after a connection loss (1 s … 30 s back-off), and publishes the values as submodel `OperationalData` on the AAS Part 2 REST API, read-only. |
| `python -m aidconnect inspect file` | Prints what the AAS says. |
| `python -m aidconnect serve file` | Runs the gateway. |

### A secured asset

If the AAS asks for an encrypted channel and a login, you provide the three things a file must never contain:

```python
cert, key = await aidconnect.ensure_client_certificate("pki")     # give client_cert.der to the server's admin
async with aidconnect.connect("plant.aasx",
                              client_cert=cert, client_key=key,
                              server_cert="server_cert.der",        # the only server this client will talk to
                              username="operator", password=os.environ["PLANT_PASSWORD"]) as session:
    values = await session.read()
```

If something is missing you get a `ConfigurationError` that names it, before any connection is attempted.

## Security

- **Never weaker than the AAS says.** Security policy and mode come from the file; there is no option to downgrade them.
- **The server is checked, not only the client.** A secure connection needs the server's certificate; the channel is encrypted for that certificate and the session is refused if the server shows another one.
- **Remote AAS files are pinned.** `fetch_aasx(url, sha256)` only accepts https and only a file with the expected hash, because the file decides where credentials are sent.
- **Read-only.** The library reads datapoints. It has no function that writes to a controller. The REST side refuses everything except GET and HEAD and binds to localhost by default.
- Passwords are passed in by the caller (the command line reads `AIDCONNECT_PASSWORD`); nothing is stored.

## Tested

`pytest` starts two OPC UA servers the library knows nothing about and connects to each from its AAS file only:

- an open server with four data types (number, integer, boolean, string);
- a secured server (Basic256Sha256, SignAndEncrypt, trust list, UserName login): the right setup reads; a wrong password, an untrusted client certificate and a wrong server certificate are each refused with the expected error;
- the gateway: live values over REST, asset switched off and on again, reconnects by itself.

## Limits

- **OPC UA only.** The Asset Interfaces Description can also describe Modbus, MQTT and HTTP interfaces; those are not implemented.
- **Scalar values only** (number, integer, boolean, string). No arrays or structures.
- **No hardware test yet.** Everything is tested against software servers on one machine. A Siemens S7-1500 names its nodes like `ns=3;s="DB"."tag"`; that form is parsed, but not tried on a real controller.
- **`OperationalData` is this project's own submodel.** The standard way to map datapoints into submodels is IDTA 02027 (Asset Interfaces Mapping Configuration); planned, not implemented.
- **The REST side needs the Eclipse BaSyx Python server**, which is not on PyPI. Install it with the pinned line in `requirements-dev.txt`.
- **Polling, not subscriptions.** One batched read per period (default 1 s).

## Related work

[FA³ST Service](https://github.com/FraunhoferIOSB/FAAAST-Service) (Fraunhofer IOSB) and the [Eclipse BaSyx DataBridge](https://github.com/eclipse-basyx/basyx-databridge) also build asset connections from interface descriptions. Both are Java services you deploy. `aidconnect` is a small Python library for scripts, test rigs and notebooks.

## Where it comes from

Split out of [heating-plant-aas](https://github.com/sulemanali5760/heating-plant-aas), where the same code connects to a simulated S7-1500 heating plant.

## Licence

MIT
