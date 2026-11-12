"""Open the OPC UA connection an Asset Interfaces Description describes, and read its datapoints."""
import pathlib
import socket

from asyncua import Client, ua
from asyncua.crypto import cert_gen, security_policies
from cryptography.x509.oid import ExtendedKeyUsageOID

from aidconnect.aid import Datapoint, Interface, read_interface

APP_URI = "urn:aidconnect:client"
CASTS = {"number": float, "integer": int, "boolean": bool, "string": str}


class ConfigurationError(ValueError):
    """The AAS asks for something the caller did not provide (certificate, password, ...).
    Retrying does not help; a person has to fix the setup."""


async def ensure_client_certificate(folder, app_uri: str = APP_URI) -> tuple[pathlib.Path, pathlib.Path]:
    """Self-signed application certificate for this client, created once in `folder`.
    Give the .der file to the server's administrator: the server must trust it before it lets you in."""
    folder = pathlib.Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    cert, key = folder / "client_cert.der", folder / "client_key.pem"
    if not cert.exists():
        await cert_gen.setup_self_signed_certificate(
            key, cert, app_uri, socket.gethostname(), [ExtendedKeyUsageOID.CLIENT_AUTH],
            {"organizationName": "aidconnect", "commonName": "aidconnect client"})
    return cert, key


class Session:
    """A live connection to the asset. Use it with `async with`."""

    def __init__(self, interface: Interface, *, username: str | None = None, password: str | None = None,
                 client_cert=None, client_key=None, server_cert=None, app_uri: str = APP_URI,
                 endpoint: str | None = None, timeout: float = 5.0):
        self.interface = interface
        self.endpoint = endpoint or interface.endpoint
        self.points: tuple[Datapoint, ...] = interface.points
        self._auth = (username, password, client_cert, client_key, server_cert, app_uri)
        self._timeout = timeout
        self._client: Client | None = None
        self._nodes = []

    async def _configure(self, c: Client) -> None:
        """Set the client up exactly as the AID demands. Never weaker, and never guessed."""
        username, password, cert, key, server_cert, app_uri = self._auth
        sec = self.interface.security
        if sec.needs_login and (not username or password is None):
            raise ConfigurationError("the AAS asks for a UserName login: username and password are needed")
        if sec.secure_channel:
            policy = getattr(security_policies, f"SecurityPolicy{sec.policy}", None)
            if policy is None:
                raise ConfigurationError(f"the AAS asks for security policy {sec.policy!r}, which is not supported")
            if cert is None or key is None:
                raise ConfigurationError("the AAS asks for a secure channel: client_cert and client_key are needed")
            if server_cert is None or not pathlib.Path(server_cert).exists():
                raise ConfigurationError("the AAS asks for a secure channel: server_cert (the server's certificate) "
                                         "is needed, so the client can check who it talks to")
            c.application_uri = app_uri
            # The channel is encrypted for this one certificate, and the session is refused if the
            # server presents another one.
            await c.set_security(policy, str(cert), str(key), server_certificate=str(server_cert),
                                 mode=ua.MessageSecurityMode[sec.mode])
        if sec.needs_login:
            c.set_user(username)
            c.set_password(password)

    async def __aenter__(self) -> "Session":
        c = Client(self.endpoint, timeout=self._timeout)
        await self._configure(c)
        await c.connect()
        try:
            self._nodes = []
            for p in self.points:
                idx = p.ns_index if p.ns_index is not None else await c.get_namespace_index(p.namespace)
                self._nodes.append(c.get_node(ua.NodeId(p.identifier, idx)))
        except BaseException:
            await c.disconnect()
            raise
        self._client = c
        return self

    async def __aexit__(self, *exc) -> None:
        c, self._client = self._client, None
        if c is not None:
            try:
                await c.disconnect()
            except (OSError, ua.UaError, TimeoutError):
                pass  # the connection is already gone

    async def read(self) -> dict:
        """All datapoints in one request: {name: value}, typed as the AID says."""
        if self._client is None:
            raise RuntimeError("Session is not open; use 'async with'")
        values = await self._client.read_values(self._nodes)
        return {p.name: None if v is None else CASTS[p.kind](v) for p, v in zip(self.points, values)}


def connect(aasx_or_interface, **options) -> Session:
    """`async with connect("asset.aasx", ...) as session:` connects as the AAS describes.

    Options: username, password, client_cert, client_key, server_cert, app_uri, endpoint (override), timeout.
    What is needed depends on the AAS: nothing for an open server; certificates and a login for a secured one.
    """
    interface = aasx_or_interface if isinstance(aasx_or_interface, Interface) else read_interface(aasx_or_interface)
    return Session(interface, **options)
