import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'models.dart';
import '../shared/workspace_chrome.dart';
import '../../models/session.dart';
import '../../services/api_client.dart';
import '../../theme/cumulus_theme.dart';
import '../../widgets/command_palette.dart' show ownsWorkspace;
import '../../widgets/run_workbench.dart';
import '../../widgets/skeleton.dart';
import '../../widgets/ui_kit.dart';

/// The desk this workspace is — the server's `desks.PLANT_SUPERVISOR`.
const _plantSupervisorQueue = 'plant-supervisor';

/// The console section slug, which is also the key the session's workspace
/// availability list is written against.
const _plantScheduleSlug = 'plant-schedule';

/// The Plant Schedule workspace — a department × day grid for one week.
///
/// The grid is drawn over the departments the governed page declares rather
/// than over the days that happen to exist, because a department with no day
/// *is* the finding: a row of absent cells is a plant that scheduled nothing,
/// and a grid assembled from what exists could not say so. An absent cell
/// therefore never reads as zero hours — it reads as no day having been
/// opened, which is a different fact and the one a supervisor has to act on.
///
/// Everything a signature needs is on the day panel and nothing is summarised
/// away: the hours against both governed bounds with the fudge band drawn, the
/// incentive as an equation a reader can check with their own arithmetic, the
/// rows with the rule that placed each one and whether a person placed it, and
/// the shares the fill deferred with the clause that deferred them.
///
/// The export is the one control here that writes nothing and still matters
/// most: it is live only on an approved day, because
/// `/api/v1/schedule/export` answers 409 to anything else, and offering a
/// button that the server refuses is how a screen teaches people to distrust
/// it.
class PlantScheduleView extends StatefulWidget {
  final ApiClient api;

  /// The acting session: whether this deployment has the workspace, and what
  /// an empty grid means when it does.
  final Session? session;

  const PlantScheduleView({super.key, required this.api, this.session});

  @override
  State<PlantScheduleView> createState() => _PlantScheduleViewState();
}

class _PlantScheduleViewState extends State<PlantScheduleView> {
  /// Whether the plant-supervisor desk is this persona's work. The grid is
  /// scoped server-side either way — this decides only what emptiness means.
  bool get _seesTheQueue =>
      ownsWorkspace(widget.session, _plantSupervisorQueue);

  PlantScheduleScreen? _screen;
  String? _error;

  /// The week being asked for, or null for "whichever week the server calls
  /// this one". Never computed here on the first read: a console that decided
  /// which seven days today falls in could disagree with the export route,
  /// and then the grid and the file would be different weeks.
  String? _week;
  bool _loading = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final screen = await widget.api.plantScheduleScreen(week: _week);
      if (mounted) {
        setState(() {
          _screen = screen;
          _week = screen.week;
          _error = null;
          _loading = false;
        });
      }
    } on ApiException catch (e) {
      if (mounted) {
        setState(() {
          _error = e.message;
          _loading = false;
        });
      }
    }
  }

  void _step(int delta) {
    final from = _screen?.week ?? _week;
    if (from == null) return;
    final next = shiftIsoWeek(from, delta);
    if (next == null) return;
    setState(() => _week = next);
    _load();
  }

  @override
  Widget build(BuildContext context) {
    final session = widget.session;
    if (session != null && !session.workspaceAvailable(_plantScheduleSlug)) {
      return CumulusEmptyState(
        icon: Icons.link_off,
        title: 'Not in this deployment',
        message: workspaceAbsenceReason(session, _plantScheduleSlug),
      );
    }
    if (_error != null) {
      return CumulusEmptyState(
        icon: Icons.cloud_off,
        title: 'Could not load the workspace',
        message: _error!,
      );
    }
    final screen = _screen;
    if (screen == null) return _skeleton();
    return PlantScheduleBody(
      screen: screen,
      seesTheQueue: _seesTheQueue,
      heading: _heading(),
      weekPicker: _weekPicker(),
      runs: FlowRunsCard(
        api: widget.api,
        flowId: 'department-scheduling',
        subtitle:
            'each fill: the shares it placed, the clause it stopped against, '
            'and the day it wrote before it asked · click one to open its '
            'journal step by step',
      ),
    );
  }

  Widget _heading() => PageHeading(
    eyebrow: 'Workspace',
    title: 'Plant Schedule',
    subtitle:
        'One week, department by department. A day is written before it is '
        'approved and exported only after — the record exists, and the door '
        'onto it stays shut until a supervisor signs it.',
    trailing: OutlinedButton.icon(
      onPressed: _loading ? null : _load,
      icon: const Icon(Icons.refresh, size: 16),
      label: const Text('Refresh'),
    ),
  );

  /// Previous / next week. The neighbouring ISO week is computed here and
  /// handed back to the server, which still decides which seven dates that
  /// week holds — so the console never becomes a second calendar.
  Widget _weekPicker() => Row(
    mainAxisSize: MainAxisSize.min,
    children: [
      IconButton(
        onPressed: _loading ? null : () => _step(-1),
        icon: const Icon(Icons.chevron_left, size: 20),
        tooltip: 'The week before',
      ),
      Text(
        _screen?.week ?? _week ?? '',
        style: CumulusText.mono.copyWith(fontSize: 13),
      ),
      IconButton(
        onPressed: _loading ? null : () => _step(1),
        icon: const Icon(Icons.chevron_right, size: 20),
        tooltip: 'The week after',
      ),
    ],
  );

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
              SkeletonStatStrip(count: 5),
              SizedBox(height: 16),
              SkeletonCard(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    SkeletonBar(width: 160, height: 13),
                    SizedBox(height: 10),
                    SkeletonBar(width: 300, height: 10),
                    SizedBox(height: 16),
                    SkeletonTableRows(
                      rows: 4,
                      columnFlex: [2, 1, 1, 1, 1, 1, 1, 1],
                    ),
                  ],
                ),
              ),
              SizedBox(height: 16),
              SkeletonCard(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    SkeletonBar(width: 200, height: 13),
                    SizedBox(height: 12),
                    SkeletonBar(height: 26, radius: CumulusRadius.control),
                    SizedBox(height: 14),
                    SkeletonBar(width: 380, height: 11),
                    SizedBox(height: 8),
                    SkeletonBar(width: 300, height: 11),
                  ],
                ),
              ),
            ],
          ),
        ),
      ],
    ),
  );
}

// ---------------------------------------------------------------------------
// The week
// ---------------------------------------------------------------------------

/// The ISO week [delta] weeks away from [week], or null when the string is not
/// an ISO week at all.
///
/// Done by moving seven days at a time over real dates rather than by adding
/// one to the week number, because the year does not have a fixed number of
/// weeks: `2026-W53` exists and `2027-W53` does not, and week arithmetic that
/// pretends otherwise silently asks the server for a week nobody has.
String? shiftIsoWeek(String week, int delta) {
  final match = RegExp(r'^(\d{4})-W(\d{1,2})$').firstMatch(week.toUpperCase());
  if (match == null) return null;
  final year = int.parse(match.group(1)!);
  final number = int.parse(match.group(2)!);
  if (number < 1 || number > 53) return null;
  final monday = _mondayOfIsoWeek(year, number).add(Duration(days: 7 * delta));
  return isoWeekOf(monday);
}

/// The Monday of an ISO week: January 4th is always in week 1, so the Monday
/// of week 1 is the Monday on or before it.
DateTime _mondayOfIsoWeek(int year, int week) {
  final jan4 = DateTime.utc(year, 1, 4);
  final mondayOfWeekOne = jan4.subtract(Duration(days: jan4.weekday - 1));
  return mondayOfWeekOne.add(Duration(days: 7 * (week - 1)));
}

/// The ISO week a date falls in, `YYYY-Www`. The Thursday decides the year,
/// which is the rule that keeps the last days of December in the first week of
/// the following year where they belong.
String isoWeekOf(DateTime day) {
  final date = DateTime.utc(day.year, day.month, day.day);
  final thursday = date.add(Duration(days: 4 - date.weekday));
  final firstThursday = DateTime.utc(thursday.year, 1, 4);
  final mondayOfWeekOne = firstThursday.subtract(
    Duration(days: firstThursday.weekday - 1),
  );
  final week = thursday.difference(mondayOfWeekOne).inDays ~/ 7 + 1;
  return '${thursday.year}-W${week.toString().padLeft(2, '0')}';
}

// ---------------------------------------------------------------------------
// The page, once the screen has landed
// ---------------------------------------------------------------------------

/// The rendered workspace, given a screen already in hand.
class PlantScheduleBody extends StatefulWidget {
  final PlantScheduleScreen screen;
  final bool seesTheQueue;
  final Widget heading;

  /// Previous / next week, passed in because stepping is the loader's job.
  final Widget? weekPicker;

  /// The flow's runs, above the grid. Null in a test.
  final Widget? runs;

  const PlantScheduleBody({
    super.key,
    required this.screen,
    required this.heading,
    this.seesTheQueue = true,
    this.weekPicker,
    this.runs,
  });

  @override
  State<PlantScheduleBody> createState() => _PlantScheduleBodyState();
}

class _PlantScheduleBodyState extends State<PlantScheduleBody> {
  /// `department|date` — the cell the panel below the grid is showing.
  String? _selected;

  @override
  void initState() {
    super.initState();
    _selected = _defaultSelection();
  }

  @override
  void didUpdateWidget(PlantScheduleBody oldWidget) {
    super.didUpdateWidget(oldWidget);
    // A new week is a new grid; keeping the old coordinate would leave the
    // panel showing a day that is no longer on screen.
    if (oldWidget.screen.week != widget.screen.week) {
      _selected = _defaultSelection();
    }
  }

  /// The first day that was actually written, so the panel opens on something
  /// worth reading rather than on an explanation of absence.
  String? _defaultSelection() {
    for (final cell in widget.screen.cells) {
      if (cell.exists) return '${cell.department}|${cell.date}';
    }
    final first = widget.screen.cells.isEmpty
        ? null
        : widget.screen.cells.first;
    return first == null ? null : '${first.department}|${first.date}';
  }

  ScheduleDayCell? get _selectedCell {
    final key = _selected;
    if (key == null) return null;
    final parts = key.split('|');
    if (parts.length != 2) return null;
    return widget.screen.cellFor(parts[0], parts[1]);
  }

  @override
  Widget build(BuildContext context) {
    final screen = widget.screen;
    final stats = screen.stats;
    final selected = _selectedCell;

    return SingleChildScrollView(
      padding: const EdgeInsets.fromLTRB(28, 20, 28, 40),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          widget.heading,
          const SizedBox(height: 18),
          StatStrip(
            tiles: [
              StatTile(
                label: 'Days written',
                value: '${stats.days}',
                caption:
                    'of ${screen.departments.length * screen.dates.length} '
                    'cells in the week',
              ),
              StatTile(
                label: 'Awaiting a signature',
                value: '${stats.pendingApproval}',
                caption: 'written, and not exportable until signed',
              ),
              StatTile(
                label: 'Approved',
                value: '${stats.approved}',
                caption: 'a supervisor signed it — the export is live',
              ),
              StatTile(
                label: 'Deferred shares',
                value: '${stats.deferred}',
                caption: 'placed nowhere, each with the clause that says why',
              ),
              StatTile(
                label: 'Below capacity',
                value: '${stats.belowCapacity}',
                caption: 'the day holds less than the crew could run',
              ),
            ],
          ),
          if (widget.runs != null) ...[
            const SizedBox(height: 16),
            widget.runs!,
          ],
          const SizedBox(height: 16),
          SectionCard(
            title: 'The week',
            subtitle:
                'departments down, days across · every department the '
                'governed page declares has a row, whether or not a day was '
                'opened for it',
            headerTrailing: widget.weekPicker,
            child: screen.departments.isEmpty || screen.dates.isEmpty
                ? _emptyGrid()
                : ScheduleGrid(
                    screen: screen,
                    selected: _selected,
                    onSelect: (key) => setState(() => _selected = key),
                  ),
          ),
          const SizedBox(height: 16),
          if (selected != null)
            ScheduleDayDetail(cell: selected)
          else
            CumulusEmptyState(
              icon: Icons.calendar_today_outlined,
              title: 'No day selected',
              message:
                  'Pick a cell in the grid above to read the day: its hours '
                  'against the governed bounds, the incentive arithmetic, the '
                  'rows and what was deferred.',
            ),
          const SizedBox(height: 16),
          GovernedPageCard(
            clauses: screen.policyClauses,
            policyRef: screen.policyRef,
            policyFound: screen.policyFound,
            subtitle:
                'the page the capacity rows, the fudge band, the incentive '
                'model and the approval table are read from',
          ),
        ],
      ),
    );
  }

  Widget _emptyGrid() => CumulusEmptyState(
    icon: widget.seesTheQueue ? Icons.grid_off : Icons.lock_outline,
    title: widget.seesTheQueue
        ? 'No departments on the governed page'
        : 'Not scoped to you',
    message: widget.seesTheQueue
        ? 'The scheduling parameters name no department, and no ScheduleDay '
              'exists to infer one from. The grid has nothing to draw rows '
              'for — write the page and this fills in.'
        : 'You are not bound to the plant-supervisor desk, and your access is '
              'to your own work. This is what you can see, not what there is.',
  );
}

// ---------------------------------------------------------------------------
// The grid
// ---------------------------------------------------------------------------

/// Departments down, days across, one week.
class ScheduleGrid extends StatelessWidget {
  final PlantScheduleScreen screen;

  /// `department|date`, or null.
  final String? selected;
  final ValueChanged<String> onSelect;

  const ScheduleGrid({
    super.key,
    required this.screen,
    required this.selected,
    required this.onSelect,
  });

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          children: [
            const SizedBox(width: 160),
            for (final date in screen.dates)
              Expanded(
                child: Padding(
                  padding: const EdgeInsets.only(left: 6, bottom: 8),
                  child: Text(
                    _dayLabel(date),
                    style: CumulusText.statLabel,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
              ),
          ],
        ),
        for (final department in screen.departments)
          Padding(
            padding: const EdgeInsets.only(bottom: 6),
            // Not stretched, and deliberately: the grid sits in a
            // page-level scroll view, where a stretched row would be handed
            // an infinite height to fill, and the obvious fix — measuring the
            // row first — cannot be done through the bar's own LayoutBuilder.
            // Every cell has the same two lines and the same bar, so a floor
            // on the height is enough to keep the week even.
            child: Row(
              children: [
                SizedBox(
                  width: 160,
                  child: Padding(
                    padding: const EdgeInsets.only(right: 10, top: 6),
                    child: Text(
                      department,
                      style: CumulusText.body.copyWith(
                        fontSize: 12.5,
                        fontWeight: FontWeight.w600,
                        color: CumulusColors.ink,
                      ),
                    ),
                  ),
                ),
                for (final date in screen.dates)
                  Expanded(
                    child: Padding(
                      padding: const EdgeInsets.only(left: 6),
                      child: _GridCell(
                        cell: screen.cellFor(department, date),
                        department: department,
                        date: date,
                        selected: selected == '$department|$date',
                        onTap: () => onSelect('$department|$date'),
                      ),
                    ),
                  ),
              ],
            ),
          ),
      ],
    );
  }

  static String _dayLabel(String date) {
    final parsed = DateTime.tryParse(date);
    if (parsed == null) return date;
    const names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
    return '${names[parsed.weekday - 1]} ${date.substring(5)}';
  }
}

/// One intersection. An absent cell says no day was opened; it does not draw a
/// zero, because zero hours is a schedule and this is the absence of one.
class _GridCell extends StatelessWidget {
  final ScheduleDayCell? cell;
  final String department;
  final String date;
  final bool selected;
  final VoidCallback onTap;

  const _GridCell({
    required this.cell,
    required this.department,
    required this.date,
    required this.selected,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final cell = this.cell;
    final exists = cell != null && cell.exists;
    final tone = statusTone(cell?.status ?? '');
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(CumulusRadius.control),
      child: Container(
        constraints: const BoxConstraints(minHeight: 76),
        padding: const EdgeInsets.fromLTRB(10, 8, 10, 9),
        decoration: BoxDecoration(
          color: exists ? tone.background : CumulusColors.surface,
          borderRadius: BorderRadius.circular(CumulusRadius.control),
          border: Border.all(
            color: selected
                ? CumulusColors.accent
                : exists
                ? tone.foreground.withValues(alpha: 0.28)
                : CumulusColors.border,
            width: selected ? 1.6 : 1,
          ),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(
              exists ? statusLabel(cell.status) : 'no day opened',
              style: CumulusText.meta.copyWith(
                fontSize: 10.5,
                color: exists ? tone.foreground : CumulusColors.inkMuted,
                fontWeight: FontWeight.w600,
              ),
              overflow: TextOverflow.ellipsis,
            ),
            const SizedBox(height: 4),
            Text(
              exists ? '${hoursText(cell.totalHours)} h' : '—',
              style: CumulusText.rowTitle.copyWith(
                fontSize: 15,
                fontFeatures: const [FontFeature.tabularFigures()],
                color: exists ? CumulusColors.ink : CumulusColors.inkMuted,
              ),
            ),
            const SizedBox(height: 6),
            if (exists)
              HoursBar(cell: cell, height: 8, showMarkers: false)
            else
              Container(
                height: 8,
                decoration: BoxDecoration(
                  color: CumulusColors.surfaceMuted,
                  borderRadius: BorderRadius.circular(4),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// The bar
// ---------------------------------------------------------------------------

/// The day's hours against both governed bounds, with the fudge band drawn.
///
/// The band is the part worth drawing rather than describing: the page says
/// exceeding the absolute max *within* the fudge is accepted and not an
/// exception, so a bar that showed one line at the goal would be reporting an
/// exception where the plant's own policy says there is none. Goal, fudge and
/// absolute max are three different marks and a supervisor argues with each of
/// them separately.
class HoursBar extends StatelessWidget {
  final ScheduleDayCell cell;
  final double height;

  /// Goal and absolute-max ticks. Off in the grid, where the cell is too
  /// small for a mark to mean anything.
  final bool showMarkers;

  /// The band between the goal and the goal plus the fudge — found by key so
  /// its presence and its width can be asserted about.
  static const fudgeBandKey = Key('schedule-fudge-band');

  /// The filled part: the hours the day actually holds.
  static const fillKey = Key('schedule-hours-fill');

  const HoursBar({
    super.key,
    required this.cell,
    this.height = 26,
    this.showMarkers = true,
  });

  Color get _fillColor {
    if (cell.overCeiling) return CumulusColors.danger;
    if (cell.inFudgeBand) return CumulusColors.warn;
    return CumulusColors.teal;
  }

  @override
  Widget build(BuildContext context) {
    final scale = cell.barScale;
    final goal = cell.goalMaxHours;
    final abs = cell.absMaxHours;
    return LayoutBuilder(
      builder: (context, constraints) {
        final width = constraints.maxWidth;
        double at(double hours) => (hours / scale).clamp(0.0, 1.0) * width;
        final bandStart = goal == null ? 0.0 : at(goal);
        final bandEnd = goal == null ? 0.0 : at(goal + cell.fudgeHours);
        return SizedBox(
          height: height,
          child: Stack(
            children: [
              Positioned.fill(
                child: Container(
                  decoration: BoxDecoration(
                    color: CumulusColors.surfaceMuted,
                    borderRadius: BorderRadius.circular(height / 3),
                    border: Border.all(color: CumulusColors.border),
                  ),
                ),
              ),
              if (goal != null && bandEnd > bandStart)
                Positioned(
                  left: bandStart,
                  width: bandEnd - bandStart,
                  top: 0,
                  bottom: 0,
                  child: Container(
                    key: fudgeBandKey,
                    decoration: BoxDecoration(
                      color: CumulusColors.warnSoft,
                      border: Border(
                        left: BorderSide(
                          color: CumulusColors.warn.withValues(alpha: 0.55),
                        ),
                        right: BorderSide(
                          color: CumulusColors.warn.withValues(alpha: 0.55),
                        ),
                      ),
                    ),
                  ),
                ),
              Positioned(
                left: 0,
                width: at(cell.totalHours),
                top: 0,
                bottom: 0,
                child: Container(
                  key: fillKey,
                  decoration: BoxDecoration(
                    color: _fillColor.withValues(alpha: 0.55),
                    borderRadius: BorderRadius.circular(height / 3),
                  ),
                ),
              ),
              if (showMarkers && abs != null)
                Positioned(
                  left: at(abs) - 1,
                  width: 2,
                  top: 0,
                  bottom: 0,
                  child: const ColoredBox(color: CumulusColors.danger),
                ),
              if (showMarkers && goal != null)
                Positioned(
                  left: at(goal) - 1,
                  width: 2,
                  top: 0,
                  bottom: 0,
                  child: const ColoredBox(color: CumulusColors.inkSecondary),
                ),
            ],
          ),
        );
      },
    );
  }
}

// ---------------------------------------------------------------------------
// One day, opened
// ---------------------------------------------------------------------------

/// Everything a signature needs, for one department on one day.
///
/// Public and free of any client so it can be put on a surface directly: the
/// three claims worth asserting about — that the fudge band is drawn, that the
/// day says whether it is out of the hole, and that the export is live only on
/// an approved day — all live here.
class ScheduleDayDetail extends StatelessWidget {
  final ScheduleDayCell cell;

  const ScheduleDayDetail({super.key, required this.cell});

  @override
  Widget build(BuildContext context) {
    if (!cell.exists) {
      return SectionCard(
        title: '${cell.department} · ${cell.date}',
        subtitle: 'no day was opened',
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const StatusBadge(
              'no day opened',
              tone: CumulusTone.neutral,
              dense: true,
            ),
            const SizedBox(height: 10),
            Text(
              'No ScheduleDay exists for ${cell.department} on ${cell.date}. '
              'That is not a day scheduled for zero hours — it is the absence '
              'of a day: either no window opened one for this department, or '
              'every candidate share was deferred. The run journal above says '
              'which.',
              style: CumulusText.body,
            ),
          ],
        ),
      );
    }

    final tone = statusTone(cell.status);
    return SectionCard(
      title: '${cell.department} · ${cell.date}',
      subtitle:
          'the hours against the governed bounds, the incentive arithmetic, '
          'the rows and what the fill left off',
      headerTrailing: StatusBadge(
        statusLabel(cell.status),
        tone: tone,
        dense: true,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _hours(),
          const SizedBox(height: 16),
          _IncentiveBlock(cell: cell),
          const SizedBox(height: 16),
          _crew(),
          const SizedBox(height: 16),
          _entries(),
          if (cell.deferred.isNotEmpty) ...[
            const SizedBox(height: 16),
            _deferred(),
          ],
          const SizedBox(height: 16),
          ScheduleExportButton(cell: cell),
          const SizedBox(height: 14),
          Text(
            [
              if (cell.approvedBy.isNotEmpty)
                'approved by ${cell.approvedBy}'
                    '${cell.approvedAt.isEmpty ? '' : ' at ${cell.approvedAt}'}',
              if (cell.pendingTaskId.isNotEmpty)
                'open Task ${cell.pendingTaskId}',
              if (cell.runId.isNotEmpty) 'written by ${cell.runId}',
              if (cell.objectRef.isNotEmpty) cell.objectRef,
            ].join(' · '),
            style: CumulusText.meta,
          ),
        ],
      ),
    );
  }

  /// The bar, and the three governed numbers it is drawn against, said in
  /// words underneath so the picture is checkable rather than decorative.
  Widget _hours() {
    final goal = cell.goalMaxHours;
    final abs = cell.absMaxHours;
    final ceiling = cell.ceilingHours;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        FieldLabel(
          'Hours',
          hint: 'against the goal, the fudge band and the absolute max',
        ),
        Row(
          crossAxisAlignment: CrossAxisAlignment.center,
          children: [
            SizedBox(
              width: 96,
              child: Text(
                '${hoursText(cell.totalHours)} h',
                style: CumulusText.statValue.copyWith(fontSize: 22),
              ),
            ),
            Expanded(child: HoursBar(cell: cell)),
          ],
        ),
        const SizedBox(height: 8),
        Wrap(
          spacing: 8,
          runSpacing: 6,
          children: [
            MetaPill(
              goal == null
                  ? 'goal max: not stated'
                  : 'goal max ${hoursText(goal)} h',
            ),
            MetaPill(
              cell.fudgeHours == 0
                  ? 'fudge: none'
                  : 'fudge band +${hoursText(cell.fudgeHours)} h',
              tone: cell.inFudgeBand ? CumulusTone.warn : null,
            ),
            MetaPill(
              abs == null
                  ? 'absolute max: not stated'
                  : 'absolute max ${hoursText(abs)} h',
            ),
            if (ceiling != null)
              MetaPill(
                'ceiling ${hoursText(ceiling)} h',
                tone: cell.overCeiling ? CumulusTone.danger : null,
              ),
          ],
        ),
        const SizedBox(height: 8),
        Text(
          cell.overCeiling
              ? 'Past every bound the page states, fudge included. This is an '
                    'exception the page does not allow for.'
              : cell.inFudgeBand
              ? 'Inside the fudge band — past the goal, and within the '
                    'latitude the governed page allows. The page says this is '
                    'accepted and is not an exception.'
              : goal == null
              ? 'The page states no goal for this department, so there is '
                    'nothing to be over.'
              : 'Under the goal.',
          style: CumulusText.body.copyWith(fontSize: 12.5),
        ),
      ],
    );
  }

  /// The crew the fill chose, and how old the number behind it is.
  Widget _crew() => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      const FieldLabel(
        'Capacity',
        hint: 'the first number a supervisor will dispute',
      ),
      Text(
        cell.capacityRow.isEmpty
            ? 'No capacity row is recorded for this day.'
            : cell.capacityRow,
        style: CumulusText.body.copyWith(fontSize: 12.5),
      ),
      const SizedBox(height: 6),
      Wrap(
        spacing: 8,
        runSpacing: 6,
        children: [
          MetaPill('headcount ${hoursText(cell.headcount)}'),
          MetaPill(
            cell.headcountAsOf.isEmpty
                ? 'as of: unrecorded'
                : 'as of ${cell.headcountAsOf}',
            tone: cell.headcountStale ? CumulusTone.warn : null,
          ),
          if (cell.belowCapacity)
            const MetaPill('below capacity', tone: CumulusTone.warn),
          if (cell.stoppedAt.isNotEmpty)
            MetaPill('stopped at ${cell.stoppedAt}', tone: CumulusTone.accent),
        ],
      ),
      if (cell.headcountStale) ...[
        const SizedBox(height: 6),
        Text(
          'The headcount behind this capacity row is older than the governed '
          'window allows. The fill used it anyway and said so — a stale '
          'number used silently is the failure this warning exists to '
          'prevent.',
          style: CumulusText.body.copyWith(
            fontSize: 12.5,
            color: CumulusColors.warn,
          ),
        ),
      ],
    ],
  );

  /// The rows, with the rule that placed each one. A row a person placed is
  /// drawn differently and labelled, because "the fill decided this" and "a
  /// supervisor decided this" are the two answers to every argument about a
  /// schedule and they must never look the same.
  Widget _entries() => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      FieldLabel(
        'Rows',
        hint: '${cell.entries.length} on this day, in sequence',
      ),
      if (cell.entries.isEmpty)
        Text(
          'The day is written and holds no row. Everything the fill '
          'considered was deferred.',
          style: CumulusText.meta,
        )
      else
        for (final entry in cell.entries) _entryRow(entry),
    ],
  );

  Widget _entryRow(ScheduleEntryRow entry) => Container(
    margin: const EdgeInsets.only(bottom: 5),
    padding: const EdgeInsets.fromLTRB(10, 7, 10, 7),
    decoration: BoxDecoration(
      color: entry.isHuman ? CumulusTone.violet.background : Colors.transparent,
      borderRadius: BorderRadius.circular(CumulusRadius.control),
      border: Border.all(
        color: entry.isHuman
            ? CumulusTone.violet.foreground.withValues(alpha: 0.35)
            : CumulusColors.border,
      ),
    ),
    child: Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SizedBox(
          width: 44,
          child: Text(
            hoursText(entry.sequence),
            style: CumulusText.mono.copyWith(fontSize: 11),
          ),
        ),
        Expanded(
          flex: 3,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                entry.jobLabel.isEmpty
                    ? (entry.jobNumber.isEmpty
                          ? entry.rowKind
                          : entry.jobNumber)
                    : entry.jobLabel,
                style: CumulusText.rowTitle.copyWith(fontSize: 12.5),
              ),
              Text(
                [
                  if (entry.jobNumber.isNotEmpty) entry.jobNumber,
                  if (entry.rowKind.isNotEmpty) entry.rowKind,
                  if (entry.vehicleRef.isNotEmpty) entry.vehicleRef,
                ].join(' · '),
                style: CumulusText.meta,
              ),
            ],
          ),
        ),
        Expanded(
          flex: 4,
          child: Text(
            entry.basis.isEmpty ? 'no basis recorded' : entry.basis,
            style: CumulusText.body.copyWith(fontSize: 12.5),
          ),
        ),
        SizedBox(
          width: 132,
          child: entry.isHuman
              ? const StatusBadge(
                  'placed by a person',
                  tone: CumulusTone.violet,
                  dense: true,
                )
              : const StatusBadge('placed by the fill', dense: true),
        ),
        SizedBox(
          width: 64,
          child: Text(
            '${hoursText(entry.laborHours)} h',
            textAlign: TextAlign.right,
            style: CumulusText.body.copyWith(fontSize: 12.5),
          ),
        ),
      ],
    ),
  );

  /// What the fill did not place, and the clause that says why. Both the
  /// clause and the sentence: "why is 260118 not on Tuesday" has to answer as
  /// a clause and as English.
  Widget _deferred() => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      FieldLabel(
        'Deferred',
        hint: '${cell.deferred.length} share(s) the fill placed nowhere',
      ),
      for (final row in cell.deferred)
        Padding(
          padding: const EdgeInsets.only(bottom: 4),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              SizedBox(
                width: 110,
                child: Text(
                  row.jobNumber,
                  style: CumulusText.mono.copyWith(fontSize: 11),
                ),
              ),
              SizedBox(
                width: 170,
                child: StatusBadge(
                  row.clause.isEmpty ? 'no clause named' : row.clause,
                  tone: CumulusTone.accent,
                  dense: true,
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  '${row.reason}'
                  '${row.laborHours == 0 ? '' : ' (${hoursText(row.laborHours)} h)'}',
                  style: CumulusText.body.copyWith(fontSize: 12.5),
                ),
              ),
            ],
          ),
        ),
    ],
  );
}

// ---------------------------------------------------------------------------
// The incentive
// ---------------------------------------------------------------------------

/// The incentive, as arithmetic a reader can check.
///
/// A single figure with a tooltip was the alternative, and it is the thing
/// this plant has been arguing about for years: the number is per-hour, it
/// starts negative, and where it crosses zero is the whole conversation. So
/// every term is on screen — the start, the rate, the hours, the answer, and
/// the crossing — and the flow's own sentence sits beside them so a reader can
/// see the two agree.
class _IncentiveBlock extends StatelessWidget {
  final ScheduleDayCell cell;

  const _IncentiveBlock({required this.cell});

  @override
  Widget build(BuildContext context) {
    final start = cell.incentiveStartUsd;
    final rate = cell.incentiveRateUsdPerHour;
    final crossing = cell.incentiveCrossingHours;
    final tone = cell.outOfTheHole ? CumulusTone.success : CumulusTone.warn;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const FieldLabel(
          'Incentive',
          hint: 'the governed figures, and where they cross zero',
        ),
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            StatusBadge(
              cell.outOfTheHole ? 'out of the hole' : 'still in the hole',
              tone: tone,
              dense: true,
            ),
            const SizedBox(width: 10),
            Expanded(
              child: Text(
                crossing == null
                    ? 'The page states no figures to cross zero with.'
                    : 'The governed figures cross zero at '
                          '${hoursText(crossing)} h; this day holds '
                          '${hoursText(cell.totalHours)} h.',
                style: CumulusText.body.copyWith(fontSize: 12.5),
              ),
            ),
          ],
        ),
        const SizedBox(height: 8),
        Container(
          padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
          decoration: BoxDecoration(
            color: CumulusColors.surfaceMuted,
            borderRadius: BorderRadius.circular(CumulusRadius.control),
            border: Border.all(color: CumulusColors.border),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                start == null || rate == null
                    ? 'This day carries an incentive of '
                          '${usdText(cell.incentivePerHour)} per hour, and '
                          'the page states neither a start nor a rate to '
                          'derive it from.'
                    : '${usdText(start)} + ${usdRateText(rate)}/h × '
                          '${hoursText(cell.totalHours)} h = '
                          '${usdText(cell.incentivePerHour)} per hour',
                style: CumulusText.mono.copyWith(fontSize: 12.5),
              ),
              if (cell.incentiveBasis.isNotEmpty) ...[
                const SizedBox(height: 6),
                Text(
                  'As the flow wrote it: ${cell.incentiveBasis}',
                  style: CumulusText.meta,
                ),
              ],
            ],
          ),
        ),
      ],
    );
  }
}

// ---------------------------------------------------------------------------
// The export
// ---------------------------------------------------------------------------

/// The Tier-1 export, live only on an approved day.
///
/// There is no `url_launcher` in this package and the control does not need
/// one: the URL goes to the clipboard, which is the shape a customer's own
/// script wants anyway — the export exists so a plant can pull its schedule
/// without this console, and handing over the address is handing over exactly
/// that.
///
/// Disabled is the interesting state. `/api/v1/schedule/export` answers 409
/// while any day in the week is unsigned, and a button that produced that
/// error would be teaching people the system is broken when it is in fact
/// working. So the refusal is said here, before the click, and it names the
/// Task that is holding the day.
class ScheduleExportButton extends StatelessWidget {
  final ScheduleDayCell cell;

  static const buttonKey = Key('schedule-export');

  const ScheduleExportButton({super.key, required this.cell});

  Future<void> _copy(BuildContext context) async {
    final messenger = ScaffoldMessenger.of(context);
    await Clipboard.setData(ClipboardData(text: cell.exportUrl));
    messenger.showSnackBar(
      SnackBar(
        content: Text(
          'The export URL is on your clipboard: ${cell.exportUrl}. It answers '
          'text/csv, regenerated from the graph and stored nowhere.',
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final refusal = cell.exportable
        ? 'GET ${cell.exportUrl} — regenerated on request and stored nowhere.'
        : 'The export refuses an unapproved day. '
              '/api/v1/schedule/export answers 409 while ${cell.date} is '
              '${statusLabel(cell.status)}'
              '${cell.pendingTaskId.isEmpty ? '' : ', naming the open Task ${cell.pendingTaskId}'}'
              '. Approve it and this answers.';
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Tooltip(
          message: refusal,
          child: FilledButton.icon(
            key: buttonKey,
            onPressed: cell.exportable ? () => _copy(context) : null,
            icon: const Icon(Icons.download_outlined, size: 16),
            label: const Text('Download CSV'),
          ),
        ),
        const SizedBox(height: 6),
        Text(
          refusal,
          style: CumulusText.meta.copyWith(
            color: cell.exportable
                ? CumulusColors.inkMuted
                : CumulusColors.warn,
          ),
        ),
      ],
    );
  }
}

// ---------------------------------------------------------------------------
// Small shared pieces
// ---------------------------------------------------------------------------

/// One vocabulary for a day's status across the grid and the panel.
CumulusTone statusTone(String status) => switch (status) {
  'approved' => CumulusTone.success,
  'pending_approval' => CumulusTone.warn,
  'rejected' => CumulusTone.danger,
  'proposed' => CumulusTone.accent,
  _ => CumulusTone.neutral,
};

String statusLabel(String status) =>
    status.isEmpty ? 'no day opened' : status.replaceAll('_', ' ');

/// Hours read as hours: `8` rather than `8.0`, `7.5` kept.
String hoursText(double value) =>
    value == value.roundToDouble() ? '${value.toInt()}' : '$value';

String usdText(double value) =>
    '${value < 0 ? '-' : ''}\$${value.abs().toStringAsFixed(2)}';

/// A per-hour rate at the precision the page states it in.
///
/// Two decimals would round \$0.0724/h to \$0.07/h, and then the equation on
/// screen would not come out at the answer beside it — which is the one thing
/// this block exists to let a reader check. Cents where cents are enough, four
/// places where they are not.
String usdRateText(double value) {
  final cents = double.parse(value.toStringAsFixed(2));
  final text = (cents - value).abs() < 1e-9
      ? value.abs().toStringAsFixed(2)
      : value.abs().toStringAsFixed(4);
  return '${value < 0 ? '-' : ''}\$$text';
}
