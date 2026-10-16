"""The office feeds: a mirror that stops where it said it would.

Two properties carry this whole stage. The first is that mail is *evidence* —
a message is a part of the mailbox that holds it, correspondents bind by
address to people the graph already knows, and an address it cannot place is
counted rather than turned into a Person. The second is that the mirror is
capped: `MailMessageRecord` has no `body` field, the flow truncates the preview
again, and the tests below assert the negative — that a sentence which appears
only in a body never appears anywhere in the graph.

The fixtures are recorded from the live estate for the usual reason, and one
row in particular earns its place: `billing@granitefreight.example.com` signs
itself `GSF Logistics`, which is a vendor nobody spells that way. A tidied
fixture would have removed exactly the drift B5 exists to resolve.
"""

import pytest

from app.graph import object_id
from app.connectors.sync import MAIL_FLOW_ID, CALENDAR_FLOW_ID, MailMessageRecord, SyncRequest
from app.connectors.office import PREVIEW_CHARS
from tests import estate_fixtures as fx
from tests import world as test_world


@pytest.fixture()
def world():
    return test_world.fresh()


@pytest.fixture()
def client(_client, world):
    return _client


# ---------------------------------------------------------------------------
# The deliveries, built the way the connector builds them
# ---------------------------------------------------------------------------


def _deref(resource: str, record_id: str) -> dict:
    """The estate's single-record route, over the off-page rows."""
    off = fx.OFF_PAGE.get(("office", resource))
    return off if off is not None and off.get("id") == record_id else {}


def _mail_request(**overrides) -> SyncRequest:
    """The mail feed, through `estate.py`'s own build function.

    Not a hand-written SyncRequest: going through the connector is what proves
    the field renames the flow depends on — and, more to the point here, proves
    that `body` is dropped on the floor rather than merely unused.
    """
    from app.estate import _build_office_mail

    rows = {"mailboxes": fx.MAILBOXES, "messages": fx.MAIL_MESSAGES}
    rows.update(overrides)
    return _build_office_mail(rows, _deref)


def _calendar_request(**overrides) -> SyncRequest:
    from app.estate import _build_office_calendar

    rows = {"mailboxes": fx.MAILBOXES, "events": fx.OFFICE_EVENTS}
    rows.update(overrides)
    return _build_office_calendar(rows, _deref)


def _workforce_request() -> SyncRequest:
    from app.estate import _build_hris_workforce

    return _build_hris_workforce(
        {"departments": fx.DEPARTMENTS, "jobs": fx.JOBS,
         "employees": fx.EMPLOYEES},
        lambda resource, record_id: fx.OFF_PAGE.get(("hris", resource), {}),
    )


def _crm_request() -> SyncRequest:
    from app.estate import _build_crm_book

    return _build_crm_book(
        {"accounts": fx.ACCOUNTS, "contacts": fx.CONTACTS,
         "opportunities": fx.OPPORTUNITIES},
        lambda resource, record_id: {},
    )


def _deliver(world, request: SyncRequest, key: str = "") -> dict:
    outcomes = world.sync.deliver(
        request.model_copy(update={"idempotency_key": key}),
        actor="system:test",
    )
    assert len(outcomes) == 1
    return outcomes[0]


@pytest.fixture()
def peopled(world):
    """The two books a correspondent can be in, filled before any mail lands.

    Deliberately in this order and no other: mail resolves against people, it
    does not create them. A world where the HRIS and the CRM had never run is
    a world where every address is unresolved — which is a real state, and one
    of the tests below asserts it rather than avoiding it.
    """
    assert _deliver(world, _workforce_request())["status"] == "succeeded"
    assert _deliver(world, _crm_request())["status"] == "succeeded"
    return world


@pytest.fixture()
def mailed(peopled):
    outcome = _deliver(peopled, _mail_request())
    assert outcome["status"] == "succeeded", outcome
    return outcome


@pytest.fixture()
def calendared(peopled):
    outcome = _deliver(peopled, _calendar_request())
    assert outcome["status"] == "succeeded", outcome
    return outcome


def _message(world, address: str, external_id: str):
    return world.graph.get(object_id("MailMessage", f"{address}#{external_id}"))


# ---------------------------------------------------------------------------
# Mailboxes and messages land bound
# ---------------------------------------------------------------------------


def test_the_mirror_lands_bound_with_no_landing_records(mailed, world):
    """A bind contract: objects, no SourceRecords, no steward Tasks.

    A mailbox address and a provider message id are exact, so there is nothing
    to adjudicate — and the absence of a `source-landing` row for this feed is
    what that decision looks like from the outside.
    """
    assert mailed["mailboxes"] == len(fx.MAILBOXES) == 4
    assert len(world.graph.find("Mailbox")) == 4
    assert {m.natural_key for m in world.graph.find("Mailbox")} == {
        m["address"] for m in fx.MAILBOXES
    }
    landed = [
        r for r in world.graph.find("SourceRecord")
        if str(r.props.get("source") or "") == "office-mail"
    ]
    assert landed == []
    assert [t for t in world.tasks.list() if t.flow_id == MAIL_FLOW_ID] == []


def test_the_directory_lands_as_a_claim_the_hris_outranks(mailed, world):
    """A job title read off a mail directory is a hint, not a fact.

    It lands rather than being dropped — where the HRIS has no row at all it is
    the only thing that knows — but it lands under the `office` authority,
    which is what makes the precedence order able to decide.
    """
    mailbox = world.graph.get(object_id("Mailbox", "raymond.fitzgerald@naviant.com"))
    assert mailbox.props["job_title"] == "Controller"
    assert mailbox.props["department"] == "Finance & Administration"
    assert mailbox.props["provider"] == "microsoft-365"
    assert {c.source for c in mailbox.claims if c.field == "job_title"} == {
        "office:office-mail"
    }


def test_a_message_is_a_part_of_its_mailbox(mailed, world):
    """The weak-entity rule, enforced rather than merely followed."""
    from app.ontology import ONTOLOGY

    assert ONTOLOGY.owner_of("MailMessage") == "Mailbox"
    assert "MailMessage" in ONTOLOGY.parts_of("Mailbox")
    mailbox = world.graph.get(object_id("Mailbox", "john.smith@naviant.com"))
    messages = world.graph.neighbors(mailbox.id, "has_message", "out")
    assert len(messages) == 3
    for _link, message in messages:
        assert message.natural_key.startswith(mailbox.natural_key + "#")
    assert world.graph.check_invariants() == []


def test_one_thread_in_two_mailboxes_is_two_rows_that_agree(mailed, world):
    """What the provider actually stores, kept rather than normalized away.

    The same restock request sits in Gregory's Sent Items and Raymond's Inbox
    under two ids, two read states and two filing decisions. Collapsing them
    would invent a shared object no provider has; what they agree on is the
    `conversation_id`, and that is where the agreement is recorded.
    """
    sent = _message(world, "gregory.lindholm@naviant.com",
                    "AAMkAdiv48e4_TGWA7VijoLQLSA")
    received = _message(world, "raymond.fitzgerald@naviant.com",
                        "AAMkA6CKvgQBDSF6MMHPEBmcnwQ")
    assert sent is not None and received is not None
    assert sent.id != received.id
    assert sent.props["folder"] == "Sent Items"
    assert received.props["folder"] == "Inbox"
    assert sent.props["conversation_id"] == received.props["conversation_id"]


def test_a_message_whose_mailbox_never_landed_is_dropped_and_counted(
    mailed, world
):
    """A part cannot exist without its owner, and the run says how many.

    Not a defensive case: the two office feeds hold separate cursors over the
    same `mailboxes` route, so a message can genuinely arrive in a window where
    its mailbox did not move. Karen Boothe's mailbox is off-page here, so her
    copy of the welcome note has nowhere to hang.
    """
    assert mailed["orphaned_messages"] == 1
    assert mailed["messages"] == len(fx.MAIL_MESSAGES) - 1 == 8
    assert world.graph.get(object_id("Mailbox", "karen.boothe@naviant.com")) is None
    assert _message(world, "karen.boothe@naviant.com",
                    "AAMkASU6akN-lQ0a9H_6Bq2jHng") is None
    assert len(world.graph.find("MailMessage")) == 8


# ---------------------------------------------------------------------------
# Correspondents: resolved, or counted
# ---------------------------------------------------------------------------


def test_a_sender_resolves_inside_or_outside_the_company(mailed, world):
    """Two link types, because that is the first question anybody asks."""
    internal = _message(world, "raymond.fitzgerald@naviant.com",
                        "AAMkAdliML1ppRHiNgR5Cq76BVg")
    senders = world.graph.neighbors(internal.id, "sent_by_employee", "out")
    assert [e.natural_key for _l, e in senders] == [
        "raymond.fitzgerald@naviant.com"
    ]
    assert world.graph.neighbors(internal.id, "sent_by", "out") == []

    external = _message(world, "john.smith@naviant.com",
                        "AAMkAxD2Z36czThyRixDYIchVMQ")
    outside = world.graph.neighbors(external.id, "sent_by", "out")
    assert [p.natural_key for _l, p in outside] == [
        "tom.berg@northwindtraders.example.com"
    ]
    assert world.graph.neighbors(external.id, "sent_by_employee", "out") == []

    assert mailed["sent_by_employee"] == 3
    assert mailed["sent_by"] == 2


def test_an_unknown_correspondent_is_counted_and_never_minted(mailed, world):
    """The vendor drift the estate really contains, left as evidence.

    `GSF Logistics` writing from `billing@granitefreight.example.com` is the
    same freight company the ERP calls something else, and this flow does not
    get to say so: it has no steward, no threshold, and no business guessing
    inside a batch feed. So the address is counted, the name stays on the row
    as `from_name`, and B5 is the flow that proposes the identity with evidence
    a human can read.
    """
    message = _message(world, "raymond.fitzgerald@naviant.com",
                       "AAMkAqYAuv8dESS-p_OjT3T9QGQ")
    assert message.props["from_email"] == "billing@granitefreight.example.com"
    assert message.props["from_name"] == "GSF Logistics"
    assert world.graph.neighbors(message.id, "sent_by", "out") == []
    assert world.graph.neighbors(message.id, "sent_by_employee", "out") == []
    # Nothing was minted for the address, under either book.
    assert world.graph.get(
        object_id("Person", "billing@granitefreight.example.com")
    ) is None
    assert world.graph.get(
        object_id("Employee", "billing@granitefreight.example.com")
    ) is None
    assert mailed["sender_unresolved"] == 3
    # Distinct correspondents, not messages: karen and john have no Employee
    # row in this HRIS delta either, and curtis, bethany and the Lakeshore
    # order desk are addressed but unknown.
    assert mailed["unresolved_correspondents"] == 6


def test_recipients_carry_whether_they_were_on_to_or_cc(mailed, world):
    """A fact about the addressing, so it rides on the link, not either end."""
    message = _message(world, "raymond.fitzgerald@naviant.com",
                       "AAMkA6CKvgQBDSF6MMHPEBmcnwQ")
    links = world.graph.neighbors(message.id, "addressed_to_employee", "out")
    by_address = {e.natural_key: link.props.get("field") for link, e in links}
    assert by_address == {"raymond.fitzgerald@naviant.com": "to"}
    # Curtis is on Cc and is in nobody's book, so he is counted, not linked.
    assert "curtis.boyd@naviant.com" in message.props["cc_emails"]
    assert world.graph.get(object_id("Employee", "curtis.boyd@naviant.com")) is None

    outward = _message(world, "john.smith@naviant.com",
                       "AAMkA7Pf7ZuYTRKa-aISbmbAemA")
    people = world.graph.neighbors(outward.id, "addressed_to", "out")
    assert {p.natural_key: link.props.get("field") for link, p in people} == {
        "priya.raman@summithealth.example.com": "to",
        "evan.walsh@summithealth.example.com": "cc",
    }


def test_with_no_books_to_resolve_against_everything_is_counted(world):
    """The honest floor: mail into an empty graph asserts nothing at all."""
    outcome = _deliver(world, _mail_request())
    assert outcome["status"] == "succeeded"
    assert outcome["messages"] == 8
    assert outcome["sent_by"] == outcome["sent_by_employee"] == 0
    assert outcome["addressed_to"] == outcome["addressed_to_employee"] == 0
    assert world.graph.find("Person") == []
    assert world.graph.check_invariants() == []


# ---------------------------------------------------------------------------
# The custody line
# ---------------------------------------------------------------------------


def test_the_wire_shape_has_no_body_at_all(mailed):
    """The first line of the defence, asserted as the absence it is.

    A field that is never on the wire cannot be landed by mistake, logged by an
    intermediary, or quietly relied on by a flow somebody writes next year.
    """
    assert "body" not in MailMessageRecord.model_fields
    assert "body_preview" in MailMessageRecord.model_fields
    built = _mail_request()
    assert all("body" not in m.model_dump() for m in built.mail_messages)


def test_the_body_never_reaches_the_graph(mailed, world):
    """The negative, asserted over every property of every object.

    The fixture rows carry their whole `body` exactly as the estate serves it,
    so these sentences exist to be found — and each one appears only past the
    140th character, which is where the provider's own preview stops. If any
    of them turns up in the graph, custody has been breached somewhere between
    the connector and the claim.
    """
    body_only = [
        "benefits enrolment opens inside your first thirty days",
        "Curtis Boyd signs for them",
        "keep talking about next year",
        "please disregard",
    ]
    # Every object, every resolved prop, and every retained claim value — a
    # body that survived only as a superseded claim would still be a body we
    # were holding.
    haystack = "\n".join(
        "\n".join([
            obj.label,
            *(str(v) for v in obj.props.values()),
            *(str(c.value) for c in obj.claims),
        ])
        for obj in world.graph.find()
    )
    for sentence in body_only:
        # Present in the fixture, absent from the graph.
        assert any(sentence in (m["body"] or "") for m in fx.MAIL_MESSAGES)
        assert sentence not in haystack


def test_the_preview_is_capped_again_on_the_way_in(peopled):
    """Belt and braces, and the braces are the ones that matter.

    The estate's preview is already 140 characters, so nothing in the recorded
    fixture exercises this. A provider that put a whole body in a field named
    `preview` would, and the shape alone would not stop it — so the flow does.
    """
    long_body = "Confidential. " * 60
    assert len(long_body) > PREVIEW_CHARS
    request = SyncRequest(
        source="office-mail",
        mailboxes=[m for m in _mail_request().mailboxes],
        mail_messages=[MailMessageRecord(
            external_id="AAMkTEST",
            mailbox_address="john.smith@naviant.com",
            subject="A provider being generous",
            body_preview=long_body,
        )],
    )
    outcome = _deliver(peopled, request)
    assert outcome["status"] == "succeeded"
    landed = _message(peopled, "john.smith@naviant.com", "AAMkTEST")
    preview = landed.props["body_preview"]
    assert len(preview) == PREVIEW_CHARS
    assert preview.endswith("…")
    assert preview[:20] == long_body[:20]


def test_every_landed_preview_is_within_the_governed_cap(mailed, world):
    for message in world.graph.find("MailMessage"):
        assert len(str(message.props["body_preview"])) <= PREVIEW_CHARS


# ---------------------------------------------------------------------------
# The calendar half of the same suite
# ---------------------------------------------------------------------------


def test_the_calendar_feed_reuses_the_contract_that_existed(calendared, world):
    """No new flow: `calendar-sync` was written against a provider mirror and
    the estate's Office app is one. Mailboxes become calendars, events become
    parts of them, and the owner edge is made by address."""
    assert calendared["calendars"] == len(fx.MAILBOXES) == 4
    assert calendared["events"] == len(fx.OFFICE_EVENTS) == 6
    assert calendared["orphaned_events"] == 0
    calendar = world.graph.get(object_id("Calendar", "john.smith@naviant.com"))
    assert calendar.props["provider"] == "microsoft-365"
    assert calendar.props["timezone"] == "America/Chicago"
    # Three of the four addresses are employees in this HRIS delta; John Smith
    # has a mailbox and no worker row, which is drift rather than a bug.
    assert calendared["owners"] == 3
    assert world.graph.neighbors(calendar.id, "has_event", "out")
    assert world.graph.check_invariants() == []


def test_an_external_attendee_is_counted_not_minted(calendared, world):
    """The same rule as mail, on the type that had it first: an invitation is
    not evidence of employment, and Tom Berg stays a CRM contact."""
    event = world.graph.get(
        object_id("CalendarEvent",
                  "john.smith@naviant.com#AAQkACFb1CmUCQaekMF3pfRrULg")
    )
    assert world.graph.neighbors(event.id, "attended_by", "out") == []
    assert world.graph.get(
        object_id("Employee", "tom.berg@northwindtraders.example.com")
    ) is None
    assert calendared["unresolved"] > 0


def test_availability_is_not_promoted_into_an_event_kind(calendared, world):
    """A fact the source does not carry is left absent rather than invented.

    The provider's `showAs` says whether the block makes somebody busy; the
    ontology's `kind` says what sort of block it is. They are different facts,
    so the all-day trip the provider marks `Out of Office` lands under the wire
    shape's default and the availability is simply not carried — which is
    visible here, where an inference would not have been.
    """
    trip = world.graph.get(
        object_id("CalendarEvent",
                  "john.smith@naviant.com#AAQkAh6WKsEo_RE-rGXsWQmZf4g")
    )
    assert trip.props["all_day"] == "true"
    assert trip.props["status"] == "confirmed"
    assert trip.props["kind"] == "meeting"


def test_the_cancellation_flag_becomes_an_enum_in_the_flow_not_the_connector(
    peopled,
):
    """Which of a source's values belong to which declared enum is the flow's.

    The source reports a boolean `isCancelled`; the ontology declares
    `confirmed | tentative | cancelled`. The connector carries the flag as it
    found it — asserted here on the built record, so a connector that quietly
    started deciding the enum again would fail — and `sync._event_status` is
    where it becomes one.
    """
    cancelled_row = {**fx.OFFICE_EVENTS[0], "id": "EVT-CALLED-OFF",
                     "isCancelled": True}
    request = _calendar_request(events=[*fx.OFFICE_EVENTS, cancelled_row])

    built = {e.external_id: e for e in request.calendar_events}
    assert built["EVT-CALLED-OFF"].cancelled is True
    # The wire shape's default is untouched: the connector states no enum.
    assert built["EVT-CALLED-OFF"].status == "confirmed"

    assert _deliver(peopled, request)["status"] == "succeeded"
    called_off = peopled.graph.get(object_id(
        "CalendarEvent",
        f"{built['EVT-CALLED-OFF'].calendar_address}#EVT-CALLED-OFF",
    ))
    assert called_off.props["status"] == "cancelled"
    # And an event the source did not cancel is not swept along with it.
    live = peopled.graph.get(object_id(
        "CalendarEvent",
        f"{built[fx.OFFICE_EVENTS[1]['id']].calendar_address}"
        f"#{fx.OFFICE_EVENTS[1]['id']}",
    ))
    assert live.props["status"] == "confirmed"


def test_an_event_names_its_mailbox_by_an_id_the_page_may_not_hold(world):
    """The one declared-key dereference both office feeds make.

    An event carries `mailboxId`; the contract keys a calendar by address. A
    delta of events whose mailboxes did not move is exactly the case, and it
    costs one GET per mailbox for the whole pull rather than one per row.

    Driven through the connector's real `_deref`, because that is the only
    place the memo lives: a stand-in that cached for itself would prove the
    property about the test rather than about the code.
    """
    from app.estate import (
        EstateConnector,
        InMemoryConnectorStateStore,
        _build_office_calendar,
    )

    fetched: list[str] = []
    rows = {m["id"]: m for m in (*fx.MAILBOXES, fx.MAILBOX_OFF_PAGE)}

    def fetch(url: str) -> dict:
        fetched.append(url)
        return rows.get(url.rsplit("/", 1)[-1], {})

    connector = EstateConnector(
        InMemoryConnectorStateStore(), project="p", namespace="naviant",
        fetch=fetch,
    )
    built = _build_office_calendar(
        {"mailboxes": [], "events": fx.OFFICE_EVENTS},
        connector._deref("office"),
    )
    assert built.calendars == []
    assert {e.calendar_address for e in built.calendar_events} == {
        "john.smith@naviant.com", "gregory.lindholm@naviant.com",
        "marcus.reyes@naviant.com",
    }
    # Six events across three mailboxes, memoized: three GETs, not six.
    assert len(fetched) == 3
    assert all("/v1/mailboxes/" in url for url in fetched)


# ---------------------------------------------------------------------------
# Redelivery and the watermark
# ---------------------------------------------------------------------------


def test_redelivery_changes_nothing(mailed, calendared, world):
    before = world.graph.stats()
    again = _deliver(world, _mail_request())
    assert again["status"] == "succeeded"
    assert again["mailboxes"] == 4 and again["messages"] == 8
    assert again["orphaned_messages"] == 1
    assert world.graph.stats()["by_type"] == before["by_type"]
    assert world.graph.check_invariants() == []


def test_an_identical_delivery_re_enters_the_same_run(peopled):
    first = _deliver(peopled, _mail_request(), key="office-mail:2026-08-30:0")
    second = _deliver(peopled, _mail_request(), key="office-mail:2026-08-30:0")
    assert first["replayed"] is False
    assert second["replayed"] is True
    assert second["run_id"] == first["run_id"]


def test_the_two_office_feeds_hold_separate_cursors_per_resource():
    """Both pull `mailboxes`, and neither may hold the other back.

    One feed over three resources would have been fewer HTTP calls and one
    cursor — and a quiet calendar would then keep the mail behind it, because
    the watermark a pull advances is the largest `updatedAt` it *saw*.
    """
    from tests.test_estate import build

    connector = build()
    mail = connector.pull("office-mail")
    calendar = connector.pull("office-calendar")

    assert set(mail["pulled"]) == {"mailboxes", "messages"}
    assert set(calendar["pulled"]) == {"mailboxes", "events"}
    assert mail["watermarks"]["messages"] == max(
        m["updatedAt"] for m in fx.MAIL_MESSAGES
    )
    assert calendar["watermarks"]["events"] == max(
        e["updatedAt"] for e in fx.OFFICE_EVENTS
    )
    # The shared resource is at the same place in both, and stored apart.
    mailbox_mark = max(m["updatedAt"] for m in fx.MAILBOXES)
    assert mail["watermarks"]["mailboxes"] == mailbox_mark
    assert calendar["watermarks"]["mailboxes"] == mailbox_mark
    assert connector.state("office-mail").watermarks != (
        connector.state("office-calendar").watermarks
    )

    # And a re-pull asks strictly past the cursor, so it finds nothing.
    again = connector.pull("office-mail")
    assert again["outcome"] == "unchanged"
    assert again["pulled"] == {"mailboxes": 0, "messages": 0}


def test_a_pull_lands_the_mirror_through_the_http_door(client, world):
    """The whole path: fake estate, real door, real Trigger, real flow."""
    from tests.test_estate import build, door

    connector = build(deliver=door(client))
    outcome = connector.pull("office-mail")
    assert outcome["outcome"] == "delivered"
    assert len(world.graph.find("Mailbox")) == 4
    assert len(world.graph.find("MailMessage")) == 8
    assert world.graph.check_invariants() == []


# ---------------------------------------------------------------------------
# The feeds' own paperwork
# ---------------------------------------------------------------------------


def test_both_office_feeds_show_up_on_the_sources_lens(mailed, calendared, world):
    rows = {(s.source, s.kind): s for s in world.sync.sources()}
    assert rows[("office-mail", "mail")].flow_id == MAIL_FLOW_ID
    assert rows[("office-mail", "mail")].last_status == "succeeded"
    assert rows[("office-calendar", "calendars")].flow_id == CALENDAR_FLOW_ID


def test_the_mapping_declares_the_custody_line():
    """The cap is a contract term, not a magic number in a flow.

    And `calendar-m365` is gone: the seeded calendar world used to run under a
    Mapping describing a system nobody operates. It now speaks under the
    contract of the system that actually serves the estate's calendars.
    """
    from app.mappings import MAPPINGS, validate_mappings

    sources = {m.source for m in MAPPINGS}
    assert "office-mail" in sources and "office-calendar" in sources
    assert "calendar-m365" not in sources

    mail = next(m for m in MAPPINGS if m.source == "office-mail")
    assert mail.transport == "pull"
    assert mail.strategy == "bind" and mail.lands_as == ""
    assert mail.precedence_source == "office"
    assert mail.collection == "correspondence"
    assert mail.escalation == "windowed aggregate"
    assert "body_preview" in mail.notes and str(PREVIEW_CHARS) in mail.notes
    assert "never minted" in mail.notes

    calendar = next(m for m in MAPPINGS if m.source == "office-calendar")
    assert calendar.transport == "pull"
    assert "watermark" in calendar.freshness
    # Risk decision 2: every dereference the FeedSpec makes is declared here.
    assert "mailbox" in calendar.notes and "internal id" in calendar.notes
    assert validate_mappings() == []


def test_the_seeded_calendar_world_runs_under_the_declared_contract(world):
    """The mock week and the pulled one now answer to one map."""
    from app.calendars import CALENDAR_SOURCE
    from app.mappings import MAPPINGS

    assert CALENDAR_SOURCE == "office-calendar"
    assert CALENDAR_SOURCE in {m.source for m in MAPPINGS}


def test_every_write_went_through_the_gateway(mailed, calendared, world):
    entries = [
        e for e in world.gateway.ledger.list()
        if e.flow_id in (MAIL_FLOW_ID, CALENDAR_FLOW_ID)
    ]
    assert entries
    assert {e.outcome for e in entries} == {"committed"}
    assert sum(e.write_count for e in entries) > 0


def test_the_exports_draw_the_steps_the_engine_actually_ran(
    mailed, calendared, world, client
):
    """Two more of the carried-debt definitions, held to the same bar: every
    step id in the declarative export is one the run journal contains."""
    for flow_id in (MAIL_FLOW_ID, CALENDAR_FLOW_ID):
        defn = client.get(f"/api/v1/flows/{flow_id}").json()
        assert defn["has_definition"] is True
        exported = [s["id"] for s in defn["steps"]]
        runs = world.engine.list(flow_id=flow_id)
        assert runs, flow_id
        assert [s.step_id for s in runs[0].steps] == exported, flow_id
