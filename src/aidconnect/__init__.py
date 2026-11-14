"""aidconnect: connect to an industrial asset from its Asset Administration Shell.

    import aidconnect

    async with aidconnect.connect("asset.aasx") as session:
        print(await session.read())
"""
from aidconnect.aid import Datapoint, Interface, Security, parse_href, read_interface
from aidconnect.gateway import Gateway, fetch_aasx
from aidconnect.session import ConfigurationError, Session, connect, ensure_client_certificate

__version__ = "0.2.0"
__all__ = ["ConfigurationError", "Datapoint", "Gateway", "Interface", "Security", "Session", "connect",
           "ensure_client_certificate", "fetch_aasx", "parse_href", "read_interface"]
