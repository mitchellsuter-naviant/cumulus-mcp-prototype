import 'package:flutter/material.dart';

import '../models/data_layer.dart';
import '../models/graph.dart';
import '../models/mapping.dart';
import '../services/api_client.dart';
import '../theme/cumulus_theme.dart';
import 'cumulus_shell.dart';
import 'abox_explorer.dart';
import 'entity_explorer.dart';
import 'ui_kit.dart';

/// The Data plane of the Knowledge layer: what the map holds, and where it
/// came from.
///
/// Four lenses, and the split between them is the argument:
///
/// - **Collections** — the unit governance hangs off. Retention, custody and
///   authoritativeness are properties of a *set*, not of a row, so they are
///   declared here and stamped on write. Read from `/api/v1/collections`,
///   which is generated from the same Python declaration as the exported
///   `data-collections` document.
/// - **Sources** — what each external feed has delivered, assembled from the
///   sync runs themselves rather than a second bookkeeping store. A feed with
///   no run has never delivered, and this lens cannot pretend otherwise.
/// - **Model** — the TBox: the ontology as declared, with live instance counts
///   beside each type. A declared type with zero instances is a promise
///   nothing has kept yet, which is worth being able to see.
/// - **Instances** — the ABox, and its own screen rather than a corner of the
///   model's. Live instances and the fragments *authored in Python* (every
///   eval fixture, the demo seed's feed deliveries) are peers here: same
///   renderer, same model, differing only in whether they were observed or
///   declared. An authored fragment is shown by running it into a throwaway
///   world, so what you see is the consequence — which vendor the sync
///   resolved to, what it escalated — rather than the payload.
/// - **Documents** — the artifacts, their extracted regions, and what is past
///   its retention horizon.
///
/// Business views onto these rows are Workspaces. A row here hands off to one
/// rather than embedding it.
/// Which face of the layer this instance shows. The split is by *origin* —
/// the doctrine's encoded-vs-synced axis — not by storage: one backend, two
/// questions.
///
/// [ontology] is what we encoded: the TBox, the ABox, the Collection
/// declarations governance hangs off, and the Mappings that bind sources in.
/// [sources] is what arrives from outside: the feeds against their contracts,
/// and the artifact store.
enum DataPlaneMode { ontology, sources }

class DataPlaneView extends StatefulWidget {
  final ApiClient api;
  final DataPlaneMode mode;
  final VoidCallback onOpenExpenseWorkspace;
  final VoidCallback onOpenPayablesWorkspace;
  final VoidCallback onOpenFlows;
  final VoidCallback onOpenLibrary;

  const DataPlaneView({
    super.key,
    required this.api,
    required this.mode,
    required this.onOpenExpenseWorkspace,
    required this.onOpenPayablesWorkspace,
    required this.onOpenFlows,
    required this.onOpenLibrary,
  });

  @override
  State<DataPlaneView> createState() => _DataPlaneViewState();
}

enum _Lens { entities, fragments, collections, mappings, sources, documents }

class _DataPlaneViewState extends State<DataPlaneView> {
  late _Lens _lens = _lenses.first;

  List<_Lens> get _lenses => switch (widget.mode) {
    DataPlaneMode.ontology => const [
      _Lens.entities,
      _Lens.fragments,
      _Lens.collections,
      _Lens.mappings,
    ],
    DataPlaneMode.sources => const [_Lens.sources, _Lens.documents],
  };

  List<DataCollection>? _collections;
  MappingRegistry? _mappings;
  List<SourceStatus>? _sources;
  Ontology? _ontology;
  InvariantReport _invariants = InvariantReport.unknown;
  Map<String, dynamic> _graphStats = const {};
  List<StoredDocument>? _documents;
  Map<String, dynamic> _documentStats = const {};
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
    // Independent requests, each swallowing its own failure: the plane is a
    // survey, so one missing lens must not blank the page.
    Future<T?> attempt<T>(Future<T> call) =>
        call.then<T?>((v) => v).catchError((_) => null);
    // Each mode fetches only what its lenses read — the two instances sit in
    // one IndexedStack, so an indiscriminate load would run everything twice.
    if (widget.mode == DataPlaneMode.ontology) {
      // graphStats went with the metrics strip: the per-type instance
      // counts the explorer shows ride along on the ontology payload.
      final results = await Future.wait([
        attempt(widget.api.listCollections()),
        attempt(widget.api.ontology()),
        attempt(widget.api.graphInvariants()),
        attempt(widget.api.listMappings()),
      ]);
      if (!mounted) return;
      setState(() {
        _collections = results[0] as List<DataCollection>?;
        _ontology = results[1] as Ontology?;
        _invariants =
            (results[2] as InvariantReport?) ?? InvariantReport.unknown;
        _mappings = results[3] as MappingRegistry?;
        _loading = false;
        if (_collections == null && _ontology == null) {
          _error = 'The data layer did not answer.';
        }
      });
      return;
    }
    final results = await Future.wait([
      attempt(widget.api.listSources()),
      attempt(widget.api.listDocuments()),
      attempt(widget.api.documentStats()),
      attempt(widget.api.graphStats()),
      attempt(widget.api.listMappings()),
    ]);
    if (!mounted) return;
    setState(() {
      _sources = results[0] as List<SourceStatus>?;
      _documents = results[1] as List<StoredDocument>?;
      _documentStats = (results[2] as Map<String, dynamic>?) ?? const {};
      _graphStats = (results[3] as Map<String, dynamic>?) ?? const {};
      _mappings = results[4] as MappingRegistry?;
      _loading = false;
      if (_sources == null && _documents == null) {
        _error = 'The data layer did not answer.';
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    final collections = _collections ?? const <DataCollection>[];
    final sources = _sources ?? const <SourceStatus>[];

    final ontologyMode = widget.mode == DataPlaneMode.ontology;
    // The Entities & Links lens is a two-pane workbench, not a document: it
    // fills the height and its panes scroll themselves, so the schema stays
    // on screen while you read down a long link table. Every other lens is a
    // list that grows, and scrolls as a page.
    final fillsHeight = ontologyMode &&
        (_lens == _Lens.entities || _lens == _Lens.fragments);

    final header = <Widget>[
      PlaneHeader(
        summary: ontologyMode
            ? 'The schema (TBox) and what it declares: core entities, the '
                'links between them, the Collection declarations governance '
                'hangs off, and the Mappings that bind external systems in.'
            : 'What arrives from outside: each inbound feed against its '
                'declared contract, and the artifacts the flows keep as '
                'evidence.',
        pills: [
          if (ontologyMode && _invariants.objectsChecked > 0)
            // The metrics moved to Overview, but the universal test tier
            // still belongs where the model is read: a violation is a bug,
            // and finding out on another screen is too late.
            MetaPill(
              _invariants.violations.isEmpty
                  ? 'conforms'
                  : '${_invariants.violations.length} violation(s)',
              tone: _invariants.violations.isEmpty
                  ? CumulusTone.success
                  : CumulusTone.danger,
            ),
        ],
        trailing: OutlinedButton.icon(
          onPressed: _loading ? null : _load,
          icon: const Icon(Icons.refresh, size: 16),
          label: const Text('Refresh'),
        ),
      ),
      if (_error != null) ErrorNotice(message: _error!, onRetry: _load),
      if (!ontologyMode)
        StatStrip(
          tiles: [
            StatTile(
              label: 'Sources',
              value: '${sources.length}',
              caption: sources.isEmpty
                  ? 'nothing synced yet'
                  : '${sources.fold<int>(0, (s, r) => s + r.recordsLanded)} '
                      'records landed',
            ),
            StatTile(
              label: 'Mappings',
              value: '${_mappings?.mappings.length ?? '—'}',
              caption: 'declared contracts',
            ),
            StatTile(
              label: 'Artifacts',
              value: _n(_documentStats['documents']),
              caption: '${_n(_documentStats['expiring'])} past retention',
            ),
            StatTile(
              label: 'Conflicted',
              value: _n(_graphStats['conflicted_objects']),
              caption: 'sources disagree, both kept',
            ),
          ],
        ),
      Row(
        children: [
          for (final lens in _lenses) ...[
            LensChip(
              label: _label(lens),
              selected: _lens == lens,
              onTap: () => setState(() => _lens = lens),
            ),
            const SizedBox(width: 8),
          ],
        ],
      ),
    ];

    if (fillsHeight) {
      return Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          PageGutter(
            padding: const EdgeInsets.fromLTRB(28, 20, 28, 0),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              spacing: 16,
              children: header,
            ),
          ),
          const SizedBox(height: 16),
          Expanded(
            child: PageGutter(
              padding: const EdgeInsets.fromLTRB(28, 0, 28, 28),
              child: _loading && _ontology == null
                  ? const CumulusCard(child: CumulusLoading())
                  : _lens == _Lens.entities
                      ? TBoxExplorer(
                          ontology: _ontology,
                          collections: _collections ?? const [],
                        )
                      : ABoxExplorer(
                          api: widget.api,
                          ontology: _ontology,
                          collections: _collections ?? const [],
                        ),
            ),
          ),
        ],
      );
    }

    return PageBody(
      padding: const EdgeInsets.fromLTRB(28, 20, 28, 40),
      children: [
        ...header,
        if (_loading && _collections == null && _sources == null)
          const CumulusLoading()
        else
          switch (_lens) {
            _Lens.collections => _CollectionsLens(
              collections: collections,
              onOpenLibrary: widget.onOpenLibrary,
              onOpenFlows: widget.onOpenFlows,
              onOpenExpense: widget.onOpenExpenseWorkspace,
              onOpenPayables: widget.onOpenPayablesWorkspace,
            ),
            _Lens.sources => _SourcesLens(
              sources: sources,
              mappings: _mappings?.mappings ?? const [],
            ),
            _Lens.mappings => _MappingsLens(registry: _mappings),
            // Handled by the fill-height branch above, which is the only way
            // its panes can scroll independently. Unreachable, and the switch
            // must still be exhaustive.
            _Lens.entities => const SizedBox.shrink(),
            // Handled by the fill-height branch above.
            _Lens.fragments => const SizedBox.shrink(),
            _Lens.documents => _DocumentsLens(
              documents: _documents ?? const [],
              stats: _documentStats,
            ),
          },
      ],
    );
  }

  static String _label(_Lens lens) => switch (lens) {
    _Lens.entities => 'ENTITIES & LINKS',
    _Lens.fragments => 'INSTANCES',
    _Lens.collections => 'COLLECTIONS',
    _Lens.mappings => 'MAPPINGS',
    _Lens.sources => 'FEEDS',
    _Lens.documents => 'DOCUMENTS',
  };

  static String _n(Object? value) => value == null ? '—' : '$value';
}

// ---------------------------------------------------------------------------
// Collections
// ---------------------------------------------------------------------------

class _CollectionsLens extends StatelessWidget {
  final List<DataCollection> collections;
  final VoidCallback onOpenLibrary;
  final VoidCallback onOpenFlows;
  final VoidCallback onOpenExpense;
  final VoidCallback onOpenPayables;

  const _CollectionsLens({
    required this.collections,
    required this.onOpenLibrary,
    required this.onOpenFlows,
    required this.onOpenExpense,
    required this.onOpenPayables,
  });

  VoidCallback? _handoff(String id) => switch (id) {
    'knowledge-library' => onOpenLibrary,
    'flow-definitions' => onOpenFlows,
    'expense-records' => onOpenExpense,
    'ap-records' => onOpenPayables,
    'audit-documents' => null,
    _ => null,
  };

  String? _handoffLabel(String id) => switch (id) {
    'knowledge-library' => 'Library',
    'flow-definitions' => 'Flows',
    'expense-records' => 'Workspace',
    'ap-records' => 'Workspace',
    _ => null,
  };

  @override
  Widget build(BuildContext context) {
    final groups = <String, List<DataCollection>>{};
    for (final collection in collections) {
      groups.putIfAbsent(collection.isKnowledge ? 'knowledge' : 'data', () => [])
          .add(collection);
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      spacing: 16,
      children: [
        if (groups['knowledge'] != null)
          ListCard(
            title: 'Governed knowledge',
            subtitle:
                'the declarative middle — editable in the shell, versioned, '
                'read at run time',
            children: [
              for (final c in groups['knowledge']!)
                _CollectionRow(
                  collection: c,
                  onOpen: _handoff(c.id),
                  openLabel: _handoffLabel(c.id),
                ),
            ],
          ),
        if (groups['data'] != null)
          ListCard(
            title: 'Data',
            subtitle:
                'the record of what happened, and the entities it happened to',
            children: [
              for (final c in groups['data']!)
                _CollectionRow(
                  collection: c,
                  onOpen: _handoff(c.id),
                  openLabel: _handoffLabel(c.id),
                ),
            ],
          ),
      ],
    );
  }
}

class _CollectionRow extends StatelessWidget {
  final DataCollection collection;
  final VoidCallback? onOpen;
  final String? openLabel;

  const _CollectionRow({
    required this.collection,
    this.onOpen,
    this.openLabel,
  });

  @override
  Widget build(BuildContext context) {
    final tone =
        collection.isKnowledge ? CumulusTone.accent : CumulusTone.teal;
    final retention = collection.retention == 'indefinite'
        ? 'kept indefinitely'
        : 'retention ${collection.retention}';
    return CumulusListRow(
      onTap: onOpen,
      leading: Container(
        width: 34,
        height: 34,
        alignment: Alignment.center,
        decoration: BoxDecoration(
          color: tone.background,
          borderRadius: BorderRadius.circular(10),
        ),
        child: Icon(_iconForKind(collection.kind), size: 17,
            color: tone.foreground),
      ),
      title: Row(
        children: [
          Flexible(child: Text(collection.name)),
          const SizedBox(width: 10),
          StatusBadge(collection.role, tone: tone, dense: true, uppercase: true),
          const SizedBox(width: 6),
          StatusBadge(collection.kind, dense: true),
          if (collection.authoritative) ...[
            const SizedBox(width: 6),
            const Tooltip(
              message:
                  'System of record for its subject — a flow may read it as '
                  'canonical.',
              child: StatusBadge(
                'authoritative',
                tone: CumulusTone.violet,
                dense: true,
              ),
            ),
          ],
          if (collection.legalHold) ...[
            const SizedBox(width: 6),
            const StatusBadge('legal hold', tone: CumulusTone.warn, dense: true),
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
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              Text(collection.container, style: CumulusText.mono),
              Text('· ${collection.origin}', style: CumulusText.meta),
              Text('· custody ${collection.custody}', style: CumulusText.meta),
              Tooltip(
                message: collection.retentionNote.isEmpty
                    ? 'Stamped on every row when it is written.'
                    : collection.retentionNote,
                child: Text('· $retention', style: CumulusText.meta),
              ),
              if (collection.writtenBy.isNotEmpty)
                Text('· written by ${collection.writtenBy.join(", ")}',
                    style: CumulusText.meta),
            ],
          ),
        ],
      ),
      trailing: [
        SizedBox(
          width: 96,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Text(
                collection.rows?.toString() ?? '—',
                style: const TextStyle(
                  fontSize: 17,
                  fontWeight: FontWeight.w600,
                  color: CumulusColors.ink,
                ),
              ),
              Text(
                collection.rows == null
                    ? 'not counted here'
                    : collection.rowsCaption,
                style: CumulusText.statCaption,
                textAlign: TextAlign.end,
              ),
            ],
          ),
        ),
        SizedBox(
          width: 128,
          child: onOpen == null
              ? null
              : Align(
                  alignment: Alignment.centerRight,
                  child: RowAction(label: openLabel ?? 'Open', onTap: onOpen!),
                ),
        ),
      ],
    );
  }
}

IconData _iconForKind(String kind) => switch (kind) {
  'documents' => Icons.description_outlined,
  'definitions' => Icons.account_tree_outlined,
  'eval datasets' => Icons.science_outlined,
  'entities' => Icons.hub_outlined,
  'landing' => Icons.inbox_outlined,
  'table' => Icons.table_rows_outlined,
  'work items' => Icons.checklist_outlined,
  'ledger' => Icons.receipt_long_outlined,
  'derived' => Icons.scatter_plot_outlined,
  _ => Icons.dataset_outlined,
};

// ---------------------------------------------------------------------------
// Sources
// ---------------------------------------------------------------------------

class _SourcesLens extends StatelessWidget {
  final List<SourceStatus> sources;

  /// Declared contracts, joined onto the rows below so a feed is always shown
  /// against what it *promised*, not just what it delivered.
  final List<SourceMapping> mappings;

  const _SourcesLens({required this.sources, required this.mappings});

  SourceMapping? _contract(SourceStatus source) {
    for (final m in mappings) {
      if (m.source == source.source) return m;
    }
    return null;
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      spacing: 16,
      children: [
        if (sources.isEmpty)
          CumulusCard(
            child: CumulusEmptyState(
              icon: Icons.cloud_off,
              title: 'Nothing has been delivered yet',
              message:
                  'A source appears here the first time it pushes to '
                  'POST /api/v1/sources/deliver. This lens is assembled from '
                  'the sync runs themselves, so it cannot show a feed that '
                  'has never run.',
            ),
          )
        else
          ListCard(
            title: 'Inbound feeds',
            subtitle:
                'one row per source and kind, assembled from the sync runs — '
                'not from a separate bookkeeping store',
            children: [
              for (final source in sources)
                _SourceRow(source: source, mapping: _contract(source)),
            ],
          ),
        CumulusCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const SectionHeader(
                title: 'How a source works here',
                subtitle:
                    'a source is not a primitive — it is a Transport plus an '
                    'inbound Flow',
              ),
              const SizedBox(height: 12),
              Text(
                'CDC, sync and MDM are Flows over sourced transport rather '
                'than mechanisms of their own, so every row above is a Flow '
                'you can open in the run list. Each delivery lands its rows '
                'exactly as they arrived (so a rule change can re-resolve '
                'without re-fetching), scores them against the vendor master, '
                'and then either links, mints, or raises a steward Task — a '
                'batch feed never suspends for one ambiguous row. Pass an '
                'idempotency key and a retried delivery re-enters the same '
                'run, replays its steps and changes nothing.',
                style: CumulusText.body,
              ),
            ],
          ),
        ),
      ],
    );
  }
}

class _SourceRow extends StatelessWidget {
  final SourceStatus source;
  final SourceMapping? mapping;

  const _SourceRow({required this.source, this.mapping});

  @override
  Widget build(BuildContext context) {
    final failed = source.lastStatus == 'failed' || source.lastError.isNotEmpty;
    final tone = failed
        ? CumulusTone.danger
        : source.awaitingSteward > 0
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
        child: Icon(_iconForSyncKind(source.kind), size: 17,
            color: tone.foreground),
      ),
      title: Row(
        children: [
          Flexible(child: Text(source.source)),
          const SizedBox(width: 10),
          StatusBadge(source.kind, dense: true, uppercase: true),
          const SizedBox(width: 6),
          StatusBadge(source.lastStatus, tone: tone, dense: true),
          if (source.awaitingSteward > 0) ...[
            const SizedBox(width: 6),
            StatusBadge(
              '${source.awaitingSteward} awaiting a steward',
              tone: CumulusTone.warn,
              dense: true,
            ),
          ],
        ],
      ),
      subtitle: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            '${source.deliveries} delivery(s) · ${source.recordsLanded} landed '
            '· ${source.unchanged} unchanged (byte-identical redelivery) · '
            '${source.linked} linked to an existing entity · ${source.created} '
            'minted',
          ),
          const SizedBox(height: 6),
          Wrap(
            spacing: 12,
            runSpacing: 4,
            children: [
              Text(source.flowId, style: CumulusText.mono),
              if (source.watermark.isNotEmpty)
                Text('· watermark ${source.watermark}',
                    style: CumulusText.meta),
              if (source.lastRunId.isNotEmpty)
                Text('· ${source.lastRunId}', style: CumulusText.mono),
            ],
          ),
          if (mapping != null) ...[
            const SizedBox(height: 6),
            // The contract line: what this feed binds to, per its Mapping.
            Wrap(
              spacing: 12,
              runSpacing: 4,
              children: [
                Text('contract: ${mapping!.strategy}'
                    '${mapping!.landsAs.isEmpty ? '' : ' → ${mapping!.landsAs}'}',
                    style: CumulusText.meta),
                for (final b in mapping!.identity)
                  Text('· ${b.column} → ${b.objectType}',
                      style: CumulusText.meta),
                Text('· escalates ${mapping!.escalation}',
                    style: CumulusText.meta),
              ],
            ),
          ],
          if (source.lastError.isNotEmpty) ...[
            const SizedBox(height: 6),
            Text(
              source.lastError,
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
                _ago(source.lastRunAtIso),
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

IconData _iconForSyncKind(String kind) => switch (kind) {
  'vendors' => Icons.storefront_outlined,
  'purchase-orders' => Icons.receipt_outlined,
  'receiving-reports' => Icons.local_shipping_outlined,
  _ => Icons.sync_alt,
};

// ---------------------------------------------------------------------------
// Documents
// ---------------------------------------------------------------------------

class _DocumentsLens extends StatelessWidget {
  final List<StoredDocument> documents;
  final Map<String, dynamic> stats;

  const _DocumentsLens({required this.documents, required this.stats});

  @override
  Widget build(BuildContext context) {
    if (documents.isEmpty) {
      return CumulusCard(
        child: CumulusEmptyState(
          icon: Icons.folder_off_outlined,
          title: 'No artifacts stored',
          message:
              'Receipts and invoices land here when a Flow stores them, under '
              'the retention their Collection declares.',
        ),
      );
    }
    return ListCard(
      title: 'Audit documents',
      subtitle:
          'the artifacts a decision was made from, with the regions an '
          'extraction read · retention is stamped on write',
      children: [
        for (final document in documents)
          CumulusListRow(
            leading: Container(
              width: 34,
              height: 34,
              alignment: Alignment.center,
              decoration: BoxDecoration(
                color: CumulusTone.teal.background,
                borderRadius: BorderRadius.circular(10),
              ),
              child: Icon(
                document.kind == 'invoice'
                    ? Icons.receipt_long_outlined
                    : Icons.receipt_outlined,
                size: 17,
                color: CumulusTone.teal.foreground,
              ),
            ),
            title: Row(
              children: [
                Flexible(
                  child: Text(
                    document.filename.isEmpty ? document.id : document.filename,
                  ),
                ),
                const SizedBox(width: 10),
                StatusBadge(document.kind, dense: true, uppercase: true),
                if (!document.hasBytes) ...[
                  const SizedBox(width: 6),
                  const Tooltip(
                    message:
                        'A hint-driven run: the flow ran but no file was '
                        'attached, so there are no bytes to show.',
                    child: StatusBadge('no bytes', dense: true),
                  ),
                ],
              ],
            ),
            subtitle: Text(
              '${document.regionCount} extracted region(s) · '
              '${document.runIds.length} run(s) · '
              '${document.byteSize == 0 ? 'no payload' : '${document.byteSize} bytes'} '
              '· ${document.collectionId}',
            ),
            trailing: [
              SizedBox(
                width: 130,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.end,
                  children: [
                    Text(
                      document.retentionUntilIso.isEmpty
                          ? 'indefinite'
                          : document.retentionUntilIso.substring(0, 10),
                      style: const TextStyle(
                        fontSize: 13,
                        fontWeight: FontWeight.w600,
                        color: CumulusColors.ink,
                      ),
                    ),
                    Text('purgeable from', style: CumulusText.statCaption),
                  ],
                ),
              ),
            ],
          ),
      ],
    );
  }
}

// ---------------------------------------------------------------------------
// Mappings — the declared seams
// ---------------------------------------------------------------------------

/// One card per feed contract, read from `server/app/mappings.py` via the
/// registry endpoint. The point of the lens is that the whole contract is one
/// artifact: binding, precedence, policy ref, custody, escalation — nowhere
/// else to look.
class _MappingsLens extends StatelessWidget {
  final MappingRegistry? registry;

  const _MappingsLens({required this.registry});

  @override
  Widget build(BuildContext context) {
    final reg = registry;
    if (reg == null) {
      return const CumulusCard(
        child: CumulusEmptyState(
          icon: Icons.cable_outlined,
          title: 'The mapping registry did not answer',
          message: 'GET /api/v1/mappings returns the declared contracts.',
        ),
      );
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      spacing: 16,
      children: [
        if (reg.problems.isNotEmpty)
          CumulusCard(
            borderColor: CumulusColors.danger,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const SectionHeader(
                  title: 'Registry does not conform',
                  subtitle: 'a mapping names something that does not exist',
                ),
                const SizedBox(height: 8),
                for (final p in reg.problems)
                  Text('· $p', style: CumulusText.body),
              ],
            ),
          ),
        ListCard(
          title: 'Declared contracts',
          subtitle:
              'one per feed — what arrives, what it binds to, and whether '
              'rows are reified into the graph or column-bound and resolved '
              'lazily. Declared in server/app/mappings.py, exported to the '
              'corpus like the ontology.',
          children: [for (final m in reg.mappings) _MappingRow(mapping: m)],
        ),
        CumulusCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const SectionHeader(
                title: 'Reify vs. bind',
                subtitle: 'the one decision a Mapping adds beyond binding',
              ),
              const SizedBox(height: 12),
              Text(
                'Reify a source row into the graph only when the row itself '
                'needs identity, provenance, or human adjudication — a vendor '
                'list, a purchase order. A high-volume feed (telemetry, GL '
                'transactions) is column-bound instead: the mapping declares '
                'that a column carries identifiers of a type, resolution '
                'happens lazily at query or rollup time, and unresolvable '
                'ids escalate on aggregates. A million readings must never '
                'become a million objects. Every feed running today reifies; '
                'the registry refuses a bind mapping that tries to name a '
                'lands-as type, so the distinction cannot blur silently.',
                style: CumulusText.body,
              ),
            ],
          ),
        ),
      ],
    );
  }
}

class _MappingRow extends StatelessWidget {
  final SourceMapping mapping;

  const _MappingRow({required this.mapping});

  @override
  Widget build(BuildContext context) {
    final m = mapping;
    return CumulusListRow(
      leading: Container(
        width: 34,
        height: 34,
        alignment: Alignment.center,
        decoration: BoxDecoration(
          color: CumulusTone.violet.background,
          borderRadius: BorderRadius.circular(10),
        ),
        child: const Icon(Icons.cable_outlined, size: 17,
            color: CumulusColors.ink),
      ),
      title: Row(
        children: [
          Flexible(child: Text(m.id)),
          const SizedBox(width: 10),
          StatusBadge(m.strategy, dense: true, uppercase: true),
          const SizedBox(width: 6),
          StatusBadge(m.transport, dense: true),
          if (m.custody == 'reference') ...[
            const SizedBox(width: 6),
            StatusBadge('reference custody',
                tone: CumulusTone.warn, dense: true),
          ],
        ],
      ),
      subtitle: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(m.summary),
          const SizedBox(height: 6),
          Wrap(
            spacing: 12,
            runSpacing: 4,
            children: [
              Text('${m.system} · payload ${m.payload}',
                  style: CumulusText.meta),
              if (m.landsAs.isNotEmpty)
                Text('· lands as ${m.landsAs}', style: CumulusText.mono),
              for (final b in m.identity)
                Text('· ${b.column} → ${b.objectType} (by ${b.key})',
                    style: CumulusText.meta),
              if (m.precedenceSource.isNotEmpty)
                Text('· claims write as ${m.precedenceSource}',
                    style: CumulusText.meta),
              if (m.matchPolicy.isNotEmpty)
                Text('· policy ${m.matchPolicy}', style: CumulusText.mono),
              if (m.collection.isNotEmpty)
                Text('· governed by ${m.collection}', style: CumulusText.meta),
              Text('· escalates ${m.escalation}', style: CumulusText.meta),
            ],
          ),
          if (m.notes.isNotEmpty) ...[
            const SizedBox(height: 6),
            Text(m.notes, style: CumulusText.meta),
          ],
        ],
      ),
    );
  }
}
