"""Read the OPC UA interface of an asset from its Asset Administration Shell.

The Asset Interfaces Description submodel (IDTA 02017) says where the asset's OPC UA server is, which
security it demands and which datapoints it offers. This module turns that submodel into plain Python
objects. It does not open any connection.
"""
import pathlib
from dataclasses import dataclass, field

from basyx.aas import model
from basyx.aas.adapter import aasx

AID_SEMANTIC_PREFIX = "https://admin-shell.io/idta/AssetInterfacesDescription/"
OPCUA_BINDING = "http://opcfoundation.org/UA/WoT-Binding/"
KINDS = ("number", "integer", "boolean", "string")


@dataclass(frozen=True)
class Datapoint:
    name: str                       # AID key, e.g. "TIC101.PV"
    title: str
    unit: str                       # as written in the AID (often a UNECE code), "" if none
    kind: str                       # number, integer, boolean or string
    namespace: str                  # namespace URI, "" when the AID gives an index instead
    identifier: str | int           # string or numeric node identifier
    ns_index: int | None = None     # namespace index, when the AID uses "ns=<n>"
    minimum: float | None = None
    maximum: float | None = None


@dataclass(frozen=True)
class Security:
    """Empty string = the AID does not ask for it."""
    mode: str = ""                  # e.g. SignAndEncrypt
    policy: str = ""                # e.g. Basic256Sha256
    user_token: str = ""            # e.g. UserName

    @property
    def secure_channel(self) -> bool:
        return self.mode not in ("", "None")

    @property
    def needs_login(self) -> bool:
        return self.user_token == "UserName"


@dataclass(frozen=True)
class Interface:
    endpoint: str
    security: Security
    points: tuple[Datapoint, ...]
    title: str = ""
    submodel_id: str = ""
    store: object = field(default=None, repr=False, compare=False)   # the loaded AAS (basyx object store)
    files: object = field(default=None, repr=False, compare=False)


def parse_href(href: str) -> tuple[str, int | None, str | int]:
    """OPC UA NodeId as written in an AID form: 'nsu=<namespace URI>;s=<string>' or ';i=<number>',
    or with a namespace index 'ns=<n>;s=...'. Without a namespace part the id is in namespace 0.
    Returns (namespace URI, namespace index, identifier)."""
    head, sep, ident = href.rpartition(";")
    kind, _, value = ident.partition("=")
    if kind not in ("s", "i") or (sep and not head.startswith(("nsu=", "ns="))):
        raise ValueError(f"unsupported OPC UA node reference in AID: {href}")
    node = int(value) if kind == "i" else value
    if head.startswith("nsu="):
        return head[4:], None, node
    return "", int(head[3:]) if head else 0, node


def _sem(e) -> str:
    return e.semantic_id.key[0].value if e.semantic_id else ""


def _get(coll, id_short, default=None):
    for e in coll.value:
        if e.id_short == id_short:
            return e
    return default


def _val(coll, id_short, default=""):
    e = _get(coll, id_short)
    return default if e is None or e.value is None else e.value


def _text(v) -> str:
    """Plain string from a Property value or a multi-language value."""
    if isinstance(v, str):
        return v
    try:
        return v.get("en") or next(iter(v.values()))
    except (AttributeError, StopIteration):
        return str(v)


def _security(endpoint_metadata) -> Security:
    mode = policy = token = ""
    definitions = _get(endpoint_metadata, "securityDefinitions")
    for d in (definitions.value if definitions is not None else ()):
        scheme = _val(d, "scheme")
        if scheme == "uav_channelsec":
            mode, policy = _val(d, "uav_securityMode"), _val(d, "uav_securityPolicy")
        elif scheme == "uav_authentication":
            token = _val(d, "uav_userIdentityToken")
    return Security(mode, policy, token)


def read_interface(aasx_path) -> Interface:
    """The OPC UA interface described in an AASX file. Raises LookupError if the file has no Asset
    Interfaces Description with an OPC UA interface, ValueError if a datapoint cannot be understood."""
    store, files = model.DictIdentifiableStore(), aasx.DictSupplementaryFileContainer()
    with aasx.AASXReader(str(pathlib.Path(aasx_path))) as r:
        r.read_into(store, files)
    for sm in store:
        if not isinstance(sm, model.Submodel) or not _sem(sm).startswith(AID_SEMANTIC_PREFIX):
            continue
        for itf in sm.submodel_element:
            if not any(s.key[0].value == OPCUA_BINDING for s in itf.supplemental_semantic_id):
                continue
            meta = _get(itf, "EndpointMetadata")
            points = []
            for p in _get(_get(itf, "InteractionMetadata"), "properties").value:
                key = _val(p, "key", p.id_short)
                kind = _val(p, "type", "number")
                if kind not in KINDS:
                    raise ValueError(f"datapoint {key}: type {kind!r} is not supported (supported: {', '.join(KINDS)})")
                nsu, idx, ident = parse_href(_val(_get(p, "forms"), "href"))
                rng = _get(p, "min_max")
                points.append(Datapoint(key, _text(_val(p, "title", key)), _val(p, "unit"), kind, nsu, ident, idx,
                                        None if rng is None or rng.min is None else float(rng.min),
                                        None if rng is None or rng.max is None else float(rng.max)))
            return Interface(_val(meta, "base"), _security(meta), tuple(points), _text(_val(itf, "title")),
                             sm.id, store, files)
    raise LookupError(f"{aasx_path}: no Asset Interfaces Description with an OPC UA interface")
