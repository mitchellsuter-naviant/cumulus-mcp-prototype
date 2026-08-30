import 'package:cumulus_admin/models/payable.dart';
import 'package:flutter_test/flutter_test.dart';

/// The composer's live preview, pinned to the strings the server writes.
///
/// `InvoiceScenario.to_hint` in `server/app/payables.py` is the authority: the
/// console posts the scenario and the server serializes it. [InvoiceScenario.toHint]
/// exists only so the composer can label a preview "what this sends", and a
/// preview that spelled the grammar differently from the string a run receives
/// would be worse than no preview at all.
///
/// So the expectations here are the hints the repository actually ships —
/// `_seed_invoices()` and the AP eval triggers — copied verbatim. The Python
/// suite asserts the server round-trips each one; this asserts the client
/// prints the same characters from the same choices.
void main() {
  const twists = [
    ScenarioTwist(
      id: 'price_variance',
      token: 'pricebump',
      label: '',
      branch: '',
      clauseId: '',
    ),
    ScenarioTwist(
      id: 'over_receipt',
      token: 'overbill',
      label: '',
      branch: '',
      clauseId: '',
    ),
    ScenarioTwist(
      id: 'low_confidence',
      token: 'blurry',
      label: '',
      branch: '',
      clauseId: '',
    ),
    ScenarioTwist(
      id: 'tax_id',
      token: 'taxid',
      label: '',
      branch: '',
      clauseId: '',
    ),
  ];

  group('InvoiceScenario.toHint', () {
    test('prints a known vendor billing its purchase order', () {
      const scenario = InvoiceScenario(
        vendorSlug: 'acme-industrial',
        poNumber: 'PO-7741',
        invoiceNumber: 'ACME-INV-20881',
        lines: [
          ScenarioLine(sku: 'STL-0250', quantity: 2),
          ScenarioLine(sku: 'FAS-M12', quantity: 3),
        ],
      );
      expect(
        scenario.toHint(twists),
        'vendor=acme-industrial po=PO-7741 invoice=ACME-INV-20881 '
        'lines=STL-0250:2,FAS-M12:3',
      );
    });

    test('prints a twist after the lines, in the declared order', () {
      const scenario = InvoiceScenario(
        vendorSlug: 'northwind-traders',
        poNumber: 'PO-7802',
        invoiceNumber: 'NW-INV-4471',
        lines: [
          ScenarioLine(sku: 'WLD-6013', quantity: 4),
          ScenarioLine(sku: 'PPE-GLV', quantity: 2),
        ],
        twists: {'price_variance'},
      );
      expect(
        scenario.toHint(twists),
        'vendor=northwind-traders po=PO-7802 invoice=NW-INV-4471 '
        'lines=WLD-6013:4,PPE-GLV:2 pricebump',
      );
    });

    test('prints an unknown vendor with no order and an explicit total', () {
      const scenario = InvoiceScenario(
        newVendor: 'meridian-supply',
        noPo: true,
        invoiceNumber: 'MER-0001',
        lines: [ScenarioLine(sku: 'PPE-GLV', quantity: 2)],
        totalUsd: 190,
      );
      expect(
        scenario.toHint(twists),
        'newvendor=meridian-supply nopo invoice=MER-0001 lines=PPE-GLV:2 '
        'total=190.00',
      );
    });

    test('prints the two-decimal total the parser reads back', () {
      const scenario = InvoiceScenario(
        vendorSlug: 'acme-industrial',
        poNumber: 'PO-7741',
        invoiceNumber: 'ACME-INV-21077',
        lines: [
          ScenarioLine(sku: 'STL-0250', quantity: 2),
          ScenarioLine(sku: 'FAS-M12', quantity: 3),
        ],
        totalUsd: 1425.25,
      );
      expect(
        scenario.toHint(twists),
        'vendor=acme-industrial po=PO-7741 invoice=ACME-INV-21077 '
        'lines=STL-0250:2,FAS-M12:3 total=1425.25',
      );
    });

    test('says nothing about what it was not asked', () {
      // No vendor, no order, no lines: an upload composes nothing, and the
      // extractor is left to read the document.
      expect(const InvoiceScenario().toHint(twists), '');
    });

    test('serializes twists as the ids the server declared', () {
      const scenario = InvoiceScenario(
        vendorSlug: 'acme-industrial',
        twists: {'over_receipt', 'tax_id'},
      );
      final json = scenario.toJson();
      expect(json['over_receipt'], true);
      expect(json['tax_id'], true);
      expect(json.containsKey('price_variance'), false);
      // Order comes from the declaration, not from the set's iteration order.
      expect(scenario.toHint(twists), 'vendor=acme-industrial overbill taxid');
    });
  });
}
