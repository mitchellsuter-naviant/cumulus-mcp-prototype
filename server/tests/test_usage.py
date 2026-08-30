"""Per-asset usage accounting.

The interesting cases are all about what is *not* counted: internal service
calls, deleted assets, and requests that fail. A usage metric nobody can trust
is worse than no metric, because it gets used to retire documents.
"""

import pytest

import app.deps as deps
from app.api_keys import InMemoryApiKeyDirectory
from app.config import settings
from app.identities import IdentityService, InMemoryMachineIdentityDirectory
from app.models import AssetCreate
from app.service import KnowledgeService
from app.storage import InMemoryRepository
from app.teams import InMemoryTeamDirectory, TeamService
from app.usage import CITED, READ, InMemoryAssetUsageStore, _summarize
from app.users import InMemoryUserDirectory

_ASSETS = [
    dict(
        id="leave-policy",
        path="people/policies/leave",
        title="Paid Leave Policy",
        content="Employees accrue fifteen days of paid leave each year.",
        authority_level="policy",
        owner="people-ops",
        status="published",
    ),
    dict(
        id="vpn-standard",
        path="security/standards/vpn",
        title="VPN Standard",
        content="All remote sessions traverse the corporate VPN.",
        authority_level="standard",
        owner="security",
        status="published",
    ),
]


@pytest.fixture()
def wired(_client, monkeypatch):
    monkeypatch.setattr(settings, "model_backend", "none")
    service = KnowledgeService(InMemoryRepository())
    for spec in _ASSETS:
        service.create(AssetCreate(**spec))
    deps._service = service
    deps._team_service = TeamService(InMemoryTeamDirectory())
    deps._user_directory = InMemoryUserDirectory()
    deps._api_key_directory = InMemoryApiKeyDirectory()
    deps._identity_service = IdentityService(
        deps._user_directory, InMemoryMachineIdentityDirectory()
    )
    deps._asset_usage = InMemoryAssetUsageStore()
    deps._answer_backend = deps._MISSING
    return _client


def _usage(client, asset_id: str) -> dict:
    return client.get(f"/api/v1/assets/{asset_id}/usage").json()


# ---- What counts ------------------------------------------------------------


def test_fetching_a_document_records_a_read(wired):
    wired.get("/api/v1/assets/leave-policy")
    assert _usage(wired, "leave-policy")["reads"] == 1


def test_internal_service_calls_are_not_counted(wired):
    """Only the API boundary counts.

    KnowledgeService.get is called by the answer planner, the context view and
    supersession checks. Counting those would measure plumbing, and would make
    a document look used because something else linked to it.
    """
    deps._service.get("leave-policy")
    deps._service.get("leave-policy")
    assert _usage(wired, "leave-policy")["total"] == 0


def test_searching_records_an_impression_but_not_a_read(wired):
    """Surfacing is not consumption. Both are counted, separately."""
    wired.get("/api/v1/search", params={"q": "leave"})
    usage = _usage(wired, "leave-policy")
    assert usage["impressions"] == 1
    assert usage["reads"] == 0


def test_listing_assets_is_not_usage(wired):
    """Browsing the taxonomy says nothing about any individual document."""
    wired.get("/api/v1/assets")
    assert _usage(wired, "leave-policy")["total"] == 0


def test_the_weak_tail_gets_no_impression(wired):
    """Otherwise every document scores an impression on every search.

    A dense retriever scores the whole corpus, so counting each returned hit
    would make impressions a measure of how busy the service is. Only hits
    that stand out from the ranking count.
    """
    wired.get("/api/v1/search", params={"q": "leave"})
    rows = {r["asset_id"]: r for r in wired.get("/api/v1/usage/assets").json()}
    surfaced = [a for a, r in rows.items() if r["impressions"] > 0]
    # "leave" is only in one of the two seeded documents.
    assert surfaced == ["leave-policy"]


def test_a_missing_document_records_nothing(wired):
    assert wired.get("/api/v1/assets/does-not-exist").status_code == 404
    assert deps._asset_usage.usage_all(30) == {}


def _use_generated_backend(monkeypatch, reply="See [leave-policy]."):
    """Install a planner that returns a real, cited answer.

    Patched on `app.rest_api`, not `app.deps`: the route module does
    `from .deps import get_answer_service`, so the name is bound at import and
    patching deps would leave the route calling the original.
    """
    import app.rest_api as rest_api
    from app.answering import AnswerService

    class _Generated:
        name = "scripted"

        def answer(self, question, context, tools):
            tools.list_assets()
            return reply, tools.steps

    monkeypatch.setattr(
        rest_api,
        "get_answer_service",
        lambda: AnswerService(deps._service, deps._team_service, _Generated()),
    )


def test_citations_are_recorded_when_an_answer_is_generated(wired, monkeypatch):
    _use_generated_backend(monkeypatch)
    body = wired.post("/api/v1/ask", json={"question": "leave policy?"}).json()
    assert body["backend"] == "scripted"
    assert [c["asset_id"] for c in body["citations"]] == ["leave-policy"]
    assert _usage(wired, "leave-policy")["citations"] == 1


def test_reads_and_citations_are_counted_separately(wired, monkeypatch):
    """The ratio is the whole point: found often, consumed rarely."""
    _use_generated_backend(monkeypatch)
    wired.get("/api/v1/assets/leave-policy")
    wired.post("/api/v1/ask", json={"question": "leave policy?"})
    usage = _usage(wired, "leave-policy")
    assert usage["reads"] == 1
    assert usage["citations"] == 1
    assert usage["total"] == 2


# ---- Breakdowns -------------------------------------------------------------


def test_surface_and_actor_are_broken_out(wired):
    wired.get("/api/v1/assets/vpn-standard")
    usage = _usage(wired, "vpn-standard")
    assert usage["surfaces"] == {"rest": 1}
    # Local dev resolves every caller to the dev principal.
    assert sum(usage["actors"].values()) == 1


def test_repeated_use_accumulates_within_a_day(wired):
    for _ in range(3):
        wired.get("/api/v1/assets/vpn-standard")
    usage = _usage(wired, "vpn-standard")
    assert usage["reads"] == 3
    assert usage["surfaces"]["rest"] == 3
    assert len(usage["days"]) == 1


# ---- The corpus view --------------------------------------------------------


def test_corpus_usage_zero_fills_untouched_assets(wired):
    """The absence is the finding.

    Assets with no traffic have no rollup rows, so returning only what the
    store holds would omit exactly the documents the dead-weight report exists
    to surface.
    """
    wired.get("/api/v1/assets/leave-policy")
    rows = {r["asset_id"]: r for r in wired.get("/api/v1/usage/assets").json()}
    assert set(rows) == {"leave-policy", "vpn-standard"}
    assert rows["leave-policy"]["total"] == 1
    assert rows["vpn-standard"]["total"] == 0


def test_deleting_an_asset_drops_its_usage(wired):
    """Otherwise a future asset reusing the id inherits the old traffic."""
    wired.get("/api/v1/assets/vpn-standard")
    assert _usage(wired, "vpn-standard")["reads"] == 1

    assert wired.delete("/api/v1/assets/vpn-standard").status_code == 204
    deps._service.create(AssetCreate(**_ASSETS[1]))
    assert _usage(wired, "vpn-standard")["total"] == 0


def test_usage_for_an_unknown_asset_is_404_not_an_empty_row(wired):
    assert wired.get("/api/v1/assets/nope/usage").status_code == 404


def test_the_window_is_bounded(wired):
    assert wired.get("/api/v1/assets/leave-policy/usage?days=0").status_code == 422
    assert wired.get("/api/v1/assets/leave-policy/usage?days=999").status_code == 422


# ---- The store --------------------------------------------------------------


def test_flush_merges_rather_than_overwrites():
    """Two flushes on the same day must add up, not replace."""
    store = InMemoryAssetUsageStore()
    store.record("a", READ, "rest", "u1")
    store.flush()
    store.record("a", READ, "mcp", "u2")
    store.record("a", CITED, "mcp", "u2")
    summary = store.usage("a", 30)
    assert summary.reads == 2
    assert summary.citations == 1
    assert summary.surfaces == {"rest": 1, "mcp": 2}
    assert summary.actors == {"u1": 1, "u2": 2}


def test_reading_flushes_pending_writes():
    """A count that only appears after 50 events would look like data loss."""
    store = InMemoryAssetUsageStore()
    store.record("a", READ, "rest", "u1")
    assert store.usage("a", 30).reads == 1


def test_accounting_never_breaks_the_request(wired, monkeypatch):
    """A metrics outage must not turn a working read into a 500."""

    def explode(*args, **kwargs):
        raise RuntimeError("usage backend down")

    monkeypatch.setattr(deps._asset_usage, "flush", explode)
    # Force the buffer past its threshold so the failing flush is reached.
    for _ in range(60):
        deps._asset_usage.record("leave-policy", READ, "rest", "u1")
    assert wired.get("/api/v1/assets/leave-policy").status_code == 200


def test_summarize_reports_the_latest_use_across_days():
    from app.usage import AssetUsageDay
    from datetime import datetime, timezone

    older = AssetUsageDay(
        asset_id="a",
        day="2026-08-01",
        events={READ: 1},
        last_used_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    newer = AssetUsageDay(
        asset_id="a",
        day="2026-08-20",
        events={READ: 2},
        last_used_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
    )
    # Deliberately out of order: the summary sorts, the caller should not have to.
    summary = _summarize("a", [newer, older])
    assert summary.reads == 3
    assert summary.last_used_at.day == 20
    assert [d.day for d in summary.days] == ["2026-08-01", "2026-08-20"]


def test_unknown_event_names_are_stored_without_a_schema_change():
    """Counters are keyed by name so search impressions can be added later."""
    store = InMemoryAssetUsageStore()
    store.record("a", "retrieved", "rest", "u1")
    summary = store.usage("a", 30)
    assert summary.total == 1
    # It does not masquerade as a read or a citation.
    assert summary.reads == 0 and summary.citations == 0


def test_the_retrieval_fallback_does_not_record_citations(wired):
    """Its "citations" are just the top hits, not a claim's provenance.

    With no generative backend, /ask returns ranked documents and labels them
    citations so the UI has something to link. Counting those would record one
    citation per document per question — a search impression promoted to the
    strongest signal in the system, which is exactly what would make the
    dead-weight report useless.
    """
    body = wired.post("/api/v1/ask", json={"question": "leave policy"}).json()
    assert body["backend"] == "retrieval"
    assert body["citations"], "fallback should still return sources for the UI"
    assert deps._asset_usage.usage_all(30) == {}


def test_only_the_cited_document_is_counted(wired, monkeypatch):
    """The fallback cited all five seed documents for one question.

    A generated answer cites what it actually used, so the count has to follow
    the answer text rather than the retrieved set.
    """
    _use_generated_backend(monkeypatch)
    wired.post("/api/v1/ask", json={"question": "leave policy?"})
    assert _usage(wired, "leave-policy")["citations"] == 1
    assert _usage(wired, "vpn-standard")["citations"] == 0


# ---- The two ways a document fails ------------------------------------------


def test_a_document_that_surfaces_but_is_never_opened_is_ignored(wired):
    """A relevance problem: it ranks for queries it does not answer."""
    for _ in range(3):
        wired.get("/api/v1/search", params={"q": "leave"})
    row = _usage(wired, "leave-policy")
    assert row["impressions"] == 3
    assert row["reads"] == 0

    summary = deps._asset_usage.usage("leave-policy", 30)
    assert summary.ignored is True
    assert summary.undiscovered is False
    assert summary.unused is True


def test_a_document_nothing_ever_finds_is_undiscovered(wired):
    """A findability problem. Rewriting the prose would change nothing."""
    wired.get("/api/v1/search", params={"q": "leave"})
    summary = deps._asset_usage.usage("vpn-standard", 30)
    assert summary.undiscovered is True
    assert summary.ignored is False


def test_being_read_clears_both_diagnoses(wired):
    wired.get("/api/v1/search", params={"q": "leave"})
    wired.get("/api/v1/assets/leave-policy")
    summary = deps._asset_usage.usage("leave-policy", 30)
    assert summary.unused is False
    assert summary.ignored is False
    assert summary.undiscovered is False


def test_impressions_alone_do_not_make_a_document_used(wired):
    """Surfacing is not use; the dead-weight report must still flag it."""
    wired.get("/api/v1/search", params={"q": "leave"})
    summary = deps._asset_usage.usage("leave-policy", 30)
    assert summary.impressions == 1
    assert summary.total == 1
    assert summary.unused is True
