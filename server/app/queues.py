"""Queues: the desk a Task lands on, with the people who hold it.

A queue was a bare string. `Task.queue` carried one, `Persona.queues` carried a
tuple of them, and nothing in between existed — no registry, no description, no
way to answer "who holds this desk" except by scanning the persona roster,
which one function in `ui_api` did inline. That is enough for a fixture and not
enough for a deployment: the roster is Naviant's code, and who sits at the AP
desk is the customer's business.

So the queue becomes a stored object with membership, and the roster becomes
its bootstrap rather than its definition. Three things follow that could not be
built before:

- **The reverse map.** `queues_for(email)` answers what a person holds, which
  is what a credential needs to know at every decision point. Forward-only
  bindings meant a real signed-in account was bound to nothing.
- **Routing to a resolved desk.** A Flow can route to `person-…`, a queue that
  is one named human's inbox, which is what "send it to their manager" needs.
- **Claims.** Broadcast is the doctrine — every bound Principal sees the Task —
  and broadcast without coordination means two stewards resolve the same entity
  in parallel and one of them wastes the morning. The lease lives here because
  its duration is a property of the desk: an AP clerk holds an invoice for
  minutes, a steward may sit with a merge for half an hour.

Membership is keyed on **email**, which is the only identifier that spans all
three worlds this has to join: a Firebase credential presents one, the persona
roster declares one, and the graph keys `Employee` on one. A provider uid joins
nothing.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from typing import Protocol

from pydantic import BaseModel, Field

from . import personas
from .models import utcnow

log = logging.getLogger("cumulus.queues")

SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9\-]*$")

#: How long a claim holds by default. Long enough to actually do the work —
#: read the evidence, look something up, decide — and short enough that an
#: abandoned claim returns to the pool within a coffee break. It is a per-queue
#: setting because the work differs: see the module docstring.
DEFAULT_CLAIM_TTL_SECONDS = 20 * 60

#: Personal queues get a readable slug plus a short digest. Readable because an
#: admin screen listing `person-a3f9c2` teaches nobody anything; digested
#: because two people can share a local part across domains and a desk that
#: silently merged two inboxes would be a quiet disaster.
_PERSONAL_PREFIX = "person-"


class QueueMember(BaseModel):
    email: str
    label: str = ""
    added_at_iso: str = Field(default_factory=lambda: utcnow().isoformat())
    added_by: str = ""
    #: Declared on the roster rather than added in the console. Shown as such
    #: and refused to `remove_member`, because deleting it would last until the
    #: next process start and then silently come back — a change that does not
    #: stick is worse than one that is refused.
    from_roster: bool = False


class Queue(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9\-]*$")
    name: str
    description: str = ""
    members: list[QueueMember] = Field(default_factory=list)
    claim_ttl_seconds: int = DEFAULT_CLAIM_TTL_SECONDS
    #: One person's own desk, targetable by a resolver like
    #: `manager-of(submitter)`. Kept as a flag rather than inferred from the
    #: slug prefix so that renaming the convention does not silently reclassify
    #: every inbox in the deployment.
    personal: bool = False
    person_email: str = ""
    created_at_iso: str = Field(default_factory=lambda: utcnow().isoformat())
    created_by: str = ""

    def holds(self, email: str) -> bool:
        lowered = (email or "").strip().lower()
        return any(m.email.lower() == lowered for m in self.members)

    @property
    def emails(self) -> list[str]:
        return [m.email for m in self.members]


class QueueExistsError(Exception):
    def __init__(self, slug: str) -> None:
        super().__init__(f"Queue already exists: {slug}")
        self.slug = slug


class QueueNotFoundError(Exception):
    def __init__(self, slug: str) -> None:
        super().__init__(f"Queue not found: {slug}")
        self.slug = slug


class QueueDirectory(Protocol):
    def list(self) -> list[Queue]: ...
    def get(self, slug: str) -> Queue | None: ...
    def put(self, queue: Queue) -> Queue: ...
    def delete(self, slug: str) -> bool: ...


class InMemoryQueueDirectory:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, Queue] = {}

    def list(self) -> list[Queue]:
        with self._lock:
            return list(self._items.values())

    def get(self, slug: str) -> Queue | None:
        with self._lock:
            return self._items.get(slug)

    def put(self, queue: Queue) -> Queue:
        with self._lock:
            self._items[queue.slug] = queue
            return queue

    def delete(self, slug: str) -> bool:
        with self._lock:
            return self._items.pop(slug, None) is not None


class FirestoreQueueDirectory:
    def __init__(self, project: str | None, collection: str) -> None:
        from google.cloud import firestore  # deferred: not needed for local dev

        self._client = firestore.Client(project=project)
        self._col = self._client.collection(collection)

    def list(self) -> list[Queue]:
        out: list[Queue] = []
        for doc in self._col.stream():
            try:
                out.append(Queue.model_validate(doc.to_dict()))
            except Exception:
                log.exception("Skipping unparseable queue", extra={"docId": doc.id})
        return out

    def get(self, slug: str) -> Queue | None:
        snap = self._col.document(slug).get()
        if not snap.exists:
            return None
        try:
            return Queue.model_validate(snap.to_dict())
        except Exception:
            log.exception("Unparseable queue", extra={"slug": slug})
            return None

    def put(self, queue: Queue) -> Queue:
        self._col.document(queue.slug).set(queue.model_dump(mode="json"))
        return queue

    def delete(self, slug: str) -> bool:
        ref = self._col.document(slug)
        if not ref.get().exists:
            return False
        ref.delete()
        return True


def personal_slug(email: str) -> str:
    """The slug of one person's own desk. Stable for a given email."""
    lowered = (email or "").strip().lower()
    local = lowered.split("@", 1)[0]
    readable = re.sub(r"[^a-z0-9]+", "-", local).strip("-") or "person"
    digest = hashlib.sha256(lowered.encode()).hexdigest()[:4]
    return f"{_PERSONAL_PREFIX}{readable}-{digest}"


class QueueService:
    """The registry, plus the lookups authorization and routing depend on."""

    def __init__(self, directory: QueueDirectory) -> None:
        self.directory = directory

    # -- reads --------------------------------------------------------------

    def list(self, include_personal: bool = True) -> list[Queue]:
        items = self.directory.list()
        if not include_personal:
            items = [q for q in items if not q.personal]
        return sorted(items, key=lambda q: (q.personal, q.slug))

    def get(self, slug: str) -> Queue | None:
        return self.directory.get(slug)

    def require(self, slug: str) -> Queue:
        queue = self.directory.get(slug)
        if queue is None:
            raise QueueNotFoundError(slug)
        return queue

    def queues_for(self, email: str) -> list[str]:
        """Every desk this person is a member of. The reverse map."""
        if not email:
            return []
        lowered = email.strip().lower()
        return sorted(
            q.slug for q in self.directory.list() if q.holds(lowered)
        )

    def holders_of(self, slug: str) -> list[str]:
        """Who holds this desk, by email — registry members and roster alike.

        Both sources, because `require_queue` accepts either: a screen that
        showed only the registry would report an unheld queue that three
        personas can in fact decide, which is exactly the kind of quiet
        disagreement between two authorization inputs that this whole change
        exists to remove.
        """
        queue = self.directory.get(slug)
        held = list(queue.emails) if queue else []
        for persona in personas.ALL_PERSONAS:
            if slug in persona.queues and persona.email:
                if persona.email not in held:
                    held.append(persona.email)
        return sorted(held)

    def claim_ttl(self, slug: str) -> int:
        queue = self.directory.get(slug)
        return queue.claim_ttl_seconds if queue else DEFAULT_CLAIM_TTL_SECONDS

    # -- writes -------------------------------------------------------------

    def create(
        self,
        slug: str,
        name: str,
        description: str = "",
        *,
        created_by: str = "",
        claim_ttl_seconds: int = DEFAULT_CLAIM_TTL_SECONDS,
        personal: bool = False,
        person_email: str = "",
    ) -> Queue:
        if not SLUG_PATTERN.match(slug):
            raise ValueError(
                f"Queue slug must be lowercase, hyphenated and url-safe: {slug}"
            )
        if self.directory.get(slug) is not None:
            raise QueueExistsError(slug)
        queue = Queue(
            slug=slug, name=name or slug, description=description,
            claim_ttl_seconds=max(60, claim_ttl_seconds),
            personal=personal, person_email=person_email,
            created_by=created_by,
        )
        log.info("Queue created", extra={"queue": slug, "actor": created_by})
        return self.directory.put(queue)

    def update(
        self,
        slug: str,
        *,
        name: str | None = None,
        description: str | None = None,
        claim_ttl_seconds: int | None = None,
    ) -> Queue:
        queue = self.require(slug)
        patch: dict = {}
        if name is not None:
            patch["name"] = name
        if description is not None:
            patch["description"] = description
        if claim_ttl_seconds is not None:
            patch["claim_ttl_seconds"] = max(60, claim_ttl_seconds)
        return self.directory.put(queue.model_copy(update=patch))

    def add_member(
        self, slug: str, email: str, label: str = "", added_by: str = ""
    ) -> Queue:
        queue = self.require(slug)
        lowered = email.strip().lower()
        if not lowered:
            raise ValueError("A queue member needs an email")
        others = [m for m in queue.members if m.email.lower() != lowered]
        member = QueueMember(email=lowered, label=label, added_by=added_by)
        log.info(
            "Queue member added",
            extra={"queue": slug, "member": lowered, "actor": added_by},
        )
        return self.directory.put(
            queue.model_copy(update={"members": others + [member]})
        )

    def remove_member(self, slug: str, email: str) -> Queue:
        queue = self.require(slug)
        lowered = email.strip().lower()
        existing = next(
            (m for m in queue.members if m.email.lower() == lowered), None
        )
        if existing is not None and existing.from_roster:
            raise ValueError(
                f"{email} holds '{slug}' through the persona roster, not this "
                "registry. Removing it here would last until the next restart "
                "and then come back."
            )
        remaining = [m for m in queue.members if m.email.lower() != lowered]
        if len(remaining) == len(queue.members):
            return queue
        log.info("Queue member removed", extra={"queue": slug, "member": lowered})
        return self.directory.put(
            queue.model_copy(update={"members": remaining})
        )

    def delete(self, slug: str) -> bool:
        return self.directory.delete(slug)

    def ensure_personal(self, email: str, label: str = "") -> Queue:
        """This person's own desk, created on first use.

        Minted lazily rather than one-per-employee up front: a deployment's
        org chart runs to hundreds of people and only the ones something has
        actually routed to need a desk. Idempotent, because the slug is a pure
        function of the email — so a Flow step that resolves an approver can be
        replayed without minting a second inbox.
        """
        lowered = (email or "").strip().lower()
        slug = personal_slug(lowered)
        existing = self.directory.get(slug)
        if existing is not None:
            return existing
        queue = Queue(
            slug=slug,
            name=label or lowered,
            description=f"{label or lowered}'s own desk — work routed to this "
                        "person by name rather than to a shared queue.",
            members=[QueueMember(email=lowered, label=label, added_by="system")],
            personal=True,
            person_email=lowered,
            created_by="system",
        )
        log.info("Personal queue minted", extra={"queue": slug, "email": lowered})
        return self.directory.put(queue)


def bootstrap_from_roster(service: QueueService) -> int:
    """Give every queue the persona roster names a registry entry.

    Runs at startup and is idempotent. The roster keeps its authority — a
    persona bound to `ap-clerk` still holds it whatever the registry says, and
    `auth.queues_for` unions the two — so this is not a migration that must
    succeed. It exists so the admin screen opens onto the desks that already
    exist rather than onto nothing, and so a customer's first act is editing
    membership rather than re-declaring the queues from memory.
    """
    created = 0
    for slug in personas.ALL_QUEUES:
        if service.get(slug) is not None:
            continue
        service.create(
            slug,
            name=slug.replace("-", " ").title(),
            description=_ROSTER_BLURBS.get(slug, ""),
            created_by="system",
        )
        created += 1
    # Roster membership is mirrored in, marked as declared so the console can
    # show where a binding came from and refuse to delete what it cannot.
    for persona in personas.ALL_PERSONAS:
        if not persona.email:
            continue
        for slug in persona.queues:
            queue = service.get(slug)
            if queue is None or queue.holds(persona.email):
                continue
            member = QueueMember(
                email=persona.email.lower(), label=persona.person or persona.label,
                added_by="roster", from_roster=True,
            )
            service.directory.put(
                queue.model_copy(update={"members": queue.members + [member]})
            )
    if created:
        log.info("Queue registry bootstrapped", extra={"queuesCreated": created})
    return created


_ROSTER_BLURBS: dict[str, str] = {
    "expense-review": "Elevated reimbursements: over the governed limit, a "
                      "restricted category, an uncertain extraction or a "
                      "missing purpose.",
    "ap-clerk": "Invoices held at the posting gate, and three-way matches "
                "that did not clear.",
    "vendor-stewardship": "Who Cumulus pays: vendor identity that will not "
                          "resolve on the evidence.",
    "data-stewardship": "Who Cumulus thinks it is dealing with: counterparty "
                        "matches and proposed merges.",
    "order-desk": "Commitments the CRM and the ERP disagree about.",
    "knowledge-curation": "Corrections proposed for promotion into canonical "
                          "knowledge.",
    "eval-review": "Flow evaluations awaiting a reviewer's label.",
    "hr": "People requests — time off, and the rest of the hub's judgment "
          "calls.",
}


def build_queue_service(
    backend: str, project: str | None, collection: str
) -> QueueService:
    directory: QueueDirectory
    if backend == "firestore":
        directory = FirestoreQueueDirectory(project, collection)
    else:
        directory = InMemoryQueueDirectory()
    return QueueService(directory)
