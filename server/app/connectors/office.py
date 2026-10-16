"""The office feed that is not the calendar: mail, mirrored as evidence.

One flow body — `mail-sync` — kept out of `connectors/sync.py` for the reason
`connectors/commerce.py` is: that file is already the wire shapes, the service,
the landing step and five flows. It registers through `SyncService.__init__`,
writes through `service._act` so the Gateway records every claim, and reads
identity out of the graph rather than deciding any.

**Why mail is in the graph at all.** A commitment is usually made in mail
before any system is told about it — a PO is promised in a thread days before
the ERP has a row for it — so a graph that can only see the systems is looking
at the shadow rather than the thing. What the graph needs from mail is that a
conversation happened, who was in it, and roughly what it was about.

**Why it stops where it stops.** That need is satisfied by headers, addressing
and an opening line. Going further would make Cumulus a second copy of the
customer's inbox, held under a retention story it cannot defend and a custody
claim nobody agreed to. So `MailMessageRecord` has no `body` field at all, and
this module truncates `body_preview` again on the way in: the wire shape is the
first line of that defence and the flow is the second, because a provider that
called a whole body a "preview" would otherwise walk straight through the first.

**A bind mapping, like the calendar.** A mailbox address and a provider message
id are exact identifiers, so there is nothing for a steward to adjudicate and
no landing record worth keeping — hence no `x1`/`s1`/`z1` here. The one place
judgment could creep in is the correspondent: a `From` address that belongs to
nobody we know. It does not creep in. An unknown sender is **counted and left
on the row as `from_email`**, never minted, because a book of counterparties
that grew a Person per unrecognized address would fill up with newsletters and
no-reply robots and stop being a book of counterparties.

That leaves the vendor drift the estate actually contains — `GSF Logistics`
writing from `billing@granitefreight.example.com`, which is Granite State
Freight under a name no system spells that way — sitting where it belongs: as
`from_name` evidence on a landed message, waiting for the flow (B5) whose job
is to propose that identity with evidence a human can read. Guessing it here,
inside a batch feed with no steward and no threshold, is exactly the shortcut
the separation of landing from resolution exists to prevent.
"""

from __future__ import annotations

import logging

from ..graph import Claim, GraphWriter, object_id
from ..models import utcnow
from ..ontology import names as onto
from ..runs import RunContext
from .sync import (
    MAIL_EVENT,
    MAIL_FLOW_ID,
    MailboxRecord,
    MailMessageRecord,
    SyncService,
    _authority,
)

log = logging.getLogger("cumulus.sync.office")

#: How much of a message body may land, in characters.
#:
#: The `correspondence` Collection declares custody "headers, addressing, and an
#: opening preview of the body", and this constant is the only place that
#: sentence becomes enforceable. It is deliberately a little more generous than
#: the estate's own 140-character preview: clipping a provider that shows a
#: slightly longer snippet would lose real text for no governance gain, while a
#: provider that put four kilobytes in the field would be storing the customer's
#: mail under a preview's name. Named in the Mapping so it is a contract term
#: rather than a magic number.
PREVIEW_CHARS = 240

#: What a message's key joins to. A part's key embeds its owner's — the
#: weak-entity rule `GraphService.check_invariants` enforces — and it is true to
#: the provider besides: a message id is unique inside one mailbox's store, and
#: the same thread delivered to four people has four of them.
KEY_SEPARATOR = "#"




# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------


def _address(value: str) -> str:
    """One address as the ontology keys it: lowercased and trimmed.

    Mail addresses are case-insensitive in the part that matters and providers
    are inconsistent about which case they hand back, so a graph that keyed on
    the string as delivered would hold `John.Smith@` and `john.smith@` as two
    correspondents. Applied at every point an address is *used* as a key, not
    once at the edge, because the connector is not allowed to have opinions and
    this is one.
    """
    return (value or "").strip().lower()


def _message_key(record: MailMessageRecord) -> str:
    return f"{_address(record.mailbox_address)}{KEY_SEPARATOR}{record.external_id}"


def _preview(value: str) -> str:
    """The custody line, applied. See `PREVIEW_CHARS`."""
    text = (value or "").strip()
    if len(text) <= PREVIEW_CHARS:
        return text
    return text[: PREVIEW_CHARS - 1].rstrip() + "…"


def _importance(value: str) -> str:
    """`Normal` → `normal`. The type declares `low | normal | high`.

    A vocabulary decision, and therefore in the flow rather than the connector:
    which of a source's strings belong to which declared enum is a mapping
    question, and mapping questions are what a flow is for.
    """
    return (value or "").strip().lower()


# ---------------------------------------------------------------------------
# mail-sync
# ---------------------------------------------------------------------------


def mail_body(service: SyncService, ctx: RunContext, inputs: dict) -> dict:
    """Mailboxes, the messages in them, and who was on each one."""
    source = inputs["source"]
    watermark = inputs.get("watermark", "")
    mailboxes = [
        MailboxRecord.model_validate(m) for m in inputs.get("mailboxes", [])
    ]
    messages = [
        MailMessageRecord.model_validate(m) for m in inputs.get("messages", [])
    ]

    ctx.event(
        "t1", MAIL_EVENT,
        f"{len(mailboxes)} mailbox(es) and {len(messages)} message(s) from "
        f"`{source}`"
        + (f" · watermark {watermark}" if watermark else ""),
        kind="trigger",
    )

    mailbox_act = service._act(ctx, "apply-source-records", "a1")
    mailbox_out = ctx.step(
        "a1", "apply-mailboxes", "action",
        fn=lambda: mailbox_act.commit(
            lambda w: _apply_mailboxes(w, source, mailboxes, ctx.record.id)
        ),
        writes=lambda _out: mailbox_act.writes,
        summary=lambda out: f"{out['mailboxes']} mailbox(es) written",
    )

    message_act = service._act(ctx, "apply-source-records", "a2")
    message_out = ctx.step(
        "a2", "apply-messages", "action",
        fn=lambda: message_act.commit(lambda w: _apply_messages(
            service, w, source, messages, ctx.record.id,
        )),
        writes=lambda _out: message_act.writes,
        summary=lambda out: (
            f"{out['messages']} message(s) written, bodies capped at "
            f"{PREVIEW_CHARS} characters"
            + (
                f" · {out['orphaned_messages']} in a mailbox this graph does "
                "not hold"
                if out["orphaned_messages"] else ""
            )
        ),
    )

    correspondent_act = service._act(ctx, "apply-source-records", "a3")
    correspondent_out = ctx.step(
        "a3", "wire-correspondents", "action",
        fn=lambda: correspondent_act.commit(
            lambda w: _wire_correspondents(service, w, messages)
        ),
        writes=lambda _out: correspondent_act.writes,
        summary=lambda out: (
            f"{out['sent_by_employee']} sender(s) inside the company, "
            f"{out['sent_by']} outside · {out['addressed_to_employee']} "
            f"internal and {out['addressed_to']} external recipient link(s)"
            + (
                f" · {out['unresolved_correspondents']} address(es) belonging "
                "to nobody in the graph, counted not minted"
                if out["unresolved_correspondents"] else ""
            )
        ),
    )

    ctx.event(
        "e1", "MailSynced",
        f"{len(mailboxes)} mailbox(es), {len(messages)} message(s) from "
        f"{source}",
    )
    return {
        "source": source, "watermark": watermark, "kind": "mail",
        "records": len(mailboxes) + len(messages),
        "landed": len(mailboxes) + len(messages),
        **mailbox_out, **message_out, **correspondent_out,
    }


def _apply_mailboxes(
    graph: GraphWriter,
    source: str,
    records: list[MailboxRecord],
    run_id: str,
) -> dict:
    """The mirror's roots. Not authoritative, and the type says so.

    `job_title` and `department` are read off the mail directory and land as
    claims like any other — which is the point of writing them under the
    `office` authority rather than dropping them: where the HRIS disagrees, the
    HRIS wins by precedence and both claims stay readable, and where the HRIS
    has no row at all this is the only thing that knows.
    """
    claim_source = f"{_authority(source)}:{source}"
    written = 0
    for record in records:
        address = _address(record.address)
        if not address:
            # The natural key is the address; a row without one names no
            # mailbox, and landing it under an empty key would collide every
            # such row into one ghost.
            log.info(
                "Mailbox row has no address",
                extra={"source": source, "record": record.external_id},
            )
            continue
        graph.upsert(
            onto.MAILBOX, address,
            [
                Claim(field="address", value=address, source=claim_source),
                Claim(field="display_name", value=record.display_name,
                      source=claim_source),
                Claim(field="provider", value=record.provider,
                      source=claim_source),
                Claim(field="external_id", value=record.external_id,
                      source=claim_source),
                Claim(field="job_title", value=record.job_title,
                      source=claim_source),
                Claim(field="department", value=record.department,
                      source=claim_source),
                Claim(field="timezone", value=record.timezone,
                      source=claim_source),
                Claim(field="synced_at", value=utcnow().isoformat(),
                      source=claim_source),
            ],
            label=record.display_name or address,
            provenance=f"synced by {run_id}",
        )
        written += 1
    return {"mailboxes": written}


def _apply_messages(
    service: SyncService,
    graph: GraphWriter,
    source: str,
    records: list[MailMessageRecord],
    run_id: str,
) -> dict:
    """Each message under the mailbox that holds it, capped at a preview."""
    claim_source = f"{_authority(source)}:{source}"
    written = 0
    orphaned = 0
    for record in records:
        mailbox = service.graph.get(
            object_id(onto.MAILBOX, _address(record.mailbox_address))
        )
        if mailbox is None:
            # No mailbox, no message: a part cannot exist without its owner,
            # so the honest outcome is to drop it and say how many. This is a
            # real case rather than a defensive one — the two office feeds hold
            # separate cursors, so a message can arrive in a window where its
            # mailbox did not move and has never been pulled.
            orphaned += 1
            continue
        message = graph.upsert(
            onto.MAIL_MESSAGE, _message_key(record),
            [
                Claim(field="external_id", value=record.external_id,
                      source=claim_source),
                Claim(field="subject", value=record.subject,
                      source=claim_source),
                Claim(field="folder", value=record.folder,
                      source=claim_source),
                Claim(field="from_email", value=_address(record.from_email),
                      source=claim_source),
                Claim(field="from_name", value=record.from_name,
                      source=claim_source),
                Claim(field="to_emails",
                      value=", ".join(_address(e) for e in record.to_emails),
                      source=claim_source),
                Claim(field="cc_emails",
                      value=", ".join(_address(e) for e in record.cc_emails),
                      source=claim_source),
                # The custody line, enforced rather than trusted.
                Claim(field="body_preview", value=_preview(record.body_preview),
                      source=claim_source),
                Claim(field="importance", value=_importance(record.importance),
                      source=claim_source),
                Claim(field="conversation_id", value=record.conversation_id,
                      source=claim_source),
                Claim(field="received_at", value=record.received_at,
                      source=claim_source),
                Claim(field="sent_at", value=record.sent_at,
                      source=claim_source),
            ],
            label=record.subject or record.external_id,
            provenance=f"synced by {run_id}",
        )
        graph.link(
            "has_message", mailbox.id, message.id,
            method="declared", provenance="mailbox_address",
        )
        written += 1
    return {"messages": written, "orphaned_messages": orphaned}


def _wire_correspondents(
    service: SyncService,
    graph: GraphWriter,
    records: list[MailMessageRecord],
) -> dict:
    """Who sent it and who it went to, by address, against people we know.

    Two declarations rather than one loose edge — `sent_by` to a Person and
    `sent_by_employee` to an Employee — because "did this come from inside or
    outside the company" is the first question anybody asks of a thread, and an
    edge that could land on either type would make it a query instead of a
    read. Employees are checked first: an address that is both is ours.

    Nothing is minted here. An address the graph cannot place is counted, and
    the message keeps it as `from_email` — which is not a shrug but the thing
    that makes the row still useful: a later pass that *does* have a steward
    and a threshold can read those addresses back off the landed messages.
    """
    counts = {
        "sent_by": 0, "sent_by_employee": 0,
        "addressed_to": 0, "addressed_to_employee": 0,
        "sender_unresolved": 0, "recipient_unresolved": 0,
    }
    #: Distinct, because the interesting number is how many *correspondents*
    #: the graph cannot place, not how much mail they sent.
    unplaced: set[str] = set()

    def known(email: str) -> tuple[str, object] | None:
        """The person behind an address, and which book they are in."""
        employee = service.graph.get(object_id(onto.EMPLOYEE, email))
        if employee is not None:
            return "employee", employee
        person = service.graph.get(object_id(onto.PERSON, email))
        if person is not None:
            return "person", person
        return None

    for record in records:
        message = service.graph.get(
            object_id(onto.MAIL_MESSAGE, _message_key(record))
        )
        if message is None:
            # Orphaned in `a2`, and already counted there.
            continue

        sender = _address(record.from_email)
        if sender:
            found = known(sender)
            if found is None:
                counts["sender_unresolved"] += 1
                unplaced.add(sender)
            else:
                book, who = found
                link = "sent_by_employee" if book == "employee" else "sent_by"
                graph.link(
                    link, message.id, who.id,
                    method="declared", provenance="mail from address",
                )
                counts[link] += 1

        cc = {_address(e) for e in record.cc_emails}
        seen: set[str] = set()
        for email in [*record.to_emails, *record.cc_emails]:
            address = _address(email)
            if not address or address in seen:
                continue
            seen.add(address)
            found = known(address)
            if found is None:
                counts["recipient_unresolved"] += 1
                unplaced.add(address)
                continue
            book, who = found
            link = (
                "addressed_to_employee" if book == "employee"
                else "addressed_to"
            )
            graph.link(
                link, message.id, who.id,
                # Whether they were on To or Cc is a fact about the addressing
                # and not about either end, so it rides on the link.
                props={"field": "cc" if address in cc else "to"},
                method="declared", provenance="mail recipient list",
            )
            counts[link] += 1

    return {**counts, "unresolved_correspondents": len(unplaced)}
