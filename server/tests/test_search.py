"""Hybrid retrieval: fusion, the vector index, and graceful degradation.

These run against `HashingEmbedder`, which is deterministic and offline. It has
no semantic understanding, so nothing here asserts that a paraphrase ranks well
— that would be testing the encoder, not this code. What is tested is the
plumbing: that both retrievers contribute, that provenance is reported
honestly, that stale vectors are rejected, and that every failure path lands on
lexical rather than on an exception.
"""

import pytest

import app.deps as deps
from app.config import settings
from app.embeddings import HashingEmbedder, content_fingerprint, embedding_text
from app.models import Asset
from app.search import VectorIndex, reciprocal_rank_fusion
from app.service import KnowledgeService, seed_if_empty
from app.storage import (
    EmbeddingRecord,
    InMemoryEmbeddingRepository,
    InMemoryRepository,
)
from app.users import InMemoryUserDirectory


def _service(**kwargs) -> KnowledgeService:
    service = KnowledgeService(
        InMemoryRepository(),
        embedder=HashingEmbedder(dimensions=64),
        embedding_repo=InMemoryEmbeddingRepository(),
        **kwargs,
    )
    seed_if_empty(service, settings.seed_dir)
    service.warm_embeddings()
    return service


@pytest.fixture()
def hybrid_client(_client):
    deps._service = _service()
    deps._user_directory = InMemoryUserDirectory()
    return _client


# ---- Rank fusion ------------------------------------------------------------


def test_rrf_rewards_agreement_between_retrievers():
    """A document both retrievers rank mid-list beats one that tops only one."""
    scores = reciprocal_rank_fusion(
        [(["only-lexical", "agreed"], 1.0), (["only-semantic", "agreed"], 1.0)]
    )
    assert scores["agreed"] > scores["only-lexical"]
    assert scores["agreed"] > scores["only-semantic"]


def test_rrf_weight_zero_removes_a_retriever():
    scores = reciprocal_rank_fusion([(["a"], 1.0), (["b"], 0.0)])
    assert "b" not in scores


def test_rrf_k_damps_the_top_of_each_list():
    """Rank 1 must not swamp everything below it, or fusion is just a max()."""
    scores = reciprocal_rank_fusion([(["first", "second"], 1.0)], k=60)
    assert scores["first"] / scores["second"] < 1.1


def test_rrf_missing_from_a_list_contributes_nothing():
    """This is what lets a partially-embedded corpus degrade smoothly."""
    both = reciprocal_rank_fusion([(["x"], 1.0), (["x"], 1.0)])
    one = reciprocal_rank_fusion([(["x"], 1.0), ([], 1.0)])
    assert both["x"] > one["x"]


# ---- Query terms ------------------------------------------------------------


def test_stopwords_are_dropped_from_queries():
    from app.search import query_terms

    assert query_terms("what to do when systems are breached") == [
        "systems",
        "breached",
    ]


def test_an_all_stopword_query_still_searches():
    """Dropping every term would turn a vague question into zero results."""
    from app.search import query_terms

    assert query_terms("how do I") == ["how", "do", "i"]


def test_stopwords_do_not_manufacture_lexical_matches():
    """The regression this fixes: noise matches outranking the real answer.

    A natural-language question used to pull back every document that happened
    to contain "what" or "are", each with a noise-level BM25 score. Under rank
    fusion those spurious matches then beat the one document semantic search
    had correctly identified, because appearing in both rankings outweighs
    ranking first in one.
    """
    service = _service()
    lexical = service.search("what to do when systems are breached", mode="lexical")
    # Only documents sharing a content word may match at all.
    assert all(
        "system" in h.asset.title.lower()
        or "system" in service.get(h.asset.id).content.lower()
        or "breach" in service.get(h.asset.id).content.lower()
        for h in lexical
    )


def test_index_still_contains_stopwords():
    """Query-side only — so this is tunable without a reindex."""
    from app.search import SearchIndex, tokenize

    index = SearchIndex()
    index.upsert(
        Asset(id="d", path="p", title="T", content="the quick brown fox")
    )
    assert "the" in tokenize("the quick brown fox")
    # Searching a bare stopword falls back to it, and the index can answer.
    assert index.search("the")


# ---- Vector index -----------------------------------------------------------


def test_vector_index_ranks_by_cosine():
    index = VectorIndex()
    index.upsert("same", [1.0, 0.0])
    index.upsert("orthogonal", [0.0, 1.0])
    index.upsert("opposite", [-1.0, 0.0])
    ranked = index.search([1.0, 0.0])
    assert [doc_id for doc_id, _ in ranked] == ["same", "orthogonal", "opposite"]
    assert ranked[0][1] == pytest.approx(1.0)


def test_vector_index_filters_before_truncating():
    """The bug this guards: a limit filled with ineligible docs hides the rest.

    Cosine scores every document, so cutting to `limit` first and filtering
    second can return nothing while eligible matches sit just below the cut.
    """
    index = VectorIndex()
    for i in range(10):
        index.upsert(f"draft-{i}", [1.0, 0.0])
    index.upsert("published", [0.9, 0.1])
    ranked = index.search([1.0, 0.0], limit=3, allowed={"published"})
    assert [doc_id for doc_id, _ in ranked] == ["published"]


def test_vector_index_survives_a_dimension_change():
    """A changed encoder must degrade the query, not raise into the caller."""
    index = VectorIndex()
    index.upsert("old", [1.0, 0.0])
    assert index.search([1.0, 0.0, 0.0]) == []


def test_vector_index_remove_and_retain():
    index = VectorIndex()
    index.upsert("a", [1.0, 0.0])
    index.upsert("b", [0.0, 1.0])
    index.remove("a")
    assert index.doc_ids() == {"b"}
    assert index.retain({"b"}) == 0
    assert index.retain(set()) == 1
    assert len(index) == 0


# ---- Embedding text and fingerprints ----------------------------------------


def test_embedding_text_puts_identity_before_the_body():
    """Truncation cuts the tail, so the title has to survive it."""
    asset = Asset(
        id="x", path="finance/policies/x", title="Expense Policy",
        content="body " * 5000, tags=["finance"],
    )
    text = embedding_text(asset, max_chars=200)
    assert text.startswith("Expense Policy")
    assert "finance" in text
    assert len(text) <= 200


def test_fingerprint_ignores_changes_past_the_truncation_point():
    """An edit the encoder never sees must not trigger a re-embed."""
    base = Asset(id="x", path="p", title="T", content="head " * 100)
    edited = base.model_copy(update={"content": "head " * 100 + "tail"})
    limit = 200
    assert content_fingerprint(embedding_text(base, limit)) == content_fingerprint(
        embedding_text(edited, limit)
    )


def test_vertex_batches_respect_the_token_cap_not_just_the_count():
    """Vertex caps a request on two axes; the token one binds first.

    250 full-length texts is well within the instance cap and roughly 25x over
    the 20,000-token cap, so batching on count alone would send requests the
    API rejects.
    """
    from app.embeddings import VertexEmbedder

    packer = VertexEmbedder.__new__(VertexEmbedder)
    packer.max_batch = 250
    packer.max_batch_tokens = 20_000

    full = "x" * 8000  # ~2000 tokens each
    batches = packer._batches([full] * 250)
    assert len(batches) > 1
    for batch in batches:
        assert len(batch) <= 250
        assert sum(VertexEmbedder._estimated_tokens(t) for t in batch) <= 20_000
    assert sum(len(b) for b in batches) == 250


def test_vertex_batches_respect_the_instance_cap_for_short_texts():
    """Short texts hit the 250-instance cap long before the token budget."""
    from app.embeddings import VertexEmbedder

    packer = VertexEmbedder.__new__(VertexEmbedder)
    packer.max_batch = 250
    packer.max_batch_tokens = 20_000

    batches = packer._batches(["hi"] * 600)
    assert [len(b) for b in batches] == [250, 250, 100]


def test_vertex_never_drops_a_text_that_exceeds_the_budget_alone():
    """A single oversized text still gets its own request rather than vanishing."""
    from app.embeddings import VertexEmbedder

    packer = VertexEmbedder.__new__(VertexEmbedder)
    packer.max_batch = 250
    packer.max_batch_tokens = 100

    batches = packer._batches(["x" * 100_000, "short"])
    assert sum(len(b) for b in batches) == 2


def test_hashing_embedder_is_deterministic_across_instances():
    """Stored vectors outlive the process, so encoding must not be salted."""
    a = HashingEmbedder(dimensions=32).embed_query("expense approval")
    b = HashingEmbedder(dimensions=32).embed_query("expense approval")
    assert a == b
    assert len(a) == 32


# ---- Service-level retrieval ------------------------------------------------


def test_warm_embeddings_covers_the_corpus_then_no_ops():
    service = _service()
    assert len(service.vectors) == len(service.repo.all())
    assert service.embeddings_ready

    # Second pass finds nothing drifted — this is what keeps cold starts cheap.
    again = service.warm_embeddings()
    assert again["status"] == "current"
    assert again["embedded"] == 0


def test_write_path_embeds_so_reconciliation_has_nothing_to_do():
    service = _service()
    service.update("vpn-setup-note", _update(content="totally new text"))
    assert service.warm_embeddings()["embedded"] == 0
    # ...and the new text is what got embedded, not the old.
    stored = {r.asset_id: r for r in service.embedding_repo.all()}
    expected = content_fingerprint(
        embedding_text(service.get("vpn-setup-note"), service.embedder.max_input_chars)
    )
    assert stored["vpn-setup-note"].fingerprint == expected


def test_warm_embeddings_discards_vectors_from_another_model():
    service = _service()
    service.embedding_repo.put_many(
        [
            EmbeddingRecord(
                asset_id="vpn-setup-note",
                vector=[0.0] * 64,
                model="some-older-model",
                fingerprint="stale",
            )
        ]
    )
    result = service.warm_embeddings()
    assert result["embedded"] == 1


def test_stale_model_vectors_are_not_loaded_into_the_index():
    repo = InMemoryRepository()
    vectors = InMemoryEmbeddingRepository()
    vectors.put_many(
        [
            EmbeddingRecord(
                asset_id="ghost", vector=[0.1] * 64,
                model="a-different-model", fingerprint="x",
            )
        ]
    )
    service = KnowledgeService(
        repo, embedder=HashingEmbedder(dimensions=64), embedding_repo=vectors
    )
    assert len(service.vectors) == 0


def test_delete_removes_the_vector_too():
    service = _service()
    before = len(service.vectors)
    service.delete("vpn-setup-note")
    assert len(service.vectors) == before - 1
    assert "vpn-setup-note" not in service.vectors.doc_ids()


def test_search_reports_which_retriever_found_each_hit():
    service = _service()
    hits = service.search("vpn", limit=5)
    assert hits
    assert all(h.retrieval in ("lexical", "semantic", "hybrid") for h in hits)
    for hit in hits:
        if hit.retrieval in ("semantic", "hybrid"):
            assert hit.semantic_rank is not None
            assert hit.similarity is not None
        if hit.retrieval in ("lexical", "hybrid"):
            assert hit.lexical_rank is not None


def test_lexical_mode_reports_bm25_scores_not_fusion_scores():
    """Guards a real bug: rank positions were once returned as scores."""
    service = _service()
    hits = service.search("expense", limit=5, mode="lexical")
    assert hits
    assert all(h.retrieval == "lexical" for h in hits)
    assert all(h.semantic_rank is None for h in hits)
    # BM25 scores are well above 1; rank positions would descend from 1.
    assert hits[0].score > 1.0
    assert hits[0].score >= hits[-1].score


def test_semantic_mode_returns_hits_with_no_shared_vocabulary():
    """A dense retriever scores every document, so it always returns something.

    That is the point of the mode — and also why the caller gets `similarity`
    back, since "best available" is not the same as "relevant".
    """
    service = _service()
    hits = service.search("zzzz-nonexistent-token", limit=3, mode="semantic")
    assert hits
    assert all(h.lexical_rank is None for h in hits)


def test_min_similarity_floor_drops_unrelated_semantic_matches():
    """Off by default; when set, it is what stops a no-match query returning noise."""
    unfiltered = _service()
    assert unfiltered.search("zzzz-nonexistent-token", limit=5, mode="semantic")

    filtered = _service(min_similarity=0.99)
    assert filtered.search("zzzz-nonexistent-token", limit=5, mode="semantic") == []
    # The floor applies to the semantic leg only — lexical matches still land.
    assert filtered.search("expense", limit=5)


def test_search_degrades_to_lexical_when_the_encoder_fails():
    """An embedding outage must cost ranking quality, never the query."""
    service = _service()

    class Broken:
        model, dimensions, max_input_chars = "broken", 64, 8000

        def embed_documents(self, texts):
            raise RuntimeError("upstream is down")

        def embed_query(self, text):
            raise RuntimeError("upstream is down")

    service.embedder = Broken()
    hits = service.search("expense approval", limit=5)
    assert hits
    assert all(h.retrieval == "lexical" for h in hits)


def test_write_survives_an_embedding_failure():
    service = _service()

    class Broken:
        model, dimensions, max_input_chars = "broken", 64, 8000

        def embed_documents(self, texts):
            raise RuntimeError("upstream is down")

        def embed_query(self, text):
            raise RuntimeError("upstream is down")

    service.embedder = Broken()
    asset = service.create(_create(id="resilient", content="unique-token-xyzzy"))
    assert asset.id == "resilient"
    hits = service.search("xyzzy", limit=5)
    assert [h.asset.id for h in hits] == ["resilient"]


def test_drafts_are_excluded_from_both_retrievers():
    service = _service()
    service.create(_create(id="secret-draft", content="quaggamatic", status="draft"))
    assert not any(
        h.asset.id == "secret-draft" for h in service.search("quaggamatic", limit=10)
    )
    assert any(
        h.asset.id == "secret-draft"
        for h in service.search("quaggamatic", limit=10, include_drafts=True)
    )


def test_lexical_only_service_still_searches():
    """The default configuration: no embedder at all."""
    service = KnowledgeService(InMemoryRepository())
    seed_if_empty(service, settings.seed_dir)
    assert not service.hybrid_enabled
    assert service.warm_embeddings() == {"status": "disabled"}
    hits = service.search("expense", limit=5)
    assert hits
    assert all(h.retrieval == "lexical" for h in hits)


# ---- HTTP surface -----------------------------------------------------------


def test_search_status_endpoint_reports_coverage(hybrid_client):
    body = hybrid_client.get("/api/v1/search/status").json()
    assert body["mode"] == "hybrid"
    assert body["embedded"] == body["assets"]
    assert body["ready"] is True


def test_search_mode_is_validated(hybrid_client):
    assert hybrid_client.get(
        "/api/v1/search", params={"q": "expense", "mode": "nonsense"}
    ).status_code == 422


def test_reindex_endpoint_is_idempotent(hybrid_client):
    body = hybrid_client.post("/api/v1/search/reindex").json()
    assert body["status"] in ("ok", "current")
    assert body["failed" if "failed" in body else "embedded"] == 0


def test_health_reports_retrieval_state(hybrid_client):
    body = hybrid_client.get("/health").json()
    assert body["retrieval"]["mode"] == "hybrid"
    assert body["retrieval"]["ready"] is True


def test_health_does_not_leak_operator_detail(hybrid_client):
    """/health is unauthenticated — it must not publish retrieval internals."""
    retrieval = hybrid_client.get("/health").json()["retrieval"]
    assert set(retrieval) == {"mode", "ready"}
    # The full picture stays behind auth.
    assert "model" in hybrid_client.get("/api/v1/search/status").json()


# ---- helpers ----------------------------------------------------------------


def _create(**kwargs):
    from app.models import AssetCreate

    payload = {"id": "x", "title": "X", "content": "", "status": "published"}
    payload.update(kwargs)
    payload.setdefault("path", f"engineering/notes/{payload['id']}")
    return AssetCreate(**payload)


def _update(**kwargs):
    from app.models import AssetUpdate

    return AssetUpdate(**kwargs)


# ---- Encoders that fuse batched input --------------------------------------


def test_single_text_models_are_capped_to_one_text_per_request():
    """gemini-embedding-2 accepts exactly one text per call.

    Verified against the live API: given two unrelated texts it returns *one*
    embedding of the pair fused together. The caller zips vectors against asset
    ids, so a fused response would give one asset a garbage vector built from
    every document and silently leave the rest unembedded.
    """
    from app.embeddings import _max_texts_per_request

    assert _max_texts_per_request("gemini-embedding-2", 250) == 1
    assert _max_texts_per_request("gemini-embedding-2-preview", 250) == 1
    # Batching encoders keep whatever was configured.
    assert _max_texts_per_request("text-embedding-005", 250) == 250
    assert _max_texts_per_request("gemini-embedding-001", 250) == 250


def test_vertex_embedder_raises_when_a_response_is_short():
    """The backstop, in case a future encoder fuses and nobody adds it above.

    A short response must raise rather than return, because every caller of
    `embed_documents` pairs the result positionally with its inputs.
    """
    from app.embeddings import VertexEmbedder

    embedder = VertexEmbedder.__new__(VertexEmbedder)
    embedder.model = "pretend-fusing-model"
    embedder.dimensions = 0
    embedder._lock = __import__("threading").Lock()

    class _OneVector:
        embeddings = [type("E", (), {"values": [0.0, 1.0]})()]

    embedder._client = type(
        "C", (), {"models": type("M", (), {"embed_content": lambda *a, **k: _OneVector()})()}
    )()

    with pytest.raises(RuntimeError, match="fuses batched inputs"):
        embedder._embed(["one", "two", "three"], "RETRIEVAL_DOCUMENT")


def test_batching_respects_the_single_text_cap():
    """Batch packing has to honour the cap, not just record it."""
    from app.embeddings import VertexEmbedder

    embedder = VertexEmbedder.__new__(VertexEmbedder)
    embedder.max_batch = 1
    embedder.max_batch_tokens = 20_000

    batches = embedder._batches(["a", "b", "c"])
    assert [len(b) for b in batches] == [1, 1, 1]


# ---- Signal vs noise in a ranking -------------------------------------------


def test_weak_hits_are_judged_against_the_ranking_not_a_fixed_cosine():
    """Absolute cosine cannot separate them.

    Measured with gemini-embedding-2, nonsense topped out at 0.545 — above
    genuine second-place hits on real queries. Only the lead over the
    background distinguishes them.
    """
    from app.search import is_weak_hit, semantic_profile

    # "vpn": one clear winner over a flat tail.
    clear = semantic_profile([0.7555, 0.5741, 0.5740, 0.5644, 0.5427])
    assert is_weak_hit("semantic", 0.5741, clear) is True
    assert is_weak_hit("semantic", 0.7555, clear) is False

    # Nonsense: nothing leads, so the whole tail is noise — including its top,
    # which outscores the weak hit accepted above under any absolute rule.
    nonsense = semantic_profile([0.5452, 0.5313, 0.5081, 0.4964, 0.4857])
    assert is_weak_hit("semantic", 0.5452, nonsense) is True


def test_a_lexical_match_is_never_weak():
    """A word the caller typed appears in it; no cosine should override that."""
    from app.search import is_weak_hit, semantic_profile

    profile = semantic_profile([0.9, 0.5, 0.5, 0.5])
    assert is_weak_hit("hybrid", 0.5, profile) is False
    assert is_weak_hit("lexical", None, profile) is False


def test_too_few_hits_to_have_a_pack():
    from app.search import is_weak_hit, semantic_profile

    assert semantic_profile([0.9, 0.2]) is None
    assert is_weak_hit("semantic", 0.2, None) is False


def test_mark_weak_hits_stamps_every_hit():
    from app.models import AssetSummary, SearchHit
    from app.search import mark_weak_hits
    from datetime import datetime, timezone

    def hit(asset_id, retrieval, similarity):
        return SearchHit(
            asset=AssetSummary(
                id=asset_id, path=f"p/{asset_id}", title=asset_id,
                content_type="text/markdown", authority_level="note",
                scope="org", owner="", tags=[], status="published",
                updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            ),
            score=1.0, snippet="", retrieval=retrieval, similarity=similarity,
        )

    hits = mark_weak_hits([
        hit("a", "hybrid", 0.76),
        hit("b", "semantic", 0.574),
        hit("c", "semantic", 0.564),
        hit("d", "semantic", 0.543),
    ])
    assert [h.weak for h in hits] == [False, True, True, True]
