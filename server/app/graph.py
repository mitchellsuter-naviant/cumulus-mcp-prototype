"""The entity layer: canonical Objects, typed Links, retained Claims.

Why this exists. Before it, a vendor was a slug string used to look up
`vendor-exemplar-{slug}` in the corpus — a knowledge shelf doing a primary
key's job — and an expense was a flat row with no edge back to the person who
submitted it. Both of the questions the two flows need to answer ("how much
has this employee submitted in six months", "does this invoice line match a PO
line and a receiving line") are graph questions, so this is a graph.

Three ideas, and they are the whole module:

1. **An Object is canonical and Cumulus-identified.** Its id is derived from a
   declared natural key, so the same real-world thing seen twice resolves to
   one object instead of two. Deterministic ids are also what let a Flow run
   replay safely: re-executing a step mints the same id rather than a
   duplicate.

2. **Claims are retained; `props` are derived.** Every field value arrives as a
   Claim carrying its source, confidence, and timestamp. `props` is the
   resolution of those claims under the ontology's precedence order — computed,
   cached on the record for cheap reads, and recomputable. Two systems
   disagreeing about a vendor's payment terms is a normal state, not an error.

3. **Links are typed, directional, temporal, and reversible.** An identity
   assertion ("this ERP row is that vendor") carries the confidence and method
   that produced it and can be withdrawn by a steward without deleting
   anything — reversal writes an end to the edge's validity, so the history of
   what we believed stays readable.

Storage is Firestore or memory, the same split as everything else. It is a
document store with two collections and no joins: every traversal here is a
handful of point lookups from a known root, which is exactly what Firestore is
good at. When traversals stop being bounded, this is the seam to reconsider.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from .models import utcnow
from .ontology import ONTOLOGY, Ontology

log = logging.getLogger("cumulus.graph")

ObjectStatus = Literal["draft", "active", "inactive", "superseded"]
LinkMethod = Literal["declared", "exact", "fuzzy", "human", "derived"]


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


class Claim(BaseModel):
    """One source's assertion about one field of one object.

    `source` is a namespaced actor: `human:dana@customer.example`,
    `erp:vendors`, `flow:accounts-payable`, `model:gemini-2.5-flash`. The
    prefix before the colon is what precedence sorts on.
    """

    field: str
    value: Any = None
    source: str
    source_record_id: str = ""
    confidence: float = 1.0
    at_iso: str = Field(default_factory=lambda: utcnow().isoformat())
    note: str = ""
    # The *act* that wrote this, as opposed to the actor `source` names. With
    # both, "why does this invoice say $1,425.25" resolves to a step in a
    # journal rather than to a category — which is what makes the audit join
    # between the run spine and the entity layer complete in both directions.
    run_id: str = ""
    step_id: str = ""

    @property
    def authority(self) -> str:
        return self.source.split(":", 1)[0]


class ObjectRecord(BaseModel):
    """A canonical entity. `props` is resolved; `claims` is the evidence."""

    id: str
    type: str
    natural_key: str = ""
    label: str = ""
    props: dict[str, Any] = Field(default_factory=dict)
    claims: list[Claim] = Field(default_factory=list)
    status: ObjectStatus = "active"
    provenance: str = ""
    # Set when this object was merged into another; reads follow the pointer.
    superseded_by: str = ""
    revision: int = 1
    created_at_iso: str = Field(default_factory=lambda: utcnow().isoformat())
    updated_at_iso: str = Field(default_factory=lambda: utcnow().isoformat())

    def ref(self) -> str:
        """The read-set reference form: `graph:Vendor/vendor_ab12cd@rev3`."""
        return f"graph:{self.type}/{self.id}@rev{self.revision}"

    def conflicts(self) -> dict[str, list[Claim]]:
        """Fields where **different** sources assert different values.

        The qualifier is load-bearing. One source restating a field with a new
        value is an *update* — a feed re-landing a row stamps a fresh
        `landed_at` every time — and counting that as a conflict buries the
        real ones in noise. A disagreement needs two parties.
        """
        by_field: dict[str, list[Claim]] = {}
        for claim in self.claims:
            by_field.setdefault(claim.field, []).append(claim)
        out: dict[str, list[Claim]] = {}
        for field, claims in by_field.items():
            sources = {claim.source for claim in claims}
            # The value each source currently stands behind: its newest claim.
            latest = {
                source: max(
                    (c for c in claims if c.source == source),
                    key=lambda c: c.at_iso,
                ).value
                for source in sources
            }
            distinct = {
                json.dumps(value, sort_keys=True, default=str)
                for value in latest.values()
            }
            if len(distinct) > 1:
                out[field] = claims
        return out


class LinkRecord(BaseModel):
    id: str
    type: str
    from_id: str
    to_id: str
    props: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 1.0
    method: LinkMethod = "declared"
    provenance: str = ""
    valid_from_iso: str = Field(default_factory=lambda: utcnow().isoformat())
    valid_to_iso: str = ""
    reversed_at_iso: str = ""
    reversed_by: str = ""
    reversal_reason: str = ""
    run_id: str = ""
    step_id: str = ""

    @property
    def live(self) -> bool:
        return not self.reversed_at_iso and not self.valid_to_iso

    def ref(self) -> str:
        """The write-set reference form: `link:identified_as/link_ab12cd`."""
        return f"link:{self.type}/{self.id}"


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def normalize_key(value: str) -> str:
    """Collapse a natural key to a comparable form.

    Vendor names are the hard case: `ACME Industrial, Inc.` and
    `Acme Industrial Inc` are the same company. Legal-form suffixes and
    punctuation come off; word order and spelling do not, because fixing those
    is matching, not normalization, and matching needs a confidence.
    """
    text = (value or "").lower().strip()
    text = re.sub(r"[.,;:'\"()]", " ", text)
    # Re-join initialisms that the punctuation strip just blew apart, so
    # `L.L.C.` becomes `llc` and is recognized as a legal form on the next
    # line. Runs of single letters are effectively always an initialism in a
    # company name; ordinary words survive because they are longer than one
    # character.
    text = re.sub(
        r"\b(?:[a-z] ){1,4}[a-z]\b", lambda m: m.group(0).replace(" ", ""), text
    )
    text = re.sub(r"\b(inc|incorporated|llc|ltd|limited|co|corp|corporation|gmbh|plc|sa|nv|bv)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return re.sub(r"-{2,}", "-", text)


def object_id(object_type: str, natural_key: str) -> str:
    """Deterministic id from type + natural key.

    Deterministic on purpose, twice over: the same entity seen from two systems
    lands on one id without a lookup, and a replayed Flow step re-mints rather
    than duplicates.
    """
    normalized = normalize_key(natural_key)
    digest = hashlib.sha256(f"{object_type}:{normalized}".encode()).hexdigest()[:12]
    return f"{object_type.lower()}_{digest}"


def link_id(link_type: str, from_id: str, to_id: str) -> str:
    digest = hashlib.sha256(f"{link_type}:{from_id}:{to_id}".encode()).hexdigest()[:12]
    return f"link_{digest}"


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def resolve_props(
    claims: list[Claim], precedence: tuple[str, ...] = ONTOLOGY.precedence
) -> dict[str, Any]:
    """Reduce retained claims to one resolved value per field.

    Precedence first, then confidence, then recency. Nothing is discarded — a
    losing claim stays on the record and shows up in `conflicts()`, which is
    what a steward needs in order to fix the source rather than the symptom.
    """
    order = {name: i for i, name in enumerate(precedence)}
    best: dict[str, tuple[tuple, Any]] = {}
    for claim in claims:
        if claim.value is None or claim.value == "":
            continue
        rank = (
            order.get(claim.authority, len(order)),
            -claim.confidence,
            # Later wins among equals; negate by comparing reversed strings is
            # awkward, so keep the timestamp and invert the comparison below.
        )
        key = (rank[0], rank[1], claim.at_iso)
        current = best.get(claim.field)
        if current is None:
            best[claim.field] = (key, claim.value)
            continue
        current_key = current[0]
        # Lower authority index wins; on a tie, higher confidence, then newer.
        if (key[0], key[1]) < (current_key[0], current_key[1]) or (
            (key[0], key[1]) == (current_key[0], current_key[1])
            and key[2] > current_key[2]
        ):
            best[claim.field] = (key, claim.value)
    return {field: value for field, (_, value) in best.items()}


# ---------------------------------------------------------------------------
# TBox conformance
# ---------------------------------------------------------------------------


class TBoxViolation(BaseModel):
    """One way the graph disagrees with the ontology that declares it.

    Enforcing the TBox is what turns it from a description into a constraint,
    and the payoff is two-tier testing: these invariants hold universally and
    need no per-case labelling, so a structural bug — a duplicate vendor, an
    orphaned line, a link pointing at the wrong type — is caught without anyone
    having written an assertion about it.
    """

    kind: Literal[
        "unknown-object-type",
        "unknown-link-type",
        "undeclared-field",
        "wrong-endpoint-type",
        "cardinality",
        "orphan",
        "dangling-link",
    ]
    subject: str
    detail: str

    def __str__(self) -> str:  # pragma: no cover - diagnostics
        return f"[{self.kind}] {self.subject}: {self.detail}"


class TBoxError(Exception):
    """Raised on a write that would violate the ontology, when strict."""

    def __init__(self, violations: list[TBoxViolation]) -> None:
        super().__init__("; ".join(str(v) for v in violations))
        self.violations = violations


# Numeric and date field kinds are coerced-checked; `enum` values are declared
# as prose in the ontology's notes rather than as a closed list, so they are
# deliberately not enforced — a check that guessed at the allowed values would
# be worse than none.
_NUMERIC_KINDS = {"number", "money"}


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


class GraphStore(Protocol):
    def objects(self) -> list[ObjectRecord]: ...
    def get_object(self, object_id: str) -> ObjectRecord | None: ...
    def put_object(self, record: ObjectRecord) -> ObjectRecord: ...
    def links(self) -> list[LinkRecord]: ...
    def get_link(self, link_id: str) -> LinkRecord | None: ...
    def put_link(self, record: LinkRecord) -> LinkRecord: ...
    # Edge lookups by endpoint. Separate from `links()` because every
    # traversal and every cardinality check does one, and streaming the whole
    # collection for each would make a single AP run read the edge table
    # dozens of times. Single-field equality on purpose: it needs no composite
    # index, so there is nothing to deploy alongside the code.
    def links_from(self, from_id: str) -> list[LinkRecord]: ...
    def links_to(self, to_id: str) -> list[LinkRecord]: ...


class InMemoryGraphStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._objects: dict[str, ObjectRecord] = {}
        self._links: dict[str, LinkRecord] = {}

    def objects(self) -> list[ObjectRecord]:
        with self._lock:
            return list(self._objects.values())

    def get_object(self, object_id: str) -> ObjectRecord | None:
        with self._lock:
            return self._objects.get(object_id)

    def put_object(self, record: ObjectRecord) -> ObjectRecord:
        with self._lock:
            self._objects[record.id] = record
            return record

    def links(self) -> list[LinkRecord]:
        with self._lock:
            return list(self._links.values())

    def get_link(self, link_id: str) -> LinkRecord | None:
        with self._lock:
            return self._links.get(link_id)

    def put_link(self, record: LinkRecord) -> LinkRecord:
        with self._lock:
            self._links[record.id] = record
            return record

    def links_from(self, from_id: str) -> list[LinkRecord]:
        with self._lock:
            return [linked for linked in self._links.values()
                    if linked.from_id == from_id]

    def links_to(self, to_id: str) -> list[LinkRecord]:
        with self._lock:
            return [linked for linked in self._links.values()
                    if linked.to_id == to_id]


class FirestoreGraphStore:
    """Two collections, no joins.

    Reads are deliberately narrow: `get_object` and the link queries are point
    lookups or single-field equality, which is what Firestore indexes for free.
    The one broad read is `objects()`, used by the console's registry views and
    by re-resolution passes — bounded by prototype volume, and the first thing
    to paginate when it stops being.
    """

    def __init__(self, project: str | None, objects_collection: str, links_collection: str) -> None:
        from google.cloud import firestore  # deferred: not needed for local dev

        self._client = firestore.Client(project=project)
        self._objects = self._client.collection(objects_collection)
        self._links = self._client.collection(links_collection)

    def objects(self) -> list[ObjectRecord]:
        out: list[ObjectRecord] = []
        for doc in self._objects.stream():
            try:
                out.append(ObjectRecord.model_validate(doc.to_dict()))
            except Exception:
                log.exception("Skipping unparseable object", extra={"docId": doc.id})
        return out

    def get_object(self, object_id: str) -> ObjectRecord | None:
        snap = self._objects.document(object_id).get()
        if not snap.exists:
            return None
        try:
            return ObjectRecord.model_validate(snap.to_dict())
        except Exception:
            log.exception("Unparseable object", extra={"objectId": object_id})
            return None

    def put_object(self, record: ObjectRecord) -> ObjectRecord:
        self._objects.document(record.id).set(record.model_dump(mode="json"))
        return record

    def links(self) -> list[LinkRecord]:
        out: list[LinkRecord] = []
        for doc in self._links.stream():
            try:
                out.append(LinkRecord.model_validate(doc.to_dict()))
            except Exception:
                log.exception("Skipping unparseable link", extra={"docId": doc.id})
        return out

    def get_link(self, link_id: str) -> LinkRecord | None:
        snap = self._links.document(link_id).get()
        if not snap.exists:
            return None
        try:
            return LinkRecord.model_validate(snap.to_dict())
        except Exception:
            log.exception("Unparseable link", extra={"linkId": link_id})
            return None

    def put_link(self, record: LinkRecord) -> LinkRecord:
        self._links.document(record.id).set(record.model_dump(mode="json"))
        return record

    def _links_where(self, field: str, value: str) -> list[LinkRecord]:
        out: list[LinkRecord] = []
        for doc in self._links.where(field, "==", value).stream():
            try:
                out.append(LinkRecord.model_validate(doc.to_dict()))
            except Exception:
                log.exception("Skipping unparseable link", extra={"docId": doc.id})
        return out

    def links_from(self, from_id: str) -> list[LinkRecord]:
        return self._links_where("from_id", from_id)

    def links_to(self, to_id: str) -> list[LinkRecord]:
        return self._links_where("to_id", to_id)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class GraphService:
    def __init__(
        self,
        store: GraphStore,
        ontology: Ontology = ONTOLOGY,
        *,
        strict: bool = True,
    ) -> None:
        self.store = store
        self.ontology = ontology
        # `strict` refuses a write that violates the ontology. On by default:
        # the whole point of validating is that a flow bug cannot quietly
        # corrupt the graph, and a violation logged-and-accepted is a violation
        # someone finds three weeks later in a report. A deployment migrating
        # older data can turn it off and read `check_invariants()` instead.
        self.strict = strict

    # -- TBox conformance ---------------------------------------------------

    def writer(self, run_id: str, step_id: str) -> "GraphWriter":
        """A view of this service bound to the act doing the writing."""
        return GraphWriter(self, run_id, step_id)

    def _validate_claims(
        self, object_type: str, claims: list[Claim]
    ) -> list[TBoxViolation]:
        declared = self.ontology.object_type(object_type)
        if declared is None:
            return [TBoxViolation(
                kind="unknown-object-type", subject=object_type,
                detail="no such type in the ontology",
            )]
        known = {f.name: f for f in declared.fields}
        violations: list[TBoxViolation] = []
        for claim in claims:
            spec = known.get(claim.field)
            if spec is None:
                violations.append(TBoxViolation(
                    kind="undeclared-field",
                    subject=f"{object_type}.{claim.field}",
                    detail=(
                        f"claimed by {claim.source} but not declared on "
                        f"{object_type}"
                    ),
                ))
                continue
            if spec.kind in _NUMERIC_KINDS and claim.value not in (None, ""):
                try:
                    float(claim.value)  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    violations.append(TBoxViolation(
                        kind="undeclared-field",
                        subject=f"{object_type}.{claim.field}",
                        detail=(
                            f"declared {spec.kind} but {claim.source} claimed "
                            f"{claim.value!r}"
                        ),
                    ))
        return violations

    def _validate_link(
        self, link_type: str, from_record: ObjectRecord | None,
        to_record: ObjectRecord | None, from_id: str, to_id: str,
    ) -> list[TBoxViolation]:
        declared = self.ontology.link_type(link_type)
        if declared is None:
            return [TBoxViolation(
                kind="unknown-link-type", subject=link_type,
                detail="no such link type in the ontology",
            )]
        violations: list[TBoxViolation] = []
        if from_record is None or to_record is None:
            missing = from_id if from_record is None else to_id
            return [TBoxViolation(
                kind="dangling-link", subject=f"{link_type} → {missing}",
                detail="endpoint does not exist",
            )]
        if from_record.type != declared.from_type:
            violations.append(TBoxViolation(
                kind="wrong-endpoint-type", subject=f"{link_type}:{from_id}",
                detail=(
                    f"declared from {declared.from_type}, got "
                    f"{from_record.type}"
                ),
            ))
        if to_record.type != declared.to_type:
            violations.append(TBoxViolation(
                kind="wrong-endpoint-type", subject=f"{link_type}:{to_id}",
                detail=f"declared to {declared.to_type}, got {to_record.type}",
            ))
        # Cardinality, checked only in the directions the ontology constrains.
        constrain_in = declared.cardinality in ("one-to-many", "one-to-one")
        constrain_out = declared.cardinality in ("many-to-one", "one-to-one")
        if constrain_in:
            others = [
                link for link in self.store.links_to(to_id)
                if link.type == link_type and link.from_id != from_id and link.live
            ]
            if others:
                violations.append(TBoxViolation(
                    kind="cardinality", subject=f"{link_type} → {to_id}",
                    detail=(
                        f"{declared.cardinality}: already has an inbound "
                        f"{link_type} from {others[0].from_id}"
                    ),
                ))
        if constrain_out:
            others = [
                link for link in self.store.links_from(from_id)
                if link.type == link_type and link.to_id != to_id and link.live
            ]
            if others:
                violations.append(TBoxViolation(
                    kind="cardinality", subject=f"{link_type} ← {from_id}",
                    detail=(
                        f"{declared.cardinality}: already has an outbound "
                        f"{link_type} to {others[0].to_id}"
                    ),
                ))
        return violations

    def _enforce(self, violations: list[TBoxViolation]) -> None:
        if not violations:
            return
        for violation in violations:
            log.warning(
                "TBox violation",
                extra={
                    "violationKind": violation.kind,
                    "subject": violation.subject,
                    "detail": violation.detail,
                },
            )
        if self.strict:
            raise TBoxError(violations)

    def check_invariants(self) -> list[TBoxViolation]:
        """Sweep the whole graph against the ontology.

        The universal tier of testing: no per-case labels, and it catches the
        shape of bug that field assertions never see — a second vendor minted
        for the same company, a line with no parent, a match set pointing at an
        invoice that is not there.
        """
        violations: list[TBoxViolation] = []
        objects = {o.id: o for o in self.store.objects()}
        for record in objects.values():
            violations.extend(self._validate_claims(record.type, record.claims))
        # Children of a declared parent edge must have one.
        parented: dict[str, set[str]] = {}
        for link in self.store.links():
            declared = self.ontology.link_type(link.type)
            if declared is None:
                violations.append(TBoxViolation(
                    kind="unknown-link-type", subject=link.id,
                    detail=f"{link.type} is not in the ontology",
                ))
                continue
            if not link.live:
                continue
            from_record, to_record = objects.get(link.from_id), objects.get(link.to_id)
            violations.extend(self._validate_link(
                link.type, from_record, to_record, link.from_id, link.to_id,
            ))
            if declared.cardinality in ("one-to-many", "one-to-one"):
                parented.setdefault(declared.to_type, set()).add(link.to_id)
        for record in objects.values():
            expected_parents = [
                linked for linked in self.ontology.links
                if linked.to_type == record.type
                and linked.cardinality in ("one-to-many", "one-to-one")
                and linked.name.startswith("has_")
            ]
            if expected_parents and record.status != "superseded":
                if record.id not in parented.get(record.type, set()):
                    violations.append(TBoxViolation(
                        kind="orphan", subject=record.ref(),
                        detail=(
                            f"{record.type} with no "
                            f"{expected_parents[0].name} parent"
                        ),
                    ))
        # A cardinality violation is reported once per offending edge above, so
        # collapse the duplicates the pairwise check produces.
        seen: set[tuple[str, str, str]] = set()
        unique: list[TBoxViolation] = []
        for violation in violations:
            key = (violation.kind, violation.subject, violation.detail)
            if key in seen:
                continue
            seen.add(key)
            unique.append(violation)
        return unique

    # -- objects ------------------------------------------------------------

    def upsert(
        self,
        object_type: str,
        natural_key: str,
        claims: list[Claim],
        *,
        label: str = "",
        status: ObjectStatus = "active",
        provenance: str = "",
    ) -> ObjectRecord:
        """Assert claims about an entity, minting it if this is the first sight.

        Idempotent by construction: the id comes from the natural key, and a
        claim identical to one already held (same source, field, and value) is
        not appended twice — which is what makes a replayed step and a
        re-delivered sync record both harmless.
        """
        self._enforce(self._validate_claims(object_type, claims))
        oid = object_id(object_type, natural_key)
        record = self.store.get_object(oid)
        if record is None:
            record = ObjectRecord(
                id=oid,
                type=object_type,
                natural_key=natural_key,
                label=label or natural_key,
                status=status,
                provenance=provenance,
            )
        elif label and record.label != label:
            record.label = label

        existing = {
            (c.source, c.field, json.dumps(c.value, sort_keys=True, default=str))
            for c in record.claims
        }
        added = 0
        for claim in claims:
            fingerprint = (
                claim.source,
                claim.field,
                json.dumps(claim.value, sort_keys=True, default=str),
            )
            if fingerprint in existing:
                continue
            record.claims.append(claim)
            existing.add(fingerprint)
            added += 1

        if added or record.revision == 1:
            record.props = resolve_props(record.claims, self.ontology.precedence)
            if added:
                record.revision += 1
            record.updated_at_iso = utcnow().isoformat()
            if status != "active":
                record.status = status
            self.store.put_object(record)
        return record

    def get(self, object_id_: str, *, follow: bool = True) -> ObjectRecord | None:
        record = self.store.get_object(object_id_)
        if record and follow and record.superseded_by:
            return self.store.get_object(record.superseded_by) or record
        return record

    def find(
        self,
        object_type: str | None = None,
        *,
        prop_equals: dict[str, Any] | None = None,
        natural_key: str | None = None,
        limit: int | None = None,
    ) -> list[ObjectRecord]:
        items = self.store.objects()
        if object_type is not None:
            items = [o for o in items if o.type == object_type]
        if natural_key is not None:
            wanted = normalize_key(natural_key)
            items = [o for o in items if normalize_key(o.natural_key) == wanted]
        for field, value in (prop_equals or {}).items():
            items = [o for o in items if o.props.get(field) == value]
        items.sort(key=lambda o: o.updated_at_iso, reverse=True)
        return items[:limit] if limit else items

    def supersede(self, loser_id: str, winner_id: str, by: str, reason: str) -> None:
        """Merge: the loser keeps its claims and points at the winner.

        Nothing is deleted. A merge that turns out to be wrong is undone by
        clearing the pointer, and the claims that were merged in are still
        attributable to the source that made them.
        """
        loser = self.store.get_object(loser_id)
        winner = self.store.get_object(winner_id)
        if loser is None or winner is None or loser_id == winner_id:
            return
        for claim in loser.claims:
            winner.claims.append(claim)
        winner.props = resolve_props(winner.claims, self.ontology.precedence)
        winner.revision += 1
        winner.updated_at_iso = utcnow().isoformat()
        loser.status = "superseded"
        loser.superseded_by = winner_id
        loser.provenance = f"{loser.provenance} · merged by {by}: {reason}".strip(" ·")
        loser.updated_at_iso = utcnow().isoformat()
        self.store.put_object(winner)
        self.store.put_object(loser)
        log.info(
            "Objects merged",
            extra={"loser": loser_id, "winner": winner_id, "actor": by},
        )

    # -- links --------------------------------------------------------------

    def link(
        self,
        link_type: str,
        from_id: str,
        to_id: str,
        *,
        props: dict[str, Any] | None = None,
        confidence: float = 1.0,
        method: LinkMethod = "declared",
        provenance: str = "",
        run_id: str = "",
        step_id: str = "",
    ) -> LinkRecord:
        lid = link_id(link_type, from_id, to_id)
        existing = self.store.get_link(lid)
        if existing is not None and existing.live:
            if props:
                existing.props.update(props)
                self.store.put_link(existing)
            return existing
        self._enforce(self._validate_link(
            link_type,
            self.store.get_object(from_id),
            self.store.get_object(to_id),
            from_id,
            to_id,
        ))
        record = LinkRecord(
            id=lid,
            type=link_type,
            from_id=from_id,
            to_id=to_id,
            props=props or {},
            confidence=confidence,
            method=method,
            provenance=provenance,
            run_id=run_id,
            step_id=step_id,
        )
        return self.store.put_link(record)

    def reverse_link(self, lid: str, by: str, reason: str) -> LinkRecord | None:
        """Withdraw an assertion without erasing that it was made."""
        record = self.store.get_link(lid)
        if record is None or not record.live:
            return record
        now = utcnow().isoformat()
        record.reversed_at_iso = now
        record.valid_to_iso = now
        record.reversed_by = by
        record.reversal_reason = reason
        log.info("Link reversed", extra={"linkId": lid, "actor": by})
        return self.store.put_link(record)

    def neighbors(
        self,
        object_id_: str,
        link_type: str | None = None,
        direction: str = "out",
        *,
        include_reversed: bool = False,
    ) -> list[tuple[LinkRecord, ObjectRecord]]:
        candidates: list[tuple[LinkRecord, str]] = []
        if direction in ("out", "both"):
            candidates += [
                (link, link.to_id) for link in self.store.links_from(object_id_)
            ]
        if direction in ("in", "both"):
            candidates += [
                (link, link.from_id) for link in self.store.links_to(object_id_)
            ]
        out: list[tuple[LinkRecord, ObjectRecord]] = []
        seen: set[str] = set()
        for link, other_id in candidates:
            if link_type is not None and link.type != link_type:
                continue
            if not include_reversed and not link.live:
                continue
            if link.id in seen:
                continue  # a self-loop would otherwise appear twice on "both"
            seen.add(link.id)
            other = self.store.get_object(other_id)
            if other is not None:
                out.append((link, other))
        return out

    def traverse(
        self, start_id: str, path: list[tuple[str, str]]
    ) -> list[ObjectRecord]:
        """Walk a fixed path of (link_type, direction) hops.

        Fixed-length and typed on purpose. Open-ended traversal is where a
        document store stops being the right answer, so the shape of the
        question is declared by the caller rather than discovered at run time.
        """
        frontier = [start_id]
        records: list[ObjectRecord] = []
        for link_type, direction in path:
            next_ids: list[str] = []
            records = []
            for node in frontier:
                for _, other in self.neighbors(node, link_type, direction):
                    if other.id not in next_ids:
                        next_ids.append(other.id)
                        records.append(other)
            frontier = next_ids
            if not frontier:
                break
        return records

    def stats(self) -> dict:
        objects = self.store.objects()
        links = [linked for linked in self.store.links() if linked.live]
        by_type: dict[str, int] = {}
        for obj in objects:
            by_type[obj.type] = by_type.get(obj.type, 0) + 1
        by_link: dict[str, int] = {}
        for link in links:
            by_link[link.type] = by_link.get(link.type, 0) + 1
        conflicted = sum(1 for o in objects if o.conflicts())
        return {
            "objects": len(objects),
            "links": len(links),
            "by_type": by_type,
            "by_link_type": by_link,
            "conflicted_objects": conflicted,
        }


class GraphWriter:
    """A GraphService bound to one run and one step.

    Two jobs, and they are the same job seen from both ends: every claim and
    link it writes carries the act that produced it, and it accumulates the
    **write-set** — the refs of what it touched — so the step that used it can
    record what it wrote. Runs already record what they read; without this they
    could not say what they changed, which is the half an auditor actually
    asks about.
    """

    def __init__(self, graph: "GraphService", run_id: str, step_id: str) -> None:
        self._graph = graph
        self.run_id = run_id
        self.step_id = step_id
        self.created: list[str] = []
        self.updated: list[str] = []
        self.linked: list[str] = []

    # -- the accumulated write-set ----------------------------------------

    @property
    def refs(self) -> list[str]:
        """Everything written, newest last, deduplicated in order."""
        seen: set[str] = set()
        out: list[str] = []
        for ref in [*self.created, *self.updated, *self.linked]:
            if ref not in seen:
                seen.add(ref)
                out.append(ref)
        return out

    def summary(self) -> str:
        parts = []
        if self.created:
            parts.append(f"{len(self.created)} object(s) minted")
        if self.updated:
            parts.append(f"{len(self.updated)} updated")
        if self.linked:
            parts.append(f"{len(self.linked)} link(s)")
        return " · ".join(parts) or "nothing written"

    # -- the same surface as GraphService, stamped -------------------------

    def upsert(
        self,
        object_type: str,
        natural_key: str,
        claims: list[Claim],
        **kwargs: Any,
    ) -> ObjectRecord:
        existed = self._graph.store.get_object(object_id(object_type, natural_key))
        stamped = [
            claim.model_copy(update={"run_id": self.run_id, "step_id": self.step_id})
            if not claim.run_id else claim
            for claim in claims
        ]
        record = self._graph.upsert(object_type, natural_key, stamped, **kwargs)
        (self.updated if existed is not None else self.created).append(record.ref())
        return record

    def link(self, link_type: str, from_id: str, to_id: str, **kwargs: Any) -> LinkRecord:
        record = self._graph.link(
            link_type, from_id, to_id,
            run_id=self.run_id, step_id=self.step_id, **kwargs,
        )
        self.linked.append(record.ref())
        return record

    def reverse_link(self, link_id_: str, by: str, reason: str) -> LinkRecord | None:
        record = self._graph.reverse_link(link_id_, by, reason)
        if record is not None:
            self.linked.append(record.ref())
        return record


def build_graph_service(
    backend: str,
    project: str | None,
    objects_collection: str,
    links_collection: str,
    strict: bool = True,
) -> GraphService:
    if backend == "firestore":
        return GraphService(
            FirestoreGraphStore(project, objects_collection, links_collection),
            strict=strict,
        )
    return GraphService(InMemoryGraphStore(), strict=strict)
