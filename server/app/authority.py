"""What a document's authority is worth, read from governed knowledge.

Relevance decides whether a document is an answer; authority decides which of
the answers governs. Keeping those two apart is the whole of this module, and
conflating them is the failure it exists to prevent: ranking by authority puts
the least relevant policy above the how-to that actually answers the question,
and a ranking a reader learns to distrust is worse than no ranking.

So authority buys *places*, not score. A `policy` may climb at most three
positions in a ranking that relevance already produced, which is enough to
settle a tie between documents that both answered and never enough to lift one
that did not. The cap is enforced here rather than trusted to the asset,
because a `lift` of 50 written into a document would silently turn retrieval
into a sort by authority — the exact thing the number exists to bound.

The table itself lives in `retrieval-authority`, a knowledge asset, for the
reason the PTO window and the model price sheet do: the order is a statement
about how this organization is governed, and the customer owns it. Editing the
asset moves the rule; nothing here needs redeploying.
"""

from __future__ import annotations

import logging
import re

from pydantic import BaseModel, Field

from .models import AUTHORITY_ORDER

log = logging.getLogger("cumulus.authority")

AUTHORITY_ASSET_ID = "retrieval-authority"

#: The most places any authority level may buy, whatever the asset says. Three
#: is "settle a tie among the documents that answered"; anything larger is a
#: sort by authority wearing a lift's clothes.
MAX_LIFT = 3

#: Used when the asset is missing entirely. Deliberately the same numbers the
#: seeded asset carries, so a deployment that never loaded it behaves the way
#: the document says it does rather than in some third way nobody wrote down.
DEFAULT_LIFT: dict[str, int] = {
    "policy": 3,
    "standard": 2,
    "guideline": 1,
    "how-to": 0,
    "note": 0,
}
DEFAULT_MIN_COVERAGE = 0.34
DEFAULT_GOVERNING_COVERAGE = 0.60
DEFAULT_TIE_BAND = 0.15

# `| policy | 3 |` — the precedence table. Row order is the precedence order,
# so the parser preserves it rather than sorting by lift.
_LIFT_ROW = re.compile(
    r"^\|\s*([a-z][a-z\-]*)\s*\|\s*(\d+)\s*\|\s*$", re.MULTILINE
)
_COVERAGE_ROW = re.compile(
    r"^\|\s*Minimum coverage\s*\|\s*(\d*\.?\d+)\s*\|",
    re.MULTILINE | re.IGNORECASE,
)
_GOVERNING_ROW = re.compile(
    r"^\|\s*Governing coverage\s*\|\s*(\d*\.?\d+)\s*\|",
    re.MULTILINE | re.IGNORECASE,
)
_TIE_BAND_ROW = re.compile(
    r"^\|\s*Tie band\s*\|\s*(\d*\.?\d+)\s*\|", re.MULTILINE | re.IGNORECASE
)


class AuthorityPolicy(BaseModel):
    """The precedence list, the lift each level buys, and the relevance floor."""

    #: Highest authority first. The order of the rows in the asset's table.
    order: list[str] = Field(default_factory=lambda: list(AUTHORITY_ORDER))
    lift: dict[str, int] = Field(default_factory=lambda: dict(DEFAULT_LIFT))
    #: The share of a question's information a document must carry before it
    #: is citable at all. Below it, an answerer says it did not find one.
    min_coverage: float = DEFAULT_MIN_COVERAGE
    #: And the higher band above which the top survivor may be called *the
    #: governing document* rather than merely the closest thing in the corpus.
    #: The gap between the two is where a corpus gap gets reported as one.
    governing_coverage: float = DEFAULT_GOVERNING_COVERAGE
    #: How far below the best-covered document another may sit and still count
    #: as answering the same question about as well. Authority breaks ties
    #: inside this band and nowhere else — see `apply_authority_prior`.
    tie_band: float = DEFAULT_TIE_BAND
    #: `knowledge:retrieval-authority@revN`, so an answer can say which
    #: revision of the rule it was ranked under.
    source_ref: str = f"knowledge:{AUTHORITY_ASSET_ID}"
    found_asset: bool = True

    def lift_for(self, level: str | None) -> int:
        """Places this level may climb. Machinery — no level — climbs none."""
        if not level:
            return 0
        return max(0, min(MAX_LIFT, self.lift.get(level, 0)))

    def rank_of(self, level: str | None) -> int:
        """Precedence index, lowest wins. Machinery sorts after every document."""
        if not level or level not in self.order:
            return len(self.order)
        return self.order.index(level)

    def outranks(self, left: str | None, right: str | None) -> bool:
        return self.rank_of(left) < self.rank_of(right)

    def describe(self) -> str:
        """The rule in one line, for an answer that has to show its working."""
        return " > ".join(self.order)


def parse_authority_policy(content: str) -> AuthorityPolicy:
    """Read the asset's precedence table. An unreadable table leaves defaults."""
    rows = [
        (match.group(1), int(match.group(2)))
        for match in _LIFT_ROW.finditer(content)
    ]
    # Only rows naming a level the corpus actually uses; the document has other
    # tables, and a settings row is not a precedence row.
    rows = [(level, value) for level, value in rows if level in AUTHORITY_ORDER]
    policy = AuthorityPolicy()
    if rows:
        policy.order = [level for level, _ in rows]
        policy.lift = {level: value for level, value in rows}
    coverage = _COVERAGE_ROW.search(content)
    if coverage:
        policy.min_coverage = max(0.0, min(1.0, float(coverage.group(1))))
    band = _TIE_BAND_ROW.search(content)
    if band:
        policy.tie_band = max(0.0, min(1.0, float(band.group(1))))
    governing = _GOVERNING_ROW.search(content)
    if governing:
        policy.governing_coverage = max(0.0, min(1.0, float(governing.group(1))))
    # A governing band below the floor would make the middle state impossible
    # to reach and the two numbers a contradiction; the floor wins, because it
    # is the one that decides whether anything is said at all.
    policy.governing_coverage = max(policy.governing_coverage, policy.min_coverage)
    return policy


def read_authority_policy(knowledge) -> AuthorityPolicy:
    """The governed policy, stamped with the revision it was read at."""
    asset = knowledge.repo.get(AUTHORITY_ASSET_ID) if knowledge else None
    if asset is None:
        log.warning("Authority asset missing; using the built-in precedence")
        return AuthorityPolicy(found_asset=False)
    policy = parse_authority_policy(asset.content)
    policy.source_ref = (
        f"knowledge:{AUTHORITY_ASSET_ID}@rev{getattr(asset, 'revision', 1)}"
    )
    return policy


def apply_authority_prior(
    ordered: list[tuple[str, float]],
    levels: dict[str, str | None],
    policy: AuthorityPolicy,
    coverages: dict[str, float] | None = None,
) -> list[tuple[str, float]]:
    """Re-rank a relevance ordering by letting authority buy a few places.

    Works in rank space rather than on the scores, because the scores are not
    comparable between the two retrieval paths — BM25 is unbounded and moves
    with corpus statistics, a fused RRF score sits in a narrow band whose width
    depends on how many rankings a document appeared in. A place is a place in
    both, so one rule covers the lexical-only path and the fused one, and
    "authority is worth up to three places" is a sentence you can say to a
    reader and have them check.

    `coverages` is what keeps the rule honest, and without it the prior does
    the wrong thing in the one case that matters most. Asked about dental
    cover, the benefits guide covers the whole question and the PTO policy
    two-thirds of it — and `policy` outranks `guideline`, so a lift blind to
    coverage hands an employee the PTO policy as the document that governs
    their dental plan. It does not: it is more authoritative *in general* and
    is not about the question. So authority breaks ties between documents that
    answer about equally well — within `tie_band` of the best-covered one —
    and buys nothing outside that band.

    Stable: two documents that end up at the same effective rank stay in the
    order relevance put them, so the prior only ever breaks ties in authority's
    favour and never invents one.
    """
    if not ordered:
        return ordered
    floor = 0.0
    if coverages:
        floor = max(coverages.values()) - policy.tie_band

    def lift(doc_id: str) -> int:
        if coverages is not None and coverages.get(doc_id, 0.0) < floor:
            return 0
        return policy.lift_for(levels.get(doc_id))

    scored = [
        (position - lift(doc_id), position, doc_id, score)
        for position, (doc_id, score) in enumerate(ordered)
    ]
    scored.sort(key=lambda row: (row[0], row[1]))
    return [(doc_id, score) for _, _, doc_id, score in scored]
