"""The job packet: which four documents a crew is entitled to, and its name.

Three bots in the original design — a dispatcher that watched the portal for
scheduled jobs, a performer that copied four PDFs into a per-foreman SharePoint
tree, and a weekly reconciliation that went looking for the jobs that had moved
between crews — collapse into one windowed pass over rows the graph already
holds. Nothing here reads a page: the fact the design's crew-schedule PDF
carried is a column on the job, so this flow makes no ModelCall at all.

**No folder is created, copied, moved or deleted anywhere in this package.**
Not once. The design's answer to "which documents does Crew 12 have" was a
directory per foreman with the files copied into it, and three sibling folders
(`Construction Details`, `JobSite Pictures`, `Archive`) that a bot maintained
by moving things between them. A packet here is the answer to a question about
the graph — `Job —documented_by→ Document` at the latest revision of each
governed type — and a crew's assignment is an edge with a validity, so "no
longer this crew's job" is a withdrawn edge rather than a folder somebody has
to move. The per-foreman SharePoint tree does not exist in this estate and
nothing in this flow would create it.

**Three of the design's steps are deliberately absent**, and they are named in
the version package's `NOTES` as well as here, because a reader comparing this
Flow to the design has to be able to see they were removed rather than
forgotten:

- *"Once the job moves to IN PROGRESS, stop checking for new versions."* A job
  whose `status` is not in the governed scope is not selected in `x2` at all.
  There is nothing to switch off.
- *"Delete the job folder when the job leaves the foreman."* The withdrawn
  `assigned_to` edge is `job-sync`'s observation, landed when the production
  system said a different crew has it. There is no destructive act left to
  gate.
- *"Move the pictures folder to `Archive`."* The same.

**Windowed, and either half can be the new fact.** Its two Triggers are
`JobRecordsDelivered` and `DocumentsDelivered`: a job becoming `Scheduled` is
one new fact, and a revision arriving for a job already scheduled is another,
and both change the same answer. The work list comes from the graph rather than
from the delivery, for `ap_intake`'s reason — a window that only gets through
part of a backlog leaves the rest for the next one, with no cursor to skew.

**One guarded Action for the whole window.** Forty guarded writes would be
forty ledger rows saying the same thing about one pass. What the window landed
is reported as counts on the run, and the exceptions are two aggregate Tasks —
never one per job.

**The policy decides what a packet is.** `field-documents-policy` carries the
four document types and their filename patterns, the folder root, the statuses
and the horizon that put a job in scope, the window bound, the naming
convention with its stopword list and collision rule, the recurrence threshold
and the desk. An edit to that page changes what this flow does with no deploy,
and the run cites the revision it read.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import date, timedelta

from pydantic import BaseModel, Field

from ... import desks, routing
from ...auth import Principal, flow_principal
from ...connectors.sync import DOCUMENTS_EVENT, JOB_EVENT
from ...dispatch import Dispatcher, InMemoryEventLog
from ...gateway import ActionGateway, InMemoryLedgerStore
from ...graph import (
    Claim,
    GraphService,
    GraphWriter,
    InMemoryGraphStore,
    normalize_key,
)
from ...models import utcnow
from ...ontology import names as onto
from ...routing import RoutingPolicy
from ...runs import RunContext, RunEngine
from ...task_queue import Task, TaskEvidence, TaskService

log = logging.getLogger("cumulus.field_documents")

FLOW_ID = "field-documents"
DEFAULT_VERSION = 1
POLICY_ASSET_ID = "field-documents-policy"

#: The two facts that wake it, imported rather than restated: the coordinator's
#: `DocumentRecordsDelivered` is the existing `DocumentsDelivered` (S6), and a
#: second spelling of one fact is the drift the constant exists to prevent.
WAKING_EVENTS: tuple[str, ...] = (JOB_EVENT, DOCUMENTS_EVENT)

#: The fact it emits. Nothing binds it; it is the run's own record of the pass,
#: carrying the counts so the Flows page can chart a cadence.
PACKETS_EVENT = "JobPacketsLinked"

#: The one guarded mutation, declared in `gateway.ACTIONS`. One per window.
LINK_ACTION = "link-job-documents"

#: Where `Job` lives, and the Action's target.
PRODUCTION_RECORDS = "production-records"

#: Who commits it. No queue: linking a document nobody disputed is nobody's
#: work — the flow does it on its own authority, unasked — and a Flow principal
#: owns only the queues its declaration binds it to, so a queue here would
#: refuse the write outright. `record-document-read` carries none for exactly
#: this reason.
DOCUMENTS_FLOW = flow_principal(FLOW_ID)

#: Where the two aggregates land when the governed table cannot be read.
DEFAULT_DESK = desks.FIELD_OPERATIONS


# ---------------------------------------------------------------------------
# The built-in defaults, and why there are any
# ---------------------------------------------------------------------------
#
# A fresh deployment with no corpus still has to work, and an empty answer here
# is a flow that silently does nothing — the failure that looks exactly like
# success. Every value below is the governed document's own, restated so the
# run can fall back to it *and say that it did*.


class DocumentType(BaseModel):
    """One of the governed types, with the order a crew reads it in.

    The order is the order the document lists them, which is the order the
    packet and the review screen show. It is deliberately not a fetch
    priority: all four are looked for every time.
    """

    order: int
    name: str
    pattern: str


DEFAULT_DOCUMENT_TYPES: tuple[DocumentType, ...] = (
    DocumentType(order=1, name="Shipping List",
                 pattern="<job number> Shipping List*.pdf"),
    DocumentType(order=2, name="Engineering Plans",
                 pattern="<job number> Engineering Plans*.pdf"),
    DocumentType(order=3, name="Job Site Map",
                 pattern="<job number> Job Site Map*.pdf"),
    DocumentType(order=4, name="Crew Hours",
                 pattern="<job number> Crew Hours*.pdf"),
)


def _default_types() -> list[DocumentType]:
    """A fresh copy of the built-in four, so no two policies share a row."""
    return [spec.model_copy() for spec in DEFAULT_DOCUMENT_TYPES]


DEFAULT_FOLDER_ROOT = "Job Documents"
DEFAULT_JOB_FOLDER_NAME = "<job number>"

#: The governed words for *which* revision is current, and the tiebreak. They
#: are the source system's spellings, which is right — the page is written for
#: somebody looking at the document system — and `_FIELDS` below is the one
#: place they are translated to the fields a connector landed them under.
DEFAULT_LATEST_BY = "latestVersion"
DEFAULT_TIEBREAK = "createdAt"

DEFAULT_STATUSES: tuple[str, ...] = ("Scheduled",)
DEFAULT_HORIZON_DAYS = 14

#: How many jobs one window may answer when the asset does not say. The bound
#: exists for `ap_intake.DEFAULT_WINDOW_LIMIT`'s reason — a window is answered
#: inside the request that opened it — and the remainder is not dropped: the
#: soonest to load are taken first and the rest is the next window's work,
#: stated on the run so a backlog is never a backlog nobody knows about.
DEFAULT_WINDOW_LIMIT = 40

DEFAULT_LABEL_PATTERN = (
    "<job number> <first keyword of the customer's company name>"
)
DEFAULT_KEYWORD_SOURCE = "the customer's company name"
DEFAULT_KEYWORD_FALLBACK = "the customer's own name"
DEFAULT_STOPWORDS: tuple[str, ...] = ("the", "a", "an", "of")
DEFAULT_COLLISION_RULE = "append the next keyword"
DEFAULT_LABEL_CASE = "as printed"

DEFAULT_MISSING_WINDOWS = 3
DEFAULT_PARTIAL_PACKET = "informational"

#: The governed sentence the identity aggregate is written from, when the page
#: does not carry its own. Prose rather than a value because it is what the
#: Task says out loud, and the page's own words are better than ours.
DEFAULT_CREW_RULE = (
    "A job whose crew id matches no crew in the workforce system is not "
    "dropped and no document question is asked about it: one identity "
    "question, once per window, rather than one document question per job "
    "nobody can deliver to."
)

#: The graph fields the governed words above name. The document system spells
#: them its own way and a connector renames on the way in, so this is the seam
#: where the page's vocabulary meets the ontology's — declared, because a
#: word the table has never heard of must fall back and *say so* rather than
#: silently comparing nothing.
_VERSION_FIELDS: dict[str, str] = {
    "latestversion": "latest_version",
    "latest_version": "latest_version",
    "readversion": "read_version",
    "read_version": "read_version",
}

#: `createdAt` is the document system's own word and the DMS wire shape does
#: not carry it (see `connectors.sync.DmsDocumentRecord`), so the tiebreak is
#: the graph row's own creation stamp — the moment `dms-sync` landed it, which
#: preserves the order the source delivered them in. Compared at full
#: precision: the estate's revisions of one document can differ by
#: microseconds, and a comparison truncated to the second would call them a
#: tie and then break it on a filename.
_TIEBREAK_FIELDS: dict[str, str] = {
    "createdat": "created_at_iso",
    "created_at": "created_at_iso",
    "created_at_iso": "created_at_iso",
}


# ---------------------------------------------------------------------------
# The two aggregate classes
# ---------------------------------------------------------------------------

NO_DOCUMENTS = "no-documents-past-threshold"
UNRESOLVED_CREW = "unresolved-crew"

#: At most two Tasks per window, and the order is the order they are raised.
#: One row belongs to exactly one of them by construction rather than by
#: precedence: a job whose crew resolves to nothing is never asked a document
#: question at all, which is the governed page's own arrangement and the reason
#: these two classes cannot overlap the way `credential_tracking`'s four can.
AGGREGATE_CLASSES: tuple[str, ...] = (NO_DOCUMENTS, UNRESOLVED_CREW)


class AggregateClass(BaseModel):
    """The copy one aggregate Task is written from."""

    id: str
    #: Filled with the row count.
    title: str
    question: str
    priority: str = "normal"


AGGREGATE_COPY: dict[str, AggregateClass] = {c.id: c for c in (
    AggregateClass(
        id=NO_DOCUMENTS,
        title="{n} scheduled job(s) have no filed documents at all",
        question=(
            "Each job below is scheduled to load inside the governed horizon "
            "and the document system holds **none** of the four governed "
            "types for it — not a partial packet, nothing. The design's own "
            "answer to this was *log as information and continue; notify if "
            "recurring*, and that is exactly what has happened: it was "
            "counted on the earlier windows and is being asked about now "
            "because it has recurred. A job missing *some* of the four is "
            "informational at every window and never raises a task on its "
            "own — a packet with plans and a site map but no crew hours "
            "sheet is a normal Tuesday. What this needs is somebody to file "
            "the documents, or to say why there are none."
        ),
    ),
    AggregateClass(
        id=UNRESOLVED_CREW,
        title="{n} job(s) name a crew nobody can be resolved to",
        priority="high",
        question=(
            "The production system wrote a crew id and a foreman's name on "
            "each job below, and that id matches no crew in the workforce "
            "system. Nothing was dropped and no document question has been "
            "asked about these jobs: there is nobody to deliver a packet to "
            "until somebody says who this crew is. Jobs erected by a "
            "subcontractor are the ordinary case, and the answer is a "
            "person's rather than a pattern's — the retired design matched "
            "the word `SUBCONTRACTOR` in a report header, which worked until "
            "a second subcontractor was hired. **One identity question per "
            "window, not one document question per job.**"
        ),
    ),
)}

#: The review screen the console opens for one of these. Declared in the
#: version package's `UI`; the manifest degrades to the generic approve/reject
#: pane in a build that does not hold the widget yet.
GAP_SCREEN = "field-documents/gap-review"


# ---------------------------------------------------------------------------
# x1 · the governed read
# ---------------------------------------------------------------------------


class FieldDocumentPolicy(BaseModel):
    """Everything `field-documents-policy` decides, at one revision.

    One read for the whole run, so the journal carries a single ref and two
    steps of this flow cannot be looking at two revisions of the same
    document.
    """

    document_types: list[DocumentType] = Field(
        default_factory=lambda: _default_types()
    )
    folder_root: str = DEFAULT_FOLDER_ROOT
    job_folder_name: str = DEFAULT_JOB_FOLDER_NAME
    latest_by: str = DEFAULT_LATEST_BY
    tiebreak: str = DEFAULT_TIEBREAK

    statuses: list[str] = Field(
        default_factory=lambda: list(DEFAULT_STATUSES)
    )
    horizon_days: int = DEFAULT_HORIZON_DAYS
    window_limit: int = DEFAULT_WINDOW_LIMIT

    label_pattern: str = DEFAULT_LABEL_PATTERN
    keyword_source: str = DEFAULT_KEYWORD_SOURCE
    keyword_fallback: str = DEFAULT_KEYWORD_FALLBACK
    stopwords: list[str] = Field(
        default_factory=lambda: list(DEFAULT_STOPWORDS)
    )
    collision_rule: str = DEFAULT_COLLISION_RULE
    label_case: str = DEFAULT_LABEL_CASE

    missing_windows: int = DEFAULT_MISSING_WINDOWS
    partial_packet: str = DEFAULT_PARTIAL_PACKET
    crew_rule: str = DEFAULT_CREW_RULE

    routing: RoutingPolicy = Field(
        default_factory=lambda: RoutingPolicy(fallback=DEFAULT_DESK)
    )
    source_ref: str = f"knowledge:{POLICY_ASSET_ID}"
    found_asset: bool = False

    @property
    def type_names(self) -> list[str]:
        """The four names, in the order the document lists them."""
        return [t.name for t in sorted(self.document_types,
                                       key=lambda t: t.order)]

    @property
    def version_field(self) -> str:
        """The graph field `latest revision decided by` names."""
        return _VERSION_FIELDS.get(
            _word(self.latest_by), _VERSION_FIELDS[_word(DEFAULT_LATEST_BY)]
        )

    @property
    def tiebreak_field(self) -> str:
        """The graph field `revision tiebreak` names."""
        return _TIEBREAK_FIELDS.get(
            _word(self.tiebreak), _TIEBREAK_FIELDS[_word(DEFAULT_TIEBREAK)]
        )

    @property
    def reads_declared_fields(self) -> bool:
        """Did both governed words resolve to a field we know how to read?

        Surfaced rather than enforced, on `AlertPolicy.honors_sla_floor`'s
        terms: an edit naming a column this deployment does not hold is a
        change somebody made in the open, and the honest response is a run
        that says it fell back — not a flow that quietly compares a field
        nobody wrote down.
        """
        return (
            _word(self.latest_by) in _VERSION_FIELDS
            and _word(self.tiebreak) in _TIEBREAK_FIELDS
        )

    def job_folder_path(self, job_number: str) -> str:
        """Where this job's documents are filed, per the governed root.

        A folder is named by its full path from the root of the document tree,
        the same way `ap-intake-policy` names its invoice folders — so this is
        a path and never a search.
        """
        leaf = self.job_folder_name.replace("<job number>", job_number)
        root = self.folder_root.strip("/")
        return f"{root}/{leaf}" if root else leaf


def _word(value: str) -> str:
    """One governed word, comparable: lowercased with separators removed."""
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def _section(content: str, heading: str) -> str:
    """The body under one `##` heading, up to the next one.

    A local copy of `routing._section`, deliberately, on the terms
    `credential_tracking._section` sets: the canonical one is private to a
    module this package should not reach into for one regex, and the two are
    pinned to agree by a test rather than left to drift.

    Everything below is section-scoped through this, and that is not tidiness.
    This document *discusses* its own values in prose — the filing section
    argues about which revision is newest, the label section quotes the labels
    two answer keys expect, the missing-documents section names its own
    threshold in a sentence — so an unscoped search would read a sentence
    about a value as the value.
    """
    match = re.search(
        rf"^#{{1,4}}\s*{re.escape(heading)}\s*$(.*?)(?=^#{{1,4}}\s|\Z)",
        content or "", re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    return match.group(1) if match else ""


def _value(section: str, key: str) -> str:
    """One `- key: value` line out of a section, or "".

    Line-oriented and forgiving in `parse_intake_policy`'s way: a customer
    edits prose in a console, and a policy that stopped parsing because
    somebody rewrapped a paragraph would take the flow down with it.
    """
    match = re.search(
        rf"^\s*[-*]\s*{re.escape(key)}\s*:\s*(.+?)\s*$",
        section, re.IGNORECASE | re.MULTILINE,
    )
    return match.group(1).strip().strip("`*_ ") if match else ""


def _list(value: str) -> list[str]:
    """A comma-separated governed list, cleaned but not re-cased."""
    return [
        word.strip().strip("`*_ ") for word in value.split(",")
        if word.strip().strip("`*_ ")
    ]


def _first_number(value: str) -> int | None:
    match = re.search(r"\d+", value or "")
    return int(match.group(0)) if match else None


def _prose(section: str) -> str:
    """The section's own words, collapsed onto one line.

    Used where a heading carries no `- key: value` at all: `## Unresolved
    crews` is a paragraph stating a policy, and the Task it governs is more
    legible quoting the page than paraphrasing it. Bold markers are dropped
    and nothing else is interpreted.
    """
    body = re.sub(r"\*\*|`", "", section or "")
    body = re.sub(r"\s+", " ", body).strip()
    return body


def parse_field_policy(content: str) -> FieldDocumentPolicy:
    """Read the whole governed read-set out of the document.

    Every value degrades on its own: a section somebody deleted leaves that
    one default standing and the rest read from the asset, which is a more
    useful failure than an all-or-nothing parse.
    """
    values = FieldDocumentPolicy(found_asset=True)

    types = _document_types(_section(content, "Document types"))
    if types:
        # Only when the table yields rows. A truncated table must fall back to
        # the declared four rather than to *none*, which would read as a clean
        # sweep of jobs nobody looked for a document for.
        values.document_types = types

    filed = _section(content, "Where job documents are filed")
    root = _value(filed, "document folder root")
    if root:
        values.folder_root = root
    leaf = _value(filed, "job folder name")
    if leaf:
        values.job_folder_name = leaf
    latest = _value(filed, "latest revision decided by")
    if latest:
        values.latest_by = latest
    tiebreak = _value(filed, "revision tiebreak")
    if tiebreak:
        values.tiebreak = tiebreak

    scope = _section(content, "Which jobs are in scope")
    statuses = _list(_value(scope, "job status in scope"))
    if statuses:
        values.statuses = statuses
    horizon = _first_number(_value(scope, "scheduling horizon"))
    if horizon is not None:
        values.horizon_days = horizon
    bound = _first_number(_value(scope, "jobs per document window"))
    if bound is not None:
        # Zero is a real answer — "answer no jobs this window" — and honored.
        values.window_limit = bound

    label = _section(content, "Packet label")
    pattern = _value(label, "label pattern")
    if pattern:
        values.label_pattern = pattern
    keyword = _value(label, "label keyword source")
    if keyword:
        values.keyword_source = keyword
    fallback = _value(label, "label keyword fallback")
    if fallback:
        values.keyword_fallback = fallback
    stopwords = _list(_value(label, "label stopwords"))
    if stopwords:
        values.stopwords = [word.lower() for word in stopwords]
    collision = _value(label, "on label collision")
    if collision:
        values.collision_rule = collision
    case = _value(label, "label case")
    if case:
        values.label_case = case

    missing = _section(content, "Missing documents")
    threshold = _first_number(
        _value(missing, "missing document windows before a task")
    )
    if threshold is not None:
        values.missing_windows = threshold
    partial = _value(missing, "partial packet")
    if partial:
        values.partial_packet = partial

    crew = _prose(_section(content, "Unresolved crews"))
    if crew:
        values.crew_rule = crew

    # The approval table, through the one parser that already reads approval
    # tables. Its own section scoping, its own resolver vocabulary, and its own
    # refusal to slugify prose into a desk nobody created.
    values.routing = routing.parse_routing(
        content, default_fallback=DEFAULT_DESK
    )
    return values


def _document_types(section: str) -> list[DocumentType]:
    """The `Order | Document type | Filename pattern` table, in its own order.

    The header and separator rows are skipped by requiring a number in the
    first cell, which also means a table somebody re-ordered still reads: the
    order is the column, not the position.
    """
    found: list[DocumentType] = []
    for raw in re.findall(r"^\s*\|(.+)\|\s*$", section, re.MULTILINE):
        cells = [cell.strip().strip("`*_ ") for cell in raw.split("|")]
        if len(cells) < 3 or not cells[0].isdigit():
            continue
        name, pattern = cells[1], cells[2]
        if not name or not pattern:
            continue
        found.append(DocumentType(
            order=int(cells[0]), name=name, pattern=pattern,
        ))
    return sorted(found, key=lambda t: t.order)


def read_field_policy(knowledge) -> FieldDocumentPolicy:
    """The asset at its revision, or the built-in default, said out loud."""
    asset = knowledge.repo.get(POLICY_ASSET_ID) if knowledge else None
    if asset is None:
        log.warning(
            "Field documents policy missing; using the built-in packet",
            extra={"assetId": POLICY_ASSET_ID},
        )
        return FieldDocumentPolicy(found_asset=False)
    values = parse_field_policy(asset.content)
    values.source_ref = (
        f"knowledge:{asset.id}@rev{getattr(asset, 'revision', 1)}"
    )
    return values


# ---------------------------------------------------------------------------
# The naming convention — pure, and the two traps it exists for
# ---------------------------------------------------------------------------


class PacketLabel(BaseModel):
    """One derived label, and which governed rule produced it.

    The rule travels because the label is only trustworthy if a person can see
    why it reads the way it does: `250661 Willows` has to be legible as *the
    article was a governed stopword* and `250553 Evermore Ridge` as *a
    collision, so the next keyword was appended*.
    """

    job_number: str
    customer: str
    label: str
    keywords: list[str] = Field(default_factory=list)
    rule: str = ""
    skipped_stopword: str = ""
    collided: bool = False
    exhausted: bool = False


def keywords_of(name: str, stopwords: list[str]) -> list[str]:
    """The customer's name as keywords, governed stopwords removed.

    Case is left exactly as printed, because `- label case: as printed` says
    so: a crew, a foreman and a scheduler all say `Willows`, and lowercasing
    the label to make the comparison easier would change what they read.

    The stopword list is applied to *every* position and not only the first.
    The document's sentence names the first keyword because that is where its
    trap is — a leading article — and skipping an article that turns up in the
    second position is the same rule applied consistently rather than a
    second rule. `Evermore of Wisconsin` colliding with another `Evermore`
    would otherwise be labelled `Evermore of`.
    """
    skip = {word.strip().lower() for word in stopwords if word.strip()}
    out: list[str] = []
    for token in re.split(r"\s+", name or ""):
        word = token.strip(".,;:!?'\"()[]{}")
        if word and word.lower() not in skip:
            out.append(word)
    return out


def derive_labels(
    rows: list[tuple[str, str]], policy: FieldDocumentPolicy
) -> dict[str, PacketLabel]:
    """The window's labels, all of them at once — pure, and order-free.

    All at once because the collision rule is a statement about the *window*:
    "the label a job would get is already held by a different customer inside
    the same window". A per-job function could not see the other customer, and
    a per-job function that looked one up would have made the label depend on
    the order the jobs were walked in.

    The collision turns on the **keyword**, not on the finished label. Every
    label already carries the job number, so two jobs can never collide on the
    whole string; what makes `250553 Evermore` and `250774 Evermore`
    indistinguishable to a person is the half that names the customer.

    Two customers spelled identically are one keyword source here, and that is
    deliberate: whether they are one party is the counterparty book's question,
    answered by a steward on a `Company` match, and this flow guessing at it
    would be a second identity opinion nobody asked for.
    """
    keywords: dict[str, list[str]] = {}
    printed: dict[str, str] = {}
    leading: dict[str, str] = {}
    for _job, customer in rows:
        key = normalize_key(customer)
        if not key or key in keywords:
            continue
        printed[key] = customer
        keywords[key] = keywords_of(customer, policy.stopwords)
        first = re.split(r"\s+", (customer or "").strip())[:1]
        head = first[0].strip(".,;:!?'\"()[]{}") if first else ""
        if head and head.lower() in {
            word.strip().lower() for word in policy.stopwords if word.strip()
        }:
            leading[key] = head

    # How many keywords each customer needs to be distinguishable. One, until
    # somebody else in the window claims the same prefix; then the next
    # keyword, and the next, which is the governed rule read literally.
    depth = {key: 1 for key in keywords}
    for _pass in range(1, 12):
        buckets: dict[tuple, list[str]] = {}
        for key, words in keywords.items():
            prefix = tuple(w.lower() for w in words[:depth[key]])
            buckets.setdefault(prefix, []).append(key)
        widened = False
        for keys in buckets.values():
            if len(keys) < 2:
                continue
            for key in keys:
                if depth[key] < len(keywords[key]):
                    depth[key] += 1
                    widened = True
        if not widened:
            break

    # Who is *still* sharing a prefix after the rule ran out of keywords. Two
    # customers whose whole names are the same words in the same order are
    # the only way to get here, and the run says so rather than handing two
    # crews the same label in silence.
    final: dict[tuple, list[str]] = {}
    for key, words in keywords.items():
        prefix = tuple(w.lower() for w in words[:depth[key]])
        final.setdefault(prefix, []).append(key)
    unresolved = {
        key for keys in final.values() if len(keys) > 1 for key in keys
    }

    labels: dict[str, PacketLabel] = {}
    for job_number, customer in rows:
        key = normalize_key(customer)
        words = keywords.get(key, [])
        used = words[:depth.get(key, 1)]
        label = " ".join([job_number, *used]).strip()
        reasons: list[str] = []
        if leading.get(key):
            reasons.append(
                f"the leading `{leading[key]}` is a governed stopword, so the "
                f"first keyword is `{used[0]}`" if used else
                f"the leading `{leading[key]}` is a governed stopword"
            )
        if len(used) > 1:
            reasons.append(
                "a collision with another customer in this window, so the "
                f"next keyword was appended ({policy.collision_rule})"
            )
        if not words:
            reasons.append(
                "no customer name on the job, so the label is the job number "
                "alone"
            )
        if key in unresolved:
            reasons.append(
                "**still shared after every keyword was appended** — two "
                "customers in this window are named identically"
            )
        labels[job_number] = PacketLabel(
            job_number=job_number,
            customer=printed.get(key, customer),
            label=label,
            keywords=used,
            rule=" · ".join(reasons) or "the customer's first keyword",
            skipped_stopword=leading.get(key, ""),
            collided=len(used) > 1,
            exhausted=key in unresolved,
        )
    return labels


# ---------------------------------------------------------------------------
# x2 · the standing question
# ---------------------------------------------------------------------------


class SelectedJob(BaseModel):
    """One job the window took on, with everything a decision needs.

    This is the fetching half, so the check below never reads the graph: the
    crew, the customer, the dates and the crew's history all travel as values.
    """

    object_id: str
    object_ref: str
    natural_key: str
    job_number: str
    job_label: str
    source: str = ""
    status: str = ""

    #: What the production system printed on the job, and what the counterparty
    #: book calls the customer the `job_for` edge resolved to. Both, because
    #: the governed convention names one as the keyword source and the other as
    #: its fallback.
    customer_name: str = ""
    company_name: str = ""

    load_date: str = ""
    days_to_load: int | None = None

    crew_id: str = ""
    foreman_name: str = ""
    crew_ref: str = ""
    crew_label: str = ""
    #: Crews that held this job and do not now — read off the **withdrawn**
    #: `assigned_to` edges, which are still on the record. This is the whole of
    #: what retired the weekly reconciliation: the difference between last
    #: week's schedule and this one is a property of the row.
    former_crews: list[str] = Field(default_factory=list)
    crew_resolved: bool = False
    #: Did the source name a crew at all? An empty column is the production
    #: system saying nothing, which is a different fact from an id that names
    #: no team — and only the second is an identity question.
    crew_stated: bool = False

    @property
    def keyword_source(self) -> str:
        """The name the label's keyword comes from.

        `- label keyword source: the customer's company name` is the
        counterparty book's name for the company the `job_for` edge resolved
        to; `- label keyword fallback: the customer's own name` is what the
        production system printed on the job, which is the only answer there
        is until a steward rules on the match.
        """
        return self.company_name or self.customer_name


def read_date(value: object) -> date | None:
    """A date, or None — never a guess, and never an exception.

    None is counted by the caller and named as its own exclusion. A job whose
    load date cannot be read cannot be placed inside or outside a horizon, and
    guessing at it would put a crew on the road against a date nobody wrote.
    """
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


#: The exclusions `x2` reports, each with its own name. Named rather than
#: summed, because "not scheduled yet" and "loaded last week" are different
#: facts about a job and a reader of the journal has to be able to tell them
#: apart.
STATUS_OUT_OF_SCOPE = "status-not-in-scope"
NO_LOAD_DATE = "no-load-date"
LOAD_DATE_PAST = "load-date-already-past"
BEYOND_HORIZON = "beyond-the-horizon"

EXCLUSIONS: tuple[str, ...] = (
    STATUS_OUT_OF_SCOPE, NO_LOAD_DATE, LOAD_DATE_PAST, BEYOND_HORIZON,
)


# ---------------------------------------------------------------------------
# x3 · the packet
# ---------------------------------------------------------------------------


class PacketDocument(BaseModel):
    """One revision of one governed type, as the document system holds it."""

    document_type: str
    order: int
    object_id: str
    object_ref: str
    natural_key: str
    filename: str
    version: int = 0
    created_at: str = ""
    #: The live `documented_by` edge this revision already has, when it has
    #: one. Read here so the Action below withdraws a link rather than
    #: re-deriving which one to withdraw from inside a guarded write.
    link_id: str = ""


class JobPacket(BaseModel):
    """What one job's packet is, before anything was written."""

    job_id: str
    job_ref: str
    job_natural_key: str
    job_number: str
    job_label: str
    customer: str = ""
    crew_label: str = ""
    crew_id: str = ""
    load_date: str = ""

    folder_id: str = ""
    folder_ref: str = ""
    folder_path: str = ""
    folder_found: bool = False

    current: list[PacketDocument] = Field(default_factory=list)
    #: Earlier revisions of a type this pass is linking — the edge to be
    #: withdrawn and the row to be marked superseded.
    superseded: list[PacketDocument] = Field(default_factory=list)
    #: Revisions a previous pass already marked superseded. Counted, never
    #: re-written: a second ledger row saying the same thing about the same
    #: row is noise.
    already_superseded: int = 0
    missing: list[str] = Field(default_factory=list)
    unclassified: int = 0

    label: str = ""
    label_rule: str = ""
    complete: bool = False

    @property
    def empty(self) -> bool:
        """None of the four governed types, which is the recurring case."""
        return not self.current


def pattern_regex(pattern: str, job_number: str) -> re.Pattern:
    """One governed filename pattern, for one job.

    `*` is the only metacharacter — the document is written for somebody
    looking at a file list, not a regex — so everything else is escaped. A
    file whose name matches none of the four is left where it is and counted
    as unclassified: a file in a job folder that nobody named to the
    convention is a filing question for the office, and this flow guessing at
    it is exactly what the governed table exists to replace.
    """
    literal = pattern.replace("<job number>", job_number)
    return re.compile(
        "^" + ".*".join(re.escape(part) for part in literal.split("*")) + "$",
        re.IGNORECASE,
    )


# ---------------------------------------------------------------------------
# a1 · the one guarded write
# ---------------------------------------------------------------------------


def link_packets(
    graph: GraphWriter,
    packets: list[JobPacket],
    policy: FieldDocumentPolicy,
    run_id: str,
) -> dict:
    """Land the window's packets — one Action, however many jobs.

    A windowed flow lands bulk observations, and forty guarded writes would be
    forty ledger rows saying the same thing about one pass.

    The regime is `decision` rather than `observation` and the distinction is
    load-bearing: **no source reported any of this.** The document system
    reported four files and their revisions; *which* of them is a job's
    current packet, and what that packet is called, is the product of a rule
    over `field-documents-policy`, which is a determination Cumulus made and
    must answer for. Filing it as an observation would credit the DMS with a
    judgment it never made.

    Nothing is deleted anywhere in here. The superseded revision keeps its
    row, its claims and its withdrawn edge, because a crew that already
    printed it should be able to find out what they are holding.
    """
    counts = {
        "jobs": 0, "linked": 0, "superseded": 0, "withdrawn": 0,
        "labels": 0, "complete": 0, "incomplete": 0, "folders": 0,
    }
    source = f"flow:{FLOW_ID}"
    for packet in packets:
        counts["jobs"] += 1

        for revision in packet.superseded:
            # Withdraw before marking, so a reader of the ledger sees the
            # edge end and the row change state in that order.
            if revision.link_id:
                graph.reverse_link(
                    revision.link_id, source,
                    f"superseded by revision {_top_version(packet, revision)} "
                    f"of `{revision.document_type}` per {policy.source_ref}",
                )
                counts["withdrawn"] += 1
            graph.upsert(
                onto.DOCUMENT, revision.natural_key,
                # The one claim, and it is what makes the status stick:
                # `GraphService.upsert` applies a status change only alongside
                # a claim it has not seen before. This flow restating the
                # revision this row *is* — under its own authority, with the
                # determination in the note — is the honest form of that: the
                # document system outranks a flow in `PRECEDENCE`, so the
                # resolved value stays the DMS's and nothing is overwritten.
                [Claim(
                    field=policy.version_field, value=revision.version,
                    source=source, run_id=run_id, step_id="a1",
                    note=(
                        f"not the current revision of "
                        f"`{revision.document_type}` for "
                        f"{packet.job_number}"
                    ),
                )],
                status="superseded",
                provenance=(
                    f"superseded by {run_id} per {policy.source_ref}"
                ),
            )
            counts["superseded"] += 1

        for revision in packet.current:
            graph.link(
                "documented_by", packet.job_id, revision.object_id,
                props={
                    "document_type": revision.document_type,
                    "revision": str(revision.version),
                },
                method="declared",
                provenance=(
                    f"latest `{revision.document_type}` under "
                    f"{packet.folder_path} per {policy.source_ref}"
                ),
            )
            counts["linked"] += 1

        if packet.folder_id:
            # Drawn here as well as by `job-sync`, and idempotent by
            # construction: the feed indexes folders by the last segment of
            # their path, this flow reaches the one the governed root names,
            # and `GraphService.link` returns a live edge rather than a second
            # one. Two writers agreeing about the same edge is the ordinary
            # case for a many-to-many link and not a duplicate.
            graph.link(
                "job_folder", packet.job_id, packet.folder_id,
                method="declared",
                provenance=f"governed document root {policy.folder_root}",
            )
            counts["folders"] += 1

        claims = [Claim(
            field="documents_complete", value="yes" if packet.complete
            else "no", source=source, run_id=run_id, step_id="a1",
        )]
        if packet.label:
            claims.append(Claim(
                field="packet_label", value=packet.label, source=source,
                run_id=run_id, step_id="a1", note=packet.label_rule,
            ))
            counts["labels"] += 1
        graph.upsert(
            onto.JOB, packet.job_natural_key, claims,
            label=packet.job_label or packet.job_natural_key,
            provenance=(
                f"packet resolved by {run_id} per {policy.source_ref}"
            ),
        )
        counts["complete" if packet.complete else "incomplete"] += 1
    return counts


def _top_version(packet: JobPacket, revision: PacketDocument) -> int:
    """Which revision superseded this one — the current one of its type."""
    return next(
        (
            current.version for current in packet.current
            if current.document_type == revision.document_type
        ),
        revision.version,
    )


# ---------------------------------------------------------------------------
# c1 · the screen
# ---------------------------------------------------------------------------


def screen_packets(
    packets: list[JobPacket],
    crewless: list[SelectedJob],
    prior_empty_windows: dict[str, int],
    policy: FieldDocumentPolicy,
) -> dict:
    """Pure: what is missing, for how long, and whose crew is a mystery.

    Reads its arguments and nothing else, which is why `x2` did the joining
    and `x3` did the walking. Every job the window answered gets a row here,
    including the ones nothing is wrong with, because a job the journal does
    not mention is a job nobody can check.

    Three findings and only two of them can become a Task:

    - **a partial packet is informational at every window.** A job with plans
      and a site map but no crew hours sheet is a normal Tuesday, and the
      governed page says so in one line (`- partial packet: informational`).
    - **an empty packet is counted until the threshold.** The design's own
      words — *log as information and continue; notify if recurring* — which
      the recurrence count is exactly.
    - **an unresolvable crew is asked about once**, and never as a document
      question.
    """
    partial: list[dict] = []
    empty: list[dict] = []
    past: list[dict] = []
    windows: dict[str, int] = {}
    for packet in packets:
        if packet.empty:
            count = prior_empty_windows.get(packet.job_natural_key, 0) + 1
            windows[packet.job_natural_key] = count
            row = {
                "job_natural_key": packet.job_natural_key,
                "object_ref": packet.job_ref,
                "job_number": packet.job_number,
                "label": packet.label or packet.job_label,
                "customer": packet.customer,
                "crew": packet.crew_label,
                "load_date": packet.load_date,
                "folder_path": packet.folder_path,
                "folder_found": packet.folder_found,
                "unclassified": packet.unclassified,
                "missing": list(packet.missing),
                "windows": count,
            }
            empty.append(row)
            if count >= max(1, policy.missing_windows):
                past.append(row)
            continue
        if packet.missing:
            partial.append({
                "job_natural_key": packet.job_natural_key,
                "object_ref": packet.job_ref,
                "job_number": packet.job_number,
                "label": packet.label or packet.job_label,
                "missing": list(packet.missing),
                "held": [d.document_type for d in packet.current],
            })

    crews: list[dict] = [
        {
            "job_natural_key": job.natural_key,
            "object_ref": job.object_ref,
            "job_number": job.job_number,
            "label": job.job_label,
            "customer": job.keyword_source,
            "crew_id": job.crew_id,
            "foreman": job.foreman_name,
            "load_date": job.load_date,
            "former_crews": list(job.former_crews),
        }
        for job in crewless
    ]
    return {
        "partial": partial,
        "empty": empty,
        "past_threshold": past,
        "crewless": crews,
        "windows": windows,
        "counts": {
            "answered": len(packets),
            "complete": sum(1 for p in packets if p.complete),
            "partial": len(partial),
            "empty": len(empty),
            "past_threshold": len(past),
            "crewless": len(crews),
        },
        "threshold": policy.missing_windows,
    }


# ---------------------------------------------------------------------------
# k1 · the desk
# ---------------------------------------------------------------------------


def banded_missing(rows: list[dict], policy: FieldDocumentPolicy) -> float:
    """The number the governed table bands one aggregate on.

    Its left column is `Missing types`, and a class of jobs has no single
    number — so the aggregate is banded by the worst row: the most types any
    one job in it is missing. An aggregate that asks no document question at
    all bands on 0, which is the fallback band and exactly where the document
    sends it.
    """
    counts = [
        float(len(row.get("missing") or [])) for row in rows
    ]
    return max(counts) if counts else 0.0


def desk_for(
    policy: FieldDocumentPolicy, rows: list[dict]
) -> tuple[str, str]:
    """Which desk one aggregate lands on, and the sentence that says why.

    Read through the governed approval table like any elevated request. Two
    honest outcomes: a queue slug the table names, or the fallback —
    including every case where the table names a *resolver*. `submitter` and
    `manager-of(submitter)` cannot be answered here and the document says why
    in its own voice: no person handed these jobs over, they arrived from a
    job system on a schedule. Falling back is stated rather than swallowed,
    because a deployment quietly routing everything to the fallback is a
    broken table wearing a working flow.
    """
    target = policy.routing.target_for(banded_missing(rows, policy))
    fallback = policy.routing.fallback or DEFAULT_DESK
    if target in (routing.SUBMITTER, routing.MANAGER_OF):
        return fallback, (
            f"`{target}` cannot be answered by an aggregate about jobs — no "
            f"person handed these over, they arrived from a job system on a "
            f"schedule — so the governed fallback desk `{fallback}` stands"
        )
    if not target:
        return fallback, (
            f"the approval table named no desk, so the governed fallback "
            f"`{fallback}` stands"
        )
    return target, "the governed approval table's desk for these jobs"


def _crew_summary(rows: list[dict]) -> str:
    """The unrecognized crews, once each, with the name the source printed.

    Once each and not once per job: the identity aggregate's whole point is
    that eleven jobs erected by one subcontractor are one question.
    """
    seen: dict[str, str] = {}
    for row in rows:
        crew = str(row.get("crew_id") or "")
        if crew and crew not in seen:
            seen[crew] = str(row.get("foreman") or "")
    return ", ".join(
        f"`{crew}`" + (f" — {name}" if name else "")
        for crew, name in sorted(seen.items())
    )


def _aggregate_task_id(name: str, keys: list[str]) -> str:
    """One id per (class, finding). Stable across windows, by construction.

    Derived from the finding rather than from the window, which is a
    deliberate reading of "at most two Tasks per window": a window is the unit
    of work, but the *question* is the finding — the same three jobs with
    nothing filed found again tomorrow is the same question, and a daily
    cadence that opened a new copy of it every morning would bury the desk it
    is trying to help. A changed finding is a different question and gets its
    own Task. `credential_tracking._aggregate_task_id`'s property, for its
    reason.
    """
    digest = hashlib.sha256("|".join(sorted(keys)).encode()).hexdigest()[:10]
    return f"task_field_{name}_{digest}"


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------


class FieldDocumentsService:
    """Asks the graph the standing question, links the packets, and reports.

    Holds no directory of its own, for `ApIntakeService`'s reason: what it
    produces is claims and edges on rows that already exist, and Tasks on a
    desk. A table here would be a second account of the same packet state,
    free to disagree with the graph the moment somebody edited one of them.
    """

    def __init__(
        self,
        graph: GraphService,
        knowledge,  # KnowledgeService — duck-typed to avoid an import cycle
        tasks: TaskService,
        engine: RunEngine | None = None,
        gateway: ActionGateway | None = None,
        dispatcher: Dispatcher | None = None,
    ) -> None:
        self.graph = graph
        self.knowledge = knowledge
        self.tasks = tasks
        self.engine = engine
        # Never None, for the reason given at the same line in `payables`: a
        # service wired without a Gateway still writes through one.
        self.gateway = gateway or ActionGateway(
            graph or GraphService(InMemoryGraphStore()), InMemoryLedgerStore()
        )
        # Never bypassed, same reason: a run nothing recorded a cause for is a
        # governance problem, not a wiring one.
        self.dispatcher = dispatcher or (
            Dispatcher(engine, InMemoryEventLog())
            if engine is not None else None
        )
        if engine is not None:
            from .. import shipped_versions

            for number, module in sorted(shipped_versions(FLOW_ID).items()):
                # No `modelcall.metered`: this flow makes no model call and
                # starts no nested run, so there is no frame below it whose
                # ledger rows would lose their run coordinates.
                engine.register(
                    FLOW_ID, module.build_body(self),
                    version=number, default=(number == DEFAULT_VERSION),
                )

    # -- governed reads ------------------------------------------------------

    def read_policy(self) -> FieldDocumentPolicy:
        return read_field_policy(self.knowledge)

    # -- x2 ------------------------------------------------------------------

    def select(
        self,
        policy: FieldDocumentPolicy,
        today: date,
        exclude_run_id: str = "",
    ) -> dict:
        """The window's work list, and everything it left behind.

        The question the three retired bots were between them asking: *which
        scheduled job inside the horizon does not have its four documents
        linked at their latest revision?* Asked of the graph rather than of
        the delivery, for `ap_intake`'s reason — what the delivery carried is
        already in the graph by the time this runs, and a work list scoped to
        one delivery's rows would lose the jobs an earlier window deferred.

        Two answers come out, kept apart because they are different answers: a
        job with a crew (which gets a packet) and a job whose crew id resolves
        to no `Team` (which gets an identity question and **no** document
        question). The bound governs the pass and the deferred remainder is
        reported — a count nobody states is a backlog nobody knows about.
        """
        statuses = {
            word.strip().lower() for word in policy.statuses if word.strip()
        }
        horizon_end = today + timedelta(days=max(0, policy.horizon_days))
        in_scope: list[SelectedJob] = []
        exclusions = {name: 0 for name in EXCLUSIONS}
        counts = {
            "superseded": 0, "crew_unstated": 0, "reassigned": 0,
            "company_resolved": 0,
        }
        notes: list[str] = []

        for record in sorted(
            self.graph.find(onto.JOB), key=lambda o: o.natural_key
        ):
            if record.status == "superseded":
                counts["superseded"] += 1
                continue
            props = record.props
            status = str(props.get("status") or "").strip()
            if status.lower() not in statuses:
                # The design's *"once the job moves to IN PROGRESS, stop
                # checking for new versions"*, kept by never starting: a job
                # whose status has moved off the governed scope is not
                # selected at all, so there is nothing to switch off.
                exclusions[STATUS_OUT_OF_SCOPE] += 1
                continue
            load = read_date(props.get("load_date"))
            if load is None:
                exclusions[NO_LOAD_DATE] += 1
                if len(notes) < 10:
                    notes.append(
                        f"{record.label}: scheduled with no load date this "
                        f"window could place in a horizon"
                    )
                continue
            if load < today:
                exclusions[LOAD_DATE_PAST] += 1
                if len(notes) < 10:
                    notes.append(
                        f"{record.label}: still `{status}` with a load date "
                        f"of {load.isoformat()}, already past"
                    )
                continue
            if load > horizon_end:
                exclusions[BEYOND_HORIZON] += 1
                continue
            job = self._selected(record, load, today)
            if job.company_name:
                counts["company_resolved"] += 1
            if job.former_crews:
                counts["reassigned"] += 1
            if not job.crew_stated:
                counts["crew_unstated"] += 1
            in_scope.append(job)

        # Soonest to load first, so a window that stops at its bound and the
        # window after it agree about where the boundary was, and so what
        # waits is what has the most time left before a crew drives.
        ordered = sorted(in_scope, key=lambda j: (j.load_date, j.job_number))
        limit = max(0, policy.window_limit)
        kept = ordered[:limit]
        return {
            "jobs": [job.model_dump() for job in kept if job.crew_resolved
                     or not job.crew_stated],
            "crewless": [
                job.model_dump() for job in kept
                if job.crew_stated and not job.crew_resolved
            ],
            "considered": len(in_scope) + sum(exclusions.values()),
            "in_horizon": len(in_scope),
            "taken": len(kept),
            "deferred": max(0, len(in_scope) - len(kept)),
            "exclusions": exclusions,
            "notes": notes,
            # The state the design kept in a `Last Run Status` cell, read
            # where it actually lives. Fetched here rather than in the check
            # so the check stays pure — `credential_tracking`'s x2 does the
            # joining for the same reason its Rule does none.
            "prior_empty_windows": self.empty_window_history(exclude_run_id),
            **counts,
        }

    def _selected(
        self, record, load: date, today: date
    ) -> SelectedJob:
        """One job with its crew, its customer and its arithmetic resolved."""
        props = record.props
        crew_id = str(props.get("crew_id") or "").strip()
        live = self.graph.neighbors(record.id, "assigned_to", "out")
        former = [
            team.label or team.natural_key
            for link, team in self.graph.neighbors(
                record.id, "assigned_to", "out", include_reversed=True,
            )
            if not link.live
        ]
        company = next(
            (
                other for _, other in self.graph.neighbors(
                    record.id, "job_for", "out"
                )
            ),
            None,
        )
        return SelectedJob(
            object_id=record.id,
            object_ref=record.ref(),
            natural_key=record.natural_key,
            job_number=str(props.get("job_number") or record.natural_key),
            job_label=record.label or record.natural_key,
            source=str(props.get("source") or ""),
            status=str(props.get("status") or ""),
            customer_name=str(props.get("customer_name") or ""),
            company_name=str(
                (company.props.get("name") if company else "") or ""
            ),
            load_date=load.isoformat(),
            days_to_load=(load - today).days,
            crew_id=crew_id,
            foreman_name=str(props.get("foreman_name") or ""),
            crew_ref=live[0][1].ref() if live else "",
            crew_label=(
                (live[0][1].label or live[0][1].natural_key) if live else ""
            ),
            former_crews=former,
            crew_resolved=bool(live),
            crew_stated=bool(crew_id),
        )

    def empty_window_history(self, exclude_run_id: str = "") -> dict[str, int]:
        """How many consecutive windows each job has had none of the four.

        The design's `Last Notified` / `Last Run Status` cells, read out of
        this flow's own run journal instead — which is where the record of a
        pass belongs, and which is why there is no counter field on `Job` for
        somebody to hand-edit. Two lists on every window's output make it
        answerable: the jobs the window *answered*, and the ones it found
        empty.

        Walked newest first and stopped per job at the first window that
        answered it and found something, because *consecutive* is the whole of
        what the threshold means: a job that was empty, then filed, then empty
        again is on its first window, not its third.

        One scan of the run store per window, for `_teams_by_cost_center`'s
        arithmetic: the alternative is a query per job, and a window answers
        forty of them.
        """
        if self.engine is None:
            return {}
        # Fresh rather than the snapshot `list` serves: this counts
        # *consecutive* empty windows to decide whether to put work in front of
        # a person, and a window another instance just finished has to be in
        # the count. It runs four times an hour, not once per page load, so the
        # scan it costs is not the one worth saving.
        runs = [
            run for run in self.engine.store.list_fresh()
            if run.flow_id == FLOW_ID and run.status == "succeeded"
            and run.id != exclude_run_id
        ]
        runs.sort(key=lambda run: run.started_at_iso, reverse=True)
        counts: dict[str, int] = {}
        settled: set[str] = set()
        for run in runs:
            empty = {str(key) for key in (run.outputs.get("empty_jobs") or [])}
            answered = {
                str(key) for key in (run.outputs.get("answered_jobs") or [])
            }
            for key in empty - settled:
                counts[key] = counts.get(key, 0) + 1
            settled |= answered - empty
        return counts

    # -- x3 ------------------------------------------------------------------

    def resolve_packets(
        self, jobs: list[SelectedJob], policy: FieldDocumentPolicy
    ) -> dict:
        """Per job: the latest revision of each governed type, and the label.

        The design's *"the topmost one in the panel is the most recent"* is not
        a rule this system can keep — a document system sorted by name says
        nothing about age — so it is a comparison rather than a position: of
        the documents matching one type's pattern, the highest revision wins,
        and where two share a revision the more recently created one does.
        Compared at full timestamp precision, because the estate's revisions
        of one document can be seconds or microseconds apart.
        """
        folders = self._folders_by_path()
        labels = derive_labels(
            [(job.job_number, job.keyword_source) for job in jobs], policy
        )
        packets: list[JobPacket] = []
        counts = {
            "folders_found": 0, "folders_missing": 0, "documents": 0,
            "linked": 0, "superseded": 0, "already_superseded": 0,
            "unclassified": 0, "complete": 0, "collisions": 0,
            "stopwords_applied": 0,
        }
        for job in jobs:
            path = policy.job_folder_path(job.job_number)
            folder = folders.get(path.casefold())
            label = labels.get(job.job_number)
            packet = JobPacket(
                job_id=job.object_id,
                job_ref=job.object_ref,
                job_natural_key=job.natural_key,
                job_number=job.job_number,
                job_label=job.job_label,
                customer=job.keyword_source,
                crew_label=job.crew_label,
                crew_id=job.crew_id,
                load_date=job.load_date,
                folder_path=path,
                label=label.label if label else job.job_number,
                label_rule=label.rule if label else "",
            )
            if label is not None and label.collided:
                counts["collisions"] += 1
            if label is not None and label.skipped_stopword:
                counts["stopwords_applied"] += 1
            if folder is None:
                # A job the office has filed nothing for yet. Not an error and
                # not a Task on its own: the recurrence threshold decides when
                # it becomes somebody's morning.
                counts["folders_missing"] += 1
                packet.missing = policy.type_names
                packets.append(packet)
                continue
            packet.folder_found = True
            packet.folder_id = folder.id
            packet.folder_ref = folder.ref()
            counts["folders_found"] += 1

            filed, already, unclassified = self._classify(
                folder, job, policy,
            )
            packet.already_superseded = already
            packet.unclassified = unclassified
            counts["already_superseded"] += already
            counts["unclassified"] += unclassified
            counts["documents"] += sum(len(v) for v in filed.values())

            live_links = {
                other.id: link.id for link, other in self.graph.neighbors(
                    job.object_id, "documented_by", "out"
                )
            }
            for governed in sorted(
                policy.document_types, key=lambda t: t.order
            ):
                revisions = filed.get(governed.name) or []
                if not revisions:
                    packet.missing.append(governed.name)
                    continue
                revisions.sort(key=lambda d: (d.version, d.created_at))
                winner = revisions[-1]
                packet.current.append(winner)
                for loser in revisions[:-1]:
                    loser.link_id = live_links.get(loser.object_id, "")
                    packet.superseded.append(loser)
            packet.complete = not packet.missing
            counts["linked"] += len(packet.current)
            counts["superseded"] += len(packet.superseded)
            counts["complete"] += 1 if packet.complete else 0
            packets.append(packet)
        return {
            "packets": [packet.model_dump() for packet in packets],
            "labels": [label.model_dump() for label in labels.values()],
            **counts,
        }

    def _classify(
        self, folder, job: SelectedJob, policy: FieldDocumentPolicy
    ) -> tuple[dict[str, list[PacketDocument]], int, int]:
        """Every file filed under one job folder, against the four patterns.

        A row a previous pass already marked superseded is skipped and
        counted: it is not the current revision of anything, and re-writing it
        would be a second ledger row saying the same thing about the same row.
        """
        patterns = [
            (governed, pattern_regex(governed.pattern, job.job_number))
            for governed in sorted(
                policy.document_types, key=lambda t: t.order
            )
        ]
        filed: dict[str, list[PacketDocument]] = {}
        already = 0
        unclassified = 0
        for _, document in self.graph.neighbors(
            folder.id, "filed_under", "in"
        ):
            if document.type != onto.DOCUMENT:
                continue
            if document.status == "superseded":
                already += 1
                continue
            props = document.props
            filename = str(props.get("filename") or document.label or "")
            governed = next(
                (
                    spec for spec, pattern in patterns
                    if pattern.match(filename)
                ),
                None,
            )
            if governed is None:
                unclassified += 1
                continue
            filed.setdefault(governed.name, []).append(PacketDocument(
                document_type=governed.name,
                order=governed.order,
                object_id=document.id,
                object_ref=document.ref(),
                natural_key=document.natural_key,
                filename=filename,
                version=int(props.get(policy.version_field) or 0),
                created_at=str(
                    getattr(document, policy.tiebreak_field, "") or ""
                ),
            ))
        return filed, already, unclassified

    def _folders_by_path(self) -> dict[str, object]:
        """Every `Folder` the graph holds, by its full path, case-folded.

        One scan per run rather than a lookup per job, the way
        `connectors.production._folders_by_leaf` does it: a window answers
        forty jobs and `find` walks the store.

        Deliberately **not** scoped to a source. This flow is woken by two
        different feeds — the production one carries `erp-jobs` and the
        document one `dms-library` — so scoping the walk to the waking
        delivery's source would have found no folders at all on every other
        window. A folder path is already source-scoped in its own natural key,
        and a second document system filing the same job is a second real
        place it is filed.

        Case-folded because the governed root is prose a superintendent types
        (`Job Documents`), and a page that spelled it `job documents` should
        find the tree rather than silently answer nothing.
        """
        by_path: dict[str, object] = {}
        for record in self.graph.find(onto.FOLDER):
            path = str(record.props.get("path") or "")
            if path:
                by_path.setdefault(path.casefold(), record)
        return by_path

    # -- a1 ------------------------------------------------------------------

    def _act(self, ctx: RunContext, step_id: str):
        """The window's one guarded write, committed by the Flow itself."""
        return self.gateway.action(
            LINK_ACTION, DOCUMENTS_FLOW, run=ctx.record, step_id=step_id,
        )

    # -- k1 ------------------------------------------------------------------

    def resume_for_task(self, task: Task, principal: Principal | None = None):
        """`principal` is the human who decided, carried to the commit.

        `DepartmentSchedulingService.resume_for_task`'s shape, and it answers
        `None` today for a reason worth reading rather than fixing: this flow
        does not gate. `k1` raises at most two aggregate Tasks and the run
        walks on to `e1`, so no run is ever suspended on one of them and
        `find_by_task` has nothing to find. The hook is here anyway because
        the console has to have somewhere to send a decision — the routing
        table in `tasks_api.RESUME_HOOKS` is the one place a reader can learn
        which flow answers for which Task, and a flow missing from it is a
        judgment that reaches nothing with no line anywhere saying so. A later
        version that does suspend on its own Task resumes through this without
        another edit.
        """
        if task.flow_id != FLOW_ID or self.engine is None:
            return None
        return self.engine.resume_for_task(task.id, principal=principal)

    def apply_task_decision(
        self, task: Task, principal: Principal | None = None,
    ):
        """The console's door, shaped like every other flow's."""
        return self.resume_for_task(task, principal)

    def raise_gap_tasks(
        self, ctx: RunContext, screened: dict, policy: FieldDocumentPolicy
    ) -> dict:
        """At most two aggregate Tasks per window, and never one per job.

        Two questions, one each: *why has nothing been filed for these jobs*
        and *who is this crew*. The second is the design's SUBCONTRACTOR case
        as one identity question rather than N document questions, which is
        the whole of what the governed page asks for — and a job in it is
        deliberately absent from the first, because no document question was
        asked about it.

        `TaskService.create` is a put, so the existence guard is what keeps an
        answered Task from being reset to `open` — `raise_desk_tasks`'
        property, for its reason.
        """
        raised: list[str] = []
        standing = 0
        findings: list[tuple[str, list[dict], list[str]]] = [
            (
                NO_DOCUMENTS,
                screened["past_threshold"],
                [row["job_natural_key"] for row in screened["past_threshold"]],
            ),
            (
                UNRESOLVED_CREW,
                screened["crewless"],
                # Keyed on the crew ids and not on the jobs, because the
                # question is *who is this crew*: a fourth job for the same
                # unrecognized crew tomorrow is the same question, and a new
                # Task for it would be a second copy of one identity problem.
                sorted({
                    row["crew_id"] for row in screened["crewless"]
                    if row["crew_id"]
                }),
            ),
        ]
        for name, rows, keys in findings:
            if not rows:
                # A class with nothing in it asks nobody anything. This is what
                # makes a well-filed window a silent one.
                continue
            copy = AGGREGATE_COPY[name]
            queue, why = desk_for(policy, rows)
            task_id = _aggregate_task_id(name, keys or [
                row["job_natural_key"] for row in rows
            ])
            if self.tasks.get(task_id) is not None:
                standing += 1
                continue
            self.tasks.create(Task(
                id=task_id,
                kind="exception",
                title=copy.title.format(n=len(rows)),
                summary=(
                    f"{copy.question}\n\n"
                    f"{len(rows)} job(s), aggregated: one question per class "
                    f"per window and never one per job. Routed to `{queue}` "
                    f"— {why}. Read against {policy.source_ref}."
                    + (
                        "\n\nThe governed page's own words: "
                        + policy.crew_rule
                        if name == UNRESOLVED_CREW else ""
                    )
                ),
                queue=queue,
                priority=copy.priority,
                flow_id=FLOW_ID,
                flow_version=ctx.record.flow_version,
                run_id=ctx.record.id,
                case_ref=f"field-documents-gap:{name}",
                refs=[policy.source_ref] + [
                    row["object_ref"] for row in rows[:20]
                ],
                evidence=[
                    TaskEvidence(label="Finding", value=name),
                    TaskEvidence(label="Jobs", value=str(len(rows))),
                ] + ([
                    TaskEvidence(
                        label="Threshold",
                        value=(
                            f"{policy.missing_windows} consecutive window(s) "
                            f"with none of the "
                            f"{len(policy.type_names)} governed types. A "
                            f"partial packet is "
                            f"{policy.partial_packet} at every window and "
                            f"never raises a task."
                        ),
                    ),
                ] if name == NO_DOCUMENTS else [
                    TaskEvidence(
                        label="Crews",
                        value=_crew_summary(rows) or "—",
                    ),
                ]) + [
                    TaskEvidence(
                        label=row.get("label") or row["job_number"],
                        value=" · ".join(filter(None, (
                            row.get("customer") or "",
                            f"loads {row['load_date']}"
                            if row.get("load_date") else "",
                            (
                                f"crew `{row['crew_id']}`"
                                f" — {row['foreman']}"
                                if row.get("foreman") else
                                f"crew `{row['crew_id']}`"
                            ) if row.get("crew_id") else "",
                            (
                                f"{row['windows']} consecutive window(s) with "
                                "nothing filed"
                            ) if row.get("windows") else "",
                            (
                                f"folder {row['folder_path']} not in the "
                                "document tree"
                            ) if row.get("folder_found") is False else "",
                            (
                                f"{row['unclassified']} file(s) under "
                                "it match none of the governed patterns"
                            ) if row.get("unclassified") else "",
                        ))),
                    )
                    for row in rows[:12]
                ],
                # No proposal, and for both classes. There is no Action in
                # this system that files a document on somebody's behalf or
                # mints a crew nobody has hired — the answer to each of these
                # is a fact somebody supplies, not a mutation somebody
                # authorizes.
                resumes=(
                    "nothing resumes; a document filed or a crew created is "
                    "read by the next window, and a name corrected on the "
                    "review screen is a `human` claim which outranks the feed"
                ),
                created_at_iso=utcnow().isoformat(),
            ))
            raised.append(task_id)
        return {
            "gap_tasks": len(raised),
            "gap_task_ids": raised,
            "gap_tasks_standing": standing,
        }


def build_field_documents_service(
    graph: GraphService,
    knowledge,
    tasks: TaskService,
    engine: RunEngine | None = None,
    gateway: ActionGateway | None = None,
    dispatcher: Dispatcher | None = None,
) -> FieldDocumentsService:
    return FieldDocumentsService(
        graph, knowledge, tasks, engine, gateway, dispatcher,
    )
