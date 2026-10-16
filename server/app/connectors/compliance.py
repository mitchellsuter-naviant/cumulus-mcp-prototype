"""Two feeds about permission: who may still do this, and what may still run.

They share a module because they share a shape. A certification, a CDL, a
medical card and an annual vehicle inspection are all the same fact with
different paperwork — *something expires on a date, and somebody has to be told
before it does* — and the two bodies here land that fact from the two systems
that hold it. Neither derives a status: `Credential.status` and the notice
stamps beside it are the compliance sweep's, computed from the dates against
the governed stages, and a feed that stamped `expiring` on the way in would
have decided a policy question in a connector.

**`credential-sync` binds.** A work email is exact and nobody adjudicates it,
so there is no landing record worth keeping and no steward to ask — the rows
become `Credential` roots directly and hang off the `Employee` the address
resolves to. A holder the workforce does not contain is counted, never minted:
a training record is not evidence of employment, and a roster that grew a
person per unrecognized `employeeId` would fill with contractors and typos.

**`equipment-sync` reifies**, on the same terms as the purchase-order feed: an
asset tag is exact too, but the row describes a thing the business is
answerable for and the raw row is worth keeping, so it lands as a
`SourceRecord` first and the entity is applied from it. What makes this feed
interesting is one absence: **a null next-due date is the source saying this
unit carries no tracked inspection.** `inspection_tracked` is derived from it
once, on the way in, so a skid steer that never needed a sticker reads as *not
tracked* rather than as *overdue since forever* — the difference between a
fleet screen a supervisor trusts and one they learn to ignore. The two dates
on the row are the whole of what the source holds, which is why there is no
inspection resource to pull and why the examination is written as a part of
the unit rather than mirrored from anywhere.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from ..graph import Claim, GraphWriter, object_id
from ..ontology import names as onto
from ..runs import RunContext
from .sync import (
    CREDENTIAL_EVENT,
    EQUIPMENT_EVENT,
    CredentialRecord,
    EquipmentRecord,
    SyncService,
    _authority,
    _slug,
)

log = logging.getLogger("cumulus.sync.compliance")


#: The issuing system's word for a kind of credential, onto the vocabulary the
#: ontology declares. A mapping decision and therefore the flow's — the
#: precedent is `sync._event_status` and `office._importance`, which are
#: already where "this estate's word becomes our enum" is decided. The driver
#: categories are here because the *connector* has already named them from the
#: column each record came from, which is addressing rather than vocabulary;
#: they appear as identities so this table is the one place the declared five
#: are checked against what arrives.
CREDENTIAL_CATEGORIES: dict[str, str] = {
    "certification": "certification",
    "licence": "licence",
    "license": "licence",
    "medical": "medical",
    "clearance": "clearance",
    "training": "training",
    # The people system's own course taxonomy, which is nothing like ours and
    # should not be: it files a course by the department that runs it, and we
    # file a credential by what it *is*. Without these rows every equipment
    # ticket in the estate landed with no category at all and the compliance
    # window, which selects on the governed `categories tracked` list, could
    # not see one — half of what that flow exists to do, silently missing.
    #
    # `Equipment` is a certification because that is what an equipment ticket
    # is: it is awarded, it expires, and it is the thing the 60/30/15 notices
    # were designed around. The other four are training: a safety orientation
    # or a compliance refresher is attendance recorded, and where one of them
    # does carry an expiry the stages still reach it.
    "equipment": "certification",
    "safety": "training",
    "compliance": "training",
    "technical": "training",
    "professional": "training",
}

#: The source's asset type onto the declared
#: `vehicle | trailer | lift | machine | other` vocabulary. `other` is a real
#: answer rather than a fallback for anything unrecognized: a word this table
#: does not hold is counted instead, because a fleet quietly filed as `other`
#: is a fleet nobody notices is unmodelled.
EQUIPMENT_CATEGORIES: dict[str, str] = {
    "truck": "vehicle",
    "pickup": "vehicle",
    "van": "vehicle",
    "vehicle": "vehicle",
    "trailer": "trailer",
    "lift": "lift",
    "forklift": "lift",
    "telehandler": "lift",
    "boom-lift": "lift",
    "skid-steer": "machine",
    "loader": "machine",
    "machine": "machine",
    "other": "other",
}

#: The ERP's site picklist onto the department the org chart holds. **A rename
#: table, and the third of its kind** — `sync._event_status` and
#: `office._importance` are the precedent, and a rename is a rename rather
#: than a judgment: `Mazo` and `Plant — Mazomanie` are one building written
#: down twice, and nothing here decides anything about the truck parked at it.
#:
#: Without this, `operated_by` resolved to nothing for every asset the ERP
#: filed by its own site word, which is not a reconciliation anybody made —
#: it is the count standing in for one.
#:
#: **The pairing is also declared for a person** in the `## Departments` table
#: and the `plant sites:` line of the `## Source documents` section of
#: `plant-scheduling-parameters`. So two copies exist: the governed page is
#: the authority a plant edits, and this is the machine's copy, held to it by
#: a test. A connector plane reading the corpus would invert the layering the
#: module docstrings spend their length defending, which is why the copy
#: exists at all — and why it may not drift.
#:
#: A key here is matched on the *slug*, so `Fork West` and `fork-west` are one
#: entry, and a site already spelled our way passes through untouched. A site
#: this table does not hold stays a **counted aggregate** on the run: a third
#: plant should arrive as a number somebody can see, never as a silent
#: absence, and never as a per-truck question.
SITE_DEPARTMENTS: dict[str, str] = {
    "mazo": "Plant — Mazomanie",
    "fork-west": "Plant — Fork West",
}


# ---------------------------------------------------------------------------
# credential-sync
# ---------------------------------------------------------------------------


def credential_body(service: SyncService, ctx: RunContext, inputs: dict) -> dict:
    """Land the dated permissions and hang each one off its holder.

    One apply step and no resolve step, because there is nothing to resolve: a
    work email is an exact key in the system that issued it. What can fail is
    a *reference* — a holder the workforce feed has not landed — and a batch
    feed never suspends for one of those.
    """
    source = inputs["source"]
    watermark = inputs.get("watermark", "")
    records = [CredentialRecord.model_validate(r) for r in inputs["records"]]

    ctx.event(
        "t1", CREDENTIAL_EVENT,
        f"{len(records)} credential record(s) from `{source}`"
        + (f" · watermark {watermark}" if watermark else ""),
        kind="trigger",
    )

    apply = service._act(ctx, "apply-source-records", "a1")
    applied = ctx.step(
        "a1", "apply-credentials", "action",
        fn=lambda: apply.commit(lambda w: _apply_credentials(
            service, w, source, records, ctx.record.id,
        )),
        writes=lambda _out: apply.writes,
        summary=lambda out: (
            f"{out['credentials']} credential(s) · "
            f"{out['holds_credential']} bound to a holder"
            + (
                f" · {out['holder_unresolved']} whose holder the workforce "
                "does not contain"
                if out["holder_unresolved"] else ""
            )
            + (
                f" · {out['category_unmapped']} in a category this "
                "vocabulary does not hold"
                if out["category_unmapped"] else ""
            )
        ),
    )

    ctx.event(
        "e1", "CredentialsSynced",
        f"{len(records)} credential record(s) from {source}",
    )
    return {
        "source": source, "watermark": watermark, "kind": "credentials",
        "records": len(records),
        # A bind feed has no landing step to count, so it states the
        # bound-record count the way the workforce and mail bodies do.
        "landed": len(records),
        **applied,
    }


def _apply_credentials(
    service: SyncService,
    graph: GraphWriter,
    source: str,
    records: list[CredentialRecord],
    run_id: str,
) -> dict:
    """Each row as a `Credential`, hung off the `Employee` it names.

    Two fields the source could fill and this step deliberately does not.
    `status` is the issuing system's word for how an *assignment* stands, and
    `Credential.status` is `valid | expiring | expired | withdrawn` derived
    from the dates against the governed notice stages — writing one into the
    other would make the compliance sweep read its own input as its own
    output. `retain_until` is a regulator's horizon and a governed number,
    which is the reason the credential is an association rather than a part of
    the employee: it outlives the employment it was earned under.
    """
    counts = {
        "credentials": 0, "holds_credential": 0, "holder_unresolved": 0,
        "category_unmapped": 0, "unkeyed": 0,
    }
    claim_source = f"{_authority(source)}:{source}"

    for record in records:
        if not record.external_id:
            # The key is the issuing system's row id. A row without one names
            # no permission, and landing it under a bare `hris-credentials:`
            # would collide every such row into one.
            log.info("Credential row has no id",
                     extra={"source": source, "holder": record.holder_email})
            counts["unkeyed"] += 1
            continue

        claims = [
            Claim(field="external_id", value=record.external_id,
                  source=claim_source),
            Claim(field="source", value=source, source=claim_source),
        ]
        category = CREDENTIAL_CATEGORIES.get(_slug(record.category), "")
        if category:
            claims.append(
                Claim(field="category", value=category, source=claim_source)
            )
        else:
            # A word this vocabulary does not hold, counted rather than
            # rounded to the nearest declared value: a credential filed under
            # a category somebody guessed is worse than one a screen can see
            # is uncategorized.
            counts["category_unmapped"] += 1
        result = _slug(record.result)
        for field, value in (
            ("credential_type", record.credential_type),
            ("holder_email", record.holder_email),
            ("assigned_on", record.assigned_date),
            ("completed_on", record.completion_date),
            ("expires_on", record.expiration_date),
            ("provider", record.provider),
            # `pass`/`fail` where the source said so; nothing where it did
            # not. A driver file carries no result at all, and defaulting one
            # to `pending` would assert that somebody is waiting on it.
            ("result", result if result in ("pass", "fail", "pending") else ""),
        ):
            if value:
                claims.append(
                    Claim(field=field, value=value, source=claim_source)
                )

        credential = graph.upsert(
            onto.CREDENTIAL, f"{source}:{record.external_id}", claims,
            label=" — ".join(filter(None, (
                record.credential_type, record.holder_email,
            ))) or record.external_id,
            provenance=f"synced by {run_id}",
        )
        counts["credentials"] += 1

        holder = _holder(service, record)
        if holder is None:
            # An address or an id belonging to nobody the workforce feed has
            # landed. Counted, never minted: a training record is not
            # evidence of employment, and `holder_email` stays on the row so
            # the question is answerable later.
            counts["holder_unresolved"] += 1
            continue
        graph.link(
            "holds_credential", holder.id, credential.id,
            method="declared", provenance="hris employeeId, joined to email",
        )
        counts["holds_credential"] += 1
    return counts


def _holder(service: SyncService, record: CredentialRecord) -> Any:
    """The `Employee` a credential names — by address, then by the source's id.

    In that order because the address is the natural key and the id is the
    fallback the join could not translate: an employee whose row this delta
    did not carry and whose `workEmail` the dereference missed still arrives
    with `holder_external_id`, and the graph already holds them under it. The
    same two-step `_wire_org_chart` makes for a manager, and for the same
    reason — the translation is only available while the person's row is on
    the page.
    """
    email = record.holder_email.strip().lower()
    if email:
        found = service.graph.get(object_id(onto.EMPLOYEE, email))
        if found is not None:
            return found
    if not record.holder_external_id:
        return None
    known = service.graph.find(
        onto.EMPLOYEE,
        prop_equals={"external_id": record.holder_external_id},
        limit=1,
    )
    return known[0] if known else None


# ---------------------------------------------------------------------------
# equipment-sync
# ---------------------------------------------------------------------------


def equipment_body(service: SyncService, ctx: RunContext, inputs: dict) -> dict:
    """Land the fleet, then the examination each unit's dates describe.

    The landing step is not ceremony. An asset row is the only place the pair
    of dates exists, and keeping it as delivered is what lets a change to the
    notice stages re-read the fleet without re-pulling it — the same argument
    the vendor feed's landing step makes, applied to a fact that expires.
    """
    source = inputs["source"]
    watermark = inputs.get("watermark", "")
    records = [EquipmentRecord.model_validate(r) for r in inputs["records"]]

    ctx.event(
        "t1", EQUIPMENT_EVENT,
        f"{len(records)} equipment record(s) from `{source}`"
        + (f" · watermark {watermark}" if watermark else ""),
        kind="trigger",
    )

    landing = service._act(ctx, "land-source-records", "s1")
    landed = ctx.step(
        "s1", "land-records", "sync",
        fn=lambda: landing.commit(lambda w: service._land(
            source, "equipment", [r.model_dump() for r in records], w,
        )),
        writes=lambda _out: landing.writes,
        summary=lambda out: (
            f"{out['landed']} landed, {out['unchanged']} unchanged "
            f"(byte-identical redelivery) in `source-landing`"
        ),
    )

    apply = service._act(ctx, "apply-source-records", "a1")
    applied = ctx.step(
        "a1", "apply-equipment", "action",
        fn=lambda: apply.commit(lambda w: _apply_equipment(
            service, w, source, records, ctx.record.id,
        )),
        writes=lambda _out: apply.writes,
        summary=lambda out: (
            f"{out['equipment']} unit(s), {out['tracked']} with a tracked "
            f"inspection · {out['inspections']} examination(s) written"
            + (
                f" · {out['operated_by']} at a site the org chart holds"
                if out["operated_by"] else ""
            )
            + (
                f" · {out['site_unresolved']} whose home site names no team"
                if out["site_unresolved"] else ""
            )
        ),
    )

    ctx.event(
        "e1", "EquipmentSynced",
        f"{len(records)} equipment record(s) from {source}",
    )
    return {
        "source": source, "watermark": watermark, "kind": "equipment",
        "records": len(records), **landed, **applied,
    }


def _apply_equipment(
    service: SyncService,
    graph: GraphWriter,
    source: str,
    records: list[EquipmentRecord],
    run_id: str,
) -> dict:
    """Each unit, the site that runs it, and the examination it has had."""
    counts = {
        "equipment": 0, "tracked": 0, "inspections": 0, "operated_by": 0,
        "site_unresolved": 0, "category_unmapped": 0, "due_without_history": 0,
        "unkeyed": 0,
    }
    claim_source = f"{_authority(source)}:{source}"

    for record in records:
        key = record.equipment_id or record.external_id
        if not key:
            log.info("Asset row has no tag and no id",
                     extra={"source": source, "asset": record.name})
            counts["unkeyed"] += 1
            continue

        # **The derivation this feed exists for, done once and written down.**
        # Whether the source holds a next-due date at all is the only thing
        # that says whether this unit is on an inspection cycle, and the
        # answer is a fact about the asset rather than a gap in the feed. A
        # sweep that re-derived it per pass would have to keep asking the
        # question, and a screen would have to explain the null.
        tracked = "yes" if record.next_inspection_due_date else "no"
        claims = [
            Claim(field="equipment_id", value=record.equipment_id,
                  source=claim_source),
            Claim(field="external_id", value=record.external_id,
                  source=claim_source),
            Claim(field="source", value=source, source=claim_source),
            Claim(field="inspection_tracked", value=tracked,
                  source=claim_source),
        ]
        category = EQUIPMENT_CATEGORIES.get(_slug(record.category), "")
        if category:
            claims.append(
                Claim(field="category", value=category, source=claim_source)
            )
        else:
            counts["category_unmapped"] += 1
        for field, value in (
            ("name", record.name),
            ("make", record.make),
            ("model", record.model),
            ("identifier", record.identifier),
            ("home_site", record.home_site),
        ):
            if value:
                claims.append(
                    Claim(field=field, value=value, source=claim_source)
                )
        if record.year:
            claims.append(
                Claim(field="year", value=record.year, source=claim_source)
            )

        unit = graph.upsert(
            onto.EQUIPMENT, f"{source}:{key}", claims,
            label=" ".join(filter(None, (record.equipment_id, record.name)))
                  or key,
            provenance=f"synced by {run_id}",
        )
        counts["equipment"] += 1
        if tracked == "yes":
            counts["tracked"] += 1

        team = _site_team(service, record.home_site)
        if team is not None:
            graph.link(
                "operated_by", unit.id, team.id,
                method="declared", provenance="erp homeSite",
            )
            counts["operated_by"] += 1
        elif record.home_site:
            # A site string that `SITE_DEPARTMENTS` does not rename and the
            # org chart does not hold. An aggregate rather than a Task: the
            # rename table above is the governed decision somebody made once,
            # and what is left over is a *third* site nobody has paired yet —
            # a number on the run, so it arrives as something visible instead
            # of as an absence, and still never as a question per truck.
            counts["site_unresolved"] += 1

        if tracked == "no":
            # Nothing to record. The unit has no cycle, so an examination
            # written from a last-inspection date would put a row on a
            # compliance screen for a machine nobody inspects.
            continue
        if not record.last_inspection_date:
            # Due, and never examined — a unit delivered new, or one whose
            # history predates the system. Counted, because writing a part
            # with no `inspected_on` would be an examination that never
            # happened, and the part's key is that date.
            counts["due_without_history"] += 1
            continue
        inspection = graph.upsert(
            onto.INSPECTION,
            _inspection_key(unit.id, record.last_inspection_date),
            [
                Claim(field="inspected_on", value=record.last_inspection_date,
                      source=claim_source),
                Claim(field="next_due_on",
                      value=record.next_inspection_due_date,
                      source=claim_source),
                # No `result`, no `inspector`, no `notes`: the source holds
                # two dates and nothing about what was found. A `pass`
                # inferred from the existence of a next due date would be this
                # feed inventing the finding a certificate carries.
                Claim(field="external_id", value=record.external_id,
                      source=claim_source),
            ],
            label=f"{record.equipment_id or key} · "
                  f"{record.last_inspection_date}",
            provenance=f"derived from the asset's dates by {run_id}",
        )
        graph.link(
            "has_inspection", unit.id, inspection.id,
            props={"inspected_on": record.last_inspection_date},
            provenance=run_id,
        )
        counts["inspections"] += 1
    return counts


def _inspection_key(equipment_id: str, inspected_on: str) -> str:
    """A part's key embeds its owner's — the weak-entity rule, enforced by
    `GraphService.check_invariants`. The date is the discriminator because a
    unit is examined once per cycle, and two examinations on one day are one
    examination somebody recorded twice."""
    return f"{equipment_id}#{inspected_on}"


#: The team-slug derivation, repeated rather than imported. `estate.team_slug`
#: is where it is canonically written and where the HRIS feed applies it on the
#: way in — but `estate` is the module that reaches the network and this plane
#: is one of the things it pushes to, so a connector importing the puller would
#: invert the whole layering for the sake of one regex. Repeated, and pinned by
#: a test that asserts the two agree, which is the only honest way to keep two
#: copies of a key derivation from drifting.
_SITE_SLUG = re.compile(r"[^a-z0-9]+")


def _site_slug(name: str) -> str:
    return _SITE_SLUG.sub("-", (name or "").lower()).strip("-")


def _site_team(service: SyncService, home_site: str) -> Any:
    """The `Team` a home-site string names, by the slug the HRIS feed keyed on.

    An exact lookup and deliberately not a match: the Mapping declares
    `home_site → Team` by team slug, and a site string the org chart does not
    hold is counted on the run. Reaching for the nearest department by name
    would put a truck under whichever site happened to sort first.

    The two vocabularies really do differ — an ERP asset says `Mazo` where the
    HRIS department is `Plant — Mazomanie` — so the site word goes through
    `SITE_DEPARTMENTS` first, which is the rename the module constant above
    documents. A word the table does not hold is looked up as it stands, so a
    site already spelled the org chart's way resolves without an entry, and
    one nothing recognizes is counted on the run rather than guessed at.
    """
    slug = _site_slug(home_site)
    if not slug:
        return None
    renamed = SITE_DEPARTMENTS.get(slug)
    if renamed:
        slug = _site_slug(renamed)
    return service.graph.get(object_id(onto.TEAM, slug))
