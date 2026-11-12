import asyncio
import base64
import hashlib
import json
import secrets
import shutil
import urllib.request

import pytest
from conftest import PLANT_AASX, PLANT_VALUES, TANK_AASX, USER, Running, plant_server, tank_server

import aidconnect
from aidconnect.gateway import BASE_PATH

TANK = "opc.tcp://127.0.0.1:48431/tank/"
PLANT = "opc.tcp://127.0.0.1:48440/heating-plant/"


# ---------------------------------------------------------------- reading the AAS (no connection)
def test_open_asset_interface():
    itf = aidconnect.read_interface(TANK_AASX)
    assert itf.endpoint == TANK and itf.title == "Tank B101"
    assert not itf.security.secure_channel and not itf.security.needs_login
    kinds = {p.name: p.kind for p in itf.points}
    assert kinds == {"Level": "number", "PumpOn": "boolean", "Cycles": "integer", "Name": "string"}
    level = next(p for p in itf.points if p.name == "Level")
    assert (level.unit, level.minimum, level.maximum, level.namespace) == ("MTR", 0.0, 4.0, "urn:example:tank:ua")


def test_secured_asset_interface():
    itf = aidconnect.read_interface(PLANT_AASX)
    assert (itf.security.mode, itf.security.policy, itf.security.user_token) == ("SignAndEncrypt", "Basic256Sha256", "UserName")
    assert {p.name for p in itf.points} == set(PLANT_VALUES)


def test_file_without_interface_description(tmp_path):
    empty = tmp_path / "empty.aasx"
    from basyx.aas import model
    from basyx.aas.adapter import aasx
    store = model.DictIdentifiableStore()
    store.add(model.AssetAdministrationShell(model.AssetInformation(model.AssetKind.INSTANCE, "urn:x:asset"), "urn:x:aas"))
    with aasx.AASXWriter(str(empty)) as w:
        w.write_aas("urn:x:aas", store, aasx.DictSupplementaryFileContainer(), write_json=True)
    with pytest.raises(LookupError):
        aidconnect.read_interface(empty)


@pytest.mark.parametrize("href, expected", [
    ("nsu=urn:a:b;s=TIC101.PV", ("urn:a:b", None, "TIC101.PV")),
    ("nsu=http://x/y/;i=42", ("http://x/y/", None, 42)),
    ('ns=3;s="DB"."tag"', ("", 3, '"DB"."tag"')),          # the way an S7-1500 names its nodes
    ("i=2258", ("", 0, 2258)),
])
def test_parse_href(href, expected):
    assert aidconnect.parse_href(href) == expected


def test_parse_href_rejects_other_schemes():
    with pytest.raises(ValueError):
        aidconnect.parse_href("http://example.com/value")


# ---------------------------------------------------------------- connecting
def test_connect_to_open_asset_with_nothing_but_the_file():
    async def go():
        async with Running(tank_server, TANK):
            async with aidconnect.connect(TANK_AASX) as session:
                return await session.read()
    values = asyncio.run(go())
    assert values == {"Level": 2.5, "PumpOn": True, "Cycles": 7, "Name": "B101"}
    assert [type(v) for v in values.values()] == [float, bool, int, str]


async def _plant(tmp_path, attempt, trust=True):
    password = secrets.token_urlsafe(16)
    cert, key = await aidconnect.ensure_client_certificate(tmp_path / "client")
    if trust:
        (tmp_path / "pki/trusted").mkdir(parents=True)
        shutil.copy(cert, tmp_path / "pki/trusted" / cert.name)
    async with Running(plant_server, PLANT, tmp_path / "pki", password):
        good = dict(username=USER, password=password, client_cert=cert, client_key=key,
                    server_cert=tmp_path / "pki/server_cert.der", endpoint=PLANT)
        return await attempt(good)


def test_connect_to_secured_asset(tmp_path):
    async def attempt(good):
        async with aidconnect.connect(PLANT_AASX, **good) as session:
            return await session.read()
    assert asyncio.run(_plant(tmp_path, attempt)) == PLANT_VALUES


@pytest.mark.parametrize("case, error", [("wrong_password", "BadUserAccessDenied"),
                                         ("untrusted_client", "BadCertificateUntrusted"),
                                         ("wrong_server", "TimeoutError")])
def test_secured_asset_refuses(tmp_path, case, error):
    async def attempt(good):
        bad = dict(good)
        if case == "wrong_password":
            bad["password"] = "wrong"
        if case == "wrong_server":   # the certificate of some other server: the real one cannot answer
            from asyncua.crypto import cert_gen
            other = tmp_path / "other.der"
            await cert_gen.setup_self_signed_certificate(tmp_path / "other.pem", other, "urn:other", "other", [], {})
            bad["server_cert"] = other
        try:
            async with aidconnect.connect(PLANT_AASX, **bad) as session:
                await session.read()
            return "accepted"
        except Exception as e:  # the kind of refusal is what the test is about
            return f"{type(e).__name__}: {e}"
    outcome = asyncio.run(_plant(tmp_path, attempt, trust=case != "untrusted_client"))
    assert error in outcome, outcome


@pytest.mark.parametrize("missing, word", [("server_cert", "server_cert"), ("client_cert", "client_cert"),
                                           ("password", "password")])
def test_missing_setup_is_a_configuration_error_before_any_connection(missing, word):
    options = dict(username=USER, password="x", client_cert="c.der", client_key="c.pem", server_cert=PLANT_AASX)
    options[missing] = None

    async def go():
        async with aidconnect.connect(PLANT_AASX, **options):
            pass
    with pytest.raises(aidconnect.ConfigurationError, match=word):
        asyncio.run(go())


# ---------------------------------------------------------------- gateway
def _rest(port, gw) -> dict:
    sm_id = base64.urlsafe_b64encode(gw.live.id.encode()).decode().rstrip("=")
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{BASE_PATH}/submodels/{sm_id}", timeout=10) as r:
        return {e["idShort"]: e.get("value") for e in json.loads(r.read())["submodelElements"]}


def test_gateway_publishes_live_values_and_reconnects():
    async def go():
        tank = Running(tank_server, TANK)
        await tank.start()
        gw = aidconnect.Gateway(TANK_AASX, period=0.2, log=lambda _: None)
        rest = gw.serve_in_thread(48442)
        connected = asyncio.Event()
        task = asyncio.create_task(gw.run(seconds=60, connected=connected))
        try:
            await asyncio.wait_for(connected.wait(), 15)
            first = await asyncio.to_thread(_rest, 48442, gw)
            await tank.stop()                               # the asset goes away
            for _ in range(150):
                if not connected.is_set():
                    break
                await asyncio.sleep(0.1)
            down = await asyncio.to_thread(_rest, 48442, gw)
            await tank.start()                              # and comes back
            await asyncio.wait_for(connected.wait(), 30)
            back = await asyncio.to_thread(_rest, 48442, gw)
        finally:
            rest.shutdown()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await tank.stop()
        return first, down, back
    first, down, back = asyncio.run(go())
    assert first["ConnectionState"].startswith("connected")
    assert (float(first["Level"]), int(first["Cycles"]), first["Name"]) == (2.5, 7, "B101")
    assert down["ConnectionState"].startswith("disconnected")
    assert back["ConnectionState"].startswith("connected") and back["LastUpdate"] > first["LastUpdate"]


def test_gateway_stops_on_configuration_error():
    gw = aidconnect.Gateway(PLANT_AASX, log=lambda _: None)      # secured asset, nothing provided
    with pytest.raises(aidconnect.ConfigurationError):
        asyncio.run(gw.run(seconds=5))


# ---------------------------------------------------------------- where the AAS file may come from
def test_remote_file_must_be_https_and_pinned():
    with pytest.raises(ValueError, match="https"):
        aidconnect.fetch_aasx("http://example.com/asset.aasx", "00" * 32)
    with pytest.raises(ValueError, match="sha256"):
        aidconnect.fetch_aasx("https://example.com/asset.aasx")
    good = hashlib.sha256(TANK_AASX.read_bytes()).hexdigest()
    assert aidconnect.fetch_aasx(str(TANK_AASX), good) == TANK_AASX
    with pytest.raises(ValueError, match="SHA-256"):
        aidconnect.fetch_aasx(str(TANK_AASX), "00" * 32)
