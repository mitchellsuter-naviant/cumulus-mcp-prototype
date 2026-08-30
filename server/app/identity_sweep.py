"""The cross-source identity proposer: two entities, one real-world party.

Per-source resolution answers "which entity is this arriving row?" and answers
it well. It structurally cannot answer the question this module exists for.
Three reasons, and each is a real hole the estate walks into:

1. **A feed scores its rows against the book as it stood before the batch.**
   The CRM delivers `Verona Foods Group` and `Verona Foods` in one page; both
   are scored against a book holding neither, so both mint. Nothing is wrong
   with that — a resolver that scored rows against its own uncommitted output
   would depend on delivery order — but it means the duplicate exists and only
   a later pass over the *book* can see it.
2. **A Company is never scored against a Vendor.** The two books are separate
   types on purpose (buying from a firm and selling to it are different roles
   with different authorities), so no per-source path ever compares them, and
   the one question a counterparty book exists to answer — "is the firm we buy
   from the firm we sell to" — has no code path at all.
3. **People are keyed on an address and never on a name**, deliberately, so
   `tom.berg@` and `thomas.berg@` are two Persons and stay two Persons. That
   is the right default. It is not the right final answer.

So this is a **windowed** Flow — the unit of work is a pass over the book, not
a row — started by an `IdentityWindowClosed` Event through the Dispatcher like
everything else. It scores pairs on evidence, writes each survivor as a typed
identity Link at `status="in_review"` carrying its confidence, method, decider
and what an un-merge restores, and raises a steward Task for **every one of
them**. A merge is the doctrine's named reversible-with-provenance case — a
high-blast-radius change — so confidence buys a proposal a gentler Task, never
an automatic one, and never a place in the book with nobody able to decide it.

**It proposes. It never merges.** No canonical object is touched: the sweep
writes edges and nothing else, which is a property you can check rather than a
promise you have to trust — every ref in its ledger entry starts `link:`. The
proposal is status-gated in the doctrine's sense: `in_review` is visible and
queryable and the Gateway will not let a merge act on it until a human flips it
to `confirmed`.

**Why rules and not a model.** Every signal here is a fact two rows either
share or do not — a domain, a phone, an anchor word, an employer — and the
blend is arithmetic. A ModelCall would add cost, non-determinism and an
unexplainable number to a decision whose whole value is that a steward can read
the evidence and disagree with it. The weights are governed knowledge
(`entity-resolution-policy`), so the tuning that would otherwise be a prompt is
a document somebody can edit.

**What is deliberately not a signal yet.** The roster's hard cases join a
vendor to its *trading name in mail* (`GSF Logistics` for Granite State
Freight, `Talon Service & Supply` for Talon Imaging). That is a
correspondent↔party claim rather than a merge between two canonical objects,
and it wants its own pass with its own evidence; naming it as a weight here
would declare a signal that never fires. Same for document-number references:
the estate's PO and SO numbering never crosses the customer and vendor books,
so a shared-document-ref weight would be decoration.
"""

from __future__ import annotations

import itertools
import logging
import re
from dataclasses import dataclass
from typing import Iterable, Literal

from pydantic import BaseModel, Field

from .graph import GraphService, GraphWriter, ObjectRecord, link_id
from .mappings import steward_queue_for_type
from .models import utcnow
from .resolution import SweepThresholds, name_similarity, read_sweep_thresholds
from .runs import RunContext
from .tasks import Task, TaskEvidence, TaskProposal, TaskService

log = logging.getLogger("cumulus.identity_sweep")

IDENTITY_SWEEP_FLOW_ID = "identity-sweep"
#: The fact that starts it: a window closed over the book. Not a delivery —
#: nothing arrived — which is why this is the first windowed Trigger in
#: `dispatch.TRIGGERS` that no feed emits.
IDENTITY_WINDOW_EVENT = "IdentityWindowClosed"

PairKind = Literal["organization", "person"]

#: The four pair shapes the sweep looks at, and the identity link each writes.
#: A table rather than four code paths: the scoring is one function, and what
#: differs between "two companies" and "a company and a vendor" is which two
#: books get walked and which declaration carries the answer.
@dataclass(frozen=True)
class PairScan:
    link_type: str
    from_type: str
    to_type: str
    kind: PairKind
    #: True when both sides come out of one book, so the scan is combinations
    #: within a list rather than a product of two.
    within_one_book: bool


SCANS: tuple[PairScan, ...] = (
    PairScan("company_same_as", "Company", "Company", "organization", True),
    PairScan("vendor_same_as", "Vendor", "Vendor", "organization", True),
    PairScan("company_same_as_vendor", "Company", "Vendor", "organization", False),
    PairScan("person_same_as", "Person", "Person", "person", True),
)

SCAN_BY_LINK: dict[str, PairScan] = {s.link_type: s for s in SCANS}
#: Which kind of thing each book holds. Derived from the scans rather than
#: declared twice — a type that appeared as an organization in one scan and a
#: person in another would be scored two ways depending on iteration order.
KIND_BY_TYPE: dict[str, PairKind] = {
    type_name: scan.kind
    for scan in SCANS
    for type_name in (scan.from_type, scan.to_type)
}

#: Signal weights, per pair kind. Governed values in prose live in the
#: `entity-resolution-policy` asset; these are the code half — the machinery
#: that produces a number a steward reads next to two names.
#:
#: The asymmetry between the two columns is the load-bearing part. A shared
#: mail domain says two organizations are one firm and says two people are
#: colleagues, which is a precondition for being the same person and nowhere
#: near proof of it — so for a person it is folded into `shared-employer` at a
#: quarter of the weight and never scored twice.
WEIGHTS: dict[tuple[PairKind, str], float] = {
    ("organization", "shared-domain"): 0.55,
    ("organization", "conflicting-domain"): -0.35,
    #: Two organizations that each published a way to be reached, in different
    #: currencies, with nothing in common: one is on a domain and has no
    #: number, the other has a number and no domain. Same weight as a
    #: conflicting domain because it is the same argument — two firms that
    #: were the same firm would be expected to agree *somewhere*, and these
    #: two records agree nowhere while both being perfectly well filled in.
    #: It is the one thing that separates a Milwaukee manufacturer's nickname
    #: from a Rockford refurbisher's legal name once the nickname arrives from
    #: a system that keeps no phone numbers.
    ("organization", "conflicting-contact"): -0.35,
    ("organization", "shared-phone"): 0.45,
    ("organization", "shared-area-code"): 0.08,
    ("organization", "conflicting-area-code"): -0.20,
    ("organization", "shared-anchor"): 0.20,
    ("organization", "name-similarity"): 0.80,
    ("person", "shared-employer"): 0.25,
    ("person", "shared-phone"): 0.45,
    ("person", "shared-area-code"): 0.08,
    ("person", "conflicting-area-code"): -0.20,
    ("person", "shared-anchor"): 0.20,
    ("person", "name-similarity"): 0.80,
}

#: How a proposal came to be, in the vocabulary the graph's `LinkMethod`
#: already speaks. `fuzzy` when the case rests on names, `exact` when a hard
#: identifier (a domain, a phone number) carried it.
_HARD_SIGNALS = {"shared-domain", "shared-phone"}

_WORD = re.compile(r"[^a-z0-9]+")
_DIGITS = re.compile(r"\D+")
#: Free mail hosts are not employers and not identity. A pair of people on
#: gmail is not a pair of colleagues, and two companies both reachable at a
#: webmail address share nothing.
_PUBLIC_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "yahoo.com", "hotmail.com", "outlook.com",
    "live.com", "aol.com", "icloud.com", "me.com", "msn.com", "proton.me",
    "protonmail.com", "willowmail.example.com",
})


class Signal(BaseModel):
    """One piece of evidence, with what it was worth and why.

    Carried onto the link and into the Task verbatim, because the number is
    only useful to a steward beside the sentence that produced it.
    """

    name: str
    weight: float
    detail: str


class Proposal(BaseModel):
    """One candidate merge, scored, with the evidence that scored it."""

    link_type: str
    from_id: str
    to_id: str
    from_label: str
    to_label: str
    kind: PairKind
    confidence: float
    method: str
    signals: list[Signal] = Field(default_factory=list)

    @property
    def reason(self) -> str:
        return " · ".join(s.detail for s in self.signals) or "no evidence"


# ---------------------------------------------------------------------------
# Reading evidence off an object
# ---------------------------------------------------------------------------


@dataclass
class Party:
    """One candidate, reduced to the facts a match turns on.

    Built once per object per pass rather than read out of the graph inside the
    pairwise loop: the scan is quadratic in the book, and walking `works_at`
    forty-five times per company is how a sweep that should take a second takes
    a minute.
    """

    record: ObjectRecord
    names: tuple[str, ...]
    domains: frozenset[str]
    phones: frozenset[str]
    anchor: str
    employer_id: str = ""

    @property
    def id(self) -> str:
        return self.record.id

    @property
    def label(self) -> str:
        return self.record.label or self.record.natural_key


def _domain(value: str) -> str:
    """The registrable-ish host out of an address or a URL, or ""."""
    text = (value or "").strip().lower()
    if not text:
        return ""
    if "@" in text:
        text = text.rsplit("@", 1)[1]
    text = re.sub(r"^[a-z]+://", "", text)
    text = text.split("/", 1)[0].split("?", 1)[0]
    text = text.removeprefix("www.").strip(".")
    if "." not in text or text in _PUBLIC_DOMAINS:
        return ""
    return text


def _phone(value: str) -> str:
    """A phone reduced to comparable digits, or "" if it is not one.

    NANP numbers are normalized to ten digits so `(608) 555-0117` and
    `+1 608 555 0117` compare equal. Anything shorter is not enough number to
    be evidence about anything.
    """
    digits = _DIGITS.sub("", value or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else ""


def _tokens(value: str) -> list[str]:
    return [t for t in _WORD.split((value or "").lower()) if t]


def _anchor(name: str, kind: PairKind) -> str:
    """The word that carries the identity, by kind.

    For an organization it is the first — `Northwind`, `Cedarline`, `Verona` —
    because English company names put the distinctive part in front and the
    generic part behind (`Group`, `Services`, `Systems`, `School District`). A
    shared *generic* word is nearly no evidence: Sauk Prairie School District
    and Sugar River School District share two of three words and are two
    school districts. For a person it is the last: a surname.

    This is the signal that separates a stale duplicate from two neighbours
    with the same line of work, and it is one token comparison.
    """
    words = _tokens(name)
    if not words:
        return ""
    return words[0] if kind == "organization" else words[-1]


def _values(record: ObjectRecord, *fields: str) -> list[str]:
    out: list[str] = []
    for field_name in fields:
        value = record.props.get(field_name)
        if isinstance(value, list):
            out += [str(v) for v in value if v]
        elif value:
            out.append(str(value))
    return out


def build_party(graph: GraphService, record: ObjectRecord, kind: PairKind) -> Party:
    """Everything the scorer needs about one object, read once."""
    # A Company's natural key *is* its name, so it belongs in the comparison; a
    # Person's is their email address, and it very much does not. Two addresses
    # at one company agree on everything after the `@`, so scoring them as
    # names reads a shared employer as a name match and proposes merging every
    # pair of colleagues — which is exactly what the CRM feed keys people on an
    # address to avoid.
    candidates = [str(record.props.get("name") or ""), record.label]
    if kind == "organization":
        candidates.append(record.natural_key)
        candidates += _values(record, "aliases")
    else:
        first, last = _values(record, "first_name"), _values(record, "last_name")
        if first and last:
            candidates.append(f"{first[0]} {last[0]}")
    names = tuple(dict.fromkeys(n for n in candidates if n))
    domains = {_domain(v) for v in _values(record, "email", "website", "domain")}
    phones = {_phone(v) for v in _values(record, "phone")}
    employer = ""
    if kind == "person":
        # The employer edge first, the mail domain second. Both say "these two
        # are in the same place"; the edge says it because the CRM filed them
        # under one account, and the domain says it because their addresses
        # agree. Either will do, and neither is scored twice.
        for _link, company in graph.neighbors(record.id, "works_at", "out"):
            employer = company.id
            break
        if not employer:
            employer = next((f"domain:{d}" for d in sorted(domains) if d), "")
    elif record.type == "Company":
        # A company's own row often carries no address at all — a CRM account
        # minted off a badge list has neither website nor phone. The people
        # filed under it do, and their addresses are the company's domain by
        # any reading a human would give them.
        for _link, person in graph.neighbors(record.id, "works_at", "in"):
            domains |= {_domain(v) for v in _values(person, "email")}
    return Party(
        record=record,
        names=names,
        domains=frozenset(d for d in domains if d),
        phones=frozenset(p for p in phones if p),
        anchor=_anchor(names[0] if names else "", kind),
        employer_id=employer,
    )


# ---------------------------------------------------------------------------
# Scoring one pair
# ---------------------------------------------------------------------------


def _phone_signal(left: Party, right: Party, kind: PairKind) -> Signal | None:
    """One signal from the numbers, in three bands.

    One signal and not three, because they are the same comparison read at
    three resolutions: the same number is the strongest ordinary evidence
    there is, the same area code is a shared town, and different area codes
    are two towns — which is exactly what tells Apex Manufacturing in
    Milwaukee from Apex Mfg. Services in Rockford.
    """
    if not left.phones or not right.phones:
        return None
    shared = left.phones & right.phones
    if shared:
        return Signal(
            name="shared-phone", weight=WEIGHTS[(kind, "shared-phone")],
            detail=f"both carry the number {sorted(shared)[0]}",
        )
    left_codes = {p[:3] for p in left.phones}
    right_codes = {p[:3] for p in right.phones}
    if left_codes & right_codes:
        return Signal(
            name="shared-area-code",
            weight=WEIGHTS[(kind, "shared-area-code")],
            detail=f"same area code ({sorted(left_codes & right_codes)[0]}), "
                   "different numbers",
        )
    return Signal(
        name="conflicting-area-code",
        weight=WEIGHTS[(kind, "conflicting-area-code")],
        detail=f"different area codes ({sorted(left_codes)[0]} and "
               f"{sorted(right_codes)[0]})",
    )


def _crossed_contact(left: Party, right: Party) -> Signal | None:
    """Both reachable, neither comparably — the negative `_phone_signal` cannot
    reach.

    A gap the estate walked straight into once a sixth system arrived. The
    phone bands only fire when *both* sides carry a number and the domain
    signals only when both carry a domain, so a pair where one side has a
    domain and no number and the other a number and no domain is scored on its
    name alone — and a name is exactly what a bait pair is built out of. `Apex
    Mfg`, a delivery system's nickname for the Milwaukee customer, reaches this
    with `apexmfg.example.com` and no switchboard; `Apex Mfg. Services LLC`, a
    Rockford refurbisher, reaches it with the 815 and no address. Their names
    agree at 0.78 and every hard fact they hold is in a currency the other one
    does not use.

    Deliberately narrow: it fires only when each side is missing exactly what
    the other has. A record that holds *nothing* — the badge-list `Verona
    Foods`, with no website and no phone — is not evidence of difference, it is
    an absence, and the name signal is properly load-bearing there. And
    organizations only: for a person a mail domain is an employer signal rather
    than a way to reach them, which is why it is folded into `shared-employer`
    and never scored as reachability.
    """
    for one, other in ((left, right), (right, left)):
        if one.domains and not one.phones and other.phones and not other.domains:
            return Signal(
                name="conflicting-contact",
                weight=WEIGHTS[("organization", "conflicting-contact")],
                detail=(
                    f"no contact point in common — one is reachable at "
                    f"{sorted(one.domains)[0]} with no number, the other on "
                    f"{sorted(other.phones)[0]} with no address"
                ),
            )
    return None


def score_pair(
    left: Party, right: Party, kind: PairKind, thresholds: SweepThresholds
) -> tuple[float, list[Signal], str]:
    """Confidence, evidence, and method for one candidate pair.

    Returns a confidence of 0 for a pair that is not a candidate at all —
    below the name floor with nothing hard joining it, or (for people) with no
    established shared employer. That is not the same as a low score: "these
    were never compared" and "these were compared and rejected" are different
    facts, and only the second belongs in front of a steward.
    """
    similarity = max(
        (name_similarity(a, b) for a in left.names for b in right.names),
        default=0.0,
    )
    signals: list[Signal] = []

    shared_domains = left.domains & right.domains
    if kind == "organization":
        if shared_domains:
            signals.append(Signal(
                name="shared-domain", weight=WEIGHTS[(kind, "shared-domain")],
                detail=f"both are on {sorted(shared_domains)[0]}",
            ))
        elif left.domains and right.domains:
            signals.append(Signal(
                name="conflicting-domain",
                weight=WEIGHTS[(kind, "conflicting-domain")],
                detail=f"different domains ({sorted(left.domains)[0]} and "
                       f"{sorted(right.domains)[0]})",
            ))
        else:
            crossed = _crossed_contact(left, right)
            if crossed is not None:
                signals.append(crossed)
    elif left.employer_id and left.employer_id == right.employer_id:
        signals.append(Signal(
            name="shared-employer", weight=WEIGHTS[(kind, "shared-employer")],
            detail="both are filed at the same company",
        ))

    phone = _phone_signal(left, right, kind)
    if phone is not None:
        signals.append(phone)

    if left.anchor and left.anchor == right.anchor:
        signals.append(Signal(
            name="shared-anchor", weight=WEIGHTS[(kind, "shared-anchor")],
            detail=(
                f"same {'first word' if kind == 'organization' else 'surname'}"
                f" — '{left.anchor}'"
            ),
        ))

    names_ok = similarity >= thresholds.name_floor
    hard = {s.name for s in signals} & _HARD_SIGNALS
    # Candidacy. Organizations get looked at on names alone or on a hard
    # identifier; people are only looked at once something places them
    # together, because two humans who share a name are two humans.
    if kind == "person":
        eligible = any(s.name == "shared-employer" for s in signals) and (
            names_ok or "shared-phone" in hard
        )
    else:
        eligible = names_ok or bool(hard)
    if not eligible:
        return 0.0, [], "none"

    signals.append(Signal(
        name="name-similarity",
        weight=round(WEIGHTS[(kind, "name-similarity")] * similarity, 4),
        detail=f"'{left.label}' ~ '{right.label}' at {similarity:.2f}",
    ))
    score = min(
        thresholds.ceiling, max(0.0, sum(s.weight for s in signals))
    )
    return round(score, 4), signals, ("exact" if hard else "fuzzy")


# ---------------------------------------------------------------------------
# The pass
# ---------------------------------------------------------------------------


def _ordered(left: Party, right: Party) -> tuple[Party, Party]:
    """A stable direction for an edge between two peers.

    By natural key, so re-running the sweep produces the same link id (they are
    derived from type + endpoints) and a second pass updates one edge rather
    than adding its mirror. Lexicographic and not "the older one loses",
    because which row survives a merge is the steward's call and encoding a
    guess about it in the direction of the edge would be the sweep quietly
    making it.
    """
    return (left, right) if left.record.natural_key <= right.record.natural_key else (right, left)


def propose(
    graph: GraphService, thresholds: SweepThresholds
) -> tuple[list[Proposal], dict]:
    """Score the whole book against itself. Reads only — nothing is written.

    Separated from the writing so the scan is testable without a run, a
    Gateway or a Task queue, and so the flow body below reads as what it is:
    read the policy, score, commit, ask.
    """
    counts = {"compared": 0, "considered": 0, "proposed": 0, "below_floor": 0}
    proposals: list[Proposal] = []
    parties: dict[str, list[Party]] = {
        type_name: [
            build_party(graph, record, kind)
            for record in graph.find(type_name)
            if record.status != "superseded"
        ]
        for type_name, kind in KIND_BY_TYPE.items()
    }

    for scan in SCANS:
        if scan.within_one_book:
            pairs: Iterable[tuple[Party, Party]] = itertools.combinations(
                parties[scan.from_type], 2
            )
        else:
            pairs = itertools.product(
                parties[scan.from_type], parties[scan.to_type]
            )
        for left, right in pairs:
            counts["compared"] += 1
            if scan.within_one_book:
                left, right = _ordered(left, right)
            score, signals, method = score_pair(left, right, scan.kind, thresholds)
            if not signals:
                continue
            counts["considered"] += 1
            if score < thresholds.propose_at:
                counts["below_floor"] += 1
                continue
            counts["proposed"] += 1
            proposals.append(Proposal(
                link_type=scan.link_type,
                from_id=left.id, to_id=right.id,
                from_label=left.label, to_label=right.label,
                kind=scan.kind, confidence=score, method=method,
                signals=signals,
            ))

    proposals.sort(key=lambda p: p.confidence, reverse=True)
    return proposals, counts


def _reversible_with(proposal: Proposal) -> str:
    """What an un-merge restores, in the words a steward will read.

    The honest answer is short because the sweep's blast radius is small: it
    supersedes nothing and rewrites no claim, so withdrawing a proposal is
    withdrawing this edge. Recorded on the link anyway, because the doctrine
    asks the identity link to carry its reversal information and because the
    next thing that acts on a confirmed proposal will need somewhere to say
    what *it* did.
    """
    return (
        f"withdraw this {proposal.link_type} link; both objects "
        f"({proposal.from_label}, {proposal.to_label}) keep their own claims, "
        "source links and history, none of which this proposal touched"
    )


def write_proposals(
    service: GraphService,
    graph: GraphWriter,
    proposals: list[Proposal],
    thresholds: SweepThresholds,
    *,
    flow_id: str,
    run_id: str,
) -> dict:
    """Each proposal as an in_review identity link. Objects are not touched.

    Link ids derive from type and endpoints, so a second pass over an unchanged
    book addresses the same edges: the sweep is idempotent by construction. An
    edge that is already there is left exactly as it is rather than restamped —
    a proposal a steward has confirmed or withdrawn must not be re-opened
    because a scorer ran again, and a still-open one gains nothing from a new
    timestamp.
    """
    counts = {"proposed": 0, "standing": 0}
    for proposal in proposals:
        existing = service.store.get_link(link_id(
            proposal.link_type, proposal.from_id, proposal.to_id
        ))
        if existing is not None:
            counts["standing"] += 1
            continue
        graph.link(
            proposal.link_type, proposal.from_id, proposal.to_id,
            props={
                # The doctrine's required properties on a typed identity Link.
                # `confidence` and `method` are first-class on the record;
                # these are the two it has no column for, plus the evidence a
                # human needs to disagree with the number.
                "status": "in_review",
                "decided_by": f"flow:{flow_id}",
                "reversible_with": _reversible_with(proposal),
                "pair_kind": proposal.kind,
                "signals": [s.model_dump() for s in proposal.signals],
                "proposed_at": utcnow().isoformat(),
                "policy_ref": thresholds.source_ref,
                "run_id": run_id,
            },
            confidence=proposal.confidence,
            method=proposal.method,
            provenance=proposal.reason,
        )
        counts["proposed"] += 1
    return counts


def raise_merge_tasks(
    tasks: TaskService,
    proposals: list[Proposal],
    thresholds: SweepThresholds,
    *,
    flow_id: str,
    run_id: str,
) -> dict:
    """A Task per proposal. Every one of them, whatever it scored.

    This used to raise a Task only *below* the governed bar, on the reasoning
    that a confident proposal could wait in the workbench with everything else.
    What it actually produced, on the live estate, was the three strongest
    proposals in the book — Kessler Holdings against Prairie Records at 0.95
    on a shared domain and a shared switchboard among them — standing at
    `in_review` with nobody paged, no Task, and therefore **no decision path
    at all**: the one status the Gateway refuses to act on, held by exactly
    the proposals most likely to be right.
    Confidence is not authority. A merge is the doctrine's named
    reversible-with-provenance case, so nothing here is ever auto-confirmed,
    and a proposal a machine is sure about still needs somewhere for a person
    to say yes.

    So the bar governs the *character* of the ask and not its existence:

    - **under it** — a genuine question at normal priority, the machine saying
      out loud that it does not know;
    - **at or above it** — a low-priority ratification: the evidence is strong,
      confirm to let downstream flows treat the two as one party.

    The queue comes from the Mapping that owns the book, so a rebind moves this
    without touching the flow.

    Task ids derive from the edge, so a second pass over an unchanged book
    addresses the same rows — and `TaskService.create` is a put. Raising one
    that already exists would therefore reset a Task a steward had answered
    back to `open` and drop their verdict, which is the quietest possible way
    for a scheduled sweep to undo human judgment. So an existing Task is left
    alone, decided or not. The same property is what backfills the proposals
    written before this rule: a later pass over the same book finds them
    Task-less and raises the ones that are missing.
    """
    raised = 0
    standing = 0
    ratifications = 0
    for proposal in proposals:
        ratifying = proposal.confidence >= thresholds.ratify_at
        # The desk that owns the book the proposal starts in, read off the
        # Mapping. `company_same_as_vendor` therefore goes to the counterparty
        # book's steward and not to the AP desk, which is right: whether the
        # firm we sell to is the firm we buy from is a master-data question,
        # and only its *consequences* are anybody's payables problem.
        queue = steward_queue_for_type(SCAN_BY_LINK[proposal.link_type].from_type)
        edge = link_id(proposal.link_type, proposal.from_id, proposal.to_id)
        task_id = f"task_merge_{edge[-12:]}"
        if tasks.get(task_id) is not None:
            standing += 1
            continue
        tasks.create(Task(
            id=task_id,
            kind="merge_review",
            title=(
                f"One party, on the evidence: {proposal.from_label} / "
                f"{proposal.to_label}"
                if ratifying
                else f"Same party? {proposal.from_label} / {proposal.to_label}"
            ),
            summary=(
                f"The identity sweep scored these two at "
                f"{proposal.confidence:.2f}, at or above the "
                f"{thresholds.ratify_at:.2f} bar where its evidence is strong "
                "enough that this is a ratification rather than a question. "
                "It is still a question for you: nothing merges on a score. "
                "Approve to let downstream flows treat them as one party, "
                "reject to withdraw the proposal. Both objects keep "
                "everything they hold either way."
                if ratifying else
                f"The identity sweep scored these two at "
                f"{proposal.confidence:.2f}, under the "
                f"{thresholds.ratify_at:.2f} bar where it would call the "
                "evidence strong — so this is a genuine question and the "
                "machine is saying out loud that it does not know. Nothing "
                "has been merged and nothing will be: approve to confirm the "
                "proposal, reject to withdraw it. Both objects keep "
                "everything they hold either way."
            ),
            queue=queue,
            # The bar as urgency. A ratification a steward never gets to is a
            # proposal standing unanswered, which is the state this whole
            # change exists to end — so it is raised low rather than not at
            # all, and it sorts under every genuine ask in the queue.
            priority="low" if ratifying else "normal",
            flow_id=flow_id,
            run_id=run_id,
            # The proposal itself is the case. A `match_review` Task points at
            # a landed SourceRecord because the decision is about a row; this
            # one points at the edge, because the decision is about an
            # assertion that already exists and needs its status flipped.
            case_ref=f"link:{proposal.link_type}/{edge}",
            refs=[
                thresholds.source_ref,
                f"graph:{proposal.from_id}", f"graph:{proposal.to_id}",
            ],
            evidence=[
                TaskEvidence(label="Confidence",
                             value=f"{proposal.confidence:.2f} "
                                   f"({proposal.method})"),
            ] + [
                TaskEvidence(label=s.name, value=f"{s.detail} ({s.weight:+.2f})")
                for s in proposal.signals
            ],
            proposal=TaskProposal(
                action="confirm-identity-link",
                target="entity-layer",
                summary=(
                    f"Confirm that {proposal.from_label} and "
                    f"{proposal.to_label} are one party"
                ),
            ),
            resumes="the proposal's status flips on decision; nothing merges",
            created_at_iso=utcnow().isoformat(),
        ))
        raised += 1
        if ratifying:
            ratifications += 1
    return {
        "steward_tasks": raised,
        "steward_tasks_standing": standing,
        # Of the ones raised, how many were formalities. Reported because
        # "five Tasks landed on the desk" and "four of them are ratifications"
        # are different facts about a steward's morning.
        "steward_ratifications": ratifications,
    }


# ---------------------------------------------------------------------------
# The flow
# ---------------------------------------------------------------------------


def register(service) -> None:
    """Bind the body to the engine `SyncService` was handed."""
    if service.engine is None:
        return
    service.engine.register(
        IDENTITY_SWEEP_FLOW_ID,
        lambda ctx, inputs: sweep_body(service, ctx, inputs),
    )


def sweep_body(service, ctx: RunContext, inputs: dict) -> dict:
    """Read the policy, score the book against itself, propose, ask.

    Five journal rows and one write: only `a1` touches the graph, and what it
    writes is edges. A reader checking the claim this module opens with — that
    the sweep cannot merge anything — has one step to read.
    """
    window = inputs.get("window", "the whole book")

    ctx.event(
        "t1", IDENTITY_WINDOW_EVENT,
        f"identity window closed over {window}", kind="trigger",
    )

    raw = ctx.step(
        "x1", "read-sweep-policy", "transform",
        fn=lambda: read_sweep_thresholds(service.knowledge).model_dump(),
        summary=lambda out: (
            f"{out['source_ref']}: propose ≥ {out['propose_at']:.2f} · "
            f"every proposal asks a steward, as a ratification ≥ "
            f"{out['ratify_at']:.2f} · names alone ≥ {out['name_floor']:.2f}"
        ),
    )
    thresholds = SweepThresholds.model_validate(raw)
    ctx.note_refs(thresholds.source_ref)

    def score() -> dict:
        found, counts = propose(service.graph, thresholds)
        return {"proposals": [p.model_dump() for p in found], "counts": counts}

    scored = ctx.step(
        "z1", "score-candidates", "resolve",
        # Deliberately outside the Action: scoring reads the whole book and
        # writes nothing, and wrapping a thousand comparisons in a guarded
        # write would put them all on one ledger entry that recorded no change.
        fn=score,
        summary=lambda out: (
            f"{out['counts']['compared']} pair(s) compared · "
            f"{out['counts']['considered']} with evidence · "
            f"{out['counts']['proposed']} above the floor"
        ),
        refs=[thresholds.source_ref],
    )
    proposals = [Proposal.model_validate(p) for p in scored["proposals"]]

    act = service._act(ctx, "propose-identity-link", "a1")
    written = ctx.step(
        "a1", "propose-merges", "action",
        fn=lambda: act.commit(lambda w: write_proposals(
            service.graph, w, proposals, thresholds,
            flow_id=IDENTITY_SWEEP_FLOW_ID, run_id=ctx.record.id,
        )),
        writes=lambda _out: act.writes,
        summary=lambda out: (
            f"{out['proposed']} proposal(s) written at `in_review` · "
            f"{out['standing']} already on the graph · nothing merged"
        ),
    )

    asked = ctx.step(
        "k1", "ask-a-steward", "sync",
        fn=lambda: raise_merge_tasks(
            service.tasks, proposals, thresholds,
            flow_id=IDENTITY_SWEEP_FLOW_ID, run_id=ctx.record.id,
        ),
        summary=lambda out: (
            f"{out['steward_tasks']} Task(s) raised — "
            f"{out['steward_tasks'] - out['steward_ratifications']} ask(s), "
            f"{out['steward_ratifications']} ratification(s) at or above "
            f"{thresholds.ratify_at:.2f} · "
            f"{out['steward_tasks_standing']} already open or answered"
            if out["steward_tasks"] or out["steward_tasks_standing"]
            else "nothing to propose, so nobody to ask"
        ),
    )

    ctx.event(
        "e1", "IdentityMergesProposed",
        f"{written['proposed']} merge(s) proposed, "
        f"{asked['steward_tasks']} sent to a steward",
    )
    return {
        "window": window, "kind": "identity-sweep",
        **scored["counts"], **written, **asked,
        # The ten a screen would show first. On the run, because a windowed
        # flow's output is the only place its findings are legible without
        # re-querying the graph — and because "what did the sweep actually
        # think" is the first question anybody asks of it.
        "top": [
            {
                "link_type": p.link_type, "confidence": p.confidence,
                "from": p.from_label, "to": p.to_label, "why": p.reason,
            }
            for p in proposals[:10]
        ],
    }
