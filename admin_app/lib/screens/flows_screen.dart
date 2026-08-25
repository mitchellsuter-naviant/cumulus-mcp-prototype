import 'package:flutter/material.dart';

import '../models/flow.dart';
import '../services/api_client.dart';
import '../theme/cumulus_theme.dart';
import '../widgets/cumulus_shell.dart';
import '../widgets/mini_charts.dart';
import '../widgets/ui_kit.dart';
import 'flow_detail_screen.dart';

const _months = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
];

/// `2026-08-24` -> `Aug 24`.
String dayLabel(String isoDate) {
  final parsed = DateTime.tryParse(isoDate);
  if (parsed == null) return isoDate;
  return '${_months[parsed.month - 1]} ${parsed.day}';
}

String money(double v) => v >= 1000
    ? '\$${(v / 1000).toStringAsFixed(1)}K'
    : v >= 100
    ? '\$${v.toStringAsFixed(0)}'
    : '\$${v.toStringAsFixed(2)}';

String percent(double? v) => v == null ? '—' : '${(v * 100).toStringAsFixed(1)}%';

CumulusTone statusTone(String status) => switch (status) {
  'active' => CumulusTone.success,
  'paused' => CumulusTone.warn,
  'deprecated' => CumulusTone.danger,
  _ => CumulusTone.neutral,
};

/// The Flows nav section: fleet landing page, with drill-down into one
/// definition. Everything on screen arrives through the governed REST
/// contract — the shell holds no privileged path.
class FlowsSection extends StatefulWidget {
  const FlowsSection({super.key});

  @override
  State<FlowsSection> createState() => _FlowsSectionState();
}

class _FlowsSectionState extends State<FlowsSection> {
  final ApiClient _api = ApiClient();
  FleetStats? _stats;
  List<FlowSummary> _all = [];
  String _myRole = 'user';
  bool _loading = true;
  String? _error;

  final TextEditingController _search = TextEditingController();
  String? _status;
  String? _team;

  /// The definition currently drilled into, if any.
  String? _detailId;

  @override
  void initState() {
    super.initState();
    _search.addListener(() => setState(() {}));
    _refresh();
  }

  @override
  void dispose() {
    _search.dispose();
    _api.close();
    super.dispose();
  }

  Future<void> _refresh() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final results = await Future.wait([
        _api.flowFleetStats(),
        _api.listFlows(),
      ]);
      try {
        final me = await _api.whoami();
        _myRole = me['role'] as String? ?? 'user';
      } catch (_) {}
      if (!mounted) return;
      setState(() {
        _stats = results[0] as FleetStats;
        _all = results[1] as List<FlowSummary>;
        _loading = false;
      });
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.message;
        _loading = false;
      });
    }
  }

  List<FlowSummary> get _filtered {
    final needle = _search.text.trim().toLowerCase();
    return _all.where((w) {
      if (_status != null && w.status != _status) return false;
      if (_team != null && w.team != _team) return false;
      if (needle.isNotEmpty) {
        final haystack = '${w.id} ${w.name} ${w.description} ${w.team} '
                '${w.tags.join(' ')}'
            .toLowerCase();
        if (!haystack.contains(needle)) return false;
      }
      return true;
    }).toList();
  }

  @override
  Widget build(BuildContext context) {
    if (_detailId != null) {
      return FlowDetailScreen(
        key: ValueKey(_detailId),
        flowId: _detailId!,
        myRole: _myRole,
        onBack: () {
          setState(() => _detailId = null);
          _refresh();
        },
      );
    }
    if (_loading) return const CumulusLoading();
    if (_error != null) {
      return PageBody(
        children: [ErrorNotice(message: _error!, onRetry: _refresh)],
      );
    }
    final stats = _stats!;
    final rows = _filtered;
    final teams = _all.map((w) => w.team).where((t) => t.isNotEmpty).toSet().toList()
      ..sort();
    return PageBody(
      children: [
        PageHeading(
          eyebrow: 'Execution layer · Authored logic',
          title: 'Flows',
          subtitle:
              'Every Flow in the enterprise — the authored logic that does '
              'the work, governed, costed, and auditable.',
          pills: [
            MetaPill('${stats.definitions} definitions'),
            if (stats.attention.isNotEmpty)
              MetaPill(
                '${stats.attention.length} need attention',
                tone: CumulusTone.warn,
              ),
          ],
          trailing: IconButton(
            tooltip: 'Refresh',
            onPressed: _refresh,
            icon: const Icon(Icons.refresh, size: 18),
          ),
        ),
        StatStrip(
          tiles: [
            StatTile(
              label: 'Definitions',
              value: '${stats.definitions}',
              caption:
                  '${stats.active} active · ${stats.draft} draft · '
                  '${stats.paused} paused',
            ),
            StatTile(
              label: 'Runs · 30d',
              value: compactCount(stats.runs),
              caption: '≈${(stats.runs / stats.days).round()} per day',
            ),
            StatTile(
              label: 'Model spend · 30d',
              value: money(stats.modelSpendUsd),
              caption:
                  'from ${compactCount(stats.modelCalls)} audited ModelCalls',
            ),
            StatTile(
              label: 'Hours returned · 30d',
              value: '${stats.hoursReturned.round()}h',
              caption: '≈${money(stats.roiUsd)} at loaded cost',
              valueColor: CumulusColors.success,
            ),
            StatTile(
              label: 'Awaiting judgment',
              value: '${stats.tasksOpen}',
              caption: 'open tasks across queues',
              valueColor: stats.tasksOpen > 0 ? CumulusColors.warn : null,
            ),
            StatTile(
              label: 'Success rate',
              value: percent(stats.successRate),
              caption: '${stats.escalated} escalated in ${stats.days}d',
            ),
          ],
        ),
        SectionCard(
          title: 'Fleet trend',
          subtitle: 'last ${stats.days} days · hover any day for the breakdown',
          child: LayoutBuilder(
            builder: (context, constraints) {
              final runsChart = DailyBarChart(
                title: 'Runs / day',
                points: [
                  for (final d in stats.series)
                    ChartPoint(
                      dayLabel(d.date),
                      d.runs.toDouble(),
                      extra: [
                        '${money(d.costUsd)} model spend',
                        '${d.tasksRaised} tasks raised',
                      ],
                    ),
                ],
                format: (v) => v.round().toString(),
              );
              final spendChart = DailyBarChart(
                title: 'Model spend / day · from audited ModelCalls',
                points: [
                  for (final d in stats.series)
                    ChartPoint(
                      dayLabel(d.date),
                      d.costUsd,
                      extra: ['${d.runs} runs'],
                    ),
                ],
                format: money,
              );
              if (constraints.maxWidth < 860) {
                return Column(
                  spacing: 20,
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [runsChart, spendChart],
                );
              }
              return Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                spacing: 32,
                children: [
                  Expanded(child: runsChart),
                  Expanded(child: spendChart),
                ],
              );
            },
          ),
        ),
        if (stats.attention.isNotEmpty)
          SectionCard(
            title: 'Needs attention',
            subtitle: 'below the success bar, or missing its golden set',
            borderColor: CumulusColors.warn.withValues(alpha: 0.4),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              spacing: 8,
              children: [
                for (final item in stats.attention)
                  InkWell(
                    onTap: () => setState(() => _detailId = item.flowId),
                    borderRadius: BorderRadius.circular(8),
                    child: Padding(
                      padding: const EdgeInsets.symmetric(vertical: 2),
                      child: Row(
                        children: [
                          const Icon(
                            Icons.warning_amber_outlined,
                            size: 15,
                            color: CumulusColors.warn,
                          ),
                          const SizedBox(width: 8),
                          Text(item.flowId, style: CumulusText.mono),
                          const SizedBox(width: 10),
                          Expanded(
                            child: Text(item.reason, style: CumulusText.meta),
                          ),
                        ],
                      ),
                    ),
                  ),
              ],
            ),
          ),
        // Search + lens row.
        Wrap(
          spacing: 8,
          runSpacing: 8,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            SizedBox(
              width: 320,
              child: TextField(
                controller: _search,
                decoration: InputDecoration(
                  hintText: 'Filter by name, tag, or team…',
                  prefixIcon: const Icon(Icons.search, size: 16),
                  suffixIcon: _search.text.isEmpty
                      ? null
                      : IconButton(
                          icon: const Icon(Icons.close, size: 14),
                          onPressed: _search.clear,
                        ),
                ),
              ),
            ),
            LensChip(
              label: 'All',
              selected: _status == null,
              onTap: () => setState(() => _status = null),
            ),
            for (final status in const ['active', 'paused', 'draft', 'deprecated'])
              LensChip(
                label: status,
                tone: statusTone(status),
                selected: _status == status,
                onTap: () => setState(
                  () => _status = _status == status ? null : status,
                ),
              ),
            if (teams.isNotEmpty)
              DropdownButtonHideUnderline(
                child: DropdownButton<String?>(
                  value: _team,
                  hint: const Text(
                    'All teams',
                    style: TextStyle(fontSize: 13),
                  ),
                  style: const TextStyle(
                    fontSize: 13,
                    color: CumulusColors.ink,
                  ),
                  borderRadius: BorderRadius.circular(CumulusRadius.control),
                  items: [
                    const DropdownMenuItem<String?>(
                      value: null,
                      child: Text('All teams'),
                    ),
                    for (final team in teams)
                      DropdownMenuItem<String?>(value: team, child: Text(team)),
                  ],
                  onChanged: (v) => setState(() => _team = v),
                ),
              ),
          ],
        ),
        ListCard(
          title: 'Definitions',
          subtitle:
              '${rows.length} of ${_all.length} shown · sorted by model spend, 30 days',
          headerTrailing: const _ColumnHeads(),
          children: rows.isEmpty
              ? [
                  const CumulusEmptyState(
                    icon: Icons.account_tree_outlined,
                    title: 'Nothing matches',
                    message:
                        'No definition matches the current filters. Clear a '
                        'lens or the search to widen the list.',
                  ),
                ]
              : [
                  for (final w in rows)
                    CumulusListRow(
                      onTap: () => setState(() => _detailId = w.id),
                      leading: Container(
                        width: 32,
                        height: 32,
                        alignment: Alignment.center,
                        decoration: BoxDecoration(
                          color: statusTone(w.status).background,
                          shape: BoxShape.circle,
                        ),
                        child: Icon(
                          Icons.account_tree_outlined,
                          size: 16,
                          color: statusTone(w.status).foreground,
                        ),
                      ),
                      title: Row(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Flexible(
                            child: Text(
                              w.id,
                              overflow: TextOverflow.ellipsis,
                              style: CumulusText.mono.copyWith(
                                fontWeight: FontWeight.w600,
                              ),
                            ),
                          ),
                          if (w.tasksOpen > 0) ...[
                            const SizedBox(width: 6),
                            StatusBadge(
                              '${w.tasksOpen} waiting',
                              tone: CumulusTone.warn,
                              dense: true,
                            ),
                          ],
                        ],
                      ),
                      subtitle: Text(
                        '${w.description} · ${w.team.isEmpty ? 'unassigned' : w.team}',
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                      ),
                      trailing: [
                        SizedBox(
                          width: 120,
                          child: Align(
                            alignment: Alignment.centerRight,
                            child: Sparkline(values: w.spark),
                          ),
                        ),
                        const SizedBox(width: 14),
                        _NumCell(compactCount(w.runs30d), width: 90),
                        const SizedBox(width: 14),
                        _NumCell(money(w.cost30dUsd), width: 80),
                        const SizedBox(width: 14),
                        _NumCell(
                          percent(w.success30d),
                          width: 70,
                          color: w.success30d == null
                              ? CumulusColors.inkMuted
                              : w.success30d! < 0.95
                              ? CumulusColors.warn
                              : CumulusColors.success,
                        ),
                        const SizedBox(width: 14),
                        SizedBox(
                          width: 90,
                          child: Align(
                            alignment: Alignment.centerRight,
                            child: StatusBadge(
                              w.status,
                              tone: statusTone(w.status),
                              dense: true,
                              uppercase: true,
                            ),
                          ),
                        ),
                        const SizedBox(width: 14),
                        const Icon(
                          Icons.north_east,
                          size: 14,
                          color: CumulusColors.inkMuted,
                        ),
                      ],
                    ),
                ],
        ),
      ],
    );
  }
}

/// Right-aligned column captions matching the row cell widths exactly.
class _ColumnHeads extends StatelessWidget {
  const _ColumnHeads();

  @override
  Widget build(BuildContext context) {
    Widget head(String text, double width) => SizedBox(
      width: width,
      child: Text(
        text.toUpperCase(),
        textAlign: TextAlign.right,
        style: CumulusText.statLabel,
      ),
    );
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        head('Trend 14d', 120),
        const SizedBox(width: 14),
        head('Runs 30d', 90),
        const SizedBox(width: 14),
        head('Spend 30d', 80),
        const SizedBox(width: 14),
        head('Success', 70),
        const SizedBox(width: 14),
        head('Status', 90),
        const SizedBox(width: 28),
      ],
    );
  }
}

class _NumCell extends StatelessWidget {
  final String text;
  final double width;
  final Color? color;

  const _NumCell(this.text, {required this.width, this.color});

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: width,
      child: Text(
        text,
        textAlign: TextAlign.right,
        style: TextStyle(
          fontSize: 12.5,
          fontWeight: FontWeight.w600,
          color: color ?? CumulusColors.ink,
        ),
      ),
    );
  }
}
