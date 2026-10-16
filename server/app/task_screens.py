"""Which screen a person opens to answer a task — declared by the code that
raises it, resolved here, served to the console.

The console used to hold this whole answer itself: a Dart table naming every
flow, every version, and the screen each one opens, plus its own copy of the
"newest package at or below the task's version" rule. Two problems, one shape.
A console can be shipped believing in a version arrangement the server does not
have — a package registered at v8 against a fleet that never deployed one — and
nothing fails loudly; the task simply opens the wrong screen. And the version
rule existing twice means it can be corrected once.

The server is the side that knows which `v<N>/` packages shipped, so the
version resolution happens here and the console receives the answer already
resolved: one entry per shipped version, plus the entry a version this
deployment never shipped falls back to. What crosses the wire is the *wiring*
and the *copy* — a screen id, a button label, a sentence. The widget itself
cannot cross a wire, so the console keeps a compile-time
``screen_id -> constructor`` map and degrades to a plain approve/reject pane
when a manifest names an id this build does not have.

A version package declares its screens as ``UI``; a version that changed
nothing a person sees declares nothing and is answered by the newest
declaration below it. That is why accounts-payable v7 correctly reads v6's
screens without v7 having to restate them — and restating them is exactly how
two versions of the same screen drift apart.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# What a version package declares
# ---------------------------------------------------------------------------


class TaskScreen(BaseModel):
    """One review screen: what to build, what to call it, what it is for."""

    #: The console's compile-time key for a widget. Names the screen, not the
    #: version that declared it — v7 reusing v6's screen is the same screen,
    #: and an id carrying a version number would make it look like two.
    screen_id: str

    #: The button in the review pane. Names the moment, not the widget.
    label: str

    #: What the full screen is *for*, shown under the button.
    hint: str


class TaskUi(BaseModel):
    """One version's screens: the kinds that get their own, and the rest.

    Both halves are needed because the two shipped shapes are genuinely
    different. accounts-payable v6 splits by kind — its run cannot say which of
    thirty inputs it was unsure of, so the task's kind carries whether the
    reading or the comparison is the question. v8's run answers that per field,
    so it declares one screen for everything and lets the field verdicts choose
    the emphasis; a kind-keyed split there would be the coarser signal arguing
    with the finer one.
    """

    by_kind: dict[str, TaskScreen] = Field(default_factory=dict)

    #: The screen for every kind `by_kind` does not name. Null means this
    #: version answers only the kinds it listed, and anything else gets the
    #: generic pane rather than a screen built for a different question.
    otherwise: TaskScreen | None = None

    def screen_for(self, kind: str) -> TaskScreen | None:
        return self.by_kind.get(kind) or self.otherwise


# ---------------------------------------------------------------------------
# The hints shared across packages
# ---------------------------------------------------------------------------

#: Most of these screens exist so a person can correct the facts. Two of them
#: have no facts to correct, which is why this is not one sentence for all.
CORRECT_THE_FACTS = (
    "Opens the full screen, where the facts themselves can be corrected — "
    "the checks then run again over what you changed."
)

_MERGE_HINT = (
    "Opens the two records side by side, with every signal the score was "
    "blended from and the governed policy behind the weights. Nothing "
    "merges on either verdict."
)

_DISAGREEMENT_HINT = (
    "Opens what each system says, the evidence that correlated them, and "
    "exactly what releasing would change in the ERP — and what it would "
    "leave alone."
)


# ---------------------------------------------------------------------------
# The screens that belong to no flow
# ---------------------------------------------------------------------------

#: Keyed by kind, and answered before any flow's declaration.
#:
#: A `match_review` is one arriving row against the book; a `merge_review` is
#: two records the book already holds; a `state_disagreement` is two of the
#: customer's own systems holding one commitment. Any flow's resolver can raise
#: any of them and what a steward reads is the same question every time, so
#: keying these by flow would mean the same three screens declared in every
#: package that can raise them — and corrected in only some of them.
#:
#: `merge_review` and `state_disagreement` are here rather than on the generic
#: pane because that pane offered an Approve button whose entire account of
#: itself was the word "Approve", over a decision that writes to a customer's
#: ERP and cannot be undone.
SHARED_SCREENS: dict[str, TaskScreen] = {
    "match_review": TaskScreen(
        screen_id="shared/entity-match-review",
        label="Open the match review",
        hint=CORRECT_THE_FACTS,
    ),
    "merge_review": TaskScreen(
        screen_id="shared/merge-review",
        label="Open the merge review",
        hint=_MERGE_HINT,
    ),
    "state_disagreement": TaskScreen(
        screen_id="shared/disagreement-review",
        label="Open the disagreement",
        hint=_DISAGREEMENT_HINT,
    ),
}


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def declared_ui(flow_id: str) -> dict[int, TaskUi]:
    """Version number → the UI that version declares, for one flow.

    Only the versions that declare one: a package with no ``UI`` is a version
    that changed nothing a person sees, and listing it with an empty
    declaration would make it *shadow* the older screens it means to keep.
    """
    from .tasks import shipped_versions

    found: dict[int, TaskUi] = {}
    for number, module in shipped_versions(flow_id).items():
        ui = getattr(module, "UI", None)
        if isinstance(ui, TaskUi):
            found[number] = ui
    return found


def resolve_ui(flow_id: str, version: int) -> TaskUi | None:
    """The declaration that answers for a task raised under `version`.

    The newest declaration at or below it, so a version that changed nothing a
    person sees keeps reading the older screens rather than falling off the
    map. Off both ends the answer is the newest declaration: a task carrying no
    version at all (0 — an older record, or a locally built double) and one
    older than anything still shipped are both better served by a screen than
    by none.
    """
    declared = declared_ui(flow_id)
    if not declared:
        return None
    at_or_below = [n for n in sorted(declared) if n <= version]
    return declared[at_or_below[-1]] if at_or_below else declared[max(declared)]


# ---------------------------------------------------------------------------
# The manifest
# ---------------------------------------------------------------------------


class VersionScreens(BaseModel):
    """One shipped version, with its resolution already applied.

    A list rather than a map keyed by version because JSON object keys are
    strings, and a client parsing `"7"` back into an int is a conversion that
    can be got wrong in a place where being wrong means opening the wrong
    screen.
    """

    version: int
    ui: TaskUi


class FlowScreens(BaseModel):
    flow_id: str

    #: Every version this deployment ships, each already resolved — so v7
    #: appears here carrying v6's screens and the console performs no version
    #: arithmetic of its own.
    versions: list[VersionScreens]

    #: What a version this deployment does not ship resolves to: the newest
    #: declaration. Covers a task carrying version 0, and one raised by a
    #: version since retired from the code.
    fallback: TaskUi | None = None


class TaskScreenManifest(BaseModel):
    shared: dict[str, TaskScreen] = Field(default_factory=dict)
    flows: list[FlowScreens] = Field(default_factory=list)


def build_manifest() -> TaskScreenManifest:
    """Every screen the console can be asked to open, and when.

    Flows that declare no UI are left out entirely rather than listed empty:
    an empty entry would read as "this flow has no bespoke screens", which is
    the manifest asserting something about a flow whose packages never spoke.
    """
    from .tasks import shipped_version_numbers, task_flow_ids

    flows: list[FlowScreens] = []
    for flow_id in task_flow_ids():
        declared = declared_ui(flow_id)
        if not declared:
            continue
        flows.append(
            FlowScreens(
                flow_id=flow_id,
                versions=[
                    VersionScreens(version=n, ui=resolve_ui(flow_id, n))
                    for n in sorted(shipped_version_numbers(flow_id))
                ],
                fallback=declared[max(declared)],
            )
        )
    return TaskScreenManifest(shared=dict(SHARED_SCREENS), flows=flows)
