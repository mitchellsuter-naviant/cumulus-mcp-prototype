---
id: vendor-exemplar-northwind-traders
path: finance/exemplars/northwind-traders
title: "Vendor exemplar: Northwind Traders"
authority_level: how-to
scope: org
owner: finance-ops
tags: exemplar, ap, northwind-traders, governed-knowledge
status: published
---
# Vendor exemplar: Northwind Traders

One-shot extraction template for Northwind Traders LLC invoices. The
accounts-payable flow hands this to the extraction ModelCall when the resolver
names this vendor, so extraction is a single confident pass instead of a
three-model ensemble.

## Layout

- Invoice number: top right, format `NW-INV-####`.
- Invoice date: below the number, ISO format (`YYYY-MM-DD`).
- PO number: "Your order" line in the header block, format `PO-####`.
- Total: bottom right, labeled "AMOUNT DUE", USD.
- Freight is a separate line labeled "Delivery" — do not fold it into
  materials.

## Line items

Columns run SKU · description · quantity · unit price · amount. This vendor
prints its own catalog numbers in the SKU column, which match the purchase
order's, so the three-way match keys on SKU rather than description.

## Provenance

Seeded from a verified Northwind invoice, confirmed by an ap-clerk pass.
Corrections to Northwind extractions accumulate in the correction set and
re-propose this exemplar when they show a pattern.
