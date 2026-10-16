import 'package:flutter/material.dart';

import '../models/data_layer.dart';
import '../services/api_client.dart';
import '../theme/cumulus_theme.dart';
import 'cumulus_shell.dart';
import 'skeleton.dart';
import 'ui_kit.dart';

/// Data: what we hold, and under what policy.
///
/// The Systems tab answers "who do we talk to"; this one answers "what do we
/// keep". The unit is the Collection, because that is where the governance
/// actually lives — retention, custody and authoritativeness are properties
/// of the *set* a row belongs to, never of the row — so a screen grouped any
/// other way would be grouped by nothing that governs anything.
///
/// Counts are honest. A Collection whose store can be counted answers a
/// number, possibly zero, which is a real answer; one whose store this
/// deployment cannot reach answers nothing, and the row says "not counted"
/// rather than wearing a reassuring 0. The same honesty holds for retention:
/// the horizon is declared and computed and the purge pass is not built, and
/// the screen says so rather than letting an empty expiring list read as
/// enforcement.
class DataPlane extends StatefulWidget {
  final ApiClient api;

  const DataPlane({super.key, required this.api});

  @override
  State<DataPlane> createState() => _DataPlaneState();
}

class _DataPlaneState extends State<DataPlane> {
  DataScreen? _screen;
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
      final screen = await widget.api.dataScreen();
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
    final collections = screen?.collections ?? const <DataCollection>[];
    // Role is the first cut because it is the deletion question: data is what
    // history is lost without, knowledge is what the agents get dumber
    // without. A reader deciding what a retention change touches starts here.
    final data = [for (final c in collections) if (c.role == 'data') c];
    final knowledge = [
      for (final c in collections)
        if (c.role != 'data') c,
    ];

    return PageBody(
      padding: const EdgeInsets.fromLTRB(28, 20, 28, 40),
      children: [
        PlaneHeader(
          summary:
              'What this deployment holds, and under what policy. Every '
              'Collection is declared in the map and counted from the store '
              'that actually holds its rows — retention, custody and '
              'authoritativeness are properties of the set, never the row. '
              'A store that cannot be counted honestly says so rather than '
              'answering zero.',
          pills: [
            if (screen != null) ...[
              MetaPill('${collections.length} collections'),
              MetaPill(
                '${screen.documents} stored artifact(s) · '
                '${_bytes(screen.storedBytes)}',
              ),
              if (screen.expiringTotal > 0)
                MetaPill(
                  '${screen.expiringTotal} past retention',
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
          // The plane header is already real; the two collection lists and the
          // retention card are what the read is for.
          const SkeletonPulse(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              spacing: 16,
              children: [
                SkeletonListCard(rows: 5),
                SkeletonListCard(rows: 4),
                SkeletonListCard(rows: 3, leadingCircle: false),
              ],
            ),
          )
        else if (screen != null) ...[
          ListCard(
            title: 'Data collections',
            subtitle: 'delete one and history is lost',
            children: [
              for (final collection in data)
                _CollectionRow(collection: collection),
            ],
          ),
          ListCard(
            title: 'Knowledge collections',
            subtitle: 'delete one and the agents get dumber',
            children: [
              for (final collection in knowledge)
                _CollectionRow(collection: collection),
            ],
          ),
          _RetentionCard(screen: screen),
        ],
      ],
    );
  }
}

/// One Collection: what it is, what governs it, how many rows it holds.
class _CollectionRow extends StatelessWidget {
  final DataCollection collection;

  const _CollectionRow({required this.collection});

  @override
  Widget build(BuildContext context) {
    return CumulusListRow(
      leading: Container(
        width: 34,
        height: 34,
        alignment: Alignment.center,
        decoration: BoxDecoration(
          color: CumulusTone.neutral.background,
          borderRadius: BorderRadius.circular(10),
        ),
        child: Icon(
          _iconFor(collection.kind),
          size: 17,
          color: CumulusTone.neutral.foreground,
        ),
      ),
      title: Row(
        children: [
          Flexible(child: Text(collection.name)),
          const SizedBox(width: 10),
          StatusBadge(collection.kind, dense: true, uppercase: true),
          if (collection.authoritative) ...[
            const SizedBox(width: 6),
            const Tooltip(
              message:
                  'This store is the system of record for what it holds — '
                  'nothing upstream can overrule it.',
              child: StatusBadge('authoritative', dense: true),
            ),
          ],
          if (collection.legalHold) ...[
            const SizedBox(width: 6),
            StatusBadge('legal hold', tone: CumulusTone.warn, dense: true),
          ],
        ],
      ),
      subtitle: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(collection.summary),
          const SizedBox(height: 6),
          Wrap(
            spacing: 12,
            runSpacing: 4,
            children: [
              Text(collection.container, style: CumulusText.meta),
              Text('· ${_custody(collection.custody)}',
                  style: CumulusText.meta),
              Text('· kept ${collection.retention}', style: CumulusText.meta),
              if (collection.origin.isNotEmpty)
                Text('· origin ${collection.origin}', style: CumulusText.meta),
              // The types this Collection claims — the same partition the
              // ontology diagram draws as its cluster frames. Empty on a
              // store-backed Collection with no graph objects, which is a
              // normal state and not a gap: a Collection can govern content
              // in a store, a subgraph, or both.
              if (collection.objectTypes.isNotEmpty)
                Text(
                  '· governs ${collection.objectTypes.join(", ")}',
                  style: CumulusText.mono,
                ),
            ],
          ),
          if (collection.retentionNote.isNotEmpty) ...[
            const SizedBox(height: 4),
            Text(collection.retentionNote, style: CumulusText.meta),
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
                // Null and zero are different facts, drawn differently: zero
                // is a counted answer, null is a store this deployment could
                // not count — and printing 0 for it would be the lie the
                // payload was shaped to avoid.
                collection.rows == null ? '—' : '${collection.rows}',
                style: const TextStyle(
                  fontSize: 13,
                  fontWeight: FontWeight.w600,
                  color: CumulusColors.ink,
                ),
              ),
              Text(
                collection.rows == null
                    ? 'not counted'
                    : collection.rowsCaption.isEmpty
                    ? 'rows'
                    : collection.rowsCaption,
                style: CumulusText.statCaption,
              ),
            ],
          ),
        ),
      ],
    );
  }
}

/// The retention policy read back against what is actually held — and the
/// honest sentence that no purge pass enforces it yet.
class _RetentionCard extends StatelessWidget {
  final DataScreen screen;

  const _RetentionCard({required this.screen});

  @override
  Widget build(BuildContext context) {
    final expiring = screen.expiring;
    return CumulusCard(
      borderColor: expiring.isNotEmpty ? CumulusColors.danger : null,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SectionHeader(
            title: 'Retention',
            subtitle: screen.purgeBuilt
                ? 'horizons are enforced by the purge pass'
                : 'declared and computed — the purge pass is not built, so '
                      'nothing is deleted yet',
          ),
          const SizedBox(height: 10),
          if (expiring.isEmpty)
            Text(
              'No stored artifact is past its Collection\'s retention '
              'horizon. Each document is stamped with its horizon at write '
              'time, from the Collection it lands under.',
              style: CumulusText.body,
            )
          else ...[
            Text(
              '${screen.expiringTotal} artifact(s) are past their retention '
              'horizon and still held. Reported rather than swept: saying so '
              'is better than implying enforcement that does not exist.',
              style: CumulusText.body,
            ),
            const SizedBox(height: 8),
            for (final doc in expiring)
              Padding(
                padding: const EdgeInsets.only(bottom: 4),
                child: Wrap(
                  spacing: 12,
                  runSpacing: 4,
                  children: [
                    Text(doc.filename, style: CumulusText.mono),
                    Text('· ${doc.kind}', style: CumulusText.meta),
                    Text(
                      '· horizon passed ${doc.retentionUntilIso}',
                      style: CumulusText.meta.copyWith(
                        color: CumulusColors.danger,
                      ),
                    ),
                  ],
                ),
              ),
            if (screen.expiringTotal > expiring.length)
              Text(
                '… and ${screen.expiringTotal - expiring.length} more.',
                style: CumulusText.meta,
              ),
          ],
        ],
      ),
    );
  }
}

String _custody(String custody) => switch (custody) {
  'copy' => 'copied into the map',
  'reference' => 'referenced in place',
  _ => 'custody $custody',
};

String _bytes(int bytes) {
  if (bytes < 1024) return '$bytes B';
  if (bytes < 1024 * 1024) return '${(bytes / 1024).toStringAsFixed(1)} KB';
  return '${(bytes / (1024 * 1024)).toStringAsFixed(1)} MB';
}

IconData _iconFor(String kind) => switch (kind) {
  'graph' => Icons.hub_outlined,
  'document' => Icons.description_outlined,
  'tabular' => Icons.table_chart_outlined,
  'ledger' => Icons.receipt_long_outlined,
  'derived' => Icons.auto_graph_outlined,
  _ => Icons.storage_outlined,
};
