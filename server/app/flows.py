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
import re
import threading
from collections.abc import Collection
from datetime import date, datetime, timedelta
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from . import dispatch
from .models import utcnow
from .runs import ModelUsage, RunRecord

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


def open_tasks_by_flow(
    queues: Collection[str] | None = None,
) -> dict[str, int]:
    """Open Tasks per flow, counted in the task store.

    The synthesized backlog this replaces was a per-day random draw off the
    definition's task rate, so it moved at midnight and correlated with
    nothing a person could act on. These are the rows in the queue.

    `queues` narrows the count to one principal's bindings — the same slice
    `TaskService.stats` takes, and for the same reason. An unscoped total on
    the Flows header put "Awaiting judgment 21" beside a My Tasks reading
    "Queue is clear", which is two screens arguing about one number.
    """
    counts: dict[str, int] = {}
    from .deps import get_task_service

    for task in get_task_service().list(status="open"):
        if queues is not None and task.queue not in queues:
            continue
        if task.flow_id:
            counts[task.flow_id] = counts.get(task.flow_id, 0) + 1
    return counts


def open_tasks(flow_id: str, queues: Collection[str] | None = None) -> int:
    return open_tasks_by_flow(queues).get(flow_id, 0)


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

MODEL_PRICING_ASSET_ID = "model-pricing"

#: The built-in copy of the governed table, for a deployment whose corpus does
#: not hold the asset yet. Same posture as the extraction prompts: the seed
#: file `seed/model-pricing.md` is the governing copy and its table is these
#: rows, and a missing asset degrades to the default rather than to no price at
#: all. $/M input, $/M output — display-plausible, not a contract.
_DEFAULT_RATES: dict[str, tuple[float, float]] = {
    "gemini-3.7-flash": (0.30, 2.50),
    "gemini-3.5-flash-lite": (0.10, 0.40),
    "claude-fable-5": (5.0, 25.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

#: How the two rates blend into the one number the downgrade ladder compares:
#: roughly an extraction workload, where the document is long and the JSON is
#: short. Only ever used as a ratio between two models, so the absolute number
#: never reaches a page.
_INPUT_WEIGHT = 0.6

# Cheaper substitutes, in descending capability. A downgrade is only ever
# proposed one rung at a time: two rungs is a different flow, not a saving.
# Cross-vendor rungs are deliberately absent — swapping vendors changes what
# the prompt means, which is a migration to evaluate, not a cost saving to
# apply from a card.
#
# This stays code while the rates became a document, and the split is the
# point: a rate is a fact about a vendor's price list that a steward should
# correct from an invoice, and a rung is a claim that two models are close
# enough in capability to swap after an eval. The asset says so out loud, so
# nobody reads a table pricing both vendors side by side as an invitation to
# trade one for the other.
_DOWNGRADE_PATH = {
    "gemini-3.7-flash": "gemini-3.5-flash-lite",
    "claude-opus-5": "claude-sonnet-5",
    "claude-fable-5": "claude-sonnet-5",
    "claude-sonnet-5": "claude-haiku-4-5",
}

#: One row of the asset's rate table: `| model-id | 0.30 | 2.50 |`. The header
#: and the `|---|` separator fail it — a model id starts lower-case and both
#: rates have to parse as numbers.
_RATE_ROW = re.compile(
    r"^\s*\|\s*([a-z][a-z0-9.\-]*)\s*\|\s*\$?\s*(\d+(?:\.\d+)?)\s*\|"
    r"\s*\$?\s*(\d+(?:\.\d+)?)\s*\|",
    re.MULTILINE,
)


class ModelRates(BaseModel):
    """The governed price sheet, at the revision it was read.

    A model with no row prices to **zero**, and zero means *unpriced*. That is
    the one decision in here worth arguing about, and the alternative is worse:
    a fallback rate would put a plausible dollar figure on a call nobody knows
    the price of, which is the same confident-number-over-a-hole failure
    `cost_basis` exists to prevent. An unpriced call has to reach the console
    as unpriced.
    """

    rates: dict[str, tuple[float, float]] = Field(
        default_factory=lambda: dict(_DEFAULT_RATES)
    )
    source_ref: str = f"knowledge:{MODEL_PRICING_ASSET_ID}"
    found_asset: bool = False

    def price_usd(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """What this call cost, or 0.0 for "the sheet does not price it"."""
        row = self.rates.get(model)
        if row is None:
            return 0.0
        rate_in, rate_out = row
        return round(
            (input_tokens * rate_in + output_tokens * rate_out) / 1_000_000, 6
        )

    def priced(self, usage: ModelUsage) -> ModelUsage:
        """The same usage with `cost_usd` filled in from this sheet.

        The seam between "what the call consumed" and "what that cost" — the
        reader that made the call knows the first and never the second, so it
        reports tokens and this puts a price on them.
        """
        return usage.model_copy(update={
            "cost_usd": self.price_usd(
                usage.model, usage.input_tokens, usage.output_tokens
            )
        })

    def blended(self, model: str) -> float:
        """One number per model, for comparing two rungs of the ladder."""
        rate_in, rate_out = self.rates.get(model, (0.0, 0.0))
        return rate_in * _INPUT_WEIGHT + rate_out * (1 - _INPUT_WEIGHT)


def parse_model_pricing(content: str) -> ModelRates:
    """Read the asset's rate table. An empty table leaves the defaults."""
    parsed = {
        match.group(1): (float(match.group(2)), float(match.group(3)))
        for match in _RATE_ROW.finditer(content)
    }
    values = ModelRates(found_asset=True)
    if parsed:
        values.rates = parsed
    return values


def read_model_rates(knowledge) -> ModelRates:
    """The price sheet as governed knowledge, stamped with its revision."""
    asset = knowledge.repo.get(MODEL_PRICING_ASSET_ID) if knowledge else None
    if asset is None:
        log.warning("Model pricing asset missing; using the built-in rates")
        return ModelRates(found_asset=False)
    values = parse_model_pricing(asset.content)
    values.source_ref = (
        f"knowledge:{MODEL_PRICING_ASSET_ID}@rev{getattr(asset, 'revision', 1)}"
    )
    return values


def fleet_model_rates() -> ModelRates:
    """The price sheet for a caller with no knowledge handle of its own.

    Deferred import for the reason `flow_runs` defers its own: `deps` builds
    this module's service. Guarded, because the recommender must degrade to
    the built-in rates rather than fail a console page over a price sheet.
    """
    try:
        from .deps import get_service

        return read_model_rates(get_service())
    except Exception:  # noqa: BLE001 — a page must not 500 over a rate card
        log.exception("Could not read the model price sheet; using defaults")
        return ModelRates(found_asset=False)


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
    rates: ModelRates | None = None,
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

    `rates` is read **now**, not at the revision the priced runs were priced
    at, and that is deliberate: a downgrade card is a claim about what a future
    run would cost, so it belongs on today's price sheet. The opposite rule
    governs a journal entry — see `ModelRates.priced`.
    """
    rates = rates if rates is not None else fleet_model_rates()
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
        # A rung the sheet does not price is a rung with no saving to claim:
        # the ratio would be a guess, and a guess with a dollar sign on it is
        # the one thing this recommender is not allowed to produce.
        current_rate = rates.blended(model)
        if not current_rate:
            withheld.append(
                f"model downgrade on {step.name} "
                f"({model} is not in {rates.source_ref})"
            )
            continue
        ratio = rates.blended(cheaper) / current_rate
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

    def activate_version(self, flow_id: str, version: int) -> FlowDefinition:
        """Make one shipped version the live one.

        The console drives which version is live, but it can only choose among
        versions whose executable package is in the deployed code — a stored
        pointer at code that is not there would be a map the engine cannot
        honor. Raises ValueError for a version that is not shipped.
        """
        from . import tasks as process_packages

        defn = self.require(flow_id)
        shipped = process_packages.shipped_versions(flow_id)
        if version not in shipped:
            raise ValueError(
                f"v{version} of {flow_id} is not shipped in this deployment; "
                "only a version whose package is in the running code can be "
                "made live"
            )
        history = defn.version_history()
        target = next((v for v in history if v.version == version), None)
        if target is None:
            module = shipped[version]
            target = FlowVersion(
                version=version,
                label=module.LABEL,
                status="draft",
                steps=[s.model_copy(deep=True) for s in module.STEPS],
                model_budget_usd_per_run=defn.model_budget_usd_per_run,
                notes=module.NOTES,
                authored_by=defn.authored_by,
                created_at=utcnow(),
            )
            history.append(target)
        reshaped = []
        for entry in history:
            if entry.version == version:
                reshaped.append(entry.model_copy(update={"status": "active"}))
            elif entry.status == "active":
                reshaped.append(entry.model_copy(update={"status": "archived"}))
            else:
                reshaped.append(entry)
        return self.update(
            defn.model_copy(
                update={
                    "version": version,
                    "steps": [s.model_copy(deep=True) for s in target.steps],
                    "model_budget_usd_per_run": target.model_budget_usd_per_run,
                    "versions": sorted(reshaped, key=lambda v: v.version),
                }
            )
        )

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
    # The three FDE-authored processes declare their own shape: the steps the
    # map shows are read off the default version package under `app/tasks/`,
    # never kept as a second copy here. Deferred import — the version packages
    # import this module for `FlowStep`.
    from . import tasks as process_packages
    from .tasks import accounts_payable, expense_reimbursement, order_release

    def package_steps(flow_id: str, version: int) -> list[FlowStep]:
        module = process_packages.shipped_versions(flow_id)[version]
        return [step.model_copy(deep=True) for step in module.STEPS]

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
            version=expense_reimbursement.DEFAULT_VERSION,
            tags=["expenses", "reimbursement", "vision"],
            queues=["expense-review"],
            workspace="ap-workspace",
            roi_minutes_per_run=6,
            model_budget_usd_per_run=0.25,
            authored_by="fde:m.suter",
            created_at=_ago(140),
            updated_at=_ago(9),
            steps=package_steps("expense-reimbursement", expense_reimbursement.DEFAULT_VERSION),
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
            version=accounts_payable.DEFAULT_VERSION,
            tags=["ap", "invoices", "vision", "three-way-match"],
            queues=["ap-clerk"],
            workspace="ap-workspace",
            roi_minutes_per_run=9,
            model_budget_usd_per_run=0.60,
            authored_by="fde:m.suter",
            created_at=_ago(90),
            updated_at=_ago(4),
            steps=package_steps("accounts-payable", accounts_payable.DEFAULT_VERSION),
        ),
        # -- the sync fleet -------------------------------------------------
        #
        # Ten definitions, one per inbound feed that writes the graph, and the
        # whole fleet: the carried debt the workplan opened with — "five flows
        # write the graph with no map" — is closed, and nothing registers a
        # sync body without a definition below it. Every step id was read off a
        # real journal rather than designed: an export that draws a step the
        # engine does not run is the exact divergence the transparency bar
        # exists to catch, and a map of a batch feed is only worth having if
        # it is the map of what happened.
        #
        # None of them has a `task` step, and that is the point rather than an
        # omission: a windowed flow never suspends for one ambiguous row. The
        # Task is raised *inside* the apply step and the run finishes; the
        # steward's answer arrives later through `apply_match_decision`, which
        # carries no run because there is no run left to carry it.
        #
        # And none of them declares `roi_minutes_per_run`. A feed's saved time
        # is not a thing anybody has measured, and a plausible number here
        # would put invented value straight into the fleet's ROI figure by the
        # side door — which is the exact failure the run count was rebuilt to
        # end. Zero is the honest assumption until somebody makes a real one.
        FlowDefinition(
            id="vendor-sync",
            name="Vendor sync",
            description=(
                "Lands an ERP vendor delivery exactly as it arrived, scores "
                "each row against the vendor master under the governed "
                "resolution thresholds, and then — in a separate step, "
                "because nothing is asserted while a match is in doubt — "
                "links, mints, or raises a steward Task per row. A batch feed "
                "that stopped on row 47 would hold up rows 48 to 200 for no "
                "reason, so it never stops."
            ),
            team="platform-eng",
            status="active",
            version=1,
            tags=["sync", "vendors", "mdm", "identity"],
            queues=["vendor-stewardship"],
            workspace="ap-workspace",
            roi_minutes_per_run=0.0,
            model_budget_usd_per_run=0.0,
            authored_by="fde:m.suter",
            created_at=_ago(120),
            updated_at=_ago(1),
            steps=package_steps("vendor-sync", 1),
        ),
        FlowDefinition(
            id="purchase-order-sync",
            name="Purchase order sync",
            description=(
                "Lands ERP purchase orders and writes each one with its lines, "
                "binding the order to the Vendor it was cut against. No "
                "resolve step of its own: the vendor is named by an id the "
                "vendor feed already resolved, so this flow reads that "
                "identity rather than deciding it a second time — and an "
                "order whose vendor is not in the graph yet is counted on the "
                "aggregate, never guessed at."
            ),
            team="platform-eng",
            status="active",
            version=1,
            tags=["sync", "purchasing", "three-way-match"],
            queues=[],
            workspace="ap-workspace",
            roi_minutes_per_run=0.0,
            model_budget_usd_per_run=0.0,
            authored_by="fde:m.suter",
            created_at=_ago(120),
            updated_at=_ago(1),
            steps=package_steps("purchase-order-sync", 1),
        ),
        FlowDefinition(
            id="crm-sync",
            name="CRM book sync",
            description=(
                "Lands the CRM's accounts and contacts and resolves them onto "
                "the counterparty book: an account becomes a Company under "
                "the same normalization the vendor master uses, a contact "
                "becomes a Person keyed by email address — and a contact with "
                "no address mints its own rather than being matched on a "
                "name, because two people who share a spelling are two "
                "people. Opportunities bind instead of reifying: a deal id is "
                "exact, and the only thing worth adjudicating about a deal is "
                "its stage, which belongs to the CRM."
            ),
            team="platform-eng",
            status="active",
            version=1,
            tags=["sync", "crm", "counterparties", "mdm", "identity"],
            queues=["data-stewardship"],
            workspace="ap-workspace",
            roi_minutes_per_run=0.0,
            model_budget_usd_per_run=0.0,
            authored_by="fde:m.suter",
            created_at=_ago(2),
            updated_at=_ago(0),
            steps=package_steps("crm-sync", 1),
        ),
        FlowDefinition(
            id="sales-sync",
            name="Sales record sync",
            description=(
                "The ERP's sell side, onto the same counterparty book the CRM "
                "writes into. Customers reify and resolve — this is the "
                "second voice on the same companies, and where the two "
                "disagree about a commercial fact both claims are kept and "
                "the ERP's precedence decides what resolves. The catalogue "
                "and the orders bind: a SKU and an order number are exact. "
                "The one thing that needs work is the line, which names its "
                "item by an internal id the catalogue row is the only "
                "translation for."
            ),
            team="platform-eng",
            status="active",
            version=1,
            tags=["sync", "erp", "orders", "counterparties", "mdm"],
            queues=["data-stewardship"],
            workspace="ap-workspace",
            roi_minutes_per_run=0.0,
            model_budget_usd_per_run=0.0,
            authored_by="fde:m.suter",
            created_at=_ago(2),
            updated_at=_ago(0),
            steps=package_steps("sales-sync", 1),
        ),
        FlowDefinition(
            id="workforce-sync",
            name="Workforce sync",
            description=(
                "Lands the HRIS's departments and workers and wires the org "
                "chart from the bind columns each row carries. Nothing is "
                "reified and nothing reaches a steward: a work email is an "
                "exact key, so there is no match to adjudicate. What can fail "
                "is a reference — a manager or a department this delta did "
                "not carry — and the flow resolves those against the ids "
                "already claimed on the people and teams it holds before "
                "counting one unresolved. A worker the HRIS calls Terminated "
                "lands saying so."
            ),
            team="platform-eng",
            status="active",
            version=1,
            tags=["sync", "hris", "people", "org-chart"],
            queues=[],
            workspace="ap-workspace",
            roi_minutes_per_run=0.0,
            model_budget_usd_per_run=0.0,
            authored_by="fde:m.suter",
            created_at=_ago(120),
            updated_at=_ago(0),
            steps=package_steps("workforce-sync", 1),
        ),
        FlowDefinition(
            id="receiving-sync",
            name="Receiving sync",
            description=(
                "Lands receiving reports and writes each one with its lines, "
                "booked against the purchase order it names. The "
                "goods-actually-arrived leg of the three-way match, which "
                "reads these rows out of the graph rather than re-fetching "
                "them. A report whose PO is not in the graph yet still "
                "lands — it is counted, and the match finds it when the order "
                "arrives."
            ),
            team="platform-eng",
            status="active",
            version=1,
            tags=["sync", "receiving", "three-way-match"],
            queues=[],
            workspace="ap-workspace",
            roi_minutes_per_run=0.0,
            model_budget_usd_per_run=0.0,
            authored_by="fde:m.suter",
            created_at=_ago(120),
            updated_at=_ago(0),
            steps=package_steps("receiving-sync", 1),
        ),
        FlowDefinition(
            id="employment-event-sync",
            name="Employment ledger sync",
            description=(
                "Mirrors the HRIS's append-only employment ledger and stops "
                "there. Landing is the whole flow — no resolve step and no "
                "apply step, because these rows describe changes to people "
                "the workforce feed already lands, and asserting them a "
                "second time would put two writers on one set of claims. Each "
                "row carries what a job, department, manager or salary became "
                "*and* what it was; no type holds a before/after pair, so the "
                "row is kept whole for the flows that will ask about history."
            ),
            team="platform-eng",
            status="active",
            version=1,
            tags=["sync", "hris", "ledger", "history"],
            queues=[],
            workspace="ap-workspace",
            roi_minutes_per_run=0.0,
            model_budget_usd_per_run=0.0,
            authored_by="fde:m.suter",
            created_at=_ago(1),
            updated_at=_ago(0),
            steps=package_steps("employment-event-sync", 1),
        ),
        FlowDefinition(
            id="dms-sync",
            name="Document library sync",
            description=(
                "Mirrors the customer's filing tree as knowledge about where "
                "things are, never as a copy of what is in them. Folders "
                "become objects keyed by their path from the root; each file "
                "becomes a Document row stamped with the document library's "
                "Collection and a version-pinned pointer back to the "
                "original. The bytes are never fetched — that is the "
                "Collection's declared reference custody, and it is why a row "
                "here has no content hash: a hash is what a file somebody "
                "read has."
            ),
            team="platform-eng",
            status="active",
            version=1,
            tags=["sync", "dms", "documents", "evidence", "filing"],
            queues=[],
            workspace="ap-workspace",
            roi_minutes_per_run=0.0,
            model_budget_usd_per_run=0.0,
            authored_by="fde:m.suter",
            created_at=_ago(1),
            updated_at=_ago(0),
            steps=package_steps("dms-sync", 1),
        ),
        FlowDefinition(
            id="ap-intake",
            name="AP invoice intake",
            description=(
                "Turns the customer's own filing tree into accounts-payable "
                "work. Woken by the same delivery that wakes `dms-sync`, it "
                "reads the governed AP folders, finds the documents under "
                "them whose pointer resolves to a revision nobody has read, "
                "fetches the bytes through that pointer and stores them under "
                "their content hash — then emits one InvoiceReceived per "
                "invoice, which is the seam where a windowed batch becomes "
                "per-case work. A file that will not fetch is a count on the "
                "run, never a Task the other thirty-nine queue behind; the "
                "only Task it raises is the aggregate, when nothing in a "
                "window answered at all."
            ),
            team="platform-eng",
            status="active",
            version=1,
            tags=["intake", "ap", "documents", "dms", "invoices"],
            queues=[],
            workspace="ap-workspace",
            roi_minutes_per_run=0.0,
            model_budget_usd_per_run=0.0,
            authored_by="fde:m.suter",
            created_at=_ago(0),
            updated_at=_ago(0),
            steps=package_steps("ap-intake", 1),
        ),
        FlowDefinition(
            id="ap-corrections",
            name="AP correction capture",
            description=(
                "The flywheel, as a flow you can watch. When a clerk corrects "
                "an extracted invoice, the accounts-payable run emits the fact "
                "and carries on posting; this answers it, appending one "
                "raw→corrected pair per field the clerk moved to the "
                "correction set — keyed by the same canonical field paths the "
                "review screen highlights and the golden sets assert on. "
                "Decoupled on purpose: a payment must not wait on a knowledge "
                "write, and what the correction *teaches* is a governed "
                "decision somebody makes later, against this set. Nothing "
                "here promotes anything into the extraction prompt."
            ),
            team="platform-eng",
            status="active",
            version=1,
            tags=["flywheel", "ap", "corrections", "evals", "knowledge"],
            queues=[],
            workspace="ap-workspace",
            roi_minutes_per_run=0.0,
            model_budget_usd_per_run=0.0,
            authored_by="fde:m.suter",
            created_at=_ago(0),
            updated_at=_ago(0),
            steps=package_steps("ap-corrections", 1),
        ),
        FlowDefinition(
            id="calendar-sync",
            name="Calendar sync",
            description=(
                "Mirrors the office suite's calendars, one row per calendar "
                "per event — which is what the provider actually stores: a "
                "meeting with four attendees is four blocks with four ids, "
                "four reminders and four responses, and collapsing them would "
                "invent a shared object no provider has. What the copies agree "
                "on is expressed as links to the same people. An attendee the "
                "workforce does not contain is counted, never minted: an "
                "invitation is not evidence of employment."
            ),
            team="platform-eng",
            status="active",
            version=1,
            tags=["sync", "calendars", "office", "mirror"],
            queues=[],
            workspace="ap-workspace",
            roi_minutes_per_run=0.0,
            model_budget_usd_per_run=0.0,
            authored_by="fde:m.suter",
            created_at=_ago(60),
            updated_at=_ago(0),
            steps=package_steps("calendar-sync", 1),
        ),
        FlowDefinition(
            id="mail-sync",
            name="Mail sync",
            description=(
                "Mirrors mailboxes and the messages in them as evidence that a "
                "conversation happened and who was in it — headers, addressing "
                "and an opening preview, and deliberately not a mail store. "
                "Bodies never reach the wire and the preview is capped again "
                "on the way in, so a provider that called a whole body a "
                "preview cannot smuggle one past. Correspondents bind by "
                "address against the people already in the graph, inside the "
                "company and out; an address belonging to nobody is counted "
                "and left on the message, because a counterparty book that "
                "grew a Person per unrecognized sender would fill with robots."
            ),
            team="platform-eng",
            status="active",
            version=1,
            tags=["sync", "mail", "office", "correspondence", "evidence"],
            queues=[],
            workspace="ap-workspace",
            roi_minutes_per_run=0.0,
            model_budget_usd_per_run=0.0,
            authored_by="fde:m.suter",
            created_at=_ago(2),
            updated_at=_ago(0),
            steps=package_steps("mail-sync", 1),
        ),
        FlowDefinition(
            id="identity-sweep",
            name="Identity sweep",
            description=(
                "Scores the counterparty book against itself for merges no "
                "single feed can see: a stale ERP customer under an old "
                "trading name, a contact re-typed off a badge list, the firm "
                "we sell to that is also the firm we buy from. Evidence is "
                "domains, phone numbers, the anchor word of a name and a "
                "shared employer — no model call, because the whole value of "
                "the number is that a steward can read the evidence beside it "
                "and disagree. Every survivor is written as a typed identity "
                "link at `in_review` carrying its confidence, its signals and "
                "what an un-merge restores; a proposal under the governed "
                "confidence bar also raises a Task. **Nothing merges.** No "
                "canonical object is touched on any path — the flow writes "
                "edges and only edges — and the Action Gateway refuses to act "
                "on a proposal until a human confirms it."
            ),
            team="platform-eng",
            status="active",
            version=1,
            tags=["mdm", "identity", "entity-resolution", "windowed",
                  "governed-knowledge"],
            queues=["data-stewardship"],
            workspace="ap-workspace",
            roi_minutes_per_run=0.0,
            model_budget_usd_per_run=0.0,
            authored_by="fde:m.suter",
            created_at=_ago(1),
            updated_at=_ago(0),
            steps=package_steps("identity-sweep", 1),
        ),
        FlowDefinition(
            id="disagreement-sweep",
            name="Disagreement sweep",
            description=(
                "Correlates the CRM's won deals against the ERP's held orders "
                "and records where two systems are holding one commitment and "
                "saying incompatible things about it — a deal closed won on "
                "the 14th whose order is still sitting at Pending Approval. "
                "Neither system is wrong and neither can see the other; the "
                "disagreement exists only in the join, and the join exists "
                "only because both feeds reify onto one counterparty book. "
                "The evidence is a shared counterparty, the two states, a "
                "governed time window, and at least one corroborator — name "
                "proximity against the order memo, an order that is a "
                "plausible fraction of its deal, or being the only candidate "
                "in the window. No model call: every signal is a date, a "
                "ratio or a state, and the value of the number is that a "
                "controller can read the evidence beside it and disagree. "
                "**Nothing is released.** The finding is a typed link at "
                "`standing` between two rows that already exist; no object is "
                "touched and no external system is written to. A counterparty "
                "join that rests on an unconfirmed merge is refused and "
                "counted as blocked on identity rather than guessed at."
            ),
            team="platform-eng",
            status="active",
            version=1,
            tags=["state", "disagreement", "sales-orders", "windowed",
                  "governed-knowledge"],
            queues=["order-desk"],
            workspace="ap-workspace",
            roi_minutes_per_run=0.0,
            model_budget_usd_per_run=0.0,
            authored_by="fde:m.suter",
            created_at=_ago(0),
            updated_at=_ago(0),
            steps=package_steps("disagreement-sweep", 1),
        ),
        FlowDefinition(
            id="credential-tracking",
            name="Credential tracking",
            description=(
                "The compliance clock: five spreadsheet sub-processes and "
                "three Excel trackers answered by one windowed pass over the "
                "credential and inspection rows the graph already holds. A "
                "timer closes a window — nothing arrives — and "
                "`certification-alert-policy` decides what an open window "
                "means: the 60/30/15 notice stages, the 30-day service-level "
                "floor a stage list is checked against, the five categories "
                "tracked, the seven-year retention horizon and the desk. A "
                "pure Rule decides which stage each row is owed and **a stage "
                "already sent this cycle is not re-sent**, which is what makes "
                "a daily cadence safe: a window with nothing due writes "
                "nothing, sends nothing and raises nothing. One guarded Action "
                "stamps the whole window — never one per row. An asset whose "
                "register carries no next-due date is excluded and *counted*, "
                "which is the skid-steer exclusion expressed as a fact about "
                "the asset rather than a list of machine types. The four "
                "exception classes — an unreadable date, an unresolvable "
                "holder, a leaver still on the active roster, a record past "
                "the horizon — go to `hr-safety` as at most four aggregate "
                "Tasks, never one per row. **Nothing is deleted:** a record "
                "past seven years is reported for manual review, which is the "
                "customer's own rule. And no `Last Notified` column, no "
                "`Last Run Status` cell and no workbook is read or written "
                "anywhere — that state is the graph rows and this run's "
                "journal."
            ),
            team="platform-eng",
            status="active",
            version=1,
            tags=["compliance", "credentials", "inspections", "windowed",
                  "governed-knowledge", "dot"],
            queues=["hr-safety"],
            workspace="compliance",
            roi_minutes_per_run=0.0,
            model_budget_usd_per_run=0.0,
            authored_by="fde:m.suter",
            created_at=_ago(0),
            updated_at=_ago(0),
            steps=package_steps("credential-tracking", 1),
        ),
        # -- the plant spine ------------------------------------------------
        #
        # Two flows and one seam. The window is a pass over the job book that
        # commits nothing; the extraction is a case that commits the demand
        # rows a plant's day is built out of. They are separate flows because
        # they are woken by different kinds of fact and answered at different
        # grain — the shape-is-read-off-the-Trigger rule, made visible on the
        # fleet page.
        FlowDefinition(
            id="plant-eligibility-sweep",
            name="Plant eligibility sweep",
            description=(
                "The 11:00 window: which of the jobs the production system "
                "holds are actually in play. Nothing arrives — a clock closes "
                "a window over the job book — and every clause that judges it "
                "is a line on a governed page: load date on or after today, a "
                "week position in the governed set, released, not held, and a "
                "load date the plant is actually open on, counted against a "
                "business-day calendar and a holiday list somebody can edit. "
                "A job that fails a clause is excluded for **that** named "
                "reason and counted, so \"why was 260118 not scheduled\" "
                "answers with a clause rather than an opinion. **An eligible "
                "set of zero raises nothing** — the governed page says a slow "
                "week is informational — and the only Task it can raise is the "
                "aggregate for a job book that is *empty*, which is a feed "
                "that stopped and goes to the operations desk. Each eligible "
                "job then becomes one `JobEligible` fact answered by a full "
                "extraction before the window is acknowledged, bounded by a "
                "governed jobs-per-window figure with the remainder reported "
                "as deferred; and after them, one `DepartmentDayOpened` per "
                "department and business day the extractions produced demand "
                "for. It writes nothing at all: the verdict is the journal."
            ),
            team="plant-operations",
            status="active",
            version=1,
            tags=["plant", "scheduling", "eligibility", "windowed",
                  "governed-knowledge"],
            queues=["platform-operations"],
            workspace="plant-schedule",
            roi_minutes_per_run=0.0,
            model_budget_usd_per_run=0.0,
            authored_by="fde:m.suter",
            created_at=_ago(0),
            updated_at=_ago(0),
            steps=package_steps("plant-eligibility-sweep", 1),
        ),
        FlowDefinition(
            id="shipping-list-extraction",
            name="Shipping list extraction",
            description=(
                "One eligible job's seven-page shipping list becomes ten "
                "departments' worth of demand — and the only ModelCall pair in "
                "the plant spine. It gathers every governed value in one step, "
                "finds the job's ticket under the governed cut-sheet root "
                "(which is what tells the floor's copy from the office's), "
                "fetches the bytes under their content hash, and makes **one** "
                "metered pass over the pages with a confidence per field. The "
                "prompt forbids the model to compute an hour: the arithmetic "
                "is a governed page a superintendent can edit, and a figure a "
                "model invented would be a hidden second implementation of it. "
                "The grids are then checked against their own printed totals "
                "and the computed hours against each department's expected "
                "band, and the union of what is left in doubt — a low "
                "confidence, a grid that does not add up, a required field "
                "that is absent, a figure outside its band — is what one "
                "supervisor is asked about, with a box on each field from a "
                "second cheap call that is **skipped entirely** when there is "
                "nothing to locate. A correction re-runs both checks rather "
                "than overriding them. Then one guarded write: a `JobTask` per "
                "department whose page family is present, each naming the "
                "formula and the asset revision that produced its hours. A job "
                "with no shipping list is a count, not a Task; a department "
                "with no page family is skipped, and the job is not."
            ),
            team="plant-operations",
            status="active",
            version=1,
            tags=["plant", "vision", "extraction", "scheduling",
                  "governed-knowledge"],
            queues=["plant-supervisor"],
            workspace="plant-schedule",
            roi_minutes_per_run=0.0,
            # Two calls at most, and usually one: a multi-page read plus a
            # narrow locate that a clean ticket never makes.
            model_budget_usd_per_run=0.40,
            authored_by="fde:m.suter",
            created_at=_ago(0),
            updated_at=_ago(0),
            steps=package_steps("shipping-list-extraction", 1),
        ),
        FlowDefinition(
            id="department-scheduling",
            name="Department scheduling",
            description=(
                "One plant department's day, filled out of the graph and "
                "signed by a person — and the flow that makes Cumulus the "
                "**system of record** for a schedule rather than a thing that "
                "writes into somebody's workbook. Woken once per (department, "
                "business day) by the fact the eligibility sweep raises after "
                "its extractions have landed demand. Everything it reads "
                "about the day it reads out of **our own graph**: the "
                "`ScheduleDay` "
                "a previous window opened, its rows — specifically the rows a "
                "supervisor's correction stamped `placed_by: human`, which it "
                "must never overwrite — and the headcount, which is the count "
                "of active employees `member_of` the department's team rather "
                "than a number somebody typed in every morning. There is no "
                "spreadsheet here, because there is no spreadsheet anywhere. "
                "Each share is judged against the governed clauses in the "
                "page's own order — still eligible, upstream department done, "
                "inside the lead-time and farthest-out windows, on a day the "
                "plant is open — and what fails is **deferred with its "
                "reason, never a failure**: nothing sits warm waiting for a "
                "saw, because the next sweep re-raises the day. The fill then "
                "adds shares in load-date order until the governed goal max, "
                "or until the computed incentive crosses zero for the two "
                "departments the page names, inside the fudge band and with "
                "the absolute max as the hard stop — and going past the "
                "absolute max by within the fudge is explicitly not an "
                "exception. Below capacity is reported and never forced. One "
                "guarded write puts the day at `pending_approval`; one Task "
                "carries the rows, the total against the limits, the "
                "incentive written out as its own arithmetic, everything "
                "deferred and why, and a headcount staleness warning that "
                "warns and proceeds; and the run suspends there, because that "
                "approval is a **permanent control point**. Approving is the "
                "instant the day becomes exportable, and the CSV export "
                "refuses a day nobody has signed."
            ),
            team="plant-operations",
            status="active",
            version=1,
            tags=["plant", "scheduling", "capacity", "windowed",
                  "governed-knowledge", "system-of-record"],
            queues=["plant-supervisor"],
            workspace="plant-schedule",
            roi_minutes_per_run=0.0,
            model_budget_usd_per_run=0.0,
            authored_by="fde:m.suter",
            created_at=_ago(0),
            updated_at=_ago(0),
            steps=package_steps("department-scheduling", 1),
        ),
        FlowDefinition(
            id="field-documents",
            name="Field documents",
            description=(
                "The job packet, as one windowed pass: a dispatcher that "
                "watched a portal for scheduled jobs, a performer that copied "
                "four PDFs into a per-foreman folder tree, and a weekly "
                "reconciliation that went looking for the jobs that had moved "
                "between crews, collapsed into one standing question asked of "
                "the graph — *which scheduled job inside the horizon does not "
                "have its four documents linked at their latest revision?* "
                "**No ModelCall at all**: the fact the retired crew-schedule "
                "PDF carried is a column on the job. Two Triggers wake it, "
                "because either half can be the new fact — a job becoming "
                "`Scheduled`, or a revision arriving for a job already "
                "scheduled. `field-documents-policy` decides what a packet "
                "is: the four types and their filename patterns, the folder "
                "root, the statuses and the horizon that put a job in scope, "
                "the window bound, the naming convention with its stopwords "
                "and collision rule, the recurrence threshold and the desk. "
                "*The topmost one is the most recent* is not a rule this "
                "system can keep, so the current revision is a comparison "
                "rather than a position. One guarded Action lands the whole "
                "window — never one per job — and at most two aggregate Tasks "
                "reach `field-operations`: the jobs nothing has been filed "
                "for past the recurrence threshold, and the jobs whose crew "
                "id resolves to no team, which is the subcontractor case as "
                "**one identity question** rather than one document question "
                "per job. **No folder is created, copied, moved or deleted "
                "anywhere:** a packet is a graph fact, a superseded revision "
                "keeps its row and its withdrawn edge, and \"no longer this "
                "crew\'s job\" is an ended validity rather than a directory "
                "somebody has to remove."
            ),
            team="platform-eng",
            status="active",
            version=1,
            tags=["field", "crews", "documents", "jobs", "windowed",
                  "governed-knowledge"],
            queues=["field-operations"],
            workspace="field-documents",
            roi_minutes_per_run=0.0,
            model_budget_usd_per_run=0.0,
            authored_by="fde:m.suter",
            created_at=_ago(0),
            updated_at=_ago(0),
            steps=package_steps("field-documents", 1),
        ),
        FlowDefinition(
            id="order-release",
            name="Order release",
            description=(
                "The one flow that changes a system Cumulus does not own. A "
                "controller approves a disagreement the sweep found, and this "
                "runs per case: it re-reads the order in the ERP, checks the "
                "move against the closed table of writes this system is "
                "permitted to make, journals the intent before the request "
                "leaves, sends one PATCH, reads the record back, and only "
                "then records the finding as resolved. The estate decides "
                "whether the write took — never the 200. **Two Actions, two "
                "regimes**: releasing is a decision a human authorized, and "
                "what the ERP says afterwards is an observation, landed under "
                "the ERP's own authority. Cumulus never writes an authority "
                "claim it did not read back, because a human claim on an "
                "order's status would outrank the ERP forever and freeze the "
                "order. Five things can be true when it looks: the order is "
                "still held (release it), it already holds the released value "
                "and this system asked for that (nothing to do), it holds it "
                "and nobody here asked (somebody else released it), it has "
                "moved somewhere this system may not write from (ask a "
                "person), or the table does not permit the move at all "
                "(refused before any request exists). **There is no undo**: "
                "moving an order back is a second decision."
            ),
            team="platform-eng",
            status="active",
            version=order_release.DEFAULT_VERSION,
            tags=["write-back", "sales-orders", "estate", "governed-write",
                  "per-case"],
            queues=["order-desk"],
            workspace="ap-workspace",
            roi_minutes_per_run=0.0,
            model_budget_usd_per_run=0.0,
            authored_by="fde:m.suter",
            created_at=_ago(0),
            updated_at=_ago(0),
            steps=package_steps("order-release", order_release.DEFAULT_VERSION),
        ),
        FlowDefinition(
            id="psa-sync",
            name="Delivery book sync",
            description=(
                "The delivery organization's own week: engagements, the "
                "phases they are broken into, and the hours booked against "
                "them. Clients reify onto the counterparty book through the "
                "same resolution the CRM and ERP feeds use — the third voice "
                "on those companies, and the informal one, since a PSA client "
                "name is what a coordinator typed after a phone call. "
                "Projects and phases bind; time entries are summed onto both "
                "and never stored as rows. Two things this flow deliberately "
                "does not do: it does not mirror the PSA's tasks (their rows "
                "arrive as a three-column lookup so an hour can find its "
                "phase, and nothing about them survives), and it does not "
                "resolve a person — `S. Hoffmann` lands as a string, because "
                "joining that to the HRIS is a fuzzy match with a steward at "
                "the end of it."
            ),
            team="platform-eng",
            status="active",
            version=1,
            tags=["sync", "psa", "delivery", "projects", "counterparties"],
            queues=["data-stewardship"],
            workspace="ap-workspace",
            roi_minutes_per_run=0.0,
            model_budget_usd_per_run=0.0,
            authored_by="fde:m.suter",
            created_at=_ago(1),
            updated_at=_ago(0),
            steps=package_steps("psa-sync", 1),
        ),
        FlowDefinition(
            id="job-sync",
            name="Production job sync",
            description=(
                "What the plant is building, mirrored from the production "
                "system: the job, its dimensions, the date it has to leave the "
                "yard, the crew it is assigned to, and ten departments' worth "
                "of completion flags. Two decisions carry this flow. The "
                "customer each job names is **resolved** through the same "
                "policy the CRM, the sell side and the PSA go through — a name "
                "is fuzzy, `Hoffman` and `Hoffmann` are one farm, and an "
                "ambiguous one raises a steward Task while the job lands "
                "anyway. The crew is **bound**, not matched: the ERP's crew id "
                "is the accounting code on the HRIS crew department, and an id "
                "that matches nothing is a count rather than a failure, "
                "because the subcontractor crew has no department and never "
                "will. A job that changes crews **withdraws** the old edge "
                "rather than deleting it, so which crew has it now and who "
                "held it before are one traversal — which is what retires the "
                "weekly folder reconciliation the RPA design needed. The "
                "production system's own words — its `Yes`/`No` flags and its "
                "week labels — are translated here against the governed "
                "vocabulary table, and the landed row keeps the strings "
                "alongside, so an edit to that table re-applies without "
                "re-fetching a thing."
            ),
            team="platform-eng",
            status="active",
            version=1,
            tags=["sync", "erp", "production", "jobs", "crews"],
            queues=["data-stewardship"],
            workspace="ap-workspace",
            roi_minutes_per_run=0.0,
            model_budget_usd_per_run=0.0,
            authored_by="fde:m.suter",
            created_at=_ago(1),
            updated_at=_ago(0),
            steps=package_steps("job-sync", 1),
        ),
        FlowDefinition(
            id="credential-sync",
            name="Credential sync",
            description=(
                "Everything a person holds that expires, from the two files "
                "that track it: completed training and certifications on one "
                "side, CDLs, medical cards and clearances on the other. One "
                "type for both, because a thing that expires and gets renewed "
                "behaves identically whichever department owns the "
                "spreadsheet — and one driver row becomes one credential per "
                "date it carries, because four permissions cannot share a "
                "key. A **bind** feed: the holder is joined by work email, "
                "which is exact, so nothing is reified and nobody "
                "adjudicates. It derives no status: `valid | expiring | "
                "expired` is the compliance sweep's verdict against the "
                "governed notice stages, and a feed that wrote its own input "
                "into that field would make the sweep read its own output."
            ),
            team="platform-eng",
            status="active",
            version=1,
            tags=["sync", "hris", "compliance", "credentials", "safety"],
            queues=[],
            workspace="ap-workspace",
            roi_minutes_per_run=0.0,
            model_budget_usd_per_run=0.0,
            authored_by="fde:m.suter",
            created_at=_ago(1),
            updated_at=_ago(0),
            steps=package_steps("credential-sync", 1),
        ),
        FlowDefinition(
            id="equipment-sync",
            name="Equipment sync",
            description=(
                "The fleet the business is answerable for, and the two dates "
                "that say whether a unit is legal to run. Its one interesting "
                "decision is an absence: **a null next-due date is the source "
                "saying this unit carries no tracked inspection**, derived "
                "once on the way in, which is what stops a skid steer that "
                "never needed a sticker from being reported as overdue "
                "forever. The examination is written as a part of the unit "
                "from the dates the asset row holds — no result, no inspector, "
                "no notes, because the source says nothing about what was "
                "found and a `pass` inferred from a due date would invent the "
                "finding a certificate carries."
            ),
            team="platform-eng",
            status="active",
            version=1,
            tags=["sync", "erp", "equipment", "compliance", "fleet"],
            queues=[],
            workspace="ap-workspace",
            roi_minutes_per_run=0.0,
            model_budget_usd_per_run=0.0,
            authored_by="fde:m.suter",
            created_at=_ago(1),
            updated_at=_ago(0),
            steps=package_steps("equipment-sync", 1),
        ),
        FlowDefinition(
            id="answer-question",
            name="Answer a question",
            description=(
                "The last ungoverned ModelCall in the system, brought inside. "
                "Every question asked of the corpus — the console's /ask, the "
                "A2A front door, the Employee Hub's HR agent — is a fact "
                "(`QuestionAsked`, or `HrQuestionAsked` carrying the "
                "employee's own context) that starts one run, and the planner "
                "loop runs inside it unchanged. **One ModelCall step per "
                "question, not per turn**: a planner that calls four tools "
                "makes five model turns to answer once, and five rows would "
                "make the fleet's call count a fact about the planner's "
                "verbosity rather than about how often anybody asked. The "
                "turns are summed into that one step's usage. With no "
                "generative backend configured the run still happens and the "
                "step is a transform, because a `modelcall` row for a call "
                "nobody made would inflate the very count this flow exists to "
                "make truthful."
            ),
            team="platform-eng",
            status="active",
            version=1,
            tags=["knowledge", "answering", "a2a", "hr-agent",
                  "governed-knowledge"],
            queues=[],
            roi_minutes_per_run=0.0,
            # A governed assumption, and a small one: the planner's budget is
            # already bounded by `answer_max_steps`, so this is the tile that
            # notices when a question costs more than a question should.
            model_budget_usd_per_run=0.05,
            authored_by="fde:m.suter",
            created_at=_ago(1),
            updated_at=_ago(0),
            steps=package_steps("answer-question", 1),
        ),
    ]


def _seed_version_history(defn: FlowDefinition) -> list[FlowVersion]:
    """The live version, plus every other version shipped as a package.

    That is the whole of a new flow's history, because it is the whole of what
    exists. This used to mint two back-dated `archived` revisions per flow —
    the active steps with an earlier `@revN` pinned on the prompt — plus, for
    expense reimbursement, a fabricated `add-duplicate-check` draft carrying a
    step nobody wrote. The version picker then offered five revisions of a flow
    that had shipped one, and the canvas diffed a draft against a change no
    engineer had proposed.

    A version is real when a `v<N>/` package for it is present in the deployed
    code: above the live pointer it is a candidate the console can activate,
    below it an older package still deployed. So a flow that has shipped once
    has one version, and accounts payable has the v6/v7/v8 set it actually
    ships.
    """
    from . import tasks as process_packages

    versions = [
        FlowVersion(
            version=defn.version,
            label=f"v{defn.version}",
            status="active",
            steps=[step.model_copy(deep=True) for step in defn.steps],
            model_budget_usd_per_run=defn.model_budget_usd_per_run,
            notes="Live.",
            authored_by=defn.authored_by,
            created_at=defn.updated_at,
        )
    ]
    for number, module in sorted(process_packages.shipped_versions(defn.id).items()):
        if number == defn.version:
            continue
        versions.append(
            FlowVersion(
                version=number,
                label=module.LABEL,
                status="draft" if number > defn.version else "archived",
                steps=[step.model_copy(deep=True) for step in module.STEPS],
                model_budget_usd_per_run=defn.model_budget_usd_per_run,
                notes=module.NOTES,
                authored_by=defn.authored_by,
                created_at=utcnow(),
            )
        )
    return sorted(versions, key=lambda v: v.version)


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


def _with_shipped_versions(stored: FlowDefinition) -> FlowDefinition | None:
    """The stored definition, plus any shipped version its history lacks.

    Returns None when there is nothing to add. New rows land as drafts when
    they sit above the live version (candidates the console can activate) and
    as archived when below it (an old package still deployed).
    """
    from . import tasks as process_packages

    shipped = process_packages.shipped_versions(stored.id)
    if not shipped:
        return None
    history = stored.version_history()
    known = {v.version for v in history}
    added = False
    for number, module in sorted(shipped.items()):
        if number in known:
            continue
        history.append(
            FlowVersion(
                version=number,
                label=module.LABEL,
                status="draft" if number > stored.version else "archived",
                steps=[step.model_copy(deep=True) for step in module.STEPS],
                model_budget_usd_per_run=stored.model_budget_usd_per_run,
                notes=module.NOTES,
                authored_by=stored.authored_by,
                created_at=utcnow(),
            )
        )
        added = True
    if not added:
        return None
    return stored.model_copy(
        update={"versions": sorted(history, key=lambda v: v.version)}
    )


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

        if defn.version > stored.version:
            stored = service.update(_upgraded(stored, defn))
            log.info(
                "Flow definition upgraded from the repository",
                extra={
                    "flowId": defn.id,
                    "from": stored.version,
                    "to": defn.version,
                },
            )
            updated += 1

        # Independent of the version bump: a redeploy that *ships* a new
        # version package (without changing what is live) must still put that
        # version in the stored history, or the console could never see it to
        # make it live.
        patched = _with_shipped_versions(stored)
        if patched is not None:
            service.update(patched)
            log.info(
                "Shipped flow versions recorded",
                extra={"flowId": defn.id},
            )
            updated += 1

    if created or updated:
        log.info(
            "Seeded flow definitions",
            extra={"count": created, "upgraded": updated},
        )
    return created
