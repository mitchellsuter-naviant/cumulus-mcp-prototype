"""The extractor, and the deterministic stand-in that lets the suite run.

Two readers behind one Protocol, exactly as accounts payable has: a governed
multimodal call on Vertex, and a stub that fabricates a reading from a case
written on the page. `build_shipping_list_reader` refuses an unrecognised
backend rather than quietly handing back the stub — `require_stub_opt_in`, and
the reason is `build_invoice_reader`'s: invented grids on a real ticket would be
a plant schedule built out of a hash of a filename while the deployment
reported healthy.

**The stub is the test adapter and not the flow's path.** The flow declares the
real `ModelCall`: one metered step, the governed prompt in its read-set, the
purpose on its ledger row. What the stub changes is only what comes back, which
is the same relationship a real extractor has to a real ticket — and it is what
makes the whole suite run with no network and no credential.

**Where a case is written.** On the page, as one marked line, or in the
filename. Both are what a stub extractor has always read here, and under a real
extractor both are inert: the document is what steers it. The grammar is below,
in one place, so the boundary is greppable.
"""

from __future__ import annotations

import json
import logging
import re
from typing import ClassVar

from pydantic import BaseModel, Field

from ... import media, modelcall
from ...modelcall import GovernedModelClient
from . import paths as vocab
from .domain import (
    EXTRACT_PURPOSE,
    A1Trusses,
    BComponents,
    BLaminates,
    BNotes,
    BTrussGrid,
    BVerticals,
    CSteelFab,
    ColourRow,
    ComponentRow,
    CribRow,
    DHardware,
    ETrim,
    FSlideDoor,
    HardwareRow,
    LLLoaderList,
    LaminateRow,
    ShippingListExtraction,
    TrimRow,
    TrussRow,
)

log = logging.getLogger("cumulus.shipping_list")

#: The marker a composed fixture prints on its page, so a stub reading is
#: something the fixture *wrote* rather than something a filename implied.
FIXTURE_MARKER = "CUMULUS-SHIPPING-LIST:"

#: What the flow sends when the corpus holds no extraction prompt. A fallback
#: for a fresh deployment, not for a bad edit: an edit, however unwise, is the
#: customer's and runs as written, with the revision in the journal saying so.
DEFAULT_EXTRACT_PROMPT = """\
You are reading one job's shipping list from a post-frame building plant. It is
a multi-page PDF with seven page families labelled A1, B, C, D, E, F and LL.
Read every page family that is present and report what it says as JSON, with a
`pages` map of the labels you found, one object per page family, and a
`field_confidence` object whose keys are the full dotted paths of the values you
reported and whose values are your confidence 0-1 in that one value. A page
family that is not in the document gets "present": false and no rows. Report
values as printed; convert page E's feet-and-inches lengths to inches. NEVER
calculate labor hours, bundle counts, door counts or a schedule. Respond with
only a JSON object."""


# ---------------------------------------------------------------------------
# The case grammar — the stub's only input, and quarantined here
# ---------------------------------------------------------------------------


class ShippingListScenario(BaseModel):
    """A ticket a test wants to exist, in one line.

    Every field has a default that produces an ordinary post-frame job: seven
    page families present, thirty forty-foot trusses, a steel roof in two
    colours, a hardware grid, a trim bundle, a slide door and a loader list —
    with every department's computed hours inside its governed band. A case
    then says only what it is *about*.
    """

    pages: list[str] = Field(
        default_factory=lambda: ["A1", "B", "C", "D", "E", "F", "LL"]
    )
    truss_quantity: float = 30
    truss_span_ft: float = 40
    truss_members: float = 9
    truss_ply: float = 1
    components_total: float = 40
    components_rows: float = 40
    laminate_quantity: float = 12
    laminate_length_ft: float = 20
    laminate_ply: float = 3
    laminate_size: str = "2x6"
    laminate_precut: float = 0
    blocks: float = 40
    plywood_sheets: float = 6
    ridge_cleats: float = 10
    steel_linear_feet: float = 12000
    steel_colours: int = 2
    #: Linear feet taken off the **last** colour row, so the colour grid no
    #: longer adds up to the printed total — a misread grid line, which is what
    #: the governed footage tolerance exists to catch.
    steel_grid_gap: float = 0
    condenstop: bool = False
    cribs: float = 2
    crib_size: str = "medium"
    hardware_items: list[str] = Field(
        default_factory=lambda: ["Simpson hangers, galvanized"]
    )
    concrete_bags: float = 8
    brackets: float = 90
    trim_weight_lb: float = 200
    trim_rows_weight: float = 200
    fj_trim: bool = False
    ridge_vents: float = 0
    house_wrap_rolls: float = 0
    end_caps: float = 4
    pull_plate: bool = False
    board_hours: float = 6.3
    truss_width_ft: float = 40
    roller_trailer: bool = False
    girts: bool = False
    confidence: float = 0.94
    #: `path` or `path:score` — the fields a case wants a supervisor asked
    #: about, at a score below the governed floor.
    unsure: dict[str, float] = Field(default_factory=dict)
    #: Paths a case wants *absent*, to exercise the required-field rule.
    missing: list[str] = Field(default_factory=list)
    #: What every other field's confidence is. High, because the interesting
    #: cases are the ones a case names.
    read_confidence: float = 0.95

    #: Short spellings a case may use for a field with a long name. Named
    #: rather than guessed: an unrecognised token is *ignored*, so a typo in a
    #: case would silently produce an ordinary ticket and the test asserting
    #: something about it would fail with no clue why.
    ALIASES: ClassVar[dict[str, str]] = {
        "steel_lf": "steel_linear_feet",
        "colours": "steel_colours",
        "weight": "trim_weight_lb",
        "rows_weight": "trim_rows_weight",
        "caps": "end_caps",
        "bags": "concrete_bags",
        "width": "truss_width_ft",
        "trusses": "truss_quantity",
        "span": "truss_span_ft",
        "members": "truss_members",
        "ply": "truss_ply",
    }

    @classmethod
    def parse(cls, text: str) -> "ShippingListScenario":
        """Read a case out of whatever string carried it.

        Tolerant and total: an unrecognised token is ignored, because the same
        string is a filename in production and nothing in it should be able to
        change a reading.
        """
        scenario = cls()
        for raw_token, raw in re.findall(r"([a-z_]+)=([^\s]+)", text or ""):
            token = cls.ALIASES.get(raw_token, raw_token)
            value = raw.strip().strip(",")
            if token == "pages":
                scenario.pages = [
                    part.strip().upper() for part in value.split(",") if part.strip()
                ]
            elif token == "unsure":
                for part in value.split(","):
                    path, _, score = part.partition(":")
                    if path:
                        scenario.unsure[path] = float(score) if score else 0.40
            elif token == "missing":
                scenario.missing = [
                    part for part in value.split(",") if part
                ]
            elif token in ("hardware", "hardware_items"):
                scenario.hardware_items = [
                    part.replace("_", " ") for part in value.split(",") if part
                ]
            elif token in ("crib_size", "laminate_size"):
                setattr(scenario, token, value)
            elif token in cls.model_fields:
                field = cls.model_fields[token]
                if field.annotation is bool:
                    setattr(scenario, token, value.lower() not in ("0", "no", "false"))
                elif field.annotation is int:
                    setattr(scenario, token, int(float(value)))
                elif field.annotation is float:
                    setattr(scenario, token, float(value))
        for flag in ("condenstop", "roller_trailer", "girts", "fj_trim",
                     "pull_plate"):
            if re.search(rf"\b{flag}\b", text or ""):
                setattr(scenario, flag, True)
        if re.search(r"\bno_pages\b", text or ""):
            scenario.pages = []
        return scenario


def fixture_page(case: str, pages: int = 7) -> bytes:
    """A minimal PDF for a composed case, with the case printed on it.

    A real PDF header and trailer so the document store sniffs
    `application/pdf`, and one `%PDF` page marker per page family so the
    locator's page guard has a document with a plausible length. There is no
    rendered shipping list in it: what a fixture needs is an artifact with an
    identity and a content hash, and the case is the one line of text on it.
    """
    body = "\n1 0 obj\n<< /Type /Page >>\nendobj" * max(pages, 0)
    return (
        f"%PDF-1.4\n% {FIXTURE_MARKER} {case.strip()}{body}\n%%EOF\n".encode()
    )


def written_case(image: bytes | None) -> str:
    """The case written on this artifact, or "" — which every real page is.

    Deliberately total and deliberately narrow: the marker has to be in the
    bytes, on its own line, and what follows it to the end of that line is the
    case. Anything else is a document, and a document steers a stub extractor
    only by its filename.
    """
    if not image:
        return ""
    try:
        text = image.decode("utf-8", "ignore")
    except Exception:  # noqa: BLE001 — bytes that will not decode are a page
        return ""
    marker = text.find(FIXTURE_MARKER)
    if marker < 0:
        return ""
    lines = text[marker + len(FIXTURE_MARKER):].splitlines()
    return lines[0].strip() if lines else ""


# ---------------------------------------------------------------------------
# The stub
# ---------------------------------------------------------------------------


class StubShippingListReader:
    """Deterministic local-dev extractor: no credential, no network.

    Steerable by a `ShippingListScenario`, which is what the case string parses
    to and where the grammar is written down. What a case leaves unsaid it
    fills in with an ordinary post-frame job, so the same case reads the same
    way every run — a grid that moved between two runs of one document would
    look like the model had changed its mind.
    """

    name = "stub"

    def read(
        self,
        image: bytes | None,
        filename: str,
        *,
        prompt: str = "",
        media_type: str = "",
    ) -> ShippingListExtraction:
        written = written_case(image)
        scenario = ShippingListScenario.parse(f"{filename} {written}")
        job_number = _job_number(filename) or _job_number(written)
        reading = _compose(scenario, job_number)
        return _scored(reading, scenario)


def _job_number(text: str) -> str:
    match = re.search(r"\b(\d{6})\b", text or "")
    return match.group(1) if match else ""


def _compose(
    scenario: ShippingListScenario, job_number: str
) -> ShippingListExtraction:
    """One ordinary ticket, bent by whatever the case said."""
    pages = {page: page in scenario.pages
             for page in vocab.FAMILIES_BY_PAGE}
    reading = ShippingListExtraction(
        model="stub-shipping-list",
        job_number=job_number,
        confidence=scenario.confidence,
        pages=pages,
    )
    if pages.get("A1"):
        # The member count the truss standards look up is the sum of three
        # printed columns, so the case's single figure is split across them the
        # way a drawing prints it: two chords and the rest webs.
        webs = max(scenario.truss_members - 4, 0)
        reading.a1_trusses = A1Trusses(present=True, rows=[TrussRow(
            mark="T1", quantity=scenario.truss_quantity,
            span_ft=scenario.truss_span_ft, ply=scenario.truss_ply,
            top_chord_members=2, bottom_chord_members=2, web_members=webs,
        )])
    if pages.get("B"):
        reading.b_components = BComponents(
            present=True, total_quantity=scenario.components_total,
            rows=[ComponentRow(description="Component pick",
                               quantity=scenario.components_rows)],
        )
        rows = [LaminateRow(
            description=f"{scenario.laminate_ply:g}-ply {scenario.laminate_size} column",
            lumber_size=scenario.laminate_size,
            length_ft=scenario.laminate_length_ft,
            ply=scenario.laminate_ply,
            quantity=scenario.laminate_quantity,
        )]
        if scenario.laminate_precut:
            rows.append(LaminateRow(
                description="Precut column", lumber_size=scenario.laminate_size,
                length_ft=scenario.laminate_length_ft, ply=scenario.laminate_ply,
                quantity=scenario.laminate_precut, precut=True,
            ))
        reading.b_laminates = BLaminates(present=True, rows=rows)
        reading.b_verticals = BVerticals(
            present=True, blocks_quantity=scenario.blocks,
            ripped_plywood_sheets=scenario.plywood_sheets,
            ridge_cleat_quantity=scenario.ridge_cleats,
        )
        reading.b_truss_grid = BTrussGrid(present=True)
        reading.b_notes = BNotes(
            girts_plated_in=scenario.girts,
            text="Trusses w/Girts Plated In" if scenario.girts else "",
        )
    if pages.get("C"):
        share = (
            scenario.steel_linear_feet / scenario.steel_colours
            if scenario.steel_colours else scenario.steel_linear_feet
        )
        reading.c_steel_fab = CSteelFab(
            present=True, total_linear_feet=scenario.steel_linear_feet,
            condenstop=scenario.condenstop,
            colours=_colour_rows(scenario, share),
            cribs=(
                [CribRow(size=scenario.crib_size, quantity=scenario.cribs)]
                if scenario.cribs else []
            ),
        )
    if pages.get("D"):
        reading.d_hardware = DHardware(
            present=True,
            rows=[
                HardwareRow(description=item, quantity=1)
                for item in scenario.hardware_items
            ],
            concrete_mix_bags=scenario.concrete_bags,
            l_bracket_12x5_quantity=scenario.brackets,
        )
    if pages.get("E"):
        reading.e_trim = ETrim(
            present=True, total_weight_lb=scenario.trim_weight_lb,
            fj_trim=scenario.fj_trim, ridge_vent_quantity=scenario.ridge_vents,
            house_wrap_rolls=scenario.house_wrap_rolls,
            rows=[TrimRow(description="Eave trim", length_in=144, quantity=12,
                          weight_lb=scenario.trim_rows_weight)],
        )
    if pages.get("F"):
        reading.f_slide_door = FSlideDoor(
            present=True, track_end_caps_quantity=scenario.end_caps,
            pull_plate=scenario.pull_plate,
        )
    if pages.get("LL"):
        reading.ll_loader_list = LLLoaderList(
            present=True, board_hours=scenario.board_hours,
            lumber_grid_present=True,
            largest_truss_width_ft=scenario.truss_width_ft,
            roller_trailer=scenario.roller_trailer,
        )
    return reading


def _colour_rows(scenario: ShippingListScenario, share: float) -> list[ColourRow]:
    """The colour grid, with the case's gap taken off the last row."""
    names = ("Rustic Red", "Brite White", "Charcoal")[
        :max(scenario.steel_colours, 0)
    ]
    rows = [ColourRow(colour=name, linear_feet=share) for name in names]
    if rows and scenario.steel_grid_gap:
        rows[-1].linear_feet = share - scenario.steel_grid_gap
    return rows


def _scored(
    reading: ShippingListExtraction, scenario: ShippingListScenario
) -> ShippingListExtraction:
    """A confidence for every path this reading carries, and the case's doubts.

    Generated off `paths_of` rather than written by hand, which is what keeps
    the stub's keys and the flow's vocabulary the same strings: a score under a
    path nothing reported would be dropped by the verdicts, silently.
    """
    dumped = reading.grid()
    for path in scenario.missing:
        vocab.set_at(dumped, path, None)
    reading = ShippingListExtraction.model_validate(dumped)
    scores = {path: scenario.read_confidence for path in vocab.paths_of(reading.grid())}
    scores.update(scenario.unsure)
    return reading.model_copy(update={"field_confidence": scores})


# ---------------------------------------------------------------------------
# The real thing
# ---------------------------------------------------------------------------


class VertexShippingListReader:
    """One metered multimodal pass, through the governed model client.

    Holds no client of its own, for the reason in `modelcall`: the one thing
    allowed to call a model is the thing that meters and ledgers the call, and
    a hand-rolled client here would be one more chance for a call to escape it.
    """

    name = "vertex"

    def __init__(self, model: str, client: GovernedModelClient) -> None:
        self._client = client
        self._model = model

    def read(
        self,
        image: bytes | None,
        filename: str,
        *,
        prompt: str = "",
        media_type: str = "",
    ) -> ShippingListExtraction:
        from google.genai import types

        parts: list = []
        if image:
            mime = media.require_model_readable(
                media_type or media.sniff(image, filename), filename
            )
            parts.append(types.Part.from_bytes(data=image, mime_type=mime))
        parts.append(types.Part(text=prompt or DEFAULT_EXTRACT_PROMPT))
        response, usage = self._client.generate(
            model=self._model,
            contents=[types.Content(role="user", parts=parts)],
            config=types.GenerateContentConfig(
                response_mime_type="application/json", temperature=0.0
            ),
            purpose=EXTRACT_PURPOSE,
        )
        return _parse_response(
            json.loads(response.text or "{}"), self._model, usage
        )


def _parse_response(data: dict, model: str, usage) -> ShippingListExtraction:
    """The model's JSON, validated into the prompt's own shape.

    Field by field through pydantic rather than by trusting the payload: a
    model that answers with a string where a grid expects a number should cost
    one field, not the run — and a key the prompt does not ask for is dropped
    rather than carried into a schedule.
    """
    reading = ShippingListExtraction(
        model=model,
        input_tokens=getattr(usage, "input_tokens", 0),
        output_tokens=getattr(usage, "output_tokens", 0),
        job_number=str(data.get("job_number") or ""),
        confidence=_float(data.get("confidence")),
        pages={
            str(key): bool(value)
            for key, value in (data.get("pages") or {}).items()
        },
        field_confidence={
            str(key): _float(value)
            for key, value in (data.get("field_confidence") or {}).items()
        },
    )
    for family in vocab.FAMILY_LABELS:
        block = data.get(family)
        if not isinstance(block, dict):
            continue
        try:
            setattr(
                reading, family,
                type(getattr(reading, family)).model_validate(block),
            )
        except Exception as exc:  # noqa: BLE001 — one family lost, not the run
            log.warning(
                "Unreadable page family from the extraction call",
                extra={"family": family, "error": str(exc)},
            )
    return reading


def _float(value) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def build_shipping_list_reader(
    backend: str,
    project: str | None,
    location: str,
    model: str,
    *,
    client: GovernedModelClient | None = None,
) -> "StubShippingListReader | VertexShippingListReader":
    """`client` is the deployment's governed client; None builds a private one.

    Refuses an unrecognised backend rather than quietly handing back the stub,
    for `build_invoice_reader`'s reason: a fabricated grid on a real ticket is
    a claim that a model looked at the page and found those numbers there.
    """
    if backend == "vertex":
        return VertexShippingListReader(
            model, client or modelcall.vertex_client(project, location)
        )
    modelcall.require_stub_opt_in(backend, "shipping list reader")
    return StubShippingListReader()
