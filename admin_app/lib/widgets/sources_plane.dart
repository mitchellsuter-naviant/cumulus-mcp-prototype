import 'package:flutter/material.dart';

import '../models/data_layer.dart';
import '../services/api_client.dart';
import '../theme/cumulus_theme.dart';
import 'cumulus_shell.dart';
import 'skeleton.dart';
import 'ui_kit.dart';

/// Sources: did the outside world deliver, and was it clean.
///
/// One row per feed, and the row carries both halves of the arrangement — how
/// it is running *and* the Mapping it lands under. That join is the whole
/// argument for the screen. A feed delivering cleanly under a contract nobody
/// declared is a different problem from one that is failing, and reading the
/// two off separate lists is exactly how the first kind goes unnoticed; the
/// old version had them on separate lenses.
///
/// A pulled feed carries a third half, and it is the one the runs cannot tell
/// you: the connector's cursor. A pull that found nothing starts no run and a
/// pull that failed has no run at all, so a screen assembled only from runs
/// shows both as a feed that has simply gone quiet. The cursor sits on the
/// same row, and the control that advances it sits beside it.
///
/// The artifacts a feed left ride on its row for the same reason, rather than
/// on a lens of their own. A document store listed whole answers "what is in
/// the store", which is not a question this screen asks — and the artifacts
/// an AP run kept are evidence for a case, reachable as `Document` instances
/// under Ontology, not evidence that a feed delivered.
class SourcesPlane extends StatefulWidget {
  final ApiClient api;

  const SourcesPlane({super.key, required this.api});

  @override
  State<SourcesPlane> createState() => _SourcesPlaneState();
}

class _SourcesPlaneState extends State<SourcesPlane> {
  SourcesScreen? _screen;
  String? _error;
  bool _loading = true;

  /// The feed a pull is in flight for, and what the last pull of each feed
  /// said. Held by the plane rather than by the row so a rebuild — including
  /// the one the finished pull itself causes — cannot drop either.
  String? _syncing;
  final Map<String, SyncNote> _notes = {};

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final screen = await widget.api.sourcesScreen();
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

  /// Pull one feed, then redraw it from the row the pull answered with.
  ///
  /// The note reports this pull's own delta — what it added to the totals the
  /// row already showed — so the sentence above the row and the counts on it
  /// add up instead of being two tellings of different things.
  Future<void> _syncNow(SourceFeed feed) async {
    setState(() {
      _syncing = feed.source;
      _notes.remove(feed.source);
    });
    try {
      final outcome = await widget.api.syncSourceNow(feed.source);
      if (!mounted) return;
      setState(() {
        _syncing = null;
        _notes[feed.source] = SyncNote.of(outcome, feed);
        final row = outcome.row;
        if (row != null) _screen = _screen?.replacing(row);
      });
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() {
        _syncing = null;
        _notes[feed.source] = SyncNote(message: e.message, failed: true);
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final screen = _screen;
    final feeds = screen?.sources ?? const <SourceFeed>[];
    // Received, not landed. `landed` counts new landing records on a reify
    // feed and every bound row on a bind feed, so a header summed from it
    // reported a third of the estate's traffic as if it were all of it — the
    // PSA feed alone carries 374 rows and keeps 13. What was kept is stated
    // beside it; both are true and they answer different questions.
    final received = feeds.fold<int>(0, (sum, f) => sum + f.records);
    final landed = feeds.fold<int>(0, (sum, f) => sum + f.recordsLanded);
    final waiting = feeds.fold<int>(0, (sum, f) => sum + f.awaitingSteward);
    final problems = screen?.problems ?? const <String>[];

    return PageBody(
      padding: const EdgeInsets.fromLTRB(28, 20, 28, 40),
      children: [
        PlaneHeader(
          summary:
              'What arrives from outside: each inbound feed against the '
              'contract it lands under. What a feed delivered is assembled '
              'from its sync runs, so a feed with no run has never delivered '
              'and this cannot pretend otherwise. A pulled feed adds the one '
              'thing the runs cannot hold — the connector\'s cursor, which '
              'survives a pull that found nothing and a pull that failed.',
          pills: [
            // The one count this screen owns. Everything else about the map is
            // reported where it is measured — the model's conformance on
            // Ontology, the corpus on the Library — so no figure here is a
            // second telling of a figure there.
            if (screen != null) ...[
              MetaPill('${feeds.length} feeds'),
              MetaPill(
                received == 0
                    ? 'nothing received yet'
                    : '$received records received · $landed newly landed',
              ),
              if (waiting > 0)
                MetaPill(
                  '$waiting awaiting a steward',
                  tone: CumulusTone.warn,
                ),
            ],
          ],
          trailing: OutlinedButton.icon(
            onPressed: _loading ? null : _load,
            icon: const Icon(Icons.refresh, size: 16),
            label: const Text('Refresh'),
          ),
        ),
        if (_error != null) ErrorNotice(message: _error!, onRetry: _load),
        if (_loading && screen == null)
          // The plane header is already real; the estate card and the feed
          // list are what the read is for.
          const SkeletonPulse(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              spacing: 16,
              children: [
                SkeletonCard(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      SkeletonBar(width: 176, height: 13),
                      SizedBox(height: 10),
                      SkeletonBar(width: 320, height: 10),
                      SizedBox(height: 16),
                      Row(
                        spacing: 8,
                        children: [
                          SkeletonPill(width: 96),
                          SkeletonPill(width: 118),
                          SkeletonPill(width: 84),
                        ],
                      ),
                    ],
                  ),
                ),
                SkeletonListCard(rows: 5),
              ],
            ),
          )
        else ...[
          if (screen != null) EstateHeaderCard(estate: screen.estate),
          // Withheld from a tenant, so an empty list is not proof of health.
          // A registry that names a type which does not exist is a Naviant
          // bug, and an alarm nobody addressed can take action on is worse
          // than no alarm.
          if (problems.isNotEmpty)
            CumulusCard(
              borderColor: CumulusColors.danger,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const SectionHeader(
                    title: 'The mapping registry does not conform',
                    subtitle: 'a contract names something that does not exist',
                  ),
                  const SizedBox(height: 8),
                  for (final problem in problems)
                    Text('· $problem', style: CumulusText.body),
                ],
              ),
            ),
          if (feeds.isEmpty)
            const CumulusCard(
              child: CumulusEmptyState(
                icon: Icons.cloud_off,
                title: 'No feed has been delivered or registered',
                message:
                    'A feed appears here the first time it pushes to '
                    'POST /api/v1/sources/deliver, or as soon as a registered '
                    'estate makes it pullable — whichever happens first.',
              ),
            )
          else
            ListCard(
              title: 'Inbound feeds',
              subtitle:
                  'one row per source and kind — how it ran, what it '
                  'promised, and what it left behind',
              children: [
                for (final feed in feeds)
                  SourceFeedRow(
                    feed: feed,
                    syncing: _syncing == feed.source,
                    note: _notes[feed.source],
                    onSyncNow: () => _syncNow(feed),
                  ),
              ],
            ),
          if (screen != null) _WritePlaneCard(writes: screen.writes),
          CumulusCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const SectionHeader(
                  title: 'How a source works here',
                  subtitle:
                      'a source is not a primitive — it is a Transport plus '
                      'an inbound Flow',
                ),
                const SizedBox(height: 12),
                Text(
                  'CDC, sync and MDM are Flows over sourced transport rather '
                  'than mechanisms of their own, so every row above is a Flow '
                  'you can open in the run list. Each delivery lands its rows '
                  'exactly as they arrived — so a rule change can re-resolve '
                  'without re-fetching — scores them against the master, and '
                  'then either links, mints, or raises a steward Task. A '
                  'batch feed never suspends for one ambiguous row.',
                  style: CumulusText.body,
                ),
                const SizedBox(height: 10),
                Text(
                  'A contract that says reify mints an object per row, which '
                  'is right only when the row itself needs identity, '
                  'provenance or adjudication. A high-volume feed binds '
                  'instead: a column is declared to carry identifiers of a '
                  'type, resolution happens lazily, and unresolvable ids '
                  'escalate on aggregates. A million readings must never '
                  'become a million objects.',
                  style: CumulusText.body,
                ),
              ],
            ),
          ),
        ],
      ],
    );
  }
}

/// The outbound half of the terms: what Cumulus may write back, and every
/// write it ever sent.
///
/// On this screen because a registered system is talked to on two-way terms —
/// the feeds above are what it tells us, the table here is what we may tell
/// it, and the journal is what we actually said. The journal is quoted rather
/// than re-derived from runs: the row it exists for is the one whose run died
/// between the PATCH and its own bookkeeping, and a row here with no run
/// around it may be the only surviving statement that Cumulus changed
/// somebody's data.
class _WritePlaneCard extends StatelessWidget {
  final WritePlane writes;

  const _WritePlaneCard({required this.writes});

  @override
  Widget build(BuildContext context) {
    final journal = writes.journal;
    return CumulusCard(
      borderColor: writes.openWrites > 0 ? CumulusColors.danger : null,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SectionHeader(
            title: 'What Cumulus may write back',
            subtitle:
                'the closed table of permitted writes, and the journal of '
                'every one ever sent',
          ),
          const SizedBox(height: 10),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              // A fact about this process: off means the writer holds no
              // callable that could reach a network at all, not a polite
              // refusal in front of one that could.
              MetaPill(
                writes.enabled
                    ? 'outbound writes enabled'
                    : 'outbound writes disabled — the live door does not exist '
                          'in this deployment',
                tone: writes.enabled ? CumulusTone.warn : CumulusTone.neutral,
              ),
              MetaPill('${writes.targets.length} writable target(s)'),
              if (writes.journalTotal > 0)
                MetaPill('${writes.journalTotal} write(s) ever sent'),
              if (writes.openWrites > 0)
                MetaPill(
                  '${writes.openWrites} awaiting echo',
                  tone: CumulusTone.warn,
                ),
            ],
          ),
          const SizedBox(height: 12),
          // The table verbatim: every write this system can make, in one
          // screen. Adding a row is a governance change with a review
          // attached — which is the point of it being a closed literal.
          for (final target in writes.targets)
            Padding(
              padding: const EdgeInsets.only(bottom: 6),
              child: Wrap(
                spacing: 12,
                runSpacing: 4,
                children: [
                  Text(
                    '${target.app} · ${target.resource}.${target.field}',
                    style: CumulusText.mono,
                  ),
                  for (final t in target.transitions)
                    Text(
                      '· ${t.fromValue} → ${t.toValues.join(" | ")}',
                      style: CumulusText.meta,
                    ),
                  Text('· Action ${target.action}', style: CumulusText.meta),
                ],
              ),
            ),
          if (journal.isEmpty) ...[
            const SizedBox(height: 4),
            Text(
              'Nothing has ever been written to the estate from this '
              'deployment. Every outbound write journals its intent before '
              'the request leaves, so an empty journal is evidence, not '
              'absence of bookkeeping.',
              style: CumulusText.body,
            ),
          ] else ...[
            const SizedBox(height: 6),
            for (final row in journal) _WriteJournalLine(row: row),
            if (writes.journalTotal > journal.length)
              Text(
                '… the ${journal.length} most recent of '
                '${writes.journalTotal}.',
                style: CumulusText.meta,
              ),
          ],
        ],
      ),
    );
  }
}

/// One journal row: the transition as approved, how it settled, who asked.
class _WriteJournalLine extends StatelessWidget {
  final WriteJournalRow row;

  const _WriteJournalLine({required this.row});

  @override
  Widget build(BuildContext context) {
    final tone = row.failed
        ? CumulusTone.danger
        : row.open
        ? CumulusTone.warn
        : CumulusTone.success;
    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: Wrap(
        spacing: 12,
        runSpacing: 4,
        crossAxisAlignment: WrapCrossAlignment.center,
        children: [
          StatusBadge(row.state, tone: tone, dense: true),
          Text(
            '${row.resource}/${row.recordId} · ${row.field}: '
            '${row.observed} → ${row.value}',
            style: CumulusText.mono,
          ),
          // What the estate held afterwards — the same string on the happy
          // path, and the whole finding otherwise.
          if (row.verified.isNotEmpty && row.verified != row.value)
            Text(
              '· re-read found "${row.verified}"',
              style: CumulusText.meta.copyWith(color: CumulusColors.danger),
            ),
          if (row.actor.isNotEmpty)
            Text('· by ${row.actor}', style: CumulusText.meta),
          if (row.runId.isNotEmpty)
            Text('· ${row.runId}', style: CumulusText.mono),
          Text('· ${_ago(row.intendedAt)}', style: CumulusText.meta),
          if (row.error.isNotEmpty)
            Text(
              '· ${row.error}',
              style: CumulusText.meta.copyWith(color: CumulusColors.danger),
            ),
        ],
      ),
    );
  }
}

/// The registered estate, above the feeds it explains.
///
/// One card rather than a line on every row: registration is one fact for the
/// whole connector, and five idle feeds explained five times is a finding
/// buried in its own repetition. Nothing registered is drawn as a state with a
/// next step in it, not as an error — it is the default everywhere but a dev
/// box, and a screen that alarmed about it would cry wolf on every deployment
/// that pulls nothing.
class EstateHeaderCard extends StatelessWidget {
  final EstateCard estate;

  const EstateHeaderCard({super.key, required this.estate});

  @override
  Widget build(BuildContext context) {
    if (!estate.registered) {
      return const CumulusCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            SectionHeader(
              title: 'No estate is registered',
              subtitle: 'nothing is pulled — every feed below was pushed here',
            ),
            SizedBox(height: 10),
            Text(
              'Setting ESTATE_PROJECT (and ESTATE_NAMESPACE) registers a '
              'customer estate. The connector then discovers which apps that '
              'namespace runs, and every feed in its registry whose app is '
              'among them becomes pullable from this screen.',
              style: CumulusText.body,
            ),
          ],
        ),
      );
    }

    final failed = estate.discoveryError.isNotEmpty;
    return CumulusCard(
      borderColor: failed ? CumulusColors.danger : null,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SectionHeader(
            title: 'Estate · ${estate.namespace.isEmpty ? "—" : estate.namespace}',
            subtitle: estate.project,
          ),
          const SizedBox(height: 10),
          if (failed)
            // The estate being unreachable and the estate answering that it
            // runs no apps are opposite findings, and only the second is about
            // the customer. Said plainly, with no control that pretends
            // pressing it again would help.
            Text(
              'Discovery failed, so what this namespace runs is unknown: '
              '${estate.discoveryError}',
              style: CumulusText.body.copyWith(color: CumulusColors.danger),
            )
          else
            Text(
              'Discovered from ${estate.discoveryUrl}. Feeds are filtered '
              'against the apps this namespace runs, so a registry row for an '
              'app it does not run stays inactive rather than failing.',
              style: CumulusText.body,
            ),
          const SizedBox(height: 12),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              MetaPill(
                '${estate.feedsActive} of ${estate.feedsRegistered} feeds '
                'active',
                tone: estate.feedsActive == 0
                    ? CumulusTone.warn
                    : CumulusTone.success,
              ),
              if (estate.industry.isNotEmpty) MetaPill(estate.industry),
              MetaPill(
                estate.apps.isEmpty
                    ? 'no apps discovered'
                    : '${estate.apps.length} apps · ${estate.apps.join(", ")}',
              ),
              // The "pulse" is scripted business activity: the estate runs
              // process scripts that invent rows on a timer. "pulse on · low"
              // and "4 processes running" both read as operational health —
              // a live system doing real work — when what they say is that
              // everything arriving from here is fabricated. One pill, and it
              // leads with the word that matters.
              //
              // Absent and off stay different facts: an estate that reports no
              // pulse block at all is not one whose simulation is stopped, so
              // it keeps its process count and claims nothing about activity.
              if (estate.pulseEnabled == null)
                MetaPill(
                  estate.processes.isEmpty
                      ? 'no process scripts declared'
                      : '${estate.processes.length} process scripts · '
                            'activity not reported',
                )
              else
                MetaPill(
                  estate.pulseEnabled!
                      ? 'simulated estate · '
                            '${estate.processes.length} process scripts · '
                            'synthetic activity: '
                            '${estate.pulseIntensity.isEmpty ? "unstated" : estate.pulseIntensity}'
                      : 'simulated estate · '
                            '${estate.processes.length} process scripts · '
                            'synthetic activity: stopped',
                  tone: CumulusTone.warn,
                ),
              MetaPill(
                estate.pullIntervalSeconds > 0
                    ? 'polling every ${estate.pullIntervalSeconds}s'
                    : 'pulled on demand — no loop of our own',
              ),
              // A fact about this process, not about the estate. "Unauthenticated
              // estate" described the customer's system as open to the world;
              // what it actually means is that ESTATE_API_KEY is unset here.
              MetaPill(
                estate.authenticated
                    ? 'credential configured'
                    : 'no credential configured',
              ),
            ],
          ),
        ],
      ),
    );
  }
}

/// What the last Sync now of one feed did, in the words the row can print.
///
/// Built from the outcome *and* the row that preceded it, so the numbers are
/// this pull's delta against totals the reader was already looking at rather
/// than a second, unrelated count.
class SyncNote {
  final String message;
  final bool failed;

  const SyncNote({required this.message, required this.failed});

  factory SyncNote.of(SyncNowOutcome outcome, SourceFeed before) {
    if (outcome.error.isNotEmpty) {
      return SyncNote(message: outcome.error, failed: true);
    }
    if (outcome.outcome == 'unchanged' || outcome.recordsPulled == 0) {
      return const SyncNote(
        message: 'Nothing has changed since the last watermark, so nothing '
            'was delivered.',
        failed: false,
      );
    }
    final row = outcome.row;
    final landed = row == null ? null : row.recordsLanded - before.recordsLanded;
    // Only a feed whose contract suppresses byte-identical redeliveries has an
    // `unchanged` to report a delta on. On one that does not, the phrase is
    // omitted rather than printed as a zero it never measured.
    final unchanged = row?.unchanged == null || before.unchanged == null
        ? null
        : row!.unchanged! - before.unchanged!;
    final waiting = row == null
        ? null
        : row.awaitingSteward - before.awaitingSteward;
    final delta = [
      if (landed != null) '$landed newly landed',
      if (unchanged != null) '$unchanged unchanged',
      if (waiting != null && waiting > 0) '$waiting awaiting a steward',
    ].join(', ');
    return SyncNote(
      message: [
        'Pulled ${outcome.recordsPulled} record(s) '
            '(${outcome.pulled.entries.map((e) => "${e.value} ${e.key}").join(" · ")})',
        if (outcome.chunks > 1) 'in ${outcome.chunks} deliveries',
        if (delta.isNotEmpty) '· $delta',
      ].join(' '),
      failed: false,
    );
  }
}

/// One feed: health on the left, contract underneath, last delivery on the
/// right. The contract is not optional decoration — its absence is the row's
/// most important finding, so a feed under no Mapping says so in red.
///
/// A pulled feed adds its cursor and the control that advances it. Whether the
/// control is drawn at all is the server's answer ([PullState.syncNowAllowed]),
/// never this widget's guess: a persona that may read the feed but not pull it
/// gets the sentence saying so, rather than a button that refuses when pressed.
class SourceFeedRow extends StatelessWidget {
  final SourceFeed feed;

  /// A pull of this feed is in flight.
  final bool syncing;

  /// What the last pull said, if this screen started one.
  final SyncNote? note;

  final VoidCallback? onSyncNow;

  const SourceFeedRow({
    super.key,
    required this.feed,
    this.syncing = false,
    this.note,
    this.onSyncNow,
  });

  @override
  Widget build(BuildContext context) {
    final contract = feed.contract;
    final pull = feed.pull;
    final tone = feed.failed
        ? CumulusTone.danger
        : feed.awaitingSteward > 0
        ? CumulusTone.warn
        : feed.neverDelivered
        ? CumulusTone.neutral
        : CumulusTone.success;
    return CumulusListRow(
      leading: Container(
        width: 34,
        height: 34,
        alignment: Alignment.center,
        decoration: BoxDecoration(
          color: tone.background,
          borderRadius: BorderRadius.circular(10),
        ),
        child: Icon(_iconFor(feed.kind), size: 17, color: tone.foreground),
      ),
      title: Row(
        children: [
          Flexible(child: Text(feed.source)),
          if (feed.kind.isNotEmpty) ...[
            const SizedBox(width: 10),
            StatusBadge(feed.kind, dense: true, uppercase: true),
          ],
          const SizedBox(width: 6),
          // A registered feed that has never pulled has no run and so no
          // status. It says that, rather than wearing an empty badge.
          StatusBadge(
            feed.lastStatus.isEmpty ? 'never delivered' : feed.lastStatus,
            tone: tone,
            dense: true,
          ),
          if (pull != null && !pull.active) ...[
            const SizedBox(width: 6),
            StatusBadge(
              'inactive',
              tone: CumulusTone.warn,
              dense: true,
            ),
          ],
          if (feed.awaitingSteward > 0) ...[
            const SizedBox(width: 6),
            StatusBadge(
              '${feed.awaitingSteward} awaiting a steward',
              tone: CumulusTone.warn,
              dense: true,
            ),
          ],
          if (contract == null) ...[
            const SizedBox(width: 6),
            const Tooltip(
              message:
                  'Rows are arriving under no declared Mapping — nothing '
                  'says what they bind to, who wins a disagreement, or how '
                  'long they are kept.',
              child: StatusBadge(
                'no contract',
                tone: CumulusTone.danger,
                dense: true,
              ),
            ),
          ],
        ],
      ),
      subtitle: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            feed.neverDelivered
                ? 'Nothing delivered yet — no run, and nothing landed.'
                : _arrivals(feed),
          ),
          if (!feed.neverDelivered) ...[
            const SizedBox(height: 6),
            Wrap(
              spacing: 12,
              runSpacing: 4,
              children: [
                Text(feed.flowId, style: CumulusText.mono),
                // One label for one thing. The run-derived watermark and the
                // connector's cursor are the same value read two ways, and
                // printing both under two names invited reading them as two
                // positions that might disagree. On a pulled feed the
                // connector's is authoritative — it survives a pull that
                // found nothing — so this line only speaks for a feed that
                // pushes and has no connector state at all.
                if (pull == null && feed.watermark.isNotEmpty)
                  Text('· cursor ${_utc(feed.watermark)}',
                      style: CumulusText.meta),
                if (feed.lastRunId.isNotEmpty)
                  Text('· ${feed.lastRunId}', style: CumulusText.mono),
              ],
            ),
          ],
          if (pull != null) ...[
            const SizedBox(height: 6),
            Wrap(
              spacing: 12,
              runSpacing: 4,
              children: [
                Text('pull · ${pull.estateApp}', style: CumulusText.meta),
                Text(pull.resources.join(", "), style: CumulusText.mono),
                Text(
                  pull.everPulled
                      ? '· pulled ${_ago(pull.lastPullAt)}'
                      : '· never pulled',
                  style: CumulusText.meta,
                ),
                if (pull.lastOutcome.isNotEmpty)
                  Text('· ${pull.lastOutcome}', style: CumulusText.meta),
                // Per resource, because they advance independently. Collapsing
                // five cursors to their maximum stated a position four of them
                // have not reached — and it is the number the Sync now caption
                // below was promising to read from.
                if (pull.watermarks.length == 1)
                  Text('· cursor ${_utc(pull.watermark)}',
                      style: CumulusText.meta)
                else
                  for (final cursor in pull.orderedWatermarks)
                    Text('· ${cursor.$1} up to ${_utc(cursor.$2)}',
                        style: CumulusText.meta),
                // A time that has not happened yet, said in the tense it is
                // in: `agoLabel` rendered the next scheduled pull as "next
                // just now", and as "next 3m ago" once the clock passed it.
                if (pull.nextPullAt.isNotEmpty)
                  Text('· next pull ${_until(pull.nextPullAt)}',
                      style: CumulusText.meta),
              ],
            ),
            // Verbatim: an operator diagnosing an estate needs the words the
            // estate used, not a category this screen invented for them.
            if (pull.lastError.isNotEmpty) ...[
              const SizedBox(height: 6),
              Text(
                'Last pull failed: ${pull.lastError}',
                style: CumulusText.meta.copyWith(color: CumulusColors.danger),
              ),
            ],
          ],
          if (contract != null) ...[
            const SizedBox(height: 6),
            // The contract in the order somebody reads it: which system this
            // is, what its rows become, who wins a disagreement, how often it
            // arrives, where the rows are kept and what happens when one will
            // not resolve. `system` was parsed and never shown, so the line
            // opened on a bare enum — `contract: bind` — and the one fact that
            // says what the feed *is* was the one fact missing.
            Wrap(
              spacing: 12,
              runSpacing: 4,
              children: [
                if (contract.system.isNotEmpty)
                  Text(contract.system, style: CumulusText.meta),
                Text('· ${_strategy(contract)}', style: CumulusText.meta),
                if (contract.resolvesTo.isNotEmpty)
                  Text('· resolves against ${contract.resolvesTo.join(", ")}',
                      style: CumulusText.meta),
                // Not "claims write as crm", which names the label rather than
                // what it decides. Precedence is only ever read when two
                // systems disagree, and this is that sentence.
                if (contract.precedenceSource.isNotEmpty)
                  Text(
                    '· conflicts resolve as ${contract.precedenceSource}',
                    style: CumulusText.meta,
                  ),
                if (contract.freshness.isNotEmpty)
                  Text('· ${contract.freshness}', style: CumulusText.meta),
                Text('· ${_custody(contract.custody)}',
                    style: CumulusText.meta),
                if (contract.collection.isNotEmpty)
                  Text('· governed by ${contract.collection}',
                      style: CumulusText.meta),
                Text('· ${_escalation(contract.escalation)}',
                    style: CumulusText.meta),
                if (contract.matchPolicy.isNotEmpty)
                  Text('· ${contract.matchPolicy}', style: CumulusText.mono),
              ],
            ),
          ],
          if (feed.artifactsTotal > 0) ...[
            const SizedBox(height: 6),
            Text(
              '${feed.artifactsTotal} artifact(s) kept · '
              '${feed.artifacts.map((a) => a.filename).take(3).join(", ")}'
              '${feed.artifactsTotal > 3 ? ' …' : ''}',
              style: CumulusText.meta,
            ),
          ],
          if (feed.lastError.isNotEmpty) ...[
            const SizedBox(height: 6),
            Text(
              feed.lastError,
              style: CumulusText.meta.copyWith(color: CumulusColors.danger),
            ),
          ],
          if (note != null) ...[
            const SizedBox(height: 6),
            Text(
              note!.message,
              style: CumulusText.meta.copyWith(
                color: note!.failed
                    ? CumulusColors.danger
                    : CumulusColors.inkSecondary,
                fontWeight: FontWeight.w600,
              ),
            ),
          ],
        ],
      ),
      trailing: [
        SizedBox(
          width: 110,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Text(
                _ago(feed.lastRunAtIso),
                style: const TextStyle(
                  fontSize: 13,
                  fontWeight: FontWeight.w600,
                  color: CumulusColors.ink,
                ),
              ),
              Text('last delivery', style: CumulusText.statCaption),
            ],
          ),
        ),
        if (pull != null)
          SizedBox(width: 176, child: _SyncControl(
            pull: pull,
            syncing: syncing,
            onSyncNow: onSyncNow,
          )),
      ],
    );
  }
}

/// Sync now, or the sentence saying why it is not there.
///
/// Never both, and never a disabled button on its own: a control that cannot
/// be pressed and does not say why is the same dead end as an error with a
/// Retry that will fail again. The tooltip states the consequence before the
/// press, because a pull writes — it delivers through the same door an
/// external system posts to, and the flow behind it runs.
class _SyncControl extends StatelessWidget {
  final PullState pull;
  final bool syncing;
  final VoidCallback? onSyncNow;

  const _SyncControl({
    required this.pull,
    required this.syncing,
    required this.onSyncNow,
  });

  @override
  Widget build(BuildContext context) {
    if (!pull.syncNowAllowed) {
      return Text(
        pull.syncNowRefusal,
        textAlign: TextAlign.right,
        style: CumulusText.meta,
      );
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.end,
      children: [
        Tooltip(
          message:
              'Asks ${pull.estateApp} for everything changed since this '
              'feed\'s watermark and delivers it through '
              'POST /api/v1/sources/deliver — which runs the flow and writes.',
          child: OutlinedButton.icon(
            onPressed: syncing ? null : onSyncNow,
            icon: syncing
                ? const SizedBox(
                    width: 14,
                    height: 14,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.sync, size: 15),
            label: Text(syncing ? 'Syncing…' : 'Sync now'),
          ),
        ),
        const SizedBox(height: 4),
        // A multi-resource feed has no single "since": each route is asked
        // from its own cursor, and naming the furthest of them promised a
        // narrower pull than the one the button starts.
        Text(
          syncing
              ? 'pulling ${pull.resources.length} route(s)'
              : !pull.everPulled
              ? 'first pull reads everything'
              : pull.watermarks.length > 1
              ? 'each route since its own cursor'
              : 'since ${_short(pull.watermark)}',
          textAlign: TextAlign.right,
          style: CumulusText.statCaption,
        ),
      ],
    );
  }
}

/// What a delivery carried, what it kept, and what became of the rows.
///
/// Two arrival counts because they are two facts: a bind feed lands every row
/// it is handed and a reify feed lands a fraction, so one number could not
/// mean the same thing twice. The dispositions print only where the feed's
/// contract reports them — a bind mapping has no resolve step and no payload
/// hash, and "0 unchanged (byte-identical redelivery)" over a feed that runs
/// no hash check was an answer to a question it never asks.
String _arrivals(SourceFeed feed) {
  final parts = [
    '${feed.deliveries} delivery(s)',
    '${feed.records} received',
    '${feed.recordsLanded} newly landed',
  ];
  final unchanged = feed.unchanged;
  final linked = feed.linked;
  final created = feed.created;
  if (unchanged == null && linked == null && created == null) {
    parts.add('disposition not reported by this mapping');
    return parts.join(' · ');
  }
  if (unchanged != null) {
    parts.add('$unchanged unchanged (byte-identical redelivery)');
  }
  if (linked != null) parts.add('$linked linked to an existing entity');
  if (created != null) parts.add('$created minted');
  return parts.join(' · ');
}

/// The materialization decision, said rather than spelled. `reify` and `bind`
/// are the vocabulary of the registry; what they mean is whether a row becomes
/// an object you can adjudicate.
String _strategy(SourceContract contract) => switch (contract.strategy) {
  'reify' => 'each row lands as '
      '${contract.landsAs.isEmpty ? "an object" : contract.landsAs}',
  'bind' => 'columns bind to types; no row is reified',
  _ => contract.strategy,
};

String _custody(String custody) => switch (custody) {
  'copy' => 'rows are copied into the map',
  'reference' => 'the map holds a pointer; the bytes stay in the source',
  _ => 'custody $custody',
};

String _escalation(String escalation) => switch (escalation) {
  'per-row task' => 'an unresolvable row raises a Task',
  'windowed aggregate' => 'unresolvable rows are counted on the run, not paged',
  _ => 'escalates $escalation',
};

/// A cursor short enough for a caption. The full value is on the row above it,
/// so this trims rather than reformats — nothing here should imply a precision
/// the watermark does not have. The zone is stated because trimming is exactly
/// what dropped it: `2026-08-30T19:10:53+00:00` became `2026-08-30 19:10`,
/// which a reader in Madison has every reason to read as local.
String _short(String iso) => iso.length > 16
    ? '${iso.substring(0, 16).replaceFirst('T', ' ')} UTC'
    : iso;

/// The full cursor, marked for what it is. Watermarks are compared as strings
/// against what the estate serves, so the value is never reformatted — only
/// labelled.
String _utc(String iso) =>
    iso.endsWith('+00:00') || iso.endsWith('Z') ? '$iso (UTC)' : iso;

/// `agoLabel` over an ISO string, tolerating the empty and the unparseable —
/// a feed that has never run has no timestamp, and that is a legible state.
String _ago(String iso) {
  if (iso.isEmpty) return 'never';
  final when = DateTime.tryParse(iso);
  return when == null ? '—' : agoLabel(when);
}

/// The forward half of [_ago], for a moment that has not arrived. A scheduled
/// next pull run through `agoLabel` read as "next just now" and then "next 3m
/// ago" — a future stated as a past.
String _until(String iso) {
  if (iso.isEmpty) return 'unscheduled';
  final when = DateTime.tryParse(iso);
  return when == null ? '—' : untilLabel(when);
}

IconData _iconFor(String kind) => switch (kind) {
  'vendors' => Icons.storefront_outlined,
  'purchase-orders' => Icons.receipt_outlined,
  'receiving-reports' => Icons.local_shipping_outlined,
  'calendars' => Icons.calendar_today_outlined,
  'workforce' => Icons.groups_outlined,
  _ => Icons.sync_alt,
};
