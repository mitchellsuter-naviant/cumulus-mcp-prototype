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

## Reversal

Every identity link is reversible. A steward who withdraws one writes an end to
its validity rather than deleting it, so the history of what the system believed
— and acted on — stays readable. Claims contributed under a withdrawn link are
retained and stop resolving.

## Precedence when sources disagree

Field-level precedence is declared in the enterprise ontology, not here: human,
then ERP, then procurement, then a Flow's own inference, then a model's. Both
claims are always retained.
