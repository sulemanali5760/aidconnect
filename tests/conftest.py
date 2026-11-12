"""Two small OPC UA servers the tests connect to. Neither is known to the library:
- "tank": open server, four datapoints of four types (tests/data/tank.aasx describes it)
- "plant": secured server (Basic256Sha256, SignAndEncrypt, trust list, UserName login), eight numbers
  (tests/data/plant.aasx describes it)
The AASX files were written with the AID builder of github.com/sulemanali5760/heating-plant-aas."""
import asyncio
import pathlib
import socket

from asyncua import Server, ua
from asyncua.common.utils import ServiceError
from asyncua.crypto import cert_gen
from asyncua.crypto.truststore import TrustStore
from asyncua.crypto.validator import CertificateValidator, CertificateValidatorOptions
from asyncua.server.user_managers import User, UserManager, UserRole
from cryptography.x509.oid import ExtendedKeyUsageOID

DATA = pathlib.Path(__file__).parent / "data"
TANK_AASX, PLANT_AASX = DATA / "tank.aasx", DATA / "plant.aasx"
TANK_NS = "urn:example:tank:ua"
PLANT_NS = "https://github.com/sulemanali5760/heating-plant-aas/UA/"
PLANT_VALUES = {"TIC101.SP": 44.0, "TIC101.PV": 43.9, "TIC101.OP": 20.5, "TI102.PV": 36.0, "TI103.PV": 0.0,
                "PDIC301.SP": 1.0, "PDIC301.PV": 0.99, "PDIC301.OP": 74.1}
SERVER_URI = "urn:example:plant:server"
USER = "operator"


async def tank_server(endpoint: str, ready: asyncio.Event, stop: asyncio.Event) -> None:
    server = Server()
    await server.init()
    server.set_endpoint(endpoint)
    server.set_security_policy([ua.SecurityPolicyType.NoSecurity])
    idx = await server.register_namespace(TANK_NS)
    tank = await server.nodes.objects.add_object(ua.NodeId("Tank", idx), ua.QualifiedName("Tank", idx))
    for name, value, vt in [("Level", 2.5, ua.VariantType.Double), ("PumpOn", True, ua.VariantType.Boolean),
                            ("Cycles", 7, ua.VariantType.Int64), ("Name", "B101", ua.VariantType.String)]:
        await tank.add_variable(ua.NodeId(name, idx), ua.QualifiedName(name, idx), ua.Variant(value, vt))
    async with server:
        ready.set()
        await stop.wait()


class _OneUser(UserManager):
    def __init__(self, password):
        self.password = password

    def get_user(self, iserver, username=None, password=None, certificate=None):
        return User(role=UserRole.User, name=username) if (username, password) == (USER, self.password) else None


async def plant_server(endpoint: str, pki: pathlib.Path, password: str, ready: asyncio.Event,
                       stop: asyncio.Event) -> None:
    """Accepts only encrypted sessions from client certificates in pki/trusted, with the right login."""
    (pki / "trusted").mkdir(parents=True, exist_ok=True)
    cert, key = pki / "server_cert.der", pki / "server_key.pem"
    if not cert.exists():
        await cert_gen.setup_self_signed_certificate(key, cert, SERVER_URI, socket.gethostname(),
                                                     [ExtendedKeyUsageOID.SERVER_AUTH], {"commonName": "test plant"})
    server = Server(user_manager=_OneUser(password))
    await server.init()
    server.set_endpoint(endpoint)
    await server.set_application_uri(SERVER_URI)
    await server.load_certificate(cert)
    await server.load_private_key(key)
    server.set_security_policy([ua.SecurityPolicyType.Basic256Sha256_SignAndEncrypt])
    server.set_identity_tokens([ua.UserNameIdentityToken])
    if any((pki / "trusted").glob("*.der")):
        store = TrustStore([pki / "trusted"], [])
        await store.load()
        server.set_certificate_validator(CertificateValidator(
            CertificateValidatorOptions.TRUSTED | CertificateValidatorOptions.PEER_CLIENT, store))
    else:
        async def reject_all(cert, app_description):
            raise ServiceError(ua.StatusCodes.BadCertificateUntrusted)
        server.set_certificate_validator(reject_all)
    idx = await server.register_namespace(PLANT_NS)
    plant = await server.nodes.objects.add_object(ua.NodeId("HeatingPlant", idx), ua.QualifiedName("HeatingPlant", idx))
    for name, value in PLANT_VALUES.items():
        await plant.add_variable(ua.NodeId(name, idx), ua.QualifiedName(name, idx),
                                 ua.Variant(value, ua.VariantType.Double))
    async with server:
        ready.set()
        await stop.wait()


class Running:
    """`async with Running(server_coroutine_function, *args) as r:` starts a server; r.stop() / r.start() cycle it."""

    def __init__(self, fn, *args):
        self.fn, self.args, self.task, self._stop = fn, args, None, None

    async def start(self):
        ready, self._stop = asyncio.Event(), asyncio.Event()
        self.task = asyncio.create_task(self.fn(*self.args, ready, self._stop))
        waiter = asyncio.create_task(ready.wait())
        done, _ = await asyncio.wait({self.task, waiter}, timeout=20, return_when=asyncio.FIRST_COMPLETED)
        if waiter not in done:
            waiter.cancel()
            self.task.result() if self.task in done else None
            raise TimeoutError("test server did not start")

    async def stop(self):
        if self.task and not self.task.done():
            self._stop.set()
            await self.task
        self.task = None

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *exc):
        await self.stop()
