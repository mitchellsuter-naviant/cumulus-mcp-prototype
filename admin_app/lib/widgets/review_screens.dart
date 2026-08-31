import 'package:flutter/material.dart';

import '../models/disagreement_screen.dart';
import '../models/identity_screen.dart';
import '../models/payable.dart';
import '../models/reimbursement.dart';
import '../models/task.dart';
import '../services/api_client.dart';
import '../theme/cumulus_theme.dart';
import 'disagreement_review.dart';
import 'merge_review.dart';
import 'ui_kit.dart';

/// The custom human-in-the-loop screens: where a person edits the *facts*
/// rather than overriding a rule.
///
/// This is the difference between a review queue and an approval button. A
/// reviewer looking at an elevated expense is usually not disagreeing with the
/// policy — the extraction read a crumpled total wrong, or a receipt covers two
/// charges, or nobody said what the dinner was for. So the screen lets them
/// change the amount, split a document into two lines, add a line the model
/// missed, and supply a purpose; the run then **re-runs the same checks** over
/// the corrected facts. Approving despite the flags is still available, and is
/// recorded as exactly that.
///
/// Everything a reviewer changes travels as a structured `payload` on the
/// decision, is applied by the flow that raised the Task, and lands in the
/// graph as a `human:` claim that outranks the model's — with the model's
/// original reading still on the record.
class ReviewOutcome {
  final String verdict; // approved | rejected | corrected
  final String note;
  final Map<String, dynamic> payload;

  const ReviewOutcome({
    required this.verdict,
    required this.note,
    this.payload = const {},
  });
}

/// Opens the right screen for a task, or null when the task has no bespoke one
/// (a plain approve/reject is then the honest affordance).
Future<ReviewOutcome?> openReviewScreen({
  required BuildContext context,
  required ApiClient api,
  required QueueTask task,
}) {
  final Widget body;
  if (task.kind == 'match_review') {
    body = _EntityMatchReview(task: task);
  } else if (task.kind == 'merge_review') {
    body = _MergeReview(api: api, task: task);
  } else if (task.kind == 'state_disagreement') {
    body = _DisagreementReview(api: api, task: task);
  } else if (task.flowId == 'expense-reimbursement') {
    body = _ExpenseReview(api: api, task: task);
  } else if (task.flowId == 'accounts-payable') {
    body = _InvoiceReview(api: api, task: task);
  } else {
    return Future.value(null);
  }
  return showDialog<ReviewOutcome>(
    context: context,
    barrierColor: CumulusColors.ink.withValues(alpha: 0.24),
    builder: (context) => Dialog(
      backgroundColor: CumulusColors.canvas,
      insetPadding: const EdgeInsets.symmetric(horizontal: 40, vertical: 32),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(CumulusRadius.card),
      ),
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 1080, maxHeight: 900),
        child: body,
      ),
    ),
  );
}

// ---------------------------------------------------------------------------
// Shared chrome
// ---------------------------------------------------------------------------

class _ReviewFrame extends StatelessWidget {
  final String eyebrow;
  final String title;
  final String subtitle;
  final Widget child;
  final Widget footer;

  const _ReviewFrame({
    required this.eyebrow,
    required this.title,
    required this.subtitle,
    required this.child,
    required this.footer,
  });

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(28, 24, 20, 8),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(eyebrow.toUpperCase(), style: CumulusText.eyebrow),
                    const SizedBox(height: 6),
                    Text(title, style: CumulusText.sectionTitle),
                    const SizedBox(height: 6),
                    Text(subtitle, style: CumulusText.meta),
                  ],
                ),
              ),
              IconButton(
                onPressed: () => Navigator.of(context).pop(),
                icon: const Icon(Icons.close, size: 18),
                tooltip: 'Close without deciding',
              ),
            ],
          ),
        ),
        const Divider(height: 1),
        Expanded(
          child: SingleChildScrollView(
            padding: const EdgeInsets.fromLTRB(28, 18, 28, 24),
            child: child,
          ),
        ),
        const Divider(height: 1),
        Padding(
          padding: const EdgeInsets.fromLTRB(28, 14, 28, 18),
          child: footer,
        ),
      ],
    );
  }
}

class _DecisionBar extends StatelessWidget {
  final TextEditingController note;
  final bool dirty;
  final String correctLabel;
  final VoidCallback onApprove;
  final VoidCallback onReject;
  final VoidCallback onCorrect;

  const _DecisionBar({
    required this.note,
    required this.dirty,
    required this.correctLabel,
    required this.onApprove,
    required this.onReject,
    required this.onCorrect,
  });

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        TextField(
          controller: note,
          maxLines: 2,
          minLines: 1,
          decoration: const InputDecoration(
            labelText: 'Note (kept on the record, and quoted in the journal)',
            isDense: true,
          ),
        ),
        const SizedBox(height: 12),
        Row(
          children: [
            Expanded(
              child: Text(
                dirty
                    ? 'Your edits will be applied and the checks will run '
                        'again over the corrected facts.'
                    : 'Approving accepts the flags as they stand, and is '
                        'recorded as exactly that.',
                style: CumulusText.meta,
              ),
            ),
            const SizedBox(width: 16),
            OutlinedButton.icon(
              onPressed: onReject,
              icon: const Icon(Icons.block, size: 16),
              label: const Text('Reject'),
              style: OutlinedButton.styleFrom(
                foregroundColor: CumulusColors.danger,
              ),
            ),
            const SizedBox(width: 10),
            if (dirty)
              FilledButton.icon(
                onPressed: onCorrect,
                icon: const Icon(Icons.edit_note, size: 16),
                label: Text(correctLabel),
              )
            else
              FilledButton.icon(
                onPressed: onApprove,
                icon: const Icon(Icons.check, size: 16),
                label: const Text('Approve as-is'),
              ),
          ],
        ),
      ],
    );
  }
}

class _WhyElevated extends StatelessWidget {
  final List<String> reasons;

  const _WhyElevated({required this.reasons});

  @override
  Widget build(BuildContext context) {
    if (reasons.isEmpty) return const SizedBox.shrink();
    return Container(
      width: double.infinity,
      margin: const EdgeInsets.only(bottom: 18),
      padding: const EdgeInsets.fromLTRB(16, 13, 16, 14),
      decoration: BoxDecoration(
        color: CumulusTone.warn.background,
        borderRadius: BorderRadius.circular(CumulusRadius.control),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('WHY THIS STOPPED', style: CumulusText.statLabel),
          const SizedBox(height: 8),
          for (final reason in reasons)
            Padding(
              padding: const EdgeInsets.only(bottom: 3),
              child: Text('· $reason', style: CumulusText.body),
            ),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Expense review
// ---------------------------------------------------------------------------

/// One editable line. Controllers rather than a value object because a reviewer
/// types into these and half-typed input has to survive a rebuild.
class _LineDraft {
  final int? ordinal; // null on a line the reviewer added
  final String documentId;
  final String documentLabel;
  final double documentTotalUsd;
  final TextEditingController vendor;
  final TextEditingController requested;
  final TextEditingController purpose;
  String category;
  bool removed = false;
  bool touched = false;

  _LineDraft({
    this.ordinal,
    required this.documentId,
    required this.documentLabel,
    required this.documentTotalUsd,
    required String vendorText,
    required String requestedText,
    required String purposeText,
    required this.category,
  })  : vendor = TextEditingController(text: vendorText),
        requested = TextEditingController(text: requestedText),
        purpose = TextEditingController(text: purposeText);

  void dispose() {
    vendor.dispose();
    requested.dispose();
    purpose.dispose();
  }

  double get requestedUsd => double.tryParse(requested.text.trim()) ?? 0;
}

const _categories = [
  'meals', 'travel', 'lodging', 'mileage', 'supplies', 'software',
  'alcohol', 'gifts', 'entertainment', 'other',
];

class _ExpenseReview extends StatefulWidget {
  final ApiClient api;
  final QueueTask task;

  const _ExpenseReview({required this.api, required this.task});

  @override
  State<_ExpenseReview> createState() => _ExpenseReviewState();
}

class _ExpenseReviewState extends State<_ExpenseReview> {
  Reimbursement? _request;
  String? _error;
  final _note = TextEditingController();
  final List<_LineDraft> _drafts = [];
  final List<_LineDraft> _added = [];

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    for (final draft in [..._drafts, ..._added]) {
      draft.dispose();
    }
    _note.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final request = await widget.api.getReimbursement(widget.task.caseRef);
      if (!mounted) return;
      setState(() {
        _request = request;
        _drafts
          ..clear()
          ..addAll(request.lines.map(_toDraft));
      });
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    }
  }

  _LineDraft _toDraft(ExpenseLine line) {
    final document = _request?.documents.firstWhere(
      (d) => d.documentId == line.documentId,
      orElse: () => const DocumentSlot(
        documentId: '', filename: '', note: '', byteSize: 0, mediaType: '',
      ),
    );
    return _LineDraft(
      ordinal: line.ordinal,
      documentId: line.documentId,
      documentLabel: document?.filename ?? '',
      documentTotalUsd: line.documentTotalUsd,
      vendorText: line.vendor,
      requestedText: line.requestedUsd.toStringAsFixed(2),
      purposeText: line.purpose,
      category: line.category,
    );
  }

  void _addLine() {
    final documents = _request?.documents ?? const <DocumentSlot>[];
    setState(() {
      _added.add(_LineDraft(
        documentId: documents.isEmpty ? '' : documents.first.documentId,
        documentLabel: documents.isEmpty ? '' : documents.first.filename,
        documentTotalUsd: 0,
        vendorText: '',
        requestedText: '',
        purposeText: '',
        category: 'other',
      ));
    });
  }

  bool get _dirty {
    if (_added.isNotEmpty) return true;
    final request = _request;
    if (request == null) return false;
    for (final draft in _drafts) {
      if (draft.removed || draft.touched) return true;
      final original = request.lines.firstWhere(
        (line) => line.ordinal == draft.ordinal,
      );
      if (draft.vendor.text.trim() != original.vendor) return true;
      if (draft.purpose.text.trim() != original.purpose) return true;
      if (draft.category != original.category) return true;
      if ((draft.requestedUsd - original.requestedUsd).abs() > 0.004) {
        return true;
      }
    }
    return false;
  }

  Map<String, dynamic> _payload() {
    final lines = <Map<String, dynamic>>[];
    final removed = <int>[];
    for (final draft in _drafts) {
      if (draft.removed) {
        removed.add(draft.ordinal!);
        continue;
      }
      lines.add({
        'ordinal': draft.ordinal,
        'vendor': draft.vendor.text.trim(),
        'category': draft.category,
        'requested_usd': draft.requestedUsd,
        'purpose': draft.purpose.text.trim(),
      });
    }
    final additions = [
      for (final draft in _added)
        {
          'vendor': draft.vendor.text.trim(),
          'category': draft.category,
          'requested_usd': draft.requestedUsd,
          'document_total_usd': draft.requestedUsd,
          'purpose': draft.purpose.text.trim(),
          'document_id': draft.documentId,
        },
    ];
    return {
      if (lines.isNotEmpty) 'lines': lines,
      if (additions.isNotEmpty) 'add_lines': additions,
      if (removed.isNotEmpty) 'remove_ordinals': removed,
    };
  }

  void _decide(String verdict) {
    Navigator.of(context).pop(ReviewOutcome(
      verdict: verdict,
      note: _note.text.trim(),
      payload: verdict == 'corrected' ? _payload() : const {},
    ));
  }

  @override
  Widget build(BuildContext context) {
    final request = _request;
    final total = [..._drafts.where((d) => !d.removed), ..._added]
        .fold<double>(0, (sum, d) => sum + d.requestedUsd);
    return _ReviewFrame(
      eyebrow: 'Expense review · ${widget.task.queue}',
      title: widget.task.title,
      subtitle:
          'Correct what the extraction got wrong, split a receipt, add a line '
          'it missed, or supply the purpose — then the checks run again.',
      footer: _DecisionBar(
        note: _note,
        dirty: _dirty,
        correctLabel: 'Apply and re-check',
        onApprove: () => _decide('approved'),
        onReject: () => _decide('rejected'),
        onCorrect: () => _decide('corrected'),
      ),
      child: _error != null
          ? ErrorNotice(message: _error!, onRetry: _load)
          : request == null
              ? const CumulusLoading()
              : Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    _WhyElevated(
                      reasons: request.escalations
                          .map((e) => '${e.kind}: ${e.detail}')
                          .toList(),
                    ),
                    if (request.commentary.isNotEmpty) ...[
                      const FieldLabel(
                        'What the submitter said',
                        hint:
                            'read by the flow — a stated purpose here may be '
                            'all that is missing',
                      ),
                      Text('“${request.commentary}”', style: CumulusText.body),
                      const SizedBox(height: 16),
                    ],
                    if (request.documents.isNotEmpty) ...[
                      const FieldLabel('Documents in this submission'),
                      Wrap(
                        spacing: 8,
                        runSpacing: 8,
                        children: [
                          for (final document in request.documents)
                            Tooltip(
                              message: document.note.isEmpty
                                  ? 'No note on this one.'
                                  : document.note,
                              child: Container(
                                padding: const EdgeInsets.symmetric(
                                  horizontal: 10,
                                  vertical: 6,
                                ),
                                decoration: BoxDecoration(
                                  color: CumulusColors.surface,
                                  borderRadius: BorderRadius.circular(8),
                                  border:
                                      Border.all(color: CumulusColors.border),
                                ),
                                child: Row(
                                  mainAxisSize: MainAxisSize.min,
                                  children: [
                                    const Icon(Icons.receipt_outlined,
                                        size: 14,
                                        color: CumulusColors.inkMuted),
                                    const SizedBox(width: 6),
                                    Text(
                                      document.filename.isEmpty
                                          ? document.documentId
                                          : document.filename,
                                      style: CumulusText.body,
                                    ),
                                  ],
                                ),
                              ),
                            ),
                        ],
                      ),
                      const SizedBox(height: 18),
                    ],
                    Row(
                      children: [
                        const Expanded(
                          child: FieldLabel(
                            'Lines',
                            hint:
                                'facts, not documents — one receipt can be two '
                                'lines',
                          ),
                        ),
                        TextButton.icon(
                          onPressed: _addLine,
                          icon: const Icon(Icons.add, size: 16),
                          label: const Text('Add a line'),
                        ),
                      ],
                    ),
                    const SizedBox(height: 4),
                    for (final draft in _drafts)
                      _LineEditor(
                        draft: draft,
                        onChanged: () => setState(() {}),
                        onRemove: () => setState(() {
                          draft.removed = !draft.removed;
                        }),
                      ),
                    for (final draft in _added)
                      _LineEditor(
                        draft: draft,
                        isNew: true,
                        onChanged: () => setState(() {}),
                        onRemove: () => setState(() {
                          _added.remove(draft);
                          draft.dispose();
                        }),
                      ),
                    const SizedBox(height: 10),
                    Row(
                      mainAxisAlignment: MainAxisAlignment.end,
                      children: [
                        Text('Total claimed', style: CumulusText.statLabel),
                        const SizedBox(width: 14),
                        Text(
                          '\$${total.toStringAsFixed(2)}',
                          style: CumulusText.statValue,
                        ),
                      ],
                    ),
                    const SizedBox(height: 8),
                    Text(
                      'Governed limit \$${request.policyLimitUsd.toStringAsFixed(2)} '
                      '· ${request.policyRef}',
                      style: CumulusText.meta,
                    ),
                  ],
                ),
    );
  }
}

class _LineEditor extends StatelessWidget {
  final _LineDraft draft;
  final bool isNew;
  final VoidCallback onChanged;
  final VoidCallback onRemove;

  const _LineEditor({
    required this.draft,
    required this.onChanged,
    required this.onRemove,
    this.isNew = false,
  });

  @override
  Widget build(BuildContext context) {
    final over = draft.documentTotalUsd > 0 &&
        draft.requestedUsd > draft.documentTotalUsd + 0.005;
    return Opacity(
      opacity: draft.removed ? 0.45 : 1,
      child: Container(
        margin: const EdgeInsets.only(bottom: 10),
        padding: const EdgeInsets.fromLTRB(14, 12, 8, 12),
        decoration: BoxDecoration(
          color: CumulusColors.surface,
          borderRadius: BorderRadius.circular(CumulusRadius.control),
          border: Border.all(
            color: isNew ? CumulusColors.accent : CumulusColors.border,
          ),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                SizedBox(
                  width: 34,
                  child: Text(
                    isNew ? 'new' : '${draft.ordinal}.',
                    style: CumulusText.meta,
                  ),
                ),
                Expanded(
                  flex: 3,
                  child: TextField(
                    controller: draft.vendor,
                    enabled: !draft.removed,
                    onChanged: (_) => onChanged(),
                    decoration: const InputDecoration(
                      labelText: 'Vendor',
                      isDense: true,
                    ),
                  ),
                ),
                const SizedBox(width: 10),
                SizedBox(
                  width: 150,
                  child: DropdownButtonFormField<String>(
                    initialValue: draft.category,
                    isDense: true,
                    decoration: const InputDecoration(
                      labelText: 'Category',
                      isDense: true,
                    ),
                    items: [
                      for (final category in _categories)
                        DropdownMenuItem(value: category, child: Text(category)),
                    ],
                    onChanged: draft.removed
                        ? null
                        : (value) {
                            draft.category = value ?? draft.category;
                            draft.touched = true;
                            onChanged();
                          },
                  ),
                ),
                const SizedBox(width: 10),
                SizedBox(
                  width: 128,
                  child: TextField(
                    controller: draft.requested,
                    enabled: !draft.removed,
                    onChanged: (_) => onChanged(),
                    keyboardType: TextInputType.number,
                    decoration: InputDecoration(
                      labelText: 'Claiming',
                      prefixText: '\$',
                      isDense: true,
                      errorText: over ? 'over the receipt' : null,
                    ),
                  ),
                ),
                IconButton(
                  onPressed: onRemove,
                  icon: Icon(
                    draft.removed
                        ? Icons.undo
                        : isNew
                            ? Icons.close
                            : Icons.delete_outline,
                    size: 16,
                  ),
                  tooltip: draft.removed ? 'Keep this line' : 'Drop this line',
                ),
              ],
            ),
            const SizedBox(height: 8),
            Row(
              children: [
                const SizedBox(width: 34),
                Expanded(
                  child: TextField(
                    controller: draft.purpose,
                    enabled: !draft.removed,
                    onChanged: (_) => onChanged(),
                    decoration: const InputDecoration(
                      labelText: 'Business purpose',
                      hintText:
                          'why this happened — required above the governed '
                          'amount',
                      isDense: true,
                    ),
                  ),
                ),
              ],
            ),
            if (draft.documentTotalUsd > 0 || draft.documentLabel.isNotEmpty) ...[
              const SizedBox(height: 6),
              Padding(
                padding: const EdgeInsets.only(left: 34),
                child: Text(
                  [
                    if (draft.documentLabel.isNotEmpty) draft.documentLabel,
                    if (draft.documentTotalUsd > 0)
                      'receipt total \$${draft.documentTotalUsd.toStringAsFixed(2)}',
                  ].join('  ·  '),
                  style: CumulusText.meta,
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Invoice review
// ---------------------------------------------------------------------------

class _InvoiceLineDraft {
  final int ordinal;
  final String sku;
  final String description;
  final String kind;
  final TextEditingController quantity;
  final TextEditingController unitPrice;

  _InvoiceLineDraft({
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
  double get amount => quantityValue * unitPriceValue;
}

class _InvoiceReview extends StatefulWidget {
  final ApiClient api;
  final QueueTask task;

  const _InvoiceReview({required this.api, required this.task});

  @override
  State<_InvoiceReview> createState() => _InvoiceReviewState();
}

class _InvoiceReviewState extends State<_InvoiceReview> {
  PayableInvoice? _invoice;
  String? _error;
  final _note = TextEditingController();
  final List<_InvoiceLineDraft> _lines = [];
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
          ..addAll([
            for (var i = 0; i < invoice.lines.length; i++)
              _InvoiceLineDraft(
                ordinal: i + 1,
                sku: invoice.lines[i].sku,
                description: invoice.lines[i].description,
                kind: invoice.lines[i].kind,
                quantityText: _num(invoice.lines[i].quantity),
                unitPriceText:
                    invoice.lines[i].unitPriceUsd.toStringAsFixed(2),
              ),
          ]);
      });
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    }
  }

  static String _num(double value) =>
      value == value.roundToDouble() ? '${value.toInt()}' : '$value';

  bool get _dirty {
    final invoice = _invoice;
    if (invoice == null) return false;
    if (_invoiceNumber.text.trim() != invoice.invoiceNumber) return true;
    if (_poNumber.text.trim() != invoice.poNumber) return true;
    for (var i = 0; i < _lines.length && i < invoice.lines.length; i++) {
      if ((_lines[i].quantityValue - invoice.lines[i].quantity).abs() > 0.0001) {
        return true;
      }
      if ((_lines[i].unitPriceValue - invoice.lines[i].unitPriceUsd).abs() >
          0.004) {
        return true;
      }
    }
    return false;
  }

  Map<String, dynamic> _payload() => {
    'invoice_number': _invoiceNumber.text.trim(),
    'po_number': _poNumber.text.trim(),
    'lines': [
      for (final line in _lines)
        {
          'ordinal': line.ordinal,
          'quantity': line.quantityValue,
          'unit_price_usd': line.unitPriceValue,
        },
    ],
  };

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
    final total = _lines.fold<double>(0, (sum, line) => sum + line.amount);
    return _ReviewFrame(
      eyebrow: 'Invoice review · ${widget.task.queue}',
      title: widget.task.title,
      subtitle: 'Correct what the extraction read, and the three-way match '
          'and the anomaly screen run again over the corrected values — a '
          'correction re-runs the checks rather than overriding them.',
      footer: _DecisionBar(
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
              : Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    _WhyElevated(
                      reasons: [
                        if (invoice.verdictReason.isNotEmpty)
                          invoice.verdictReason,
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
                    const SizedBox(height: 18),
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
                            'Changing the PO re-runs the match against a '
                            'different order.',
                            style: CumulusText.meta,
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 18),
                    const FieldLabel(
                      'Lines',
                      hint:
                          'quantity × unit price; the amount is recomputed so '
                          'the arithmetic cannot drift',
                    ),
                    for (final line in _lines)
                      Padding(
                        padding: const EdgeInsets.only(bottom: 8),
                        child: Row(
                          crossAxisAlignment: CrossAxisAlignment.end,
                          children: [
                            SizedBox(
                              width: 30,
                              child: Text('${line.ordinal}.',
                                  style: CumulusText.meta),
                            ),
                            SizedBox(
                              width: 96,
                              child: Text(line.sku, style: CumulusText.mono),
                            ),
                            Expanded(
                              child: Text(
                                line.description,
                                style: CumulusText.body,
                              ),
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
                        Text('\$${total.toStringAsFixed(2)}',
                            style: CumulusText.statValue),
                        const SizedBox(width: 20),
                        Text(
                          'invoice reads '
                          '\$${invoice.totalUsd.toStringAsFixed(2)}',
                          style: CumulusText.meta,
                        ),
                      ],
                    ),
                    if (invoice.matchPairings.isNotEmpty) ...[
                      const SizedBox(height: 20),
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
                                  tone: pairing.clean
                                      ? CumulusTone.success
                                      : CumulusTone.warn,
                                  dense: true,
                                ),
                              ),
                              Expanded(
                                child: Text(pairing.reason,
                                    style: CumulusText.body),
                              ),
                            ],
                          ),
                        ),
                    ],
                    if (invoice.anomalyBasis.isNotEmpty) ...[
                      const SizedBox(height: 20),
                      FieldLabel('Anomaly basis', hint: invoice.anomalyBasis),
                    ],
                  ],
                ),
    );
  }
}

// ---------------------------------------------------------------------------
// Entity match review
// ---------------------------------------------------------------------------

/// The resolution stream of the queue. Nothing was asserted while this Task was
/// open — a batch feed lands the row and escalates rather than guessing — so
/// the steward's decision here is the *first* write.
class _EntityMatchReview extends StatefulWidget {
  final QueueTask task;

  const _EntityMatchReview({required this.task});

  @override
  State<_EntityMatchReview> createState() => _EntityMatchReviewState();
}

class _EntityMatchReviewState extends State<_EntityMatchReview> {
  final _note = TextEditingController();
  String? _chosen;

  @override
  void dispose() {
    _note.dispose();
    super.dispose();
  }

  /// Candidate ids come off the task's refs; their labels and scores come off
  /// the evidence the flow attached. Both were written by the run that raised
  /// this, so there is nothing to fetch.
  List<({String id, String label})> get _candidates {
    final ids = widget.task.refs
        .where((ref) => ref.startsWith('graph:'))
        .map((ref) => ref.split('/').last.split('@').first)
        .toList();
    final labels = widget.task.evidence
        .where((e) => e.label.startsWith('Candidate'))
        .map((e) => '${e.label} — ${e.value}')
        .toList();
    return [
      for (var i = 0; i < ids.length; i++)
        (id: ids[i], label: i < labels.length ? labels[i] : ids[i]),
    ];
  }

  void _decide(String verdict) {
    Navigator.of(context).pop(ReviewOutcome(
      verdict: verdict,
      note: _note.text.trim(),
      payload: verdict == 'corrected' && _chosen != null
          ? {'object_id': _chosen}
          : const {},
    ));
  }

  @override
  Widget build(BuildContext context) {
    final candidates = _candidates;
    return _ReviewFrame(
      eyebrow: 'Entity resolution · ${widget.task.queue}',
      title: widget.task.title,
      subtitle:
          'Nothing has been asserted yet. Linking writes a reversible identity '
          'edge; rejecting mints a new entity instead.',
      footer: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          TextField(
            controller: _note,
            maxLines: 2,
            minLines: 1,
            decoration: const InputDecoration(
              labelText: 'Note (kept on the link, and on the record)',
              isDense: true,
            ),
          ),
          const SizedBox(height: 12),
          Row(
            children: [
              Expanded(
                child: Text(
                  _chosen == null
                      ? 'Approve links it to the top candidate. Reject treats '
                          'it as a new entity.'
                      : 'Linking to the selected candidate.',
                  style: CumulusText.meta,
                ),
              ),
              const SizedBox(width: 16),
              OutlinedButton.icon(
                onPressed: () => _decide('rejected'),
                icon: const Icon(Icons.add_business_outlined, size: 16),
                label: const Text('It is a new entity'),
              ),
              const SizedBox(width: 10),
              FilledButton.icon(
                onPressed: () =>
                    _decide(_chosen == null ? 'approved' : 'corrected'),
                icon: const Icon(Icons.link, size: 16),
                label: const Text('Link it'),
              ),
            ],
          ),
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(widget.task.summary, style: CumulusText.body),
          const SizedBox(height: 18),
          const FieldLabel('What arrived'),
          for (final evidence in widget.task.evidence
              .where((e) => !e.label.startsWith('Candidate')))
            Padding(
              padding: const EdgeInsets.only(bottom: 4),
              child: Row(
                children: [
                  SizedBox(
                    width: 190,
                    child: Text(evidence.label, style: CumulusText.meta),
                  ),
                  Expanded(
                    child: Text(evidence.value, style: CumulusText.body),
                  ),
                ],
              ),
            ),
          const SizedBox(height: 18),
          const FieldLabel(
            'Candidates',
            hint: 'ranked by the resolver, with the reason for each score',
          ),
          if (candidates.isEmpty)
            Text(
              'The resolver found nothing to compare against — this is the '
              'first entity of its type.',
              style: CumulusText.meta,
            )
          else
            RadioGroup<String>(
              groupValue: _chosen,
              onChanged: (value) => setState(() => _chosen = value),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  for (final candidate in candidates)
                    RadioListTile<String>(
                      value: candidate.id,
                      dense: true,
                      title: Text(candidate.label, style: CumulusText.body),
                      subtitle: Text(candidate.id, style: CumulusText.mono),
                    ),
                ],
              ),
            ),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Proposed merge
// ---------------------------------------------------------------------------

/// A `merge_review` Task, opened onto the proposal it points at.
///
/// The odd one out among these screens, deliberately: the other three look at
/// facts that arrived, and this one looks at an assertion the sweep already
/// wrote into the graph at `in_review`. There is nothing here to correct — the
/// two records are what their sources say they are — so the verdict is binary
/// and every consequence is about the edge rather than about the records.
///
/// It loads the workbench's own payload and finds this Task's case in it,
/// rather than fetching the two objects itself. One composition, two surfaces:
/// what a steward reads in the modal is the row the workbench draws, so the
/// two can never describe one proposal differently.
class _MergeReview extends StatefulWidget {
  final ApiClient api;
  final QueueTask task;

  const _MergeReview({required this.api, required this.task});

  @override
  State<_MergeReview> createState() => _MergeReviewState();
}

class _MergeReviewState extends State<_MergeReview> {
  IdentityProposal? _proposal;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final screen = await widget.api.identityScreen();
      if (!mounted) return;
      setState(() {
        _proposal = screen.byCaseRef(widget.task.caseRef);
        _error = _proposal == null
            ? 'This task points at ${widget.task.caseRef}, and the '
                  'counterparty book holds no such proposal. It may have been '
                  'withdrawn and purged, or raised against another world.'
            : null;
      });
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    final proposal = _proposal;
    return _ReviewFrame(
      eyebrow: 'Proposed merge · ${widget.task.queue}',
      title: widget.task.title,
      subtitle:
          'Two records the book already holds, and one question: are they one '
          'party? Nothing merges either way — the proposal\'s status flips, '
          'reversibly, and both records keep everything they hold.',
      footer: Text(
        proposal == null
            ? 'Reading the proposal this task points at.'
            : 'Proposed by the identity sweep on the evidence above. A verdict '
                  'is recorded against ${widget.task.id} and flips the link; '
                  'it does not collapse the two records.',
        style: CumulusText.meta,
      ),
      child: _error != null
          ? ErrorNotice(message: _error!, onRetry: _load)
          : proposal == null
          ? const CumulusLoading()
          : MergeReviewPanel(
              proposal: proposal,
              api: widget.api,
              // The dialog does not commit: it answers with a verdict, and the
              // Task pane posts it — so a merge decision travels the same path
              // as every other decision in the queue.
              onDecide: proposal.gate.mayDecide
                  ? (verdict, note) => Navigator.of(
                      context,
                    ).pop(ReviewOutcome(verdict: verdict, note: note))
                  : null,
            ),
    );
  }
}

// ---------------------------------------------------------------------------
// Systems disagree
// ---------------------------------------------------------------------------

/// A `state_disagreement` Task, opened onto the finding it points at.
///
/// The merge review's sibling and the last of the four, with one difference
/// that governs everything about it: approving here **leaves Cumulus**. The
/// other three screens change what this system holds — a corrected total, a
/// first write on a record, an edge's status. This one asks a customer's ERP to
/// move a sales order, and there is no undo.
///
/// It loads the order desk's own payload and finds this Task's case in it,
/// rather than fetching the finding, the two rows and the write table itself.
/// One composition, two surfaces — what a controller reads in the modal is what
/// the dashboard counts, so the two can never describe one finding differently.
class _DisagreementReview extends StatefulWidget {
  final ApiClient api;
  final QueueTask task;

  const _DisagreementReview({required this.api, required this.task});

  @override
  State<_DisagreementReview> createState() => _DisagreementReviewState();
}

class _DisagreementReviewState extends State<_DisagreementReview> {
  DisagreementCase? _finding;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final screen = await widget.api.disagreementScreen();
      if (!mounted) return;
      setState(() {
        _finding = screen.byCaseRef(widget.task.caseRef);
        _error = _finding == null
            ? 'This task points at ${widget.task.caseRef}, and the order desk '
                  'holds no such finding. It may have been superseded by a '
                  'later sweep, or raised against another world.'
            : null;
      });
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    final finding = _finding;
    return _ReviewFrame(
      eyebrow: 'Systems disagree · ${widget.task.queue}',
      title: widget.task.title,
      subtitle:
          'Two of the customer\'s own systems, one commitment, two answers. '
          'Cumulus found the pair and has written to neither — releasing is a '
          'decision you make here, and it reaches the ERP.',
      footer: Text(
        finding == null
            ? 'Reading the finding this task points at.'
            : 'Found by the disagreement sweep on the evidence above. A '
                  'verdict is recorded against ${widget.task.id}; approving '
                  'starts a run that writes to the ERP and reads it back, and '
                  'leaving it as it is settles the finding here.',
        style: CumulusText.meta,
      ),
      child: _error != null
          ? ErrorNotice(message: _error!, onRetry: _load)
          : finding == null
          ? const CumulusLoading()
          : DisagreementReviewPanel(
              finding: finding,
              api: widget.api,
              // The dialog does not commit. It answers with a verdict and the
              // Task pane posts it, so a release decision travels the same
              // authorization path as every other decision in the queue —
              // which is where the Action Gateway actually checks it.
              onDecide: finding.gate.mayDecide
                  ? (verdict, note) => Navigator.of(
                      context,
                    ).pop(ReviewOutcome(verdict: verdict, note: note))
                  : null,
            ),
    );
  }
}
