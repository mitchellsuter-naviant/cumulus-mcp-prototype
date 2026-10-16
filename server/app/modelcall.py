"""The governed ModelCall: one client, one ledger, one row per invocation.

The doctrine is blunt about this one. There is no Agent primitive — the Flow is
the agent — and the thing that gets governed is the **ModelCall**. Before this
module that was an aspiration held up by repetition: four hand-rolled
`genai.Client(vertexai=True, …)` instances in four files, each switching on the
same three settings, three of them metering their token counts onto a
`StepRecord` and the fourth — the embedder — metering nothing at all. A
governed operation with four implementations is four chances for one of them to
stop being governed, and one of them already had.

So this is the ModelCall's chokepoint in the way `gateway.py` is the write's.
It builds the client **once per configuration**, **meters** what the call
consumed, **prices** it against the same governed rate sheet the run journal
prices against, and **appends a record**: which model ran, under which run and
step, on which governed prompt, what it cost, how long it took, and whether it
worked.

**References, never bytes.** A row names the prompt at the revision it was read
and the case it was about; it never holds the prompt text or the document. A
ledger that copied its inputs would be a second, ungoverned copy of every
invoice a customer ever submitted, kept under a retention policy nobody wrote.

**An unpriced call reads as unpriced.** Pricing comes from `model-pricing`, the
same governed asset `RunContext.step(meter=…)` prices against, so the two
records of one call agree by construction. A model with no row costs 0.0, and
zero means *nobody knows the rate*, never *this was free* — `flows.cost_basis`
is what keeps those two zeroes apart on a page, and it can only do that because
nothing here ever guesses a rate.

**A failed call is still a call.** It consumed tokens, it took latency, and it
is the row an operator most wants when a bill moves. `refused` is kept apart
from `failed` for `gateway.LedgerEntry`'s reason: one is the model declining to
answer and the other is the world breaking, and an operator watching a spike
needs to know which of those is happening.

The ledger is a **sibling** of the curation ledger rather than part of it — its
own collection, append-only, keyed by run/step/case. That one records what a
person decided; this one records what a model was asked. Neither belongs in the
graph: a ModelCall is machinery, and the business facts a run wrote are what
reach the map.

The **journal still rolls costs up** exactly as it did. This ledger is
additional truth, not a replacement: `ModelUsage` still comes back to the
caller so `meter=` keeps filling `StepRecord.model/.tokens/.cost_usd`, and a
step that made three planner turns is one journal row and three ledger rows
that sum to it.
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Iterator, Literal, Protocol

from pydantic import BaseModel, Field

from .models import utcnow
from .runs import ModelUsage

log = logging.getLogger("cumulus.modelcall")

#: What became of one invocation. `succeeded` is the model answering, whatever
#: the answer was worth; `refused` is the model declining to; `failed` is the
#: call not completing at all.
Outcome = Literal["succeeded", "refused", "failed"]

#: Finish reasons that mean the model declined rather than answered. Matched on
#: the bare name because the SDK has spelled these as plain strings and as
#: `FinishReason.SAFETY` in different versions, and a refusal filed as a success
#: is the one misreading that makes a governance ledger useless — it is exactly
#: the row somebody is looking for.
#:
#: `MAX_TOKENS` is deliberately absent: a truncated answer is an answer, and the
#: caller can see that it is short.
_REFUSALS = frozenset({
    "SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "RECITATION", "SPII",
    "IMAGE_SAFETY", "UNEXPECTED_TOOL_CALL",
})


class RateSheet(Protocol):
    """`flows.ModelRates`, duck-typed to keep the import one-way.

    `flows` reads runs and dispatch; nothing here needs the rest of it, and a
    real import would tie the one module every model call passes through to the
    fleet console's dependency tree.
    """

    def priced(self, usage: ModelUsage) -> ModelUsage: ...


# ---------------------------------------------------------------------------
# Which backend a reader is allowed to be
# ---------------------------------------------------------------------------

#: The configurations under which a fabricating reader is the *answer* rather
#: than a consolation prize. `none` is the historical spelling and what the
#: test suite and every local box run on; `stub` says the same thing out loud.
#: Both are choices somebody made, which is the whole distinction this set
#: exists to draw.
STUB_BACKENDS = frozenset({"none", "stub"})


class UnusableModelBackend(Exception):
    """A reader was asked for a backend nobody configured on purpose.

    Raised rather than quietly answered with the stub, and that is the point.
    A stub reader invents an extraction out of a hash: under `MODEL_BACKEND=none`
    that is a local dev convenience, and under a misspelled or failed `vertex`
    it is a deployment fabricating business records while reporting healthy.
    The two are indistinguishable downstream — same type, same shape, same
    ledger row — so they have to be told apart *here*, at construction, or not
    at all.
    """

    def __init__(self, backend: str, kind: str) -> None:
        super().__init__(
            f"MODEL_BACKEND={backend!r} names no {kind} this deployment can "
            f"build. Use `vertex`, or say one of {sorted(STUB_BACKENDS)} "
            "explicitly to run on the stub."
        )
        self.backend = backend


def require_stub_opt_in(backend: str, kind: str) -> None:
    """Guard the fall-through in a reader factory. See `UnusableModelBackend`."""
    if backend not in STUB_BACKENDS:
        raise UnusableModelBackend(backend, kind)


# ---------------------------------------------------------------------------
# Where a call happened
# ---------------------------------------------------------------------------


class ModelCallContext(BaseModel):
    """Where a call happened, and what it ran on.

    Empty is a real answer, not a gap: an embedding computed by the backfill
    thread happened under no run at all, and saying so is honest where naming
    whichever run happened to be on the stack would be a fabrication with a
    plausible shape.
    """

    flow_id: str = ""
    flow_version: int = 0
    run_id: str = ""
    step_id: str = ""
    case_ref: str = ""
    #: The governed knowledge the step declared it read, at the revisions it
    #: read them. Carried so a caller can name the one its call actually ran on
    #: without reading the asset a second time — see `governed_ref`.
    read_set: list[str] = Field(default_factory=list)
    #: The governed instruction this call runs on, once a caller has picked it
    #: out of the read-set. See `reading`.
    prompt_ref: str = ""


_NO_CONTEXT = ModelCallContext()
_CURRENT: ContextVar[ModelCallContext] = ContextVar(
    "cumulus_modelcall_context", default=_NO_CONTEXT
)


@contextmanager
def under(context: ModelCallContext) -> Iterator[ModelCallContext]:
    """Put a run's coordinates in scope for every ModelCall made inside it.

    Ambient rather than a parameter because of where the two ends sit: the
    coordinates belong to the flow body, and the call is made three frames down
    inside a reader that has — correctly — never heard of a Flow. Threading a
    context through `InvoiceReader.read` would put the engine's vocabulary into
    the signature of every extractor anyone ever writes, and the stub reader
    would have to carry a parameter it exists precisely to ignore.

    Reset on the way out, always. A run that has finished must not lend its id
    to whatever the thread does next: a row naming the wrong run is worse than
    one naming none, because the second is visibly a gap and the first is not.
    """
    token = _CURRENT.set(context)
    try:
        yield context
    finally:
        _CURRENT.reset(token)


def current() -> ModelCallContext:
    """The coordinates in scope, or the empty ones outside any run."""
    return _CURRENT.get()


@contextmanager
def reading(prompt_ref: str) -> Iterator[ModelCallContext]:
    """The governed instruction the ModelCalls inside this block run on.

    Its own scope rather than an argument on `Reader.read` for the reason the
    run coordinates are ambient: a prompt revision is governance vocabulary,
    and a reader — including the stub, which is where it would be most absurd
    — is machinery that should not have to carry it through a signature just so
    something four frames down can file it.
    """
    with under(current().model_copy(update={"prompt_ref": prompt_ref})) as ctx:
        yield ctx


def governed_ref(asset_id: str, context: ModelCallContext | None = None) -> str:
    """The revision of one governed asset that the current step declared it read.

    Taken off the step's own read-set rather than by reading the asset again.
    The flow read the prompt in an earlier step and named the revision on the
    metered one; a second read here would be a second answer to "which revision
    did this call run on", and the two would differ exactly when it mattered —
    the run where somebody published a new prompt mid-flight.
    """
    prefix = f"knowledge:{asset_id}"
    ctx = context if context is not None else current()
    return next(
        (
            ref for ref in ctx.read_set
            if ref == prefix or ref.startswith(f"{prefix}@")
        ),
        "",
    )


def metered(body: Callable[..., Any]) -> Callable[..., Any]:
    """A flow body whose ModelCalls know which run and step made them.

    Two coordinates, and neither is reachable from where the call is made. The
    **run** is on the record the engine hands the body. The **step** is known
    only at the `ctx.step(...)` call site — a reader is invoked from a closure
    with no idea which step it is running under, and the expense flow files one
    ModelCall per document, so a ledger without the step id cannot say which
    receipt a call read.

    Both are put in scope here, at registration, rather than in the engine: the
    engine deliberately knows nothing about pricing or this ledger, exactly as
    it knows nothing about the Action Gateway — a flow names its own governed
    operations. Only `modelcall` steps are wrapped, because they are the only
    steps that can make one.
    """

    def wrapped(ctx: Any, inputs: dict) -> Any:
        with under(ModelCallContext(
            flow_id=ctx.record.flow_id,
            flow_version=ctx.record.flow_version,
            run_id=ctx.record.id,
            case_ref=ctx.record.case_ref,
        )):
            return body(_StepScoped(ctx), inputs)

    return wrapped


class _StepScoped:
    """`RunContext`, with the step id in scope for the calls a step makes.

    A proxy rather than an attribute set on the engine's own object: this
    module does not own `RunContext`, and quietly rebinding one of its methods
    would be a change no reader of `runs.py` could see. Everything but `step`
    delegates, so a body cannot tell the difference and the engine keeps
    reading its own `replayed` counter off the object it created.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def step(
        self, step_id: str, name: str, kind: str,
        fn: Callable[[], Any] | None = None, **kwargs: Any,
    ) -> Any:
        if fn is None or kind != "modelcall":
            return self._inner.step(step_id, name, kind, fn, **kwargs)
        # The step's declared read-set travels with it: the governed prompt a
        # ModelCall runs on was read by an earlier step and named here, and this
        # is the only place both the step id and that list are in one frame.
        scoped = current().model_copy(update={
            "step_id": step_id, "read_set": list(kwargs.get("refs") or []),
        })

        def within() -> Any:
            with under(scoped):
                return fn()

        return self._inner.step(step_id, name, kind, within, **kwargs)


# ---------------------------------------------------------------------------
# The ledger
# ---------------------------------------------------------------------------


class ModelCallRecord(BaseModel):
    """One invocation, as the ledger keeps it. Written once, never edited."""

    id: str
    at_iso: str
    # Where it happened. Empty throughout means nothing governed by a Flow made
    # this call — the embedding backfill is the honest example.
    flow_id: str = ""
    flow_version: int = 0
    run_id: str = ""
    step_id: str = ""
    case_ref: str = ""
    #: The model, never the backend. "vertex" is a vendor's product line; it has
    #: no price, no downgrade rung and no place in this column.
    model: str = ""
    #: What the call was for — extraction, planning, embedding. The one field
    #: that makes a bill legible without joining anything: two flows on one
    #: model differ here and nowhere else.
    purpose: str = ""
    #: The governed prompt at the revision it was read. A reference, never the
    #: text: the prompt is knowledge and already has a home, and copying it here
    #: would fork it.
    prompt_ref: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    #: Priced from the governed rate sheet. 0.0 is *unpriced* — see the module
    #: docstring, and `flows.cost_basis` for how a page must say so.
    cost_usd: float = 0.0
    latency_ms: int = 0
    outcome: Outcome = "failed"
    #: Why, when the outcome is not `succeeded`. The refusal reason the provider
    #: gave, or the exception that ended the call.
    error: str = ""

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class ModelCallStore(Protocol):
    def append(self, record: ModelCallRecord) -> ModelCallRecord: ...
    def list(self, limit: int | None = None) -> list[ModelCallRecord]: ...
    def get(self, record_id: str) -> ModelCallRecord | None: ...


class InMemoryModelCallStore:
    """Append-only by omission: there is no update and no delete to call."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: list[ModelCallRecord] = []

    def append(self, record: ModelCallRecord) -> ModelCallRecord:
        with self._lock:
            self._items.append(record)
        return record

    def list(self, limit: int | None = None) -> list[ModelCallRecord]:
        with self._lock:
            items = list(reversed(self._items))  # newest first
        return items[:limit] if limit else items

    def get(self, record_id: str) -> ModelCallRecord | None:
        with self._lock:
            return next((r for r in self._items if r.id == record_id), None)


class FirestoreModelCallStore:
    def __init__(self, project: str | None, collection: str) -> None:
        from google.cloud import firestore  # deferred: not needed in dev mode

        self._col = firestore.Client(project=project).collection(collection)
        self._desc = firestore.Query.DESCENDING

    def append(self, record: ModelCallRecord) -> ModelCallRecord:
        self._col.document(record.id).set(record.model_dump(mode="json"))
        return record

    def list(self, limit: int | None = None) -> list[ModelCallRecord]:
        query = self._col.order_by("at_iso", direction=self._desc)
        out: list[ModelCallRecord] = []
        for doc in query.limit(limit or 500).stream():
            try:
                out.append(ModelCallRecord.model_validate(doc.to_dict()))
            except Exception:
                log.exception("Skipping unparseable model call", extra={"docId": doc.id})
        return out

    def get(self, record_id: str) -> ModelCallRecord | None:
        snap = self._col.document(record_id).get()
        return ModelCallRecord.model_validate(snap.to_dict()) if snap.exists else None


def build_modelcall_store(
    backend: str, project: str | None, collection: str
) -> ModelCallStore:
    if backend == "firestore":
        return FirestoreModelCallStore(project, collection)
    return InMemoryModelCallStore()


# ---------------------------------------------------------------------------
# The client
# ---------------------------------------------------------------------------

#: One google-genai client per configuration, for the whole process. Four call
#: sites used to build one each, and they are not free — a client resolves
#: credentials and holds a connection pool, so four pools against one endpoint
#: is four warm-ups buying concurrency a shared one already has. Keyed by
#: everything that changes the client's behaviour, so the embedder's retrying
#: client and the readers' non-retrying one stay two objects.
_WIRES: dict[tuple[str, str, int], Any] = {}
_WIRE_LOCK = threading.Lock()


def vertex_wire(project: str | None, location: str, retry_attempts: int) -> Any:
    """The google-genai client for one configuration, built at most once."""
    key = (project or "", location, retry_attempts)
    wire = _WIRES.get(key)
    if wire is not None:
        return wire
    with _WIRE_LOCK:
        wire = _WIRES.get(key)
        if wire is None:
            from google import genai  # deferred: only when Vertex is selected

            options = {}
            if retry_attempts:
                # The client does not retry by default — one attempt, zero
                # retries — which is the wrong posture for a bulk backfill
                # against a quota that answers 429.
                from google.genai.types import HttpOptions, HttpRetryOptions

                options["http_options"] = HttpOptions(
                    retry_options=HttpRetryOptions(attempts=retry_attempts)
                )
            wire = genai.Client(
                vertexai=True, project=project, location=location, **options
            )
            _WIRES[key] = wire
    return wire


class GovernedModelClient:
    """The only thing in this system allowed to call a model.

    Holds the wire, the ledger it appends to, and the price sheet it reads.
    Callers hand it their own request — `contents` and an SDK `config` — because
    what a receipt reader asks for and what the answer planner asks for
    genuinely differ, and flattening both into one signature would be a second,
    worse SDK. What a caller does not get is the ability to make the call
    without a record of it.
    """

    def __init__(
        self,
        wire: Any,
        store: ModelCallStore,
        rates: Callable[[], RateSheet],
        *,
        serialize: bool = False,
    ) -> None:
        self._wire = wire
        self.store = store
        self._rates = rates
        # The google-genai client is not documented as thread-safe, and the
        # embedding backfill runs on its own thread alongside query traffic.
        # Only the embedder asks for this: the three generation call sites
        # already shared nothing and ran concurrently, so serializing them on
        # the way through here would turn a shared client into a shared queue.
        self._lock = threading.Lock() if serialize else None

    # -- calls ---------------------------------------------------------------

    def generate(
        self,
        *,
        model: str,
        contents: Any,
        config: Any,
        purpose: str,
        prompt_ref: str = "",
        context: ModelCallContext | None = None,
    ) -> tuple[Any, ModelUsage]:
        """One generation call: the provider's response, and what it consumed.

        The `ModelUsage` comes back priced, because the caller almost never
        knows the rate and the ledger has just read the sheet to price its own
        row — handing back a different number would put two accounts of one
        call in one process.
        """
        started = time.monotonic()
        response: Any = None
        failure: Exception | None = None
        try:
            response = self._invoke(
                lambda: self._wire.models.generate_content(
                    model=model, contents=contents, config=config
                )
            )
        except Exception as exc:  # noqa: BLE001 — recorded, then re-raised
            failure = exc

        usage = self._priced(
            ModelUsage.from_vertex(model, response) if response is not None
            else ModelUsage(model=model)
        )
        outcome, error = _verdict(response, failure)
        scope = context if context is not None else current()
        self._append(
            model=model, purpose=purpose,
            prompt_ref=prompt_ref or scope.prompt_ref, usage=usage,
            latency_ms=int((time.monotonic() - started) * 1000),
            outcome=outcome, error=error, context=scope,
        )
        if failure is not None:
            # Re-raised after the append, for `gateway.PendingAction.commit`'s
            # reason: the call happened, it cost what it cost, and the row
            # saying so is exactly the one that used to be skipped.
            raise failure
        return response, usage

    def embed(
        self,
        *,
        model: str,
        contents: Any,
        config: Any,
        purpose: str,
    ) -> tuple[Any, ModelUsage]:
        """One embedding call, metered the way a generation call is.

        **No run coordinates, ever.** Embedding is corpus work — a backfill
        thread, a query encoder — and when it does happen inside a run it is
        retrieval underneath a step, not the step's own ModelCall: the step's
        `meter=` does not count it, so borrowing the step's id would leave the
        journal and the ledger disagreeing about what that step cost. Empty is
        the honest answer and it is a visible one.
        """
        started = time.monotonic()
        response: Any = None
        failure: Exception | None = None
        try:
            response = self._invoke(
                lambda: self._wire.models.embed_content(
                    model=model, contents=contents, config=config
                )
            )
        except Exception as exc:  # noqa: BLE001 — recorded, then re-raised
            failure = exc

        usage = self._priced(_embedding_usage(model, response))
        self._append(
            model=model, purpose=purpose, prompt_ref="", usage=usage,
            latency_ms=int((time.monotonic() - started) * 1000),
            outcome="failed" if failure is not None else "succeeded",
            error="" if failure is None else f"{type(failure).__name__}: {failure}",
            context=_NO_CONTEXT,
        )
        if failure is not None:
            raise failure
        return response, usage

    # -- internals -----------------------------------------------------------

    def _invoke(self, call: Callable[[], Any]) -> Any:
        if self._lock is None:
            return call()
        with self._lock:
            return call()

    def _priced(self, usage: ModelUsage) -> ModelUsage:
        """The governed rate sheet's answer, or none at all.

        Guarded because a price sheet that could not be read must degrade the
        *number*, never the record: a call nobody could price still happened,
        and 0.0 already means unpriced.
        """
        try:
            return self._rates().priced(usage)
        except Exception:  # noqa: BLE001 — a rate card must not fail a call
            log.exception("Could not price a model call; recording it unpriced")
            return usage

    def _append(
        self, *, model: str, purpose: str, prompt_ref: str, usage: ModelUsage,
        latency_ms: int, outcome: Outcome, error: str,
        context: ModelCallContext,
    ) -> ModelCallRecord:
        record = ModelCallRecord(
            id=f"mc_{secrets.token_hex(5)}",
            at_iso=utcnow().isoformat(),
            flow_id=context.flow_id, flow_version=context.flow_version,
            run_id=context.run_id, step_id=context.step_id,
            case_ref=context.case_ref,
            model=model, purpose=purpose, prompt_ref=prompt_ref,
            input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
            cost_usd=usage.cost_usd, latency_ms=latency_ms,
            outcome=outcome, error=error,
        )
        try:
            self.store.append(record)
        except Exception:  # noqa: BLE001 — logged loudly, never fatal
            # A store that will not take the row must not also swallow the
            # answer somebody is waiting for. Loud, because the alternative
            # reading of a quiet gap in this ledger is that nothing ran.
            log.exception(
                "ModelCall ledger append failed",
                extra={"model": model, "purpose": purpose,
                       "runId": context.run_id, "stepId": context.step_id},
            )
        log.log(
            logging.WARNING if outcome != "succeeded" else logging.INFO,
            "ModelCall",
            extra={
                "modelCallId": record.id, "model": model, "purpose": purpose,
                "runId": record.run_id, "stepId": record.step_id,
                "inputTokens": record.input_tokens,
                "outputTokens": record.output_tokens,
                "costUsd": record.cost_usd, "latencyMs": latency_ms,
                "outcome": outcome, "error": error,
            },
        )
        return record


def _verdict(response: Any, failure: Exception | None) -> tuple[Outcome, str]:
    """Succeeded, refused, or failed — read off the response, never assumed.

    Duck-typed for `ModelUsage.from_vertex`'s reason: one place knows what the
    SDK calls these fields, and a response shape it does not recognise reads as
    a plain success rather than raising inside the audit path.
    """
    if failure is not None:
        return "failed", f"{type(failure).__name__}: {failure}"
    blocked = getattr(getattr(response, "prompt_feedback", None), "block_reason", None)
    if blocked:
        return "refused", f"prompt blocked: {_enum_name(blocked)}"
    candidate = next(iter(getattr(response, "candidates", None) or []), None)
    reason = _enum_name(getattr(candidate, "finish_reason", None))
    if reason in _REFUSALS:
        return "refused", f"finish reason: {reason}"
    return "succeeded", ""


def _enum_name(value: Any) -> str:
    """`FinishReason.SAFETY`, `"SAFETY"` and a bare enum all read as SAFETY."""
    if value is None:
        return ""
    name = getattr(value, "name", None) or str(value)
    return name.rsplit(".", 1)[-1].upper()


def _embedding_usage(model: str, response: Any) -> ModelUsage:
    """What an embedding request consumed, as Vertex actually reports it.

    Not `usage_metadata`: an `EmbedContentResponse` has none. The count lives
    per vector in `statistics.token_count`, and only for encoders that report
    one — an encoder that reports nothing leaves zero, which is `unrecorded`
    rather than a claim that embedding is free. There is no output side at all,
    because the answer is a vector and vectors are not tokens.
    """
    if response is None:
        return ModelUsage(model=model)
    total = 0
    for embedding in getattr(response, "embeddings", None) or []:
        stats = getattr(embedding, "statistics", None)
        total += int(getattr(stats, "token_count", 0) or 0)
    return ModelUsage(model=model, input_tokens=total)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

#: Where a client built without one files its rows. Process-local, and nothing
#: serves it — which is the point: a model client that could skip the ledger
#: altogether is the one thing this module exists to prevent, so the fallback
#: for a script or a bare constructor is a ledger nobody reads rather than no
#: ledger at all.
_FALLBACK_STORE = InMemoryModelCallStore()


def _fallback_rates() -> RateSheet:
    """The governed sheet, reached the way the fleet console reaches it.

    Deferred for `flows.fleet_model_rates`' own reason: `deps` builds the
    service that holds the corpus, and importing it at module scope would tie
    every model call to the composition root's import order.
    """
    from .flows import fleet_model_rates

    return fleet_model_rates()


def vertex_client(
    project: str | None,
    location: str,
    *,
    store: ModelCallStore | None = None,
    rates: Callable[[], RateSheet] | None = None,
    retry_attempts: int = 0,
    serialize: bool = False,
) -> GovernedModelClient:
    """A governed client over the shared wire for this configuration."""
    return GovernedModelClient(
        vertex_wire(project, location, retry_attempts),
        store if store is not None else _FALLBACK_STORE,
        rates if rates is not None else _fallback_rates,
        serialize=serialize,
    )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


class ModelSpend(BaseModel):
    """One model's share of the bill."""

    calls: int = 0
    priced_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class ModelCallStats(BaseModel):
    """The bill, and how much of it is actually known.

    `cost_basis` is the field that matters most and the reason the priced
    counts are carried beside the totals: a $0.00 that means nothing ran and a
    $0.00 that means nobody recorded a price are opposite facts, and a console
    handed only the sum renders the second as the first.
    """

    calls: int = 0
    succeeded: int = 0
    refused: int = 0
    failed: int = 0
    priced_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    cost_basis: str = "no-model-calls"
    latency_ms_total: int = 0
    by_model: dict[str, ModelSpend] = Field(default_factory=dict)
    by_purpose: dict[str, int] = Field(default_factory=dict)


def summarize(records: list[ModelCallRecord]) -> ModelCallStats:
    """Roll a set of ledger rows up, counting priced calls separately."""
    from .flows import cost_basis  # deferred: see `_fallback_rates`

    stats = ModelCallStats()
    for record in records:
        stats.calls += 1
        setattr(stats, record.outcome, getattr(stats, record.outcome) + 1)
        stats.input_tokens += record.input_tokens
        stats.output_tokens += record.output_tokens
        stats.cost_usd = round(stats.cost_usd + record.cost_usd, 6)
        stats.latency_ms_total += record.latency_ms
        if record.cost_usd:
            stats.priced_calls += 1
        spend = stats.by_model.setdefault(record.model, ModelSpend())
        spend.calls += 1
        spend.input_tokens += record.input_tokens
        spend.output_tokens += record.output_tokens
        spend.cost_usd = round(spend.cost_usd + record.cost_usd, 6)
        if record.cost_usd:
            spend.priced_calls += 1
        stats.by_purpose[record.purpose] = stats.by_purpose.get(record.purpose, 0) + 1
    stats.cost_basis = cost_basis(stats.calls, stats.priced_calls)
    return stats
