import 'dart:convert';

import 'package:file_selector/file_selector.dart';
import 'package:flutter/material.dart';

import '../models/graph.dart';
import '../models/payable.dart';
import '../services/api_client.dart';
import '../theme/cumulus_theme.dart';
import 'run_workbench.dart';
import 'ui_kit.dart';

/// The Accounts Payable workspace: the AP invoices table, the
/// accounts-payable flow's output.
///
/// What makes this table interesting is the two extraction paths it records:
/// a known vendor is a single one-shot pass against its exemplar in the
/// knowledge layer; an unknown vendor is a three-model ensemble whose
/// adjudicated fields — conflicts included — went through a human before the
/// exemplar was written back. Expanding a row shows which path ran, the
/// per-model candidates when there were three, and the placeholders the flow
/// declares honestly (three-way match, fraud screen) until their feeds land.
class PayablesView extends StatefulWidget {
  final ApiClient api;

  const PayablesView({super.key, required this.api});

  @override
  State<PayablesView> createState() => _PayablesViewState();
}

class _PayablesViewState extends State<PayablesView> {
  List<PayableInvoice>? _rows;
  List<VendorRecord>? _vendors;
  PayableStats? _stats;
  String? _error;
  String _lens = 'all';
  String? _expanded;

  // Submit strip state.
  XFile? _file;
  final _note = TextEditingController();
  bool _busy = false;
  String? _message;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _note.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final rows = await widget.api.listPayables();
      final stats = await widget.api.payableStats();
      // The vendor master is a separate read on purpose: it is the entity
      // layer, not the table, and the invoices resolve against it.
      final vendors = await widget.api
          .listVendors()
          .then<List<VendorRecord>?>((v) => v)
          .catchError((_) => null);
      if (mounted) {
        setState(() {
          _rows = rows;
          _stats = stats;
          _vendors = vendors;
          _error = null;
        });
      }
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    }
  }

  Future<void> _pick() async {
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
      _message = null;
    });
    try {
      final bytes = _file == null ? null : await _file!.readAsBytes();
      final row = await widget.api.submitInvoice(
        imageBase64: bytes == null ? '' : base64Encode(bytes),
        filename: _file?.name ?? '',
        note: _note.text.trim(),
      );
      setState(() {
        _busy = false;
        _file = null;
        _note.clear();
        _message = switch (row.state) {
          'posted' =>
            'Posted — ${row.vendorName}, \$${row.totalUsd.toStringAsFixed(2)} '
                '(${row.extractionPath}).',
          _ =>
            'Suspended for review — ${row.verdictReason}. '
                'The task is in the ap-clerk queue.',
        };
      });
      _load();
    } on ApiException catch (e) {
      setState(() {
        _busy = false;
        _message = e.message;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_error != null) {
      return CumulusEmptyState(
        icon: Icons.cloud_off,
        title: 'Could not load the table',
        message: _error!,
      );
    }
    final rows = _rows;
    final stats = _stats;
    if (rows == null || stats == null) {
      return const Center(child: CircularProgressIndicator(strokeWidth: 2.5));
    }
    final visible = _lens == 'all'
        ? rows
        : rows.where((r) => r.state == _lens).toList();

    return SingleChildScrollView(
      padding: const EdgeInsets.fromLTRB(28, 20, 28, 40),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          PageHeading(
            eyebrow: 'Workspace',
            title: 'Accounts Payable',
            subtitle:
                'The accounts-payable flow writes this table: one-shot from a '
                'vendor exemplar when one exists, a three-model ensemble plus '
                'human verification when one does not.',
            trailing: OutlinedButton.icon(
              onPressed: _load,
              icon: const Icon(Icons.refresh, size: 16),
              label: const Text('Refresh'),
            ),
          ),
          const SizedBox(height: 18),
          StatStrip(
            tiles: [
              StatTile(label: 'Invoices', value: '${stats.total}',
                  caption: 'all time'),
              StatTile(label: 'Posted', value: '${stats.posted}',
                  caption: '\$${stats.postedUsd.toStringAsFixed(2)} to the ledger'),
              StatTile(label: 'Pending', value: '${stats.pending}',
                  caption: 'suspended, waiting on a Task'),
              StatTile(label: 'One-shot', value: '${stats.oneShot}',
                  caption: 'known vendors, from exemplars'),
              StatTile(label: 'Ensemble', value: '${stats.ensemble}',
                  caption: 'unknown vendors, 3 models + human'),
              StatTile(label: 'Matched', value: '${stats.matched}',
                  caption: '${stats.matchVariance} variance · '
                      '${stats.matchBlocked} not received yet'),
              StatTile(label: 'Anomalies', value: '${stats.anomalies}',
                  caption: 'unusual for that vendor'),
            ],
          ),
          const SizedBox(height: 16),
          SectionCard(
            title: 'Submit an invoice',
            subtitle:
                'runs the flow now — try note "vendor=acme-industrial total=1840 '
                'invoice=ACME-INV-20881 po=PO-7741" for the one-shot path, or a '
                'new vendor name for the ensemble',
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    OutlinedButton.icon(
                      onPressed: _busy ? null : _pick,
                      icon: const Icon(Icons.description_outlined, size: 16),
                      label: Text(_file?.name ?? 'Choose invoice image'),
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: TextField(
                        controller: _note,
                        enabled: !_busy,
                        onChanged: (_) => setState(() {}),
                        decoration: const InputDecoration(
                          labelText: 'Note / hints',
                          isDense: true,
                        ),
                      ),
                    ),
                    const SizedBox(width: 10),
                    FilledButton(
                      onPressed:
                          _busy || (_file == null && _note.text.trim().isEmpty)
                          ? null
                          : _submit,
                      child: _busy
                          ? const SizedBox(
                              width: 14,
                              height: 14,
                              child: CircularProgressIndicator(strokeWidth: 2),
                            )
                          : const Text('Submit'),
                    ),
                  ],
                ),
                if (_message != null) ...[
                  const SizedBox(height: 10),
                  Text(_message!, style: CumulusText.body),
                ],
              ],
            ),
          ),
          const SizedBox(height: 16),
          if (_vendors != null && _vendors!.isNotEmpty)
            _VendorMasterCard(vendors: _vendors!),
          const SizedBox(height: 16),
          FlowRunsCard(
            api: widget.api,
            flowId: 'accounts-payable',
            subtitle:
                'resolve → extract → match → screen → post, with every gate '
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
              children: [for (final r in visible) _row(r)],
            ),
        ],
      ),
    );
  }

  Widget _row(PayableInvoice r) {
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
                StatusBadge(
                  r.extractionPath,
                  tone: r.extractionPath == 'one-shot'
                      ? CumulusTone.teal
                      : CumulusTone.violet,
                  dense: true,
                ),
                if (r.matchVerdict.isNotEmpty) ...[
                  const SizedBox(width: 6),
                  Tooltip(
                    message: r.matchSummary,
                    child: StatusBadge(
                      r.matchVerdict,
                      tone: _matchTone(r.matchVerdict),
                      dense: true,
                    ),
                  ),
                ],
                if (r.anomalyFindings.isNotEmpty) ...[
                  const SizedBox(width: 6),
                  Tooltip(
                    message: r.anomalyFindings
                        .map((f) => f.detail)
                        .join('\n'),
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
                    tone: switch (r.state) {
                      'posted' => CumulusTone.success,
                      'pending' => CumulusTone.warn,
                      _ => CumulusTone.danger,
                    },
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
        if (expanded) _detail(r),
      ],
    );
  }

  Widget _detail(PayableInvoice r) {
    return Container(
      color: CumulusColors.surfaceMuted,
      padding: const EdgeInsets.fromLTRB(18, 12, 18, 14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(r.verdictReason, style: CumulusText.body),
          const SizedBox(height: 8),
          Wrap(
            spacing: 6,
            runSpacing: 6,
            children: [
              if (r.exemplarRef.isNotEmpty) _monoChip(r.exemplarRef),
              if (r.policyRef.isNotEmpty) _monoChip(r.policyRef),
              _monoChip('limit \$${r.postLimitUsd.toStringAsFixed(2)}'),
              if (r.runId.isNotEmpty) _monoChip(r.runId),
              if (r.graphRef.isNotEmpty) _monoChip(r.graphRef),
              if (r.matchRef.isNotEmpty) _monoChip(r.matchRef),
              if (r.taskId.isNotEmpty) _monoChip(r.taskId),
              if (r.decidedBy.isNotEmpty) _monoChip('decided by ${r.decidedBy}'),
            ],
          ),
          const SizedBox(height: 12),
          const FieldLabel(
            'Vendor resolution',
            hint: 'names off the page, then one canonical entity',
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
                  '${r.vendorCandidates.isEmpty ? "no candidates read" : r.vendorCandidates.join(", ")}'
                  '${r.vendorMethod.isEmpty ? '' : ' → ${r.vendorMethod} at ${r.vendorConfidence.toStringAsFixed(2)}'}',
                  style: CumulusText.meta,
                ),
              ),
            ],
          ),
          if (r.lines.isNotEmpty) ...[
            const SizedBox(height: 12),
            const FieldLabel('Invoice lines'),
            for (var i = 0; i < r.lines.length; i++) _invoiceLine(i + 1, r.lines[i]),
          ],
          if (r.matchPairings.isNotEmpty) ...[
            const SizedBox(height: 12),
            FieldLabel(
              'Three-way match',
              hint: r.matchSummary,
            ),
            for (final pairing in r.matchPairings) _pairingRow(pairing),
          ],
          if (r.anomalyBasis.isNotEmpty) ...[
            const SizedBox(height: 12),
            FieldLabel(
              'Anomaly screen',
              hint: r.anomalyBasis,
            ),
            if (r.anomalyFindings.isEmpty)
              Text(
                'Nothing unusual for this vendor.',
                style: CumulusText.meta,
              )
            else
              for (final finding in r.anomalyFindings)
                Padding(
                  padding: const EdgeInsets.only(bottom: 4),
                  child: Row(
                    children: [
                      StatusBadge(finding.kind, tone: CumulusTone.danger,
                          dense: true),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Text(finding.detail, style: CumulusText.meta),
                      ),
                    ],
                  ),
                ),
          ],
          if (r.candidates.isNotEmpty) ...[
            const SizedBox(height: 12),
            const FieldLabel('Ensemble candidates'),
            for (final c in r.candidates)
              Padding(
                padding: const EdgeInsets.only(bottom: 4),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    SizedBox(
                      width: 150,
                      child: Text(
                        c.model,
                        style: CumulusText.mono.copyWith(fontSize: 11),
                      ),
                    ),
                    Expanded(
                      child: Text(
                        c.fields.entries
                            .where((e) => e.value.isNotEmpty)
                            .map((e) => '${e.key}=${e.value}')
                            .join(' · '),
                        style: CumulusText.body.copyWith(fontSize: 12),
                      ),
                    ),
                  ],
                ),
              ),
          ],
          if (r.conflicts.isNotEmpty) ...[
            const SizedBox(height: 8),
            const FieldLabel('Conflicts the adjudicator resolved'),
            for (final c in r.conflicts)
              Padding(
                padding: const EdgeInsets.only(bottom: 3),
                child: Text(
                  '• $c',
                  style: CumulusText.body.copyWith(
                    fontSize: 12.5,
                    color: CumulusColors.warn,
                  ),
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
      ),
    );
  }

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

  static String _qty(double value) =>
      value == value.roundToDouble() ? '${value.toInt()}' : '$value';

  static CumulusTone _matchTone(String verdict) => switch (verdict) {
    'matched' => CumulusTone.success,
    'variance' => CumulusTone.warn,
    'blocked' => CumulusTone.accent,
    'unmatched' => CumulusTone.danger,
    _ => CumulusTone.neutral,
  };

  Widget _monoChip(String text) => Container(
    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
    decoration: BoxDecoration(
      color: CumulusColors.surface,
      borderRadius: BorderRadius.circular(6),
      border: Border.all(color: CumulusColors.border),
    ),
    child: Text(text, style: CumulusText.mono.copyWith(fontSize: 11)),
  );
}

/// The vendor master, as the AP workspace needs to see it.
///
/// The column that matters is `sources`: one canonical vendor carrying every
/// external system's id for it. That is the thing that was impossible when a
/// vendor was a slug used to look up an exemplar asset — the same company in
/// two systems was two vendors, and merging them merged nothing.
class _VendorMasterCard extends StatefulWidget {
  final List<VendorRecord> vendors;

  const _VendorMasterCard({required this.vendors});

  @override
  State<_VendorMasterCard> createState() => _VendorMasterCardState();
}

class _VendorMasterCardState extends State<_VendorMasterCard> {
  bool _expanded = false;

  @override
  Widget build(BuildContext context) {
    final vendors = widget.vendors;
    final withExemplar = vendors.where((v) => v.hasExemplar).length;
    final conflicted = vendors.where((v) => v.conflictFields.isNotEmpty).length;
    final multiSource =
        vendors.where((v) => v.sourceRecords.length > 1).length;
    final shown = _expanded ? vendors : vendors.take(5).toList();
    return ListCard(
      title: 'Vendor master',
      subtitle:
          '${vendors.length} canonical vendor(s) · $withExemplar with an '
          'exemplar (one-shot path) · $multiSource known to more than one '
          'system · $conflicted with sources that disagree',
      headerTrailing: vendors.length > 5
          ? TextButton(
              onPressed: () => setState(() => _expanded = !_expanded),
              child: Text(_expanded ? 'Show fewer' : 'Show all'),
            )
          : null,
      children: [
        for (final vendor in shown)
          CumulusListRow(
            leading: Monogram(
              vendor.label.isEmpty ? '?' : vendor.label.substring(0, 1),
              tone: vendor.status == 'active'
                  ? CumulusTone.teal
                  : CumulusTone.warn,
            ),
            title: Row(
              children: [
                Flexible(child: Text(vendor.label)),
                const SizedBox(width: 10),
                StatusBadge(
                  vendor.status,
                  tone: vendor.status == 'active'
                      ? CumulusTone.success
                      : CumulusTone.warn,
                  dense: true,
                ),
                if (vendor.hasExemplar) ...[
                  const SizedBox(width: 6),
                  const Tooltip(
                    message:
                        'A verified one-shot template exists, so this vendor’s '
                        'invoices take the single-pass path.',
                    child: StatusBadge('exemplar', tone: CumulusTone.accent,
                        dense: true),
                  ),
                ],
                if (vendor.conflictFields.isNotEmpty) ...[
                  const SizedBox(width: 6),
                  Tooltip(
                    message:
                        'Sources disagree on ${vendor.conflictFields.join(", ")}. '
                        'Both claims are kept; precedence decides which '
                        'resolves.',
                    child: StatusBadge(
                      '${vendor.conflictFields.length} conflict(s)',
                      tone: CumulusTone.warn,
                      dense: true,
                    ),
                  ),
                ],
              ],
            ),
            subtitle: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  [
                    if (vendor.taxId.isNotEmpty) 'tax ${vendor.taxId}',
                    if (vendor.paymentTerms.isNotEmpty) vendor.paymentTerms,
                    '${vendor.invoices} invoice(s) '
                        '(\$${vendor.invoicedUsd.toStringAsFixed(2)})',
                    '${vendor.purchaseOrders} PO(s)',
                    if (vendor.aliases.isNotEmpty)
                      'also seen as ${vendor.aliases.join(", ")}',
                  ].join('  ·  '),
                ),
                const SizedBox(height: 5),
                Wrap(
                  spacing: 6,
                  runSpacing: 4,
                  children: [
                    for (final source in vendor.sourceRecords)
                      Tooltip(
                        message:
                            '${source.method} match at '
                            '${source.confidence.toStringAsFixed(2)}'
                            '${source.live ? '' : ' — withdrawn by a steward'}',
                        child: Container(
                          padding: const EdgeInsets.symmetric(
                            horizontal: 8,
                            vertical: 3,
                          ),
                          decoration: BoxDecoration(
                            color: source.live
                                ? CumulusColors.surface
                                : CumulusColors.surfaceMuted,
                            borderRadius: BorderRadius.circular(6),
                            border: Border.all(
                              color: source.live
                                  ? CumulusColors.border
                                  : CumulusColors.borderStrong,
                            ),
                          ),
                          child: Text(
                            '${source.source}:${source.externalId}',
                            style: CumulusText.mono.copyWith(
                              fontSize: 11,
                              decoration: source.live
                                  ? null
                                  : TextDecoration.lineThrough,
                            ),
                          ),
                        ),
                      ),
                  ],
                ),
              ],
            ),
            trailing: [
              SizedBox(
                width: 150,
                child: Text(
                  vendor.ref,
                  textAlign: TextAlign.end,
                  style: CumulusText.mono,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ],
          ),
      ],
    );
  }
}
