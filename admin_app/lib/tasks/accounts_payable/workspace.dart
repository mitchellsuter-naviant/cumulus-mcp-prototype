import 'dart:convert';

import 'package:file_selector/file_selector.dart';
import 'package:flutter/material.dart';

import 'models.dart';
import '../../models/run.dart';
import '../../models/session.dart';
import '../../models/task.dart';
import '../../services/api_client.dart';
import '../../theme/cumulus_theme.dart';
import '../../widgets/command_palette.dart' show ownsWorkspace, payablesQueue;
import '../../widgets/document_drawer.dart';
import '../../widgets/ref_chips.dart';
import '../../widgets/run_workbench.dart';
import '../../widgets/side_drawer.dart';
import '../../widgets/skeleton.dart';
import '../../widgets/ui_kit.dart';

/// The Accounts Payable workspace: enter an invoice, and the table of what the
/// accounts-payable flow made of every invoice so far.
///
/// What makes this table interesting is what it records *around* the
/// extraction: which canonical vendor the printed name resolved to and how,
/// how the billed lines paired against the purchase order — and against the
/// receipts, where a receiving feed has delivered any — and what the anomaly
/// screen made of the invoice against that vendor's own history. [InvoiceOutcomeView] shows all three, plus the
/// governed values the posting rule read — and it is the same widget for a row
/// you expand and for the run you just started, because they are the same
/// thing seen at two moments.
class PayablesView extends StatefulWidget {
  final ApiClient api;

  /// The acting session, so an empty table can say which of its two causes it
  /// is. Null only before the first session load.
  final Session? session;

  const PayablesView({super.key, required this.api, this.session});

  @override
  State<PayablesView> createState() => _PayablesViewState();
}

class _PayablesViewState extends State<PayablesView> {
  /// Whether the AP queue is this persona's work. The table is scoped
  /// server-side either way — this only decides what its emptiness *means*.
  /// Home's AP panel draws exactly this distinction; the workspace itself did
  /// not, and "No invoices in this state yet" told the person least able to
  /// check that the business was quiet.
  bool get _seesTheQueue => ownsWorkspace(widget.session, payablesQueue);

  PayablesScreen? _screen;
  String? _error;
  String _lens = 'all';
  String? _expanded;

  /// The run this session just started. Held apart from the table so it stays
  /// put while the table reloads under it — the outcome is the thing you were
  /// looking at, and having it scroll away into a list is how the old
  /// one-sentence confirmation managed to say nothing.
  PayableOutcome? _submitted;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final screen = await widget.api.payablesScreen();
      if (mounted) {
        setState(() {
          _screen = screen;
          _error = null;
        });
      }
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_error != null) {
      return CumulusEmptyState(
        icon: Icons.cloud_off,
        title: 'Could not load the workspace',
        message: _error!,
      );
    }
    final screen = _screen;
    if (screen == null) return _skeleton();
    final stats = screen.stats;
    final visible = _lens == 'all'
        ? screen.invoices
        : screen.invoices.where((o) => o.invoice.state == _lens).toList();

    return SingleChildScrollView(
      padding: const EdgeInsets.fromLTRB(28, 20, 28, 40),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _heading(),
          const SizedBox(height: 18),
          StatStrip(
            tiles: [
              StatTile(
                label: 'Invoices',
                value: '${stats.total}',
                caption: 'all time',
              ),
              StatTile(
                label: 'Posted',
                value: '${stats.posted}',
                caption:
                    '\$${stats.postedUsd.toStringAsFixed(2)} to the ledger',
              ),
              StatTile(
                label: 'Pending',
                value: '${stats.pending}',
                caption: 'suspended, waiting on a Task',
              ),
              StatTile(
                label: 'Denied',
                value: '${stats.denied}',
                caption: 'rejected on review',
              ),
              StatTile(
                label: 'Matched',
                value: '${stats.matched}',
                caption:
                    '${stats.matchVariance} variance · '
                    '${stats.matchBlocked} not received yet',
              ),
              StatTile(
                label: 'Anomalies',
                value: '${stats.anomalies}',
                caption: 'unusual for that vendor',
              ),
            ],
          ),
          const SizedBox(height: 16),
          InvoiceUploadCard(
            api: widget.api,
            submission: screen.submission,
            onSubmitted: (outcome) {
              setState(() => _submitted = outcome);
              _load();
            },
          ),
          if (_submitted != null) ...[
            const SizedBox(height: 16),
            SectionCard(
              title:
                  'Run ${_submitted!.invoice.runId} · '
                  '${_submitted!.invoice.invoiceNumber}',
              subtitle:
                  'what the flow made of it — the same view the table gives '
                  'this row from here on',
              child: InvoiceOutcomeView(
                api: widget.api,
                outcome: _submitted!,
                inset: false,
              ),
            ),
          ],
          const SizedBox(height: 16),
          FlowRunsCard(
            api: widget.api,
            flowId: 'accounts-payable',
            subtitle:
                'extract → resolve → match → screen → post, with every gate '
                'and governed read · click one to open it',
          ),
          const SizedBox(height: 16),
          _lensRail(),
          const SizedBox(height: 14),
          if (visible.isEmpty)
            // Three states, and only one of them used to be said. "Nothing at
            // this lens" is meaningless with ALL selected — no lens is on —
            // and an empty table has two causes that are opposite claims: no
            // invoices exist, or none of them is yours to see.
            CumulusEmptyState(
              icon: _seesTheQueue
                  ? Icons.table_rows_outlined
                  : Icons.lock_outline,
              title: !_seesTheQueue
                  ? 'Not scoped to you'
                  : _lens == 'all'
                  ? 'No invoices yet'
                  : 'Nothing $_lens',
              message: !_seesTheQueue
                  ? 'You are not bound to the ap-clerk queue, so this table '
                        'holds only invoices you filed yourself. This is what '
                        'you can see, not what there is.'
                  : _lens == 'all'
                  ? 'The accounts-payable flow has written no rows here. '
                        'Upload a document above, or let ap-intake '
                        'find one in the document system.'
                  : 'No invoice is in the $_lens state. Clear the lens to see '
                        'the whole table.',
            )
          else
            ListCard(
              title: 'Invoices',
              subtitle: '${visible.length} shown · click a row for its run',
              children: [for (final o in visible) _row(o)],
            ),
        ],
      ),
    );
  }

  /// The chrome that owes the table nothing. It is the same widget on the first
  /// frame and the hundredth, so the page can say what it is before it knows
  /// what is in it.
  Widget _heading() => PageHeading(
    eyebrow: 'Workspace',
    title: 'Accounts Payable',
    subtitle:
        'The accounts-payable flow writes this table: one extraction '
        'pass over the document, the vendor resolved against the '
        'master, then matched, screened and posted under policy.',
    trailing: OutlinedButton.icon(
      onPressed: _screen == null ? null : _load,
      icon: const Icon(Icons.refresh, size: 16),
      label: const Text('Refresh'),
    ),
  );

  Widget _lensRail() => Wrap(
    spacing: 8,
    children: [
      for (final lens in const [
        ('all', CumulusTone.neutral),
        ('posted', CumulusTone.success),
        ('pending', CumulusTone.warn),
        ('denied', CumulusTone.danger),
      ])
        LensChip(
          label: lens.$1.toUpperCase(),
          tone: lens.$2,
          selected: _lens == lens.$1,
          onTap: () => setState(() => _lens = lens.$1),
        ),
    ],
  );

  /// The page while the screen is in flight: heading and lens rail for real,
  /// grey geometry in the shape of the six-tile strip, the submission door and
  /// the invoice table. Nothing moves when the rows land except the fill.
  Widget _skeleton() => SingleChildScrollView(
    padding: const EdgeInsets.fromLTRB(28, 20, 28, 40),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _heading(),
        const SizedBox(height: 18),
        const SkeletonPulse(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              SkeletonStatStrip(count: 6),
              SizedBox(height: 16),
              SkeletonCard(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    SkeletonBar(width: 130, height: 13),
                    SizedBox(height: 10),
                    SkeletonBar(width: 340, height: 10),
                    SizedBox(height: 18),
                    Row(
                      children: [
                        SkeletonBar(
                          width: 232,
                          height: 34,
                          radius: CumulusRadius.control,
                        ),
                        SizedBox(width: 12),
                        SkeletonBar(
                          width: 108,
                          height: 34,
                          radius: CumulusRadius.control,
                        ),
                      ],
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 16),
        _lensRail(),
        const SizedBox(height: 14),
        const SkeletonPulse(
          child: SkeletonListCard(rows: 6, leadingCircle: false),
        ),
      ],
    ),
  );

  Widget _row(PayableOutcome outcome) {
    final r = outcome.invoice;
    final expanded = _expanded == r.id;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        InkWell(
          onTap: () => setState(() => _expanded = expanded ? null : r.id),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 12),
            child: Row(
              children: [
                Expanded(
                  flex: 3,
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        '${r.vendorName.isEmpty ? r.vendorSlug : r.vendorName}'
                        ' · ${r.invoiceNumber}',
                        style: CumulusText.rowTitle,
                      ),
                      const SizedBox(height: 2),
                      Text(
                        '${r.invoiceDate} · ${r.poNumber.isEmpty ? "no PO" : r.poNumber}'
                        ' · ${r.submittedBy}',
                        style: CumulusText.meta,
                      ),
                    ],
                  ),
                ),
                if (r.vendorResolution.isNotEmpty)
                  Tooltip(
                    message:
                        'the printed name resolved '
                        '${r.vendorResolution} '
                        '${r.vendorMethod.isEmpty ? '' : 'by ${r.vendorMethod} '}'
                        'at ${r.vendorConfidence.toStringAsFixed(2)}',
                    child: StatusBadge(
                      r.vendorResolution,
                      tone: switch (r.vendorResolution) {
                        'linked' => CumulusTone.teal,
                        'new' => CumulusTone.warn,
                        'review' => CumulusTone.danger,
                        _ => CumulusTone.neutral,
                      },
                      dense: true,
                    ),
                  ),
                if (r.matchVerdict.isNotEmpty) ...[
                  const SizedBox(width: 6),
                  Tooltip(
                    message: r.matchSummary,
                    child: StatusBadge(
                      r.matchVerdict,
                      tone: matchTone(r.matchVerdict),
                      dense: true,
                    ),
                  ),
                ],
                if (r.anomalyFindings.isNotEmpty) ...[
                  const SizedBox(width: 6),
                  Tooltip(
                    message: r.anomalyFindings.map((f) => f.detail).join('\n'),
                    child: StatusBadge(
                      '${r.anomalyFindings.length} anomaly',
                      tone: CumulusTone.danger,
                      dense: true,
                    ),
                  ),
                ],
                const SizedBox(width: 12),
                Expanded(
                  child: Text(
                    '\$${r.totalUsd.toStringAsFixed(2)}',
                    style: CumulusText.rowTitle.copyWith(
                      fontFeatures: const [FontFeature.tabularFigures()],
                    ),
                    textAlign: TextAlign.right,
                  ),
                ),
                const SizedBox(width: 16),
                SizedBox(
                  width: 84,
                  child: StatusBadge(
                    r.state,
                    tone: stateTone(r.state),
                    dense: true,
                  ),
                ),
                Icon(
                  expanded ? Icons.expand_less : Icons.expand_more,
                  size: 18,
                  color: CumulusColors.inkMuted,
                ),
              ],
            ),
          ),
        ),
        if (expanded)
          InvoiceOutcomeView(api: widget.api, outcome: outcome),
      ],
    );
  }
}

/// One vocabulary for a match verdict and an invoice state across the screen.
CumulusTone matchTone(String verdict) => switch (verdict) {
  'matched' => CumulusTone.success,
  'variance' => CumulusTone.warn,
  'blocked' => CumulusTone.accent,
  'unmatched' => CumulusTone.danger,
  _ => CumulusTone.neutral,
};

CumulusTone stateTone(String state) => switch (state) {
  'posted' => CumulusTone.success,
  'pending' => CumulusTone.warn,
  _ => CumulusTone.danger,
};

// ---------------------------------------------------------------------------
// The submission door
// ---------------------------------------------------------------------------

/// Entering an invoice by hand: a document, and nothing else.
///
/// There used to be a second tab. **Compose** let a person pick a vendor, then
/// one of that vendor's purchase orders, then step the quantities on that
/// order's lines and tick a deviation — and the choices serialized to the hint
/// string the extractor reads. Every control was a real row out of the graph
/// and the thing it produced was still an invoice nobody wrote: an extraction
/// out of a form, indistinguishable afterwards from one a model made of a page.
///
/// In a deployed estate invoices do not arrive here at all. They arrive
/// through `ap-intake`, out of the folders the customer already files them in,
/// and this door is for the one somebody has in their hand.
///
/// The caption is load-bearing rather than decorative: with the stub extractor
/// wired, the reader reads the *filename*, and a person is owed that before
/// they read anything into what comes back.
class InvoiceUploadCard extends StatefulWidget {
  final ApiClient api;
  final SubmissionPanel submission;
  final void Function(PayableOutcome) onSubmitted;

  const InvoiceUploadCard({
    super.key,
    required this.api,
    required this.submission,
    required this.onSubmitted,
  });

  @override
  State<InvoiceUploadCard> createState() => _InvoiceUploadCardState();
}

class _InvoiceUploadCardState extends State<InvoiceUploadCard> {
  XFile? _file;
  bool _busy = false;
  String? _error;

  Future<void> _pickFile() async {
    final file = await openFile(
      acceptedTypeGroups: const [
        XTypeGroup(label: 'invoices', extensions: ['png', 'jpg', 'jpeg', 'pdf']),
      ],
    );
    if (file != null) setState(() => _file = file);
  }

  Future<void> _submit() async {
    final file = _file;
    if (file == null) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final bytes = await file.readAsBytes();
      final outcome = await widget.api.submitInvoice(
        imageBase64: base64Encode(bytes),
        filename: file.name,
      );
      if (!mounted) return;
      setState(() {
        _busy = false;
        _file = null;
      });
      widget.onSubmitted(outcome);
    } on ApiException catch (e) {
      if (mounted) {
        setState(() {
          _busy = false;
          _error = e.message;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final extractor = widget.submission.extractor;
    final limit = widget.submission.clause('auto-post-limit');
    return SectionCard(
      title: 'Enter an invoice',
      subtitle:
          'the side door — a document you have in your hand. Filed invoices '
          'reach the flow through ap-intake, out of the document system',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              OutlinedButton.icon(
                onPressed: _busy ? null : _pickFile,
                icon: const Icon(Icons.description_outlined, size: 16),
                label: Text(_file?.name ?? 'Choose an invoice image or PDF'),
              ),
              const SizedBox(width: 12),
              FilledButton(
                onPressed: _busy || _file == null ? null : _submit,
                child: _busy
                    ? const SizedBox(
                        width: 14,
                        height: 14,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Text('Run the flow'),
              ),
            ],
          ),
          const SizedBox(height: 10),
          Text(
            extractor == 'stub'
                ? 'No extraction model is configured in this deployment. The '
                      'reader behind the ModelCall is the stub: it reads the '
                      'filename, not the page, and invents the rest '
                      'deterministically from the bytes. Everything after '
                      'extraction — the resolver, the three-way match, the '
                      'screen, the posting rule — is the real thing.'
                : 'Extracted by the $extractor reader, one ModelCall over the '
                      'document, against the governed extraction prompt this '
                      'run reads at its revision.',
            style: CumulusText.meta,
          ),
          if (limit != null) ...[
            const SizedBox(height: 10),
            Text(
              'This run will be judged against ${limit.label.toLowerCase()} '
              '${limit.value}, read at ${limit.ref}.',
              style: CumulusText.meta,
            ),
          ],
          if (_error != null) ...[
            const SizedBox(height: 12),
            Text(
              _error!,
              style: CumulusText.body.copyWith(color: CumulusColors.danger),
            ),
          ],
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// One invoice, as a screen
// ---------------------------------------------------------------------------

/// The outcome of a run, and the expansion of a row — one widget, because they
/// are the same thing seen at two moments.
///
/// Submitting used to answer with one line of prose and no way to reach
/// anything it named. This says where the run stopped, whether *this* persona
/// may release it and why not, which governed clauses it read and which one
/// its verdict cites, how the lines paired, what the screen found, and every
/// step in order — and it opens the run, the Task and the artifact.
class InvoiceOutcomeView extends StatelessWidget {
  final ApiClient api;
  final PayableOutcome outcome;

  /// True inside a table row, where the tinted inset separates the expansion
  /// from the rows around it. False in a card that is already its own surface.
  final bool inset;

  const InvoiceOutcomeView({
    super.key,
    required this.api,
    required this.outcome,
    this.inset = true,
  });

  @override
  Widget build(BuildContext context) {
    final r = outcome.invoice;
    final gate = outcome.gate;
    final body = Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            StatusBadge(r.state, tone: stateTone(r.state), dense: true),
            const SizedBox(width: 10),
            Expanded(child: Text(r.verdictReason, style: CumulusText.body)),
          ],
        ),
        const SizedBox(height: 10),
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            if (r.runId.isNotEmpty)
              _action(context, Icons.timeline, 'Open the run', () {
                _openRun(context, api, r.runId);
              }),
            if (gate != null)
              _action(context, Icons.inbox_outlined, 'Open the task', () {
                _openTask(context, api, gate.taskId);
              }),
            if (r.documentId.isNotEmpty)
              _action(context, Icons.description_outlined, 'Open the document',
                  () {
                openDocumentDrawer(context, api, r.documentId);
              }),
          ],
        ),
        if (gate != null) ...[
          const SizedBox(height: 12),
          _GateCard(gate: gate),
        ],
        if (outcome.policy.isNotEmpty) ...[
          const SizedBox(height: 12),
          FieldLabel(
            'Governed values this run read',
            hint: r.policyRef.isEmpty
                ? null
                : 'read at ${r.policyRef} · a marked clause is one the '
                      'verdict names',
          ),
          Wrap(
            spacing: 6,
            runSpacing: 6,
            children: [
              for (final clause in outcome.policy)
                Tooltip(
                  message: clause.deciding
                      ? 'The verdict names this clause.'
                      : clause.ref,
                  child: _monoChip(
                    '${clause.label}: ${clause.value}',
                    tone: clause.deciding
                        ? CumulusTone.warn
                        : CumulusTone.neutral,
                  ),
                ),
            ],
          ),
        ],
        const SizedBox(height: 12),
        const FieldLabel(
          'Vendor resolution',
          hint: 'a name off the page, then one canonical entity',
        ),
        Row(
          children: [
            StatusBadge(
              r.vendorResolution.isEmpty ? 'unresolved' : r.vendorResolution,
              tone: switch (r.vendorResolution) {
                'linked' => CumulusTone.success,
                'new' => CumulusTone.warn,
                'review' => CumulusTone.danger,
                _ => CumulusTone.neutral,
              },
              dense: true,
            ),
            const SizedBox(width: 8),
            Expanded(
              child: Text(
                '${r.vendorName.isEmpty ? "no vendor read" : r.vendorName}'
                '${r.vendorTaxId.isEmpty ? '' : ' · tax id ${r.vendorTaxId}'}'
                '${r.vendorMethod.isEmpty ? '' : ' → ${r.vendorMethod} at ${r.vendorConfidence.toStringAsFixed(2)}'}',
                style: CumulusText.meta,
              ),
            ),
          ],
        ),
        if (r.lines.isNotEmpty) ...[
          const SizedBox(height: 12),
          const FieldLabel('Invoice lines'),
          for (var i = 0; i < r.lines.length; i++)
            _invoiceLine(i + 1, r.lines[i]),
        ],
        if (r.matchPairings.isNotEmpty) ...[
          const SizedBox(height: 12),
          FieldLabel('Three-way match', hint: r.matchSummary),
          for (final pairing in r.matchPairings) _pairingRow(pairing),
        ],
        if (r.anomalyBasis.isNotEmpty) ...[
          const SizedBox(height: 12),
          FieldLabel('Anomaly screen', hint: r.anomalyBasis),
          if (r.anomalyFindings.isEmpty)
            Text('Nothing unusual for this vendor.', style: CumulusText.meta)
          else
            for (final finding in r.anomalyFindings)
              Padding(
                padding: const EdgeInsets.only(bottom: 4),
                child: Row(
                  children: [
                    StatusBadge(
                      finding.kind,
                      tone: CumulusTone.danger,
                      dense: true,
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(finding.detail, style: CumulusText.meta),
                    ),
                  ],
                ),
              ),
        ],
        const SizedBox(height: 12),
        const FieldLabel('Run journal'),
        for (final e in r.journal)
          Padding(
            padding: const EdgeInsets.only(bottom: 4),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                SizedBox(
                  width: 40,
                  child: Text(
                    e.stepId,
                    style: CumulusText.mono.copyWith(
                      fontSize: 11,
                      color: CumulusColors.accentInk,
                    ),
                  ),
                ),
                SizedBox(
                  width: 170,
                  child: Text(e.name, style: CumulusText.meta),
                ),
                Expanded(
                  child: Text(
                    e.summary,
                    style: CumulusText.body.copyWith(fontSize: 12.5),
                  ),
                ),
              ],
            ),
          ),
        if (r.decisionNote.isNotEmpty) ...[
          const SizedBox(height: 6),
          Text('“${r.decisionNote}”', style: CumulusText.body),
        ],
      ],
    );
    if (!inset) return body;
    return Container(
      color: CumulusColors.surfaceMuted,
      padding: const EdgeInsets.fromLTRB(18, 12, 18, 14),
      child: body,
    );
  }

  Widget _action(
    BuildContext context,
    IconData icon,
    String label,
    VoidCallback onTap,
  ) => OutlinedButton.icon(
    onPressed: onTap,
    icon: Icon(icon, size: 15),
    label: Text(label),
    style: OutlinedButton.styleFrom(
      visualDensity: VisualDensity.compact,
      textStyle: CumulusText.meta.copyWith(fontWeight: FontWeight.w600),
    ),
  );

  Widget _invoiceLine(int ordinal, InvoiceLine line) => Padding(
    padding: const EdgeInsets.only(bottom: 4),
    child: Row(
      children: [
        SizedBox(width: 24, child: Text('$ordinal.', style: CumulusText.meta)),
        SizedBox(
          width: 96,
          child: Text(line.sku, style: CumulusText.mono.copyWith(fontSize: 11)),
        ),
        Expanded(
          child: Text(
            line.description,
            style: CumulusText.body.copyWith(fontSize: 12.5),
          ),
        ),
        if (line.kind != 'goods') ...[
          StatusBadge(line.kind, dense: true),
          const SizedBox(width: 8),
        ],
        SizedBox(
          width: 60,
          child: Text(
            line.quantity == 0 ? '—' : '×${_qty(line.quantity)}',
            textAlign: TextAlign.right,
            style: CumulusText.meta,
          ),
        ),
        SizedBox(
          width: 92,
          child: Text(
            '\$${line.unitPriceUsd.toStringAsFixed(2)}',
            textAlign: TextAlign.right,
            style: CumulusText.meta,
          ),
        ),
        SizedBox(
          width: 96,
          child: Text(
            '\$${line.amountUsd.toStringAsFixed(2)}',
            textAlign: TextAlign.right,
            style: CumulusText.body.copyWith(
              fontSize: 12.5,
              fontWeight: FontWeight.w600,
            ),
          ),
        ),
      ],
    ),
  );

  /// One pairing of the retained match. The reason is the whole value: a clerk
  /// needs "billed 3 but 2 was received on RR-5510", not a boolean.
  Widget _pairingRow(MatchPairing pairing) => Padding(
    padding: const EdgeInsets.only(bottom: 5),
    child: Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SizedBox(
          width: 24,
          child: Text('${pairing.invoiceOrdinal}.', style: CumulusText.meta),
        ),
        SizedBox(
          width: 132,
          child: StatusBadge(
            pairing.verdict,
            tone: pairing.clean ? CumulusTone.success : CumulusTone.warn,
            dense: true,
          ),
        ),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                pairing.reason,
                style: CumulusText.body.copyWith(fontSize: 12.5),
              ),
              Text(
                [
                  if (pairing.poNumber.isNotEmpty)
                    'PO ${pairing.poNumber} line ${pairing.poOrdinal} '
                        '(×${_qty(pairing.poQuantity)} at '
                        '\$${pairing.poUnitPriceUsd.toStringAsFixed(2)})',
                  if (pairing.receiptNumber.isNotEmpty)
                    'received ×${_qty(pairing.receivedQuantity)} on '
                        '${pairing.receiptNumber}',
                  if (pairing.priceVariancePct > 0)
                    '${pairing.priceVariancePct.toStringAsFixed(1)}% price '
                        'variance',
                ].join('  ·  '),
                style: CumulusText.meta,
              ),
            ],
          ),
        ),
      ],
    ),
  );
}

/// Where the run stopped, and whether you may release it.
///
/// `may_decide` is `require_queue` answered before the 403 rather than after
/// it. Tomas is bound to `ap-clerk` and Kim is bound to nothing; they sit on
/// the same rung of the write ladder, so the ladder cannot tell them apart and
/// only the queue binding can.
class _GateCard extends StatelessWidget {
  final OutcomeGate gate;

  const _GateCard({required this.gate});

  @override
  Widget build(BuildContext context) {
    final tone = gate.mayDecide ? CumulusTone.accent : CumulusTone.warn;
    return Container(
      padding: const EdgeInsets.fromLTRB(14, 12, 14, 12),
      decoration: BoxDecoration(
        color: tone.background,
        borderRadius: BorderRadius.circular(CumulusRadius.control),
        border: Border.all(
          color: tone.foreground.withValues(alpha: 0.35),
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(
                Icons.pause_circle_outline,
                size: 16,
                color: tone.foreground,
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  'Held at ${gate.stepId} ${gate.stepName} · '
                  '${gate.taskTitle}',
                  style: CumulusText.body.copyWith(fontWeight: FontWeight.w600),
                ),
              ),
              StatusBadge(gate.queue, tone: tone, dense: true),
            ],
          ),
          const SizedBox(height: 6),
          Text(
            gate.mayDecide
                ? 'This queue is yours, so the Task is yours to decide — in '
                      'My Tasks, where the correction re-runs the checks '
                      'rather than overriding them.'
                : gate.whyNot,
            style: CumulusText.meta,
          ),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// The three detours
// ---------------------------------------------------------------------------

/// One record the outcome names, opened beside it.
///
/// A drawer rather than a route: reaching the run behind an invoice is a
/// detour, and Escape should put you back where you were with the page's
/// scroll position intact. All three share this scaffold, so the loading and
/// failure states cannot drift apart between them.
void _openDetail<T>(
  BuildContext context, {
  required String title,
  required String subtitle,
  required Future<T> pending,
  required Widget Function(T) body,
  double width = 640,
}) {
  showSideDrawer<void>(
    context: context,
    width: width,
    title: title,
    subtitle: subtitle,
    builder: (context) => FutureBuilder<T>(
      future: pending,
      builder: (context, snap) {
        if (snap.hasError) return ErrorNotice(message: '${snap.error}');
        final value = snap.data;
        if (value == null) {
          return const Padding(
            padding: EdgeInsets.symmetric(vertical: 28),
            child: CumulusLoading(),
          );
        }
        return body(value);
      },
    ),
  );
}

void _openRun(BuildContext context, ApiClient api, String runId) => _openDetail(
  context,
  width: 760,
  title: runId,
  subtitle:
      'Every step, in order, with what it read and what it wrote — the record '
      'the outcome is a projection of.',
  pending: api.getRun(runId),
  body: (FlowRun run) => RunJournalView(api: api, run: run),
);

void _openTask(BuildContext context, ApiClient api, String taskId) =>
    _openDetail(
      context,
      title: taskId,
      subtitle: 'The question the run stopped to ask, and what it rests on.',
      pending: api.getTask(taskId),
      body: (QueueTask task) => _drawerBody([
        Text(task.title, style: CumulusText.sectionTitle),
        const SizedBox(height: 8),
        Wrap(
          spacing: 6,
          children: [
            StatusBadge(task.queue, tone: CumulusTone.accent, dense: true),
            StatusBadge(task.kind, dense: true),
            StatusBadge(
              task.status,
              tone: task.isOpen ? CumulusTone.warn : CumulusTone.success,
              dense: true,
            ),
          ],
        ),
        const SizedBox(height: 10),
        Text(task.summary, style: CumulusText.body),
        if (task.evidence.isNotEmpty) ...[
          const SizedBox(height: 14),
          const FieldLabel('Evidence'),
          _facts([for (final e in task.evidence) (e.label, e.value)]),
        ],
        if (task.refs.isNotEmpty) ...[
          const SizedBox(height: 14),
          const FieldLabel(
            'Read-set',
            hint: 'what this judgment rests on, at the revision it read',
          ),
          const SizedBox(height: 6),
          RefChips(api: api, refs: task.refs),
        ],
        if (task.decision != null) ...[
          const SizedBox(height: 14),
          FieldLabel(
            'Decided',
            hint: '${task.decision!.verdict} by ${task.decision!.decidedBy}',
          ),
          if (task.decision!.note.isNotEmpty)
            Text('“${task.decision!.note}”', style: CumulusText.body),
        ],
      ]),
    );

// ---------------------------------------------------------------------------
// Small shared pieces
// ---------------------------------------------------------------------------

Widget _drawerBody(List<Widget> children) => Padding(
  padding: const EdgeInsets.fromLTRB(20, 4, 20, 24),
  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: children),
);

/// A label/value list — a Task's evidence and a document's regions are the
/// same shape, and reading them the same way is the point.
Widget _facts(List<(String, String)> rows) => Column(
  crossAxisAlignment: CrossAxisAlignment.start,
  children: [
    for (final (label, value) in rows)
      Padding(
        padding: const EdgeInsets.only(bottom: 4),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            SizedBox(
              width: 230,
              child: Text(label, style: CumulusText.meta),
            ),
            Expanded(
              child: Text(
                value,
                style: CumulusText.body.copyWith(fontSize: 12.5),
              ),
            ),
          ],
        ),
      ),
  ],
);

String _qty(double value) =>
    value == value.roundToDouble() ? '${value.toInt()}' : '$value';

Widget _monoChip(
  String text, {
  bool strike = false,
  CumulusTone tone = CumulusTone.neutral,
}) {
  final plain = tone == CumulusTone.neutral;
  return Container(
    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
    decoration: BoxDecoration(
      color: plain ? CumulusColors.surface : tone.background,
      borderRadius: BorderRadius.circular(6),
      border: Border.all(
        color: plain
            ? CumulusColors.border
            : tone.foreground.withValues(alpha: 0.3),
      ),
    ),
    child: Text(
      text,
      style: CumulusText.mono.copyWith(
        fontSize: 11,
        color: plain ? null : tone.foreground,
        decoration: strike ? TextDecoration.lineThrough : null,
      ),
    ),
  );
}
