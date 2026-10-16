"""The plant-floor additions to the ontology, pinned — and the promise that
nothing already in it moved.

Two halves, and the second is the one worth having. The first pins what the
seven new types and eleven new links declare: the natural keys, the
collections, which two types Cumulus is the book of record for, and which
three edges are compositions. The second is a snapshot of every type that was
already here before them — name, natural key, authoritativeness, governing
Collection — because "purely additive" is a claim about the *existing* model
and the only way to keep it is to write it down. A key that quietly gained a
clause, an `authoritative` that flipped, a type that changed Collection: each
would re-resolve or re-govern data in a deployment that is already running,
and each is a one-character diff away.
"""

from app import data_collections
from app.data_collections import COLLECTIONS, validate_collections
from app.mappings import validate_mappings
from app.ontology import ONTOLOGY, validate_ontology
from app.ontology import names

# The seven types this file is about, and what each one declares.
# (natural key, authoritative, collection, tags)
PLANT_TYPES: dict[str, tuple[str, bool, str, tuple[str, ...]]] = {
    "Job": (
        "source + job number",
        False, "production-records", ("jobs", "production", "scheduling"),
    ),
    "JobTask": (
        "job key + department slug",
        False, "production-records", (),
    ),
    "ScheduleDay": (
        "department slug + date",
        True, "production-records", ("scheduling", "capacity", "production"),
    ),
    "ScheduleEntry": (
        "schedule day key + job number",
        True, "production-records", (),
    ),
    "Credential": (
        "source + the source's own record id",
        False, "compliance-records", ("people", "compliance", "credentials"),
    ),
    "Equipment": (
        "source + equipment id",
        False, "equipment-register", ("equipment", "operations", "compliance"),
    ),
    "Inspection": (
        "equipment key + inspection date",
        False, "compliance-records", (),
    ),
}

PARTS = {
    "JobTask": ("Job", "has_job_task"),
    "ScheduleEntry": ("ScheduleDay", "has_schedule_entry"),
    "Inspection": ("Equipment", "has_inspection"),
}

# name -> (from, to, cardinality, nature)
PLANT_LINKS = {
    "has_job_task": ("Job", "JobTask", "one-to-many", "composition"),
    "job_for": ("Job", "Company", "many-to-one", "association"),
    "assigned_to": ("Job", "Team", "many-to-many", "association"),
    "documented_by": ("Job", "Document", "many-to-many", "association"),
    "job_folder": ("Job", "Folder", "many-to-many", "association"),
    "has_schedule_entry": (
        "ScheduleDay", "ScheduleEntry", "one-to-many", "composition",
    ),
    "scheduled_for": ("ScheduleEntry", "Job", "many-to-one", "association"),
    "scheduled_by": ("ScheduleDay", "Team", "many-to-one", "association"),
    "holds_credential": (
        "Employee", "Credential", "one-to-many", "association",
    ),
    "has_inspection": (
        "Equipment", "Inspection", "one-to-many", "composition",
    ),
    "operated_by": ("Equipment", "Team", "many-to-one", "association"),
}

NEW_COLLECTIONS = {
    "production-records": ("Job", "JobTask", "ScheduleDay", "ScheduleEntry"),
    "compliance-records": ("Credential", "Inspection"),
    "equipment-register": ("Equipment",),
}

# Every type declared before the plant floor was, with the three properties a
# running deployment's behavior actually depends on: the key rows are found
# by, whether Cumulus is the book of record, and which Collection's retention
# and custody govern them. Generated from the declaration at the moment the
# additions landed, and pinned here so a later edit to any of it is a test
# failure with a name on it rather than a silent re-resolution.
NAVIANT_TYPES: dict[str, tuple[str, bool, str]] = {
    "Employee": (
        "work email (re-keyable; identity is a minted uid)",
        True, "workforce",
    ),
    "Team": (
        "team slug",
        True, "workforce",
    ),
    "Capability": (
        "capability slug",
        True, "workforce",
    ),
    "Calendar": (
        "calendar address at the provider",
        False, "calendars",
    ),
    "CalendarEvent": (
        "calendar address + provider event id",
        False, "calendars",
    ),
    "ExpenseRequest": (
        "run id",
        True, "expense-records",
    ),
    "ExpenseLine": (
        "run id + ordinal",
        True, "expense-records",
    ),
    "Document": (
        "content hash; or source + external id for an original that is referenced in "
        "place rather than copied — a file we never fetched the bytes of has no hash "
        "to be keyed by, and inventing one would make it look read",
        False, "audit-documents",
    ),
    "Folder": (
        "source + folder path from the tree's root",
        False, "document-library",
    ),
    "Vendor": (
        "normalized legal name",
        True, "vendor-master",
    ),
    "Company": (
        "normalized legal name — the same normalization Vendor uses, so the "
        "two books collapse spellings identically and a proposed "
        "Company↔Vendor match is comparing like with like",
        True, "company-master",
    ),
    "Person": (
        "lowercased email. Where a source carries none, source + external id — a "
        "record with no address mints its own Person rather than guessing, so two "
        "same-named strangers stay two people and the merge, if there is one, is "
        "proposed later with evidence a human can read",
        True, "company-master",
    ),
    "SourceRecord": (
        "source + external id",
        False, "source-landing",
    ),
    "Invoice": (
        "vendor slug + invoice number",
        True, "ap-records",
    ),
    "InvoiceLine": (
        "invoice key + ordinal",
        True, "ap-records",
    ),
    "PurchaseOrder": (
        "po number",
        False, "ap-records",
    ),
    "PurchaseOrderLine": (
        "po id + ordinal",
        False, "ap-records",
    ),
    "ReceivingReport": (
        "receipt number",
        False, "ap-records",
    ),
    "ReceivingReportLine": (
        "receiving report id + ordinal",
        False, "ap-records",
    ),
    "MatchSet": (
        "the invoice key it matches",
        True, "ap-records",
    ),
    "Opportunity": (
        "source + opportunity id at the CRM that issued it",
        False, "sales-records",
    ),
    "SalesOrder": (
        "order number as the ERP issued it",
        False, "sales-records",
    ),
    "SalesOrderLine": (
        "sales order id + ordinal",
        False, "sales-records",
    ),
    "Item": (
        "SKU",
        False, "sales-records",
    ),
    "Mailbox": (
        "mailbox address at the provider",
        False, "correspondence",
    ),
    "MailMessage": (
        "mailbox address + provider message id",
        False, "correspondence",
    ),
    "Project": (
        "source + the PSA's own project code (`ENG-0100`) — source-scoped for the "
        "reason an opportunity id is: two delivery systems can both issue `ENG-0100` "
        "and mean different engagements",
        False, "delivery-records",
    ),
    "ProjectPhase": (
        "project id + the PSA's own phase id",
        False, "delivery-records",
    ),
}


# ---- the validators --------------------------------------------------------


def test_the_whole_declaration_still_conforms():
    """All three registries, because the additions touch all three: types and
    links in the TBox, three Collections beside them, and the Mapping registry
    which names both."""
    assert validate_ontology() == []
    assert validate_collections() == []
    assert validate_mappings() == []


# ---- the seven types -------------------------------------------------------


def test_the_plant_types_declare_what_the_plan_says_they_declare():
    for name, (key, authoritative, collection, tags) in PLANT_TYPES.items():
        declared = ONTOLOGY.object_type(name)
        assert declared is not None, name
        # The key's leading clause; the prose after it explains rather than
        # keys, so a reworded justification is not a rekey.
        assert declared.natural_key.startswith(key), name
        assert declared.authoritative is authoritative, name
        assert declared.collection == collection, name
        assert declared.tags == tags, name


def test_cumulus_is_the_book_of_record_for_the_schedule_and_nothing_else_here():
    """The plan's central claim, as an assertion.

    Nothing in the customer's estate is the system of record for a plant
    schedule, so Cumulus is — and the flag is what makes the day's approval a
    control point. The jobs, credentials and equipment are somebody else's
    rows, and the stamps a flow writes on them are `flow` claims on a
    non-authoritative type, exactly as `Document.sha256` is.
    """
    authoritative = {
        name for name in PLANT_TYPES
        if ONTOLOGY.object_type(name).authoritative
    }
    assert authoritative == {"ScheduleDay", "ScheduleEntry"}


def test_the_new_parts_declare_neither_tags_nor_competency_questions():
    """A part inherits its owner's facets and is asked about through it. Both
    are enforced by `validate_ontology`; pinned here because the spec for
    these three types states it explicitly and a later hand may not know
    why the fields are empty."""
    for part, (owner, _link) in PARTS.items():
        declared = ONTOLOGY.object_type(part)
        assert declared.tags == (), part
        assert declared.competency_questions == (), part
        assert ONTOLOGY.effective_tags(part) == (
            ONTOLOGY.object_type(owner).tags
        ), part


def test_the_new_roots_name_the_questions_they_exist_to_answer():
    for root in ("Job", "ScheduleDay", "Credential", "Equipment"):
        declared = ONTOLOGY.object_type(root)
        assert ONTOLOGY.owner_of(root) is None, root
        assert declared.competency_questions, root
        assert declared.tags, root


def test_no_new_type_carries_a_ref_field():
    """The department slug, the crew id and the two printed display names stay
    plain strings on purpose.

    A `ref:` field is an observation awaiting promotion, and the doctrine's
    rule is that the moment a competency question traverses one it must become
    a Link. The department's `Team` is already reachable through
    `ScheduleDay.scheduled_by` and the crew's through `assigned_to`, so a ref
    here would be a typed chip nothing walks — and a future reader "fixing"
    one of these fields into a ref would be adding a second, unresolved way to
    ask a question the graph already answers.
    """
    for name in PLANT_TYPES:
        for spec in ONTOLOGY.object_type(name).fields:
            assert spec.ref_target is None, f"{name}.{spec.name}"


def test_the_department_carrying_fields_are_strings():
    for type_name, field_name in (
        ("JobTask", "department"),
        ("ScheduleDay", "department"),
        ("Job", "upstream_done"),
        ("Job", "crew_id"),
        ("Job", "builder_name"),
        ("Job", "foreman_name"),
    ):
        spec = next(
            f for f in ONTOLOGY.object_type(type_name).fields
            if f.name == field_name
        )
        assert spec.kind == "string", f"{type_name}.{field_name}"
        assert spec.note, f"{type_name}.{field_name} carries no note"


# ---- the eleven links ------------------------------------------------------


def test_the_plant_links_declare_their_endpoints_and_nature():
    for name, (from_type, to_type, cardinality, nature) in PLANT_LINKS.items():
        link = ONTOLOGY.link_type(name)
        assert link is not None, name
        assert (link.from_type, link.to_type) == (from_type, to_type), name
        assert link.cardinality == cardinality, name
        assert link.nature == nature, name


def test_exactly_three_compositions_were_added_and_each_owns_one_part():
    compositions = {
        link.name: (link.from_type, link.to_type)
        for link in ONTOLOGY.links
        if link.composes and link.to_type in PLANT_TYPES
    }
    assert compositions == {
        "has_job_task": ("Job", "JobTask"),
        "has_schedule_entry": ("ScheduleDay", "ScheduleEntry"),
        "has_inspection": ("Equipment", "Inspection"),
    }
    for part, (owner, via) in PARTS.items():
        assert ONTOLOGY.owner_of(part) == owner, part
        assert ONTOLOGY.owning_link(part).name == via, part
    # The four roots are owned by nothing, which is what makes them roots.
    for root in ("Job", "ScheduleDay", "Credential", "Equipment"):
        assert ONTOLOGY.owner_of(root) is None, root


def test_a_part_key_embeds_its_owners():
    """The weak-entity test, read off the declared keys: `job key + …`,
    `schedule day key + …`, `equipment key + …`. Enforced per instance
    elsewhere; stated here because it is the reason these three are parts and
    not types of their own."""
    for part, (owner, _via) in PARTS.items():
        key = ONTOLOGY.object_type(part).natural_key
        assert key.startswith(
            {"Job": "job key", "ScheduleDay": "schedule day key",
             "Equipment": "equipment key"}[owner]
        ), part


def test_a_credential_is_associated_with_its_holder_and_not_owned_by_it():
    """Deliberately not a composition, and this is the assertion that says so.

    A composition means the part is identified through its owner, cannot exist
    without it, and dies with it. A credential does the opposite: the retention
    policy keeps a terminated worker's certification for years after the
    employment record stops being current, so killing it with the `Employee`
    is exactly what the policy forbids. `retain_until` lives on the credential
    for the same reason.
    """
    link = ONTOLOGY.link_type("holds_credential")
    assert link.nature == "association"
    assert not link.composes
    assert ONTOLOGY.owner_of("Credential") is None
    fields = {f.name for f in ONTOLOGY.object_type("Credential").fields}
    assert "retain_until" in fields


def test_no_link_name_is_declared_twice():
    """`assigned_to` and `documented_by` are the two the plan flagged as
    likeliest to collide with something already declared — a name lookup
    returns one declaration, so a second would have its edges checked against
    the first's endpoints and refused."""
    names_seen = [link.name for link in ONTOLOGY.links]
    assert len(names_seen) == len(set(names_seen))


# ---- the three Collections -------------------------------------------------


def test_the_new_collections_govern_the_new_types():
    for collection_id, types in NEW_COLLECTIONS.items():
        collection = data_collections.get(collection_id)
        assert collection is not None, collection_id
        assert collection.object_types == types, collection_id
        assert collection.role == "data"
        assert collection.custody == "copy"


def test_every_new_type_is_claimed_by_exactly_one_collection():
    """Retention and custody are properties of one set. Two claimants means a
    row governed by whichever tuple order won, which `validate_collections`
    refuses — pinned per type because the parts here are deliberately *not*
    in their owner's Collection: an `Inspection` is a dated compliance record
    and its `Equipment` is a live register entry."""
    for name in PLANT_TYPES:
        claimants = [c.id for c in COLLECTIONS if name in c.object_types]
        assert claimants == [PLANT_TYPES[name][2]], name
        assert data_collections.for_object_type(name).id == PLANT_TYPES[name][2]


def test_the_schedule_collection_carries_the_seven_year_horizon():
    """A day is the record of what a plant was told to build, who approved it
    and what it deferred — the document somebody comes back to after an
    incident, which is why it rides the financial-record horizon rather than
    the one-year working-record one."""
    assert data_collections.get("production-records").retention == "P7Y"
    assert data_collections.get("compliance-records").retention == "P7Y"
    # A condition, not a duration: a unit stays in the register while it is
    # operated, so stamping a horizon at write time would make a truck
    # purgeable while it is still on the road.
    register = data_collections.get("equipment-register")
    assert register.retention == "indefinite"
    assert "seven years" in register.retention_note


def test_the_new_collections_do_not_claim_an_existing_flow_as_a_writer():
    """The declared-writer lists are read back by the Flows screen, and the
    estate feeds' lists are pinned elsewhere. A Wick flow appearing in one of
    those would change what an unrelated deployment claims to write."""
    new_writers = set()
    for collection_id in NEW_COLLECTIONS:
        new_writers |= set(data_collections.get(collection_id).written_by)
    assert new_writers == {
        "job-sync", "credential-sync", "equipment-sync", "field-documents",
        "shipping-list-extraction", "department-scheduling",
    }


# ---- names.py --------------------------------------------------------------


def test_the_seven_type_constants_resolve():
    assert (
        names.JOB, names.JOB_TASK, names.SCHEDULE_DAY, names.SCHEDULE_ENTRY,
        names.CREDENTIAL, names.EQUIPMENT, names.INSPECTION,
    ) == (
        "Job", "JobTask", "ScheduleDay", "ScheduleEntry", "Credential",
        "Equipment", "Inspection",
    )


def test_no_link_constants_were_added():
    """The module's own convention: it protects lookup keys and declarations,
    and traversals pass link names as strings today. `require_link` stands
    ready for the day that changes."""
    for link_name in PLANT_LINKS:
        assert link_name.upper() not in vars(names)


# ---- the naviant-regression contract ---------------------------------------


def test_precedence_is_unchanged():
    """No new authority, and none needed: every claim the plant floor carries
    comes from a source already ranked — `erp` for a job, `hris` for a
    credential, `dms` for a filed document, `flow` for a derived stamp — and
    `erp` already outranks the other three while `human` outranks all of
    them."""
    assert ONTOLOGY.precedence == (
        "human", "erp", "hris", "crm", "calendar", "office", "dms", "psa",
        "procurement", "flow", "model",
    )


def test_no_pre_existing_type_declaration_changed():
    """The snapshot. Additive means the twenty-eight types that were here
    before are byte-for-byte what they were: same names, same natural keys,
    same authority, same governing Collection."""
    existing = {
        o.name: (o.natural_key, o.authoritative, o.collection)
        for o in ONTOLOGY.objects
        if o.name not in PLANT_TYPES
    }
    assert existing == NAVIANT_TYPES


def test_team_gained_a_cost_centre_and_lost_nothing():
    """The one field the plant floor added to a pre-existing type.

    A *field* on an existing type is the additive change the naviant contract
    allows, and this is it: `erp-jobs` binds a job's `crew_id` to a `Team`
    through the HRIS department's accounting code, so the graph has to hold
    the fact the join runs on. The alternative — a department whose primary id
    is the bare string `12` — is not what an HRIS looks like and would make
    the seam an accident of seeding.

    Pinned in both directions. The four fields `Team` had are still there and
    still in order, `external_id` still means the department id, and the new
    one is last, so the addition cannot quietly have been a replacement.
    """
    team = ONTOLOGY.object_type("Team")
    assert [f.name for f in team.fields] == [
        "slug", "name", "mission", "external_id", "cost_center",
    ]
    external_id, cost_center = team.fields[3], team.fields[4]
    assert external_id.kind == "string"
    assert "department id" in external_id.note
    assert cost_center.kind == "string"
    # And the note has to say the empty case is ordinary, because most of an
    # org chart has no cost centre and a reader who assumed otherwise would
    # go looking for a feed bug.
    assert "perfectly ordinary" in cost_center.note


def test_the_declaration_grew_by_exactly_the_planned_additions():
    assert set(ONTOLOGY.object_names()) == set(NAVIANT_TYPES) | set(PLANT_TYPES)
    assert len(ONTOLOGY.objects) == len(NAVIANT_TYPES) + 7


def test_no_pre_existing_collection_changed_what_it_governs():
    """The other end of the same contract. A type quietly moving Collection
    would re-govern live rows' retention and custody."""
    governed = {
        c.id: c.object_types for c in COLLECTIONS
        if c.id not in NEW_COLLECTIONS
    }
    assert governed == {
        "knowledge-library": (),
        "flow-definitions": (),
        "eval-datasets": (),
        "workforce": ("Employee", "Team", "Capability"),
        "calendars": ("Calendar", "CalendarEvent"),
        "correspondence": ("Mailbox", "MailMessage"),
        "vendor-master": ("Vendor",),
        "company-master": ("Company", "Person"),
        "sales-records": ("Opportunity", "SalesOrder", "SalesOrderLine", "Item"),
        "delivery-records": ("Project", "ProjectPhase"),
        "source-landing": ("SourceRecord",),
        "expense-records": ("ExpenseRequest", "ExpenseLine"),
        "ap-records": (
            "Invoice", "InvoiceLine", "PurchaseOrder", "PurchaseOrderLine",
            "ReceivingReport", "ReceivingReportLine", "MatchSet",
        ),
        "audit-documents": ("Document",),
        "document-library": ("Folder",),
        "task-ledger": (),
        "vendor-profiles": (),
    }


# ---- the export ------------------------------------------------------------


def test_the_exported_ontology_document_carries_the_additions():
    """The map is what the customer keeps, and the additions are only real
    once they are in it."""
    from app.ontology import ontology_markdown

    rendered = ontology_markdown()
    for name in PLANT_TYPES:
        assert f"`{name}`" in rendered, name
    for link_name in PLANT_LINKS:
        assert f"--{link_name}-->" in rendered, link_name
    # A part is rendered under its owner, which is how a reader of the
    # document learns the hierarchy without reading the Python.
    assert "Part of `Equipment` (via `has_inspection`)." in rendered
