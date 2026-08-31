import 'package:flutter/material.dart';

import '../models/dashboard.dart';
import '../models/session.dart';
import '../services/api_client.dart';
import '../theme/cumulus_theme.dart';
import 'cumulus_shell.dart';
import 'sources_plane.dart' show EstateHeaderCard;
// One vocabulary for a Task kind across the console. A desk here counting
// `merge_review` while My Tasks calls it a Proposed merge would be two names
// for one thing, and the reader has to hold both.
import 'tasks_view.dart' show kindLabel;
import 'ui_kit.dart';

/// System state — the current state of the whole system, on one screen.
///
/// Four questions in the order an operator asks them. **What does the map
/// hold**: objects by Collection, the disagreements standing in them, and
/// whether the graph conforms to its own ontology. **Is the outside world
/// arriving**: per-feed health, cursors and what landed. **Is judgment
/// flowing**: queue depths, who holds each desk, what the Gateway committed.
/// **Is governance holding**: where declared and observed disagree, which feeds
/// arrive under no contract, and what the ModelCalls cost.
///
/// The rule this screen is built to keep: **every number is answerable by a
/// list somewhere else**. Nothing here is a figure this screen owns — each
/// panel is composed from the surface that already holds its rows, and each
/// panel hands off to that surface. So a count that looks wrong is a count you
/// can go and read, and there is no second bookkeeping path for the two to
/// drift apart along.
///
/// What that rules out is what a dashboard is normally for. No history of its
/// own, no trend it invented, no figure derived from an assumption. Where a
/// number cannot be read out of stored state the tile says so — the spend
/// basis and the unswept book are the two that matter — because a confident
/// zero over a hole is the same dishonesty as a confident invented number, and
/// it survives longer because it looks like good news.
class SystemStateSection extends StatefulWidget {
  final ApiClient api;

  /// Null until the session has loaded. The route behind this screen answers
  /// 403 to an own-scoped persona, so the section says so itself rather than
  /// rendering an outage.
  final Session? session;

  /// Where each panel hands off. Every one of these is the surface that holds
  /// the rows the panel counted.
  final VoidCallback? onOpenOntology;
  final VoidCallback? onOpenSources;
  final VoidCallback? onOpenTasks;
  final VoidCallback? onOpenIdentity;
  final VoidCallback? onOpenFlows;

  const SystemStateSection({
    super.key,
    required this.api,
    this.session,
    this.onOpenOntology,
    this.onOpenSources,
    this.onOpenTasks,
    this.onOpenIdentity,
    this.onOpenFlows,
  });

  @override
  State<SystemStateSection> createState() => _SystemStateSectionState();
}

class _SystemStateSectionState extends State<SystemStateSection> {
  Dashboard? _screen;
  String? _error;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  /// Whether this persona reads past its own work. The rail already drops the
  /// stop for one that does not; this catches the case where the section was
  /// open when the persona changed under it.
  bool get _mayRead => widget.session?.scope != 'own';

  Future<void> _load() async {
    if (!_mayRead) {
      setState(() => _loading = false);
      return;
    }
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final screen = await widget.api.dashboard();
      if (!mounted) return;
      setState(() {
        _screen = screen;
        _loading = false;
      });
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.message;
        _loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final screen = _screen;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(28, 20, 28, 0),
          child: PageHeading(
            eyebrow: 'Governance',
            title: 'System state',
            subtitle:
                'What the map holds, whether the outside world is arriving, '
                'whether judgment is flowing, and whether governance is '
                'holding. Every figure here is composed from a surface that '
                'already owns it, so each one is answerable by a list one '
                'stop away.',
            pills: [
              if (screen != null) ...[
                MetaPill('as of ${agoIso(screen.atIso)}'),
                // Named for what actually has a window. Two panels here read
                // over one — the Gateway ledger and the ModelCall spend — and
                // everything else is a stock as of now, so "rates over 30
                // days" put a window on counts that have none.
                MetaPill(
                  'ledger and spend cover ${screen.windowDays} days',
                ),
              ],
            ],
            trailing: OutlinedButton.icon(
              onPressed: _loading || !_mayRead ? null : _load,
              icon: const Icon(Icons.refresh, size: 16),
              label: const Text('Refresh'),
            ),
          ),
        ),
        Expanded(child: _body(screen)),
      ],
    );
  }

  Widget _body(Dashboard? screen) {
    if (!_mayRead) return const SystemStateNotYours();
    if (_error != null) {
      return PageBody(
        padding: const EdgeInsets.fromLTRB(28, 20, 28, 40),
        children: [ErrorNotice(message: _error!, onRetry: _load)],
      );
    }
    if (screen == null) return const CumulusLoading();
    return PageBody(
      padding: const EdgeInsets.fromLTRB(28, 18, 28, 48),
      children: [
        MapPanel(map: screen.map, onOpen: widget.onOpenOntology),
        ArrivalsPanel(
          arrivals: screen.arrivals,
          onOpen: widget.onOpenSources,
        ),
        JudgmentPanel(
          judgment: screen.judgment,
          onOpenTasks: widget.onOpenTasks,
          onOpenIdentity: widget.onOpenIdentity,
        ),
        GovernancePanel(
          governance: screen.governance,
          onOpenFlows: widget.onOpenFlows,
          onOpenSources: widget.onOpenSources,
        ),
      ],
    );
  }
}

/// What an own-scoped persona sees if the section is still open when they
/// become one. Not an error: the route behind it refuses on purpose, and a
/// Retry on a refusal that can never succeed is what reads like an outage.
class SystemStateNotYours extends StatelessWidget {
  const SystemStateNotYours({super.key});

  @override
  Widget build(BuildContext context) => PageBody(
    padding: const EdgeInsets.fromLTRB(28, 20, 28, 40),
    children: const [
      CumulusCard(
        child: CumulusEmptyState(
          icon: Icons.lock_outline,
          title: 'This is not scoped to you',
          // Second person, and never "this persona" — the persona is what
          // you are here, not a third party the screen is discussing.
          message:
              'System state reads every collection, feed, desk and ledger '
              'entry at once. Your access is to your own work, so there is '
              'nothing here for you rather than something withheld. Your '
              'queues are on Home and My Tasks.',
        ),
      ),
    ],
  );
}

// ---------------------------------------------------------------------------
// 1 · What the map holds
// ---------------------------------------------------------------------------

/// Objects by Collection, the disagreements standing in them, and conformance.
///
/// Grouped by Collection because that is the unit governance hangs off:
/// retention, custody and authoritativeness are properties of the set, so a
/// count grouped any other way is a count of nothing anybody governs.
class MapPanel extends StatelessWidget {
  final MapState map;
  final VoidCallback? onOpen;

  const MapPanel({super.key, required this.map, this.onOpen});

  @override
  Widget build(BuildContext context) {
    final biggest = map.collections.fold<int>(
      0,
      (m, c) => c.objects > m ? c.objects : m,
    );
    return SectionCard(
      title: 'What the map holds',
      subtitle: 'objects by Collection, as of now',
      headerTrailing: TextButton.icon(
        onPressed: onOpen,
        icon: const Icon(Icons.hub_outlined, size: 15),
        label: const Text('Open the ontology'),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          StatStrip(
            tiles: [
              // Exact, not `1.5K`. Every figure on this screen is meant to be
              // answerable by a list one stop away, and a rounded magnitude is
              // the one shape you cannot check a list against.
              StatTile(
                label: 'Objects',
                value: groupedCount(map.objects),
                caption: '${map.collections.length} collections govern them',
              ),
              StatTile(
                label: 'Links',
                value: groupedCount(map.links),
                caption: 'live edges between them',
              ),
              StatTile(
                label: 'Claim conflicts',
                value: '${map.conflicted}',
                caption: map.conflicted == 0
                    ? 'no object has two sources disagreeing'
                    : 'objects where two sources assert one field '
                          'differently; precedence picks what you read',
                valueColor: map.conflicted > 0
                    ? CumulusTone.warn.foreground
                    : null,
              ),
              StatTile(
                label: 'Conformance',
                value: map.invariants == null ? '—' : '${map.invariants}',
                caption: _conformanceCaption(map),
                valueColor: (map.invariants ?? 0) > 0
                    ? CumulusTone.danger.foreground
                    : null,
              ),
            ],
          ),
          const SizedBox(height: 18),
          if (map.objects == 0)
            Text(
              'Nothing has landed in the map yet. Every Collection below is '
              'declared and empty — the shape of the book, without the book.',
              style: CumulusText.body,
            ),
          const SizedBox(height: 6),
          for (final collection in map.collections)
            _CollectionRow(collection: collection, max: biggest),
        ],
      ),
    );
  }
}

String _conformanceCaption(MapState map) {
  if (map.invariants == null) {
    // Withheld, not reassured. A zero here would be a claim rather than the
    // refusal it actually is.
    return 'withheld — a TBox violation is a Naviant bug, and an alarm you '
        'cannot act on is worse than none';
  }
  if (map.invariants == 0) return 'the graph conforms to its own ontology';
  final kinds = (map.invariantKinds ?? const {}).entries
      .map((e) => '${e.value} ${e.key}')
      .join(' · ');
  return kinds.isEmpty ? 'TBox violations' : kinds;
}

class _CollectionRow extends StatelessWidget {
  final CollectionState collection;
  final int max;

  const _CollectionRow({required this.collection, required this.max});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          UsageBar(
            label: collection.label,
            count: collection.objects,
            max: max,
          ),
          const SizedBox(height: 4),
          Wrap(
            spacing: 10,
            runSpacing: 4,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              Text(
                collection.types.isEmpty
                    ? 'no object type'
                    : collection.types
                          .map((t) => '${t.name} ${t.instances}')
                          .join(' · '),
                style: CumulusText.mono,
              ),
              if (collection.authoritative)
                const StatusBadge(
                  'authoritative',
                  tone: CumulusTone.teal,
                  dense: true,
                ),
              Text(
                'custody ${collection.custody} · '
                '${collection.retention == 'indefinite'
                    ? 'retained indefinitely'
                    : 'retention ${collection.retention}'}',
                style: CumulusText.meta,
              ),
              if (collection.conflicted > 0)
                StatusBadge(
                  '${collection.conflicted} in conflict',
                  tone: CumulusTone.warn,
                  dense: true,
                ),
            ],
          ),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// 2 · Is the outside world arriving
// ---------------------------------------------------------------------------

/// Per-feed health, composed from the Sources rows rather than recounted.
class ArrivalsPanel extends StatelessWidget {
  final ArrivalsState arrivals;
  final VoidCallback? onOpen;

  const ArrivalsPanel({super.key, required this.arrivals, this.onOpen});

  @override
  Widget build(BuildContext context) {
    final waiting = arrivals.feeds.fold<int>(
      0,
      (sum, f) => sum + f.awaitingSteward,
    );
    return SectionCard(
      title: 'Is the outside world arriving',
      subtitle: 'one row per feed — what it pulled, what landed, what it became',
      headerTrailing: TextButton.icon(
        onPressed: onOpen,
        icon: const Icon(Icons.cloud_sync_outlined, size: 15),
        label: const Text('Open Sources'),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          StatStrip(
            tiles: [
              StatTile(
                label: 'Feeds',
                value: '${arrivals.feeds.length}',
                caption: arrivals.feeds.isEmpty
                    ? 'nothing has ever been delivered or registered'
                    : '${arrivals.estate.feedsActive} of '
                          '${arrivals.estate.feedsRegistered} registered feeds '
                          'are pullable',
              ),
              // Received, not landed. `landed` means new landing records on a
              // reify feed and every bound row on a bind feed, so a total
              // summed from it reported two thirds of the traffic as if it
              // were all of it. What was kept is stated beside it rather than
              // replacing it — both are true and they answer different
              // questions.
              StatTile(
                label: 'Records received',
                value: groupedCount(arrivals.recordsReceived),
                caption:
                    'rows handed to the door · '
                    '${groupedCount(arrivals.recordsLanded)} kept as landing '
                    'records or bound rows',
              ),
              StatTile(
                label: 'Awaiting a steward',
                value: '$waiting',
                caption: waiting == 0
                    ? 'no arriving row is stuck on a human answer'
                    : 'rows a feed could not resolve on its own',
                valueColor: waiting > 0 ? CumulusTone.warn.foreground : null,
              ),
              StatTile(
                label: 'Failing',
                value: '${arrivals.failing.length}',
                caption: arrivals.failing.isEmpty
                    ? 'no feed reported a failure'
                    : arrivals.failing.join(', '),
                valueColor: arrivals.failing.isNotEmpty
                    ? CumulusTone.danger.foreground
                    : null,
              ),
            ],
          ),
          const SizedBox(height: 16),
          EstateHeaderCard(estate: arrivals.estate),
          const SizedBox(height: 14),
          if (arrivals.feeds.isEmpty)
            Text(
              'No feed has delivered or been registered. A feed appears the '
              'first time it pushes to POST /api/v1/sources/deliver, or as '
              'soon as a registered estate makes it pullable.',
              style: CumulusText.body,
            )
          else
            for (final feed in arrivals.feeds)
              FeedHealthRow(
                feed: feed,
                // The served list, not a second derivation of it. The row used
                // to re-decide "no contract" from its own empty `contractId`,
                // which is the same answer by a different route — and the
                // route that can drift from the governance panel's count.
                underNoContract: arrivals.withoutContract.contains(feed.source),
              ),
        ],
      ),
    );
  }
}

/// One feed, with the two `unchanged`s kept apart.
///
/// A pull outcome of `unchanged` is the estate reporting no delta: nothing was
/// fetched and no run started. `unchangedRows` counts rows that arrived
/// byte-identical on a pull that *did* deliver. They share a word and are
/// different facts, so they are never printed in the same phrase.
class FeedHealthRow extends StatelessWidget {
  final FeedState feed;

  /// Whether the served `without_contract` list names this feed. Passed in
  /// rather than re-derived from [FeedState.contractId] so the badge and the
  /// governance panel's count are one answer.
  final bool underNoContract;

  const FeedHealthRow({
    super.key,
    required this.feed,
    this.underNoContract = false,
  });

  @override
  Widget build(BuildContext context) {
    final tone = feed.failed
        ? CumulusTone.danger
        : feed.awaitingSteward > 0
        ? CumulusTone.warn
        : feed.neverDelivered
        ? CumulusTone.neutral
        : CumulusTone.success;
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Wrap(
            spacing: 8,
            runSpacing: 4,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              Text(feed.source, style: CumulusText.rowTitle),
              if (feed.kind.isNotEmpty)
                StatusBadge(feed.kind, dense: true, uppercase: true),
              StatusBadge(
                feed.lastStatus.isEmpty ? 'never delivered' : feed.lastStatus,
                tone: tone,
                dense: true,
              ),
              if (underNoContract || feed.contractId.isEmpty)
                const StatusBadge(
                  'no contract',
                  tone: CumulusTone.danger,
                  dense: true,
                ),
              if (feed.active == false)
                const StatusBadge(
                  'inactive',
                  tone: CumulusTone.warn,
                  dense: true,
                ),
              if (feed.awaitingSteward > 0)
                StatusBadge(
                  '${feed.awaitingSteward} awaiting a steward',
                  tone: CumulusTone.warn,
                  dense: true,
                ),
            ],
          ),
          const SizedBox(height: 4),
          Text(
            feed.neverDelivered
                ? 'Nothing delivered yet — no run, and nothing landed.'
                : _arrivals,
            style: CumulusText.meta,
          ),
          // The connector's half, and the word `unchanged` in its other sense:
          // the estate reported no delta, so nothing was fetched at all.
          if (feed.pulls) ...[
            const SizedBox(height: 4),
            Text(
              [
                'pull',
                if (feed.lastPullAgeSeconds == null)
                  '· never pulled'
                else
                  '· pulled ${_ago(feed.lastPullAgeSeconds)}',
                if (feed.lastPullOutcome == 'unchanged')
                  '· the estate reported no delta, so nothing was fetched'
                else if (feed.lastPullOutcome.isNotEmpty)
                  '· ${feed.lastPullOutcome}',
                if (feed.watermarkAgeSeconds != null)
                  '· read up to ${_ago(feed.watermarkAgeSeconds)}',
              ].join(' '),
              style: CumulusText.meta,
            ),
          ],
          if (_destination.isNotEmpty) ...[
            const SizedBox(height: 4),
            Text(_destination, style: CumulusText.meta),
          ],
          if (_collapse.isNotEmpty) ...[
            const SizedBox(height: 4),
            Text(
              _collapse,
              style: CumulusText.meta.copyWith(
                color: CumulusColors.inkSecondary,
                fontStyle: FontStyle.italic,
              ),
            ),
          ],
          if (feed.lastPullError.isNotEmpty) ...[
            const SizedBox(height: 4),
            Text(
              'Last pull failed: ${feed.lastPullError}',
              style: CumulusText.meta.copyWith(color: CumulusColors.danger),
            ),
          ],
          if (feed.lastError.isNotEmpty) ...[
            const SizedBox(height: 4),
            Text(
              feed.lastError,
              style: CumulusText.meta.copyWith(color: CumulusColors.danger),
            ),
          ],
        ],
      ),
    );
  }

  /// What arrived, what was kept, and what became of it.
  ///
  /// Two arrival counts because they are two facts: `received` is what the
  /// delivery carried, `landed` is what the landing step kept, and on a reify
  /// feed they differ by most of the traffic. The three dispositions are
  /// printed only where the feed's contract reports them — a bind mapping has
  /// no resolve step and no payload hash, so "0 minted · 0 linked · 0
  /// redelivered" over 145 landed rows was a confident answer to a question
  /// this contract never asks.
  String get _arrivals {
    final parts = [
      '${feed.deliveries} delivery(s)',
      '${feed.records} received',
      '${feed.recordsLanded} newly landed',
    ];
    final created = feed.created;
    final linked = feed.linked;
    final unchanged = feed.unchangedRows;
    if (created == null && linked == null && unchanged == null) {
      parts.add('disposition not reported by this mapping');
      return parts.join(' · ');
    }
    if (created != null) parts.add('$created minted');
    if (linked != null) {
      parts.add('$linked linked to an entity already in the book');
    }
    if (unchanged != null) parts.add('$unchanged redelivered byte-identical');
    return parts.join(' · ');
  }

  /// Where the rows went: the type they *became*, then the types they resolve
  /// against. Never merged under one heading — a reify feed's rows land as
  /// `SourceRecord` and then resolve against Vendor, and printing the second
  /// under "the book now holds" described a book those rows are not in.
  ///
  /// A contract that resolves nothing still lands somewhere, so this stays
  /// non-empty for the employment-event ledger rather than vanishing.
  String get _destination {
    final landed = feed.landedType;
    final parts = <String>[
      if (landed != null) 'lands as ${_entity(landed, verb: 'land into')}',
      if (feed.entities.isNotEmpty)
        '${landed == null ? 'binds to' : 'resolves against'} '
            '${feed.entities.map((e) => _entity(e)).join(' · ')}',
    ];
    return parts.join(' · ');
  }

  /// The natural-key collapse, said only where it is *proved* rather than
  /// merely suggested.
  ///
  /// A shared book does not spoil the inference, which is why this is worth
  /// stating at all: another feed writing the same type can only make the
  /// book **larger**, so a feed that minted more rows than the whole book
  /// holds has certainly collapsed some of them. The same argument carries the
  /// rows a **bind** mapping landed, because those become exactly the types
  /// named and nothing else — a `reify` feed lands its own `lands_as` type
  /// first, so its row count is not comparable and nothing is claimed about it.
  String get _collapse {
    if (feed.contractId.isEmpty || feed.entities.isEmpty) return '';
    final held = feed.entities.fold<int>(0, (sum, e) => sum + e.instances);
    if (held == 0) return '';
    // A feed that never reports what it minted proves nothing by this
    // argument, so it is not offered one.
    final created = feed.created;
    if (created != null && created > held) {
      return 'Minted $created rows against a book holding $held: the '
          'natural key collapsed the difference — two source rows under one '
          'key are one entity, not a dropped delivery.';
    }
    if (feed.landsAs.isEmpty && feed.recordsLanded > held) {
      return 'Landed ${feed.recordsLanded} rows against a book holding $held: '
          'the natural key collapsed the difference — two source rows under '
          'one key are one entity, not a dropped delivery.';
    }
    return '';
  }

  /// A type and what the book holds of it, with the shared-book caveat when
  /// more than one contract writes into it — `instances` is then not this
  /// feed's output, and a row that did not say so would invite reading it as
  /// one. `verb` is how the other contracts reach it: they resolve *against* a
  /// resolution target and *land into* a landing type.
  static String _entity(EntityCount e, {String verb = 'resolve against'}) =>
      e.feeds > 1
      ? '${e.type} ${e.instances} (${e.feeds} feeds $verb it)'
      : '${e.type} ${e.instances}';
}

// ---------------------------------------------------------------------------
// 3 · Is judgment flowing
// ---------------------------------------------------------------------------

/// Queue depths with who holds each desk, the Gateway's window, and what the
/// counterparty books are waiting on.
class JudgmentPanel extends StatelessWidget {
  final JudgmentState judgment;
  final VoidCallback? onOpenTasks;
  final VoidCallback? onOpenIdentity;

  const JudgmentPanel({
    super.key,
    required this.judgment,
    this.onOpenTasks,
    this.onOpenIdentity,
  });

  @override
  Widget build(BuildContext context) {
    final ledger = judgment.ledger;
    final decisions = ledger.byRegime.fold<int>(0, (s, r) => s + r.total);
    final deepest = judgment.queues.fold<int>(
      0,
      (m, q) => q.open > m ? q.open : m,
    );
    return SectionCard(
      title: 'Is judgment flowing',
      subtitle:
          'what is on each desk, who holds it, and what the Gateway '
          'committed in the last ${ledger.days} days',
      headerTrailing: TextButton.icon(
        onPressed: onOpenTasks,
        icon: const Icon(Icons.inbox_outlined, size: 15),
        label: const Text('Open My Tasks'),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          StatStrip(
            tiles: [
              StatTile(
                label: 'Open tasks',
                value: '${judgment.openTasks}',
                caption: judgment.openTasks == 0
                    ? 'no desk is holding work'
                    : 'across ${judgment.queues.where((q) => q.open > 0).length} '
                          'desk(s)',
              ),
              StatTile(
                label: 'High priority',
                value: '${judgment.highPriority}',
                caption: 'a ratification sorts below these, never beside them',
                valueColor: judgment.highPriority > 0
                    ? CumulusTone.warn.foreground
                    : null,
              ),
              // "Governed writes: 27" over a book holding thousands of rows
              // read as a nearly idle system. A Gateway entry is one committed
              // *operation* — a sync feed's whole landing pass is one — so the
              // caption says what the unit is rather than letting the number
              // be read as a row count.
              StatTile(
                label: 'Gateway commits',
                value: '$decisions',
                caption: decisions == 0
                    ? 'nothing has been committed through the Gateway'
                    : '$decisions governed operations, ${ledger.refused} '
                          'refused — one commit writes a batch, not a row',
              ),
              StatTile(
                label: 'Standing proposals',
                value: '${judgment.identity.standing}',
                caption: judgment.identity.standing == 0
                    ? 'no merge is waiting on a steward'
                    : '${judgment.identity.awaitingDecision} carry a Task '
                          'somebody can answer',
              ),
            ],
          ),
          const SizedBox(height: 18),
          const FieldLabel('Desks'),
          const SizedBox(height: 6),
          if (judgment.unownedWithWork.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: Text(
                'Work is sitting on ${judgment.unownedWithWork.join(', ')}, '
                'which no persona is bound to. A Task routes to a queue and '
                'never to a person so work survives absence — which also means '
                'an unbound desk accumulates where no personal inbox would ever '
                'show it.',
                style: CumulusText.body.copyWith(color: CumulusColors.danger),
              ),
            ),
          for (final queue in judgment.queues)
            QueueDepthRow(queue: queue, max: deepest),
          const SizedBox(height: 16),
          const FieldLabel('Governed writes, by the regime they declared'),
          const SizedBox(height: 6),
          _LedgerWindowView(ledger: ledger),
          const SizedBox(height: 16),
          const FieldLabel('Counterparty identity'),
          const SizedBox(height: 6),
          _IdentityView(
            identity: judgment.identity,
            onOpen: onOpenIdentity,
          ),
        ],
      ),
    );
  }
}

/// One desk: depth, the age of the oldest thing on it, and who is bound.
class QueueDepthRow extends StatelessWidget {
  final QueueState queue;
  final int max;

  const QueueDepthRow({super.key, required this.queue, required this.max});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          UsageBar(
            label: queue.queue,
            count: queue.open,
            max: max,
            mono: true,
          ),
          const SizedBox(height: 4),
          Wrap(
            spacing: 10,
            runSpacing: 4,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              Text(
                queue.open == 0
                    ? 'nothing open'
                    : 'oldest waiting '
                          '${_since(queue.oldestOpenAgeSeconds)}'
                          // The kinds a person reads, not the enum a Task
                          // stores. `match_review` is a wire value; "Entity
                          // match review" is the thing on the desk, and
                          // `kindLabel` is where the console already says so.
                          ' · ${queue.byKind.entries.map((e) => '${e.value} '
                              '${kindLabel(e.key).toLowerCase()}').join(' · ')}',
                style: CumulusText.meta,
              ),
              if (queue.unowned)
                StatusBadge(
                  queue.open > 0 ? 'nobody bound' : 'no persona bound',
                  tone: queue.open > 0 ? CumulusTone.danger : CumulusTone.warn,
                  dense: true,
                )
              else
                Text('held by ${queue.bound.join(', ')}',
                    style: CumulusText.meta),
              if (queue.highPriority > 0)
                StatusBadge(
                  '${queue.highPriority} high',
                  tone: CumulusTone.warn,
                  dense: true,
                ),
            ],
          ),
        ],
      ),
    );
  }
}

/// A write regime as a person reads it. The same treatment `kindLabel` gives
/// a Task kind, and for the same reason: `resolution` is a wire value, and the
/// three of them are only distinguishable to somebody who already knows the
/// distinction the Gateway is drawing.
String regimeLabel(String regime) => switch (regime) {
  'observation' => 'Observation — a system asserting what it saw',
  'decision' => 'Decision — a person answering',
  'resolution' => 'Resolution — two records ruled one',
  _ => regime,
};

class _LedgerWindowView extends StatelessWidget {
  final LedgerWindow ledger;

  const _LedgerWindowView({required this.ledger});

  @override
  Widget build(BuildContext context) {
    if (ledger.inWindow == 0) {
      return Text(
        'Nothing has been committed through the Action Gateway in the last '
        '${ledger.days} days. The three regimes below exist whether or not '
        'anything used them.\n'
        '${ledger.byRegime.map((r) => regimeLabel(r.regime)).join(' · ')}',
        style: CumulusText.body,
      );
    }
    final widest = ledger.byRegime.fold<int>(
      0,
      (m, r) => r.total > m ? r.total : m,
    );
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        for (final regime in ledger.byRegime) ...[
          // Not `mono: true` any more: these were slugs typeset as slugs, and
          // a reader who has to be told what `observation` means is not helped
          // by having it set in code font.
          UsageBar(
            label: regimeLabel(regime.regime),
            count: regime.total,
            max: widest,
          ),
          const SizedBox(height: 2),
          Text(
            regime.refused == 0
                ? '${regime.committed} committed'
                : '${regime.committed} committed · ${regime.refused} refused '
                      'at the write',
            style: CumulusText.meta.copyWith(
              color: regime.refused > 0 ? CumulusColors.warn : null,
            ),
          ),
          const SizedBox(height: 6),
        ],
        Text(
          ledger.truncated
              ? 'Read from the ${ledger.scanned} newest entries, which the '
                    'window filled — there may be more inside it.'
              : '${ledger.inWindow} of ${ledger.scanned} entries fall in the '
                    'window · newest ${agoIso(ledger.newestAtIso)}',
          style: CumulusText.meta,
        ),
      ],
    );
  }
}

class _IdentityView extends StatelessWidget {
  final IdentityState identity;
  final VoidCallback? onOpen;

  const _IdentityView({required this.identity, this.onOpen});

  @override
  Widget build(BuildContext context) {
    final sweep = identity.lastSweep;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          sweep == null
              // Never a zeroed row standing in for a sweep that did not happen.
              ? 'The identity sweep has never run over these books, so nothing '
                    'has been proposed. What is below is the size of each book, '
                    'not a verdict on it.'
              // Present tense, because nobody has answered any of them yet:
              // "9 were ratifications" over zero decided reads as a report of
              // work that happened. A ratification is what the Task *is* —
              // a formality to countersign — not something it became. The
              // identity workbench's own footer is the phrasing this follows.
              : 'Swept ${agoIso(sweep.atIso)} — ${sweep.compared} pair(s) '
                    'compared, ${sweep.proposed} proposed, '
                    '${sweep.stewardTasks} Task(s) raised, '
                    '${sweep.ratifications} of those a ratification rather '
                    'than a question.',
          style: CumulusText.body,
        ),
        const SizedBox(height: 8),
        for (final book in identity.books)
          Padding(
            padding: const EdgeInsets.only(bottom: 4),
            child: Wrap(
              spacing: 10,
              runSpacing: 4,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                Text(book.label, style: CumulusText.rowTitle),
                Text(
                  '${book.objects} in the book · ${book.standing} standing · '
                  '${book.confirmed} confirmed · ${book.withdrawn} withdrawn',
                  style: CumulusText.meta,
                ),
                Text('desk ${book.stewardQueue}', style: CumulusText.mono),
              ],
            ),
          ),
        if (identity.withoutTask > 0) ...[
          const SizedBox(height: 6),
          Text(
            '${identity.withoutTask} standing proposal(s) carry no Task, so '
            'there is nowhere to decide them. The sweep raises one for every '
            'proposal it writes — these predate that rule or lost theirs.',
            style: CumulusText.meta.copyWith(color: CumulusColors.danger),
          ),
        ],
        const SizedBox(height: 6),
        Align(
          alignment: Alignment.centerLeft,
          child: TextButton.icon(
            onPressed: onOpen,
            icon: const Icon(Icons.compare_arrows, size: 15),
            label: const Text('Open the identity workbench'),
          ),
        ),
      ],
    );
  }
}

// ---------------------------------------------------------------------------
// 4 · Is governance holding
// ---------------------------------------------------------------------------

/// Where declared and observed disagree, which feeds arrive under no contract,
/// and what the ModelCalls cost — or why that figure is a hole.
class GovernancePanel extends StatelessWidget {
  final GovernanceState governance;
  final VoidCallback? onOpenFlows;
  final VoidCallback? onOpenSources;

  const GovernancePanel({
    super.key,
    required this.governance,
    this.onOpenFlows,
    this.onOpenSources,
  });

  @override
  Widget build(BuildContext context) {
    final spend = governance.spend;
    final problems = governance.registryProblems;
    // Two findings pointing opposite ways, and merging them made a breach
    // hide inside a heap of stale declarations. `undeclared` is a flow
    // touching what no Collection permits — governance failing to cover what
    // is happening, and the only one of the two anybody must act on today.
    // The rest declare a path no run has taken, which is a promise nothing has
    // kept: worth stating, never worth alarming about.
    final breaches = governance.gaps.where((g) => g.undeclared).length;
    final unexercised = governance.gaps.length - breaches;
    return SectionCard(
      title: 'Is governance holding',
      subtitle: 'what is declared, against what actually happened',
      headerTrailing: TextButton.icon(
        onPressed: onOpenFlows,
        icon: const Icon(Icons.account_tree_outlined, size: 15),
        label: const Text('Open Flows'),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          StatStrip(
            tiles: [
              StatTile(
                label: 'Flows',
                value: '${governance.flowsTotal}',
                caption: 'every flow the engine can name, run or not',
              ),
              StatTile(
                label: 'Undeclared reach',
                value: '$breaches',
                caption: breaches > 0
                    ? 'flow(s) reading or writing what no Collection permits'
                          '${unexercised == 0 ? '' : ' · $unexercised more '
                              'declare a path no run has taken'}'
                    : unexercised == 0
                    ? 'declared and observed agree everywhere'
                    : '$unexercised flow(s) declare a path no run has taken — '
                          'a promise, not a breach',
                valueColor: breaches > 0 ? CumulusTone.danger.foreground : null,
              ),
              StatTile(
                label: 'Feeds with no contract',
                value: '${governance.feedsWithoutContract.length}',
                caption: governance.feedsWithoutContract.isEmpty
                    ? 'every feed lands under a declared Mapping'
                    : governance.feedsWithoutContract.join(', '),
                valueColor: governance.feedsWithoutContract.isNotEmpty
                    ? CumulusTone.danger.foreground
                    : null,
              ),
              StatTile(
                label: 'ModelCall spend',
                value: _spendValue(spend),
                caption: _spendCaption(spend),
                valueColor: spend.basis == 'unrecorded'
                    ? CumulusTone.warn.foreground
                    : null,
              ),
            ],
          ),
          const SizedBox(height: 18),
          if (governance.feedsWithoutContract.isNotEmpty) ...[
            Text(
              'Rows are arriving under no declared Mapping from '
              '${governance.feedsWithoutContract.join(', ')} — nothing says '
              'what they bind to, who wins a disagreement, or how long they '
              'are kept.',
              style: CumulusText.body.copyWith(color: CumulusColors.danger),
            ),
            const SizedBox(height: 6),
            Align(
              alignment: Alignment.centerLeft,
              child: TextButton.icon(
                onPressed: onOpenSources,
                icon: const Icon(Icons.cloud_sync_outlined, size: 15),
                label: const Text('See them on Sources'),
              ),
            ),
            const SizedBox(height: 12),
          ],
          const FieldLabel('Declared against observed'),
          const SizedBox(height: 6),
          if (governance.gaps.isEmpty)
            Text(
              'Every flow writes and reads exactly what a Collection declares '
              'it may. Nothing is undeclared, and no declaration is standing '
              'unexercised.',
              style: CumulusText.body,
            )
          else
            for (final gap in governance.gaps) FlowGapRow(gap: gap),
          const SizedBox(height: 14),
          const FieldLabel('The mapping registry'),
          const SizedBox(height: 6),
          Text(
            problems == null
                ? 'Withheld — a Mapping naming a type that does not exist is a '
                      'Naviant declaration bug, not something this deployment '
                      'can act on.'
                : problems.isEmpty
                ? 'Every Mapping names a type and a Collection that exist.'
                : problems.map((p) => '· $p').join('\n'),
            style: CumulusText.body.copyWith(
              color: (problems != null && problems.isNotEmpty)
                  ? CumulusColors.danger
                  : null,
            ),
          ),
        ],
      ),
    );
  }
}

String _spendValue(SpendState spend) => switch (spend.basis) {
  // A hole is not a number. Rendering $0.00 over calls nobody priced is the
  // one failure mode this tile exists to prevent.
  'unrecorded' => '—',
  _ => '\$${spend.costUsd.toStringAsFixed(2)}',
};

String _spendCaption(SpendState spend) => switch (spend.basis) {
  'unrecorded' =>
    '${spend.modelCalls} model call(s) over ${spend.days}d, none carrying a '
        'cost — the runs record which model ran, not what it cost',
  'no-model-calls' =>
    'nothing called a model in ${spend.days}d, so this is genuinely zero',
  'partial' =>
    '${spend.pricedModelCalls} of ${spend.modelCalls} model call(s) priced — '
        'the rest is not in this figure',
  _ => '${spend.modelCalls} model call(s), all priced · '
      '${compactCount(spend.tokens)} tokens',
};

/// One flow whose declared footprint and its observed one disagree.
class FlowGapRow extends StatelessWidget {
  final FlowGap gap;

  const FlowGapRow({super.key, required this.gap});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Wrap(
            spacing: 8,
            runSpacing: 4,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              Text(gap.label, style: CumulusText.rowTitle),
              Text(gap.flowId, style: CumulusText.mono),
              StatusBadge(
                switch (gap.state) {
                  'never_ran' => 'never ran',
                  'ran_but_wrote_nothing' => 'ran, wrote nothing',
                  _ => '${gap.totalRuns} run(s)',
                },
                tone: gap.state == 'never_ran'
                    ? CumulusTone.neutral
                    : CumulusTone.accent,
                dense: true,
              ),
              if (gap.undeclared)
                const StatusBadge(
                  'undeclared',
                  tone: CumulusTone.danger,
                  dense: true,
                ),
            ],
          ),
          const SizedBox(height: 3),
          // The two readings, never merged: one is governance failing to cover
          // what happens, the other is a promise nothing has kept.
          if (gap.undeclaredWrites.isNotEmpty)
            Text(
              'writes ${gap.undeclaredWrites.join(', ')} — no Collection '
              'declares it may',
              style: CumulusText.meta.copyWith(color: CumulusColors.danger),
            ),
          if (gap.undeclaredReads.isNotEmpty)
            Text(
              'reads ${gap.undeclaredReads.join(', ')} — no Collection '
              'declares it may',
              style: CumulusText.meta.copyWith(color: CumulusColors.danger),
            ),
          if (gap.unobservedWrites.isNotEmpty)
            Text(
              'declares it writes ${gap.unobservedWrites.join(', ')} — no run '
              'ever has',
              style: CumulusText.meta,
            ),
          if (gap.unobservedReads.isNotEmpty)
            Text(
              'declares it reads ${gap.unobservedReads.join(', ')} — no run '
              'ever has',
              style: CumulusText.meta,
            ),
        ],
      ),
    );
  }
}

/// A duration a person reads, from the seconds the server measured.
///
/// `never` for an absent age rather than a large number: "never" and "a long
/// time ago" are opposite readings, and only the second is about staleness.
String _since(int? seconds) {
  if (seconds == null) return 'never';
  if (seconds < 60) return 'just now';
  if (seconds < 3600) return '${seconds ~/ 60}m';
  if (seconds < 86400) return '${seconds ~/ 3600}h';
  if (seconds < 86400 * 30) return '${seconds ~/ 86400}d';
  return '${seconds ~/ (86400 * 30)}mo';
}

/// [_since] with the direction attached, and the one case where attaching it
/// blindly is wrong: a pull a moment old rendered as "pulled just now ago".
String _ago(int? seconds) {
  final span = _since(seconds);
  return span == 'never' || span == 'just now' ? span : '$span ago';
}
