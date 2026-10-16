---
id: labor-hour-formulas
path: operations/standards/labor-hours
title: Labor Hour Formulas
authority_level: standard
owner: plant-operations
tags: plant, labor-hours, formulas, rates, scheduling
related: plant-scheduling-parameters, truss-standards
status: published
---
# Labor Hour Formulas

What a job costs each department, in hours, from what its shipping list says.

Every rate, divisor, threshold and add-on this plant uses is on this page. The
code that computes an hour holds the **shape** of the arithmetic and not one of
these numbers: it is handed them as parameters, every run records the revision of
this page it read, and a schedule row can therefore say *why* it is 27.4 hours by
naming a formula and a revision rather than by pointing at a stack trace.

That is not a claim you have to take on faith. There is a test that edits this
page, re-runs a calculation and asserts the computed number moved — so a rate
that quietly got hard-coded somewhere would fail the build rather than the demo.

**A number that is wrong here is one edit away from being right.** A number that
is wrong in code is a deploy away, and the four departments whose figures are
still owed would be sitting behind that deploy.

## How these formulas are used

Each department gets its hours from its own section below, computed from the
page families of the shipping list that feed it (see the **Plant Scheduling
Parameters**' page-family table). A department whose page family is absent gets
no hours and is skipped — that department only, never the whole job.

A computed figure outside its department's expected band goes in front of a
supervisor before it is written. The band is in the scheduling parameters, not
here, because it is a question about a *day* rather than about arithmetic.

- computed hours rounding: 2 decimals

Rounding is stated once and applies to every result below unless a section says
otherwise (Stack Out does).

## Components

Hours come from a quantity-range lookup rather than a per-item rate: the setup
dominates a small pick and the marginal item dominates a large one.

| Items up to | Hours |
|---|---|
| 10 | 0.5 |
| 25 | 1.0 |
| 50 | 1.8 |
| 100 | 3.0 |
| 200 | 5.0 |

- components beyond the table, base hours: 5.0
- components beyond the table, hours per extra item: 0.02

The first row whose ceiling the count does not exceed wins. Past the last row the
two lines above take over, so 260 items is `5.0 + 60 × 0.02 = 6.2` hours.

The source design says only *"quantity-range lookup table"* and quotes none of
it. **The five bands and both overflow figures are frozen here** on the shape the
sentence implies — steps, not a slope. Chris Robson owes this department's
minimum and maximum hours as well, so this is a table to expect an early
correction to.

## Laminates

Hours are `quantity × length × rate`, where the rate depends on the ply count and
the lumber size printed in the column's description.

| Ply and size | Hours per foot per column |
|---|---|
| 2-ply 2x6 | 0.006 |
| 3-ply 2x6 | 0.008 |
| 4-ply 2x6 | 0.010 |
| 2-ply 2x8 | 0.007 |
| 3-ply 2x8 | 0.009 |
| 4-ply 2x8 | 0.011 |
| 3-ply 2x10 | 0.010 |
| 3-ply 2x12 | 0.012 |

- laminates perma column base deduction: 4 ft
- laminates perma column attach hours: 0.15
- laminates precut items scheduled in: truss-cut
- laminates precut cut hours per item: 0.25

**Perma Columns** are shorter than the column they replace by a fixed base, so
four feet come off each one's length before the rate is applied, and the attach
hours above go back on per column. Subtracting the base and forgetting the attach
would under-count a Perma Column job; the design names both halves.

**Precut items are not Laminates hours at all.** A precut item is cut in Truss
Cutting and its cut hours are charged there, at the figure above. The sequencing
rule that follows from it lives in the scheduling parameters: the precut must be
cut before the laminates date, and it rolls to the next business day if the saw
is full but still has to finish first.

### Spliced, and larger than 2x6

Which capacity row Laminates uses for the day depends on the mix, and this is how
the mix is identified. The lumber size is in the column's description.

- laminates larger-than-2x6 sizes: 2x8, 2x10, 2x12
- laminates spliced when 2x6 longer than: 24 ft
- laminates spliced when 2x8, 2x10 or 2x12 longer than: 20 ft

A job with any spliced column, or any column larger than a 2x6, puts the day on
the `spliced-or-over-2x6` capacity row; otherwise it is `solid-2x6`. Both
thresholds and the size list are the design's own numbers.

## Verticals

A fixed base plus per-item additions.

- verticals base hours: 1.5
- verticals hours per block: 0.02
- verticals hours per ripped plywood sheet: 0.05
- verticals hours per ridge cleat: 0.03

The design names the base and the three additions — *"fixed base + per-item
additions for blocks, ripped plywood, ridge cleats"* — and quotes no figures, so
all four numbers above are **frozen** and should be checked against a real
Verticals day. This is one of the four departments Chris Robson still owes limits
for.

## Stack Out

- stack out hours source: loader list board hours
- stack out board hours rounding: 1 decimal
- stack out plywood and pallet items: not computed

Stack Out is the one department whose hours are not calculated here: the loader
list page already carries pre-calculated **board hours**, and this platform reads
that figure rather than recomputing it from the lumber grid. Reading a number
somebody else already computed is honest; deriving a second one and quietly
preferring it would give the plant two answers to one question.

Rounding is to one decimal, the design's own figure, and overrides the global
rounding above.

Plywood and pallet items *"remain manual"*. They are named on the row and left
out of the computed figure rather than estimated — a supervisor adds them.

## Steel Fab

- steel fab linear foot rate: 0.00073
- steel fab condenstop linear foot rate: 0.002
- steel fab setup hours: 0.75
- steel fab colour surcharge per additional colour: 0.25

Hours are `total linear feet × rate + setup + colour surcharge`. The rate is the
CondenStop rate when the job's steel carries CondenStop and the standard rate
otherwise — nearly three times as much per foot, which is why the flag on page C
is worth confirming and why CondenStop also moves the department's capacity row.

The colour surcharge is charged **per colour beyond the first**: a two-colour roof
adds 0.25 hours, a three-colour roof 0.50. The design names a *"colour
surcharge"* without a figure, so the 0.25 is **frozen**; the two linear-foot rates
and the setup are its own numbers.

Cribs are added on top, by size:

| Crib size | Hours |
|---|---|
| small | 0.3 |
| medium | 0.5 |
| large | 0.8 |

*"Crib hours scaled by size"* is all the design says, so these three are also
**frozen**. A crib is the shipping frame the steel travels in; the note is on
page C.

## Trim Pack

Trim is packed in bundles by weight, and almost everything else is an add-on.

- trim pack bundle weight: 80 lb
- trim pack bundle rounding: up
- trim pack extra bundle for FJ or JJ trim: 1
- trim pack packaging hours per bundle: 0.15
- trim pack packaging rounding: up to 0.1 hours
- trim pack long trim threshold: 196 in

Bundles are `total trim weight ÷ 80 lb`, rounded **up** — a partial bundle is a
bundle somebody still has to band. One extra bundle is added when FJ or JJ trim
is present, because those cannot travel with the rest. Packaging is then
`bundles × 0.15` hours.

*The rounding of the packaging figure is frozen, and it is the one place this
page departs from a literal reading.* The design writes `ceil(bundles × 0.15)`.
Read as a ceiling to the whole hour, three bundles' 0.45 hours would become 1.0 —
more than doubling a real figure and doing it worst at the small end, where most
tickets are. So the ceiling is taken **to a tenth of an hour**: 0.45 stays 0.5.
Somebody who knows how the plant actually books this should confirm which reading
was meant.

The long-trim threshold is 196 inches, which is the design's `16'-4"` in one
unit so nothing has to parse feet and inches.

| Add-on | Condition | Hours |
|---|---|---|
| long UCR trim | any UCR trim longer than the long trim threshold (the outside stiffener board) | +0.2 |
| long OHFO cover | any OHFO jamb or column cover longer than the long trim threshold | +0.2 |
| ridge light | present anywhere on the job | +0.15 per job |
| ridge vent | per vent | +0.1 each |
| house wrap | per roll | +0.1 each |
| precut house wrap | present | +0.3 flat |

- trim pack house wrap cap: 0.5

House wrap is the only capped add-on: 0.1 per roll up to the cap above, so six
rolls and sixteen rolls both add 0.5. Precut house wrap is a flat 0.3 instead —
it is a different operation, not more of the same one. Every figure in the table
is the design's own.

Trim Pack's hours are combined with Hardware Pack's on the same day, one business
day after that job's trim fabrication, per the sequencing table in the scheduling
parameters.

## Slide Door

- slide door end caps per door: 2
- slide door hours per additional door: 0.4
- slide door pull plate hours: 0.1
- slide door non-wick trim repackaging hours: 1.0
- slide door framing grid: excluded
- slide door hours are added to: hardware-trim-pack

The door count is not printed; it is derived. Page F prints a **track end caps**
quantity, and there are two per door, so four end caps is two doors. The base
hours then come off a short ladder:

| Doors | Hours |
|---|---|
| 1 | 0.8 |
| 2 | 1.0 |

Past two doors, each additional door adds the per-door figure above: three doors
is `1.0 + 0.4 = 1.4`, four is 1.8. A pull plate adds 0.1. A non-Wick trim grid
present on the ticket adds 1.0 for repackaging — somebody else's trim has to come
out of somebody else's packaging.

**The slide door framing grid is excluded.** It is on the page and it is not
slide door hardware work; counting it would double-charge whichever department
actually frames the opening.

## Hardware Pack

- hardware pack wings rate per foot: 0.005
- hardware pack setup hours: 0.5
- hardware pack hours per line item: 0.1
- hardware pack concrete mix hours per two bags: 0.1
- hardware pack concrete mix cap above ten bags: 0.5
- hardware pack 12x5 l bracket hours up to 30: 0.1
- hardware pack 12x5 l bracket divisor above 30: 30
- hardware pack 12x5 l bracket hours per divisor: 0.1

The base is a function of the building's wings:
`(wings total width + wings total length) × 0.005 + 0.5` hours. Both wing
dimensions come off the job record — the building and wings screen — not off the
shipping list, which is why a job with no wings still has a base of 0.5.

Then 0.1 hours per line item present, for each of:

| Line item | Hours |
|---|---|
| LP2 / profile vent | +0.1 |
| Simpson hangers | +0.1 |
| poly-film | +0.1 |
| CS20 strapping | +0.1 |
| rebar | +0.1 |
| bird screen | +0.1 |
| sill seal | +0.1 |

The two quantity-driven items have their own rules, both the design's:

- **Concrete mix** adds 0.1 hours per two bags, and is capped at 0.5 hours once
  there are more than ten bags. So eight bags is 0.4; twenty bags is 0.5.
- **12x5 L brackets** are 0.1 hours flat up to thirty; above thirty it is
  `quantity ÷ 30 × 0.1`, so ninety brackets is 0.3.

## Truss Cut, Truss Fab and Nailing

- truss hours lookup: knowledge:truss-standards
- truss nailing applies when: any truss is multi-ply
- truss girts hours source: truss-standards

The three truss operations are not computed from a rate on this page. Each truss's
cut, fabrication and nailing hours are looked up by **span and member count** in
the **Truss Standards** page, and the job's hours are the sum over its trusses.
That table is governed knowledge for the same reason these rates are: it used to
be a spreadsheet read fresh on every run, and a run whose numbers came from an
unversioned spreadsheet could not say which numbers it used.

Nailing is charged only where a truss is multi-ply, and it becomes its own row on
the day rather than being folded into fabrication — the row-shaping rules are in
the scheduling parameters. Girts plated in are the same shape: a separate row,
with per-truss hours from the standards page.

Precut items cut here on Laminates' behalf carry the precut cut hours from the
Laminates section above.

## Loading

Loading hours follow the widest truss on the truck, because that is what decides
how the load is built.

| Largest truss width up to (ft) | Hours |
|---|---|
| 24 | 2.0 |
| 36 | 2.5 |
| 48 | 3.0 |
| 60 | 3.5 |
| 72 | 4.0 |

- loading beyond the table, hours: 4.5
- loading roller trailer override hours: 4.5
- loading hours per material grid: 0.1

A **roller trailer overrides the table entirely** at 4.5 hours, whatever the
truss width — the design's own rule, and the only override on this page. Each
material grid on the ticket adds 0.1 hours on top.

The width bands and the 2.0-to-4.0 ladder are **frozen**: the design says
*"largest truss width mapped to rule-sheet hours"* and the rule sheet is not in
our hands. The roller trailer figure is its own, and the two agreeing at 4.5 is
why the overflow row was set there.

## What is not computed

Three things are deliberately left off a computed row, and each is named on the
row instead so that a supervisor can see the gap rather than inherit a guess:

- Stack Out's plywood and pallet items.
- The trailer number on a Loading row.
- Anything on a page family that is absent — which is a skipped department, not a
  zero. A zero would read as "no work"; a skip reads as "we were not told".
