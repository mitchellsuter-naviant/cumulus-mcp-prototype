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
              'contract it lands under. Assembled from the sync runs '
              'themselves rather than a second bookkeeping store, so a feed '
              'with no run has never delivered and this cannot pretend '
              'otherwise.',
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
                title: 'Nothing has been delivered yet',
                message:
                    'A feed appears here the first time it pushes to '
                    'POST /api/v1/sources/deliver.',
              ),
            )
          else
            ListCard(
              title: 'Inbound feeds',
              subtitle:
                  'one row per source and kind — how it ran, what it '
                  'promised, and what it left behind',
              children: [for (final feed in feeds) _FeedRow(feed: feed)],
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

/// One feed: health on the left, contract underneath, last delivery on the
/// right. The contract is not optional decoration — its absence is the row's
/// most important finding, so a feed under no Mapping says so in red.
class _FeedRow extends StatelessWidget {
  final SourceFeed feed;

  const _FeedRow({required this.feed});

  @override
  Widget build(BuildContext context) {
    final contract = feed.contract;
    final tone = feed.failed
        ? CumulusTone.danger
        : feed.awaitingSteward > 0
        ? CumulusTone.warn
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
          const SizedBox(width: 10),
          StatusBadge(feed.kind, dense: true, uppercase: true),
          const SizedBox(width: 6),
          StatusBadge(feed.lastStatus, tone: tone, dense: true),
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
            '${feed.deliveries} delivery(s) · ${feed.recordsLanded} landed · '
            '${feed.unchanged} unchanged (byte-identical redelivery) · '
            '${feed.linked} linked to an existing entity · ${feed.created} '
            'minted',
          ),
          const SizedBox(height: 6),
          Wrap(
            spacing: 12,
            runSpacing: 4,
            children: [
              Text(feed.flowId, style: CumulusText.mono),
              if (feed.watermark.isNotEmpty)
                Text('· watermark ${feed.watermark}', style: CumulusText.meta),
              if (feed.lastRunId.isNotEmpty)
                Text('· ${feed.lastRunId}', style: CumulusText.mono),
            ],
          ),
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
      ],
    );
  }
}

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
