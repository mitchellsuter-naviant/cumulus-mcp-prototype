import 'package:flutter/material.dart';

import '../models/data_layer.dart';
import '../services/api_client.dart';
import '../theme/cumulus_theme.dart';
import 'cumulus_shell.dart';
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
                landed == 0 ? 'nothing landed yet' : '$landed records landed',
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
          const CumulusCard(child: CumulusLoading())
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
              if (estate.processes.isNotEmpty)
                MetaPill('${estate.processes.length} processes running'),
              // Absent and off are different facts, so an estate that reports
              // no pulse at all gets no pill rather than a false "stopped".
              if (estate.pulseEnabled != null)
                MetaPill(
                  estate.pulseEnabled!
                      ? 'pulse on${estate.pulseIntensity.isEmpty ? "" : " · ${estate.pulseIntensity}"}'
                      : 'pulse off',
                  tone: estate.pulseEnabled! ? CumulusTone.teal : null,
                ),
              MetaPill(
                estate.pullIntervalSeconds > 0
                    ? 'polling every ${estate.pullIntervalSeconds}s'
                    : 'pulled on demand — no loop of our own',
              ),
              MetaPill(
                estate.authenticated
                    ? 'credential configured'
                    : 'unauthenticated estate',
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
    final unchanged = row == null ? null : row.unchanged - before.unchanged;
    final waiting = row == null
        ? null
        : row.awaitingSteward - before.awaitingSteward;
    return SyncNote(
      message: [
        'Pulled ${outcome.recordsPulled} record(s) '
            '(${outcome.pulled.entries.map((e) => "${e.value} ${e.key}").join(" · ")})',
        if (outcome.chunks > 1) 'in ${outcome.chunks} deliveries',
        if (landed != null) '· $landed landed, $unchanged unchanged',
        if (waiting != null && waiting > 0) ', $waiting awaiting a steward',
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
                : '${feed.deliveries} delivery(s) · ${feed.recordsLanded} '
                      'landed · ${feed.unchanged} unchanged (byte-identical '
                      'redelivery) · ${feed.linked} linked to an existing '
                      'entity · ${feed.created} minted',
          ),
          if (!feed.neverDelivered) ...[
            const SizedBox(height: 6),
            Wrap(
              spacing: 12,
              runSpacing: 4,
              children: [
                Text(feed.flowId, style: CumulusText.mono),
                if (feed.watermark.isNotEmpty)
                  Text('· watermark ${feed.watermark}',
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
                if (pull.watermark.isNotEmpty)
                  Text('· cursor ${pull.watermark}', style: CumulusText.mono),
                if (pull.nextPullAt.isNotEmpty)
                  Text('· next ${_ago(pull.nextPullAt)}',
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
            Wrap(
              spacing: 12,
              runSpacing: 4,
              children: [
                Text(
                  'contract: ${contract.strategy}'
                  '${contract.landsAs.isEmpty ? '' : ' → ${contract.landsAs}'}',
                  style: CumulusText.meta,
                ),
                for (final type in contract.resolvesTo)
                  Text('· $type', style: CumulusText.mono),
                if (contract.precedenceSource.isNotEmpty)
                  Text(
                    '· claims write as ${contract.precedenceSource}',
                    style: CumulusText.meta,
                  ),
                if (contract.matchPolicy.isNotEmpty)
                  Text('· policy ${contract.matchPolicy}',
                      style: CumulusText.mono),
                Text('· custody ${contract.custody}', style: CumulusText.meta),
                if (contract.collection.isNotEmpty)
                  Text('· governed by ${contract.collection}',
                      style: CumulusText.meta),
                Text('· escalates ${contract.escalation}',
                    style: CumulusText.meta),
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
        Text(
          syncing
              ? 'pulling ${pull.resources.length} route(s)'
              : pull.everPulled
              ? 'since ${_short(pull.watermark)}'
              : 'first pull reads everything',
          textAlign: TextAlign.right,
          style: CumulusText.statCaption,
        ),
      ],
    );
  }
}

/// A cursor short enough for a caption. The full value is on the row above it,
/// so this trims rather than reformats — nothing here should imply a precision
/// the watermark does not have.
String _short(String iso) =>
    iso.length > 16 ? iso.substring(0, 16).replaceFirst('T', ' ') : iso;

/// `agoLabel` over an ISO string, tolerating the empty and the unparseable —
/// a feed that has never run has no timestamp, and that is a legible state.
String _ago(String iso) {
  if (iso.isEmpty) return 'never';
  final when = DateTime.tryParse(iso);
  return when == null ? '—' : agoLabel(when);
}

IconData _iconFor(String kind) => switch (kind) {
  'vendors' => Icons.storefront_outlined,
  'purchase-orders' => Icons.receipt_outlined,
  'receiving-reports' => Icons.local_shipping_outlined,
  'calendars' => Icons.calendar_today_outlined,
  'workforce' => Icons.groups_outlined,
  _ => Icons.sync_alt,
};
