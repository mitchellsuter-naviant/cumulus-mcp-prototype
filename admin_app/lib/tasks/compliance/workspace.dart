import 'package:flutter/material.dart';

import 'models.dart';
import '../shared/workspace_chrome.dart';
import '../shared/governed_clause.dart';
import '../../models/session.dart';
import '../../services/api_client.dart';
import '../../theme/cumulus_theme.dart';
import '../../widgets/command_palette.dart' show ownsWorkspace;
import '../../widgets/run_workbench.dart';
import '../../widgets/skeleton.dart';
import '../../widgets/ui_kit.dart';

/// The desk this workspace is — the server's `desks.HR_SAFETY`.
const _hrSafetyQueue = 'hr-safety';

/// The console section slug, which is also the key the session's workspace
/// availability list is written against.
const _complianceSlug = 'compliance';

/// The keys the two lists are found by. The whole point of the untracked
/// section is that it is *not* the expiry table, and a test that could not
/// tell them apart could not hold the distinction.
const complianceTableKey = Key('compliance-expiry-table');
const complianceUntrackedKey = Key('compliance-untracked');

/// The Compliance workspace — credentials and inspections, by days to expiry.
///
/// Two distinctions the whole screen exists to keep, both of them ones a
/// spreadsheet loses on its first sort.
///
/// **An untracked asset is not an overdue one.** A unit the register holds no
/// next-due date for is excluded at the *unit*, and it gets its own list here
/// rather than a row in the table with an empty date — because a sweep that
/// reported it as out of inspection would chase it forever, and a supervisor
/// who saw it in the expiry table would go looking for a lapsed inspection
/// that never existed.
///
/// **A `null` days-to-expiry is not a zero.** A date nobody could read is the
/// first exception class, not a record that expired today. It is rendered in
/// words, never as a number, and it sorts last because that is where the sweep
/// itself puts it.
class ComplianceView extends StatefulWidget {
  final ApiClient api;

  /// The acting session: whether this deployment has the workspace, and what
  /// an empty table means when it does.
  final Session? session;

  const ComplianceView({super.key, required this.api, this.session});

  @override
  State<ComplianceView> createState() => _ComplianceViewState();
}

class _ComplianceViewState extends State<ComplianceView> {
  /// Whether the hr-safety desk is this persona's work. The rows are scoped
  /// server-side either way — this decides only what emptiness *means*.
  bool get _seesTheQueue => ownsWorkspace(widget.session, _hrSafetyQueue);

  ComplianceScreen? _screen;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final screen = await widget.api.complianceScreen();
      if (mounted) {
        setState(() {
          _screen = screen;
          _error = null;
        });
      }
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    final session = widget.session;
    if (session != null && !session.workspaceAvailable(_complianceSlug)) {
      return CumulusEmptyState(
        icon: Icons.link_off,
        title: 'Not in this deployment',
        message: workspaceAbsenceReason(session, _complianceSlug),
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
    return ComplianceBody(
      screen: screen,
      seesTheQueue: _seesTheQueue,
      heading: _heading(),
      runs: FlowRunsCard(
        api: widget.api,
        flowId: 'credential-tracking',
        subtitle:
            'each sweep: what it gathered, which stage it sent, and which '
            'rows fell into an exception class · click one to open its '
            'journal step by step',
      ),
    );
  }

  Widget _heading() => PageHeading(
    eyebrow: 'Workspace',
    title: 'Compliance',
    subtitle:
        'Credentials and inspections, in the order the sweep works them: '
        'days to expiry ascending, and the dates nobody could read last. '
        'The notice stages are read off the governed page, and a unit the '
        'register holds no next-due date for is excluded at the unit rather '
        'than reported overdue.',
    trailing: OutlinedButton.icon(
      onPressed: _screen == null ? null : _load,
      icon: const Icon(Icons.refresh, size: 16),
      label: const Text('Refresh'),
    ),
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
              SkeletonStatStrip(count: 6),
              SizedBox(height: 16),
              SkeletonCard(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    SkeletonBar(width: 150, height: 13),
                    SizedBox(height: 10),
                    SkeletonBar(width: 320, height: 10),
                    SizedBox(height: 16),
                    SkeletonTableRows(rows: 4, columnFlex: [1, 4, 3, 2]),
                  ],
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 16),
        const SkeletonPulse(
          child: SkeletonListCard(rows: 8, leadingCircle: false),
        ),
      ],
    ),
  );
}

// ---------------------------------------------------------------------------
// The page, once the screen has landed
// ---------------------------------------------------------------------------

/// The rendered workspace, given a screen already in hand — split from the
/// loader so the ordering, the stage trail and the untracked list can be put
/// on a surface and asserted about without a client behind them.
class ComplianceBody extends StatefulWidget {
  final ComplianceScreen screen;
  final bool seesTheQueue;
  final Widget heading;

  /// The flow's runs, above the table. Null in a test.
  final Widget? runs;

  const ComplianceBody({
    super.key,
    required this.screen,
    required this.heading,
    this.seesTheQueue = true,
    this.runs,
  });

  @override
  State<ComplianceBody> createState() => _ComplianceBodyState();
}

class _ComplianceBodyState extends State<ComplianceBody> {
  String _lens = 'all';

  static const _lenses = <(String, String, CumulusTone)>[
    ('all', 'ALL', CumulusTone.neutral),
    ('expired', 'EXPIRED', CumulusTone.danger),
    ('soon', 'INSIDE 30 DAYS', CumulusTone.warn),
    ('unreadable', 'DATE UNREADABLE', CumulusTone.violet),
    ('exception', 'EXCEPTIONS', CumulusTone.accent),
  ];

  bool _matches(ComplianceRow row) => switch (_lens) {
    'expired' => row.expired,
    'soon' => row.daysToExpiry != null && row.daysToExpiry! <= 30,
    'unreadable' => !row.dateReadable,
    'exception' => row.exception.isNotEmpty,
    _ => true,
  };

  @override
  Widget build(BuildContext context) {
    final screen = widget.screen;
    final stats = screen.stats;
    // Filtered, never re-sorted. The server hands these back in the order the
    // sweep works them — days to expiry ascending, unreadable dates last — and
    // a default sort of our own here would be a second opinion about urgency
    // that nothing on the governed page authorises.
    final visible = screen.rows.where(_matches).toList();

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
                label: 'Tracked',
                value: '${stats.rows}',
                caption: 'credentials and inspections in scope',
              ),
              StatTile(
                label: 'Inside 60',
                value: '${stats.expiring60}',
                caption: 'days to expiry',
              ),
              StatTile(
                label: 'Inside 30',
                value: '${stats.expiring30}',
                caption: 'days to expiry',
              ),
              StatTile(
                label: 'Inside 15',
                value: '${stats.expiring15}',
                caption: 'days to expiry',
              ),
              StatTile(
                label: 'Expired',
                value: '${stats.expired}',
                caption: 'already past the date on the record',
              ),
              StatTile(
                label: 'Untracked units',
                value: '${stats.untracked}',
                caption: 'no next-due date — not overdue',
              ),
            ],
          ),
          const SizedBox(height: 16),
          _RetentionNotice(
            horizon: screen.retentionHorizon,
            flagged: stats.retentionFlagged,
          ),
          if (stats.exceptionsByClass.isNotEmpty) ...[
            const SizedBox(height: 16),
            _ExceptionClasses(
              counts: stats.exceptionsByClass,
              taskIds: screen.exceptionTaskIds,
            ),
          ],
          if (widget.runs != null) ...[
            const SizedBox(height: 16),
            widget.runs!,
          ],
          const SizedBox(height: 16),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              for (final (id, label, tone) in _lenses)
                LensChip(
                  label: label,
                  tone: tone,
                  selected: _lens == id,
                  onTap: () => setState(() => _lens = id),
                ),
            ],
          ),
          const SizedBox(height: 14),
          if (visible.isEmpty)
            _emptyTable()
          else
            ListCard(
              key: complianceTableKey,
              title: 'Credentials and inspections',
              subtitle:
                  '${visible.length} shown, in the sweep\'s own order — days '
                  'to expiry ascending, dates nobody could read last',
              children: [
                for (final row in visible)
                  ComplianceRowTile(
                    row: row,
                    governedStages: governedStages(screen.policyClauses),
                  ),
              ],
            ),
          const SizedBox(height: 16),
          _untracked(screen),
          const SizedBox(height: 16),
          GovernedPageCard(
            clauses: screen.policyClauses,
            policyRef: screen.policyRef,
            policyFound: screen.policyFound,
            subtitle:
                'the page the stages, the categories, the untracked rule and '
                'the retention horizon are read from',
          ),
        ],
      ),
    );
  }

  Widget _emptyTable() => CumulusEmptyState(
    icon: widget.seesTheQueue ? Icons.event_available : Icons.lock_outline,
    title: !widget.seesTheQueue
        ? 'Not scoped to you'
        : _lens == 'all'
        ? 'Nothing tracked yet'
        : 'Nothing at this lens',
    message: !widget.seesTheQueue
        ? 'You are not bound to the hr-safety desk, and your access is to '
              'your own work. This is what you can see, not what there is.'
        : _lens == 'all'
        ? 'The credential-tracking sweep has gathered no credential and no '
              'inspection in the governed categories. Either the registers '
              'hold none, or no sweep has run — the flow runs above say '
              'which.'
        : 'No row is at this lens. Clear it to see the whole book.',
  );

  /// The untracked units, in their own card, never in the expiry table.
  Widget _untracked(ComplianceScreen screen) {
    if (screen.untrackedAssets.isEmpty) {
      return SectionCard(
        key: complianceUntrackedKey,
        title: 'Untracked units',
        subtitle: 'excluded at the unit — not overdue, not late, not tracked',
        child: Text(
          'Every unit in the governed categories carries a next-due date, so '
          'nothing is excluded this sweep.',
          style: CumulusText.meta,
        ),
      );
    }
    return ListCard(
      key: complianceUntrackedKey,
      title: 'Untracked units',
      subtitle:
          '${screen.untrackedAssets.length} unit(s) the register holds no '
          'next-due date for. These are excluded at the unit and are NOT '
          'overdue — a sweep that reported them as out of inspection would '
          'chase them forever',
      children: [
        for (final asset in screen.untrackedAssets)
          CumulusListRow(
            leading: const Icon(
              Icons.help_outline,
              size: 20,
              color: CumulusColors.inkMuted,
            ),
            title: Text(
              '${asset.name.isEmpty ? asset.equipmentId : asset.name} · '
              'untracked',
              style: CumulusText.rowTitle,
            ),
            subtitle: Text(
              [
                if (asset.equipmentId.isNotEmpty) asset.equipmentId,
                if (asset.category.isNotEmpty) asset.category,
                if (asset.homeSite.isNotEmpty) asset.homeSite,
                if (asset.why.isNotEmpty) asset.why,
              ].join(' · '),
              style: CumulusText.meta,
            ),
            trailing: const [
              StatusBadge('untracked', tone: CumulusTone.violet, dense: true),
            ],
          ),
      ],
    );
  }
}

// ---------------------------------------------------------------------------
// One row of the book
// ---------------------------------------------------------------------------

/// One credential or inspection, with its notice trail.
///
/// Public so the two things worth asserting about — that an unreadable date is
/// rendered in words rather than as a zero, and that the stages actually sent
/// are distinguishable from the governed stages that were not — can be pumped
/// without a screen around them.
class ComplianceRowTile extends StatelessWidget {
  final ComplianceRow row;

  /// Every stage the page declares, so an unsent one can be drawn hollow
  /// beside the sent ones. A trail that showed only what was sent cannot say
  /// whether anything is still owed.
  final List<int> governedStages;

  const ComplianceRowTile({
    super.key,
    required this.row,
    this.governedStages = const [],
  });

  /// `null` is a fact about the record, not a number. It is the first
  /// exception class, and rendering it as `0` would file "nobody could read
  /// this date" under "expires today".
  String get _daysText {
    final days = row.daysToExpiry;
    if (days == null) return 'date could not be read';
    if (days < 0) return '${-days} day(s) past the date';
    if (days == 0) return 'expires today';
    return '$days day(s) to expiry';
  }

  CumulusTone get _daysTone {
    final days = row.daysToExpiry;
    if (days == null) return CumulusTone.violet;
    if (days < 0) return CumulusTone.danger;
    if (days <= 30) return CumulusTone.warn;
    return CumulusTone.neutral;
  }

  @override
  Widget build(BuildContext context) {
    final stages = <int>{...governedStages, ...row.stagesSent}.toList()
      ..sort((a, b) => b.compareTo(a));
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 11),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 96,
            child: StatusBadge(
              row.kind.isEmpty ? 'record' : row.kind,
              tone: row.kind == 'inspection'
                  ? CumulusTone.teal
                  : CumulusTone.accent,
              dense: true,
            ),
          ),
          Expanded(
            flex: 4,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(row.subject, style: CumulusText.rowTitle),
                const SizedBox(height: 2),
                Text(
                  [
                    if (row.credentialType.isNotEmpty) row.credentialType,
                    if (row.category.isNotEmpty) row.category,
                    if (row.supervisor.isNotEmpty)
                      'supervisor ${row.supervisor}',
                  ].join(' · '),
                  style: CumulusText.meta,
                ),
              ],
            ),
          ),
          const SizedBox(width: 10),
          Expanded(
            flex: 3,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                // Verbatim, including the string nothing could parse — which
                // is the only evidence a person has for why the sweep could
                // not read it either.
                Text(
                  row.expiresOn.isEmpty
                      ? 'no date on the record'
                      : row.expiresOn,
                  style: CumulusText.body.copyWith(fontSize: 12.5),
                ),
                const SizedBox(height: 2),
                Text(
                  row.lastNotifiedOn.isEmpty
                      ? 'no notice sent'
                      : 'last notified ${row.lastNotifiedOn}',
                  style: CumulusText.meta,
                ),
              ],
            ),
          ),
          const SizedBox(width: 10),
          SizedBox(
            width: 170,
            child: Wrap(
              spacing: 4,
              runSpacing: 4,
              children: [
                if (stages.isEmpty)
                  Text('no stages declared', style: CumulusText.meta)
                else
                  for (final stage in stages)
                    _StageChip(
                      stage: stage,
                      sent: row.stagesSent.contains(stage),
                    ),
              ],
            ),
          ),
          const SizedBox(width: 10),
          SizedBox(
            width: 170,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                StatusBadge(_daysText, tone: _daysTone, dense: true),
                const SizedBox(height: 4),
                if (row.status.isNotEmpty)
                  StatusBadge(row.status, dense: true),
                if (row.exception.isNotEmpty) ...[
                  const SizedBox(height: 4),
                  StatusBadge(
                    row.exception,
                    tone: CumulusTone.danger,
                    dense: true,
                  ),
                ],
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// One governed notice stage: filled when the record carries it, hollow when
/// the page declares it and nothing was sent.
class _StageChip extends StatelessWidget {
  final int stage;
  final bool sent;

  const _StageChip({required this.stage, required this.sent});

  @override
  Widget build(BuildContext context) {
    return Tooltip(
      message: sent
          ? 'The $stage-day notice is on the record.'
          : 'The $stage-day notice is governed and has not been sent.',
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
        decoration: BoxDecoration(
          color: sent ? CumulusTone.accent.background : Colors.transparent,
          borderRadius: BorderRadius.circular(CumulusRadius.pill),
          border: Border.all(
            color: sent
                ? CumulusTone.accent.foreground.withValues(alpha: 0.4)
                : CumulusColors.borderStrong,
          ),
        ),
        child: Text(
          sent ? '${stage}d sent' : '${stage}d',
          style: CumulusText.mono.copyWith(
            fontSize: 10.5,
            color: sent ? CumulusColors.accentInk : CumulusColors.inkMuted,
          ),
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// The two things the table is not
// ---------------------------------------------------------------------------

/// Retention, said where a person will read it before they go looking for a
/// delete button there is no route for.
class _RetentionNotice extends StatelessWidget {
  final String horizon;
  final int flagged;

  const _RetentionNotice({required this.horizon, required this.flagged});

  @override
  Widget build(BuildContext context) {
    const tone = CumulusTone.teal;
    return Container(
      padding: const EdgeInsets.fromLTRB(16, 13, 16, 13),
      decoration: BoxDecoration(
        color: tone.background,
        borderRadius: BorderRadius.circular(CumulusRadius.control),
        border: Border.all(color: tone.foreground.withValues(alpha: 0.3)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Icon(Icons.inventory_2_outlined, size: 18, color: CumulusColors.teal),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  'Retention horizon: '
                  '${horizon.isEmpty ? 'not stated on the governed page' : horizon}',
                  style: CumulusText.body.copyWith(
                    fontWeight: FontWeight.w600,
                    color: CumulusColors.ink,
                  ),
                ),
                const SizedBox(height: 3),
                Text(
                  'Nothing is deleted. A record older than the horizon is '
                  'flagged for review and kept — an expired credential is the '
                  'evidence that somebody held it, and destroying it is how a '
                  'system loses the only proof it had.'
                  '${flagged > 0 ? ' $flagged record(s) are past the horizon and still on the book.' : ''}',
                  style: CumulusText.meta,
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// The four exception classes, each with its own count.
///
/// They are classes rather than severities: an unreadable date, a row whose
/// stages never reached the SLA floor, a subject with no supervisor to tell,
/// and a record past retention are four different questions, and adding them
/// into one "problems" number is how each of them stops being answerable.
class _ExceptionClasses extends StatelessWidget {
  final Map<String, int> counts;
  final List<String> taskIds;

  const _ExceptionClasses({required this.counts, required this.taskIds});

  @override
  Widget build(BuildContext context) {
    final entries = counts.entries.toList()
      ..sort((a, b) => a.key.compareTo(b.key));
    return SectionCard(
      title: 'Exception classes',
      subtitle:
          'one class per row, counted separately — four different questions, '
          'not one severity scale',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              for (final entry in entries)
                MetaPill(
                  '${entry.key}: ${entry.value}',
                  tone: entry.value == 0
                      ? CumulusTone.neutral
                      : CumulusTone.danger,
                ),
            ],
          ),
          if (taskIds.isNotEmpty) ...[
            const SizedBox(height: 10),
            Text(
              '${taskIds.length} open exception Task(s) — '
              '${taskIds.join(', ')} — waiting on a desk in My Tasks.',
              style: CumulusText.meta,
            ),
          ],
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// The governed stages
// ---------------------------------------------------------------------------

/// The notice stages the page declares, read out of the `stages` clause.
///
/// The clause is written for a person — `60 day, 30 day, 15 day` — so the
/// numbers are taken out of it rather than a second stage list being kept in
/// Dart, which would go on rendering three hollow chips the day somebody
/// edited the page down to two.
List<int> governedStages(List<GovernedClause> clauses) {
  for (final clause in clauses) {
    if (clause.id != 'stages') continue;
    final stages = <int>[
      for (final match in RegExp(r'\d+').allMatches(clause.value))
        int.parse(match.group(0)!),
    ];
    stages.sort((a, b) => b.compareTo(a));
    return stages;
  }
  return const [];
}
