from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator

ContentType = Literal["text/markdown", "text/plain"]
AuthorityLevel = Literal["policy", "standard", "guideline", "how-to", "note"]
Status = Literal["draft", "published", "deprecated"]

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
    authority_level: AuthorityLevel = "note"
    scope: str = "org"
    owner: str = ""
    tags: list[str] = Field(default_factory=list)
    parent: str | None = None
    related: list[str] = Field(default_factory=list)
    supersedes: str | None = None
    superseded_by: str | None = None
    status: Status = "draft"
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

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
            scope=self.scope,
            owner=self.owner,
            tags=self.tags,
            status=self.status,
            updated_at=self.updated_at,
        )


class AssetSummary(BaseModel):
    id: str
    path: str
    title: str
    content_type: ContentType
    authority_level: AuthorityLevel
    scope: str
    owner: str
    tags: list[str]
    status: Status
    updated_at: datetime


class AssetCreate(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9\-]*$")
    path: str
    title: str
    content: str
    content_type: ContentType = "text/markdown"
    authority_level: AuthorityLevel = "note"
    scope: str = "org"
    owner: str = ""
    tags: list[str] = Field(default_factory=list)
    parent: str | None = None
    related: list[str] = Field(default_factory=list)
    supersedes: str | None = None
    superseded_by: str | None = None
    status: Status = "draft"


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
    status: Status | None = None
    tag: str | None = None
    path_prefix: str | None = None

    def is_empty(self) -> bool:
        return not any(
            (self.owner, self.authority_level, self.status, self.tag, self.path_prefix)
        )

    def matches(self, asset: Asset) -> bool:
        if self.owner is not None and asset.owner != self.owner:
            return False
        if (
            self.authority_level is not None
            and asset.authority_level != self.authority_level
        ):
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
    authority_level: AuthorityLevel
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
    status: list[FacetValue] = Field(default_factory=list)
    branch: list[FacetValue] = Field(default_factory=list)


SearchSort = Literal["relevance", "updated", "authority"]
