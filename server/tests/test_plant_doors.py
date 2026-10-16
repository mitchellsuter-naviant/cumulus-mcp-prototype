"""The two windows a scheduler closes, and who may close them.

Neither the compliance sweep nor the plant sweep is woken by a delivery:
nothing arrives, a window closes over a book Cumulus already holds, and the
response reads it. That is the whole reason these routes exist — an estate pull
cannot start them — and it is why they are tested here rather than through the
flows, which are exercised against their own services elsewhere. What is
asserted is the door: the role it demands, the fact it logs before any run
exists, and that the run it starts is the one the Trigger table names.
"""

import pytest

from app import dispatch
from tests import world as test_world


@pytest.fixture()
def world():
    return test_world.fresh()


@pytest.fixture()
def client(_client, world):
    return _client


@pytest.fixture()
def owner(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "auth_backend", "firebase")
    monkeypatch.setattr(settings, "admin_api_key", "root-sekret")
    return {"X-API-Key": "root-sekret"}


@pytest.mark.parametrize(
    "path, event, flow",
    [
        ("/api/v1/compliance/sweep", "ComplianceWindowClosed",
         "credential-tracking"),
        ("/api/v1/plant/sweep", "PlantWindowClosed", "plant-eligibility-sweep"),
    ],
)
def test_the_door_emits_the_window_and_starts_what_the_table_names(
    client, owner, world, path, event, flow
):
    response = client.post(path, headers=owner)

    assert response.status_code == 202
    body = response.json()
    assert body["event_id"]
    # The Trigger table decides what a closed window starts; the door does not.
    # Asserting the run against `TRIGGERS` rather than against a literal is
    # what makes this a test of the wiring instead of a second copy of it.
    bound = [t.starts for t in dispatch.TRIGGERS.values() if t.event == event]
    assert bound == [flow]
    assert len(body["runs"]) == len(bound)


@pytest.mark.parametrize(
    "path", ["/api/v1/compliance/sweep", "/api/v1/plant/sweep"]
)
def test_a_customer_user_cannot_close_a_window(client, world, path):
    """Admin, and not for tidiness: each of these raises work into a queue.

    The compliance one can put a lapsed medical card in front of a safety
    supervisor, and the plant one spends model budget on every eligible job.
    Neither is a read, so the persona that owns a desk still may not open one.

    Acted as a customer user rather than uncredentialed, because an
    uncredentialed local call is the *engineer* — the console's development
    default — and would prove nothing about the role.
    """
    response = client.post(
        path, headers={"X-Cumulus-Persona": "customer-user:ap"}
    )

    assert response.status_code == 403


def test_the_window_carries_its_own_day(client, owner, world):
    """`as_of` is the window's day, not the moment the call landed.

    A window that closes at 23:59 and is answered at 00:01 must still be
    answered as of the window, or the one flow whose whole purpose is being
    early goes out a day late.
    """
    response = client.post(
        "/api/v1/compliance/sweep?as_of=2026-09-07", headers=owner
    )

    assert response.status_code == 202
    assert response.json()["as_of"] == "2026-09-07"
