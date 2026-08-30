"""The Mapping registry: declared seams, validated against what they name.

The doctrine's Mapping primitive was smeared across four modules; now that it
is one artifact, these tests pin the two properties that make it worth having:
the registry cannot silently name things that do not exist, and the corpus
carries it the same way it carries the ontology.
"""

import pytest

from app.mappings import (
    MAPPINGS,
    IdentityBinding,
    Mapping,
    mappings_asset_fields,
    mappings_markdown,
    validate_mappings,
)
from tests import world as test_world


@pytest.fixture()
def world():
    return test_world.fresh()


@pytest.fixture()
def client(_client, world):
    return _client


def test_declared_mappings_conform():
    assert validate_mappings() == []


def test_a_mapping_naming_a_ghost_type_is_caught():
    bad = Mapping(
        id="ghost", source="ghost-feed", system="x", summary="s",
        transport="push", payload="VendorRecord", strategy="reify",
        lands_as="Widget", resolves_to=("Gadget",),
        identity=(IdentityBinding(column="c", object_type="Gizmo", key="k"),),
        precedence_source="oracle", collection="no-such-collection",
    )
    problems = validate_mappings((bad,))
    text = " ".join(problems)
    for missing in ("Widget", "Gadget", "Gizmo", "no-such-collection", "oracle"):
        assert missing in text


def test_bind_must_not_reify_by_the_back_door():
    """A bind mapping naming `lands_as` would blur the one distinction the
    strategy field exists to keep sharp: bind rows never become objects."""
    bad = Mapping(
        id="telemetry", source="iot", system="historian", summary="s",
        transport="push", payload="ReadingRecord", strategy="bind",
        lands_as="SourceRecord",
    )
    assert any("bind" in p for p in validate_mappings((bad,)))


def test_every_mapping_describes_a_system_the_estate_actually_runs():
    """The registry is a claim about the customer's estate, so a contract for
    a system nobody operates is a lie the Sources screen would repeat.

    `procurement-coupa`, `legacy-ap-extract` and `wms-receiving` were exactly
    that — invented systems declared so a seeded demo world had somewhere to
    arrive from — and they retired with the seed. `receiving-sync` keeps its
    Flow and its Tier-2 definition; what it has no more of is a Mapping,
    because this estate has no receiving system. That gap is stated, not
    papered over: a delivery under `wms-receiving` still lands, and the screen
    reports it as arriving under no declared contract.
    """
    declared = {m.source for m in MAPPINGS}
    for source in (
        "erp-vendors", "erp-purchase-orders", "erp-sales", "crm-book",
        "hris-workforce", "office-calendar",
    ):
        assert source in declared
    assert declared.isdisjoint(
        {"procurement-coupa", "legacy-ap-extract", "wms-receiving"}
    )


def test_a_feed_name_resolves_to_an_authority_the_ontology_ranks():
    """Precedence is declared over authorities, not over feed names, so this
    function is the whole join between the two. A bucket the ontology does not
    rank would sort last by accident rather than by decision.

    The office branch sits below the calendar branch deliberately: one
    provider serves both surfaces, so `office-calendar` and `office-mail`
    share a prefix while only the mail half is a hint. Matching on the suite
    name first would demote the calendar — the one thing that actually knows
    what is on somebody's Friday.
    """
    from app.ontology import ONTOLOGY
    from app.sync import _authority

    assert _authority("crm-book") == "crm"
    assert _authority("erp-sales") == "erp"
    assert _authority("office-mail") == "office"
    assert _authority("office-calendar") == "calendar"
    assert _authority("hris-employment-events") == "hris"
    # The existing feeds keep the standing they had.
    for source, expected in (
        ("erp-vendors", "erp"), ("erp-purchase-orders", "erp"),
        ("hris-workforce", "hris"),
        ("procurement-coupa", "procurement"), ("wms-receiving", "procurement"),
    ):
        assert _authority(source) == expected, source

    ranked = set(ONTOLOGY.precedence)
    for m in MAPPINGS:
        assert _authority(m.source) in ranked, m.source


def test_export_is_a_schema_export_and_says_where_it_lives():
    fields = mappings_asset_fields()
    assert fields["kind"] == "schema-export"
    assert "server/app/mappings.py" in fields["content"]
    assert "Conformance: **clean**" in mappings_markdown()


def test_registry_is_readable_and_read_only_over_http(client):
    body = client.get("/api/v1/mappings").json()
    assert body["problems"] == []
    ids = {m["id"] for m in body["mappings"]}
    assert "erp-vendors" in ids and "hris-workforce" in ids
    one = client.get("/api/v1/mappings/erp-purchase-orders").json()
    # Both halves of the contract are in the payload: the binding and the
    # escalation shape a batch feed carries.
    assert one["strategy"] == "reify"
    assert one["escalation"] == "windowed aggregate"
    assert any(b["object_type"] == "Vendor" for b in one["identity"])
    assert client.get("/api/v1/mappings/nope").status_code == 404
    # No write routes exist at all — declared in Python, like the ontology.
    assert client.post("/api/v1/mappings", json={}).status_code in (404, 405)
