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
import '../widgets/run_workbench.dart' show RunJournalView, runTone;
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

/// How far back the run history reaches. The whole history arrives in one
/// fetch, newest first, so a window is a cut on the list already in hand
/// rather than another round trip.
enum RunWindow {
  all('All time', null),
  day('Last 24 hours', Duration(hours: 24)),
  week('Last 7 days', Duration(days: 7)),
  month('Last 30 days', Duration(days: 30));

  final String label;
  final Duration? span;

  const RunWindow(this.label, this.span);
}

/// Cut the run history by status, by time and by a free-text search over the
/// fields a run is actually looked up by: the id someone pasted out of a
/// ticket, the case it ran on, the person it ran for, the step it is stuck at,
/// the error it failed with, a governed document it read.
///
/// Order is preserved, so the history stays newest-first through any filter.
List<FlowRunSummary> filterRuns(
  List<FlowRunSummary> runs, {
  String? status,
  RunWindow window = RunWindow.all,
  String query = '',
  DateTime? now,
}) {
  final needle = query.trim().toLowerCase();
  final span = window.span;
  final clock = now ?? DateTime.now();
  return runs.where((run) {
    if (status != null && run.status != status) return false;
    if (span != null && !runWithin(run, clock, span)) return false;
    if (needle.isEmpty) return true;
    return [
      run.id,
      run.caseRef,
      run.actor,
      run.status,
      run.suspendedAtStep,
      run.error,
      run.openTaskId,
      ...run.refs,
    ].any((field) => field.toLowerCase().contains(needle));
  }).toList();
}

/// A run whose timestamp will not parse cannot be placed in a window, so it
/// stays in every window. Dropping it would hide a run that exists.
bool runWithin(FlowRunSummary run, DateTime now, Duration span) {
  final started = DateTime.tryParse(run.startedAtIso);
  return started == null || now.difference(started) <= span;
}

/// One definition, in four views: what it is and how it is doing, the graph,
/// every run it has ever had, and how a candidate version measures up.
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

  /// The run whose journal is open, and the journal itself. One cache: the
  /// Runs tab expands it inline and the canvas traces its path, and both of
  /// them want the same fetch.
  FlowRunSummary? _selectedRun;
  FlowRun? _runDetail;
  bool _loadingRun = false;

  /// Whether the canvas is painting [_selectedRun]'s path. Opening a journal
  /// to read it is not the same act as asking the graph to show the branch it
  /// took: the second one moves the canvas onto the version that ran, and
  /// doing that behind an expand would swap the version selector under you.
  bool _tracing = false;

  /// Run-history filters. The history is fetched once and cut in the client —
  /// a few hundred rows is a list to narrow, not a query to re-issue.
  final TextEditingController _runSearch = TextEditingController();
  String? _runStatus;
  RunWindow _runWindow = RunWindow.all;

  ChartMeasure _measure = ChartMeasure.runs;

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

  RunPath? get _runPath => !_tracing || _runDetail == null
      ? null
      : RunPath.fromRun(_runDetail!, [for (final d in _canvasSteps) d.step]);

  @override
  void initState() {
    super.initState();
    _runSearch.addListener(() => setState(() {}));
    _refresh();
  }

  @override
  void dispose() {
    _runSearch.dispose();
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
        _api.listRuns(flowId: id, limit: _runHistoryLimit),
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

  /// Open a run's journal. With [trace] the canvas paints its path too, and
  /// because a path belongs to the version that ran, that — and only that —
  /// moves the canvas off any draft it was showing.
  Future<void> _selectRun(FlowRunSummary? run, {bool trace = false}) async {
    if (run == null) {
      setState(() {
        _selectedRun = null;
        _runDetail = null;
        _tracing = false;
      });
      return;
    }
    final alreadyLoaded = _selectedRun?.id == run.id && _runDetail != null;
    setState(() {
      _selectedRun = run;
      _tracing = trace;
      if (!alreadyLoaded) {
        _runDetail = null;
        _loadingRun = true;
      }
    });
    if (trace) {
      final active = _activeVersion;
      if (active != null && _selectedVersion != active.version) {
        setState(() => _selectedVersion = active.version);
        await _loadCanvas();
      }
    }
    if (alreadyLoaded) return;
    try {
      final detail = await _api.getRun(run.id);
      // A second row clicked while this one was in flight owns the panel now.
      if (!mounted || _selectedRun?.id != run.id) return;
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

  /// The one thing the canvas shows that a journal cannot: which branch this
  /// run took through the graph.
  Future<void> _traceOnCanvas(FlowRunSummary run) async {
    setState(() => _tab = 1);
    await _selectRun(run, trace: true);
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
                        'Runs',
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
            2 => PageBody(
              padding: const EdgeInsets.fromLTRB(14, 16, 28, 48),
              children: _runHistoryBody(),
            ),
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
              _tracing = false;
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

  /// One subject: the step you clicked. The run history is a tab of its own
  /// now, so nothing here competes with the step for the column — and when a
  /// run is being traced, its journal entry for that step follows the step's
  /// definition instead of hiding behind a second row of tabs.
  Widget _inspectorColumn() {
    return ColoredBox(
      color: CumulusColors.surface,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (_tracing && _selectedRun != null) ...[
            _traceBanner(),
            const Divider(height: 1),
          ],
          Expanded(child: _definitionPanel()),
        ],
      ),
    );
  }

  /// The canvas is painting one execution's path; this says which, and gives
  /// the way out. Without it the highlighted path is an unexplained colour.
  Widget _traceBanner() {
    final run = _selectedRun!;
    return Container(
      color: CumulusColors.surfaceMuted.withValues(alpha: 0.6),
      padding: const EdgeInsets.fromLTRB(16, 10, 8, 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              const Icon(
                Icons.route_outlined,
                size: 16,
                color: CumulusColors.inkMuted,
              ),
              const SizedBox(width: 8),
              Flexible(
                child: Text(
                  run.id,
                  overflow: TextOverflow.ellipsis,
                  style: CumulusText.mono.copyWith(
                    fontSize: 13,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ),
              const SizedBox(width: 8),
              StatusBadge(
                run.status,
                tone: runTone(run.status),
                dense: true,
                uppercase: true,
              ),
              const Spacer(),
              IconButton(
                tooltip: 'Stop tracing this run',
                visualDensity: VisualDensity.compact,
                icon: const Icon(Icons.close, size: 16),
                onPressed: () => setState(() => _tracing = false),
              ),
            ],
          ),
          Padding(
            padding: const EdgeInsets.only(left: 24, right: 8, top: 2),
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

  Widget _definitionPanel() {
    final step = _selectedStep;
    if (step == null) {
      return CumulusEmptyState(
        icon: Icons.touch_app_outlined,
        title: 'Select a step',
        message: _tracing && _selectedRun != null
            ? 'Click any node on the traced path to see its configuration and '
                  'what it read, wrote and returned in ${_selectedRun!.id}.'
            : 'Click any node on the canvas to see its exported configuration '
                  '— the model it calls, the threshold it checks, where it '
                  'routes.',
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
          if (_tracing && _selectedRun != null) ...[
            const SizedBox(height: 22),
            const Divider(height: 1),
            const SizedBox(height: 16),
            Text('IN THIS RUN', style: CumulusText.statLabel),
            const SizedBox(height: 10),
            _stepInRun(step),
          ],
        ],
      ),
    );
  }

  /// What the traced run did at this step. A looped node has one journal
  /// entry per round; the newest is the state the canvas is painting, so it
  /// is the one whose payload belongs here.
  Widget _stepInRun(FlowStep step) {
    if (_loadingRun) return const CumulusLoading();
    final run = _runDetail;
    if (run == null) {
      return CumulusEmptyState(
        icon: Icons.error_outline,
        title: 'Journal unavailable',
        message:
            'The execution trail for ${_selectedRun?.id ?? 'this run'} could '
            'not be loaded.',
        action: OutlinedButton(
          onPressed: () => _selectRun(_selectedRun, trace: true),
          child: const Text('Retry'),
        ),
      );
    }
    final nodeIds = {for (final d in _canvasSteps) d.step.id};
    final entry = run.steps
        .where((s) => RunPath.canvasNode(s.stepId, nodeIds) == step.id)
        .lastOrNull;
    if (entry == null) {
      return CumulusEmptyState(
        icon: Icons.alt_route,
        title: 'Not in this run',
        message:
            '${step.id} is not part of ${run.id} — the run took a different '
            'branch.',
      );
    }
    return _StepRunDetail(step: entry, runId: run.id);
  }

  // ---- Tab 3: run history -------------------------------------------------

  /// Deep enough that the tab is the whole history rather than the last
  /// screenful of it, and inside the endpoint's own ceiling of 500.
  static const int _runHistoryLimit = 300;

  List<FlowRunSummary> get _visibleRuns => filterRuns(
    _runs,
    status: _runStatus,
    window: _runWindow,
    query: _runSearch.text,
  );

  List<Widget> _runHistoryBody() {
    final rows = _visibleRuns;
    return [
      _runFilterBar(),
      ListCard(
        title: 'Run history',
        subtitle: _runs.isEmpty
            ? 'this definition has never executed'
            : '${rows.length} of ${_runs.length} run'
                  '${_runs.length == 1 ? '' : 's'} · newest first · '
                  'click one for its journal',
        headerTrailing: TextButton.icon(
          onPressed: _reloadRuns,
          icon: const Icon(Icons.refresh, size: 15),
          label: const Text('Refresh'),
        ),
        children: [
          if (_runs.isEmpty)
            const CumulusEmptyState(
              icon: Icons.play_circle_outline,
              title: 'No runs yet',
              message: 'This definition has never executed.',
            )
          else if (rows.isEmpty)
            CumulusEmptyState(
              icon: Icons.search_off,
              title: 'Nothing matches',
              message:
                  'No run matches the current filters. Clear the search, the '
                  'status lens or the time window to widen the history.',
              action: OutlinedButton(
                onPressed: _clearRunFilters,
                child: const Text('Clear filters'),
              ),
            )
          else
            for (final run in rows) ...[
              _runHistoryRow(run),
              if (_selectedRun?.id == run.id)
                _loadingRun
                    ? const Padding(
                        padding: EdgeInsets.symmetric(vertical: 18),
                        child: CumulusLoading(),
                      )
                    : RunJournalView(api: _api, run: _runDetail),
            ],
        ],
      ),
    ];
  }

  /// Search, status and time on one row. The status counts are taken after
  /// the time window but before the search, so a chip's number keeps meaning
  /// "how many runs are in this state" while you type rather than counting
  /// only what the query has already matched.
  Widget _runFilterBar() {
    final span = _runWindow.span;
    final now = DateTime.now();
    final inWindow = span == null
        ? _runs
        : _runs.where((run) => runWithin(run, now, span)).toList();
    final counts = <String, int>{};
    for (final run in inWindow) {
      counts[run.status] = (counts[run.status] ?? 0) + 1;
    }
    // The four states runs.py stores lead, in that order; anything else the
    // history holds still gets a lens rather than being unreachable. A lens
    // that is selected stays on screen even once its count falls to zero —
    // otherwise the only chip you could use to undo the filter vanishes.
    const known = ['succeeded', 'suspended', 'failed', 'running'];
    final present = <String>{
      ...counts.keys,
      if (_runStatus != null) _runStatus!,
    };
    final statuses = [
      for (final status in known)
        if (present.contains(status)) status,
      for (final status in present)
        if (!known.contains(status)) status,
    ];
    return Wrap(
      spacing: 8,
      runSpacing: 8,
      crossAxisAlignment: WrapCrossAlignment.center,
      children: [
        SizedBox(
          width: 340,
          child: TextField(
            controller: _runSearch,
            decoration: InputDecoration(
              hintText: 'Search run id, case, actor, step, error…',
              prefixIcon: const Icon(Icons.search, size: 16),
              suffixIcon: _runSearch.text.isEmpty
                  ? null
                  : IconButton(
                      icon: const Icon(Icons.close, size: 14),
                      onPressed: _runSearch.clear,
                    ),
            ),
          ),
        ),
        LensChip(
          label: 'All · ${inWindow.length}',
          selected: _runStatus == null,
          onTap: () => setState(() => _runStatus = null),
        ),
        for (final status in statuses)
          LensChip(
            label: '$status · ${counts[status] ?? 0}',
            tone: runTone(status),
            selected: _runStatus == status,
            onTap: () => setState(
              () => _runStatus = _runStatus == status ? null : status,
            ),
          ),
        DropdownButtonHideUnderline(
          child: DropdownButton<RunWindow>(
            value: _runWindow,
            style: const TextStyle(fontSize: 13, color: CumulusColors.ink),
            borderRadius: BorderRadius.circular(CumulusRadius.control),
            items: [
              for (final window in RunWindow.values)
                DropdownMenuItem<RunWindow>(
                  value: window,
                  child: Text(window.label),
                ),
            ],
            onChanged: (window) =>
                setState(() => _runWindow = window ?? RunWindow.all),
          ),
        ),
      ],
    );
  }

  void _clearRunFilters() {
    setState(() {
      _runStatus = null;
      _runWindow = RunWindow.all;
    });
    // Clearing the field fires the listener, which rebuilds.
    _runSearch.clear();
  }

  /// The history is the one part of this screen that goes stale while you
  /// read it — a run can land at any moment — so it refetches on its own,
  /// without pulling the definition, its versions and its telemetry with it.
  Future<void> _reloadRuns() async {
    try {
      final runs = await _api.listRuns(
        flowId: widget.flowId,
        limit: _runHistoryLimit,
      );
      if (!mounted) return;
      setState(() => _runs = runs);
    } on ApiException catch (e) {
      _snack(e.message);
    }
  }

  Widget _runHistoryRow(FlowRunSummary run) {
    final tone = runTone(run.status);
    final open = _selectedRun?.id == run.id;
    return CumulusListRow(
      onTap: () => _selectRun(open ? null : run),
      leading: Container(
        width: 34,
        height: 34,
        alignment: Alignment.center,
        decoration: BoxDecoration(
          color: tone.background,
          borderRadius: BorderRadius.circular(10),
        ),
        child: Icon(
          switch (run.status) {
            'succeeded' => Icons.check,
            'suspended' => Icons.pause,
            'failed' => Icons.priority_high,
            _ => Icons.play_arrow,
          },
          size: 17,
          color: tone.foreground,
        ),
      ),
      title: Wrap(
        crossAxisAlignment: WrapCrossAlignment.center,
        spacing: 8,
        runSpacing: 4,
        children: [
          Text(_runSubject(run).isEmpty ? run.id : _runSubject(run)),
          StatusBadge(run.status, tone: tone, dense: true, uppercase: true),
          if (run.status == 'suspended' && run.suspendedAtStep.isNotEmpty)
            StatusBadge('at ${run.suspendedAtStep}', dense: true),
          if (run.openTaskId.isNotEmpty)
            StatusBadge('task open', tone: CumulusTone.accent, dense: true),
          if (run.resumeCount > 0)
            Tooltip(
              message:
                  'The journal was re-entered ${run.resumeCount} time(s); '
                  'completed steps replayed rather than re-running.',
              child: StatusBadge(
                '${run.resumeCount}× resumed',
                tone: CumulusTone.accent,
                dense: true,
              ),
            ),
        ],
      ),
      subtitle: Text(
        '${run.id} · ${run.steps} step${run.steps == 1 ? '' : 's'}'
        ' · ${run.modelCalls} ModelCall${run.modelCalls == 1 ? '' : 's'}'
        ' · ${run.refs.length} governed read${run.refs.length == 1 ? '' : 's'}'
        '${run.costUsd > 0 ? ' · ${money(run.costUsd)}' : ''}'
        '${run.actor.isEmpty ? '' : ' · ${run.actor}'}'
        '${run.error.isEmpty ? '' : ' · ${run.error}'}',
        maxLines: 2,
        overflow: TextOverflow.ellipsis,
      ),
      trailing: [
        SizedBox(
          width: 92,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Text(
                agoIso(run.startedAtIso),
                style: const TextStyle(
                  fontSize: 13,
                  fontWeight: FontWeight.w600,
                  color: CumulusColors.ink,
                ),
              ),
              Text(
                // Every seeded run reports 0ms, and printing that claims a
                // measurement of zero rather than none.
                run.durationMs <= 0 ? 'not timed' : '${run.durationMs}ms',
                style: CumulusText.statCaption,
              ),
            ],
          ),
        ),
        IconButton(
          tooltip: 'Trace this run on the canvas',
          visualDensity: VisualDensity.compact,
          icon: const Icon(Icons.account_tree_outlined, size: 17),
          color: CumulusColors.inkMuted,
          onPressed: () => _traceOnCanvas(run),
        ),
        Icon(
          open ? Icons.expand_less : Icons.expand_more,
          size: 20,
          color: CumulusColors.inkMuted,
        ),
      ],
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
        _tracing = false;
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
