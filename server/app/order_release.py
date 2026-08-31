"""The outbound Action: releasing one sales order, and everything that guards it.

`disagreement_sweep` notices that the CRM says a deal was won and the ERP has
not released the order behind it. It writes an edge and asks a controller. This
is what happens when the controller says yes — the only flow in the system that
changes a record Cumulus does not own.

**Per case, and started by an Event rather than by a resume.** The sweep is
windowed and does not suspend: releasing revenue is not a step in a pass over
the book, it is a decision somebody makes days later about one order. So the
Task's approval emits `OrderReleaseApproved` and this flow starts fresh, which
is what lets it re-read the world instead of acting on a state a sweep looked at
last Tuesday.

**Two Actions, two regimes, and the split is the point.** `a1` is the decision:
a human approved a change and Cumulus made it. `s1` is an observation: the ERP
was asked afterwards what it now holds, and its answer is landed the way every
other thing the ERP says is landed. They are two ledger entries because they are
two different claims about the world, and collapsing them would produce the one
failure this flow must never cause:

    Cumulus never writes an authority claim it did not read back from the ERP.

A `human:` claim on `SalesOrder.status` would outrank `erp:` **forever** —
`ontology.PRECEDENCE` puts human corrections above every system — so the order
would read `pending-fulfillment` in Cumulus no matter what the ERP later did
with it, and every future disagreement about that order would be invisible. The
graph learns the new status from `erp:erp-sales`, the same source the feed uses,
or it does not learn it at all. The tests assert the claim sources after a
release for exactly this reason.

**Five outcomes before anything leaves the process.** `z1` re-reads the row and
answers one of five ways, and only one of them writes:

| the ERP holds        | the journal says            | what happens                  |
|----------------------|-----------------------------|-------------------------------|
| `Pending Approval`   | anything                    | proceed — one PATCH           |
| `Pending Fulfillment`| an entry asking for it      | already ours — no PATCH       |
| `Pending Fulfillment`| nothing                     | resolved elsewhere — no PATCH |
| anything else, or 404| anything                    | escalate to the desk, no write|
| —                    | the table does not allow it | refused, before any HTTP      |

The last row never reaches the wire and the fourth never reaches the writer.
That ordering is load-bearing: an `EstateWriteRefused` escaping the guarded
write would be ledgered `failed`, and `failed` is the accusation that the
*world* broke. A stale precondition is not the world breaking. So the flow
catches it here, where it is what it actually is — a fact about the estate, told
to a desk.

**`retries=1` is safe only because of the journal.** A step that replays its
body after a wire error must not send a second PATCH, and the estate offers no
`If-Match` to stop it. So a second attempt asks the journal whether the first
one reached the wire, and if it did, asks the *estate* what it did — the same
value comparison the precondition makes, at the only other moment it matters.

**No inverse Action.** Moving an order back is a second decision with its own
approval, not a rollback this flow is entitled to perform. Nothing here undoes
anything, and the Task copy says so out loud.
"""

from __future__ import annotations

import logging
from typing import Any

from .auth import Principal, flow_principal
from .disagreement_sweep import ORDER_DESK_QUEUE, SCAN, STANDING
from .estate import ERP_SALES
from .estate_writes import (
    EstateWriteError,
    EstateWriteRecord,
    WritableTarget,
    entries_for,
    writable,
    write_id,
)
from .graph import Claim, GraphWriter, LinkRecord, parse_ref
from .models import utcnow
from .runs import RunContext
from .sync import _slug
from .tasks import Task, TaskEvidence, TaskService

log = logging.getLogger("cumulus.order_release")

ORDER_RELEASE_FLOW_ID = "order-release"
#: The fact that starts it: not "an order changed" but "somebody with the desk
#: for it said release this one".
ORDER_RELEASE_EVENT = "OrderReleaseApproved"
RELEASED_EVENT = "SalesOrderReleased"
#: The other ending, and it is emitted just as loudly. A run that decided not
#: to write is not a run that did nothing, and a log with only the successes in
#: it cannot answer "what did Cumulus do about that finding".
WITHHELD_EVENT = "OrderReleaseWithheld"

#: The decision, the observation, and the withdrawal — three Actions for one
#: Task, because they are three different things to have done.
RELEASE_ACTION = "release-sales-order"
OBSERVE_ACTION = "apply-source-records"
#: Rejecting is a write on the finding's own status and nothing else, so it
#: commits under the Action that wrote `standing` in the first place. It is
#: deliberately *not* `release-sales-order`: a ledger row saying
#: `release-sales-order committed` for a decision that declined to release is a
#: sentence an auditor would have to read the payload to disbelieve.
DISMISS_ACTION = "record-disagreements"

#: The row in `estate_writes.WRITABLE` this flow is allowed to go through. Named
#: rather than searched for: which door a flow uses is a property of the flow.
RELEASE_TARGET = "sales-order-status"

# -- the five outcomes --------------------------------------------------------

PROCEED = "proceed"
ALREADY_APPLIED = "already_applied"
RESOLVED_EXTERNALLY = "resolved_externally"
ESCALATE = "escalate"
REFUSED = "refused"
OUTCOMES: tuple[str, ...] = (
    PROCEED, ALREADY_APPLIED, RESOLVED_EXTERNALLY, ESCALATE, REFUSED,
)
#: The three that end with the ERP holding the released value, and therefore
#: the three where the finding stops being true and the guarded write runs.
SETTLING: frozenset[str] = frozenset(
    {PROCEED, ALREADY_APPLIED, RESOLVED_EXTERNALLY}
)

#: `disagreement_sweep.FINDING_STATUSES` is closed and `resolved` is its one
#: word for a finding that is over. *How* it ended is a property of the
#: resolution rather than a fourth status, which is what the console's
#: "resolved, split by_cumulus/externally" tile actually needs — two numbers off
#: one status, not two statuses that every reader has to remember to add up.
RESOLVED = "resolved"
DISMISSED = "dismissed"
BY_CUMULUS = "by_cumulus"
EXTERNALLY = "externally"


# ---------------------------------------------------------------------------
# x1 — the door, and the case it is being opened for
# ---------------------------------------------------------------------------


def read_write_policy(service, finding_ref: str) -> dict:
    """The permitted write, and the two rows this one is about.

    Half governance and half lookup, and they belong in one step because
    neither is useful alone: the table says `erp.salesOrders.status` may move
    from `Pending Approval` to `Pending Fulfillment`, and the finding says
    *which* sales order. A run journal carrying only the first would not let a
    reader check the second.

    **The record id comes off the object, never off the tranId.** The ERP
    addresses this row as `salesOrders/5037` and the graph, the Task and every
    human involved call it `SO5037`. `SalesOrder.external_id` is the only thing
    that maps between them — it is carried by the sales feed for exactly this
    moment — and string surgery on a transaction id is a guess that works until
    a customer numbers their orders differently.
    """
    ref = parse_ref(finding_ref)
    link = (
        service.graph.store.get_link(ref.id)
        if ref.namespace == "link" and ref.id else None
    )
    if link is None or not link.live:
        raise LookupError(f"no live finding at `{finding_ref}`")
    order = service.graph.get(link.to_id)
    if order is None:
        raise LookupError(f"the finding's order {link.to_id} is not in the graph")

    spec = writable(RELEASE_TARGET)
    refusal = _refuse_the_table(spec)
    policy: dict[str, Any] = {
        "target": RELEASE_TARGET,
        "writable": not refusal,
        "refusal": refusal,
        "finding_id": link.id,
        "finding_type": link.type,
        "finding_ref": f"link:{link.type}/{link.id}",
        "order_id": order.id,
        "order_number": order.natural_key,
        "record_id": str(order.props.get("external_id") or ""),
        "graph_status": str(order.props.get("status") or ""),
        "state_when_found": dict(link.props.get("state_when_found") or {}),
    }
    if spec is not None:
        policy.update({
            "app": spec.app, "resource": spec.resource, "field": spec.field,
            "action": spec.action,
            "transitions": {k: list(v) for k, v in spec.transitions.items()},
        })
    if policy["writable"] and not policy["record_id"]:
        # The graph holds the order and not the ERP's id for it — a feed that
        # landed without `external_id`. Refused rather than escalated: there is
        # no address to knock on, so nothing about the estate is in question.
        policy["writable"] = False
        policy["refusal"] = (
            f"{order.natural_key} carries no `external_id`, so there is no "
            "record in the ERP for this system to address"
        )
    return policy


def _refuse_the_table(spec: WritableTarget | None) -> str:
    """Why this write is not one the table permits, or "" if it is.

    Three refusals, all of them about *Cumulus's own declaration* rather than
    about the customer's data, which is what separates them from an escalation.
    They are checked before the estate is read at all, so a misconfigured write
    plane costs a customer's ERP not even a GET.
    """
    if spec is None:
        return (
            f"`{RELEASE_TARGET}` is not a writable target — the write table "
            "does not name it, and a write it does not name is one nobody "
            "approved"
        )
    if spec.action != RELEASE_ACTION:
        # The table and the Gateway have to agree about which Action a target
        # is written through, or the ledger entry and the journal row would be
        # two accounts of one event that cannot be joined.
        return (
            f"`{spec.id}` is written through `{spec.action}` and this flow "
            f"commits `{RELEASE_ACTION}`"
        )
    crowded = sorted(
        observed for observed, values in spec.transitions.items()
        if len(values) > 1
    )
    if crowded:
        # Not a hypothetical tidiness check: the moment a row offers a choice,
        # something has to make it, and that something is a governance decision
        # with a person attached — not a flow quietly taking the first entry.
        return (
            f"`{spec.id}` names more than one destination from "
            f"{', '.join(repr(c) for c in crowded)}, and nothing in this flow "
            "chooses between them"
        )
    return ""


# ---------------------------------------------------------------------------
# z1 — the precondition
# ---------------------------------------------------------------------------


def check_preconditions(service, policy: dict) -> dict:
    """Re-read the row and answer in one of five ways. Writes nothing.

    Outside the guarded write for `disagreement_sweep.z1`'s reason and one
    more: this reads a customer's system over the network, and a read that
    happens inside an Action is a read whose failure would be ledgered as a
    failed *write*.

    The journal is consulted only when the estate already holds the value we
    would have written, because that is the only moment the two answers differ
    — "we did this" and "somebody else did" look identical on the row, and the
    difference is the entire reason a write journal exists.
    """
    if not policy["writable"]:
        return _outcome(REFUSED, policy["refusal"])

    writer = getattr(service, "estate_writer", None)
    if writer is None:
        return _outcome(
            ESCALATE,
            "no estate writer is wired into this deployment, so nothing can "
            "be read or written",
        )
    spec = writable(policy["target"])
    try:
        row = writer.read(spec.app, spec.resource, policy["record_id"])
    except EstateWriteError as exc:
        # A 404 and an outage are the same answer here: the row this decision
        # was made about could not be shown to still exist. Neither is a
        # failure of the write — nothing was attempted — and both are somebody
        # else's to look at.
        return _outcome(
            ESCALATE,
            f"the ERP could not be read for "
            f"{spec.resource}/{policy['record_id']}: {exc}",
        )

    observed = str(row.get(spec.field) or "")
    destinations = spec.destinations(observed)
    if len(destinations) == 1:
        return _outcome(
            PROCEED,
            f"the ERP still holds `{observed}`, which the write table may "
            f"move to `{destinations[0]}`",
            observed=observed, value=destinations[0], record=row,
        )

    reachable = {v for values in spec.transitions.values() for v in values}
    if observed in reachable:
        # Already where we would have put it. Ours or somebody else's?
        ours = [
            e for e in entries_for(writer.journal, spec.id, policy["record_id"])
            if e.value == observed
        ]
        if ours:
            # Any state counts, `intent` and `failed` included. A row in the
            # journal asking for this value means a request either left this
            # process or was about to, and `failed` explicitly means "may have
            # landed" — the write-ahead entry exists precisely so that a
            # request nobody got an answer to is still attributable.
            return _outcome(
                ALREADY_APPLIED,
                f"the ERP holds `{observed}` and this system asked it to — "
                f"{len(ours)} journal entr(ies) for {spec.id}",
                observed=observed, value=observed, record=row,
                journal=[e.id for e in ours],
            )
        return _outcome(
            RESOLVED_EXTERNALLY,
            f"the ERP already holds `{observed}` and nothing in the write "
            "journal asked for it — somebody released this order elsewhere",
            observed=observed, value=observed, record=row,
        )

    return _outcome(
        ESCALATE,
        f"the ERP holds `{observed}`, which is not a state this system may "
        "move an order out of — it has changed since the finding was raised",
        observed=observed, record=row,
    )


def _outcome(
    outcome: str, reason: str, *, observed: str = "", value: str = "",
    record: dict | None = None, journal: list[str] | None = None,
) -> dict:
    return {
        "outcome": outcome, "reason": reason, "observed": observed,
        "value": value, "record": record or {}, "journal": journal or [],
    }


# ---------------------------------------------------------------------------
# a1 — the decision
# ---------------------------------------------------------------------------


def _send(service, ctx: RunContext, spec: WritableTarget, policy: dict,
          pre: dict, actor: str) -> dict:
    """One PATCH at most, whatever number of times this body runs.

    The guard is the journal and not a flag, because the question a retry has
    to answer is "did the *estate* see it", and only the estate can answer
    that. Four cases, and the shape of the answer is different in each:

    - no entry — the first attempt in this run; send.
    - an entry past `intent`/`failed` — the wire already answered us; do not
      ask again, and settle on what it said.
    - an entry at `intent` or `failed` — a request left, or was about to, and
      we never learned what it did. Ask the estate before asking it again.
    - and if that re-read finds the row somewhere the table cannot write from,
      stop: the world moved under this run, and that is an escalation rather
      than a failure.
    """
    writer = service.estate_writer
    entry_id = write_id(spec.id, policy["record_id"], ctx.record.id)
    prior = writer.journal.get(entry_id)
    observed, value = pre["observed"], pre["value"]

    if prior is not None and prior.state not in ("intent", "failed"):
        return {"record": prior, "sent": False,
                "why": "this run had already sent it and been answered"}
    if prior is not None:
        settled = writer.reconcile(prior)
        if settled.state == "applied":
            return {"record": settled, "sent": False,
                    "why": "an earlier attempt in this run had landed after all"}
        if not spec.allows(settled.verified, value):
            return {"record": settled, "sent": False, "stale": settled.verified,
                    "why": f"the ERP now holds `{settled.verified}`"}
        observed = settled.verified

    return {
        "record": writer.apply(
            spec.id, policy["record_id"], observed=observed, value=value,
            run_id=ctx.record.id, actor=actor,
        ),
        "sent": True,
        "why": f"`{observed}` → `{value}`",
    }


def release(service, ctx: RunContext, policy: dict, pre: dict,
            graph: GraphWriter, *, task_id: str, decided_by: str,
            actor: str) -> dict:
    """Change the ERP if that is what the precondition asked for, then close
    the finding. Everything here runs inside one guarded Action.

    The finding's status moves in the same commit as the PATCH on purpose: they
    are one decision, and an auditor reading the ledger entry sees the edge it
    closed beside the record it changed. What does *not* happen here is any
    claim on the order — see the module docstring, and `s1`.
    """
    spec = writable(policy["target"])
    outcome = pre["outcome"]
    result: dict[str, Any] = {
        "outcome": outcome, "patched": False, "state": "",
        "observed": pre["observed"], "value": pre["value"],
        "verified": pre["observed"], "write_id": "", "estate_updated_at": "",
        "resolved": False, "resolved_how": "", "error": "",
    }

    if outcome == PROCEED:
        sent = _send(service, ctx, spec, policy, pre, actor)
        record: EstateWriteRecord = sent["record"]
        result.update({
            "patched": bool(sent["sent"]), "state": record.state,
            "write_id": record.id, "verified": record.verified,
            "estate_updated_at": record.estate_updated_at,
            "error": record.error, "why": sent["why"],
        })
        if "stale" in sent:
            # The one edge the flag exists for: the row moved between `z1` and
            # here. Not `failed` — nothing broke and nothing was sent — and not
            # `refused` either, because the Gateway allowed this and was right
            # to. It is the same fact `z1` reports when the ERP holds `Billed`,
            # noticed one step later, so it gets the same name and the same
            # desk.
            result["outcome"] = ESCALATE
            result["verified"] = sent["stale"]
            result["reason"] = (
                f"the ERP moved to `{sent['stale']}` between the precondition "
                "and the write; nothing was sent"
            )
            return result
        if record.state != "applied":
            # The PATCH did not raise and the re-read did not agree. A Task,
            # never a success, and emphatically not a finding we close: the
            # estate is the source of truth for whether a write took, and it
            # has just said this one did not.
            result["reason"] = (
                f"the ERP answered the write and then reported "
                f"`{record.verified or 'nothing'}` — the change did not take"
            )
            return result

    result["resolved"] = True
    result["resolved_how"] = (
        EXTERNALLY if outcome == RESOLVED_EXTERNALLY else BY_CUMULUS
    )
    _close_finding(
        graph, service, policy, result["resolved_how"],
        run_id=ctx.record.id, task_id=task_id, decided_by=decided_by,
        note=pre["reason"],
    )
    return result


def _close_finding(
    graph: GraphWriter, service, policy: dict, how: str, *,
    run_id: str, task_id: str, decided_by: str, note: str,
) -> LinkRecord | None:
    """The finding, at `resolved`. The edge and nothing else.

    A props write on a live edge, which merges rather than replacing, so the
    signals and the confidence that produced the finding survive the decision
    that closed it — the numbers a later tuning pass needs are exactly the ones
    a "clean up on resolve" would delete.
    """
    link = service.graph.store.get_link(policy["finding_id"])
    if link is None or not link.live:
        return None
    return graph.link(
        link.type, link.from_id, link.to_id,
        props={
            "status": RESOLVED,
            # Two numbers off one status — see `RESOLVED` above.
            "resolved_how": how,
            "resolved_at": utcnow().isoformat(),
            "resolved_by": decided_by,
            "task_id": task_id,
            "run_id": run_id,
            "resolution": note,
        },
    )


def dismiss_finding(
    graph: GraphWriter, link: LinkRecord, *, task_id: str, decided_by: str,
    note: str,
) -> dict:
    """The other verdict: the two systems may disagree here, and that is fine.

    `dismissed` rather than a reversal, because the finding was *true* — the
    CRM really does say won and the ERP really has not released — and what the
    controller decided is that it does not need acting on. Withdrawing the edge
    would delete a correct observation in order to record a judgment about it.

    The sweep's own guard is what makes this stick: `raise_desk_tasks` refuses
    to re-raise a finding whose link is not at `standing`, so tonight's pass
    finds this edge, reads its status and leaves it alone.
    """
    graph.link(
        link.type, link.from_id, link.to_id,
        props={
            "status": DISMISSED,
            "dismissed_at": utcnow().isoformat(),
            "dismissed_by": decided_by,
            "task_id": task_id,
            "resolution": note or "left as it is by the order desk",
        },
    )
    log.info(
        "Disagreement dismissed",
        extra={"linkId": link.id, "taskId": task_id, "actor": decided_by},
    )
    return {"action": "dismissed", "link_id": link.id}


# ---------------------------------------------------------------------------
# s1 — the observation
# ---------------------------------------------------------------------------


def land_the_answer(graph: GraphWriter, policy: dict, status: str,
                    run_id: str) -> dict:
    """What the ERP says the order is now, landed as the ERP saying it.

    The claim source is the sales feed's own — `erp:erp-sales`, from
    `estate.ERP_SALES` so there is one spelling of it — and that is not a
    convenience. `graph.upsert` deduplicates on (source, field, value), so when
    the next delta carries this order home the feed writes a claim byte-identical
    to this one and nothing happens: no revision bump, no second claim, no
    conflict for a steward to look at. That is the whole of "echo suppression"
    on the graph side, and it works because this is an *observation* wearing the
    right authority rather than a decision wearing a human's.

    Slugged, because `SalesOrder.status` is the ontology's vocabulary and the
    ERP's `Pending Fulfillment` is the ERP's. `sync._slug` is the one place
    that translation lives.
    """
    if not status:
        return {"landed": False, "status": "", "source": ""}
    source = f"erp:{ERP_SALES}"
    graph.upsert(
        "SalesOrder", policy["order_number"],
        [Claim(field="status", value=_slug(status), source=source)],
        provenance=f"read back from the ERP by {run_id}",
    )
    return {"landed": True, "status": _slug(status), "source": source}


# ---------------------------------------------------------------------------
# k1 — what the desk is told
# ---------------------------------------------------------------------------


def tell_the_desk(
    tasks: TaskService, policy: dict, pre: dict, released: dict, *,
    queue: str, run_id: str, task_id: str,
) -> dict:
    """A Task when a person has to look, and silence when nobody does.

    Three reasons to raise one and one loud reason not to. `resolved_externally`
    raises nothing at all — the order is where the controller wanted it and the
    only thing that happened is that somebody else got there first, which is a
    number on a dashboard and not an interruption.
    """
    outcome = released.get("outcome") or pre["outcome"]
    state = released.get("state") or ""
    if outcome in (ESCALATE, REFUSED):
        title = (
            f"{policy['order_number']}: the ERP is not where you left it"
            if outcome == ESCALATE
            else f"{policy['order_number']}: Cumulus refused its own write"
        )
        summary = (
            f"The release was approved and not made. {released.get('reason') or pre['reason']}. "
            "Nothing in the ERP was changed and the finding is still standing. "
            "Releasing it now is a fresh decision on what the order actually "
            "says today."
        )
    elif state and state != "applied":
        title = f"{policy['order_number']}: the ERP said yes and did not change"
        summary = (
            f"Cumulus asked the ERP to set {policy['field']} to "
            f"`{released.get('value')}`, the request was accepted, and the "
            f"re-read found `{released.get('verified') or 'nothing'}`. The "
            "write journal holds this as unverified. It may have landed and "
            "it may not — the order has to be looked at in the ERP itself, "
            "and nothing here will retry it."
        )
    else:
        return {"desk_tasks": 0, "queue": queue}

    raised = tasks.create(Task(
        id=f"task_release_{run_id}",
        kind="exception",
        title=title,
        summary=summary,
        queue=queue,
        priority="high",
        flow_id=ORDER_RELEASE_FLOW_ID,
        run_id=run_id,
        case_ref=policy["finding_ref"],
        refs=[policy["finding_ref"], f"graph:{policy['order_id']}"],
        evidence=[
            TaskEvidence(label="The order",
                         value=f"{policy['order_number']} "
                               f"({policy.get('resource', '')}/"
                               f"{policy['record_id']})"),
            TaskEvidence(label="The ERP holds",
                         value=released.get("verified")
                         or pre.get("observed") or "unreadable"),
            TaskEvidence(label="Approved on",
                         value=task_id or "a decision with no task id"),
            TaskEvidence(label="Write journal",
                         value=released.get("write_id") or "no entry"),
        ],
        resumes=(
            "nothing resumes: releasing this order now is a new decision, and "
            "there is no undo here"
        ),
        created_at_iso=utcnow().isoformat(),
    ))
    return {"desk_tasks": 1, "queue": queue, "task_id": raised.id}


# ---------------------------------------------------------------------------
# The flow
# ---------------------------------------------------------------------------


def register(service) -> None:
    if service.engine is None:
        return
    service.engine.register(
        ORDER_RELEASE_FLOW_ID,
        lambda ctx, inputs: release_body(service, ctx, inputs),
    )


def release_body(service, ctx: RunContext, inputs: dict) -> dict:
    """Read the door, check the row, write it, read it back, tell the desk."""
    finding_ref = str(inputs.get("finding") or "")
    task_id = str(inputs.get("task_id") or "")
    decided_by = str(inputs.get("decided_by") or "")
    queue = str(inputs.get("queue") or ORDER_DESK_QUEUE)
    actor = decided_by or ctx.record.actor

    ctx.event(
        "t1", ORDER_RELEASE_EVENT,
        f"{decided_by or 'the order desk'} approved the release behind "
        f"{finding_ref}",
        kind="trigger",
    )

    policy = ctx.step(
        "x1", "read-write-policy", "transform",
        fn=lambda: read_write_policy(service, finding_ref),
        summary=lambda out: (
            f"{out['target']}: {out.get('app', '?')}.{out.get('resource', '?')}"
            f".{out.get('field', '?')} · "
            + (
                " · ".join(
                    f"`{was}` → {', '.join(f'`{v}`' for v in to)}"
                    for was, to in (out.get("transitions") or {}).items()
                )
                if out["writable"] else f"refused — {out['refusal']}"
            )
            + f" · {out['order_number']} is "
            f"{out.get('resource', '?')}/{out['record_id'] or '(no id)'}"
        ),
    )
    ctx.note_refs(policy["finding_ref"], f"graph:{policy['order_id']}")

    pre = ctx.step(
        "z1", "check-preconditions", "resolve",
        fn=lambda: check_preconditions(service, policy),
        summary=lambda out: f"{out['outcome']} — {out['reason']}",
        refs=[policy["finding_ref"]],
    )

    released: dict = {}
    if pre["outcome"] in SETTLING:
        act: dict[str, Any] = {}

        def commit() -> dict:
            # Built inside the step body so a retried attempt gets its own
            # ledger entry rather than appending one id twice — two attempts
            # are two things that happened, and an append-only log that
            # overwrites is not one.
            act["pending"] = service.gateway.action(
                RELEASE_ACTION,
                ctx.principal or flow_principal(ORDER_RELEASE_FLOW_ID, queue),
                run=ctx.record, step_id="a1", task_id=task_id,
                decided_by=decided_by, queue=queue,
            )
            return act["pending"].commit(lambda w: release(
                service, ctx, policy, pre, w,
                task_id=task_id, decided_by=decided_by, actor=actor,
            ))

        released = ctx.step(
            "a1", RELEASE_ACTION, "action",
            fn=commit,
            writes=lambda _out: (
                act["pending"].writes if "pending" in act else []
            ),
            summary=lambda out: (
                (
                    f"PATCH {policy.get('resource')}/{policy['record_id']} "
                    f"{policy.get('field')}=`{out['value']}` · "
                    f"journal {out['state']}"
                    if out["patched"]
                    else f"no PATCH — {out.get('why') or out['outcome']}"
                )
                + (
                    f" · finding resolved ({out['resolved_how']})"
                    if out["resolved"] else " · the finding still stands"
                )
            ),
            refs=[policy["finding_ref"]],
            # Safe only because of the journal guard in `_send`, which is
            # tested for exactly this: a second attempt issues no second PATCH.
            retries=1,
            escalate_queue=queue,
        )

    # Whatever the estate last told us, landed as the ERP's own claim. It runs
    # on the escalation path too, and that is the useful half: an order that
    # moved to `Billed` under us is a fact the graph should learn from the read
    # we just made, not one it waits for the next delta to hear.
    observed = released.get("verified") or pre.get("observed") or ""
    landed: dict = {}
    if observed:
        watch: dict[str, Any] = {}

        def observe() -> dict:
            watch["pending"] = service._act(ctx, OBSERVE_ACTION, "s1")
            return watch["pending"].commit(lambda w: land_the_answer(
                w, policy, observed, ctx.record.id,
            ))

        landed = ctx.step(
            "s1", OBSERVE_ACTION, "action",
            fn=observe,
            writes=lambda _out: (
                watch["pending"].writes if "pending" in watch else []
            ),
            summary=lambda out: (
                f"{policy['order_number']}.status = `{out['status']}` claimed "
                f"by `{out['source']}` — the ERP's own answer, at the ERP's "
                "own authority, which is what keeps the next delta a no-op"
                if out["landed"] else "nothing read back, nothing landed"
            ),
        )

    asked = ctx.step(
        "k1", "tell-the-order-desk", "sync",
        fn=lambda: tell_the_desk(
            service.tasks, policy, pre, released,
            queue=queue, run_id=ctx.record.id, task_id=task_id,
        ),
        summary=lambda out: (
            f"{out['desk_tasks']} Task raised to {out['queue']}"
            if out["desk_tasks"] else "nothing a person needs to look at"
        ),
    )

    outcome = released.get("outcome") or pre["outcome"]
    settled = bool(released.get("resolved"))
    ctx.event(
        "e1", RELEASED_EVENT if settled else WITHHELD_EVENT,
        (
            f"{policy['order_number']} is "
            f"`{released.get('verified') or pre.get('observed')}` in the ERP"
            + (
                " — Cumulus moved it" if released.get("patched")
                else " — it was already there"
            )
            if settled else
            f"{policy['order_number']} was not released: {outcome} — "
            f"{released.get('reason') or pre['reason']}"
        ),
    )

    return {
        "kind": "order-release",
        "outcome": outcome,
        "reason": released.get("reason") or pre["reason"],
        "order": policy["order_number"],
        "record_id": policy["record_id"],
        "finding": policy["finding_ref"],
        "observed": pre.get("observed", ""),
        "value": released.get("value", ""),
        "verified": released.get("verified", ""),
        "patched": bool(released.get("patched")),
        "write_state": released.get("state", ""),
        "write_id": released.get("write_id", ""),
        "resolved": settled,
        "resolved_how": released.get("resolved_how", ""),
        "landed_status": landed.get("status", ""),
        **asked,
    }


# ---------------------------------------------------------------------------
# The order desk's decision
# ---------------------------------------------------------------------------


def apply_release_decision(
    service, task: Task, principal: Principal | None = None
) -> dict | None:
    """What a `state_disagreement` verdict does. The third resume hook shape.

    `apply_match_decision` writes the record the Task was about;
    `apply_merge_decision` flips an assertion already in the graph. This one
    does neither on the approve path — it **emits a fact and returns**, because
    the work it authorizes reaches into a customer's ERP and that deserves a
    run of its own: a journal, a ledger entry, a re-read and a Task if it goes
    wrong. A hook that did the PATCH inline would have no durable account of
    itself if the request took eleven seconds and the process was recycled at
    ten.

    Rejecting has no such need. It is one props write on one edge, so it
    commits here, synchronously, through the Gateway — which is also what makes
    a refusal reach `tasks_api` and re-open the Task instead of silently
    marking it done.
    """
    if task.kind != "state_disagreement" or task.decision is None:
        return None
    ref = parse_ref(task.case_ref)
    if ref.namespace != "link" or ref.type != SCAN.link_type:
        return None
    link = service.graph.store.get_link(ref.id)
    if link is None or not link.live:
        return None
    if str(link.props.get("status") or "") != STANDING:
        # Already ruled on. A second decider learns that from the Task store's
        # own 409; what this refuses is re-deciding the finding — and, on the
        # approve path, starting a second run that would ask a customer's ERP
        # to do something twice.
        return None

    decided_by = task.decision.decided_by
    if task.decision.verdict == "rejected":
        act = service.gateway.action(
            DISMISS_ACTION,
            principal or flow_principal(
                task.flow_id or ORDER_RELEASE_FLOW_ID, task.queue,
            ),
            step_id="desk-decision", task_id=task.id, decided_by=decided_by,
            target=f"link:{ref.type}/{ref.id}",
        )
        return act.commit(lambda w: dismiss_finding(
            w, link, task_id=task.id, decided_by=decided_by,
            note=task.decision.note,
        ))

    # Approved, or corrected — `apply_merge_decision`'s reading, for its
    # reason: there is one proposal on this Task and a correction to it is
    # still an instruction to act, not a third verdict this flow knows how to
    # interpret.
    record = service.dispatcher.emit(
        ORDER_RELEASE_EVENT,
        {
            "finding": f"link:{ref.type}/{ref.id}",
            "task_id": task.id,
            "decided_by": decided_by,
            "queue": task.queue or ORDER_DESK_QUEUE,
        },
        actor=decided_by or f"task:{task.id}",
        case_ref=f"link:{ref.type}/{ref.id}",
        principal=principal,
    ).one()
    log.info(
        "Order release approved",
        extra={"taskId": task.id, "linkId": link.id, "runId": record.id,
               "actor": decided_by},
    )
    return {
        "action": "released", "link_id": link.id, "flow_id": record.flow_id,
        "run_id": record.id, "status": record.status, **(record.outputs or {}),
    }
