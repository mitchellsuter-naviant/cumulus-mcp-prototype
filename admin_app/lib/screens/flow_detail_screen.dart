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
import '../widgets/ref_chips.dart';
import '../widgets/run_workbench.dart'
    show RunJournalView, runTone, stepDocuments;
import '../widgets/side_drawer.dart';
import '../widgets/skeleton.dart';
import '../widgets/ui_kit.dart';
import '../widgets/flow_canvas.dart';
import 'flows_screen.dart'
    show
        FleetCol,
        FleetGridHead,
        MetricTile,
        StickyGridHeader,
        WindowSelector,
        dayLabel,
        gridFigures,
        healthLabel,
        healthTone,
        money,
        percent,
        statusTone,
        thousands;

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
  runs('Runs / day', 'execution volume'),
  spend('Model spend / day', 'from audited ModelCalls'),
  errors('Errors / day', 'runs that ended in failure'),
  judgment('Human judgment / day', 'work routed to a person');

  final String label;

  /// The half-sentence under the plot's title. Each measure has to say what
  /// it counts, because four of them share one axis and only one is on screen.
  final String caption;

  const ChartMeasure(this.label, this.caption);
}

/// How far back the run history reaches. The whole history arrives in one
/// fetch, newest first, so a window is a cut on the list already in hand
/// rather than another round trip.
///
/// The three long windows are the console's global timeframes, so the run log
/// opens over the same span the hero bar's figures are measured over; the
/// short ones are the narrowing a reader does once they are already here.
enum RunWindow {
  all('All time', null),
  day('Last 24 hours', Duration(hours: 24)),
  week('Last 7 days', Duration(days: 7)),
  month('Last 30 days', Duration(days: 30)),
  twoMonths('Last 60 days', Duration(days: 60)),
  quarter('Last 90 days', Duration(days: 90));

  final String label;
  final Duration? span;

  const RunWindow(this.label, this.span);

  /// The window matching the page's global timeframe. An unrecognised span
  /// falls back to 30 days rather than to `all`: the header would otherwise
  /// be stating figures over a window the log beneath it did not share.
  static RunWindow forDays(int days) => switch (days) {
    60 => twoMonths,
    90 => quarter,
    _ => month,
  };
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

/// Which stored version a run's path should be painted over, and what has to
/// be said when it cannot be the one that ran.
class TraceTarget {
  /// The version to put on the canvas. Null leaves the canvas where it is —
  /// there is nothing better to move it to.
  final int? version;

  /// Empty when the canvas is showing the graph this run actually executed.
  /// Otherwise the sentence the trace banner has to carry, because the path
  /// is being drawn over a stand-in.
  final String caveat;

  const TraceTarget({this.version, this.caveat = ''});

  bool get exact => caveat.isEmpty;
}

/// Pick the canvas a run's path belongs on.
///
/// A run pins its `flow_version` at start and the engine honours it on
/// resume, so an execution is evidence about that graph and no other. Tracing
/// over the *active* version instead — which is what this page did — paints a
/// v6 run on v7's canvas: same node ids, different graph, and no sign
/// anything is wrong. So:
///
/// - the version the run names, when the history still holds it;
/// - otherwise the active version, with the substitution stated. A definition
///   that has been pruned is a real gap in the record, and a silent stand-in
///   would turn it into a claim about a branch nobody can check.
///
/// Version 0 is not a version. It is a run written before the field existed,
/// and reading it as "the active one" would be the console inventing a fact
/// about an execution it did not watch.
TraceTarget traceTarget({
  required int runVersion,
  required List<FlowVersion> versions,
  required int? selected,
}) {
  if (versions.isEmpty) return const TraceTarget();
  final active = versions.where((v) => v.isActive).firstOrNull;
  final fallback = active?.version ?? selected ?? versions.first.version;
  final stand = versions.where((v) => v.version == fallback).firstOrNull;
  final standIn = stand?.display ?? 'v$fallback';
  if (runVersion <= 0) {
    return TraceTarget(
      version: fallback,
      caveat:
          'This run recorded no version — it started before the journal '
          'pinned one. The path below is drawn over $standIn, which may not '
          'be the graph it ran.',
    );
  }
  if (versions.any((v) => v.version == runVersion)) {
    return TraceTarget(version: runVersion);
  }
  return TraceTarget(
    version: fallback,
    caveat:
        'This run executed v$runVersion, which is no longer in this flow’s '
        'stored history. The path below is drawn over $standIn — a different '
        'definition, so a node it crossed may be missing here or may mean '
        'something else.',
  );
}

/// One definition, in four views: what it is and how it is doing, the graph,
/// every run it has ever had, and how a candidate version measures up.
class FlowDetailScreen extends StatefulWidget {
  final String flowId;
  final String myRole;
  final VoidCallback onBack;

  /// Which of the four views opens first. The fleet grid's row actions are
  /// the reason this exists: "open the canvas" and "open the run log" are
  /// different destinations, and landing both on Overview would make the
  /// operator take the second click the action was there to save.
  final int initialTab;

  /// The window this flow's usage is measured over, carried in from the fleet
  /// page's global selector. Drilling into a row keeps the span the operator
  /// was reading the estate over, so the numbers here and the numbers on the
  /// row they clicked cover the same days.
  final int days;

  /// Changing the window here changes it for the fleet too. There is one
  /// timeframe for the console, not a per-screen preference: coming back to a
  /// list measured over a different span than the page you just left is how a
  /// reader ends up comparing two windows without noticing.
  final ValueChanged<int> onDaysChanged;

  /// Told when the reader changes tab, so the address bar can follow them.
  /// The tab stays this screen's own state — this reports it rather than
  /// surrendering it, which is what keeps the screen usable with no router
  /// attached at all.
  final ValueChanged<int>? onTabChanged;

  const FlowDetailScreen({
    super.key,
    required this.flowId,
    required this.myRole,
    required this.onBack,
    this.initialTab = 0,
    this.days = 30,
    this.onDaysChanged = _ignoreDays,
    this.onTabChanged,
  });

  static void _ignoreDays(int _) {}

  @override
  State<FlowDetailScreen> createState() => _FlowDetailScreenState();
}

class _FlowDetailScreenState extends State<FlowDetailScreen> {
  final ApiClient _api = ApiClient();

  FlowDetail? _detail;
  FlowUsage? _usage;
  List<FlowRunSummary> _runs = [];
  List<FlowVersion> _versions = [];

  /// What this flow is wired to: models, ontology, collections, desks. Not
  /// windowed, so it survives a change of timeframe untouched.
  FlowInventory? _inventory;

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

  /// Set when the canvas could not be moved onto the version the traced run
  /// executed. The path is still worth drawing — the banner says what it is
  /// drawn over, because an unannounced stand-in is the failure this whole
  /// mechanism exists to stop.
  String _traceCaveat = '';

  /// Run-history filters. The history is fetched once and cut in the client —
  /// a few hundred rows is a list to narrow, not a query to re-issue.
  final TextEditingController _runSearch = TextEditingController();
  String? _runStatus;
  late RunWindow _runWindow = RunWindow.forDays(widget.days);

  /// The query the log is actually cut by, which trails the field by a
  /// debounce.
  String _runQuery = '';
  Timer? _runSearchDebounce;

  /// Long enough to swallow a burst of typing, short enough that a pause
  /// reads as instant.
  static const _runSearchDelay = Duration(milliseconds: 150);

  /// The run log's own scroll, so its pinned head can lift off the rows once
  /// they start passing underneath — the fleet grid's behaviour, on the fleet
  /// grid's delegate.
  final ScrollController _runScroll = ScrollController();

  /// Watched by the pinned block alone. As a field it rebuilt this whole
  /// screen — four tabs and three hundred rows — to move one shadow.
  final ValueNotifier<bool> _runHeadLifted = ValueNotifier(false);

  /// Which run row the pointer is over, held once here rather than as a
  /// `State` on every row in the log.
  final ValueNotifier<String?> _hoveredRun = ValueNotifier(null);

  ChartMeasure _measure = ChartMeasure.runs;

  /// Set while a version activation is in flight, so the control cannot be
  /// pressed twice onto the same publish.
  bool _activatingVersion = false;

  bool _loading = true;
  String? _error;
  late int _tab = widget.initialTab;

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
    _runSearch.addListener(_onRunSearchChanged);
    _runScroll.addListener(_onRunScroll);
    _refresh();
  }

  @override
  void dispose() {
    _runSearchDebounce?.cancel();
    _runSearch.dispose();
    _runScroll.dispose();
    _runHeadLifted.dispose();
    _hoveredRun.dispose();
    _api.close();
    super.dispose();
  }

  /// The first character of a burst is applied on the frame it was typed in
  /// and the rest are coalesced, so the history is re-cut twice for a word
  /// rather than once per keystroke.
  void _onRunSearchChanged() {
    if (_runSearchDebounce?.isActive ?? false) {
      _runSearchDebounce!.cancel();
    } else {
      _applyRunQuery();
    }
    _runSearchDebounce = Timer(_runSearchDelay, _applyRunQuery);
  }

  void _applyRunQuery() {
    if (!mounted || _runSearch.text == _runQuery) return;
    setState(() => _runQuery = _runSearch.text);
  }

  /// The window moved. Only the telemetry is measured over it — the
  /// definition, its versions and its history are not — so only the telemetry
  /// is refetched, and the run log simply re-cuts the list it already holds.
  @override
  void didUpdateWidget(FlowDetailScreen old) {
    super.didUpdateWidget(old);
    if (old.initialTab != widget.initialTab &&
        widget.initialTab != _tab) {
      setState(() => _tab = widget.initialTab);
    }
    if (old.days == widget.days) return;
    setState(() => _runWindow = RunWindow.forDays(widget.days));
    _reloadUsage();
  }

  /// A few pixels of travel, not one, and only the pinned block hears about
  /// it: the notifier already swallows a value it is already holding.
  void _onRunScroll() =>
      _runHeadLifted.value = _runScroll.hasClients && _runScroll.offset > 8;

  Future<void> _reloadUsage() async {
    try {
      final usage = await _api.flowUsage(widget.flowId, days: widget.days);
      if (!mounted) return;
      setState(() => _usage = usage);
    } on ApiException catch (e) {
      _snack(e.message);
    }
  }

  /// The coverage figure is a nicety on a card of measurements. A flow with
  /// no spec, or a corpus that refuses the read, must not take the page down
  /// with it — so it is fetched beside the page rather than inside its load.
  /// The inventory is a panel on one tab; a corpus that refuses the read must
  /// not take the page down with it.
  Future<void> _loadInventory() async {
    try {
      final inventory = await _api.flowInventory(widget.flowId);
      if (!mounted) return;
      setState(() => _inventory = inventory);
    } on ApiException catch (_) {}
  }

  Future<void> _refresh() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    final id = widget.flowId;
    // All four go out at once and each is read back as itself. A positional
    // cast out of the results list is one reordering away from claiming a
    // usage is a definition, and nothing would say so until it crashed.
    final detailCall = _api.getFlow(id);
    final usageCall = _api.flowUsage(id, days: widget.days);
    final runsCall = _api.listRuns(flowId: id, limit: _runHistoryLimit);
    final versionsCall = _api.flowVersions(id);
    try {
      // Settled together before any one of them is read: awaiting the first
      // and never reaching the second is how the second's failure becomes an
      // unhandled error in the zone.
      await Future.wait([detailCall, usageCall, runsCall, versionsCall]);
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.message;
        _loading = false;
      });
      return;
    }
    if (!mounted) return;
    final detail = await detailCall;
    final usage = await usageCall;
    final runs = await runsCall;
    final versions = await versionsCall;
    setState(() {
      _detail = detail;
      _usage = usage;
      _runs = runs;
      _versions = versions;
      _selectedVersion ??= (versions.where((v) => v.isDraft).firstOrNull ??
              versions.where((v) => v.isActive).firstOrNull)
          ?.version;
      _loading = false;
    });
    unawaited(_loadInventory());
    await _loadCanvas();
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

  /// Make the selected version the live one.
  ///
  /// Only offered for a version the deployed code ships. A definition and the
  /// package that executes it are two artifacts, and making live a version
  /// nothing can run would point the fleet at an empty shelf — so the button
  /// is absent rather than failing, and the server checks it again anyway.
  ///
  /// Both the detail and the version list are re-read afterwards: activation
  /// moves a status off whichever version held it, and re-reading is the only
  /// way the list says so rather than the console guessing.
  Future<void> _activateVersion(FlowVersion version) async {
    setState(() => _activatingVersion = true);
    final activation = _api.activateFlowVersion(widget.flowId, version.version);
    final versionsCall = _api.flowVersions(widget.flowId);
    try {
      // Settled together, then read one at a time as itself — the same reason
      // the page load does it that way.
      await Future.wait([activation, versionsCall]);
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() => _activatingVersion = false);
      _snack(e.message);
      return;
    }
    if (!mounted) return;
    final detail = await activation;
    final versions = await versionsCall;
    setState(() {
      _detail = detail;
      _versions = versions;
      _activatingVersion = false;
    });
    _snack('${version.display} is live — new runs take this definition.');
    await _loadCanvas();
  }

  /// Open a run's journal. With [trace] the canvas paints its path too, and
  /// because a path belongs to the version that ran, that — and only that —
  /// moves the canvas off whatever it was showing and onto the run's own
  /// definition.
  Future<void> _selectRun(FlowRunSummary? run, {bool trace = false}) async {
    if (run == null) {
      setState(() {
        _selectedRun = null;
        _runDetail = null;
        _tracing = false;
        _traceCaveat = '';
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
      final target = traceTarget(
        runVersion: run.flowVersion,
        versions: _versions,
        selected: _selectedVersion,
      );
      setState(() => _traceCaveat = target.caveat);
      if (target.version != null && _selectedVersion != target.version) {
        setState(() {
          _selectedVersion = target.version;
          // The node the reader had open belongs to the version they were
          // looking at, not to this one.
          _selectedStepId = null;
        });
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
    _goToTab(1);
    await _selectRun(run, trace: true);
  }

  @override
  Widget build(BuildContext context) {
    if (_error != null && _detail == null) {
      return PageBody(
        children: [
          BackLink(label: 'All flows', onTap: widget.onBack),
          ErrorNotice(message: _error!, onRetry: _refresh),
        ],
      );
    }
    if (_detail == null) {
      // The chrome is waiting on nothing: the id came in on the route that
      // opened this page, and the four tabs are the same four whatever the
      // fetch comes back with.
      return Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          _heroBarSkeleton(),
          _tabStrip(),
          Expanded(child: _skeletonBody()),
        ],
      );
    }
    final w = _detail!;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _heroBar(w),
        _tabStrip(),
        Expanded(
          child: switch (_tab) {
            0 => PageBody(
              padding: const EdgeInsets.fromLTRB(14, 16, 28, 48),
              children: _overviewBody(w),
            ),
            1 => _canvasBody(),
            2 => _runsBody(),
            _ => PageBody(
              padding: const EdgeInsets.fromLTRB(14, 16, 28, 48),
              children: [EvalPanel(api: _api, flowId: widget.flowId)],
            ),
          },
        ),
      ],
    );
  }

  // ---- the header ---------------------------------------------------------

  /// The top line: where you are, whether it is well, and what acts on the
  /// definition.
  static const double _heroHeight = 54;

  /// The baseline: navigation, and the four figures right-aligned against it.
  static const double _tabStripHeight = 44;

  /// Unboxed. This used to be a white band with a hairline under it — a card
  /// in all but name, sitting on the canvas and boxing the one part of the
  /// page that is not content. Now the title, the badges and the controls sit
  /// directly on the page ground, and the only rule on screen is the one the
  /// tabs stand on, which is a rule that means something.
  ///
  /// It is deliberately not the fleet page's header. That page names a place
  /// and offers one control; this one names a thing, says how it is, and acts
  /// on it — so it carries badges and verbs where the other carries a
  /// sentence.
  Widget _heroBar(FlowDetail w) {
    return PageGutter(
      padding: const EdgeInsets.fromLTRB(6, 8, 20, 0),
      child: SizedBox(
        height: _heroHeight,
        child: Row(
          children: [
            // One Expanded for everything on the left. A `Flexible` title and
            // a `Spacer` both default to flex 1, so they used to split the
            // free space between them and leave the controls stranded at the
            // middle of the row.
            Expanded(
              child: Row(
                children: [
                  IconButton(
                    tooltip: 'All flows',
                    onPressed: widget.onBack,
                    visualDensity: VisualDensity.compact,
                    icon: const Icon(Icons.arrow_back, size: 18),
                  ),
                  Flexible(
                    child: Text(
                      w.id,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: CumulusText.mono.copyWith(
                        fontSize: 17,
                        fontWeight: FontWeight.w600,
                        letterSpacing: -0.2,
                      ),
                    ),
                  ),
                  const SizedBox(width: 12),
                  StatusBadge(
                    healthLabel(w.health),
                    tone: healthTone(w.health),
                    dense: true,
                    uppercase: true,
                  ),
                  // Only when it is not the ordinary case. An active flow says
                  // so through the Pause control at the other end of this row.
                  if (w.status != 'active') ...[
                    const SizedBox(width: 6),
                    StatusBadge(
                      w.status,
                      tone: statusTone(w.status),
                      dense: true,
                      uppercase: true,
                    ),
                  ],
                ],
              ),
            ),
            const SizedBox(width: 12),
            WindowSelector(
              days: widget.days,
              onChanged: widget.onDaysChanged,
            ),
            const SizedBox(width: 6),
            ..._quickControls(w),
          ],
        ),
      ),
    );
  }

  /// The hero bar with the definition still in flight.
  ///
  /// The id is the one fact the route already carries, so it is set as itself
  /// rather than as a grey bar — and it does not move when the fetch lands.
  /// Only the verdict badges are waiting on an answer.
  Widget _heroBarSkeleton() {
    return PageGutter(
      padding: const EdgeInsets.fromLTRB(6, 8, 20, 0),
      child: SizedBox(
        height: _heroHeight,
        child: Row(
          children: [
            Expanded(
              child: Row(
                children: [
                  IconButton(
                    tooltip: 'All flows',
                    onPressed: widget.onBack,
                    visualDensity: VisualDensity.compact,
                    icon: const Icon(Icons.arrow_back, size: 18),
                  ),
                  Flexible(
                    child: Text(
                      widget.flowId,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: CumulusText.mono.copyWith(
                        fontSize: 17,
                        fontWeight: FontWeight.w600,
                        letterSpacing: -0.2,
                      ),
                    ),
                  ),
                  const SizedBox(width: 12),
                  const SkeletonPulse(child: SkeletonPill(width: 76)),
                ],
              ),
            ),
            const SizedBox(width: 12),
            WindowSelector(days: widget.days, onChanged: widget.onDaysChanged),
            const SizedBox(width: 6),
            const IconButton(
              tooltip: 'Refreshing…',
              onPressed: null,
              visualDensity: VisualDensity.compact,
              icon: SizedBox(
                width: 17,
                height: 17,
                child: CircularProgressIndicator(strokeWidth: 2),
              ),
            ),
          ],
        ),
      ),
    );
  }

  /// The body of whichever tab the reader landed on, shaped like what is
  /// coming. Landing on Runs and being shown an overview's geometry would be
  /// a worse lie than a spinner.
  Widget _skeletonBody() => switch (_tab) {
    1 => _canvasSkeleton(),
    2 => _runsSkeleton(),
    // The evaluation panel fetches its own spec off the flow id and knows how
    // to wait for it, so it is the real thing from the first frame.
    3 => PageBody(
      padding: const EdgeInsets.fromLTRB(14, 16, 28, 48),
      children: [EvalPanel(api: _api, flowId: widget.flowId)],
    ),
    _ => _overviewSkeleton(),
  };

  /// Two columns of cards, at the Overview's own proportions.
  Widget _overviewSkeleton() => PageBody(
    padding: const EdgeInsets.fromLTRB(14, 16, 28, 48),
    children: [
      SkeletonPulse(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          spacing: 16,
          children: [
            const SkeletonCard(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  SkeletonBar(width: 240, height: 13),
                  SizedBox(height: 12),
                  SkeletonBar(height: 11),
                  SizedBox(height: 8),
                  SkeletonBar(width: 380, height: 11),
                ],
              ),
            ),
            LayoutBuilder(
              builder: (context, constraints) {
                const left = Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  spacing: 16,
                  children: [
                    SkeletonListCard(rows: 5, leadingCircle: false),
                    SkeletonListCard(rows: 3, leadingCircle: false),
                  ],
                );
                const right = Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  spacing: 16,
                  children: [
                    SkeletonCard(
                      padding: EdgeInsets.all(16),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          SkeletonBar(width: 128, height: 12),
                          SizedBox(height: 14),
                          SkeletonBar(
                            height: 148,
                            radius: CumulusRadius.control,
                            color: CumulusColors.surfaceMuted,
                          ),
                        ],
                      ),
                    ),
                    SkeletonListCard(rows: 4, leadingCircle: false),
                  ],
                );
                if (constraints.maxWidth < 1040) {
                  return const Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    spacing: 16,
                    children: [left, right],
                  );
                }
                return const Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  spacing: 16,
                  children: [
                    Expanded(flex: 35, child: left),
                    Expanded(flex: 65, child: right),
                  ],
                );
              },
            ),
            const SkeletonCard(
              child: SkeletonTableRows(rows: 4, columnFlex: [3, 3, 2, 2]),
            ),
          ],
        ),
      ),
    ],
  );

  Widget _canvasSkeleton() => const Padding(
    padding: EdgeInsets.fromLTRB(14, 14, 28, 24),
    child: SkeletonPulse(child: SkeletonCanvas(height: double.infinity)),
  );

  /// The log's own pinned block — the search, the window and the column
  /// captions are all real — over rows of geometry.
  Widget _runsSkeleton() {
    return LayoutBuilder(
      builder: (context, constraints) {
        final width = constraints.maxWidth;
        return SingleChildScrollView(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const SizedBox(height: 14),
              PageGutter(
                padding: const EdgeInsets.fromLTRB(14, 0, 28, 0),
                child: Container(
                  decoration: const BoxDecoration(
                    color: CumulusColors.surface,
                    borderRadius: BorderRadius.vertical(
                      top: Radius.circular(CumulusRadius.card),
                    ),
                    border: Border.fromBorderSide(
                      BorderSide(color: CumulusColors.border),
                    ),
                  ),
                  child: _runControlsAndHead(
                    0,
                    RunColumns.forWidth(width),
                    width,
                    waiting: true,
                  ),
                ),
              ),
              PageGutter(
                padding: const EdgeInsets.fromLTRB(14, 0, 28, 0),
                child: Container(
                  decoration: const BoxDecoration(
                    color: CumulusColors.surface,
                    border: Border(
                      left: BorderSide(color: CumulusColors.border),
                      right: BorderSide(color: CumulusColors.border),
                      bottom: BorderSide(color: CumulusColors.border),
                    ),
                  ),
                  child: const SkeletonPulse(
                    child: Padding(
                      padding: EdgeInsets.symmetric(
                        horizontal: FleetCol.rowPadding - 4,
                      ),
                      child: SkeletonTableRows(
                        rows: 12,
                        columnFlex: [3, 8, 3, 2, 2, 2],
                      ),
                    ),
                  ),
                ),
              ),
            ],
          ),
        );
      },
    );
  }

  /// Navigation on a rule, with the four figures standing on the same
  /// baseline.
  ///
  /// Underlines rather than the fleet page's pills: this is a place inside one
  /// subject, not a filter over a list, and reusing the pill would have said
  /// the two rows do the same kind of thing. The rule is also the page's only
  /// horizontal division, so the header needs no box to be separate from the
  /// body.
  Widget _tabStrip() {
    return PageGutter(
      padding: const EdgeInsets.fromLTRB(14, 0, 28, 0),
      child: Container(
        height: _tabStripHeight,
        decoration: const BoxDecoration(
          border: Border(
            bottom: BorderSide(color: CumulusColors.borderStrong),
          ),
        ),
        // Tabs alone. The four figures used to stand on this baseline, and
        // they said exactly what the metric tiles and the plot below them
        // already say — on the one tab where those live, and to nobody's
        // benefit on the other three.
        child: Align(
          alignment: Alignment.centerLeft,
          child: _UnderlineTabs(
            labels: const ['Overview', 'Flow canvas', 'Runs', 'Evaluation'],
            selectedIndex: _tab,
            onChanged: _goToTab,
          ),
        ),
      ),
    );
  }

  /// The controls that act on the definition itself.
  ///
  /// There is no "trigger run" here. Nothing in the REST contract starts a
  /// run — every flow in this estate is dispatched by an event — and a button
  /// that could only pretend to start one is worse than no button.
  List<Widget> _quickControls(FlowDetail w) {
    final canPause = _isAdmin && w.status == 'active';
    final canActivate =
        _isAdmin && w.status != 'active' && w.status != 'deprecated';
    return [
      if (canPause)
        _HeroPill(
          icon: Icons.pause,
          label: 'Pause',
          tooltip: 'Stop dispatching runs to this definition',
          onTap: () => _setStatus('paused'),
        ),
      if (canActivate)
        _HeroPill(
          icon: Icons.play_arrow,
          label: 'Activate',
          tooltip: 'Let this definition take events again',
          onTap: () => _setStatus('active'),
        ),
      if (canPause || canActivate) const SizedBox(width: 2),
      // A refetch over a page already on screen is said here, at the size of
      // the control that started it, rather than by the page going blank.
      IconButton(
        tooltip: _loading ? 'Refreshing…' : 'Refresh',
        onPressed: _loading ? null : _refresh,
        visualDensity: VisualDensity.compact,
        icon: _loading
            ? const SizedBox(
                width: 17,
                height: 17,
                child: CircularProgressIndicator(strokeWidth: 2),
              )
            : const Icon(Icons.refresh, size: 17),
      ),
    ];
  }

  void _goToTab(int tab) {
    if (tab == _tab) return;
    setState(() => _tab = tab);
    widget.onTabChanged?.call(tab);
  }

  void _selectVersion(int version) {
    setState(() {
      _selectedVersion = version;
      _selectedStepId = null;
      // A version and a run are two different things to look at; the run's
      // path belongs to the version that ran.
      _selectedRun = null;
      _runDetail = null;
      _tracing = false;
      _traceCaveat = '';
    });
    _goToTab(1);
    _loadCanvas();
  }

  /// The specification: what this flow is, and how it is wired.
  ///
  /// Opened from the Configuration & Governance card rather than from a
  /// button in the page header. A specification is read once and then
  /// skipped, so it belongs beside the other facts about the definition, not
  /// in the chrome above every tab — and as a drawer it costs the Overview
  /// nothing until somebody asks for it.
  void _openSpecDrawer(FlowDetail w) {
    final trigger = w.steps.where((s) => s.type == 'trigger').firstOrNull;
    final event = trigger?.config['event'] ?? '';
    showSideDrawer<void>(
      context: context,
      width: 640,
      title: w.id,
      subtitle: 'specification · ${w.name.isEmpty ? w.id : w.name}',
      titleBadges: [
        StatusBadge(
          healthLabel(w.health),
          tone: healthTone(w.health),
          dense: true,
          uppercase: true,
        ),
      ],
      builder: (context) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Authored in Python beside a Markdown export, so it arrives with
          // its markers on.
          Prose(w.description),
          const SizedBox(height: 16),
          Wrap(
            spacing: 8,
            runSpacing: 6,
            children: [
              if (trigger != null)
                MetaPill(
                  'Trigger · ${event.isEmpty ? trigger.name : event}',
                  tone: CumulusTone.accent,
                ),
              MetaPill('Team · ${w.team.isEmpty ? 'unassigned' : w.team}'),
              if (w.workspace.isNotEmpty)
                MetaPill('Workspace · ${w.workspace}'),
              MetaPill('Authored by ${w.authoredBy}'),
              MetaPill(
                '${w.versionCount} version${w.versionCount == 1 ? '' : 's'}',
              ),
              for (final tag in w.tags) MetaPill(tag),
            ],
          ),
          const SizedBox(height: 22),
          const Divider(height: 1),
          const SizedBox(height: 18),
          Text('STEPS', style: CumulusText.statLabel),
          const SizedBox(height: 10),
          for (final step in w.steps)
            Padding(
              padding: const EdgeInsets.only(bottom: 12),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      SizedBox(
                        width: 84,
                        child: Text(
                          step.type.toUpperCase(),
                          style: CumulusText.statLabel,
                        ),
                      ),
                      Expanded(
                        child: Text(
                          step.name,
                          style: CumulusText.mono.copyWith(fontSize: 12),
                        ),
                      ),
                    ],
                  ),
                  if (step.summary.isNotEmpty)
                    Padding(
                      padding: const EdgeInsets.only(left: 84, top: 3),
                      child: Prose(step.summary, style: CumulusText.meta),
                    ),
                ],
              ),
            ),
        ],
      ),
    );
  }

  // ---- Tab 1: overview ----------------------------------------------------

  /// Two columns, no prose. Left: what the definition *is* — config and
  /// governance, as a definition table. Right: what it has been *doing* — the
  /// trend canvas the fleet page plots, and the cost and coverage figures
  /// under it.
  ///
  /// The five-tile stat strip that used to head this tab is gone. Its four
  /// live measures are in the hero bar now, over the same window, and a page
  /// that states its headline numbers twice is a page you have to scroll.
  List<Widget> _overviewBody(FlowDetail w) {
    final usage = _usage!;
    return [
      _narrativeCard(w, usage),
      LayoutBuilder(
        builder: (context, constraints) {
          // Left is what the flow *is*; right is what it has been *doing*.
          final left = Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            spacing: 16,
            children: [_configCard(w, usage), _modelsCard()],
          );
          final right = Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            spacing: 16,
            children: [_telemetryCard(usage), _recentRunsCard()],
          );
          if (constraints.maxWidth < 1040) {
            return Column(
              spacing: 16,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [left, right],
            );
          }
          // Proportional rather than a fixed left column: the inventory's
          // chips wrap, and a column pinned to 404px kept them at three per
          // line on a monitor with room for six.
          return Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            spacing: 16,
            children: [
              Expanded(flex: 35, child: left),
              Expanded(flex: 65, child: right),
            ],
          );
        },
      ),
      // Full width, because it is a table and a table in a 35% column is
      // four ellipses in a row.
      _linksCard(),
    ];
  }

  /// Above this the tiles sit beside the plot rather than above it. Lower
  /// than the fleet page's own threshold because this card lives in the
  /// Overview's right column rather than across the whole page.
  static const double _telemetrySideBySide = 820;

  /// How tall the block is when the two halves sit side by side: two rows of
  /// tiles, and a plot as tall as both of them together.
  static const double _telemetryHeight = 184;

  /// Four measures and their daily series in one card — the block the fleet
  /// page heads with, over one definition.
  ///
  /// The tiles are the chart's only control. They replaced a dropdown that
  /// sat in the card's header and named the same four series: a picker beside
  /// a set of figures is a second set of controls to fall out of step with
  /// the first, and it hid three of the four numbers behind a click.
  Widget _telemetryCard(FlowUsage usage) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final fill = constraints.maxWidth >= _telemetrySideBySide;
        final tiles = _measureTiles(usage, fill: fill);
        final chart = _measureChart(usage, fill: fill);
        return CumulusCard(
          padding: const EdgeInsets.all(16),
          // Side by side the block has a stated height and both halves fill
          // it, which is what keeps the four tiles the same height as the plot
          // beside them. Stacked, there is no height to fill: the tiles size
          // to their content and the plot takes its own. A flex child in that
          // second case is an unbounded-constraint crash, not a layout.
          child: fill
              ? SizedBox(
                  height: _telemetryHeight,
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      SizedBox(width: 380, child: tiles),
                      const SizedBox(width: 18),
                      Expanded(child: chart),
                    ],
                  ),
                )
              : Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [tiles, const SizedBox(height: 16), chart],
                ),
        );
      },
    );
  }

  /// Two by two, so the block is as tall as the chart beside it rather than a
  /// strip running the width of the column.
  Widget _measureTiles(FlowUsage usage, {required bool fill}) {
    final tiles = [
      MetricTile(
        label: 'Runs',
        value: thousands(usage.runs),
        // The window is stated once, by the selector in the hero bar. The
        // caption gives the shape of the traffic inside it — and states the
        // count rather than a rate when the rate would round to nothing.
        caption: usage.runs == 0
            ? 'nothing ran in this window'
            : usage.runs < usage.days
            ? '${usage.runs} in ${usage.days} days'
            : '≈${(usage.runs / usage.days).toStringAsFixed(1)} per day',
        selected: _measure == ChartMeasure.runs,
        onTap: () => setState(() => _measure = ChartMeasure.runs),
      ),
      MetricTile(
        label: 'Model spend',
        value: money(usage.costUsd),
        // A spend of zero means two different things, and the caption is
        // where the tile says which: no call was made, or calls were made and
        // this is what they came to.
        caption: usage.modelCalls == 0
            ? 'nothing called a model'
            : '${compactCount(usage.modelCalls)} ModelCalls',
        selected: _measure == ChartMeasure.spend,
        onTap: () => setState(() => _measure = ChartMeasure.spend),
      ),
      // A count of what broke, not a rate. The tokens tile this replaced was
      // a number nobody acts on: it moves with volume, and the operator who
      // needs it is reading the spend tile beside it anyway.
      MetricTile(
        label: 'Errors',
        value: thousands(usage.failed),
        caption: usage.runs == 0
            ? 'nothing ran in this window'
            : usage.failed == 0
            ? 'every finished run succeeded'
            : '${percent(usage.successRate)} of finished runs succeeded',
        valueColor: usage.failed > 0 ? CumulusColors.danger : null,
        selected: _measure == ChartMeasure.errors,
        onTap: () => setState(() => _measure = ChartMeasure.errors),
      ),
      MetricTile(
        label: 'Human judgment',
        value: thousands(usage.tasksRaised),
        // Not tinted. A run that routed to a person is the design working;
        // amber here would read as a fault every time the flow did its job.
        caption: usage.runs == 0
            ? 'no runs yet'
            : '${(usage.tasksRaised * 100 / usage.runs).toStringAsFixed(1)}% '
                  'of runs',
        selected: _measure == ChartMeasure.judgment,
        onTap: () => setState(() => _measure = ChartMeasure.judgment),
      ),
    ];
    Widget pair(Widget left, Widget right) => Row(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Expanded(child: left),
        const SizedBox(width: 10),
        Expanded(child: right),
      ],
    );
    final top = pair(tiles[0], tiles[1]);
    final bottom = pair(tiles[2], tiles[3]);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      mainAxisSize: fill ? MainAxisSize.max : MainAxisSize.min,
      children: fill
          ? [
              Expanded(child: top),
              const SizedBox(height: 10),
              Expanded(child: bottom),
            ]
          : [
              IntrinsicHeight(child: top),
              const SizedBox(height: 10),
              IntrinsicHeight(child: bottom),
            ],
    );
  }

  Widget _measureChart(FlowUsage usage, {required bool fill}) {
    double valueOf(UsageDay d) => switch (_measure) {
      ChartMeasure.runs => d.runs.toDouble(),
      ChartMeasure.spend => d.costUsd,
      ChartMeasure.errors => d.failed.toDouble(),
      ChartMeasure.judgment => d.tasksRaised.toDouble(),
    };
    String format(double v) => switch (_measure) {
      ChartMeasure.spend => money(v),
      _ => v.round().toString(),
    };
    DailyBarChart plot(double height) => DailyBarChart(
      title: _measure.label,
      showTitle: false,
      height: height,
      format: format,
      points: [
        for (final d in usage.series)
          ChartPoint(
            dayLabel(d.date),
            valueOf(d),
            // Whichever measure is on screen, the tooltip carries the rest:
            // switching charts to answer "and what did that day cost?" is the
            // friction a single-measure plot would otherwise create.
            extra: [
              if (_measure != ChartMeasure.runs) '${d.runs} runs',
              if (_measure != ChartMeasure.spend)
                '${money(d.costUsd)} model spend',
              if (_measure != ChartMeasure.errors)
                '${d.failed} failed',
              if (_measure != ChartMeasure.judgment)
                '${d.tasksRaised} tasks raised',
            ],
          ),
      ],
    );
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      mainAxisSize: fill ? MainAxisSize.max : MainAxisSize.min,
      children: [
        SectionHeader(title: _measure.label, subtitle: _measure.caption),
        const SizedBox(height: 10),
        // The plot takes whatever the card's stated height leaves after the
        // caption, so the caption can wrap without the plot overrunning.
        if (fill)
          Expanded(
            child: LayoutBuilder(builder: (context, c) => plot(c.maxHeight)),
          )
        else
          plot(148),
      ],
    );
  }

  /// A definition table, not a prose block: label left, value right, a
  /// hairline between rows. Ten facts in less height than the six and a
  /// paragraph it replaced — and the paragraph was governance boilerplate
  /// that said the same thing on every flow in the estate.
  Widget _configCard(FlowDetail w, FlowUsage usage) {
    final trigger = w.steps.where((s) => s.type == 'trigger').firstOrNull;
    final event = trigger?.config['event'] ?? '';
    return SectionCard(
      title: 'Configuration & governance',
      subtitle: 'Tier-2 map asset',
      padding: const EdgeInsets.fromLTRB(18, 18, 18, 8),
      headerTrailing: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          TextButton(
            onPressed: () => _openSpecDrawer(w),
            style: TextButton.styleFrom(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
              minimumSize: Size.zero,
              tapTargetSize: MaterialTapTargetSize.shrinkWrap,
            ),
            child: const Text('View spec', style: TextStyle(fontSize: 12.5)),
          ),
          if (_isAdmin)
            IconButton(
              tooltip: 'Edit the ROI assumptions',
              onPressed: () => _editRoi(w),
              visualDensity: VisualDensity.compact,
              icon: const Icon(Icons.tune, size: 16),
            ),
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          _DefRow(
            'Trigger',
            trigger == null
                ? '—'
                : event.isEmpty
                ? trigger.name
                : event,
            mono: trigger != null && event.isNotEmpty,
          ),
          _DefRow(
            'Cadence',
            event.startsWith('timer:')
                ? 'Windowed — one run per window'
                : 'Per case — one run per event',
          ),
          _DefRow('Authored by', w.authoredBy),
          _DefRow(
            'Version',
            '${w.activeVersionLabel} active · ${w.versionCount} in history'
            '${w.draftVersionLabel.isEmpty ? '' : ' · ${w.draftVersionLabel} open'}',
          ),
          _DefRow('Workspace', w.workspace.isEmpty ? '—' : w.workspace),
          _DefRow(
            'Model budget',
            '\$${w.modelBudgetUsdPerRun.toStringAsFixed(2)} cap per run',
          ),
          _DefRow(
            'ROI basis',
            '${w.roiMinutesPerRun.toStringAsFixed(0)} min/run · '
                '\$${w.roiHourlyRateUsd.toStringAsFixed(0)}/h loaded',
          ),
          _DefRow(
            'Returned',
            '${(usage.minutesSaved / 60).round()}h ≈ ${money(usage.roiUsd)}',
            valueColor: CumulusColors.success,
          ),
          _DefRow('Updated', agoLabel(w.updatedAt), last: true),
        ],
      ),
    );
  }

  /// What is happening with this flow, in sentences.
  ///
  /// Every clause is a number that appears elsewhere on this page — the point
  /// is not to add information but to say what the figures amount to, which a
  /// grid of tiles cannot do. An operator who has just opened the page should
  /// be able to read one paragraph and know whether to look further.
  ///
  /// Composed here rather than server-side because each clause has to be
  /// answerable by something on screen; prose assembled somewhere else would
  /// be free to drift from the tiles beside it.
  Widget _narrativeCard(FlowDetail w, FlowUsage usage) {
    return SectionCard(
      title: 'What is happening',
      subtitle: 'read off this flow\'s own telemetry',
      padding: const EdgeInsets.fromLTRB(20, 18, 20, 20),
      child: Text(
        _narrative(w, usage),
        style: CumulusText.body.copyWith(
          fontSize: 13.5,
          height: 1.55,
          color: CumulusColors.ink,
        ),
      ),
    );
  }

  String _narrative(FlowDetail w, FlowUsage usage) {
    final days = usage.days;
    final parts = <String>[];

    // 1. Is it even switched on?
    parts.add(switch (w.status) {
      'paused' => 'This flow is paused, so no event reaches it.',
      'draft' => 'This flow is a draft and has not been let out to run.',
      'deprecated' => 'This flow is deprecated and is being kept for its history.',
      _ => 'This flow is active and takes events as they arrive.',
    });

    // 2. What it has done, and how that ended.
    if (usage.runs == 0) {
      parts.add('It has not run in the last $days days.');
    } else {
      final finished = usage.completed + usage.failed;
      final outcome = usage.failed == 0
          ? (finished == 0
                ? 'none of them have finished yet'
                : 'every one that finished succeeded')
          : '${usage.failed} of them failed'
                '${usage.successRate == null ? '' : ' (${percent(usage.successRate)} of finished runs succeeded)'}';
      parts.add(
        'It ran ${thousands(usage.runs)} time'
        '${usage.runs == 1 ? '' : 's'} in the last $days days, and $outcome.',
      );
    }

    // 3. Whether it is asking anything of a person.
    if (usage.tasksRaised > 0 || w.tasksOpen > 0) {
      parts.add(
        w.tasksOpen == 0
            ? 'It routed ${thousands(usage.tasksRaised)} run'
                  '${usage.tasksRaised == 1 ? '' : 's'} to a person, and '
                  'nothing is still waiting.'
            : '${thousands(w.tasksOpen)} run'
                  '${w.tasksOpen == 1 ? '' : 's'} '
                  '${w.tasksOpen == 1 ? 'is' : 'are'} waiting on a person '
                  '${w.queues.isEmpty ? '' : 'at ${w.queues.join(' and ')}'}'
                  '.',
      );
    } else if (usage.runs > 0) {
      parts.add('No run needed a person.');
    }

    // 4. What it cost. A zero here means two different things.
    if (usage.modelCalls == 0) {
      if (usage.runs > 0) {
        parts.add('It called no model, so its spend is a real zero.');
      }
    } else {
      parts.add(
        'It made ${thousands(usage.modelCalls)} model call'
        '${usage.modelCalls == 1 ? '' : 's'} costing '
        '${money(usage.costUsd)}, against a cap of '
        '\$${w.modelBudgetUsdPerRun.toStringAsFixed(2)} a run.',
      );
    }

    // 5. What it is wired to, if the inventory has arrived.
    final inv = _inventory;
    if (inv != null && inv.links.isNotEmpty) {
      String names(String verb) {
        final labels = inv.withVerb(verb).map((l) => l.label).toList();
        if (labels.isEmpty) return '';
        if (labels.length <= 2) return labels.join(' and ');
        return '${labels.take(2).join(', ')} and ${labels.length - 2} more';
      }

      final reads = names('reads');
      final mutates = names('mutates');
      final clauses = [
        if (reads.isNotEmpty) 'reads $reads',
        if (mutates.isNotEmpty) 'changes $mutates',
        if (mutates.isEmpty && inv.state == 'ran_but_wrote_nothing')
          'changes nothing — it is read-only by design',
      ];
      if (clauses.isNotEmpty) {
        parts.add('It ${clauses.join(', and ')}.');
      }
    }

    // 6. The one governance sentence worth printing.
    if (inv != null && inv.undeclared.isNotEmpty) {
      parts.add(
        'It touched ${inv.undeclared.length} thing'
        '${inv.undeclared.length == 1 ? '' : 's'} nothing declares: '
        '${inv.undeclared.join(', ')}.',
      );
    }
    return parts.join(' ');
  }

  /// The models this flow calls, declared and observed.
  Widget _modelsCard() {
    final inv = _inventory;
    if (inv == null) {
      return const SectionCard(
        title: 'Configured models',
        subtitle: 'from the export and the run journal',
        child: Padding(
          padding: EdgeInsets.symmetric(vertical: 14),
          child: CumulusLoading(),
        ),
      );
    }
    return SectionCard(
      title: 'Configured models',
      subtitle: inv.models.isEmpty
          ? 'this flow calls no model'
          : '\$${inv.modelBudgetUsdPerRun.toStringAsFixed(2)} cap per run',
      padding: const EdgeInsets.fromLTRB(18, 18, 18, 12),
      child: inv.models.isEmpty
          ? Text(
              'No step of this definition calls a model, and no run has '
              'recorded one. Its spend is a real zero rather than an '
              'unmeasured one.',
              style: CumulusText.meta,
            )
          : Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                for (final model in inv.models) _ModelRow(model: model),
              ],
            ),
    );
  }

  /// How this flow touches the estate: one row per relationship.
  ///
  /// A table rather than four groups of chips. The chips looked tidy and made
  /// the reader do the work of comparing across groups — and they had no room
  /// to say, per row, whether the relationship was observed in a journal or
  /// only claimed by the export.
  Widget _linksCard() {
    final inv = _inventory;
    if (inv == null) {
      return const SectionCard(
        title: 'Links',
        subtitle: 'what this flow touches',
        child: Padding(
          padding: EdgeInsets.symmetric(vertical: 18),
          child: CumulusLoading(),
        ),
      );
    }
    final undeclared = inv.undeclared.toSet();
    return CumulusCard(
      padding: EdgeInsets.zero,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(20, 18, 20, 14),
            child: SectionHeader(
              title: 'Links · ${inv.links.length}',
              subtitle: inv.state == 'never_ran'
                  ? 'declared by the export · no run has exercised any of it'
                  : 'entities, collections, desks and systems this flow '
                        'touches',
            ),
          ),
          if (inv.links.isEmpty)
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 0, 20, 20),
              child: Text(
                'This flow reaches no entity, collection, desk or system.',
                style: CumulusText.meta,
              ),
            )
          else ...[
            const _LinkHead(),
            const Divider(height: 1),
            for (var i = 0; i < inv.links.length; i++)
              _LinkRow(
                link: inv.links[i],
                flagged: undeclared.contains(inv.links[i].label),
                last: i == inv.links.length - 1,
              ),
            const SizedBox(height: 6),
          ],
        ],
      ),
    );
  }

  /// The last few runs, with the way into the rest of them.
  ///
  /// Five, not fifty. The Runs tab is the journal; this is the glance that
  /// tells you whether to open it, and a feed long enough to scroll would be
  /// that tab built twice.
  Widget _recentRunsCard() {
    final recent = _runs.take(5).toList();
    return SectionCard(
      title: 'Recent executions',
      subtitle: _runs.isEmpty
          ? 'this definition has never executed'
          : 'newest first · click one for its journal',
      padding: const EdgeInsets.fromLTRB(18, 18, 18, 12),
      headerTrailing: _runs.isEmpty
          ? null
          : TextButton(
              onPressed: () => _goToTab(2),
              style: TextButton.styleFrom(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                minimumSize: Size.zero,
                tapTargetSize: MaterialTapTargetSize.shrinkWrap,
              ),
              child: Text(
                'All ${thousands(_runs.length)}',
                style: const TextStyle(fontSize: 12.5),
              ),
            ),
      child: recent.isEmpty
          ? Text(
              'No run to show. This flow is dispatched by events; when one '
              'lands, its journal appears here.',
              style: CumulusText.meta,
            )
          : Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                for (var i = 0; i < recent.length; i++)
                  _RecentRunRow(
                    run: recent[i],
                    last: i == recent.length - 1,
                    onOpen: () => _openRunJournal(recent[i]),
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

  /// The graph is why this tab exists, so it gets the viewport. The
  /// inspector is a fixed column beside it, not half the screen: an even
  /// split spent half a workbench on a panel that is empty until you click a
  /// node, and the node graph — the one thing here that cannot be read in a
  /// list — was the half that paid for it.
  Widget _canvasBody() {
    return LayoutBuilder(
      builder: (context, constraints) {
        final inspector = constraints.maxWidth >= 1500
            ? 460.0
            : constraints.maxWidth >= 1100
            ? 400.0
            : 340.0;
        return Row(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Expanded(
              child: FlowCanvas(
                steps: _canvasSteps,
                // Which revision of the graph you are looking at is a fact
                // about the graph, so the control for it stands on the graph.
                toolbar: _VersionSelector(
                  versions: _versions,
                  selected: _selectedVersion,
                  onChanged: _selectVersion,
                  // Publishing a definition and running it are two acts. The
                  // control appears only on a version the deployment can
                  // actually execute, and only for somebody who may change
                  // what the fleet runs.
                  onActivate:
                      _isAdmin &&
                          !_activatingVersion &&
                          (_current?.canActivate ?? false)
                      ? () => _activateVersion(_current!)
                      : null,
                  activating: _activatingVersion,
                ),
                diffAgainst:
                    (_current?.isDraft ?? false) && _activeVersion != null
                    ? '${_activeVersion!.display} (active)'
                    : '',
                runPath: _runPath,
                selectedStepId: _selectedStepId,
                onSelect: (step) => setState(() => _selectedStepId = step?.id),
                onExportSchema: _current == null
                    ? null
                    : () => _exportSchema(_current!),
              ),
            ),
            const VerticalDivider(width: 1, color: CumulusColors.border),
            SizedBox(width: inspector, child: _inspectorColumn()),
          ],
        );
      },
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
                onPressed: () => setState(() {
                  _tracing = false;
                  _traceCaveat = '';
                }),
              ),
            ],
          ),
          Padding(
            padding: const EdgeInsets.only(left: 24, right: 8, top: 2),
            child: Text(
              '${run.claimsVersion ? 'ran v${run.flowVersion} · ' : ''}'
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
          // The one thing a highlighted path must never do quietly: be drawn
          // over a graph the run did not execute.
          if (_traceCaveat.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(left: 24, right: 8, top: 8),
              child: Container(
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: CumulusTone.warn.background,
                  borderRadius: BorderRadius.circular(CumulusRadius.control),
                ),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Icon(
                      Icons.report_problem_outlined,
                      size: 15,
                      color: CumulusTone.warn.foreground,
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        _traceCaveat,
                        style: CumulusText.meta.copyWith(
                          color: CumulusTone.warn.foreground,
                        ),
                      ),
                    ),
                  ],
                ),
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
    // Pane-shaped, not a spinner: what is coming is a short table of what the
    // step read, wrote and returned, and the inspector column should not jump
    // when it lands.
    if (_loadingRun) {
      return const SkeletonPulse(
        child: SkeletonTableRows(rows: 4, columnFlex: [2, 5]),
      );
    }
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
    return _StepRunDetail(api: _api, step: entry, runId: run.id);
  }

  // ---- Tab 3: run history -------------------------------------------------

  /// Deep enough that the tab is the whole history rather than the last
  /// screenful of it, and inside the endpoint's own ceiling of 500.
  static const int _runHistoryLimit = 300;

  /// The last cut of the history and what produced it. Three hundred runs are
  /// cheap to filter once and wasteful to filter on every frame a hover, a
  /// scroll or a drawer causes — and the window is measured from the moment
  /// the cut was taken rather than from whenever the last widget rebuilt.
  Object? _runCutKey;
  List<FlowRunSummary> _runCut = const [];

  List<FlowRunSummary> get _visibleRuns {
    final key = (_runs, _runStatus, _runWindow, _runQuery);
    if (key != _runCutKey) {
      _runCutKey = key;
      _runCut = filterRuns(
        _runs,
        status: _runStatus,
        window: _runWindow,
        query: _runQuery,
      );
    }
    return _runCut;
  }

  /// The run log, built as the fleet grid: one pinned block of controls and
  /// column heads, high-density rows under it, and the journal in a drawer
  /// rather than an accordion.
  ///
  /// The accordion is what this replaced. Expanding a row in place pushed
  /// every run below it off the screen — precisely the rows you opened this
  /// one to compare against.
  Widget _runsBody() {
    final rows = _visibleRuns;
    return LayoutBuilder(
      builder: (context, constraints) {
        final width = constraints.maxWidth;
        final columns = RunColumns.forWidth(width);
        return CustomScrollView(
          controller: _runScroll,
          slivers: [
            const SliverToBoxAdapter(child: SizedBox(height: 14)),
            SliverPersistentHeader(
              pinned: true,
              delegate: StickyGridHeader(
                extent: runStickyExtentFor(width),
                lifted: _runHeadLifted,
                child: _runControlsAndHead(rows.length, columns, width),
              ),
            ),
            if (rows.isEmpty)
              SliverToBoxAdapter(
                child: PageGutter(
                  padding: const EdgeInsets.fromLTRB(14, 0, 28, 0),
                  child: _runsEmptyState(),
                ),
              )
            else
              SliverList.builder(
                itemCount: rows.length,
                itemBuilder: (context, i) => PageGutter(
                  padding: const EdgeInsets.fromLTRB(14, 0, 28, 0),
                  child: RunGridRow(
                    run: rows[i],
                    columns: columns,
                    open: _selectedRun?.id == rows[i].id,
                    hovered: _hoveredRun,
                    onOpen: () => _openRunJournal(rows[i]),
                  ),
                ),
              ),
            const SliverToBoxAdapter(child: SizedBox(height: 40)),
          ],
        );
      },
    );
  }

  /// Search, status lenses and the time window, then the column heads — the
  /// fleet grid's pinned block, over runs.
  ///
  /// The status counts are taken after the time window but before the search,
  /// so a lens's number keeps meaning "how many runs are in this state" while
  /// you type rather than counting only what the query already matched.
  /// [waiting] is the history's first fetch still being in flight: the box,
  /// the window and the captions are real, and only what has to be counted —
  /// the lenses and the row tally — is held back rather than stated as zero.
  Widget _runControlsAndHead(
    int shown,
    RunColumns columns,
    double width, {
    bool waiting = false,
  }) {
    final search = _RunSearchField(controller: _runSearch);
    final lenses = waiting ? _lensStandIns() : _runLenses();
    final window = _RunWindowSelector(
      value: _runWindow,
      onChanged: (w) => setState(() => _runWindow = w),
    );
    final refresh = IconButton(
      tooltip: 'Refresh the history',
      onPressed: _reloadRuns,
      visualDensity: VisualDensity.compact,
      icon: const Icon(Icons.refresh, size: 16),
    );

    // Explicit arrangements rather than a Wrap: the pinned sliver states its
    // height from the width before it lays anything out, so what is laid out
    // here has to follow the same rule.
    final controls = switch (runStickyRowsFor(width)) {
      1 => [
        SizedBox(
          height: _runControlHeight,
          child: Row(
            children: [
              ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 320),
                child: search,
              ),
              const SizedBox(width: 12),
              Expanded(child: lenses),
              const SizedBox(width: 10),
              window,
              refresh,
            ],
          ),
        ),
      ],
      _ => [
        SizedBox(
          height: _runControlHeight,
          child: Row(
            children: [
              Expanded(child: search),
              const SizedBox(width: 10),
              window,
              refresh,
            ],
          ),
        ),
        const SizedBox(height: _runRowGap),
        SizedBox(height: _runControlHeight, child: lenses),
      ],
    };

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        const SizedBox(height: _runStickyTopPad),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: FleetCol.rowPadding),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: controls,
          ),
        ),
        const SizedBox(height: _runStickyGap),
        SizedBox(
          height: _runHeadHeight,
          child: RunGridHead(
            columns: columns,
            // Nothing has been counted yet, and a caption reading "0 of 0"
            // would be a measurement rather than a gap.
            caption: waiting ? '' : '$shown of ${_runs.length}',
          ),
        ),
      ],
    );
  }

  /// The status lenses, scrolling sideways rather than wrapping: the pinned
  /// block has already declared how tall it is, and a second line of chips
  /// would appear underneath the rows.
  Widget _runLenses() {
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
    // otherwise the only chip that could undo the filter vanishes.
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
    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      child: Row(
        children: [
          LensChip(
            label: 'All · ${inWindow.length}',
            selected: _runStatus == null,
            onTap: () => setState(() => _runStatus = null),
          ),
          for (final status in statuses)
            Padding(
              padding: const EdgeInsets.only(left: 6),
              child: LensChip(
                label: '$status · ${counts[status] ?? 0}',
                tone: runTone(status),
                selected: _runStatus == status,
                onTap: () => setState(
                  () => _runStatus = _runStatus == status ? null : status,
                ),
              ),
            ),
        ],
      ),
    );
  }

  /// The lens rail before there is a status to count. Chips at the widths
  /// the four stored states come to, so the toolbar does not reflow when the
  /// real counts arrive.
  Widget _lensStandIns() => SkeletonPulse(
    child: Row(
      children: [
        for (final width in const [64.0, 108.0, 104.0, 84.0, 86.0])
          Padding(
            padding: const EdgeInsets.only(right: 6),
            child: SkeletonPill(width: width),
          ),
      ],
    ),
  );

  /// Inside the table card's side rules, so an empty result reads as the
  /// table being empty rather than the table being gone.
  Widget _runsEmptyState() => Container(
    decoration: const BoxDecoration(
      color: CumulusColors.surface,
      border: Border(
        left: BorderSide(color: CumulusColors.border),
        right: BorderSide(color: CumulusColors.border),
        bottom: BorderSide(color: CumulusColors.border),
      ),
    ),
    padding: const EdgeInsets.symmetric(vertical: 36),
    child: _runs.isEmpty
        ? const CumulusEmptyState(
            icon: Icons.play_circle_outline,
            title: 'No runs yet',
            message: 'This definition has never executed.',
          )
        : CumulusEmptyState(
            icon: Icons.search_off,
            title: 'Nothing matches',
            message:
                'No run matches the current filters. Clear the search, the '
                'status lens or the time window to widen the history.',
            action: OutlinedButton(
              onPressed: _clearRunFilters,
              child: const Text('Clear filters'),
            ),
          ),
  );

  void _clearRunFilters() {
    // Cancelled first, so the empty box is honoured on this frame rather than
    // a debounce later.
    _runSearchDebounce?.cancel();
    setState(() {
      _runStatus = null;
      _runWindow = RunWindow.forDays(widget.days);
    });
    // Clearing the field fires the listener, which applies the empty query.
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

  /// One run's journal — inputs, read-set, write-set, every step — in a
  /// slide-over, with the log still on screen behind it.
  ///
  /// The journal is fetched beside the drawer rather than before it, so the
  /// panel opens on the click and fills in, instead of the row sitting dead
  /// for a round trip.
  Future<void> _openRunJournal(FlowRunSummary run) async {
    final journal = ValueNotifier<(FlowRun?, String?)>((null, null));
    setState(() => _selectedRun = run);
    unawaited(() async {
      try {
        final detail = await _api.getRun(run.id);
        if (!mounted) return;
        setState(() => _runDetail = detail);
        journal.value = (detail, null);
      } on ApiException catch (e) {
        journal.value = (null, e.message);
      }
    }());
    final action = await showSideDrawer<String>(
      context: context,
      width: 700,
      title: run.id,
      subtitle:
          '${_runSubject(run).isEmpty ? '' : '${_runSubject(run)} · '}'
          '${run.steps} step${run.steps == 1 ? '' : 's'}'
          ' · ${run.modelCalls} ModelCall${run.modelCalls == 1 ? '' : 's'}'
          ' · ${run.refs.length} governed read'
          '${run.refs.length == 1 ? '' : 's'}'
          ' · started ${agoIso(run.startedAtIso)}',
      titleBadges: [
        StatusBadge(
          run.status,
          tone: runTone(run.status),
          dense: true,
          uppercase: true,
        ),
        if (run.status == 'suspended' && run.suspendedAtStep.isNotEmpty)
          StatusBadge('at ${run.suspendedAtStep}', dense: true),
        if (run.openTaskId.isNotEmpty)
          StatusBadge('task open', tone: CumulusTone.accent, dense: true),
        if (run.resumeCount > 0)
          StatusBadge(
            '${run.resumeCount}× resumed',
            tone: CumulusTone.accent,
            dense: true,
          ),
      ],
      actions: [
        OutlinedButton.icon(
          // The root navigator, because that is the one `showSideDrawer`
          // pushed onto: popping the wrong one would close the console.
          onPressed: () =>
              Navigator.of(context, rootNavigator: true).pop('trace'),
          icon: const Icon(Icons.account_tree_outlined, size: 16),
          label: const Text('Trace on canvas'),
        ),
      ],
      builder: (context) => ValueListenableBuilder<(FlowRun?, String?)>(
        valueListenable: journal,
        builder: (context, value, _) {
          final (detail, failure) = value;
          if (failure != null) return ErrorNotice(message: failure);
          if (detail == null) return const CumulusLoading();
          return RunJournalView(api: _api, run: detail);
        },
      ),
    );
    journal.dispose();
    if (!mounted) return;
    if (action == 'trace') {
      await _traceOnCanvas(run);
    } else {
      setState(() => _selectedRun = null);
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

}

// ---------------------------------------------------------------------------
// The header
// ---------------------------------------------------------------------------

/// Navigation inside one subject: labels standing on a rule, the current one
/// striking through it.
///
/// Deliberately not [SegmentedTabs]. The fleet page's pills read as a filter
/// over a list — four cuts of the same rows — and these are four different
/// places to be. Borrowing the pill would have said the wrong thing about
/// both pages, and made this one a copy of a page it is not.
class _UnderlineTabs extends StatelessWidget {
  final List<String> labels;
  final int selectedIndex;
  final ValueChanged<int> onChanged;

  const _UnderlineTabs({
    required this.labels,
    required this.selectedIndex,
    required this.onChanged,
  });

  @override
  Widget build(BuildContext context) => Row(
    mainAxisSize: MainAxisSize.min,
    crossAxisAlignment: CrossAxisAlignment.stretch,
    children: [
      for (var i = 0; i < labels.length; i++)
        _UnderlineTab(
          label: labels[i],
          selected: i == selectedIndex,
          onTap: () => onChanged(i),
        ),
    ],
  );
}

class _UnderlineTab extends StatefulWidget {
  final String label;
  final bool selected;
  final VoidCallback onTap;

  const _UnderlineTab({
    required this.label,
    required this.selected,
    required this.onTap,
  });

  @override
  State<_UnderlineTab> createState() => _UnderlineTabState();
}

class _UnderlineTabState extends State<_UnderlineTab> {
  bool _hover = false;

  @override
  Widget build(BuildContext context) {
    final ink = widget.selected
        ? CumulusColors.ink
        : _hover
        ? CumulusColors.ink
        : CumulusColors.inkSecondary;
    return MouseRegion(
      cursor: SystemMouseCursors.click,
      onEnter: (_) => setState(() => _hover = true),
      onExit: (_) => setState(() => _hover = false),
      child: GestureDetector(
        onTap: widget.onTap,
        behavior: HitTestBehavior.opaque,
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 14),
          alignment: Alignment.center,
          decoration: BoxDecoration(
            // The stroke is painted over the strip's own rule, at the same
            // baseline, so the selected tab reads as standing on the line
            // rather than floating above it.
            border: Border(
              bottom: BorderSide(
                color: widget.selected
                    ? CumulusColors.ink
                    : _hover
                    ? CumulusColors.borderStrong
                    : Colors.transparent,
                width: 2,
              ),
            ),
          ),
          child: Text(
            widget.label,
            style: TextStyle(
              fontSize: 13,
              fontWeight: widget.selected ? FontWeight.w600 : FontWeight.w500,
              color: ink,
            ),
          ),
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// The hero strip
// ---------------------------------------------------------------------------

/// A control sized for the hero strip — the same 30px pill as the window
/// selector beside it, so the strip reads as one row of controls rather than
/// a row of differently-sized buttons.
///
/// The framework's own `OutlinedButton` is 44px tall under this theme, which
/// is most of a 56px strip; that is the whole reason this exists.
class _HeroPill extends StatelessWidget {
  final IconData icon;
  final String label;
  final String? tooltip;
  final VoidCallback onTap;

  const _HeroPill({
    required this.icon,
    required this.label,
    required this.onTap,
    this.tooltip,
  });

  @override
  Widget build(BuildContext context) {
    const ink = CumulusColors.ink;
    final pill = Material(
      color: CumulusColors.surface,
      borderRadius: BorderRadius.circular(CumulusRadius.pill),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(CumulusRadius.pill),
        hoverColor: CumulusColors.surfaceMuted,
        child: Container(
          height: 30,
          padding: const EdgeInsets.symmetric(horizontal: 11),
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(CumulusRadius.pill),
            border: Border.all(color: CumulusColors.borderStrong),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(icon, size: 15, color: ink),
              const SizedBox(width: 6),
              Text(
                label,
                style: TextStyle(
                  fontSize: 12.5,
                  fontWeight: FontWeight.w600,
                  color: ink,
                ),
              ),
            ],
          ),
        ),
      ),
    );
    return tooltip == null ? pill : Tooltip(message: tooltip!, child: pill);
  }
}

// ---------------------------------------------------------------------------
// Asset inventory
// ---------------------------------------------------------------------------

/// One model, with what declares it and what it has actually cost.
class _ModelRow extends StatelessWidget {
  final ModelInventory model;

  const _ModelRow({required this.model});

  @override
  Widget build(BuildContext context) {
    final steps = model.steps.isEmpty ? '' : model.steps.join(', ');
    // A model with calls and no price is not a cheap model: its spend is a
    // hole, and the row says so rather than printing $0.00 as a measurement.
    final unpriced = model.calls > 0 && model.pricedCalls == 0;
    final observed = model.calls == 0
        ? (model.declared ? 'declared · not yet called' : 'no calls recorded')
        : '${thousands(model.calls)} call${model.calls == 1 ? '' : 's'} · '
              '${compactCount(model.tokens)} tokens · '
              '${unpriced ? 'unpriced' : money(model.costUsd)}';
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Flexible(
                child: Text(
                  model.model,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: model.isDefault
                      ? CumulusText.meta.copyWith(
                          fontStyle: FontStyle.italic,
                          color: CumulusColors.inkSecondary,
                        )
                      : CumulusText.mono.copyWith(
                          fontSize: 12,
                          fontWeight: FontWeight.w600,
                        ),
                ),
              ),
              if (!model.declared) ...[
                const SizedBox(width: 8),
                const StatusBadge(
                  'undeclared',
                  tone: CumulusTone.warn,
                  dense: true,
                ),
              ],
            ],
          ),
          const SizedBox(height: 2),
          Text(
            steps.isEmpty ? observed : '$steps · $observed',
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: CumulusText.meta.copyWith(
              fontSize: 11.5,
              color: unpriced ? CumulusColors.inkMuted : null,
            ),
          ),
        ],
      ),
    );
  }
}

/// One line of the recent-execution feed.
class _RecentRunRow extends StatefulWidget {
  final FlowRunSummary run;
  final bool last;
  final VoidCallback onOpen;

  const _RecentRunRow({
    required this.run,
    required this.last,
    required this.onOpen,
  });

  @override
  State<_RecentRunRow> createState() => _RecentRunRowState();
}

class _RecentRunRowState extends State<_RecentRunRow> {
  bool _hover = false;

  @override
  Widget build(BuildContext context) {
    final run = widget.run;
    final tone = runTone(run.status);
    return MouseRegion(
      cursor: SystemMouseCursors.click,
      onEnter: (_) => setState(() => _hover = true),
      onExit: (_) => setState(() => _hover = false),
      child: GestureDetector(
        onTap: widget.onOpen,
        behavior: HitTestBehavior.opaque,
        child: Container(
          padding: const EdgeInsets.symmetric(vertical: 8),
          // The fill lives on the decoration, not beside it: a Container
          // given both asserts at build time, which the analyzer does not
          // catch and every render does.
          decoration: BoxDecoration(
            color: _hover
                ? CumulusColors.surfaceMuted.withValues(alpha: 0.6)
                : null,
            border: widget.last
                ? null
                : const Border(
                    bottom: BorderSide(color: CumulusColors.border),
                  ),
          ),
          child: Row(
            children: [
              SizedBox(
                width: 116,
                child: StatusBadge(
                  run.status,
                  tone: tone,
                  dense: true,
                  uppercase: true,
                ),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: Text(
                  run.id,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: CumulusText.mono.copyWith(
                    fontSize: 12,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ),
              const SizedBox(width: 10),
              Text(
                '${run.modelCalls} calls',
                style: gridFigures.copyWith(
                  fontSize: 11.5,
                  color: run.modelCalls == 0 ? CumulusColors.inkMuted : null,
                ),
              ),
              const SizedBox(width: 14),
              SizedBox(
                width: 74,
                child: Text(
                  agoIso(run.startedAtIso),
                  textAlign: TextAlign.right,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    fontSize: 11.5,
                    color: CumulusColors.inkSecondary,
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// The links table
// ---------------------------------------------------------------------------

/// Column widths for the links table, in one place so the head cannot drift
/// from its rows.
abstract final class LinkCol {
  static const verb = 108.0;
  static const kind = 104.0;
  static const evidence = 132.0;
  static const gap = 14.0;
  static const pad = 20.0;
}

/// How each relationship reads in the table, and how it is toned.
///
/// The vocabulary is the server's — `reads`, `mutates`, `establishes`,
/// `writes-to`, `routes-to`, `reaches`. Rendered here as the phrase a person
/// would say, because the wire form has to be a stable token and a table cell
/// has to be English.
String linkPhrase(String verb) => switch (verb) {
  'reads' => 'reads',
  'mutates' => 'mutates',
  'establishes' => 'establishes',
  'writes-to' => 'writes to',
  'routes-to' => 'routes to',
  'reaches' => 'reaches',
  _ => verb,
};

CumulusTone linkTone(String verb) => switch (verb) {
  // Taking data in is the quiet case; changing something is the one a reader
  // scans for, so it carries the warmer colour.
  'reads' => CumulusTone.teal,
  'mutates' => CumulusTone.violet,
  'establishes' => CumulusTone.violet,
  'writes-to' => CumulusTone.accent,
  'routes-to' => CumulusTone.warn,
  _ => CumulusTone.neutral,
};

IconData linkKindIcon(String kind) => switch (kind) {
  'entity' => Icons.category_outlined,
  'knowledge' => Icons.menu_book_outlined,
  'collection' => Icons.inventory_2_outlined,
  'link' => Icons.link,
  'queue' => Icons.inbox_outlined,
  'system' => Icons.swap_horiz,
  _ => Icons.circle_outlined,
};

class _LinkHead extends StatelessWidget {
  const _LinkHead();

  @override
  Widget build(BuildContext context) {
    Widget cell(String text, double width) => SizedBox(
      width: width,
      child: Text(
        text.toUpperCase(),
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
        style: CumulusText.statLabel,
      ),
    );
    return Container(
      color: CumulusColors.surfaceMuted.withValues(alpha: 0.45),
      padding: const EdgeInsets.symmetric(
        horizontal: LinkCol.pad,
        vertical: 8,
      ),
      child: Row(
        children: [
          cell('Link', LinkCol.verb),
          const SizedBox(width: LinkCol.gap),
          Expanded(child: Text('TARGET', style: CumulusText.statLabel)),
          const SizedBox(width: LinkCol.gap),
          cell('Kind', LinkCol.kind),
          const SizedBox(width: LinkCol.gap),
          cell('Evidence', LinkCol.evidence),
        ],
      ),
    );
  }
}

/// One relationship: the verb, what it points at, what kind of thing that is,
/// and whether a run was ever seen doing it.
class _LinkRow extends StatefulWidget {
  final FlowLink link;

  /// Observed with nothing declaring it — the one state in this table that is
  /// a finding rather than a fact. Computed by the server, which knows which
  /// kinds have a declaration to be missing.
  final bool flagged;
  final bool last;

  const _LinkRow({
    required this.link,
    required this.flagged,
    required this.last,
  });

  @override
  State<_LinkRow> createState() => _LinkRowState();
}

class _LinkRowState extends State<_LinkRow> {
  bool _hover = false;

  @override
  Widget build(BuildContext context) {
    final link = widget.link;
    final tone = linkTone(link.verb);
    // Declared-and-never-seen is drawn back, not flagged: it is a path no run
    // has taken yet as often as it is a stale claim, and the table cannot say
    // which.
    final faded = link.unobserved;
    return MouseRegion(
      onEnter: (_) => setState(() => _hover = true),
      onExit: (_) => setState(() => _hover = false),
      child: Container(
        padding: const EdgeInsets.symmetric(
          horizontal: LinkCol.pad,
          vertical: 7,
        ),
        decoration: BoxDecoration(
          color: _hover
              ? CumulusColors.surfaceMuted.withValues(alpha: 0.5)
              : null,
          border: widget.last
              ? null
              : const Border(bottom: BorderSide(color: CumulusColors.border)),
        ),
        child: Row(
          children: [
            SizedBox(
              width: LinkCol.verb,
              child: Align(
                alignment: Alignment.centerLeft,
                child: StatusBadge(
                  linkPhrase(link.verb),
                  tone: tone,
                  dense: true,
                  uppercase: true,
                ),
              ),
            ),
            const SizedBox(width: LinkCol.gap),
            Expanded(
              child: Row(
                children: [
                  Flexible(
                    child: Text(
                      link.label,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: CumulusText.mono.copyWith(
                        fontSize: 12.5,
                        height: 1.2,
                        fontWeight: FontWeight.w600,
                        color: faded
                            ? CumulusColors.inkSecondary
                            : CumulusColors.ink,
                      ),
                    ),
                  ),
                  if (widget.flagged) ...[
                    const SizedBox(width: 10),
                    const StatusBadge(
                      'undeclared',
                      tone: CumulusTone.warn,
                      dense: true,
                    ),
                  ],
                ],
              ),
            ),
            const SizedBox(width: LinkCol.gap),
            SizedBox(
              width: LinkCol.kind,
              child: Row(
                children: [
                  Icon(
                    linkKindIcon(link.kind),
                    size: 13,
                    color: CumulusColors.inkMuted,
                  ),
                  const SizedBox(width: 6),
                  Flexible(
                    child: Text(
                      link.kind,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                        fontSize: 11.5,
                        color: CumulusColors.inkSecondary,
                      ),
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(width: LinkCol.gap),
            SizedBox(
              width: LinkCol.evidence,
              child: Text(
                link.evidence,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                  fontSize: 11.5,
                  color: link.observed
                      ? CumulusColors.inkSecondary
                      : CumulusColors.inkMuted,
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Overview cells
// ---------------------------------------------------------------------------

/// One line of a definition table: small-caps label, value, hairline under.
class _DefRow extends StatelessWidget {
  final String label;
  final String value;
  final bool mono;
  final bool last;
  final Color? valueColor;

  const _DefRow(
    this.label,
    this.value, {
    this.mono = false,
    this.last = false,
    this.valueColor,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(vertical: 8),
      decoration: BoxDecoration(
        border: last
            ? null
            : const Border(bottom: BorderSide(color: CumulusColors.border)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 100,
            child: Text(label.toUpperCase(), style: CumulusText.statLabel),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Text(
              value,
              style: mono
                  ? CumulusText.mono.copyWith(fontSize: 12, height: 1.35)
                  : TextStyle(
                      fontSize: 12.5,
                      height: 1.35,
                      color: valueColor ?? CumulusColors.ink,
                    ),
            ),
          ),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// The run grid
// ---------------------------------------------------------------------------

/// Column widths for the run log, in one place for the same reason
/// [FleetCol] is: a head that drifts from its rows by two pixels is a table
/// that reads as broken at every scroll position.
abstract final class RunCol {
  /// Wide enough for `SUCCEEDED` — the longest status the engine stores — in
  /// a font noticeably wider than the one that ships. A status badge that
  /// wraps to two lines takes the whole row with it, and the row being one
  /// line is the entire point of the grid.
  static const status = 116.0;
  static const actor = 176.0;
  static const duration = 84.0;
  static const calls = 92.0;
  static const when = 96.0;
}

/// Which optional columns a width can seat. Status, the run id and when it
/// started are never dropped: they are what a run is looked up by.
class RunColumns {
  final bool actor;
  final bool duration;
  final bool calls;

  const RunColumns({
    required this.actor,
    required this.duration,
    required this.calls,
  });

  factory RunColumns.forWidth(double width) => RunColumns(
    actor: width >= 1000,
    calls: width >= 880,
    duration: width >= 780,
  );
}

const _runControlHeight = 34.0;
const _runHeadHeight = 34.0;
const _runStickyTopPad = 12.0;
const _runStickyGap = 10.0;
const _runRowGap = 8.0;

/// Above this the search, the status lenses and the window share one line.
const _runOneRowWidth = 1180.0;

int runStickyRowsFor(double width) => width >= _runOneRowWidth ? 1 : 2;

/// The exact height of the run log's pinned block at a given width. A pinned
/// sliver states its height before its child is laid out, so this and
/// `_runControlsAndHead` have to be one rule rather than two that can drift.
double runStickyExtentFor(double width) {
  final rows = runStickyRowsFor(width);
  return 1 + // the card's top hairline
      _runStickyTopPad +
      (_runControlHeight * rows) +
      (_runRowGap * (rows - 1)) +
      _runStickyGap +
      _runHeadHeight +
      1; // the hairline under the column heads
}

/// A run's duration in the coarsest unit that still says something. Every
/// seeded run reports 0ms, and printing that claims a measurement of zero
/// rather than none.
String runDuration(int ms) {
  if (ms <= 0) return 'not timed';
  if (ms < 1000) return '${ms}ms';
  if (ms < 60000) return '${(ms / 1000).toStringAsFixed(1)}s';
  return '${ms ~/ 60000}m ${(ms % 60000) ~/ 1000}s';
}

/// The run log's column captions, on the fleet grid's geometry.
class RunGridHead extends StatelessWidget {
  final RunColumns columns;
  final String caption;

  const RunGridHead({
    super.key,
    required this.columns,
    required this.caption,
  });

  @override
  Widget build(BuildContext context) {
    return Padding(
      // The rule down a failed row's edge is inside the card, so the heads
      // start where a row's content starts, not where its border does.
      padding: const EdgeInsets.fromLTRB(
        FleetCol.rowPadding + FleetCol.rule,
        0,
        FleetCol.rowPadding,
        0,
      ),
      child: Row(
        children: [
          FleetGridHead.cell('Status', RunCol.status, align: TextAlign.left),
          const SizedBox(width: FleetCol.gap),
          Expanded(
            child: Row(
              children: [
                Text('RUN ID', style: CumulusText.statLabel),
                const SizedBox(width: 10),
                Flexible(
                  child: Text(
                    caption,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: CumulusText.sectionSubtitle,
                  ),
                ),
              ],
            ),
          ),
          if (columns.actor) ...[
            const SizedBox(width: FleetCol.gap),
            FleetGridHead.cell(
              'Actor / trigger',
              RunCol.actor,
              align: TextAlign.left,
            ),
          ],
          if (columns.duration) ...[
            const SizedBox(width: FleetCol.gap),
            FleetGridHead.cell('Duration', RunCol.duration),
          ],
          if (columns.calls) ...[
            const SizedBox(width: FleetCol.gap),
            FleetGridHead.cell('Model calls', RunCol.calls),
          ],
          const SizedBox(width: FleetCol.gap),
          FleetGridHead.cell('Started', RunCol.when, align: TextAlign.left),
        ],
      ),
    );
  }
}

/// One run, one click target, inside the table card's side rules.
///
/// One line. What used to be here was a 60px list row carrying an icon tile,
/// a wrap of five badges and a two-line subtitle that restated every figure
/// this table now has a column for.
class RunGridRow extends StatelessWidget {
  final FlowRunSummary run;
  final RunColumns columns;

  /// Whether this run's journal is the one open in the drawer.
  final bool open;
  final VoidCallback onOpen;

  /// Which row the pointer is over, held by the log. Three hundred rows used
  /// to carry a `State` each to answer that for themselves. Null where a row
  /// is rendered outside a log, which is the honest answer for a row that is
  /// not part of a list to move between.
  final ValueNotifier<String?>? hovered;

  const RunGridRow({
    super.key,
    required this.run,
    required this.columns,
    required this.open,
    required this.onOpen,
    this.hovered,
  });

  @override
  Widget build(BuildContext context) {
    final tone = runTone(run.status);
    // Only a failure tints the row. A suspended run is waiting on a person,
    // which is the design working, and spending the page's one alarm colour
    // on it would leave nothing for the runs that actually broke.
    final flagged = run.status == 'failed';
    final note = run.error.isNotEmpty
        ? run.error
        : run.suspendedAtStep.isNotEmpty
        ? 'at ${run.suspendedAtStep}'
        : '';

    // Built once, whatever the pointer does: the cells do not depend on
    // hover, so moving the mouse down the log repaints two rectangles a row
    // rather than rebuilding every figure on it.
    final cells = Row(
      children: [
        SizedBox(
          width: RunCol.status,
          child: Align(
            alignment: Alignment.centerLeft,
            child: StatusBadge(
              run.status,
              tone: tone,
              dense: true,
              uppercase: true,
            ),
          ),
        ),
        const SizedBox(width: FleetCol.gap),
        Expanded(
          child: Row(
            children: [
              Flexible(
                child: Text(
                  run.id,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: CumulusText.mono.copyWith(
                    fontSize: 12.5,
                    height: 1.2,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ),
              // Why it stopped, when it stopped for a reason. The one thing a
              // run row can say that no column has a home for.
              if (note.isNotEmpty) ...[
                const SizedBox(width: 10),
                Flexible(
                  child: Text(
                    note,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                      fontSize: 11.5,
                      color: flagged
                          ? CumulusColors.danger
                          : CumulusColors.inkMuted,
                    ),
                  ),
                ),
              ],
              if (run.openTaskId.isNotEmpty) ...[
                const SizedBox(width: 8),
                const StatusBadge(
                  'task open',
                  tone: CumulusTone.accent,
                  dense: true,
                ),
              ],
            ],
          ),
        ),
        if (columns.actor) ...[
          const SizedBox(width: FleetCol.gap),
          SizedBox(
            width: RunCol.actor,
            child: Text(
              _actor(run),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(
                fontSize: 11.5,
                color: CumulusColors.inkSecondary,
              ),
            ),
          ),
        ],
        if (columns.duration) ...[
          const SizedBox(width: FleetCol.gap),
          _figure(
            runDuration(run.durationMs),
            RunCol.duration,
            color: run.durationMs <= 0 ? CumulusColors.inkMuted : null,
          ),
        ],
        if (columns.calls) ...[
          const SizedBox(width: FleetCol.gap),
          _figure(
            thousands(run.modelCalls),
            RunCol.calls,
            color: run.modelCalls == 0 ? CumulusColors.inkMuted : null,
          ),
        ],
        const SizedBox(width: FleetCol.gap),
        SizedBox(
          width: RunCol.when,
          child: Text(
            agoIso(run.startedAtIso),
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: const TextStyle(
              fontSize: 11.5,
              color: CumulusColors.inkSecondary,
            ),
          ),
        ),
      ],
    );

    Widget painted(bool hover) {
      final tint = open
          ? CumulusColors.accentSoft.withValues(alpha: 0.7)
          : flagged
          ? tone.background.withValues(alpha: hover ? 0.95 : 0.55)
          : (hover
                ? CumulusColors.surfaceMuted.withValues(alpha: 0.55)
                : CumulusColors.surface);
      return Container(
        decoration: const BoxDecoration(
          border: Border(
            left: BorderSide(color: CumulusColors.border),
            right: BorderSide(color: CumulusColors.border),
            bottom: BorderSide(color: CumulusColors.border),
          ),
        ),
        child: Row(
          children: [
            // A fixed-width child rather than a thicker border side, so a
            // flagged row and a clean one keep their cells in column.
            Container(
              width: FleetCol.rule,
              color: flagged
                  ? tone.foreground
                  : open
                  ? CumulusColors.accent
                  : tint,
            ),
            Expanded(
              child: Container(
                color: tint,
                padding: const EdgeInsets.fromLTRB(
                  FleetCol.rowPadding,
                  7,
                  FleetCol.rowPadding,
                  7,
                ),
                child: cells,
              ),
            ),
          ],
        ),
      );
    }

    final hover = hovered;
    // Its own layer: a tint that changes under the pointer is a repaint of
    // one row rather than of the log it sits in.
    return RepaintBoundary(
      child: MouseRegion(
        cursor: SystemMouseCursors.click,
        onEnter: hover == null ? null : (_) => hover.value = run.id,
        onExit: hover == null
            ? null
            : (_) {
                // Only if the pointer has not already been claimed by the row
                // it moved on to.
                if (hover.value == run.id) hover.value = null;
              },
        child: GestureDetector(
          onTap: onOpen,
          behavior: HitTestBehavior.opaque,
          child: hover == null
              ? painted(false)
              : ValueListenableBuilder<String?>(
                  valueListenable: hover,
                  builder: (context, id, _) => painted(id == run.id),
                ),
        ),
      ),
    );
  }

  /// Who or what this run was for. A run started without an actor keeps the
  /// case it ran on; one with neither has nothing honest to print.
  String _actor(FlowRunSummary run) => run.actor.isNotEmpty
      ? run.actor
      : run.caseRef.isNotEmpty && run.caseRef != run.id
      ? run.caseRef
      : '—';

  Widget _figure(String text, double width, {Color? color}) => SizedBox(
    width: width,
    child: Text(
      text,
      textAlign: TextAlign.right,
      maxLines: 1,
      overflow: TextOverflow.ellipsis,
      style: color == null ? gridFigures : gridFigures.copyWith(color: color),
    ),
  );
}

/// The run log's filter box, sized to the toolbar's stated 34px.
class _RunSearchField extends StatelessWidget {
  final TextEditingController controller;

  const _RunSearchField({required this.controller});

  @override
  Widget build(BuildContext context) {
    return TextField(
      controller: controller,
      style: const TextStyle(fontSize: 13),
      decoration: InputDecoration(
        hintText: 'Search run id, case, actor, step, error…',
        hintStyle: const TextStyle(
          fontSize: 12.5,
          color: CumulusColors.inkMuted,
        ),
        isDense: true,
        contentPadding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
        prefixIcon: const Icon(Icons.search, size: 15),
        prefixIconConstraints: const BoxConstraints(
          minWidth: 32,
          minHeight: _runControlHeight,
        ),
        suffixIcon: controller.text.isEmpty
            ? null
            : IconButton(
                tooltip: 'Clear',
                icon: const Icon(Icons.close, size: 14),
                visualDensity: VisualDensity.compact,
                onPressed: controller.clear,
              ),
        suffixIconConstraints: const BoxConstraints(
          minWidth: 30,
          minHeight: _runControlHeight,
        ),
      ),
    );
  }
}

/// The run log's time window, shaped like the page's global one so the two
/// read as the same kind of control — because they are, one nested in the
/// other's span.
class _RunWindowSelector extends StatelessWidget {
  final RunWindow value;
  final ValueChanged<RunWindow> onChanged;

  const _RunWindowSelector({required this.value, required this.onChanged});

  @override
  Widget build(BuildContext context) {
    return Container(
      height: 30,
      padding: const EdgeInsets.symmetric(horizontal: 10),
      decoration: BoxDecoration(
        color: CumulusColors.surface,
        borderRadius: BorderRadius.circular(CumulusRadius.pill),
        border: Border.all(color: CumulusColors.borderStrong),
      ),
      child: DropdownButtonHideUnderline(
        child: DropdownButton<RunWindow>(
          value: value,
          isDense: true,
          icon: const Icon(
            Icons.expand_more,
            size: 15,
            color: CumulusColors.inkMuted,
          ),
          style: const TextStyle(
            fontSize: 12.5,
            fontWeight: FontWeight.w600,
            color: CumulusColors.ink,
          ),
          borderRadius: BorderRadius.circular(CumulusRadius.control),
          items: [
            for (final window in RunWindow.values)
              DropdownMenuItem<RunWindow>(
                value: window,
                child: Text(
                  window.label,
                  style: const TextStyle(fontSize: 12.5),
                ),
              ),
          ],
          onChanged: (window) => window == null ? null : onChanged(window),
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

  /// Make the selected version live. Null when there is nothing to offer:
  /// the version is already active, the deployment does not ship its code, or
  /// this reader may not change what the fleet runs.
  final VoidCallback? onActivate;

  final bool activating;

  const _VersionSelector({
    required this.versions,
    required this.selected,
    required this.onChanged,
    this.onActivate,
    this.activating = false,
  });

  @override
  Widget build(BuildContext context) {
    if (versions.isEmpty) return const SizedBox.shrink();
    final current = versions.where((v) => v.version == selected).firstOrNull;
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        PopupMenuButton<int>(
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
                    // A definition and the code that runs it are two
                    // artifacts. Which versions the deployment can actually
                    // execute is a fact about the estate, and the only one
                    // this menu could not otherwise tell you.
                    if (v.shipped) ...[
                      const SizedBox(width: 6),
                      const StatusBadge(
                        'shipped',
                        dense: true,
                        uppercase: true,
                        tone: CumulusTone.teal,
                      ),
                    ],
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
                  if (current.shipped) ...[
                    const SizedBox(width: 6),
                    const Tooltip(
                      message: 'The deployed code ships this version',
                      child: Icon(
                        Icons.inventory_2_outlined,
                        size: 14,
                        color: CumulusColors.inkMuted,
                      ),
                    ),
                  ],
                ],
                const Icon(
                  Icons.expand_more,
                  size: 16,
                  color: CumulusColors.inkMuted,
                ),
              ],
            ),
          ),
        ),
        if (onActivate != null || activating) ...[
          const SizedBox(width: 8),
          FilledButton.icon(
            onPressed: activating ? null : onActivate,
            style: FilledButton.styleFrom(
              padding: const EdgeInsets.symmetric(horizontal: 14),
              visualDensity: VisualDensity.compact,
            ),
            icon: activating
                ? const SizedBox(
                    width: 13,
                    height: 13,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.publish_outlined, size: 15),
            label: Text(activating ? 'Making live…' : 'Make live'),
          ),
        ],
      ],
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
  final ApiClient api;
  final RunStep step;
  final String runId;

  const _StepRunDetail({
    required this.api,
    required this.step,
    required this.runId,
  });

  @override
  Widget build(BuildContext context) {
    final tone = step.status == 'failed'
        ? CumulusTone.danger
        : step.status == 'suspended'
        ? CumulusTone.accent
        : stepTone(step.kind);
    final decision = step.decision;
    // Only the ones the two sets above do not already offer: a document named
    // in the write-set is already a chip, and a second one would read as a
    // second artifact.
    final documents = [
      for (final ref in stepDocuments(step))
        if (!step.refs.contains(ref) && !step.writes.contains(ref)) ref,
    ];
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
        // Chips rather than a printed list, because the whole reason to click
        // a node on a traced path is to keep going.
        if (step.refs.isNotEmpty) ...[
          const FieldLabel('Read'),
          RefChips(api: api, refs: step.refs),
          const SizedBox(height: 12),
        ],
        if (step.writes.isNotEmpty) ...[
          const FieldLabel('Wrote'),
          RefChips(api: api, refs: step.writes, tone: CumulusTone.success),
          const SizedBox(height: 12),
        ],
        if (documents.isNotEmpty) ...[
          const FieldLabel(
            'Artifact',
            hint: 'the page this step worked on',
          ),
          RefChips(api: api, refs: documents),
          const SizedBox(height: 12),
        ],
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
