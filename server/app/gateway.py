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
restore its text. Nothing here is transactional either: a write refused halfway
leaves what landed before it, which is why a refused entry still records the
refs it managed to write.
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


#: The closed set of guarded mutations. A write naming anything else is refused
#: before it reaches the graph — an undeclared Action is an ungoverned one.
ACTIONS: dict[str, Action] = {a.id: a for a in (
    # decisions — a person (or the Flow on the queue's behalf) determined this
    Action(id="post-invoice", target="ap-records", queue="ap-clerk"),
    Action(id="record-expense", target="expense-records", queue="expense-review"),
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
    Action(id="confirm-vendor", target="entity-layer", queue="ap-clerk"),
    # observations — a source reported this; audited by lineage, not approval
    Action(id="land-source-records", target="source-landing",
           regime="observation"),
    Action(id="apply-source-records", target="entity-layer",
           regime="observation"),
    # a resolution — a Rule picked the winner, automatically and on the record
    Action(id="resolve-identities", target="entity-layer", regime="resolution"),
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
    outcome: Literal["pending", "committed", "refused"] = "pending"
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
        """
        refusal = _refusal(self._action, self._principal)
        result: Any = None
        if not refusal:
            try:
                result = write(self.writer)
            except TBoxError as exc:
                refusal = f"does not conform to the ontology — {exc}"
        refs = list(self.writer.refs)
        self.entry.writes = refs[:MAX_WRITES]
        self.entry.write_count = len(refs)
        self.entry.outcome = "refused" if refusal else "committed"
        self.entry.refusal = refusal
        self._ledger.append(self.entry)
        log.log(
            logging.WARNING if refusal else logging.INFO,
            "Action refused" if refusal else "Action committed",
            extra={
                "ledgerId": self.entry.id, "action": self._action.id,
                "regime": self._action.regime, "uid": self._principal.uid,
                "impersonatedBy": self.entry.impersonated_by,
                "runId": self.entry.run_id, "writes": self.entry.write_count,
                "refusal": refusal,
            },
        )
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
        task_id: str = "", decided_by: str = "",
    ) -> PendingAction:
        """Name an Action and who is committing it. Nothing is written yet."""
        action = ACTIONS.get(action_id)
        if action is None:
            raise Refused(action_id, "not a declared Action")
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
