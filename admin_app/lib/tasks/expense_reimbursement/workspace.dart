import 'package:flutter/material.dart';

import 'models.dart';
import '../../models/session.dart';
import '../../services/api_client.dart';
import '../../theme/cumulus_theme.dart';
import '../../widgets/command_palette.dart' show expenseQueue, ownsWorkspace;
import '../../widgets/mini_charts.dart';
import '../../widgets/run_workbench.dart';
import '../../widgets/skeleton.dart';
import '../../widgets/ui_kit.dart';

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

  /// The acting session, so an empty table can say which of its two causes it
  /// is. Null only before the first session load.
  final Session? session;

  const ReimbursementsView({super.key, required this.api, this.session});

  @override
  State<ReimbursementsView> createState() => _ReimbursementsViewState();
}

class _ReimbursementsViewState extends State<ReimbursementsView> {
  /// Whether the expense queue is this persona's work. The table is scoped
  /// server-side either way — this only decides what its emptiness *means*.
  /// Home's AP panel draws exactly this distinction; the workspaces were the
  /// two screens that did not, and "No requests in this state yet" told the
  /// person least able to check that the business was quiet.
  bool get _seesTheQueue =>
      ownsWorkspace(widget.session, expenseQueue);

  List<Reimbursement>? _rows;
  ReimbursementStats? _stats;

  /// The 28-day series, bucketed once when the rows land. It is a function of
  /// the table and nothing else, so recomputing a map and a list of 28 points
  /// on every rebuild — every lens click, every row expansion — bought nothing.
  List<ChartPoint> _spend = const [];
  String? _error;
  String _lens = 'all';
  String? _expanded;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    // The table and its totals are independent reads; the screen waits for the
    // slower of the two rather than for their sum.
    final Future<List<Reimbursement>> rowsRead = widget.api
        .listReimbursements();
    final Future<ReimbursementStats> statsRead = widget.api
        .reimbursementStats();
    try {
      // Grouped rather than awaited in a row: a first failure would otherwise
      // escape while the second read is still in flight, and its error would
      // land with nobody listening.
      await Future.wait<void>([rowsRead, statsRead]);
      final rows = await rowsRead;
      final stats = await statsRead;
      if (mounted) {
        setState(() {
          _rows = rows;
          _stats = stats;
          _spend = _dailySpend(rows);
          _error = null;
        });
      }
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    }
  }

  static List<ChartPoint> _dailySpend(List<Reimbursement> rows) {
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
    if (rows == null || stats == null) return _skeleton();

    final visible = _lens == 'all'
        ? rows
        : rows.where((r) => r.state == _lens).toList();

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
              StatTile(
                label: 'Lines',
                value: '${stats.lines}',
                caption: '${stats.humanCorrected} corrected by a reviewer',
              ),
            ],
          ),
          const SizedBox(height: 16),
          SectionCard(
            title: 'Approved spend by day',
            subtitle: 'last 28 days',
            child: DailyBarChart(
              title: 'Approved spend',
              points: _spend,
              format: (v) => '\$${v.toStringAsFixed(2)}',
              height: 140,
              showTitle: false,
            ),
          ),
          const SizedBox(height: 16),
          FlowRunsCard(
            api: widget.api,
            flowId: 'expense-reimbursement',
            subtitle:
                'the durable journal each request came out of — gates, '
                'governed reads, and replays · click one to open it',
          ),
          const SizedBox(height: 16),
          _lensRail(),
          const SizedBox(height: 14),
          if (visible.isEmpty)
            // Three states, and only one of them used to be said. "Nothing at
            // this lens" is meaningless with ALL selected — no lens is on —
            // and an empty table has two causes that are opposite claims: no
            // requests exist, or none of them is yours to see.
            CumulusEmptyState(
              icon: _seesTheQueue
                  ? Icons.table_rows_outlined
                  : Icons.lock_outline,
              title: !_seesTheQueue
                  ? 'Not scoped to you'
                  : _lens == 'all'
                  ? 'No requests yet'
                  : 'Nothing $_lens',
              message: !_seesTheQueue
                  ? 'You are not bound to the expense-review queue, so this '
                        'table holds only requests you filed yourself. This is '
                        'what you can see, not what there is.'
                  : _lens == 'all'
                  ? 'The expense-reimbursement flow has written no rows here.'
                  : 'No request is in the $_lens state. Clear the lens to see '
                        'the whole table.',
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

  /// The chrome that owes the table nothing — the same widget before the rows
  /// arrive and after.
  Widget _heading() => PageHeading(
    eyebrow: 'Workspace',
    title: 'Expense Reimbursement',
    subtitle:
        'The expense-reimbursement flow writes this table — every row '
        'carries the run journal that produced it.',
    trailing: OutlinedButton.icon(
      onPressed: _rows == null ? null : _load,
      icon: const Icon(Icons.refresh, size: 16),
      label: const Text('Refresh'),
    ),
  );

  Widget _lensRail() => Wrap(
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
  );

  /// The page while the table is in flight: real heading and lens rail, grey
  /// geometry where the strip, the daily chart and the rows will be.
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
                    SkeletonBar(width: 168, height: 13),
                    SizedBox(height: 10),
                    SkeletonBar(width: 96, height: 10),
                    SizedBox(height: 18),
                    SkeletonBar(height: 140, radius: CumulusRadius.control),
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
                      Row(
                        children: [
                          Flexible(
                            child: Text(r.vendor, style: CumulusText.rowTitle),
                          ),
                          if (r.lines.length > 1) ...[
                            const SizedBox(width: 8),
                            StatusBadge(
                              '${r.lines.length} lines',
                              dense: true,
                            ),
                          ],
                          if (r.lines.any((line) => line.isSplit)) ...[
                            const SizedBox(width: 6),
                            const Tooltip(
                              message:
                                  'The submitter claimed less than the receipt '
                                  'total — their own split, and legitimate.',
                              child: StatusBadge(
                                'split',
                                tone: CumulusTone.teal,
                                dense: true,
                              ),
                            ),
                          ],
                          if (r.reviewRounds > 0) ...[
                            const SizedBox(width: 6),
                            StatusBadge(
                              '${r.reviewRounds} review round(s)',
                              tone: CumulusTone.accent,
                              dense: true,
                            ),
                          ],
                        ],
                      ),
                      const SizedBox(height: 2),
                      Text(
                        '${r.date} · ${r.category} · ${r.employee}'
                        '${r.documents.length > 1 ? ' · ${r.documents.length} documents' : ''}',
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
          if (r.commentary.isNotEmpty) ...[
            const SizedBox(height: 10),
            const FieldLabel(
              'Submitter\u2019s commentary',
              hint: 'a governed input to the run, not a note on the side',
            ),
            Text('\u201c${r.commentary}\u201d', style: CumulusText.body),
          ],
          if (r.escalations.isNotEmpty) ...[
            const SizedBox(height: 10),
            const FieldLabel(
              'Why it was elevated',
              hint: 'typed, because each reason needs a different fix',
            ),
            for (final escalation in r.escalations)
              Padding(
                padding: const EdgeInsets.only(bottom: 4),
                child: Row(
                  children: [
                    StatusBadge(
                      escalation.kind,
                      tone: CumulusTone.warn,
                      dense: true,
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(escalation.detail, style: CumulusText.meta),
                    ),
                  ],
                ),
              ),
          ],
          if (r.lines.isNotEmpty) ...[
            const SizedBox(height: 12),
            const FieldLabel(
              'Lines',
              hint:
                  'facts, not documents — a compound receipt yields several, '
                  'and a reviewer can add one',
            ),
            for (final line in r.lines) _lineRow(r, line),
          ],
          const SizedBox(height: 12),
          Wrap(
            spacing: 6,
            runSpacing: 6,
            children: [
              _monoChip(r.policyRef),
              if (r.runId.isNotEmpty) _monoChip(r.runId),
              if (r.graphRef.isNotEmpty) _monoChip(r.graphRef),
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

  /// One line, with the two amounts side by side. The gap between them is the
  /// submitter's own split, and showing both is what makes it legible rather
  /// than looking like a mis-extraction.
  Widget _lineRow(Reimbursement r, ExpenseLine line) {
    final document = r.documents.firstWhere(
      (d) => d.documentId == line.documentId,
      orElse: () => const DocumentSlot(
        documentId: '', filename: '', memo: '', byteSize: 0, mediaType: '',
      ),
    );
    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 24,
            child: Text('${line.ordinal}.', style: CumulusText.meta),
          ),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Flexible(
                      child: Text(
                        '${line.vendor} · ${line.category}',
                        style: CumulusText.body.copyWith(fontSize: 12.5),
                      ),
                    ),
                    const SizedBox(width: 8),
                    StatusBadge(
                      line.origin,
                      tone: line.isHuman
                          ? CumulusTone.violet
                          : CumulusTone.neutral,
                      dense: true,
                    ),
                    if (line.correctedBy.isNotEmpty) ...[
                      const SizedBox(width: 6),
                      Text('by ${line.correctedBy}', style: CumulusText.meta),
                    ],
                  ],
                ),
                if (line.purpose.isNotEmpty)
                  Text(line.purpose, style: CumulusText.meta),
                if (document.filename.isNotEmpty)
                  Text(
                    '${document.filename}'
                    '${document.memo.isEmpty ? '' : ' — \u201c${document.memo}\u201d'}',
                    style: CumulusText.meta,
                  ),
              ],
            ),
          ),
          const SizedBox(width: 12),
          SizedBox(
            width: 96,
            child: Text(
              '\$${line.documentTotalUsd.toStringAsFixed(2)}',
              textAlign: TextAlign.right,
              style: CumulusText.meta.copyWith(
                decoration: line.isSplit ? TextDecoration.lineThrough : null,
              ),
            ),
          ),
          SizedBox(
            width: 96,
            child: Text(
              '\$${line.requestedUsd.toStringAsFixed(2)}',
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
