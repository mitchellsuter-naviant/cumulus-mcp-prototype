"""Where an elevated request goes: governed knowledge, resolved at run time.

The expense policy has always carried an approval table — *under $500, direct
manager; $500–5,000, department head; over $5,000, VP of Finance* — and the
flow has always ignored it, sending every elevated request to one hardcoded
queue. The document said one thing and the machinery did another, which is the
exact failure mode the knowledge layer exists to prevent.

So routing becomes a governed read like the limit and the confidence floor
beside it. Two kinds of target, because two different questions get asked:

- a **queue slug** — "send it to the exec desk" — which is a standing
  arrangement somebody administers;
- a **resolver** — `manager-of(submitter)`, or `submitter` — which is a
  question about *this* case, answered from the case itself and from the org
  chart in the graph.

`submitter` is the one that sends a review back to the person who raised it,
which is what confirming an extraction is: nobody else can say whether the
number on the receipt is the number on the receipt. It resolves to that
person's own desk, so the work is still routed to a Queue and still gated by
`require_queue` — a Task addressed to a human rather than to a desk would be
work that disappears when they are away, which is the reason the doctrine has
no such thing.

The resolver has to run somewhere, and DOCTRINE point 199 says where it may
not: a Rule is a pure check that never fetches. So resolution is a *transform*
step that walks the graph and hands the Rule a concrete queue. The journal then
records which desk was chosen and what it was chosen from, which is the whole
difference between a routed decision and one that merely happened.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel, Field

from . import desks
from .ontology import names as onto

log = logging.getLogger("cumulus.routing")

#: Where a *step* that threw goes, as opposed to a business escalation. A
#: Vertex 400 about a file format landed on the finance approver's desk for as
#: long as flows named their own review queue for both; the Controller cannot
#: fix a mime type, and the person who can was not looking at that queue.
OPS_QUEUE = desks.PLATFORM_OPERATIONS

#: The first resolver. Written as a call rather than a keyword so the policy
#: reads as an instruction with an argument, and so a second resolver
#: (`department-head-of`, `team-lead-of`) is a new name in this table rather
#: than a new grammar.
MANAGER_OF = "manager-of(submitter)"

#: The second: the person the case is about, reviewing their own submission.
#: A bare word and not a call, because there is no argument to take — the case
#: already names its submitter, where `manager-of(submitter)` names whose
#: manager to look up. It is still a resolver rather than a queue slug: the
#: desk it answers with is different for every case, which is the distinction
#: the module docstring draws and the reason a literal `submitter` queue must
#: never be minted.
SUBMITTER = "submitter"


class RoutingRule(BaseModel):
    """One band of the approval table: everything above `above_usd` goes here."""

    above_usd: float = 0.0
    target: str = ""
    label: str = ""


class RoutingPolicy(BaseModel):
    """The parsed approval routing, and where it was read from."""

    rules: list[RoutingRule] = Field(default_factory=list)
    #: Where a restricted category goes regardless of amount. Empty means the
    #: amount bands decide it like anything else.
    restricted_target: str = ""
    #: Where anything unrouted lands — including every case where a resolver
    #: could not answer. Never empty: an unroutable Task with no desk is work
    #: that exists and that nobody will ever see.
    fallback: str = "expense-review"
    found_asset: bool = False
    source_ref: str = ""

    def target_for(self, amount_usd: float, restricted: bool = False) -> str:
        """The desk this amount routes to, before any resolver runs."""
        if restricted and self.restricted_target:
            return self.restricted_target
        chosen = self.fallback
        for rule in sorted(self.rules, key=lambda r: r.above_usd):
            if amount_usd > rule.above_usd:
                chosen = rule.target
        return chosen or self.fallback


class ResolvedQueue(BaseModel):
    """The answer, with enough of its derivation to be worth auditing.

    `explanation` is written to be read in the journal and in the Task itself.
    "Routed to Diane Vogel's desk: submitter's manager per the org chart" is a
    sentence somebody can check; a queue slug on its own is not.
    """

    queue: str
    target: str = ""
    explanation: str = ""
    #: True when a resolver was asked and could not answer, so the fallback
    #: stands. Surfaced rather than swallowed: a deployment quietly routing
    #: every request to the fallback because nobody has a manager in the graph
    #: is a broken org chart wearing a working flow.
    fell_back: bool = False


def parse_routing(content: str, default_fallback: str = "expense-review") -> RoutingPolicy:
    """Read the routing table out of a governed policy document.

    Tolerant on purpose. This is a document a finance lead edits in the
    console, not a config file: a missing section, a re-ordered table or a
    stray currency symbol has to degrade to "route everything to the fallback"
    rather than to an exception in the middle of somebody's reimbursement.
    """
    policy = RoutingPolicy(fallback=default_fallback)
    section = _section(content, "approval routing")
    if not section:
        return policy
    policy.found_asset = True

    for raw_above, raw_target in re.findall(
        r"^\s*\|\s*(?:over|above)?\s*\$?([\d,]+(?:\.\d{1,2})?)[^|]*\|\s*([^|\n]+?)\s*\|",
        section, re.IGNORECASE | re.MULTILINE,
    ):
        target = _clean_target(raw_target)
        if not target:
            continue
        policy.rules.append(RoutingRule(
            above_usd=float(raw_above.replace(",", "")),
            target=target,
            label=raw_target.strip(),
        ))

    restricted = re.search(
        r"restricted category desk:?\s*([^\n]+)", section, re.IGNORECASE
    )
    if restricted:
        policy.restricted_target = _clean_target(restricted.group(1))
    fallback = re.search(r"fallback desk:?\s*([^\n]+)", section, re.IGNORECASE)
    if fallback:
        policy.fallback = _clean_target(fallback.group(1)) or default_fallback
    return policy


def resolve(
    target: str,
    *,
    submitter_email: str,
    graph: Any,
    queues: Any,
    fallback: str,
    submitter_name: str = "",
) -> ResolvedQueue:
    """Turn a routing target into a desk that actually exists.

    A literal target is returned as it stands — it is somebody's standing
    arrangement and this is not the place to second-guess whether they created
    the queue. A resolver is answered against the case, and *every* way of
    failing to answer lands on the fallback with `fell_back` set and a sentence
    saying why. Silence here would route a VP's expense to the wrong desk and
    look exactly like success.

    `submitter_name` is what to call the person on their own desk when the
    caller already knows — a flow holds the submitter's display name at gate
    time. Omitted, the org chart is asked, and the email stands if it cannot
    answer: a desk named after an address is legible, and a routing step that
    failed because nobody had a display name would not be.

    Deterministic, which a replayed step requires: the personal desk's slug is
    a pure function of the person's email, so resuming this run mints no second
    inbox and picks no different queue.
    """
    if target == SUBMITTER:
        if not submitter_email:
            return ResolvedQueue(
                queue=fallback, target=target, fell_back=True,
                explanation=(
                    "this case names no submitter, so it routed to the "
                    f"'{fallback}' desk instead of back to the person who "
                    "raised it"
                ),
            )
        name = submitter_name or name_of(graph, submitter_email)
        desk = _personal_desk(queues, submitter_email, name)
        if desk is None:
            return ResolvedQueue(
                queue=fallback, target=target, fell_back=True,
                explanation=(
                    f"no desk could be opened for {name or submitter_email}, "
                    f"so this routed to the '{fallback}' desk instead of back "
                    "to the person who raised it"
                ),
            )
        return ResolvedQueue(
            queue=desk, target=target,
            explanation=(
                f"routed to {name or submitter_email}'s own desk: the person "
                "who submitted this case reviews it"
            ),
        )

    if target != MANAGER_OF:
        return ResolvedQueue(
            queue=target or fallback,
            target=target,
            explanation=f"routed to the '{target or fallback}' desk by the "
                        "governed approval table",
        )

    manager = manager_of(graph, submitter_email)
    if manager is None:
        who = submitter_email or "the submitter"
        return ResolvedQueue(
            queue=fallback, target=target, fell_back=True,
            explanation=(
                f"the org chart has no manager for {who}, so this routed to "
                f"the '{fallback}' desk instead of to a named approver"
            ),
        )
    email, name = manager
    desk = _personal_desk(queues, email, name)
    if desk is None:
        return ResolvedQueue(
            queue=fallback, target=target, fell_back=True,
            explanation=(
                f"no desk could be opened for {name or email}, so this routed "
                f"to the '{fallback}' desk instead of to a named approver"
            ),
        )
    return ResolvedQueue(
        queue=desk,
        target=target,
        explanation=(
            f"routed to {name or email}'s own desk: the submitter's manager "
            "per the org chart"
        ),
    )


def _personal_desk(queues: Any, email: str, name: str) -> str | None:
    """One person's own desk, minted on first use — or None if it could not be.

    The registry is a store like any other and can be unreachable, and a
    resolver that let that out as an exception would fail the run over a
    routing question the policy already answered: the fallback desk is exactly
    what the document names for the case where a resolver cannot answer. So the
    failure is caught here, logged with the address it was for, and handed back
    as "no desk" — which the caller turns into a `fell_back` answer nobody has
    to read a stack trace to understand.
    """
    try:
        return queues.ensure_personal(email, name).slug
    except Exception:  # noqa: BLE001 — a registry outage is a fallback, not a crash
        log.exception("Could not open a personal desk", extra={"email": email})
        return None


def manager_of(graph: Any, email: str) -> tuple[str, str] | None:
    """The submitter's manager as (email, name), or None.

    Reads the `reports_to` edge the workforce sync already lands, so this needs
    no new data — only a flow willing to ask. `Employee` is keyed on email
    everywhere in this system, which is what lets a graph answer become a desk.
    """
    if not email or graph is None:
        return None
    from .graph import object_id  # deferred: graph imports are heavy

    person = graph.get(object_id(onto.EMPLOYEE, email))
    if person is None:
        return None
    for _, other in graph.neighbors(person.id, "reports_to", "out"):
        manager_email = str(other.props.get("email") or "")
        if manager_email:
            return manager_email, str(other.props.get("name") or other.label)
    return None


def name_of(graph: Any, email: str) -> str:
    """What to call this person, as the org chart holds it, or "".

    Only ever used for a label — the desk's slug comes from the email, so a
    cold deployment with no `Employee` for anybody still routes to the right
    inbox and merely prints an address on it.
    """
    if not email or graph is None:
        return ""
    from .graph import object_id  # deferred: graph imports are heavy

    person = graph.get(object_id(onto.EMPLOYEE, email))
    if person is None:
        return ""
    return str(person.props.get("name") or person.label or "")


def section(content: str, heading: str) -> str:
    """The body under one heading, up to the next heading of any level.

    Public because it is the house answer to a question four modules now ask:
    *which part of this governed page am I allowed to read?* Scoping is not
    tidiness — `plant-scheduling-parameters` carries ten tables and a quoted
    `- goal max: —` under its `## Open` heading, so a parser matching
    document-wide reads a quotation as a capacity row. Every parser over a
    multi-section asset starts here.

    Case-insensitive on the heading, because a page is written for a person: a
    reader asking for `source vocabulary` should find `## Source vocabulary`.
    """
    match = re.search(
        rf"^#{{1,4}}\s*{re.escape(heading)}\s*$(?P<body>.*?)(?=^#{{1,4}}\s|\Z)",
        content or "", re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    return match.group("body") if match else ""


#: The name this had while it was private and `connectors.production` was its
#: only outside caller. Kept as an alias rather than renamed at the call site,
#: because a helper going public should not make anybody's import a
#: coordination problem.
_section = section


def _clean_target(raw: str) -> str:
    """A queue slug or a resolver, from however the document wrote it."""
    text = (raw or "").strip().strip("`*_ ").lower()
    if text.startswith("manager-of") or text in ("direct manager", "manager"):
        return MANAGER_OF
    # Before the slug check below, and that ordering is the whole point:
    # `submitter` matches the slug pattern, so read as a literal it would name
    # a shared desk called "submitter" that nobody created and that every
    # submitter's review would pile into.
    if text in ("submitter", "the submitter", "submitter (self)"):
        return SUBMITTER
    # A slug, or prose we cannot honour. Prose is dropped rather than slugified:
    # inventing `vp-of-finance` from "VP of Finance" would name a desk nobody
    # created and route real work into it.
    return text if re.fullmatch(r"[a-z0-9][a-z0-9\-]*", text) else ""
