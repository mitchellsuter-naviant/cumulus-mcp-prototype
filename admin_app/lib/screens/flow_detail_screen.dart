import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../models/flow.dart';
import '../models/run.dart';
import '../services/api_client.dart';
import '../theme/cumulus_theme.dart';
import '../widgets/cumulus_shell.dart';
import '../widgets/eval_panel.dart';
import '../widgets/mini_charts.dart';
import '../widgets/run_workbench.dart' show runTone;
import '../widgets/side_drawer.dart';
import '../widgets/ui_kit.dart';
import '../widgets/flow_canvas.dart';
import 'flows_screen.dart' show dayLabel, money, percent, statusTone;

/// What a run row names itself after. A run started without a case reference
/// keeps the engine's default — its own id — and printing that beside the id
/// says nothing, so the actor stands in.
String _runSubject(FlowRunSummary run) =>
    run.caseRef == run.id ? run.actor : run.caseRef;

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
class FlowDetailScreen extends StatefulWidget {
  final String flowId;
  final String myRole;
  final VoidCallback onBack;

  const FlowDetailScreen({
    super.key,
    required this.flowId,
    required this.myRole,
    required this.onBack,
  });

  @override
  State<FlowDetailScreen> createState() => _FlowDetailScreenState();
}

class _FlowDetailScreenState extends State<FlowDetailScreen> {
  final ApiClient _api = ApiClient();

  FlowDetail? _detail;
  FlowUsage? _usage;
  List<FlowRunSummary> _runs = [];
  List<Recommendation> _recommendations = [];
  List<FlowVersion> _versions = [];

  int? _selectedVersion;
  List<StepDiff> _canvasSteps = [];
  String? _selectedStepId;

  /// The run being traced through the graph, and its journal.
  FlowRunSummary? _selectedRun;
  FlowRun? _runDetail;
  bool _loadingRun = false;

  ChartMeasure _measure = ChartMeasure.runs;

  /// 0 = step definition, 1 = runs and their payloads.
  int _rightTab = 0;

  bool _loading = true;
  String? _error;
  int _tab = 0;
  String? _applyingRecommendation;

  bool get _isAdmin => widget.myRole == 'admin' || widget.myRole == 'owner';

  FlowVersion? get _current =>
      _versions.where((v) => v.version == _selectedVersion).firstOrNull;

  FlowVersion? get _activeVersion =>
      _versions.where((v) => v.isActive).firstOrNull;

  FlowStep? get _selectedStep =>
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
    _api.close();
    super.dispose();
  }

  Future<void> _refresh() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final id = widget.flowId;
      final results = await Future.wait([
        _api.getFlow(id),
        _api.flowUsage(id),
        _api.listRuns(flowId: id, limit: 40),
        _api.flowVersions(id),
        _api.flowRecommendations(id),
      ]);
      if (!mounted) return;
      final versions = results[3] as List<FlowVersion>;
      setState(() {
        _detail = results[0] as FlowDetail;
        _usage = results[1] as FlowUsage;
        _runs = results[2] as List<FlowRunSummary>;
        _versions = versions;
        _recommendations = results[4] as List<Recommendation>;
        _selectedVersion ??= (versions.where((v) => v.isDraft).firstOrNull ??
                versions.where((v) => v.isActive).firstOrNull)
            ?.version;
        _loading = false;
      });
      await _loadCanvas();
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
      final diff = await _api.flowVersionDiff(
        widget.flowId,
        version.version,
      );
      if (!mounted) return;
      setState(() => _canvasSteps = diff);
    } on ApiException catch (e) {
      _snack(e.message);
    }
  }

  void _snack(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(message)));
  }

  Future<void> _setStatus(String status) async {
    try {
      final updated = await _api.updateFlow(widget.flowId, status: status);
      if (!mounted) return;
      setState(() => _detail = updated);
      _snack('Status set to $status');
    } on ApiException catch (e) {
      _snack(e.message);
    }
  }

  /// Tracing a run means looking at the version that ran, so selecting one
  /// moves the canvas off any draft it was showing.
  Future<void> _selectRun(FlowRunSummary? run) async {
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
      final detail = await _api.getRun(run.id);
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
          BackLink(label: 'All flows', onTap: widget.onBack),
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
                  BackLink(label: 'All flows', onTap: widget.onBack),
                  _topBar(w),
                  const SizedBox(height: 16),
                  Align(
                    alignment: Alignment.centerLeft,
                    child: SegmentedTabs(
                      labels: const [
                        'Overview',
                        'Flow canvas',
                        'Evaluation',
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
              children: [EvalPanel(api: _api, flowId: widget.flowId)],
            ),
          },
        ),
      ],
    );
  }

  // ---- top bar ------------------------------------------------------------

  Widget _topBar(FlowDetail w) {
    return PageHeading(
      eyebrow: 'Flow · ${w.team.isEmpty ? 'unassigned' : w.team}',
      title: w.id,
      subtitle: w.description,
      pills: [
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
      // No run button here. Running the eval belongs beside the cases it
      // grades, on the Evaluation tab; a button up here could only navigate.
      trailing: _isAdmin
          ? (w.status == 'active'
                ? OutlinedButton(
                    onPressed: () => _setStatus('paused'),
                    child: const Text('Pause'),
                  )
                : w.status != 'deprecated'
                ? OutlinedButton(
                    onPressed: () => _setStatus('active'),
                    child: const Text('Activate'),
                  )
                : null)
          : null,
    );
  }

  // ---- Tab 1: overview ----------------------------------------------------

  List<Widget> _overviewBody(FlowDetail w) {
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
      // Cost work reads the same telemetry the strip and the chart above it
      // plot, so it belongs here rather than beside the evals.
      _recommenderCard(),
    ];
  }

  Widget _chartCard(FlowUsage usage) {
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

  Widget _metadataCard(FlowDetail w) {
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
          if (trigger != null) ...[
            row('Trigger', trigger.config['event'] ?? trigger.name),
            row(
              'Cadence',
              (trigger.config['event'] ?? '').startsWith('timer:')
                  ? 'Windowed — one run per window, landing work in bulk'
                  : 'Per case — one run per event',
            ),
          ],
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

  Future<void> _editRoi(FlowDetail w) async {
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
      final updated = await _api.updateFlow(
        widget.flowId,
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
            child: FlowCanvas(
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
            child: Align(
              alignment: Alignment.centerLeft,
              child: SegmentedTabs(
                labels: const ['Definition', 'Runs'],
                selectedIndex: _rightTab,
                onChanged: (i) => setState(() {
                  _rightTab = i;
                  // Leaving Runs drops the trace with it. The run highlight
                  // belongs to the Runs tab; leaving it on while reading the
                  // definition claims the graph is showing one execution when
                  // nothing on screen says which.
                  if (i != 1) {
                    _selectedRun = null;
                    _runDetail = null;
                  }
                }),
              ),
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

  /// Two modes, not two panes. Browsing runs wants the full column; once one
  /// is chosen the list collapses to a compact header with a way back, and
  /// the payloads — the reason you picked the run — get the rest of the space.
  Widget _runsPanel() {
    if (_selectedRun == null) return _runList();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _selectedRunHeader(),
        const Divider(height: 1),
        Expanded(child: _stepPayloadPanel()),
      ],
    );
  }

  Widget _selectedRunHeader() {
    final run = _selectedRun!;
    return Container(
      color: CumulusColors.surfaceMuted.withValues(alpha: 0.6),
      padding: const EdgeInsets.fromLTRB(10, 10, 16, 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              IconButton(
                tooltip: 'Back to all runs',
                visualDensity: VisualDensity.compact,
                icon: const Icon(Icons.arrow_back, size: 17),
                onPressed: () => _selectRun(null),
              ),
              const SizedBox(width: 2),
              Text(
                run.id,
                style: CumulusText.mono.copyWith(
                  fontSize: 13,
                  fontWeight: FontWeight.w600,
                ),
              ),
              const SizedBox(width: 8),
              StatusBadge(
                run.status,
                tone: runTone(run.status),
                dense: true,
                uppercase: true,
              ),
            ],
          ),
          Padding(
            padding: const EdgeInsets.only(left: 12, top: 2),
            child: Text(
              '${_runSubject(run).isEmpty ? '' : '${_runSubject(run)} · '}'
              '${run.steps} step${run.steps == 1 ? '' : 's'}'
              ' · ${run.modelCalls} ModelCall${run.modelCalls == 1 ? '' : 's'}'
              '${run.suspendedAtStep.isEmpty ? '' : ' · at ${run.suspendedAtStep}'}'
              '${run.resumeCount > 0 ? ' · ${run.resumeCount}× resumed' : ''}'
              ' · ${run.refs.length} governed read${run.refs.length == 1 ? '' : 's'}'
              ' · ${agoIso(run.startedAtIso)}',
              style: CumulusText.meta,
            ),
          ),
        ],
      ),
    );
  }

  Widget _runList() {
    if (_runs.isEmpty) {
      return const CumulusEmptyState(
        icon: Icons.play_circle_outline,
        title: 'No runs yet',
        message: 'This definition has never executed.',
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
                  'select one to trace its path through the graph',
                  style: CumulusText.sectionSubtitle,
                  overflow: TextOverflow.ellipsis,
                ),
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
              // No selected state to paint: choosing a run replaces this list
              // with the run's own header.
              return Material(
                color: Colors.transparent,
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
                                    ),
                                  ),
                                  const SizedBox(width: 8),
                                  if (run.status != 'succeeded')
                                    StatusBadge(
                                      run.status,
                                      tone: runTone(run.status),
                                      dense: true,
                                      uppercase: true,
                                    ),
                                ],
                              ),
                              const SizedBox(height: 2),
                              Text(
                                '${_runSubject(run).isEmpty ? '' : '${_runSubject(run)} · '}'
                                '${run.steps} steps · ${run.modelCalls} MC'
                                '${run.openTaskId.isEmpty ? '' : ' · task'}',
                                style: CumulusText.meta,
                              ),
                            ],
                          ),
                        ),
                        Text(
                          '${run.durationMs}ms',
                          style: const TextStyle(
                            fontSize: 12,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                        const SizedBox(width: 12),
                        SizedBox(
                          width: 62,
                          child: Text(
                            agoIso(run.startedAtIso),
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
      // Reachable only when the journal fetch failed — the panel is not shown
      // at all until a run is selected.
      return CumulusEmptyState(
        icon: Icons.error_outline,
        title: 'Journal unavailable',
        message:
            'The execution trail for ${_selectedRun?.id ?? 'this run'} could '
            'not be loaded.',
        action: OutlinedButton(
          onPressed: () => _selectRun(_selectedRun),
          child: const Text('Retry'),
        ),
      );
    }
    final nodeId = _selectedStepId;
    final nodeIds = {for (final d in _canvasSteps) d.step.id};
    // A looped node has one journal entry per round; the newest is the state
    // the canvas is painting, so it is the one whose payload belongs here.
    final entry = nodeId == null
        ? null
        : run.steps
              .where((s) => RunPath.canvasNode(s.stepId, nodeIds) == nodeId)
              .lastOrNull;
    if (entry == null) {
      return CumulusEmptyState(
        icon: Icons.touch_app_outlined,
        title: nodeId == null ? 'Select a step' : 'Not in this run',
        message: nodeId == null
            ? 'Click a step on the canvas to see what it read, wrote and '
                  'returned in ${run.id}.'
            : 'This step is not part of ${run.id} — the run took a different '
                  'branch.',
      );
    }
    return SingleChildScrollView(
      padding: const EdgeInsets.fromLTRB(20, 16, 20, 28),
      child: _StepRunDetail(step: entry, runId: run.id),
    );
  }

  Future<void> _exportSchema(FlowVersion version) async {
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
  String _schemaJson(FlowVersion version) {
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
        '  "flow": "${widget.flowId}",\n'
        '  "version": "${version.display}",\n'
        '  "status": "${version.status}",\n'
        '  "model_budget_usd_per_run": ${version.modelBudgetUsdPerRun},\n'
        '  "steps": [\n$steps\n  ]\n'
        '}';
  }

  /// Cost optimization, not evaluation: it reads the usage series this tab
  /// already plots, and what it stages is a draft the eval spec then has to
  /// grade — a separate act, on a separate tab.
  Widget _recommenderCard() {
    return SectionCard(
      title: 'Cost & performance',
      subtitle:
          'derived from this definition\'s own execution telemetry — '
          'applying stages a draft to grade',
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
      final applied = await _api.applyRecommendation(widget.flowId, rec.id);
      final versions = await _api.flowVersions(widget.flowId);
      final detail = await _api.getFlow(widget.flowId);
      if (!mounted) return;
      setState(() {
        _versions = versions;
        _detail = detail;
        _selectedVersion = applied.draft.version;
        _selectedRun = null;
        _runDetail = null;
      });
      await _loadCanvas();
      _snack(
        'Staged in ${applied.draft.display} — run the eval to grade it',
      );
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
  final List<FlowVersion> versions;
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

// ---------------------------------------------------------------------------
// Step inspector (right column, Definition tab)
// ---------------------------------------------------------------------------

class _StepHeader extends StatelessWidget {
  final FlowStep step;
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
  final FlowStep step;
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
              'resolve' =>
                'A Resolve step picks a winner among competing claims by a '
                    'declarative Rule — automatic like an observation, '
                    'audited like a decision, and it raises a Task when it '
                    'cannot decide.',
              'check' =>
                'A Check is a pure evaluation over what the flow already '
                    'gathered — a match, a screen — recorded as a result the '
                    'run can point at, not a number computed and discarded.',
              'sync' =>
                'A Sync step lands what an external system reported. It is an '
                    'observation: audited by per-revision lineage, bulk '
                    'rather than per-row.',
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

/// One journal entry, for the canvas node the reader clicked.
///
/// The journal records what a step *returned* and what it touched, not a
/// transcript of the call: a step whose result cannot be serialized is a step
/// doing too much, so the output below is the whole of what the next step saw.
class _StepRunDetail extends StatelessWidget {
  final RunStep step;
  final String runId;

  const _StepRunDetail({required this.step, required this.runId});

  @override
  Widget build(BuildContext context) {
    final tone = step.status == 'failed'
        ? CumulusTone.danger
        : step.status == 'suspended'
        ? CumulusTone.accent
        : stepTone(step.kind);
    final decision = step.decision;
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
                fontSize: 13.5,
                fontWeight: FontWeight.w600,
              ),
            ),
            StatusBadge(step.kind, tone: tone, dense: true, uppercase: true),
            if (step.status != 'done')
              StatusBadge(step.status, tone: tone, dense: true, uppercase: true),
            if (step.attempts > 1)
              StatusBadge(
                '${step.attempts} attempts',
                tone: CumulusTone.warn,
                dense: true,
              ),
            if (step.model.isNotEmpty)
              StatusBadge(step.model, tone: CumulusTone.violet, dense: true),
            if (step.costUsd > 0)
              StatusBadge('\$${step.costUsd.toStringAsFixed(4)}', dense: true),
            StatusBadge('${step.durationMs}ms', dense: true),
          ],
        ),
        const SizedBox(height: 6),
        Text(
          '${step.stepId} in $runId · ${agoIso(step.atIso)}',
          style: CumulusText.meta,
        ),
        const SizedBox(height: 6),
        Text(step.summary, style: CumulusText.body),
        if (decision != null)
          Padding(
            padding: const EdgeInsets.only(top: 6),
            child: Text(
              '${decision['verdict']} by ${decision['decided_by']}'
              '${(decision['note'] as String? ?? '').isEmpty ? '' : ' — ${decision['note']}'}',
              style: CumulusText.meta,
            ),
          ),
        if (step.error.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 6),
            child: Text(
              step.error,
              style: CumulusText.meta.copyWith(color: CumulusColors.danger),
            ),
          ),
        const SizedBox(height: 14),
        // Read at a revision, written under a Collection: the two halves an
        // auditor asks about, kept apart so neither is mistaken for the other.
        PayloadBlock(label: 'Read', content: step.refs.join('\n'), maxLines: 5),
        PayloadBlock(
          label: 'Wrote',
          content: step.writes.join('\n'),
          maxLines: 5,
        ),
        PayloadBlock(
          label: 'Output',
          content: step.output == null
              ? ''
              : const JsonEncoder.withIndent('  ').convert(step.output),
          maxLines: 16,
        ),
      ],
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
                  child: Text(applying ? 'Applying…' : 'Stage as draft'),
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
