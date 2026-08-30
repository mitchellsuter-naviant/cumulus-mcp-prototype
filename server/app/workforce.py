"""The workforce seed: a professional services firm's org chart and skills.

Two origins, deliberately different, because the entities are different kinds
of fact:

- **Teams and employees are synced.** They arrive as an HRIS delivery through
  the `workforce-sync` Flow, so their claims carry `hris:` authority and the
  org chart is wired from the bind columns in the payload. This is the named
  trigger that `member_of`, `reports_to`, `subteam_of` and `led_by` were
  declared ahead of.
- **Capabilities and who holds them are asserted by people.** A skill is not
  a system's observation; somebody vouches for it. So the catalogue and the
  assignments are written with `human:people-ops` authority, and the
  proficiency and the evidence live on the `has_capability` link — on the
  relationship, because that is what they are about.

The firm is small on purpose: eleven people over six nested teams is enough to
make the org chart a real traversal (a lead's reports, a team's parent, a
partner's whole line) without inventing a directory nobody reads. Ten of them
are fictional; the eleventh is whoever signs in to the console, because a
principal that resolves to nobody turns every personal surface into a demo
persona with an apology attached.
"""

from __future__ import annotations

import logging

from .graph import Claim, GraphService, object_id
from .models import utcnow

log = logging.getLogger("cumulus.workforce")

HRIS_SOURCE = "hris-workday"
PEOPLE_AUTHORITY = "human:people-ops"

# The console's operator: the person who signs in to this prototype. A real
# work address sits among the fictional `customer.example` ones on purpose —
# the signed-in principal has to *resolve to somebody* or every personal
# surface falls back to a demo persona and says so. Title, team and manager
# below are placeholders for the demo's sake; change them freely.
OPERATOR_EMAIL = "masuter@naviant.com"


# ---------------------------------------------------------------------------
# The firm
# ---------------------------------------------------------------------------

TEAMS: tuple[dict, ...] = (
    {
        "external_id": "WD-T-100", "slug": "firm-leadership",
        "name": "Firm Leadership",
        "mission": "Sets the firm's strategy, pricing and partner-level "
                   "client relationships.",
        "lead_email": "rowan@customer.example",
    },
    {
        "external_id": "WD-T-110", "slug": "delivery", "name": "Delivery",
        "mission": "Runs client engagements end to end: scope, staffing, "
                   "schedule and the quality of what ships.",
        "parent_slug": "firm-leadership",
        "lead_email": "dana@customer.example",
    },
    {
        "external_id": "WD-T-111", "slug": "data-engineering",
        "name": "Data Engineering",
        "mission": "Builds the warehouses and pipelines client analytics "
                   "runs on.",
        "parent_slug": "delivery",
        "lead_email": "priya@customer.example",
    },
    {
        "external_id": "WD-T-112", "slug": "cloud-platform",
        "name": "Cloud Platform",
        "mission": "Landing zones, CI/CD and the platform work every "
                   "engagement stands on.",
        "parent_slug": "delivery",
        "lead_email": "sam@customer.example",
    },
    {
        "external_id": "WD-T-120", "slug": "advisory", "name": "Advisory",
        "mission": "Operating-model and change work: the part of an "
                   "engagement that is not code.",
        "parent_slug": "firm-leadership",
        "lead_email": "marcus@customer.example",
    },
    {
        "external_id": "WD-T-130", "slug": "finance-ops",
        "name": "Finance & Operations",
        "mission": "Billing, payables, revenue recognition and the firm's "
                   "own systems.",
        "parent_slug": "firm-leadership",
        "lead_email": "nadia@customer.example",
    },
)

EMPLOYEES: tuple[dict, ...] = (
    {
        "external_id": "WD-1002", "email": OPERATOR_EMAIL,
        "name": "Mitchell Suter", "title": "Director, Platform & Operations",
        "location": "Boston, MA", "start_date": "2021-01-11",
        "manager_email": "rowan@customer.example",
        "team_slug": "firm-leadership",
    },
    {
        "external_id": "WD-1001", "email": "rowan@customer.example",
        "name": "Rowan Ellis", "title": "Managing Partner",
        "location": "Boston, MA", "start_date": "2017-04-03",
        "team_slug": "firm-leadership",
    },
    {
        "external_id": "WD-1014", "email": "dana@customer.example",
        "name": "Dana Whitfield", "title": "Engagement Director",
        "location": "Boston, MA", "start_date": "2019-01-14",
        "manager_email": "rowan@customer.example", "team_slug": "delivery",
    },
    {
        "external_id": "WD-1027", "email": "priya@customer.example",
        "name": "Priya Raghavan", "title": "Data Engineering Lead",
        "location": "Toronto, ON", "start_date": "2020-06-08",
        "manager_email": "dana@customer.example",
        "team_slug": "data-engineering",
    },
    {
        "external_id": "WD-1041", "email": "kim@customer.example",
        "name": "Kim Reyes", "title": "Senior Data Engineer",
        "location": "Austin, TX", "start_date": "2022-02-21",
        "manager_email": "priya@customer.example",
        "team_slug": "data-engineering",
    },
    {
        "external_id": "WD-1033", "email": "sam@customer.example",
        "name": "Sam Okafor", "title": "Cloud Platform Lead",
        "location": "Remote · UK", "start_date": "2021-03-15",
        "manager_email": "dana@customer.example",
        "team_slug": "cloud-platform",
    },
    {
        "external_id": "WD-1056", "email": "pat@customer.example",
        "name": "Pat Nguyen", "title": "Platform Engineer",
        "location": "Chicago, IL", "start_date": "2023-09-05",
        "manager_email": "sam@customer.example",
        "team_slug": "cloud-platform",
    },
    {
        "external_id": "WD-1009", "email": "marcus@customer.example",
        "name": "Marcus Hale", "title": "Principal Consultant",
        "location": "Boston, MA", "start_date": "2018-08-20",
        "manager_email": "rowan@customer.example", "team_slug": "advisory",
    },
    {
        "external_id": "WD-1062", "email": "elena@customer.example",
        "name": "Elena Sokolov", "title": "Change Management Consultant",
        "location": "Remote · Germany", "start_date": "2024-01-08",
        "manager_email": "marcus@customer.example", "team_slug": "advisory",
    },
    {
        "external_id": "WD-1018", "email": "nadia@customer.example",
        "name": "Nadia Farrell", "title": "Finance & Operations Lead",
        "location": "Boston, MA", "start_date": "2019-10-07",
        "manager_email": "rowan@customer.example", "team_slug": "finance-ops",
    },
    {
        # The AP submitter the payables flow already writes invoices for: the
        # same person, now with a place in the org chart, which is what makes
        # "whose spend is this" a traversal rather than a string match.
        "external_id": "WD-1071", "email": "ap@customer.example",
        "name": "Tomas Berg", "title": "Accounts Payable Specialist",
        "location": "Boston, MA", "start_date": "2023-04-17",
        "manager_email": "nadia@customer.example", "team_slug": "finance-ops",
    },
)

CAPABILITIES: tuple[dict, ...] = (
    {
        "slug": "dimensional-modelling", "name": "Dimensional modelling",
        "category": "data",
        "description": "Designs star schemas and slowly-changing dimensions "
                       "that survive a decade of reporting change.",
    },
    {
        "slug": "dbt", "name": "dbt", "category": "data",
        "description": "Builds and reviews dbt projects: model layering, "
                       "tests, exposures and CI.",
    },
    {
        "slug": "snowflake", "name": "Snowflake", "category": "data",
        "description": "Warehouse design, workload isolation and cost "
                       "control on Snowflake.",
    },
    {
        "slug": "python", "name": "Python", "category": "engineering",
        "description": "Production Python: packaging, typing, tests, and the "
                       "restraint to keep a pipeline readable.",
    },
    {
        "slug": "gcp-architecture", "name": "GCP architecture",
        "category": "engineering",
        "description": "Landing zones, IAM boundaries and service selection "
                       "on Google Cloud.",
    },
    {
        "slug": "terraform", "name": "Terraform", "category": "engineering",
        "description": "Infrastructure as code with a review trail: modules, "
                       "state hygiene, plan discipline.",
    },
    {
        "slug": "change-management", "name": "Change management",
        "category": "advisory",
        "description": "Takes an organization from a decision to the "
                       "behaviour the decision assumed.",
    },
    {
        "slug": "stakeholder-facilitation", "name": "Stakeholder facilitation",
        "category": "advisory",
        "description": "Runs the room when the people in it disagree and a "
                       "decision is still required.",
    },
    {
        "slug": "engagement-management", "name": "Engagement management",
        "category": "delivery",
        "description": "Scope, staffing, margin and the difficult "
                       "conversation before it becomes a surprise.",
    },
    {
        "slug": "revenue-recognition", "name": "Revenue recognition",
        "category": "finance",
        "description": "Applies the firm's revenue policy to real "
                       "engagements, including the awkward ones.",
    },
)

# (employee email, capability slug, proficiency, how it was evidenced)
ASSIGNMENTS: tuple[tuple[str, str, str, str], ...] = (
    (OPERATOR_EMAIL, "engagement-management", "expert",
     "owns the platform programme end to end"),
    (OPERATOR_EMAIL, "gcp-architecture", "practising",
     "designed the Cumulus landing zone with Sam"),
    (OPERATOR_EMAIL, "change-management", "practising",
     "ran the knowledge-layer rollout"),
    ("rowan@customer.example", "engagement-management", "expert",
     "11 engagements as partner-in-charge"),
    ("rowan@customer.example", "stakeholder-facilitation", "expert",
     "board-level facilitation, 6 clients"),
    ("dana@customer.example", "engagement-management", "expert",
     "14 engagements delivered on scope"),
    ("dana@customer.example", "change-management", "practising",
     "co-led two operating-model rollouts"),
    ("priya@customer.example", "dimensional-modelling", "expert",
     "warehouse rebuild, Northwind engagement"),
    ("priya@customer.example", "dbt", "expert",
     "authored the firm's dbt project standard"),
    ("priya@customer.example", "snowflake", "practising",
     "two migrations, cost review owner"),
    ("kim@customer.example", "python", "expert",
     "maintains the ingestion framework"),
    ("kim@customer.example", "dbt", "practising",
     "peer-reviewed on three projects"),
    ("kim@customer.example", "dimensional-modelling", "practising",
     "modelled the claims mart under review"),
    ("sam@customer.example", "gcp-architecture", "expert",
     "landing zones for four clients"),
    ("sam@customer.example", "terraform", "expert",
     "owns the module library"),
    ("sam@customer.example", "python", "practising",
     "platform tooling"),
    ("pat@customer.example", "terraform", "practising",
     "shipped the CI pipeline modules"),
    ("pat@customer.example", "gcp-architecture", "practising",
     "certified 2025-03"),
    ("marcus@customer.example", "change-management", "expert",
     "seven transformations, two turned around"),
    ("marcus@customer.example", "stakeholder-facilitation", "expert",
     "runs the firm's facilitation training"),
    ("marcus@customer.example", "engagement-management", "practising",
     "advisory workstream lead"),
    ("elena@customer.example", "change-management", "practising",
     "two rollouts under Marcus"),
    ("elena@customer.example", "stakeholder-facilitation", "practising",
     "completed facilitation training 2024"),
    ("nadia@customer.example", "revenue-recognition", "expert",
     "owns the firm's revenue policy"),
    ("nadia@customer.example", "engagement-management", "practising",
     "margin review on every engagement"),
    ("ap@customer.example", "revenue-recognition", "practising",
     "applies the policy on the AP side"),
)


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def sync_workforce_additions(sync, graph: GraphService) -> int:
    """Deliver the people on the roster who are not in the graph yet.

    `seed_workforce_if_empty` only fires on an empty store, which means a
    person added to `EMPLOYEES` after a deployment was first seeded would never
    arrive — and the console would keep telling them they are not in the
    workforce. This runs on every start instead, and is a no-op the moment the
    roster and the graph agree.

    It is a delivery, not a patch: the same `workforce-sync` Flow, the same
    `hris:` claims, the same bind columns, so a person who arrives late is
    indistinguishable from one who was there at the start. Teams travel along
    because the org chart cannot be wired until both ends of every edge exist;
    re-asserting a team that already exists changes nothing.
    """
    missing = [
        person for person in EMPLOYEES
        if graph.get(object_id("Employee", person["email"])) is None
    ]
    if not missing:
        return 0

    from .sync import SyncRequest

    emails = {person["email"] for person in missing}
    request = SyncRequest.model_validate({
        "source": HRIS_SOURCE,
        "watermark": utcnow().isoformat(),
        "teams": list(TEAMS),
        "employees": missing,
    })
    sync.deliver(request, actor="system:roster")

    linked = 0
    for email, slug, proficiency, evidence in ASSIGNMENTS:
        if email not in emails:
            continue
        person = graph.get(object_id("Employee", email))
        capability = graph.get(object_id("Capability", slug))
        if person is None or capability is None:
            log.warning(
                "Skipping capability assignment",
                extra={"email": email, "capability": slug},
            )
            continue
        graph.link(
            "has_capability", person.id, capability.id,
            props={"proficiency": proficiency, "evidenced_by": evidence},
            method="human",
            provenance="asserted by people-ops",
        )
        linked += 1
    log.info(
        "Delivered roster additions",
        extra={"employees": len(missing), "assignments": linked},
    )
    return len(missing)


def seed_workforce_if_empty(sync, graph: GraphService) -> int:
    """Deliver the org chart, then assert the skills. Idempotent.

    Emptiness is judged on Teams rather than Employees: the expense flow
    mints an Employee the first time somebody submits, so people can already
    exist with nothing but an email. A Team only ever comes from here.
    """
    if graph.find("Team", limit=1):
        return 0

    from .sync import SyncRequest

    request = SyncRequest.model_validate({
        "source": HRIS_SOURCE,
        "watermark": "2026-08-20T00:00:00Z",
        "idempotency_key": "workforce-seed",
        "teams": list(TEAMS),
        "employees": list(EMPLOYEES),
    })
    sync.deliver(request, actor="system:seed")

    # Capabilities are a people-ops assertion, not a system's observation.
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
    linked = 0
    for email, slug, proficiency, evidence in ASSIGNMENTS:
        person = graph.get(object_id("Employee", email))
        capability = graph.get(object_id("Capability", slug))
        if person is None or capability is None:
            log.warning(
                "Skipping capability assignment",
                extra={"email": email, "capability": slug},
            )
            continue
        # Proficiency and evidence belong on the link: they are facts about
        # the holding, not about the person or the skill.
        graph.link(
            "has_capability", person.id, capability.id,
            props={"proficiency": proficiency, "evidenced_by": evidence},
            method="human",
            provenance="asserted by people-ops",
        )
        linked += 1
    log.info(
        "Seeded the workforce",
        extra={
            "teams": len(TEAMS), "employees": len(EMPLOYEES),
            "capabilities": len(CAPABILITIES), "assignments": linked,
        },
    )
    return len(TEAMS) + len(EMPLOYEES) + len(CAPABILITIES)
