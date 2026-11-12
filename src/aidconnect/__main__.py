"""Command line:  python -m aidconnect inspect asset.aasx   |   python -m aidconnect serve asset.aasx"""
import argparse
import asyncio
import os
import pathlib
import sys

from aidconnect import Gateway, read_interface
from aidconnect.gateway import BASE_PATH, fetch_aasx
from aidconnect.session import ConfigurationError, ensure_client_certificate


def _inspect(a) -> None:
    itf = read_interface(fetch_aasx(a.aasx, a.sha256))
    sec = itf.security
    print(f"{itf.title or 'OPC UA interface'}")
    print(f"  endpoint  {itf.endpoint}")
    print(f"  security  {sec.policy or 'None'} {sec.mode or ''}  login {sec.user_token or 'Anonymous'}")
    for p in itf.points:
        rng = "" if p.minimum is None else f"  {p.minimum:g} .. {p.maximum:g}"
        print(f"  {p.name:<28} {p.kind:<8} {p.unit:<5}{rng}  {p.title}")


async def _serve(a) -> None:
    itf = read_interface(fetch_aasx(a.aasx, a.sha256))
    options = {"endpoint": a.endpoint}
    if itf.security.secure_channel:
        cert, key = await ensure_client_certificate(a.pki)
        options.update(client_cert=cert, client_key=key, server_cert=a.server_cert)
        print(f"client certificate: {cert}  (the server must trust it)")
    if itf.security.needs_login:
        options.update(username=a.username or os.environ.get("AIDCONNECT_USERNAME"),
                       password=os.environ.get("AIDCONNECT_PASSWORD"))
    gw = Gateway(itf, period=a.period, **options)
    gw.serve_in_thread(a.port, a.host)
    print(f"live AAS: http://{a.host}:{a.port}{BASE_PATH}/submodels   (submodel OperationalData)")
    await gw.run()


def main() -> None:
    ap = argparse.ArgumentParser(prog="aidconnect", description="Connect to an asset from its AAS file")
    sub = ap.add_subparsers(dest="command", required=True)
    for name, text in (("inspect", "show what the AAS says about the OPC UA interface (no connection)"),
                       ("serve", "connect and publish live values on the AAS REST API")):
        p = sub.add_parser(name, help=text)
        p.add_argument("aasx", help="AASX file, or https URL (then --sha256 is required)")
        p.add_argument("--sha256", help="expected SHA-256 of the AASX file")
    p.add_argument("--endpoint", help="use this OPC UA endpoint instead of the one in the AAS")
    p.add_argument("--server-cert", type=pathlib.Path, help="the OPC UA server's certificate (secure channel)")
    p.add_argument("--pki", type=pathlib.Path, default=pathlib.Path("pki"), help="folder for the client certificate")
    p.add_argument("--username", help="login name (or AIDCONNECT_USERNAME); the password comes from AIDCONNECT_PASSWORD")
    p.add_argument("--period", type=float, default=1.0, help="seconds between reads")
    p.add_argument("--host", default="127.0.0.1", help="REST bind address (keep localhost unless behind a proxy)")
    p.add_argument("--port", type=int, default=8082)
    a = ap.parse_args()
    try:
        if a.command == "inspect":
            _inspect(a)
        else:
            asyncio.run(_serve(a))
    except (ConfigurationError, LookupError, ValueError) as e:
        sys.exit(f"aidconnect: {e}")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
