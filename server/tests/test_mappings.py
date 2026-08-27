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


def test_every_feed_that_delivers_has_a_mapping():
    """The seed world's deliveries all arrive under declared sources — a feed
    with no contract would be exactly the smear this module retired."""
    declared = {m.source for m in MAPPINGS}
    for source in (
        "erp-vendors", "procurement-coupa", "legacy-ap-extract",
        "erp-purchasing", "wms-receiving",
    ):
        assert source in declared


def test_export_is_a_schema_export_and_says_where_it_lives():
    fields = mappings_asset_fields()
    assert fields["kind"] == "schema-export"
    assert "server/app/mappings.py" in fields["content"]
    assert "Conformance: **clean**" in mappings_markdown()


def test_registry_is_readable_and_read_only_over_http(client):
    body = client.get("/api/v1/mappings").json()
    assert body["problems"] == []
    ids = {m["id"] for m in body["mappings"]}
    assert "erp-vendors" in ids and "wms-receiving" in ids
    one = client.get("/api/v1/mappings/erp-purchasing").json()
    # Both halves of the contract are in the payload: the binding and the
    # escalation shape a batch feed carries.
    assert one["strategy"] == "reify"
    assert one["escalation"] == "windowed aggregate"
    assert any(b["object_type"] == "Vendor" for b in one["identity"])
    assert client.get("/api/v1/mappings/nope").status_code == 404
    # No write routes exist at all — declared in Python, like the ontology.
    assert client.post("/api/v1/mappings", json={}).status_code in (404, 405)
