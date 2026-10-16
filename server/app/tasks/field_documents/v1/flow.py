"""Field documents, v1 — the shape this flow has run since it landed.

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
    DOCUMENTS_EVENT,
    EXCLUSIONS,
    GAP_SCREEN,
    JOB_EVENT,
    LINK_ACTION,
    PACKETS_EVENT,
    POLICY_ASSET_ID,
    PRODUCTION_RECORDS,
    FieldDocumentPolicy,
    JobPacket,
    SelectedJob,
    link_packets,
    screen_packets,
)

VERSION = 1
LABEL = "v1"
NOTES = (
    "The whole of the field-documents design, as one windowed pass: a "
    "dispatcher that watched the portal for scheduled jobs, a performer that "
    "copied four PDFs into a per-foreman folder tree, and a weekly "
    "reconciliation that went looking for the jobs that had moved between "
    "crews, collapsed into one standing question asked of the graph — *which "
    "scheduled job inside the horizon does not have its four documents linked "
    "at their latest revision?* **No ModelCall at all**: the fact the "
    "design's crew-schedule PDF carried is a column on the job. "
    "`field-documents-policy` says what a packet is — the four types and "
    "their filename patterns, the folder root, the statuses and the horizon, "
    "the window bound, the naming convention with its stopwords and its "
    "collision rule, the recurrence threshold and the desk — and an edit to "
    "that page changes what this flow does with no deploy. "
    "\n\n"
    "**Three of the design's steps are deliberately not steps here**, said "
    "out loud because a reader comparing this Flow to the design needs to see "
    "they were removed rather than forgotten. *Once the job moves to IN "
    "PROGRESS, stop checking for new versions* — a job whose status is out of "
    "the governed scope is not selected in `x2` at all, so there is nothing "
    "to switch off. *Delete the job folder when the job leaves the foreman* — "
    "the withdrawn `assigned_to` edge is `job-sync`'s observation, landed "
    "when the production system said a different crew has it, and there is no "
    "destructive act left to gate. *Move the pictures folder to Archive* — "
    "the same. "
    "\n\n"
    "**No folder is created, copied, moved or deleted anywhere in this "
    "flow.** A packet is a graph fact — `Job —documented_by→ Document` at the "
    "latest revision of each governed type — and the design's per-foreman "
    "SharePoint tree, with its `Construction Details`, `JobSite Pictures` and "
    "`Archive` siblings, does not exist in this estate. Nothing is deleted "
    "either: a superseded revision keeps its row, its claims and its "
    "withdrawn edge, because a crew that already printed it should be able to "
    "find out what they are holding."
)

STEPS: list[FlowStep] = [
    FlowStep(id="t1", type="trigger", name=f"{JOB_EVENT} | {DOCUMENTS_EVENT}",
             summary="one run per delivery of either half, because either half can be the new fact: a job becoming `Scheduled`, or a revision arriving for a job already scheduled. `job-sync` and `dms-sync` are declared ahead of it on the same two events, so the rows this reads are in the graph before it reads them",
             config={"events": f"{JOB_EVENT}, {DOCUMENTS_EVENT}",
                     "wakes": "window"},
             next=["x1"]),
    FlowStep(id="x1", type="transform", name="read-field-policy",
             summary="the whole read-set in one step, at one revision: the four document types with their order and filename patterns, the folder root and the rule that decides which revision is current, the statuses and horizon that put a job in scope, the window bound, the naming convention with its stopword list and collision rule, the recurrence threshold and the approval table — and it says out loud when the corpus holds no such document and the built-in default is standing in",
             config={"source": f"knowledge:{POLICY_ASSET_ID}"}, next=["x2"]),
    FlowStep(id="x2", type="transform", name="select-scheduled-jobs",
             summary="the standing question, asked of the graph: `Job` rows whose status is in the governed scope and whose load date is inside the horizon, each already joined to the crew its live `assigned_to` edge names and to the crews that held it before — plus, kept separately because they are a different answer, the ones whose `crew_id` resolves to no `Team`. Every exclusion is named and counted rather than summed. A backlog past the window bound is the next window's work, reported not dropped",
             config={"source": "graph:Job,Team,Company",
                     "history": "runs:field-documents",
                     "excludes": ", ".join(EXCLUSIONS)},
             next=["x3"]),
    FlowStep(id="x3", type="transform", name="resolve-document-packet",
             summary="per job: walks `Job Documents/<job number>` to the `Document` rows `dms-sync` landed and takes the **latest revision of each of the four governed types**. The design's *topmost is most recent* is not a rule this system can keep — a document system sorted by name says nothing about age — so it is a comparison rather than a position: highest revision wins, and where two share one, the more recently created does, compared at full timestamp precision. The packet label is derived here too, over the whole window at once, because the collision rule is a statement about the window",
             config={"source": "graph:Folder,Document",
                     "convention": f"knowledge:{POLICY_ASSET_ID}"},
             next=["a1"]),
    FlowStep(id="a1", type="action", name="link-job-documents",
             summary="**one** guarded write for the whole window, not one per job: `documented_by` drawn to each latest revision, the previous revision's edge withdrawn and its `Document` marked superseded, `job_folder` drawn, and `packet_label` and `documents_complete` claimed on the job. Regime `decision` and no queue — the document system reported four files, and *which* of them is a job's current packet is the product of a rule over a governed policy, which Cumulus determined and must answer for; linking a document nobody disputed is nobody's work, which is `record-document-read`'s reasoning",
             config={"target": PRODUCTION_RECORDS, "gateway": "action-gateway",
                     "action": LINK_ACTION, "regime": "decision"},
             next=["c1"]),
    FlowStep(id="c1", type="check", name="screen-packets",
             summary="pure: which of the four types each job is missing, how many consecutive windows it has had none of them at all, and which jobs carry a crew id that resolves to no team. A **partial** packet is informational at every window and never raises a task on its own — plans and a site map but no crew hours sheet is a normal Tuesday — and an **empty** one is counted until the governed threshold, which is the design's own *log as information and continue; notify if recurring*",
             config={"rule_type": "precedence-list",
                     "source": f"knowledge:{POLICY_ASSET_ID}"},
             next=["k1"]),
    FlowStep(id="k1", type="task", name="report-document-gaps",
             summary="**at most two aggregate Tasks per window** — never one per job — both routed through the governed approval table: (i) the jobs with nothing filed at all past the recurrence threshold, and (ii) the jobs whose crew id resolves to no team, which is the design's SUBCONTRACTOR case as **one identity question** rather than N document questions. A job in the second is deliberately absent from the first: no document question was asked about it. Each Task's id is derived from the finding rather than from the window, so the same finding tomorrow is standing rather than re-raised",
             config={"queue": "field-operations", "aggregate": "one per class",
                     "screen": GAP_SCREEN},
             next=["e1"]),
    FlowStep(id="e1", type="event", name=PACKETS_EVENT,
             summary="the pass's own fact, with the counts. Nothing binds it: there is no per-case sibling to raise, because a packet is not a case — the job and the desk are where the work is",
             config={"event": PACKETS_EVENT}),
]

#: One screen for this flow's one kind of Task. `by_kind` rather than
#: `otherwise` so a kind a later version raises gets the generic pane instead
#: of a screen built for a different question.
UI = TaskUi(by_kind={
    "exception": TaskScreen(
        screen_id=GAP_SCREEN,
        label="Open the packet gap review",
        hint=(
            "Opens the jobs of one finding — each with its crew, its load "
            "date, the folder the governed root points at and how many "
            "consecutive windows have found nothing in it. Nothing on this "
            "screen files a document or creates a crew: both answers are "
            "facts somebody supplies, and the next window reads them."
        ),
    ),
})


def build_body(service):
    """Bind the field documents service and return the runnable body."""

    def body(ctx: RunContext, inputs: dict) -> dict:
        source = str(inputs.get("source") or "")
        today = _as_of(inputs)
        woke, carried = _woken_by(inputs)

        ctx.event(
            "t1", f"{JOB_EVENT} | {DOCUMENTS_EVENT}",
            f"{woke} from `{source}`" + (f" · {carried}" if carried else "")
            + f" · as of {today.isoformat()}",
            kind="trigger",
        )

        # -- x1: the governed read that decides what a packet is -------------
        policy = FieldDocumentPolicy.model_validate(ctx.step(
            "x1", "read-field-policy", "transform",
            fn=lambda: service.read_policy().model_dump(),
            summary=lambda out: _policy_summary(out),
        ))
        ctx.note_refs(policy.source_ref)

        # -- x2: the standing question, asked of the graph -------------------
        selection = ctx.step(
            "x2", "select-scheduled-jobs", "transform",
            fn=lambda: service.select(policy, today, ctx.record.id),
            summary=lambda out: _selection_summary(out, policy),
            refs=[policy.source_ref],
        )
        jobs = [SelectedJob.model_validate(j) for j in selection["jobs"]]
        crewless = [
            SelectedJob.model_validate(j) for j in selection["crewless"]
        ]

        # -- x3: the four revisions, and the name a crew calls the job -------
        resolved = ctx.step(
            "x3", "resolve-document-packet", "transform",
            fn=lambda: service.resolve_packets(jobs, policy),
            summary=lambda out: _packet_summary(out, policy),
            refs=[policy.source_ref],
        )
        packets = [JobPacket.model_validate(p) for p in resolved["packets"]]

        # -- a1: one Action for the window -----------------------------------
        act = service._act(ctx, "a1")
        written = ctx.step(
            "a1", "link-job-documents", "action",
            fn=lambda: act.commit(lambda w: link_packets(
                w, packets, policy, ctx.record.id,
            )),
            writes=lambda _out: act.writes,
            summary=lambda out: (
                f"{out['jobs']} packet(s) landed — {out['linked']} "
                f"document(s) linked at their latest revision, "
                f"{out['superseded']} "
                f"superseded ({out['withdrawn']} edge(s) withdrawn) · "
                f"{out['labels']} label(s) claimed · {out['complete']} "
                f"complete, {out['incomplete']} short of the governed set"
                if out["jobs"] else
                "no job in scope this window, so nothing was written"
            ),
        )

        # -- c1: pure -------------------------------------------------------
        screened = ctx.step(
            "c1", "screen-packets", "check",
            fn=lambda: screen_packets(
                packets, crewless, selection["prior_empty_windows"], policy,
            ),
            summary=lambda out: _screen_summary(out, policy),
        )

        # -- k1: the aggregates, when there are any --------------------------
        asked = ctx.step(
            "k1", "report-document-gaps", "sync",
            fn=lambda: service.raise_gap_tasks(ctx, screened, policy),
            summary=lambda out: (
                f"{out['gap_tasks']} aggregate Task(s) raised"
                + (
                    f" · {out['gap_tasks_standing']} already standing for the "
                    "same finding and not re-raised"
                    if out["gap_tasks_standing"] else ""
                )
                if out["gap_tasks"] or out["gap_tasks_standing"]
                else "nothing to report, so nobody to ask"
            ),
        )

        # -- e1: the pass's own fact -----------------------------------------
        ctx.event(
            "e1", PACKETS_EVENT,
            f"{written['jobs']} packet(s) linked — {written['linked']} "
            f"document(s) at their latest revision, {written['superseded']} "
            f"superseded · {screened['counts']['complete']} complete, "
            f"{screened['counts']['partial']} partial, "
            f"{screened['counts']['empty']} with nothing filed · "
            f"{asked['gap_tasks']} aggregate Task(s)",
        )

        return {
            "source": source, "kind": "field-documents",
            "woken_by": woke,
            "as_of": today.isoformat(),
            "policy_ref": policy.source_ref,
            "found_asset": policy.found_asset,
            "document_types": policy.type_names,
            "horizon_days": policy.horizon_days,
            "window_limit": policy.window_limit,
            "considered": selection["considered"],
            "in_horizon": selection["in_horizon"],
            "taken": selection["taken"],
            "deferred": selection["deferred"],
            "exclusions": selection["exclusions"],
            "crew_unstated": selection["crew_unstated"],
            "reassigned": selection["reassigned"],
            "folders_found": resolved["folders_found"],
            "folders_missing": resolved["folders_missing"],
            "documents_seen": resolved["documents"],
            "unclassified": resolved["unclassified"],
            "collisions": resolved["collisions"],
            "stopwords_applied": resolved["stopwords_applied"],
            **written,
            "screened": screened["counts"],
            "threshold": screened["threshold"],
            **asked,
            # The two lists the *next* window's recurrence count is derived
            # from. This flow keeps no counter field on `Job` — the design's
            # `Last Run Status` cell is exactly the thing that should not come
            # back — so the record of what a pass found is the run's own
            # output, and "consecutive" is read off the sequence of them.
            "answered_jobs": [p.job_natural_key for p in packets],
            "empty_jobs": [
                row["job_natural_key"] for row in screened["empty"]
            ],
            # What the pass decided, on the run, for `identity_sweep`'s
            # reason: a windowed flow's output is the only place its per-job
            # reasoning is legible without re-querying the graph. Capped,
            # because a journal is a document with a size limit.
            "labels": [
                {
                    "job": label["job_number"], "label": label["label"],
                    "customer": label["customer"], "why": label["rule"],
                }
                for label in resolved["labels"]
            ][:20],
            "packets": [
                {
                    "job": p.job_number, "label": p.label,
                    "crew": p.crew_label,
                    "held": [d.document_type for d in p.current],
                    "missing": p.missing,
                    "superseded": [d.filename for d in p.superseded],
                }
                for p in packets
            ][:20],
        }

    return body


def _as_of(inputs: dict) -> date:
    """The date the window is answered as of — the caller's, or today's.

    On the payload rather than read off the clock inside the selection, for
    two reasons that happen to agree: a delivery that arrived at 23:59 and is
    answered at 00:01 must be answered *as of the delivery*, and a graded case
    has to be able to say what day it is without moving the machine's clock.
    """
    given = str(inputs.get("as_of") or "").strip()
    if given:
        try:
            return date.fromisoformat(given[:10])
        except ValueError:
            # Named here rather than counted: an unreadable window date is the
            # caller's mistake, and answering it as today is more honest than
            # failing a whole pass over a string.
            pass
    return utcnow().date()


def _woken_by(inputs: dict) -> tuple[str, str]:
    """Which of the two facts woke this run, and what it carried.

    Read off the payload's shape rather than off `RunRecord.caused_by`, which
    holds the Event's *id* and not its name. Both deliveries carry `source`
    and `watermark`; only the production one carries a `records` list, and
    only the document one carries `folders`/`documents`. A run somebody
    started directly carries neither, and says so.

    On the presence of the key rather than on its contents: an empty delivery
    is still a delivery — a feed whose window turned up nothing new is exactly
    the case this flow answers out of the graph rather than out of the
    payload — and reading it as a direct start would have made the journal
    lie about what woke it.
    """
    if "documents" in inputs or "folders" in inputs:
        return DOCUMENTS_EVENT, (
            f"{len(inputs.get('documents') or [])} document(s), "
            f"{len(inputs.get('folders') or [])} folder(s)"
        )
    if "records" in inputs:
        return JOB_EVENT, f"{len(inputs.get('records') or [])} job record(s)"
    return "a direct start", ""


def _policy_summary(out: dict) -> str:
    types = ", ".join(
        f"`{spec['name']}`" for spec in sorted(
            out["document_types"], key=lambda s: s["order"]
        )
    ) or "none"
    parts = [
        f"{out['source_ref']}: {types}",
        f"under `{out['folder_root']}/{out['job_folder_name']}`",
        f"current revision by {out['latest_by']}, ties on {out['tiebreak']}",
        f"{', '.join(out['statuses']) or 'no status'} inside "
        f"{out['horizon_days']} day(s)",
        f"up to {out['window_limit']} job(s) per window",
        f"label `{out['label_pattern']}`, stopwords "
        f"{', '.join(out['stopwords']) or 'none'}, "
        f"on collision {out['collision_rule']}",
        f"a task after {out['missing_windows']} empty window(s); a partial "
        f"packet is {out['partial_packet']}",
    ]
    if not out["found_asset"]:
        parts.append("not in the corpus, running on the built-in default")
    return " · ".join(parts)


def _selection_summary(out: dict, policy: FieldDocumentPolicy) -> str:
    if not out["considered"]:
        return "no job in the graph this window"
    parts = [
        f"{out['in_horizon']} job(s) `"
        f"{'`/`'.join(policy.statuses)}` inside the "
        f"{policy.horizon_days}-day horizon of {out['considered']} considered",
        f"{out['taken']} taken this window",
    ]
    named = {
        name: count for name, count in out["exclusions"].items() if count
    }
    if named:
        parts.append(
            "excluded: " + ", ".join(
                f"{count} {name}" for name, count in named.items()
            )
        )
    if out["crewless"]:
        parts.append(
            f"{len(out['crewless'])} whose crew id resolves to no team — an "
            "identity question, and no document question is asked about them"
        )
    if out["crew_unstated"]:
        parts.append(
            f"{out['crew_unstated']} with no crew named at all, which is the "
            "source saying nothing rather than naming nobody"
        )
    if out["reassigned"]:
        parts.append(
            f"{out['reassigned']} that have moved crews — the former edge is "
            "withdrawn and still on the record"
        )
    if out["deferred"]:
        parts.append(
            f"{out['deferred']} left for the next window (bound "
            f"{policy.window_limit})"
        )
    return " · ".join(parts)


def _packet_summary(out: dict, policy: FieldDocumentPolicy) -> str:
    if not out["folders_found"] and not out["folders_missing"]:
        return "no job to resolve a packet for"
    parts = [
        f"{out['folders_found']} job folder(s) walked under "
        f"`{policy.folder_root}`",
        f"{out['linked']} document(s) at their latest revision",
        f"{out['complete']} complete packet(s)",
    ]
    if out["superseded"]:
        parts.append(
            f"{out['superseded']} earlier revision(s) to supersede"
        )
    if out["already_superseded"]:
        parts.append(
            f"{out['already_superseded']} already superseded by an earlier "
            "window"
        )
    if out["folders_missing"]:
        parts.append(
            f"{out['folders_missing']} job(s) with no folder in the document "
            "tree at all"
        )
    if out["unclassified"]:
        parts.append(
            f"{out['unclassified']} file(s) matching none of the governed "
            "patterns, left where they are and counted"
        )
    if out["stopwords_applied"]:
        parts.append(
            f"{out['stopwords_applied']} label(s) whose leading word was a "
            "governed stopword"
        )
    if out["collisions"]:
        parts.append(
            f"{out['collisions']} label(s) that collided, so the next keyword "
            "was appended"
        )
    return " · ".join(parts)


def _screen_summary(out: dict, policy: FieldDocumentPolicy) -> str:
    counts = out["counts"]
    if not counts["answered"] and not counts["crewless"]:
        return "nothing to screen this window"
    parts = [
        f"{counts['complete']} complete of {counts['answered']} answered",
    ]
    if counts["partial"]:
        parts.append(
            f"{counts['partial']} partial — {policy.partial_packet} at every "
            "window and never a task on its own"
        )
    if counts["empty"]:
        parts.append(
            f"{counts['empty']} with nothing filed, "
            f"{counts['past_threshold']} of them past the "
            f"{policy.missing_windows}-window threshold"
        )
    if counts["crewless"]:
        parts.append(
            f"{counts['crewless']} job(s) whose crew nobody can be resolved to"
        )
    return " · ".join(parts)
