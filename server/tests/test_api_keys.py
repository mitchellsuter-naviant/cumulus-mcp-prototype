"""Minted API keys: lifecycle, authorization rules, and usage accounting."""

import pytest

import app.deps as deps
from app.api_keys import InMemoryApiKeyDirectory, hash_token, parse_key_id
from app.config import settings
from app.identities import IdentityService, InMemoryMachineIdentityDirectory
from app.service import KnowledgeService, seed_if_empty
from app.storage import InMemoryRepository
from app.users import InMemoryUserDirectory


@pytest.fixture()
def client(_client):
    deps._service = KnowledgeService(InMemoryRepository())
    seed_if_empty(deps._service, settings.seed_dir)
    deps._user_directory = InMemoryUserDirectory()
    deps._api_key_directory = InMemoryApiKeyDirectory()
    deps._identity_service = IdentityService(
        deps._user_directory, InMemoryMachineIdentityDirectory()
    )
    return _client


@pytest.fixture()
def owner(monkeypatch):
    """Authenticate as the legacy root credential (owner)."""
    monkeypatch.setattr(settings, "auth_backend", "firebase")
    monkeypatch.setattr(settings, "admin_api_key", "root-sekret")
    return {"X-API-Key": "root-sekret"}


_names = iter(f"Agent {n}" for n in range(1, 1000))


def _identity(client, owner, role="user", **kw):
    """An agent identity to hang a key off; display names must be unique."""
    payload = {"display_name": next(_names), "role": role, **kw}
    resp = client.post("/api/v1/identities", json=payload, headers=owner)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _mint(client, owner, role="user", identity_uid=None, **kw):
    if identity_uid is None:
        identity_uid = _identity(client, owner, role=role)["uid"]
    payload = {"name": "Claude Desktop", "identity_uid": identity_uid, **kw}
    resp = client.post("/api/v1/api-keys", json=payload, headers=owner)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_mint_returns_token_once_and_never_again(client, owner):
    minted = _mint(client, owner)
    token = minted["token"]
    assert parse_key_id(token) == minted["key"]["id"]
    # The masked display must not leak the secret.
    assert token not in minted["key"]["display"]

    listed = client.get("/api/v1/api-keys", headers=owner).json()
    assert [k["id"] for k in listed] == [minted["key"]["id"]]
    assert "token" not in listed[0]
    assert "token_hash" not in listed[0]


def test_mint_response_carries_pasteable_mcp_config(client, owner):
    minted = _mint(client, owner, name="Cursor")
    cfg = minted["mcp_config"]["servers"]["cumulus-knowledge"]
    assert cfg["url"].endswith("/mcp/")
    assert cfg["headers"]["X-API-Key"] == minted["token"]


def test_secret_is_not_stored_in_the_clear(client, owner):
    minted = _mint(client, owner)
    record = deps._api_key_directory.get(minted["key"]["id"])
    assert record.token_hash == hash_token(minted["token"])
    assert minted["token"] not in record.model_dump_json()


def test_minted_key_authenticates_and_is_counted(client, owner):
    minted = _mint(client, owner)
    headers = {"X-API-Key": minted["token"]}

    assert client.get("/api/v1/assets", headers=headers).status_code == 200
    assert client.get("/api/v1/assets", headers=headers).status_code == 200

    deps._api_key_directory.flush()
    record = deps._api_key_directory.get(minted["key"]["id"])
    assert record.request_count == 2
    assert record.last_used_at is not None

    usage = client.get(
        f"/api/v1/api-keys/{minted['key']['id']}/usage", headers=owner
    ).json()
    assert sum(day["count"] for day in usage) == 2
    assert usage[0]["endpoints"]["/api/v1/assets"] == 2


def test_usage_paths_are_templated_not_per_asset(client, owner):
    minted = _mint(client, owner)
    headers = {"X-API-Key": minted["token"]}
    client.get("/api/v1/assets/expense-approval-policy", headers=headers)
    client.get("/api/v1/assets/travel-booking-guideline", headers=headers)

    usage = client.get(
        f"/api/v1/api-keys/{minted['key']['id']}/usage", headers=owner
    ).json()
    # One bucket for both, so counter cardinality does not grow with the corpus.
    assert usage[0]["endpoints"] == {"/api/v1/assets/:id": 2}


def test_user_role_key_cannot_write(client, owner):
    minted = _mint(client, owner, role="user")
    headers = {"X-API-Key": minted["token"]}
    resp = client.post(
        "/api/v1/assets",
        headers=headers,
        json={"id": "nope", "path": "a/b", "title": "T", "content": "c"},
    )
    assert resp.status_code == 403


def test_admin_role_key_can_write(client, owner):
    minted = _mint(client, owner, role="admin")
    headers = {"X-API-Key": minted["token"]}
    resp = client.post(
        "/api/v1/assets",
        headers=headers,
        json={"id": "from-key", "path": "a/b", "title": "T", "content": "c"},
    )
    assert resp.status_code == 201


def test_machine_identities_cannot_be_granted_owner(client, owner):
    resp = client.post(
        "/api/v1/identities", json={"display_name": "Root agent", "role": "owner"}, headers=owner
    )
    assert resp.status_code == 422  # rejected by the Literal["user","admin"] schema


def test_owner_identity_cannot_hold_a_key(client, owner):
    """Owner is a sign-in privilege; it must not end up in a file on disk."""
    person = client.post(
        "/api/v1/users",
        json={"email": "boss@example.com", "password": "password123", "role": "owner"},
        headers=owner,
    ).json()
    resp = client.post(
        "/api/v1/api-keys",
        json={"name": "root", "identity_uid": person["uid"]},
        headers=owner,
    )
    assert resp.status_code == 400
    assert "owner" in resp.json()["detail"].lower()


def test_mint_requires_an_existing_identity(client, owner):
    assert (
        client.post("/api/v1/api-keys", json={"name": "orphan"}, headers=owner).status_code
        == 422
    )
    resp = client.post(
        "/api/v1/api-keys",
        json={"name": "orphan", "identity_uid": "agt_nope"},
        headers=owner,
    )
    assert resp.status_code == 404


def test_key_reports_and_follows_its_identity(client, owner):
    """Authority lives on the identity, so re-scoping it re-scopes the key."""
    identity = _identity(client, owner, role="user")
    minted = _mint(client, owner, identity_uid=identity["uid"])
    headers = {"X-API-Key": minted["token"]}
    asset = {"id": "from-agent", "path": "a/b", "title": "T", "content": "c"}

    listed = client.get("/api/v1/api-keys", headers=owner).json()[0]
    assert listed["identity_uid"] == identity["uid"]
    assert listed["identity_name"] == identity["display_name"]
    assert listed["identity_kind"] == "agent"
    assert listed["role"] == "user"
    assert client.post("/api/v1/assets", headers=headers, json=asset).status_code == 403

    client.put(
        f"/api/v1/identities/{identity['uid']}/role", json={"role": "admin"}, headers=owner
    )
    assert client.post("/api/v1/assets", headers=headers, json=asset).status_code == 201
    assert client.get("/api/v1/api-keys", headers=owner).json()[0]["role"] == "admin"


def test_deleting_an_identity_revokes_and_blocks_its_keys(client, owner):
    identity = _identity(client, owner)
    minted = _mint(client, owner, identity_uid=identity["uid"])
    headers = {"X-API-Key": minted["token"]}
    assert client.get("/api/v1/assets", headers=headers).status_code == 200

    assert (
        client.delete(f"/api/v1/identities/{identity['uid']}", headers=owner).status_code
        == 204
    )
    assert client.get("/api/v1/assets", headers=headers).status_code == 401
    listed = client.get("/api/v1/api-keys", headers=owner).json()[0]
    assert listed["status"] == "revoked"


def test_keys_minted_before_identities_are_dead(client, owner):
    """The cutover: a key with no identity authenticates nothing."""
    minted = _mint(client, owner)
    record = deps._api_key_directory.get(minted["key"]["id"])
    record.identity_uid = None  # as a pre-identity key deserializes today

    assert client.get(
        "/api/v1/assets", headers={"X-API-Key": minted["token"]}
    ).status_code == 401
    assert client.get("/api/v1/api-keys", headers=owner).json()[0]["status"] == "unattributed"

    assert deps._api_key_directory.revoke_unattributed("system") == 1
    assert client.get("/api/v1/api-keys", headers=owner).json()[0]["status"] == "revoked"


def test_minted_key_cannot_manage_keys(client, owner):
    """A key that could mint keys would make revocation meaningless."""
    admin_key = _mint(client, owner, role="admin")
    headers = {"X-API-Key": admin_key["token"]}

    assert client.get("/api/v1/api-keys", headers=headers).status_code == 403
    assert (
        client.post(
            "/api/v1/api-keys",
            json={"name": "clone", "identity_uid": admin_key["key"]["identity_uid"]},
            headers=headers,
        ).status_code
        == 403
    )
    assert (
        client.delete(
            f"/api/v1/api-keys/{admin_key['key']['id']}", headers=headers
        ).status_code
        == 403
    )


def test_revoke_blocks_the_key_immediately(client, owner):
    minted = _mint(client, owner)
    headers = {"X-API-Key": minted["token"]}
    assert client.get("/api/v1/assets", headers=headers).status_code == 200

    assert (
        client.delete(f"/api/v1/api-keys/{minted['key']['id']}", headers=owner).status_code
        == 204
    )
    resp = client.get("/api/v1/assets", headers=headers)
    assert resp.status_code == 401
    assert "revoked" in resp.json()["detail"].lower()

    listed = client.get("/api/v1/api-keys", headers=owner).json()
    assert listed[0]["status"] == "revoked"
    assert listed[0]["revoked_by"]


def test_revoked_key_is_rejected_at_the_mcp_transport(client, owner):
    minted = _mint(client, owner)
    headers = {
        "X-API-Key": minted["token"],
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    assert client.post("/mcp/", headers=headers, json=body).status_code != 401

    client.delete(f"/api/v1/api-keys/{minted['key']['id']}", headers=owner)
    assert client.post("/mcp/", headers=headers, json=body).status_code == 401


def test_expired_key_is_rejected(client, owner, monkeypatch):
    from datetime import timedelta

    import app.api_keys as api_keys

    minted = _mint(client, owner, expires_in_days=1)
    headers = {"X-API-Key": minted["token"]}
    assert client.get("/api/v1/assets", headers=headers).status_code == 200

    record = deps._api_key_directory.get(minted["key"]["id"])
    record.expires_at = api_keys.utcnow() - timedelta(seconds=1)
    assert client.get("/api/v1/assets", headers=headers).status_code == 401
    assert client.get("/api/v1/api-keys", headers=owner).json()[0]["status"] == "expired"


def test_unknown_or_malformed_key_is_rejected_not_downgraded(client, monkeypatch):
    """AUTH_BACKEND=none must not turn a bad key into the dev owner principal."""
    monkeypatch.setattr(settings, "auth_backend", "none")
    monkeypatch.setattr(settings, "admin_api_key", None)
    for bad in ("cumk_deadbeefcafe_nope", "garbage", "cumk_x"):
        resp = client.get("/api/v1/assets", headers={"X-API-Key": bad})
        assert resp.status_code == 401, bad


def test_revoke_and_usage_404_on_unknown_key(client, owner):
    assert client.delete("/api/v1/api-keys/does-not-exist", headers=owner).status_code == 404
    assert client.get("/api/v1/api-keys/does-not-exist/usage", headers=owner).status_code == 404
