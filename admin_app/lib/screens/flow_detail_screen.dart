import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../models/flow.dart';
import '../services/api_client.dart';
import '../theme/cumulus_theme.dart';
import '../widgets/case_review.dart';
import '../widgets/cumulus_shell.dart';
import '../widgets/mini_charts.dart';
import '../widgets/side_drawer.dart';
import '../widgets/ui_kit.dart';
import '../widgets/flow_canvas.dart';
import 'flows_screen.dart' show dayLabel, money, percent, statusTone;

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
  List<RunSummary> _runs = [];
  GoldenSetInfo? _golden;
  List<TestRun> _tests = [];
  List<Recommendation> _recommendations = [];
  List<FlowVersion> _versions = [];
  List<CorrectionSet> _correctionSets = [];

  int? _selectedVersion;
  List<StepDiff> _canvasSteps = [];
  String? _selectedStepId;

  /// The run being traced through the graph, and its journal.
  RunSummary? _selectedRun;
  RunDetail? _runDetail;
  bool _loadingRun = false;

  /// The experiment painted on the canvas in the Eval lens.
  TestRun? _evalRun;
  bool _liveReads = false;

  ChartMeasure _measure = ChartMeasure.runs;

  /// 0 = step definition, 1 = runs and their payloads, 2 = eval lens.
  int _rightTab = 0;

  bool _loading = true;
  String? _error;
  int _tab = 0;
  bool _launching = false;
  String? _applyingRecommendation;
  Timer? _poll;

  bool get _isAdmin => widget.myRole == 'admin' || widget.myRole == 'owner';

  FlowVersion? get _current =>
      _versions.where((v) => v.version == _selectedVersion).firstOrNull;

  FlowVersion? get _activeVersion =>
      _versions.where((v) => v.isActive).firstOrNull;

  FlowVersion? get _draftVersion =>
      _versions.where((v) => v.isDraft).firstOrNull;

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
      final id = widget.flowId;
      final results = await Future.wait([
        _api.getFlow(id),
        _api.flowUsage(id),
        _api.flowRuns(id, limit: 40),
        _api.flowGoldenSet(id),
        _api.flowTestRuns(id),
        _api.flowVersions(id),
        _api.flowRecommendations(id),
        _api.flowCorrectionSets(id),
      ]);
      if (!mounted) return;
      final versions = results[5] as List<FlowVersion>;
      final tests = results[4] as List<TestRun>;
      setState(() {
        _detail = results[0] as FlowDetail;
        _usage = results[1] as FlowUsage;
        _runs = results[2] as List<RunSummary>;
        _golden = results[3] as GoldenSetInfo;
        _tests = tests;
        _versions = versions;
        _recommendations = results[6] as List<Recommendation>;
        _correctionSets = results[7] as List<CorrectionSet>;
        _selectedVersion ??= (versions.where((v) => v.isDraft).firstOrNull ??
                versions.where((v) => v.isActive).firstOrNull)
            ?.version;
        _evalRun = _evalRun == null
            ? tests.where((t) => !t.running).firstOrNull
            : tests.where((t) => t.id == _evalRun!.id).firstOrNull ?? _evalRun;
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

  void _ensurePolling() {
    if (_tests.any((t) => t.running)) {
      _poll ??= Timer.periodic(const Duration(seconds: 1), (_) async {
        try {
          final tests = await _api.flowTestRuns(widget.flowId);
          if (!mounted) return;
          setState(() {
            _tests = tests;
            // Keep the canvas overlay pointed at the same experiment as its
            // step stats fill in.
            if (_evalRun != null) {
              _evalRun =
                  tests.where((t) => t.id == _evalRun!.id).firstOrNull ??
                  _evalRun;
            } else {
              _evalRun = tests.where((t) => !t.running).firstOrNull;
            }
          });
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
      final updated = await _api.updateFlow(widget.flowId, status: status);
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
      final launched = await _api.launchTestRun(
        widget.flowId,
        candidateVersion: _draftVersion?.display ?? '',
        baselineVersion: _activeVersion?.display ?? '',
        reads: _liveReads ? 'live' : 'pinned',
      );
      final tests = await _api.flowTestRuns(widget.flowId);
      if (!mounted) return;
      setState(() {
        _tests = tests;
        _evalRun = tests.where((t) => t.id == launched.id).firstOrNull;
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
      final detail = await _api.flowRun(widget.flowId, run.id);
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
              evalOverlay: _rightTab == 2 && _evalRun != null
                  ? EvalOverlay.fromRun(_evalRun!)
                  : null,
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
                labels: const ['Definition', 'Runs', 'Eval'],
                selectedIndex: _rightTab,
                onChanged: (i) => setState(() {
                  _rightTab = i;
                  // Leaving Runs drops the trace with it. The run highlight
                  // belongs to the Runs tab; leaving it on while reading the
                  // definition claims the graph is showing one execution when
                  // nothing on screen says which. The eval heatmap likewise
                  // paints only while its own tab owns the graph.
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
            child: switch (_rightTab) {
              0 => _definitionPanel(),
              1 => _runsPanel(),
              _ => _evalPanel(),
            },
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
    final detail = _runDetail;
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
                runStatusLabel(run.status),
                tone: runTone(run.status),
                dense: true,
                uppercase: true,
              ),
              const Spacer(),
              if (detail != null && _isAdmin)
                TextButton.icon(
                  onPressed: () => _promoteRun(detail),
                  icon: const Icon(Icons.playlist_add_check, size: 15),
                  label: const Text('Promote'),
                ),
            ],
          ),
          Padding(
            padding: const EdgeInsets.only(left: 12, top: 2),
            child: Text(
              '${run.caseRef} · ${run.activeSeconds.round()}s active'
              '${run.suspendedMinutes > 0 ? ' · ${run.suspendedMinutes}m suspended' : ''}'
              ' · \$${run.costUsd.toStringAsFixed(2)}'
              ' · ${run.modelCalls} ModelCall${run.modelCalls == 1 ? '' : 's'}'
              ' · ${run.actionsCommitted} Action${run.actionsCommitted == 1 ? '' : 's'}'
              ' · ${agoLabel(run.startedAt)}',
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
      // Reachable only when the journal fetch failed — the panel is not shown
      // at all until a run is selected.
      return CumulusEmptyState(
        icon: Icons.error_outline,
        title: 'Journal unavailable',
        message:
            'The execution trail for ${_selectedRun?.id ?? 'this run'} could '
            'not be loaded. Journals are kept 45 days.',
        action: OutlinedButton(
          onPressed: () => _selectRun(_selectedRun),
          child: const Text('Retry'),
        ),
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

  // ---- Eval lens (right column, third tab) --------------------------------

  /// The graph as a heatmap for one experiment: pick the experiment here,
  /// click a hot node on the canvas, and its failing assertions land below.
  Widget _evalPanel() {
    if (_tests.isEmpty) {
      return CumulusEmptyState(
        icon: Icons.science_outlined,
        title: 'No experiments yet',
        message:
            'Run the suite to grade the candidate against the golden set — '
            'each step lights up with the pass rate of the facts it owns.',
        action: OutlinedButton(
          onPressed: (_isAdmin && !_launching) ? _launchTest : null,
          child: const Text('Run experiment'),
        ),
      );
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(20, 14, 20, 8),
          child: Text('EXPERIMENT', style: CumulusText.sectionTitle),
        ),
        for (final run in _tests.take(4)) _evalRunOption(run),
        const Divider(height: 17),
        Expanded(
          child: _evalRun == null
              ? const CumulusEmptyState(
                  icon: Icons.touch_app_outlined,
                  title: 'Pick an experiment',
                  message: 'Select one above to paint it onto the graph.',
                )
              : _evalDetail(_evalRun!),
        ),
      ],
    );
  }

  Widget _evalRunOption(TestRun run) {
    final selected = _evalRun?.id == run.id;
    return Material(
      color: selected
          ? CumulusColors.surfaceMuted.withValues(alpha: 0.7)
          : Colors.transparent,
      child: InkWell(
        onTap: () => setState(() => _evalRun = run),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 8),
          child: Row(
            children: [
              Icon(
                selected
                    ? Icons.radio_button_checked
                    : Icons.radio_button_off,
                size: 14,
                color: selected
                    ? CumulusColors.accent
                    : CumulusColors.inkMuted,
              ),
              const SizedBox(width: 10),
              Expanded(
                child: Text(
                  '${run.candidateVersion} vs ${run.baselineVersion}'
                  ' · dataset v${run.datasetVersion}',
                  overflow: TextOverflow.ellipsis,
                  style: CumulusText.mono.copyWith(fontSize: 11.5),
                ),
              ),
              const SizedBox(width: 8),
              if (run.running)
                Text(
                  '${run.results.length}/${run.totalCases}…',
                  style: CumulusText.meta,
                )
              else ...[
                if (run.gate != null)
                  Icon(
                    run.gate!.passed
                        ? Icons.verified_outlined
                        : Icons.block_outlined,
                    size: 14,
                    color: run.gate!.passed
                        ? CumulusColors.success
                        : CumulusColors.danger,
                  ),
                const SizedBox(width: 6),
                Text(
                  '${run.passed}/${run.totalCases}',
                  style: const TextStyle(
                    fontSize: 11.5,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }

  Widget _evalDetail(TestRun run) {
    final stepId = _selectedStepId;
    if (stepId == null) {
      return ListView(
        padding: const EdgeInsets.fromLTRB(20, 12, 20, 24),
        children: [
          _gateBanner(run),
          const SizedBox(height: 14),
          Text('BY STEP', style: CumulusText.sectionTitle),
          const SizedBox(height: 4),
          Text(
            'assertions graded where their facts are produced — click a '
            'node on the canvas to see its failures',
            style: CumulusText.meta,
          ),
          const SizedBox(height: 10),
          for (final stat in run.stepStats) _stepStatRow(stat),
          if (run.stepStats.isEmpty)
            Text(
              run.running
                  ? 'Grading — stats fill in as cases land.'
                  : 'No localized assertions in this experiment.',
              style: CumulusText.meta,
            ),
        ],
      );
    }
    // Every assertion this step owns, across all cases; failures first.
    final owned = <(TestCaseResult, AssertionResult)>[
      for (final result in run.results)
        for (final a in result.assertions)
          if (a.stepId == stepId) (result, a),
    ];
    if (owned.isEmpty) {
      return const CumulusEmptyState(
        icon: Icons.rule_outlined,
        title: 'Owns no assertions',
        message:
            'No contract fact projects onto this step in the candidate '
            'version, so the experiment has nothing to say about it.',
      );
    }
    final failing = owned.where((e) => !e.$2.passed).toList();
    final passing = owned.length - failing.length;
    return ListView(
      padding: const EdgeInsets.fromLTRB(20, 12, 20, 24),
      children: [
        Text(
          '${failing.length} failing · $passing passing on this step',
          style: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.w600),
        ),
        const SizedBox(height: 10),
        for (final (result, a) in failing) _assertionRow(result, a),
        if (failing.isEmpty)
          Container(
            padding: const EdgeInsets.all(13),
            decoration: BoxDecoration(
              color: CumulusColors.surfaceMuted,
              borderRadius: BorderRadius.circular(CumulusRadius.control),
            ),
            child: Text(
              'Every assertion this step owns passed in '
              '${run.candidateVersion}.',
              style: CumulusText.meta,
            ),
          ),
      ],
    );
  }

  Widget _gateBanner(TestRun run) {
    if (run.running) {
      return Container(
        padding: const EdgeInsets.all(13),
        decoration: BoxDecoration(
          color: CumulusColors.surfaceMuted,
          borderRadius: BorderRadius.circular(CumulusRadius.control),
        ),
        child: Text(
          'Grading ${run.results.length}/${run.totalCases} cases — the gate '
          'is stated when the run completes.',
          style: CumulusText.meta,
        ),
      );
    }
    final gate = run.gate;
    if (gate == null) return const SizedBox.shrink();
    final tone = gate.passed ? CumulusTone.success : CumulusTone.danger;
    return Container(
      padding: const EdgeInsets.all(13),
      decoration: BoxDecoration(
        color: tone.background,
        borderRadius: BorderRadius.circular(CumulusRadius.control),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(
            gate.passed ? Icons.verified_outlined : Icons.block_outlined,
            size: 16,
            color: tone.foreground,
          ),
          const SizedBox(width: 9),
          Expanded(
            child: Text(
              gate.passed
                  ? 'Gate: promotable — no regressions, at or above the '
                        'baseline.'
                  : 'Gate: blocked — ${gate.reasons.join('; ')}.',
              style: TextStyle(
                fontSize: 12,
                height: 1.4,
                fontWeight: FontWeight.w600,
                color: tone.foreground,
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _stepStatRow(StepStat stat) {
    final color = evalTone(stat.passRate);
    return InkWell(
      onTap: () => setState(() => _selectedStepId = stat.stepId),
      borderRadius: BorderRadius.circular(CumulusRadius.control),
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 6, horizontal: 2),
        child: Row(
          children: [
            SizedBox(
              width: 138,
              child: Text(
                stat.stepName,
                overflow: TextOverflow.ellipsis,
                style: CumulusText.mono.copyWith(fontSize: 11.5),
              ),
            ),
            Expanded(
              child: ClipRRect(
                borderRadius: BorderRadius.circular(99),
                child: LinearProgressIndicator(
                  minHeight: 7,
                  value: stat.passRate,
                  color: color,
                  backgroundColor: CumulusColors.surfaceMuted,
                ),
              ),
            ),
            const SizedBox(width: 10),
            SizedBox(
              width: 64,
              child: Text(
                '${stat.passed}/${stat.total}',
                textAlign: TextAlign.right,
                style: const TextStyle(
                  fontSize: 11.5,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _assertionRow(TestCaseResult result, AssertionResult a) {
    final review = a.verdict == 'review';
    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
      decoration: BoxDecoration(
        color: CumulusColors.surface,
        borderRadius: BorderRadius.circular(CumulusRadius.control),
        border: Border.all(
          color: review ? CumulusColors.accent : CumulusColors.danger,
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  result.name,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    fontSize: 12,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ),
              StatusBadge(
                review ? 'review task' : a.grader,
                dense: true,
                tone: review ? CumulusTone.accent : CumulusTone.neutral,
              ),
            ],
          ),
          const SizedBox(height: 5),
          Text(
            a.label,
            style: CumulusText.mono.copyWith(
              fontSize: 11,
              color: CumulusColors.inkSecondary,
            ),
          ),
          const SizedBox(height: 3),
          Text(
            'expected ${a.expected}\ngot      ${a.actual}',
            style: CumulusText.mono.copyWith(fontSize: 11.5, height: 1.5),
          ),
          if (_golden?.cases.any((c) => c.id == result.caseId) ?? false)
            Align(
              alignment: Alignment.centerRight,
              child: TextButton(
                style: TextButton.styleFrom(
                  padding: const EdgeInsets.symmetric(horizontal: 8),
                  visualDensity: VisualDensity.compact,
                ),
                onPressed: () => _openReview(
                  _golden!.cases.firstWhere((c) => c.id == result.caseId),
                ),
                child: const Text('Review case'),
              ),
            ),
        ],
      ),
    );
  }

  Future<void> _openReview(GoldenCase goldenCase) async {
    final updated = await showCaseReview(
      context,
      api: _api,
      flowId: widget.flowId,
      goldenCase: goldenCase,
      canEdit: _isAdmin,
    );
    if (updated != null && mounted) {
      setState(() => _golden = updated);
      _snack(
        'Review saved — golden dataset is now v${updated.version}',
      );
    }
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
                'The case keeps a pointer back to ${run.id}, and captures '
                'the reads the run made off its journal — the pinned world '
                'that keeps the regression grade reproducible. Refine the '
                'labels afterwards in the review screen.',
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
        widget.flowId,
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

  // ---- Tab 3: evaluation & optimization ----------------------------------

  List<Widget> _evaluationBody() {
    return [
      LayoutBuilder(
        builder: (context, constraints) {
          final datasets = Column(
            spacing: 16,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [_goldenCard(), _correctionSetsCard()],
          );
          final experiments = Column(
            spacing: 16,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [_experimentsCard(), _recommenderCard()],
          );
          if (constraints.maxWidth < 1120) {
            return Column(
              spacing: 16,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [datasets, experiments],
            );
          }
          return Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            spacing: 16,
            children: [
              Expanded(child: datasets),
              Expanded(child: experiments),
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
      subtitle:
          '${cases.length} cases · dataset v${golden?.version ?? 1} · '
          'knowledge-role Dataset',
      headerTrailing: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (golden != null && golden.revisions.isNotEmpty)
            _RevisionsButton(revisions: golden.revisions),
          if (_isAdmin)
            TextButton.icon(
              onPressed: _importCases,
              icon: const Icon(Icons.upload_file_outlined, size: 15),
              label: const Text('Upload'),
            ),
        ],
      ),
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
                    'Authored in code, synced from ${golden.syncedFrom}'
                    '${golden.syncedAt == null ? '' : ' · ${agoLabel(golden.syncedAt!)}'}'
                    ' — the console curates; the SDK authors.',
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
                'Seed the set: promote runs from the canvas (labels and the '
                'pinned read-set come off the journal), upload JSONL from '
                'your notebook, or push from the SDK.',
          )
        else
          for (final c in cases)
            CumulusListRow(
              leading: SizedBox(
                width: 82,
                child: Text(
                  c.id,
                  overflow: TextOverflow.ellipsis,
                  style: CumulusText.mono.copyWith(fontSize: 11.5),
                ),
              ),
              title: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Flexible(
                    child: Text(c.name, overflow: TextOverflow.ellipsis),
                  ),
                  if (c.reviewedBy.isNotEmpty) ...[
                    const SizedBox(width: 6),
                    const Tooltip(
                      message: 'Labels human-reviewed',
                      child: Icon(
                        Icons.verified_outlined,
                        size: 13,
                        color: CumulusColors.success,
                      ),
                    ),
                  ],
                ],
              ),
              subtitle: Text(
                c.assertionCount == 0
                    ? 'expect: ${c.expected}'
                    : '${c.assertions.length} fact · ${c.trajectory.length} '
                          'trajectory assertions · ${c.readSet.length} pinned '
                          'reads${c.runId.isEmpty ? '' : ' · from ${c.runId}'}',
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
              ),
              onTap: () => _openReview(c),
              trailing: [
                StatusBadge(
                  c.source,
                  dense: true,
                  tone: c.source == 'promoted-from-run'
                      ? CumulusTone.accent
                      : CumulusTone.neutral,
                ),
                const SizedBox(width: 6),
                TextButton(
                  style: TextButton.styleFrom(
                    visualDensity: VisualDensity.compact,
                    padding: const EdgeInsets.symmetric(horizontal: 8),
                  ),
                  onPressed: () => _openReview(c),
                  child: const Text('Review'),
                ),
                if (_isAdmin)
                  IconButton(
                    tooltip: 'Remove case',
                    icon: const Icon(Icons.close, size: 14),
                    onPressed: () async {
                      try {
                        final updated = await _api.removeGoldenCase(
                          widget.flowId,
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
            ),
        Container(
          margin: const EdgeInsets.fromLTRB(20, 10, 20, 16),
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
          decoration: BoxDecoration(
            color: CumulusColors.surfaceMuted,
            borderRadius: BorderRadius.circular(CumulusRadius.control),
          ),
          child: Row(
            children: [
              Expanded(
                child: Text(
                  'cumulus eval push --flow ${widget.flowId} golden/*.jsonl',
                  style: CumulusText.mono.copyWith(fontSize: 11),
                ),
              ),
              IconButton(
                tooltip: 'Copy the SDK command',
                visualDensity: VisualDensity.compact,
                icon: const Icon(Icons.copy, size: 13),
                onPressed: () async {
                  await Clipboard.setData(
                    ClipboardData(
                      text:
                          'cumulus eval push --flow ${widget.flowId} '
                          'golden/*.jsonl',
                    ),
                  );
                  _snack('Copied');
                },
              ),
            ],
          ),
        ),
      ],
    );
  }

  Widget _correctionSetsCard() {
    return ListCard(
      title: 'Correction sets',
      subtitle:
          'raw → corrected pairs, auto-appended by the flywheel · one per '
          'ModelCall',
      children: [
        if (_correctionSets.isEmpty)
          const CumulusEmptyState(
            icon: Icons.model_training_outlined,
            title: 'No ModelCalls',
            message:
                'This definition invokes no models, so there is nothing for '
                'human corrections to accumulate against.',
          )
        else
          for (final set in _correctionSets)
            CumulusListRow(
              leading: SizedBox(
                width: 82,
                child: Text(
                  set.stepId,
                  style: CumulusText.mono.copyWith(fontSize: 11.5),
                ),
              ),
              title: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Flexible(
                    child: Text(
                      set.stepName,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                  if (set.model.isNotEmpty) ...[
                    const SizedBox(width: 8),
                    StatusBadge(set.model, dense: true, tone: CumulusTone.violet),
                  ],
                ],
              ),
              subtitle: Text(
                set.pairs == 0
                    ? 'no corrections yet'
                    : '${set.pairs} pairs · last appended '
                          '${set.lastAppended == null ? '—' : agoLabel(set.lastAppended!)}',
              ),
              onTap: set.sample.isEmpty ? null : () => _openCorrectionSet(set),
              trailing: [
                if (set.sample.isNotEmpty)
                  const Icon(
                    Icons.chevron_right,
                    size: 16,
                    color: CumulusColors.inkMuted,
                  ),
              ],
            ),
      ],
    );
  }

  Future<void> _openCorrectionSet(CorrectionSet set) async {
    await showSideDrawer<void>(
      context: context,
      width: 560,
      title: 'Correction set · ${set.stepName}',
      subtitle:
          '${set.pairs} raw → corrected pairs · the step-eval dataset a '
          'prompt or model change is graded against',
      builder: (context) => Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          for (final pair in set.sample)
            Container(
              margin: const EdgeInsets.only(bottom: 10),
              padding: const EdgeInsets.all(13),
              decoration: BoxDecoration(
                color: CumulusColors.surfaceMuted,
                borderRadius: BorderRadius.circular(CumulusRadius.control),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    pair.field,
                    style: CumulusText.mono.copyWith(
                      fontSize: 11,
                      color: CumulusColors.inkSecondary,
                    ),
                  ),
                  const SizedBox(height: 6),
                  Row(
                    children: [
                      Text(
                        pair.raw,
                        style: CumulusText.mono.copyWith(
                          fontSize: 12,
                          color: CumulusColors.inkMuted,
                          decoration: TextDecoration.lineThrough,
                        ),
                      ),
                      const Padding(
                        padding: EdgeInsets.symmetric(horizontal: 8),
                        child: Icon(
                          Icons.arrow_forward,
                          size: 13,
                          color: CumulusColors.inkMuted,
                        ),
                      ),
                      Text(
                        pair.corrected,
                        style: CumulusText.mono.copyWith(
                          fontSize: 12,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 5),
                  Text(
                    'by ${pair.by} · ${agoLabel(pair.at)} · in the curation '
                    'ledger',
                    style: CumulusText.meta,
                  ),
                ],
              ),
            ),
          Text(
            'Corrections land here automatically whenever a person corrects '
            'this step\'s output in a Task — never promoted to canonical '
            'knowledge without a Rule or a curation decision.',
            style: CumulusText.meta,
          ),
        ],
      ),
    );
  }

  Future<void> _importCases() async {
    final controller = TextEditingController();
    final saved = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Upload cases · JSONL'),
        content: SizedBox(
          width: 560,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                'One case per line — the same shape `cumulus eval push` '
                'emits. Assertions key to contract fact paths.',
                style: CumulusText.meta,
              ),
              const SizedBox(height: 12),
              TextField(
                controller: controller,
                maxLines: 9,
                style: CumulusText.mono.copyWith(fontSize: 11.5),
                decoration: const InputDecoration(
                  hintText:
                      '{"name": "Credit memo", "assertions": [{"path": '
                      '"invoice.amount_usd", "expected": "-1120.00"}], '
                      '"trajectory": [{"kind": "task_raised", "expected": '
                      '"true"}]}',
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
            child: const Text('Import'),
          ),
        ],
      ),
    );
    if (saved != true || controller.text.trim().isEmpty) return;
    try {
      final updated = await _api.importGoldenCases(
        widget.flowId,
        controller.text.trim(),
      );
      if (!mounted) return;
      setState(() => _golden = updated);
      _snack('Imported — golden dataset is now v${updated.version}');
    } on ApiException catch (e) {
      _snack(e.message);
    }
  }

  Widget _experimentsCard() {
    final golden = _golden;
    final cases = golden?.cases.length ?? 0;
    final completed = _tests.where((t) => !t.running).toList();
    // Oldest → newest, so the trend reads left to right.
    final trend = completed.reversed
        .map((t) => (t.passRate * 100).round())
        .toList();
    return SectionCard(
      title: 'Experiments',
      subtitle:
          'candidate vs baseline · dataset revision pinned per run · writes '
          'suppressed',
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
                child: Text(_launching ? 'Launching…' : 'Run experiment'),
              ),
              const SizedBox(width: 10),
              Tooltip(
                message: _liveReads
                    ? 'Reads today\'s knowledge layer — a smoke check, not a '
                          'comparable regression run'
                    : 'Each case\'s pinned read-set is served back — '
                          'reproducible regression grading',
                child: InkWell(
                  borderRadius: BorderRadius.circular(99),
                  onTap: () => setState(() => _liveReads = !_liveReads),
                  child: StatusBadge(
                    _liveReads ? 'live reads' : 'pinned reads',
                    dense: true,
                    tone: _liveReads ? CumulusTone.warn : CumulusTone.neutral,
                  ),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Text(
                  '$cases cases · graded against both versions on dataset '
                  'v${golden?.version ?? 1}',
                  style: CumulusText.meta,
                ),
              ),
            ],
          ),
          if (trend.length >= 2) ...[
            const SizedBox(height: 16),
            Row(
              children: [
                Text('PASS-RATE TREND', style: CumulusText.statLabel),
                const SizedBox(width: 12),
                Sparkline(values: trend, width: 120, height: 22),
                const SizedBox(width: 10),
                Text(
                  'latest ${trend.last}%',
                  style: const TextStyle(
                    fontSize: 11.5,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ],
            ),
          ],
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
      width: 720,
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
        if (run.gate != null)
          StatusBadge(
            run.gate!.passed ? 'gate: promotable' : 'gate: blocked',
            tone: run.gate!.passed ? CumulusTone.success : CumulusTone.danger,
            dense: true,
          ),
      ],
      subtitle:
          'baseline ${run.baselinePassed}/${run.totalCases} · '
          'cost ${run.costDeltaUsd >= 0 ? '+' : ''}'
          '\$${run.costDeltaUsd.toStringAsFixed(2)} · '
          'latency ${run.latencyDeltaMs >= 0 ? '+' : ''}${run.latencyDeltaMs}ms '
          'per case · dataset v${run.datasetVersion} · ${run.reads} reads',
      actions: [
        OutlinedButton.icon(
          icon: const Icon(Icons.account_tree_outlined, size: 15),
          label: const Text('View on canvas'),
          onPressed: () {
            Navigator.of(context).pop();
            setState(() {
              _evalRun = run;
              _tab = 1;
              _rightTab = 2;
              _selectedRun = null;
              _runDetail = null;
            });
          },
        ),
      ],
      builder: (context) => _TestRunComparison(
        run: run,
        onReviewCase: (caseId) {
          final goldenCase = _golden?.cases
              .where((c) => c.id == caseId)
              .firstOrNull;
          if (goldenCase == null) return;
          Navigator.of(context).pop();
          _openReview(goldenCase);
        },
      ),
    );
  }

  Widget _recommenderCard() {
    return SectionCard(
      title: 'Cost & performance',
      subtitle:
          'derived from this definition\'s own execution telemetry — '
          'applying stages a draft and measures it',
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
                    // The experiment the apply auto-launched, once it exists:
                    // the card's estimate becomes a measurement.
                    measured: _tests
                        .where((t) => t.launchedFor == rec.id)
                        .firstOrNull,
                    onApply: () => _applyRecommendation(rec),
                    onOpenMeasured: (run) => _openTestRun(run),
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
      final tests = await _api.flowTestRuns(widget.flowId);
      if (!mounted) return;
      setState(() {
        _versions = versions;
        _detail = detail;
        _tests = tests;
        _selectedVersion = applied.draft.version;
        _selectedRun = null;
        _runDetail = null;
        if (applied.testRunId.isNotEmpty) {
          _evalRun = tests
              .where((t) => t.id == applied.testRunId)
              .firstOrNull;
        }
      });
      await _loadCanvas();
      _ensurePolling();
      _snack(
        applied.testRunId.isEmpty
            ? 'Staged in ${applied.draft.display} — add golden cases to '
                  'measure it'
            : 'Staged in ${applied.draft.display} — experiment measuring it '
                  'against the golden set now',
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

/// The dataset's revision history, as a popup off the golden card header.
class _RevisionsButton extends StatelessWidget {
  final List<GoldenSetRevision> revisions;

  const _RevisionsButton({required this.revisions});

  @override
  Widget build(BuildContext context) {
    final newest = revisions.reversed.toList();
    return PopupMenuButton<void>(
      tooltip: 'Dataset revisions',
      offset: const Offset(0, 34),
      itemBuilder: (context) => [
        for (final rev in newest.take(8))
          PopupMenuItem<void>(
            enabled: false,
            height: 52,
            child: SizedBox(
              width: 340,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Text(
                    'v${rev.version} · ${rev.note}',
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                      fontSize: 12,
                      fontWeight: FontWeight.w600,
                      color: CumulusColors.ink,
                    ),
                  ),
                  const SizedBox(height: 2),
                  Text(
                    '${rev.caseCount} cases · '
                    '${rev.by.isEmpty ? 'console' : rev.by} · '
                    '${agoLabel(rev.at)}',
                    style: CumulusText.meta,
                  ),
                ],
              ),
            ),
          ),
      ],
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 4),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(
              Icons.history,
              size: 15,
              color: CumulusColors.inkSecondary,
            ),
            const SizedBox(width: 5),
            Text(
              'v${revisions.last.version}',
              style: CumulusText.mono.copyWith(
                fontSize: 11.5,
                fontWeight: FontWeight.w600,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

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
              width: 20,
              child: run.running
                  ? const SizedBox(
                      width: 12,
                      height: 12,
                      child: CircularProgressIndicator(strokeWidth: 1.6),
                    )
                  : Tooltip(
                      message: run.gate == null
                          ? ''
                          : run.gate!.passed
                          ? 'Gate: promotable'
                          : 'Gate: ${run.gate!.reasons.join('; ')}',
                      child: Icon(
                        run.gate?.passed ?? false
                            ? Icons.verified_outlined
                            : Icons.block_outlined,
                        size: 14,
                        color: run.gate?.passed ?? false
                            ? CumulusColors.success
                            : CumulusColors.danger,
                      ),
                    ),
            ),
            const SizedBox(width: 6),
            SizedBox(
              width: 118,
              child: Row(
                children: [
                  Flexible(
                    child: Text(
                      run.candidateVersion,
                      overflow: TextOverflow.ellipsis,
                      style: CumulusText.mono.copyWith(fontSize: 11.5),
                    ),
                  ),
                  if (run.launchedFor.isNotEmpty)
                    const Tooltip(
                      message: 'Auto-launched by applying a recommendation',
                      child: Padding(
                        padding: EdgeInsets.only(left: 4),
                        child: Icon(
                          Icons.bolt,
                          size: 12,
                          color: CumulusColors.inkMuted,
                        ),
                      ),
                    ),
                ],
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
  final ValueChanged<String> onReviewCase;

  const _TestRunComparison({required this.run, required this.onReviewCase});

  @override
  Widget build(BuildContext context) {
    final gate = run.gate;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (gate != null) ...[
          Container(
            padding: const EdgeInsets.all(13),
            decoration: BoxDecoration(
              color: (gate.passed ? CumulusTone.success : CumulusTone.danger)
                  .background,
              borderRadius: BorderRadius.circular(CumulusRadius.control),
            ),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(
                  gate.passed ? Icons.verified_outlined : Icons.block_outlined,
                  size: 16,
                  color: (gate.passed
                          ? CumulusTone.success
                          : CumulusTone.danger)
                      .foreground,
                ),
                const SizedBox(width: 9),
                Expanded(
                  child: Text(
                    gate.passed
                        ? 'Promotable: no regressions against '
                              '${run.baselineVersion}, pass rate at or above '
                              'the baseline.'
                        : 'Blocked: ${gate.reasons.join('; ')}.',
                    style: TextStyle(
                      fontSize: 12.5,
                      height: 1.4,
                      fontWeight: FontWeight.w600,
                      color: (gate.passed
                              ? CumulusTone.success
                              : CumulusTone.danger)
                          .foreground,
                    ),
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(height: 12),
        ],
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
        if (run.reviewsRaised > 0) ...[
          const SizedBox(height: 10),
          Text(
            '${run.reviewsRaised} assertion'
            '${run.reviewsRaised == 1 ? '' : 's'} the judge could not decide '
            '— each raised a review Task instead of guessing.',
            style: CumulusText.meta,
          ),
        ],
        if (run.stepStats.isNotEmpty) ...[
          const SizedBox(height: 18),
          Text('BY STEP', style: CumulusText.sectionTitle),
          const SizedBox(height: 4),
          Text(
            'assertions localized to the step owning each fact in '
            '${run.candidateVersion}\'s producer map',
            style: CumulusText.meta,
          ),
          const SizedBox(height: 10),
          for (final stat in run.stepStats)
            Padding(
              padding: const EdgeInsets.only(bottom: 7),
              child: Row(
                children: [
                  SizedBox(
                    width: 150,
                    child: Text(
                      stat.stepName,
                      overflow: TextOverflow.ellipsis,
                      style: CumulusText.mono.copyWith(fontSize: 11.5),
                    ),
                  ),
                  Expanded(
                    child: ClipRRect(
                      borderRadius: BorderRadius.circular(99),
                      child: LinearProgressIndicator(
                        minHeight: 7,
                        value: stat.passRate,
                        color: evalTone(stat.passRate),
                        backgroundColor: CumulusColors.surfaceMuted,
                      ),
                    ),
                  ),
                  const SizedBox(width: 10),
                  SizedBox(
                    width: 120,
                    child: Text(
                      '${stat.passed}/${stat.total} · was '
                      '${stat.baselinePassed}/${stat.baselinePassed + stat.baselineFailed}',
                      textAlign: TextAlign.right,
                      style: CumulusText.meta,
                    ),
                  ),
                ],
              ),
            ),
        ],
        const SizedBox(height: 18),
        Text('CASES', style: CumulusText.sectionTitle),
        const SizedBox(height: 6),
        for (final r in run.results)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 9),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Row(
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
                            tone: r.passed
                                ? CumulusTone.success
                                : CumulusTone.danger,
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
                            '${r.assertions.where((a) => a.passed).length}'
                            '/${r.assertions.length} assertions pass',
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
                // The diff that makes a failure actionable: which claim
                // diverged, what came out instead, and who graded it.
                for (final a in r.assertions.where((a) => !a.passed))
                  Container(
                    margin: const EdgeInsets.only(left: 120, top: 7),
                    padding: const EdgeInsets.fromLTRB(11, 9, 11, 9),
                    decoration: BoxDecoration(
                      color: CumulusColors.surfaceMuted,
                      borderRadius: BorderRadius.circular(
                        CumulusRadius.control,
                      ),
                    ),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(
                          children: [
                            Expanded(
                              child: Text(
                                a.label,
                                style: CumulusText.mono.copyWith(
                                  fontSize: 11,
                                  color: CumulusColors.inkSecondary,
                                ),
                              ),
                            ),
                            StatusBadge(
                              a.verdict == 'review'
                                  ? 'review task'
                                  : a.grader,
                              dense: true,
                              tone: a.verdict == 'review'
                                  ? CumulusTone.accent
                                  : CumulusTone.neutral,
                            ),
                          ],
                        ),
                        const SizedBox(height: 4),
                        Text(
                          'expected ${a.expected}\ngot      ${a.actual}',
                          style: CumulusText.mono.copyWith(
                            fontSize: 11.5,
                            height: 1.5,
                          ),
                        ),
                      ],
                    ),
                  ),
                if (!r.passed)
                  Align(
                    alignment: Alignment.centerRight,
                    child: TextButton(
                      style: TextButton.styleFrom(
                        visualDensity: VisualDensity.compact,
                        padding: const EdgeInsets.symmetric(horizontal: 8),
                      ),
                      onPressed: () => onReviewCase(r.caseId),
                      child: const Text('Review case'),
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

  /// The experiment auto-launched when this card was applied, if any — the
  /// measured delta that replaces the estimate.
  final TestRun? measured;

  final VoidCallback onApply;
  final ValueChanged<TestRun> onOpenMeasured;

  const _RecommendationCard({
    required this.rec,
    required this.canApply,
    required this.applying,
    required this.measured,
    required this.onApply,
    required this.onOpenMeasured,
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
          if (measured != null) ...[
            const SizedBox(height: 10),
            InkWell(
              onTap: measured!.running
                  ? null
                  : () => onOpenMeasured(measured!),
              borderRadius: BorderRadius.circular(8),
              child: Container(
                padding: const EdgeInsets.symmetric(
                  horizontal: 10,
                  vertical: 8,
                ),
                decoration: BoxDecoration(
                  color: measured!.running
                      ? CumulusColors.surfaceMuted
                      : (measured!.gate?.passed ?? false)
                      ? CumulusTone.success.background
                      : CumulusTone.warn.background,
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Row(
                  children: [
                    Icon(
                      measured!.running
                          ? Icons.hourglass_top_outlined
                          : Icons.speed_outlined,
                      size: 14,
                      color: CumulusColors.inkSecondary,
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        measured!.running
                            ? 'Measuring — '
                                  '${measured!.results.length}/${measured!.totalCases} '
                                  'cases graded…'
                            : 'Measured on ${measured!.totalCases} cases: '
                                  '${measured!.costDeltaUsd <= 0 ? '' : '+'}'
                                  '\$${measured!.costDeltaUsd.toStringAsFixed(2)} '
                                  'cost · ${measured!.passed}/${measured!.totalCases} '
                                  'pass · gate '
                                  '${(measured!.gate?.passed ?? false) ? 'promotable' : 'blocked'}',
                        style: CumulusText.meta.copyWith(
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ],
          const SizedBox(height: 12),
          Row(
            children: [
              if (rec.applicable)
                FilledButton(
                  onPressed: (canApply && !applying && measured == null)
                      ? onApply
                      : null,
                  child: Text(
                    applying
                        ? 'Applying…'
                        : measured == null
                        ? 'Apply & measure'
                        : 'Applied',
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
