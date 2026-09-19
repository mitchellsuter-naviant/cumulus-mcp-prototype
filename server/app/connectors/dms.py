"""The DMS mirror and the HRIS ledger: two feeds that deliberately do less.

Both bodies here are defined as much by what they refuse to do as by what they
write, which is why they share a module.

**`dms-sync` mirrors a filing tree, not a document store.** Where a customer
put a file is knowledge — the same PDF under `Contracts/Northwind/Executed` and
under `Scratch/inbox` means two different things, and a flat list throws that
away along with every retention rule a department hung off a folder. So the
tree lands as `Folder` objects and each file lands as a `Document` row stamped
with `collection_id="document-library"` and a `storage_ref` back to the
original. The bytes are never fetched. That is not a phase-one shortcut to be
lifted later: `document-library` declares `custody="reference"`, and a mirror
that pulled the content would have turned a map into a second system of record
for the customer's own documents. A `Document` with a `storage_ref` and no
`sha256` is Cumulus saying, precisely, *we know this file exists and where it
is, and nobody has read it* — which is a state the eval work has been unable to
reach until now because every Document in the world had been read.

**`employment-event-sync` lands the ledger and stops.** The HRIS's
`employmentEvents` route is append-only and each row carries the new values
*and* the previous ones: a promotion says what the job, department, manager and
salary became and what each of them was. No type in the ontology holds a
before/after pair, and inventing an `EmploymentEvent` type to hold one would
have been modelling the feed rather than the business. So the rows land as
`SourceRecord`s — the same landing every reify feed uses — and the flow asserts
nothing else. Not a stub: it is the substrate. A worker's history is the one
thing that cannot be reconstructed from a later pull, because the workforce
feed only ever shows the current state, and a later flow reading "who moved out
of Professional Services this quarter" needs rows nobody threw away.

The deliberate asymmetry between them is worth naming. The DMS feed *binds*:
a folder id and a document id are exact, nobody adjudicates them, so there is
no landing record worth keeping and the rows become objects directly. The
ledger *reifies* and goes no further: its rows are exact too, but they describe
no entity, so landing is the whole of the work.
"""

from __future__ import annotations

import logging
from typing import Any

from .graph import Claim, GraphWriter, object_id
from .runs import RunContext
from .sync import (
    DMS_FLOW_ID,
    DOCUMENTS_EVENT,
    EMPLOYMENT_EVENT_EVENT,
    EMPLOYMENT_EVENT_FLOW_ID,
    DmsDocumentRecord,
    EmploymentEventRecord,
    FolderRecord,
    SyncService,
    _authority,
)

log = logging.getLogger("cumulus.sync.dms")

#: The Collection a DMS-sourced file belongs to, stamped per row. `Document`
#: the *type* is governed by `audit-documents` — the partition allows one home
#: per type — and `collection_id` is the field that places an instance
#: somewhere else, which is exactly what it was declared for.
DOCUMENT_LIBRARY = "document-library"


def register(service: SyncService) -> None:
    """Bind both bodies to the engine `SyncService` was handed."""
    if service.engine is None:
        return
    service.engine.register(
        DMS_FLOW_ID, lambda ctx, inputs: dms_body(service, ctx, inputs)
    )
    service.engine.register(
        EMPLOYMENT_EVENT_FLOW_ID,
        lambda ctx, inputs: employment_event_body(service, ctx, inputs),
    )


# ---------------------------------------------------------------------------
# dms-sync
# ---------------------------------------------------------------------------


def dms_body(service: SyncService, ctx: RunContext, inputs: dict) -> dict:
    """Land the filing tree, then the files hanging off it.

    Two apply steps and no resolve step, because there is nothing to resolve:
    a folder id and a document id are exact identifiers in the system that
    minted them. What can fail is a *reference* — a folder whose parent this
    delta did not carry and the graph has never seen — and a batch feed never
    suspends for one of those. They are counted on the aggregate and reported
    on the run.
    """
    source = inputs["source"]
    watermark = inputs.get("watermark", "")
    folders = [FolderRecord.model_validate(f) for f in inputs.get("folders", [])]
    documents = [
        DmsDocumentRecord.model_validate(d) for d in inputs.get("documents", [])
    ]

    ctx.event(
        "t1", DOCUMENTS_EVENT,
        f"{len(folders)} folder(s) and {len(documents)} document(s) from "
        f"`{source}`" + (f" · watermark {watermark}" if watermark else ""),
        kind="trigger",
    )

    # The graph's existing folders for this source, read once and passed down.
    # Three places need it — placing a batch folder whose parent arrived in an
    # earlier delta, drawing that parent edge, and filing a document into a
    # folder this delta did not carry — and each of them used to scan for
    # itself, the last one per record.
    known_paths = _known_folder_paths(service, source)
    paths = _folder_paths(source, folders, known_paths)

    tree = service._act(ctx, "apply-source-records", "a1")
    tree_out = ctx.step(
        "a1", "apply-folders", "action",
        fn=lambda: tree.commit(lambda w: _apply_folders(
            service, w, source, folders, ctx.record.id, paths, known_paths,
        )),
        writes=lambda _out: tree.writes,
        summary=lambda out: (
            f"{out['folders']} folder(s) · {out['subfolder_of']} nested"
            + (
                f" · {out['unplaced']} whose parent this delta could not place"
                if out["unplaced"] else ""
            )
        ),
    )

    files = service._act(ctx, "apply-source-records", "a2")
    file_out = ctx.step(
        "a2", "apply-documents", "action",
        fn=lambda: files.commit(lambda w: _apply_documents(
            service, w, source, documents, ctx.record.id, paths, known_paths,
        )),
        writes=lambda _out: files.writes,
        summary=lambda out: (
            f"{out['documents']} document(s) referenced in place, "
            f"{out['filed_under']} filed"
            + (f" · {out['at_root']} at the root" if out["at_root"] else "")
            + (
                f" · {out['folder_unresolved']} in a folder the graph does "
                "not hold"
                if out["folder_unresolved"] else ""
            )
        ),
    )

    ctx.event(
        "e1", "DocumentsSynced",
        f"{len(folders)} folder(s), {len(documents)} document(s) from {source}",
    )
    return {
        "source": source, "watermark": watermark, "kind": "documents",
        "records": len(folders) + len(documents),
        # `landed` is the Sources lens's word for how many rows a delivery put
        # into the graph, summed across runs — a bind feed has no landing step
        # to count, so it states the bound-record count the way the workforce,
        # calendar and mail bodies do.
        "landed": len(folders) + len(documents),
        **tree_out, **file_out,
    }


def _known_folder_paths(service: SyncService, source: str) -> dict[str, str]:
    """Every folder this source has already landed, by its DMS id, as a path.

    One read per run. The tree walk, the parent edge and the document filing
    all ask the same question of the same rows, and asking it three times —
    once per record, in the third case — was three scans of one index.
    """
    return {
        str(o.props.get("external_id")): str(o.props.get("path") or "")
        for o in service.graph.find("Folder", prop_equals={"source": source})
        if o.props.get("external_id")
    }


def _folder_paths(
    source: str, records: list[FolderRecord], known: dict[str, str]
) -> dict[str, str]:
    """Every folder in the batch that can be placed, by id, as a full path.

    The path is the natural key, so this is the step that decides which rows
    can land at all. It walks parent pointers through the batch first and falls
    back to `known` — the paths already in the graph — which is what makes a
    delta of one renamed subfolder resolvable without re-pulling the tree.

    A folder whose parent is in neither place is simply absent from the result.
    Landing it under its own name would have filed it at the root, and a
    document filed at the root when it is really six levels down is a worse
    answer than an honest count of what could not be placed.
    """
    in_batch = {r.external_id: r for r in records if r.external_id}
    resolved: dict[str, str] = {}

    def path_of(external_id: str, seen: frozenset[str]) -> str:
        if not external_id:
            return ""  # a root's parent; not a failure
        if external_id in resolved:
            return resolved[external_id]
        record = in_batch.get(external_id)
        if record is None:
            return known.get(external_id, "")
        if external_id in seen:
            # A cycle is the source contradicting itself. Treated as a missing
            # parent rather than recursed into — the alternative is a hang.
            log.warning(
                "DMS folder tree cycles",
                extra={"source": source, "folder": external_id},
            )
            return ""
        parent = path_of(record.parent_external_id, seen | {external_id})
        if record.parent_external_id and not parent:
            return ""
        name = record.name or external_id
        resolved[external_id] = f"{parent}/{name}" if parent else name
        return resolved[external_id]

    for record in records:
        path_of(record.external_id, frozenset())
    return resolved


def _apply_folders(
    service: SyncService,
    graph: GraphWriter,
    source: str,
    records: list[FolderRecord],
    run_id: str,
    paths: dict[str, str],
    known_paths: dict[str, str],
) -> dict:
    counts = {"folders": 0, "subfolder_of": 0, "unplaced": 0}
    claim_source = f"{_authority(source)}:{source}"

    # Two passes: every folder exists before any edge is drawn, so the order
    # the estate happened to return the tree in decides nothing.
    for record in records:
        path = paths.get(record.external_id, "")
        if not path:
            counts["unplaced"] += 1
            continue
        graph.upsert(
            "Folder", _folder_key(source, path),
            [
                Claim(field="name", value=record.name, source=claim_source),
                Claim(field="path", value=path, source=claim_source),
                Claim(field="external_id", value=record.external_id,
                      source=claim_source),
                Claim(field="source", value=source, source=claim_source),
                Claim(field="depth", value=path.count("/"),
                      source=claim_source),
            ],
            label=record.name or path,
            provenance=f"synced by {run_id}",
        )
        counts["folders"] += 1

    for record in records:
        parent_path = _path_of(record.parent_external_id, paths, known_paths)
        if not record.parent_external_id or not parent_path:
            continue
        child = _folder(service, source, paths.get(record.external_id, ""))
        parent = _folder(service, source, parent_path)
        if child is None or parent is None:
            continue
        graph.link(
            "subfolder_of", child.id, parent.id,
            method="declared", provenance="dms parentId",
        )
        counts["subfolder_of"] += 1
    return counts


def _apply_documents(
    service: SyncService,
    graph: GraphWriter,
    source: str,
    records: list[DmsDocumentRecord],
    run_id: str,
    paths: dict[str, str],
    known_paths: dict[str, str],
) -> dict:
    """Each file as a pointer, filed where the customer filed it.

    The claims deliberately stop short of `sha256`: nothing here was read, and
    a content hash is what a *read* document has. Minting one from the metadata
    would make an unread file indistinguishable from one we hold the bytes of,
    which is the single fact this whole mapping exists to keep straight.

    The folder a file names is looked up in the same two places the tree walk
    used — this delta's paths, then the ones the graph already held — rather
    than re-scanning the Folder rows a third time.
    """
    counts = {
        "documents": 0, "filed_under": 0, "at_root": 0, "folder_unresolved": 0,
    }
    claim_source = f"{_authority(source)}:{source}"

    for record in records:
        claims = [
            Claim(field="filename", value=record.name, source=claim_source),
            Claim(field="media_type", value=record.content_type,
                  source=claim_source),
            Claim(field="byte_size", value=record.size, source=claim_source),
            # The per-instance placement `Document.collection_id` exists for:
            # this row is governed by the document library's reference custody,
            # not by the copy custody the type's own Collection declares.
            Claim(field="collection_id", value=DOCUMENT_LIBRARY,
                  source=claim_source),
            Claim(field="storage_ref", value=record.storage_ref,
                  source=claim_source),
            Claim(field="latest_version", value=record.latest_version,
                  source=claim_source),
        ]
        document = graph.upsert(
            "Document", _document_key(source, record.external_id), claims,
            label=record.name or record.external_id,
            provenance=f"referenced by {run_id}",
        )
        counts["documents"] += 1

        if not record.folder_external_id:
            # Filed at the root, which is a real place — the live library has
            # nine of these, uploaded by hand outside anyone's tree. Counted
            # rather than left as the arithmetic between `documents` and
            # `filed_under`, because an unaccounted difference reads as a bug
            # in the mirror when it is a fact about the customer's filing.
            counts["at_root"] += 1
            continue
        folder = _folder(service, source, _path_of(
            record.folder_external_id, paths, known_paths,
        ))
        if folder is None:
            counts["folder_unresolved"] += 1
            continue
        graph.link(
            "filed_under", document.id, folder.id,
            method="declared", provenance="dms folderId",
        )
        counts["filed_under"] += 1
    return counts


def _folder_key(source: str, path: str) -> str:
    """`Folder`'s declared natural key: source + path from the tree's root.

    Scoped by source because two document systems can both hold a `Contracts`
    folder and they are not the same place.
    """
    return f"{source}:{path}"


def _document_key(source: str, external_id: str) -> str:
    """`Document`'s second declared key — the one for referenced originals.

    A file we never fetched has no content hash to be keyed by, and inventing
    one would make it look read.
    """
    return f"{source}:{external_id}"


def _folder(service: SyncService, source: str, path: str) -> Any:
    if not path:
        return None
    return service.graph.get(object_id("Folder", _folder_key(source, path)))


def _path_of(
    external_id: str, paths: dict[str, str], known_paths: dict[str, str]
) -> str:
    """This delta's placement first, then the one the graph already held.

    In that order because a delta that renamed a folder is the newer truth,
    and the graph's copy is what makes a delta of one document resolvable
    against a tree nobody re-pulled.
    """
    if not external_id:
        return ""
    return paths.get(external_id) or known_paths.get(external_id, "")


# ---------------------------------------------------------------------------
# employment-event-sync
# ---------------------------------------------------------------------------


def employment_event_body(
    service: SyncService, ctx: RunContext, inputs: dict
) -> dict:
    """Mirror the append-only ledger. Landing is the whole flow.

    No resolve step and no apply step — not an omission, a decision. These rows
    describe changes to people the workforce feed already lands, and asserting
    them a second time here would put two writers on one set of claims with no
    way to tell which one a value came from. The ledger's job is to be *kept*,
    exactly as it arrived, so that a later flow can ask a question about
    history that the current-state feed cannot answer.
    """
    source = inputs["source"]
    watermark = inputs.get("watermark", "")
    records = [
        EmploymentEventRecord.model_validate(r) for r in inputs["records"]
    ]

    ctx.event(
        "t1", EMPLOYMENT_EVENT_EVENT,
        f"{len(records)} employment event(s) from `{source}`"
        + (f" · watermark {watermark}" if watermark else ""),
        kind="trigger",
    )

    landing = service._act(ctx, "land-source-records", "s1")
    landed = ctx.step(
        "s1", "land-records", "sync",
        fn=lambda: landing.commit(lambda w: service._land(
            source, "employment-event", [r.model_dump() for r in records], w,
        )),
        writes=lambda _out: landing.writes,
        summary=lambda out: (
            f"{out['landed']} landed, {out['unchanged']} unchanged "
            f"(byte-identical redelivery) in `source-landing`"
        ),
    )

    ctx.event(
        "e1", "EmploymentEventsLanded",
        f"{len(records)} ledger row(s) from {source}, asserted against nothing",
    )
    return {
        "source": source, "watermark": watermark, "kind": "employment-events",
        "records": len(records),
        **landed,
    }
