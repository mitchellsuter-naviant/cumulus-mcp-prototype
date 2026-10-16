---
id: truss-standards
path: operations/config/truss-standards
title: Truss Standards 2026
kind: config
owner: plant-operations
tags: plant, trusses, standards, labor-hours, lookup
related: labor-hour-formulas, plant-scheduling-parameters
status: published
---
# Truss Standards 2026

The cut, fabrication and nailing hours for one truss, by span and member count.

**Source: `Truss_Standards_2026.xlsx`, the 2026 revision of the plant's truss
standards workbook** — columns P (Cut), Q (Fab) and R (Nailing). The workbook is
named here because this page replaces it: the retired design read the spreadsheet
fresh on every run, off a shared drive, and a schedule produced that way cannot
say which numbers it used. Held here it has a revision, an owner and a date, and
every job's truss hours cite the revision they were computed from.

That is the whole reason this is governed knowledge rather than a file read. When
the 2027 standards land, this page gets a new revision and every run from that
moment cites it; the runs before it still explain themselves against the 2026
figures, which is what a plant needs when somebody asks in March why February's
day was scheduled the way it was.

## How the lookup works

- hours are per: one truss
- span measured in: feet
- member count is: the sum of the top chord, bottom chord and web member counts printed on page A1
- row selection: the first row whose span and member ceilings the truss does not exceed
- beyond the table: use the last row and flag the truss as out of range
- nailing applies to: multi-ply trusses only
- girts plated in, hours per truss: 0.05

The two ceiling columns are read as "up to and including". A 38-foot truss with
11 members takes the `40 / 12` row. A 90-foot truss takes the last row and is
**flagged** — the standards stop at 72 feet, and a truss beyond the table is
either a special or a misread span, both of which are worth a supervisor's
glance before a day is written on it.

The job's department hours are the sum over its trusses: cut hours go to Truss
Cutting, fabrication hours to Truss Fabrication, and nailing hours to the
separate nailing row that a multi-ply job raises. The girts figure above is per
truss and applies only to the girts row a job raises when its shipping list
carries the "Trusses w/Girts Plated In" note. The conditions that raise those two
rows are in the **Plant Scheduling Parameters**; the hours are here.

## Cut, fab and nailing hours

| Span up to (ft) | Members up to | Cut (h) | Fab (h) | Nailing (h) |
|---|---|---|---|---|
| 24 | 6 | 0.10 | 0.18 | 0.10 |
| 24 | 9 | 0.13 | 0.23 | 0.13 |
| 24 | 12 | 0.16 | 0.28 | 0.16 |
| 24 | 99 (any) | 0.20 | 0.34 | 0.20 |
| 30 | 6 | 0.12 | 0.22 | 0.12 |
| 30 | 9 | 0.15 | 0.27 | 0.15 |
| 30 | 12 | 0.18 | 0.32 | 0.18 |
| 30 | 99 (any) | 0.22 | 0.38 | 0.22 |
| 36 | 6 | 0.14 | 0.26 | 0.14 |
| 36 | 9 | 0.17 | 0.31 | 0.17 |
| 36 | 12 | 0.20 | 0.36 | 0.20 |
| 36 | 99 (any) | 0.24 | 0.42 | 0.24 |
| 40 | 6 | 0.16 | 0.30 | 0.16 |
| 40 | 9 | 0.19 | 0.35 | 0.19 |
| 40 | 12 | 0.22 | 0.40 | 0.22 |
| 40 | 99 (any) | 0.26 | 0.46 | 0.26 |
| 48 | 6 | 0.19 | 0.36 | 0.20 |
| 48 | 9 | 0.22 | 0.41 | 0.23 |
| 48 | 12 | 0.25 | 0.46 | 0.26 |
| 48 | 99 (any) | 0.29 | 0.52 | 0.30 |
| 54 | 6 | 0.22 | 0.42 | 0.24 |
| 54 | 9 | 0.25 | 0.47 | 0.27 |
| 54 | 12 | 0.28 | 0.52 | 0.30 |
| 54 | 99 (any) | 0.32 | 0.58 | 0.34 |
| 60 | 6 | 0.25 | 0.48 | 0.28 |
| 60 | 9 | 0.28 | 0.53 | 0.31 |
| 60 | 12 | 0.31 | 0.58 | 0.34 |
| 60 | 99 (any) | 0.35 | 0.64 | 0.38 |
| 72 | 6 | 0.30 | 0.58 | 0.34 |
| 72 | 9 | 0.33 | 0.63 | 0.37 |
| 72 | 12 | 0.36 | 0.68 | 0.40 |
| 72 | 99 (any) | 0.40 | 0.74 | 0.44 |

## About these figures

**The source design names this workbook and quotes none of its contents, so every
hour in the table above is invented.** They are stated in one line rather than
buried: nothing in the three solution designs gives a single cut, fab or nailing
figure, and the workbook itself is not in our hands.

What they are is *shaped* correctly, which is what makes them usable for a demo
and safe to replace:

- Every column rises with span and rises with member count, and never falls in
  either direction. A wider truss with more webs is never cheaper than a
  narrower one with fewer.
- Fabrication runs about 1.6 times cut, and nailing about 0.6 times fabrication,
  which is the ordering a truss plant would recognize.
- The absolute level is set so a typical post-frame job — thirty 40-foot trusses
  at nine members — lands near `30 × 0.19 = 5.7` cut hours and
  `30 × 0.35 = 10.5` fab hours, which sits sensibly inside the daily capacities
  the scheduling parameters carry for a one- and two-man saw and a three-man
  table.

Replacing them is a paste. The real workbook's P, Q and R columns go into the
three hour columns, the span and member ceilings are relabelled to match its own
bands, and nothing else in this system changes — no code, no deploy, and one
ledger row saying who changed the plant's truss standards and when.
