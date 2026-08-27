import 'package:flutter/material.dart';

import '../models/asset.dart';
import '../models/data_layer.dart';
import '../models/graph.dart';
import '../models/mapping.dart';
import '../services/api_client.dart';
import '../theme/cumulus_theme.dart';
import 'cumulus_shell.dart';
import 'ui_kit.dart';

/// The Knowledge layer's landing screen: every number in one place.
///
/// The planes each answer a question — what people wrote, what we encoded,
/// what the system learned, what arrived from outside — and none of them is
/// the right place to report on the layer as a whole. Metrics that used to
/// sit at the top of the Library and Ontology screens live here instead, so
/// those screens are about their subject and this one is about the state of
/// the layer.
class KnowledgeOverview extends StatefulWidget {
  final ApiClient api;

  /// Jumps to a plane by its tab index — the cards at the bottom are the
  /// navigational half of an overview.
  final ValueChanged<int> onOpenPlane;

  const KnowledgeOverview({
    super.key,
    required this.api,
    required this.onOpenPlane,
  });

  @override
  State<KnowledgeOverview> createState() => _KnowledgeOverviewState();
}

class _KnowledgeOverviewState extends State<KnowledgeOverview> {
  List<AssetSummary>? _assets;
  Map<String, AssetUsage> _usage = const {};
  Ontology? _ontology;
  Map<String, dynamic> _graphStats = const {};
  InvariantReport _invariants = InvariantReport.unknown;
  List<DataCollection>? _collections;
  MappingRegistry? _mappings;
  List<SourceStatus>? _sources;

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
    // Independent requests, each swallowing its own failure: an overview is a
    // survey, so one unavailable number must not blank the page.
    Future<T?> attempt<T>(Future<T> call) =>
        call.then<T?>((v) => v).catchError((_) => null);
    final results = await Future.wait([
      attempt(widget.api.listAssets()),
      attempt(widget.api.corpusUsage()),
      attempt(widget.api.ontology()),
      attempt(widget.api.graphStats()),
      attempt(widget.api.graphInvariants()),
      attempt(widget.api.listCollections()),
      attempt(widget.api.listMappings()),
      attempt(widget.api.listSources()),
    ]);
    if (!mounted) return;
    setState(() {
      _assets = results[0] as List<AssetSummary>?;
      _usage = (results[1] as Map<String, AssetUsage>?) ?? const {};
      _ontology = results[2] as Ontology?;
      _graphStats = (results[3] as Map<String, dynamic>?) ?? const {};
      _invariants = (results[4] as InvariantReport?) ?? InvariantReport.unknown;
      _collections = results[5] as List<DataCollection>?;
      _mappings = results[6] as MappingRegistry?;
      _sources = results[7] as List<SourceStatus>?;
      _loading = false;
      if (_assets == null && _ontology == null) {
        _error = 'The knowledge layer did not answer.';
      }
    });
  }

  static String _n(Object? value) => value == null ? '—' : '$value';

  @override
  Widget build(BuildContext context) {
    return PageBody(
      padding: const EdgeInsets.fromLTRB(28, 20, 28, 40),
      children: [
        PlaneHeader(
          summary:
              'Four planes, cut by origin: what people wrote, what we '
              'encoded, what the system learned, and what arrived from '
              'outside — with the numbers that describe the layer as a whole.',
          trailing: OutlinedButton.icon(
            onPressed: _loading ? null : _load,
            icon: const Icon(Icons.refresh, size: 16),
            label: const Text('Refresh'),
          ),
        ),
        if (_error != null) ErrorNotice(message: _error!, onRetry: _load),
        if (_loading && _assets == null)
          const CumulusCard(child: CumulusLoading())
        else ...[
          const _StripLabel(
            'The corpus',
            'documents, exemplars and generated exports — and whether anyone '
                'reads them',
          ),
          StatStrip(tiles: _corpusTiles()),
          const _StripLabel(
            'The model',
            'the schema, what exists against it, and whether it conforms',
          ),
          StatStrip(tiles: _modelTiles()),
          const _StripLabel('The planes', 'where each of these lives'),
          _planeCards(),
        ],
      ],
    );
  }

  // -- the corpus (was the Library's strip) -------------------------------------

  List<StatTile> _corpusTiles() {
    final assets = _assets ?? const <AssetSummary>[];
    int countWhere(bool Function(AssetSummary) test) =>
        assets.where(test).length;
    final documents = countWhere((a) => a.kind == 'document');
    final exemplars = countWhere((a) => a.kind == 'exemplar');
    final generated = countWhere((a) => a.kind == 'schema-export');
    final drafts = countWhere((a) => a.status == 'draft');
    final deprecated = countWhere((a) => a.status == 'deprecated');
    final governing = countWhere(
      (a) => a.authorityLevel == 'policy' || a.authorityLevel == 'standard',
    );
    final freshest = assets.isEmpty
        ? null
        : assets
            .map((a) => a.updatedAt)
            .reduce((a, b) => a.isAfter(b) ? a : b);
    // An asset with no usage row at all counts as unused: the store only
    // holds documents that saw traffic, so absence is the signal.
    final used = assets.where((a) => !(_usage[a.id]?.unused ?? true)).length;
    final ignored =
        assets.where((a) => _usage[a.id]?.ignored ?? false).length;
    final undiscovered = assets.length - used - ignored;
    final reads = _usage.values.fold(0, (n, u) => n + u.reads);
    final citations = _usage.values.fold(0, (n, u) => n + u.citations);

    return [
      StatTile(
        label: 'Assets',
        value: '${assets.length}',
        caption: '$documents authored · $exemplars learned · $generated '
            'generated',
      ),
      StatTile(
        label: 'Governing',
        value: '$governing',
        caption: 'policy + standard',
        valueColor: CumulusColors.accent,
      ),
      StatTile(
        label: 'Drafts',
        value: '$drafts',
        caption: drafts == 0 ? 'nothing pending' : 'awaiting publication',
        valueColor: drafts == 0 ? null : CumulusColors.warn,
      ),
      StatTile(
        label: 'Deprecated',
        value: '$deprecated',
        caption: 'still retrievable, not authoritative',
        valueColor: deprecated == 0 ? null : CumulusColors.danger,
      ),
      StatTile(
        label: 'Last change',
        value: freshest == null ? '—' : agoLabel(freshest),
        caption: 'across the corpus',
      ),
      StatTile(
        label: 'Used (30d)',
        value: '$used',
        caption: used == 0
            ? 'no reads or citations yet'
            : '$reads read · $citations cited',
        valueColor: used == 0 ? null : CumulusColors.success,
      ),
      StatTile(
        label: 'Ignored',
        value: _usage.isEmpty ? '—' : '$ignored',
        caption: ignored == 0
            ? 'nothing surfaces and gets passed over'
            : 'found in search, never opened',
        valueColor: ignored == 0 ? null : CumulusColors.warn,
      ),
      StatTile(
        label: 'Undiscovered',
        value: _usage.isEmpty ? '—' : '$undiscovered',
        caption: undiscovered == 0
            ? 'every document surfaces'
            : 'search never returns these',
        valueColor: undiscovered == 0 ? null : CumulusColors.danger,
      ),
    ];
  }

  // -- the model (was the Ontology's strip) -------------------------------------

  List<StatTile> _modelTiles() {
    final collections = _collections ?? const <DataCollection>[];
    final knowledge = collections.where((c) => c.isKnowledge).length;
    final data = collections.length - knowledge;
    final types = _ontology?.objects ?? const <OntologyObjectType>[];
    final cores = types.where((t) => !t.isPart).length;
    final violations = _invariants.violations.length;
    final checked = _invariants.objectsChecked > 0;

    return [
      StatTile(
        label: 'Entity types',
        value: '${types.isEmpty ? '—' : types.length}',
        caption: '$cores core · ${_ontology?.links.length ?? '—'} link types',
      ),
      StatTile(
        label: 'Entities',
        value: _n(_graphStats['objects']),
        caption: '${_n(_graphStats['links'])} typed links',
      ),
      StatTile(
        label: 'Conformance',
        value: !checked
            ? '—'
            : violations == 0
                ? 'clean'
                : '$violations',
        caption: violations == 0
            ? 'graph conforms · bad writes refused'
            : 'invariant violation(s) — a bug, not a warning',
        valueColor: violations == 0 ? null : CumulusColors.danger,
      ),
      StatTile(
        label: 'Conflicted',
        value: _n(_graphStats['conflicted_objects']),
        caption: 'sources disagree, both claims kept',
      ),
      StatTile(
        label: 'Collections',
        value: '${collections.length}',
        caption: '$knowledge knowledge · $data data',
      ),
      StatTile(
        label: 'Mappings',
        value: '${_mappings?.mappings.length ?? '—'}',
        caption: (_mappings?.problems.isEmpty ?? true)
            ? 'all conform'
            : '${_mappings!.problems.length} problem(s)',
      ),
    ];
  }

  // -- the planes ----------------------------------------------------------------

  Widget _planeCards() {
    final assets = _assets ?? const <AssetSummary>[];
    final sources = _sources ?? const <SourceStatus>[];
    final exemplars = assets.where((a) => a.kind == 'exemplar').length;
    final landed = sources.fold<int>(0, (s, r) => s + r.recordsLanded);
    final stewardWaits = sources.fold<int>(0, (s, r) => s + r.awaitingSteward);
    final cards = <Widget>[
      _PlaneCard(
        icon: Icons.menu_book_outlined,
        title: 'Library',
        origin: 'authored',
        summary:
            'Policies, standards and guidelines — the documents that argue '
            'and carry authority.',
        detail: '${assets.where((a) => a.kind == 'document').length} documents',
        onTap: () => widget.onOpenPlane(1),
      ),
      _PlaneCard(
        icon: Icons.hub_outlined,
        title: 'Ontology',
        origin: 'encoded',
        summary:
            'The schema, what exists against it, the Collections governance '
            'hangs off, and the Mappings that bind sources in.',
        detail:
            '${(_ontology?.objects ?? const []).where((t) => !t.isPart).length}'
            ' core entities',
        onTap: () => widget.onOpenPlane(2),
      ),
      _PlaneCard(
        icon: Icons.auto_awesome_outlined,
        title: 'Learned',
        origin: 'flywheel',
        summary:
            'Exemplars the flywheel promoted, derived profiles, and the eval '
            'datasets built from real runs.',
        detail: '$exemplars exemplars promoted',
        onTap: () => widget.onOpenPlane(3),
      ),
      _PlaneCard(
        icon: Icons.cloud_download_outlined,
        title: 'Sources',
        origin: 'synced',
        summary:
            'Each inbound feed against its declared contract, and the '
            'artifacts the flows keep as evidence.',
        detail: sources.isEmpty
            ? 'nothing delivered yet'
            : '${sources.length} feeds · $landed landed'
                '${stewardWaits > 0 ? ' · $stewardWaits awaiting a steward' : ''}',
        tone: stewardWaits > 0 ? CumulusTone.warn : CumulusTone.neutral,
        onTap: () => widget.onOpenPlane(4),
      ),
    ];
    return LayoutBuilder(
      builder: (context, constraints) {
        final perRow = constraints.maxWidth < 900 ? 2 : 4;
        final rows = <List<Widget>>[];
        for (var i = 0; i < cards.length; i += perRow) {
          rows.add(cards.sublist(i, (i + perRow).clamp(0, cards.length)));
        }
        return Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          spacing: 14,
          children: [
            for (final row in rows)
              IntrinsicHeight(
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    for (final (i, card) in row.indexed) ...[
                      if (i > 0) const SizedBox(width: 14),
                      Expanded(child: card),
                    ],
                  ],
                ),
              ),
          ],
        );
      },
    );
  }
}

/// A label above a metric strip — the strips answer different questions, and
/// an unlabelled wall of tiles makes the reader work that out.
class _StripLabel extends StatelessWidget {
  final String title;
  final String note;

  const _StripLabel(this.title, this.note);

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: 6, bottom: 2),
      child: Wrap(
        crossAxisAlignment: WrapCrossAlignment.center,
        spacing: 10,
        children: [
          Text(title.toUpperCase(), style: CumulusText.sectionTitle),
          Text(note, style: CumulusText.meta),
        ],
      ),
    );
  }
}

class _PlaneCard extends StatelessWidget {
  final IconData icon;
  final String title;
  final String origin;
  final String summary;
  final String detail;
  final CumulusTone tone;
  final VoidCallback onTap;

  const _PlaneCard({
    required this.icon,
    required this.title,
    required this.origin,
    required this.summary,
    required this.detail,
    this.tone = CumulusTone.neutral,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return CumulusCard(
      onTap: onTap,
      padding: const EdgeInsets.fromLTRB(18, 16, 18, 16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(icon, size: 18, color: CumulusColors.inkSecondary),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  title,
                  style: const TextStyle(
                    fontSize: 15,
                    fontWeight: FontWeight.w600,
                    color: CumulusColors.ink,
                  ),
                ),
              ),
              StatusBadge(origin, dense: true),
            ],
          ),
          const SizedBox(height: 8),
          Text(summary, style: CumulusText.meta),
          const SizedBox(height: 10),
          StatusBadge(detail, tone: tone, dense: true),
        ],
      ),
    );
  }
}
