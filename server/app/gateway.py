"""The Action Gateway: the one place a governed write is allowed to happen.

The doctrine's governance spine draws one picture — every actor funnels through
one control point that validates, permission-checks, and ledgers. Before this
module that picture was three half-mechanisms in three places: permission at
the HTTP boundary, provenance on `graph.GraphWriter`, and no ledger at all.
Three duties in three places is the same as none, because the hole is wherever
they fail to overlap, and a caller reaching `GraphService` directly was checked
by nobody.

So this is a chokepoint, not a policy engine. It **validates** — the ontology
check already lives in `graph.py`, and what the Gateway adds is that a
violation refuses the write here rather than being reported by a sweep three
weeks later. It **permission-checks per write**, against the Principal named at
the call site rather than at the route that led there, because a caller that
skips the boundary must not skip the check. And it **ledgers**, append-only,
naming both principals when the write happened under impersonation — which is
what `Principal.impersonated_by` exists for.

**The regime is declared, never inferred.** An observation, a decision and a
resolution are indistinguishable by their effect on the graph: all three end in
an `upsert`. So the Action carries its regime and the call site picks the
Action; reading it back off the shape of a write would be a guess recorded as
an audit fact.

**What the ledger does not guarantee.** It records that a change happened, who
committed it under what authority, what authorized it, and which refs it
touched — not how to undo it. The entity layer retains claims, so a graph write
is reconstructible from them; `service.py`'s asset `update()` overwrites
destructively, so a ledgered knowledge change could name the asset and never
restore its text. Nothing here is transactional either: a write refused or
broken halfway leaves what landed before it, which is why an entry that did not
commit still records the refs it managed to write.
"""

from __future__ import annotations

import logging
import secrets
import threading
from typing import Any, Callable, Literal, Protocol

from pydantic import BaseModel, Field

from .auth import Principal
from .graph import GraphService, GraphWriter, TBoxError
from .models import utcnow
from .runs import RunRecord

log = logging.getLogger("cumulus.gateway")

Regime = Literal["observation", "decision", "resolution"]

#: Refs kept on one entry. See `LedgerEntry.writes`.
MAX_WRITES = 500


class Refused(Exception):
    """A write the Gateway would not let through.

    Raised rather than returned, so a refusal cannot be mistaken for a commit
    by a caller that forgot to look. Inside a Flow it fails the run, which is
    honest: a decision nobody was allowed to make did not happen.
    """

    def __init__(self, action_id: str, reason: str) -> None:
        super().__init__(f"{action_id}: {reason}")
        self.action_id = action_id
        self.reason = reason


class Action(BaseModel):
    """A defined, typed mutation — declared, never inferred from a call site.

    `queue` is the second half of the doctrine's Role, which confers both
    permissions and queue-bindings: the rung on the write ladder says what a
    principal may touch at all, the queue says whose work it is. Two personas
    on the same rung own different work, and only the queue tells them apart.
    """

    id: str
    target: str
    regime: Regime = "decision"
    min_role: str = "user"
    queue: str = ""
    #: True when the desk is a property of the *case* rather than of the
    #: Action: one mutation over several books, where the Task that authorized
    #: it is what says whose work this instance is. The call site then passes
    #: `queue=` and the check runs against that. Declared, so "my desk comes
    #: from the Task" is something an Action says about itself and not
    #: something any caller may assert about any Action — an undeclared
    #: override is refused, and so is a declared one with nothing to derive.
    #:
    #: Set **beside** a `queue` where a mutation has a standing desk and a
    #: per-case one, which is not a contradiction: the pin is where the write
    #: commits when no Task authorized it — a Flow posting under the governed
    #: auto-post limit decided nothing and belongs to the standing desk — and
    #: the Task's queue wins whenever there is one, because a review routed to
    #: the submitter's own desk is committed by the person sitting at it. See
    #: `_for_case`.
    queue_from_task: bool = False


#: The closed set of guarded mutations. A write naming anything else is refused
#: before it reaches the graph — an undeclared Action is an ungoverned one.
ACTIONS: dict[str, Action] = {a.id: a for a in (
    # decisions — a person (or the Flow on the queue's behalf) determined this
    # The two flow commits, each with a standing desk *and* a per-case one.
    # A governed approval table may route a review back to the person who
    # submitted the case — `submitter` in routing.py, which resolves to that
    # person's own desk — and with the desk pinned here, the queue that raised
    # the work and the queue that may commit it were different queues: the
    # submitter would answer their own Task and have the write refused. That is
    # the same bug `confirm-identity-link` was fixed for, arrived at from a
    # third direction, so it takes the same shape.
    #
    # The pin stays because it is still the answer for every commit no Task
    # authorized: an invoice under the auto-post limit, an expense the flow
    # approved outright. Nobody decided those, the Flow commits on its own
    # authority, and the desk it commits under is the standing one.
    Action(id="post-invoice", target="ap-records", queue="ap-clerk",
           queue_from_task=True),
    Action(id="record-expense", target="expense-records",
           queue="expense-review", queue_from_task=True),
    # A job's departments, and what each of them owes in hours. `decision` and
    # not `observation`, and the distinction is the one `record-disagreements`
    # draws: **no source reported this**. The plant's document system holds a
    # PDF; nothing in it says Truss Fabrication owes 10.5 hours. That figure is
    # the product of a model reading a grid and a governed formula multiplying
    # it, which is a determination Cumulus made and must answer for — and the
    # hours it writes are what a plant's day is then built out of.
    #
    # A standing desk *and* a per-case one, for `post-invoice`'s reason. The
    # review that authorizes this is routed by the governed approval table, so
    # the queue that raised the work and the queue that commits it have to be
    # the same queue by construction; the pin is what answers for the commits
    # no Task authorized — a ticket that read cleanly, where nothing was in
    # doubt and nobody decided.
    Action(id="apply-job-demand", target="production-records",
           queue="plant-supervisor", queue_from_task=True),
    Action(id="link-source-record", target="entity-layer",
           queue="vendor-stewardship"),
    # The same decision on the other book. A second declaration rather than a
    # widened queue on the one above, because the queue *is* the governance
    # here: whoever owns `vendor-stewardship` decides who Cumulus pays, and
    # whoever owns `data-stewardship` decides who Cumulus thinks it is dealing
    # with. Letting one Action answer to both would hand the first authority
    # the second's work with nothing in the table to say it had happened.
    Action(id="link-company-record", target="entity-layer",
           queue="data-stewardship"),
    # The vendor a reviewer named. A standing desk *and* a per-case one, for
    # `post-invoice`'s reason above and arrived at from the same direction:
    # from v9 this answer arrives inside the invoice review, and that review is
    # routed by the governed approval table — to the submitter's own desk when
    # a person handed the invoice over. Pinned to `ap-clerk` alone, the queue
    # that raised the work and the queue that could commit it were different
    # queues, and the submitter would be refused their own answer. The pin
    # stands for the commits no Task authorized.
    Action(id="confirm-vendor", target="entity-layer", queue="ap-clerk",
           queue_from_task=True),
    # A cross-source merge *proposal*, and the steward's answer to one. Two
    # Actions, because they are not the same act and an auditor must not have
    # to read the payload to tell them apart: the first writes an assertion
    # nothing may act on (`status=in_review`) and the second is the judgment
    # that makes it actionable.
    #
    # The proposer carries **no queue** and the confirmation takes its desk
    # from the Task. Two different silences, and neither is a gap. A queue
    # binding says whose *work* an Action is; proposing is nobody's work — the
    # Flow does it on its own authority, unasked — and a queue on the proposer
    # would refuse the Flow outright, since a Flow principal owns only the
    # queues its declaration binds it to.
    #
    # Confirming is somebody's work, but not always the *same* somebody: the
    # sweep proposes across both counterparty books, and a `vendor_same_as`
    # pair is `vendor-stewardship`'s call where a `company_same_as` pair is
    # `data-stewardship`'s. Pinning one desk here meant the vendor steward's
    # own Task was refused at the write — the queue that raised it and the
    # queue that could commit it were different queues — so the desk is
    # derived from the Task being answered, the same way `apply_match_decision`
    # already takes its principal's queue off the Task rather than re-deriving
    # it. The alternative, a second Action per book (as `link-source-record`
    # and `link-company-record` are), buys separation the identity link does
    # not need: those two differ in the book they *write*, and these would
    # differ in nothing but the desk the Task already names.
    #
    # `decision` and not `resolution`: a Rule picking the winner is what
    # `resolution` means, and nothing here picks a winner.
    Action(id="propose-identity-link", target="entity-layer"),
    Action(id="confirm-identity-link", target="entity-layer",
           queue_from_task=True),
    # observations — a source reported this; audited by lineage, not approval
    Action(id="land-source-records", target="source-landing",
           regime="observation"),
    Action(id="apply-source-records", target="entity-layer",
           regime="observation"),
    # Reading a file the customer's system holds, and saying so on the row
    # that points at it. Its own Action rather than `apply-source-records`
    # because nothing was *reported*: no source delivered this claim, Cumulus
    # went and looked, and the observation is about our reading rather than
    # about their record. **No queue** — a file being read is nobody's work,
    # and a Flow principal owns only the queues its declaration binds it to.
    Action(id="record-document-read", target="document-library",
           regime="observation"),
    # a resolution — a Rule picked the winner, automatically and on the record
    Action(id="resolve-identities", target="entity-layer", regime="resolution"),
    # Recording that two systems disagree. **No queue**, for exactly the
    # reason `propose-identity-link` above carries none: a queue binding says
    # whose *work* an Action is, and noticing a disagreement is nobody's work.
    # The sweep does it on its own authority, unasked, and a queue here would
    # refuse the Flow outright — a Flow principal owns only the queues its
    # declaration binds it to.
    #
    # The work that *does* have a desk is the Task this write makes possible,
    # which lands in `order-desk`, and the write that would act on it lives in
    # a different Action entirely. Keeping them apart is what lets an auditor
    # tell "Cumulus noticed" from "somebody decided" without reading a payload.
    #
    # `decision` and not `observation`: no source reported this. It is the
    # product of a rule reading two sources against a governed policy, which
    # is a determination Cumulus made and must answer for.
    Action(id="record-disagreements", target="entity-layer"),
    # Recording that a permission is close enough to expiring that somebody
    # should be told. **No queue**, and `decision` rather than `observation`,
    # and both for `record-disagreements`' reasons directly above.
    #
    # No source reported any of this. The issuing system reported a date; the
    # *notice* — which of the governed 60/30/15 stages this row is now owed, and
    # therefore what `last_notice_stage` and `status` should say — is the
    # product of a rule reading that date against `certification-alert-policy`,
    # which is a determination Cumulus made and must answer for. Filing it as
    # an observation would credit the HRIS with a judgment it never made, and
    # would put the compliance sweep's own output where it reads its own input.
    #
    # And the queue is empty because noticing a date is nobody's work: the
    # Flow does it on its own authority, unasked, every window. A queue here
    # would refuse the write outright — a Flow principal owns only the queues
    # its declaration binds it to — which is exactly the shape of the bug
    # `propose-identity-link` carries the same note for.
    #
    # The work that *does* have a desk is the four aggregate Tasks this write
    # makes possible, which land in `hr-safety`. There is deliberately **no**
    # Action beside this one that deletes a compliance record: a record past
    # the governed retention horizon is reported for manual review, which is
    # the customer's own rule and deletes-are-decisions arriving from their
    # side of the table.
    Action(id="record-compliance-alert", target="compliance-records"),
    # A job's document packet: the four governed types at their latest
    # revision drawn onto the job, the revision each one replaced withdrawn
    # and marked superseded, and the label a crew calls the job by claimed.
    # **No queue**, and `decision` rather than `observation`, for
    # `record-compliance-alert`'s two reasons directly above.
    #
    # No source reported it. The document system reported four files and the
    # revision numbers it holds them at; *which* of them is a job's current
    # packet, and what that packet is called, is the product of a rule over
    # `field-documents-policy` — a determination Cumulus made and must answer
    # for. Filing it as an observation would credit the DMS with a judgment it
    # never made.
    #
    # And the queue is empty because linking a document nobody disputed is
    # nobody's work: the flow does it on its own authority, unasked, every
    # window, and a queue here would refuse the write outright — a Flow
    # principal owns only the queues its declaration binds it to. That is
    # `record-document-read`'s reasoning exactly, arrived at from the other
    # end of the same filing tree.
    #
    # The work that *does* have a desk is the two aggregate Tasks this write
    # makes possible, which land in `field-operations`. There is deliberately
    # **no** Action beside this one that deletes anything: the design this
    # replaces created, copied, moved and deleted per-foreman folders, and
    # none of that survives — a packet is a graph fact, a superseded revision
    # keeps its row and its withdrawn edge, and "no longer this crew's job" is
    # an ended validity rather than a directory somebody has to remove.
    Action(id="link-job-documents", target="production-records"),
    # A department's day, filled. `decision` and not `observation`, for
    # `apply-job-demand`'s reason one screen up and with the same force: **no
    # source reported this**. Nothing in Wick's estate holds a plant schedule
    # at all — that is the whole reason `ScheduleDay` is authoritative here —
    # so which jobs are on Tuesday, in what order, and against which capacity
    # row is the product of a Rule over a governed policy, which is a
    # determination Cumulus made and must answer for. Filing it as an
    # observation would credit the job system with a judgment it never made
    # and put this flow's own output where it reads its own input.
    #
    # A standing desk *and* a per-case one, for `post-invoice`'s reason. The
    # first proposal of a day is authorized by nobody — the flow writes it at
    # `pending_approval` precisely so a person has something to open — so the
    # pin answers for that; every re-fill after a correction is committed
    # under the desk of the Task where the supervisor answered.
    Action(id="apply-schedule-day", target="production-records",
           queue="plant-supervisor", queue_from_task=True),
    # And the signature on it, which is the one act in the plant spine that a
    # person always performs: the original design makes approval of a
    # bot-generated day a **permanent** control point, not a temporary
    # safeguard, and this is that control point as an Action.
    #
    # `queue_from_task=True` and **no reliance on the pin**, for
    # `confirm-identity-link`'s reason: taking the desk from the Task is what
    # makes the queue that raised the work and the queue that commits it the
    # same queue by construction. The governed approval table may route a
    # day's review anywhere the plant chooses; pinning a desk here would mean
    # a supervisor answering their own Task and having the write refused.
    #
    # `decision`, emphatically. A day becoming `approved` is the moment its
    # rows may leave this platform — the export answers for an approved day
    # and refuses a pending one — so this is the ledger row an auditor reads
    # to find out who let a schedule out of the building.
    Action(id="mark-day-approved", target="production-records",
           queue="plant-supervisor", queue_from_task=True),
    # The one Action that changes somebody else's system, and the only one in
    # this table whose target is not a book Cumulus keeps. Everything above it
    # writes into the graph, where a bad write is reversible by a steward and
    # every claim it made is still on the record; this one ends in an HTTP
    # PATCH to a customer's ERP, where the ledger entry is the *only* thing
    # Cumulus will still hold if the run that made it dies.
    #
    # `queue_from_task=True` for `confirm-identity-link`'s reason, arrived at
    # from the other side. There is one desk today — `order-desk`, bound to the
    # controller alone, because releasing revenue is a different authority from
    # deciding who the customer is — and pinning it here would be right until
    # the second class of disagreement arrives with a second desk behind it.
    # Taking the desk off the Task keeps the queue that raised the work and the
    # queue that may commit it the same queue by construction, which is the bug
    # the identity link had to be fixed for.
    #
    # `decision`, emphatically: a human approved this, and the whole two-regime
    # split in `order_release.py` exists so that the *observation* of what the
    # ERP then held is a separate Action with a separate ledger entry. Cumulus
    # never writes an authority claim it did not read back.
    Action(id="release-sales-order", target="sales-records",
           queue_from_task=True),
    # The flywheel's one write. **No queue** — appending what a clerk already
    # decided is nobody's work: the judgment happened at the review Task, was
    # ledgered there, and this records what it taught. A queue here would
    # refuse the flow outright, since a Flow principal owns only the queues its
    # declaration binds it to.
    #
    # `observation`, and the distinction is worth being exact about. The
    # *decision* was the clerk's and is already on the record as one; this is
    # Cumulus observing what that decision changed and keeping the pair. Filing
    # it as a `decision` would put a second judgment in the ledger for one act
    # and credit an automatic append with authority nobody exercised.
    Action(id="append-correction", target="eval-datasets",
           regime="observation"),
)}


class LedgerEntry(BaseModel):
    """One append to the curation ledger. Written once, never edited.

    `principal_*` is who committed; `impersonated_by` is the real operator when
    the principal was the product of a persona switch. `task_id`/`decided_by`
    are what *authorized* it — the human judgment a Flow resumed on — which is
    a different question from who ran the write, and an auditor asks both.
    """

    id: str
    at_iso: str
    regime: Regime
    action: str
    target: str = ""
    # `pending` is the stub state and never reaches the store. An outcome
    # defaulting to "committed" would be a field that fails open.
    #
    # `refused` and `failed` are two different accusations and the ledger must
    # not blur them: refused is *the Gateway* saying no — a principal without
    # the desk, a write the ontology rejects — and nothing happened. Failed is
    # the world saying no, after the Gateway had already allowed it: a graph
    # write that raised halfway, or an outbound call to a system that did not
    # answer. The first is governance working; the second is a write whose
    # effect nobody can be sure of, which is the entry an auditor most needs.
    outcome: Literal["pending", "committed", "refused", "failed"] = "pending"
    principal_uid: str = ""
    principal_email: str = ""
    principal_role: str = ""
    impersonated_by: str = ""
    key_id: str = ""
    task_id: str = ""
    decided_by: str = ""
    flow_id: str = ""
    run_id: str = ""
    step_id: str = ""
    case_ref: str = ""
    # What it touched, and how much. A bulk observation can write thousands of
    # refs and the entry has to fit in one document, so the list is capped and
    # the count never is: an entry that fails to store is worse than one that
    # says how much of itself it is not showing.
    writes: list[str] = Field(default_factory=list)
    write_count: int = 0
    refusal: str = ""
    #: What raised, when the outcome is `failed`. Its own field rather than
    #: `refusal` reworded: a reader filtering on refusals is asking which
    #: writes the spine stopped, and an exception text sitting in that field
    #: would answer with writes it did not stop.
    failure: str = ""


class LedgerStore(Protocol):
    def append(self, entry: LedgerEntry) -> LedgerEntry: ...
    def list(self, limit: int | None = None) -> list[LedgerEntry]: ...
    def get(self, entry_id: str) -> LedgerEntry | None: ...


class InMemoryLedgerStore:
    """Append-only by omission: there is no update and no delete to call."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: list[LedgerEntry] = []

    def append(self, entry: LedgerEntry) -> LedgerEntry:
        with self._lock:
            self._items.append(entry)
        return entry

    def list(self, limit: int | None = None) -> list[LedgerEntry]:
        with self._lock:
            items = list(reversed(self._items))  # newest first
        return items[:limit] if limit else items

    def get(self, entry_id: str) -> LedgerEntry | None:
        with self._lock:
            return next((e for e in self._items if e.id == entry_id), None)


class FirestoreLedgerStore:
    def __init__(self, project: str | None, collection: str) -> None:
        from google.cloud import firestore  # deferred: not needed in dev mode

        self._col = firestore.Client(project=project).collection(collection)
        self._desc = firestore.Query.DESCENDING

    def append(self, entry: LedgerEntry) -> LedgerEntry:
        self._col.document(entry.id).set(entry.model_dump(mode="json"))
        return entry

    def list(self, limit: int | None = None) -> list[LedgerEntry]:
        query = self._col.order_by("at_iso", direction=self._desc)
        return [
            LedgerEntry.model_validate(d.to_dict())
            for d in query.limit(limit or 500).stream()
        ]

    def get(self, entry_id: str) -> LedgerEntry | None:
        snap = self._col.document(entry_id).get()
        return LedgerEntry.model_validate(snap.to_dict()) if snap.exists else None


def _for_case(action: Action, queue: str) -> Action:
    """The Action as it applies to one case, when its desk is per-case.

    Both refusals matter. An Action that declares its own desk *and no
    derivation* must not accept one from a caller — that would turn the queue
    check into a parameter and let any call site name a queue it happens to
    own. And an Action that derives its desk with nothing to fall back on must
    not fall through to *no* desk when nothing was named, because an empty
    queue passes `_refusal` silently: the one failure here that would look
    exactly like success.

    Between them sits the declaration that carries both — a standing desk and a
    derived one. The Task's queue wins when there is a Task, and the pin
    answers for the commits no human authorized. Exactly one desk is checked
    either way: a caller holding neither the Task's desk nor the pin is refused
    the same as before, and holding the pin never buys the right to commit
    somebody else's case.
    """
    if not action.queue_from_task:
        if queue:
            raise Refused(
                action.id,
                "declares the desk it commits under; a caller does not get "
                "to name one",
            )
        return action
    if queue:
        return action.model_copy(update={"queue": queue})
    if not action.queue:
        raise Refused(
            action.id,
            "commits under the queue of the Task it answers, and no queue "
            "was named",
        )
    return action


def _refusal(action: Action, principal: Principal) -> str:
    """Why this principal may not commit this Action, or "" if it may."""
    if not principal.outranks_or_is(action.min_role):
        return f"requires the {action.min_role} role or higher"
    if action.queue and not principal.owns_queue(action.queue):
        return f"is the '{action.queue}' queue's work, which this principal does not own"
    return ""


class PendingAction:
    """One Action, constructed up front and committed inside a Flow step.

    Two-phase because the call sites are: a step needs the writer's accumulated
    refs *after* the work is done (`RunContext.step(writes=...)`), and the step
    body is what does the work. Committing inside the step also puts the ledger
    append on the same replay boundary as the write — a replayed step
    re-executes neither, so a run driven three times appends one entry.
    """

    def __init__(
        self, ledger: LedgerStore, entry: LedgerEntry, action: Action,
        principal: Principal, writer: GraphWriter,
    ) -> None:
        self._ledger = ledger
        self._action = action
        self._principal = principal
        self.entry = entry
        self.writer = writer

    @property
    def writes(self) -> list[str]:  # the write-set, for the wrapping step
        return self.writer.refs

    def commit(self, write: Callable[[GraphWriter], Any]) -> Any:
        """Check, write, ledger — in that order, on every path.

        Permission first, so a refused write never touches the graph. Ontology
        second, by catching what a strict `GraphService` raises: the Gateway's
        job is that the violation *stops* here, not that it keep a second copy
        of the check `graph.py` already owns.

        And *anything else* third. This used to let an unexpected exception
        leave through `write()` with the append still ahead of it, so a write
        that raised for any reason but the ontology appended nothing at all —
        the one shape of governed write with no ledger row. Tolerable while
        every Action ended in a graph upsert, a hole the moment one ends in an
        HTTP call to somebody else's system: the write may well have landed
        there, and the entry saying so is exactly what was being skipped. So
        the entry is written *before* the exception continues on its way,
        carrying the refs that did land, and the outcome keeps the two apart
        — `refused` is the Gateway saying no, `failed` is the world.
        """
        refusal = _refusal(self._action, self._principal)
        result: Any = None
        failure: Exception | None = None
        if not refusal:
            try:
                result = write(self.writer)
            except TBoxError as exc:
                refusal = f"does not conform to the ontology — {exc}"
            except Exception as exc:  # noqa: BLE001 — ledgered, then re-raised
                failure = exc
        refs = list(self.writer.refs)
        self.entry.writes = refs[:MAX_WRITES]
        self.entry.write_count = len(refs)
        self.entry.outcome = (
            "failed" if failure is not None
            else "refused" if refusal else "committed"
        )
        self.entry.refusal = refusal
        self.entry.failure = (
            "" if failure is None else f"{type(failure).__name__}: {failure}"
        )
        self._ledger.append(self.entry)
        log.log(
            logging.ERROR if failure is not None
            else logging.WARNING if refusal else logging.INFO,
            "Action failed" if failure is not None
            else "Action refused" if refusal else "Action committed",
            extra={
                "ledgerId": self.entry.id, "action": self._action.id,
                "regime": self._action.regime, "uid": self._principal.uid,
                "impersonatedBy": self.entry.impersonated_by,
                "runId": self.entry.run_id, "writes": self.entry.write_count,
                "refusal": refusal, "failure": self.entry.failure,
            },
        )
        if failure is not None:
            raise failure
        if refusal:
            raise Refused(self._action.id, refusal)
        return result


class ActionGateway:
    """The chokepoint: the graph it writes through, and the ledger it appends."""

    def __init__(self, graph: GraphService, ledger: LedgerStore) -> None:
        self.graph = graph
        self.ledger = ledger
        if not graph.strict:
            # Refusal is discharged by catching what a strict graph raises, so
            # on a lenient one the Gateway can ledger a bad write, not stop it.
            log.warning("Gateway built on a non-strict graph: ontology "
                        "violations will be recorded, not refused")

    def action(
        self, action_id: str, principal: Principal, *,
        run: RunRecord | None = None, step_id: str = "", target: str = "",
        task_id: str = "", decided_by: str = "", queue: str = "",
    ) -> PendingAction:
        """Name an Action and who is committing it. Nothing is written yet.

        `queue` is the desk this particular case belongs to, and only an Action
        declaring `queue_from_task` accepts one — see `_for_case`.
        """
        action = ACTIONS.get(action_id)
        if action is None:
            raise Refused(action_id, "not a declared Action")
        action = _for_case(action, queue)
        run_id = run.id if run is not None else ""
        entry = LedgerEntry(
            id=f"led_{secrets.token_hex(5)}",
            at_iso=utcnow().isoformat(),
            regime=action.regime, action=action.id,
            target=target or action.target,
            principal_uid=principal.uid, principal_email=principal.email,
            principal_role=principal.role, key_id=principal.key_id or "",
            impersonated_by=principal.impersonated_by or "",
            task_id=task_id, decided_by=decided_by,
            flow_id=run.flow_id if run is not None else "",
            case_ref=run.case_ref if run is not None else "",
            run_id=run_id, step_id=step_id,
        )
        return PendingAction(
            self.ledger, entry, action, principal,
            self.graph.writer(run_id, step_id),
        )


def build_action_gateway(
    graph: GraphService, backend: str, project: str | None, collection: str
) -> ActionGateway:
    ledger: LedgerStore = (
        FirestoreLedgerStore(project, collection) if backend == "firestore"
        else InMemoryLedgerStore()
    )
    return ActionGateway(graph, ledger)
