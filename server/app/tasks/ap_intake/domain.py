"""AP intake: the customer's own filing tree becomes accounts-payable work.

Before this flow, an invoice reached the accounts-payable process by somebody
composing one in the console. The vendor's actual PDF sat in their document
system, mirrored by `dms-sync` as a `Document` row with a `storage_ref` and no
`sha256` — which is precisely `dms-sync` saying *we know this file exists and
where it is, and nobody has read it*. This flow is the thing that reads it.

**Windowed, and its per-case sibling is `accounts-payable`.** A delivery of
forty documents is a batch: it lands bulk observations (one guarded Action for
the whole window, never one per row) and reports what it could not do as counts
on the run. An invoice whose bytes will not fetch does not become a Task that
forty other invoices queue behind — the doctrine's line, and the same one every
sync feed here already draws. The one Task it can raise is an aggregate: when
*every* fetch in a window failed, that is not forty bad documents, it is one
broken door, and it goes to the operations desk.

What it emits *is* per record, and that is the seam rather than a contradiction:
`InvoiceReceived` carries one document id, the Trigger table turns it into one
`accounts-payable` run, and that run is where per-case commitment happens. The
batch stops being a batch exactly here.

**What "an AP invoice" means.** Not a filename pattern and not a guess from the
bytes: a document is a supplier invoice when the customer filed it in the
folders they file supplier invoices in. Where those are is knowledge — the
`ap-intake-policy` asset — read at a revision like every other governed value,
because a customer who reorganizes their filing tree should be able to say so
without a deploy. The tree itself is already in the graph as `Folder` objects
with full paths, landed by `dms-sync`, so nothing here parses a path convention
of its own invention.

**Why the work list comes from the graph and not from the delivery.** The
delivery is the *wake*: what it carries is already in the graph by the time this
runs (the Trigger table starts `dms-sync` first, and the Dispatcher starts
matched responses in order). What the flow then does is ask the graph the
standing question — which AP-filed documents has nobody read yet — and answer as
much of it as one window is allowed to. A window that only gets through half of
a backlog leaves the rest for the next one, with no cursor to skew and nothing
lost, which is not true of a work list scoped to one delivery's rows.
"""

from __future__ import annotations

import base64
import logging
import re
from typing import Protocol

from pydantic import BaseModel, Field

from ... import media, routing
from ...auth import flow_principal
from ...connectors.dms import DOCUMENT_LIBRARY
from ...dispatch import Dispatcher, InMemoryEventLog
from ...documents import DocumentService
from ...gateway import ActionGateway, InMemoryLedgerStore
from ...graph import Claim, GraphService, GraphWriter, InMemoryGraphStore
from ...models import utcnow
from ...ontology import names as onto
from ...runs import RunContext, RunEngine
from ...task_queue import Task, TaskEvidence, TaskService

log = logging.getLogger("cumulus.ap_intake")

FLOW_ID = "ap-intake"
DEFAULT_VERSION = 1
POLICY_ASSET_ID = "ap-intake-policy"

#: The fact this flow emits, one per invoice. Bound to `accounts-payable` in
#: `dispatch.TRIGGERS`, which is where the per-case response is declared.
INVOICE_EVENT = "InvoiceReceived"

#: Who this flow commits its one observation as. No queue: nothing here is
#: anybody's work — a file was read, and reading it was not a judgment.
INTAKE_FLOW = flow_principal(FLOW_ID)

#: What the flow does when the corpus holds no intake policy. A fresh
#: deployment still has to work, and an empty answer here is a flow that
#: silently does nothing — the failure that looks exactly like success. The
#: value is this estate's own top-level AP folder as its document system spells
#: it, and the run says out loud when it is running on the fallback rather than
#: on the asset.
DEFAULT_INVOICE_FOLDERS: tuple[str, ...] = ("Accounts Payable",)

#: How many invoices one window may take on when the asset does not say. The
#: bound exists because a delivery is answered inside the HTTP request that
#: made it: each invoice this flow raises runs a whole accounts-payable case,
#: extraction included, before the delivery is acknowledged. Unbounded, the
#: first pull of a seeded estate would answer forty invoices in one request and
#: time out. The remainder is not dropped — it is the next window's work.
DEFAULT_WINDOW_LIMIT = 25


class IntakePolicy(BaseModel):
    """The governed values this flow reads, and the revision it read them at."""

    folders: list[str] = Field(default_factory=lambda: list(DEFAULT_INVOICE_FOLDERS))
    window_limit: int = DEFAULT_WINDOW_LIMIT
    source_ref: str = f"knowledge:{POLICY_ASSET_ID}"
    found_asset: bool = False


def parse_intake_policy(content: str) -> IntakePolicy:
    """Read the folders and the window bound out of the governed document.

    Line-oriented and forgiving in the same way `parse_posting_policy` is: a
    customer edits prose, and a policy that stopped parsing because somebody
    rewrapped a paragraph would take the flow down with it.
    """
    folders = [
        match.group(1).strip().strip("`").strip("/")
        for match in re.finditer(
            r"^\s*[-*]\s*invoice folder:\s*(.+?)\s*$",
            content, re.IGNORECASE | re.MULTILINE,
        )
    ]
    values = IntakePolicy(found_asset=True)
    if folders:
        values.folders = folders
    limit = re.search(
        r"invoices per intake window:?\s*(\d+)", content, re.IGNORECASE
    )
    if limit:
        # Zero is a real answer — "stop starting runs" — and is honored. A
        # negative one cannot be written by this pattern.
        values.window_limit = int(limit.group(1))
    return values


class ContentFetcher(Protocol):
    """Whatever can turn a customer-resolvable address into bytes.

    A Protocol rather than the `EstateConnector` type, for the reason the
    readers are Protocols: the flow needs one call, and a test that had to
    build a connector to exercise a folder filter would be testing the wrong
    thing. In a deployment it *is* the connector — the one component allowed to
    reach outward, and the only one that knows which hosts are the customer's.
    """

    def fetch_content(self, url: str) -> tuple[bytes, str]: ...


class InvoiceCandidate(BaseModel):
    """One AP-filed document this window is going to try to read.

    The selection step's output, so it is also what a person reading the run
    journal sees before anything was fetched — which is why the graph ref and
    the folder are on it and not only the address.
    """

    external_id: str
    #: `document:<id>` — the row in the graph this is about, so a reader can
    #: open it from the step that named it.
    object_ref: str
    natural_key: str
    filename: str
    folder_path: str
    storage_ref: str
    latest_version: int = 0


class ApIntakeService:
    """Reads the filing tree, fetches what is new, and raises the invoices.

    Holds no directory of its own. What it produces is documents in the
    document store and Events on the log; the *record* of an invoice is the
    accounts-payable process's, and a second table here would be a second
    account of the same thing.
    """

    def __init__(
        self,
        graph: GraphService,
        knowledge,  # KnowledgeService — duck-typed to avoid an import cycle
        documents: DocumentService,
        tasks: TaskService,
        engine: RunEngine | None = None,
        gateway: ActionGateway | None = None,
        dispatcher: Dispatcher | None = None,
        fetcher: ContentFetcher | None = None,
    ) -> None:
        self.graph = graph
        self.knowledge = knowledge
        self.documents = documents
        self.tasks = tasks
        self.engine = engine
        #: None is a working deployment — a Cumulus with no estate registered
        #: has nothing to fetch from, and the flow says so on the run rather
        #: than failing. It is not a stub: there is no substitute for bytes.
        self.fetcher = fetcher
        # Never None, for the reason given at the same line in `payables`: a
        # service wired without a Gateway still writes through one.
        self.gateway = gateway or ActionGateway(
            graph or GraphService(InMemoryGraphStore()), InMemoryLedgerStore()
        )
        # Never bypassed, same reason: a run nothing recorded a cause for is a
        # governance problem, not a wiring one.
        self.dispatcher = dispatcher or (
            Dispatcher(engine, InMemoryEventLog()) if engine is not None else None
        )
        if engine is not None:
            from .. import shipped_versions
            from ... import modelcall

            for number, module in sorted(shipped_versions(FLOW_ID).items()):
                # `metered` for the reason it is applied at every other
                # registration loop: this body makes no ModelCall itself, but
                # the accounts-payable runs it starts are driven from inside
                # it, and a context that stopped at this frame would leave
                # their ledger rows without run coordinates.
                engine.register(
                    FLOW_ID, modelcall.metered(module.build_body(self)),
                    version=number, default=(number == DEFAULT_VERSION),
                )

    # -- governed reads ------------------------------------------------------

    def read_policy(self) -> IntakePolicy:
        asset = self.knowledge.repo.get(POLICY_ASSET_ID) if self.knowledge else None
        if asset is None:
            log.warning(
                "AP intake policy missing; using the built-in folders",
                extra={"assetId": POLICY_ASSET_ID},
            )
            return IntakePolicy(found_asset=False)
        values = parse_intake_policy(asset.content)
        values.source_ref = (
            f"knowledge:{asset.id}@rev{getattr(asset, 'revision', 1)}"
        )
        return values

    # -- step internals ------------------------------------------------------

    def _candidates(self, source: str, policy: IntakePolicy) -> list[dict]:
        """AP-filed documents nobody has read the bytes of yet.

        Walked from the folders rather than from the documents. The AP tree is
        a couple of dozen `Folder` rows whatever the library's size, so this
        costs one neighbour read per AP folder instead of one per delivered
        document — the same arithmetic `dms-sync` fixed in its own body.

        A row is a candidate when its pointer resolves to a revision the map
        has not read. `sha256` is the marker because it already means exactly
        that (see `connectors.dms`), and `read_version` is beside it because a
        hash alone cannot tell a file nobody has read from one whose vendor
        reissued it — which is not hypothetical here: a disputed invoice comes
        back corrected, at v2, under the same document id.
        """
        folders = [
            folder for folder in self.graph.find(
                onto.FOLDER, prop_equals={"source": source}
            )
            if _under(str(folder.props.get("path") or ""), policy.folders)
        ]
        found: list[dict] = []
        seen: set[str] = set()
        for folder in sorted(folders, key=lambda f: str(f.props.get("path") or "")):
            path = str(folder.props.get("path") or "")
            for _, document in self.graph.neighbors(folder.id, "filed_under", "in"):
                if document.id in seen or document.status == "superseded":
                    continue
                seen.add(document.id)
                props = document.props
                storage_ref = str(props.get("storage_ref") or "")
                if not storage_ref:
                    # A row the map holds the bytes of already, or one whose
                    # source gave no address. Neither is intake's to fetch.
                    continue
                version = int(props.get("latest_version") or 0)
                if props.get("sha256") and int(props.get("read_version") or 0) == version:
                    continue
                found.append(InvoiceCandidate(
                    external_id=str(props.get("external_id") or ""),
                    object_ref=document.ref(),
                    natural_key=document.natural_key,
                    filename=str(props.get("filename") or document.label),
                    folder_path=path,
                    storage_ref=storage_ref,
                    latest_version=version,
                ).model_dump())
        # Stable order, so a window that stops at the limit and the window
        # after it agree about where the boundary was.
        found.sort(key=lambda c: (c["folder_path"], c["filename"], c["external_id"]))
        return found

    def _fetch_and_store(
        self,
        candidates: list[dict],
        run_id: str,
        writer: GraphWriter | None = None,
    ) -> dict:
        """Read each file, put the bytes in the map, and mark the row read.

        One guarded Action for the whole window — the bulk-observation shape a
        windowed flow owes the doctrine, and the reason a file that will not
        fetch is a counter rather than an exception.

        The store is keyed by content hash, so a file the map already holds
        under other circumstances costs one fetch and no second copy. It is
        still raised as an invoice: the same bytes filed twice by the customer
        is a duplicate *submission*, which is a question for the anomaly screen
        and not one this flow may answer by dropping the row.
        """
        graph = writer if writer is not None else self.graph
        counts = {
            "read": 0, "unreachable": 0, "unreadable": 0, "no_fetcher": 0,
        }
        stored: list[dict] = []
        notes: list[str] = []
        for raw_candidate in candidates:
            candidate = InvoiceCandidate.model_validate(raw_candidate)
            if self.fetcher is None:
                counts["no_fetcher"] += 1
                continue
            try:
                content, declared = self.fetcher.fetch_content(candidate.storage_ref)
            except Exception as exc:  # noqa: BLE001 — counted, never fatal
                counts["unreachable"] += 1
                notes.append(f"{candidate.filename}: {type(exc).__name__}: {exc}")
                log.warning(
                    "Invoice bytes did not fetch",
                    extra={
                        "runId": run_id, "documentId": candidate.external_id,
                        "storageRef": candidate.storage_ref, "error": str(exc),
                    },
                )
                continue
            # Sniffed from the bytes: the DMS's `contentType` is the uploader's
            # claim, and a scanned page filed as `application/octet-stream` is
            # still a page. What this refuses is a file no extractor can read —
            # counted rather than sent to a ModelCall that would fail at the
            # wire and escalate as an engineering fault.
            media_type = media.sniff(content, candidate.filename, declared)
            if not media.is_model_readable(media_type):
                counts["unreadable"] += 1
                notes.append(f"{candidate.filename}: {media_type} carries no page to read")
                continue
            record = self.documents.put_bytes(
                base64.b64encode(content).decode(),
                filename=candidate.filename, kind="invoice",
                uploaded_by=f"flow:{FLOW_ID}", run_id=run_id,
                media_type=media_type,
            )
            counts["read"] += 1
            # Ids and names, never the bytes. This dict is the step's output
            # and the step's output is the run journal, which a Firestore
            # document has to fit inside: twenty-five base64 invoices would
            # blow a 1MiB cap and take the whole window's journal with them.
            # The store already holds the artifact; the id is how to reach it.
            stored.append({
                "document_id": record.id,
                "external_id": candidate.external_id,
                "filename": candidate.filename,
                "folder_path": candidate.folder_path,
                "byte_size": record.byte_size,
            })
            # The row stops saying "nobody has read this". Two claims, because
            # one of them cannot answer the question on its own: the hash says
            # what was read and the version says which revision it was read
            # from, and a reissued invoice is exactly the case where a hash
            # alone would report a stale reading as a current one.
            graph.upsert(
                onto.DOCUMENT, candidate.natural_key,
                [
                    Claim(field="sha256", value=record.sha256,
                          source=f"flow:{FLOW_ID}"),
                    Claim(field="read_version", value=candidate.latest_version,
                          source=f"flow:{FLOW_ID}"),
                    Claim(field="kind", value="invoice",
                          source=f"flow:{FLOW_ID}"),
                ],
                label=candidate.filename or candidate.natural_key,
                provenance=f"read by {run_id}",
            )
        return {**counts, "stored": stored, "notes": notes[:10]}

    def _raise_invoices(self, source: str, stored: list[dict]) -> dict:
        """One `InvoiceReceived` per file, and one accounts-payable run each.

        A response starting another response is fan-out, which `dispatch` says
        needs a queue rather than a call and does not have one — so this is a
        synchronous call, and the cost is named where it is paid: the delivery
        that woke this flow is not acknowledged until every case it raised has
        run or suspended. That is what `window_limit` bounds.

        Both the stored id *and* the bytes travel on the event, and the bytes
        are read back out of the store rather than carried down from the fetch
        step — see the note there. Which accounts-payable version is live is
        the deployment's decision, not intake's: v8 reads the document id and
        v6/v7 read the bytes off the inputs, so an intake that spoke only to
        the candidate would break the fleet on the day it started emitting.
        """
        if self.dispatcher is None:
            raise RuntimeError("AP intake has no dispatcher wired")
        started: list[dict] = []
        failed: list[dict] = []
        for item in stored:
            record = self.documents.get(item["document_id"])
            inputs = {
                "submitted_by": f"flow:{FLOW_ID}",
                "document_id": item["document_id"],
                "filename": item["filename"],
                "image_b64": record.content_b64 if record is not None else "",
                "submitted_at_iso": utcnow().isoformat(),
                "source": source,
            }
            dispatch = self.dispatcher.emit(
                INVOICE_EVENT, inputs, actor=f"flow:{FLOW_ID}",
                case_ref=item["document_id"],
            )
            for run in dispatch.runs:
                row = {
                    "run_id": run.id, "status": run.status,
                    "document_id": item["document_id"],
                    "filename": item["filename"],
                }
                # A case that failed is this window's report, not its
                # exception: `RunEngine._drive` never raises, so the only way
                # a broken invoice could stop the other thirty-nine is if this
                # loop decided to let it.
                (failed if run.status == "failed" else started).append(row)
        return {
            "raised": len(started) + len(failed),
            "runs": started, "failed_runs": failed,
        }

    def _raise_intake_fault(self, ctx: RunContext, outcome: dict) -> str:
        """The aggregate escalation, and the only Task this flow can raise.

        Raised when a window reached for files and got nothing at all — every
        candidate unreachable. Forty documents failing one at a time is forty
        counters; forty failing together is one address that stopped answering,
        which is an operations fault and nobody's business decision.

        Idempotent on the run id, because a re-entered window must not open a
        second copy of the same fault.
        """
        task_id = f"task_{ctx.record.id}_intake"
        if self.tasks.get(task_id) is not None:
            return task_id
        self.tasks.create(Task(
            id=task_id,
            kind="exception",
            title="AP intake could not fetch any invoice in this window",
            summary=(
                f"{outcome['unreachable']} document(s) filed under the "
                "accounts-payable folders were selected for reading and none "
                "of them answered. Nothing has been asserted and nothing has "
                "been lost — the rows still carry their pointers and the next "
                "window will try again. What this needs is somebody who can "
                "say why the document system stopped serving content."
            ),
            queue=routing.OPS_QUEUE,
            priority="high",
            flow_id=FLOW_ID,
            flow_version=ctx.record.flow_version,
            run_id=ctx.record.id,
            evidence=[
                TaskEvidence(label="Attempted", value=str(outcome["unreachable"])),
                TaskEvidence(label="Collection", value=DOCUMENT_LIBRARY),
            ] + [
                TaskEvidence(label="Failure", value=note)
                for note in outcome.get("notes", [])[:3]
            ],
            resumes="nothing resumes; the next delivery re-reads the same rows",
            created_at_iso=utcnow().isoformat(),
        ))
        log.error(
            "Every AP intake fetch in a window failed",
            extra={"runId": ctx.record.id, "attempted": outcome["unreachable"]},
        )
        return task_id


def _under(path: str, prefixes: list[str]) -> bool:
    """Is this folder path the AP root, or somewhere below it?

    Compared on whole segments — `Accounts Payable Archive` is not under
    `Accounts Payable`, and a `startswith` on the bare string would have said
    it was.
    """
    if not path:
        return False
    return any(
        prefix and (path == prefix or path.startswith(f"{prefix}/"))
        for prefix in prefixes
    )


def build_intake_service(
    graph: GraphService,
    knowledge,
    documents: DocumentService,
    tasks: TaskService,
    engine: RunEngine | None = None,
    gateway: ActionGateway | None = None,
    dispatcher: Dispatcher | None = None,
    fetcher: ContentFetcher | None = None,
) -> ApIntakeService:
    return ApIntakeService(
        graph, knowledge, documents, tasks, engine, gateway, dispatcher, fetcher,
    )
