"""The plant's arithmetic: the shape is code, and not one number is.

Three claims, and the first is the one the whole design rests on.

- **`formulas.py` holds no number.** Proved twice: by walking the module's own
  syntax tree and refusing a numeric literal outside a named allowance, and by
  editing the governed page and watching the computed figure move. A rate that
  quietly got hard-coded somewhere fails the build rather than the demo.
- **The formulas reproduce the governed pages' own worked examples.** Every
  figure the two documents state about themselves — 260 components is 6.2
  hours, thirty forty-foot nine-member trusses are 5.7 cut and 10.5 fab, three
  bundles' 0.45 hours becomes 0.5, ninety brackets are 0.3, twenty bags are
  capped at 0.5, four doors are 1.8, a roller trailer is 4.5 — is asserted
  here. A parser that read a column wrong would pass a shape test and fail
  these.
- **A page that does not carry a line is reported, not computed around.** The
  formula names the governed line it could not find, because a plausible figure
  derived from an invented rate is a schedule nobody can dispute.
"""

import ast
import pathlib

import pytest

from app.models import AssetUpdate
from app.tasks.department_scheduling import formulas
from app.tasks.department_scheduling.parameters import parse_plant_parameters
from tests import world as test_world

#: The literals the module is allowed to hold, and why. `0` is emptiness and an
#: index; `1` is "one" — the English inside *multi*-ply and *beyond the first*.
#: Anything else is a plant parameter wearing a Python literal's clothes.
ALLOWED_INTS = {0, 1}

#: The one exception, by name: the English-numeral table, which exists because
#: the governed page spells a divisor as a word ("per **two** bags"). The words
#: are read off the page, so changing the page still changes the figure — the
#: behavioural test below proves that for the values the table decodes.
NUMERAL_TABLE = "_NUMERALS"


@pytest.fixture()
def governed():
    """The two pages as the corpus holds them, parsed the way a run parses them."""
    truss = formulas.parse_truss_standards(
        _seed("truss-standards.md"), "knowledge:truss-standards@rev1"
    )
    return formulas.parse_labor_parameters(
        _seed("labor-hour-formulas.md"),
        "knowledge:labor-hour-formulas@rev1", truss,
    )


def _seed(name: str) -> str:
    return (pathlib.Path(__file__).parent.parent / "seed" / name).read_text()


def _plant():
    return parse_plant_parameters(
        _seed("plant-scheduling-parameters.md"),
        "knowledge:plant-scheduling-parameters@rev1",
    )


# ---- the claim the module exists for ---------------------------------------


def test_the_arithmetic_module_holds_no_number():
    """Every rate, threshold, band and add-on arrives as a parameter.

    Walked as a syntax tree rather than grepped, so a number inside a
    docstring, an f-string or a comment cannot fail it and a number in an
    expression cannot hide from it.
    """
    source = pathlib.Path(formulas.__file__).read_text()
    tree = ast.parse(source)

    # The lines the numeral table occupies, taken off the tree rather than
    # counted by hand: the allowance is "that one assignment", not "those lines".
    numeral_lines: set[int] = set()
    for node in ast.walk(tree):
        targets = (
            [node.target] if isinstance(node, ast.AnnAssign)
            else list(node.targets) if isinstance(node, ast.Assign) else []
        )
        if any(
            isinstance(target, ast.Name) and target.id == NUMERAL_TABLE
            for target in targets
        ):
            numeral_lines.update(
                range(node.lineno, (node.end_lineno or node.lineno) + 1)
            )
    assert numeral_lines, "the numeral table moved; the allowance has to move with it"

    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant):
            continue
        value = node.value
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if node.lineno in numeral_lines:
            continue
        if isinstance(value, int) and value in ALLOWED_INTS:
            continue
        offenders.append(f"line {node.lineno}: {value!r}")
    assert not offenders, (
        "formulas.py must hold no plant parameter as a literal — found "
        + "; ".join(offenders)
    )


def test_editing_the_governed_page_moves_the_computed_hours():
    """The proof a source scan cannot give: the number follows the document.

    Five departments, five different shapes of constant — a rate, a band's
    hours, a fixed base, a per-item figure and the truss lookup — because a
    module that read one of them from the page and held the rest would pass a
    narrower test.
    """
    world = test_world.fresh()
    plant = parse_plant_parameters(
        world.knowledge.get("plant-scheduling-parameters").content, "r"
    )
    before = _figures(world, plant)

    world.knowledge.update("labor-hour-formulas", AssetUpdate(
        content=world.knowledge.get("labor-hour-formulas").content
        .replace("- steel fab linear foot rate: 0.00073",
                 "- steel fab linear foot rate: 0.002")
        .replace("- verticals base hours: 1.5", "- verticals base hours: 3.0")
        .replace("| 50 | 1.8 |", "| 50 | 2.5 |")
        .replace("- components beyond the table, hours per extra item: 0.02",
                 "- components beyond the table, hours per extra item: 0.05")
        .replace("- trim pack bundle weight: 80 lb",
                 "- trim pack bundle weight: 40 lb"),
        updated_by="plant-operations",
    ))
    world.knowledge.update("truss-standards", AssetUpdate(
        content=world.knowledge.get("truss-standards").content
        .replace("| 40 | 9 | 0.19 | 0.35 | 0.19 |",
                 "| 40 | 9 | 0.25 | 0.50 | 0.25 |"),
        updated_by="plant-operations",
    ))
    after = _figures(world, plant)

    assert before["steel"] != after["steel"]
    assert before["verticals"] == 2.9 and after["verticals"] == 4.4
    assert before["components_band"] == 1.8 and after["components_band"] == 2.5
    assert before["components_overflow"] != after["components_overflow"]
    assert before["trim"] != after["trim"]
    assert before["truss_cut"] == 5.7 and after["truss_cut"] == 7.5
    assert before["truss_fab"] == 10.5 and after["truss_fab"] == 15.0
    # And the basis moved with it: a schedule row cites the revision it was
    # computed from, so two rows produced a week apart are distinguishable.
    assert "@rev1" in before["basis"] and "@rev2" in after["basis"]


def _figures(world, plant) -> dict:
    """One reading, priced against whatever the corpus currently says."""
    params = _read(world)
    grid = {
        "a1_trusses": {"present": True, "rows": [{
            "quantity": 30, "span_ft": 40, "ply": 1, "top_chord_members": 2,
            "bottom_chord_members": 2, "web_members": 5,
        }]},
        "b_components": {"present": True, "total_quantity": 40},
        "b_verticals": {"present": True, "blocks_quantity": 40,
                        "ripped_plywood_sheets": 6, "ridge_cleat_quantity": 10},
        "c_steel_fab": {"present": True, "total_linear_feet": 12000,
                        "colours": [{"linear_feet": 12000}]},
        "e_trim": {"present": True, "total_weight_lb": 200},
    }
    overflow = {"b_components": {"present": True, "total_quantity": 260}}
    return {
        "steel": formulas.steel_fab_hours(grid, params).hours,
        "verticals": formulas.verticals_hours(grid, params).hours,
        "components_band": formulas.components_hours(grid, params).hours,
        "components_overflow": formulas.components_hours(overflow, params).hours,
        "trim": formulas.trim_pack_hours(grid, params).hours,
        "truss_cut": formulas.truss_cut_hours(grid, params).hours,
        "truss_fab": formulas.truss_fab_hours(grid, params).hours,
        "basis": formulas.truss_cut_hours(grid, params).basis,
    }


def _read(world) -> formulas.Parameters:
    truss = world.knowledge.get("truss-standards")
    labor = world.knowledge.get("labor-hour-formulas")
    return formulas.parse_labor_parameters(
        labor.content, f"knowledge:labor-hour-formulas@rev{labor.revision}",
        formulas.parse_truss_standards(
            truss.content, f"knowledge:truss-standards@rev{truss.revision}"
        ),
    )


# ---- the pages' own worked examples ----------------------------------------


def test_every_governed_value_on_the_two_pages_parses(governed):
    """A silent parse failure is the failure mode this whole design has.

    A `None` on `Parameters` reads downstream as *the page does not carry this
    line*, and a formula reports it rather than computing — which is right when
    the page really is missing a line and a lie when the parser simply missed
    one.
    """
    unread = [
        name for name, value in governed.model_dump().items()
        if value is None
    ]
    assert not unread, f"the parser did not find: {unread}"
    assert len(governed.components_bands) == 5
    assert len(governed.laminate_rates) == 8
    assert len(governed.loading_width_bands) == 5
    assert len(governed.slide_door_ladder) == 2
    assert len(governed.trim_addons) == 6
    assert len(governed.hardware_line_items) == 7
    assert len(governed.crib_hours) == 3
    assert len(governed.truss.rows) == 32
    assert governed.truss.girts_hours_per_truss == 0.05
    # The English-numeral divisor, read off the key rather than assumed.
    assert governed.concrete_bags_per_increment == 2.0
    assert governed.concrete_cap_above_bags == 10.0


def test_components_reproduces_the_pages_own_example(governed):
    """*"260 items is 5.0 + 60 × 0.02 = 6.2 hours"* — the page's own sentence."""
    assert formulas.components_hours(
        {"b_components": {"present": True, "total_quantity": 260}}, governed
    ).hours == 6.2
    # And the band table: the first row whose ceiling the count does not exceed.
    for count, hours in ((10, 0.5), (11, 1.0), (25, 1.0), (50, 1.8), (200, 5.0)):
        assert formulas.components_hours(
            {"b_components": {"present": True, "total_quantity": count}}, governed
        ).hours == hours


def test_the_truss_lookup_lands_where_the_standards_page_says(governed):
    """*"thirty 40-foot trusses at nine members lands near 5.7 cut and 10.5 fab"*."""
    grid = {"a1_trusses": {"present": True, "rows": [{
        "quantity": 30, "span_ft": 40, "ply": 1, "top_chord_members": 2,
        "bottom_chord_members": 2, "web_members": 5,
    }]}}
    assert formulas.truss_cut_hours(grid, governed).hours == 5.7
    assert formulas.truss_fab_hours(grid, governed).hours == 10.5
    # Single-ply: nailing is charged only where a truss is multi-ply, and it is
    # a separate row rather than a line folded into fabrication.
    assert formulas.nailing_hours(grid, governed).hours == 0.0
    assert not formulas.nailing_applies(grid)

    multi = {"a1_trusses": {"present": True, "rows": [{
        **grid["a1_trusses"]["rows"][0], "ply": 2,
    }]}}
    assert formulas.nailing_applies(multi)
    assert formulas.nailing_hours(multi, governed).hours == 5.7

    # A 38-foot truss with 11 members takes the `40 / 12` row, which is the
    # page's own worked reading of "up to and including".
    row, beyond = formulas.truss_row_for(38, 11, governed.truss)
    assert (row.span_ft, row.members) == (40.0, 12.0) and not beyond


def test_a_truss_past_the_end_of_the_table_is_flagged_not_refused(governed):
    """*"a 90-foot truss takes the last row and is flagged"*."""
    grid = {"a1_trusses": {"present": True, "rows": [{
        "quantity": 1, "span_ft": 90, "ply": 1, "top_chord_members": 2,
        "bottom_chord_members": 2, "web_members": 5,
    }]}}
    result = formulas.truss_cut_hours(grid, governed)
    assert result.hours == 0.4  # the last row's cut figure
    assert result.flags and "out of range" in result.flags[0]


def test_the_girts_row_is_conditioned_on_the_handwritten_note(governed):
    grid = {
        "a1_trusses": {"present": True, "rows": [{"quantity": 30, "span_ft": 40,
                                                  "ply": 1, "web_members": 5}]},
        "b_notes": {"girts_plated_in": True},
    }
    assert formulas.girts_applies(grid)
    assert formulas.girts_hours(grid, governed).hours == 1.5  # 30 × 0.05
    without = {**grid, "b_notes": {"girts_plated_in": False}}
    assert not formulas.girts_applies(without)
    assert formulas.girts_hours(without, governed).hours == 0.0


def test_trim_bundles_round_up_to_the_governed_tenth(governed):
    """*"three bundles' 0.45 hours stays 0.5"*, and two bundles' 0.3 stays 0.3.

    The second half is the one floating point gets wrong: `ceil(0.3 / 0.1)` is
    4 on binary floats, which would bill a tenth of an hour that nobody works.
    """
    assert formulas.trim_pack_hours(
        {"e_trim": {"present": True, "total_weight_lb": 200}}, governed
    ).hours == 0.5
    assert formulas.trim_pack_hours(
        {"e_trim": {"present": True, "total_weight_lb": 160}}, governed
    ).hours == 0.3
    # A partial bundle is a bundle somebody still has to band.
    assert formulas.trim_pack_hours(
        {"e_trim": {"present": True, "total_weight_lb": 81}}, governed
    ).hours == 0.3
    # FJ trim cannot travel with the rest, so it adds a whole bundle.
    assert formulas.trim_pack_hours(
        {"e_trim": {"present": True, "total_weight_lb": 160, "fj_trim": True}},
        governed,
    ).hours == 0.5


def test_the_capped_and_the_flat_trim_add_ons(governed):
    """*"six rolls and sixteen rolls both add 0.5"*, and precut is 0.3 flat."""
    six = formulas.trim_pack_hours({"e_trim": {
        "present": True, "total_weight_lb": 80, "house_wrap_rolls": 6,
    }}, governed)
    sixteen = formulas.trim_pack_hours({"e_trim": {
        "present": True, "total_weight_lb": 80, "house_wrap_rolls": 16,
    }}, governed)
    # One bundle's 0.15 h rounds up to the governed tenth (0.2), plus the cap.
    assert six.hours == sixteen.hours == 0.7
    precut = formulas.trim_pack_hours({"e_trim": {
        "present": True, "total_weight_lb": 80, "precut_house_wrap": True,
    }}, governed)
    assert precut.hours == 0.5  # 0.2 packaging + 0.3, a different operation


def test_the_long_trim_add_on_reads_its_keyword_off_the_governed_name(governed):
    """A UCR board over 196 inches adds 0.2, and one under it adds nothing.

    The keyword `UCR` comes out of the add-on's own printed name, so renaming
    the add-on on the page renames what a description is tested against.
    """
    long_board = formulas.trim_pack_hours({"e_trim": {
        "present": True, "total_weight_lb": 80,
        "rows": [{"description": "UCR outside stiffener", "length_in": 240}],
    }}, governed)
    short_board = formulas.trim_pack_hours({"e_trim": {
        "present": True, "total_weight_lb": 80,
        "rows": [{"description": "UCR outside stiffener", "length_in": 96}],
    }}, governed)
    assert long_board.hours - short_board.hours == pytest.approx(0.2)


def test_hardware_pack_reproduces_both_quantity_rules(governed):
    """*"eight bags is 0.4; twenty bags is 0.5"* and *"ninety brackets is 0.3"*."""
    def hours(**page) -> float:
        return formulas.hardware_pack_hours(
            {"wings_total_ft": 0, "d_hardware": {"present": True, **page}},
            governed,
        ).hours

    base = hours()
    assert base == 0.5  # a job with no wings still has the setup figure
    assert hours(concrete_mix_bags=8) - base == pytest.approx(0.4)
    assert hours(concrete_mix_bags=20) - base == pytest.approx(0.5)
    assert hours(l_bracket_12x5_quantity=10) - base == pytest.approx(0.1)
    assert hours(l_bracket_12x5_quantity=90) - base == pytest.approx(0.3)
    # Both wing dimensions come off the job record, not the shipping list.
    assert formulas.hardware_pack_hours(
        {"wings_total_ft": 36, "d_hardware": {"present": True}}, governed
    ).hours == 0.68


def test_slide_doors_come_off_the_end_caps_and_then_the_ladder(governed):
    """*"three doors is 1.4, four is 1.8"* — two end caps per door."""
    def hours(caps: float, **page) -> float:
        return formulas.slide_door_hours(
            {"f_slide_door": {"present": True,
                              "track_end_caps_quantity": caps, **page}},
            governed,
        ).hours

    assert (hours(2), hours(4), hours(6), hours(8)) == (0.8, 1.0, 1.4, 1.8)
    assert hours(4, pull_plate=True) == 1.1
    assert hours(4, non_wick_trim_grid=True) == 2.0
    # The framing grid is excluded and *named* rather than silently dropped.
    framed = formulas.slide_door_hours({"f_slide_door": {
        "present": True, "track_end_caps_quantity": 4, "framing_grid": True,
    }}, governed)
    assert framed.hours == 1.0 and framed.flags


def test_steel_takes_the_condenstop_rate_and_the_surcharge_beyond_the_first(governed):
    def hours(**page) -> float:
        return formulas.steel_fab_hours(
            {"c_steel_fab": {"present": True, "total_linear_feet": 10000, **page}},
            governed,
        ).hours

    plain = hours(colours=[{"linear_feet": 10000}])
    assert plain == 8.05  # 10000 × 0.00073 + 0.75
    assert hours(condenstop=True, colours=[{"linear_feet": 10000}]) == 20.75
    two = hours(colours=[{"linear_feet": 5000}, {"linear_feet": 5000}])
    assert two - plain == pytest.approx(0.25)
    three = hours(colours=[{"linear_feet": 4000}] * 3)
    assert three - plain == pytest.approx(0.5)
    assert hours(colours=[{"linear_feet": 10000}],
                 cribs=[{"size": "large", "quantity": 2}]) - plain == pytest.approx(1.6)


def test_loading_is_the_widest_truss_unless_a_roller_trailer_says_otherwise(governed):
    def hours(**page) -> float:
        return formulas.loading_hours(
            {"ll_loader_list": {"present": True, **page}}, governed
        ).hours

    assert hours(largest_truss_width_ft=24) == 2.0
    assert hours(largest_truss_width_ft=48) == 3.0
    assert hours(largest_truss_width_ft=90) == 4.5  # past the table
    # The override is on the *table*; the material grids still add on top.
    assert hours(largest_truss_width_ft=24, roller_trailer=True) == 4.5
    assert hours(largest_truss_width_ft=24, roller_trailer=True,
                 lumber_grid_present=True, plywood_grid_present=True) == 4.7


def test_stack_out_reads_the_loader_lists_own_figure_and_names_the_gap(governed):
    """Read, not recomputed — and rounded to the governed one decimal."""
    result = formulas.stack_out_hours({"ll_loader_list": {
        "present": True, "board_hours": 6.349, "plywood_grid_present": True,
    }}, governed)
    assert result.hours == 6.3
    assert result.flags and "plywood and pallet" in result.flags[0]


def test_laminates_perma_columns_subtract_the_base_and_add_the_attach(governed):
    perma = formulas.laminates_hours({"b_laminates": {"present": True, "rows": [{
        "lumber_size": "2x8", "length_ft": 16, "ply": 3, "quantity": 4,
        "perma_column": True,
    }]}}, governed)
    # (16 − 4) × 4 × 0.009 = 0.432, plus 4 × 0.15 attach = 1.032 → 1.03
    assert perma.hours == 1.03
    plain = formulas.laminates_hours({"b_laminates": {"present": True, "rows": [{
        "lumber_size": "2x8", "length_ft": 16, "ply": 3, "quantity": 4,
    }]}}, governed)
    assert plain.hours == 0.58


def test_precut_items_are_charged_to_truss_cutting_and_not_to_laminates(governed):
    grid = {"b_laminates": {"present": True, "rows": [
        {"lumber_size": "2x6", "length_ft": 20, "ply": 3, "quantity": 12},
        {"lumber_size": "2x6", "length_ft": 10, "ply": 2, "quantity": 6,
         "precut": True},
    ]}}
    assert formulas.laminates_hours(grid, governed).hours == 1.92  # the precut row is out
    precut = formulas.precut_cut_hours(grid, governed)
    assert precut.hours == 1.5 and precut.department == "truss-cut"


def test_the_laminates_mix_names_which_condition_holds(governed):
    over = formulas.laminates_mix({"b_laminates": {"present": True, "rows": [
        {"lumber_size": "2x8", "length_ft": 12, "ply": 3, "quantity": 4},
    ]}}, governed)
    assert over.has_larger_than_2x6 and over.spliced_or_over_2x6 and over.reasons

    spliced = formulas.laminates_mix({"b_laminates": {"present": True, "rows": [
        {"lumber_size": "2x6", "length_ft": 30, "ply": 3, "quantity": 4},
    ]}}, governed)
    assert spliced.has_spliced and not spliced.has_larger_than_2x6

    solid = formulas.laminates_mix({"b_laminates": {"present": True, "rows": [
        {"lumber_size": "2x6", "length_ft": 20, "ply": 3, "quantity": 4},
    ]}}, governed)
    assert not solid.spliced_or_over_2x6 and not solid.missing


def test_loading_consolidates_tickets_that_share_a_truck(governed):
    groups = formulas.loading_row_groups([
        {"job_number": "260451", "hours": 3.0, "combined_with": ""},
        {"job_number": "260470", "hours": 2.5, "combined_with": "260451"},
        {"job_number": "260418", "hours": 4.0, "combined_with": ""},
    ])
    assert [(row["truck_key"], row["consolidated"], row["hours"]) for row in groups] == [
        ("260418", False, 4.0), ("260451", True, 5.5),
    ]
    # And the relation is undirected: whichever ticket names the other, the
    # truck is one row.
    reversed_groups = formulas.loading_row_groups([
        {"job_number": "260451", "hours": 3.0, "combined_with": "260470"},
        {"job_number": "260470", "hours": 2.5, "combined_with": ""},
    ])
    assert len(reversed_groups) == 1 and reversed_groups[0]["consolidated"]


# ---- the incentive ---------------------------------------------------------


def test_the_incentive_is_two_governed_numbers_and_its_own_arithmetic():
    """The bonus cell, stated so a supervisor can dispute it on day one."""
    plant = _plant()
    trim = plant.incentive_for("trim-fab")
    assert (trim.start_usd, trim.rate_usd_per_hour) == (-180.0, 6.2)
    # Computed, never read: the page prints a fourth column so the pair can be
    # checked at a glance, and reading it would be a second answer.
    assert formulas.crosses_zero_at(trim) == pytest.approx(29.032, abs=0.001)
    assert formulas.incentive(29.0, trim) == pytest.approx(-0.2)
    assert "$-180.00 + $6.20/h × 29.0 h" in formulas.incentive_sentence(29.0, trim)
    # Steel Fab's crossing lands on the same 12 hours its capacity row gives as
    # a numeric minimum — the page says that agreement is intentional, and this
    # is the check it invites.
    steel = plant.incentive_for("steel-fab")
    assert formulas.crosses_zero_at(steel) == 12.0
    assert plant.capacity.row_for("steel-fab", "no-condenstop").min_hours == 12.0


def test_a_department_with_no_incentive_row_says_so_rather_than_answering_zero():
    plant = _plant()
    unknown = plant.incentive_for("paint-shop")
    assert formulas.incentive(20.0, unknown) == 0.0
    assert "no incentive figures" in formulas.incentive_sentence(20.0, unknown)


# ---- the honest degrade ----------------------------------------------------


def test_a_page_that_carries_no_rate_is_reported_not_computed_around():
    """A missing governed value names itself; it never becomes a plausible figure."""
    empty = formulas.parse_labor_parameters("", "knowledge:labor-hour-formulas")
    steel = formulas.steel_fab_hours(
        {"c_steel_fab": {"present": True, "total_linear_feet": 12000}}, empty
    )
    assert steel.hours == 0.0
    assert steel.missing == [
        "steel fab linear foot rate", "steel fab setup hours",
    ]
    assert not steel.complete
    assert "not in the corpus" in steel.basis

    trusses = formulas.truss_cut_hours(
        {"a1_trusses": {"present": True, "rows": [{"quantity": 30, "span_ft": 40}]}},
        empty,
    )
    assert trusses.missing == ["the truss standards span/member table"]


def test_a_lumber_size_the_rate_table_does_not_carry_names_the_row(governed):
    result = formulas.laminates_hours({"b_laminates": {"present": True, "rows": [
        {"lumber_size": "2x14", "length_ft": 20, "ply": 3, "quantity": 4},
    ]}}, governed)
    assert result.missing == ["a laminates rate for `3-ply 2x14` (b_laminates.rows[0])"]
