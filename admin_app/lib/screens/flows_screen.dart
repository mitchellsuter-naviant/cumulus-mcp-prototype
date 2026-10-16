import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../models/flow.dart';
import '../services/api_client.dart';
import '../services/app_route.dart';
import '../theme/cumulus_theme.dart';
import '../widgets/cumulus_shell.dart';
import '../widgets/mini_charts.dart';
import '../widgets/skeleton.dart';
import '../widgets/ui_kit.dart';
import 'flow_detail_screen.dart';

const _months = [
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
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

String percent(double? v) =>
    v == null ? '—' : '${(v * 100).toStringAsFixed(1)}%';

/// `1240` -> `1,240`. A run count is read as a magnitude, and grouped digits
/// are how a magnitude is read at a glance.
String thousands(int n) {
  final digits = n.abs().toString();
  final buffer = StringBuffer(n < 0 ? '-' : '');
  for (var i = 0; i < digits.length; i++) {
    if (i > 0 && (digits.length - i) % 3 == 0) buffer.write(',');
    buffer.write(digits[i]);
  }
  return buffer.toString();
}

/// How long ago, in the coarsest unit that still says something: `4m ago`,
/// `3h ago`, `6d ago`. Past a year it is `1y+ ago` rather than a day count
/// nobody reads.
String ago(DateTime then, DateTime now) {
  final gap = now.difference(then);
  if (gap.inMinutes < 1) return 'just now';
  if (gap.inMinutes < 60) return '${gap.inMinutes}m ago';
  if (gap.inHours < 24) return '${gap.inHours}h ago';
  if (gap.inDays < 365) return '${gap.inDays}d ago';
  return '1y+ ago';
}

CumulusTone statusTone(String status) => switch (status) {
  'active' => CumulusTone.success,
  'paused' => CumulusTone.warn,
  'deprecated' => CumulusTone.danger,
  'undeclared' => CumulusTone.teal,
  _ => CumulusTone.neutral,
};

/// `event` + `InvoiceReceived` -> `Event (InvoiceReceived)`.
///
/// Every flow in this estate is event-driven, so this reads `Event (…)`
/// everywhere today. The other arms are not decoration: the value is derived
/// from the export's own trigger step, so an authored schedule or webhook
/// renders as itself the moment one exists.
String triggerLabel(String kind, String detail) {
  if (kind.isEmpty) return '—';
  final name = switch (kind) {
    'event' => 'Event',
    'schedule' => 'Cron',
    'webhook' => 'Webhook',
    'manual' => 'Manual',
    _ => kind,
  };
  return detail.isEmpty ? name : '$name ($detail)';
}

// ---- health ---------------------------------------------------------------

/// The one-word verdict the Health column badges, as the server computes it.
///
/// `idle` is a real answer, not a gap: a flow that has finished nothing has no
/// success rate, and a dashboard that calls that HEALTHY is indistinguishable
/// from one reporting a flow that is genuinely fine.
CumulusTone healthTone(String health) => switch (health) {
  'healthy' => CumulusTone.success,
  'degraded' => CumulusTone.warn,
  'failed' => CumulusTone.danger,
  _ => CumulusTone.neutral,
};

/// `no_runs` -> `NO RUNS`. The badge says what the state is rather than
/// naming a mood: the word it replaced, `idle`, left a reader working out
/// whether the flow was broken, switched off, or simply quiet.
String healthLabel(String health) => health == 'no_runs' ? 'no runs' : health;

/// Whether a row is tinted and ruled. Only the two verdicts that mean
/// something is actually wrong — a draft has not been let out to run, and an
/// idle flow has done nothing to judge.
bool isUnhealthy(String health) => health == 'degraded' || health == 'failed';

/// The measures the fleet canvas can show, each backed by a real daily series
/// on `/flows/stats`, and each reached from its own metric card. The cards are
/// the only control for the series — the chart carries no second set to fall
/// out of step with them.
enum FleetMetric {
  runs('Runs / day', 'execution volume'),
  spend('Model spend / day', 'from audited ModelCalls'),
  errors('Errors / day', 'runs that ended in failure'),
  judgment('Tasks raised / day', 'work routed to a person');

  final String title;
  final String caption;

  const FleetMetric(this.title, this.caption);
}

/// The pre-filtered cuts of the fleet an operator reaches for.
enum FleetView {
  all('All'),
  attention('Needs attention'),
  active('Active'),
  drafts('Drafts');

  final String label;

  const FleetView(this.label);
}

bool inFleetView(FlowSummary w, FleetView view) => switch (view) {
  FleetView.all => true,
  // What the tab selects is exactly what the Health column shows, because
  // the badge is now the only anomaly signal on the row. It used to also
  // catch flows with no eval spec and no declarative export; those are
  // governance gaps rather than execution faults, and with their pills gone
  // from the row a tab that still counted them selected rows that looked
  // fine.
  FleetView.attention => isUnhealthy(w.health),
  FleetView.active => w.status == 'active',
  FleetView.drafts => w.status == 'draft',
};

/// The rows one view shows, in the order it shows them. Every view but the
/// exception list keeps the server's order — busiest first. On the exception
/// list the worst comes first instead: a failing flow outranks a degraded one
/// whatever its run volume.
List<FlowSummary> fleetRows(FleetView view, List<FlowSummary> scoped) {
  final rows = scoped.where((w) => inFleetView(w, view)).toList();
  if (view != FleetView.attention) return rows;
  int severity(FlowSummary w) => w.health == 'failed' ? 0 : 1;
  // A stable sort, so two flows of equal severity keep the run order the
  // server put them in rather than swapping places between refreshes.
  final indexed = [for (var i = 0; i < rows.length; i++) (i, rows[i])]
    ..sort((a, b) {
      final bySeverity = severity(a.$2).compareTo(severity(b.$2));
      return bySeverity != 0 ? bySeverity : a.$1.compareTo(b.$1);
    });
  return [for (final entry in indexed) entry.$2];
}

/// The four measures the grid can be ordered by — the same four the hero
/// tiles show, which is the only reason they are worth clicking.
enum FleetSort {
  runs('Runs'),
  spend('Spend'),
  errors('Errors'),
  awaiting('Awaiting');

  final String label;

  const FleetSort(this.label);
}

/// Order [rows] by one measure, or hand them back untouched.
///
/// A null [key] means the order the caller was given, which is the server's:
/// busiest first, or worst-first on the exception cut. Sorting is a client cut
/// on a list already in hand, not another round trip.
///
/// Every measure is now a count or an amount, so every row has an honest
/// place in every ordering. The success *rate* this replaced did not: a flow
/// that had finished nothing had no rate, and it had to be parked at the
/// bottom in both directions rather than pretend to be a zero or a one.
List<FlowSummary> sortFleetRows(
  List<FlowSummary> rows,
  FleetSort? key,
  bool ascending,
) {
  if (key == null) return rows;
  num measure(FlowSummary w) => switch (key) {
    FleetSort.runs => w.runsWindow,
    FleetSort.spend => w.costWindowUsd,
    FleetSort.errors => w.errorsWindow,
    FleetSort.awaiting => w.tasksOpen,
  };
  final sorted = [...rows];
  sorted.sort((a, b) {
    final by = measure(a).compareTo(measure(b));
    return ascending ? by : -by;
  });
  return sorted;
}

/// The windows the global selector offers. Anything the server will accept
/// would work; these are the three an operator actually reaches for.
const fleetWindows = [30, 60, 90];

// ---- grid geometry --------------------------------------------------------

/// Column widths shared by the pinned header and every row. They live in one
/// place because a header that drifts from its rows by two pixels is a table
/// that reads as broken at every scroll position.
abstract final class FleetCol {
  static const health = 106.0;
  static const lastRun = 96.0;
  static const runs = 84.0;
  static const spend = 92.0;
  static const errors = 84.0;
  static const awaiting = 92.0;
  static const gap = 12.0;
  static const rowPadding = 16.0;

  /// The coloured rule down an unhealthy row's leading edge.
  static const rule = 2.5;
}

/// Right-aligned figures with tabular numerals, so digits line up down the
/// grid instead of drifting with the width of a 1 against a 0. The four of
/// them are the same four measures as the hero tiles, in the same order.
///
/// Public because the run grid on a flow's own page is the same table in a
/// different subject: two definitions of "a figure in a grid" would drift on
/// the first weight change, and a console whose two tables set numbers
/// differently reads as two consoles.
const gridFigures = TextStyle(
  fontSize: 12.5,
  fontWeight: FontWeight.w600,
  color: CumulusColors.ink,
  fontFeatures: [FontFeature.tabularFigures()],
);

/// Which optional column a given width can seat. Only the last run is ever
/// dropped: the other four are the hero metrics, and a table that stops
/// mirroring them at a narrow width is a table that stops being the point.
class FleetColumns {
  final bool lastRun;

  const FleetColumns({required this.lastRun});

  factory FleetColumns.forWidth(double width) =>
      FleetColumns(lastRun: width >= 900);
}

// A pinned sliver has to state its height before its child is laid out, so
// the toolbar is built from explicit rows rather than a Wrap and every
// arrangement's height is stated here. A test renders all three.
const _controlHeight = 34.0;
const _headHeight = 34.0;
const _stickyTopPad = 12.0;
const _stickyGap = 10.0;
const _rowGap = 8.0;

/// Above this the metric tiles sit beside the canvas rather than above it.
const _sideBySideWidth = 1080.0;

/// How tall the telemetry card's contents are when the two sit side by side:
/// two rows of tiles, and a plot the same height as both of them together.
const _heroHeight = 184.0;

/// Above this the search and every control share one line. Set from what the
/// controls actually measure — four tabs carrying counts and two dropdowns
/// sized to their widest option come to roughly a thousand pixels — not from a
/// round number: below this they do fit, but only by pushing the search out.
const _oneRowWidth = 1500.0;

/// Above this the search takes a line and the tabs and dropdowns share a
/// second. Below it they need a line each.
const _twoRowWidth = 1100.0;

int stickyRowsFor(double width) =>
    width >= _oneRowWidth ? 1 : (width >= _twoRowWidth ? 2 : 3);

/// The exact height of the pinned block at a given width: the table card's top
/// border, the toolbar rows, and the column heads. [FleetDashboard] hands this
/// to the sliver and lays the same rows out inside it, so the two are one rule
/// rather than two that can drift.
double stickyExtentFor(double width) {
  final rows = stickyRowsFor(width);
  return 1 + // the card's top hairline
      _stickyTopPad +
      (_controlHeight * rows) +
      (_rowGap * (rows - 1)) +
      _stickyGap +
      _headHeight +
      1; // the hairline under the column heads
}

/// The Flows nav section: a fleet command centre, with drill-down into one
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

  /// Whether a fetch is in flight. It is not a gate: the skeleton is shown by
  /// having nothing to show, so every fetch after the first runs underneath
  /// the rows already on screen and only the header's control says so.
  bool _refreshing = false;
  String? _error;

  /// The window every figure on the page is measured over. It lives here and
  /// not in the dashboard because changing it is a refetch: the tiles, the
  /// canvas and every numeric column are all cut from the same server call,
  /// which is the only way they can be guaranteed to agree.
  int _days = 30;

  /// The definition currently drilled into, and which of its tabs opens.
  ///
  /// Mirrored from [AppRouteState], not owned here: the address bar and this
  /// screen have to agree about where the reader is, and two copies of that
  /// answer is how a back button starts lying.
  String? _detailId;
  int _detailTab = 0;

  @override
  void initState() {
    super.initState();
    AppRouteState.instance.addListener(_onRoute);
    // A pasted link may already name a flow, so read the location before
    // fetching anything.
    _readRoute();
    _refresh();
  }

  @override
  void dispose() {
    AppRouteState.instance.removeListener(_onRoute);
    _api.close();
    super.dispose();
  }

  /// The location moved under us — the back button, or a link opened into a
  /// session already running.
  void _onRoute() {
    if (!mounted) return;
    final route = AppRouteState.instance.value;
    // Leaving the section is the shell's business, not this screen's.
    if (route.section != 'flows') return;
    final wasList = _detailId == null;
    if (!_readRoute()) return;
    // Coming back out to the list: the fleet's figures are as old as the
    // drill-down, so they are refetched — underneath the rows the reader is
    // coming back to. Blanking a list behind a spinner on the way *back* is
    // the one refetch a reader is guaranteed to notice.
    if (_detailId == null && !wasList) _refresh();
  }

  /// Pull the location into this screen's state. Answers whether anything
  /// actually moved, so an echo of our own write changes nothing.
  bool _readRoute() {
    final route = AppRouteState.instance.value;
    final id = route.flowId.isEmpty ? null : route.flowId;
    final tab = route.flowTab.index;
    if (id == _detailId && tab == _detailTab) return false;
    setState(() {
      _detailId = id;
      _detailTab = tab;
    });
    return true;
  }

  /// Fetch the fleet. All three calls go out at once and each is read back as
  /// its own type, so a reordering cannot hand the tiles a flow list.
  Future<void> _refresh() async {
    setState(() {
      _refreshing = true;
      _error = null;
    });
    final stats = _api.flowFleetStats(days: _days);
    final flows = _api.listFlows(days: _days);
    // The role governs one control on the page behind this one, so a whoami
    // that fails leaves the role as it was rather than taking the fleet with
    // it.
    final role = _api
        .whoami()
        .then((me) => me['role'] as String? ?? 'user')
        .catchError((_) => _myRole);
    try {
      // Settled together before any one of them is read: awaiting the first
      // and never reaching the second is how the second's failure becomes an
      // unhandled error in the zone.
      await Future.wait([stats, flows, role]);
    } on ApiException catch (e) {
      if (!mounted) return;
      final stale = _stats != null;
      setState(() {
        _refreshing = false;
        // A refresh over rows already on screen leaves them there. They are
        // out of date, not wrong, and replacing a readable fleet with an
        // error page is the worse answer.
        if (!stale) _error = e.message;
      });
      if (stale) _snack(e.message);
      return;
    }
    if (!mounted) return;
    final fleet = await stats;
    final rows = await flows;
    final myRole = await role;
    setState(() {
      _stats = fleet;
      _all = rows;
      _myRole = myRole;
      _refreshing = false;
    });
  }

  void _snack(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(SnackBar(content: Text(message)));
  }

  void _setDays(int days) {
    if (days == _days) return;
    setState(() => _days = days);
    _refresh();
  }

  @override
  Widget build(BuildContext context) {
    if (_detailId != null) {
      return FlowDetailScreen(
        key: ValueKey(_detailId),
        flowId: _detailId!,
        myRole: _myRole,
        initialTab: _detailTab,
        // The window is the page's, not the fleet grid's: drilling into a
        // flow keeps whatever span the operator was reading the estate over,
        // so a run count on the detail page and the one on the row they
        // clicked cover the same days.
        days: _days,
        onTabChanged: (tab) => AppRouteState.instance.openFlow(
          _detailId!,
          tab: FlowTab.fromIndex(tab),
        ),
        onDaysChanged: (days) {
          if (days == _days) return;
          // No refetch here: the fleet lists reload on the way back out, and
          // this screen reloads its own usage off `didUpdateWidget`.
          setState(() => _days = days);
        },
        onBack: () => AppRouteState.instance.openFlows(),
      );
    }
    if (_error != null) {
      return PageBody(
        children: [ErrorNotice(message: _error!, onRetry: _refresh)],
      );
    }
    final stats = _stats;
    // Only a genuinely cold page gets the skeleton. Every later fetch — a
    // changed window, a return from a flow — runs under the fleet already on
    // screen.
    if (stats == null) {
      return _FleetSkeleton(
        days: _days,
        onDaysChanged: _setDays,
        onRefresh: _refresh,
      );
    }
    return FleetDashboard(
      stats: stats,
      flows: _all,
      days: _days,
      refreshing: _refreshing,
      onDaysChanged: _setDays,
      onRefresh: _refresh,
      onOpen: (flowId, tab) => AppRouteState.instance.openFlow(
        flowId,
        tab: FlowTab.fromIndex(tab),
      ),
    );
  }
}

// ---- the page before its data ---------------------------------------------

/// The page's name and the one control that acts on the whole page. The
/// fleet's size, its declared/undeclared split and the window length all used
/// to sit here; every one of them is stated by a metric tile or a column
/// caption a few pixels below, and a header that repeats the page is a line
/// the eye learns to skip.
///
/// One definition, used by the loaded page and by the skeleton in front of
/// it: the heading waits on nothing, so it must be the same heading in the
/// same place before and after the figures land.
class _FleetHeader extends StatelessWidget {
  final int days;
  final ValueChanged<int> onDaysChanged;
  final VoidCallback onRefresh;
  final bool refreshing;

  const _FleetHeader({
    required this.days,
    required this.onDaysChanged,
    required this.onRefresh,
    required this.refreshing,
  });

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        const Text(
          'Flows',
          style: TextStyle(
            fontSize: 20,
            fontWeight: FontWeight.w600,
            letterSpacing: -0.4,
            color: CumulusColors.ink,
          ),
        ),
        const SizedBox(width: 10),
        // Barely there on purpose. It answers one question a newcomer has —
        // am I looking at my flows or everyone's — and then gets out of the
        // way of the numbers.
        Text(
          'every flow in the enterprise',
          style: CumulusText.meta.copyWith(
            fontSize: 11.5,
            color: CumulusColors.inkMuted.withValues(alpha: 0.75),
          ),
        ),
        const Spacer(),
        // One window for the whole page. It sits in the header rather than on
        // the chart because it governs the tiles, the canvas and every
        // numeric column at once — a selector inside the chart would look
        // like it only moved the chart.
        WindowSelector(days: days, onChanged: onDaysChanged),
        const SizedBox(width: 6),
        // A fetch in flight is said here, in the control that started one,
        // and at the same size — so the whole page no longer has to be
        // replaced by a spinner to say that a number is on its way.
        IconButton(
          tooltip: refreshing ? 'Refreshing…' : 'Refresh',
          onPressed: refreshing ? null : onRefresh,
          visualDensity: VisualDensity.compact,
          icon: refreshing
              ? const SizedBox(
                  width: 17,
                  height: 17,
                  child: CircularProgressIndicator(strokeWidth: 2),
                )
              : const Icon(Icons.refresh, size: 17),
        ),
      ],
    );
  }
}

/// The fleet page with its first fetch still in flight.
///
/// Everything that does not depend on the answer is the real thing — the
/// heading, the window selector, the filter box, the column captions — so the
/// page arrives at its final geometry immediately and only the figures fade
/// in. Only a cold page renders this: a refetch happens under the rows.
class _FleetSkeleton extends StatelessWidget {
  final int days;
  final ValueChanged<int> onDaysChanged;
  final VoidCallback onRefresh;

  const _FleetSkeleton({
    required this.days,
    required this.onDaysChanged,
    required this.onRefresh,
  });

  /// A stand-in for a control that cannot be drawn until there is something
  /// to count — the view tabs and the two attribute filters.
  static Widget _control(double width) => SkeletonBar(
    width: width,
    height: _controlHeight,
    radius: CumulusRadius.pill,
    color: CumulusColors.surfaceMuted,
  );

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final width = constraints.maxWidth;
        return SingleChildScrollView(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              PageGutter(
                padding: const EdgeInsets.fromLTRB(14, 12, 28, 0),
                child: _FleetHeader(
                  days: days,
                  onDaysChanged: onDaysChanged,
                  onRefresh: onRefresh,
                  refreshing: true,
                ),
              ),
              PageGutter(
                padding: const EdgeInsets.fromLTRB(14, 10, 28, 0),
                child: _telemetry(width),
              ),
              const SizedBox(height: 14),
              PageGutter(
                padding: const EdgeInsets.fromLTRB(14, 0, 28, 0),
                child: _tableTop(width),
              ),
              PageGutter(
                padding: const EdgeInsets.fromLTRB(14, 0, 28, 0),
                child: _tableRows(),
              ),
            ],
          ),
        );
      },
    );
  }

  /// The telemetry card's frame is real and its contents are geometry, so the
  /// card does not resize under the tiles when they arrive.
  Widget _telemetry(double width) {
    final fill = width >= _sideBySideWidth;
    final tiles = Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      mainAxisSize: fill ? MainAxisSize.max : MainAxisSize.min,
      spacing: 10,
      children: [
        for (var row = 0; row < 2; row++)
          if (fill)
            Expanded(child: _tilePair())
          else
            IntrinsicHeight(child: _tilePair()),
      ],
    );
    final chart = Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      mainAxisSize: fill ? MainAxisSize.max : MainAxisSize.min,
      children: [
        const SkeletonBar(width: 128, height: 13),
        const SizedBox(height: 8),
        const SkeletonBar(width: 168, height: 10),
        const SizedBox(height: 14),
        if (fill)
          const Expanded(
            child: SkeletonBar(
              height: double.infinity,
              radius: CumulusRadius.control,
              color: CumulusColors.surfaceMuted,
            ),
          )
        else
          const SkeletonBar(
            height: 148,
            radius: CumulusRadius.control,
            color: CumulusColors.surfaceMuted,
          ),
      ],
    );
    return CumulusCard(
      padding: const EdgeInsets.all(16),
      child: SkeletonPulse(
        child: fill
            ? SizedBox(
                height: _heroHeight,
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    SizedBox(width: 420, child: tiles),
                    const SizedBox(width: 18),
                    Expanded(child: chart),
                  ],
                ),
              )
            : Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [tiles, const SizedBox(height: 16), chart],
              ),
      ),
    );
  }

  Widget _tilePair() => const Row(
    crossAxisAlignment: CrossAxisAlignment.stretch,
    spacing: 10,
    children: [
      Expanded(child: _MetricTileSkeleton()),
      Expanded(child: _MetricTileSkeleton()),
    ],
  );

  /// The table card's pinned top, exactly as [StickyGridHeader] paints it —
  /// with the real column captions, because a column is a column before
  /// anything has been counted into it.
  Widget _tableTop(double width) {
    final controls = switch (stickyRowsFor(width)) {
      1 => [
        SizedBox(
          height: _controlHeight,
          child: Row(
            children: [
              ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 340),
                child: const _SearchChrome(),
              ),
              const Spacer(),
              _tabStandIns(),
              const SizedBox(width: 10),
              _filterStandIns(),
            ],
          ),
        ),
      ],
      2 => [
        const SizedBox(height: _controlHeight, child: _SearchChrome()),
        const SizedBox(height: _rowGap),
        SizedBox(
          height: _controlHeight,
          child: Row(
            children: [_tabStandIns(), const Spacer(), _filterStandIns()],
          ),
        ),
      ],
      _ => [
        const SizedBox(height: _controlHeight, child: _SearchChrome()),
        const SizedBox(height: _rowGap),
        SizedBox(
          height: _controlHeight,
          child: Align(alignment: Alignment.centerLeft, child: _tabStandIns()),
        ),
        const SizedBox(height: _rowGap),
        SizedBox(
          height: _controlHeight,
          child: Align(
            alignment: Alignment.centerLeft,
            child: _filterStandIns(),
          ),
        ),
      ],
    };
    return Container(
      decoration: const BoxDecoration(
        color: CumulusColors.surface,
        borderRadius: BorderRadius.vertical(
          top: Radius.circular(CumulusRadius.card),
        ),
        border: Border.fromBorderSide(BorderSide(color: CumulusColors.border)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const SizedBox(height: _stickyTopPad),
          Padding(
            padding: const EdgeInsets.symmetric(
              horizontal: FleetCol.rowPadding,
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: controls,
            ),
          ),
          const SizedBox(height: _stickyGap),
          SizedBox(
            height: _headHeight,
            child: FleetGridHead(
              columns: FleetColumns.forWidth(width),
              // No count yet, and a caption reading "0 of 0" would be a
              // measurement rather than a gap.
              caption: '',
            ),
          ),
        ],
      ),
    );
  }

  /// Four view tabs, at the widths their labels and counts come to.
  Widget _tabStandIns() => Row(
    mainAxisSize: MainAxisSize.min,
    children: [
      for (final width in const [78.0, 152.0, 96.0, 92.0])
        Padding(
          padding: const EdgeInsets.only(right: 6),
          child: _control(width),
        ),
    ],
  );

  Widget _filterStandIns() => Row(
    mainAxisSize: MainAxisSize.min,
    children: [_control(104), const SizedBox(width: 6), _control(96)],
  );

  /// A dozen rows of geometry inside the card's own side rules, on the fleet
  /// grid's column proportions.
  Widget _tableRows() => Container(
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
        padding: EdgeInsets.symmetric(horizontal: FleetCol.rowPadding - 4),
        child: SkeletonTableRows(rows: 12, columnFlex: [3, 9, 2, 2, 2, 2, 2]),
      ),
    ),
  );
}

/// One metric tile as geometry: the recessed fill is real, the label, figure
/// and caption are bars at the weights [MetricTile] sets them in.
class _MetricTileSkeleton extends StatelessWidget {
  const _MetricTileSkeleton();

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.fromLTRB(13, 10, 13, 10),
      decoration: BoxDecoration(
        color: CumulusColors.surfaceMuted,
        borderRadius: BorderRadius.circular(CumulusRadius.control),
      ),
      child: const Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisAlignment: MainAxisAlignment.center,
        mainAxisSize: MainAxisSize.min,
        children: [
          SkeletonBar(width: 58, height: 9),
          SizedBox(height: 8),
          SkeletonBar(width: 86, height: 20),
          SizedBox(height: 7),
          SkeletonBar(width: 108, height: 9),
        ],
      ),
    );
  }
}

/// The filter box before there is anything to filter: the real decoration,
/// with no field behind it to take a keystroke that the empty box replacing it
/// would then throw away.
class _SearchChrome extends StatelessWidget {
  const _SearchChrome();

  @override
  Widget build(BuildContext context) {
    return const IgnorePointer(
      child: InputDecorator(
        isEmpty: true,
        decoration: InputDecoration(
          hintText: 'Search flows, tags, or teams…',
          hintStyle: TextStyle(fontSize: 12.5, color: CumulusColors.inkMuted),
          isDense: true,
          contentPadding: EdgeInsets.symmetric(horizontal: 10, vertical: 8),
          prefixIcon: Icon(Icons.search, size: 15),
          prefixIconConstraints: BoxConstraints(
            minWidth: 32,
            minHeight: _controlHeight,
          ),
          suffixIcon: Padding(
            padding: EdgeInsets.only(right: 8),
            child: _KeyCap('⌘/'),
          ),
          suffixIconConstraints: BoxConstraints(
            minWidth: 30,
            minHeight: _controlHeight,
          ),
        ),
      ),
    );
  }
}

/// The fleet command centre, over data someone else fetched.
///
/// Two cards. The telemetry card holds the four metric tiles and the trend
/// canvas side by side, and scrolls away. The table card — search, filters,
/// column heads, rows — begins below it and pins its own top edge under the
/// shell's nav bar on the way past, so the controls and captions stay
/// reachable at any depth while the rows run off the bottom of the screen.
class FleetDashboard extends StatefulWidget {
  final FleetStats stats;
  final List<FlowSummary> flows;

  /// The window every figure on this page covers, and how to change it.
  final int days;
  final ValueChanged<int> onDaysChanged;
  final VoidCallback onRefresh;

  /// Whether a fetch is running over the rows already on screen. Coming back
  /// from a flow refetches the fleet, and it does it under the list rather
  /// than in place of it — so the header's own control is the only thing that
  /// says so.
  final bool refreshing;

  /// Open one flow, on the tab the caller names: 0 overview, 1 canvas, 2 runs.
  final void Function(String flowId, int tab) onOpen;

  /// The clock the relative timestamps are read against. Injected so a test
  /// can state "four minutes ago" instead of hoping the wall clock cooperates.
  final DateTime? now;

  const FleetDashboard({
    super.key,
    required this.stats,
    required this.flows,
    required this.onRefresh,
    required this.onOpen,
    this.days = 30,
    this.onDaysChanged = _ignoreDays,
    this.refreshing = false,
    this.now,
  });

  static void _ignoreDays(int _) {}

  @override
  State<FleetDashboard> createState() => _FleetDashboardState();
}

class _FleetDashboardState extends State<FleetDashboard> {
  final TextEditingController _search = TextEditingController();
  final FocusNode _searchFocus = FocusNode();
  final ScrollController _scroll = ScrollController();

  /// Whether the page has moved, which is when the pinned block needs to read
  /// as being in front of the rows rather than part of them.
  ///
  /// Tracked off the controller rather than taken from the sliver's own
  /// `overlapsContent`: that flag is about nested scroll views overlapping
  /// each other and stays false for a pinned header in a plain
  /// `CustomScrollView`, so a shadow driven by it never appeared at all.
  ///
  /// A notifier rather than a field, because it is read by exactly one
  /// shadow: setting it used to rebuild the whole grid to paint one edge.
  final ValueNotifier<bool> _lifted = ValueNotifier(false);

  /// Which row the pointer is over. Held once here rather than as a `State`
  /// per row, so a fleet of three hundred is three hundred stateless rows and
  /// one piece of hover state.
  final ValueNotifier<String?> _hovered = ValueNotifier(null);

  FleetView _view = FleetView.all;

  /// The one piece of state the metric tiles and the chart both read. Binding
  /// both to it is what keeps the highlighted tile and the plotted series from
  /// ever disagreeing.
  FleetMetric _metric = FleetMetric.runs;
  String? _team;
  String? _tag;

  /// Which measure the grid is ordered by, and which way. Null is the order
  /// the server sent — busiest first — which is a better default than any of
  /// the four, so a click can always get back to it.
  FleetSort? _sort;
  bool _sortAscending = false;

  /// Descending, then ascending, then back to the server's order. Three
  /// clicks returns you to where you started, so nothing is a one-way door.
  void _toggleSort(FleetSort key) {
    setState(() {
      if (_sort != key) {
        _sort = key;
        _sortAscending = false;
      } else if (!_sortAscending) {
        _sortAscending = true;
      } else {
        _sort = null;
      }
    });
  }

  @override
  void initState() {
    super.initState();
    _search.addListener(_onSearchChanged);
    _scroll.addListener(_onScroll);
    HardwareKeyboard.instance.addHandler(_onKey);
    _indexFacets();
  }

  @override
  void didUpdateWidget(FleetDashboard old) {
    super.didUpdateWidget(old);
    // A refetch hands over a new list; nothing else can change what teams and
    // tags exist.
    if (!identical(old.flows, widget.flows)) _indexFacets();
  }

  @override
  void dispose() {
    HardwareKeyboard.instance.removeHandler(_onKey);
    _searchDebounce?.cancel();
    _scroll.dispose();
    _search.dispose();
    _searchFocus.dispose();
    _lifted.dispose();
    _hovered.dispose();
    super.dispose();
  }

  /// A few pixels of travel, not one: a shadow that flickers on at the first
  /// stray wheel tick reads as a glitch rather than as depth.
  void _onScroll() => _lifted.value = _scroll.offset > 8;

  // ---- what the grid is cut by --------------------------------------------

  /// The query the grid is actually filtered by, which trails the field by a
  /// debounce.
  String _query = '';
  Timer? _searchDebounce;

  /// Long enough to swallow a burst of typing, short enough that a pause
  /// reads as instant.
  static const _searchDelay = Duration(milliseconds: 150);

  /// The first character of a burst is applied on the frame it was typed in
  /// and the rest are coalesced, so the fleet is re-cut twice for a word
  /// rather than once per keystroke — and the box never feels laggy from the
  /// very first letter.
  void _onSearchChanged() {
    if (_searchDebounce?.isActive ?? false) {
      _searchDebounce!.cancel();
    } else {
      _applyQuery();
    }
    _searchDebounce = Timer(_searchDelay, _applyQuery);
  }

  void _applyQuery() {
    final next = _search.text.trim().toLowerCase();
    if (!mounted || next == _query) return;
    setState(() => _query = next);
  }

  /// The teams and tags the filters offer, indexed when the fleet arrives
  /// rather than sorted afresh on every frame.
  List<String> _teams = const [];
  List<String> _tags = const [];

  void _indexFacets() {
    _teams = _all.map((w) => w.team).where((t) => t.isNotEmpty).toSet().toList()
      ..sort();
    _tags = _all.expand((w) => w.tags).toSet().toList()..sort();
  }

  /// The last cut and what produced it. Filtering and ordering three hundred
  /// flows is cheap once and wasteful sixty times a second, and a scroll, a
  /// hover or a repaint is not a change of filter.
  Object? _cutKey;
  List<FlowSummary> _scoped = const [];
  List<FlowSummary> _rows = const [];

  void _recut() {
    final key = (
      _query,
      _team,
      _tag,
      _view,
      _sort,
      _sortAscending,
      widget.flows,
    );
    if (key == _cutKey) return;
    _cutKey = key;
    // Search, team and tag — the cuts that apply to every view. The view tabs
    // count their rows against *this* list, so a tab's number is exactly what
    // clicking it will show rather than a fleet-wide total that shrinks the
    // moment you type.
    _scoped = _all.where((w) {
      if (_team != null && w.team != _team) return false;
      if (_tag != null && !w.tags.contains(_tag)) return false;
      if (_query.isNotEmpty) {
        final haystack =
            '${w.id} ${w.name} ${w.description} ${w.team} ${w.tags.join(' ')}'
                .toLowerCase();
        if (!haystack.contains(_query)) return false;
      }
      return true;
    }).toList();
    _rows = sortFleetRows(fleetRows(_view, _scoped), _sort, _sortAscending);
  }

  /// ⌘/ (⌃/ off a Mac) puts the cursor in this page's filter, beside the
  /// shell's ⌘K for the global palette.
  ///
  /// Registered on `HardwareKeyboard` rather than as a `Shortcuts` widget for
  /// the same reason the shell does it: a widget-tree binding only fires once
  /// focus is already inside that subtree, and on a page nobody has clicked
  /// yet there is no focus to route from. The handler comes off in `dispose`,
  /// so drilling into a flow takes the binding with it.
  ///
  /// Bare `/` is deliberately not bound: the shell claims it on this same
  /// channel as a second way into the palette, which is why pressing it here
  /// used to jump to the global search. ⌘L and ⌘J were the alternatives and
  /// are both unusable — browsers own them for the address bar and the
  /// download list. ⌘/ is free in Chrome, Safari and Firefox, and matches the
  /// split GitHub and Linear use: a palette to go anywhere, a find to narrow
  /// what you are on.
  bool _onKey(KeyEvent event) {
    if (event is! KeyDownEvent) return false;
    final keyboard = HardwareKeyboard.instance;
    if (event.logicalKey == LogicalKeyboardKey.slash &&
        (keyboard.isMetaPressed || keyboard.isControlPressed)) {
      _searchFocus.requestFocus();
      return true;
    }
    return false;
  }

  List<FlowSummary> get _all => widget.flows;

  void _clearSearch() {
    // Cancelled first, so the empty box is honoured on this frame rather than
    // a debounce later.
    _searchDebounce?.cancel();
    _search.clear();
  }

  void _clearFilters() {
    _searchDebounce?.cancel();
    setState(() {
      _view = FleetView.all;
      _team = null;
      _tag = null;
      _query = '';
      // Fires the listener, which finds the query already cleared above.
      _search.clear();
    });
  }

  @override
  Widget build(BuildContext context) {
    final stats = widget.stats;
    _recut();
    final rows = _rows;
    final now = widget.now ?? DateTime.now();

    return LayoutBuilder(
      builder: (context, constraints) {
        final width = constraints.maxWidth;
        final columns = FleetColumns.forWidth(width);
        return CustomScrollView(
          controller: _scroll,
          slivers: [
            SliverToBoxAdapter(
              child: PageGutter(
                padding: const EdgeInsets.fromLTRB(14, 12, 28, 0),
                child: _header(),
              ),
            ),
            SliverToBoxAdapter(
              child: PageGutter(
                padding: const EdgeInsets.fromLTRB(14, 10, 28, 0),
                child: _telemetryCard(stats, width),
              ),
            ),
            const SliverToBoxAdapter(child: SizedBox(height: 14)),
            SliverPersistentHeader(
              pinned: true,
              delegate: StickyGridHeader(
                extent: stickyExtentFor(width),
                lifted: _lifted,
                child: _controlsAndHead(columns, rows.length, width),
              ),
            ),
            if (rows.isEmpty)
              SliverToBoxAdapter(
                child: PageGutter(
                  padding: const EdgeInsets.fromLTRB(14, 0, 28, 0),
                  child: _emptyState(),
                ),
              )
            else
              SliverList.builder(
                itemCount: rows.length,
                itemBuilder: (context, i) => PageGutter(
                  padding: const EdgeInsets.fromLTRB(14, 0, 28, 0),
                  child: FleetRow(
                    flow: rows[i],
                    columns: columns,
                    now: now,
                    hovered: _hovered,
                    onOpen: () => widget.onOpen(rows[i].id, 0),
                  ),
                ),
              ),
          ],
        );
      },
    );
  }

  Widget _header() => _FleetHeader(
    days: widget.days,
    onDaysChanged: widget.onDaysChanged,
    onRefresh: widget.onRefresh,
    refreshing: widget.refreshing,
  );

  // ---- telemetry card -----------------------------------------------------

  /// Metric tiles and the canvas in one card: the tiles are the chart's
  /// controls, so putting them on separate surfaces made the reader work out
  /// that clicking one changed the other.
  Widget _telemetryCard(FleetStats stats, double width) {
    final fill = width >= _sideBySideWidth;
    final tiles = _metricTiles(stats, fill: fill);
    final chart = _chart(stats, fill: fill);
    return CumulusCard(
      padding: const EdgeInsets.all(16),
      // Side by side the block has a stated height and both halves fill it,
      // which is what keeps the four tiles the same height as the plot beside
      // them. Stacked, there is no height to fill: the tiles size to their
      // content and the plot takes its own. A flex child in that second case
      // is an unbounded-constraint crash, not a layout.
      child: width >= _sideBySideWidth
          ? SizedBox(
              height: _heroHeight,
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  SizedBox(width: 420, child: tiles),
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
  }

  /// Two by two, so the block is as tall as the chart beside it rather than a
  /// strip running the width of the page.
  Widget _metricTiles(FleetStats stats, {required bool fill}) {
    final tiles = [
      MetricTile(
        label: 'Runs',
        value: thousands(stats.runs),
        // Rounded to a whole number, eleven runs in a month came out as
        // "≈0 per day" next to a headline of 11 — a rate that reads as
        // "nothing is happening" over a fleet that is running.
        // The window is stated once, by the selector in the header. The
        // caption gives the shape of the traffic inside it.
        caption: stats.runs == 0
            ? 'nothing ran in this window'
            : stats.runs < stats.days
            ? '${stats.runs} in ${stats.days} days'
            : '≈${(stats.runs / stats.days).toStringAsFixed(1)} per day',
        selected: _metric == FleetMetric.runs,
        onTap: () => setState(() => _metric = FleetMetric.runs),
      ),
      MetricTile(
        label: 'Model spend',
        value: money(stats.modelSpendUsd),
        // A spend of zero means two opposite things and the tile has to say
        // which. `unrecorded` is a hole — calls happened and nothing priced
        // them. `no-model-calls` is a real zero.
        caption: switch (stats.costBasis) {
          'unrecorded' =>
            '${compactCount(stats.modelCalls)} calls, none priced',
          'no-model-calls' => 'nothing called a model',
          _ =>
            '${compactCount(stats.pricedModelCalls)} of '
                '${compactCount(stats.modelCalls)} priced',
        },
        selected: _metric == FleetMetric.spend,
        onTap: () => setState(() => _metric = FleetMetric.spend),
      ),
      // A count, not a rate. 97% reads as fine over a thousand runs and as
      // catastrophic over thirty; "12 failed" reads the same either way, and
      // it is the number somebody can go and look at.
      MetricTile(
        label: 'Errors',
        value: thousands(stats.failed),
        caption: stats.runs == 0
            ? 'nothing ran in this window'
            : stats.failed == 0
            ? 'every finished run succeeded'
            : '${percent(stats.successRate)} of finished runs succeeded',
        valueColor: stats.failed > 0 ? CumulusColors.danger : null,
        selected: _metric == FleetMetric.errors,
        onTap: () => setState(() => _metric = FleetMetric.errors),
      ),
      // Scoped server-side to the queues this persona is bound to — the same
      // slice My Tasks takes. Unscoped, an AE bound to nothing read
      // "Awaiting judgment 21" beside a queue reading "clear".
      MetricTile(
        label: 'Awaiting judgment',
        value: thousands(stats.tasksOpen),
        caption: 'open tasks in your queues',
        valueColor: stats.tasksOpen > 0 ? CumulusColors.warn : null,
        selected: _metric == FleetMetric.judgment,
        onTap: () => setState(() => _metric = FleetMetric.judgment),
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

  Widget _chart(FleetStats stats, {required bool fill}) {
    // No collapse control and no measure picker. The tiles beside it are the
    // only thing that changes the series, and a canvas that shares its card
    // with its own controls has nothing to hide from.
    DailyBarChart plot(double height) => DailyBarChart(
      title: _metric.title,
      showTitle: false,
      height: height,
      format: _axisFormat,
      points: _points(stats),
    );
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      mainAxisSize: fill ? MainAxisSize.max : MainAxisSize.min,
      children: [
        SectionHeader(title: _metric.title, subtitle: _metric.caption),
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

  String _axisFormat(double v) => switch (_metric) {
    FleetMetric.spend => money(v),
    _ => v.round().toString(),
  };

  List<ChartPoint> _points(FleetStats stats) {
    return [
      for (final d in stats.series)
        switch (_metric) {
          FleetMetric.runs => ChartPoint(
            dayLabel(d.date),
            d.runs.toDouble(),
            extra: [
              '${money(d.costUsd)} model spend',
              '${d.tasksRaised} tasks raised',
            ],
          ),
          FleetMetric.spend => ChartPoint(
            dayLabel(d.date),
            d.costUsd,
            extra: ['${d.runs} runs'],
          ),
          // A day on which nothing finished has no success rate. It plots at
          // the baseline because there is nowhere else to put it, and the
          // tooltip has to say why rather than reading "0.0%".
          // A clean day plots zero, which is a real measurement here — unlike
          // a success *rate*, which had to plot a null as 0.0 and then explain
          // in the tooltip that 0% did not mean everything broke.
          FleetMetric.errors => ChartPoint(
            dayLabel(d.date),
            d.failed.toDouble(),
            extra: [
              '${d.runs} run(s) · ${d.completed} completed',
              if (d.successRate != null)
                '${percent(d.successRate)} of finished runs succeeded',
            ],
          ),
          FleetMetric.judgment => ChartPoint(
            dayLabel(d.date),
            d.tasksRaised.toDouble(),
            extra: ['${d.runs} runs'],
          ),
        },
    ];
  }

  // ---- the pinned top of the table card -----------------------------------

  Widget _controlsAndHead(FleetColumns columns, int shown, double width) {
    final search = _SearchField(
      controller: _search,
      focusNode: _searchFocus,
      onClear: _clearSearch,
    );
    final tabs = Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        for (final view in FleetView.values)
          Padding(
            padding: const EdgeInsets.only(right: 6),
            child: _ViewTab(
              label: view.label,
              count: _scoped.where((w) => inFleetView(w, view)).length,
              selected: _view == view,
              alerting: view == FleetView.attention,
              onTap: () => setState(() => _view = view),
            ),
          ),
      ],
    );
    final dropdowns = Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        if (_teams.isNotEmpty)
          _FilterDropdown(
            hint: 'All teams',
            value: _team,
            options: _teams,
            onChanged: (v) => setState(() => _team = v),
          ),
        if (_tags.isNotEmpty) ...[
          const SizedBox(width: 6),
          _FilterDropdown(
            hint: 'All tags',
            value: _tag,
            options: _tags,
            onChanged: (v) => setState(() => _tag = v),
          ),
        ],
      ],
    );

    // Explicit arrangements rather than a Wrap: the pinned sliver declares its
    // height from the width via `stickyExtentFor`, so the rows laid out here
    // have to follow the same rule and not however the children happened to
    // flow.
    final controls = switch (stickyRowsFor(width)) {
      1 => [
        SizedBox(
          height: _controlHeight,
          child: Row(
            children: [
              ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 340),
                child: search,
              ),
              const Spacer(),
              tabs,
              const SizedBox(width: 10),
              dropdowns,
            ],
          ),
        ),
      ],
      2 => [
        SizedBox(height: _controlHeight, child: search),
        const SizedBox(height: _rowGap),
        SizedBox(
          height: _controlHeight,
          child: Row(children: [tabs, const Spacer(), dropdowns]),
        ),
      ],
      _ => [
        SizedBox(height: _controlHeight, child: search),
        const SizedBox(height: _rowGap),
        SizedBox(
          height: _controlHeight,
          child: Align(alignment: Alignment.centerLeft, child: tabs),
        ),
        const SizedBox(height: _rowGap),
        SizedBox(
          height: _controlHeight,
          child: Align(alignment: Alignment.centerLeft, child: dropdowns),
        ),
      ],
    };

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        const SizedBox(height: _stickyTopPad),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: FleetCol.rowPadding),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: controls,
          ),
        ),
        const SizedBox(height: _stickyGap),
        SizedBox(
          height: _headHeight,
          child: FleetGridHead(
            columns: columns,
            caption: '$shown of ${_all.length}',
            sort: _sort,
            sortAscending: _sortAscending,
            onSort: _toggleSort,
          ),
        ),
      ],
    );
  }

  /// Inside the table card's side rules, so an empty result still reads as the
  /// table being empty rather than the table being gone.
  Widget _emptyState() => Container(
    decoration: const BoxDecoration(
      color: CumulusColors.surface,
      border: Border(
        left: BorderSide(color: CumulusColors.border),
        right: BorderSide(color: CumulusColors.border),
        bottom: BorderSide(color: CumulusColors.border),
      ),
    ),
    padding: const EdgeInsets.symmetric(vertical: 36),
    child: CumulusEmptyState(
      icon: _view == FleetView.attention
          ? Icons.verified_outlined
          : Icons.account_tree_outlined,
      title: _view == FleetView.attention
          ? 'Nothing is flagged'
          : 'Nothing matches',
      message: _view == FleetView.attention
          ? 'No flow in this cut is below the success bar, missing an eval '
                'spec, or running without a declarative export.'
          : 'No flow matches the current view and filters. Clear a filter or '
                'widen the view to see more.',
      action: TextButton(
        onPressed: _clearFilters,
        child: const Text('Clear filters'),
      ),
    ),
  );
}

/// The top of a table card — its rounded lip, the toolbar and the column
/// heads — held against the top of the viewport while the rows run under it.
///
/// Used by the fleet grid here and by the run grid on a flow's own page: one
/// pinned lip, so a table pins the same way wherever the console puts one.
///
/// The extent is computed from the width by [stickyExtentFor] rather than
/// measured: a pinned sliver must state its height before its child is laid
/// out, so both have to come from the same rule.
class StickyGridHeader extends SliverPersistentHeaderDelegate {
  final double extent;

  /// Whether rows are passing underneath, as something this block can watch
  /// for itself. A bool on the page's State meant a scroll past eight pixels
  /// rebuilt the whole grid to paint one shadow.
  final ValueListenable<bool> lifted;
  final Widget child;

  StickyGridHeader({
    required this.extent,
    required this.lifted,
    required this.child,
  });

  @override
  double get minExtent => extent;

  @override
  double get maxExtent => extent;

  @override
  Widget build(
    BuildContext context,
    double shrinkOffset,
    bool overlapsContent,
  ) {
    return PageGutter(
      padding: const EdgeInsets.fromLTRB(14, 0, 28, 0),
      // Opaque, or the rows scroll through it. A uniform border, because
      // Flutter refuses a borderRadius on a border whose sides differ — and a
      // uniform one is right anyway: the bottom edge here is the hairline
      // between the column heads and the first row.
      //
      // The shadow appears only once there is something underneath to pass
      // beneath it. At rest the block is the top of a card and needs no
      // elevation; the moment rows start sliding under, it has to read as
      // being in front of them rather than bleeding into them.
      //
      // Only the frame listens. The toolbar and the column heads inside it
      // are handed through untouched, so a scroll repaints an edge instead of
      // rebuilding every control on it.
      child: ValueListenableBuilder<bool>(
        valueListenable: lifted,
        child: child,
        builder: (context, raised, body) => Container(
          decoration: BoxDecoration(
            color: CumulusColors.surface,
            borderRadius: const BorderRadius.vertical(
              top: Radius.circular(CumulusRadius.card),
            ),
            border: const Border.fromBorderSide(
              BorderSide(color: CumulusColors.border),
            ),
            boxShadow: raised ? CumulusShadows.card : null,
          ),
          child: body,
        ),
      ),
    );
  }

  @override
  bool shouldRebuild(StickyGridHeader old) =>
      old.extent != extent || old.lifted != lifted || old.child != child;
}

// ---- metric tile ----------------------------------------------------------

/// One measure, as a figure you can click: label, value, one line of
/// provenance. Clicking it points the canvas beside it at that measure's own
/// daily history.
///
/// Public because a single flow's Overview heads with the same block over its
/// own telemetry. Two tiles that were meant to look identical and were
/// declared twice would stop being identical on the first weight change.
///
/// Tinted rather than white. The tiles sit inside the telemetry card, and a
/// white tile on a white card is a rectangle you have to find the edges of;
/// the recessed fill says these are objects on a surface, and the selected one
/// lifts out of it.
class MetricTile extends StatelessWidget {
  final String label;
  final String value;
  final String caption;
  final Color? valueColor;
  final bool selected;
  final VoidCallback onTap;

  const MetricTile({
    super.key,
    required this.label,
    required this.value,
    required this.caption,
    required this.selected,
    required this.onTap,
    this.valueColor,
  });

  @override
  Widget build(BuildContext context) {
    return Material(
      color: selected ? CumulusColors.accentSoft : CumulusColors.surfaceMuted,
      borderRadius: BorderRadius.circular(CumulusRadius.control),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(CumulusRadius.control),
        hoverColor: selected
            ? CumulusColors.accentSoft
            : CumulusColors.border.withValues(alpha: 0.6),
        child: Container(
          padding: const EdgeInsets.fromLTRB(13, 10, 13, 10),
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(CumulusRadius.control),
            border: Border.all(
              color: selected ? CumulusColors.accent : Colors.transparent,
              width: 1.5,
            ),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisAlignment: MainAxisAlignment.center,
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(
                label.toUpperCase(),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: CumulusText.statLabel.copyWith(
                  color: selected
                      ? CumulusColors.accentInk
                      : CumulusColors.inkMuted,
                ),
              ),
              const SizedBox(height: 5),
              Text(
                value,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                  fontSize: 22,
                  fontWeight: FontWeight.w600,
                  letterSpacing: -0.5,
                  height: 1.1,
                  color: valueColor ?? CumulusColors.ink,
                ),
              ),
              const SizedBox(height: 3),
              Text(
                caption,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(
                  fontSize: 10.5,
                  height: 1.3,
                  color: CumulusColors.inkMuted,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

// ---- toolbar pieces -------------------------------------------------------

class _SearchField extends StatelessWidget {
  final TextEditingController controller;
  final FocusNode focusNode;
  final VoidCallback onClear;

  const _SearchField({
    required this.controller,
    required this.focusNode,
    required this.onClear,
  });

  @override
  Widget build(BuildContext context) {
    // The key cap and the clear button swap on the first and last character,
    // and this box watches the controller for that itself. The grid behind it
    // is cut on a debounce, and a page that rebuilt to move one icon is what
    // made typing here cost a filter pass per keystroke.
    return ValueListenableBuilder<TextEditingValue>(
      valueListenable: controller,
      builder: (context, value, _) => TextField(
        controller: controller,
        focusNode: focusNode,
        style: const TextStyle(fontSize: 13),
        decoration: InputDecoration(
          hintText: 'Search flows, tags, or teams…',
          hintStyle: const TextStyle(
            fontSize: 12.5,
            color: CumulusColors.inkMuted,
          ),
          isDense: true,
          contentPadding: const EdgeInsets.symmetric(
            horizontal: 10,
            vertical: 8,
          ),
          prefixIcon: const Icon(Icons.search, size: 15),
          prefixIconConstraints: const BoxConstraints(
            minWidth: 32,
            minHeight: _controlHeight,
          ),
          // ⌘/ — the page's own find, next to the shell's ⌘K for the global
          // palette. Bare `/` is not available: the shell claims it as a
          // second, browser-proof way into that palette, which is why pressing
          // it here used to jump to the global search instead of this box.
          suffixIcon: value.text.isEmpty
              ? const Padding(
                  padding: EdgeInsets.only(right: 8),
                  child: _KeyCap('⌘/'),
                )
              : IconButton(
                  padding: EdgeInsets.zero,
                  constraints: const BoxConstraints(
                    minWidth: 30,
                    minHeight: 30,
                  ),
                  icon: const Icon(Icons.close, size: 13),
                  onPressed: onClear,
                ),
          suffixIconConstraints: const BoxConstraints(
            minWidth: 30,
            minHeight: _controlHeight,
          ),
        ),
      ),
    );
  }
}

class _KeyCap extends StatelessWidget {
  final String label;

  const _KeyCap(this.label);

  @override
  Widget build(BuildContext context) {
    return Center(
      widthFactor: 1,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 3),
        decoration: BoxDecoration(
          color: CumulusColors.surfaceMuted,
          borderRadius: BorderRadius.circular(6),
        ),
        child: Text(
          label,
          style: const TextStyle(
            fontSize: 11,
            fontWeight: FontWeight.w600,
            color: CumulusColors.inkMuted,
          ),
        ),
      ),
    );
  }
}

/// A pre-filtered cut of the fleet, carrying the size of the cut. The count is
/// the point: a tab reading `NEEDS ATTENTION 6` is an alert that lives in the
/// operator's line of travel instead of a banner above it.
class _ViewTab extends StatelessWidget {
  final String label;
  final int count;
  final bool selected;
  final bool alerting;
  final VoidCallback onTap;

  const _ViewTab({
    required this.label,
    required this.count,
    required this.selected,
    required this.alerting,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final warn = alerting && count > 0;
    final fg = selected
        ? Colors.white
        : warn
        ? CumulusColors.warn
        : CumulusColors.inkSecondary;
    return Material(
      color: Colors.transparent,
      borderRadius: BorderRadius.circular(CumulusRadius.pill),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(CumulusRadius.pill),
        hoverColor: CumulusColors.surfaceMuted,
        child: Container(
          height: _controlHeight,
          padding: const EdgeInsets.fromLTRB(12, 0, 11, 0),
          decoration: BoxDecoration(
            color: selected
                ? CumulusColors.ink
                : warn
                ? CumulusColors.warnSoft
                : CumulusColors.surface,
            borderRadius: BorderRadius.circular(CumulusRadius.pill),
            border: Border.all(
              color: selected
                  ? CumulusColors.ink
                  : warn
                  ? CumulusColors.warn.withValues(alpha: 0.45)
                  : CumulusColors.borderStrong,
            ),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(
                label.toUpperCase(),
                style: TextStyle(
                  fontSize: 11,
                  fontWeight: FontWeight.w600,
                  letterSpacing: 0.8,
                  color: fg,
                ),
              ),
              const SizedBox(width: 8),
              // A figure, not a chip. The pill behind the count never lined up
              // across four tabs of different label widths, and it carried no
              // information the colour does not: the count is set right
              // against a fixed box and told apart from the label by weight
              // and tone alone.
              SizedBox(
                width: 22,
                child: Text(
                  '$count',
                  textAlign: TextAlign.right,
                  style: TextStyle(
                    fontSize: 11,
                    fontWeight: FontWeight.w700,
                    color: selected
                        ? Colors.white.withValues(alpha: 0.75)
                        : warn
                        ? CumulusColors.warn
                        : CumulusColors.inkMuted,
                    fontFeatures: const [FontFeature.tabularFigures()],
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

/// The console's one timeframe control: 30 / 60 / 90 days.
///
/// Pill-shaped like the filters below it, but always "on": there is no unset
/// timeframe, so it never renders as a hint. It sits in a page header rather
/// than on a chart because it governs every number on the page.
class WindowSelector extends StatelessWidget {
  final int days;
  final ValueChanged<int> onChanged;

  const WindowSelector({
    super.key,
    required this.days,
    required this.onChanged,
  });

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
        child: DropdownButton<int>(
          value: days,
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
            for (final option in fleetWindows)
              DropdownMenuItem<int>(
                value: option,
                child: Text(
                  '$option days',
                  style: const TextStyle(fontSize: 12.5),
                ),
              ),
          ],
          onChanged: (v) => v == null ? null : onChanged(v),
        ),
      ),
    );
  }
}

/// Pill-shaped attribute filter, sized to sit level with the view tabs.
///
/// A menu button rather than a `DropdownButton`: that widget is always as wide
/// as the widest entry in its menu, so a tag list containing
/// `three-way-match` left the words "All tags" stranded at the far left of a
/// two-hundred-pixel control with nothing between them and the chevron.
/// `selectedItemBuilder` does not help — the button still measures every
/// builder's output. A pill that hugs its own label is the only thing that
/// sits naturally beside the view tabs.
class _FilterDropdown extends StatelessWidget {
  final String hint;
  final String? value;
  final List<String> options;
  final ValueChanged<String?> onChanged;

  const _FilterDropdown({
    required this.hint,
    required this.value,
    required this.options,
    required this.onChanged,
  });

  /// `PopupMenuButton` reports a null selection as a cancel, so "everything"
  /// travels as a sentinel and is mapped back on the way out.
  static const _all = '\u0000all';

  @override
  Widget build(BuildContext context) {
    final on = value != null;
    final ink = on ? CumulusColors.accentInk : CumulusColors.ink;
    return PopupMenuButton<String>(
      tooltip: '',
      position: PopupMenuPosition.under,
      onSelected: (v) => onChanged(v == _all ? null : v),
      itemBuilder: (context) => [
        PopupMenuItem<String>(
          value: _all,
          height: 36,
          child: Text(hint, style: const TextStyle(fontSize: 12.5)),
        ),
        for (final option in options)
          PopupMenuItem<String>(
            value: option,
            height: 36,
            child: Text(option, style: const TextStyle(fontSize: 12.5)),
          ),
      ],
      child: Container(
        height: _controlHeight,
        padding: const EdgeInsets.fromLTRB(12, 0, 8, 0),
        decoration: BoxDecoration(
          color: on ? CumulusColors.accentSoft : CumulusColors.surface,
          borderRadius: BorderRadius.circular(CumulusRadius.pill),
          border: Border.all(
            color: on ? CumulusColors.accent : CumulusColors.borderStrong,
          ),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            ConstrainedBox(
              // A very long tag still cannot run the toolbar off the page.
              constraints: const BoxConstraints(maxWidth: 130),
              child: Text(
                value ?? hint,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                  fontSize: 12.5,
                  fontWeight: FontWeight.w600,
                  color: ink,
                ),
              ),
            ),
            const SizedBox(width: 4),
            Icon(
              Icons.expand_more,
              size: 15,
              color: on ? CumulusColors.accentInk : CumulusColors.inkMuted,
            ),
          ],
        ),
      ),
    );
  }
}

// ---- grid head and rows ---------------------------------------------------

/// The column captions. Widths come from [FleetCol] so they stay locked to the
/// cells beneath them.
class FleetGridHead extends StatelessWidget {
  final FleetColumns columns;
  final String caption;

  /// Which measure the grid is ordered by, which way, and how to change it.
  /// A chevron appears only on the column actually doing the ordering; the
  /// rest show theirs on hover, which is how the row says it is clickable
  /// without four arrows competing with the data underneath.
  final FleetSort? sort;
  final bool sortAscending;
  final ValueChanged<FleetSort>? onSort;

  const FleetGridHead({
    super.key,
    required this.columns,
    required this.caption,
    this.sort,
    this.sortAscending = false,
    this.onSort,
  });

  static Widget cell(
    String text,
    double width, {
    TextAlign align = TextAlign.right,
  }) => SizedBox(
    width: width,
    child: Text(
      text.toUpperCase(),
      textAlign: align,
      maxLines: 1,
      overflow: TextOverflow.ellipsis,
      style: CumulusText.statLabel,
    ),
  );

  Widget _sortable(FleetSort key, double width) => _SortableHead(
    label: key.label,
    width: width,
    active: sort == key,
    ascending: sortAscending,
    onTap: onSort == null ? null : () => onSort!(key),
  );

  @override
  Widget build(BuildContext context) {
    return Padding(
      // The rule down an unhealthy row's edge is inside the card, so the
      // heads start where a row's content starts, not where its border does.
      padding: const EdgeInsets.fromLTRB(
        FleetCol.rowPadding + FleetCol.rule,
        0,
        FleetCol.rowPadding,
        0,
      ),
      child: Row(
        children: [
          cell('Health', FleetCol.health, align: TextAlign.left),
          const SizedBox(width: FleetCol.gap),
          Expanded(
            child: Row(
              children: [
                Text('FLOW', style: CumulusText.statLabel),
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
          if (columns.lastRun) ...[
            const SizedBox(width: FleetCol.gap),
            cell('Last run', FleetCol.lastRun, align: TextAlign.left),
          ],
          // The same four measures as the hero tiles, in the same order, so
          // the eye can run straight down from a tile to its column — and
          // right-aligned like the figures beneath them, so the captions and
          // the digits share one edge.
          const SizedBox(width: FleetCol.gap),
          _sortable(FleetSort.runs, FleetCol.runs),
          const SizedBox(width: FleetCol.gap),
          _sortable(FleetSort.spend, FleetCol.spend),
          const SizedBox(width: FleetCol.gap),
          _sortable(FleetSort.errors, FleetCol.errors),
          const SizedBox(width: FleetCol.gap),
          _sortable(FleetSort.awaiting, FleetCol.awaiting),
        ],
      ),
    );
  }
}

/// A numeric column caption you can order the grid by.
///
/// The chevron occupies its space whether or not it is painted, so turning a
/// sort on does not shove the caption sideways — the one thing that would
/// make a sortable header feel unstable to click.
class _SortableHead extends StatefulWidget {
  final String label;
  final double width;
  final bool active;
  final bool ascending;
  final VoidCallback? onTap;

  const _SortableHead({
    required this.label,
    required this.width,
    required this.active,
    required this.ascending,
    required this.onTap,
  });

  @override
  State<_SortableHead> createState() => _SortableHeadState();
}

class _SortableHeadState extends State<_SortableHead> {
  bool _hover = false;

  @override
  Widget build(BuildContext context) {
    final showChevron = widget.active || _hover;
    final ink = widget.active
        ? CumulusColors.ink
        : _hover
        ? CumulusColors.inkSecondary
        : CumulusColors.inkMuted;
    return SizedBox(
      width: widget.width,
      child: MouseRegion(
        cursor: widget.onTap == null
            ? SystemMouseCursors.basic
            : SystemMouseCursors.click,
        onEnter: (_) => setState(() => _hover = true),
        onExit: (_) => setState(() => _hover = false),
        child: GestureDetector(
          onTap: widget.onTap,
          behavior: HitTestBehavior.opaque,
          child: Row(
            mainAxisAlignment: MainAxisAlignment.end,
            children: [
              // The chevron sits to the *left* of the caption, not the right.
              // Reserving its space on the right pushed every numeric caption
              // thirteen pixels in from its column's edge, so the headers no
              // longer stacked with the figures under them — which is the one
              // thing a right-aligned column exists to do.
              SizedBox(
                width: 13,
                child: showChevron
                    ? Icon(
                        widget.ascending && widget.active
                            ? Icons.arrow_drop_up
                            : Icons.arrow_drop_down,
                        size: 13,
                        color: ink,
                      )
                    : null,
              ),
              Flexible(
                child: Text(
                  widget.label.toUpperCase(),
                  textAlign: TextAlign.right,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: CumulusText.statLabel.copyWith(color: ink),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// One flow, one click target, inside the table card's side rules.
///
/// Strictly the id — no description. The row is a line in a table, and a
/// second line of prose under every id is what put the grid at sixty pixels a
/// row when the fleet is meant to be read in one sweep.
///
/// No per-row buttons. Three icon targets inside a clickable row is a row you
/// cannot reliably click; the row is the target, and the detail page holds the
/// canvas, the journal and the eval spec behind their own tabs.
class FleetRow extends StatelessWidget {
  final FlowSummary flow;
  final FleetColumns columns;
  final DateTime now;
  final VoidCallback onOpen;

  /// Which row the pointer is over, held by the grid. Three hundred rows used
  /// to carry a `State` each to answer that for themselves.
  ///
  /// Null where a row is rendered outside a grid, which is the honest answer
  /// for a row that is not part of a list to move between.
  final ValueNotifier<String?>? hovered;

  const FleetRow({
    super.key,
    required this.flow,
    required this.columns,
    required this.now,
    required this.onOpen,
    this.hovered,
  });

  @override
  Widget build(BuildContext context) {
    final w = flow;
    // Only the two verdicts that mean something is wrong tint the row. A
    // draft and an idle flow are ordinary, and tinting them would spend the
    // one signal the page has on rows nobody needs to look at.
    final flagged = isUnhealthy(w.health);
    final tone = healthTone(w.health);

    // Built once, whatever the pointer does. The cells do not depend on
    // hover, so handing this same instance to the hover builder is what keeps
    // moving the mouse down the grid to repainting two rectangles a row.
    final cells = Row(
      children: [
        SizedBox(
          width: FleetCol.health,
          child: Align(
            alignment: Alignment.centerLeft,
            child: StatusBadge(
              healthLabel(w.health),
              tone: tone,
              dense: true,
              uppercase: true,
            ),
          ),
        ),
        const SizedBox(width: FleetCol.gap),
        Expanded(
          child: Text(
            w.id,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: CumulusText.mono.copyWith(
              fontSize: 12.5,
              height: 1.2,
              fontWeight: FontWeight.w600,
            ),
          ),
        ),
        if (columns.lastRun) ...[
          const SizedBox(width: FleetCol.gap),
          SizedBox(width: FleetCol.lastRun, child: _lastRun(w)),
        ],
        const SizedBox(width: FleetCol.gap),
        _figure(thousands(w.runsWindow), FleetCol.runs),
        const SizedBox(width: FleetCol.gap),
        _figure(
          money(w.costWindowUsd),
          FleetCol.spend,
          // A row that called a model and priced nothing is not a cheap row;
          // its spend is a hole, and the figure says so quietly rather than
          // passing for a measurement.
          color: w.costBasisWindow == 'unrecorded'
              ? CumulusColors.inkMuted
              : null,
        ),
        const SizedBox(width: FleetCol.gap),
        _figure(
          w.errorsWindow == 0 ? '0' : thousands(w.errorsWindow),
          FleetCol.errors,
          // Red only when something actually failed. A clean column of grey
          // zeroes is the state to hope for, and painting those green would
          // spend the eye's attention on rows with nothing to look at.
          color: w.errorsWindow > 0
              ? CumulusColors.danger
              : CumulusColors.inkMuted,
        ),
        const SizedBox(width: FleetCol.gap),
        _figure(
          w.tasksOpen == 0 ? '0' : thousands(w.tasksOpen),
          FleetCol.awaiting,
          // A plain count. Amber only when there is something in the queue,
          // and never red: a run waiting on a person is the system working as
          // designed.
          color: w.tasksOpen > 0 ? CumulusColors.warn : CumulusColors.inkMuted,
        ),
      ],
    );

    Widget painted(bool hover) {
      final tint = flagged
          ? tone.background.withValues(alpha: hover ? 0.95 : 0.55)
          : (hover
                ? CumulusColors.surfaceMuted.withValues(alpha: 0.55)
                : CumulusColors.surface);
      return Container(
        // Sides and a hairline underneath: the card's rules run down past the
        // last row and off the bottom of the viewport, because the list is
        // scrolling and a rounded foot would claim it had ended.
        decoration: const BoxDecoration(
          border: Border(
            left: BorderSide(color: CumulusColors.border),
            right: BorderSide(color: CumulusColors.border),
            bottom: BorderSide(color: CumulusColors.border),
          ),
        ),
        child: Row(
          children: [
            // The severity rule is a child, not a border side: a Border with
            // one side thicker than the others cannot carry a radius and
            // cannot change width without moving every cell beside it. As a
            // fixed-width child it always occupies the same space, coloured
            // or not, so a flagged row and a clean one stay in column.
            Container(
              width: FleetCol.rule,
              color: flagged ? tone.foreground : tint,
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
    // one row rather than of the grid it sits in.
    return RepaintBoundary(
      child: MouseRegion(
        cursor: SystemMouseCursors.click,
        onEnter: hover == null ? null : (_) => hover.value = w.id,
        onExit: hover == null
            ? null
            : (_) {
                // Only if the pointer has not already been claimed by the row
                // it moved on to.
                if (hover.value == w.id) hover.value = null;
              },
        child: GestureDetector(
          onTap: onOpen,
          behavior: HitTestBehavior.opaque,
          child: hover == null
              ? painted(false)
              : ValueListenableBuilder<String?>(
                  valueListenable: hover,
                  builder: (context, id, _) => painted(id == w.id),
                ),
        ),
      ),
    );
  }

  /// When, and only when. How that run ended is the Health badge's job and the
  /// success column's; said a third time here, one run's outcome read as the
  /// flow's condition.
  Widget _lastRun(FlowSummary w) {
    final at = DateTime.tryParse(w.lastRunAt);
    return Text(
      at == null ? 'never' : ago(at.toLocal(), now),
      maxLines: 1,
      overflow: TextOverflow.ellipsis,
      style: TextStyle(
        fontSize: 11.5,
        color: at == null ? CumulusColors.inkMuted : CumulusColors.inkSecondary,
      ),
    );
  }

  Widget _figure(String text, double width, {Color? color}) => SizedBox(
    width: width,
    child: Text(
      text,
      textAlign: TextAlign.right,
      style: color == null ? gridFigures : gridFigures.copyWith(color: color),
    ),
  );
}
