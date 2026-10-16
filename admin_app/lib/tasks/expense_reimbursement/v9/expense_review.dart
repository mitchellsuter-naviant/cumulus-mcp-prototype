import 'package:flutter/material.dart';

import '../../../models/task.dart';
import '../../../services/api_client.dart';
import '../../../theme/cumulus_theme.dart';
import '../../../widgets/ui_kit.dart';
import '../../shared/review_frame.dart';
import '../models.dart';

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

/// The reimbursement screen as expense-reimbursement v9 raises it.
///
/// A reviewer here is usually not disagreeing with the policy: the extraction
/// read a crumpled total wrong, a receipt covers two charges, or nobody said
/// what the dinner was for. So the lines are editable, one can be split off or
/// dropped, and the verdict travels as a correction the flow re-checks.
class ExpenseReview extends StatefulWidget {
  final ApiClient api;
  final QueueTask task;

  const ExpenseReview({super.key, required this.api, required this.task});

  @override
  State<ExpenseReview> createState() => _ExpenseReviewState();
}

class _ExpenseReviewState extends State<ExpenseReview> {
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
        documentId: '', filename: '', memo: '', byteSize: 0, mediaType: '',
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
    return ReviewFrame(
      eyebrow: 'Expense review · ${widget.task.queue}',
      title: widget.task.title,
      subtitle:
          'Correct what the extraction got wrong, split a receipt, add a line '
          'it missed, or supply the purpose — then the checks run again.',
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
          : request == null
              ? const CumulusLoading()
              : Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    WhyElevated(
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
                              message: document.memo.isEmpty
                                  ? 'No note on this one.'
                                  : document.memo,
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
