"""Credential tracking, v1 — the shape this flow has run since it landed.

The executable body and the declarative steps travel together, for the reason
`accounts_payable/v6` gives: the map the console draws is this module's STEPS
and the engine runs this module's body, so the two cannot drift apart without a
diff in one file saying so.
"""

from __future__ import annotations

from datetime import date

from ....flows import FlowStep
from ....models import utcnow
from ....runs import RunContext
from ....task_screens import TaskScreen, TaskUi
from ..domain import (
    ALERT_ACTION,
    ALERT_EVENT,
    COMPLIANCE_RECORDS,
    COMPLIANCE_WINDOW_EVENT,
    EXCEPTION_CLASSES,
    EXCEPTION_SCREEN,
    POLICY_ASSET_ID,
    AlertPolicy,
    StageDecision,
    TrackedRow,
    decide_stages,
    record_alerts,
    screen_exceptions,
)

VERSION = 1
LABEL = "v1"
NOTES = (
    "The whole of the training and recertification tracking design, as one "
    "governed pass: five spreadsheet sub-processes and three Excel trackers "
    "answered by one windowed run over the credential and inspection rows the "
    "graph already holds. A timer closes a window, the "
    "`certification-alert-policy` asset says what is in scope, a pure Rule "
    "decides which of the 60/30/15 stages each row is owed, one guarded "
    "Action stamps them all, and the four exception classes go to "
    "`hr-safety` as four aggregate Tasks — never one per row. "
    "**A stage already sent this cycle is not re-sent**, which is the guard "
    "that makes a daily cadence safe and an empty day a genuine no-op: no "
    "write, no notice, no Task. "
    "**Three Excel trackers, the `Last Notified` column and the "
    "`Last Run Status` column are not read and not written anywhere in this "
    "flow.** That state is these graph rows — `last_notified_on`, "
    "`last_notice_stage`, `status`, `retain_until` — and this run's journal. "
    "**What it deliberately does not do, because the customer scoped it "
    "out:** "
    "download a DOT document, update a date in a spreadsheet, retrieve an "
    "inspection photo. It tracks the row and asks; widening the automation "
    "past what was signed off would be a decision taken by whoever wrote the "
    "code. Sending the notices is also not here — the desks and the workspace "
    "are the notification in this release. And nothing deletes: a record past "
    "the seven-year horizon is reported for manual review, which is the "
    "customer's own rule and deletes-are-decisions arriving from their side."
)

STEPS: list[FlowStep] = [
    FlowStep(id="t1", type="trigger", name=COMPLIANCE_WINDOW_EVENT,
             summary="one run per window over the credential and inspection book — nothing arrived and nothing is waiting: a timer closed a window over rows the graph already holds, which is why this is the only windowed Trigger here that is not a delivery",
             config={"event": COMPLIANCE_WINDOW_EVENT, "wakes": "window"},
             next=["x1"]),
    FlowStep(id="x1", type="transform", name="read-alert-policy",
             summary="the whole read-set in one step, at one revision: the notice stages, the service-level floor a stage list is checked against, the categories tracked, the rule that makes an asset untracked, the retention horizon, the run cadence and the approval table — and it says out loud when the corpus holds no such document and the built-in default is standing in",
             config={"source": f"knowledge:{POLICY_ASSET_ID}"}, next=["x2"]),
    FlowStep(id="x2", type="transform", name="gather-expiring",
             summary="the standing question, asked of the graph: `Credential` and `Inspection` rows inside the widest stage, each already joined to its holder or its unit and to the supervisor the notice would go to — the fetching half, so the Rule need not fetch. Equipment whose `inspection_tracked` is not `yes` is excluded here and **counted**, never carried to the Rule to be rejected; a category the policy does not track is counted the same way; and a backlog past the window's bound is the next window's work, reported rather than dropped",
             config={"source": "graph:Credential,Inspection,Equipment,"
                               "Employee,Team",
                     "excludes": "inspection_tracked != yes (counted)"},
             next=["r1"]),
    FlowStep(id="r1", type="rule", name="stage-due",
             summary="pure, per row: which of the governed stages is due, given the expiry or next-due date, today, and the stage last sent. The narrowest stage reached wins, a row beyond the widest is not due, and **a stage at or below the one already sent is not re-sent** — that last line is what makes a daily cadence safe rather than a supervisor's inbox filling every morning, and a recertification resets the cycle for free because a renewal is a new record",
             config={"rule_type": "threshold",
                     "source": f"knowledge:{POLICY_ASSET_ID}"},
             next=["a1"]),
    FlowStep(id="a1", type="action", name="record-credential-alerts",
             summary="**one** guarded write for the whole window, not one per row: `last_notified_on` and `last_notice_stage` stamped, `status` moved, `retain_until` claimed. Regime `decision` and no queue — no source reported any of this, Cumulus determined it from a rule over a governed policy, and noticing a date is nobody's work, which is `record-disagreements`' reasoning exactly",
             config={"target": COMPLIANCE_RECORDS, "gateway": "action-gateway",
                     "action": ALERT_ACTION, "regime": "decision"},
             next=["c1"]),
    FlowStep(id="c1", type="check", name="screen-exceptions",
             summary="pure, and four classes in precedence order so one row belongs to exactly one of them: a missing or unreadable date, a holder nobody can be resolved to, a terminated holder still on the active roster, and a retained record past the horizon. Read over every row the window took rather than only the dated ones, because three of the four are not questions about a date",
             config={"rule_type": "precedence-list",
                     "classes": ", ".join(EXCEPTION_CLASSES),
                     "source": f"knowledge:{POLICY_ASSET_ID}"},
             next=["k1"]),
    FlowStep(id="k1", type="task", name="report-compliance-exceptions",
             summary="**one aggregate Task per exception class per window** — at most four, never one per row — each carrying its rows, routed through the governed approval table (one band, because an aggregate about records has no submitter to route back to and no amount to band on). The retention class carries the customer's own words: a record past seven years is reported for manual review and **never deleted automatically**, and nothing in this system can delete one",
             config={"queue": "hr-safety", "aggregate": "one per class",
                     "screen": EXCEPTION_SCREEN},
             next=["e1"]),
    FlowStep(id="e1", type="event", name=ALERT_EVENT,
             summary="the pass's own fact, with the per-stage counts. Nothing binds it: there is no per-case sibling to raise, because a notice is not a case — the row and the desk are where the work is",
             config={"event": ALERT_EVENT}),
]

#: One screen for this flow's one kind of Task. `by_kind` rather than
#: `otherwise` so a kind a later version raises gets the generic pane instead
#: of a screen built for a different question.
UI = TaskUi(by_kind={
    "exception": TaskScreen(
        screen_id=EXCEPTION_SCREEN,
        label="Open the exception review",
        hint=(
            "Opens the rows of one exception class, each with the date as the "
            "source wrote it and an editable date beside it. A date corrected "
            "here is a `human` claim, which outranks the issuing system — and "
            "nothing on this screen deletes a record."
        ),
    ),
})


def build_body(service):
    """Bind the tracking service and return the engine-runnable body."""

    def body(ctx: RunContext, inputs: dict) -> dict:
        window = inputs.get("window", "the credential and inspection book")
        today = _as_of(inputs)

        ctx.event(
            "t1", COMPLIANCE_WINDOW_EVENT,
            f"compliance window closed over {window} · as of "
            f"{today.isoformat()}",
            kind="trigger",
        )

        # -- x1: the governed read that decides whether anything happens -----
        policy = AlertPolicy.model_validate(ctx.step(
            "x1", "read-alert-policy", "transform",
            fn=lambda: service.read_policy().model_dump(),
            summary=lambda out: _policy_summary(out),
        ))
        ctx.note_refs(policy.source_ref)

        # -- x2: the standing question, asked of the graph -------------------
        gathered = ctx.step(
            "x2", "gather-expiring", "transform",
            fn=lambda: service.gather(policy, today),
            summary=lambda out: _gather_summary(out, policy),
            refs=[policy.source_ref],
        )
        rows = [TrackedRow.model_validate(r) for r in gathered["rows"]]
        candidates = [
            TrackedRow.model_validate(r) for r in gathered["candidates"]
        ]

        # -- r1: the Rule, and the guard the whole cadence rests on ----------
        ruled = ctx.step(
            "r1", "stage-due", "rule",
            fn=lambda: _decide(candidates, policy),
            summary=lambda out: _rule_summary(out),
            refs=[policy.source_ref],
        )
        decisions = [
            StageDecision.model_validate(d) for d in ruled["decisions"]
        ]

        # -- a1: one Action for the window -----------------------------------
        act = service._act(ctx, "a1")
        written = ctx.step(
            "a1", "record-credential-alerts", "action",
            fn=lambda: act.commit(lambda w: record_alerts(
                w, decisions, policy, today, ctx.record.id,
            )),
            writes=lambda _out: act.writes,
            summary=lambda out: (
                f"{out['alerted']} notice(s) stamped — "
                f"{out['credentials']} credential(s), "
                f"{out['inspections']} inspection(s) · "
                f"{out['status_moved']} status(es) moved · "
                f"{out['retention_claimed']} retention horizon(s) claimed"
                if out["alerted"] else
                "nothing was due, so nothing was written"
            ),
        )

        # -- c1: pure, four classes ------------------------------------------
        screened = ctx.step(
            "c1", "screen-exceptions", "check",
            fn=lambda: screen_exceptions(rows, policy, today),
            summary=lambda out: (
                " · ".join(
                    f"{name}: {out['counts'][name]}"
                    for name in EXCEPTION_CLASSES if out["counts"][name]
                ) + f" · horizon {out['horizon']}"
                if out["total"] else "no exception in any of the four classes"
            ),
        )

        # -- k1: the aggregates, when there are any --------------------------
        asked = ctx.step(
            "k1", "report-compliance-exceptions", "sync",
            fn=lambda: service.raise_exception_tasks(ctx, screened, policy),
            summary=lambda out: (
                f"{out['exception_tasks']} aggregate Task(s) raised"
                + (
                    f" · {out['exception_tasks_standing']} already standing "
                    "for the same rows and not re-raised"
                    if out["exception_tasks_standing"] else ""
                )
                if out["exception_tasks"] or out["exception_tasks_standing"]
                else "nothing to report, so nobody to ask"
            ),
        )

        # -- e1: the pass's own fact -----------------------------------------
        by_stage = ruled["counts"]["by_stage"]
        ctx.event(
            "e1", ALERT_EVENT,
            f"{written['alerted']} notice(s) recorded — "
            + (
                ", ".join(
                    f"{count} at {stage} days"
                    for stage, count in sorted(
                        by_stage.items(), key=lambda kv: -int(kv[0])
                    ) if count
                ) or "none at any stage"
            )
            + f" · {asked['exception_tasks']} exception Task(s)",
        )

        return {
            "window": window, "kind": "credential-tracking",
            "as_of": today.isoformat(),
            "cadence": policy.cadence,
            "policy_ref": policy.source_ref,
            "found_asset": policy.found_asset,
            "stages": policy.stages,
            "honors_sla_floor": policy.honors_sla_floor,
            "considered": gathered["considered"],
            "taken": gathered["taken"],
            "deferred": gathered["deferred"],
            "credentials": gathered["credentials"],
            "inspections": gathered["inspections"],
            "equipment": gathered["equipment"],
            "untracked_assets": gathered["untracked_assets"],
            "tracked_without_examination":
                gathered["tracked_without_examination"],
            "category_untracked": gathered["category_untracked"],
            "holder_unresolved": gathered["holder_unresolved"],
            "supervisor_unresolved": gathered["supervisor_unresolved"],
            "in_window": gathered["in_window"],
            **ruled["counts"],
            **written,
            "exceptions": screened["counts"],
            "retention_horizon": screened["horizon"],
            **asked,
            # What the pass decided, on the run, for `identity_sweep`'s
            # reason: a windowed flow's output is the only place its per-row
            # reasoning is legible without re-querying the graph. Capped,
            # because a journal is a document with a size limit.
            "moves": [
                {
                    "row": d.label, "kind": d.kind,
                    "subject": d.subject_label,
                    "days": d.days_remaining,
                    "from_stage": d.from_stage, "to_stage": d.to_stage,
                    "status": d.status_to,
                    "notify": d.supervisor_email,
                }
                for d in decisions if d.sent
            ][:20],
            "skipped": [
                {"row": d.label, "at_stage": d.from_stage, "why": d.reason}
                for d in decisions if not d.sent
            ][:20],
        }

    return body


def _as_of(inputs: dict) -> date:
    """The date the window closed over — the trigger's, or today's.

    On the payload rather than read off the clock inside the Rule, for two
    reasons that happen to agree: a window that closed at 23:59 and is answered
    at 00:01 must be answered *as of the window*, and a graded case has to be
    able to say what day it is without moving the machine's clock.
    """
    given = str(inputs.get("as_of") or "").strip()
    if given:
        try:
            return date.fromisoformat(given[:10])
        except ValueError:
            # Counted by nobody and named here: an unreadable window date is
            # the caller's mistake, and answering it as today is more honest
            # than failing a whole compliance pass over a string.
            pass
    return utcnow().date()


def _decide(candidates: list[TrackedRow], policy: AlertPolicy) -> dict:
    """The Rule step's output: a decision per row, and the counts."""
    decisions, counts = decide_stages(candidates, policy)
    return {
        "decisions": [d.model_dump() for d in decisions],
        "counts": counts,
    }


def _policy_summary(out: dict) -> str:
    stages = ", ".join(str(stage) for stage in out["stages"]) or "none"
    floor = out["sla_floor_days"]
    widest = max(out["stages"]) if out["stages"] else 0
    parts = [
        f"{out['source_ref']}: stages {stages} day(s)",
        f"floor ≥ {floor}",
        f"cadence {out['cadence']}",
        f"{len(out['categories'])} category(ies) tracked",
        f"retained {out['retention_years']} year(s), "
        f"{out['past_horizon']}",
    ]
    if widest < floor:
        # Said out loud rather than quietly honoured: a stage list whose widest
        # entry is under the floor breaks the promise the document states.
        parts.append(
            f"**the widest stage ({widest}) is under the {floor}-day "
            "service-level floor — this edit breaks the promise the policy "
            "states**"
        )
    if not out["found_asset"]:
        parts.append(
            "not in the corpus, running on the built-in default"
        )
    return " · ".join(parts)


def _gather_summary(out: dict, policy: AlertPolicy) -> str:
    parts = [
        f"{out['credentials']} credential(s) and {out['inspections']} "
        f"inspection(s) tracked",
        f"{out['in_window']} inside the {policy.widest_stage}-day stage",
    ]
    if out["untracked_assets"]:
        parts.append(
            f"{out['untracked_assets']} asset(s) excluded and counted — "
            f"{policy.untracked_when}"
        )
    if out["tracked_without_examination"]:
        parts.append(
            f"{out['tracked_without_examination']} tracked unit(s) with no "
            "examination on record"
        )
    if out["category_untracked"]:
        parts.append(
            f"{out['category_untracked']} row(s) in a category the policy "
            "does not track"
        )
    if out["holder_unresolved"]:
        parts.append(
            f"{out['holder_unresolved']} whose holder the workforce does not "
            "contain"
        )
    if out["supervisor_unresolved"]:
        parts.append(
            f"{out['supervisor_unresolved']} with nobody to notify on the org "
            "chart"
        )
    if out["unreadable_dates"]:
        parts.append(f"{out['unreadable_dates']} carrying no readable date")
    if out["deferred"]:
        parts.append(
            f"{out['deferred']} left for the next window (bound "
            f"{policy.window_limit})"
        )
    return " · ".join(parts) if out["considered"] else (
        "nothing tracked in the book this window"
    )


def _rule_summary(out: dict) -> str:
    counts = out["counts"]
    stages = ", ".join(
        f"{count} at {stage}"
        for stage, count in sorted(
            counts["by_stage"].items(), key=lambda kv: -int(kv[0])
        ) if count
    )
    parts = [
        f"{counts['due']} notice(s) due"
        + (f" — {stages}" if stages else "")
    ]
    if counts["already_sent"]:
        parts.append(
            f"{counts['already_sent']} row(s) skipped: the stage they are at "
            "was already sent this cycle"
        )
    if counts["not_yet_due"]:
        parts.append(f"{counts['not_yet_due']} not yet at a stage")
    if counts["unreadable"]:
        parts.append(
            f"{counts['unreadable']} with no date to measure from"
        )
    return " · ".join(parts) if counts["due"] or len(parts) > 1 else (
        "nothing due this window"
    )
