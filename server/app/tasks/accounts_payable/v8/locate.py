"""`locate-fields` — a second, narrow look at the page, for the doubts only.

The extraction pass reads the whole document and reports *what* it found; this
one is handed a short list of field paths and reports only *where* they are. It
exists because a clerk reviewing an invoice on a screen is doing one thing —
comparing a number in a box against the same number on the page — and a box
drawn on the page is what turns that from a hunt into a glance.

Three properties hold it in shape:

- **It is skipped entirely when nothing is in doubt.** No boxes, no call, no
  cost. A clean invoice makes exactly one model call, and that is asserted
  rather than hoped for.
- **It receives only the disputed paths.** Not the whole extraction, not the
  whole field list. The prompt is a handful of names, so the answer is a
  handful of rectangles and the call is short.
- **Nothing it returns is trusted.** A model asked for four boxes can return a
  fifth for a field nobody asked about, or coordinates off the page, and either
  one would be drawn on a reviewer's screen as though something had been
  located there. `accept` is the gate, and what it refuses is counted in the
  journal rather than dropped in silence.

Its own model, and deliberately not the deployment's one generation model: this
is a geometry question over a page, asked separately so its cost shows up in
the ledger as its own line and can be turned off or swapped without touching
the extraction that a posting decision actually rests on.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Protocol

from pydantic import BaseModel, Field

from .... import media, modelcall
from ....documents import Region
from ....modelcall import GovernedModelClient

log = logging.getLogger("cumulus.payables")

#: What this ModelCall is *for*, on its ledger row. See `EXTRACT_PURPOSE`.
LOCATE_PURPOSE = "locate-fields"

#: The governed instruction, and the fallback for a corpus that has not seeded.
LOCATE_PROMPT_ASSET_ID = "invoice-locate-prompt"
DEFAULT_LOCATE_PROMPT = """\
You are given a document and a list of field paths that a reviewer needs to see
on the page. For each path in the list, find where that value is printed and
return its bounding box. Respond with only a JSON object of the form
{"regions": [{"field": "<the path, copied exactly>", "page": 1, "x": 0.0,
"y": 0.0, "width": 0.0, "height": 0.0, "text": "<the text in the box>",
"confidence": 0.0}]}. Coordinates are fractions of the page, from 0 to 1, with
the origin at the top-left. Return a box only for the paths you were given, and
only when you can actually see the value on the page — omit a path you cannot
find rather than guessing at a location for it."""


class LocatedFields(BaseModel):
    """What the locate call produced, in the shape a metered step reports.

    `model` and the two token counts are named exactly as the extraction's are,
    so the same `meter=` hook prices both — one accounting for every ModelCall
    this flow makes, rather than one per call site.
    """

    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    regions: list[Region] = Field(default_factory=list)
    #: Boxes the guard threw away, and why. Kept because a model that keeps
    #: answering about fields nobody asked for is a prompt problem somebody has
    #: to be able to see.
    refused: list[str] = Field(default_factory=list)


class FieldLocator(Protocol):
    def locate(
        self,
        image: bytes | None,
        filename: str,
        paths: list[str],
        *,
        prompt: str = "",
        media_type: str = "",
    ) -> LocatedFields:
        """Boxes for exactly these paths. `prompt` is the governed instruction."""
        ...


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------

#: Coordinates are fractions of a page, and a box that runs off the edge is a
#: box a viewer would draw outside the frame. A hair of slack, because a model
#: reporting a full-width field as `x=0, width=1.0000001` is right about the
#: page and wrong about floating point.
_EPSILON = 0.002


def accept(
    regions: list[Region], requested: list[str], pages: int = 0
) -> tuple[list[Region], list[str]]:
    """The boxes worth drawing, and one sentence per box thrown away.

    Three refusals, and each one has been a real bug in something:

    - a box for a path nobody asked about — the model answering a question it
      was not asked, which on a screen looks exactly like a located field;
    - coordinates outside the page — drawn over the toolbar, or invisible;
    - a second box for a path that already has one — two rectangles on one
      number, with no way for a viewer to know which the model meant. The
      more confident of the two wins, ties going to the first.
    """
    wanted = list(dict.fromkeys(requested))
    kept: dict[str, Region] = {}
    refused: list[str] = []
    for region in regions:
        if region.field not in wanted:
            refused.append(
                f"`{region.field}`: a box for a field this call did not ask about"
            )
            continue
        if not _on_the_page(region):
            refused.append(
                f"`{region.field}`: box at "
                f"({region.x:.3f}, {region.y:.3f}) {region.width:.3f}×"
                f"{region.height:.3f} is not inside the page"
            )
            continue
        if pages and not (1 <= region.page <= pages):
            refused.append(
                f"`{region.field}`: page {region.page} of a {pages}-page document"
            )
            continue
        held = kept.get(region.field)
        if held is not None:
            if region.confidence > held.confidence:
                kept[region.field] = region
            refused.append(
                f"`{region.field}`: a second box, kept the more confident one"
            )
            continue
        kept[region.field] = region
    # In the order they were asked for, so the journal and the screen read the
    # same way down the page rather than in whatever order the model answered.
    return [kept[path] for path in wanted if path in kept], refused


def _on_the_page(region: Region) -> bool:
    return (
        0.0 - _EPSILON <= region.x
        and 0.0 - _EPSILON <= region.y
        and region.width > 0.0
        and region.height > 0.0
        and region.x + region.width <= 1.0 + _EPSILON
        and region.y + region.height <= 1.0 + _EPSILON
    )


# ---------------------------------------------------------------------------
# The locators
# ---------------------------------------------------------------------------


class StubFieldLocator:
    """Deterministic geometry for local dev: no credential, no network.

    It invents boxes, and that is exactly what a stub is for — under
    `MODEL_BACKEND=none` or `stub` the whole review screen, boxes included, runs
    on a laptop with no Vertex project. What keeps it out of a deployment is the
    same thing that keeps the stub extractor out: `build_field_locator` refuses
    to reach this class for any backend nobody chose on purpose.

    The geometry is derived from the path so a given field lands in the same
    place every run — a box that moved between two runs of the same document
    would look like the model had changed its mind.
    """

    name = "stub"

    def locate(
        self,
        image: bytes | None,
        filename: str,
        paths: list[str],
        *,
        prompt: str = "",
        media_type: str = "",
    ) -> LocatedFields:
        regions: list[Region] = []
        for path in paths:
            digest = hashlib.sha256(path.encode()).digest()
            regions.append(Region(
                field=path, page=1,
                x=round(0.08 + (digest[0] % 55) / 100, 3),
                y=round(0.06 + (digest[1] % 80) / 100, 3),
                width=0.24, height=0.035,
                # No text: the stub did not read the page, and putting the
                # extracted value here would dress an invention up as a
                # reading of the document.
                text="", confidence=0.5,
            ))
        return LocatedFields(model="stub-locator", regions=regions)


class VertexFieldLocator:
    """The real second pass, through the governed model client.

    Holds no client of its own, for the reason in `modelcall`: the one thing
    allowed to call a model is the thing that meters and ledgers the call, and
    a fifth hand-rolled client would be a fifth chance for a call to escape it.
    """

    name = "vertex"

    def __init__(self, model: str, client: GovernedModelClient) -> None:
        self._client = client
        self._model = model

    def locate(
        self,
        image: bytes | None,
        filename: str,
        paths: list[str],
        *,
        prompt: str = "",
        media_type: str = "",
    ) -> LocatedFields:
        from google.genai import types

        parts: list = []
        if image:
            mime = media.require_model_readable(
                media_type or media.sniff(image, filename), filename
            )
            parts.append(types.Part.from_bytes(data=image, mime_type=mime))
        parts.append(types.Part(text=(
            (prompt or DEFAULT_LOCATE_PROMPT)
            + "\n\nField paths:\n"
            + "\n".join(f"- {path}" for path in paths)
        )))
        response, usage = self._client.generate(
            model=self._model,
            contents=[types.Content(role="user", parts=parts)],
            config=types.GenerateContentConfig(
                response_mime_type="application/json", temperature=0.0
            ),
            purpose=LOCATE_PURPOSE,
        )
        data = json.loads(response.text or "{}")
        regions: list[Region] = []
        for item in data.get("regions") or []:
            try:
                regions.append(Region(
                    field=str(item.get("field", "") or ""),
                    page=int(item.get("page", 1) or 1),
                    x=float(item.get("x", 0) or 0),
                    y=float(item.get("y", 0) or 0),
                    width=float(item.get("width", 0) or 0),
                    height=float(item.get("height", 0) or 0),
                    text=str(item.get("text", "") or ""),
                    confidence=float(item.get("confidence", 0) or 0),
                ))
            except (TypeError, ValueError):
                # A box whose numbers will not parse is one box lost, not a
                # failed step: the other three still put a reviewer on the
                # right part of the page.
                log.warning("Unreadable region from the locate call: %r", item)
        return LocatedFields(
            model=self._model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            regions=regions,
        )


def build_field_locator(
    backend: str,
    project: str | None,
    location: str,
    model: str,
    *,
    client: GovernedModelClient | None = None,
) -> FieldLocator:
    """`client` is the deployment's governed client; None builds a private one.

    Refuses an unrecognised backend rather than quietly handing back the stub,
    for `build_invoice_reader`'s reason: invented geometry on a real invoice is
    a claim that a model looked at the page and found the number there.
    """
    if backend == "vertex":
        return VertexFieldLocator(
            model, client or modelcall.vertex_client(project, location)
        )
    modelcall.require_stub_opt_in(backend, "field locator")
    return StubFieldLocator()
