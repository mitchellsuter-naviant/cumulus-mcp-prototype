"""The invented world the tests stand in.

Every one of these fixtures used to be a **startup seed** in `app/` — an org
chart in `workforce.py`, a three-week schedule in `calendars.py`, a vendor
master and seven invoices in `payables.py`, twelve expense runs in
`reimbursements.py`, a task backlog in `tasks.py`. A running server built all
of it on first boot, which was how the prototype got a populated console
before it had anything to populate it from.

It has something now. The estate connector pulls nine feeds off a live
simulation of a customer environment, so a server that invents a vendor master
is a server telling the operator something untrue about their own data. The
seeds are gone from `app/`; a cold server with no estate configured starts
empty and says so on every screen.

The *data* survives here, because the tests below it were never about the
seeds — they are about spend rollups over a populated graph, a run history the
workspace can project, a queue with enough rows to order, a hub that resolves
"me". Fabricated records are legitimate in a test fixture and nowhere else,
which is the whole of why this module is under `tests/`.

Two things it is deliberately **not**:

- Not the eval worlds. Those are declared in `app/eval_specs.py` and graded, so
  they live in the map with the specs that read them. The AP deliveries here
  are imported from there rather than copied, so a graded world and a tested
  one cannot drift apart.
- Not a miniature of the estate. The people are real Naviant employees at their
  real work addresses — so a persona test and a live session resolve the same
  human — but the org chart is compressed to ten people over five departments,
  and a manager edge or two is straightened to keep the traversals readable.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from pydantic import BaseModel

from app.eval_specs import ap_demo_deliveries
from app.graph import Claim, GraphService, object_id
from app.models import utcnow
from app.payables import PayableService, StubInvoiceReader
from app.reimbursements import (
    ReimbursementService,
    StubReceiptReader,
    SubmittedDocument,
)
from app.runs import RunEngine
from app.sync import SyncRequest
from app.tasks import Task, TaskEvidence, TaskProposal, TaskService

HRIS_SOURCE = "hris-workforce"
CALENDAR_SOURCE = "office-calendar"
PEOPLE_AUTHORITY = "human:people-ops"
PROVIDER = "microsoft-365"
DEFAULT_TZ = "America/Chicago"

# The people, by the desk they sit at rather than by name: a test that says
# CONTROLLER is stating which authority it is exercising, where one that says
# "raymond" is stating only who happens to hold it.
CEO = "diane.vogel@naviant.com"
VP_SALES = "marcus.reyes@naviant.com"
CONTROLLER = "raymond.fitzgerald@naviant.com"
HR = "karen.boothe@naviant.com"
IT = "nathan.pryce@naviant.com"
ACCOUNTANT = "howard.lachman@naviant.com"
AP = "connie.alberts@naviant.com"
SALES_MANAGER = "dana.prentiss@naviant.com"
AE = "john.smith@naviant.com"
AE_REMOTE = "felicia.marks@naviant.com"

#: An outside attendee on a customer meeting. Counted by the sync, never
#: minted — which is the invariant a fixture with no external guest cannot test.
CUSTOMER_SPONSOR = "aaron.feldman@mercervance.example"


# ---------------------------------------------------------------------------
# The org chart
# ---------------------------------------------------------------------------

TEAMS: tuple[dict, ...] = (
    {
        "external_id": "DEPT-1001", "slug": "executive", "name": "Executive",
        "mission": "Sets the company's strategy and owns the P&L.",
        "lead_email": CEO,
    },
    {
        "external_id": "DEPT-1002", "slug": "sales", "name": "Sales",
        "mission": "Wins new business and grows the installed base.",
        "parent_slug": "executive",
        "lead_email": VP_SALES,
    },
    {
        "external_id": "DEPT-1006", "slug": "finance-administration",
        "name": "Finance & Administration",
        "mission": "Billing, payables, payroll and the company's own systems.",
        "parent_slug": "executive",
        "lead_email": CONTROLLER,
    },
    {
        "external_id": "DEPT-1007", "slug": "human-resources",
        "name": "Human Resources",
        "mission": "Hiring, benefits, and the policies people are held to.",
        "parent_slug": "executive",
        "lead_email": HR,
    },
    {
        # Under Finance in the live estate too: the IT owner reports to the
        # Controller, which is why the admin persona and the finance persona
        # can disagree about the map and still both be right.
        "external_id": "DEPT-1010", "slug": "information-technology",
        "name": "Information Technology",
        "mission": "The systems every other department runs on.",
        "parent_slug": "finance-administration",
        "lead_email": IT,
    },
)

EMPLOYEES: tuple[dict, ...] = (
    {
        "external_id": "EMP-1001", "email": CEO, "name": "Diane Vogel",
        "title": "Chief Executive Officer", "location": "Madison, WI",
        "start_date": "2009-03-02", "team_slug": "executive",
    },
    {
        "external_id": "EMP-1002", "email": VP_SALES, "name": "Marcus Reyes",
        "title": "VP of Sales", "location": "Madison, WI",
        "start_date": "2014-06-16", "manager_email": CEO,
        "team_slug": "sales",
    },
    {
        "external_id": "EMP-1005", "email": CONTROLLER,
        "name": "Raymond Fitzgerald", "title": "Controller",
        "location": "Madison, WI", "start_date": "2012-01-09",
        "manager_email": CEO, "team_slug": "finance-administration",
    },
    {
        "external_id": "EMP-1006", "email": HR, "name": "Karen Boothe",
        "title": "HR Generalist", "location": "Madison, WI",
        "start_date": "2016-08-15", "manager_email": CEO,
        "team_slug": "human-resources",
    },
    {
        "external_id": "EMP-1035", "email": IT, "name": "Nathan Pryce",
        "title": "IT Manager", "location": "Madison, WI",
        "start_date": "2018-02-05", "manager_email": CONTROLLER,
        "team_slug": "information-technology",
    },
    {
        "external_id": "EMP-1030", "email": ACCOUNTANT, "name": "Howard Lachman",
        "title": "Senior Accountant", "location": "Madison, WI",
        "start_date": "2017-04-24", "manager_email": CONTROLLER,
        "team_slug": "finance-administration",
    },
    {
        "external_id": "EMP-1031", "email": AP, "name": "Connie Alberts",
        "title": "Accounts Payable Specialist", "location": "Madison, WI",
        "start_date": "2019-09-30", "manager_email": ACCOUNTANT,
        "team_slug": "finance-administration",
    },
    {
        "external_id": "EMP-1018", "email": SALES_MANAGER,
        "name": "Dana Prentiss", "title": "Sales Manager",
        "location": "Madison, WI", "start_date": "2016-11-07",
        "manager_email": VP_SALES, "team_slug": "sales",
    },
    {
        "external_id": "EMP-1007", "email": AE, "name": "John Smith",
        "title": "Account Executive", "location": "Madison, WI",
        "start_date": "2015-05-11", "manager_email": VP_SALES,
        "team_slug": "sales",
    },
    {
        "external_id": "EMP-1021", "email": AE_REMOTE, "name": "Felicia Marks",
        "title": "Account Executive", "location": "Remote",
        "start_date": "2021-07-19", "manager_email": SALES_MANAGER,
        "team_slug": "sales",
    },
)

# A skill is asserted by people, never observed by a system — no feed carries
# one, which is exactly why the catalogue is written with `human:people-ops`
# authority rather than delivered.
CAPABILITIES: tuple[dict, ...] = (
    {
        "slug": "revenue-recognition", "name": "Revenue recognition",
        "category": "finance",
        "description": "Applies the revenue policy to real contracts, "
                       "including the awkward ones.",
    },
    {
        "slug": "accounts-payable", "name": "Accounts payable",
        "category": "finance",
        "description": "Three-way matching, vendor onboarding and the "
                       "judgment calls a match cannot make.",
    },
    {
        "slug": "erp-administration", "name": "ERP administration",
        "category": "systems",
        "description": "Owns the finance system: configuration, integrations "
                       "and who may change what.",
    },
    {
        "slug": "solution-selling", "name": "Solution selling",
        "category": "sales",
        "description": "Qualifies a problem before proposing a product, and "
                       "walks away from the ones that do not fit.",
    },
    {
        "slug": "people-management", "name": "People management",
        "category": "leadership",
        "description": "Hiring, coaching, and the difficult conversation "
                       "before it becomes a surprise.",
    },
)

# (employee email, capability slug, proficiency, how it was evidenced)
ASSIGNMENTS: tuple[tuple[str, str, str, str], ...] = (
    (CONTROLLER, "revenue-recognition", "expert", "owns the revenue policy"),
    (CONTROLLER, "accounts-payable", "expert", "signs every exception"),
    (CONTROLLER, "people-management", "practising", "four direct reports"),
    (ACCOUNTANT, "revenue-recognition", "practising", "quarterly close owner"),
    (AP, "accounts-payable", "expert", "works the queue daily"),
    (IT, "erp-administration", "expert", "owns the finance system"),
    (VP_SALES, "solution-selling", "expert", "eleven years carrying a number"),
    (VP_SALES, "people-management", "expert", "built the current team"),
    (SALES_MANAGER, "people-management", "practising", "two AEs"),
    (AE, "solution-selling", "expert", "top of the board three years running"),
    (AE_REMOTE, "solution-selling", "practising", "ramped 2021"),
    (CEO, "people-management", "expert", "the whole company reports up"),
)


def seed_workforce(sync, graph: GraphService) -> int:
    """Deliver the org chart through the real `workforce-sync` Flow, then
    assert the skills. Idempotent on the presence of any Team."""
    if graph.find("Team", limit=1):
        return 0

    sync.deliver(
        SyncRequest.model_validate({
            "source": HRIS_SOURCE,
            "watermark": "2026-08-20T00:00:00Z",
            "idempotency_key": "workforce-fixture",
            "teams": list(TEAMS),
            "employees": list(EMPLOYEES),
        }),
        actor="system:fixture",
    )

    for capability in CAPABILITIES:
        graph.upsert(
            "Capability", capability["slug"],
            [
                Claim(field=field, value=capability[field],
                      source=PEOPLE_AUTHORITY)
                for field in ("slug", "name", "description", "category")
            ],
            label=capability["name"],
            provenance="capability catalogue, asserted by people-ops",
        )
    for email, slug, proficiency, evidence in ASSIGNMENTS:
        person = graph.get(object_id("Employee", email))
        capability = graph.get(object_id("Capability", slug))
        if person is None or capability is None:
            continue
        # Proficiency and evidence belong on the link: they are facts about
        # the holding, not about the person or the skill.
        graph.link(
            "has_capability", person.id, capability.id,
            props={"proficiency": proficiency, "evidenced_by": evidence},
            method="human",
            provenance="asserted by people-ops",
        )
    return len(TEAMS) + len(EMPLOYEES) + len(CAPABILITIES)


# ---------------------------------------------------------------------------
# The week
# ---------------------------------------------------------------------------


class Block(BaseModel):
    """One block in the weekly template.

    `attendees` is what makes a block a meeting rather than a personal hold: a
    copy lands on every attendee's calendar, exactly as a provider delivers it.
    """

    slug: str
    title: str
    kind: str  # meeting | focus | task-block | deadline | out-of-office
    day: int  # 0 = Monday … 6 = Sunday
    start: str  # "HH:MM" wall clock
    minutes: int
    organizer: str
    attendees: tuple[str, ...] = ()
    location: str = ""
    agenda_ref: str = ""
    all_day: bool = False
    #: Which weeks it appears in, relative to the current one.
    weeks: tuple[int, ...] = (-1, 0, 1)

    @property
    def invitees(self) -> tuple[str, ...]:
        return self.attendees or (self.organizer,)


BLOCKS: tuple[Block, ...] = (
    Block(
        slug="pipeline-review", title="Pipeline review",
        kind="meeting", day=0, start="09:00", minutes=60,
        organizer=VP_SALES,
        attendees=(VP_SALES, SALES_MANAGER, AE, AE_REMOTE),
        location="Madison · Boardroom", agenda_ref="queue:expense-review",
    ),
    Block(
        slug="ap-run", title="Weekly AP run",
        kind="task-block", day=2, start="13:00", minutes=120,
        organizer=AP, agenda_ref="knowledge:invoice-posting-policy",
    ),
    Block(
        slug="close-checkpoint", title="Month-end close checkpoint",
        kind="meeting", day=3, start="10:30", minutes=45,
        organizer=CONTROLLER, attendees=(CONTROLLER, ACCOUNTANT, AP, IT),
        agenda_ref="knowledge:expense-approval-policy",
    ),
    Block(
        slug="company-all-hands", title="Company all-hands",
        kind="meeting", day=4, start="15:00", minutes=45,
        organizer=CEO,
        attendees=tuple(person["email"] for person in EMPLOYEES),
        location="Madison · Commons",
    ),
    Block(
        slug="mercer-vance-qbr", title="Mercer & Vance QBR",
        kind="meeting", day=4, start="11:00", minutes=90,
        organizer=AE,
        # The outside sponsor: an attendee who is not our employee. The sync
        # counts them and mints nothing, which is the point of including one.
        attendees=(AE, SALES_MANAGER, CUSTOMER_SPONSOR),
        location="Mercer & Vance · Chicago", weeks=(0,),
    ),
    Block(
        slug="ceo-focus", title="Strategy block",
        kind="focus", day=1, start="08:00", minutes=120, organizer=CEO,
    ),
    Block(
        slug="benefits-close", title="Benefits enrollment closes",
        kind="deadline", day=6, start="00:00", minutes=0, organizer=HR,
        attendees=tuple(person["email"] for person in EMPLOYEES),
        all_day=True, agenda_ref="knowledge:benefits-overview", weeks=(0,),
    ),
)


def _week_start(today: date | None = None) -> date:
    day = today or datetime.now().date()
    return day - timedelta(days=day.weekday())


def build_calendar_delivery(
    today: date | None = None, only: set[str] | None = None
) -> dict:
    """The payload a provider would push: three weeks around now.

    A plain dict, so the fixture and the tests both hand it to
    `SyncRequest.model_validate` — the same door an external caller uses.
    """
    monday = _week_start(today)
    people = [
        person for person in EMPLOYEES
        if only is None or person["email"] in only
    ]
    calendars = [
        {
            "external_id": f"AAMk-{person['external_id']}",
            "address": person["email"],
            "name": f"{person['name']} — calendar",
            "provider": PROVIDER,
            "timezone": DEFAULT_TZ,
            "owner_email": person["email"],
        }
        for person in people
    ]
    known = {person["email"] for person in people}

    events: list[dict] = []
    for block in BLOCKS:
        for week in block.weeks:
            day = monday + timedelta(days=block.day + 7 * week)
            hour, minute = (int(part) for part in block.start.split(":"))
            starts = datetime(day.year, day.month, day.day, hour, minute)
            ends = starts + timedelta(minutes=block.minutes)
            for attendee in block.invitees:
                # An outside guest gets no calendar here: we mirror our own
                # people's schedules, not the customer's.
                if attendee not in known:
                    continue
                events.append({
                    "external_id": f"{block.slug}-w{week}",
                    "calendar_address": attendee,
                    "title": block.title,
                    "kind": block.kind,
                    "starts_at": starts.isoformat(timespec="seconds"),
                    "ends_at": ends.isoformat(timespec="seconds"),
                    "all_day": block.all_day,
                    "location": block.location,
                    "status": "confirmed",
                    "organizer_email": block.organizer,
                    "attendee_emails": list(block.invitees),
                    "agenda_ref": block.agenda_ref,
                })
    return {
        "source": CALENDAR_SOURCE,
        "watermark": f"{monday.isoformat()}T00:00:00Z",
        "idempotency_key": f"calendar-fixture-{monday.isoformat()}",
        "calendars": calendars,
        "calendar_events": events,
    }


def seed_calendars(sync, graph: GraphService) -> int:
    """Deliver the week through the real `calendar-sync` Flow.

    Depends on the workforce fixture having run: a calendar with no owner in
    the graph is a mirror of nobody.
    """
    if graph.find("Calendar", limit=1):
        return 0
    if not graph.find("Employee", limit=1):
        return 0
    delivery = build_calendar_delivery()
    sync.deliver(SyncRequest.model_validate(delivery), actor="system:fixture")
    return len(delivery["calendars"])


# ---------------------------------------------------------------------------
# Accounts payable — vendors, orders, receipts, then invoices
# ---------------------------------------------------------------------------
#
# Nothing here writes a row by hand. The vendor master is populated by real
# `vendor-sync` deliveries; the orders and receipts arrive through their own
# sync Flows; and every invoice is an actual `accounts-payable` run whose Tasks
# are decided through the same path a clerk would use. The payoff is that the
# history and the live behaviour cannot diverge: a bug in the matcher, the
# resolver or the flywheel fails a test rather than lurking.


class SeedInvoice(BaseModel):
    run_id: str
    submitted_by: str
    days_ago: int
    filename: str
    hint: str
    # Decisions applied in order to whichever gate is open at the time. Each is
    # {verdict, decided_by, note, payload}.
    decisions: list[dict] = []
    # Rebuild the vendor profiles before this invoice runs, so the anomaly
    # screen has history to compare against.
    refresh_profiles: bool = False


def seed_invoices() -> list[SeedInvoice]:
    return [
        # 1. Known vendor, bills its PO exactly, posts with no human.
        SeedInvoice(
            run_id="run_ap_seed01", submitted_by=AP,
            days_ago=13, filename="acme-inv-20881.pdf",
            hint="vendor=acme-industrial po=PO-7741 invoice=ACME-INV-20881 "
                 "lines=STL-0250:2,FAS-M12:3",
        ),
        # 2. Price variance: the clerk corrects the unit price, the match
        #    re-runs, and the invoice posts on the second round.
        SeedInvoice(
            run_id="run_ap_seed02", submitted_by=AP,
            days_ago=11, filename="northwind-inv-4471.pdf",
            hint="vendor=northwind-traders po=PO-7802 invoice=NW-INV-4471 "
                 "lines=WLD-6013:4,PPE-GLV:2 pricebump",
            decisions=[{
                "verdict": "corrected", "decided_by": CONTROLLER,
                "note": "Vendor billed the old list price; corrected to the PO price.",
                "payload": {"lines": [{"ordinal": 1, "unit_price_usd": 92.40}]},
            }],
        ),
        # 3. The invoice bills more than was received, so the match holds it
        #    at the posting gate — left open, so the AP queue has a live
        #    exception with a real match set behind it.
        SeedInvoice(
            run_id="run_ap_seed03", submitted_by=AP,
            days_ago=9, filename="baystate-inv-88120.pdf",
            hint="vendor=bay-state-fasteners po=PO-7815 invoice=BSF-INV-88120 "
                 "lines=CNS-HYD:3,FAS-M12:2 overbill",
        ),
        # 4. An unknown vendor: nothing in the master resembles the name, so
        #    it is minted `onboarding`. No PO on the document either, so the
        #    invoice stops at the posting gate; a clerk posts it, and that
        #    decision is what graduates the vendor to `active`.
        SeedInvoice(
            run_id="run_ap_seed04", submitted_by=AP,
            days_ago=7, filename="meridian-first-invoice.pdf",
            hint="newvendor=meridian-supply nopo invoice=MER-0001 "
                 "lines=PPE-GLV:2 total=190.00",
            decisions=[{
                "verdict": "approved", "decided_by": CONTROLLER,
                "note": "First invoice from this vendor, no PO expected at this size.",
            }],
        ),
        # 5. The same vendor again — now `active`, and the resolver links to
        #    the object run 4 minted rather than minting a second one.
        SeedInvoice(
            run_id="run_ap_seed05", submitted_by=AP,
            days_ago=5, filename="meridian-second-invoice.pdf",
            hint="newvendor=meridian-supply nopo invoice=MER-0002 "
                 "lines=PPE-GLV:1 total=112.00",
            decisions=[{
                "verdict": "approved", "decided_by": CONTROLLER,
                "note": "Read matches the document; no PO at this size.",
            }],
        ),
        # 6. A large invoice from a vendor whose history says otherwise: over
        #    the auto-post limit *and* an amount outlier. Left open.
        SeedInvoice(
            run_id="run_ap_seed06", submitted_by=AP,
            days_ago=3, filename="acme-inv-21044.pdf",
            hint="vendor=acme-industrial po=PO-7741 invoice=ACME-INV-21044 "
                 "lines=STL-0375:12 total=9800.00",
            refresh_profiles=True,
        ),
        # 7. A suspected duplicate: same vendor, same total as invoice 1, a
        #    different number, inside the governed window. Rejected.
        SeedInvoice(
            run_id="run_ap_seed07", submitted_by=AP,
            days_ago=1, filename="acme-inv-21077.pdf",
            hint="vendor=acme-industrial po=PO-7741 invoice=ACME-INV-21077 "
                 "lines=STL-0250:2,FAS-M12:3 total=1425.25",
            refresh_profiles=True,
            decisions=[{
                "verdict": "rejected", "decided_by": CONTROLLER,
                "note": "Duplicate of ACME-INV-20881 — returned to the vendor.",
            }],
        ),
    ]


def seed_payables(service: PayableService, sync=None) -> int:
    """Build the AP world: vendors, orders, receipts, then invoices.

    `sync` is the SyncService. Without it the vendor master stays empty, every
    invoice resolves to a new vendor, and nothing matches — so this says so
    rather than producing a world that looks fine and demonstrates nothing.
    """
    if service.directory.list():
        return 0
    if service.engine is None:
        return 0

    configured = service.reader
    service.reader = StubInvoiceReader()
    seeded = 0
    try:
        if sync is not None:
            for delivery in ap_demo_deliveries():
                sync.deliver(
                    SyncRequest.model_validate(delivery), actor="system:fixture"
                )

        for case in seed_invoices():
            if case.refresh_profiles and service.anomaly is not None:
                service.anomaly.rebuild()
            submitted_at = utcnow() - timedelta(days=case.days_ago, minutes=11)
            service.submit(
                case.submitted_by, filename=case.filename, hint=case.hint,
                run_id=case.run_id, submitted_at_iso=submitted_at.isoformat(),
            )
            for decision in case.decisions:
                run = service.engine.get(case.run_id)
                if run is None or run.status != "suspended" or not run.open_task_id:
                    break
                decided = service.tasks.decide(
                    run.open_task_id,
                    verdict=decision["verdict"],
                    decided_by=decision["decided_by"],
                    note=decision.get("note", ""),
                    payload=decision.get("payload") or {},
                )
                service.resume_for_task(decided)
            backdate(service.engine, service.tasks, case.run_id, case.days_ago)
            seeded += 1

        if service.anomaly is not None:
            service.anomaly.rebuild()
    finally:
        service.reader = configured
    return seeded


# ---------------------------------------------------------------------------
# Expense history — driven through the real engine
# ---------------------------------------------------------------------------


class SeedCase(BaseModel):
    run_id: str
    employee: str
    days_ago: int
    documents: list[SubmittedDocument]
    commentary: str = ""
    # None leaves whatever the flow decided (approved, or pending on a Task).
    decision: dict | None = None


def seed_cases() -> list[SeedCase]:
    def doc(hint: str, filename: str = "", note: str = "") -> SubmittedDocument:
        return SubmittedDocument(
            filename=filename or f"{hint}.jpg", note=note, hint=hint
        )

    return [
        SeedCase(
            run_id="run_er_seed01", employee=AE_REMOTE, days_ago=21,
            documents=[doc("total=64.20 vendor=corner-bistro category=meals")],
            commentary="Team lunch after the Mercer & Vance phase 1 review.",
        ),
        SeedCase(
            run_id="run_er_seed02", employee=IT, days_ago=19,
            documents=[doc("total=32.50 vendor=transit-authority category=travel")],
            commentary="Rail fare to the customer site.",
        ),
        SeedCase(
            run_id="run_er_seed03", employee=AE_REMOTE, days_ago=16,
            documents=[doc("total=612.00 vendor=harbor-hotel category=lodging")],
            commentary="Conference block rate for the finance summit, three nights.",
            decision={
                "verdict": "approved", "decided_by": SALES_MANAGER,
                "note": "Block rate confirmed against the event contract.",
            },
        ),
        SeedCase(
            run_id="run_er_seed04", employee=AE, days_ago=14,
            documents=[doc("total=141.13 vendor=office-depot category=supplies")],
            commentary="Supplies for the onsite customer workshop.",
        ),
        SeedCase(
            run_id="run_er_seed05", employee=IT, days_ago=12,
            documents=[doc("total=96.40 vendor=vintners-cellar category=alcohol")],
            commentary="Client gift wine for the Mercer & Vance close.",
            decision={
                "verdict": "rejected", "decided_by": SALES_MANAGER,
                "note": "Gift wine is out of policy §4.2 — use the gifting desk.",
            },
        ),
        SeedCase(
            run_id="run_er_seed06", employee=AE, days_ago=8,
            documents=[doc("total=49.00 vendor=cloudline-software category=software")],
            commentary="Monthly seat for the design tool.",
        ),
        # The submitter's own split: one receipt, part of it personal. This is
        # the case the commentary parser exists for, and it auto-approves.
        SeedCase(
            run_id="run_er_seed07", employee=AE_REMOTE, days_ago=6,
            documents=[doc(
                "total=110.00 vendor=corner-bistro category=meals",
                filename="dinner-phase2.jpg",
                note=(
                    "Dinner with the Mercer & Vance team after the phase 2 "
                    "review. I am only requesting $75 of the $110 — I ordered "
                    "wine on my own tab."
                ),
            )],
            commentary="Client dinner, Mercer & Vance phase 2.",
        ),
        # A blurry receipt: the extraction is uncertain, a reviewer confirms the
        # amount, and the run finishes on the second round.
        SeedCase(
            run_id="run_er_seed08", employee=AE_REMOTE, days_ago=5,
            documents=[doc(
                "blurry total=58.75 vendor=airport-taxi category=travel",
                filename="taxi-crumpled.jpg",
            )],
            commentary="Airport transfer for the customer visit.",
            decision={
                "verdict": "corrected", "decided_by": SALES_MANAGER,
                "note": "Amount matches the card feed; vendor spelling fixed.",
                "payload": {"lines": [{
                    "ordinal": 1, "requested_usd": 58.75,
                    "vendor": "airport-taxi",
                    "purpose": "Airport transfer for the customer visit.",
                }]},
            },
        ),
        # A compound receipt over the limit: two lines out of one document, the
        # reviewer supplies purposes and trims one line, and the re-check passes.
        SeedCase(
            run_id="run_er_seed09", employee=AE, days_ago=4,
            documents=[doc(
                "compound total=210.00 vendor=harbor-hotel category=lodging",
                filename="hotel-folio.pdf",
                note="Room and the parking charge are on one folio.",
            )],
            commentary="Two nights at the customer onsite.",
            decision={
                "verdict": "corrected", "decided_by": SALES_MANAGER,
                "note": (
                    "Split the folio: room stands, parking trimmed to the "
                    "reimbursable rate."
                ),
                "payload": {"lines": [
                    {"ordinal": 1, "purpose": "Two nights at the customer onsite.",
                     "category": "lodging"},
                    {"ordinal": 2, "requested_usd": 60.00, "category": "travel",
                     "purpose": "Parking at the customer onsite, capped rate."},
                ]},
            },
        ),
        # A multi-document submission that needs nobody.
        SeedCase(
            run_id="run_er_seed10", employee=IT, days_ago=3,
            documents=[
                doc("total=42.00 vendor=airport-taxi category=travel"),
                doc("total=38.50 vendor=corner-bistro category=meals"),
            ],
            commentary="Customer visit — cab and lunch.",
        ),
        # Two left open on purpose, so the Task Queue has live work and the
        # workspace has suspended runs to show.
        SeedCase(
            run_id="run_er_seed11", employee=IT, days_ago=2,
            documents=[doc(
                "total=487.90 vendor=harbor-hotel category=lodging",
                filename="hotel-invoice.pdf",
            )],
        ),
        SeedCase(
            run_id="run_er_seed12", employee=AE, days_ago=0,
            documents=[doc(
                "blurry total=182.40 vendor=corner-bistro category=meals",
                filename="team-dinner.jpg",
            )],
        ),
    ]


def seed_expenses(service: ReimbursementService) -> int:
    if service.directory.list():
        return 0
    if service.engine is None:
        return 0

    # Seeding must be deterministic and must not spend model calls, so the stub
    # reader is forced here even when a real one is configured.
    configured = service.reader
    service.reader = StubReceiptReader()
    seeded = 0
    try:
        for case in seed_cases():
            submitted_at = utcnow() - timedelta(days=case.days_ago, minutes=7)
            request = service.submit(
                case.employee,
                documents=case.documents,
                commentary=case.commentary,
                run_id=case.run_id,
                submitted_at_iso=submitted_at.isoformat(),
            )
            if case.decision and request.task_id:
                decided = service.tasks.decide(
                    request.task_id,
                    verdict=case.decision["verdict"],
                    decided_by=case.decision["decided_by"],
                    note=case.decision.get("note", ""),
                    payload=case.decision.get("payload") or {},
                )
                service.resume_for_task(decided)
            backdate(service.engine, service.tasks, case.run_id, case.days_ago)
            seeded += 1
    finally:
        service.reader = configured
    return seeded


def _shift_iso(value: str, delta: timedelta) -> str:
    if not value:
        return value
    try:
        return (datetime.fromisoformat(value) + delta).isoformat()
    except ValueError:
        return value


def backdate(
    engine: RunEngine, tasks: TaskService, run_id: str, days_ago: int
) -> None:
    """Move a run (and its Tasks) into the past, so a fixture history spreads
    across a month instead of piling up at the moment the test started.

    Nothing in the engine reads these timestamps to make decisions, so shifting
    them is a display concern, not a rewrite of history.
    """
    record = engine.get(run_id)
    if record is None:
        return
    delta = timedelta(days=-days_ago, minutes=-7)
    record.started_at_iso = _shift_iso(record.started_at_iso, delta)
    record.updated_at_iso = _shift_iso(record.updated_at_iso, delta)
    record.finished_at_iso = _shift_iso(record.finished_at_iso, delta)
    for step in record.steps:
        step.at_iso = _shift_iso(step.at_iso, delta)
        if step.task_id:
            task = tasks.get(step.task_id)
            if task is not None:
                task.created_at_iso = _shift_iso(task.created_at_iso, delta)
                if task.decision is not None:
                    task.decision.decided_at_iso = _shift_iso(
                        task.decision.decided_at_iso, delta
                    )
                tasks.directory.put(task)
    engine.store.put(record)


# ---------------------------------------------------------------------------
# The task backlog
# ---------------------------------------------------------------------------


def _hours_ago(hours: float) -> str:
    return (utcnow() - timedelta(hours=hours)).isoformat()


def seed_task_backlog() -> list[Task]:
    """A demo backlog across the three streams, tied to the seeded world.

    Ids are stable slugs (not random) so the seed is idempotent per backend
    and a demo script can name them.

    The elevated-reimbursement tasks that used to be written by hand here are
    gone: the seeded expense runs suspend for real, so the engine raises those
    Tasks itself and deciding one genuinely resumes its run. What is left is
    the work no live flow produces yet.
    """
    return [
        # ---- HITL proper: an exception, plus curation and resolution ----
        Task(
            id="task_payout_3307",
            kind="exception",
            title="Payout export rejected an approved reimbursement",
            summary=(
                "Transient retries exhausted pushing run_er_seed03 to the "
                "payroll export: duplicate employee cost-center row. Nothing "
                "is rolled back; the approval stands and the export needs a "
                "human call."
            ),
            queue="expense-review",
            priority="high",
            flow_id="expense-reimbursement",
            case_ref="run_er_seed03",
            refs=["knowledge:expense-approval-policy"],
            evidence=[
                TaskEvidence(label="Succeeded", value="reimbursements table, ledger"),
                TaskEvidence(
                    label="Rejected", value="payroll export: duplicate cost center"
                ),
                TaskEvidence(label="Attempts", value="4 over 18 minutes"),
            ],
            proposal=TaskProposal(
                action="repost-with-cost-center",
                target="payroll-export",
                summary="Re-export with the corrected cost-center row",
            ),
            resumes="export retries with the corrected row",
            created_at_iso=_hours_ago(1),
        ),
        # ---- Entity resolution: merchant identity from receipt extraction ----
        Task(
            id="task_mdm_0071",
            kind="match_review",
            title="Low-confidence merchant match: \u201cCorner Bistro #114\u201d",
            summary=(
                "Receipt extraction resolved the printed merchant name to the "
                "canonical vendor at 0.71, below the 0.85 auto-accept "
                "threshold. Confirming creates the identity Link with "
                "reversal info retained; rejecting creates a new canonical "
                "merchant."
            ),
            queue="vendor-stewardship",
            priority="normal",
            flow_id="expense-reimbursement",
            case_ref="run_er_seed01",
            refs=["vendor:corner-bistro@rev3"],
            evidence=[
                TaskEvidence(
                    label="Printed on receipt", value="Corner Bistro #114 \u00b7 5th St"
                ),
                TaskEvidence(
                    label="Candidate", value="corner-bistro \u00b7 Corner Bistro LLC"
                ),
                TaskEvidence(label="Method", value="fuzzy_name \u00b7 confidence 0.71"),
            ],
            proposal=TaskProposal(
                action="confirm-identity-link",
                target="graph",
                summary="Link merchant \u2192 vendor:corner-bistro (reversible)",
            ),
            resumes="future receipts from this merchant resolve without a Task",
            created_at_iso=_hours_ago(26),
        ),
        # ---- Governed knowledge: the flywheel's human half ----
        Task(
            id="task_cur_0210",
            kind="curation",
            title="Promote a repeated reviewer correction into the receipt prompt",
            summary=(
                "Five reviewers on the expense-review queue made the same "
                "correction on thermal-paper receipts: the tip line had been "
                "read as the total. The proposal amends the extraction prompt "
                "so the flow stops making it. The prompt is an asset in the "
                "corpus, so this amends knowledge and not code \u2014 which is "
                "exactly why promoting it is a curation decision and not an "
                "automatic side effect of five people agreeing."
            ),
            queue="knowledge-curation",
            priority="normal",
            flow_id="expense-reimbursement",
            refs=["knowledge:receipt-extraction-prompt@rev1"],
            evidence=[
                TaskEvidence(
                    label="Pattern", value="tip line mis-read as total, 5\u00d7"
                ),
                TaskEvidence(label="Corrections", value="5 consistent, 3 deciders"),
                TaskEvidence(label="Blast radius", value="~90 receipts/month"),
            ],
            proposal=TaskProposal(
                action="amend-prompt",
                target="knowledge-layer",
                summary="Prompt rev1 \u2192 rev2 with the tip-line instruction",
            ),
            resumes=(
                "prompt revision published; every run after it records the new "
                "rev in its ModelCall read-set"
            ),
            created_at_iso=_hours_ago(50),
        ),
        Task(
            id="task_cur_0214",
            kind="curation",
            title="Two published policies disagree on the auto-approval limit",
            summary=(
                "expense-approval-policy says $200.00; the travel booking "
                "guideline still implies $150 for travel meals. Both are "
                "published, so retrieval cannot break the tie."
            ),
            queue="knowledge-curation",
            priority="high",
            refs=[
                "knowledge:expense-approval-policy",
                "knowledge:travel-booking-guideline",
            ],
            evidence=[
                TaskEvidence(label="Conflict", value="$200.00 vs $150.00"),
                TaskEvidence(
                    label="Signal", value="cited together in 14 answers (30d)"
                ),
            ],
            proposal=TaskProposal(
                action="deprecate-or-amend",
                target="knowledge-layer",
                summary="Supersede the guideline's meal-limit clause",
            ),
            resumes="losing clause marked superseded; ledger records why",
            created_at_iso=_hours_ago(76),
        ),
        # ---- Eval: governed knowledge moved under a declared case ----
        Task(
            id="task_eval_0032",
            kind="eval_review",
            title="A graded case read knowledge its fixture never declared",
            summary=(
                "`expense/felicia-on-the-roster` is declared against the expense "
                "approval policy alone, but the graded run also read the "
                "receipt extraction prompt \u2014 governed knowledge the flow "
                "reads at run time. A fixture's read-set is a claim about what "
                "a case was written against, so a run that read more than the "
                "claim is the signal that knowledge moved under the case rather "
                "than the flow regressing. Which of the two it is, only a "
                "person can say."
            ),
            queue="eval-review",
            priority="normal",
            flow_id="expense-reimbursement",
            case_ref="eval:expense-in-policy-auto-approves",
            refs=[
                "knowledge:expense-approval-policy@rev1",
                "knowledge:receipt-extraction-prompt@rev1",
            ],
            evidence=[
                TaskEvidence(
                    label="Declared", value="knowledge:expense-approval-policy@rev1"
                ),
                TaskEvidence(
                    label="Also read",
                    value="knowledge:receipt-extraction-prompt@rev1",
                ),
                TaskEvidence(
                    label="Consequence",
                    value="the case still passes, but against a wider read-set",
                ),
            ],
            proposal=TaskProposal(
                action="redeclare-read-set",
                target="eval-fixtures",
                summary=(
                    "Pin `expense/felicia-on-the-roster` to the revisions the run "
                    "actually read"
                ),
            ),
            resumes="fixture re-declared; later runs compare against it",
            created_at_iso=_hours_ago(96),
        ),
        # ---- HR: a PTO request over the auto-approval window ----
        Task(
            id="task_pto_seed_01",
            kind="decision",
            title="PTO request: 7 days in October",
            summary=(
                "Over the governed 3-day auto-approval window, so the "
                "request routes to HR. Balance covers it."
            ),
            queue="hr",
            priority="normal",
            case_ref="pto_seed_01",
            refs=["knowledge:pto-policy"],
            evidence=[
                TaskEvidence(
                    label="Employee", value="John Smith"
                ),
                TaskEvidence(label="Dates", value="Oct 12 \u2013 Oct 20 \u00b7 7 working days"),
                TaskEvidence(label="Balance", value="17.5 days"),
                TaskEvidence(
                    label="Auto-approval window", value="\u2264 3 days (governed)"
                ),
            ],
            proposal=TaskProposal(
                action="approve-pto",
                target="hr-system",
                summary="Approve and deduct 7 days from the balance",
            ),
            resumes="balance deducted; calendar hold confirmed",
            created_at_iso=_hours_ago(20),
        ),
    ]


def seed_tasks(service: TaskService) -> int:
    """Idempotent: only an empty store is seeded."""
    if service.directory.list():
        return 0
    tasks = seed_task_backlog()
    for task in tasks:
        service.directory.put(task)
    return len(tasks)


PTO_REQUEST = {
    "id": "pto_seed_01",
    "email": AE,
    "start_date": "2026-10-12",
    "end_date": "2026-10-20",
    "days": 7,
    "note": "Family trip",
    "state": "pending",
    "reason": "over the governed 3-day window; routed to HR",
    "task_id": "task_pto_seed_01",
}


def seed_pto(service) -> int:
    """The pending request `task_pto_seed_01` is the human half of."""
    if service.directory.get_request(PTO_REQUEST["id"]) is not None:
        return 0
    from app.employee import PtoRequest

    service.directory.put_request(PtoRequest(
        created_at_iso=(utcnow() - timedelta(hours=20)).isoformat(),
        **PTO_REQUEST,
    ))
    return 1
