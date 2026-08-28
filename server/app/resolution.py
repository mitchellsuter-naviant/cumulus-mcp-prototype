"""Entity resolution: an inbound name becomes a canonical id, or a Task.

This is the piece that makes "one concept of vendor" true. It is called from
two places on purpose:

- **The vendor sync**, when an ERP row arrives and we have to decide whether it
  is a vendor we already know.
- **The invoice path**, when the extraction has read a vendor name off a
  document and we need the canonical id before we can look for a purchase
  order or this vendor's payment history.

One resolver, two callers, so the two can never disagree about what counts as a
match — which is the failure mode that produces two vendors named Acme.

Thresholds are governed knowledge, parsed from the `entity-resolution-policy`
asset at run time. The scoring signals are code (they are machinery); where the
lines fall between auto-link, steward review, and mint-new is a business
decision, and it lives in a document a steward can edit.

The fuzzy signal is deliberately boring: token overlap blended with character
similarity over normalized names. It is explainable in one sentence to the
person reviewing the match, which matters more here than a point of recall.
"""

from __future__ import annotations

import difflib
import logging
import re
from typing import Literal

from pydantic import BaseModel, Field

from .graph import GraphService, ObjectRecord, normalize_key

log = logging.getLogger("cumulus.resolution")

RESOLUTION_ASSET_ID = "entity-resolution-policy"

MatchMethod = Literal["tax-id", "normalized-name", "fuzzy-name", "source-link", "none"]
Disposition = Literal["linked", "review", "new"]


class ResolutionThresholds(BaseModel):
    auto_link_at: float = 0.92
    review_from: float = 0.70
    source_ref: str = f"knowledge:{RESOLUTION_ASSET_ID}"
    found_asset: bool = False


class MatchCandidate(BaseModel):
    object_id: str
    label: str
    score: float
    method: MatchMethod
    reason: str = ""


class ResolutionOutcome(BaseModel):
    """What the resolver decided, and enough of why to review it.

    `candidates` is always populated when there was anything to compare
    against, including on a `new` disposition — "we minted a new vendor and
    here is the closest thing we already had" is the sentence a steward needs.
    """

    disposition: Disposition
    object_id: str = ""
    label: str = ""
    score: float = 0.0
    method: MatchMethod = "none"
    candidates: list[MatchCandidate] = Field(default_factory=list)
    reason: str = ""
    thresholds_ref: str = ""

    @property
    def needs_review(self) -> bool:
        return self.disposition == "review"


def parse_resolution_policy(content: str) -> ResolutionThresholds:
    values = ResolutionThresholds(found_asset=True)
    auto = re.search(r"auto-link at or above:?\s*(\d+(?:\.\d+)?)", content, re.I)
    if auto:
        values.auto_link_at = float(auto.group(1))
    review = re.search(
        r"steward review between:?\s*(\d+(?:\.\d+)?)\s*and\s*(\d+(?:\.\d+)?)",
        content, re.I,
    )
    if review:
        values.review_from = float(review.group(1))
        values.auto_link_at = float(review.group(2))
    mint = re.search(r"mint a new entity below:?\s*(\d+(?:\.\d+)?)", content, re.I)
    if mint:
        values.review_from = float(mint.group(1))
    return values


def read_resolution_thresholds(knowledge) -> ResolutionThresholds:
    asset = knowledge.repo.get(RESOLUTION_ASSET_ID) if knowledge else None
    if asset is None:
        log.warning("Entity-resolution policy missing; using defaults")
        return ResolutionThresholds(found_asset=False)
    values = parse_resolution_policy(asset.content)
    values.source_ref = (
        f"knowledge:{RESOLUTION_ASSET_ID}@rev{getattr(asset, 'revision', 1)}"
    )
    return values


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")


def _tokens(value: str) -> set[str]:
    return {t for t in _TOKEN_SPLIT.split(normalize_key(value)) if t}


def name_similarity(left: str, right: str) -> float:
    """Three signals over normalized names, blended.

    Three because each fails differently, and vendor names fail in all three
    ways at once:

    - **Exact token overlap** (Jaccard) handles word order and extra words —
      "Acme Industrial" against "Industrial Acme Supply" — but a single typo
      drops a token out of the intersection entirely, so on its own it scores
      "Acme Industial" as barely related.
    - **Character similarity** over the whole normalized string handles typos
      but is thrown by word order.
    - **Token-wise soft overlap** — each token's best match in the other name,
      averaged — is what rescues the typo case without giving up word-order
      tolerance. It is the signal that decides whether a misspelling lands in
      the steward's review band or silently mints a second vendor, which is the
      single most consequential judgment this module makes.

    The weights are deliberately blunt and the result is one number a reviewer
    sees next to the two names, so a wrong call is legible rather than buried.
    """
    left_tokens, right_tokens = _tokens(left), _tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    jaccard = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
    ratio = difflib.SequenceMatcher(
        None, normalize_key(left), normalize_key(right)
    ).ratio()
    smaller, larger = (
        (left_tokens, right_tokens) if len(left_tokens) <= len(right_tokens)
        else (right_tokens, left_tokens)
    )
    soft = sum(
        max(difflib.SequenceMatcher(None, token, other).ratio() for other in larger)
        for token in smaller
    ) / len(smaller)
    return round(0.35 * jaccard + 0.30 * ratio + 0.35 * soft, 4)


def score_candidates(
    names: list[str],
    tax_id: str,
    known: list[ObjectRecord],
    *,
    source: str = "",
    source_external_id: str = "",
    graph: GraphService | None = None,
) -> list[MatchCandidate]:
    """Rank known entities against the inbound signals, best first."""
    candidates: list[MatchCandidate] = []
    clean_names = [n for n in names if (n or "").strip()]

    for record in known:
        if record.status == "superseded":
            continue
        best: MatchCandidate | None = None

        # 1. A source that has already told us its id for this entity.
        if source and source_external_id and graph is not None:
            for link, other in graph.neighbors(record.id, "identified_as", "out"):
                if (
                    other.props.get("source") == source
                    and str(other.props.get("external_id")) == str(source_external_id)
                ):
                    best = MatchCandidate(
                        object_id=record.id, label=record.label, score=1.0,
                        method="source-link",
                        reason=f"{source} already identifies this entity as "
                               f"{source_external_id}",
                    )
                    break

        # 2. Tax id: decisive on its own.
        if best is None and tax_id and record.props.get("tax_id"):
            if normalize_key(str(record.props["tax_id"])) == normalize_key(tax_id):
                best = MatchCandidate(
                    object_id=record.id, label=record.label, score=0.99,
                    method="tax-id", reason=f"tax id {tax_id} matches exactly",
                )

        if best is None:
            record_names = [
                str(record.props.get("name") or ""),
                record.label,
                record.natural_key,
            ]
            record_names += [
                str(alias) for alias in (record.props.get("aliases") or [])
            ]
            # 3. Normalized-name equality.
            exact = next(
                (
                    (inbound, existing)
                    for inbound in clean_names
                    for existing in record_names
                    if existing and normalize_key(inbound) == normalize_key(existing)
                ),
                None,
            )
            if exact is not None:
                best = MatchCandidate(
                    object_id=record.id, label=record.label, score=0.97,
                    method="normalized-name",
                    reason=f"'{exact[0]}' normalizes to the same key as "
                           f"'{exact[1]}'",
                )
            else:
                # 4. The one fuzzy signal.
                scored = [
                    (name_similarity(inbound, existing), inbound, existing)
                    for inbound in clean_names
                    for existing in record_names
                    if existing
                ]
                if scored:
                    score, inbound, existing = max(scored, key=lambda item: item[0])
                    if score > 0:
                        best = MatchCandidate(
                            object_id=record.id, label=record.label,
                            score=round(score, 4), method="fuzzy-name",
                            reason=f"'{inbound}' ~ '{existing}' at {score:.2f}",
                        )
        if best is not None:
            candidates.append(best)

    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates


def resolve(
    graph: GraphService,
    object_type: str,
    names: list[str],
    thresholds: ResolutionThresholds,
    *,
    tax_id: str = "",
    source: str = "",
    source_external_id: str = "",
    limit: int = 5,
) -> ResolutionOutcome:
    """Decide: link to an existing entity, ask a steward, or mint a new one."""
    known = graph.find(object_type)
    candidates = score_candidates(
        names, tax_id, known,
        source=source, source_external_id=source_external_id, graph=graph,
    )[:limit]
    top = candidates[0] if candidates else None
    outcome = ResolutionOutcome(
        disposition="new", candidates=candidates,
        thresholds_ref=thresholds.source_ref,
    )
    if top is None:
        outcome.reason = f"nothing to compare against — first {object_type}"
        return outcome
    if top.score >= thresholds.auto_link_at:
        outcome.disposition = "linked"
        outcome.object_id = top.object_id
        outcome.label = top.label
        outcome.score = top.score
        outcome.method = top.method
        outcome.reason = (
            f"{top.reason} — at or above the {thresholds.auto_link_at:.2f} "
            "auto-link threshold"
        )
        return outcome
    if top.score >= thresholds.review_from:
        outcome.disposition = "review"
        outcome.score = top.score
        outcome.method = top.method
        outcome.label = top.label
        outcome.object_id = ""  # nothing is asserted until a steward says so
        outcome.reason = (
            f"best match {top.label} at {top.score:.2f} sits between "
            f"{thresholds.review_from:.2f} and {thresholds.auto_link_at:.2f} — "
            "a steward decides"
        )
        return outcome
    outcome.score = top.score
    outcome.reason = (
        f"closest known entity is {top.label} at {top.score:.2f}, below the "
        f"{thresholds.review_from:.2f} floor — minting a new {object_type}"
    )
    return outcome
