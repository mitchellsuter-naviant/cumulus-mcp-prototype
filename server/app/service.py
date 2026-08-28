"""Knowledge service: CRUD + search + hierarchy context over a repository."""

import logging
import os
import re
import threading

from pydantic import ValidationError

from .embeddings import Embedder, content_fingerprint, embedding_text
from .models import (
    AUTHORITY_ORDER,
    Asset,
    AssetCreate,
    AssetFilter,
    AssetUpdate,
    FacetValue,
    SearchFacets,
    SearchHit,
    utcnow,
)
from .search import (
    SearchIndex,
    VectorIndex,
    make_snippet,
    mark_weak_hits,
    reciprocal_rank_fusion,
)
from .storage import AssetRepository, EmbeddingRecord, EmbeddingRepository

log = logging.getLogger("cumulus.service")


class AssetExistsError(Exception):
    pass


class AssetNotFoundError(Exception):
    pass


class AssetInvalidError(Exception):
    """An update would produce an invalid asset (e.g. an empty `path`)."""

    def __init__(self, errors: list[dict]) -> None:
        super().__init__("Invalid asset update")
        self.errors = errors


class KnowledgeService:
    """CRUD plus retrieval.

    Retrieval is hybrid when an embedder is configured: BM25 and cosine run
    independently and their rankings are fused. When `embedder` is None — the
    local default — everything below the fusion step is skipped and search is
    exactly the lexical behaviour it was before. That is also the failure mode:
    an embedding call that raises degrades one query to lexical rather than
    failing it.
    """

    def __init__(
        self,
        repo: AssetRepository,
        embedder: Embedder | None = None,
        embedding_repo: EmbeddingRepository | None = None,
        lexical_weight: float = 1.0,
        semantic_weight: float = 0.8,
        candidates: int = 50,
        min_similarity: float = 0.0,
    ) -> None:
        self.repo = repo
        self.index = SearchIndex()
        self.embedder = embedder
        self.embedding_repo = embedding_repo
        self.vectors = VectorIndex()
        self.lexical_weight = lexical_weight
        self.semantic_weight = semantic_weight
        self.candidates = candidates
        self.min_similarity = min_similarity
        # Set once the startup reconciliation has finished, so /health can say
        # whether semantic results are complete or still warming.
        self.embeddings_ready = embedder is None
        self._warm_lock = threading.Lock()
        self.reindex()

    @property
    def hybrid_enabled(self) -> bool:
        return self.embedder is not None and self.embedding_repo is not None

    # ---- CRUD -------------------------------------------------------------

    def reindex(self) -> None:
        self.index.rebuild(self.repo.all())
        self._load_vectors()

    def _load_vectors(self) -> None:
        """Populate the vector index from storage. No model calls."""
        if not self.hybrid_enabled:
            return
        try:
            records = self.embedding_repo.all()
        except Exception:
            log.exception("Could not load stored embeddings; search stays lexical")
            return
        usable = [
            (r.asset_id, r.vector)
            for r in records
            if r.model == self.embedder.model
            and len(r.vector) == self.embedder.dimensions
        ]
        self.vectors.upsert_many(usable)
        if len(usable) != len(records):
            log.info(
                "Ignored embeddings from a previous model",
                extra={"stale": len(records) - len(usable), "model": self.embedder.model},
            )

    def list_assets(
        self,
        prefix: str | None = None,
        status: str | None = None,
        filters: AssetFilter | None = None,
    ) -> list[Asset]:
        """The structured plane: metadata predicates, no ranking involved.

        `prefix` and `status` predate `filters` and are kept because the REST
        route and MCP tool both spell them that way; `filters` is the general
        form the answer planner uses.
        """
        assets = self.repo.all()
        if prefix:
            prefix = prefix.strip("/")
            assets = [
                a for a in assets if a.path == prefix or a.path.startswith(prefix + "/")
            ]
        if status:
            assets = [a for a in assets if a.status == status]
        if filters is not None:
            assets = [a for a in assets if filters.matches(a)]
        return sorted(assets, key=lambda a: a.path)

    def get(self, asset_id: str) -> Asset:
        asset = self.repo.get(asset_id)
        if asset is None:
            raise AssetNotFoundError(asset_id)
        return asset

    def create(self, payload: AssetCreate) -> Asset:
        if self.repo.get(payload.id) is not None:
            raise AssetExistsError(payload.id)
        asset = Asset(**payload.model_dump())
        self.repo.put(asset)
        self.index.upsert(asset)
        self._embed_one(asset)
        return asset

    def update(self, asset_id: str, payload: AssetUpdate) -> Asset:
        current = self.get(asset_id)
        changes = payload.model_dump(exclude_unset=True)
        # Re-validate through the model rather than model_copy(), which skips
        # validators — otherwise PUT could store a `path` that POST rejects.
        try:
            asset = Asset.model_validate(
                {
                    **current.model_dump(),
                    **changes,
                    "updated_at": utcnow(),
                    # Every write is a new revision; refs pin against this.
                    "revision": current.revision + 1,
                }
            )
        except ValidationError as exc:
            # errors() carries the original exception object in `ctx`, which is
            # not JSON serializable — keep only the reportable fields.
            raise AssetInvalidError(
                [
                    {"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]}
                    for e in exc.errors(include_url=False)
                ]
            ) from exc
        self.repo.put(asset)
        self.index.upsert(asset)
        self._embed_one(asset)
        return asset

    def delete(self, asset_id: str) -> None:
        if not self.repo.delete(asset_id):
            raise AssetNotFoundError(asset_id)
        self.index.remove(asset_id)
        self.vectors.remove(asset_id)
        if self.embedding_repo is not None:
            try:
                self.embedding_repo.delete(asset_id)
            except Exception:
                # The vector is already out of the live index; a leftover row
                # is reconciled away on the next warm-up.
                log.warning("Could not delete stored embedding", extra={"assetId": asset_id})

    # ---- Embedding --------------------------------------------------------

    def _embed_one(self, asset: Asset) -> None:
        """Embed a single asset on the write path.

        Deliberately non-fatal: a document that fails to embed is still
        created, still lexically searchable, and gets picked up by the next
        reconciliation. Losing a write because an embedding endpoint is having
        a bad minute would be the worse trade.
        """
        if not self.hybrid_enabled:
            return
        text = embedding_text(asset, self.embedder.max_input_chars)
        try:
            vector = self.embedder.embed_documents([text])[0]
        except Exception:
            log.exception("Embedding failed; asset stays lexical-only",
                          extra={"assetId": asset.id})
            return
        self.vectors.upsert(asset.id, vector)
        try:
            self.embedding_repo.put_many(
                [
                    EmbeddingRecord(
                        asset_id=asset.id,
                        vector=vector,
                        model=self.embedder.model,
                        fingerprint=content_fingerprint(text),
                    )
                ]
            )
        except Exception:
            log.exception("Could not persist embedding", extra={"assetId": asset.id})

    def warm_embeddings(self) -> dict:
        """Bring stored vectors in line with the corpus. Safe to call anytime.

        Embeds only what drifted — a new asset, edited text, or a changed
        model. Metadata-only edits and restarts cost nothing, which is what
        keeps Cloud Run cold starts from re-embedding the whole corpus.
        """
        if not self.hybrid_enabled:
            return {"status": "disabled"}
        # A second caller waits rather than issuing the same embedding calls.
        with self._warm_lock:
            assets = self.repo.all()
            try:
                stored = {r.asset_id: r for r in self.embedding_repo.all()}
            except Exception:
                log.exception("Could not read stored embeddings; skipping warm-up")
                return {"status": "error"}

            pending: list[tuple[Asset, str]] = []
            for asset in assets:
                text = embedding_text(asset, self.embedder.max_input_chars)
                record = stored.get(asset.id)
                if (
                    record is not None
                    and record.model == self.embedder.model
                    and record.fingerprint == content_fingerprint(text)
                    and len(record.vector) == self.embedder.dimensions
                ):
                    continue
                pending.append((asset, text))

            dropped = self.vectors.retain({a.id for a in assets})
            if not pending:
                self.embeddings_ready = True
                log.info(
                    "Embeddings already current",
                    extra={"assets": len(assets), "dropped": dropped},
                )
                return {"status": "current", "embedded": 0, "total": len(assets)}

            log.info(
                "Embedding backfill starting",
                extra={"pending": len(pending), "total": len(assets),
                       "model": self.embedder.model},
            )
            embedded = failed = 0
            batch_size = max(1, getattr(self.embedder, "max_batch", 64))
            for start in range(0, len(pending), batch_size):
                chunk = pending[start : start + batch_size]
                try:
                    vectors = self.embedder.embed_documents([t for _, t in chunk])
                except Exception:
                    # One bad batch must not abandon the rest of the corpus.
                    log.exception("Embedding batch failed", extra={"size": len(chunk)})
                    failed += len(chunk)
                    continue
                self.vectors.upsert_many(
                    [(a.id, v) for (a, _), v in zip(chunk, vectors)]
                )
                try:
                    self.embedding_repo.put_many(
                        [
                            EmbeddingRecord(
                                asset_id=asset.id,
                                vector=vector,
                                model=self.embedder.model,
                                fingerprint=content_fingerprint(text),
                            )
                            for (asset, text), vector in zip(chunk, vectors)
                        ]
                    )
                except Exception:
                    # The vectors are live in memory; persistence retries next
                    # warm-up. Cheaper than discarding the work.
                    log.exception("Could not persist embedding batch")
                embedded += len(chunk)

            self.embeddings_ready = failed == 0
            log.info(
                "Embedding backfill complete",
                extra={"embedded": embedded, "failed": failed, "dropped": dropped},
            )
            return {
                "status": "ok" if not failed else "partial",
                "embedded": embedded,
                "failed": failed,
                "total": len(assets),
            }

    def warm_embeddings_async(self) -> None:
        """Reconcile off the startup path.

        Embedding a large corpus can take longer than a container is given to
        become healthy, and the service is perfectly useful before it finishes
        — RRF just contributes nothing for documents that have no vector yet,
        so results start lexical and sharpen as vectors land.
        """
        if not self.hybrid_enabled:
            return
        threading.Thread(
            target=self._warm_quietly, name="embedding-warmup", daemon=True
        ).start()

    def _warm_quietly(self) -> None:
        try:
            self.warm_embeddings()
        except Exception:
            log.exception("Embedding warm-up thread died")

    # ---- Retrieval --------------------------------------------------------

    def search(
        self,
        query: str,
        limit: int = 10,
        include_drafts: bool = False,
        mode: str = "hybrid",
        filters: AssetFilter | None = None,
        sort: str = "relevance",
    ) -> list[SearchHit]:
        """Ranked hits for a query.

        `mode` is "hybrid" (default), "lexical", or "semantic". Hybrid falls
        back to lexical whenever there is no embedder, no vectors yet, or the
        query fails to encode.

        `filters` narrows the eligible set before either retriever scores
        anything. That ordering is the point: filtering the top ten afterwards
        would let unrelated documents consume the slots and hide the matches
        the caller asked for. It is the same reason `include_drafts` is applied
        here rather than downstream.

        An explicit `filters.status` overrides `include_drafts`: asking for
        drafts is already an unambiguous request, and making the caller pass a
        second flag to be granted what they just asked for reads as a bug.
        """
        explicit_status = filters is not None and filters.status is not None
        eligible = {
            a.id: a
            for a in self.repo.all()
            if (include_drafts or explicit_status or a.status == "published")
            and (filters is None or filters.matches(a))
        }
        if not eligible:
            return []

        lexical_scored: list[tuple[str, float]] = []
        if mode != "semantic":
            # limit=None: the status filter would otherwise discard candidates
            # the index had already truncated away, hiding published docs.
            lexical_scored = [
                (doc_id, score)
                for doc_id, score in self.index.search(query, limit=None)
                if doc_id in eligible
            ][: self.candidates]
        lexical_ids = [doc_id for doc_id, _ in lexical_scored]

        semantic_scored: list[tuple[str, float]] = []
        if mode != "lexical" and self.hybrid_enabled:
            semantic_scored = self._semantic(query, set(eligible))

        if not semantic_scored:
            # Nothing to fuse; keep the raw BM25 scores rather than inventing
            # fusion scores from a single ranking, and report it as lexical.
            return _sorted_hits(
                mark_weak_hits(
                    self._as_hits(
                    query,
                    lexical_scored,
                    eligible,
                    lexical_positions={d: i for i, d in enumerate(lexical_ids, 1)},
                    semantic_positions={},
                    similarities={},
                        limit=limit,
                        lexical_only=True,
                    )
                ),
                sort,
            )

        semantic_ids = [doc_id for doc_id, _ in semantic_scored]
        similarities = dict(semantic_scored)
        fused = reciprocal_rank_fusion(
            [
                (lexical_ids, self.lexical_weight),
                (semantic_ids, self.semantic_weight),
            ]
        )
        ordered = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)
        return _sorted_hits(
            mark_weak_hits(
                self._as_hits(
                    query,
                    ordered,
                    eligible,
                    lexical_positions={d: i for i, d in enumerate(lexical_ids, 1)},
                    semantic_positions={
                        d: i for i, d in enumerate(semantic_ids, 1)
                    },
                    similarities=similarities,
                    limit=limit,
                    lexical_only=False,
                )
            ),
            sort,
        )

    def _semantic(self, query: str, eligible: set[str]) -> list[tuple[str, float]]:
        try:
            vector = self.embedder.embed_query(query)
        except Exception:
            log.exception("Query embedding failed; falling back to lexical")
            return []
        scored = self.vectors.search(vector, limit=self.candidates, allowed=eligible)
        if self.min_similarity > 0:
            scored = [(d, s) for d, s in scored if s >= self.min_similarity]
        return scored

    def _as_hits(
        self,
        query: str,
        ordered: list[tuple[str, float]],
        eligible: dict[str, Asset],
        lexical_positions: dict[str, int],
        semantic_positions: dict[str, int],
        similarities: dict[str, float],
        limit: int,
        lexical_only: bool,
    ) -> list[SearchHit]:
        hits: list[SearchHit] = []
        for doc_id, score in ordered:
            asset = eligible.get(doc_id)
            if asset is None:
                continue
            in_lexical = doc_id in lexical_positions
            in_semantic = doc_id in semantic_positions
            if lexical_only:
                retrieval = "lexical"
            elif in_lexical and in_semantic:
                retrieval = "hybrid"
            elif in_semantic:
                retrieval = "semantic"
            else:
                retrieval = "lexical"
            hits.append(
                SearchHit(
                    asset=asset.summary(),
                    score=round(score, 6),
                    snippet=make_snippet(asset.content, query),
                    retrieval=retrieval,
                    lexical_rank=lexical_positions.get(doc_id),
                    semantic_rank=semantic_positions.get(doc_id),
                    similarity=(
                        round(similarities[doc_id], 4)
                        if doc_id in similarities
                        else None
                    ),
                )
            )
            if len(hits) >= limit:
                break
        return hits

    def search_facets(
        self,
        query: str = "",
        include_drafts: bool = False,
        filters: AssetFilter | None = None,
    ) -> SearchFacets:
        """Refinement counts for a query, one dimension at a time.

        Two things here are deliberate and easy to get wrong.

        First, the counted set is the *lexical* match, not the search result.
        A dense retriever scores every document, so counting over hits would
        put the whole corpus behind every facet value. BM25 matching means the
        document contains a word the user typed, which is a set worth counting
        and one you can explain.

        Second, each dimension is counted with every filter applied *except its
        own*. Counting a dimension against its own filter is the classic
        faceting bug: pick `owner=finance-ops` and every other owner drops to
        zero, so the control that is supposed to show you alternatives instead
        erases them. Excluding the dimension keeps the other owners visible and
        switchable.
        """
        active = filters or AssetFilter()
        candidates = [
            a
            for a in self.repo.all()
            if include_drafts
            or active.status is not None
            or a.status == "published"
        ]

        if query.strip():
            matched = {doc_id for doc_id, _ in self.index.search(query, limit=None)}
            candidates = [a for a in candidates if a.id in matched]

        def counts(dimension: str, key) -> list[FacetValue]:
            # Everything except this dimension's own predicate.
            others = active.model_copy(update={dimension: None})
            tally: dict[str, int] = {}
            for asset in candidates:
                if not others.matches(asset):
                    continue
                value = key(asset)
                if not value:
                    continue
                tally[value] = tally.get(value, 0) + 1
            return [
                FacetValue(value=v, label=v, count=c)
                for v, c in sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))
            ]

        return SearchFacets(
            total=len([a for a in candidates if active.matches(a)]),
            owner=counts("owner", lambda a: a.owner),
            authority_level=counts("authority_level", lambda a: a.authority_level),
            kind=counts("kind", lambda a: a.kind),
            status=counts("status", lambda a: a.status),
            branch=counts("path_prefix", lambda a: a.path.split("/")[0]),
        )

    def retrieval_summary(self) -> dict:
        """The projection safe for an unauthenticated probe.

        Enough for an uptime check to tell "hybrid, warm" from "hybrid, still
        backfilling" from "lexical, misconfigured" — and nothing else. Model
        name, dimensions, coverage counts, and fusion weights are operator
        detail with no reason to be public; they stay behind auth in
        `retrieval_status`.
        """
        return {
            "mode": "hybrid" if self.hybrid_enabled else "lexical",
            "ready": self.embeddings_ready,
        }

    def retrieval_status(self) -> dict:
        """Full retrieval state. Authenticated callers only."""
        total = len(self.repo.all())
        return {
            "mode": "hybrid" if self.hybrid_enabled else "lexical",
            "model": self.embedder.model if self.embedder else None,
            "dimensions": self.embedder.dimensions if self.embedder else None,
            "assets": total,
            "embedded": len(self.vectors),
            "ready": self.embeddings_ready,
            "lexical_weight": self.lexical_weight,
            "semantic_weight": self.semantic_weight,
            "min_similarity": self.min_similarity,
        }

    def context(self, asset_id: str) -> dict:
        """Ancestors, children, siblings, and related docs for an asset."""
        asset = self.get(asset_id)
        by_id = {a.id: a for a in self.repo.all()}

        ancestors: list[Asset] = []
        cursor = asset
        seen = {asset.id}
        while cursor.parent and cursor.parent in by_id and cursor.parent not in seen:
            cursor = by_id[cursor.parent]
            seen.add(cursor.id)
            ancestors.append(cursor)

        children = [a for a in by_id.values() if a.parent == asset.id]
        parent_dir = asset.path.rsplit("/", 1)[0] if "/" in asset.path else ""
        siblings = [
            a
            for a in by_id.values()
            if a.id != asset.id
            and ("/" in a.path and a.path.rsplit("/", 1)[0] == parent_dir)
        ]
        related = [by_id[r] for r in asset.related if r in by_id]

        return {
            "asset": asset.summary(),
            "ancestors": [a.summary() for a in ancestors],
            "children": [a.summary() for a in sorted(children, key=lambda a: a.path)],
            "siblings": [a.summary() for a in sorted(siblings, key=lambda a: a.path)],
            "related": [a.summary() for a in related],
            "superseded_by": asset.superseded_by,
        }

    def tree(self) -> dict:
        """Nested taxonomy tree of published + draft assets for browsing."""
        root: dict = {"name": "", "children": {}, "assets": []}
        for asset in sorted(self.repo.all(), key=lambda a: a.path):
            node = root
            parts = asset.path.split("/")
            for part in parts[:-1]:
                node = node["children"].setdefault(
                    part, {"name": part, "children": {}, "assets": []}
                )
            node["assets"].append(asset.summary().model_dump(mode="json"))
        return _materialize(root)


def _materialize(node: dict) -> dict:
    return {
        "name": node["name"],
        "assets": node["assets"],
        "children": [_materialize(c) for _, c in sorted(node["children"].items())],
    }


# ---- Seeding ----------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)
_LIST_FIELDS = {"tags", "related"}


def parse_seed_file(name: str, text: str) -> Asset:
    """Parse a seed .md/.txt file with simple `key: value` frontmatter."""
    meta: dict = {}
    body = text
    match = _FRONTMATTER_RE.match(text)
    if match:
        body = text[match.end() :]
        for line in match.group(1).splitlines():
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            key, value = key.strip(), value.strip()
            # A value containing a colon has to be quoted to survive the
            # partition above ("Expense policy: travel"); strip the quoting so
            # it does not end up in the stored title.
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1].strip()
            if key in _LIST_FIELDS:
                meta[key] = [v.strip() for v in value.split(",") if v.strip()]
            elif value:
                meta[key] = value
    stem = os.path.splitext(name)[0]
    ext = os.path.splitext(name)[1].lower()
    meta.setdefault("id", stem)
    meta.setdefault("path", stem)
    meta.setdefault("title", stem.replace("-", " ").title())
    meta.setdefault("content_type", "text/markdown" if ext == ".md" else "text/plain")
    meta.setdefault("status", "published")
    return Asset(content=body.strip(), **meta)


def seed_if_empty(service: KnowledgeService, seed_dir: str | None) -> int:
    if not seed_dir or not os.path.isdir(seed_dir) or service.repo.all():
        return 0
    count = 0
    for name in sorted(os.listdir(seed_dir)):
        if not name.lower().endswith((".md", ".txt")):
            continue
        path = os.path.join(seed_dir, name)
        try:
            with open(path, encoding="utf-8") as f:
                service.repo.put(parse_seed_file(name, f.read()))
        except Exception:
            log.exception("Failed to load seed file", extra={"seedFile": name})
            continue
        count += 1
    service.reindex()
    log.info("Seeded knowledge store", extra={"seedDir": seed_dir, "count": count})
    return count


def _sorted_hits(hits: list[SearchHit], sort: str) -> list[SearchHit]:
    """Reorder a ranked page.

    Applied after ranking and truncation, so this reorders the top N rather
    than choosing a different N. Sorting by date before the cut would return
    the newest documents in the corpus rather than the newest *relevant* ones,
    which is a different question than the one that was asked.
    """
    if sort == "updated":
        return sorted(hits, key=lambda h: h.asset.updated_at, reverse=True)
    if sort == "authority":
        return sorted(
            hits,
            key=lambda h: (
                # None (a prompt, a schema export) sorts after every document
                # that actually carries authority.
                AUTHORITY_ORDER.index(h.asset.authority_level)
                if h.asset.authority_level in AUTHORITY_ORDER
                else len(AUTHORITY_ORDER),
                -h.score,
            ),
        )
    return hits
