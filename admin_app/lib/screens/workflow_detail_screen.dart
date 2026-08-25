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

/// The measures the overview chart can show. One chart, one axis — switching
/// measure is a choice, never two scales stacked on one plot.
enum ChartMeasure {
  runs('Runs / day'),
  spend('Model spend / day'),
  tokens('Tokens / day'),
  judgment('Human judgment / day');

  final String label;

  const ChartMeasure(this.label);
}

/// One definition, in three views: what it is and how it is doing, the graph
/// with its runs, and how a candidate version measures up.
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

  WorkflowDetail? _detail;
  WorkflowUsage? _usage;
  List<RunSummary> _runs = [];
  GoldenSetInfo? _golden;
  List<TestRun> _tests = [];
  List<Recommendation> _recommendations = [];
  List<WorkflowVersion> _versions = [];

  int? _selectedVersion;
  List<StepDiff> _canvasSteps = [];
  String? _selectedStepId;

  /// The run being traced through the graph, and its journal.
  RunSummary? _selectedRun;
  RunDetail? _runDetail;
  bool _loadingRun = false;

  ChartMeasure _measure = ChartMeasure.runs;

  /// 0 = step definition, 1 = runs and their step payloads.
  int _rightTab = 0;

  bool _loading = true;
  String? _error;
  int _tab = 0;
  bool _launching = false;
  String? _applyingRecommendation;
  Timer? _poll;

  bool get _isAdmin => widget.myRole == 'admin' || widget.myRole == 'owner';

  WorkflowVersion? get _current =>
      _versions.where((v) => v.version == _selectedVersion).firstOrNull;

  WorkflowVersion? get _activeVersion =>
      _versions.where((v) => v.isActive).firstOrNull;

  WorkflowVersion? get _draftVersion =>
      _versions.where((v) => v.isDraft).firstOrNull;

  WorkflowStep? get _selectedStep =>
      _canvasSteps.where((d) => d.step.id == _selectedStepId).firstOrNull?.step;

  RunPath? get _runPath => _runDetail == null
      ? null
      : RunPath.fromRun(_runDetail!, [for (final d in _canvasSteps) d.step]);

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  @override
  void dispose() {
    _poll?.cancel();
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
        _api.workflowRuns(id, limit: 40),
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
      if (!mounted) return;
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

  /// Tracing a run means looking at the version that ran, so selecting one
  /// moves the canvas off any draft it was showing.
  Future<void> _selectRun(RunSummary? run) async {
    if (run == null) {
      setState(() {
        _selectedRun = null;
        _runDetail = null;
      });
      return;
    }
    setState(() {
      _selectedRun = run;
      _loadingRun = true;
    });
    final active = _activeVersion;
    if (active != null && _selectedVersion != active.version) {
      setState(() => _selectedVersion = active.version);
      await _loadCanvas();
    }
    try {
      final detail = await _api.workflowRun(widget.workflowId, run.id);
      if (!mounted) return;
      setState(() {
        _runDetail = detail;
        _loadingRun = false;
      });
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() => _loadingRun = false);
      _snack(e.message);
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
    // The canvas tab is a workbench, not a document: its body runs edge to
    // edge and fills the viewport, while the other two stay in the measured
    // reading column the rest of the console uses.
    final fullBleed = _tab == 1;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: EdgeInsets.fromLTRB(fullBleed ? 24 : 14, 12, 28, 0),
          child: Center(
            child: ConstrainedBox(
              constraints: BoxConstraints(
                maxWidth: fullBleed ? double.infinity : 1420,
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  BackLink(label: 'All workflows', onTap: widget.onBack),
                  _topBar(w),
                  const SizedBox(height: 16),
                  Align(
                    alignment: Alignment.centerLeft,
                    child: SegmentedTabs(
                      labels: const [
                        'Overview',
                        'Workflow canvas',
                        'Evaluation & optimization',
                      ],
                      selectedIndex: _tab,
                      onChanged: (i) => setState(() => _tab = i),
                    ),
                  ),
                  SizedBox(height: fullBleed ? 14 : 0),
                ],
              ),
            ),
          ),
        ),
        Expanded(
          child: switch (_tab) {
            0 => PageBody(
              padding: const EdgeInsets.fromLTRB(14, 16, 28, 48),
              children: _overviewBody(w),
            ),
            1 => _canvasBody(),
            _ => PageBody(
              padding: const EdgeInsets.fromLTRB(14, 16, 28, 48),
              children: _evaluationBody(),
            ),
          },
        ),
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
              // A version and a run are two different things to look at; the
              // run's path belongs to the version that ran.
              _selectedRun = null;
              _runDetail = null;
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

  // ---- Tab 1: overview ----------------------------------------------------

  List<Widget> _overviewBody(WorkflowDetail w) {
    final usage = _usage!;
    return [
      StatStrip(
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
      ),
      LayoutBuilder(
        builder: (context, constraints) {
          final metadata = _metadataCard(w);
          final chart = _chartCard(usage);
          if (constraints.maxWidth < 1040) {
            return Column(
              spacing: 16,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [metadata, chart],
            );
          }
          return IntrinsicHeight(
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              spacing: 16,
              children: [
                SizedBox(width: 420, child: metadata),
                Expanded(child: chart),
              ],
            ),
          );
        },
      ),
    ];
  }

  Widget _chartCard(WorkflowUsage usage) {
    double valueOf(UsageDay d) => switch (_measure) {
      ChartMeasure.runs => d.runs.toDouble(),
      ChartMeasure.spend => d.costUsd,
      ChartMeasure.tokens => d.tokens.toDouble(),
      ChartMeasure.judgment => d.tasksRaised.toDouble(),
    };
    String format(double v) => switch (_measure) {
      ChartMeasure.spend => money(v),
      ChartMeasure.tokens => compactCount(v.round()),
      _ => v.round().toString(),
    };
    return SectionCard(
      title: 'Usage · ${usage.days} days',
      subtitle: 'hover any day for the full breakdown',
      headerTrailing: _MeasurePicker(
        value: _measure,
        onChanged: (m) => setState(() => _measure = m),
      ),
      child: DailyBarChart(
        title: _measure.label,
        showTitle: false,
        height: 260,
        format: format,
        points: [
          for (final d in usage.series)
            ChartPoint(
              dayLabel(d.date),
              valueOf(d),
              // Whichever measure is on screen, the tooltip carries the rest:
              // switching charts to answer "and what did that day cost?" is
              // the friction a single-measure plot would otherwise create.
              extra: [
                if (_measure != ChartMeasure.runs) '${d.runs} runs',
                if (_measure != ChartMeasure.spend)
                  '${money(d.costUsd)} model spend',
                if (_measure != ChartMeasure.tokens)
                  '${compactCount(d.tokens)} tokens',
                if (_measure != ChartMeasure.judgment)
                  '${d.tasksRaised} tasks raised',
              ],
            ),
        ],
      ),
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
      subtitle: 'Tier-2 map asset · transparent export',
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
          const Spacer(),
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

  // ---- Tab 2: canvas workbench -------------------------------------------

  Widget _canvasBody() {
    return Container(
      decoration: const BoxDecoration(
        border: Border(top: BorderSide(color: CumulusColors.border)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Expanded(
            child: WorkflowCanvas(
              steps: _canvasSteps,
              diffAgainst: (_current?.isDraft ?? false) && _activeVersion != null
                  ? '${_activeVersion!.display} (active)'
                  : '',
              runPath: _runPath,
              selectedStepId: _selectedStepId,
              onSelect: (step) => setState(() => _selectedStepId = step?.id),
              onExportSchema:
                  _current == null ? null : () => _exportSchema(_current!),
            ),
          ),
          const VerticalDivider(width: 1, color: CumulusColors.border),
          Expanded(child: _inspectorColumn()),
        ],
      ),
    );
  }

  Widget _inspectorColumn() {
    return ColoredBox(
      color: CumulusColors.surface,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
            child: Row(
              children: [
                SegmentedTabs(
                  labels: const ['Definition', 'Runs'],
                  selectedIndex: _rightTab,
                  onChanged: (i) => setState(() => _rightTab = i),
                ),
                const Spacer(),
                if (_rightTab == 1 && _runDetail != null && _isAdmin)
                  TextButton.icon(
                    onPressed: () => _promoteRun(_runDetail!),
                    icon: const Icon(Icons.playlist_add_check, size: 15),
                    label: const Text('Promote to golden set'),
                  ),
              ],
            ),
          ),
          const Divider(height: 1),
          Expanded(
            child: _rightTab == 0 ? _definitionPanel() : _runsPanel(),
          ),
        ],
      ),
    );
  }

  Widget _definitionPanel() {
    final step = _selectedStep;
    if (step == null) {
      return const CumulusEmptyState(
        icon: Icons.touch_app_outlined,
        title: 'Select a step',
        message:
            'Click any node on the canvas to see its exported configuration — '
            'the model it calls, the threshold it checks, where it routes.',
      );
    }
    final diff = _canvasSteps.where((d) => d.step.id == step.id).firstOrNull;
    return SingleChildScrollView(
      padding: const EdgeInsets.fromLTRB(20, 18, 20, 32),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _StepHeader(step: step, diff: diff),
          const SizedBox(height: 16),
          _NodeConfiguration(step: step, diff: diff),
        ],
      ),
    );
  }

  Widget _runsPanel() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        // Top half: which run to trace.
        Expanded(child: _runList()),
        const Divider(height: 1),
        // Bottom half: what the selected step did during that run.
        Expanded(child: _stepPayloadPanel()),
      ],
    );
  }

  Widget _runList() {
    if (_runs.isEmpty) {
      return const CumulusEmptyState(
        icon: Icons.play_circle_outline,
        title: 'No runs yet',
        message: 'This definition has not executed in the journal window.',
      );
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(20, 14, 20, 8),
          child: Row(
            children: [
              Text('RECENT RUNS', style: CumulusText.sectionTitle),
              const SizedBox(width: 10),
              Expanded(
                child: Text(
                  _selectedRun == null
                      ? 'select one to trace its path'
                      : 'tracing ${_selectedRun!.id}',
                  style: CumulusText.sectionSubtitle,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              if (_selectedRun != null)
                TextButton(
                  onPressed: () => _selectRun(null),
                  child: const Text('Clear'),
                ),
            ],
          ),
        ),
        Expanded(
          child: ListView.separated(
            padding: EdgeInsets.zero,
            itemCount: _runs.length,
            separatorBuilder: (_, _) => const Divider(height: 1),
            itemBuilder: (context, i) {
              final run = _runs[i];
              final selected = run.id == _selectedRun?.id;
              return Material(
                color: selected
                    ? CumulusColors.accentSoft
                    : Colors.transparent,
                child: InkWell(
                  onTap: () => _selectRun(run),
                  child: Padding(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 20,
                      vertical: 11,
                    ),
                    child: Row(
                      children: [
                        Container(
                          width: 8,
                          height: 8,
                          decoration: BoxDecoration(
                            color: runTone(run.status).dot,
                            shape: BoxShape.circle,
                          ),
                        ),
                        const SizedBox(width: 12),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Row(
                                children: [
                                  Text(
                                    run.id,
                                    style: CumulusText.mono.copyWith(
                                      fontSize: 12,
                                      fontWeight: selected
                                          ? FontWeight.w600
                                          : FontWeight.w400,
                                    ),
                                  ),
                                  const SizedBox(width: 8),
                                  if (run.status != 'completed')
                                    StatusBadge(
                                      runStatusLabel(run.status),
                                      tone: runTone(run.status),
                                      dense: true,
                                      uppercase: true,
                                    ),
                                ],
                              ),
                              const SizedBox(height: 2),
                              Text(
                                '${run.caseRef} · ${run.modelCalls} MC · '
                                '${run.actionsCommitted} act'
                                '${run.tasksRaised > 0 ? ' · task' : ''}',
                                style: CumulusText.meta,
                              ),
                            ],
                          ),
                        ),
                        Text(
                          '\$${run.costUsd.toStringAsFixed(2)}',
                          style: const TextStyle(
                            fontSize: 12,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                        const SizedBox(width: 12),
                        SizedBox(
                          width: 62,
                          child: Text(
                            agoLabel(run.startedAt),
                            textAlign: TextAlign.right,
                            style: CumulusText.meta,
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
              );
            },
          ),
        ),
      ],
    );
  }

  Widget _stepPayloadPanel() {
    if (_loadingRun) return const CumulusLoading();
    final run = _runDetail;
    if (run == null) {
      return const CumulusEmptyState(
        icon: Icons.route_outlined,
        title: 'No run selected',
        message:
            'Pick a run above to light up the path it took, then click a step '
            'on that path to see what went in and what came out.',
      );
    }
    final stepId = _selectedStepId;
    final event = stepId == null
        ? null
        : run.steps.where((e) => e.stepId == stepId).firstOrNull;
    if (event == null) {
      return CumulusEmptyState(
        icon: Icons.touch_app_outlined,
        title: stepId == null ? 'Select a step' : 'Not in this run',
        message: stepId == null
            ? 'Click a step on the canvas to see its inputs and outputs for '
                  '${run.id}.'
            : 'This step is not part of ${run.id} — the run took a different '
                  'branch.',
      );
    }
    final call = run.calls.where((c) => c.stepId == event.stepId).firstOrNull;
    return SingleChildScrollView(
      padding: const EdgeInsets.fromLTRB(20, 16, 20, 28),
      child: _StepRunDetail(event: event, call: call, runId: run.id),
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

  List<Widget> _evaluationBody() {
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
          if (_tests.isNotEmpty) ...[
            const SizedBox(height: 18),
            const Divider(height: 1),
            const SizedBox(height: 14),
            for (final run in _tests.take(5)) ...[
              _TestRunRow(run: run, onOpen: () => _openTestRun(run)),
              const SizedBox(height: 8),
            ],
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
        _selectedRun = null;
        _runDetail = null;
        _selectedStepId = rec.stepId.isEmpty ? null : rec.stepId;
        _rightTab = 0;
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
// Chart measure picker
// ---------------------------------------------------------------------------

class _MeasurePicker extends StatelessWidget {
  final ChartMeasure value;
  final ValueChanged<ChartMeasure> onChanged;

  const _MeasurePicker({required this.value, required this.onChanged});

  @override
  Widget build(BuildContext context) {
    return PopupMenuButton<ChartMeasure>(
      tooltip: 'Choose a measure',
      offset: const Offset(0, 36),
      onSelected: onChanged,
      itemBuilder: (context) => [
        for (final measure in ChartMeasure.values)
          PopupMenuItem<ChartMeasure>(
            value: measure,
            height: 40,
            child: Row(
              children: [
                Icon(
                  measure == value ? Icons.check : Icons.check,
                  size: 15,
                  color: measure == value
                      ? CumulusColors.accent
                      : Colors.transparent,
                ),
                const SizedBox(width: 10),
                Text(measure.label, style: const TextStyle(fontSize: 13)),
              ],
            ),
          ),
      ],
      child: Container(
        padding: const EdgeInsets.fromLTRB(13, 8, 9, 8),
        decoration: BoxDecoration(
          color: CumulusColors.surface,
          borderRadius: BorderRadius.circular(CumulusRadius.control),
          border: Border.all(color: CumulusColors.borderStrong),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(
              value.label,
              style: const TextStyle(
                fontSize: 12.5,
                fontWeight: FontWeight.w600,
                color: CumulusColors.ink,
              ),
            ),
            const SizedBox(width: 6),
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
// Step inspector (right column, Definition tab)
// ---------------------------------------------------------------------------

class _StepHeader extends StatelessWidget {
  final WorkflowStep step;
  final StepDiff? diff;

  const _StepHeader({required this.step, required this.diff});

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Wrap(
          crossAxisAlignment: WrapCrossAlignment.center,
          spacing: 8,
          runSpacing: 6,
          children: [
            Text(
              step.name,
              style: CumulusText.mono.copyWith(
                fontSize: 15,
                fontWeight: FontWeight.w600,
              ),
            ),
            StatusBadge(
              step.type,
              tone: stepTone(step.type),
              dense: true,
              uppercase: true,
            ),
            if (governedBadge(step.type) != null)
              StatusBadge(
                governedBadge(step.type)!,
                tone: stepTone(step.type),
                dense: true,
              ),
            if (diff != null && diff!.change != 'unchanged')
              StatusBadge(
                diff!.change,
                tone: switch (diff!.change) {
                  'added' => CumulusTone.success,
                  'removed' => CumulusTone.danger,
                  _ => CumulusTone.warn,
                },
                dense: true,
                uppercase: true,
              ),
          ],
        ),
        if (step.summary.isNotEmpty) ...[
          const SizedBox(height: 8),
          Text(step.summary, style: CumulusText.body),
        ],
      ],
    );
  }
}

class _NodeConfiguration extends StatelessWidget {
  final WorkflowStep step;
  final StepDiff? diff;

  const _NodeConfiguration({required this.step, required this.diff});

  // Config keys worth naming explicitly, in the order a reviewer reads them;
  // anything else the definition carries is listed after.
  static const _known = {
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

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('CONFIGURATION', style: CumulusText.sectionTitle),
        const SizedBox(height: 12),
        row('Step id', step.id),
        for (final entry in _known.entries)
          if (step.config[entry.key] != null)
            row(entry.value, step.config[entry.key]!),
        for (final entry in step.config.entries)
          if (!_known.containsKey(entry.key)) row(entry.key, entry.value),
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
        const SizedBox(height: 6),
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
// Step payloads for one run (right column, Runs tab, lower half)
// ---------------------------------------------------------------------------

class _StepRunDetail extends StatelessWidget {
  final RunStepEvent event;
  final ModelCallRecord? call;
  final String runId;

  const _StepRunDetail({
    required this.event,
    required this.call,
    required this.runId,
  });

  @override
  Widget build(BuildContext context) {
    final tone = event.status == 'failed'
        ? CumulusTone.danger
        : event.status == 'skipped'
        ? CumulusTone.neutral
        : stepTone(event.type);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Wrap(
          crossAxisAlignment: WrapCrossAlignment.center,
          spacing: 8,
          runSpacing: 6,
          children: [
            Text(
              event.name,
              style: CumulusText.mono.copyWith(
                fontSize: 13.5,
                fontWeight: FontWeight.w600,
              ),
            ),
            StatusBadge(event.type, tone: tone, dense: true, uppercase: true),
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
        const SizedBox(height: 6),
        Text('in $runId · ${event.detail}', style: CumulusText.meta),
        if (event.decidedBy.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 4),
            child: Text(
              'decided by ${event.decidedBy}'
              '${event.suspendedMinutes > 0 ? ' after ${event.suspendedMinutes}m' : ''}'
              ' · in the curation ledger',
              style: CumulusText.meta,
            ),
          ),
        if (call != null) ...[
          const SizedBox(height: 12),
          Wrap(
            spacing: 8,
            runSpacing: 6,
            children: [
              StatusBadge(call!.model, dense: true, tone: CumulusTone.violet),
              StatusBadge(
                '${call!.inputTokens} in / ${call!.outputTokens} out',
                dense: true,
              ),
              StatusBadge(
                '\$${call!.costUsd.toStringAsFixed(3)}',
                dense: true,
              ),
              if (call!.promptVersion.isNotEmpty)
                StatusBadge(call!.promptVersion, dense: true),
              StatusBadge('${call!.durationMs}ms', dense: true),
            ],
          ),
        ],
        const SizedBox(height: 14),
        PayloadBlock(
          label: call == null ? 'Input' : 'Prompt',
          content: event.inputPreview,
          maxLines: 10,
        ),
        PayloadBlock(
          label: call == null ? 'Output' : 'Raw response',
          content: call?.rawResponse.isNotEmpty == true
              ? call!.rawResponse
              : event.outputPreview,
          maxLines: 10,
        ),
      ],
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
                  style: CumulusText.meta.copyWith(fontStyle: FontStyle.italic),
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
