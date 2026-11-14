"""Gateway: one AAS file in, live values out.

    AASX -> Asset Interfaces Description -> OPC UA connection -> submodel "OperationalData",
    and every Property an Asset Interfaces Mapping Configuration (IDTA 02027) maps a datapoint to
    -> AAS Part 2 REST API (read-only), next to the asset's own submodels

It keeps reading, says so when the asset is gone, and reconnects on its own. It never writes to the asset.
"""
import asyncio
import datetime
import hashlib
import pathlib
import re
import urllib.parse
import urllib.request

from asyncua import ua
from basyx.aas import model

from aidconnect.aid import Interface, read_interface
from aidconnect.aimc import read_mappings
from aidconnect.session import ConfigurationError, Session

BASE_PATH = "/api/v3.1"
OPERATIONAL_SEMANTIC_ID = "https://github.com/sulemanali5760/aidconnect/semantics/OperationalData/1/0"
MAX_AASX_BYTES = 20 * 1024 * 1024
VALUE_TYPES = {"number": model.datatypes.Double, "integer": model.datatypes.Int,
               "boolean": model.datatypes.Boolean, "string": model.datatypes.String}


def fetch_aasx(source: str, sha256: str | None = None, dest=None) -> pathlib.Path:
    """A local path, or an https URL together with the expected SHA-256 of the file.
    The AAS decides where credentials are sent, so a remote file is treated like code: pinned or refused."""
    if not source.startswith(("http://", "https://")):
        path = pathlib.Path(source)
        if sha256 and hashlib.sha256(path.read_bytes()).hexdigest() != sha256.lower():
            raise ValueError(f"{path} does not match the expected SHA-256")
        return path
    url = urllib.parse.urlparse(source)
    if url.scheme != "https" and url.hostname not in ("127.0.0.1", "localhost"):
        raise ValueError("remote AAS files are only fetched over https")
    if not sha256:
        raise ValueError("a remote AAS file needs its sha256: it tells the gateway where to send credentials")
    with urllib.request.urlopen(source, timeout=30) as r:  # noqa: S310 (scheme checked above)
        data = r.read(MAX_AASX_BYTES + 1)
    if len(data) > MAX_AASX_BYTES:
        raise ValueError("AAS file larger than 20 MB, refused")
    if hashlib.sha256(data).hexdigest() != sha256.lower():
        raise ValueError("downloaded AAS file does not match the expected SHA-256")
    dest = pathlib.Path(dest or "downloaded.aasx")
    dest.write_bytes(data)
    return dest


def _id_short(key: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_]", "_", key)
    return s if s[:1].isalpha() else "P_" + s


class _ReadOnly:
    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        if environ["REQUEST_METHOD"] not in ("GET", "HEAD"):
            start_response("405 Method Not Allowed", [("Allow", "GET, HEAD"), ("Content-Type", "text/plain")])
            return [b"read-only AAS repository"]
        return self.app(environ, start_response)


class Gateway:
    def __init__(self, aasx_or_interface, *, period: float = 1.0, log=print, **session_options):
        """session_options are passed to aidconnect.connect (credentials, certificates, endpoint override)."""
        self.interface = (aasx_or_interface if isinstance(aasx_or_interface, Interface)
                          else read_interface(aasx_or_interface))
        self.period, self.log, self.session_options = period, log, session_options
        self.values: dict = {p.name: None for p in self.interface.points}
        self.connected = False
        self.store, self.files = self.interface.store, self.interface.files
        self.mappings = read_mappings(self.interface)      # [(datapoint, target Property)], may be empty
        self._state = model.Property("ConnectionState", model.datatypes.String, "starting")
        self._updated = model.Property("LastUpdate", model.datatypes.DateTime)
        self._props = {}
        for p in self.interface.points:
            text = f"{p.title} [{p.unit}]" if p.unit else p.title
            self._props[p.name] = model.Property(_id_short(p.name), VALUE_TYPES[p.kind],
                                                 description=model.MultiLanguageTextType({"en": text}))
        self.live = model.Submodel(
            f"{self.interface.submodel_id}/OperationalData", id_short="OperationalData",
            submodel_element=[self._state, self._updated, *self._props.values()],
            semantic_id=model.ExternalReference((model.Key(model.KeyTypes.GLOBAL_REFERENCE, OPERATIONAL_SEMANTIC_ID),)))
        self.store.add(self.live)
        for shell in [o for o in self.store if isinstance(o, model.AssetAdministrationShell)]:
            shell.submodel.add(model.ModelReference.from_referable(self.live))

    @property
    def state(self) -> str:
        return self._state.value

    def _set_state(self, text: str) -> None:
        if self._state.value != text:
            self.log(f"aidconnect: {text}")
        self._state.value = text

    async def run(self, seconds: float | None = None, connected: asyncio.Event | None = None) -> None:
        """Keep the values current until `seconds` are over (None = forever). A lost connection is retried
        with a growing pause (1 s ... 30 s). A ConfigurationError is raised: retrying cannot fix it."""
        loop = asyncio.get_running_loop()
        end = None if seconds is None else loop.time() + seconds
        pause = 1.0
        while end is None or loop.time() < end:
            try:
                async with Session(self.interface, **self.session_options) as session:
                    self._set_state(f"connected to {session.endpoint}")
                    pause = 1.0
                    while end is None or loop.time() < end:
                        self.values = await session.read()
                        for name, v in self.values.items():
                            # ponytail: single attribute writes, read by the REST thread; a lock if a value
                            # ever needs more than one assignment
                            self._props[name].value = v
                        for name, sink in self.mappings:
                            sink.value = self.values[name]
                        self._updated.value = datetime.datetime.now(datetime.timezone.utc)
                        self.connected = True
                        if connected:
                            connected.set()
                        await asyncio.sleep(self.period)
            except ConfigurationError:
                raise
            except (OSError, asyncio.TimeoutError, ua.UaError) as e:
                self.connected = False
                self._set_state(f"disconnected ({type(e).__name__}), retrying")
                if connected:
                    connected.clear()
                await asyncio.sleep(pause)
                pause = min(pause * 2, 30.0)

    def serve_in_thread(self, port: int, host: str = "127.0.0.1"):
        """AAS Part 2 REST API (read-only): the asset's submodels plus OperationalData.
        Needs the Eclipse BaSyx Python server (see README). Returns the server; call .shutdown() to stop."""
        import threading

        try:
            from app.interfaces.repository import WSGIApp
            from werkzeug.serving import make_server
        except ImportError as e:
            raise ConfigurationError("the REST side needs the Eclipse BaSyx Python server; "
                                     "install it as shown in the README") from e
        srv = make_server(host, port, _ReadOnly(WSGIApp(self.store, self.files, base_path=BASE_PATH)))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv
