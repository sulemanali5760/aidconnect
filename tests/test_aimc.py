"""IDTA 02027 (AIMC 1.0): the gateway writes each mapped datapoint into the Property the mapping names."""
import asyncio

import pytest
from basyx.aas import model
from basyx.aas.adapter import aasx
from conftest import TANK_AASX, Running, tank_server

import aidconnect
from aidconnect.aimc import read_mappings

TANK = "opc.tcp://127.0.0.1:48451/tank/"
AIMC = "https://admin-shell.io/idta/AssetInterfacesMappingConfiguration/1/0/"


def _sem(value):
    return model.ExternalReference((model.Key(model.KeyTypes.GLOBAL_REFERENCE, value),))


def tank_with_aimc(tmp_path, relations, sink_types=None):
    """tank.aasx plus a 'Monitoring' submodel and an AIMC whose relations map AID datapoint -> Monitoring
    property. relations: [(datapoint key, sink idShort)]."""
    sink_types = sink_types or {}
    store, files = model.DictIdentifiableStore(), aasx.DictSupplementaryFileContainer()
    with aasx.AASXReader(str(TANK_AASX)) as r:
        r.read_into(store, files)
    itf = aidconnect.read_interface(TANK_AASX)
    aid = store.get_item(itf.submodel_id)
    opcua = aid.get_referable("InterfaceOPCUA")
    props = opcua.get_referable("InteractionMetadata").get_referable("properties")
    monitoring = model.Submodel("urn:example:tank:sm:monitoring", id_short="Monitoring", submodel_element=[
        model.Property("TankLevel", sink_types.get("TankLevel", model.datatypes.Double)),
        model.Property("PumpRunning", model.datatypes.Boolean)])
    rels = model.SubmodelElementList(
        "MappingSourceSinkRelations", model.RelationshipElement,
        semantic_id=_sem(AIMC + "MappingSourceSinkRelations"),
        semantic_id_list_element=_sem(AIMC + "MappingSourceSinkRelation"))
    config = model.SubmodelElementCollection(None, semantic_id=_sem(AIMC + "MappingConfiguration"), value=[
        model.ReferenceElement("InterfaceReference", model.ModelReference.from_referable(opcua),
                               semantic_id=_sem(AIMC + "InterfaceReference")),
        rels])
    configs = model.SubmodelElementList(
        "MappingConfigurations", model.SubmodelElementCollection, value=[config],
        semantic_id=_sem(AIMC + "MappingConfigurations"),
        semantic_id_list_element=_sem(AIMC + "MappingConfiguration"))
    store.add(monitoring)
    aimc = model.Submodel("urn:example:tank:sm:aimc", id_short="AssetInterfacesMappingConfiguration",
                          semantic_id=_sem(AIMC + "Submodel"), submodel_element=[configs])
    store.add(aimc)
    for key, sink in relations:
        first = props.get_referable(key) if key in [p.id_short for p in props.value] else monitoring.get_referable("PumpRunning")
        rels.value.append(model.RelationshipElement(
            None, model.ModelReference.from_referable(first),
            model.ModelReference.from_referable(monitoring.get_referable(sink)),
            semantic_id=_sem(AIMC + "MappingSourceSinkRelation")))
    shell = next(o for o in store if isinstance(o, model.AssetAdministrationShell))
    shell.submodel.update({model.ModelReference.from_referable(monitoring), model.ModelReference.from_referable(aimc)})
    path = tmp_path / "tank_aimc.aasx"
    with aasx.AASXWriter(str(path)) as w:
        w.write_aas(shell.id, store, files)
    return path


def test_mappings_are_read_from_the_file(tmp_path):
    path = tank_with_aimc(tmp_path, [("Level", "TankLevel"), ("PumpOn", "PumpRunning")])
    pairs = [(name, sink.id_short) for name, sink in read_mappings(aidconnect.read_interface(path))]
    assert pairs == [("Level", "TankLevel"), ("PumpOn", "PumpRunning")]


def test_a_file_without_aimc_has_no_mappings():
    assert read_mappings(aidconnect.read_interface(TANK_AASX)) == []


def test_sink_of_the_wrong_type_is_refused_when_loading(tmp_path):
    path = tank_with_aimc(tmp_path, [("Level", "TankLevel")], {"TankLevel": model.datatypes.Int})
    with pytest.raises(ValueError, match="Level is a number, but sink TankLevel is Int"):
        aidconnect.Gateway(path, log=lambda _: None)


def test_source_that_is_not_a_datapoint_is_refused(tmp_path):
    path = tank_with_aimc(tmp_path, [("NotADatapoint", "TankLevel")])
    with pytest.raises(ValueError, match="not a datapoint"):
        aidconnect.Gateway(path, log=lambda _: None)


def test_gateway_writes_live_values_into_the_mapped_properties(tmp_path):
    path = tank_with_aimc(tmp_path, [("Level", "TankLevel"), ("PumpOn", "PumpRunning")])

    async def go():
        async with Running(tank_server, TANK):
            gw = aidconnect.Gateway(path, period=0.1, log=lambda _: None, endpoint=TANK)
            connected = asyncio.Event()
            task = asyncio.create_task(gw.run(seconds=20, connected=connected))
            try:
                await asyncio.wait_for(connected.wait(), 15)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            monitoring = gw.store.get_item("urn:example:tank:sm:monitoring")
            return monitoring.get_referable("TankLevel").value, monitoring.get_referable("PumpRunning").value
    assert asyncio.run(go()) == (2.5, True)
