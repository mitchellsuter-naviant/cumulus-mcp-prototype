---
id: vendor-exemplar-acme-industrial
path: finance/exemplars/acme-industrial
title: "Vendor exemplar: Acme Industrial"
kind: exemplar
origin: flywheel
scope: org
owner: finance-ops
tags: exemplar, ap, acme-industrial
status: published
---
# Vendor exemplar: Acme Industrial

One-shot extraction template for Acme Industrial Inc. invoices. The
accounts-payable flow hands this to the extraction ModelCall when the
classifier resolves a document to this vendor, so extraction is a single
confident pass instead of a three-model ensemble.

## Layout

- Invoice number: top right, format `ACME-INV-#####`.
- Invoice date: below the number, ISO format (`YYYY-MM-DD`).
- PO number: "Customer PO" line in the header block, format `PO-####`.
- Total: bottom right, labeled "TOTAL DUE", USD, includes freight.
- Freight appears as its own line item — do not fold it into materials.

## Provenance

Seeded from the first verified Acme invoice (inv-24193 lineage), confirmed
by an ap-clerk pass. Corrections to Acme extractions accumulate in the
correction set and re-propose this exemplar when they show a pattern.
