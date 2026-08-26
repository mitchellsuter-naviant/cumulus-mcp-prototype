import 'dart:convert';

import 'package:file_selector/file_selector.dart';
import 'package:flutter/material.dart';

import '../models/payable.dart';
import '../services/api_client.dart';
import '../theme/cumulus_theme.dart';
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
      if (mounted) {
        setState(() {
          _rows = rows;
          _stats = stats;
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
              _monoChip('classifier ${r.vendorConfidence.toStringAsFixed(2)}'),
              _monoChip('3-way match: ${r.threeWayMatch}'),
              _monoChip('fraud screen: ${r.fraudScreen}'),
              if (r.taskId.isNotEmpty) _monoChip(r.taskId),
              if (r.decidedBy.isNotEmpty) _monoChip('decided by ${r.decidedBy}'),
            ],
          ),
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
