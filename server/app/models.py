from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

ContentType = Literal["text/markdown", "text/plain"]
AuthorityLevel = Literal["policy", "standard", "guideline", "how-to", "note"]
Status = Literal["draft", "published", "deprecated"]

# The doctrine's two Dataset dials, on the corpus. `kind` is what shape of
# thing this is; a `document` argues and carries authority, a `prompt` or a
# `config` is governed machinery, a `schema-export` is generated from Python on
# every startup and editing it here is pointless by construction.
AssetKind = Literal["document", "schema-export", "prompt", "config"]
# Who put it here. Matches the Origin literal on Collections: `authored` is a
# person (including code a person ratified), `flywheel` was promoted out of
# runs, `derived` is regenerable from a recipe.
AssetOrigin = Literal["authored", "flywheel", "derived", "synced", "flow-written"]

# Precedence used when flagging conflicts: lower index = higher authority.
AUTHORITY_ORDER: list[str] = ["policy", "standard", "guideline", "how-to", "note"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Asset(BaseModel):
    """A single text asset in the knowledge layer."""

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9\-]*$")
    path: str = Field(
        description="Taxonomy path, e.g. finance/policies/expense-approval"
    )
    title: str
    content: str
    content_type: ContentType = "text/markdown"
    # Authority is a property of documents that argue — how much force this
    # carries when two documents disagree. A prompt never argues with a
    # policy, so for kind != document it is None rather than a polite lie.
    authority_level: AuthorityLevel | None = "note"
    kind: AssetKind = "document"
    origin: AssetOrigin = "authored"
    scope: str = "org"
    owner: str = ""
    tags: list[str] = Field(default_factory=list)
    parent: str | None = None
    related: list[str] = Field(default_factory=list)
    supersedes: str | None = None
    superseded_by: str | None = None
    status: Status = "draft"
    # Bumps on every content/metadata write. This is what `knowledge:{id}@revN`
    # references pin to: read-sets, task refs, and run journals all stamp the
    # revision they actually read, so a decision is explainable against the
    # document as it was — not as it is. (Full revision *history* is a later
    # step; the counter is the seam that makes the refs real today.)
    revision: int = 1
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


    @model_validator(mode="after")
    def _authority_is_for_documents(self):
        # A prompt or a schema export never argues with a policy; carrying an
        # authority level would be a category error, so it is dropped rather
        # than defaulted.
        if self.kind != "document":
            object.__setattr__(self, "authority_level", None)
        return self

    @field_validator("path")
    @classmethod
    def normalize_path(cls, v: str) -> str:
        v = v.strip().strip("/")
        if not v:
            raise ValueError("path must not be empty")
        return v

    def summary(self) -> "AssetSummary":
        return AssetSummary(
            id=self.id,
            path=self.path,
            title=self.title,
            content_type=self.content_type,
            authority_level=self.authority_level,
            kind=self.kind,
            origin=self.origin,
            scope=self.scope,
            owner=self.owner,
            tags=self.tags,
            status=self.status,
            revision=self.revision,
            updated_at=self.updated_at,
        )


class AssetSummary(BaseModel):
    id: str
    path: str
    title: str
    content_type: ContentType
    authority_level: AuthorityLevel | None
    kind: AssetKind = "document"
    origin: AssetOrigin = "authored"
    scope: str
    owner: str
    tags: list[str]
    status: Status
    revision: int = 1
    updated_at: datetime


class AssetCreate(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9\-]*$")
    path: str
    title: str
    content: str
    content_type: ContentType = "text/markdown"
    authority_level: AuthorityLevel | None = "note"
    kind: AssetKind = "document"
    origin: AssetOrigin = "authored"
    scope: str = "org"
    owner: str = ""
    tags: list[str] = Field(default_factory=list)
    parent: str | None = None
    related: list[str] = Field(default_factory=list)
    supersedes: str | None = None
    superseded_by: str | None = None
    status: Status = "draft"

    @model_validator(mode="after")
    def _authority_is_for_documents(self):
        if self.kind != "document":
            object.__setattr__(self, "authority_level", None)
        return self


class AssetUpdate(BaseModel):
    path: str | None = None
    title: str | None = None
    content: str | None = None
    content_type: ContentType | None = None
    authority_level: AuthorityLevel | None = None
    scope: str | None = None
    owner: str | None = None
    tags: list[str] | None = None
    parent: str | None = None
    related: list[str] | None = None
    supersedes: str | None = None
    superseded_by: str | None = None
    status: Status | None = None


RetrievalSource = Literal["lexical", "semantic", "hybrid"]


class SearchHit(BaseModel):
    asset: AssetSummary
    score: float
    snippet: str
    # Which retrievers found this, and where each of them placed it. Fusion
    # scores are only meaningful relative to each other within one response, so
    # these ranks are what you actually read when tuning weights or explaining
    # to someone why a document surfaced.
    retrieval: RetrievalSource = "lexical"
    lexical_rank: int | None = None
    semantic_rank: int | None = None
    # Cosine against the query, when the semantic retriever scored it. Unlike
    # `score` this is comparable across queries, so it is the one to threshold
    # on if you ever need "is this actually related, or just the best of a bad
    # set".
    similarity: float | None = None
    # True when only the vector index found this *and* it does not stand out
    # from the rest of the ranking — a nearest neighbour rather than a match.
    # Computed server-side (see search.mark_weak_hits) so the console, the MCP
    # tools and the usage counters all draw the line in the same place.
    weak: bool = False


class AssetFilter(BaseModel):
    """The structured plane of search: predicates over metadata, not text.

    These are deliberately separate from the query string. A question like
    "which policies does the HR team own" has a structured half (`owner=hr`,
    `authority_level=policy`) that is exactly answerable and a topical half
    that is not; conflating them into one text query turns a fact into a
    ranking. Filters are applied *before* retrieval scores anything, so a
    filtered search still returns its best matches rather than whatever
    survived a post-hoc cut of the top ten.
    """

    owner: str | None = None
    authority_level: AuthorityLevel | None = None
    kind: AssetKind | None = None
    status: Status | None = None
    tag: str | None = None
    path_prefix: str | None = None

    def is_empty(self) -> bool:
        return not any(
            (
                self.owner,
                self.authority_level,
                self.kind,
                self.status,
                self.tag,
                self.path_prefix,
            )
        )

    def matches(self, asset: Asset) -> bool:
        if self.owner is not None and asset.owner != self.owner:
            return False
        if (
            self.authority_level is not None
            and asset.authority_level != self.authority_level
        ):
            return False
        if self.kind is not None and asset.kind != self.kind:
            return False
        if self.status is not None and asset.status != self.status:
            return False
        if self.tag is not None and self.tag not in asset.tags:
            return False
        if self.path_prefix is not None:
            prefix = self.path_prefix.strip("/")
            if not (asset.path == prefix or asset.path.startswith(prefix + "/")):
                return False
        return True


class Citation(BaseModel):
    """A claim's provenance. `asset_id` is the join key back into the corpus."""

    asset_id: str
    title: str
    path: str
    # None when the cited asset is not a document (a prompt carries no
    # authority — the kind is the signal there).
    authority_level: AuthorityLevel | None
    kind: AssetKind = "document"
    status: Status
    snippet: str = ""


class AskStep(BaseModel):
    """One tool call the planner made, surfaced so the answer is auditable.

    Shown in the UI behind a disclosure. An answer you cannot trace back to
    the queries that produced it is not usable for governance work, which is
    the whole point of this corpus.
    """

    tool: str
    arguments: dict = Field(default_factory=dict)
    summary: str = ""
    # The graph ref the step landed on, when it landed on one — what lets a
    # console replay the planner's walk hop by hop instead of parsing prose.
    ref: str = ""


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    # When the caller is reading a document, that document is prepended to the
    # planner's context: "does this apply to contractors?" means something
    # different with an asset open than it does from a blank console.
    scope_asset_id: str | None = None
    include_drafts: bool = False


class AskAnswer(BaseModel):
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    steps: list[AskStep] = Field(default_factory=list)
    # "vertex" when the planner ran, "retrieval" when the endpoint degraded to
    # ranked documents. The UI renders these differently on purpose — prose
    # with no model behind it would be a lie about how the answer was reached.
    backend: str = "retrieval"
    truncated: bool = False


class FacetValue(BaseModel):
    value: str
    label: str
    count: int


class SearchFacets(BaseModel):
    """Refinement counts alongside a result set.

    Counted over the documents the *lexical* retriever matched, not over every
    hit the search returns. That distinction is the whole design: a dense
    retriever assigns every document a score, so "documents matching this
    query" is the entire corpus and every facet count would equal the corpus
    size — technically true and completely useless. BM25 matching means "this
    contains a word you typed", which is a crisp, explainable set worth
    counting. With no query text at all, the counts are over the whole corpus
    under the active filters.
    """

    total: int
    owner: list[FacetValue] = Field(default_factory=list)
    authority_level: list[FacetValue] = Field(default_factory=list)
    kind: list[FacetValue] = Field(default_factory=list)
    status: list[FacetValue] = Field(default_factory=list)
    branch: list[FacetValue] = Field(default_factory=list)


SearchSort = Literal["relevance", "updated", "authority"]
