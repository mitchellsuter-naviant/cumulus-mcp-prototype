"""The live accounts-payable runner and its table.

The doctrinal assertions: the posting rule's values come from the governed
policy document (editing it changes behavior with no code change), a vendor
with no exemplar gets three independent extractions adjudicated field by
field, verifying that ensemble writes the exemplar into the knowledge layer
for real (so the next invoice takes the one-shot path), an elevated run
suspends into a real Task, deciding that Task resumes the run and commits
the state, and submission is a human act (keys refused).
"""

import pytest

import app.deps as deps
from app.config import settings
from app.main import app
from app.models import AssetCreate
from app.payables import (
    InMemoryPayableDirectory,
    PayableService,
    StubInvoiceReader,
    seed_payables_if_empty,
)
from app.payables_api import router as payables_router
from app.service import KnowledgeService, seed_if_empty
from app.storage import InMemoryRepository
from app.tasks import InMemoryTaskDirectory, TaskService, seed_tasks_if_empty

# The two knowledge docs the runner reads. The other engineer's seed files
# may land later; the fixture creates these only when the seed corpus lacks
# them, so this module is green standalone.
_POSTING_POLICY_MD = """# Invoice Posting Policy

The accounts-payable flow reads this section as governed values. Edits here
change what the flow does — no code changes, and every edit is logged.

- Auto-post limit: $2,500.00 per invoice. At or under this limit a
  confidently classified invoice posts without review.
- Minimum classification confidence: 0.75. Below this the invoice routes
  to the ap-clerk queue regardless of amount.
"""

_ACME_EXEMPLAR_MD = """# Vendor exemplar: Acme Industrial

One-shot extraction template for `acme-industrial` invoices.

- Invoice number format: `INV-####` — match this pattern exactly
- Invoice date format: ISO 8601 (YYYY-MM-DD), header block
- PO pattern: `PO-####`, under the bill-to block
- Where the total sits: bottom-right summary block, labelled "Amount Due"
"""


@pytest.fixture()
def client(_client):
    deps._service = KnowledgeService(InMemoryRepository())
    seed_if_empty(deps._service, settings.seed_dir)
    if deps._service.repo.get("invoice-posting-policy") is None:
        deps._service.create(AssetCreate(
            id="invoice-posting-policy",
            path="finance/policies/invoice-posting",
            title="Invoice Posting Policy",
            content=_POSTING_POLICY_MD,
            authority_level="policy",
            owner="finance-ops",
            tags=["payables", "finance"],
            status="published",
        ))
    if deps._service.repo.get("vendor-exemplar-acme-industrial") is None:
        deps._service.create(AssetCreate(
            id="vendor-exemplar-acme-industrial",
            path="finance/exemplars/acme-industrial",
            title="Vendor exemplar: Acme Industrial",
            content=_ACME_EXEMPLAR_MD,
            authority_level="how-to",
            owner="finance-ops",
            tags=["exemplar", "ap", "acme-industrial"],
            status="published",
        ))
    deps._task_service = TaskService(InMemoryTaskDirectory())
    seed_tasks_if_empty(deps._task_service)
    deps._payable_service = PayableService(
        InMemoryPayableDirectory(),
        StubInvoiceReader(),
        deps._service,
        deps._task_service,
    )
    seed_payables_if_empty(deps._payable_service)

    # ---- temporary integration shims, test-fixture only -------------------
    # 1) deps.get_payable_service lands with the wiring PR; until it does,
    #    install an accessor that serves the singleton set above. Once the
    #    real accessor exists this setattr is skipped and the singleton
    #    assignment above is all the isolation needed.
    if not hasattr(deps, "get_payable_service"):
        setattr(deps, "get_payable_service", lambda: deps._payable_service)
    # 2) main.py gains include_router(payables_api.router) in the same PR;
    #    until then, mount it here so the TestClient can reach the routes.
    if not any(getattr(r, "path", "") == "/api/v1/payables" for r in app.routes):
        app.include_router(payables_router)
    return _client


@pytest.fixture()
def owner(monkeypatch):
    monkeypatch.setattr(settings, "auth_backend", "firebase")
    monkeypatch.setattr(settings, "admin_api_key", "root-sekret")
    return {"X-API-Key": "root-sekret"}


def _decide(client, task_id, verdict, note="", correction=""):
    """Decide via the API, then resume the payable run directly.

    Temporary integration shim, test-only: tasks_api's resume-hook list
    gains get_payable_service().apply_task_decision with the wiring PR;
    until then the decided task is handed to the service by hand.
    """
    resp = client.post(
        f"/api/v1/tasks/{task_id}/decide",
        json={"verdict": verdict, "note": note, "correction": correction},
    )
    assert resp.status_code == 200
    return deps._payable_service.apply_task_decision(
        deps._task_service.get(task_id)
    )


# ---- the table ----


def test_seeded_table_and_stats_agree(client):
    rows = client.get("/api/v1/payables").json()
    stats = client.get("/api/v1/payables/stats").json()
    assert stats["total"] == len(rows) == 7
    assert stats["posted"] == sum(1 for r in rows if r["state"] == "posted") == 6
    assert stats["denied"] == 1 and stats["pending"] == 0
    assert stats["one_shot"] == 5 and stats["ensemble"] == 2
    assert stats["posted_usd"] == round(
        sum(r["total_usd"] for r in rows if r["state"] == "posted"), 2
    )
    # Newest first.
    submitted = [r["submitted_at_iso"] for r in rows]
    assert submitted == sorted(submitted, reverse=True)


def test_state_filter_and_missing_row(client):
    denied = client.get("/api/v1/payables", params={"state": "denied"}).json()
    assert {r["id"] for r in denied} == {"inv_seed_06"}
    assert client.get("/api/v1/payables/inv_nope").status_code == 404


def test_placeholders_present_on_every_record(client):
    rows = client.get("/api/v1/payables").json()
    fresh = client.post(
        "/api/v1/payables",
        json={"note": "vendor=acme-industrial total=310.00 invoice=INV-2301"},
    ).json()
    for r in rows + [fresh]:
        assert r["three_way_match"] == "placeholder"
        assert r["fraud_screen"] == "placeholder"


# ---- the run: a known vendor takes the one-shot path ----


def test_known_vendor_under_limit_posts_one_shot(client):
    resp = client.post(
        "/api/v1/payables",
        json={
            "filename": "acme-aug.png",
            "note": "vendor=acme-industrial total=1234.56 invoice=INV-2041 "
                    "po=PO-7741 date=2026-08-20",
        },
    )
    assert resp.status_code == 201
    row = resp.json()
    assert row["state"] == "posted"
    assert row["extraction_path"] == "one-shot"
    assert row["total_usd"] == 1234.56
    assert row["invoice_number"] == "INV-2041" and row["po_number"] == "PO-7741"
    assert row["exemplar_ref"].startswith(
        "knowledge:vendor-exemplar-acme-industrial@rev"
    )
    assert row["task_id"] == ""
    # The journal traces the declared steps, ending in the gateway commit.
    steps = [e["step_id"] for e in row["journal"]]
    assert steps == ["t1", "m0", "x1", "r1", "m1", "x3", "x4", "r2", "a1", "e1"]
    assert "knowledge:invoice-posting-policy" in row["policy_ref"]


def test_known_vendor_over_limit_suspends_and_decision_posts(client):
    row = client.post(
        "/api/v1/payables",
        json={"note": "vendor=acme-industrial total=4100.00 invoice=INV-2050"},
    ).json()
    assert row["state"] == "pending"
    assert "exceeds the $2,500.00 auto-post limit" in row["verdict_reason"]
    steps = [e["step_id"] for e in row["journal"]]
    assert steps == ["t1", "m0", "x1", "r1", "m1", "x3", "x4", "r2", "k2"]

    # The Task is real, in the declared queue, carrying the policy ref.
    task = client.get(f"/api/v1/tasks/{row['task_id']}").json()
    assert task["queue"] == "ap-clerk" and task["kind"] == "decision"
    assert task["case_ref"] == row["id"]
    assert "knowledge:invoice-posting-policy" in task["refs"]

    # Deciding the task resumes the run: state commits, journal completes.
    _decide(client, row["task_id"], "approved", note="Matches the PO.")
    resumed = client.get(f"/api/v1/payables/{row['id']}").json()
    assert resumed["state"] == "posted"
    assert resumed["decided_by"]
    assert [e["step_id"] for e in resumed["journal"]][-3:] == ["k2", "a1", "e1"]


# ---- the run: an unknown vendor gets the ensemble, then the flywheel ----


def _submit_unknown(client, slug="meridian-freight", total="940.00"):
    return client.post(
        "/api/v1/payables",
        json={
            "filename": f"{slug}-invoice.pdf",
            "note": f"vendor={slug} total={total} invoice=INV-3320 "
                    "po=PO-8812 date=2026-08-20",
        },
    ).json()


def test_unknown_vendor_runs_the_ensemble_and_suspends(client):
    row = _submit_unknown(client)
    assert row["state"] == "pending"
    assert row["extraction_path"] == "ensemble"
    assert row["exemplar_ref"] == ""
    # Three independent extractions, model names recorded.
    assert len(row["candidates"]) == 3
    assert {c["model"] for c in row["candidates"]} == {
        "stub-extractor-a", "stub-extractor-b", "stub-extractor-c",
    }
    # Extractor b renders the date MM/DD/YYYY, so adjudication had work.
    assert row["conflicts"]
    assert any("invoice_date" in c for c in row["conflicts"])
    assert row["invoice_date"] == "2026-08-20"  # majority ISO
    assert row["total_usd"] == 940.00  # median absorbs the ±$0.01 offset
    steps = [e["step_id"] for e in row["journal"]]
    assert steps == ["t1", "m0", "x1", "r1", "m2a", "m2b", "m2c", "x2", "k1"]

    # The curation Task carries per-model values for the verifier.
    task = client.get(f"/api/v1/tasks/{row['task_id']}").json()
    assert task["queue"] == "ap-clerk" and task["kind"] == "curation"
    date_evidence = next(
        e for e in task["evidence"] if e["label"].startswith("invoice_date")
    )
    assert "2026-08-20" in date_evidence["value"]
    assert "08/20/2026" in date_evidence["value"]


def test_verifying_the_ensemble_writes_the_exemplar_and_posts(client):
    row = _submit_unknown(client)
    _decide(
        client, row["task_id"], "corrected",
        note="Fields verified against the paper invoice.",
        correction="due_date confirmed off the remittance stub",
    )
    # The exemplar now exists in the knowledge layer, for real.
    asset = deps._service.repo.get("vendor-exemplar-meridian-freight")
    assert asset is not None
    assert "INV-3320" in asset.content  # the invoice-number format
    assert "Correction applied" in asset.content  # corrected → provenance line

    resumed = client.get(f"/api/v1/payables/{row['id']}").json()
    assert resumed["state"] == "posted"  # $940.00 is under the limit
    assert resumed["exemplar_ref"].startswith(
        "knowledge:vendor-exemplar-meridian-freight@rev"
    )
    assert [e["step_id"] for e in resumed["journal"]][-5:] == [
        "k1", "a2", "r2", "a1", "e1",
    ]


def test_verified_vendor_takes_the_one_shot_path_next_time(client):
    first = _submit_unknown(client)
    assert first["extraction_path"] == "ensemble"
    _decide(client, first["task_id"], "approved", note="Looks right.")

    second = _submit_unknown(client, total="815.50")
    assert second["extraction_path"] == "one-shot"
    assert second["state"] == "posted"
    assert "m1" in [e["step_id"] for e in second["journal"]]
    assert "m2a" not in [e["step_id"] for e in second["journal"]]


def test_rejected_verification_denies_and_writes_no_exemplar(client):
    row = _submit_unknown(client, slug="harvest-catering", total="450.00")
    _decide(client, row["task_id"], "rejected", note="Not a real invoice.")
    resumed = client.get(f"/api/v1/payables/{row['id']}").json()
    assert resumed["state"] == "denied"
    assert resumed["verdict_reason"] == "document rejected; no exemplar written"
    assert deps._service.repo.get("vendor-exemplar-harvest-catering") is None


def test_verified_ensemble_over_limit_still_needs_the_review(client):
    row = _submit_unknown(client, slug="titanium-tooling", total="3900.00")
    _decide(client, row["task_id"], "approved", note="Exemplar verified.")
    resumed = client.get(f"/api/v1/payables/{row['id']}").json()
    # The exemplar is written, but the posting rule still elevates the amount.
    assert resumed["state"] == "pending"
    assert deps._service.repo.get("vendor-exemplar-titanium-tooling") is not None
    assert [e["step_id"] for e in resumed["journal"]][-4:] == [
        "k1", "a2", "r2", "k2",
    ]
    _decide(client, resumed["task_id"], "approved", note="Contract amount.")
    assert (
        client.get(f"/api/v1/payables/{row['id']}").json()["state"] == "posted"
    )


# ---- governance: the rule reads the document at run time ----


def test_editing_the_policy_changes_the_flow_without_code(client, owner):
    asset = client.get(
        "/api/v1/assets/invoice-posting-policy", headers=owner
    ).json()
    tightened = asset["content"].replace(
        "Auto-post limit: $2,500.00", "Auto-post limit: $500.00"
    )
    assert tightened != asset["content"]
    resp = client.put(
        "/api/v1/assets/invoice-posting-policy",
        json={"content": tightened},
        headers=owner,
    )
    assert resp.status_code == 200

    row = client.post(
        "/api/v1/payables",
        json={"note": "vendor=acme-industrial total=800.00 invoice=INV-2060"},
        headers=owner,
    ).json()
    assert row["state"] == "pending"
    assert "$500.00 auto-post limit" in row["verdict_reason"]
    assert row["post_limit_usd"] == 500.0


# ---- gates ----


def test_key_may_read_but_not_submit(client, owner):
    from app.api_keys import InMemoryApiKeyDirectory
    from app.identities import IdentityService, InMemoryMachineIdentityDirectory
    from app.users import InMemoryUserDirectory

    deps._user_directory = InMemoryUserDirectory()
    deps._api_key_directory = InMemoryApiKeyDirectory()
    deps._identity_service = IdentityService(
        deps._user_directory, InMemoryMachineIdentityDirectory()
    )
    identity = client.post(
        "/api/v1/identities",
        json={"display_name": "AP bot", "role": "admin"},
        headers=owner,
    ).json()
    minted = client.post(
        "/api/v1/api-keys",
        json={"name": "CI", "identity_uid": identity["uid"]},
        headers=owner,
    ).json()
    key = {"X-API-Key": minted["token"]}
    assert client.get("/api/v1/payables", headers=key).status_code == 200
    resp = client.post(
        "/api/v1/payables", json={"note": "total=10"}, headers=key
    )
    assert resp.status_code == 403


def test_unauthenticated_is_refused(client, monkeypatch):
    monkeypatch.setattr(settings, "auth_backend", "firebase")
    assert client.get("/api/v1/payables").status_code == 401
