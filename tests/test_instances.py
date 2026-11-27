"""Beacon Local installations: registration, leased license, remote revocation with wipe, offboarding and the
commercial edition, which does not serve without a valid registration."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402
import yaml  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from core import config as _config  # noqa: E402
from core import edition, instance  # noqa: E402
from hub.app import Hub, create_app  # noqa: E402
from hub.security import sign_token  # noqa: E402


@pytest.fixture
def hub_client(tmp_path):
    hub = Hub(tmp_path / "hub")
    c = TestClient(create_app(hub))
    res = c.post("/signup", json={"org_name": "Acme", "name": "Ana Admin", "email": "ana@acme.cl", "password": "secreta123"})
    c.admin = {"Authorization": f"Bearer {res.json()['session']}"}
    c.hub = hub
    return c


@pytest.fixture
def local(tmp_path, monkeypatch):
    """A fake Beacon Local installation in temporary folders (config, credentials and data)."""
    data = tmp_path / "data"
    (data / "api").mkdir(parents=True)
    (data / "api" / "chroma.bin").write_text("índice")
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.safe_dump({"projects": [{"id": "api", "name": "API", "repo_path": str(tmp_path / "repo")}]}))
    creds = tmp_path / "credentials.yaml"
    creds.write_text("api: token-secreto\n")
    (tmp_path / "repo").mkdir()
    monkeypatch.setattr(_config, "CONFIG_PATH", cfg)
    monkeypatch.setattr(_config, "CREDENTIALS_PATH", creds)
    monkeypatch.setattr(instance, "DATA_ROOT", data)
    monkeypatch.setattr(instance, "STATE_FILE", data / ".instance_state.json")
    removed = []

    def fake_unregister(project_id, purge_data=False):
        removed.append(project_id)
        import shutil
        shutil.rmtree(data / project_id, ignore_errors=True)

    import core.services as services
    monkeypatch.setattr(services, "unregister_project", fake_unregister)
    return tmp_path, removed


def _register(hub_client, personal=False, headers=None):
    res = hub_client.post("/instances", json={"name": "Servidor del equipo", "personal": personal}, headers=headers or hub_client.admin)
    assert res.status_code == 201, res.text
    return res.json()


def _beat(hub_client, reg):
    body = hub_client.post("/instances/heartbeat", json={"instance_id": reg["instance_id"], "secret": reg["secret"]}).json()
    return instance.apply_status(body["status"], body["license"], hub_client.hub.public_key)


def test_heartbeat_gives_a_short_lease_and_rejects_a_wrong_secret(hub_client, local):
    reg = _register(hub_client)
    instance.save_credentials(reg["instance_id"], reg["secret"], "http://hub")
    state = _beat(hub_client, reg)
    assert not state["revoked"] and 2 * 86400 < state["lease_exp"] - time.time() <= 3 * 86400
    instance.check()  # registered with a valid lease: may serve
    assert hub_client.post("/instances/heartbeat", json={"instance_id": reg["instance_id"], "secret": "otro"}).status_code == 401


def test_revoking_an_installation_wipes_what_beacon_keeps(hub_client, local):
    tmp, removed = local
    reg = _register(hub_client)
    instance.save_credentials(reg["instance_id"], reg["secret"], "http://hub")
    _beat(hub_client, reg)
    assert hub_client.post(f"/instances/{reg['instance_id']}/revoke", headers=hub_client.admin).status_code == 200
    state = _beat(hub_client, reg)
    assert state["revoked"] and state["wiped"]
    assert removed == ["api"] and not (tmp / "data" / "api").exists()
    assert not (tmp / "credentials.yaml").exists()
    assert (tmp / "repo").exists()  # the person's own repository folder is never deleted
    with pytest.raises(instance.InstanceBlockedError):
        instance.check()
    events = [e["action"] for e in hub_client.get("/audit", headers=hub_client.admin).json()["events"]]
    assert "instalación revocada" in events and "instalación borrada a distancia" in events


def test_deactivating_a_person_revokes_their_personal_installation_and_roles(hub_client, local):
    code = hub_client.post("/invitations", json={"email": "diego@acme.cl"}, headers=hub_client.admin).json()["code"]
    res = hub_client.post("/invitations/accept", json={"code": code, "name": "Diego", "password": "secreta123"})
    dev_id, dev_h = res.json()["user"]["sub"], {"Authorization": f"Bearer {res.json()['session']}"}
    hub_client.post("/projects", json={"id": "api", "name": "API"}, headers=hub_client.admin)
    hub_client.put(f"/projects/api/members/{dev_id}", json={"role": "developer"}, headers=hub_client.admin)
    assert hub_client.post("/instances", json={"name": "x", "personal": False}, headers=dev_h).status_code == 403
    reg = _register(hub_client, personal=True, headers=dev_h)
    instance.save_credentials(reg["instance_id"], reg["secret"], "http://hub")
    _beat(hub_client, reg)
    hub_client.patch(f"/users/{dev_id}", json={"active": False}, headers=hub_client.admin)
    assert _beat(hub_client, reg)["wiped"] is True
    assert hub_client.get("/projects", headers=hub_client.admin).json()["projects"][0]["members"] == []
    # The deactivated person can no longer renew the session either.
    assert hub_client.post("/auth/refresh", headers=dev_h).status_code == 401


def test_an_expired_lease_blocks_the_installation(hub_client, local):
    reg = _register(hub_client)
    instance.save_credentials(reg["instance_id"], reg["secret"], "http://hub")
    stale = sign_token(hub_client.hub.key, {"typ": "instance", "instance": reg["instance_id"], "org": 1, "revoked": False,
                                            "iat": 0, "exp": int(time.time()) + 5})
    instance.apply_status(stale, None, hub_client.hub.public_key)
    state = instance.read_state()
    state["lease_exp"] = time.time() - 1
    instance._write_state(state)
    with pytest.raises(instance.InstanceBlockedError, match="venció"):
        instance.check()


def test_the_commercial_edition_does_not_serve_without_registration(local, monkeypatch):
    monkeypatch.setattr(edition, "EDITION", "comercial")
    with pytest.raises(instance.InstanceBlockedError, match="registrarse"):
        instance.check()
    import core.api as api
    res = TestClient(api.app).get("/projects")
    assert res.status_code == 403 and res.json()["instance_blocked"] is True
    assert TestClient(api.app).get("/healthz").status_code == 200
