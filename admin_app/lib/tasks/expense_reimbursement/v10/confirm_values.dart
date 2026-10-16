/// Your own receipt, and the question "is this what it says?" —
/// expense-reimbursement v10.
///
/// v9 asked *somebody else*, and only when a check fired. That made its screen
/// an audit: the escalation led, the reviewer was a clerk ruling on a stranger's
/// dinner, and a clean submission never reached a person at all — which meant a
/// model's reading of a photograph was written to the graph with nobody in the
/// loop. v10 raises the gate on every run and routes it to the submitter,
/// because the person holding the receipt is the only one who can say what is
/// printed on it.
///
/// So the screen is built around one pairing, repeated per document: **the page
/// on one side, what was read off it on the other, and the submitter's own memo
/// about it beside both.** The policy findings are context on that answer, not
/// the reason for asking — a person told "confirm this" and a person told "this
/// was flagged" read the same screen differently, and only the first sentence
/// is true of every run.
///
/// Two orderings, deliberately different. **Documents** lead with the ones
/// carrying a finding, the way the invoice review leads with the fields in
/// doubt: that is where a second look is worth something. **Lines** stay in
/// their ordinal order inside a document, because the ordinal is the identity
/// the correction is keyed by, and re-sorting a numbered list somebody is
/// typing into moves a field out from under a cursor.
///
/// The screen never commits. It pops a [ReviewOutcome] and the Task pane posts
/// it, so every decision travels one authorization path.
library;

import 'dart:typed_data';

import 'package:flutter/material.dart';

import '../../../models/data_layer.dart';
import '../../../models/task.dart';
import '../../../services/api_client.dart';
import '../../../theme/cumulus_theme.dart';
import '../../../widgets/document_drawer.dart';
import '../../../widgets/ui_kit.dart';
import '../../shared/review_frame.dart';
import '../models.dart';

// ---------------------------------------------------------------------------
// What the proposal carries
// ---------------------------------------------------------------------------

/// What one metered call read off one receipt, as the Task reports it.
///
/// Every field is optional on the wire and none of them is invented here: a
/// reading that came back without a date has no date, and a screen that filled
/// one in would be answering the question it was opened to ask.
class ReceiptReading {
  final String vendor;
  final String date;
  final double totalUsd;
  final String category;

  /// 0–1 from the extractor. Zero means it did not say, which is a different
  /// claim from "certainly wrong" and is rendered differently.
  final double confidence;

  const ReceiptReading({
    this.vendor = '',
    this.date = '',
    this.totalUsd = 0,
    this.category = '',
    this.confidence = 0,
  });

  factory ReceiptReading.fromJson(Map<String, dynamic> json) => ReceiptReading(
    vendor: json['vendor'] as String? ?? '',
    date: json['date'] as String? ?? '',
    totalUsd: (json['total_usd'] as num? ?? 0).toDouble(),
    category: json['category'] as String? ?? '',
    confidence: (json['confidence'] as num? ?? 0).toDouble(),
  );
}

/// One row of the confirm proposal: a stored document, the submitter's memo
/// about it, and the extraction summary for it.
///
/// The lines are **not** here. They are on the request row the screen already
/// fetches, unchanged from v9 — a second copy on the Task would be the same
/// facts able to disagree with themselves.
class ConfirmRow {
  final String documentId;
  final String filename;
  final String memo;

  /// Null when the row carried no `extraction` key at all — a document stored
  /// but never read. Different from a reading whose fields are blank, and the
  /// screen says which.
  final ReceiptReading? reading;

  const ConfirmRow({
    required this.documentId,
    this.filename = '',
    this.memo = '',
    this.reading,
  });

  String get label => filename.isNotEmpty ? filename : documentId;

  factory ConfirmRow.fromJson(Map<String, dynamic> json) => ConfirmRow(
    documentId: json['document_id'] as String? ?? '',
    filename: json['filename'] as String? ?? '',
    // `memo` is v10's name for it; `note` is what a run recorded before the
    // rename put there, and a resumed run must still render.
    memo: json['memo'] as String? ?? json['note'] as String? ?? '',
    reading: json['extraction'] == null
        ? null
        : ReceiptReading.fromJson(
            (json['extraction'] as Map).cast<String, dynamic>(),
          ),
  );
}

/// The proposal's document rows, or null when the version that raised this task
/// put none there.
///
/// Null and empty stay different facts here for the same reason they do for
/// field verdicts: null is a flow that never reported per-document readings —
/// a v9 task answered through this screen, say — and the honest response is to
/// show the request as it stands rather than to claim nothing was read. Empty
/// is a submission with no documents, which v10 refuses at the door and so
/// should never arrive.
List<ConfirmRow>? confirmRows(TaskProposal? proposal) {
  final raw = proposal?.rawFields;
  if (raw == null) return null;
  return [for (final row in raw) ConfirmRow.fromJson(row)];
}

// ---------------------------------------------------------------------------
// Drafts
// ---------------------------------------------------------------------------

/// One editable line. Controllers rather than a value object because a person
/// types into these and half-typed input has to survive a rebuild.
class _LineDraft {
  /// Null on a line the submitter added, which is what tells the payload
  /// builder to send it as an addition rather than a patch.
  final int? ordinal;

  final String documentId;
  final double documentTotalUsd;
  final TextEditingController vendor;
  final TextEditingController date;
  final TextEditingController requested;
  final TextEditingController purpose;
  String category;
  bool removed = false;
  bool touched = false;

  _LineDraft({
    this.ordinal,
    required this.documentId,
    required this.documentTotalUsd,
    required String vendorText,
    required String dateText,
    required String requestedText,
    required String purposeText,
    required this.category,
  })  : vendor = TextEditingController(text: vendorText),
        date = TextEditingController(text: dateText),
        requested = TextEditingController(text: requestedText),
        purpose = TextEditingController(text: purposeText);

  void dispose() {
    vendor.dispose();
    date.dispose();
    requested.dispose();
    purpose.dispose();
  }

  double get requestedUsd => double.tryParse(requested.text.trim()) ?? 0;
}

const _categories = [
  'meals', 'travel', 'lodging', 'mileage', 'supplies', 'software',
  'alcohol', 'gifts', 'entertainment', 'other',
];

// ---------------------------------------------------------------------------
// The screen the registry opens
// ---------------------------------------------------------------------------

/// Fetches the request, then hands it to the panel.
class ConfirmValues extends StatefulWidget {
  final ApiClient api;
  final QueueTask task;

  const ConfirmValues({super.key, required this.api, required this.task});

  @override
  State<ConfirmValues> createState() => _ConfirmValuesState();
}

class _ConfirmValuesState extends State<ConfirmValues> {
  Reimbursement? _request;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _error = null);
    try {
      final request = await widget.api.getReimbursement(widget.task.caseRef);
      if (!mounted) return;
      setState(() => _request = request);
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    final request = _request;
    if (_error != null) {
      return _frame(ErrorNotice(message: _error!, onRetry: _load));
    }
    if (request == null) return _frame(const CumulusLoading());
    return ConfirmValuesPanel(
      task: widget.task,
      request: request,
      rows: confirmRows(widget.task.proposal),
      // One page fetch per document, started lazily by the panel: the receipts
      // are the slowest thing on the screen and the values are the decidable
      // part, so the numbers paint first and the pages fill in beside them.
      artifact: (id) => _fetchArtifact(widget.api, id),
      onDecide: (outcome) => Navigator.of(context).pop(outcome),
    );
  }

  /// The chrome around a state with no decision in it yet. The panel draws its
  /// own frame, because the footer's controls depend on its drafts.
  Widget _frame(Widget child) => ReviewFrame(
    eyebrow: 'Confirm your expense · ${widget.task.queue}',
    title: widget.task.title,
    subtitle: _subtitle,
    footer: const SizedBox.shrink(),
    child: child,
  );
}

const _subtitle =
    'What was read off your receipts, beside the receipts. Correct anything '
    'that is wrong — an amount, a date, a category, a missing purpose — or '
    'split one receipt into several lines. Confirming records these values as '
    'they stand.';

/// A document and its bytes, as one settled future — the same pairing the
/// document drawer loads, and for the same reason: the custody record is worth
/// showing even when the bytes will not come.
Future<(StoredDocument, Uint8List?)> _fetchArtifact(
  ApiClient api,
  String documentId,
) async {
  final document = await api.getDocument(documentId);
  if (!document.hasBytes) return (document, null);
  try {
    return (document, await api.documentContent(documentId));
  } on ApiException {
    return (document, null);
  }
}

// ---------------------------------------------------------------------------
// The panel
// ---------------------------------------------------------------------------

/// Below this the two panes stack. The dialog opens at 1080, so side by side is
/// the normal case — with the values above the page when it stacks, because the
/// values are what the screen is for.
const double _twoPaneWidth = 880;

/// The screen proper, over data somebody else fetched.
///
/// Public and data-in so it can be pumped with a fixture: what it says when a
/// reading is missing, when a memo is blank, and when the proposal carried no
/// rows at all are three claims about the system, and those are worth holding
/// still.
class ConfirmValuesPanel extends StatefulWidget {
  final QueueTask task;
  final Reimbursement request;

  /// Null when the version that raised the task reported no per-document
  /// readings — see [confirmRows].
  final List<ConfirmRow>? rows;

  /// The page for one document id. Called once per document the screen shows.
  final Future<(StoredDocument, Uint8List?)> Function(String documentId)?
      artifact;

  final ValueChanged<ReviewOutcome> onDecide;

  const ConfirmValuesPanel({
    super.key,
    required this.task,
    required this.request,
    required this.rows,
    required this.artifact,
    required this.onDecide,
  });

  @override
  State<ConfirmValuesPanel> createState() => _ConfirmValuesPanelState();
}

class _ConfirmValuesPanelState extends State<ConfirmValuesPanel> {
  final _note = TextEditingController();
  final List<_LineDraft> _drafts = [];
  final List<_LineDraft> _added = [];

  /// The documents in the order the submitter meets them: the ones carrying a
  /// finding first. Settled once with the drafts rather than recomputed per
  /// build, so the order cannot shift under a rebuild.
  final List<ConfirmRow> _ordered = [];

  /// The document whose page is shown, and which page of it. The screen opens
  /// on the first document with something worth a second look.
  String _open = '';
  int _page = 1;

  /// One future per document, so switching back to a receipt does not refetch
  /// its bytes.
  final Map<String, Future<(StoredDocument, Uint8List?)>> _pages = {};

  @override
  void initState() {
    super.initState();
    _build();
  }

  @override
  void didUpdateWidget(ConfirmValuesPanel old) {
    super.didUpdateWidget(old);
    if (old.request.id != widget.request.id) {
      _disposeDrafts();
      _drafts.clear();
      _added.clear();
      _pages.clear();
      _build();
    }
  }

  @override
  void dispose() {
    _disposeDrafts();
    _note.dispose();
    super.dispose();
  }

  void _disposeDrafts() {
    for (final draft in [..._drafts, ..._added]) {
      draft.dispose();
    }
  }

  void _build() {
    _drafts.addAll(widget.request.lines.map(_toDraft));
    _ordered
      ..clear()
      ..addAll(_flaggedFirst());
    _open = _ordered.isEmpty ? '' : _ordered.first.documentId;
    _page = 1;
  }

  _LineDraft _toDraft(ExpenseLine line) => _LineDraft(
    ordinal: line.ordinal,
    documentId: line.documentId,
    documentTotalUsd: line.documentTotalUsd,
    vendorText: line.vendor,
    dateText: line.date,
    requestedText: line.requestedUsd.toStringAsFixed(2),
    purposeText: line.purpose,
    category: line.category,
  );

  // -------------------------------------------------------------------------
  // What the panel knows about itself
  // -------------------------------------------------------------------------

  /// The rows this screen shows, which is the proposal's when it has them and
  /// the request's own documents when it does not.
  ///
  /// The fallback is not a guess: a v9 task has the same documents on the
  /// request row, minus the reading. It renders as a document with no reading,
  /// which is exactly what it is.
  List<ConfirmRow> get _rows =>
      widget.rows ??
      [
        for (final document in widget.request.documents)
          ConfirmRow(
            documentId: document.documentId,
            filename: document.filename,
            memo: document.memo,
          ),
      ];

  /// Line ordinals the governed check said something about.
  Set<int> get _flagged => {
    for (final escalation in widget.request.escalations)
      if (escalation.lineOrdinal > 0) escalation.lineOrdinal,
  };

  /// The findings against one document, in the check's own words.
  List<Escalation> _findingsOn(String documentId) {
    final ordinals = {
      for (final line in widget.request.lines)
        if (line.documentId == documentId) line.ordinal,
    };
    return [
      for (final escalation in widget.request.escalations)
        // A finding with no line — an over-limit on the whole request — is
        // about every document and is attributed to none of them; the banner
        // above carries it instead.
        if (escalation.lineOrdinal > 0 &&
            ordinals.contains(escalation.lineOrdinal))
          escalation,
    ];
  }

  List<ConfirmRow> _flaggedFirst() {
    final rows = _rows;
    final indexed = [for (var i = 0; i < rows.length; i++) (i, rows[i])]
      ..sort((a, b) {
        final flags = _findingsOn(b.$2.documentId).length
            .compareTo(_findingsOn(a.$2.documentId).length);
        // The arriving order is the tie-break, because `List.sort` is not
        // stable and two equally clean receipts swapping places between builds
        // is the kind of nothing that costs somebody a click.
        return flags != 0 ? flags : a.$1.compareTo(b.$1);
      });
    return [for (final entry in indexed) entry.$2];
  }

  /// The lines that belong to one document, in ordinal order.
  List<_LineDraft> _linesOn(String documentId) => [
    for (final draft in _drafts)
      if (draft.documentId == documentId) draft,
    for (final draft in _added)
      if (draft.documentId == documentId) draft,
  ];

  /// Lines whose `document_id` names no document on this screen. Shown rather
  /// than dropped: a line nobody can see is a line nobody can correct, and it
  /// is still in the total that gets recorded.
  List<_LineDraft> get _orphans {
    final known = {for (final row in _rows) row.documentId};
    return [
      for (final draft in [..._drafts, ..._added])
        if (!known.contains(draft.documentId)) draft,
    ];
  }

  double get _total => [..._drafts.where((d) => !d.removed), ..._added]
      .fold<double>(0, (sum, draft) => sum + draft.requestedUsd);

  bool get _dirty {
    if (_added.isNotEmpty) return true;
    for (final draft in _drafts) {
      if (draft.removed || draft.touched) return true;
      final original = widget.request.lines.firstWhere(
        (line) => line.ordinal == draft.ordinal,
      );
      if (draft.vendor.text.trim() != original.vendor) return true;
      if (draft.date.text.trim() != original.date) return true;
      if (draft.purpose.text.trim() != original.purpose) return true;
      if (draft.category != original.category) return true;
      if ((draft.requestedUsd - original.requestedUsd).abs() > 0.004) {
        return true;
      }
    }
    return false;
  }

  /// The patch the flow's applier reads: edited lines by ordinal, additions
  /// without one, and the ordinals dropped.
  ///
  /// Every kept line travels, not only the ones that moved. That is what the
  /// applier expects — it patches by ordinal — and it is also what makes the
  /// correction set honest: the flow pairs each patched value against what the
  /// model read, so a total the submitter quietly fixed becomes a raw→corrected
  /// pair even though no check had doubted it. Those are the most interesting
  /// rows in the set.
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
        'date': draft.date.text.trim(),
        'category': draft.category,
        'requested_usd': draft.requestedUsd,
        'purpose': draft.purpose.text.trim(),
      });
    }
    final additions = [
      for (final draft in _added)
        {
          'vendor': draft.vendor.text.trim(),
          'date': draft.date.text.trim(),
          'category': draft.category,
          'requested_usd': draft.requestedUsd,
          // A line the extraction missed has no paper total of its own to
          // claim against, so what is claimed is what it supports.
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

  void _decide(String verdict) => widget.onDecide(ReviewOutcome(
    verdict: verdict,
    note: _note.text.trim(),
    payload: verdict == 'corrected' ? _payload() : const {},
  ));

  void _addLineTo(String documentId) => setState(() {
    _added.add(_LineDraft(
      documentId: documentId,
      documentTotalUsd: 0,
      vendorText: '',
      dateText: '',
      requestedText: '',
      purposeText: '',
      category: 'other',
    ));
  });

  // -------------------------------------------------------------------------
  // Build
  // -------------------------------------------------------------------------

  @override
  Widget build(BuildContext context) {
    return ReviewFrame(
      eyebrow: 'Confirm your expense · ${widget.task.queue}',
      title: widget.task.title,
      subtitle: _subtitle,
      footer: DecisionBar(
        note: _note,
        dirty: _dirty,
        correctLabel: 'Apply and re-check',
        onApprove: () => _decide('approved'),
        onReject: () => _decide('rejected'),
        onCorrect: () => _decide('corrected'),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (widget.task.subjectEmail.isNotEmpty) ...[
            // Whose expense this is. Quiet, and worth saying even on the
            // submitter's own screen: the governed routing sends this back to
            // them, and the one case where it does not — no personal desk
            // could be opened, so it landed on the shared expense desk — is
            // the case where "your receipts" is being read by somebody else.
            Align(
              alignment: Alignment.centerLeft,
              child: MetaPill('about ${widget.task.subjectEmail}'),
            ),
            const SizedBox(height: 12),
          ],
          _lede(),
          const SizedBox(height: 18),
          LayoutBuilder(
            builder: (context, constraints) {
              if (constraints.maxWidth < _twoPaneWidth) {
                return Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    ..._workPane(),
                    const SizedBox(height: 24),
                    ..._pagePane(),
                  ],
                );
              }
              return Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: _workPane(),
                    ),
                  ),
                  const SizedBox(width: 26),
                  SizedBox(
                    width: 340,
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: _pagePane(),
                    ),
                  ),
                ],
              );
            },
          ),
        ],
      ),
    );
  }

  /// What the screen is asking, before it asks it.
  ///
  /// Three states, and none of them is the same as the others: nothing was
  /// flagged (the common case, and a fast confirm), something was (worth a
  /// second look, in the check's own words), or the version that raised this
  /// reported no readings at all.
  Widget _lede() {
    if (widget.rows == null) {
      return _Lede(
        tone: CumulusTone.neutral,
        title: 'This run reported no per-receipt reading',
        body: 'The version that raised this task did not say what it read off '
            'each document, so the values below are the request as it stands '
            'rather than a reading you can check against a page. Correcting '
            'any of them still re-runs the checks.',
      );
    }
    final findings = widget.request.escalations;
    if (findings.isEmpty) {
      return const _Lede(
        tone: CumulusTone.success,
        title: 'Nothing was flagged — this is yours to confirm',
        body: 'Every line cleared the governed policy. You are looking at this '
            'because a model read a photograph of your receipt and you are the '
            'only person who can say whether it read it correctly, not because '
            'anything is in doubt. Confirming is the whole job.',
      );
    }
    return _Lede(
      tone: CumulusTone.warn,
      title: findings.length == 1
          ? 'One thing is worth a second look'
          : '${findings.length} things are worth a second look',
      body: 'The governed check had something to say about the lines below. It '
          'did not stop your expense and it did not send it to anybody else — '
          'you are still the one confirming it.',
      reasons: [for (final finding in findings) finding.detail],
    );
  }

  // -------------------------------------------------------------------------
  // The work pane
  // -------------------------------------------------------------------------

  List<Widget> _workPane() {
    final rows = _ordered;
    final orphans = _orphans;
    return [
      if (widget.request.commentary.isNotEmpty) ...[
        const FieldLabel(
          'What you said about the whole request',
          hint: 'read by the flow — a stated purpose here may be all that was '
              'missing',
        ),
        Text('“${widget.request.commentary}”', style: CumulusText.body),
        const SizedBox(height: 18),
      ],
      if (rows.isEmpty)
        _muted(
          'This request names no documents. v10 refuses a submission with no '
          'bytes at the door, so a request that reached a confirmation with '
          'none was filed some other way — the lines below are the whole of '
          'what is known.',
        ),
      for (var i = 0; i < rows.length; i++) ...[
        if (i > 0) const SizedBox(height: 14),
        _documentCard(rows[i], i + 1, rows.length),
      ],
      if (orphans.isNotEmpty) ...[
        const SizedBox(height: 18),
        const FieldLabel(
          'Lines with no receipt on this screen',
          hint: 'still claimed, still correctable',
        ),
        for (final draft in orphans) _editor(draft),
      ],
      const SizedBox(height: 14),
      Row(
        mainAxisAlignment: MainAxisAlignment.end,
        children: [
          Text('Total claimed', style: CumulusText.statLabel),
          const SizedBox(width: 14),
          Text('\$${_total.toStringAsFixed(2)}', style: CumulusText.statValue),
        ],
      ),
      const SizedBox(height: 8),
      Text(
        'Governed limit '
        '\$${widget.request.policyLimitUsd.toStringAsFixed(2)} · '
        '${widget.request.policyRef}',
        style: CumulusText.meta,
      ),
    ];
  }

  Widget _documentCard(ConfirmRow row, int position, int of) {
    final findings = _findingsOn(row.documentId);
    final lines = _linesOn(row.documentId);
    final selected = _open == row.documentId;
    return Container(
      padding: const EdgeInsets.fromLTRB(14, 12, 14, 14),
      decoration: BoxDecoration(
        color: selected ? CumulusColors.accentSoft : CumulusColors.surface,
        borderRadius: BorderRadius.circular(CumulusRadius.control),
        border: Border.all(
          color: selected ? CumulusColors.accent : CumulusColors.borderStrong,
          width: selected ? 1.6 : 1,
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Text(
                  'Receipt $position of $of · ${row.label}',
                  style: CumulusText.rowTitle,
                ),
              ),
              const SizedBox(width: 10),
              if (findings.isNotEmpty)
                StatusBadge(
                  findings.length == 1
                      ? '1 finding'
                      : '${findings.length} findings',
                  tone: CumulusTone.warn,
                  dense: true,
                ),
              if (!selected) ...[
                const SizedBox(width: 8),
                RowAction(
                  label: 'Show the page',
                  onTap: () => setState(() {
                    _open = row.documentId;
                    _page = 1;
                  }),
                ),
              ],
            ],
          ),
          const SizedBox(height: 10),
          _reading(row),
          const SizedBox(height: 12),
          _memo(row),
          if (findings.isNotEmpty) ...[
            const SizedBox(height: 12),
            for (final finding in findings)
              Padding(
                padding: const EdgeInsets.only(bottom: 3),
                child: Text(
                  '· ${finding.kind}: ${finding.detail}',
                  style: CumulusText.body,
                ),
              ),
          ],
          const SizedBox(height: 14),
          Row(
            children: [
              const Expanded(
                child: FieldLabel(
                  'Lines',
                  hint: 'facts, not documents — one receipt can be two lines',
                ),
              ),
              TextButton.icon(
                onPressed: () => _addLineTo(row.documentId),
                icon: const Icon(Icons.add, size: 16),
                label: const Text('Add a line'),
              ),
            ],
          ),
          const SizedBox(height: 4),
          if (lines.isEmpty)
            Text(
              'No line was projected from this receipt. Nothing is being '
              'claimed against it — add one if it should be.',
              style: CumulusText.meta,
            ),
          for (final draft in lines) _editor(draft),
        ],
      ),
    );
  }

  /// What the model read off this page. Shown as values, not as inputs: the
  /// editable copy is the line below, and two controls over one fact is how a
  /// person types two answers to one question.
  Widget _reading(ConfirmRow row) {
    final reading = row.reading;
    if (reading == null) {
      return Text(
        'Nothing was read off this document. The bytes are stored and no '
        'extraction reported against them — which is not the same as a '
        'reading that came back blank.',
        style: CumulusText.meta,
      );
    }
    return Wrap(
      spacing: 18,
      runSpacing: 8,
      children: [
        _fact('Vendor', reading.vendor),
        _fact('Date', reading.date),
        _fact('Total', '\$${reading.totalUsd.toStringAsFixed(2)}'),
        _fact('Category', reading.category),
        _fact(
          'Confidence',
          reading.confidence <= 0
              ? 'not reported'
              : '${(reading.confidence * 100).round()}%',
        ),
      ],
    );
  }

  Widget _fact(String label, String value) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      Text(label.toUpperCase(), style: CumulusText.statLabel),
      const SizedBox(height: 3),
      Text(value.isEmpty ? '—' : value, style: CumulusText.mono),
    ],
  );

  /// The submitter's own sentence about this receipt.
  ///
  /// Beside the reading rather than under the whole request, because that is
  /// where it belongs: three receipts in one envelope each get their own. It is
  /// never given to the extractor — the run reads it for a stated claim and a
  /// purpose — and saying so here is the difference v10 exists to make.
  Widget _memo(ConfirmRow row) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      const FieldLabel(
        'Your note on this receipt',
        hint: 'read by the flow, never given to the extractor',
      ),
      Text(
        row.memo.isEmpty
            ? 'You wrote nothing about this one.'
            : '“${row.memo}”',
        style: row.memo.isEmpty ? CumulusText.meta : CumulusText.body,
      ),
    ],
  );

  Widget _editor(_LineDraft draft) => _LineEditor(
    draft: draft,
    isNew: draft.ordinal == null,
    flagged: draft.ordinal != null && _flagged.contains(draft.ordinal),
    onChanged: () => setState(() {}),
    onRemove: () => setState(() {
      if (draft.ordinal == null) {
        _added.remove(draft);
        draft.dispose();
        return;
      }
      draft.removed = !draft.removed;
    }),
  );

  // -------------------------------------------------------------------------
  // The page pane
  // -------------------------------------------------------------------------

  List<Widget> _pagePane() {
    final rows = _ordered;
    if (widget.artifact == null || rows.isEmpty) {
      return [
        const FieldLabel('The receipt', hint: 'no page'),
        _muted(
          'There is no artifact to draw here, so the values beside are the '
          'whole of what is known. Nothing on this screen was read off a page '
          'this build can show you.',
        ),
      ];
    }
    final open = rows.firstWhere(
      (row) => row.documentId == _open,
      orElse: () => rows.first,
    );
    final page = _pages.putIfAbsent(
      open.documentId,
      () => widget.artifact!(open.documentId),
    );
    return [
      if (rows.length > 1) ...[
        Wrap(
          spacing: 6,
          runSpacing: 6,
          children: [
            for (var i = 0; i < rows.length; i++)
              LensChip(
                label: 'RECEIPT ${i + 1}',
                tone: CumulusTone.accent,
                selected: rows[i].documentId == open.documentId,
                onTap: () => setState(() {
                  _open = rows[i].documentId;
                  _page = 1;
                }),
              ),
          ],
        ),
        const SizedBox(height: 12),
      ],
      FutureBuilder<(StoredDocument, Uint8List?)>(
        future: page,
        builder: (context, snapshot) {
          if (snapshot.hasError) {
            return Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                const FieldLabel('The receipt',
                    hint: 'the artifact did not load'),
                _muted(
                  'The values are still confirmable: what was read is beside, '
                  'and correcting it does not need the page. '
                  '${snapshot.error}',
                ),
              ],
            );
          }
          if (!snapshot.hasData) {
            return const Padding(
              padding: EdgeInsets.symmetric(vertical: 40),
              child: Center(child: CircularProgressIndicator()),
            );
          }
          final (document, bytes) = snapshot.data!;
          final pages = document.pages < 1 ? 1 : document.pages;
          final shown = _page.clamp(1, pages);
          return Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              FieldLabel(
                'The receipt',
                hint: document.filename.isEmpty
                    ? document.id
                    : document.filename,
              ),
              if (pages > 1) ...[
                Wrap(
                  spacing: 6,
                  runSpacing: 6,
                  children: [
                    for (var n = 1; n <= pages; n++)
                      LensChip(
                        label: 'PAGE $n',
                        tone: CumulusTone.accent,
                        selected: n == shown,
                        onTap: () => setState(() => _page = n),
                      ),
                  ],
                ),
                const SizedBox(height: 12),
              ],
              DocumentPageView(
                document: document,
                bytes: bytes,
                page: shown,
                maxWidth: 340,
              ),
              const SizedBox(height: 10),
              // No boxes, and that is v10's doing rather than an omission: v9
              // persisted regions the stub reader had invented, so a screen
              // drew confident rectangles over a real receipt at coordinates
              // nobody had looked at. Nothing here draws a box it did not get.
              Text(
                'Nothing is boxed. This version stores a region only when a '
                'reader actually located a value on the page, and this '
                'reading located none.',
                style: CumulusText.meta,
              ),
            ],
          );
        },
      ),
    ];
  }

  Widget _muted(String message) => Container(
    width: double.infinity,
    padding: const EdgeInsets.all(14),
    decoration: BoxDecoration(
      color: CumulusColors.surfaceMuted,
      borderRadius: BorderRadius.circular(CumulusRadius.control),
    ),
    child: Text(message, style: CumulusText.body),
  );
}

// ---------------------------------------------------------------------------
// The pieces
// ---------------------------------------------------------------------------

/// What the screen is asking, in one sentence, before it asks it.
class _Lede extends StatelessWidget {
  final CumulusTone tone;
  final String title;
  final String body;

  /// The check's own sentences, verbatim. A screen paraphrasing "the $612.00
  /// block rate is over the $200.00 limit" into "over policy" has thrown away
  /// the answer.
  final List<String> reasons;

  const _Lede({
    required this.tone,
    required this.title,
    required this.body,
    this.reasons = const [],
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.fromLTRB(16, 13, 16, 14),
      decoration: BoxDecoration(
        color: tone.background,
        borderRadius: BorderRadius.circular(CumulusRadius.control),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            title,
            style: CumulusText.rowTitle.copyWith(color: tone.foreground),
          ),
          const SizedBox(height: 6),
          Text(body, style: CumulusText.body),
          if (reasons.isNotEmpty) const SizedBox(height: 8),
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

/// One line, editable. The same five facts the applier patches by ordinal.
class _LineEditor extends StatelessWidget {
  final _LineDraft draft;
  final bool isNew;
  final bool flagged;
  final VoidCallback onChanged;
  final VoidCallback onRemove;

  const _LineEditor({
    required this.draft,
    required this.onChanged,
    required this.onRemove,
    this.isNew = false,
    this.flagged = false,
  });

  @override
  Widget build(BuildContext context) {
    final over = draft.documentTotalUsd > 0 &&
        draft.requestedUsd > draft.documentTotalUsd + 0.005;
    return Opacity(
      opacity: draft.removed ? 0.45 : 1,
      child: Container(
        margin: const EdgeInsets.only(bottom: 10),
        padding: const EdgeInsets.fromLTRB(12, 10, 6, 10),
        decoration: BoxDecoration(
          color: CumulusColors.canvas,
          borderRadius: BorderRadius.circular(CumulusRadius.control),
          border: Border.all(
            color: isNew
                ? CumulusColors.accent
                : flagged
                    ? CumulusTone.warn.foreground
                    : CumulusColors.border,
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
                  width: 122,
                  child: TextField(
                    controller: draft.date,
                    enabled: !draft.removed,
                    onChanged: (_) => onChanged(),
                    decoration: const InputDecoration(
                      labelText: 'Date',
                      hintText: 'YYYY-MM-DD',
                      isDense: true,
                    ),
                  ),
                ),
                const SizedBox(width: 10),
                SizedBox(
                  width: 140,
                  child: DropdownButtonFormField<String>(
                    initialValue: draft.category,
                    isDense: true,
                    // The longest category is "entertainment", and a
                    // dropdown sized to its own contents overflows its box
                    // rather than shortening the label. Expanding it makes
                    // the control the width it was given at any font size.
                    isExpanded: true,
                    decoration: const InputDecoration(
                      labelText: 'Category',
                      isDense: true,
                    ),
                    items: [
                      for (final category in _categories)
                        DropdownMenuItem(
                          value: category,
                          child: Text(category),
                        ),
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
                      hintText: 'why this happened — required above the '
                          'governed amount',
                      isDense: true,
                    ),
                  ),
                ),
              ],
            ),
            if (draft.documentTotalUsd > 0) ...[
              const SizedBox(height: 6),
              Padding(
                padding: const EdgeInsets.only(left: 34),
                child: Text(
                  'receipt total '
                  '\$${draft.documentTotalUsd.toStringAsFixed(2)}',
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
