import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../models/workflow.dart';
import '../services/api_client.dart';
import '../theme/cumulus_theme.dart';
import '../widgets/cumulus_shell.dart';
import '../widgets/mini_charts.dart';
import '../widgets/side_drawer.dart';
import '../widgets/ui_kit.dart';
import '../widgets/workflow_canvas.dart';
import 'workflows_screen.dart' show dayLabel, kindTone, money, percent, statusTone;

String runStatusLabel(String status) => switch (status) {
  'waiting_on_human' => 'waiting on human',
  _ => status,
};

CumulusTone runTone(String status) => switch (status) {
  'completed' => CumulusTone.success,
  'waiting_on_human' => CumulusTone.accent,
  'escalated' || 'failed' => CumulusTone.danger,
  _ => CumulusTone.neutral,
};

CumulusTone recommendationTone(String kind) => switch (kind) {
  'model-downgrade' => CumulusTone.success,
  'prompt-cache' => CumulusTone.violet,
  'batching' => CumulusTone.teal,
  'budget' => CumulusTone.warn,
  _ => CumulusTone.neutral,
};

const _runsPerPage = 10;

/// One definition, in three views: what it is and how it is doing, what it
/// looks like as a graph, and how a candidate version measures up.
class WorkflowDetailScreen extends StatefulWidget {
  final String workflowId;
  final String myRole;
  final VoidCallback onBack;

  const WorkflowDetailScreen({
    super.key,
    required this.workflowId,
    required this.myRole,
    required this.onBack,
  });

  @override
  State<WorkflowDetailScreen> createState() => _WorkflowDetailScreenState();
}

class _WorkflowDetailScreenState extends State<WorkflowDetailScreen> {
  final ApiClient _api = ApiClient();
  final ChartSync _chartSync = ChartSync();

  WorkflowDetail? _detail;
  WorkflowUsage? _usage;
  List<RunSummary> _runs = [];
  GoldenSetInfo? _golden;
  List<TestRun> _tests = [];
  List<Recommendation> _recommendations = [];
  List<WorkflowVersion> _versions = [];

  /// The version the canvas is showing. Defaults to the draft when one is
  /// open, because a draft exists to be reviewed.
  int? _selectedVersion;
  List<StepDiff> _canvasSteps = [];
  String? _selectedStepId;

  bool _loading = true;
  String? _error;
  int _tab = 0;
  int _runPage = 0;
  bool _launching = false;
  String? _applyingRecommendation;
  Timer? _poll;

  bool get _isAdmin => widget.myRole == 'admin' || widget.myRole == 'owner';

  WorkflowVersion? get _current => _versions
      .where((v) => v.version == _selectedVersion)
      .firstOrNull;

  WorkflowVersion? get _activeVersion =>
      _versions.where((v) => v.isActive).firstOrNull;

  WorkflowVersion? get _draftVersion =>
      _versions.where((v) => v.isDraft).firstOrNull;

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  @override
  void dispose() {
    _poll?.cancel();
    _chartSync.dispose();
    _api.close();
    super.dispose();
  }

  Future<void> _refresh() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final id = widget.workflowId;
      final results = await Future.wait([
        _api.getWorkflow(id),
        _api.workflowUsage(id),
        _api.workflowRuns(id, limit: 60),
        _api.workflowGoldenSet(id),
        _api.workflowTestRuns(id),
        _api.workflowVersions(id),
        _api.workflowRecommendations(id),
      ]);
      if (!mounted) return;
      final versions = results[5] as List<WorkflowVersion>;
      setState(() {
        _detail = results[0] as WorkflowDetail;
        _usage = results[1] as WorkflowUsage;
        _runs = results[2] as List<RunSummary>;
        _golden = results[3] as GoldenSetInfo;
        _tests = results[4] as List<TestRun>;
        _versions = versions;
        _recommendations = results[6] as List<Recommendation>;
        _selectedVersion ??= (versions.where((v) => v.isDraft).firstOrNull ??
                versions.where((v) => v.isActive).firstOrNull)
            ?.version;
        _loading = false;
      });
      await _loadCanvas();
      _ensurePolling();
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.message;
        _loading = false;
      });
    }
  }

  /// A draft is rendered as a diff against the active version; anything else
  /// is rendered as itself.
  Future<void> _loadCanvas() async {
    final version = _current;
    if (version == null) return;
    if (!version.isDraft) {
      setState(() {
        _canvasSteps = [
          for (final step in version.steps)
            StepDiff(step: step, change: 'unchanged', changedFields: const []),
        ];
      });
      return;
    }
    try {
      final diff = await _api.workflowVersionDiff(
        widget.workflowId,
        version.version,
      );
      if (!mounted) return;
      setState(() => _canvasSteps = diff);
    } on ApiException catch (e) {
      _snack(e.message);
    }
  }

  void _ensurePolling() {
    if (_tests.any((t) => t.running)) {
      _poll ??= Timer.periodic(const Duration(seconds: 1), (_) async {
        try {
          final tests = await _api.workflowTestRuns(widget.workflowId);
          if (!mounted) return;
          setState(() => _tests = tests);
          if (!tests.any((t) => t.running)) {
            _poll?.cancel();
            _poll = null;
          }
        } catch (_) {}
      });
    }
  }

  void _snack(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(message)));
  }

  Future<void> _setStatus(String status) async {
    try {
      final updated = await _api.updateWorkflow(widget.workflowId, status: status);
      setState(() => _detail = updated);
      _snack('Status set to $status');
    } on ApiException catch (e) {
      _snack(e.message);
    }
  }

  Future<void> _launchTest() async {
    if (_launching) return;
    setState(() => _launching = true);
    try {
      await _api.launchTestRun(
        widget.workflowId,
        candidateVersion: _draftVersion?.display ?? '',
        baselineVersion: _activeVersion?.display ?? '',
      );
      final tests = await _api.workflowTestRuns(widget.workflowId);
      if (!mounted) return;
      setState(() {
        _tests = tests;
        _tab = 2;
      });
      _ensurePolling();
    } on ApiException catch (e) {
      _snack(e.message);
    } finally {
      if (mounted) setState(() => _launching = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_loading && _detail == null) return const CumulusLoading();
    if (_error != null && _detail == null) {
      return PageBody(
        children: [
          BackLink(label: 'All workflows', onTap: widget.onBack),
          ErrorNotice(message: _error!, onRetry: _refresh),
        ],
      );
    }
    final w = _detail!;
    return PageBody(
      children: [
        BackLink(label: 'All workflows', onTap: widget.onBack),
        _topBar(w),
        SegmentedTabs(
          labels: const [
            'Overview & telemetry',
            'Workflow canvas',
            'Evaluation & optimization',
          ],
          selectedIndex: _tab,
          onChanged: (i) => setState(() => _tab = i),
        ),
        ...switch (_tab) {
          0 => _overviewTab(w),
          1 => _canvasTab(),
          _ => _evaluationTab(),
        },
      ],
    );
  }

  // ---- top bar ------------------------------------------------------------

  Widget _topBar(WorkflowDetail w) {
    return PageHeading(
      eyebrow: '${w.kind} · ${w.team.isEmpty ? 'unassigned' : w.team}',
      title: w.id,
      subtitle: w.description,
      pills: [
        StatusBadge(w.kind, tone: kindTone(w.kind), uppercase: true),
        StatusBadge(w.status, tone: statusTone(w.status), uppercase: true),
        _VersionSelector(
          versions: _versions,
          selected: _selectedVersion,
          onChanged: (version) {
            setState(() {
              _selectedVersion = version;
              _selectedStepId = null;
              // The selector governs the canvas, so jumping versions takes
              // you to the view where that choice is visible.
              _tab = 1;
            });
            _loadCanvas();
          },
        ),
      ],
      trailing: _isAdmin
          ? Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                if (w.status == 'active')
                  OutlinedButton(
                    onPressed: () => _setStatus('paused'),
                    child: const Text('Pause'),
                  )
                else if (w.status != 'deprecated')
                  OutlinedButton(
                    onPressed: () => _setStatus('active'),
                    child: const Text('Activate'),
                  ),
                const SizedBox(width: 10),
                FilledButton(
                  onPressed: _launching ? null : _launchTest,
                  child: Text(_launching ? 'Launching…' : 'Run test suite'),
                ),
              ],
            )
          : null,
    );
  }

  // ---- Tab 1: overview & telemetry ---------------------------------------

  List<Widget> _overviewTab(WorkflowDetail w) {
    final usage = _usage!;
    return [
      LayoutBuilder(
        builder: (context, constraints) {
          final metadata = _metadataCard(w);
          final kpis = _kpiGrid(usage);
          if (constraints.maxWidth < 1040) {
            return Column(
              spacing: 16,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [metadata, kpis],
            );
          }
          return Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            spacing: 16,
            children: [
              SizedBox(width: 420, child: metadata),
              Expanded(child: kpis),
            ],
          );
        },
      ),
      SectionCard(
        title: 'Usage · ${usage.days} days',
        subtitle: 'hover any day — all three charts follow the same cursor',
        child: LayoutBuilder(
          builder: (context, constraints) {
            final charts = [
              DailyBarChart(
                title: 'Runs / day',
                height: 128,
                sync: _chartSync,
                points: [
                  for (final d in usage.series)
                    ChartPoint(
                      dayLabel(d.date),
                      d.runs.toDouble(),
                      extra: ['${d.tasksRaised} tasks raised'],
                    ),
                ],
                format: (v) => v.round().toString(),
              ),
              DailyBarChart(
                title: 'Model spend / day',
                height: 128,
                sync: _chartSync,
                points: [
                  for (final d in usage.series)
                    ChartPoint(dayLabel(d.date), d.costUsd),
                ],
                format: money,
              ),
              DailyBarChart(
                title: 'Tokens / day',
                height: 128,
                sync: _chartSync,
                points: [
                  for (final d in usage.series)
                    ChartPoint(dayLabel(d.date), d.tokens.toDouble()),
                ],
                format: (v) => compactCount(v.round()),
              ),
            ];
            if (constraints.maxWidth < 1000) {
              return Column(
                spacing: 22,
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: charts,
              );
            }
            return Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              spacing: 28,
              children: [for (final c in charts) Expanded(child: c)],
            );
          },
        ),
      ),
      _recentRunsCard(),
    ];
  }

  Widget _kpiGrid(WorkflowUsage usage) {
    return StatStrip(
      tiles: [
        StatTile(
          label: 'Runs · ${usage.days}d',
          value: compactCount(usage.runs),
          caption: '${usage.completed} completed',
        ),
        StatTile(
          label: 'Success rate',
          value: percent(usage.successRate),
          caption: '${usage.escalated} escalated',
        ),
        StatTile(
          label: 'Model spend',
          value: money(usage.costUsd),
          caption:
              '${compactCount(usage.modelCalls)} ModelCalls · '
              '${compactCount(usage.tokens)} tokens',
        ),
        StatTile(
          label: 'Human judgment',
          value: compactCount(usage.tasksRaised),
          caption: usage.runs == 0
              ? 'no runs yet'
              : '${(usage.tasksRaised * 100 / usage.runs).toStringAsFixed(1)}% of runs',
        ),
        StatTile(
          label: 'Time saved',
          value: '${(usage.minutesSaved / 60).round()}h',
          caption: '≈${money(usage.roiUsd)} at loaded cost',
          valueColor: CumulusColors.success,
        ),
      ],
    );
  }

  Widget _metadataCard(WorkflowDetail w) {
    Widget row(String label, String value) => Padding(
      padding: const EdgeInsets.only(bottom: 13),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 128,
            child: Text(label.toUpperCase(), style: CumulusText.statLabel),
          ),
          Expanded(
            child: Text(
              value,
              style: const TextStyle(
                fontSize: 12.5,
                color: CumulusColors.ink,
                height: 1.45,
              ),
            ),
          ),
        ],
      ),
    );
    final trigger = w.steps.where((s) => s.type == 'trigger').firstOrNull;
    return SectionCard(
      title: 'Definition',
      subtitle: 'Tier-2 map asset · transparent export · no opaque steps',
      headerTrailing: _isAdmin
          ? TextButton.icon(
              onPressed: () => _editRoi(w),
              icon: const Icon(Icons.tune, size: 15),
              label: const Text('Edit ROI'),
            )
          : null,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          row(
            'Kind',
            w.kind == 'dataflow'
                ? 'DataFlow — data/knowledge layer; populates and maintains the model'
                : 'Workflow — business-logic layer; one run per case',
          ),
          if (trigger != null)
            row('Trigger', trigger.config['event'] ?? trigger.name),
          row('Authored by', '${w.authoredBy} · Python + decorators → export'),
          row(
            'Version',
            '${w.activeVersionLabel} active · ${w.versionCount} in history'
            '${w.draftVersionLabel.isEmpty ? '' : ' · ${w.draftVersionLabel} open'}',
          ),
          if (w.queues.isNotEmpty) row('Queues', w.queues.join(', ')),
          if (w.workspace.isNotEmpty) row('Workspace', w.workspace),
          row(
            'Model budget',
            '\$${w.modelBudgetUsdPerRun.toStringAsFixed(2)} cap per run',
          ),
          row(
            'ROI assumption',
            '${w.roiMinutesPerRun.toStringAsFixed(0)} min saved per completed '
                'run · \$${w.roiHourlyRateUsd.toStringAsFixed(0)}/h loaded',
          ),
          Container(
            margin: const EdgeInsets.only(top: 5),
            padding: const EdgeInsets.all(13),
            decoration: BoxDecoration(
              color: CumulusColors.surfaceMuted,
              borderRadius: BorderRadius.circular(CumulusRadius.control),
            ),
            child: Text(
              'ROI assumptions are governed knowledge — versioned and '
              'editable here; every change lands in the ledger.',
              style: CumulusText.meta,
            ),
          ),
        ],
      ),
    );
  }

  Future<void> _editRoi(WorkflowDetail w) async {
    final minutes = TextEditingController(
      text: w.roiMinutesPerRun.toStringAsFixed(0),
    );
    final rate = TextEditingController(
      text: w.roiHourlyRateUsd.toStringAsFixed(0),
    );
    final saved = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('ROI assumptions'),
        content: SizedBox(
          width: 380,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              LabeledField(
                label: 'Minutes saved per completed run',
                child: TextField(
                  controller: minutes,
                  keyboardType: TextInputType.number,
                ),
              ),
              const SizedBox(height: 14),
              LabeledField(
                label: 'Loaded hourly rate · USD',
                child: TextField(
                  controller: rate,
                  keyboardType: TextInputType.number,
                ),
              ),
            ],
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(context).pop(true),
            child: const Text('Save'),
          ),
        ],
      ),
    );
    if (saved != true) return;
    try {
      final updated = await _api.updateWorkflow(
        widget.workflowId,
        roiMinutesPerRun: double.tryParse(minutes.text),
        roiHourlyRateUsd: double.tryParse(rate.text),
      );
      if (!mounted) return;
      setState(() => _detail = updated);
    } on ApiException catch (e) {
      _snack(e.message);
    }
  }

  Widget _recentRunsCard() {
    final pages = (_runs.length / _runsPerPage).ceil().clamp(1, 999);
    final page = _runPage.clamp(0, pages - 1);
    final start = page * _runsPerPage;
    final visible = _runs.skip(start).take(_runsPerPage).toList();
    return ListCard(
      title: 'Recent runs',
      subtitle: 'click a run to inspect its full execution trail',
      headerTrailing: _runs.length <= _runsPerPage
          ? null
          : Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(
                  '${start + 1}–${start + visible.length} of ${_runs.length}',
                  style: CumulusText.meta,
                ),
                IconButton(
                  tooltip: 'Newer',
                  icon: const Icon(Icons.chevron_left, size: 18),
                  onPressed:
                      page == 0 ? null : () => setState(() => _runPage = page - 1),
                ),
                IconButton(
                  tooltip: 'Older',
                  icon: const Icon(Icons.chevron_right, size: 18),
                  onPressed: page >= pages - 1
                      ? null
                      : () => setState(() => _runPage = page + 1),
                ),
              ],
            ),
      children: visible.isEmpty
          ? [
              const CumulusEmptyState(
                icon: Icons.play_circle_outline,
                title: 'No runs yet',
                message: 'This definition has not executed in the journal window.',
              ),
            ]
          : [
              for (final run in visible)
                CumulusListRow(
                  onTap: () => _openRun(run),
                  leading: Container(
                    width: 8,
                    height: 8,
                    decoration: BoxDecoration(
                      color: runTone(run.status).dot,
                      shape: BoxShape.circle,
                    ),
                  ),
                  title: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Text(run.id, style: CumulusText.mono),
                      const SizedBox(width: 10),
                      StatusBadge(
                        runStatusLabel(run.status),
                        tone: runTone(run.status),
                        dense: true,
                        uppercase: true,
                      ),
                    ],
                  ),
                  subtitle: Text(
                    '${run.caseRef} · ${run.modelCalls} '
                    '${run.modelCalls == 1 ? 'ModelCall' : 'ModelCalls'} · '
                    '${run.actionsCommitted} '
                    '${run.actionsCommitted == 1 ? 'Action' : 'Actions'}'
                    '${run.tasksRaised > 0 ? ' · task raised' : ''}',
                  ),
                  trailing: [
                    SizedBox(
                      width: 130,
                      child: Text(
                        run.suspendedMinutes > 0
                            ? '${run.activeSeconds.round()}s + ${run.suspendedMinutes}m wait'
                            : '${run.activeSeconds.round()}s',
                        textAlign: TextAlign.right,
                        style: CumulusText.meta,
                      ),
                    ),
                    const SizedBox(width: 16),
                    SizedBox(
                      width: 60,
                      child: Text(
                        '\$${run.costUsd.toStringAsFixed(2)}',
                        textAlign: TextAlign.right,
                        style: const TextStyle(
                          fontSize: 12.5,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                    ),
                    const SizedBox(width: 16),
                    SizedBox(
                      width: 80,
                      child: Text(
                        agoLabel(run.startedAt),
                        textAlign: TextAlign.right,
                        style: CumulusText.meta,
                      ),
                    ),
                  ],
                ),
            ],
    );
  }

  Future<void> _openRun(RunSummary run) async {
    RunDetail detail;
    try {
      detail = await _api.workflowRun(widget.workflowId, run.id);
    } on ApiException catch (e) {
      _snack(e.message);
      return;
    }
    if (!mounted) return;
    await showSideDrawer<void>(
      context: context,
      title: detail.id,
      titleBadges: [
        StatusBadge(
          runStatusLabel(detail.status),
          tone: runTone(detail.status),
          dense: true,
          uppercase: true,
        ),
      ],
      subtitle:
          '${detail.caseRef} · ${detail.activeSeconds.round()}s active'
          '${detail.suspendedMinutes > 0 ? ' · ${detail.suspendedMinutes}m suspended' : ''}'
          ' · \$${detail.costUsd.toStringAsFixed(2)} model spend'
          ' · ${compactCount(detail.tokensIn)} in / ${compactCount(detail.tokensOut)} out',
      actions: [
        if (_isAdmin)
          FilledButton.icon(
            onPressed: () {
              Navigator.of(context).pop();
              _promoteRun(detail);
            },
            icon: const Icon(Icons.playlist_add_check, size: 16),
            label: const Text('Promote to golden set'),
          ),
      ],
      builder: (context) => _RunAuditTrail(run: detail),
    );
  }

  /// The flywheel in miniature: an execution that needed judgment becomes the
  /// case that guards against needing it again.
  Future<void> _promoteRun(RunDetail run) async {
    final name = TextEditingController(text: 'Promoted ${run.caseRef}');
    final expected = TextEditingController(
      text: run.steps
          .where((s) => s.type == 'action' && s.status == 'completed')
          .map((s) => '${s.name} committed')
          .join(' · '),
    );
    final saved = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Promote to golden set'),
        content: SizedBox(
          width: 460,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                'The case keeps a pointer back to ${run.id}, so a future '
                'failure on it is traceable to the execution it came from.',
                style: CumulusText.meta,
              ),
              const SizedBox(height: 16),
              LabeledField(label: 'Name', child: TextField(controller: name)),
              const SizedBox(height: 14),
              LabeledField(
                label: 'Expected outcome',
                hint: 'what a passing run must reproduce',
                child: TextField(controller: expected, maxLines: 2),
              ),
            ],
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(context).pop(true),
            child: const Text('Promote'),
          ),
        ],
      ),
    );
    if (saved != true || name.text.trim().isEmpty) return;
    try {
      final golden = await _api.addGoldenCase(
        widget.workflowId,
        name: name.text.trim(),
        expected: expected.text.trim(),
        inputRef: run.caseRef,
        runId: run.id,
        payload: run.steps.isEmpty ? '' : run.steps.first.inputPreview,
      );
      if (!mounted) return;
      setState(() {
        _golden = golden;
        _tab = 2;
      });
      _snack('Promoted ${run.caseRef} to the golden set');
    } on ApiException catch (e) {
      _snack(e.message);
    }
  }

  // ---- Tab 2: canvas ------------------------------------------------------

  List<Widget> _canvasTab() {
    final version = _current;
    final active = _activeVersion;
    return [
      SectionCard(
        title: 'Workflow canvas',
        subtitle: version == null
            ? 'the declarative export as a graph'
            : '${version.display} · ${version.notes}',
        child: WorkflowCanvas(
          steps: _canvasSteps,
          diffAgainst: (version?.isDraft ?? false) && active != null
              ? '${active.display} (active)'
              : '',
          selectedStepId: _selectedStepId,
          onSelect: (step) {
            setState(() => _selectedStepId = step?.id);
            if (step != null) _openNodeInspector(step);
          },
          onExportSchema: version == null ? null : () => _exportSchema(version),
        ),
      ),
    ];
  }

  Future<void> _openNodeInspector(WorkflowStep step) async {
    final diff = _canvasSteps.where((d) => d.step.id == step.id).firstOrNull;
    await showSideDrawer<void>(
      context: context,
      width: 520,
      title: step.name,
      titleBadges: [
        StatusBadge(step.type, tone: stepTone(step.type), dense: true, uppercase: true),
        if (governedBadge(step.type) != null)
          StatusBadge(
            governedBadge(step.type)!,
            tone: stepTone(step.type),
            dense: true,
          ),
        if (diff != null && diff.change != 'unchanged')
          StatusBadge(
            diff.change,
            tone: switch (diff.change) {
              'added' => CumulusTone.success,
              'removed' => CumulusTone.danger,
              _ => CumulusTone.warn,
            },
            dense: true,
            uppercase: true,
          ),
      ],
      subtitle: step.summary,
      builder: (context) => _NodeInspector(step: step, diff: diff),
    );
    if (mounted) setState(() => _selectedStepId = null);
  }

  Future<void> _exportSchema(WorkflowVersion version) async {
    final schema = _schemaJson(version);
    await showDialog<void>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text('Schema · ${version.display}'),
        content: SizedBox(
          width: 620,
          height: 420,
          child: SingleChildScrollView(
            child: SelectableText(
              schema,
              style: CumulusText.mono.copyWith(fontSize: 11.5, height: 1.5),
            ),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(),
            child: const Text('Close'),
          ),
          FilledButton.icon(
            onPressed: () async {
              await Clipboard.setData(ClipboardData(text: schema));
              if (context.mounted) Navigator.of(context).pop();
              _snack('Schema copied to the clipboard');
            },
            icon: const Icon(Icons.copy, size: 15),
            label: const Text('Copy'),
          ),
        ],
      ),
    );
  }

  /// The portable form of the definition — what the customer keeps. Written
  /// out here rather than fetched so it always matches the version on screen.
  String _schemaJson(WorkflowVersion version) {
    String esc(String s) => s.replaceAll('"', r'\"');
    final steps = version.steps
        .map(
          (s) =>
              '    {\n'
              '      "id": "${s.id}",\n'
              '      "type": "${s.type}",\n'
              '      "name": "${esc(s.name)}",\n'
              '      "summary": "${esc(s.summary)}",\n'
              '      "config": {${s.config.entries.map((e) => '"${e.key}": "${esc(e.value)}"').join(', ')}},\n'
              '      "next": [${s.next.map((n) => '"$n"').join(', ')}],\n'
              '      "branch_labels": [${s.branchLabels.map((b) => '"$b"').join(', ')}]\n'
              '    }',
        )
        .join(',\n');
    return '{\n'
        '  "workflow": "${widget.workflowId}",\n'
        '  "version": "${version.display}",\n'
        '  "status": "${version.status}",\n'
        '  "model_budget_usd_per_run": ${version.modelBudgetUsdPerRun},\n'
        '  "steps": [\n$steps\n  ]\n'
        '}';
  }

  // ---- Tab 3: evaluation & optimization ----------------------------------

  List<Widget> _evaluationTab() {
    return [
      LayoutBuilder(
        builder: (context, constraints) {
          final golden = _goldenCard();
          final right = Column(
            spacing: 16,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [_harnessCard(), _recommenderCard()],
          );
          if (constraints.maxWidth < 1120) {
            return Column(
              spacing: 16,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [golden, right],
            );
          }
          return Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            spacing: 16,
            children: [
              Expanded(child: golden),
              Expanded(child: right),
            ],
          );
        },
      ),
    ];
  }

  Widget _goldenCard() {
    final golden = _golden;
    final cases = golden?.cases ?? const <GoldenCase>[];
    return ListCard(
      title: 'Golden dataset',
      subtitle: '${cases.length} cases · knowledge-role Dataset',
      headerTrailing: _isAdmin
          ? TextButton.icon(
              onPressed: _addGoldenCase,
              icon: const Icon(Icons.add, size: 15),
              label: const Text('Add case'),
            )
          : null,
      children: [
        if (golden != null && golden.syncedFrom.isNotEmpty)
          Container(
            margin: const EdgeInsets.fromLTRB(20, 0, 20, 10),
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
            decoration: BoxDecoration(
              color: CumulusColors.surfaceMuted,
              borderRadius: BorderRadius.circular(CumulusRadius.control),
            ),
            child: Row(
              children: [
                const Icon(Icons.sync, size: 15, color: CumulusColors.inkMuted),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    'Seeded from ${golden.syncedFrom}'
                    '${golden.syncedAt == null ? '' : ' · synced ${agoLabel(golden.syncedAt!)}'}',
                    style: CumulusText.meta,
                  ),
                ),
              ],
            ),
          ),
        if (cases.isEmpty)
          const CumulusEmptyState(
            icon: Icons.dataset_outlined,
            title: 'No golden cases yet',
            message:
                'Promote a run, or add a case by hand, before grading a '
                'candidate version.',
          )
        else
          for (final c in cases)
            CumulusListRow(
              leading: SizedBox(
                width: 96,
                child: Text(
                  c.id,
                  overflow: TextOverflow.ellipsis,
                  style: CumulusText.mono.copyWith(fontSize: 11.5),
                ),
              ),
              title: Text(c.name),
              subtitle: Text(
                'expect: ${c.expected}'
                '${c.runId.isEmpty ? '' : ' · from ${c.runId}'}',
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
              ),
              trailing: [
                StatusBadge(
                  c.source,
                  dense: true,
                  tone: c.source == 'promoted-from-run'
                      ? CumulusTone.accent
                      : CumulusTone.neutral,
                ),
                if (_isAdmin) ...[
                  const SizedBox(width: 6),
                  IconButton(
                    tooltip: 'Remove case',
                    icon: const Icon(Icons.close, size: 14),
                    onPressed: () async {
                      try {
                        final updated = await _api.removeGoldenCase(
                          widget.workflowId,
                          c.id,
                        );
                        if (!mounted) return;
                        setState(() => _golden = updated);
                      } on ApiException catch (e) {
                        _snack(e.message);
                      }
                    },
                  ),
                ],
              ],
            ),
      ],
    );
  }

  Future<void> _addGoldenCase() async {
    final name = TextEditingController();
    final expected = TextEditingController();
    final inputRef = TextEditingController();
    final payload = TextEditingController();
    var source = 'human-authored';
    final saved = await showDialog<bool>(
      context: context,
      builder: (context) => StatefulBuilder(
        builder: (context, setLocal) => AlertDialog(
          title: const Text('Add golden case'),
          content: SizedBox(
            width: 520,
            child: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  LabeledField(
                    label: 'Name',
                    child: TextField(
                      controller: name,
                      decoration: const InputDecoration(
                        hintText: 'e.g. Credit memo (negative amount)',
                      ),
                    ),
                  ),
                  const SizedBox(height: 14),
                  LabeledField(
                    label: 'Expected outcome',
                    child: TextField(
                      controller: expected,
                      decoration: const InputDecoration(
                        hintText: 'what a passing run must produce',
                      ),
                    ),
                  ),
                  const SizedBox(height: 14),
                  LabeledField(
                    label: 'Input document',
                    hint: 'a knowledge-layer asset id, kept in the map',
                    child: TextField(
                      controller: inputRef,
                      decoration: const InputDecoration(
                        hintText: 'e.g. invoice-fixture-042',
                      ),
                    ),
                  ),
                  const SizedBox(height: 14),
                  LabeledField(
                    label: 'JSON payload',
                    hint: 'optional — stored verbatim',
                    child: TextField(
                      controller: payload,
                      maxLines: 5,
                      style: CumulusText.mono.copyWith(fontSize: 11.5),
                      decoration: const InputDecoration(
                        hintText: '{"amount_usd": 2340, "vendor": "acme"}',
                      ),
                    ),
                  ),
                  const SizedBox(height: 14),
                  LabeledField(
                    label: 'Source',
                    child: DropdownButtonFormField<String>(
                      initialValue: source,
                      items: const [
                        DropdownMenuItem(
                          value: 'human-authored',
                          child: Text('human-authored'),
                        ),
                        DropdownMenuItem(
                          value: 'correction-set',
                          child: Text('correction-set'),
                        ),
                        DropdownMenuItem(
                          value: 'exemplar',
                          child: Text('exemplar'),
                        ),
                      ],
                      onChanged: (v) =>
                          setLocal(() => source = v ?? 'human-authored'),
                    ),
                  ),
                ],
              ),
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(context).pop(false),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: () => Navigator.of(context).pop(true),
              child: const Text('Add case'),
            ),
          ],
        ),
      ),
    );
    if (saved != true || name.text.trim().isEmpty) return;
    try {
      final updated = await _api.addGoldenCase(
        widget.workflowId,
        name: name.text.trim(),
        expected: expected.text.trim(),
        inputRef: inputRef.text.trim(),
        payload: payload.text.trim(),
        source: source,
      );
      if (!mounted) return;
      setState(() => _golden = updated);
    } on ApiException catch (e) {
      _snack(e.message);
    }
  }

  Widget _harnessCard() {
    final cases = _golden?.cases.length ?? 0;
    final latest = _tests.firstOrNull;
    return SectionCard(
      title: 'Test harness',
      subtitle: 'candidate vs baseline over the golden set · writes suppressed',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: LabeledField(
                  label: 'Candidate',
                  child: _VersionField(
                    label: _draftVersion?.display ?? 'no open draft',
                    tone: _draftVersion == null
                        ? CumulusTone.neutral
                        : CumulusTone.accent,
                  ),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: LabeledField(
                  label: 'Baseline',
                  child: _VersionField(
                    label: '${_activeVersion?.display ?? '—'} · live',
                    tone: CumulusTone.neutral,
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 16),
          Row(
            children: [
              FilledButton(
                onPressed: (_isAdmin && !_launching) ? _launchTest : null,
                child: Text(_launching ? 'Launching…' : 'Run suite'),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Text(
                  '$cases cases graded against both versions · test '
                  'ModelCalls are audited like any other',
                  style: CumulusText.meta,
                ),
              ),
            ],
          ),
          if (latest != null) ...[
            const SizedBox(height: 18),
            const Divider(height: 1),
            const SizedBox(height: 14),
            _TestRunRow(run: latest, onOpen: () => _openTestRun(latest)),
          ],
          for (final run in _tests.skip(1).take(4)) ...[
            const SizedBox(height: 10),
            _TestRunRow(run: run, onOpen: () => _openTestRun(run)),
          ],
        ],
      ),
    );
  }

  Future<void> _openTestRun(TestRun run) async {
    await showSideDrawer<void>(
      context: context,
      width: 680,
      title: '${run.candidateVersion} vs ${run.baselineVersion}',
      titleBadges: [
        StatusBadge(
          '${run.passed}/${run.totalCases} passed',
          tone: run.passRate >= 0.9 ? CumulusTone.success : CumulusTone.warn,
          dense: true,
        ),
        if (run.regressions > 0)
          StatusBadge(
            '${run.regressions} regressed',
            tone: CumulusTone.danger,
            dense: true,
          ),
      ],
      subtitle:
          'baseline ${run.baselinePassed}/${run.totalCases} · '
          'cost ${run.costDeltaUsd >= 0 ? '+' : ''}'
          '\$${run.costDeltaUsd.toStringAsFixed(2)} · '
          'latency ${run.latencyDeltaMs >= 0 ? '+' : ''}${run.latencyDeltaMs}ms '
          'per case · writes suppressed',
      builder: (context) => _TestRunComparison(run: run),
    );
  }

  Widget _recommenderCard() {
    return SectionCard(
      title: 'Cost & performance',
      subtitle: 'derived from this definition\'s own execution telemetry',
      child: _recommendations.isEmpty
          ? const CumulusEmptyState(
              icon: Icons.savings_outlined,
              title: 'Nothing to suggest',
              message:
                  'No cost or coverage insight stands out from the last 30 '
                  'days of telemetry.',
            )
          : Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              spacing: 12,
              children: [
                for (final rec in _recommendations)
                  _RecommendationCard(
                    rec: rec,
                    canApply: _isAdmin,
                    applying: _applyingRecommendation == rec.id,
                    onApply: () => _applyRecommendation(rec),
                  ),
              ],
            ),
    );
  }

  Future<void> _applyRecommendation(Recommendation rec) async {
    setState(() => _applyingRecommendation = rec.id);
    try {
      final draft = await _api.applyRecommendation(widget.workflowId, rec.id);
      final versions = await _api.workflowVersions(widget.workflowId);
      final detail = await _api.getWorkflow(widget.workflowId);
      if (!mounted) return;
      setState(() {
        _versions = versions;
        _detail = detail;
        _selectedVersion = draft.version;
        _tab = 1;
      });
      await _loadCanvas();
      _snack('Staged in ${draft.display} — run the suite before promoting it');
    } on ApiException catch (e) {
      _snack(e.message);
    } finally {
      if (mounted) setState(() => _applyingRecommendation = null);
    }
  }
}

// ---------------------------------------------------------------------------
// Top-bar version selector
// ---------------------------------------------------------------------------

class _VersionSelector extends StatelessWidget {
  final List<WorkflowVersion> versions;
  final int? selected;
  final ValueChanged<int> onChanged;

  const _VersionSelector({
    required this.versions,
    required this.selected,
    required this.onChanged,
  });

  @override
  Widget build(BuildContext context) {
    if (versions.isEmpty) return const SizedBox.shrink();
    final current = versions.where((v) => v.version == selected).firstOrNull;
    return PopupMenuButton<int>(
      tooltip: 'Select version',
      offset: const Offset(0, 38),
      onSelected: onChanged,
      itemBuilder: (context) => [
        for (final v in versions)
          PopupMenuItem<int>(
            value: v.version,
            height: 46,
            child: Row(
              children: [
                SizedBox(
                  width: 92,
                  child: Text(v.display, style: CumulusText.mono),
                ),
                StatusBadge(
                  v.status,
                  dense: true,
                  uppercase: true,
                  tone: v.isActive
                      ? CumulusTone.success
                      : v.isDraft
                      ? CumulusTone.accent
                      : CumulusTone.neutral,
                ),
              ],
            ),
          ),
      ],
      child: Container(
        padding: const EdgeInsets.fromLTRB(13, 7, 9, 7),
        decoration: BoxDecoration(
          color: CumulusColors.surface,
          borderRadius: BorderRadius.circular(CumulusRadius.pill),
          border: Border.all(color: CumulusColors.borderStrong),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(
              current?.display ?? 'version',
              style: CumulusText.mono.copyWith(
                fontSize: 12,
                fontWeight: FontWeight.w600,
              ),
            ),
            if (current != null) ...[
              const SizedBox(width: 8),
              StatusBadge(
                current.status,
                dense: true,
                uppercase: true,
                tone: current.isActive
                    ? CumulusTone.success
                    : current.isDraft
                    ? CumulusTone.accent
                    : CumulusTone.neutral,
              ),
            ],
            const Icon(
              Icons.expand_more,
              size: 16,
              color: CumulusColors.inkMuted,
            ),
          ],
        ),
      ),
    );
  }
}

class _VersionField extends StatelessWidget {
  final String label;
  final CumulusTone tone;

  const _VersionField({required this.label, required this.tone});

  @override
  Widget build(BuildContext context) {
    return Container(
      height: 42,
      padding: const EdgeInsets.symmetric(horizontal: 14),
      alignment: Alignment.centerLeft,
      decoration: BoxDecoration(
        color: tone == CumulusTone.neutral
            ? CumulusColors.surfaceMuted
            : tone.background,
        borderRadius: BorderRadius.circular(CumulusRadius.control),
        border: Border.all(color: CumulusColors.border),
      ),
      child: Text(
        label,
        overflow: TextOverflow.ellipsis,
        style: CumulusText.mono.copyWith(
          fontSize: 12.5,
          color: tone == CumulusTone.neutral
              ? CumulusColors.inkSecondary
              : tone.foreground,
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Node inspector
// ---------------------------------------------------------------------------

class _NodeInspector extends StatelessWidget {
  final WorkflowStep step;
  final StepDiff? diff;

  const _NodeInspector({required this.step, required this.diff});

  @override
  Widget build(BuildContext context) {
    Widget row(String label, String value) => Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 132,
            child: Text(label.toUpperCase(), style: CumulusText.statLabel),
          ),
          Expanded(
            child: SelectableText(
              value,
              style: const TextStyle(
                fontSize: 12.5,
                color: CumulusColors.ink,
                height: 1.45,
              ),
            ),
          ),
        ],
      ),
    );

    // Config keys worth naming explicitly, in the order a reviewer reads
    // them; anything else the definition carries is listed after.
    const known = {
      'model': 'Model',
      'prompt': 'Prompt template',
      'temperature': 'Temperature',
      'max_tokens': 'Max tokens',
      'fallback': 'Fallback',
      'cache': 'Prompt cache',
      'rule_type': 'Rule type',
      'threshold': 'Threshold',
      'queue': 'Queue',
      'target': 'Target',
      'event': 'Event',
      'source': 'Source',
      'language': 'Implementation',
      'transport': 'Transport',
      'window': 'Window',
      'gateway': 'Gateway',
    };

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        row('Step id', step.id),
        for (final entry in known.entries)
          if (step.config[entry.key] != null)
            row(entry.value, step.config[entry.key]!),
        for (final entry in step.config.entries)
          if (!known.containsKey(entry.key)) row(entry.key, entry.value),
        if (step.next.isNotEmpty)
          row(
            'Output paths',
            [
              for (var i = 0; i < step.next.length; i++)
                i < step.branchLabels.length
                    ? '${step.branchLabels[i]} → ${step.next[i]}'
                    : '→ ${step.next[i]}',
            ].join('\n'),
          ),
        if (diff != null && diff!.changedFields.isNotEmpty)
          row('Changed in draft', diff!.changedFields.join(', ')),
        const SizedBox(height: 8),
        Container(
          padding: const EdgeInsets.all(13),
          decoration: BoxDecoration(
            color: CumulusColors.surfaceMuted,
            borderRadius: BorderRadius.circular(CumulusRadius.control),
          ),
          child: Text(
            switch (step.type) {
              'modelcall' =>
                'A ModelCall is a governed operation: which model ran, its '
                    'inputs, and its captured output are recorded for audit '
                    'and cost control.',
              'action' =>
                'An Action is the only path to a decision write. The gateway '
                    'checks schema, permission and status before committing, '
                    'then records to the ledger.',
              'task' =>
                'A Task suspends the run in place — a stored record that '
                    'costs nothing while it waits — and the same run resumes '
                    'when a human decides.',
              'rule' =>
                'A Rule is a pure check: it evaluates inputs handed to it and '
                    'returns a verdict. It never fetches, acts, or waits.',
              'trigger' =>
                'A Trigger is a binding — when this Event occurs, start this '
                    'response. The Dispatcher evaluates it before any run '
                    'exists.',
              'event' =>
                'An Event is the immutable fact that something happened; '
                    'other definitions bind to it through their own Triggers.',
              _ =>
                'Authored non-model logic. Its meaning is exported in plain '
                    'language; the implementation stays machinery.',
            },
            style: CumulusText.meta,
          ),
        ),
      ],
    );
  }
}

// ---------------------------------------------------------------------------
// Run audit trail (inside the drawer)
// ---------------------------------------------------------------------------

class _RunAuditTrail extends StatelessWidget {
  final RunDetail run;

  const _RunAuditTrail({required this.run});

  @override
  Widget build(BuildContext context) {
    final callsByStep = {for (final c in run.calls) c.stepName: c};
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (run.triggerEvent.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(bottom: 14),
            child: Text(
              'Triggered by ${run.triggerEvent}',
              style: CumulusText.meta,
            ),
          ),
        for (var i = 0; i < run.steps.length; i++)
          _JournalEntry(
            event: run.steps[i],
            call: callsByStep[run.steps[i].name],
            isLast: i == run.steps.length - 1,
          ),
      ],
    );
  }
}

class _JournalEntry extends StatefulWidget {
  final RunStepEvent event;
  final ModelCallRecord? call;
  final bool isLast;

  const _JournalEntry({
    required this.event,
    required this.call,
    required this.isLast,
  });

  @override
  State<_JournalEntry> createState() => _JournalEntryState();
}

class _JournalEntryState extends State<_JournalEntry> {
  bool _open = false;

  @override
  Widget build(BuildContext context) {
    final event = widget.event;
    final tone = event.status == 'failed'
        ? CumulusTone.danger
        : event.status == 'skipped'
        ? CumulusTone.neutral
        : stepTone(event.type);
    final tag = switch (event.type) {
      'trigger' || 'event' => 'EVT',
      'modelcall' => 'MC',
      'rule' => 'RL',
      'task' => 'TSK',
      'action' => 'ACT',
      _ => 'TRN',
    };
    final dim = event.status == 'skipped';
    final hasPayloads =
        event.inputPreview.isNotEmpty || event.outputPreview.isNotEmpty;

    return IntrinsicHeight(
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          SizedBox(
            width: 34,
            child: Column(
              children: [
                Container(
                  width: 30,
                  height: 30,
                  alignment: Alignment.center,
                  decoration: BoxDecoration(
                    color: tone.background,
                    shape: BoxShape.circle,
                  ),
                  child: Text(
                    tag,
                    style: TextStyle(
                      fontSize: 8.5,
                      fontWeight: FontWeight.w700,
                      letterSpacing: 0.4,
                      color: tone.foreground,
                    ),
                  ),
                ),
                if (!widget.isLast)
                  Expanded(
                    child: Container(
                      width: 1,
                      margin: const EdgeInsets.symmetric(vertical: 4),
                      color: CumulusColors.border,
                    ),
                  ),
              ],
            ),
          ),
          const SizedBox(width: 14),
          Expanded(
            child: Padding(
              padding: const EdgeInsets.only(bottom: 16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Wrap(
                    crossAxisAlignment: WrapCrossAlignment.center,
                    spacing: 8,
                    runSpacing: 4,
                    children: [
                      Text(
                        '${switch (event.type) {
                          'trigger' => 'Event received',
                          'event' => 'Event emitted',
                          'modelcall' => 'ModelCall',
                          'rule' => 'Rule',
                          'task' => 'Task',
                          'action' => 'Action',
                          _ => 'Transform',
                        }} — ${event.name}',
                        style: TextStyle(
                          fontSize: 12.5,
                          fontWeight: FontWeight.w600,
                          color: dim ? CumulusColors.inkMuted : CumulusColors.ink,
                        ),
                      ),
                      if (event.verdict.isNotEmpty)
                        StatusBadge(
                          'verdict: ${event.verdict}',
                          tone: event.verdict == 'pass'
                              ? CumulusTone.success
                              : CumulusTone.warn,
                          dense: true,
                          uppercase: true,
                        ),
                      if (event.status == 'waiting')
                        const StatusBadge(
                          'WAITING',
                          tone: CumulusTone.accent,
                          dense: true,
                        ),
                      if (event.status == 'failed')
                        const StatusBadge(
                          'FAILED',
                          tone: CumulusTone.danger,
                          dense: true,
                        ),
                      if (event.ledgerRef.isNotEmpty)
                        Text(
                          'ledger #${event.ledgerRef}',
                          style: CumulusText.mono.copyWith(fontSize: 10.5),
                        ),
                    ],
                  ),
                  if (event.detail.isNotEmpty) ...[
                    const SizedBox(height: 3),
                    Text(
                      event.detail,
                      style: TextStyle(
                        fontSize: 11.5,
                        height: 1.5,
                        color: dim
                            ? CumulusColors.inkMuted
                            : CumulusColors.inkSecondary,
                      ),
                    ),
                  ],
                  if (event.decidedBy.isNotEmpty)
                    Text(
                      'decided by ${event.decidedBy}'
                      '${event.suspendedMinutes > 0 ? ' after ${event.suspendedMinutes}m' : ''}'
                      ' · in the curation ledger',
                      style: CumulusText.meta,
                    ),
                  if (hasPayloads) ...[
                    const SizedBox(height: 6),
                    InkWell(
                      onTap: () => setState(() => _open = !_open),
                      borderRadius: BorderRadius.circular(6),
                      child: Row(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Icon(
                            _open ? Icons.expand_less : Icons.expand_more,
                            size: 15,
                            color: CumulusColors.inkSecondary,
                          ),
                          const SizedBox(width: 4),
                          Text(
                            _open ? 'Hide payloads' : 'Inputs & outputs',
                            style: const TextStyle(
                              fontSize: 11.5,
                              fontWeight: FontWeight.w600,
                              color: CumulusColors.inkSecondary,
                            ),
                          ),
                        ],
                      ),
                    ),
                    if (_open) ...[
                      const SizedBox(height: 8),
                      if (widget.call != null)
                        Padding(
                          padding: const EdgeInsets.only(bottom: 10),
                          child: Wrap(
                            spacing: 8,
                            runSpacing: 6,
                            children: [
                              StatusBadge(widget.call!.model, dense: true),
                              StatusBadge(
                                '${widget.call!.inputTokens} in / '
                                '${widget.call!.outputTokens} out',
                                dense: true,
                              ),
                              StatusBadge(
                                '\$${widget.call!.costUsd.toStringAsFixed(3)}',
                                dense: true,
                              ),
                              if (widget.call!.promptVersion.isNotEmpty)
                                StatusBadge(
                                  widget.call!.promptVersion,
                                  dense: true,
                                ),
                            ],
                          ),
                        ),
                      PayloadBlock(
                        label: widget.call == null ? 'Input' : 'Prompt',
                        content: event.inputPreview,
                      ),
                      PayloadBlock(
                        label: widget.call == null ? 'Output' : 'Raw response',
                        content: widget.call?.rawResponse.isNotEmpty == true
                            ? widget.call!.rawResponse
                            : event.outputPreview,
                      ),
                    ],
                  ],
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Test runs
// ---------------------------------------------------------------------------

class _TestRunRow extends StatelessWidget {
  final TestRun run;
  final VoidCallback onOpen;

  const _TestRunRow({required this.run, required this.onOpen});

  @override
  Widget build(BuildContext context) {
    final delta = run.costDeltaUsd;
    return InkWell(
      onTap: run.running ? null : onOpen,
      borderRadius: BorderRadius.circular(CumulusRadius.control),
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 6, horizontal: 4),
        child: Row(
          children: [
            SizedBox(
              width: 108,
              child: Text(
                run.candidateVersion,
                overflow: TextOverflow.ellipsis,
                style: CumulusText.mono.copyWith(fontSize: 11.5),
              ),
            ),
            Expanded(
              child: ClipRRect(
                borderRadius: BorderRadius.circular(99),
                child: LinearProgressIndicator(
                  minHeight: 8,
                  value: run.running
                      ? (run.totalCases == 0
                            ? 0
                            : run.results.length / run.totalCases)
                      : run.passRate,
                  color: run.running
                      ? CumulusColors.accent
                      : run.regressions > 0
                      ? CumulusColors.warn
                      : CumulusColors.success,
                  backgroundColor: CumulusColors.surfaceMuted,
                ),
              ),
            ),
            const SizedBox(width: 12),
            SizedBox(
              width: 86,
              child: Text(
                run.running
                    ? '${run.results.length}/${run.totalCases}…'
                    : '${run.passed}/${run.totalCases} passed',
                textAlign: TextAlign.right,
                style: const TextStyle(
                  fontSize: 12,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ),
            const SizedBox(width: 12),
            SizedBox(
              width: 74,
              child: Text(
                run.running
                    ? ''
                    : '${delta >= 0 ? '+' : '−'}\$${delta.abs().toStringAsFixed(2)}',
                textAlign: TextAlign.right,
                style: TextStyle(
                  fontSize: 12,
                  fontWeight: FontWeight.w600,
                  color: delta < 0
                      ? CumulusColors.success
                      : delta > 0
                      ? CumulusColors.warn
                      : CumulusColors.inkMuted,
                ),
              ),
            ),
            const SizedBox(width: 10),
            SizedBox(
              width: 74,
              child: Text(
                run.running ? '' : agoLabel(run.launchedAt),
                textAlign: TextAlign.right,
                style: CumulusText.meta,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _TestRunComparison extends StatelessWidget {
  final TestRun run;

  const _TestRunComparison({required this.run});

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          children: [
            Expanded(
              child: _DeltaTile(
                label: 'Pass rate',
                candidate: '${(run.passRate * 100).toStringAsFixed(1)}%',
                baseline: '${(run.baselinePassRate * 100).toStringAsFixed(1)}%',
                good: run.passRate >= run.baselinePassRate,
              ),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: _DeltaTile(
                label: 'Cost',
                candidate: '\$${run.costUsd.toStringAsFixed(2)}',
                baseline: '\$${run.baselineCostUsd.toStringAsFixed(2)}',
                good: run.costDeltaUsd <= 0,
              ),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: _DeltaTile(
                label: 'Latency / case',
                candidate:
                    '${run.latencyDeltaMs >= 0 ? '+' : ''}${run.latencyDeltaMs}ms',
                baseline: 'vs baseline',
                good: run.latencyDeltaMs <= 0,
              ),
            ),
          ],
        ),
        const SizedBox(height: 18),
        for (final r in run.results)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 9),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                SizedBox(
                  width: 108,
                  child: Wrap(
                    spacing: 4,
                    runSpacing: 4,
                    children: [
                      StatusBadge(
                        r.passed ? 'PASS' : 'FAIL',
                        tone: r.passed ? CumulusTone.success : CumulusTone.danger,
                        dense: true,
                      ),
                      if (r.regressed)
                        const StatusBadge(
                          'REGRESSED',
                          tone: CumulusTone.danger,
                          dense: true,
                        ),
                      if (r.improved)
                        const StatusBadge(
                          'FIXED',
                          tone: CumulusTone.success,
                          dense: true,
                        ),
                    ],
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        r.name,
                        style: const TextStyle(
                          fontSize: 12.5,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                      const SizedBox(height: 2),
                      Text(
                        r.passed
                            ? 'expect: ${r.expected}'
                            : 'expect: ${r.expected}\nactual: ${r.actual}',
                        style: CumulusText.meta,
                      ),
                    ],
                  ),
                ),
                const SizedBox(width: 12),
                SizedBox(
                  width: 96,
                  child: Text(
                    '\$${r.costUsd.toStringAsFixed(3)}\n'
                    'was \$${r.baselineCostUsd.toStringAsFixed(3)}',
                    textAlign: TextAlign.right,
                    style: CumulusText.meta,
                  ),
                ),
              ],
            ),
          ),
      ],
    );
  }
}

class _DeltaTile extends StatelessWidget {
  final String label;
  final String candidate;
  final String baseline;
  final bool good;

  const _DeltaTile({
    required this.label,
    required this.candidate,
    required this.baseline,
    required this.good,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: CumulusColors.surfaceMuted,
        borderRadius: BorderRadius.circular(CumulusRadius.control),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label.toUpperCase(), style: CumulusText.statLabel),
          const SizedBox(height: 8),
          Text(
            candidate,
            style: TextStyle(
              fontSize: 18,
              fontWeight: FontWeight.w600,
              letterSpacing: -0.3,
              color: good ? CumulusColors.success : CumulusColors.warn,
            ),
          ),
          const SizedBox(height: 4),
          Text(baseline, style: CumulusText.meta),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Recommendation card
// ---------------------------------------------------------------------------

class _RecommendationCard extends StatelessWidget {
  final Recommendation rec;
  final bool canApply;
  final bool applying;
  final VoidCallback onApply;

  const _RecommendationCard({
    required this.rec,
    required this.canApply,
    required this.applying,
    required this.onApply,
  });

  @override
  Widget build(BuildContext context) {
    final tone = recommendationTone(rec.kind);
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: CumulusColors.surface,
        borderRadius: BorderRadius.circular(CumulusRadius.control),
        border: Border.all(color: CumulusColors.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              StatusBadge(
                rec.kind.replaceAll('-', ' '),
                tone: tone,
                dense: true,
                uppercase: true,
              ),
              const Spacer(),
              if (rec.estimatedMonthlySavingsUsd > 0)
                Text(
                  '≈${money(rec.estimatedMonthlySavingsUsd)}/mo',
                  style: const TextStyle(
                    fontSize: 13,
                    fontWeight: FontWeight.w700,
                    color: CumulusColors.success,
                  ),
                ),
            ],
          ),
          const SizedBox(height: 10),
          Text(
            rec.title,
            style: const TextStyle(
              fontSize: 13.5,
              fontWeight: FontWeight.w600,
              color: CumulusColors.ink,
            ),
          ),
          const SizedBox(height: 5),
          Text(rec.detail, style: CumulusText.body),
          const SizedBox(height: 10),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
            decoration: BoxDecoration(
              color: CumulusColors.surfaceMuted,
              borderRadius: BorderRadius.circular(8),
            ),
            child: Row(
              children: [
                const Icon(
                  Icons.insights_outlined,
                  size: 14,
                  color: CumulusColors.inkMuted,
                ),
                const SizedBox(width: 8),
                Expanded(child: Text(rec.evidence, style: CumulusText.meta)),
              ],
            ),
          ),
          const SizedBox(height: 12),
          Row(
            children: [
              if (rec.applicable)
                FilledButton(
                  onPressed: (canApply && !applying) ? onApply : null,
                  child: Text(
                    applying ? 'Applying…' : 'Apply to draft version',
                  ),
                )
              else
                Text(
                  'Advisory — no automatic change',
                  style: CumulusText.meta.copyWith(
                    fontStyle: FontStyle.italic,
                  ),
                ),
              const Spacer(),
              Text(
                'confidence ${(rec.confidence * 100).round()}%',
                style: CumulusText.meta,
              ),
            ],
          ),
        ],
      ),
    );
  }
}
