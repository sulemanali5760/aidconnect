"""Asset Interfaces Mapping Configuration (IDTA 02027, version 1.0): where each datapoint's value goes.

An AIMC submodel holds MappingConfigurations. Each points at one interface of an Asset Interfaces
Description (InterfaceReference) and lists MappingSourceSinkRelations: `first` references a datapoint of
that interface, `second` the Property in some submodel that shall carry its value. The gateway is then
what the template calls the Data Mapping Processor. Only the direction asset -> AAS exists in 1.0.
"""
from basyx.aas import model

from aidconnect.aid import Interface

AIMC_SEMANTIC_PREFIX = "https://admin-shell.io/idta/AssetInterfacesMappingConfiguration/"
_SAMPLE = {"number": 0.5, "integer": 1, "boolean": True, "string": "x"}


def _sem(e) -> str:
    return e.semantic_id.key[0].value if e.semantic_id else ""


def _child(coll, id_short):
    for e in (coll.submodel_element if isinstance(coll, model.Submodel) else coll.value):
        if e.id_short == id_short:
            return e
    return None


def _resolve(ref, store, what: str, where: str):
    try:
        return ref.resolve(store)
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        raise ValueError(f"AIMC {where}: {what} cannot be resolved in this AAS ({e})") from None


def read_mappings(interface: Interface) -> list[tuple[str, model.Property]]:
    """(datapoint name, target Property) for every relation of every MappingConfiguration that refers to
    this OPC UA interface. Configurations for other interfaces are left alone. Raises ValueError for a
    relation that cannot be followed: a mapping that silently does nothing is worse than none."""
    points = {id(p.element): p for p in interface.points}
    store, out = interface.store, []
    for sm in store:
        if not isinstance(sm, model.Submodel) or not _sem(sm).startswith(AIMC_SEMANTIC_PREFIX):
            continue
        configs = _child(sm, "MappingConfigurations")
        for n, cfg in enumerate(configs.value if configs is not None else (), 1):
            where = f"{sm.id_short} configuration {n}"
            itf_ref = _child(cfg, "InterfaceReference")
            if itf_ref is None or itf_ref.value is None:
                raise ValueError(f"AIMC {where}: no InterfaceReference")
            if _resolve(itf_ref.value, store, "InterfaceReference", where) is not interface.element:
                continue
            relations = _child(cfg, "MappingSourceSinkRelations")
            for m, rel in enumerate(relations.value if relations is not None else (), 1):
                at = f"{where} relation {m}"
                source = _resolve(rel.first, store, "the source (first)", at)
                sink = _resolve(rel.second, store, "the sink (second)", at)
                if id(source) not in points:
                    raise ValueError(f"AIMC {at}: the source is not a datapoint of interface {interface.title!r}")
                if not isinstance(sink, model.Property):
                    raise ValueError(f"AIMC {at}: the sink must be a Property, it is a {type(sink).__name__}")
                point = points[id(source)]
                try:
                    model.datatypes.trivial_cast(_SAMPLE[point.kind], sink.value_type)
                except (TypeError, ValueError):
                    raise ValueError(f"AIMC {at}: datapoint {point.name} is a {point.kind}, but sink "
                                     f"{sink.id_short} is {sink.value_type.__name__}") from None
                out.append((point.name, sink))
    return out
