/// What a clerk's edits to an invoice *are*, independent of the screen that
/// collected them.
///
/// Lifted out of v6's review core when v8 arrived. v6 asks about the whole
/// extraction and v8 asks about three fields, so they are two screens — but a
/// correction means the same thing to the flow either way, and the flow reads
/// exactly one shape. Two screens each building that shape by hand is two
/// chances for a version to send a patch the applier silently drops half of.
///
/// So the draft, the dirty test and the patch live here, beside the models
/// rather than inside either version's package. The same reasoning the server
/// gives for `v8/flow.py` calling `v6`'s phases rather than copying them: a fix
/// to what a correction means reaches every version at once.
///
/// The wire shape is `_apply_invoice_patch` in
/// `server/app/tasks/accounts_payable/domain.py`.
library;

import 'package:flutter/material.dart';

import 'models.dart';

/// One editable invoice line. Controllers rather than a value object because a
/// reviewer types into these and half-typed input has to survive a rebuild.
class InvoiceLineDraft {
  /// One-based, matching the patch's `ordinal` and the match pairings.
  final int ordinal;
  final String sku;
  final String description;
  final String kind;
  final TextEditingController quantity;
  final TextEditingController unitPrice;

  InvoiceLineDraft({
    required this.ordinal,
    required this.sku,
    required this.description,
    required this.kind,
    required String quantityText,
    required String unitPriceText,
  })  : quantity = TextEditingController(text: quantityText),
        unitPrice = TextEditingController(text: unitPriceText);

  void dispose() {
    quantity.dispose();
    unitPrice.dispose();
  }

  double get quantityValue => double.tryParse(quantity.text.trim()) ?? 0;
  double get unitPriceValue => double.tryParse(unitPrice.text.trim()) ?? 0;

  /// Recomputed rather than editable, so the arithmetic on the screen cannot
  /// drift from the two numbers above it — and so a clerk fixing a misread
  /// quantity is never also asked to do the multiplication.
  double get amount => quantityValue * unitPriceValue;
}

/// `3` rather than `3.0`, `2.5` rather than `2.50`. A quantity a person typed
/// should come back looking like a quantity a person typed.
String numberText(double value) =>
    value == value.roundToDouble() ? '${value.toInt()}' : '$value';

/// A fresh set of line drafts for [invoice], in the invoice's own order.
List<InvoiceLineDraft> invoiceLineDrafts(PayableInvoice invoice) => [
  for (var i = 0; i < invoice.lines.length; i++)
    InvoiceLineDraft(
      ordinal: i + 1,
      sku: invoice.lines[i].sku,
      description: invoice.lines[i].description,
      kind: invoice.lines[i].kind,
      quantityText: numberText(invoice.lines[i].quantity),
      unitPriceText: invoice.lines[i].unitPriceUsd.toStringAsFixed(2),
    ),
];

/// Whether anything on the screen differs from what the run read.
///
/// The two tolerances are the precisions the fields are *displayed* at, not
/// guesses: a quantity round-trips exactly and a unit price is shown to the
/// cent, so a difference under half a cent is the rendering, not an edit.
/// Getting this wrong in either direction is bad in a particular way — too
/// tight and Approve turns into Apply for a clerk who touched nothing, too
/// loose and a real correction is offered as an approval.
bool invoiceEdited({
  required PayableInvoice invoice,
  required String invoiceNumber,
  required String poNumber,
  required List<InvoiceLineDraft> lines,
}) {
  if (invoiceNumber.trim() != invoice.invoiceNumber) return true;
  if (poNumber.trim() != invoice.poNumber) return true;
  for (var i = 0; i < lines.length && i < invoice.lines.length; i++) {
    if ((lines[i].quantityValue - invoice.lines[i].quantity).abs() > 0.0001) {
      return true;
    }
    if ((lines[i].unitPriceValue - invoice.lines[i].unitPriceUsd).abs() >
        0.004) {
      return true;
    }
  }
  return false;
}

/// The correction as the flow applies it: header fields, and per-line quantity
/// and unit price by ordinal.
///
/// No amount is sent per line. `_apply_invoice_patch` recomputes it from the
/// two numbers when neither an amount nor a contradiction is supplied, which
/// is the same rule [InvoiceLineDraft.amount] renders under — one arithmetic,
/// stated once, on both sides of the wire.
Map<String, dynamic> invoicePatch({
  required String invoiceNumber,
  required String poNumber,
  required List<InvoiceLineDraft> lines,
}) => {
  'invoice_number': invoiceNumber.trim(),
  'po_number': poNumber.trim(),
  'lines': [
    for (final line in lines)
      {
        'ordinal': line.ordinal,
        'quantity': line.quantityValue,
        'unit_price_usd': line.unitPriceValue,
      },
  ],
};
