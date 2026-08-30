---
id: entity-resolution-policy
path: platform/policies/entity-resolution
title: Entity Resolution Policy
authority_level: policy
scope: org
owner: platform-eng
tags: mdm, entity-resolution, vendors, governed-knowledge
status: published
---
# Entity Resolution Policy

How an inbound record becomes a link to an existing entity, a new entity, or a
steward's Task. The accounts-payable and vendor-sync flows read these values at
run time; editing them changes what the flows do with no code change.

## Match thresholds

- Auto-link at or above: 0.92. The inbound record is linked to the matched
  entity without review, and the link records the score and method that
  produced it.
- Steward review between: 0.70 and 0.92. The record lands, no link is asserted,
  and a match_review Task is raised with the ranked candidates. A batch sync
  does not stop for this — the row waits, the feed continues.
- Mint a new entity below: 0.70. A record that resembles nothing known is a new
  entity, marked `onboarding` until a steward confirms it.

## Signals, in order of force

1. Tax id exact match. Decisive on its own: score 0.99.
2. Normalized-name exact match. Legal-form suffixes (Inc, LLC, Ltd, GmbH) and
   punctuation are removed before comparing; word order is not.
3. Token overlap plus character similarity on the normalized names, blended.
   This is the only fuzzy signal, and it is the one that needs a threshold.
4. An existing identity link from the same source system. A source that has
   already told us its id for an entity is believed again without re-scoring.

## Cross-source merge proposals

The thresholds above judge one arriving record against the book. These judge
two entities the book already holds — the ERP customer created in 2019 under
an old trading name, the contact re-typed off a trade-show list — which no
single feed can see, because a feed scores its own rows and never scores the
book against itself. The `identity-sweep` flow reads these values at run time.

- Propose a merge at or above: 0.55. A typed identity link is written between
  the two entities at `status = in_review`, carrying its confidence, the
  signals that produced it, and what an un-merge restores. **Nothing merges.**
  No canonical object changes, and the Action Gateway refuses to act on the
  proposal until a human confirms it.
- Every proposal raises a `merge_review` Task in the queue the Mapping names,
  with the evidence written out. A merge is a high-blast-radius change and is
  never automatic, however strong the evidence: a proposal with nobody paged
  would be an assertion standing in the book with no way to decide it.
- Ratify rather than ask at or above: 0.80. This governs the *character* of
  that Task and nothing else. Under it the sweep is saying out loud that it
  does not know: the Task is a genuine question at normal priority. At or above
  it the evidence is strong and the Task is a low-priority ratification —
  confirm to let downstream flows treat the two as one party. Either way
  nothing merges until a human answers.
- Consider a pair on names alone at or above: 0.55. Below that similarity a
  pair is only looked at when a domain or a phone number joins it — which is
  how a trading name (`GSF Logistics` for Granite State Freight) gets
  considered at all.
- Never propose above: 0.95. A machine does not get to claim certainty about
  an identity nobody has confirmed.

### Evidence, and what it is worth

Weights are per pair kind, because the same fact means different things about
an organization and about a person: two people sharing an email domain are
colleagues, which is a precondition for being the same person and nowhere near
proof of it.

| Signal | Organizations | People |
|---|---|---|
| Shared internet domain | +0.55 | — (see shared employer) |
| Identical phone number | +0.45 | +0.45 |
| Same area code, different number | +0.08 | +0.08 |
| Different area code | −0.20 | −0.20 |
| Different internet domain | −0.35 | — |
| Same anchor word (first word of a company, surname of a person) | +0.20 | +0.20 |
| Shared employer (same company, or the same mail domain) | — | +0.25 |
| Blended name similarity | ×0.80 | ×0.80 |

A pair of people is only considered when a shared employer is established.
Two humans who share a name are two humans; what makes them candidates is
being in the same place.

## Reversal

Every identity link is reversible. A steward who withdraws one writes an end to
its validity rather than deleting it, so the history of what the system believed
— and acted on — stays readable. Claims contributed under a withdrawn link are
retained and stop resolving.

## Precedence when sources disagree

Field-level precedence is declared in the enterprise ontology, not here: human,
then ERP, then procurement, then a Flow's own inference, then a model's. Both
claims are always retained.
