import 'package:flutter/material.dart';

import '../models/reimbursement.dart';
import '../services/api_client.dart';
import '../theme/cumulus_theme.dart';
import 'mini_charts.dart';
import 'ui_kit.dart';

/// The Expense Reimbursement workspace: the reimbursements table.
///
/// A Workspace is a business view onto rows a flow wrote — the work itself,
/// as opposed to the Knowledge layer's Data plane, which registers the same
/// table as a Dataset and says where its rows come from. The presentation
/// follows that: a metric strip, one daily spend chart, a state lens, and the
/// table, where every row expands into the run journal that produced it
/// (which policy revision was read, what the rule said, who decided).
/// Provenance is the point: a row you cannot explain is not governed data.
class ReimbursementsView extends StatefulWidget {
  final ApiClient api;

  const ReimbursementsView({super.key, required this.api});

  @override
  State<ReimbursementsView> createState() => _ReimbursementsViewState();
}

class _ReimbursementsViewState extends State<ReimbursementsView> {
  List<Reimbursement>? _rows;
  ReimbursementStats? _stats;
  String? _error;
  String _lens = 'all';
  String? _expanded;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final rows = await widget.api.listReimbursements();
      final stats = await widget.api.reimbursementStats();
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

  List<ChartPoint> _dailySpend(List<Reimbursement> rows) {
    final byDay = <String, double>{};
    final now = DateTime.now();
    for (var i = 27; i >= 0; i--) {
      byDay[now.subtract(Duration(days: i)).toIso8601String().substring(0, 10)] =
          0;
    }
    for (final r in rows) {
      if (r.state != 'approved') continue;
      final day = r.submittedAtIso.substring(0, 10);
      if (byDay.containsKey(day)) byDay[day] = byDay[day]! + r.totalUsd;
    }
    return [
      for (final e in byDay.entries) ChartPoint(e.key.substring(5), e.value),
    ];
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
            title: 'Expense Reimbursement',
            subtitle:
                'The expense-reimbursement flow writes this table — every row '
                'carries the run journal that produced it.',
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
                label: 'Requests',
                value: '${stats.total}',
                caption: 'all time',
              ),
              StatTile(
                label: 'Approved',
                value: '${stats.approved}',
                caption: '${stats.autoApproved} without human review',
              ),
              StatTile(
                label: 'Pending',
                value: '${stats.pending}',
                caption: 'suspended, waiting on a Task',
              ),
              StatTile(
                label: 'Denied',
                value: '${stats.denied}',
                caption: 'by human decision',
              ),
              StatTile(
                label: 'Approved spend',
                value: '\$${stats.approvedUsd.toStringAsFixed(2)}',
                caption: 'committed via the gateway',
              ),
            ],
          ),
          const SizedBox(height: 16),
          SectionCard(
            title: 'Approved spend by day',
            subtitle: 'last 28 days',
            child: DailyBarChart(
              title: 'Approved spend',
              points: _dailySpend(rows),
              format: (v) => '\$${v.toStringAsFixed(2)}',
              height: 140,
              showTitle: false,
            ),
          ),
          const SizedBox(height: 16),
          Wrap(
            spacing: 8,
            children: [
              for (final lens in const [
                ('all', CumulusTone.neutral),
                ('approved', CumulusTone.success),
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
              message: 'No requests in this state yet.',
            )
          else
            ListCard(
              title: 'Requests',
              subtitle: '${visible.length} shown · click a row for its journal',
              children: [for (final r in visible) _row(r)],
            ),
        ],
      ),
    );
  }

  Widget _row(Reimbursement r) {
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
                      Text(r.vendor, style: CumulusText.rowTitle),
                      const SizedBox(height: 2),
                      Text(
                        '${r.date} · ${r.category} · ${r.employee}',
                        style: CumulusText.meta,
                      ),
                    ],
                  ),
                ),
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
                  width: 60,
                  child: Text(
                    r.confidence > 0 ? r.confidence.toStringAsFixed(2) : '—',
                    style: CumulusText.meta,
                    textAlign: TextAlign.right,
                  ),
                ),
                const SizedBox(width: 16),
                SizedBox(
                  width: 84,
                  child: StatusBadge(
                    r.state,
                    tone: switch (r.state) {
                      'approved' => CumulusTone.success,
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
        if (expanded) _journal(r),
      ],
    );
  }

  Widget _journal(Reimbursement r) {
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
              _monoChip(r.policyRef),
              _monoChip('limit \$${r.policyLimitUsd.toStringAsFixed(2)}'),
              if (r.model.isNotEmpty) _monoChip(r.model),
              if (r.taskId.isNotEmpty) _monoChip(r.taskId),
              if (r.decidedBy.isNotEmpty) _monoChip('decided by ${r.decidedBy}'),
            ],
          ),
          const SizedBox(height: 12),
          const FieldLabel('Run journal'),
          for (final e in r.journal)
            Padding(
              padding: const EdgeInsets.only(bottom: 4),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  SizedBox(
                    width: 34,
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
