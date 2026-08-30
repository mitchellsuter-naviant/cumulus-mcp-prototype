"""The answering layer: structured filters, the tool surface, and degradation.

No network here. The Vertex planner is replaced by a scripted backend that
drives the real `Toolbox`, so what is tested is the tool contract — that
ownership questions are answerable from metadata, that citations only resolve
to documents the planner actually touched, and that every failure lands on a
retrieval answer rather than a 500.
"""

import pytest

import app.deps as deps
from app.answering import AnswerService, StepBudgetExhausted, Toolbox
from app.config import settings
from app.models import AssetFilter
from app.service import KnowledgeService
from app.storage import InMemoryRepository
from app.teams import InMemoryTeamDirectory, Team, TeamService
from app.users import InMemoryUserDirectory

# A corpus small enough to reason about, and deliberately shaped so the
# ownership question has an answer that no text search could produce: none of
# the HR documents contain the words "HR" or "people ops".
_ASSETS = [
    dict(
        id="leave-policy",
        path="people/policies/leave",
        title="Paid Leave Policy",
        content="Employees accrue fifteen days of paid leave each calendar year.",
        authority_level="policy",
        owner="people-ops",
        status="published",
        tags=["leave"],
    ),
    dict(
        id="onboarding-checklist",
        path="people/how-to/onboarding",
        title="Onboarding Checklist",
        content="Day one: issue a laptop, grant directory access, assign a buddy.",
        authority_level="how-to",
        owner="people-ops",
        status="published",
    ),
    dict(
        id="offboarding-draft",
        path="people/how-to/offboarding",
        title="Offboarding Checklist",
        content="Revoke access within one hour of the final day.",
        authority_level="how-to",
        owner="people-ops",
        status="draft",
    ),
    dict(
        id="vpn-standard",
        path="security/standards/vpn",
        title="VPN Standard",
        content="All remote sessions traverse the corporate VPN before access is granted.",
        authority_level="standard",
        owner="security",
        status="published",
        tags=["network"],
    ),
]


def _seeded_service() -> KnowledgeService:
    from app.models import AssetCreate

    service = KnowledgeService(InMemoryRepository())
    for spec in _ASSETS:
        service.create(AssetCreate(**spec))
    return service


def _seeded_teams() -> TeamService:
    directory = InMemoryTeamDirectory()
    directory.create(
        Team(slug="people-ops", name="People Operations", description="HR and hiring.")
    )
    directory.create(
        Team(slug="security", name="Security", description="Security engineering.")
    )
    return TeamService(directory)


@pytest.fixture()
def wired(_client, monkeypatch):
    monkeypatch.setattr(settings, "model_backend", "none")
    deps._service = _seeded_service()
    deps._team_service = _seeded_teams()
    deps._user_directory = InMemoryUserDirectory()
    deps._answer_backend = deps._MISSING
    return _client


# ---- Structured filters -----------------------------------------------------


def test_filters_apply_before_scoring_not_after(wired):
    """A narrow filter with a small limit must still return that filter's best.

    Filtering the top N afterwards is the bug this guards. Both teams own a
    document mentioning "access", and unfiltered the People Ops one wins the
    single slot — so a post-hoc owner filter would return nothing at all.
    """
    unfiltered = wired.get("/api/v1/search", params={"q": "access", "limit": 1}).json()
    assert unfiltered[0]["asset"]["owner"] == "people-ops"

    filtered = wired.get(
        "/api/v1/search", params={"q": "access", "limit": 1, "owner": "security"}
    ).json()
    assert [h["asset"]["id"] for h in filtered] == ["vpn-standard"]


def test_search_filters_narrow_by_authority_and_tag(wired):
    policies = wired.get(
        "/api/v1/search", params={"q": "leave", "authority_level": "policy"}
    ).json()
    assert [h["asset"]["id"] for h in policies] == ["leave-policy"]

    tagged = wired.get("/api/v1/search", params={"q": "vpn", "tag": "network"}).json()
    assert [h["asset"]["id"] for h in tagged] == ["vpn-standard"]

    assert wired.get(
        "/api/v1/search", params={"q": "leave", "tag": "network"}
    ).json() == []


def test_path_prefix_filter_matches_subtree_not_string_prefix(wired):
    inside = wired.get(
        "/api/v1/search", params={"q": "checklist", "path_prefix": "people"}
    ).json()
    assert {h["asset"]["id"] for h in inside} == {"onboarding-checklist"}

    # "peop" is a string prefix of "people/..." but not a taxonomy ancestor.
    assert (
        wired.get(
            "/api/v1/search", params={"q": "checklist", "path_prefix": "peop"}
        ).json()
        == []
    )


def test_status_filter_reaches_drafts_without_include_drafts(wired):
    """`status=draft` is an explicit request, so it should not need a second flag."""
    drafts = wired.get(
        "/api/v1/search", params={"q": "offboarding", "status": "draft"}
    ).json()
    assert [h["asset"]["id"] for h in drafts] == ["offboarding-draft"]


# ---- The toolbox ------------------------------------------------------------


def _toolbox(include_drafts: bool = False) -> Toolbox:
    return Toolbox(_seeded_service(), _seeded_teams(), include_drafts=include_drafts)


def test_ownership_is_answerable_from_metadata_and_not_from_text():
    """The question this whole module exists for.

    `list_assets(owner=...)` returns exactly the team's documents. A text search
    for the same thing returns topically similar documents and misses the point
    — asserted here so the distinction cannot quietly regress.
    """
    tools = _toolbox()
    owned = tools.list_assets(owner="people-ops")
    assert {a["id"] for a in owned["assets"]} == {
        "leave-policy",
        "onboarding-checklist",
    }

    # The corpus never says "HR", so retrieval cannot find these.
    text_hits = tools.search_knowledge(query="HR team documents")["hits"]
    assert {h["id"] for h in text_hits} != {"leave-policy", "onboarding-checklist"}


def test_list_assets_hides_drafts_unless_asked():
    assert "offboarding-draft" not in {
        a["id"] for a in _toolbox().list_assets(owner="people-ops")["assets"]
    }
    assert "offboarding-draft" in {
        a["id"]
        for a in _toolbox().list_assets(owner="people-ops", status="draft")["assets"]
    }


def test_resolve_team_matches_slug_name_and_substring():
    tools = _toolbox()
    assert tools.resolve_team("people-ops")["match"]["slug"] == "people-ops"
    assert tools.resolve_team("People Operations")["match"]["slug"] == "people-ops"
    assert tools.resolve_team("secur")["match"]["slug"] == "security"


def test_resolve_team_refuses_to_guess_between_candidates():
    """An ambiguous or unmatched name returns None plus the candidate list.

    Picking one would attribute documents to the wrong owner, which is a worse
    failure than saying "I could not tell" — the planner can then ask or list.
    """
    result = _toolbox().resolve_team("engineering")
    assert result["match"] is None
    assert {c["slug"] for c in result["candidates"]} == {"people-ops", "security"}


def test_list_teams_counts_owned_assets():
    teams = {t["slug"]: t for t in _toolbox().list_teams()["teams"]}
    assert teams["people-ops"]["assets_owned"] == 3
    assert teams["security"]["assets_owned"] == 1


def test_read_document_is_capped_per_question(monkeypatch):
    monkeypatch.setattr(settings, "answer_max_documents", 2)
    tools = _toolbox()
    assert "content" in tools.read_document("leave-policy")
    assert "content" in tools.read_document("vpn-standard")
    refused = tools.read_document("onboarding-checklist")
    assert "error" in refused and "budget" in refused["error"]


def test_tool_errors_are_returned_not_raised():
    """A planner must be able to recover from its own bad call."""
    tools = _toolbox()
    assert "error" in tools.call("read_document", {"asset_id": "nope"})
    assert "error" in tools.call("get_team", {"slug": "nope"})
    assert "error" in tools.call("no_such_tool", {})
    assert "error" in tools.call("list_assets", {"bogus_argument": 1})


def test_every_tool_call_is_recorded():
    tools = _toolbox()
    tools.list_teams()
    tools.resolve_team("secur")
    tools.list_assets(owner="security")
    assert [s.tool for s in tools.steps] == [
        "list_teams",
        "resolve_team",
        "list_assets",
    ]


# ---- The service ------------------------------------------------------------


class _ScriptedBackend:
    """Drives the real Toolbox through a fixed plan, then returns fixed prose."""

    name = "scripted"

    def __init__(self, plan, reply, raises=None):
        self._plan = plan
        self._reply = reply
        self._raises = raises
        self.context = None

    def answer(self, question, context, tools):
        self.context = context
        if self._raises:
            raise self._raises
        for name, arguments in self._plan:
            tools.call(name, arguments)
        return self._reply, tools.steps


def _service_with(backend):
    return AnswerService(_seeded_service(), _seeded_teams(), backend)


def test_planner_answer_carries_citations_for_ids_it_touched():
    backend = _ScriptedBackend(
        plan=[("resolve_team", {"name": "HR"}), ("list_assets", {"owner": "people-ops"})],
        reply="People Operations owns [leave-policy] and [onboarding-checklist].",
    )
    result = _service_with(backend).ask("which assets are managed by HR?")
    assert result.backend == "scripted"
    assert {c.asset_id for c in result.citations} == {
        "leave-policy",
        "onboarding-checklist",
    }
    assert [s.tool for s in result.steps] == ["resolve_team", "list_assets"]


def test_invented_citations_are_dropped():
    """An id the planner never read gets no citation, so the gap is visible."""
    backend = _ScriptedBackend(
        plan=[("list_assets", {"owner": "security"})],
        reply="See [vpn-standard] and also [remote-work-policy].",
    )
    result = _service_with(backend).ask("vpn?")
    assert [c.asset_id for c in result.citations] == ["vpn-standard"]


def test_backend_failure_degrades_to_ranked_documents():
    backend = _ScriptedBackend(plan=[], reply="", raises=RuntimeError("vertex down"))
    result = _service_with(backend).ask("what is the leave policy?")
    assert result.backend == "retrieval"
    assert "leave-policy" in {c.asset_id for c in result.citations}


def test_step_budget_exhaustion_is_reported_not_hidden():
    backend = _ScriptedBackend(plan=[], reply="", raises=StepBudgetExhausted())
    result = _service_with(backend).ask("what is the leave policy?")
    assert result.truncated is True
    assert result.backend == "retrieval"


def test_scope_asset_is_put_in_front_of_the_planner():
    backend = _ScriptedBackend(plan=[], reply="Yes.")
    _service_with(backend).ask("does this apply to contractors?", scope_asset_id="leave-policy")
    assert 'id="leave-policy"' in backend.context
    assert "fifteen days" in backend.context


def test_unknown_scope_asset_is_ignored_rather_than_failing():
    backend = _ScriptedBackend(plan=[], reply="Yes.")
    result = _service_with(backend).ask("hello?", scope_asset_id="does-not-exist")
    assert backend.context == ""
    assert result.answer == "Yes."


# ---- The endpoint -----------------------------------------------------------


def test_ask_endpoint_degrades_honestly_without_a_backend(wired):
    resp = wired.post("/api/v1/ask", json={"question": "what is the leave policy?"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["backend"] == "retrieval"
    assert "leave-policy" in {c["asset_id"] for c in body["citations"]}
    # It must not pretend to have reasoned over the corpus.
    assert "not enabled" in body["answer"]


def test_ask_endpoint_says_so_when_the_corpus_has_no_answer(wired):
    body = wired.post(
        "/api/v1/ask", json={"question": "zzzzz quarterly kombucha allowance"}
    ).json()
    assert body["citations"] == []
    assert "Nothing in the governed corpus" in body["answer"]


def test_ask_status_reports_the_mode(wired):
    assert wired.get("/api/v1/ask/status").json()["mode"] == "retrieval"


def test_ask_rejects_an_empty_question(wired):
    assert wired.post("/api/v1/ask", json={"question": ""}).status_code == 422


def test_asset_filter_is_empty_is_honest():
    assert AssetFilter().is_empty()
    assert not AssetFilter(owner="people-ops").is_empty()


# ---- Semantic team resolution ----------------------------------------------


class _StubEmbedder:
    """Returns whatever cosine profile a test asks for.

    Team order matches `_seeded_teams()`: people-ops, security.
    """

    model = "stub"
    dimensions = 2

    def __init__(self, doc_vectors, query_vector):
        self._docs = doc_vectors
        self._query = query_vector

    def embed_documents(self, texts):
        return self._docs[: len(texts)]

    def embed_query(self, text):
        return self._query


def _service_with_embedder(embedder):
    service = _seeded_service()
    service.embedder = embedder
    return AnswerService(service, _seeded_teams(), None)


def _resolve(embedder, name="HR"):
    tools = Toolbox(_service_with_embedder(embedder)._service, _seeded_teams())
    return tools.resolve_team(name)


def test_semantic_resolution_accepts_an_unambiguous_match():
    # people-ops 0.95, security 0.10 -> clear of the floor and the field.
    embedder = _StubEmbedder([[1.0, 0.0], [0.0, 1.0]], [0.99, 0.10])
    result = _resolve(embedder, "human resources")
    assert result["match"]["slug"] == "people-ops"
    assert result["match"]["matched_by"].startswith("semantic:")


def test_semantic_resolution_rejects_a_photo_finish():
    """The real failure: measured against three teams, "HR" put the *wrong*
    team ahead by 0.006. Both scores clear any sane floor, so only the margin
    can tell them apart."""
    embedder = _StubEmbedder([[1.0, 0.0], [0.0, 1.0]], [0.751, 0.745])
    result = _resolve(embedder)
    assert result["match"] is None
    assert {c["slug"] for c in result["candidates"]} == {"people-ops", "security"}


def test_semantic_resolution_rejects_a_team_that_does_not_exist():
    """"marketing" and "kitchen staff" scored 0.66 and 0.57 against unrelated
    teams. A floor loose enough to accept paraphrases accepts these too."""
    embedder = _StubEmbedder([[1.0, 0.0], [0.0, 1.0]], [0.66, 0.60])
    assert _resolve(embedder, "marketing")["match"] is None


def test_a_rejected_match_still_hands_back_candidates_to_recover_from():
    """Rejection must not be a dead end — the planner resolves it from here,
    and a language model knows HR means Human Resources."""
    embedder = _StubEmbedder([[1.0, 0.0], [0.0, 1.0]], [0.60, 0.59])
    result = _resolve(embedder)
    assert result["match"] is None
    assert len(result["candidates"]) == 2


# ---- Facets and sort --------------------------------------------------------


def test_facets_count_a_dimension_without_its_own_filter(wired):
    """The classic faceting bug: picking one owner zeroes every other owner.

    Each dimension has to be counted with the *other* filters applied but not
    its own, or the control that exists to show you alternatives erases them
    and you can never switch without clearing first.
    """
    body = wired.get(
        "/api/v1/search/facets", params={"owner": "people-ops", "include_drafts": True}
    ).json()
    owners = {f["value"]: f["count"] for f in body["owner"]}
    # security is still offered, and still counted, despite the owner filter.
    assert owners == {"people-ops": 3, "security": 1}
    # Other dimensions *are* narrowed by the active owner filter.
    assert {f["value"] for f in body["authority_level"]} == {"policy", "how-to"}
    assert body["total"] == 3


def test_facets_over_a_query_count_lexical_matches_only(wired):
    """Not every hit — a dense retriever scores the whole corpus.

    Counting hits would put the corpus size behind every facet value, which is
    true and useless. BM25 matching means the document contains a word that was
    actually typed.
    """
    body = wired.get(
        "/api/v1/search/facets", params={"q": "checklist", "include_drafts": True}
    ).json()
    assert body["total"] == 2  # onboarding + offboarding, not all four
    assert {f["value"] for f in body["branch"]} == {"people"}


def test_facets_with_no_query_describe_the_whole_corpus(wired):
    body = wired.get("/api/v1/search/facets", params={"include_drafts": True}).json()
    assert body["total"] == 4
    assert {f["value"]: f["count"] for f in body["branch"]} == {"people": 3, "security": 1}


def test_facets_are_ordered_by_count_then_name(wired):
    body = wired.get("/api/v1/search/facets", params={"include_drafts": True}).json()
    counts = [f["count"] for f in body["owner"]]
    assert counts == sorted(counts, reverse=True)


def test_facets_hide_drafts_unless_asked(wired):
    published = wired.get("/api/v1/search/facets").json()
    assert published["total"] == 3
    with_drafts = wired.get(
        "/api/v1/search/facets", params={"include_drafts": True}
    ).json()
    assert with_drafts["total"] == 4


def test_sort_reorders_the_page_without_changing_which_page(wired):
    """Sorting runs after ranking, so it cannot smuggle in different results.

    Sorting by date *before* the cut would return the newest documents in the
    corpus rather than the newest relevant ones — a different question.
    """
    params = {"q": "checklist access leave", "limit": 2, "include_drafts": True}
    by_relevance = wired.get("/api/v1/search", params=params).json()
    by_date = wired.get("/api/v1/search", params={**params, "sort": "updated"}).json()
    assert {h["asset"]["id"] for h in by_relevance} == {
        h["asset"]["id"] for h in by_date
    }


def test_sort_by_authority_puts_policy_first(wired):
    hits = wired.get(
        "/api/v1/search",
        params={"q": "leave access checklist", "sort": "authority", "include_drafts": True},
    ).json()
    levels = [h["asset"]["authority_level"] for h in hits]
    order = ["policy", "standard", "guideline", "how-to", "note"]
    assert levels == sorted(levels, key=order.index)


def test_sort_by_updated_is_newest_first(wired):
    hits = wired.get(
        "/api/v1/search",
        params={"q": "leave access checklist", "sort": "updated", "include_drafts": True},
    ).json()
    stamps = [h["asset"]["updated_at"] for h in hits]
    assert stamps == sorted(stamps, reverse=True)


def test_an_unknown_sort_is_rejected(wired):
    assert (
        wired.get("/api/v1/search", params={"q": "leave", "sort": "vibes"}).status_code
        == 422
    )


# ---- the entity layer's tools ----------------------------------------------
#
# Questions whose answers live in the edges rather than in any document. The
# planner needs a credential to *phrase* an answer, so these exercise the
# Toolbox directly — which is where the retrieval-vs-traversal distinction
# actually lives.


def test_graph_tools_answer_a_question_no_document_contains():
    from app.answering import Toolbox
    from app.reimbursements import SubmittedDocument
    from tests import world as test_world

    world = test_world.fresh()
    for hint in (
        "total=64.20 category=meals",
        "total=32.50 category=travel",
        "total=41.00 category=meals",
    ):
        world.expenses.submit(
            "john.smith@customer.example",
            documents=[SubmittedDocument(hint=hint)],
            commentary="Client visit to Acme.",
        )

    tools = Toolbox(world.knowledge, None, graph=world.graph)
    resolved = tools.resolve_entity("Employee", "john.smith@customer.example")
    assert resolved["resolved"] is not None

    rollup = tools.aggregate_edges(
        resolved["resolved"]["id"],
        "submitted:out>has_expense_line:out",
        sum_field="requested_usd",
        group_by="category",
        since_days=180,
    )
    assert rollup["count"] == 3
    assert rollup["total"] == pytest.approx(137.70)
    assert rollup["grouped"]["meals"] == pytest.approx(105.20)
    # The step is recorded, so the traversal is auditable the way a citation is.
    assert any(step.tool == "aggregate_edges" for step in tools.steps)


def test_an_ambiguous_name_is_not_guessed_at():
    from app.answering import Toolbox
    from app.graph import Claim
    from tests import world as test_world

    world = test_world.fresh()
    world.graph.upsert(
        "Vendor", "Acme Industrial Inc.",
        [Claim(field="name", value="Acme Industrial Inc.", source="erp:vendors")],
    )
    tools = Toolbox(world.knowledge, None, graph=world.graph)
    outcome = tools.resolve_entity("Vendor", "Acme Industial Inc")
    assert outcome["resolved"] is None
    assert outcome["candidates"][0]["label"] == "Acme Industrial Inc."
    assert "rather than guessing" in outcome["note"]


def test_graph_tools_degrade_politely_without_an_entity_layer():
    from app.answering import Toolbox
    from tests import world as test_world

    world = test_world.fresh()
    tools = Toolbox(world.knowledge, None, graph=None)
    assert "error" in tools.resolve_entity("Vendor", "Acme")
    assert "error" in tools.aggregate_edges("x", "issued:out")


def test_describe_entity_surfaces_retained_conflicts():
    from app.answering import Toolbox
    from app.sync import SyncRequest
    from tests import world as test_world

    world = test_world.fresh()
    for source, terms in (("erp-vendors", "NET30"), ("procurement-coupa", "NET60")):
        world.sync.deliver(SyncRequest.model_validate({
            "source": source,
            "vendors": [{
                "external_id": f"{source}-1", "name": "Acme Industrial Inc.",
                "payment_terms": terms,
            }],
        }), actor="system:test")
    tools = Toolbox(world.knowledge, None, graph=world.graph)
    resolved = tools.resolve_entity("Vendor", "Acme Industrial Inc.")
    described = tools.describe_entity(resolved["resolved"]["id"])
    assert described["fields"]["payment_terms"] == "NET30"
    assert {c["source"] for c in described["conflicts"]["payment_terms"]} == {
        "erp:erp-vendors", "procurement:procurement-coupa"
    }
