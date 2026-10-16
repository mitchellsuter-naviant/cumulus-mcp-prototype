"""What a document's authority is worth, and the bound on it.

The rule under test is one sentence: **relevance decides whether a document is
an answer, authority decides which of the answers governs.** Everything here
exists to stop the second half eating the first — a ranking sorted by authority
puts the least relevant policy above the how-to that answers the question, and
that is the failure the lift is bounded to prevent.
"""

import pytest

from app.authority import (
    MAX_LIFT,
    AuthorityPolicy,
    apply_authority_prior,
    parse_authority_policy,
    read_authority_policy,
)
from tests import world as test_world


@pytest.fixture()
def knowledge():
    return test_world.build().knowledge


def test_the_precedence_is_read_from_the_governed_asset(knowledge):
    """Not a constant in Python. The order is a statement about how this
    organization is governed, so the customer owns the document that says it —
    the same posture as the PTO window and the model price sheet."""
    policy = read_authority_policy(knowledge)
    assert policy.found_asset
    assert policy.order == ["policy", "standard", "guideline", "how-to", "note"]
    assert policy.describe() == "policy > standard > guideline > how-to > note"
    assert policy.source_ref.startswith("knowledge:retrieval-authority@rev")


def test_editing_the_asset_moves_the_rule(knowledge):
    """The whole point of governed knowledge: a deployment that decides a
    guideline outranks a standard says so in the document, not in a deploy."""
    asset = knowledge.repo.get("retrieval-authority")
    from app.models import AssetUpdate

    knowledge.update(
        "retrieval-authority",
        AssetUpdate(
            content=asset.content.replace(
                "| standard | 2 |\n| guideline | 1 |",
                "| guideline | 2 |\n| standard | 1 |",
            )
        ),
    )
    policy = read_authority_policy(knowledge)
    assert policy.order[1:3] == ["guideline", "standard"]
    assert policy.outranks("guideline", "standard")
    assert policy.lift_for("guideline") == 2


def test_a_lift_written_larger_than_the_cap_is_still_capped():
    """A `lift` of 50 in the document would silently turn retrieval into a sort
    by authority. The number is governed; the bound on it is not."""
    policy = parse_authority_policy(
        "| policy | 50 |\n| standard | 2 |\n| note | 0 |"
    )
    assert policy.lift["policy"] == 50  # what the document says
    assert policy.lift_for("policy") == MAX_LIFT  # what retrieval may do


def test_machinery_carries_no_authority_and_buys_no_places():
    """A prompt, a config and a generated schema export have no authority level
    by construction. They are not answers to questions about how the
    organization works, and they never climb over one."""
    policy = AuthorityPolicy()
    assert policy.lift_for(None) == 0
    assert policy.rank_of(None) == len(policy.order)
    assert policy.outranks("note", None)


def test_authority_settles_a_tie_and_cannot_manufacture_one():
    """The bound, stated as the behaviour it produces.

    A policy three places down reaches second — enough to settle which of the
    documents that answered governs. It never reaches first over a document
    relevance put there, and a policy that was not close was not an answer.
    """
    ordered = [(f"note-{i}", 5.0 - i) for i in range(3)] + [("policy-x", 1.0)]
    levels = {f"note-{i}": "note" for i in range(3)} | {"policy-x": "policy"}
    ranked = [
        doc for doc, _ in apply_authority_prior(ordered, levels, AuthorityPolicy())
    ]
    # It climbs to the top of its lift and no further: tied with the document
    # at that place, the one relevance put there stays in front.
    assert ranked[0] == "note-0"
    assert ranked[1] == "policy-x"

    # And ten places down it still climbs exactly three, which is the whole
    # guarantee: a document relevance did not put near the answer cannot be
    # lifted onto the head of the page by being important.
    far = [(f"note-{i}", 10.0 - i) for i in range(10)] + [("policy-y", 0.5)]
    far_levels = {f"note-{i}": "note" for i in range(10)} | {"policy-y": "policy"}
    far_ranked = [
        doc for doc, _ in apply_authority_prior(far, far_levels, AuthorityPolicy())
    ]
    assert far_ranked.index("policy-y") == 8
    assert far_ranked[:8] == [f"note-{i}" for i in range(8)]


def test_authority_buys_nothing_outside_the_tie_band():
    """The case that makes the rule necessary.

    Asked about dental cover, the benefits guide covers the whole question and
    the PTO policy two-thirds of it. `policy` outranks `guideline`, so a lift
    blind to coverage hands an employee the PTO policy as the document
    governing their dental plan — which it is not. It is more authoritative in
    general and it is not about the question, and that distinction is the whole
    reason relevance and authority are two decisions rather than one.
    """
    ordered = [("benefits-overview", 2.0), ("pto-policy", 1.0)]
    levels = {"benefits-overview": "guideline", "pto-policy": "policy"}
    policy = AuthorityPolicy()

    blind = apply_authority_prior(ordered, levels, policy)
    assert blind[0][0] == "pto-policy"  # what a lift without coverage does

    gated = apply_authority_prior(
        ordered, levels, policy,
        {"benefits-overview": 1.0, "pto-policy": 0.67},
    )
    assert gated[0][0] == "benefits-overview"

    # And inside the band it still decides, which is the job it was kept for:
    # two documents that answer about equally well, and the policy governs.
    tied = apply_authority_prior(
        ordered, levels, policy,
        {"benefits-overview": 1.0, "pto-policy": 0.95},
    )
    assert tied[0][0] == "pto-policy"


def test_the_prior_is_stable_so_it_only_ever_breaks_ties():
    """Two documents at the same level keep the order relevance gave them."""
    ordered = [("a", 3.0), ("b", 2.0), ("c", 1.0)]
    levels = {"a": "policy", "b": "policy", "c": "policy"}
    assert [d for d, _ in apply_authority_prior(ordered, levels, AuthorityPolicy())] == [
        "a",
        "b",
        "c",
    ]


def test_the_two_coverage_bands_are_governed_and_ordered(knowledge):
    """Below the floor nothing is citable; between the bands a document is the
    closest thing and not the rule; above, it governs."""
    policy = read_authority_policy(knowledge)
    assert 0.0 < policy.min_coverage < policy.governing_coverage <= 1.0

    # A document that put the governing band under the floor would make the
    # middle state unreachable, so the floor wins.
    contradictory = parse_authority_policy(
        "| policy | 3 |\n\n| Minimum coverage | 0.7 |\n| Governing coverage | 0.2 |"
    )
    assert contradictory.governing_coverage == contradictory.min_coverage == 0.7


def test_a_missing_asset_falls_back_to_what_the_document_would_have_said(
    knowledge,
):
    """A deployment that never loaded the asset behaves the way the document
    describes rather than in some third way nobody wrote down."""
    knowledge.delete("retrieval-authority")
    policy = read_authority_policy(knowledge)
    assert policy.found_asset is False
    assert policy.order == ["policy", "standard", "guideline", "how-to", "note"]
    assert policy.lift_for("policy") == 3
