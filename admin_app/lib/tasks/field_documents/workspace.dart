import 'package:flutter/material.dart';

import 'models.dart';
import '../shared/governed_clause.dart';
import '../shared/workspace_chrome.dart';
import '../../models/session.dart';
import '../../services/api_client.dart';
import '../../theme/cumulus_theme.dart';
import '../../widgets/command_palette.dart' show ownsWorkspace;
import '../../widgets/run_workbench.dart';
import '../../widgets/skeleton.dart';
import '../../widgets/ui_kit.dart';

/// The desk this workspace is. Held here rather than imported so the three
/// plant workspaces do not have to wait on one another's edits to a shared
/// constants file; the string is the server's `desks.FIELD_OPERATIONS`, and
/// the route behind the screen refuses in a sentence naming exactly it.
const _fieldOperationsQueue = 'field-operations';

/// The console section slug, which is also the key the session's workspace
/// availability list is written against.
const _fieldDocumentsSlug = 'field-documents';

/// The Field Documents workspace — **packets, not folders**.
///
/// Every other document surface a plant has ever been shown is a tree, and a
/// tree makes a promise this system does not keep: that somewhere a directory
/// was created, that a crew reassignment moved files into a different one, and
/// that a revision replaced what came before it. None of those acts exist
/// here. A packet is `Job —documented_by→ Document` at the latest revision of
/// each governed type; a crew move is an edge whose validity ended and which
/// is *still on the record*; a superseded revision is a row that kept its
/// filename and its date, because a crew that already printed it has to be
/// able to find out what they are holding.
///
/// So the screen draws none of it. It says the sentence out loud instead, at
/// the top, and every part below it is written so that a reader who takes the
/// page literally is not misled: former crews are drawn as history rather than
/// erased, superseded revisions are labelled rather than hidden, and a job
/// whose crew id resolves to no team is an *identity* question sitting on a
/// document screen — never a missing-document one, because no document
/// question was asked about that job at all.
class FieldDocumentsView extends StatefulWidget {
  final ApiClient api;

  /// The acting session: it decides whether this deployment has the workspace
  /// at all, and what an empty table means when it does.
  final Session? session;

  const FieldDocumentsView({super.key, required this.api, this.session});

  @override
  State<FieldDocumentsView> createState() => _FieldDocumentsViewState();
}

class _FieldDocumentsViewState extends State<FieldDocumentsView> {
  /// Whether the field-operations desk is this persona's work. The rows are
  /// scoped server-side either way — this only decides what an empty table
  /// *means*, and "no jobs" told to somebody who cannot see any is a claim
  /// about the business made out of a claim about permissions.
  bool get _seesTheQueue => ownsWorkspace(widget.session, _fieldOperationsQueue);

  FieldDocumentsScreen? _screen;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final screen = await widget.api.fieldDocumentsScreen();
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
    if (session != null && !session.workspaceAvailable(_fieldDocumentsSlug)) {
      return CumulusEmptyState(
        icon: Icons.link_off,
        title: 'Not in this deployment',
        message: workspaceAbsenceReason(session, _fieldDocumentsSlug),
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
    return FieldDocumentsBody(
      screen: screen,
      seesTheQueue: _seesTheQueue,
      heading: _heading(),
      runs: FlowRunsCard(
        api: widget.api,
        flowId: 'field-documents',
        subtitle:
            'each pass over the scheduled window: the jobs it read, the '
            'packets it labelled, and the gaps it counted · click one to '
            'open its journal step by step',
      ),
    );
  }

  /// The chrome that owes the table nothing, so the page can say what it is
  /// before it knows what is in it — and so nothing moves when the rows land.
  Widget _heading() => PageHeading(
    eyebrow: 'Workspace',
    title: 'Field Documents',
    subtitle:
        'A packet is the governed set of documents filed against one '
        'scheduled job, assembled from the edges the flow wrote. Nothing on '
        'this screen creates, moves, renames or deletes a folder — there are '
        'no folders: a crew change ends an assignment and keeps it, and a new '
        'revision supersedes an old one without removing it.',
    pills: const [MetaPill('packets, not folders', tone: CumulusTone.teal)],
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
              SkeletonStatStrip(count: 5),
              SizedBox(height: 16),
              SkeletonCard(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    SkeletonBar(width: 130, height: 13),
                    SizedBox(height: 10),
                    SkeletonBar(width: 340, height: 10),
                    SizedBox(height: 16),
                    SkeletonTableRows(rows: 4, columnFlex: [3, 2, 2, 1]),
                  ],
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 16),
        lensRail(selected: 'all'),
        const SizedBox(height: 14),
        const SkeletonPulse(
          child: SkeletonListCard(rows: 6, leadingCircle: false),
        ),
      ],
    ),
  );
}

// ---------------------------------------------------------------------------
// The page, once the screen has landed
// ---------------------------------------------------------------------------

/// The rendered workspace, given a screen that is already in hand.
///
/// Split from the loader so the page can be put on a surface without a client
/// behind it: everything below is a pure function of [screen] plus the lens
/// and expansion a reader is holding, and both of those are view state that
/// nothing outside this widget needs.
class FieldDocumentsBody extends StatefulWidget {
  final FieldDocumentsScreen screen;

  /// Whether the reader holds the field-operations desk — what an empty table
  /// means, not what it contains.
  final bool seesTheQueue;

  /// The real [PageHeading], passed in so the skeleton and the loaded page
  /// render the identical widget and the title does not move.
  final Widget heading;

  /// The flow's runs, above the table. Null in a test, where the runs card
  /// would be a second network read of something this page is not about.
  final Widget? runs;

  const FieldDocumentsBody({
    super.key,
    required this.screen,
    required this.heading,
    this.seesTheQueue = true,
    this.runs,
  });

  @override
  State<FieldDocumentsBody> createState() => _FieldDocumentsBodyState();
}

/// The lenses, at file scope because the skeleton draws the same rail as the
/// loaded page: the chrome is not waiting on anything, and a rail that
/// appeared when the rows did would shift the table under a reader's cursor.
const _lenses = <(String, String, CumulusTone)>[
  ('all', 'ALL', CumulusTone.neutral),
  ('complete', 'COMPLETE', CumulusTone.success),
  ('missing', 'MISSING', CumulusTone.warn),
  ('unresolved', 'UNRESOLVED CREW', CumulusTone.danger),
];

/// The rail. Inert while the screen is in flight — there is nothing to filter
/// yet, and a chip that answered a click by doing nothing would be worse than
/// one that is plainly not ready.
Widget lensRail({
  required String selected,
  ValueChanged<String>? onSelect,
}) => Wrap(
  spacing: 8,
  runSpacing: 8,
  children: [
    for (final (id, label, tone) in _lenses)
      LensChip(
        label: label,
        tone: tone,
        selected: selected == id,
        onTap: onSelect == null ? null : () => onSelect(id),
      ),
  ],
);

class _FieldDocumentsBodyState extends State<FieldDocumentsBody> {
  String _lens = 'all';
  String? _expanded;

  bool _matches(FieldJobRow row) => switch (_lens) {
    'complete' => row.complete,
    'missing' => row.missing.isNotEmpty,
    'unresolved' => row.unresolvedCrewId.isNotEmpty,
    _ => true,
  };

  @override
  Widget build(BuildContext context) {
    final screen = widget.screen;
    final stats = screen.stats;
    final visible = screen.jobs.where(_matches).toList();
    final types = governedTypes(screen.policyClauses);

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
                label: 'Jobs',
                value: '${stats.jobs}',
                caption: 'scheduled, on the board',
              ),
              StatTile(
                label: 'In horizon',
                value: '${stats.inHorizon}',
                caption: 'loading inside the governed window',
              ),
              StatTile(
                label: 'Packets complete',
                value: '${stats.packetsComplete}',
                caption: 'every governed type filed',
              ),
              StatTile(
                label: 'Missing a type',
                value: '${stats.missing}',
                caption: 'informational — not a Task on its own',
              ),
              StatTile(
                label: 'Unresolved crews',
                value: '${stats.unresolvedCrews}',
                caption: 'an identity question, not a document one',
              ),
            ],
          ),
          if (screen.gapTaskIds.isNotEmpty) ...[
            const SizedBox(height: 14),
            _GapTasksNotice(taskIds: screen.gapTaskIds),
          ],
          if (widget.runs != null) ...[
            const SizedBox(height: 16),
            widget.runs!,
          ],
          const SizedBox(height: 16),
          lensRail(
            selected: _lens,
            onSelect: (id) => setState(() => _lens = id),
          ),
          const SizedBox(height: 14),
          if (visible.isEmpty)
            _emptyTable()
          else
            ListCard(
              title: 'Packets',
              subtitle:
                  '${visible.length} shown · click a row for the packet, the '
                  'crew history and what a revision replaced',
              children: [
                for (final row in visible) _row(row, types),
              ],
            ),
          if (screen.crews.isNotEmpty) ...[
            const SizedBox(height: 16),
            ListCard(
              title: 'Crews',
              subtitle:
                  'who is carrying how much of the board · a crew here is a '
                  'team the job resolved to, not a folder somebody owns',
              children: [
                for (final crew in screen.crews) _crewRow(crew),
              ],
            ),
          ],
          const SizedBox(height: 16),
          GovernedPageCard(
            clauses: screen.policyClauses,
            policyRef: screen.policyRef,
            policyFound: screen.policyFound,
            subtitle:
                'the page the naming convention, the governed types and the '
                'window are read from — argue with this, not with the screen',
          ),
        ],
      ),
    );
  }

  /// Three causes, and only one of them is "nothing happened".
  Widget _emptyTable() => CumulusEmptyState(
    icon: widget.seesTheQueue ? Icons.folder_off_outlined : Icons.lock_outline,
    title: !widget.seesTheQueue
        ? 'Not scoped to you'
        : _lens == 'all'
        ? 'No scheduled jobs yet'
        : 'Nothing at this lens',
    message: !widget.seesTheQueue
        ? 'You are not bound to the field-operations desk, and your access is '
              'to your own work. This is what you can see, not what there is.'
        : _lens == 'all'
        ? 'The field-documents flow has written no packets. Either the '
              'scheduling feed has delivered no job inside the governed '
              'window, or no pass has run yet — the flow runs above say '
              'which.'
        : 'No job is at this lens. Clear it to see the whole board.',
  );

  Widget _crewRow(FieldCrewSummary crew) => CumulusListRow(
    leading: Monogram(crew.name.isEmpty ? crew.teamSlug : crew.name),
    title: Text(
      crew.name.isEmpty ? crew.teamSlug : crew.name,
      style: CumulusText.rowTitle,
    ),
    subtitle: Text(
      '${crew.teamSlug}'
      '${crew.foreman.isEmpty ? '' : ' · foreman ${crew.foreman}'}',
      style: CumulusText.meta,
    ),
    trailing: [
      StatusBadge('${crew.jobs} job(s)', dense: true),
      const SizedBox(width: 6),
      StatusBadge(
        '${crew.complete} complete',
        tone: crew.complete == crew.jobs && crew.jobs > 0
            ? CumulusTone.success
            : CumulusTone.neutral,
        dense: true,
      ),
    ],
  );

  Widget _row(FieldJobRow row, List<String> types) {
    final expanded = _expanded == row.jobNumber;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        InkWell(
          onTap: () => setState(
            () => _expanded = expanded ? null : row.jobNumber,
          ),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 12),
            child: Row(
              children: [
                Expanded(
                  flex: 4,
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      // The rule rides with the label everywhere the label
                      // appears. A convention nobody can read is indis-
                      // tinguishable from a name somebody typed.
                      Tooltip(
                        message: row.labelRule.isEmpty
                            ? 'No naming rule was recorded on this claim.'
                            : row.labelRule,
                        child: Text(
                          row.packetLabel.isEmpty
                              ? row.jobNumber
                              : row.packetLabel,
                          style: CumulusText.rowTitle,
                        ),
                      ),
                      const SizedBox(height: 2),
                      Text(
                        [
                          row.jobNumber,
                          if (row.customer.isNotEmpty) row.customer,
                          if (row.builder.isNotEmpty) row.builder,
                        ].join(' · '),
                        style: CumulusText.meta,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ],
                  ),
                ),
                Expanded(
                  flex: 3,
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        row.currentCrew == null
                            ? 'no crew on the record'
                            : row.currentCrew!.name,
                        style: CumulusText.body.copyWith(fontSize: 12.5),
                        overflow: TextOverflow.ellipsis,
                      ),
                      Text(
                        [
                          if (row.plantSite.isNotEmpty) row.plantSite,
                          if (row.status.isNotEmpty) row.status,
                          if (row.loadDate.isNotEmpty) 'loads ${row.loadDate}',
                        ].join(' · '),
                        style: CumulusText.meta,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ],
                  ),
                ),
                if (row.unresolvedCrewId.isNotEmpty) ...[
                  Tooltip(
                    message:
                        'The job names crew "${row.unresolvedCrewId}", which '
                        'resolves to no team. Nothing is wrong with this '
                        'packet — the question is who the crew is.',
                    child: const StatusBadge(
                      'unresolved crew',
                      tone: CumulusTone.danger,
                      dense: true,
                    ),
                  ),
                  const SizedBox(width: 6),
                ],
                if (row.emptyWindows > 0) ...[
                  StatusBadge(
                    '${row.emptyWindows} empty window(s)',
                    tone: CumulusTone.warn,
                    dense: true,
                  ),
                  const SizedBox(width: 6),
                ],
                SizedBox(
                  width: 108,
                  child: StatusBadge(
                    row.complete
                        ? 'complete'
                        : row.empty
                        ? 'nothing filed'
                        : '${row.missing.length} missing',
                    tone: row.complete
                        ? CumulusTone.success
                        : row.empty
                        ? CumulusTone.danger
                        : CumulusTone.warn,
                    dense: true,
                  ),
                ),
                Icon(
                  expanded ? Icons.expand_less : Icons.expand_more,
                  size: 18,
                  color: CumulusColors.inkMuted,
                ),
              ],
            ),
          ),
        ),
        if (expanded) PacketDetail(row: row, governedTypes: types),
      ],
    );
  }
}

/// The open aggregate Tasks the flow raised, named rather than described.
class _GapTasksNotice extends StatelessWidget {
  final List<String> taskIds;

  const _GapTasksNotice({required this.taskIds});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.fromLTRB(14, 11, 14, 11),
      decoration: BoxDecoration(
        color: CumulusTone.warn.background,
        borderRadius: BorderRadius.circular(CumulusRadius.control),
        border: Border.all(
          color: CumulusTone.warn.foreground.withValues(alpha: 0.32),
        ),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(
            Icons.inbox_outlined,
            size: 16,
            color: CumulusTone.warn.foreground,
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              '${taskIds.length} open gap Task(s) — ${taskIds.join(', ')}. '
              'The recurrence threshold was crossed on a job that has filed '
              'nothing for several windows running; the question is on a desk '
              'in My Tasks.',
              style: CumulusText.meta,
            ),
          ),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// One packet, opened
// ---------------------------------------------------------------------------

/// The expansion of a job row: the packet, its crew history, and what a
/// revision replaced.
///
/// Public because it is the whole claim of the screen and worth asserting
/// about directly — that four governed types each show a version and a filing
/// date, that the crew holding the job now and the crews that held it before
/// are both present and cannot be confused for one another, and that a
/// superseded revision says the word.
class PacketDetail extends StatelessWidget {
  final FieldJobRow row;

  /// The governed types, in the order the page states them, so a type nobody
  /// filed still has a line rather than vanishing into a count.
  final List<String> governedTypes;

  const PacketDetail({
    super.key,
    required this.row,
    this.governedTypes = const [],
  });

  List<String> get _types {
    if (governedTypes.isNotEmpty) return governedTypes;
    final seen = <String>[];
    for (final doc in row.documents) {
      if (!seen.contains(doc.kind)) seen.add(doc.kind);
    }
    for (final kind in row.missing) {
      if (!seen.contains(kind)) seen.add(kind);
    }
    return seen;
  }

  @override
  Widget build(BuildContext context) {
    final current = row.current;
    final replaced = row.replaced;
    return Container(
      color: CumulusColors.surfaceMuted,
      padding: const EdgeInsets.fromLTRB(18, 14, 18, 16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          FieldLabel(
            'Packet label',
            hint: row.labelRule.isEmpty
                ? 'no naming rule was recorded on this claim'
                : row.labelRule,
          ),
          Text(
            row.packetLabel.isEmpty
                ? 'This job has no packet label yet.'
                : row.packetLabel,
            style: CumulusText.body,
          ),
          const SizedBox(height: 14),
          const FieldLabel(
            'Crew',
            hint: 'who holds it now, and who held it before',
          ),
          Row(
            children: [
              const StatusBadge(
                'assigned now',
                tone: CumulusTone.accent,
                dense: true,
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  row.currentCrew == null
                      ? 'No crew resolves for this job.'
                      : '${row.currentCrew!.name}'
                            '${row.currentCrew!.foreman.isEmpty ? '' : ' · foreman ${row.currentCrew!.foreman}'}'
                            ' · ${row.currentCrew!.teamSlug}',
                  style: CumulusText.body.copyWith(fontSize: 12.5),
                ),
              ),
            ],
          ),
          if (row.formerCrews.isNotEmpty) ...[
            const SizedBox(height: 6),
            for (final former in row.formerCrews)
              Padding(
                padding: const EdgeInsets.only(bottom: 4),
                child: Row(
                  children: [
                    const StatusBadge('held until', dense: true),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        '${former.name} held this job until '
                        '${former.untilIso.isEmpty ? 'an unrecorded date' : former.untilIso}'
                        ' · ${former.teamSlug}',
                        style: CumulusText.body.copyWith(fontSize: 12.5),
                      ),
                    ),
                  ],
                ),
              ),
            Text(
              'The assignment ended; the edge is still on the record. Nothing '
              'was deleted and no documents moved — there is nowhere for them '
              'to move to.',
              style: CumulusText.meta,
            ),
          ],
          if (row.unresolvedCrewId.isNotEmpty) ...[
            const SizedBox(height: 12),
            _IdentityQuestion(crewId: row.unresolvedCrewId),
          ],
          const SizedBox(height: 14),
          FieldLabel(
            'Packet',
            hint: row.complete
                ? 'every governed type is filed'
                : '${row.missing.length} of ${_types.length} type(s) not filed',
          ),
          for (final kind in _types)
            _typeLine(
              kind,
              current.where((doc) => doc.kind == kind).toList(),
            ),
          if (replaced.isNotEmpty) ...[
            const SizedBox(height: 12),
            const FieldLabel(
              'Superseded',
              hint: 'kept, not removed — a crew may be holding one of these',
            ),
            for (final doc in replaced)
              Padding(
                padding: const EdgeInsets.only(bottom: 4),
                child: Row(
                  children: [
                    const SizedBox(
                      width: 150,
                      child: StatusBadge(
                        'superseded',
                        tone: CumulusTone.violet,
                        dense: true,
                      ),
                    ),
                    Expanded(
                      child: Text(
                        '${doc.kind} · ${doc.filename} · version '
                        '${doc.version.isEmpty ? 'unstated' : doc.version}'
                        '${doc.filedAt.isEmpty ? '' : ' · filed ${doc.filedAt}'}',
                        style: CumulusText.body.copyWith(fontSize: 12.5),
                      ),
                    ),
                  ],
                ),
              ),
          ],
          if (row.emptyWindows > 0) ...[
            const SizedBox(height: 12),
            Text(
              '${row.emptyWindows} consecutive window(s) with nothing filed '
              'against this job.',
              style: CumulusText.body.copyWith(
                color: CumulusColors.warn,
                fontWeight: FontWeight.w600,
              ),
            ),
          ],
          const SizedBox(height: 12),
          Text(
            [
              if (row.lastRunId.isNotEmpty) 'last pass ${row.lastRunId}',
              if (row.lastRunAtIso.isNotEmpty) row.lastRunAtIso,
              if (row.objectRef.isNotEmpty) row.objectRef,
            ].join(' · '),
            style: CumulusText.meta,
          ),
        ],
      ),
    );
  }

  /// One governed type: the revision standing today, or the fact that none is.
  Widget _typeLine(String kind, List<PacketDocument> filed) {
    final doc = filed.isEmpty ? null : filed.first;
    return Padding(
      padding: const EdgeInsets.only(bottom: 4),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 150,
            child: Text(
              kind,
              style: CumulusText.body.copyWith(
                fontSize: 12.5,
                fontWeight: FontWeight.w600,
              ),
            ),
          ),
          if (doc == null)
            const Expanded(
              child: Text(
                'not filed',
                style: TextStyle(
                  fontSize: 12.5,
                  color: CumulusColors.warn,
                  height: 1.45,
                ),
              ),
            )
          else
            Expanded(
              child: Text(
                '${doc.filename} · version '
                '${doc.version.isEmpty ? 'unstated' : doc.version}'
                ' · filed ${doc.filedAt.isEmpty ? 'at an unrecorded time' : doc.filedAt}',
                style: CumulusText.body.copyWith(fontSize: 12.5),
              ),
            ),
        ],
      ),
    );
  }
}

/// A job whose crew id resolves to no team.
///
/// Drawn apart from the packet on purpose. Every instinct on a document screen
/// is to read a red badge as "a document is missing", and this one means the
/// opposite: the packet may be complete, and what nobody can answer is who the
/// crew is.
class _IdentityQuestion extends StatelessWidget {
  final String crewId;

  const _IdentityQuestion({required this.crewId});

  @override
  Widget build(BuildContext context) {
    const tone = CumulusTone.danger;
    return Container(
      padding: const EdgeInsets.fromLTRB(14, 11, 14, 11),
      decoration: BoxDecoration(
        color: tone.background,
        borderRadius: BorderRadius.circular(CumulusRadius.control),
        border: Border.all(color: tone.foreground.withValues(alpha: 0.32)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Icon(Icons.person_search_outlined, size: 16, color: CumulusColors.danger),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              'Unresolved crew — an identity question, not a document one. '
              'The job names crew "$crewId" and no team resolves from it, so '
              'nobody can say who is carrying this job. No document is '
              'missing on account of this.',
              style: CumulusText.meta,
            ),
          ),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Shared with the other two plant workspaces
// ---------------------------------------------------------------------------

/// The governed types the page declares, read out of the `document-types`
/// clause rather than restated in Dart.
///
/// A second list here would be a second answer to "what belongs in a packet",
/// free to disagree with the one the flow judged the job against.
List<String> governedTypes(List<GovernedClause> clauses) {
  for (final clause in clauses) {
    if (clause.id != 'document-types') continue;
    return [
      for (final part in clause.value.split(','))
        if (part.trim().isNotEmpty) part.trim(),
    ];
  }
  return const [];
}
