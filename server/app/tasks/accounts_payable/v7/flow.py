"""Accounts payable, v7 — v6 plus a perfect-match allowance.

The one change: an invoice whose three-way match is clean line by line, whose
extraction is confident, and which raises no anomaly findings may auto-post up
to twice the governed limit instead of stopping at a clerk. The reasoning is
the clerk's own: on a perfect match the review step was a rubber stamp, and a
rubber stamp is exactly the low-value confirmation the doctrine says not to
send a human. Everything else — the extraction, the resolution, the match, the
screen, the review loop for anything less than perfect — is v6's, reused
rather than copied, so a fix to the shared shape reaches both versions.

Shipped as a candidate: v6 stays the default until the console makes this one
live.
"""

from __future__ import annotations

from ....flows import FlowStep
from .. import domain
from ..v6 import flow as v6

VERSION = 7
LABEL = "v7"
NOTES = (
    "Adds the perfect-match allowance: a cleanly matched, confidently read, "
    "anomaly-free invoice may auto-post up to twice the governed limit rather "
    "than waiting on a clerk whose review would be a rubber stamp. Every "
    "other step is unchanged from v6."
)


def _steps() -> list[FlowStep]:
    steps = [step.model_copy(deep=True) for step in v6.STEPS]
    for index, step in enumerate(steps):
        if step.id != "r2":
            continue
        config = dict(step.config)
        config["allowance"] = "perfect match ≤ 2× auto-post limit"
        steps[index] = step.model_copy(
            update={
                "summary": (
                    step.summary
                    + " — with the v7 allowance: a cleanly matched, "
                    "confidently read, anomaly-free invoice may post up to "
                    "twice the limit"
                ),
                "config": config,
            }
        )
    return steps


STEPS: list[FlowStep] = _steps()


def _posting_check(extraction, match, screen, policy) -> dict:
    """v6's governed check, with the perfect-match allowance ahead of it.

    A pure check still: it evaluates only what the flow handed it and returns
    a verdict. The allowance applies exactly when the *only* objection is the
    auto-post limit — any variance, any anomaly finding, any confidence doubt,
    any arithmetic disagreement keeps v6's answer.
    """
    verdict = domain._posting_check(extraction, match, screen, policy)
    reasons = verdict["reasons"]
    over_limit_only = (
        len(reasons) == 1
        and "auto-post limit" in reasons[0]
        and match.verdict == "matched"
        and not (screen.get("findings") or [])
        and extraction.total_usd <= policy.limit_usd * 2
    )
    if over_limit_only:
        return {
            "reasons": [],
            "note": (
                f"post: ${extraction.total_usd:,.2f} exceeds the "
                f"${policy.limit_usd:,.2f} auto-post limit, but a clean match "
                "with nothing unusual carries the perfect-match allowance "
                "(≤ 2× the limit)"
            ),
        }
    return verdict


def build_body(service):
    return v6.build_body(service, posting_check=_posting_check)
