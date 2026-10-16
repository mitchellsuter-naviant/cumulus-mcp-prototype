"""The UI manifest: which screen answers which task, resolved here.

The console used to hold this whole answer — a compiled Dart table of flows,
versions and screens, plus its own copy of the "newest package at or below the
task's version" rule. Two failures that shape came with, and that these
defend against:

- **A console can be shipped believing in a version arrangement the server does
  not have.** A package registered at a version the fleet never deployed
  resolves by the fallback rather than on purpose, and nothing says so. Here
  the versions in the manifest are the `v<N>/` directories that shipped, so the
  two cannot disagree.
- **A rule that exists twice can be corrected once.** `resolve_ui` is the only
  place the version rule lives now; the client looks the answer up.

The copy is asserted verbatim rather than by shape. The labels and hints are
the part a person actually reads, and this lane moved them across a wire — a
move that quietly reworded one of them would have changed the thing it was
supposed to leave alone.
"""

from __future__ import annotations

import inspect

import pytest
from fastapi import HTTPException

from app import task_screens, ui_api
from app.auth import Principal
from app.task_screens import build_manifest, declared_ui, resolve_ui

ROUTE = "/api/ui/v1/task-screens"

CORRECT_THE_FACTS = (
    "Opens the full screen, where the facts themselves can be corrected — "
    "the checks then run again over what you changed."
)


# ---- resolution ------------------------------------------------------------


def test_only_versions_that_declare_a_ui_are_declarations():
    """v7 changed nothing a person sees and says nothing.

    Listing it with an empty declaration would be worse than not listing it:
    an empty `UI` at v7 would *shadow* v6's screens for every v7 task, which is
    the opposite of what "this version changed no screens" means.
    """
    assert sorted(declared_ui("accounts-payable")) == [6, 8, 9]
    # v10 asks a different question of a different person, on a screen of its
    # own; v9's is still declared because v9 is still selectable.
    assert sorted(declared_ui("expense-reimbursement")) == [9, 10]
    assert declared_ui("vendor-sync") == {}
    assert declared_ui("not-a-flow") == {}


def test_a_version_between_two_declarations_reads_the_older_one():
    """A v7 task is v6's question, and v8's screen is about field verdicts a
    v7 run never wrote — so the newer screen is the wrong answer, not merely a
    less specific one."""
    six = resolve_ui("accounts-payable", 6)
    seven = resolve_ui("accounts-payable", 7)
    assert seven == six
    assert seven.screen_for("curation").label == "Open the extraction verification"


def test_a_version_past_the_end_reads_the_newest():
    """Both directions off the ends. A v40 that never changed its screens and
    a task carrying no version at all — an older record, or a locally built
    double — are better served by the newest declaration than by none."""
    newest = resolve_ui("accounts-payable", 9)
    for version in (0, 3, 40):
        assert resolve_ui("accounts-payable", version) == newest, version
    assert newest.screen_for("curation").screen_id == "accounts-payable/invoice-review"


def test_a_flow_that_declares_nothing_resolves_to_nothing():
    """Rather than to some other flow's screen. A task with no bespoke screen
    gets the generic approve/reject pane, which is the honest affordance when
    there is nothing structured to edit."""
    assert resolve_ui("vendor-sync", 1) is None
    assert resolve_ui("not-a-flow", 1) is None


# ---- what a version declares -----------------------------------------------


def test_accounts_payable_v6_splits_by_kind():
    """The extraction asking to be checked, and the comparison asking to be
    answered, are two moments of one invoice."""
    ui = resolve_ui("accounts-payable", 6)
    curation = ui.screen_for("curation")
    assert curation.screen_id == "accounts-payable/extraction-review"
    assert curation.label == "Open the extraction verification"
    assert curation.hint == CORRECT_THE_FACTS

    for kind in ("decision", "exception", "anything-else"):
        other = ui.screen_for(kind)
        assert other.screen_id == "accounts-payable/three-way-match", kind
        assert other.label == "Open the invoice review", kind
        assert other.hint == CORRECT_THE_FACTS, kind


def test_accounts_payable_v8_answers_every_kind_with_one_screen():
    """Its run reports which fields are in doubt, so the task's kind is the
    older, coarser version of the same signal — declaring both would be them
    arguing."""
    ui = resolve_ui("accounts-payable", 8)
    assert ui.by_kind == {}
    for kind in ("curation", "decision", "exception"):
        screen = ui.screen_for(kind)
        assert screen.screen_id == "accounts-payable/invoice-review", kind
        assert screen.label == "Open the invoice review", kind
        assert "a box on each field the run could not settle" in screen.hint, kind


def test_expense_reimbursement_declares_its_review():
    screen = resolve_ui("expense-reimbursement", 9).screen_for("decision")
    assert screen.screen_id == "expense-reimbursement/expense-review"
    assert screen.label == "Open the expense review"
    assert screen.hint == CORRECT_THE_FACTS


def test_the_shared_screens_belong_to_no_flow():
    """Any flow's resolver can raise these, and what a steward reads is the
    same question every time — so they are keyed by kind, and the console
    answers them before it looks at the task's flow."""
    shared = task_screens.SHARED_SCREENS
    assert shared["match_review"].label == "Open the match review"
    assert shared["match_review"].hint == CORRECT_THE_FACTS
    assert shared["merge_review"].label == "Open the merge review"
    assert "Nothing merges on either verdict" in shared["merge_review"].hint
    assert shared["state_disagreement"].label == "Open the disagreement"
    assert (
        "exactly what releasing would change in the ERP"
        in shared["state_disagreement"].hint
    )


# ---- the manifest ----------------------------------------------------------


def test_the_manifest_lists_every_shipped_version_already_resolved():
    """The client performs no version arithmetic: it looks up the version the
    task carries. So v7 has to appear here, carrying v6's screens."""
    manifest = build_manifest()
    ap = next(f for f in manifest.flows if f.flow_id == "accounts-payable")
    assert [v.version for v in ap.versions] == [6, 7, 8, 9]
    by_version = {v.version: v.ui for v in ap.versions}
    assert by_version[7] == by_version[6]
    assert by_version[8] != by_version[6]
    # v9 reuses v8's screen and says something different about it — the vendor
    # candidates are on the same page now — so it is its own declaration.
    assert by_version[9] != by_version[8]
    assert ap.fallback == by_version[9]


def test_flows_that_declare_no_screens_are_absent_rather_than_empty():
    """An empty entry would read as "this flow has no bespoke screens", which
    is the manifest asserting something about packages that never spoke."""
    listed = {f.flow_id for f in build_manifest().flows}
    assert listed == {
        "accounts-payable", "credential-tracking", "department-scheduling",
        "expense-reimbursement", "field-documents", "shipping-list-extraction",
    }


def test_every_declared_screen_id_is_distinct_and_named():
    """Two screens sharing an id is one of them unreachable, and the console
    cannot tell — it would build the first constructor its map holds."""
    seen = {s.screen_id for s in task_screens.SHARED_SCREENS.values()}
    for flow in build_manifest().flows:
        for version in flow.versions:
            screens = list(version.ui.by_kind.values())
            if version.ui.otherwise is not None:
                screens.append(version.ui.otherwise)
            for screen in screens:
                assert screen.screen_id, f"{flow.flow_id} v{version.version}"
                assert screen.label
                assert screen.hint
                seen.add(screen.screen_id)
    assert len(seen) == 12
    # Version-free, because v7 reads v6's screen and an id carrying a version
    # would make one screen look like two.
    assert not any(part.startswith("v") and part[1:].isdigit()
                   for sid in seen for part in sid.split("/"))


# ---- the route -------------------------------------------------------------


@pytest.fixture()
def client(_client):
    return _client


def test_the_route_serves_the_manifest(client):
    body = client.get(ROUTE).json()
    assert set(body["shared"]) == {
        "match_review", "merge_review", "state_disagreement",
    }
    ap = next(f for f in body["flows"] if f["flow_id"] == "accounts-payable")
    seven = next(v for v in ap["versions"] if v["version"] == 7)
    assert (
        seven["ui"]["by_kind"]["curation"]["label"]
        == "Open the extraction verification"
    )
    assert ap["fallback"]["otherwise"]["screen_id"] == (
        "accounts-payable/invoice-review"
    )


def test_the_manifest_is_code_shaped_not_world_shaped(client):
    """It answers on a cold deployment with nothing delivered and nothing
    seeded: which screens exist is a property of what was deployed, and a
    console that could not open its queue until an estate was configured would
    be broken for exactly the person setting one up."""
    from tests import world as test_world

    test_world.fresh()
    body = client.get(ROUTE).json()
    assert body["flows"]
    assert body["shared"]


def test_it_inherits_the_console_door():
    """One spelling of the no-keys rule. A machine has no screens, and a
    second dependency here would be a second, quieter copy of it."""
    depends = inspect.signature(ui_api.task_screens).parameters["principal"].default
    assert depends.dependency is ui_api.get_ui_principal

    machine = Principal(
        uid="svc-1", email="svc@machine", role="owner", key_id="k_1",
        scope="platform",
    )
    with pytest.raises(HTTPException) as refused:
        ui_api.get_ui_principal(machine)
    assert refused.value.status_code == 403
