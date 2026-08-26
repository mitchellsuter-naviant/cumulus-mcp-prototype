"""Grounded question answering over the knowledge layer.

Retrieval alone cannot answer every question people type into a search bar.
"What is our expense approval policy?" is a retrieval question — the answer is
prose inside a document, and hybrid search finds it. "Which assets are managed
by the HR team?" is not: that answer lives in `Asset.owner`, a team slug, and
no amount of embedding similarity over document *text* will produce it. A
vector search for that question returns HR-flavoured documents, which is a
confidently wrong answer and worse than none.

So this module puts a planner in front of two retrieval planes:

  - the unstructured plane — `search_knowledge`, the existing BM25 + cosine
    fusion, for questions whose answer is written down somewhere;
  - the structured plane — `list_assets`, `list_teams`, `team_assets`, for
    questions about the corpus itself: ownership, authority, staleness, gaps.

`resolve_team` bridges the two. People type "HR", the slug is `people-ops`, and
matching on the string alone fails; when an embedder is configured it falls
back to cosine over team names and descriptions, which is the one place
embeddings genuinely help with a structured question.

Backends mirror embeddings.py: "none" (the default) degrades to a retrieval-only
answer instead of failing, so the endpoint works in local dev and in tests with
no credential and no network. Every generated claim carries a citation to an
asset id, and every tool call is recorded, because an answer nobody can trace
is not usable for governance work.
"""

import json
import logging
from typing import Any, Callable, Protocol

from .config import settings
from .models import (
    AskAnswer,
    AskStep,
    AssetFilter,
    Citation,
)
from .search import make_snippet

log = logging.getLogger("cumulus.answering")

MAX_TOOL_RESULT_CHARS = 12_000


SYSTEM_INSTRUCTION = """\
You are Cumulus, the answering layer over an enterprise knowledge corpus.

The corpus has two halves and you must not confuse them:

1. Document CONTENT — what policies, standards, guidelines, how-tos and notes
   actually say. Reach it with `search_knowledge` and `read_document`.
2. Corpus METADATA — which team owns a document, what authority level it
   carries, whether it is published or still a draft, when it last changed,
   who is on which team. Reach it with `list_assets`, `list_teams`,
   `get_team` and `resolve_team`.

Questions about ownership, coverage, counts, staleness or governance are
METADATA questions. Answer them from `list_assets` and the team tools, never by
searching document text — text search will return topically similar documents
that are not the answer.

Rules:
- Ground every factual claim in a tool result. Never state a policy detail you
  have not read.
- Cite documents by their exact asset id in square brackets, like [expense-
  approval-policy]. Cite the ids you actually used, nothing else.
- When two documents conflict, say so and prefer the higher authority level
  (policy > standard > guideline > how-to > note).
- Flag drafts and deprecated documents when you rely on them; they are
  retrievable but not authoritative.
- If the corpus does not answer the question, say exactly that and name the
  document that should have answered it. Do not fill the gap from general
  knowledge.
- Be brief. A few sentences, or a short markdown list for a set of documents.
"""


class AnswerBackend(Protocol):
    """A planner that turns a question plus a toolbox into a grounded answer."""

    name: str

    def answer(
        self, question: str, context: str, tools: "Toolbox"
    ) -> tuple[str, list[AskStep]]: ...


# ---- Toolbox ----------------------------------------------------------------


class Toolbox:
    """The tool surface exposed to a planner, and the record of what it used.

    Deliberately the same shape as the MCP tools in mcp_server.py plus the
    structured plane. Every call is logged into `steps`, and every asset id the
    planner touched lands in `touched` so citations can be resolved back to real
    documents rather than trusting the model's spelling.
    """

    def __init__(
        self, service, teams, include_drafts: bool = False, graph=None
    ) -> None:
        self._service = service
        self._teams = teams
        self._include_drafts = include_drafts
        # The entity layer. Optional so a deployment without one still answers
        # every document question; the graph tools simply say they have nothing
        # to walk rather than failing the turn.
        self._graph = graph
        self.steps: list[AskStep] = []
        self.touched: dict[str, str] = {}
        self._documents_read = 0

    # -- helpers ----------------------------------------------------------

    def _record(self, tool: str, arguments: dict, summary: str) -> None:
        self.steps.append(AskStep(tool=tool, arguments=arguments, summary=summary))

    def _asset_row(self, asset) -> dict:
        self.touched[asset.id] = asset.title
        return {
            "id": asset.id,
            "title": asset.title,
            "path": asset.path,
            "authority_level": asset.authority_level,
            "status": asset.status,
            "owner": asset.owner or None,
            "tags": asset.tags,
            "updated_at": asset.updated_at.isoformat(),
        }

    # -- the tools --------------------------------------------------------

    def search_knowledge(
        self,
        query: str,
        limit: int = 6,
        owner: str | None = None,
        authority_level: str | None = None,
        status: str | None = None,
    ) -> dict:
        """Hybrid retrieval over document text, optionally narrowed by metadata."""
        filters = AssetFilter(
            owner=owner, authority_level=authority_level, status=status
        )
        hits = self._service.search(
            query,
            limit=min(limit, 10),
            include_drafts=self._include_drafts,
            filters=None if filters.is_empty() else filters,
        )
        results = []
        for hit in hits:
            self.touched[hit.asset.id] = hit.asset.title
            results.append(
                {
                    "id": hit.asset.id,
                    "title": hit.asset.title,
                    "path": hit.asset.path,
                    "authority_level": hit.asset.authority_level,
                    "status": hit.asset.status,
                    "owner": hit.asset.owner or None,
                    "snippet": hit.snippet,
                    "retrieval": hit.retrieval,
                    "similarity": hit.similarity,
                }
            )
        self._record(
            "search_knowledge",
            {k: v for k, v in {
                "query": query, "owner": owner,
                "authority_level": authority_level, "status": status,
            }.items() if v is not None},
            f"{len(results)} hit(s)",
        )
        return {"hits": results}

    def read_document(self, asset_id: str) -> dict:
        """Full text of one document. Capped per question; see answer_max_documents."""
        if self._documents_read >= settings.answer_max_documents:
            self._record("read_document", {"asset_id": asset_id}, "refused: budget")
            return {
                "error": (
                    "Document budget for this question is exhausted. Answer from "
                    "what you have already read."
                )
            }
        try:
            asset = self._service.get(asset_id)
        except Exception:
            self._record("read_document", {"asset_id": asset_id}, "not found")
            return {"error": f"No document with id '{asset_id}'."}
        self._documents_read += 1
        self.touched[asset.id] = asset.title
        self._record("read_document", {"asset_id": asset_id}, asset.title)
        row = self._asset_row(asset)
        row["content"] = asset.content[: settings.embedding_max_input_chars]
        return row

    def list_assets(
        self,
        owner: str | None = None,
        authority_level: str | None = None,
        status: str | None = None,
        tag: str | None = None,
        path_prefix: str | None = None,
    ) -> dict:
        """Metadata query over the corpus. No text matching, no ranking."""
        filters = AssetFilter(
            owner=owner,
            authority_level=authority_level,
            status=status,
            tag=tag,
            path_prefix=path_prefix,
        )
        assets = self._service.list_assets(filters=filters)
        if not self._include_drafts and status is None:
            assets = [a for a in assets if a.status != "draft"]
        rows = [self._asset_row(a) for a in assets]
        self._record(
            "list_assets",
            {k: v for k, v in {
                "owner": owner, "authority_level": authority_level,
                "status": status, "tag": tag, "path_prefix": path_prefix,
            }.items() if v is not None},
            f"{len(rows)} asset(s)",
        )
        return {"count": len(rows), "assets": rows}

    def list_teams(self) -> dict:
        """Every team, with how many assets each one owns."""
        owners: dict[str, int] = {}
        for asset in self._service.list_assets():
            if asset.owner:
                owners[asset.owner] = owners.get(asset.owner, 0) + 1
        rows = [
            {
                "slug": t.slug,
                "name": t.name,
                "description": t.description,
                "members": len(t.members),
                "assets_owned": owners.get(t.slug, 0),
            }
            for t in self._teams.list()
        ]
        self._record("list_teams", {}, f"{len(rows)} team(s)")
        return {"teams": rows}

    def resolve_team(self, name: str) -> dict:
        """Map what a person typed ("HR", "the security folks") onto a slug.

        Exact slug, then case-insensitive name, then substring, then — only if
        an embedder is configured — cosine between the phrase and each team's
        name plus description. The last rung is what makes "HR" find a team
        called "People Operations", which no string comparison can do.
        """
        needle = name.strip().lower()
        teams = self._teams.list()
        if not teams:
            self._record("resolve_team", {"name": name}, "no teams exist")
            return {"match": None, "candidates": []}

        exact = next(
            (t for t in teams if t.slug == needle or t.name.lower() == needle), None
        )
        if exact is None:
            partial = [
                t
                for t in teams
                if needle in t.slug or needle in t.name.lower()
            ]
            exact = partial[0] if len(partial) == 1 else None

        how = "exact"
        if exact is None:
            exact, how = self._resolve_team_semantically(needle, teams)

        summary = f"'{name}' -> {exact.slug} ({how})" if exact else f"'{name}' -> none"
        self._record("resolve_team", {"name": name}, summary)
        return {
            "match": (
                {"slug": exact.slug, "name": exact.name, "matched_by": how}
                if exact
                else None
            ),
            "candidates": [{"slug": t.slug, "name": t.name} for t in teams],
        }

    # A match must clear an absolute floor *and* beat the runner-up by a
    # margin. The floor alone is not enough, and measured scores say why:
    # against three teams, "HR" scored finance-ops 0.627 and people-team 0.621
    # — the wrong team, ahead by 0.006, which is noise. "marketing" and
    # "kitchen staff", teams that do not exist, scored 0.664 and 0.570 against
    # unrelated teams. Any single-threshold rule loose enough to accept a real
    # paraphrase also accepts those, and attributing documents to the wrong
    # owner is the worst failure this function has.
    #
    # The margin is what separates them. Unambiguous matches were far clear of
    # the field ("finance" +0.257, "engineering" +0.130) while every wrong
    # answer sat under +0.04. Rejecting the ambiguous ones is cheap: the caller
    # gets the candidate list and the planner picks, and a language model knows
    # HR means Human Resources means People Team in a way cosine over three
    # short team names does not.
    #
    # Calibrated on a small corpus, so it is deliberately conservative: it
    # fires only when the answer is obvious and defers otherwise.
    _SEMANTIC_FLOOR = 0.70
    _SEMANTIC_MARGIN = 0.10

    def _resolve_team_semantically(self, needle: str, teams: list):
        embedder = getattr(self._service, "embedder", None)
        if embedder is None or not teams:
            return None, "none"
        try:
            import numpy as np

            query = np.asarray(embedder.embed_query(needle), dtype=np.float32)
            texts = [f"{t.name}. {t.description}".strip() for t in teams]
            matrix = np.asarray(embedder.embed_documents(texts), dtype=np.float32)
            scores = matrix @ query
            order = np.argsort(-scores)
            best = int(order[0])
            top = float(scores[best])
            # One team means there is no runner-up to measure against, so the
            # floor is all there is.
            margin = top - float(scores[int(order[1])]) if len(teams) > 1 else 1.0

            if top < self._SEMANTIC_FLOOR or margin < self._SEMANTIC_MARGIN:
                log.info(
                    "Semantic team match rejected as ambiguous",
                    extra={"query": needle, "top": round(top, 3),
                           "margin": round(margin, 3)},
                )
                return None, "none"
            return teams[best], f"semantic:{top:.2f}+{margin:.2f}"
        except Exception:
            log.exception("Semantic team resolution failed")
            return None, "none"

    def get_team(self, slug: str) -> dict:
        """One team: details, member count, and the assets it owns."""
        team = self._teams.get(slug)
        if team is None:
            self._record("get_team", {"slug": slug}, "not found")
            return {"error": f"No team with slug '{slug}'. Try resolve_team first."}
        owned = [
            self._asset_row(a)
            for a in self._service.list_assets(filters=AssetFilter(owner=slug))
        ]
        self._record("get_team", {"slug": slug}, f"{len(owned)} asset(s) owned")
        return {
            "slug": team.slug,
            "name": team.name,
            "description": team.description,
            "member_count": len(team.members),
            "admin_count": len(team.admins()),
            "assets_owned": owned,
        }

    # -- the entity layer -------------------------------------------------
    #
    # These exist because a whole class of question is not retrieval at all.
    # "How much has John Smith submitted in the last six months" is not written
    # in any document — it lives in the edges — so no amount of searching finds
    # it. The planner needs to resolve an entity, walk typed edges, and add up
    # what it finds, and it needs to show that path in its steps so the answer
    # is auditable the way a citation is.

    def resolve_entity(self, type: str, name: str, limit: int = 5) -> dict:
        """Find the canonical entity a name refers to, with the alternatives."""
        if self._graph is None:
            return {"error": "No entity layer is configured in this deployment."}
        from .resolution import ResolutionThresholds, score_candidates

        known = self._graph.find(type)
        candidates = score_candidates([name], "", known)[:limit]
        self._record(
            "resolve_entity", {"type": type, "name": name},
            f"{len(candidates)} candidate {type}(s)"
            + (f", best {candidates[0].label} at {candidates[0].score:.2f}"
               if candidates else ""),
        )
        thresholds = ResolutionThresholds()
        return {
            "type": type,
            "query": name,
            "resolved": (
                {
                    "id": candidates[0].object_id,
                    "label": candidates[0].label,
                    "score": candidates[0].score,
                }
                if candidates and candidates[0].score >= thresholds.auto_link_at
                else None
            ),
            "candidates": [
                {"id": c.object_id, "label": c.label, "score": c.score,
                 "why": c.reason}
                for c in candidates
            ],
            "note": (
                "Nothing scored above the auto-link threshold; say which "
                "candidate you mean rather than guessing."
                if candidates and candidates[0].score < thresholds.auto_link_at
                else ""
            ),
        }

    def describe_entity(self, object_id: str) -> dict:
        """One entity: its resolved fields, its conflicts, and its edges."""
        if self._graph is None:
            return {"error": "No entity layer is configured in this deployment."}
        record = self._graph.get(object_id)
        if record is None:
            return {"error": f"No entity with id '{object_id}'."}
        edges: dict[str, int] = {}
        for direction in ("out", "in"):
            for link, _ in self._graph.neighbors(record.id, None, direction):
                key = f"{link.type} ({direction})"
                edges[key] = edges.get(key, 0) + 1
        self._record(
            "describe_entity", {"object_id": object_id},
            f"{record.type} {record.label}: {len(record.props)} field(s), "
            f"{sum(edges.values())} edge(s)",
        )
        return {
            "id": record.id,
            "type": record.type,
            "label": record.label,
            "status": record.status,
            "fields": record.props,
            "edges": edges,
            "conflicts": {
                field: [
                    {"value": c.value, "source": c.source} for c in claims
                ]
                for field, claims in record.conflicts().items()
            },
            "ref": record.ref(),
        }

    def aggregate_edges(
        self,
        object_id: str,
        path: str,
        sum_field: str = "",
        group_by: str = "",
        since_days: int = 0,
    ) -> dict:
        """Walk a typed path from one entity and add up what is at the end.

        `path` is `link_type:direction` hops joined by `>`, e.g.
        `submitted:out>has_expense_line:out`. Typed and fixed-length on
        purpose: open-ended traversal is where this store stops being the right
        answer, so the shape of the question is declared rather than discovered.
        """
        if self._graph is None:
            return {"error": "No entity layer is configured in this deployment."}
        root = self._graph.get(object_id)
        if root is None:
            return {"error": f"No entity with id '{object_id}'."}
        hops: list[tuple[str, str]] = []
        for hop in path.split(">"):
            link_type, _, direction = hop.strip().partition(":")
            if not link_type:
                continue
            hops.append((link_type, direction or "out"))
        if not hops:
            return {"error": "path must be like 'submitted:out>has_expense_line:out'."}

        cutoff = None
        if since_days:
            from datetime import timedelta

            from .models import utcnow

            cutoff = utcnow() - timedelta(days=since_days)

        reached = self._graph.traverse(root.id, hops)
        rows: list[dict] = []
        for record in reached:
            if cutoff is not None:
                stamp = ""
                for field in ("submitted_at", "invoice_date", "date", "ordered_date"):
                    if record.props.get(field):
                        stamp = str(record.props[field])
                        break
                if stamp:
                    from datetime import datetime

                    try:
                        when = datetime.fromisoformat(stamp)
                    except ValueError:
                        when = None
                    if when is not None:
                        if when.tzinfo is None:
                            when = when.replace(tzinfo=cutoff.tzinfo)
                        if when < cutoff:
                            continue
            rows.append({"id": record.id, "label": record.label, **record.props})

        total = None
        if sum_field:
            total = round(
                sum(float(r.get(sum_field) or 0) for r in rows), 2
            )
        grouped: dict[str, float] = {}
        if group_by and sum_field:
            for row in rows:
                key = str(row.get(group_by) or "—")
                grouped[key] = round(
                    grouped.get(key, 0.0) + float(row.get(sum_field) or 0), 2
                )
        self._record(
            "aggregate_edges",
            {"object_id": object_id, "path": path, "sum_field": sum_field,
             "since_days": since_days},
            f"{len(rows)} record(s) at the end of {path}"
            + (f", {sum_field} totalling {total}" if total is not None else ""),
        )
        return {
            "root": {"id": root.id, "type": root.type, "label": root.label},
            "path": path,
            "count": len(rows),
            "total": total,
            "grouped": grouped or None,
            # Capped: the planner needs the shape and the sum, not every row.
            "rows": rows[:25],
            "truncated": len(rows) > 25,
        }

    # -- dispatch ---------------------------------------------------------

    @property
    def dispatch(self) -> dict[str, Callable[..., dict]]:
        return {
            "search_knowledge": self.search_knowledge,
            "read_document": self.read_document,
            "list_assets": self.list_assets,
            "list_teams": self.list_teams,
            "resolve_team": self.resolve_team,
            "get_team": self.get_team,
            "resolve_entity": self.resolve_entity,
            "describe_entity": self.describe_entity,
            "aggregate_edges": self.aggregate_edges,
        }

    def call(self, name: str, arguments: dict) -> dict:
        fn = self.dispatch.get(name)
        if fn is None:
            return {"error": f"Unknown tool '{name}'."}
        try:
            return fn(**arguments)
        except TypeError as exc:
            return {"error": f"Bad arguments for '{name}': {exc}"}
        except Exception as exc:
            log.exception("Tool %s failed", name)
            return {"error": f"'{name}' failed: {exc}"}


# ---- Tool declarations (Gemini function-calling schema) ---------------------

_AUTHORITY_ENUM = ["policy", "standard", "guideline", "how-to", "note"]
_STATUS_ENUM = ["draft", "published", "deprecated"]

TOOL_DECLARATIONS: list[dict[str, Any]] = [
    {
        "name": "search_knowledge",
        "description": (
            "Search document TEXT with hybrid lexical + semantic retrieval. Use "
            "for questions whose answer is written inside a document. Do not use "
            "it to determine ownership, counts, or authority — use list_assets."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {"type": "STRING", "description": "Natural language query."},
                "limit": {"type": "INTEGER"},
                "owner": {"type": "STRING", "description": "Team slug to restrict to."},
                "authority_level": {"type": "STRING", "enum": _AUTHORITY_ENUM},
                "status": {"type": "STRING", "enum": _STATUS_ENUM},
            },
            "required": ["query"],
        },
    },
    {
        "name": "read_document",
        "description": "Full text of one document by asset id.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"asset_id": {"type": "STRING"}},
            "required": ["asset_id"],
        },
    },
    {
        "name": "list_assets",
        "description": (
            "List documents by METADATA — owner team, authority level, status, "
            "tag, taxonomy path. This is the correct tool for 'which documents "
            "does team X own', 'what policies exist', 'what is still a draft'. "
            "Returns every match, unranked."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "owner": {"type": "STRING", "description": "Team slug, from resolve_team."},
                "authority_level": {"type": "STRING", "enum": _AUTHORITY_ENUM},
                "status": {"type": "STRING", "enum": _STATUS_ENUM},
                "tag": {"type": "STRING"},
                "path_prefix": {"type": "STRING"},
            },
        },
    },
    {
        "name": "list_teams",
        "description": "Every team with member and owned-asset counts.",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "resolve_team",
        "description": (
            "Turn a team name a human typed ('HR', 'security') into its slug. "
            "Always call this before filtering by owner."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {"name": {"type": "STRING"}},
            "required": ["name"],
        },
    },
    {
        "name": "get_team",
        "description": "One team's details plus every asset it owns.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"slug": {"type": "STRING"}},
            "required": ["slug"],
        },
    },
    {
        "name": "resolve_entity",
        "description": (
            "Turn a name into a canonical entity id in the graph. Use this "
            "FIRST for any question about a specific person, vendor, invoice or "
            "purchase order, because the graph is keyed by id and names are "
            "ambiguous. Returns ranked candidates; if nothing clears the "
            "threshold, ask which one is meant instead of guessing."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "type": {
                    "type": "STRING",
                    "description": (
                        "Object type, e.g. Employee, Vendor, Invoice, "
                        "PurchaseOrder."
                    ),
                },
                "name": {"type": "STRING", "description": "The name as written."},
                "limit": {"type": "INTEGER"},
            },
            "required": ["type", "name"],
        },
    },
    {
        "name": "describe_entity",
        "description": (
            "Everything known about one entity: its resolved field values, the "
            "fields where two source systems disagree (both values are kept), "
            "and a count of its edges by type. Use it to see what an entity has "
            "before deciding which path to walk."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "object_id": {
                    "type": "STRING",
                    "description": "Canonical id from resolve_entity.",
                },
            },
            "required": ["object_id"],
        },
    },
    {
        "name": "aggregate_edges",
        "description": (
            "Walk a typed path of edges from one entity and total a field at "
            "the end. This is the tool for counting and summing questions — "
            "'how much has this employee submitted', 'what has this vendor "
            "billed' — whose answers are in no document and cannot be searched "
            "for. Path is `link_type:direction` hops joined by `>`, for example "
            "`submitted:out>has_expense_line:out` from an Employee, or "
            "`issued:out` from a Vendor. Use since_days to bound the window."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "object_id": {"type": "STRING"},
                "path": {
                    "type": "STRING",
                    "description": (
                        "e.g. submitted:out>has_expense_line:out"
                    ),
                },
                "sum_field": {
                    "type": "STRING",
                    "description": (
                        "Numeric field to total, e.g. requested_usd, total_usd."
                    ),
                },
                "group_by": {
                    "type": "STRING",
                    "description": "Field to break the total down by, e.g. category.",
                },
                "since_days": {
                    "type": "INTEGER",
                    "description": "Only records dated within this many days.",
                },
            },
            "required": ["object_id", "path"],
        },
    },
]


# ---- Backends ---------------------------------------------------------------


class VertexAnswerBackend:
    """Gemini function-calling loop on Vertex AI, via the google-genai client.

    Same client library and same Application Default Credentials as
    VertexEmbedder, so enabling this adds no dependency and no new secret — the
    Cloud Run service account already has `roles/aiplatform.user`.

    The loop is bounded on two axes: `answer_max_steps` model turns, and
    `answer_max_documents` full documents (enforced inside the Toolbox). A
    planner that never stops calling tools produces a truncated answer flagged
    as such, rather than an unbounded bill.
    """

    name = "vertex"

    def __init__(
        self,
        project: str | None,
        location: str,
        model: str,
        max_steps: int,
        max_output_tokens: int,
    ) -> None:
        from google import genai

        self._client = genai.Client(vertexai=True, project=project, location=location)
        self._model = model
        self._max_steps = max_steps
        self._max_output_tokens = max_output_tokens

    def answer(
        self, question: str, context: str, tools: Toolbox
    ) -> tuple[str, list[AskStep]]:
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            tools=[types.Tool(function_declarations=TOOL_DECLARATIONS)],
            max_output_tokens=self._max_output_tokens,
            temperature=0.2,
        )
        prompt = f"{context}\n\nQuestion: {question}" if context else question
        contents: list[Any] = [
            types.Content(role="user", parts=[types.Part(text=prompt)])
        ]

        for _ in range(self._max_steps):
            response = self._client.models.generate_content(
                model=self._model, contents=contents, config=config
            )
            candidate = (response.candidates or [None])[0]
            if candidate is None or candidate.content is None:
                break
            contents.append(candidate.content)

            calls = [p.function_call for p in (candidate.content.parts or []) if p.function_call]
            if not calls:
                return (response.text or "").strip(), tools.steps

            replies = []
            for call in calls:
                result = tools.call(call.name, dict(call.args or {}))
                replies.append(
                    types.Part.from_function_response(
                        name=call.name, response=_clip(result)
                    )
                )
            contents.append(types.Content(role="user", parts=replies))

        raise StepBudgetExhausted()


class StepBudgetExhausted(Exception):
    """The planner kept calling tools past `answer_max_steps`."""


def _clip(result: dict) -> dict:
    """Keep one tool result from swallowing the context window."""
    encoded = json.dumps(result, default=str)
    if len(encoded) <= MAX_TOOL_RESULT_CHARS:
        return result
    return {
        "truncated": True,
        "note": (
            "Result too large; showing a prefix. Narrow the query with more "
            "specific filters."
        ),
        "data": encoded[:MAX_TOOL_RESULT_CHARS],
    }


def build_answer_backend(
    backend: str,
    project: str | None,
    location: str,
    model: str,
    max_steps: int,
    max_output_tokens: int,
) -> AnswerBackend | None:
    """None means "no generation" — /ask degrades to ranked documents."""
    if backend == "none":
        return None
    if backend == "vertex":
        return VertexAnswerBackend(
            project=project,
            location=location,
            model=model,
            max_steps=max_steps,
            max_output_tokens=max_output_tokens,
        )
    raise ValueError(f"Unknown ANSWER_BACKEND: {backend}")


# ---- The service ------------------------------------------------------------


class AnswerService:
    """Answers questions, with or without a generative backend behind it."""

    def __init__(self, service, teams, backend: AnswerBackend | None, graph=None) -> None:
        self._service = service
        self._teams = teams
        self._backend = backend
        self._graph = graph

    @property
    def enabled(self) -> bool:
        return self._backend is not None

    def status(self) -> dict:
        return {
            "mode": self._backend.name if self._backend else "retrieval",
            "model": settings.answer_model if self._backend else None,
        }

    def ask(
        self,
        question: str,
        scope_asset_id: str | None = None,
        include_drafts: bool = False,
    ) -> AskAnswer:
        tools = Toolbox(
            self._service, self._teams, include_drafts=include_drafts,
            graph=self._graph,
        )
        context = self._scope_context(question, scope_asset_id, tools)

        if self._backend is None:
            return self._retrieval_answer(question, tools, include_drafts)

        try:
            text, steps = self._backend.answer(question, context, tools)
        except StepBudgetExhausted:
            log.warning("Answer planner exhausted its step budget", extra={"q": question})
            fallback = self._retrieval_answer(question, tools, include_drafts)
            fallback.truncated = True
            fallback.answer = (
                "I could not settle on an answer within the step budget. The most "
                "relevant documents are below.\n\n" + fallback.answer
            )
            return fallback
        except Exception:
            log.exception("Answer backend failed; falling back to retrieval")
            return self._retrieval_answer(question, tools, include_drafts)

        if not text:
            return self._retrieval_answer(question, tools, include_drafts)

        return AskAnswer(
            answer=text,
            citations=self._citations(text, question, tools),
            steps=steps,
            backend=self._backend.name,
        )

    def _scope_context(
        self, question: str, scope_asset_id: str | None, tools: Toolbox
    ) -> str:
        """Prepend the open document, if any.

        "Does this apply to contractors?" is a different question with an asset
        open than it is from a blank console, and the planner cannot know which
        document "this" is unless we say so.
        """
        if not scope_asset_id:
            return ""
        try:
            asset = self._service.get(scope_asset_id)
        except Exception:
            return ""
        tools.touched[asset.id] = asset.title
        body = asset.content[: settings.embedding_max_input_chars]
        return (
            "The person asking is currently reading this document. Treat "
            '"this" / "here" as referring to it, but still verify against the '
            "rest of the corpus.\n\n"
            f"<open_document id=\"{asset.id}\" path=\"{asset.path}\" "
            f'authority="{asset.authority_level}" status="{asset.status}">\n'
            f"# {asset.title}\n{body}\n</open_document>"
        )

    def _citations(
        self, text: str, question: str, tools: Toolbox
    ) -> list[Citation]:
        """Resolve [asset-id] mentions against documents the planner actually read.

        Only ids in `touched` are accepted. A model that invents a plausible id
        gets no citation rather than a link to nothing, and the missing citation
        is a visible signal that the claim is unsupported.
        """
        cited: list[Citation] = []
        seen: set[str] = set()
        for asset_id in tools.touched:
            if asset_id in seen or f"[{asset_id}]" not in text:
                continue
            seen.add(asset_id)
            try:
                asset = self._service.get(asset_id)
            except Exception:
                continue
            cited.append(
                Citation(
                    asset_id=asset.id,
                    title=asset.title,
                    path=asset.path,
                    authority_level=asset.authority_level,
                    status=asset.status,
                    snippet=make_snippet(asset.content, question, width=200),
                )
            )
        return cited

    def _retrieval_answer(
        self, question: str, tools: Toolbox, include_drafts: bool
    ) -> AskAnswer:
        """The no-generation path: ranked documents, stated as such.

        This is not a degraded imitation of a prose answer — it says plainly
        that nothing summarized the corpus, and hands over the sources. Writing
        confident prose here would misrepresent how the answer was reached.
        """
        hits = self._service.search(question, limit=5, include_drafts=include_drafts)
        if not hits:
            return AskAnswer(
                answer=(
                    "Nothing in the governed corpus matches that question. Either "
                    "the document that should answer it does not exist yet, or it "
                    "is filed under wording this search did not reach."
                ),
                steps=tools.steps,
                backend="retrieval",
            )
        lines = [
            "Answer generation is not enabled, so here are the documents that "
            "best match the question, best first:",
            "",
        ]
        citations = []
        for hit in hits:
            flag = "" if hit.asset.status == "published" else f" ({hit.asset.status})"
            lines.append(
                f"- **{hit.asset.title}**{flag} — {hit.asset.authority_level}, "
                f"`{hit.asset.path}` [{hit.asset.id}]"
            )
            citations.append(
                Citation(
                    asset_id=hit.asset.id,
                    title=hit.asset.title,
                    path=hit.asset.path,
                    authority_level=hit.asset.authority_level,
                    status=hit.asset.status,
                    snippet=hit.snippet,
                )
            )
        return AskAnswer(
            answer="\n".join(lines),
            citations=citations,
            steps=tools.steps,
            backend="retrieval",
        )
