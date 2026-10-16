"""Order release, v1 — the answer to a disagreement finding, in the ERP."""

from __future__ import annotations

from ....flows import FlowStep
from ....runs import RunContext
from .. import domain
from ..domain import *  # noqa: F401,F403 — the body reads its domain's vocabulary

VERSION = 1
LABEL = "v1"
NOTES = (
    "Reads the write policy for the finding's system, checks preconditions "
    "against the live row, commits the release (or dismissal) through the "
    "gateway, lands the answer on the graph, and tells the order desk."
)

STEPS: list[FlowStep] = [
    FlowStep(id="t1", type="trigger", name="OrderReleaseApproved",
             summary="one run per approved finding — per case, unlike the windowed sweep that raised it, because a controller releases one order at a time and the run has to re-read the world rather than trust a state somebody looked at days ago",
             config={"event": "OrderReleaseApproved"}, next=["x1"]),
    FlowStep(id="x1", type="transform", name="read-write-policy",
             summary="the one row of the write table this flow may go through — resource, field and the transitions permitted out of each value — and the order it is about, whose ERP record id is read off the object's `external_id` and never sliced out of its transaction number",
             config={"source": "estate_writes:WRITABLE",
                     "target": "sales-order-status"},
             next=["z1"]),
    FlowStep(id="z1", type="resolve", name="check-preconditions",
             summary="re-reads the record in the ERP and answers one of five ways — proceed, already ours, resolved by somebody else, escalate, or refused by the table before any request exists. Read-only, and deliberately outside the guarded write: this reaches a customer's network, and a read that failed inside an Action would be ledgered as a failed write",
             config={"reads": "erp.salesOrders", "outcomes": "5",
                     "compares": "value, not updatedAt"},
             next=["a1", "k1"]),
    FlowStep(id="a1", type="action", name="release-sales-order",
             summary="the intent is journalled before the request leaves, one PATCH is sent, the record is read back, and the finding moves to `resolved` in the same commit — write-ahead because a request that raises may still have landed, and verified by re-read because a cheerful 200 that changed nothing settles `unverified` and raises a Task instead of reporting success",
             config={"target": "sales-records",
                     "gateway": "action-gateway",
                     "journal": "estate_write_journal",
                     "asserts": "no human claim on the order; at most one PATCH per run"},
             next=["s1"]),
    FlowStep(id="s1", type="action", name="apply-source-records",
             summary="what the ERP said when it was read back, landed as an observation under `erp:erp-sales` — the same claim the feed itself writes, so the delta that carries this change home is a byte-identical no-op rather than a conflict",
             config={"target": "entity-layer",
                     "gateway": "action-gateway",
                     "regime": "observation"},
             next=["k1"]),
    FlowStep(id="k1", type="task", name="tell-the-order-desk",
             summary="a Task when the ERP is not where the approval left it, or when it accepted the write and did not change — and deliberately nothing at all when somebody else had already released the order, which is a number and not an interruption",
             config={"queue": "order-desk"}, next=["e1"]),
    FlowStep(id="e1", type="event", name="SalesOrderReleased",
             summary="emitted on commit — or `OrderReleaseWithheld`, just as loudly, because a run that decided not to write is not a run that did nothing",
             config={"event": "SalesOrderReleased"}),
]


def _release_body(service, ctx: RunContext, inputs: dict) -> dict:
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


def build_body(service):
    def body(ctx: RunContext, inputs: dict) -> dict:
        return _release_body(service, ctx, inputs)
    return body
