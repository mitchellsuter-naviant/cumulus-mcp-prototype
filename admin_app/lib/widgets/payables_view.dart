import 'dart:convert';

import 'package:file_selector/file_selector.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../models/data_layer.dart';
import '../models/payable.dart';
import '../models/run.dart';
import '../models/task.dart';
import '../services/api_client.dart';
import '../theme/cumulus_theme.dart';
import 'ref_chips.dart';
import 'run_workbench.dart';
import 'side_drawer.dart';
import 'ui_kit.dart';

/// The Accounts Payable workspace: enter an invoice, and the table of what the
/// accounts-payable flow made of every invoice so far.
///
/// What makes this table interesting is what it records *around* the
/// extraction: which canonical vendor the printed name resolved to and how,
/// how the billed lines paired against the purchase order and the receiving
/// report, and what the anomaly screen made of the invoice against that
/// vendor's own history. [InvoiceOutcomeView] shows all three, plus the
/// governed values the posting rule read — and it is the same widget for a row
/// you expand and for the run you just started, because they are the same
/// thing seen at two moments.
class PayablesView extends StatefulWidget {
  final ApiClient api;

  const PayablesView({super.key, required this.api});

  @override
  State<PayablesView> createState() => _PayablesViewState();
}

class _PayablesViewState extends State<PayablesView> {
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
    if (screen == null) {
      return const Center(child: CircularProgressIndicator(strokeWidth: 2.5));
    }
    final stats = screen.stats;
    final visible = _lens == 'all'
        ? screen.invoices
        : screen.invoices.where((o) => o.invoice.state == _lens).toList();

    return SingleChildScrollView(
      padding: const EdgeInsets.fromLTRB(28, 20, 28, 40),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          PageHeading(
            eyebrow: 'Workspace',
            title: 'Accounts Payable',
            subtitle:
                'The accounts-payable flow writes this table: one extraction '
                'pass over the document, the vendor resolved against the '
                'master, then matched, screened and posted under policy.',
            trailing: OutlinedButton.icon(
              onPressed: _load,
              icon: const Icon(Icons.refresh, size: 16),
              label: const Text('Refresh'),
            ),
          ),
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
          InvoiceComposerCard(
            api: widget.api,
            composer: screen.composer,
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
          Wrap(
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
          ),
          const SizedBox(height: 14),
          if (visible.isEmpty)
            const CumulusEmptyState(
              icon: Icons.table_rows_outlined,
              title: 'Nothing at this lens',
              message: 'No invoices in this state yet.',
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
// The composer
// ---------------------------------------------------------------------------

/// Entering an invoice, two ways, and they are not variations of each other.
///
/// **Upload** is a document. It says nothing about the case, and with the stub
/// extractor wired it says so plainly: the reader reads the filename, not the
/// page.
///
/// **Compose** is a case. Every control is a real row out of the graph — the
/// vendor master, then *that vendor's* purchase orders, then *that order's*
/// lines with their ordered and received quantities — and the choices
/// serialize to the hint string shown live underneath. That string is the
/// case-authoring language of this flow: the seeded world and the eval
/// triggers are written in it. Nobody has to be taught it, because they watch
/// it assemble.
///
/// One restraint is deliberate: nothing here predicts the outcome. The
/// composed total sits *beside* the governed limit a run will read, and the
/// reader draws the conclusion. `_posting_check` in `payables.py` is the rule,
/// and a second implementation of it here would eventually disagree with the
/// first.
class InvoiceComposerCard extends StatefulWidget {
  final ApiClient api;
  final InvoiceComposer composer;
  final void Function(PayableOutcome) onSubmitted;

  const InvoiceComposerCard({
    super.key,
    required this.api,
    required this.composer,
    required this.onSubmitted,
  });

  @override
  State<InvoiceComposerCard> createState() => _InvoiceComposerCardState();
}

class _InvoiceComposerCardState extends State<InvoiceComposerCard> {
  static const _unknownVendor = 'meridian-supply';

  int _tab = 0;
  ComposerVendor? _vendor;
  ComposerOrder? _order;
  final Map<String, double> _quantities = {};
  final Set<String> _twists = {};
  bool _overrideTotal = false;
  final _total = TextEditingController();
  final _invoice = TextEditingController();
  final _newVendor = TextEditingController(text: _unknownVendor);
  XFile? _file;
  bool _busy = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _pickVendor(
      widget.composer.vendors.isEmpty ? null : widget.composer.vendors.first,
    );
  }

  @override
  void dispose() {
    _total.dispose();
    _invoice.dispose();
    _newVendor.dispose();
    super.dispose();
  }

  /// Choosing a vendor re-seeds everything downstream of it, because
  /// everything downstream of it belongs to that vendor: its orders, its
  /// order's lines, and an invoice number in its own series.
  void _pickVendor(ComposerVendor? vendor) {
    _vendor = vendor;
    _pickOrder(
      vendor == null || vendor.orders.isEmpty ? null : vendor.orders.first,
    );
    final slug = vendor?.slug ?? _newVendor.text;
    final prefix = slug.isEmpty
        ? 'INV'
        : slug.split('-').first.toUpperCase();
    _invoice.text =
        '$prefix-INV-${DateTime.now().millisecondsSinceEpoch % 100000}';
  }

  /// The stepper defaults to what the order ordered, so the untouched
  /// composition is the invoice that matches exactly — every deviation from a
  /// clean three-way match is then something a person deliberately did.
  void _pickOrder(ComposerOrder? order) {
    _order = order;
    _quantities.clear();
    if (order != null) {
      for (final line in order.lines) {
        _quantities[line.sku] = line.quantityOrdered;
      }
    } else if (widget.composer.catalog.isNotEmpty) {
      _quantities[widget.composer.catalog.first.sku] = 1;
    }
  }

  List<ComposerLine> get _lines => _order?.lines ?? widget.composer.catalog;

  /// What the stepped lines come to. Kept separate from [_composedUsd] so
  /// that turning the override *on* can seed the field from the lines rather
  /// than from the empty field it is about to read.
  double get _linesUsd {
    var sum = 0.0;
    for (final line in _lines) {
      sum += (_quantities[line.sku] ?? 0) * line.unitPriceUsd;
    }
    return sum;
  }

  double get _composedUsd => _overrideTotal
      ? (double.tryParse(_total.text.trim()) ?? 0)
      : _linesUsd;

  InvoiceScenario get _scenario => InvoiceScenario(
    vendorSlug: _vendor?.slug ?? '',
    newVendor: _vendor == null ? _newVendor.text.trim().toLowerCase() : '',
    poNumber: _order?.poNumber ?? '',
    noPo: _order == null,
    invoiceNumber: _invoice.text.trim().toUpperCase(),
    lines: [
      for (final line in _lines)
        if ((_quantities[line.sku] ?? 0) > 0)
          ScenarioLine(sku: line.sku, quantity: _quantities[line.sku]!),
    ],
    totalUsd: _overrideTotal ? _composedUsd : 0,
    twists: _twists,
  );

  Future<void> _pickFile() async {
    final file = await openFile(
      acceptedTypeGroups: const [
        XTypeGroup(label: 'invoices', extensions: ['png', 'jpg', 'jpeg', 'pdf']),
      ],
    );
    if (file != null) setState(() => _file = file);
  }

  Future<void> _submit() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final bytes = _tab == 1 && _file != null ? await _file!.readAsBytes() : null;
      final outcome = await widget.api.submitInvoice(
        scenario: _tab == 0 ? _scenario : null,
        imageBase64: bytes == null ? '' : base64Encode(bytes),
        filename: _tab == 1 ? (_file?.name ?? '') : '',
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
    return SectionCard(
      title: 'Enter an invoice',
      subtitle:
          'runs the accounts-payable flow now, against the vendor master and '
          'the purchase orders already in the graph',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SegmentedTabs(
            labels: const ['Compose', 'Upload a document'],
            selectedIndex: _tab,
            onChanged: (i) => setState(() => _tab = i),
          ),
          const SizedBox(height: 16),
          if (_tab == 0) ..._compose() else ..._upload(),
          if (_error != null) ...[
            const SizedBox(height: 12),
            Text(_error!, style: CumulusText.body.copyWith(
              color: CumulusColors.danger,
            )),
          ],
        ],
      ),
    );
  }

  // -- upload ---------------------------------------------------------------

  List<Widget> _upload() => [
    Row(
      children: [
        OutlinedButton.icon(
          onPressed: _busy ? null : _pickFile,
          icon: const Icon(Icons.description_outlined, size: 16),
          label: Text(_file?.name ?? 'Choose an invoice image or PDF'),
        ),
        const SizedBox(width: 12),
        _submitButton(enabled: _file != null),
      ],
    ),
    const SizedBox(height: 10),
    Text(
      widget.composer.extractor == 'stub'
          ? 'No extraction model is configured in this deployment. The reader '
                'behind the ModelCall is the stub: it reads the filename, not '
                'the page, and invents the rest deterministically from the '
                'bytes. Everything after extraction — the resolver, the '
                'three-way match, the screen, the posting rule — is the real '
                'thing. To steer a run deliberately, compose one instead.'
          : 'Extracted by the ${widget.composer.extractor} reader, one '
                'ModelCall over the document, against the governed extraction '
                'prompt this run reads at its revision.',
      style: CumulusText.meta,
    ),
  ];

  // -- compose --------------------------------------------------------------

  List<Widget> _compose() {
    final vendor = _vendor;
    return [
      Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Expanded(flex: 3, child: _vendorPicker()),
          const SizedBox(width: 16),
          Expanded(flex: 2, child: _orderPicker()),
          const SizedBox(width: 16),
          Expanded(
            flex: 2,
            child: LabeledField(
              label: 'Invoice number',
              child: TextField(
                controller: _invoice,
                enabled: !_busy,
                onChanged: (_) => setState(() {}),
                textCapitalization: TextCapitalization.characters,
                inputFormatters: [
                  // The grammar's charset. Refusing the keystroke is kinder
                  // than refusing the submission, and it is the same rule:
                  // a character `to_hint` cannot write is one `parse` cannot
                  // read back.
                  FilteringTextInputFormatter.allow(RegExp(r'[A-Za-z0-9\-]')),
                ],
                decoration: const InputDecoration(isDense: true),
              ),
            ),
          ),
        ],
      ),
      if (vendor != null) ...[
        const SizedBox(height: 8),
        _vendorFacts(vendor),
      ],
      const SizedBox(height: 16),
      FieldLabel(
        _order == null ? 'Lines, from the catalog' : 'Lines on ${_order!.poNumber}',
        hint: _order == null
            ? 'no purchase order behind this invoice, so nothing to bill '
                  'against — the match will have nothing to pair with'
            : 'stepped from what the order ordered; the receiving column is '
                  'what was actually booked in',
      ),
      for (final line in _lines) _lineRow(line),
      const SizedBox(height: 14),
      _totalRow(),
      const SizedBox(height: 16),
      const FieldLabel(
        'Twists',
        hint: 'each one reaches a different branch, decided by a governed '
            'clause this run reads',
      ),
      for (final twist in widget.composer.twists) _twistRow(twist),
      const SizedBox(height: 16),
      _hintPreview(),
    ];
  }

  Widget _vendorPicker() => LabeledField(
    label: 'Vendor',
    child: DropdownButtonFormField<String>(
      initialValue: _vendor?.slug ?? '',
      isDense: true,
      decoration: const InputDecoration(isDense: true),
      items: [
        for (final vendor in widget.composer.vendors)
          DropdownMenuItem(value: vendor.slug, child: Text(vendor.label)),
        const DropdownMenuItem(
          value: '',
          child: Text('A vendor we do not know'),
        ),
      ],
      onChanged: _busy
          ? null
          : (slug) => setState(() {
              _pickVendor(
                slug == null || slug.isEmpty
                    ? null
                    : widget.composer.vendors.firstWhere((v) => v.slug == slug),
              );
            }),
    ),
  );

  Widget _orderPicker() => LabeledField(
    label: 'Purchase order',
    child: DropdownButtonFormField<String>(
      initialValue: _order?.poNumber ?? '',
      isDense: true,
      decoration: const InputDecoration(isDense: true),
      items: [
        for (final order in _vendor?.orders ?? const <ComposerOrder>[])
          DropdownMenuItem(value: order.poNumber, child: Text(order.poNumber)),
        const DropdownMenuItem(value: '', child: Text('No purchase order')),
      ],
      onChanged: _busy
          ? null
          : (number) => setState(() {
              _pickOrder(
                number == null || number.isEmpty
                    ? null
                    : _vendor!.orders.firstWhere((o) => o.poNumber == number),
              );
            }),
    ),
  );

  /// What the vendor master holds for the vendor you just chose. This used to
  /// be a card of its own further down the page, which meant its most useful
  /// column — every source system's id for one canonical company — was read
  /// nowhere near the moment it mattered.
  Widget _vendorFacts(ComposerVendor vendor) => Wrap(
    spacing: 6,
    runSpacing: 6,
    crossAxisAlignment: WrapCrossAlignment.center,
    children: [
      StatusBadge(
        vendor.status,
        tone: vendor.status == 'active' ? CumulusTone.success : CumulusTone.warn,
        dense: true,
      ),
      Text(
        [
          if (vendor.taxId.isNotEmpty) 'tax ${vendor.taxId}',
          if (vendor.paymentTerms.isNotEmpty) vendor.paymentTerms,
          '${vendor.invoices} invoice(s)',
          '${vendor.orders.length} open PO(s)',
        ].join('  ·  '),
        style: CumulusText.meta,
      ),
      for (final source in vendor.sources)
        Tooltip(
          message: source.live
              ? 'One canonical vendor carrying this system\'s id for it. When '
                    'a vendor was a slug on a row, the same company in two '
                    'systems was two vendors.'
              : 'Withdrawn by a steward — kept, and no longer resolving.',
          child: _monoChip(
            '${source.source}:${source.externalId}',
            strike: !source.live,
          ),
        ),
    ],
  );

  Widget _lineRow(ComposerLine line) {
    final quantity = _quantities[line.sku] ?? 0;
    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: Row(
        children: [
          SizedBox(width: 96, child: _monoChip(line.sku)),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
              line.description,
              style: CumulusText.body.copyWith(
                fontSize: 12.5,
                color: quantity > 0
                    ? CumulusColors.ink
                    : CumulusColors.inkMuted,
              ),
            ),
          ),
          if (line.quantityReceived >= 0)
            SizedBox(
              width: 150,
              child: Text(
                '${_qty(line.quantityOrdered)} ordered · '
                '${_qty(line.quantityReceived)} received',
                textAlign: TextAlign.right,
                style: CumulusText.meta,
              ),
            ),
          const SizedBox(width: 12),
          SizedBox(
            width: 84,
            child: Text(
              '\$${line.unitPriceUsd.toStringAsFixed(2)}',
              textAlign: TextAlign.right,
              style: CumulusText.meta,
            ),
          ),
          const SizedBox(width: 12),
          _stepper(line.sku, quantity),
          SizedBox(
            width: 96,
            child: Text(
              '\$${(quantity * line.unitPriceUsd).toStringAsFixed(2)}',
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
  }

  Widget _stepper(String sku, double quantity) => Row(
    mainAxisSize: MainAxisSize.min,
    children: [
      _step(Icons.remove, quantity > 0
          ? () => setState(() => _quantities[sku] = quantity - 1)
          : null),
      SizedBox(
        width: 34,
        child: Text(
          _qty(quantity),
          textAlign: TextAlign.center,
          style: CumulusText.mono.copyWith(fontSize: 12),
        ),
      ),
      _step(Icons.add, () => setState(() => _quantities[sku] = quantity + 1)),
    ],
  );

  Widget _step(IconData icon, VoidCallback? onTap) => InkWell(
    onTap: _busy ? null : onTap,
    borderRadius: BorderRadius.circular(6),
    child: Container(
      width: 24,
      height: 24,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        color: CumulusColors.surfaceMuted,
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: CumulusColors.border),
      ),
      child: Icon(
        icon,
        size: 14,
        color: onTap == null ? CumulusColors.inkMuted : CumulusColors.ink,
      ),
    ),
  );

  /// The juxtaposition, and the whole of it. What you composed on the left,
  /// what the run will read on the right, and no verdict in between.
  Widget _totalRow() {
    final limit = widget.composer.clause('auto-post-limit');
    return Row(
      crossAxisAlignment: CrossAxisAlignment.center,
      children: [
        Checkbox(
          value: _overrideTotal,
          visualDensity: VisualDensity.compact,
          onChanged: _busy
              ? null
              : (on) => setState(() {
                  _overrideTotal = on ?? false;
                  if (_overrideTotal && _total.text.isEmpty) {
                    _total.text = _linesUsd.toStringAsFixed(2);
                  }
                }),
        ),
        Text('Bill a different total', style: CumulusText.body),
        const SizedBox(width: 10),
        if (_overrideTotal)
          SizedBox(
            width: 120,
            child: TextField(
              controller: _total,
              enabled: !_busy,
              onChanged: (_) => setState(() {}),
              keyboardType: TextInputType.number,
              inputFormatters: [
                FilteringTextInputFormatter.allow(RegExp(r'[0-9.]')),
              ],
              decoration: const InputDecoration(
                isDense: true,
                prefixText: '\$',
              ),
            ),
          ),
        const Spacer(),
        Text(
          '\$${_composedUsd.toStringAsFixed(2)}',
          style: CumulusText.statValue.copyWith(fontSize: 20),
        ),
        const SizedBox(width: 14),
        if (limit != null)
          Tooltip(
            message:
                'The value the posting rule will read at this revision. What '
                'it makes of your amount is the run\'s answer, not this '
                'screen\'s.',
            child: _monoChip(
              'auto-post ≤ ${limit.value} · ${limit.ref}',
              tone: CumulusTone.accent,
            ),
          ),
      ],
    );
  }

  Widget _twistRow(ScenarioTwist twist) {
    final clause = widget.composer.clause(twist.clauseId);
    return Padding(
      padding: const EdgeInsets.only(bottom: 2),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Checkbox(
            value: _twists.contains(twist.id),
            visualDensity: VisualDensity.compact,
            onChanged: _busy
                ? null
                : (on) => setState(() {
                    if (on ?? false) {
                      _twists.add(twist.id);
                    } else {
                      _twists.remove(twist.id);
                    }
                  }),
          ),
          Expanded(
            child: Padding(
              padding: const EdgeInsets.only(top: 9),
              child: Wrap(
                spacing: 8,
                runSpacing: 4,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  Text(twist.label, style: CumulusText.body),
                  Text('→ ${twist.branch} →', style: CumulusText.meta),
                  if (clause != null)
                    Tooltip(
                      message: clause.ref,
                      child: _monoChip(
                        '${clause.label}: ${clause.value}',
                        tone: CumulusTone.accent,
                      ),
                    ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _hintPreview() {
    final hint = _scenario.toHint(widget.composer.twists);
    return Container(
      padding: const EdgeInsets.fromLTRB(14, 12, 14, 12),
      decoration: BoxDecoration(
        color: CumulusColors.surfaceMuted,
        borderRadius: BorderRadius.circular(CumulusRadius.control),
        border: Border.all(color: CumulusColors.border),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.center,
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('WHAT THIS SENDS', style: CumulusText.statLabel),
                const SizedBox(height: 6),
                SelectableText(
                  hint.isEmpty ? '(nothing yet — pick a vendor)' : hint,
                  style: CumulusText.mono.copyWith(fontSize: 12.5),
                ),
                const SizedBox(height: 6),
                Text(
                  'The same language the seeded invoices and the eval cases '
                  'are written in. It is composed here and serialized on the '
                  'server, so this is the string the run receives.',
                  style: CumulusText.meta,
                ),
              ],
            ),
          ),
          const SizedBox(width: 16),
          _submitButton(enabled: hint.isNotEmpty),
        ],
      ),
    );
  }

  Widget _submitButton({required bool enabled}) => FilledButton(
    onPressed: _busy || !enabled ? null : _submit,
    child: _busy
        ? const SizedBox(
            width: 14,
            height: 14,
            child: CircularProgressIndicator(strokeWidth: 2),
          )
        : const Text('Run the flow'),
  );
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
                _openDocument(context, api, r.documentId);
              }),
          ],
        ),
        if (gate != null) ...[
          const SizedBox(height: 12),
          _GateCard(gate: gate),
        ],
        if (outcome.hint.isNotEmpty) ...[
          const SizedBox(height: 12),
          const FieldLabel('What this sent'),
          SelectableText(
            outcome.hint,
            style: CumulusText.mono.copyWith(fontSize: 12),
          ),
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

void _openDocument(BuildContext context, ApiClient api, String documentId) =>
    _openDetail(
      context,
      title: documentId,
      subtitle:
          'The artifact this run read, and the custody it is held under. Its '
          'regions are where the extractor claims it read each field.',
      pending: api.getDocument(documentId),
      body: (StoredDocument doc) => _drawerBody([
        Text(
          doc.filename.isEmpty ? '(no filename)' : doc.filename,
          style: CumulusText.sectionTitle,
        ),
        const SizedBox(height: 8),
        Text(
          [
            doc.mediaType,
            '${doc.byteSize} bytes',
            '${doc.pages} page(s)',
            doc.collectionId,
            if (doc.retentionUntilIso.isNotEmpty)
              'retained until ${doc.retentionUntilIso.split("T").first}',
          ].join('  ·  '),
          style: CumulusText.meta,
        ),
        if (!doc.hasBytes) ...[
          const SizedBox(height: 10),
          Text(
            'No bytes: this run was composed rather than uploaded, so the '
            'artifact is a placeholder the journal can still point at. '
            'Everything downstream of extraction ran on the real machinery '
            'either way.',
            style: CumulusText.body,
          ),
        ],
        if (doc.regions.isNotEmpty) ...[
          const SizedBox(height: 14),
          FieldLabel(
            'Regions',
            hint: '${doc.regionCount} field(s) located on the page',
          ),
          _facts([
            for (final region in doc.regions)
              (
                region.field,
                '${region.text}  ·  confidence '
                    '${region.confidence.toStringAsFixed(2)}',
              ),
          ]),
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
