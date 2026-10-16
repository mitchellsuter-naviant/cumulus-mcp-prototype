import 'package:flutter/material.dart';

import '../../../models/task.dart';
import '../../../services/api_client.dart';
import '../../../theme/cumulus_theme.dart';
import '../../../widgets/ui_kit.dart';
import '../../shared/review_frame.dart';
import '../invoice_drafts.dart';
import '../models.dart';

export '../invoice_drafts.dart' show InvoiceLineDraft;

/// What the invoice screen leads with.
///
/// Both moments in accounts-payable v6 read the same invoice and answer with
/// the same payload — a reviewer correcting an extraction may well be the one
/// who then sees the match clear, and hiding either half would make the other
/// undecidable. What differs is which half the screen opens on.
enum InvoicePanel {
  /// What the extraction read: the numbers on the document, editable.
  extraction,

  /// What the match made of it: line pairings and the anomaly basis.
  match,
}

/// The body both accounts-payable v6 screens are built from.
///
/// Not exported to the registry: the registry picks between [ExtractionReview]
/// and [VerifyThreeWayMatch], which are the two named moments a person is
/// actually called into. This holds what they share — the fetch, the drafts,
/// the dirty test, and the correction payload — so the two can never disagree
/// about what a correction means.
///
/// `ExtractionReview` and `VerifyThreeWayMatch` are in sibling files.
class InvoiceReviewCore extends StatefulWidget {
  final ApiClient api;
  final QueueTask task;
  final String eyebrow;
  final String subtitle;
  final InvoicePanel primary;

  const InvoiceReviewCore({
    super.key,
    required this.api,
    required this.task,
    required this.eyebrow,
    required this.subtitle,
    required this.primary,
  });

  @override
  State<InvoiceReviewCore> createState() => _InvoiceReviewCoreState();
}

class _InvoiceReviewCoreState extends State<InvoiceReviewCore> {
  PayableInvoice? _invoice;
  String? _error;
  final _note = TextEditingController();
  final List<InvoiceLineDraft> _lines = [];
  late final TextEditingController _invoiceNumber = TextEditingController();
  late final TextEditingController _poNumber = TextEditingController();

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    for (final line in _lines) {
      line.dispose();
    }
    _note.dispose();
    _invoiceNumber.dispose();
    _poNumber.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final invoice = await widget.api.getPayable(widget.task.caseRef);
      if (!mounted) return;
      setState(() {
        _invoice = invoice;
        _invoiceNumber.text = invoice.invoiceNumber;
        _poNumber.text = invoice.poNumber;
        _lines
          ..clear()
          ..addAll(invoiceLineDrafts(invoice));
      });
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    }
  }

  bool get _dirty {
    final invoice = _invoice;
    if (invoice == null) return false;
    return invoiceEdited(
      invoice: invoice,
      invoiceNumber: _invoiceNumber.text,
      poNumber: _poNumber.text,
      lines: _lines,
    );
  }

  Map<String, dynamic> _payload() => invoicePatch(
    invoiceNumber: _invoiceNumber.text,
    poNumber: _poNumber.text,
    lines: _lines,
  );

  void _decide(String verdict) {
    Navigator.of(context).pop(ReviewOutcome(
      verdict: verdict,
      note: _note.text.trim(),
      payload: verdict == 'corrected' ? _payload() : const {},
    ));
  }

  @override
  Widget build(BuildContext context) {
    final invoice = _invoice;
    return ReviewFrame(
      eyebrow: widget.eyebrow,
      title: widget.task.title,
      subtitle: widget.subtitle,
      footer: DecisionBar(
        note: _note,
        dirty: _dirty,
        correctLabel: 'Apply and re-check',
        onApprove: () => _decide('approved'),
        onReject: () => _decide('rejected'),
        onCorrect: () => _decide('corrected'),
      ),
      child: _error != null
          ? ErrorNotice(message: _error!, onRetry: _load)
          : invoice == null
              ? const CumulusLoading()
              : _body(invoice),
    );
  }

  Widget _body(PayableInvoice invoice) {
    final extraction = _extractionPanel(invoice);
    final match = _matchPanel(invoice);
    final first = widget.primary == InvoicePanel.match ? match : extraction;
    final second = widget.primary == InvoicePanel.match ? extraction : match;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        WhyElevated(
          reasons: [
            if (invoice.verdictReason.isNotEmpty) invoice.verdictReason,
            for (final finding in invoice.anomalyFindings)
              '${finding.kind}: ${finding.detail}',
          ],
        ),
        Wrap(
          spacing: 10,
          runSpacing: 10,
          children: [
            MetaPill(
              '${invoice.vendorName} · ${invoice.vendorResolution}',
              tone: CumulusTone.teal,
            ),
            if (invoice.matchVerdict.isNotEmpty)
              MetaPill('match: ${invoice.matchVerdict}',
                  tone: invoice.matchVerdict == 'matched'
                      ? CumulusTone.success
                      : CumulusTone.warn),
            MetaPill(invoice.policyRef),
          ],
        ),
        if (first.isNotEmpty) const SizedBox(height: 18),
        ...first,
        if (first.isNotEmpty && second.isNotEmpty) const SizedBox(height: 20),
        if (first.isEmpty && second.isNotEmpty) const SizedBox(height: 18),
        ...second,
      ],
    );
  }

  /// What the extraction read off the document, editable, with the arithmetic
  /// recomputed so it cannot drift from the numbers above it.
  List<Widget> _extractionPanel(PayableInvoice invoice) {
    final total = _lines.fold<double>(0, (sum, line) => sum + line.amount);
    return [
      Row(
        children: [
          SizedBox(
            width: 240,
            child: TextField(
              controller: _invoiceNumber,
              onChanged: (_) => setState(() {}),
              decoration: const InputDecoration(
                labelText: 'Invoice number',
                isDense: true,
              ),
            ),
          ),
          const SizedBox(width: 12),
          SizedBox(
            width: 200,
            child: TextField(
              controller: _poNumber,
              onChanged: (_) => setState(() {}),
              decoration: const InputDecoration(
                labelText: 'PO number',
                hintText: 'blank if the invoice carries none',
                isDense: true,
              ),
            ),
          ),
          const SizedBox(width: 16),
          Expanded(
            child: Text(
              'Changing the PO re-runs the match against a different order.',
              style: CumulusText.meta,
            ),
          ),
        ],
      ),
      const SizedBox(height: 18),
      const FieldLabel(
        'Lines',
        hint: 'quantity × unit price; the amount is recomputed so the '
            'arithmetic cannot drift',
      ),
      for (final line in _lines)
        Padding(
          padding: const EdgeInsets.only(bottom: 8),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              SizedBox(
                width: 30,
                child: Text('${line.ordinal}.', style: CumulusText.meta),
              ),
              SizedBox(
                width: 96,
                child: Text(line.sku, style: CumulusText.mono),
              ),
              Expanded(
                child: Text(line.description, style: CumulusText.body),
              ),
              if (line.kind != 'goods') ...[
                StatusBadge(line.kind, dense: true),
                const SizedBox(width: 8),
              ],
              SizedBox(
                width: 92,
                child: TextField(
                  controller: line.quantity,
                  onChanged: (_) => setState(() {}),
                  keyboardType: TextInputType.number,
                  decoration: const InputDecoration(
                    labelText: 'Qty',
                    isDense: true,
                  ),
                ),
              ),
              const SizedBox(width: 10),
              SizedBox(
                width: 118,
                child: TextField(
                  controller: line.unitPrice,
                  onChanged: (_) => setState(() {}),
                  keyboardType: TextInputType.number,
                  decoration: const InputDecoration(
                    labelText: 'Unit price',
                    prefixText: '\$',
                    isDense: true,
                  ),
                ),
              ),
              const SizedBox(width: 12),
              SizedBox(
                width: 100,
                child: Text(
                  '\$${line.amount.toStringAsFixed(2)}',
                  textAlign: TextAlign.right,
                  style: CumulusText.body.copyWith(
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ),
            ],
          ),
        ),
      const SizedBox(height: 6),
      Row(
        mainAxisAlignment: MainAxisAlignment.end,
        children: [
          Text('Lines total', style: CumulusText.statLabel),
          const SizedBox(width: 14),
          Text('\$${total.toStringAsFixed(2)}', style: CumulusText.statValue),
          const SizedBox(width: 20),
          Text(
            'invoice reads \$${invoice.totalUsd.toStringAsFixed(2)}',
            style: CumulusText.meta,
          ),
        ],
      ),
    ];
  }

  /// What the three-way match and the anomaly screen made of it. Empty when
  /// the run never got that far — an extraction nobody could match has nothing
  /// to say here, and an empty heading would imply it did.
  List<Widget> _matchPanel(PayableInvoice invoice) => [
    if (invoice.matchPairings.isNotEmpty) ...[
      FieldLabel('Three-way match', hint: invoice.matchSummary),
      for (final pairing in invoice.matchPairings)
        Padding(
          padding: const EdgeInsets.only(bottom: 5),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              SizedBox(
                width: 30,
                child: Text('${pairing.invoiceOrdinal}.',
                    style: CumulusText.meta),
              ),
              SizedBox(
                width: 140,
                child: StatusBadge(
                  pairing.verdict,
                  tone: pairing.clean ? CumulusTone.success : CumulusTone.warn,
                  dense: true,
                ),
              ),
              Expanded(
                child: Text(pairing.reason, style: CumulusText.body),
              ),
            ],
          ),
        ),
    ],
    if (invoice.anomalyBasis.isNotEmpty) ...[
      if (invoice.matchPairings.isNotEmpty) const SizedBox(height: 20),
      FieldLabel('Anomaly basis', hint: invoice.anomalyBasis),
    ],
  ];
}
