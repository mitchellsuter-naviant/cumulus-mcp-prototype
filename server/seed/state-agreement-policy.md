---
id: state-agreement-policy
path: platform/policies/state-agreement
title: State Agreement Policy
authority_level: policy
scope: org
owner: platform-eng
tags: disagreement, state, sales-orders, opportunities, governed-knowledge
status: published
---
# State Agreement Policy

Two systems hold the same commercial fact and say different things about it.
The CRM says a deal closed won; the ERP says the order it produced is still
waiting for somebody to release it. Neither system is wrong on its own terms —
each is the book of record for its own half — and neither can see the other,
so the disagreement is invisible in both.

This document governs the `disagreement-sweep` flow: which pairs of states are
a disagreement, how far apart in time two rows may be and still be about the
same commitment, and what has to corroborate the join before a person is asked
to look. The flow reads these values at run time; editing them changes what it
finds with no code change.

**Finding a disagreement is not resolving one.** The sweep writes a typed link
between the two rows, at `status = standing`, carrying its evidence. It
changes no object, releases no order, and writes to no external system. What
it produces is a question for the order desk.

## The disagreement table

One row per class. A pair of rows whose states appear here, and which survives
the evidence rules below, is a finding of that class.

| The CRM says | The ERP says | The disagreement |
|---|---|---|
| `closed-won` | `pending-approval` | `won-not-released` |

A class is added by adding a row. The wave-1 table holds one, because one is
what the estate can currently demonstrate: a second class with no live
instance is a rule that never fires and cannot be shown to be right.

## The window

An order that follows a deal follows it within a season, not within a year.
Outside these bounds two rows that share a counterparty and a state are two
different commitments that happen to look alike, and joining them would be the
sweep inventing a relationship the estate never had.

- Open the window: 21 days before the close date. A deal is often ordered
  ahead of the signature — the paperwork lands first and the CRM catches up.
- Close the window: 30 days after it. Past a month, an order is the *next*
  engagement rather than this one, and the delivery calendar says so.

Both offsets are read off the sales process's own day counts. They are
deliberately asymmetric: the lag is the longer half because ordering is
downstream of closing, and the lead exists only because the two systems are
updated by different people on different days.

## Evidence: three conditions, all required

A pair that fails any of these is not a finding, whatever else it scores.

1. **One counterparty.** The deal and the order reach the same company, or two
   companies joined by a `company_same_as` link a steward has **confirmed**. A
   proposal still at `in_review` does not carry it. The pair is counted as
   `blocked on identity` and never raised — an unconfirmed merge is exactly
   the assertion nothing may act on, and a disagreement raised on one would be
   a decision made on a guess about who the customer is.
2. **States.** Both rows hold the states one row of the disagreement table
   names.
3. **Time.** The order's date falls inside the window above, measured from the
   deal's close date.

## Corroboration: at least one, or it is ambiguous

The three conditions above are satisfied by every won deal a company has ever
had against every held order it has ever had. What separates *this* deal from
*that* one is corroboration, and a pair with none is recorded as `ambiguous`
rather than raised: the sweep saying out loud that it can see a disagreement
and cannot say which two rows it is between.

- **Names.** Corroborate on names at or above: 0.60, comparing the deal's name
  with the account prefix removed against the order's memo. The prefix comes
  off because every deal at a company starts with the company's name, so
  leaving it on would score every pair at that company alike.
- **Amount.** An order between 0.10 and 1.15 of the deal corroborates. The
  band is deliberately one-sided: an order is routinely a phase of a deal and
  legitimately much smaller, and an order meaningfully *larger* than the deal
  that produced it is a different commitment rather than a strong match. The
  15% headroom is for tax, freight and a rounded quote.
- **Sole candidate.** Exactly one won deal in the window for that counterparty
  corroborates on its own. Not because one candidate is better evidence, but
  because there is nothing to confuse it with.

## Confidence, and the ceiling

Confidence is arithmetic over the evidence, reported beside the evidence that
produced it, so a person on the order desk can read the sentences and disagree
with the number.

| Evidence | Weight |
|---|---|
| The three required conditions, met | +0.50 |
| Name proximity | ×0.25 |
| Amount containment inside the band | +0.12 |
| Sole candidate in the window | +0.10 |
| A delivery engagement for the same counterparty | +0.08 |

Never raise above: 0.95. A machine does not get to be certain that two systems
are talking about one commitment when it is the disagreement between them that
made the question interesting.

## No model call

Every signal here is a date comparison, a ratio, a state lookup or a name
similarity, and the blend is addition. A model would add cost,
non-determinism and a number nobody could argue with to a decision whose whole
value is that it is arguable.

## Who decides

A finding is raised into the `order-desk` queue. Releasing an order is a
revenue decision and belongs to whoever is accountable for the revenue — not
to the desk that owns the customer master, and not to accounts payable, whose
authority runs the other way. A finding is nobody's *work* until it is raised:
the sweep records it on its own authority, unasked, because noticing that two
systems disagree is not a decision.

## Reversal

A finding is a link and is withdrawn like one. `dismissed` is a judgment — the
two systems are allowed to disagree here — and the sweep must never re-raise
it; only a change in what the two systems say may produce the question again.
