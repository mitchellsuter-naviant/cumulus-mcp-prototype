"""Teams: ownership of knowledge, and the second axis of authorization.

The interesting cases here are the boundaries — what a team admin can do that a
plain user cannot, and what they still cannot do that only a platform admin can.
"""

import pytest

import app.deps as deps
from app.api_keys import InMemoryApiKeyDirectory
from app.config import settings
from app.identities import IdentityService, InMemoryMachineIdentityDirectory
from app.service import KnowledgeService, seed_if_empty
from app.storage import InMemoryRepository
from app.teams import InMemoryTeamDirectory, TeamService, bootstrap_teams_from_assets
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
    deps._team_service = TeamService(InMemoryTeamDirectory())
    return _client


@pytest.fixture()
def owner(monkeypatch):
    monkeypatch.setattr(settings, "auth_backend", "firebase")
    monkeypatch.setattr(settings, "admin_api_key", "root-sekret")
    return {"X-API-Key": "root-sekret"}


def _team(client, owner, slug="platform-eng", **kw):
    resp = client.post(
        "/api/v1/teams", json={"slug": slug, "name": slug.title(), **kw}, headers=owner
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _person(client, owner, email, role="user"):
    resp = client.post(
        "/api/v1/users",
        json={"email": email, "password": "password123", "role": role},
        headers=owner,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.fixture()
def act_as(_client):
    """Run subsequent requests as a signed-in person of our choosing.

    The other suites impersonate by minting a key, but a key is deliberately
    never a team admin (teams.py), so team authority has to be exercised as a
    human. Overriding the dependency is the only way to be a plain `user` who
    signed in. Do the setup as `owner` first — the override applies to every
    request once installed.
    """
    from app.auth import Principal, get_principal
    from app.main import app

    def _set(uid, email="person@example.com", role="user"):
        app.dependency_overrides[get_principal] = lambda: Principal(
            uid=uid, email=email, role=role
        )

    yield _set
    app.dependency_overrides.pop(get_principal, None)


# ---- registry ----


def test_bootstrap_imports_teams_from_existing_asset_owners(client, owner):
    """The corpus already said `platform-eng`; that becomes a real team."""
    owners = [a.owner for a in deps._service.repo.all()]
    created = bootstrap_teams_from_assets(deps._team_service, owners)

    slugs = {t["slug"] for t in client.get("/api/v1/teams", headers=owner).json()}
    assert created > 0
    assert "platform-eng" in slugs
    # And it is idempotent — a second pass finds nothing to do.
    assert bootstrap_teams_from_assets(deps._team_service, owners) == 0


def test_team_reports_the_assets_it_owns(client, owner):
    bootstrap_teams_from_assets(
        deps._team_service, [a.owner for a in deps._service.repo.all()]
    )
    teams = {t["slug"]: t for t in client.get("/api/v1/teams", headers=owner).json()}
    assert teams["platform-eng"]["asset_count"] >= 1

    assets = client.get("/api/v1/teams/platform-eng/assets", headers=owner).json()
    assert assets
    assert all(a["owner"] == "platform-eng" for a in assets)


def test_deleting_a_team_that_owns_assets_is_refused(client, owner):
    bootstrap_teams_from_assets(
        deps._team_service, [a.owner for a in deps._service.repo.all()]
    )
    resp = client.delete("/api/v1/teams/platform-eng", headers=owner)
    assert resp.status_code == 409
    assert "still owns" in resp.json()["detail"]


def test_duplicate_slug_is_refused(client, owner):
    _team(client, owner)
    resp = client.post(
        "/api/v1/teams", json={"slug": "platform-eng", "name": "Dup"}, headers=owner
    )
    assert resp.status_code == 409


# ---- membership ----


def test_membership_add_promote_and_remove(client, owner):
    _team(client, owner)
    person = _person(client, owner, "jake@example.com")

    added = client.put(
        f"/api/v1/teams/platform-eng/members/{person['uid']}",
        json={"team_role": "member"},
        headers=owner,
    ).json()
    assert [m["identity_uid"] for m in added["members"]] == [person["uid"]]
    assert added["members"][0]["team_role"] == "member"
    # The roster carries enough identity detail to render without a second call.
    assert added["members"][0]["email"] == "jake@example.com"
    assert added["members"][0]["kind"] == "person"

    promoted = client.put(
        f"/api/v1/teams/platform-eng/members/{person['uid']}",
        json={"team_role": "admin"},
        headers=owner,
    ).json()
    assert promoted["members"][0]["team_role"] == "admin"

    removed = client.delete(
        f"/api/v1/teams/platform-eng/members/{person['uid']}", headers=owner
    ).json()
    assert removed["members"] == []


def test_a_machine_cannot_administer_a_team(client, owner):
    """Same principle that caps machine identities below owner."""
    _team(client, owner)
    agent = client.post(
        "/api/v1/identities", json={"display_name": "Brief Agent"}, headers=owner
    ).json()

    as_member = client.put(
        f"/api/v1/teams/platform-eng/members/{agent['uid']}",
        json={"team_role": "member"},
        headers=owner,
    )
    assert as_member.status_code == 200

    as_admin = client.put(
        f"/api/v1/teams/platform-eng/members/{agent['uid']}",
        json={"team_role": "admin"},
        headers=owner,
    )
    assert as_admin.status_code == 400
    assert "Only a person" in as_admin.json()["detail"]


def test_deleting_an_identity_clears_its_memberships(client, owner):
    _team(client, owner)
    person = _person(client, owner, "leaver@example.com")
    client.put(
        f"/api/v1/teams/platform-eng/members/{person['uid']}",
        json={"team_role": "member"},
        headers=owner,
    )

    client.delete(f"/api/v1/identities/{person['uid']}", headers=owner)

    team = client.get("/api/v1/teams/platform-eng", headers=owner).json()
    assert team["members"] == []


def test_unknown_identity_cannot_be_added(client, owner):
    _team(client, owner)
    resp = client.put(
        "/api/v1/teams/platform-eng/members/nobody",
        json={"team_role": "member"},
        headers=owner,
    )
    assert resp.status_code == 404


# ---- the two axes: what team authority does and does not buy ----


def test_team_admin_manages_their_own_team_only(client, owner, act_as):
    _team(client, owner, "platform-eng")
    _team(client, owner, "finance-ops")
    jake = _person(client, owner, "jake@example.com")
    mara = _person(client, owner, "mara@example.com")
    client.put(
        f"/api/v1/teams/platform-eng/members/{jake['uid']}",
        json={"team_role": "admin"},
        headers=owner,
    )

    act_as(jake["uid"], "jake@example.com", role="user")

    mine = client.put(
        f"/api/v1/teams/platform-eng/members/{mara['uid']}",
        json={"team_role": "member"},
    )
    assert mine.status_code == 200

    theirs = client.put(
        f"/api/v1/teams/finance-ops/members/{mara['uid']}",
        json={"team_role": "member"},
    )
    assert theirs.status_code == 403


def test_team_admin_writes_only_what_their_team_owns(client, owner, act_as):
    _team(client, owner, "platform-eng")
    _team(client, owner, "finance-ops")
    jake = _person(client, owner, "jake@example.com")
    client.put(
        f"/api/v1/teams/platform-eng/members/{jake['uid']}",
        json={"team_role": "admin"},
        headers=owner,
    )

    act_as(jake["uid"], "jake@example.com", role="user")

    ours = client.post(
        "/api/v1/assets",
        json={
            "id": "runbook",
            "path": "engineering/runbook",
            "title": "Runbook",
            "content": "x",
            "owner": "platform-eng",
        },
    )
    assert ours.status_code == 201, ours.text

    theirs = client.post(
        "/api/v1/assets",
        json={
            "id": "budget",
            "path": "finance/budget",
            "title": "Budget",
            "content": "x",
            "owner": "finance-ops",
        },
    )
    assert theirs.status_code == 403

    # Nor may they move one of their own documents into another team.
    moved = client.put("/api/v1/assets/runbook", json={"owner": "finance-ops"})
    assert moved.status_code == 403


def test_team_admin_cannot_grant_global_authority(client, owner, act_as):
    """The escalation path this model must not have."""
    _team(client, owner, "platform-eng")
    jake = _person(client, owner, "jake@example.com")
    mara = _person(client, owner, "mara@example.com")
    client.put(
        f"/api/v1/teams/platform-eng/members/{jake['uid']}",
        json={"team_role": "admin"},
        headers=owner,
    )

    act_as(jake["uid"], "jake@example.com", role="user")

    promote = client.put(
        f"/api/v1/identities/{mara['uid']}/role", json={"role": "admin"}
    )
    assert promote.status_code == 403
    # Not even for themselves.
    assert (
        client.put(
            f"/api/v1/identities/{jake['uid']}/role", json={"role": "admin"}
        ).status_code
        in (400, 403)
    )


def test_team_admin_registers_agents_and_mints_their_keys(client, owner, act_as):
    """The self-service the model exists to provide."""
    _team(client, owner, "platform-eng")
    jake = _person(client, owner, "jake@example.com")
    client.put(
        f"/api/v1/teams/platform-eng/members/{jake['uid']}",
        json={"team_role": "admin"},
        headers=owner,
    )

    act_as(jake["uid"], "jake@example.com", role="user")

    agent = client.post(
        "/api/v1/identities",
        json={"display_name": "Runbook Agent", "team": "platform-eng"},
    )
    assert agent.status_code == 201, agent.text
    uid = agent.json()["uid"]

    minted = client.post(
        "/api/v1/api-keys", json={"name": "runbook client", "identity_uid": uid}
    )
    assert minted.status_code == 201, minted.text

    key_id = minted.json()["key"]["id"]
    assert client.get(f"/api/v1/api-keys/{key_id}/usage").status_code == 200
    assert client.delete(f"/api/v1/api-keys/{key_id}").status_code == 204


def test_team_admin_cannot_mint_a_key_carrying_admin_authority(client, owner, act_as):
    """Team authority never lifts the ceiling on what a credential may carry."""
    _team(client, owner, "platform-eng")
    jake = _person(client, owner, "jake@example.com")
    boss = _person(client, owner, "boss@example.com", role="admin")
    for uid, role in ((jake["uid"], "admin"), (boss["uid"], "member")):
        client.put(
            f"/api/v1/teams/platform-eng/members/{uid}",
            json={"team_role": role},
            headers=owner,
        )

    act_as(jake["uid"], "jake@example.com", role="user")

    resp = client.post(
        "/api/v1/api-keys",
        json={"name": "escalation", "identity_uid": boss["uid"]},
    )
    assert resp.status_code == 403


def test_team_admin_sees_only_their_teams_keys(client, owner, act_as):
    _team(client, owner, "platform-eng")
    _team(client, owner, "finance-ops")
    jake = _person(client, owner, "jake@example.com")
    client.put(
        f"/api/v1/teams/platform-eng/members/{jake['uid']}",
        json={"team_role": "admin"},
        headers=owner,
    )

    mine = client.post(
        "/api/v1/identities", json={"display_name": "Ours"}, headers=owner
    ).json()
    theirs = client.post(
        "/api/v1/identities", json={"display_name": "Theirs"}, headers=owner
    ).json()
    client.put(
        f"/api/v1/teams/platform-eng/members/{mine['uid']}",
        json={"team_role": "member"},
        headers=owner,
    )
    client.put(
        f"/api/v1/teams/finance-ops/members/{theirs['uid']}",
        json={"team_role": "member"},
        headers=owner,
    )
    for uid in (mine["uid"], theirs["uid"]):
        client.post(
            "/api/v1/api-keys",
            json={"name": f"key-{uid}", "identity_uid": uid},
            headers=owner,
        )

    assert len(client.get("/api/v1/api-keys", headers=owner).json()) == 2

    act_as(jake["uid"], "jake@example.com", role="user")
    visible = client.get("/api/v1/api-keys").json()
    assert [k["identity_uid"] for k in visible] == [mine["uid"]]


def test_a_plain_user_with_no_team_writes_nothing(client, owner, act_as):
    _team(client, owner, "platform-eng")
    nobody = _person(client, owner, "nobody@example.com")

    act_as(nobody["uid"], "nobody@example.com", role="user")

    assert client.get("/api/v1/assets").status_code == 200
    resp = client.post(
        "/api/v1/assets",
        json={
            "id": "sneaky",
            "path": "x/sneaky",
            "title": "Sneaky",
            "content": "x",
            "owner": "platform-eng",
        },
    )
    assert resp.status_code == 403
    # And listing keys is not theirs to do either.
    assert client.get("/api/v1/api-keys").status_code == 403


def test_asset_owner_must_name_a_real_team(client, owner):
    resp = client.post(
        "/api/v1/assets",
        json={
            "id": "floating",
            "path": "x/floating",
            "title": "Floating",
            "content": "x",
            "owner": "not-a-team",
        },
        headers=owner,
    )
    assert resp.status_code == 422
    assert "Unknown owning team" in str(resp.json()["detail"])


def test_last_team_admin_is_protected_from_self_removal(client, owner, act_as):
    _team(client, owner, "platform-eng")
    jake = _person(client, owner, "jake@example.com")
    client.put(
        f"/api/v1/teams/platform-eng/members/{jake['uid']}",
        json={"team_role": "admin"},
        headers=owner,
    )

    act_as(jake["uid"], "jake@example.com", role="user")
    resp = client.delete(f"/api/v1/teams/platform-eng/members/{jake['uid']}")
    assert resp.status_code == 409
    assert "no admin" in resp.json()["detail"]


def test_team_admin_cannot_mint_a_key_that_impersonates_a_person(
    client, owner, act_as
):
    """The hole team membership would otherwise open.

    A team admin may add anyone to their own team. If team authority extended
    to a person's credentials, they could pull someone on and then mint a key
    that authenticates *as* that person.
    """
    _team(client, owner, "platform-eng")
    jake = _person(client, owner, "jake@example.com")
    mara = _person(client, owner, "mara@example.com")
    client.put(
        f"/api/v1/teams/platform-eng/members/{jake['uid']}",
        json={"team_role": "admin"},
        headers=owner,
    )

    act_as(jake["uid"], "jake@example.com", role="user")

    # Adding her is his to do — it is his team.
    assert (
        client.put(
            f"/api/v1/teams/platform-eng/members/{mara['uid']}",
            json={"team_role": "member"},
        ).status_code
        == 200
    )
    # Wearing her identity is not.
    resp = client.post(
        "/api/v1/api-keys", json={"name": "puppet", "identity_uid": mara["uid"]}
    )
    assert resp.status_code == 403


def test_team_admin_cannot_revoke_a_persons_key(client, owner, act_as):
    _team(client, owner, "platform-eng")
    jake = _person(client, owner, "jake@example.com")
    mara = _person(client, owner, "mara@example.com")
    for uid, role in ((jake["uid"], "admin"), (mara["uid"], "member")):
        client.put(
            f"/api/v1/teams/platform-eng/members/{uid}",
            json={"team_role": role},
            headers=owner,
        )
    key = client.post(
        "/api/v1/api-keys",
        json={"name": "hers", "identity_uid": mara["uid"]},
        headers=owner,
    ).json()["key"]["id"]

    act_as(jake["uid"], "jake@example.com", role="user")
    assert client.delete(f"/api/v1/api-keys/{key}").status_code == 403
    assert client.get(f"/api/v1/api-keys/{key}/usage").status_code == 403


def test_last_admin_cannot_demote_themselves(client, owner, act_as):
    """Demotion orphans a team exactly as removal does."""
    _team(client, owner, "platform-eng")
    jake = _person(client, owner, "jake@example.com")
    client.put(
        f"/api/v1/teams/platform-eng/members/{jake['uid']}",
        json={"team_role": "admin"},
        headers=owner,
    )

    act_as(jake["uid"], "jake@example.com", role="user")
    resp = client.put(
        f"/api/v1/teams/platform-eng/members/{jake['uid']}",
        json={"team_role": "member"},
    )
    assert resp.status_code == 409
    assert "no admin" in resp.json()["detail"]


def test_the_roster_is_not_readable_by_every_authenticated_caller(
    client, owner, act_as
):
    """Who owns a document is public; who is on the team is not."""
    _team(client, owner, "platform-eng")
    mara = _person(client, owner, "mara@example.com")
    outsider = _person(client, owner, "outsider@example.com")
    client.put(
        f"/api/v1/teams/platform-eng/members/{mara['uid']}",
        json={"team_role": "member"},
        headers=owner,
    )

    act_as(outsider["uid"], "outsider@example.com", role="user")
    team = client.get("/api/v1/teams/platform-eng").json()
    # They can still see the team exists and what it owns...
    assert team["slug"] == "platform-eng"
    assert team["member_count"] == 1
    # ...but not who is on it, nor their emails and platform roles.
    assert team["members"] == []
    assert "mara@example.com" not in client.get("/api/v1/teams").text


def test_a_member_sees_their_own_teams_roster(client, owner, act_as):
    _team(client, owner, "platform-eng")
    mara = _person(client, owner, "mara@example.com")
    client.put(
        f"/api/v1/teams/platform-eng/members/{mara['uid']}",
        json={"team_role": "member"},
        headers=owner,
    )

    act_as(mara["uid"], "mara@example.com", role="user")
    team = client.get("/api/v1/teams/platform-eng").json()
    assert [m["identity_uid"] for m in team["members"]] == [mara["uid"]]
    assert team["my_team_role"] == "member"
    assert team["can_administer"] is False


def test_deleting_a_person_through_users_cleans_up_like_identities_does(
    client, owner
):
    _team(client, owner, "platform-eng")
    mara = _person(client, owner, "mara@example.com")
    client.put(
        f"/api/v1/teams/platform-eng/members/{mara['uid']}",
        json={"team_role": "member"},
        headers=owner,
    )
    key = client.post(
        "/api/v1/api-keys",
        json={"name": "hers", "identity_uid": mara["uid"]},
        headers=owner,
    ).json()["key"]["id"]

    assert client.delete(f"/api/v1/users/{mara['uid']}", headers=owner).status_code == 204

    team = client.get("/api/v1/teams/platform-eng", headers=owner).json()
    assert team["members"] == []
    revoked = next(
        k for k in client.get("/api/v1/api-keys", headers=owner).json() if k["id"] == key
    )
    assert revoked["status"] != "active"
