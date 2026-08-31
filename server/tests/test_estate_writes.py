"""The outbound write plane: a closed table, a journal, and one PATCH.

Cumulus reads its customers' systems all day. This is the module that changes
one, and these tests hold it to the four properties that make that safe to
ship:

1. **The table is the door.** A target it does not name, or a transition it
   does not list, is refused inside this process — before a request exists.
   The estate itself validates the picklist and nothing else (the fake
   reproduces that, deliberately), so `WRITABLE` is the only place the
   *legality* of a state change is written down.
2. **The journal is write-ahead.** The intent is durable before the PATCH
   leaves, because a request that raises may still have landed.
3. **The estate decides whether the write took.** Not the 200 — the re-read.
   A lying 200 settles `unverified`, and that is a Task, not a success.
4. **Nothing is wired without being asked.** `ESTATE_WRITES_ENABLED` off
   means the writer holds no way to reach a network at all.

Nothing here touches one. The estate is `estate_fixtures.FakeEstate`, whose
`patch` is the real `erp.py` `_update` narrowed to what a PATCH can reach.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from app import estate_writes
from app.data_collections import COLLECTIONS
from app.estate import EstateConnector, InMemoryConnectorStateStore
from app.estate_writes import (
    WRITABLE,
    EstateWriteRefused,
    EstateWriteRejected,
    EstateWriter,
    EstateWritesDisabled,
    InMemoryEstateWriteJournal,
    build_estate_writer,
    entries_for,
    writable,
)
from tests import estate_fixtures as fx

PROJECT = "cumulus-demo-mitchell-suter"
TARGET = "sales-order-status"
#: SO5003 in the recorded estate — the one seeded order still Pending Approval,
#: which is why the fixture keeps the five originals and not the Billed ten.
ORDER = "5003"
PENDING = "Pending Approval"
RELEASED = "Pending Fulfillment"


def build(estate: fx.FakeEstate | None = None, **over) -> EstateWriter:
    """A writer over the fake estate: the table and the journal are real, and
    the only thing faked is the wire — the same bargain `test_estate.py`'s
    `build` makes for the read side."""
    estate = estate or fx.FakeEstate()
    connector = EstateConnector(
        InMemoryConnectorStateStore(),
        project=PROJECT,
        namespace="naviant",
        fetch=estate.fetch,
    )
    writer = EstateWriter(
        connector,
        over.pop("journal", None) or InMemoryEstateWriteJournal(),
        fetch=over.pop("fetch", None) or estate.fetch,
        patch=over.pop("patch", None) or estate.patch,
        **over,
    )
    writer.estate = estate  # type: ignore[attr-defined]  — test handle
    return writer


# -- the boundary -------------------------------------------------------------

#: `estate.py`'s reason (3), enforced here rather than promised: `pull_all`
#: runs on a timer, and it must never be one refactor away from a loop that
#: writes. The cursor and the write journal are different machinery with
#: different lifetimes, so the writer may not reach for either.
OFF_LIMITS = {"sync", "graph", "resolution", "gateway"}


def test_the_writer_reaches_for_no_cursor_and_no_sweep():
    """A sibling of `estate.py`, not a method on it.

    Module paths are normalized to their last segment for the reason
    `test_estate.py` gives: one import has several spellings. This guard reads
    only what `estate_writes.py` reaches for *directly* — it imports
    `estate.py`, which imports `sync`, and that is the point of the direction:
    the writer may borrow the reader's addresses, and the reader may not
    borrow the writer's PATCH.
    """
    source = pathlib.Path(__file__).parent.parent / "app" / "estate_writes.py"
    tree = ast.parse(source.read_text())
    imported: set[str] = set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = (node.module or "").strip(".")
            if module:
                imported.add(module.rsplit(".", 1)[-1])
            else:
                imported.update(a.name.rsplit(".", 1)[-1] for a in node.names)
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            imported.update(a.name.rsplit(".", 1)[-1] for a in node.names)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    assert imported & OFF_LIMITS == set()
    assert "ConnectorState" not in names
    assert "ConnectorStateStore" not in names


def test_the_journal_is_machinery_and_not_a_dataset():
    """DOCTRINE.md: a flow run journal is not a Dataset, and neither is this.

    Its own collection, absent from the map — the same argument
    `ConnectorState` makes, and worth asserting for the same reason: a write
    journal in `COLLECTIONS` would acquire a retention policy, a custody
    class and a Sources-screen row it has no business having.
    """
    containers = {c.container for c in COLLECTIONS} | {c.id for c in COLLECTIONS}
    assert estate_writes.DEFAULT_JOURNAL_COLLECTION not in containers


# -- the table ----------------------------------------------------------------


def test_the_table_is_one_row_and_it_names_the_legal_move():
    """The whole write plane, readable in one screen. A second row is a
    decision someone makes on purpose."""
    assert [t.id for t in WRITABLE] == ["sales-order-status"]
    row = writable(TARGET)
    assert (row.app, row.resource, row.field) == ("erp", "salesOrders", "status")
    assert row.action == "release-sales-order"
    assert row.transitions == {PENDING: (RELEASED,)}


def test_a_target_outside_the_table_is_refused_before_any_http():
    writer = build()
    with pytest.raises(EstateWriteRefused) as caught:
        writer.apply(
            "purchase-order-status", "6000", observed=PENDING, value="Closed"
        )
    assert "purchase-order-status" in str(caught.value)
    assert writer.estate.calls == []
    assert writer.journal.list() == []


def test_a_transition_outside_the_table_is_refused_before_any_http():
    """`Billed` is a status the estate would take. The table is where the
    system knows it is not ours to set."""
    writer = build()
    with pytest.raises(EstateWriteRefused) as caught:
        writer.apply(TARGET, ORDER, observed=PENDING, value="Billed")
    assert "Billed" in str(caught.value)
    assert writer.estate.calls == []
    assert writer.journal.list() == []


def test_a_move_from_the_wrong_state_is_refused_before_any_http():
    """Same order, same destination, a different starting point — the
    transition table is keyed on where the row actually stands."""
    writer = build()
    with pytest.raises(EstateWriteRefused):
        writer.apply(TARGET, ORDER, observed="Closed", value=RELEASED)
    assert writer.estate.calls == []


def test_the_estate_would_have_accepted_what_the_table_refused():
    """The load-bearing asymmetry, asserted in both directions at once.

    The same PATCH the writer refuses lands cleanly when issued through the
    fake's wire — because `erp.py` validates the picklist and stops. If this
    test ever fails because the fake grew a transition check, the fake has
    started testing a system nothing serves; fix the fake, not this.
    """
    writer = build()
    writer.estate.patch(
        writer.connector.record_url("erp", "salesOrders", ORDER),
        {"status": "Billed"},
    )
    assert writer.estate.record("erp", "salesOrders", ORDER)["status"] == "Billed"


# -- what the estate says back ------------------------------------------------


def test_a_rejected_value_carries_the_estates_own_message():
    """A 400 arrives as the estate wrote it. An operator reading the Task sees
    the ERP's sentence, not a paraphrase of it."""
    writer = build()
    with pytest.raises(EstateWriteRejected) as caught:
        writer.patch("erp", "salesOrders", ORDER, {"status": "Shipped"})
    assert caught.value.status == 400
    assert "must be one of" in caught.value.message
    assert "Pending Fulfillment" in caught.value.message


def test_an_unknown_field_is_the_estates_400_and_not_a_silent_no_op():
    writer = build()
    with pytest.raises(EstateWriteRejected) as caught:
        writer.patch("erp", "salesOrders", ORDER, {"approvedBy": "raymond"})
    assert caught.value.status == 400
    assert "unknown fields: approvedBy" in caught.value.message


def test_a_missing_record_is_a_404_not_a_400():
    """Distinguishable, because the two mean different things to the flow: a
    400 is a value we chose, a 404 is a row that moved out from under us."""
    writer = build()
    with pytest.raises(EstateWriteRejected) as caught:
        writer.patch("erp", "salesOrders", "9999", {"status": RELEASED})
    assert caught.value.status == 404
    assert "9999" in caught.value.message


# -- the one write ------------------------------------------------------------


def test_the_patch_sends_only_the_declared_field():
    """No round-tripped record, no `updatedAt` on the wire, no second field
    riding along. The approval said one thing changes; one thing changes."""
    writer = build()
    sent: list[dict] = []
    original = writer.estate.patch

    def watching(url: str, body: dict) -> dict:
        sent.append(dict(body))
        return original(url, body)

    writer = build(writer.estate, patch=watching)
    writer.apply(TARGET, ORDER, observed=PENDING, value=RELEASED)
    assert sent == [{"status": RELEASED}]


def test_the_read_after_the_write_shows_the_value_and_a_later_updated_at():
    writer = build()
    before = writer.read("erp", "salesOrders", ORDER)
    record = writer.apply(TARGET, ORDER, observed=PENDING, value=RELEASED)
    after = writer.read("erp", "salesOrders", ORDER)

    assert before["status"] == PENDING
    assert after["status"] == RELEASED
    assert after["updatedAt"] > before["updatedAt"]
    # Everything else on the order is exactly what it was.
    assert {k: v for k, v in after.items() if k not in {"status", "updatedAt"}} == {
        k: v for k, v in before.items() if k not in {"status", "updatedAt"}
    }
    assert record.state == "applied"
    assert record.estate_updated_at == after["updatedAt"]


def test_the_applied_entry_carries_what_a_later_pass_needs_to_recognise():
    """The echo pass and the idempotency check both read this row, so the
    fields they key on are asserted here rather than discovered there."""
    writer = build()
    record = writer.apply(
        TARGET, ORDER, observed=PENDING, value=RELEASED,
        run_id="run-1", actor="raymond@naviant.com",
    )
    assert (record.target, record.record_id) == (TARGET, ORDER)
    assert (record.app, record.resource, record.field) == (
        "erp", "salesOrders", "status",
    )
    assert (record.observed, record.value) == (PENDING, RELEASED)
    assert record.action == "release-sales-order"
    assert (record.run_id, record.actor) == ("run-1", "raymond@naviant.com")
    assert record.state == "applied"
    assert record.intended_at and record.settled_at >= record.intended_at
    assert entries_for(writer.journal, TARGET, ORDER) == [record]


# -- the journal --------------------------------------------------------------


def test_the_intent_is_journalled_before_the_patch_goes_out():
    """Write-ahead, and the fake is the witness: at the moment the estate is
    asked to change, the durable record that we asked already exists.

    Not a nicety. A PATCH that raises may still have landed, and the entry
    naming the record and the value we sent is the only thing that lets a
    human find out which.
    """
    journal = InMemoryEstateWriteJournal()
    estate = fx.FakeEstate()
    seen: list[list[tuple[str, str]]] = []

    def watching(url: str, body: dict) -> dict:
        seen.append([(e.state, e.record_id) for e in journal.list()])
        return estate.patch(url, body)

    writer = build(estate, journal=journal, patch=watching)
    writer.apply(TARGET, ORDER, observed=PENDING, value=RELEASED)
    assert seen == [[("intent", ORDER)]]


def test_a_patch_that_raises_settles_the_intent_as_failed_and_re_raises():
    """`failed` is the world saying no, and it keeps the intent visible. The
    exception continues on its way so the Gateway ledgers it too."""
    writer = build()

    def refusing(url: str, body: dict) -> dict:
        raise fx.EstateRefused(503, "erp unavailable")

    writer = build(writer.estate, patch=refusing)
    with pytest.raises(EstateWriteRejected):
        writer.apply(TARGET, ORDER, observed=PENDING, value=RELEASED)

    entry = writer.journal.list()[0]
    assert entry.state == "failed"
    assert "erp unavailable" in entry.error
    assert entry.value == RELEASED  # what we sent, still legible
    assert entry.settled_at


def test_a_lying_200_settles_as_unverified():
    """The estate is the source of truth for whether the write took. A door
    that answers 200 and changes nothing is a discrepancy, not a success."""
    writer = build()

    def lying(url: str, body: dict) -> dict:
        return {"id": ORDER, "status": body["status"]}

    writer = build(writer.estate, patch=lying)
    record = writer.apply(TARGET, ORDER, observed=PENDING, value=RELEASED)
    assert record.state == "unverified"
    assert record.verified == PENDING
    assert writer.estate.record("erp", "salesOrders", ORDER)["status"] == PENDING


def test_a_verification_that_cannot_be_read_is_unverified_and_does_not_raise():
    """The PATCH went out and was not refused; we simply could not confirm.
    That is the `unverified` case exactly, and it must not be re-tried by an
    exception escaping into a step that retries — that is the double-PATCH.
    """
    estate = fx.FakeEstate()

    def blind(url: str) -> dict:
        raise fx.EstateDown(f"GET {url} → 503")

    writer = build(estate, fetch=blind)
    record = writer.apply(TARGET, ORDER, observed=PENDING, value=RELEASED)
    assert record.state == "unverified"
    assert "503" in record.error


def test_the_journal_keeps_one_row_per_run_so_a_retry_settles_it():
    """A step with `retries=1` replays the whole body, and the second attempt
    is the same attempt — not a second row claiming a second write."""
    journal = InMemoryEstateWriteJournal()
    estate = fx.FakeEstate()
    attempts = {"n": 0}

    def flaky(url: str, body: dict) -> dict:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise fx.EstateRefused(503, "erp unavailable")
        return estate.patch(url, body)

    writer = build(estate, journal=journal, patch=flaky)
    with pytest.raises(EstateWriteRejected):
        writer.apply(TARGET, ORDER, observed=PENDING, value=RELEASED, run_id="r1")
    record = writer.apply(
        TARGET, ORDER, observed=PENDING, value=RELEASED, run_id="r1"
    )
    assert [e.state for e in journal.list()] == ["applied"]
    assert record.id == journal.list()[0].id


def test_two_runs_against_one_order_are_two_rows():
    """Different runs are different decisions, and the failed one is evidence
    that must survive the retry someone starts an hour later."""
    journal = InMemoryEstateWriteJournal()
    estate = fx.FakeEstate()

    def refusing(url: str, body: dict) -> dict:
        raise fx.EstateRefused(503, "erp unavailable")

    with pytest.raises(EstateWriteRejected):
        build(estate, journal=journal, patch=refusing).apply(
            TARGET, ORDER, observed=PENDING, value=RELEASED, run_id="r1"
        )
    build(estate, journal=journal).apply(
        TARGET, ORDER, observed=PENDING, value=RELEASED, run_id="r2"
    )
    assert sorted(e.state for e in journal.list()) == ["applied", "failed"]
    assert len(entries_for(journal, TARGET, ORDER)) == 2


def test_the_open_entries_are_the_ones_an_echo_could_still_come_home_for():
    """What the delivery-side pass will walk. `failed` is not on the list: a
    write that never landed has no echo owed to it, and a refusal has no row
    at all."""
    journal = InMemoryEstateWriteJournal()
    estate = fx.FakeEstate()

    def refusing(url: str, body: dict) -> dict:
        raise fx.EstateRefused(503, "erp unavailable")

    with pytest.raises(EstateWriteRejected):
        build(estate, journal=journal, patch=refusing).apply(
            TARGET, ORDER, observed=PENDING, value=RELEASED, run_id="r1"
        )
    build(estate, journal=journal).apply(
        TARGET, ORDER, observed=PENDING, value=RELEASED, run_id="r2"
    )
    assert [e.run_id for e in estate_writes.awaiting_echo(journal)] == ["r2"]


def test_a_state_the_arc_does_not_name_is_not_writable():
    """The journal's vocabulary is closed for the same reason the table is: a
    typo'd state is a row no dashboard will ever count."""
    fields = dict(
        id="x", target=TARGET, app="erp", resource="salesOrders",
        record_id=ORDER, field="status", observed=PENDING, value=RELEASED,
    )
    assert estate_writes.EstateWriteRecord(**fields).state == "intent"
    with pytest.raises(ValueError):
        estate_writes.EstateWriteRecord(**fields, state="applyed")
    # The two the echo pass sets later are part of the arc from the start, so
    # S2b-2 lands a reader and not a vocabulary.
    assert {"echoed", "diverged", "unechoed"} <= set(estate_writes.WRITE_STATES)


# -- the switch ---------------------------------------------------------------


class _Boom:
    """Stands where `httpx` does, and fails the test if anything reaches it."""

    def __init__(self) -> None:
        self.touched: list[str] = []

    def __getattr__(self, name: str):
        self.touched.append(name)
        raise AssertionError(f"the writer reached for httpx.{name}")


def test_writes_disabled_constructs_no_way_to_reach_the_estate(monkeypatch):
    """Default false, and false means *unwired* — not "wired and polite".

    Three things have to be true at once for a live write, and this is the
    one that holds when the other two are wrong.
    """
    boom = _Boom()
    monkeypatch.setattr(estate_writes, "httpx", boom)
    connector = EstateConnector(
        InMemoryConnectorStateStore(), project=PROJECT, namespace="naviant"
    )
    writer = build_estate_writer(connector, "memory", None, "estate_write_journal")

    assert writer.live is False
    with pytest.raises(EstateWritesDisabled):
        writer.apply(TARGET, ORDER, observed=PENDING, value=RELEASED)
    with pytest.raises(EstateWritesDisabled):
        writer.read("erp", "salesOrders", ORDER)
    assert boom.touched == []
    assert writer.journal.list() == []  # refused before the write-ahead


def test_an_injected_wire_needs_no_switch():
    """How the evals get a writer: the flag gates the *real* door, and a fake
    handed in at construction is not that door. `ESTATE_WRITES_ENABLED` stays
    false in every test run, including the ones that write."""
    writer = build()  # enabled defaults to false
    assert writer.live is False
    assert writer.apply(TARGET, ORDER, observed=PENDING, value=RELEASED).state == (
        "applied"
    )


def test_half_a_wire_is_refused_at_construction():
    """Injecting `patch` and forgetting `fetch` would leave a writer that can
    change the estate and can never confirm it did — every write settling
    `unverified` for a reason nothing in the output names."""
    connector = EstateConnector(
        InMemoryConnectorStateStore(), project=PROJECT, namespace="naviant"
    )
    with pytest.raises(ValueError):
        EstateWriter(
            connector,
            InMemoryEstateWriteJournal(),
            patch=lambda url, body: {},
        )


def test_the_wired_writer_this_deployment_would_get_is_not_live():
    """The accessor, exercised — a singleton nothing constructs is a singleton
    nothing knows is broken. The settings a test run has are the settings a
    deployment has until somebody sets the variable on purpose."""
    import app.deps as deps

    previous = deps._estate_writer
    deps._estate_writer = None
    try:
        writer = deps.get_estate_writer()
        assert writer.live is False
        assert writer is deps.get_estate_writer()
        assert writer.connector is deps.get_estate_connector()
    finally:
        deps._estate_writer = previous


def test_an_unregistered_estate_is_refused_before_a_url_is_built(monkeypatch):
    boom = _Boom()
    monkeypatch.setattr(estate_writes, "httpx", boom)
    connector = EstateConnector(InMemoryConnectorStateStore(), project="")
    writer = build_estate_writer(
        connector, "memory", None, "estate_write_journal", enabled=True
    )
    assert writer.live is True
    with pytest.raises(EstateWriteRefused) as caught:
        writer.apply(TARGET, ORDER, observed=PENDING, value=RELEASED)
    assert "no estate registered" in str(caught.value)
    assert boom.touched == []
