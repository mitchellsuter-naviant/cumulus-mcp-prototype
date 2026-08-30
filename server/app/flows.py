"""Flows: the registry and observability plane for authored logic.

Doctrine grounding (DOCTRINE.md, Lens 2 and the Governance spine):

  * There is deliberately **no "Agent" primitive** — the Flow *is* the agent.
  * There is one kind of authored logic: a **Flow**. The doctrine's original
    Workflow/DataFlow split has been retired — it was right about the physics
    (per-case work and windowed bulk work behave differently) and wrong about
    the level, putting a behavioural difference in the type system where it
    produced arguments about which noun a definition was. What actually
    differs is derived from a definition's own shape: how it is triggered and
    where it writes. See `is_windowed` and `writes_canonical`.
  * A **ModelCall** is a governed operation, not a primitive: every LLM
    invocation records which model ran, its tokens, and its cost. All spend
    numbers here roll up from ModelCall records — cost is an audit product,
    never a separate meter.
  * The definition shown to the customer is the **Tier-2 declarative export**:
    typed steps with plain-language summaries. Transparent, never opaque; it
    need not be runnable (the transparency bar, Lens 1).

What a flow *promises* and whether it keeps that promise are not here. Evals
live in `evals.py`, which executes the flow in an isolated world and grades
what it wrote; this module owns the definition, its version history, and the
insights derived from telemetry.

Every number this module reports is read off runs that happened. There used
to be a seed profile per definition that manufactured a plausible month of
traffic — thousands of runs, hundreds of dollars, a 99.5% success rate — and
the console rendered it beside a dollar ROI figure with no marking. A
synthesized count is tolerable scaffolding; a synthesized finance figure is
not, and the two were indistinguishable on the page. So the synthesis is
gone: the rollups here aggregate `RunRecord`s from `runs.py`, and where the
journals record nothing the answer is zero.

Zero has two readings, and they are opposite facts. No model was called, or
models were called and nobody wrote down the cost. `cost_basis` is what keeps
those apart, because filling the tile with a plausible number rather than
admitting the hole is exactly the failure being removed here.
"""

from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timedelta
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from . import dispatch
from .models import utcnow
from .runs import RunRecord

log = logging.getLogger("cumulus.flows")

FlowStatus = Literal["draft", "active", "paused", "deprecated"]
# The step vocabulary of the declarative export. `transform` is authored
# non-model logic (parse, reshape, entity-match) carried as a faithful
# plain-language description — implementation stays machinery.
# The declared vocabulary is the executed one: `StepKind` in runs.py is the
# same list, so a step a run journals can always be found on the definition.
StepType = Literal[
    "trigger", "modelcall", "transform", "rule", "task", "action", "event",
    "sync", "check", "resolve",
]
VersionStatus = Literal["active", "archived", "draft"]
# What changed about a step between two versions, for the canvas diff overlay.
ChangeKind = Literal["unchanged", "added", "removed", "modified"]


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class FlowStep(BaseModel):
    """One typed step of the declarative export.

    `summary` is the transparency bar in miniature: a plain-language statement
    of what the step does, present even when the implementation is reserved.
    `config` carries the structured-where-cheap half (model ids, thresholds,
    queues) as display strings.
    """

    id: str
    type: StepType
    name: str
    summary: str = ""
    config: dict[str, str] = Field(default_factory=dict)
    # Ids of the steps this one can hand off to. The first entry is the main
    # path; extras are branches, labelled via `branch_labels` in config-free
    # form: parallel list, same order as `next`.
    next: list[str] = Field(default_factory=list)
    branch_labels: list[str] = Field(default_factory=list)


class FlowVersion(BaseModel):
    """One published or drafted revision of a definition.

    A version is a full snapshot of the declarative export, not a delta: the
    map has to stay readable on its own, and a chain of deltas is exactly the
    kind of opaque artifact the transparency bar rules out. Snapshots are
    small (a handful of typed steps), so the cost is nothing.
    """

    version: int
    label: str = ""
    status: VersionStatus = "archived"
    steps: list[FlowStep] = Field(default_factory=list)
    model_budget_usd_per_run: float = 0.50
    notes: str = ""
    authored_by: str = ""
    created_at: datetime = Field(default_factory=utcnow)

    def display(self) -> str:
        return self.label or f"v{self.version}"


class StepDiff(BaseModel):
    """One step of a version, annotated against the version compared with."""

    step: FlowStep
    change: ChangeKind = "unchanged"
    changed_fields: list[str] = Field(default_factory=list)


class FlowDefinition(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9\-]*$")
    name: str = ""
    description: str = ""
    team: str = ""
    status: FlowStatus = "draft"
    version: int = 1
    tags: list[str] = Field(default_factory=list)
    steps: list[FlowStep] = Field(default_factory=list)
    # Where human judgment lands, and where the work is watched from.
    queues: list[str] = Field(default_factory=list)
    workspace: str = ""
    # Governed knowledge, not code: editable in the shell, versioned, ledgered.
    roi_minutes_per_run: float = 0.0
    roi_hourly_rate_usd: float = 30.0
    model_budget_usd_per_run: float = 0.50
    authored_by: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    # Published history plus at most one open draft. `steps` above stays the
    # active version's steps, so every existing reader keeps working.
    versions: list[FlowVersion] = Field(default_factory=list)

    def version_history(self) -> list[FlowVersion]:
        """Newest first, with the active version synthesized if absent."""
        if self.versions:
            return sorted(self.versions, key=lambda v: v.version, reverse=True)
        return [
            FlowVersion(
                version=self.version,
                label=f"v{self.version}",
                status="active",
                steps=self.steps,
                model_budget_usd_per_run=self.model_budget_usd_per_run,
                authored_by=self.authored_by,
                created_at=self.updated_at,
            )
        ]

    def find_version(self, version: int) -> FlowVersion | None:
        return next(
            (v for v in self.version_history() if v.version == version), None
        )

    def active_version(self) -> FlowVersion:
        history = self.version_history()
        return next(
            (v for v in history if v.status == "active"),
            next((v for v in history if v.status != "draft"), history[0]),
        )

    def draft_version(self) -> FlowVersion | None:
        return next(
            (v for v in self.version_history() if v.status == "draft"), None
        )


class Recommendation(BaseModel):
    """One cost or performance insight derived from execution telemetry.

    Every card carries its evidence and the exact change it proposes, because
    a recommendation you cannot audit is not usable for governance work — and
    applying one edits governed knowledge, which is a ledgered act.
    """

    id: str
    # `evidence` is not an insight — it is the recommender stating what it had
    # to work with. It is always emitted, so a thin-evidence recommender says
    # so rather than returning an empty list that reads as "nothing to fix".
    kind: Literal[
        "evidence", "model-downgrade", "prompt-cache", "budget", "coverage",
        "batching",
    ]
    title: str
    detail: str
    evidence: str = ""
    step_id: str = ""
    step_name: str = ""
    estimated_monthly_savings_usd: float = 0.0
    confidence: float = 0.0
    # None when the insight is advisory (e.g. "add eval cases") rather than
    # a mechanical edit the console can stage into a draft.
    proposed_change: dict[str, str] | None = None


# ---- rollup shape (a projection of the run journals, never stored) --------


class UsageDay(BaseModel):
    """One day of a flow's real execution history.

    `priced_model_calls` is the field that stops `cost_usd` lying. A day with
    12 model calls and $0.00 recorded is not a cheap day; it is a day nobody
    priced, and the two have to be distinguishable downstream.
    """

    date: date
    runs: int = 0
    completed: int = 0
    tasks_raised: int = 0
    escalated: int = 0
    failed: int = 0
    cost_usd: float = 0.0
    tokens: int = 0
    model_calls: int = 0
    priced_model_calls: int = 0
    minutes_saved: float = 0.0


# ---------------------------------------------------------------------------
# Directory (stored state: the definitions themselves)
# ---------------------------------------------------------------------------


class FlowExistsError(Exception):
    pass


class FlowNotFoundError(Exception):
    pass


class FlowDirectory(Protocol):
    def list(self) -> list[FlowDefinition]: ...
    def get(self, flow_id: str) -> FlowDefinition | None: ...
    def create(self, defn: FlowDefinition) -> FlowDefinition: ...
    def update(self, defn: FlowDefinition) -> FlowDefinition: ...
    def delete(self, flow_id: str) -> None: ...


class InMemoryFlowDirectory:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, FlowDefinition] = {}

    def list(self) -> list[FlowDefinition]:
        with self._lock:
            return sorted(self._items.values(), key=lambda w: w.id)

    def get(self, flow_id: str) -> FlowDefinition | None:
        with self._lock:
            return self._items.get(flow_id)

    def create(self, defn: FlowDefinition) -> FlowDefinition:
        with self._lock:
            if defn.id in self._items:
                raise FlowExistsError(defn.id)
            self._items[defn.id] = defn
            return defn

    def update(self, defn: FlowDefinition) -> FlowDefinition:
        with self._lock:
            if defn.id not in self._items:
                raise FlowNotFoundError(defn.id)
            self._items[defn.id] = defn
            return defn

    def delete(self, flow_id: str) -> None:
        with self._lock:
            if flow_id not in self._items:
                raise FlowNotFoundError(flow_id)
            del self._items[flow_id]


class FirestoreFlowDirectory:
    def __init__(self, project: str | None, collection: str) -> None:
        from google.cloud import firestore  # deferred: not needed for local dev

        self._client = firestore.Client(project=project)
        self._col = self._client.collection(collection)

    def list(self) -> list[FlowDefinition]:
        out = []
        for doc in self._col.stream():
            try:
                out.append(FlowDefinition.model_validate(doc.to_dict()))
            except Exception:
                log.exception("Skipping unparseable flow", extra={"docId": doc.id})
        return sorted(out, key=lambda w: w.id)

    def get(self, flow_id: str) -> FlowDefinition | None:
        snap = self._col.document(flow_id).get()
        if not snap.exists:
            return None
        try:
            return FlowDefinition.model_validate(snap.to_dict())
        except Exception:
            log.exception("Unparseable flow", extra={"flowId": flow_id})
            return None

    def create(self, defn: FlowDefinition) -> FlowDefinition:
        ref = self._col.document(defn.id)
        if ref.get().exists:
            raise FlowExistsError(defn.id)
        ref.set(defn.model_dump(mode="json"))
        return defn

    def update(self, defn: FlowDefinition) -> FlowDefinition:
        ref = self._col.document(defn.id)
        if not ref.get().exists:
            raise FlowNotFoundError(defn.id)
        ref.set(defn.model_dump(mode="json"))
        return defn

    def delete(self, flow_id: str) -> None:
        ref = self._col.document(flow_id)
        if not ref.get().exists:
            raise FlowNotFoundError(flow_id)
        ref.delete()


# ---------------------------------------------------------------------------
# Shape signals, and telemetry read off the runs
# ---------------------------------------------------------------------------


def is_windowed(defn: "FlowDefinition") -> bool:
    """True when the flow is woken by a window rather than by a single case.

    This is the mechanical replacement for asking "is it a DataFlow?": a flow
    woken by a window is processing a batch, whatever anyone named it, and a
    flow woken by a business event is processing one case.

    Read off the **declared Trigger** — the record `dispatch.py` evaluates to
    start the run — rather than off the definition's exported trigger step.
    The export is a display of that binding and the binding is what actually
    happens; when they disagree the binding is right. A definition nothing
    binds falls back to its own export, because a draft authored ahead of its
    wiring has nothing else to be read off.
    """
    declared = dispatch.triggers_for_flow(defn.id)
    if declared:
        return any(t.wakes == "window" for t in declared)
    trigger = next((s for s in defn.steps if s.type == "trigger"), None)
    if trigger is None:
        return False
    return trigger.config.get("event", "").startswith("timer:")


def writes_canonical(defn: "FlowDefinition") -> bool:
    """True when the flow commits into the canonical graph.

    Where competing claims meet, a Resolution picks a winner and records why —
    so this, not a type, is what says a flow's audit trail will contain them.
    """
    return any(
        s.type == "action" and s.config.get("target") == "graph"
        for s in defn.steps
    )


def _run_day(run: RunRecord) -> date | None:
    """The day a run started, or None when the stamp is unreadable."""
    try:
        return date.fromisoformat(run.started_at_iso[:10])
    except ValueError:
        return None


def usage_series(
    runs: list[RunRecord], days: int, roi_minutes_per_run: float = 0.0
) -> list[UsageDay]:
    """Daily rollup of real runs over the last `days`, oldest first.

    A day with no runs is a zero row — not a gap, and not a guess. Asked for
    thirty days of a flow that ran nine times this morning, the honest answer
    is twenty-nine zeroes and one real day, and the shape of that series is
    itself the information the old synthesized curve was hiding.

    Runs outside the window are dropped rather than folded into the edge day,
    so the series always sums to the headline over the same window.
    """
    today = utcnow().date()
    days_back = [today - timedelta(days=n) for n in range(days - 1, -1, -1)]
    by_day = {day: UsageDay(date=day) for day in days_back}

    for run in runs:
        row = by_day.get(_run_day(run))
        if row is None:
            continue
        row.runs += 1
        if run.status == "succeeded":
            row.completed += 1
            row.minutes_saved = round(row.minutes_saved + roi_minutes_per_run, 1)
        elif run.status == "failed":
            row.failed += 1
        # Every step carrying a task_id put work in front of a person: a gate
        # raising one, or a step that exhausted its retries and escalated.
        # The second is counted again as an escalation, because "a human
        # decided this" and "this broke and a human had to catch it" are not
        # the same event and a queue depth made of both tells you neither.
        row.tasks_raised += sum(1 for step in run.steps if step.task_id)
        if any(step.status == "failed" for step in run.steps):
            row.escalated += 1
        for step in run.steps:
            if step.kind != "modelcall" or step.status != "done":
                continue
            row.model_calls += 1
            row.cost_usd = round(row.cost_usd + step.cost_usd, 6)
            row.tokens += step.tokens
            if step.cost_usd:
                row.priced_model_calls += 1

    return [by_day[day] for day in days_back]


CostBasis = Literal["measured", "partial", "unrecorded", "no-model-calls"]


def cost_basis(model_calls: int, priced_model_calls: int) -> CostBasis:
    """Why the spend figure is the number it is.

    A $0.00 spend tile has two readings that point in opposite directions:
    nothing called a model, or models were called and the call sites never
    passed a cost. Only the first is a fact about spending. Without this
    field a console renders a confident zero over a hole — the same class of
    dishonesty as a confident invented number, and the one that survives
    longer because it looks like good news.
    """
    if model_calls == 0:
        return "no-model-calls"
    if priced_model_calls == 0:
        return "unrecorded"
    return "measured" if priced_model_calls == model_calls else "partial"


class RoiBasis(BaseModel):
    """How `hours_returned` and `roi_usd` were arrived at.

    Nobody timed a clerk against this flow. The minutes are a governed
    assumption on the definition — editable in the console, ledgered on
    change — and the dollars are that assumption times a rate; what *is* real
    is the run count they multiply, which is the only reason the figures
    survived this pass at all. So the derivation travels with the figure: a
    reader shown a dollar number who cannot tell whether anyone measured it
    will assume somebody did.
    """

    completed_runs: int = 0
    # Of those, the ones a rate was actually applied to. The five inbound
    # syncs have no FlowDefinition and so no ROI assumption; folding their
    # runs into the per-run figure would dilute an assumption with runs it
    # was never made about.
    modelled_runs: int = 0
    assumed_minutes_per_run: float = 0.0
    hourly_rate_usd: float = 0.0
    statement: str = ""


def roi_basis(
    completed_runs: int,
    minutes: float,
    hourly_rate_usd: float,
    runs_without_assumption: int = 0,
) -> RoiBasis:
    modelled = max(0, completed_runs - runs_without_assumption)
    per_run = round(minutes / modelled, 2) if modelled else 0.0
    if not minutes:
        statement = (
            f"no modelled time returned: none of {completed_runs} completed "
            "run(s) carries an ROI assumption"
        )
    else:
        statement = (
            f"{modelled} of {completed_runs} completed run(s) x {per_run:g} "
            f"assumed min/run x ${hourly_rate_usd:,.2f}/h - an assumption, "
            "not a measurement"
        )
    return RoiBasis(
        completed_runs=completed_runs,
        modelled_runs=modelled,
        assumed_minutes_per_run=per_run,
        hourly_rate_usd=round(hourly_rate_usd, 2),
        statement=statement,
    )


def flow_runs(flow_id: str) -> list[RunRecord]:
    """This flow's stored runs.

    Deferred import for the same reason `eval_case_count` defers its own:
    `deps` builds this module's service. The runs are the record; keeping a
    rollup of them here beside it is what produced the numbers this module
    just stopped inventing.
    """
    from .deps import get_run_engine

    return get_run_engine().list(flow_id=flow_id)


def fleet_flow_ids() -> list[str]:
    """Every flow the fleet covers: registered bodies, runs, and definitions.

    The first two come from `flow_projection.known_flow_ids`, which already
    answers "which flows are there" for the ontology overlay. The registry is
    unioned on top so a definition authored in the console and not yet run
    still appears — a flow with no runs reports zeroes, which is a fact about
    it, not a reason to omit it.
    """
    from .deps import get_flow_service, get_run_engine
    from .flow_projection import known_flow_ids

    engine_ids = known_flow_ids(get_run_engine())
    return sorted(set(engine_ids) | {d.id for d in get_flow_service().list()})


def fleet_runs() -> dict[str, list[RunRecord]]:
    """Every stored run grouped by flow — one read for the whole fleet."""
    from .deps import get_run_engine

    grouped: dict[str, list[RunRecord]] = {}
    for run in get_run_engine().list():
        grouped.setdefault(run.flow_id, []).append(run)
    return grouped


def open_tasks_by_flow() -> dict[str, int]:
    """Open Tasks per flow, counted in the task store.

    The synthesized backlog this replaces was a per-day random draw off the
    definition's task rate, so it moved at midnight and correlated with
    nothing a person could act on. These are the rows in the queue.
    """
    counts: dict[str, int] = {}
    from .deps import get_task_service

    for task in get_task_service().list(status="open"):
        if task.flow_id:
            counts[task.flow_id] = counts.get(task.flow_id, 0) + 1
    return counts


def open_tasks(flow_id: str) -> int:
    return open_tasks_by_flow().get(flow_id, 0)


# ---------------------------------------------------------------------------
# Version diffing
# ---------------------------------------------------------------------------

# Fields whose change is worth surfacing on the canvas. `id` is the identity,
# so it is deliberately absent: a changed id is an add plus a remove.
_DIFFABLE = ("type", "name", "summary", "config", "next", "branch_labels")


def diff_versions(
    candidate: FlowVersion, baseline: FlowVersion
) -> list[StepDiff]:
    """Annotate `candidate`'s steps against `baseline`.

    Removed steps are appended at the end carrying the *baseline* step, so the
    canvas can render a ghost node rather than silently dropping it — "what
    did this draft delete" is exactly the question a reviewer asks.
    """
    base_by_id = {s.id: s for s in baseline.steps}
    out: list[StepDiff] = []
    for step in candidate.steps:
        previous = base_by_id.get(step.id)
        if previous is None:
            out.append(StepDiff(step=step, change="added"))
            continue
        changed = [
            field
            for field in _DIFFABLE
            if getattr(step, field) != getattr(previous, field)
        ]
        out.append(
            StepDiff(
                step=step,
                change="modified" if changed else "unchanged",
                changed_fields=changed,
            )
        )
    candidate_ids = {s.id for s in candidate.steps}
    for step in baseline.steps:
        if step.id not in candidate_ids:
            out.append(StepDiff(step=step, change="removed"))
    return out


# ---------------------------------------------------------------------------
# Cost & performance recommender
# ---------------------------------------------------------------------------

_MODEL_RATES = {
    # $/M input, $/M output — display-plausible, not a price sheet.
    "gemini-3.7-flash": (0.30, 2.50),
    "gemini-3.5-flash-lite": (0.10, 0.40),
    "claude-fable-5": (5.0, 25.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

# Cheaper substitutes, in descending capability. A downgrade is only ever
# proposed one rung at a time: two rungs is a different flow, not a saving.
# Cross-vendor rungs are deliberately absent — swapping vendors changes what
# the prompt means, which is a migration to evaluate, not a cost saving to
# apply from a card.
_DOWNGRADE_PATH = {
    "gemini-3.7-flash": "gemini-3.5-flash-lite",
    "claude-opus-5": "claude-sonnet-5",
    "claude-fable-5": "claude-sonnet-5",
    "claude-sonnet-5": "claude-haiku-4-5",
}


def _blended_rate(model: str) -> float:
    rate_in, rate_out = _MODEL_RATES.get(model, (3.0, 15.0))
    return rate_in * 0.6 + rate_out * 0.4


def eval_case_count(flow_id: str) -> int:
    """How many eval cases actually grade this flow.

    Deferred import because `deps` builds this module's service: the coverage
    insight is worth the real number and not worth a second copy of it kept
    here, which is what the old synthesized golden set amounted to.
    """
    from .deps import get_eval_service

    service = get_eval_service()
    return sum(len(service.cases(spec.id)) for spec in service.specs(flow_id))


def _basis_card(
    flow_id: str,
    runs: int,
    days: int,
    model_calls: int,
    priced_calls: int,
    eval_cases: int,
    withheld: list[str],
) -> Recommendation:
    """What the recommender had to work with, stated as a card.

    A recommender whose evidence has thinned out has three ways to behave and
    only one of them is acceptable. It can keep recommending from numbers that
    are no longer there (fabricated confidence — a model downgrade priced off
    spend nobody recorded). It can return an empty list, which a console
    renders as "no issues found" and a reader takes as a clean bill of health.
    Or it can say what it looked at and what it therefore could not decide.

    So this card is emitted unconditionally, including — especially — when it
    is the only one.
    """
    basis = cost_basis(model_calls, priced_calls)
    spend_phrase = {
        "measured": f"{model_calls} model call(s), all priced",
        "partial": f"{model_calls} model call(s), {priced_calls} priced",
        "unrecorded": f"{model_calls} model call(s), none carrying a cost",
        "no-model-calls": "no model calls",
    }[basis]
    detail = (
        f"These cards are derived from {runs} stored run(s) over the last "
        f"{days} day(s) and {eval_cases} eval case(s). "
    )
    if basis == "unrecorded":
        detail += (
            "The runs record which model ran but not what it cost, so there "
            "is no measured spend to reason about. Anything priced off that "
            "spend is withheld rather than estimated. "
        )
    elif basis == "no-model-calls":
        detail += "Nothing in this window called a model, so spend is genuinely zero. "
    if withheld:
        detail += "Withheld for want of evidence: " + ", ".join(withheld) + "."
    else:
        detail += "Nothing was withheld for want of evidence."
    return Recommendation(
        id=f"{flow_id}:evidence",
        kind="evidence",
        title=(
            "Evidence is thin — most insights are withheld"
            if withheld else "What these insights are derived from"
        ),
        detail=detail,
        evidence=f"{runs:,} run(s) / {days}d · {spend_phrase} · {eval_cases} eval case(s)",
        # Not a proposal, so nothing is being claimed at a confidence. This
        # is a report on the journals, and it is exactly as good as they are.
        confidence=1.0,
    )


def recommendations(
    flow_id: str,
    defn: FlowDefinition | None,
    usage: list[UsageDay],
    eval_cases: int,
    days: int | None = None,
) -> list[Recommendation]:
    """Insight cards derived from this flow's own execution telemetry.

    Every card carries the evidence it was derived from, because an
    unauditable recommendation cannot be acted on safely — and applying one
    edits governed knowledge, which lands in the ledger.

    The gates below are all one rule wearing different clothes: a card may
    only exist when the measurement it rests on was actually taken. A model
    downgrade sized off spend nobody recorded is not a cautious estimate, it
    is a number invented to fill a card — and a card is a thing an admin
    clicks Apply on.
    """
    out: list[Recommendation] = []
    withheld: list[str] = []
    days = days if days is not None else max(1, len(usage))
    runs = sum(d.runs for d in usage)
    completed = sum(d.completed for d in usage)
    failed = sum(d.failed for d in usage)
    spend = round(sum(d.cost_usd for d in usage), 6)
    tokens = sum(d.tokens for d in usage)
    tasks = sum(d.tasks_raised for d in usage)
    model_calls = sum(d.model_calls for d in usage)
    priced_calls = sum(d.priced_model_calls for d in usage)
    basis = cost_basis(model_calls, priced_calls)
    spend_measured = basis in ("measured", "partial")
    # Runs still in flight are evidence of neither outcome, so the rate is
    # taken over the ones that finished.
    finished = completed + failed
    success = completed / finished if finished else None
    monthly = 30.0 / days

    model_steps = [s for s in defn.steps if s.type == "modelcall"] if defn else []
    per_step_spend = spend / len(model_steps) if model_steps else 0.0

    if defn is None:
        withheld.append(
            "every step-level card (this flow has no declarative export to "
            "propose a change against)"
        )

    for step in model_steps:
        model = step.config.get("model", "gemini-3.5-flash-lite")
        cheaper = _DOWNGRADE_PATH.get(model)
        if not cheaper:
            continue
        # Three separate reasons to withhold, and only the last is about the
        # flow being unhealthy. The first two are about *us*: proposing to
        # spend less when nothing measured the spending, or when the sample
        # is a handful of runs, is a guess wearing a dollar sign.
        if not spend_measured:
            withheld.append(f"model downgrade on {step.name} (no measured spend)")
            continue
        if success is None:
            withheld.append(f"model downgrade on {step.name} (no finished runs)")
            continue
        if success < 0.97:
            withheld.append(
                f"model downgrade on {step.name} "
                f"(success {success:.0%} is below the bar to spend less)"
            )
            continue
        # Not withheld for want of evidence — the evidence is here and says
        # there is too little at stake to be worth a card.
        if per_step_spend < 5:
            continue
        ratio = _blended_rate(cheaper) / _blended_rate(model)
        savings = per_step_spend * (1 - ratio) * monthly
        if savings < 1:
            continue
        out.append(
            Recommendation(
                id=f"{flow_id}:downgrade:{step.id}",
                kind="model-downgrade",
                title=f"Downgrade opportunity: {step.name}",
                detail=(
                    f"{step.name} runs on {model}. This flow is completing "
                    f"{success:.1%} of finished runs, which leaves headroom to try "
                    f"{cheaper} against this flow's eval spec before promoting it."
                ),
                evidence=(
                    f"{runs:,} runs / {days}d · ${per_step_spend:,.2f} attributed "
                    f"to this step · success {success:.1%} of {finished} finished"
                ),
                step_id=step.id,
                step_name=step.name,
                estimated_monthly_savings_usd=round(savings, 2),
                confidence=round(min(0.95, success), 2),
                proposed_change={
                    "step_id": step.id,
                    "field": "model",
                    "from": model,
                    "to": cheaper,
                },
            )
        )

    # Prompt caching: a stable system prompt re-sent on every run is the
    # cheapest win available, and it is invisible unless someone looks at the
    # input/output token ratio — which means it needs a token count that was
    # actually metered, not one inferred from a run count.
    if model_steps and runs >= 200:
        step = model_steps[0]
        if not (spend_measured and tokens):
            withheld.append(f"prompt cache on {step.name} (no metered tokens)")
        elif tokens / max(1, runs) > 3000:
            savings = spend * 0.25 * monthly
            out.append(
                Recommendation(
                    id=f"{flow_id}:cache:{step.id}",
                    kind="prompt-cache",
                    title=f"Cache the standing prompt on {step.name}",
                    detail=(
                        "Input tokens dominate this definition's spend and the "
                        "system prompt is identical across runs, so the stable "
                        "prefix can be cached rather than re-sent."
                    ),
                    evidence=(
                        f"{tokens / max(1, runs):,.0f} tokens per run across "
                        f"{runs:,} runs · ${spend:,.2f} in {days}d"
                    ),
                    step_id=step.id,
                    step_name=step.name,
                    estimated_monthly_savings_usd=round(savings, 2),
                    confidence=0.6,
                    proposed_change={
                        "step_id": step.id,
                        "field": "cache",
                        "from": step.config.get("cache", "off"),
                        "to": "prefix",
                    },
                )
            )

    # Budget drift: the per-run cap is governed knowledge, so a systematic
    # breach is a decision someone owes, not a number to quietly raise. It
    # takes a measured cost per run to know a cap was breached — and equally
    # to know it was not, which is why silence here would be a false comfort
    # rather than a neutral absence.
    if defn and defn.model_budget_usd_per_run and runs:
        if not spend_measured:
            withheld.append("budget check (no measured cost per run)")
        else:
            cost_per_run = spend / runs
            if cost_per_run > defn.model_budget_usd_per_run:
                out.append(
                    Recommendation(
                        id=f"{flow_id}:budget",
                        kind="budget",
                        title="Runs are exceeding their model budget",
                        detail=(
                            f"Average spend is ${cost_per_run:.2f} per run against a "
                            f"${defn.model_budget_usd_per_run:.2f} cap. Either the cap is "
                            "stale or the pipeline has grown — both are decisions, so "
                            "this one is not applied automatically."
                        ),
                        evidence=(
                            f"${spend:,.2f} over {runs:,} runs in {days}d "
                            f"= ${cost_per_run:.2f}/run"
                        ),
                        confidence=0.9,
                    )
                )

    # Coverage: a thin eval set makes every other recommendation unsafe to
    # accept, so it is surfaced as an insight rather than left implicit. This
    # one needs no spend at all — both halves are counted, not modelled — so
    # it is the card that survives when everything else is withheld.
    if runs and eval_cases < 8:
        out.append(
            Recommendation(
                id=f"{flow_id}:coverage",
                kind="coverage",
                title="Eval coverage is thin for the traffic this flow has seen",
                detail=(
                    f"{eval_cases} case(s) cover {runs:,} run(s). Add cases to "
                    "this flow's eval spec — especially shapes a human had to "
                    "decide — so a candidate version can be graded on work "
                    "that actually occurs."
                ),
                evidence=(
                    f"{eval_cases} eval case(s) · {tasks:,} human decision(s) "
                    f"in {days}d that nothing currently grades"
                ),
                confidence=0.75,
            )
        )

    # Batching: bulk work paying per-record model costs is the doctrine's
    # named anti-pattern. The tell is the trigger, not a type — a flow woken
    # by a window is doing bulk work whatever anyone called it.
    if defn and is_windowed(defn) and model_steps and runs > 300:
        step = model_steps[0]
        if not spend_measured:
            withheld.append(f"batching on {step.name} (no measured spend)")
        else:
            savings = spend * 0.35 * monthly
            out.append(
                Recommendation(
                    id=f"{flow_id}:batch:{step.id}",
                    kind="batching",
                    title=f"Widen the window on {step.name}",
                    detail=(
                        "This flow invokes a model on a narrow window. A wider "
                        "window amortizes the standing prompt across more records "
                        "and cuts the per-record share."
                    ),
                    evidence=f"{runs:,} windowed runs in {days}d · ${spend:,.2f}",
                    step_id=step.id,
                    step_name=step.name,
                    estimated_monthly_savings_usd=round(savings, 2),
                    confidence=0.55,
                    proposed_change={
                        "step_id": step.id,
                        "field": "window",
                        "from": step.config.get("window", "2h"),
                        "to": "6h",
                    },
                )
            )

    out.sort(key=lambda r: r.estimated_monthly_savings_usd, reverse=True)
    # The basis leads: a reader has to know what the list rests on before
    # reading the list, and when the list is empty it is the whole answer.
    return [
        _basis_card(flow_id, runs, days, model_calls, priced_calls, eval_cases, withheld),
        *out,
    ]


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class FlowService:
    def __init__(self, flows: FlowDirectory) -> None:
        self.flows = flows

    # ---- registry ----

    def list(self) -> list[FlowDefinition]:
        return self.flows.list()

    def get(self, flow_id: str) -> FlowDefinition | None:
        return self.flows.get(flow_id)

    def require(self, flow_id: str) -> FlowDefinition:
        defn = self.flows.get(flow_id)
        if defn is None:
            raise FlowNotFoundError(flow_id)
        return defn

    def create(self, defn: FlowDefinition) -> FlowDefinition:
        return self.flows.create(defn)

    def update(self, defn: FlowDefinition) -> FlowDefinition:
        return self.flows.update(
            defn.model_copy(update={"updated_at": utcnow()})
        )

    def delete(self, flow_id: str) -> None:
        self.flows.delete(flow_id)

    # ---- versions ----

    def versions(self, flow_id: str) -> list[FlowVersion]:
        return self.require(flow_id).version_history()

    def version(self, flow_id: str, version: int) -> FlowVersion | None:
        return self.require(flow_id).find_version(version)

    def version_by_label(self, flow_id: str, label: str) -> FlowVersion | None:
        return next(
            (v for v in self.versions(flow_id) if v.display() == label), None
        )

    def diff(
        self, flow_id: str, version: int, against: int | None
    ) -> list[StepDiff]:
        defn = self.require(flow_id)
        candidate = defn.find_version(version)
        if candidate is None:
            raise ValueError(f"Version not found: v{version}")
        baseline = (
            defn.find_version(against) if against is not None else defn.active_version()
        )
        if baseline is None:
            raise ValueError(f"Version not found: v{against}")
        return diff_versions(candidate, baseline)

    def _ensure_draft(
        self, defn: FlowDefinition, authored_by: str
    ) -> tuple[FlowDefinition, FlowVersion]:
        """Open a draft off the active version, or return the existing one.

        A change is never applied to the live version: the point of a draft is
        that a candidate is graded against the eval spec before it becomes
        the thing running in production.
        """
        existing = defn.draft_version()
        if existing is not None:
            return defn, existing
        active = defn.active_version()
        draft = FlowVersion(
            version=active.version + 1,
            label=f"v{active.version + 1}-draft",
            status="draft",
            steps=[s.model_copy(deep=True) for s in active.steps],
            model_budget_usd_per_run=defn.model_budget_usd_per_run,
            notes="Opened from the console.",
            authored_by=authored_by,
        )
        history = defn.version_history()
        return defn.model_copy(update={"versions": history + [draft]}), draft

    def apply_recommendation(
        self, flow_id: str, rec: Recommendation, actor: str
    ) -> tuple[FlowDefinition, FlowVersion]:
        """Stage a recommendation's change into the draft version.

        Advisory cards (no `proposed_change`) are refused rather than
        silently no-oping: "applied" must mean something changed.
        """
        if not rec.proposed_change:
            raise ValueError(
                "This insight is advisory — there is no mechanical change to apply."
            )
        defn = self.require(flow_id)
        defn, draft = self._ensure_draft(defn, actor)
        change = rec.proposed_change
        step_id = change.get("step_id", "")
        field = change.get("field", "")
        target = change.get("to", "")
        steps = []
        touched = False
        for step in draft.steps:
            if step.id != step_id:
                steps.append(step)
                continue
            config = dict(step.config)
            config[field] = target
            steps.append(step.model_copy(update={"config": config}))
            touched = True
        if not touched:
            raise ValueError(f"Step not found in the draft: {step_id}")
        note = (
            f"{rec.title} — set {field} to {target} on {step_id} "
            f"(applied by {actor})."
        )
        updated_draft = draft.model_copy(
            update={
                "steps": steps,
                "notes": (draft.notes + " " + note).strip(),
                "authored_by": actor,
            }
        )
        versions = [
            updated_draft if v.version == draft.version else v
            for v in defn.version_history()
        ]
        saved = self.update(defn.model_copy(update={"versions": versions}))
        log.info(
            "Recommendation applied to draft",
            extra={
                "flowId": flow_id,
                "recommendationId": rec.id,
                "draft": updated_draft.display(),
                "actor": actor,
            },
        )
        return saved, updated_draft

    def usage(self, flow_id: str, days: int) -> list[UsageDay]:
        """This flow's real daily history. A definition is not required.

        Five of the seven flows that run have no `FlowDefinition` — they are
        inbound sync feeds — and their runs are as real as anyone's. Requiring
        a definition here is what let the fleet report a two-flow world.
        """
        defn = self.flows.get(flow_id)
        return usage_series(
            flow_runs(flow_id), days, defn.roi_minutes_per_run if defn else 0.0
        )

    def recommendations(self, flow_id: str, days: int = 30) -> list[Recommendation]:
        return recommendations(
            flow_id,
            self.flows.get(flow_id),
            self.usage(flow_id, days),
            eval_case_count(flow_id),
            days,
        )


def build_flow_service(
    backend: str, project: str | None, flows_collection: str
) -> FlowService:
    if backend == "firestore":
        return FlowService(FirestoreFlowDirectory(project, flows_collection))
    return FlowService(InMemoryFlowDirectory())


# ---------------------------------------------------------------------------
# Seed fleet
# ---------------------------------------------------------------------------


def _ago(days: int) -> datetime:
    return utcnow() - timedelta(days=days)


def _seed_definitions() -> list[FlowDefinition]:
    return [
        FlowDefinition(
            id="expense-reimbursement",
            name="Expense reimbursement",
            description=(
                "Reads a submitted receipt image with a ModelCall, checks it against "
                "the governed expense policy in the knowledge layer, elevates to human "
                "review when policy or confidence demands it, and records the request "
                "with its state in the reimbursements table."
            ),
            team="finance-ops",
            status="active",
            version=9,
            tags=["expenses", "reimbursement", "vision"],
            queues=["expense-review"],
            workspace="ap-workspace",
            roi_minutes_per_run=6,
            model_budget_usd_per_run=0.25,
            authored_by="fde:m.suter",
            created_at=_ago(140),
            updated_at=_ago(9),
            steps=[
                # The event name is the one the `expense-submitted` Trigger
                # binds, not a near-synonym: an export naming a fact nothing
                # emits is the display string this module used to carry.
                FlowStep(id="t1", type="trigger", name="ExpenseSubmitted",
                         summary="one run per submitted receipt",
                         config={"event": "ExpenseSubmitted"}, next=["m1"]),
                FlowStep(id="m1", type="modelcall", name="extract-receipt",
                         summary="reads the receipt image: vendor, date, total, category — with a confidence per field",
                         config={"model": "gemini-3.5-flash-lite",
                                 "prompt": "knowledge:receipt-extraction-prompt"},
                         next=["x1"]),
                FlowStep(id="x1", type="transform", name="read-policy",
                         summary="gathers the governed policy values (auto-approval limit, category caps) from the knowledge layer for the rule — rules never fetch",
                         config={"source": "knowledge:expense-approval-policy"}, next=["r1"]),
                FlowStep(id="r1", type="rule", name="needs-elevation",
                         summary="within the policy's auto-approval limit, an allowed category, and confidently extracted?",
                         config={"rule_type": "threshold",
                                 "source": "knowledge:expense-approval-policy"},
                         next=["a1", "k1"], branch_labels=["approve", "elevate"]),
                FlowStep(id="k1", type="task", name="elevate-review",
                         summary="a reviewer decides the elevated request with the extraction and the policy findings attached",
                         config={"queue": "expense-review"}, next=["a1"]),
                FlowStep(id="a1", type="action", name="record-reimbursement",
                         summary="guarded write of the request and its state into the reimbursements table",
                         config={"target": "reimbursements", "gateway": "action-gateway"}, next=["e1"]),
                FlowStep(id="e1", type="event", name="ReimbursementRecorded",
                         summary="emitted on commit", config={"event": "ReimbursementRecorded"}),
            ],
        ),
        FlowDefinition(
            id="accounts-payable",
            name="Accounts payable",
            description=(
                "Reads an inbound invoice in one extraction pass, resolves the "
                "printed vendor name against the vendor master, matches the "
                "billed lines against the purchase order and the receiving "
                "report already in the graph, screens the invoice against this "
                "vendor's own payment history, and posts under the governed "
                "auto-post limit — or routes it to a clerk, whose correction "
                "re-runs the match rather than overriding it."
            ),
            team="finance-ops",
            status="active",
            version=6,
            tags=["ap", "invoices", "vision", "three-way-match"],
            queues=["ap-clerk"],
            workspace="ap-workspace",
            roi_minutes_per_run=9,
            model_budget_usd_per_run=0.60,
            authored_by="fde:m.suter",
            created_at=_ago(90),
            updated_at=_ago(4),
            steps=[
                FlowStep(id="t1", type="trigger", name="InvoiceReceived",
                         summary="one run per inbound invoice",
                         config={"event": "InvoiceReceived"}, next=["s1"]),
                FlowStep(id="s1", type="action", name="store-document",
                         summary="the invoice bytes land in the document store before anything reads them, so every later claim has an artifact to point at",
                         config={"target": "documents", "gateway": "action-gateway"},
                         next=["m1"]),
                FlowStep(id="m1", type="modelcall", name="extract-invoice",
                         summary="one pass over the document: the vendor as printed, the invoice number, dates, PO, totals and the billed lines — with a confidence",
                         config={"model": "gemini-3.5-flash-lite",
                                 "prompt": "knowledge:invoice-extraction-prompt"},
                         next=["z1"]),
                FlowStep(id="z1", type="resolve", name="resolve-vendor",
                         summary="the printed name becomes a canonical Vendor id against the vendor master; an unknown one is minted `onboarding`, an ambiguous one stops the run",
                         config={"entity": "Vendor", "source": "knowledge:entity-resolution-policy"},
                         next=["x3", "k0"], branch_labels=["resolved", "ambiguous"]),
                FlowStep(id="k0", type="task", name="confirm-vendor",
                         summary="a clerk names the vendor from the scored candidates — nothing is asserted until they do, because posting against the wrong vendor is not recoverable",
                         config={"queue": "ap-clerk"}, next=["a0"]),
                FlowStep(id="a0", type="action", name="apply-vendor-decision",
                         summary="guarded write of the vendor the clerk chose: an identity link, or a newly minted vendor",
                         config={"target": "vendor-master", "gateway": "action-gateway"},
                         next=["x3"]),
                FlowStep(id="x3", type="transform", name="project-lines",
                         summary="the extracted lines become facts against the ontology's paths — the same vocabulary the eval spec asserts on",
                         config={"language": "python"}, next=["x4"]),
                FlowStep(id="x4", type="transform", name="gather-po-and-receiving",
                         summary="reads the purchase order and receiving lines out of the graph — the flow gathers; the matcher and the rule never fetch",
                         config={"source": "graph:PurchaseOrder,ReceivingReport"},
                         next=["c1"]),
                FlowStep(id="c1", type="check", name="three-way-match",
                         summary="pairs invoice ↔ PO ↔ receiving line by line within the governed tolerances, and writes the pairings as a MatchSet rather than discarding them",
                         config={"source": "knowledge:invoice-posting-policy"},
                         next=["c2"]),
                FlowStep(id="c2", type="check", name="anomaly-screen",
                         summary="scores the invoice against this vendor's own payment history — duplicates, amount outliers, round numbers — with thresholds read at run time",
                         config={"source": "knowledge:invoice-posting-policy"},
                         next=["x5"]),
                FlowStep(id="x5", type="transform", name="read-posting-policy",
                         summary="gathers the governed posting values (auto-post limit, confidence floor) for the rule",
                         config={"source": "knowledge:invoice-posting-policy"}, next=["r2"]),
                FlowStep(id="r2", type="rule", name="post-or-review",
                         summary="under the governed auto-post limit, confidently extracted, matched, and nothing unusual?",
                         config={"rule_type": "threshold", "source": "knowledge:invoice-posting-policy"},
                         next=["a1", "k2"], branch_labels=["post", "review"]),
                FlowStep(id="k2", type="task", name="review-invoice",
                         summary="a clerk decides the flagged invoice; a correction re-runs the match and the rule rather than overriding them",
                         config={"queue": "ap-clerk"}, next=["a1"]),
                FlowStep(id="a1", type="action", name="post-to-ap",
                         summary="guarded write of the invoice, its lines and the match set into the graph and the AP ledger",
                         config={"target": "ap-ledger", "gateway": "action-gateway"}, next=["e1"]),
                FlowStep(id="e1", type="event", name="InvoicePosted",
                         summary="emitted on commit", config={"event": "InvoicePosted"}),
            ],
        ),
    ]


# Definitions that seed an open draft, and how that draft differs from the
# active version — an added node plus a modified one, so the canvas diff
# overlay has real indicators to paint.
_SEED_DRAFTS: dict[str, str] = {
    "expense-reimbursement": "add-duplicate-check",
}


def _earlier_revision(ref: str, back: int) -> str:
    """The same knowledge ref, `back` revisions earlier.

    A bare ref is treated as naming the current revision, because that is what
    it resolves to — so the archived version pins one explicitly and the diff
    shows the edit as what it was: a prompt revision, not a rename.
    """
    base, _, revision = ref.partition("@rev")
    current = int(revision) if revision.isdigit() else 1
    return f"{base}@rev{max(1, current - back)}"


def _seed_version_history(defn: FlowDefinition) -> list[FlowVersion]:
    """Two archived revisions, the active one, and sometimes an open draft."""
    active_steps = [s.model_copy(deep=True) for s in defn.steps]
    versions: list[FlowVersion] = []

    # Archived revisions differ only in their governed knowledge — an earlier
    # revision of the prompt the ModelCall reads. That is the most common real
    # edit, it keeps the diff legible, and it is now literally how a prompt
    # versions: the config holds a `knowledge:` ref, and an older version of
    # the flow pinned an older revision of the same asset.
    for back in (2, 1):
        older = []
        for step in active_steps:
            copy = step.model_copy(deep=True)
            if copy.type == "modelcall" and "prompt" in copy.config:
                config = dict(copy.config)
                config["prompt"] = _earlier_revision(config["prompt"], back)
                copy = copy.model_copy(update={"config": config})
            older.append(copy)
        versions.append(
            FlowVersion(
                version=defn.version - back,
                label=f"v{defn.version - back}",
                status="archived",
                steps=older,
                model_budget_usd_per_run=defn.model_budget_usd_per_run,
                notes="Superseded.",
                authored_by=defn.authored_by,
                created_at=defn.updated_at - timedelta(days=30 * back),
            )
        )

    versions.append(
        FlowVersion(
            version=defn.version,
            label=f"v{defn.version}",
            status="active",
            steps=active_steps,
            model_budget_usd_per_run=defn.model_budget_usd_per_run,
            notes="Live.",
            authored_by=defn.authored_by,
            created_at=defn.updated_at,
        )
    )

    recipe = _SEED_DRAFTS.get(defn.id)
    if recipe is None:
        return versions

    draft_steps = [s.model_copy(deep=True) for s in active_steps]
    # add-duplicate-check: a new deterministic guard in front of the model
    # call, so the trigger is modified (its `next` moves) and the guard
    # itself is added.
    draft_steps = [
        s.model_copy(update={"next": ["x0"]}) if s.id == "t1" else s
        for s in draft_steps
    ]
    guard = FlowStep(
        id="x0", type="transform", name="duplicate-check",
        summary="rejects a receipt already reimbursed, before any model runs",
        config={"language": "python"}, next=["m1"],
    )
    draft_steps.insert(1, guard)
    notes = "Adds a deterministic duplicate guard ahead of extraction."

    versions.append(
        FlowVersion(
            version=defn.version + 1,
            label=f"v{defn.version + 1}-draft",
            status="draft",
            steps=draft_steps,
            model_budget_usd_per_run=defn.model_budget_usd_per_run,
            notes=notes,
            authored_by=defn.authored_by,
            created_at=utcnow() - timedelta(days=2),
        )
    )
    return versions


def _upgraded(stored: FlowDefinition, latest: FlowDefinition) -> FlowDefinition:
    """The code's definition, carrying the stored one's history beneath it.

    The point of re-seeding by version is that the change is *legible*: the
    revision the console was showing is archived rather than dropped, so the
    version picker still opens it and `v5 → v6` diffs to the steps that
    actually changed. Drafts at or below the new version go — they were
    proposals against a map that no longer exists.
    """
    history = [
        version.model_copy(update={"status": "archived"})
        if version.status == "active" else version
        for version in stored.version_history()
        if not (version.status == "draft" and version.version <= latest.version)
    ]
    history = [v for v in history if v.version != latest.version]
    history.append(
        FlowVersion(
            version=latest.version,
            label=f"v{latest.version}",
            status="active",
            steps=[step.model_copy(deep=True) for step in latest.steps],
            model_budget_usd_per_run=latest.model_budget_usd_per_run,
            notes="Published from the repository definition.",
            authored_by=latest.authored_by,
            created_at=utcnow(),
        )
    )
    return latest.model_copy(update={
        "versions": history,
        # The definition's own age is a fact about the deployment, not about
        # this release.
        "created_at": stored.created_at,
        "updated_at": utcnow(),
    })


def seed_flows_if_empty(service: FlowService) -> int:
    """Bring the registry in line with the definitions declared in code.

    Registering the fleet is the easy half. The half that bit us: a stored
    definition is the *only* source of a flow's steps — `FlowUpdate` cannot
    carry them — so a seed that ran once meant a definition edited in code
    could never reach an environment that already had it. A console would go
    on drawing a canvas the engine had stopped running, which is precisely
    the divergence the transparency bar exists to prevent.

    So the version is the contract. A definition whose declared `version` is
    higher than the stored one replaces it, archiving what was there into the
    version history; equal or lower is left alone. That makes the bump the
    explicit opt-in — nothing is overwritten because a summary was reworded —
    and it keeps the map honest across a redeploy.
    """
    created = updated = 0
    for defn in _seed_definitions():
        stored = service.get(defn.id)
        if stored is None:
            try:
                service.create(
                    defn.model_copy(update={"versions": _seed_version_history(defn)})
                )
            except FlowExistsError:  # raced with another worker; theirs wins
                continue
            created += 1
            continue

        if defn.version <= stored.version:
            continue

        service.update(_upgraded(stored, defn))
        log.info(
            "Flow definition upgraded from the repository",
            extra={
                "flowId": defn.id,
                "from": stored.version,
                "to": defn.version,
            },
        )
        updated += 1

    if created or updated:
        log.info(
            "Seeded flow definitions",
            extra={"count": created, "upgraded": updated},
        )
    return created
