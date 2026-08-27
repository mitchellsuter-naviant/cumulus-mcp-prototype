import 'package:flutter/material.dart';

import '../models/data_layer.dart';
import '../models/graph.dart';
import '../models/mapping.dart';
import '../models/world.dart';
import '../services/api_client.dart';
import '../theme/cumulus_theme.dart';
import 'cumulus_shell.dart';
import 'instance_map.dart';
import 'schema_map.dart';
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

enum _Lens { model, instances, collections, mappings, sources, documents }

class _DataPlaneViewState extends State<DataPlaneView> {
  late _Lens _lens = _lenses.first;

  List<_Lens> get _lenses => switch (widget.mode) {
    DataPlaneMode.ontology => const [
      _Lens.model,
      _Lens.instances,
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

  /// The object type being browsed under the Model lens.
  String? _objectType;
  List<GraphObject>? _objects;
  GraphObjectDetail? _selected;

  /// The Instances lens: which ABox is showing, and what it holds.
  List<AuthoredWorld> _worlds = const [];
  String _abox = 'live';
  String? _aboxType;
  MaterializedWorld? _materialized;
  bool _loadingAbox = false;
  WorldObject? _instance;

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
      final results = await Future.wait([
        attempt(widget.api.listCollections()),
        attempt(widget.api.ontology()),
        attempt(widget.api.graphStats()),
        attempt(widget.api.graphInvariants()),
        attempt(widget.api.listWorlds()),
        attempt(widget.api.listMappings()),
      ]);
      if (!mounted) return;
      setState(() {
        _collections = results[0] as List<DataCollection>?;
        _ontology = results[1] as Ontology?;
        _graphStats = (results[2] as Map<String, dynamic>?) ?? const {};
        _invariants =
            (results[3] as InvariantReport?) ?? InvariantReport.unknown;
        _worlds = (results[4] as List<AuthoredWorld>?) ?? const [];
        _mappings = results[5] as MappingRegistry?;
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

  /// Materialize whichever ABox is selected. `live` reads the running graph;
  /// anything else runs an authored fragment into a throwaway world.
  Future<void> _loadAbox() async {
    setState(() {
      _loadingAbox = true;
      _instance = null;
    });
    try {
      final loaded = _abox == 'live'
          ? await widget.api.graphInstances(type: _aboxType)
          : await widget.api.materializeWorld(_abox);
      if (mounted) {
        setState(() {
          _materialized = loaded;
          _loadingAbox = false;
        });
      }
    } on ApiException catch (e) {
      if (mounted) {
        setState(() {
          _error = e.message;
          _loadingAbox = false;
        });
      }
    }
  }

  Future<void> _browse(String type) async {
    setState(() {
      _objectType = type;
      _objects = null;
      _selected = null;
    });
    try {
      final objects = await widget.api.listObjects(type: type, limit: 200);
      if (mounted) setState(() => _objects = objects);
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    }
  }

  Future<void> _inspect(String objectId) async {
    try {
      final detail = await widget.api.getObject(objectId);
      if (mounted) setState(() => _selected = detail);
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    final collections = _collections ?? const <DataCollection>[];
    final sources = _sources ?? const <SourceStatus>[];
    final knowledge = collections.where((c) => c.isKnowledge).length;
    final data = collections.length - knowledge;

    final ontologyMode = widget.mode == DataPlaneMode.ontology;
    return PageBody(
      padding: const EdgeInsets.fromLTRB(28, 20, 28, 40),
      children: [
        PageHeading(
          eyebrow: ontologyMode
              ? 'Knowledge layer · encoded'
              : 'Knowledge layer · synced',
          title: ontologyMode ? 'Ontology' : 'Sources',
          subtitle: ontologyMode
              ? 'What we encoded: the schema (TBox), what exists against it '
                  '(ABox), the Collection declarations governance hangs off, '
                  'and the Mappings that bind external systems in.'
              : 'What arrives from outside: each feed against its declared '
                  'contract, and the artifacts the flows keep as evidence.',
          trailing: OutlinedButton.icon(
            onPressed: _loading ? null : _load,
            icon: const Icon(Icons.refresh, size: 16),
            label: const Text('Refresh'),
          ),
        ),
        if (_error != null) ErrorNotice(message: _error!, onRetry: _load),
        StatStrip(
          tiles: ontologyMode
              ? [
                  StatTile(
                    label: 'Object types',
                    value: '${_ontology?.objects.length ?? '—'}',
                    caption:
                        '${_ontology?.links.length ?? '—'} link types',
                  ),
                  StatTile(
                    label: 'Objects',
                    value: _n(_graphStats['objects']),
                    caption: '${_n(_graphStats['links'])} typed links',
                  ),
                  StatTile(
                    label: 'Conformance',
                    value: _invariants.objectsChecked > 0
                        ? '${_invariants.violations.length}'
                        : '—',
                    caption: 'invariant violations',
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
                ]
              : [
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
            _Lens.model => _ModelLens(
              ontology: _ontology,
              invariants: _invariants,
              type: _objectType,
              objects: _objects,
              selected: _selected,
              onBrowse: _browse,
              onInspect: _inspect,
              onClear: () => setState(() {
                _objectType = null;
                _objects = null;
                _selected = null;
              }),
              onCloseDetail: () => setState(() => _selected = null),
            ),
            _Lens.instances => _InstancesLens(
              worlds: _worlds,
              selected: _abox,
              type: _aboxType,
              typeNames: [
                for (final t in _ontology?.objects ?? const <OntologyObjectType>[])
                  if (t.instances > 0) t.name,
              ],
              materialized: _materialized,
              loading: _loadingAbox,
              instance: _instance,
              onSelectAbox: (id) {
                setState(() {
                  _abox = id;
                  _aboxType = null;
                  _materialized = null;
                });
                _loadAbox();
              },
              onSelectType: (type) {
                setState(() => _aboxType = type);
                _loadAbox();
              },
              onLoad: _loadAbox,
              onSelectInstance: (object) =>
                  setState(() => _instance = object),
            ),
            _Lens.documents => _DocumentsLens(
              documents: _documents ?? const [],
              stats: _documentStats,
            ),
          },
      ],
    );
  }

  static String _label(_Lens lens) => switch (lens) {
    _Lens.model => 'MODEL · TBOX',
    _Lens.instances => 'INSTANCES · ABOX',
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
    'ap-vendor-exemplars' => onOpenLibrary,
    'flow-definitions' => onOpenFlows,
    'expense-records' => onOpenExpense,
    'ap-records' => onOpenPayables,
    'audit-documents' => null,
    _ => null,
  };

  String? _handoffLabel(String id) => switch (id) {
    'knowledge-library' || 'ap-vendor-exemplars' => 'Library',
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
  'exemplar shelf' => Icons.auto_awesome_outlined,
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
// Entities
// ---------------------------------------------------------------------------

class _ModelLens extends StatelessWidget {
  final Ontology? ontology;
  final InvariantReport invariants;
  final String? type;
  final List<GraphObject>? objects;
  final GraphObjectDetail? selected;
  final void Function(String type) onBrowse;
  final void Function(String objectId) onInspect;
  final VoidCallback onClear;
  final VoidCallback onCloseDetail;

  const _ModelLens({
    required this.ontology,
    required this.invariants,
    required this.type,
    required this.objects,
    required this.selected,
    required this.onBrowse,
    required this.onInspect,
    required this.onClear,
    required this.onCloseDetail,
  });

  @override
  Widget build(BuildContext context) {
    final ontology = this.ontology;
    if (ontology == null) {
      return CumulusCard(
        child: CumulusEmptyState(
          icon: Icons.hub_outlined,
          title: 'No ontology',
          message: 'The entity layer did not answer.',
        ),
      );
    }
    if (selected != null) {
      return _ObjectDetailCard(detail: selected!, onClose: onCloseDetail);
    }
    if (type != null) {
      return _ObjectListCard(
        type: type!,
        objects: objects,
        onInspect: onInspect,
        onClear: onClear,
      );
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      spacing: 16,
      children: [
        _ConformanceBanner(invariants: invariants),
        CumulusCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              SectionHeader(
                title: 'The model',
                subtitle:
                    'object types and the typed edges between them, with what '
                    'is actually in the graph · dashed edges are identity '
                    'assertions, which a steward can withdraw',
                trailing: MetaPill('knowledge:${ontology.assetId}'),
              ),
              const SizedBox(height: 16),
              SchemaMap(ontology: ontology, onBrowse: onBrowse),
            ],
          ),
        ),
        CumulusCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              SectionHeader(
                title: 'Claim precedence',
                subtitle:
                    'when two systems assert the same field, both claims are '
                    'kept and this order decides which resolves',
              ),
              const SizedBox(height: 12),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  for (var i = 0; i < ontology.precedence.length; i++) ...[
                    StatusBadge(
                      '${i + 1}. ${ontology.precedence[i]}',
                      tone: i == 0 ? CumulusTone.violet : CumulusTone.neutral,
                    ),
                    if (i < ontology.precedence.length - 1)
                      const Icon(Icons.chevron_right, size: 16,
                          color: CumulusColors.inkMuted),
                  ],
                ],
              ),
              const SizedBox(height: 10),
              Text(
                'Changing this order re-resolves history rather than rewriting '
                'it — the losing claims are still on the record.',
                style: CumulusText.meta,
              ),
            ],
          ),
        ),
        ListCard(
          title: 'Object types',
          subtitle:
              'as declared, with what is actually in the graph beside it',
          children: [
            for (final objectType in ontology.objects)
              _ObjectTypeRow(type: objectType, onBrowse: onBrowse),
          ],
        ),
        ListCard(
          title: 'Link types',
          subtitle: 'typed, directional, and — for identity — reversible',
          children: [
            for (final link in ontology.links) _LinkTypeRow(link: link),
          ],
        ),
      ],
    );
  }
}

class _ObjectTypeRow extends StatelessWidget {
  final OntologyObjectType type;
  final void Function(String type) onBrowse;

  const _ObjectTypeRow({required this.type, required this.onBrowse});

  @override
  Widget build(BuildContext context) {
    final empty = type.instances == 0;
    return CumulusListRow(
      onTap: empty ? null : () => onBrowse(type.name),
      leading: Monogram(
        type.name.substring(0, 1),
        tone: empty ? CumulusTone.neutral : CumulusTone.accent,
      ),
      title: Row(
        children: [
          Flexible(child: Text(type.label)),
          const SizedBox(width: 10),
          StatusBadge(type.name, dense: true),
          if (type.authoritative) ...[
            const SizedBox(width: 6),
            const StatusBadge('authoritative', tone: CumulusTone.violet,
                dense: true),
          ],
        ],
      ),
      subtitle: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(type.summary),
          const SizedBox(height: 6),
          Text(
            'natural key: ${type.naturalKey} · collection '
            '${type.collection} · ${type.fields.length} declared field(s)',
            style: CumulusText.meta,
          ),
        ],
      ),
      trailing: [
        SizedBox(
          width: 92,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Text(
                '${type.instances}',
                style: TextStyle(
                  fontSize: 17,
                  fontWeight: FontWeight.w600,
                  color: empty ? CumulusColors.inkMuted : CumulusColors.ink,
                ),
              ),
              Text(
                empty ? 'declared only' : 'in the graph',
                style: CumulusText.statCaption,
                textAlign: TextAlign.end,
              ),
            ],
          ),
        ),
        SizedBox(
          width: 110,
          child: empty
              ? null
              : Align(
                  alignment: Alignment.centerRight,
                  child: RowAction(
                    label: 'Browse',
                    icon: Icons.chevron_right,
                    onTap: () => onBrowse(type.name),
                  ),
                ),
        ),
      ],
    );
  }
}

class _LinkTypeRow extends StatelessWidget {
  final OntologyLinkType link;

  const _LinkTypeRow({required this.link});

  @override
  Widget build(BuildContext context) {
    return CumulusListRow(
      leading: Icon(
        link.identity ? Icons.link : Icons.arrow_forward,
        size: 18,
        color: link.identity ? CumulusColors.accent : CumulusColors.inkMuted,
      ),
      title: Row(
        children: [
          Flexible(
            child: Text(
              '${link.fromType} → ${link.toType}',
              style: CumulusText.rowTitle,
            ),
          ),
          const SizedBox(width: 10),
          StatusBadge(link.name, dense: true),
          const SizedBox(width: 6),
          Tooltip(
            message: link.enforced
                ? 'Enforced on write: a second edge would be refused.'
                : 'Declared unconstrained on purpose — a second edge here is '
                    'legitimate, not a bug.',
            child: StatusBadge(
              link.cardinality,
              tone: link.enforced ? CumulusTone.accent : CumulusTone.neutral,
              dense: true,
            ),
          ),
          if (link.identity) ...[
            const SizedBox(width: 6),
            const Tooltip(
              message:
                  'Carries a confidence and a method, and can be withdrawn '
                  'without erasing that it was asserted.',
              child: StatusBadge('identity', tone: CumulusTone.accent,
                  dense: true),
            ),
          ],
        ],
      ),
      subtitle: Text(link.summary),
      trailing: [
        SizedBox(
          width: 80,
          child: Text(
            '${link.instances}',
            textAlign: TextAlign.end,
            style: TextStyle(
              fontSize: 15,
              fontWeight: FontWeight.w600,
              color: link.instances == 0
                  ? CumulusColors.inkMuted
                  : CumulusColors.ink,
            ),
          ),
        ),
      ],
    );
  }
}

class _ObjectListCard extends StatelessWidget {
  final String type;
  final List<GraphObject>? objects;
  final void Function(String objectId) onInspect;
  final VoidCallback onClear;

  const _ObjectListCard({
    required this.type,
    required this.objects,
    required this.onInspect,
    required this.onClear,
  });

  @override
  Widget build(BuildContext context) {
    final rows = objects;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      spacing: 12,
      children: [
        Align(
          alignment: Alignment.centerLeft,
          child: BackLink(label: '← All object types', onTap: onClear),
        ),
        if (rows == null)
          const CumulusLoading()
        else
          ListCard(
            title: type,
            subtitle: '${rows.length} object(s) · click one for its claims',
            children: [
              for (final object in rows)
                CumulusListRow(
                  onTap: () => onInspect(object.id),
                  leading: Monogram(
                    object.label.isEmpty ? '?' : object.label.substring(0, 1),
                    tone: object.conflictFields.isEmpty
                        ? CumulusTone.neutral
                        : CumulusTone.warn,
                  ),
                  title: Row(
                    children: [
                      Flexible(child: Text(object.label)),
                      const SizedBox(width: 10),
                      StatusBadge(
                        object.props['status']?.toString() ?? object.status,
                        tone: object.status == 'active'
                            ? CumulusTone.success
                            : CumulusTone.neutral,
                        dense: true,
                      ),
                      if (object.conflictFields.isNotEmpty) ...[
                        const SizedBox(width: 6),
                        StatusBadge(
                          '${object.conflictFields.length} conflict(s)',
                          tone: CumulusTone.warn,
                          dense: true,
                        ),
                      ],
                    ],
                  ),
                  subtitle: Text(
                    object.props.entries
                        .take(4)
                        .map((e) => '${e.key}: ${e.value}')
                        .join(' · '),
                  ),
                  trailing: [
                    SizedBox(
                      width: 150,
                      child: Text(
                        object.ref,
                        textAlign: TextAlign.end,
                        style: CumulusText.mono,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                  ],
                ),
            ],
          ),
      ],
    );
  }
}

class _ObjectDetailCard extends StatelessWidget {
  final GraphObjectDetail detail;
  final VoidCallback onClose;

  const _ObjectDetailCard({required this.detail, required this.onClose});

  @override
  Widget build(BuildContext context) {
    final object = detail.object;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      spacing: 12,
      children: [
        Align(
          alignment: Alignment.centerLeft,
          child: BackLink(label: '← Back to the list', onTap: onClose),
        ),
        CumulusCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              SectionHeader(
                title: object.label,
                subtitle:
                    '${object.type} · ${object.claimCount} claim(s) · revision '
                    '${object.revision}',
                trailing: MetaPill(object.ref),
              ),
              const SizedBox(height: 14),
              _KeyValueGrid(
                entries: object.props.entries
                    .map((e) => (e.key, '${e.value}'))
                    .toList(),
              ),
            ],
          ),
        ),
        if (detail.conflicts.isNotEmpty)
          ListCard(
            title: 'Retained conflicts',
            subtitle:
                'two sources assert different values; precedence decides which '
                'resolves and both stay on the record',
            children: [
              for (final entry in detail.conflicts.entries)
                CumulusListRow(
                  leading: const Icon(Icons.call_split, size: 18,
                      color: CumulusColors.warn),
                  title: Text(entry.key),
                  subtitle: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      for (final claim in entry.value)
                        Padding(
                          padding: const EdgeInsets.only(top: 4),
                          child: Row(
                            children: [
                              StatusBadge(
                                claim.authority,
                                tone: claim.authority == 'human'
                                    ? CumulusTone.violet
                                    : CumulusTone.neutral,
                                dense: true,
                              ),
                              const SizedBox(width: 8),
                              Expanded(
                                child: Text(
                                  '${claim.value}  ·  ${claim.source}',
                                  style: CumulusText.meta,
                                ),
                              ),
                            ],
                          ),
                        ),
                    ],
                  ),
                ),
            ],
          ),
        _FieldHistoryCard(detail: detail),
        if (detail.neighbors.isNotEmpty)
          ListCard(
            title: 'Edges',
            subtitle: 'typed, directional, and reversible where they assert '
                'identity',
            children: [
              for (final edge in detail.neighbors)
                CumulusListRow(
                  leading: Icon(
                    edge.direction == 'out'
                        ? Icons.arrow_forward
                        : Icons.arrow_back,
                    size: 18,
                    color: edge.live
                        ? CumulusColors.inkSecondary
                        : CumulusColors.inkMuted,
                  ),
                  title: Row(
                    children: [
                      Flexible(child: Text(edge.other.label)),
                      const SizedBox(width: 10),
                      StatusBadge(edge.type, dense: true),
                      const SizedBox(width: 6),
                      StatusBadge(edge.other.type, dense: true),
                      if (!edge.live) ...[
                        const SizedBox(width: 6),
                        const StatusBadge('withdrawn',
                            tone: CumulusTone.danger, dense: true),
                      ],
                    ],
                  ),
                  subtitle: Text(
                    '${edge.method} · confidence '
                    '${edge.confidence.toStringAsFixed(2)}'
                    '${edge.props.isEmpty ? '' : ' · ${edge.props}'}',
                  ),
                ),
            ],
          ),
      ],
    );
  }
}

class _KeyValueGrid extends StatelessWidget {
  final List<(String, String)> entries;

  const _KeyValueGrid({required this.entries});

  @override
  Widget build(BuildContext context) {
    return Wrap(
      spacing: 28,
      runSpacing: 12,
      children: [
        for (final (key, value) in entries)
          ConstrainedBox(
            constraints: const BoxConstraints(minWidth: 150, maxWidth: 320),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(key.toUpperCase(), style: CumulusText.statLabel),
                const SizedBox(height: 3),
                Text(value, style: CumulusText.body),
              ],
            ),
          ),
      ],
    );
  }
}

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

/// TBox conformance, stated plainly.
///
/// This is the tier of testing that needs no per-case labels: the invariants
/// hold for every object and edge, so a structural bug — a second vendor
/// minted for one company, a line with no parent, an edge pointing at the
/// wrong type — is caught without anyone having written an assertion about it.
/// Green here is a claim worth making; a violation is a bug, not a warning.
class _ConformanceBanner extends StatelessWidget {
  final InvariantReport invariants;

  const _ConformanceBanner({required this.invariants});

  @override
  Widget build(BuildContext context) {
    final ok = invariants.ok;
    final tone = ok ? CumulusTone.success : CumulusTone.danger;
    return CumulusCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                width: 32,
                height: 32,
                alignment: Alignment.center,
                decoration: BoxDecoration(
                  color: tone.background,
                  borderRadius: BorderRadius.circular(9),
                ),
                child: Icon(
                  ok ? Icons.verified_outlined : Icons.report_gmailerrorred,
                  size: 17,
                  color: tone.foreground,
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      ok
                          ? 'The graph conforms to its ontology'
                          : '${invariants.violations.length} violation(s) of '
                              'the declared ontology',
                      style: const TextStyle(
                        fontSize: 14,
                        fontWeight: FontWeight.w600,
                        color: CumulusColors.ink,
                      ),
                    ),
                    const SizedBox(height: 3),
                    Text(
                      '${invariants.objectsChecked} object(s) and '
                      '${invariants.linksChecked} live link(s) checked · '
                      '${invariants.strictWrites ? "writes that would violate it are refused" : "validation is reporting only on this deployment"}',
                      style: CumulusText.meta,
                    ),
                  ],
                ),
              ),
              StatusBadge(
                ok ? 'conforming' : 'violated',
                tone: tone,
                uppercase: true,
              ),
            ],
          ),
          if (!ok) ...[
            const SizedBox(height: 14),
            for (final violation in invariants.violations.take(12))
              Padding(
                padding: const EdgeInsets.only(bottom: 5),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    SizedBox(
                      width: 168,
                      child: StatusBadge(
                        violation.kind,
                        tone: CumulusTone.danger,
                        dense: true,
                      ),
                    ),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(violation.subject, style: CumulusText.mono),
                          Text(violation.detail, style: CumulusText.meta),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
            if (invariants.violations.length > 12)
              Text(
                '… and ${invariants.violations.length - 12} more',
                style: CumulusText.meta,
              ),
          ],
        ],
      ),
    );
  }
}

/// Every field of one object, with the claims behind it.
///
/// The view that makes retained claims feel like a feature rather than a
/// storage detail: for each field, who asserted what, which assertion resolves,
/// and — where the run recorded it — the step that wrote it. "Why does this say
/// NET30" is answered by looking, not by reasoning about precedence.
class _FieldHistoryCard extends StatelessWidget {
  final GraphObjectDetail detail;

  const _FieldHistoryCard({required this.detail});

  /// Whether this claim is the one that resolved. Compared against the object's
  /// resolved value rather than recomputed, so the card cannot disagree with
  /// the server about precedence.
  bool _resolves(GraphClaim claim) {
    final resolved = detail.object.props[claim.field];
    if (resolved == null) return false;
    return '$resolved' == '${claim.value}';
  }

  @override
  Widget build(BuildContext context) {
    final byField = <String, List<GraphClaim>>{};
    for (final claim in detail.claims) {
      byField.putIfAbsent(claim.field, () => []).add(claim);
    }
    for (final claims in byField.values) {
      claims.sort((a, b) => b.atIso.compareTo(a.atIso));
    }
    final fields = byField.keys.toList()..sort();

    return ListCard(
      title: 'Fields and the claims behind them',
      subtitle:
          '${detail.claims.length} assertion(s) across ${fields.length} '
          'field(s) · a losing claim is kept, not overwritten',
      children: [
        for (final field in fields)
          CumulusListRow(
            leading: Container(
              width: 8,
              height: 8,
              margin: const EdgeInsets.only(top: 6),
              decoration: BoxDecoration(
                color: byField[field]!.length > 1
                    ? CumulusTone.warn.foreground
                    : CumulusColors.borderStrong,
                shape: BoxShape.circle,
              ),
            ),
            title: Row(
              children: [
                Flexible(child: Text(field)),
                const SizedBox(width: 10),
                Text(
                  '${detail.object.props[field] ?? "—"}',
                  style: CumulusText.body.copyWith(
                    fontWeight: FontWeight.w600,
                  ),
                ),
                if (byField[field]!.length > 1) ...[
                  const SizedBox(width: 8),
                  StatusBadge(
                    '${byField[field]!.length} claims',
                    tone: CumulusTone.warn,
                    dense: true,
                  ),
                ],
              ],
            ),
            subtitle: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                for (final claim in byField[field]!)
                  Padding(
                    padding: const EdgeInsets.only(top: 4),
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        SizedBox(
                          width: 18,
                          child: _resolves(claim)
                              ? Icon(
                                  Icons.check,
                                  size: 13,
                                  color: CumulusTone.success.foreground,
                                )
                              : const SizedBox.shrink(),
                        ),
                        SizedBox(
                          width: 96,
                          child: StatusBadge(
                            claim.authority,
                            tone: claim.authority == 'human'
                                ? CumulusTone.violet
                                : CumulusTone.neutral,
                            dense: true,
                          ),
                        ),
                        Expanded(
                          child: Text(
                            '${claim.value}',
                            style: CumulusText.meta.copyWith(
                              color: _resolves(claim)
                                  ? CumulusColors.ink
                                  : CumulusColors.inkMuted,
                            ),
                          ),
                        ),
                        const SizedBox(width: 10),
                        Expanded(
                          child: Text(
                            [
                              claim.source,
                              if (claim.confidence < 1)
                                'confidence ${claim.confidence.toStringAsFixed(2)}',
                              if (claim.stepId.isNotEmpty)
                                'wrote at ${claim.stepId}',
                              _ago(claim.atIso),
                            ].join('  ·  '),
                            style: CumulusText.mono,
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                      ],
                    ),
                  ),
              ],
            ),
          ),
      ],
    );
  }
}

// ---------------------------------------------------------------------------
// Instances (ABox)
// ---------------------------------------------------------------------------

/// The ABox, on its own screen.
///
/// Live instances and the fragments authored in Python are peers here, chosen
/// from one selector and drawn by one renderer. The distinction that matters is
/// not which store they came from but whether they were **observed** — written
/// by a Flow through an Action — or **declared**, in an eval fixture or the
/// demo seed. Both are assertions about instances of the same model.
///
/// An authored fragment is *materialized* rather than statically expanded: it
/// runs into a throwaway world, so what you see includes the resolution the
/// sync flow performed and the Tasks it escalated. A prediction would show the
/// payload; this shows the consequence. Nothing real is touched either way,
/// which is what makes looking a plain read.
class _InstancesLens extends StatelessWidget {
  final List<AuthoredWorld> worlds;
  final String selected;
  final String? type;
  final List<String> typeNames;
  final MaterializedWorld? materialized;
  final bool loading;
  final WorldObject? instance;
  final void Function(String id) onSelectAbox;
  final void Function(String? type) onSelectType;
  final VoidCallback onLoad;
  final void Function(WorldObject object) onSelectInstance;

  const _InstancesLens({
    required this.worlds,
    required this.selected,
    required this.type,
    required this.typeNames,
    required this.materialized,
    required this.loading,
    required this.instance,
    required this.onSelectAbox,
    required this.onSelectType,
    required this.onLoad,
    required this.onSelectInstance,
  });

  bool get _isLive => selected == 'live';

  @override
  Widget build(BuildContext context) {
    final world = materialized;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      spacing: 16,
      children: [
        CumulusCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              SectionHeader(
                title: 'Which ABox',
                subtitle:
                    'the running graph, or a fragment declared in Python — '
                    'observed and declared instances of the same model',
                trailing: OutlinedButton.icon(
                  onPressed: loading ? null : onLoad,
                  icon: const Icon(Icons.play_arrow, size: 16),
                  label: Text(
                    loading
                        ? 'Loading…'
                        : world == null
                            ? 'Load'
                            : 'Reload',
                  ),
                ),
              ),
              const SizedBox(height: 14),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  LensChip(
                    label: 'LIVE GRAPH',
                    tone: CumulusTone.success,
                    selected: _isLive,
                    onTap: () => onSelectAbox('live'),
                  ),
                  for (final candidate in worlds)
                    LensChip(
                      label: candidate.isSeed
                          ? 'SEED · ${candidate.name}'
                          : candidate.id.replaceFirst('eval:', ''),
                      tone: candidate.isSeed
                          ? CumulusTone.teal
                          : CumulusTone.violet,
                      selected: selected == candidate.id,
                      onTap: () => onSelectAbox(candidate.id),
                    ),
                ],
              ),
              if (_isLive) ...[
                const SizedBox(height: 14),
                const FieldLabel(
                  'Focus',
                  hint:
                      'a type, plus whatever it connects to — a screen of boxes '
                      'with no edges answers less than a smaller one that shows '
                      'how they join',
                ),
                Wrap(
                  spacing: 6,
                  runSpacing: 6,
                  children: [
                    LensChip(
                      label: 'EVERYTHING',
                      selected: type == null,
                      onTap: () => onSelectType(null),
                    ),
                    for (final name in typeNames)
                      LensChip(
                        label: name,
                        selected: type == name,
                        onTap: () => onSelectType(name),
                      ),
                  ],
                ),
              ],
              if (world != null) ...[
                const SizedBox(height: 14),
                Text(
                  world.world.summary,
                  style: CumulusText.body,
                ),
                const SizedBox(height: 6),
                Wrap(
                  spacing: 10,
                  runSpacing: 8,
                  children: [
                    MetaPill(
                      '${world.objects.length} object(s)',
                      tone: CumulusTone.teal,
                    ),
                    MetaPill('${world.links.length} link(s)'),
                    if (world.world.declaredIn.isNotEmpty)
                      MetaPill(world.world.declaredIn),
                    if (world.world.owner.isNotEmpty &&
                        world.world.owner != 'live')
                      MetaPill(world.world.owner),
                    if (world.truncated)
                      const MetaPill('capped', tone: CumulusTone.warn),
                    if (world.violations.isEmpty)
                      const MetaPill('conforms', tone: CumulusTone.success)
                    else
                      MetaPill(
                        '${world.violations.length} violation(s)',
                        tone: CumulusTone.danger,
                      ),
                  ],
                ),
              ],
              if (world?.error.isNotEmpty ?? false) ...[
                const SizedBox(height: 12),
                ErrorNotice(message: world!.error),
              ],
            ],
          ),
        ),
        if (loading)
          const CumulusCard(child: CumulusLoading())
        else if (world == null)
          CumulusCard(
            child: CumulusEmptyState(
              icon: Icons.scatter_plot_outlined,
              title: 'Nothing loaded yet',
              message:
                  'Materializing runs the fragment through the real sync '
                  'flows, so it is a deliberate click rather than something '
                  'that happens on arrival.',
            ),
          )
        else ...[
          CumulusCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const SectionHeader(
                  title: 'Instance map',
                  subtitle:
                      'laid out by type, in the same places the model diagram '
                      'puts them — the two views are one picture at two levels',
                ),
                const SizedBox(height: 14),
                InstanceMap(
                  objects: world.objects,
                  links: world.links,
                  selectedId: instance?.id,
                  onSelect: onSelectInstance,
                ),
              ],
            ),
          ),
          if (instance != null) _InstanceDetailCard(object: instance!),
          if (world.runs.isNotEmpty || world.tasks.isNotEmpty)
            _ConsequenceCard(world: world),
          _InstanceInventoryCard(
            world: world,
            onSelect: onSelectInstance,
            selectedId: instance?.id,
          ),
        ],
      ],
    );
  }
}

/// What the fragment *did*, as opposed to what it says.
///
/// The reason a fragment is materialized rather than parsed: a delivery goes
/// through the real sync flow, so the runs it drove and the Tasks it escalated
/// are part of what it asserts. A misspelled vendor waiting for a steward is a
/// property of the fixture, not an accident of the environment.
class _ConsequenceCard extends StatelessWidget {
  final MaterializedWorld world;

  const _ConsequenceCard({required this.world});

  @override
  Widget build(BuildContext context) {
    return CumulusCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionHeader(
            title: 'What establishing it did',
            subtitle:
                'the fragment ran through the real flows, so these are part of '
                'what it asserts',
          ),
          if (world.runs.isNotEmpty) ...[
            const SizedBox(height: 12),
            const FieldLabel('Runs it drove'),
            for (final run in world.runs)
              Padding(
                padding: const EdgeInsets.only(bottom: 4),
                child: Row(
                  children: [
                    StatusBadge(
                      run.status,
                      tone: run.status == 'succeeded'
                          ? CumulusTone.success
                          : CumulusTone.warn,
                      dense: true,
                    ),
                    const SizedBox(width: 8),
                    SizedBox(
                      width: 190,
                      child: Text(run.flowId, style: CumulusText.body),
                    ),
                    Expanded(
                      child: Text(
                        '${run.steps} step(s)'
                        '${run.outputs.isEmpty ? "" : " · ${run.outputs}"}',
                        style: CumulusText.meta,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                  ],
                ),
              ),
          ],
          if (world.tasks.isNotEmpty) ...[
            const SizedBox(height: 12),
            const FieldLabel(
              'Judgment it asked for',
              hint: 'a batch feed escalates rather than guessing, and keeps going',
            ),
            for (final task in world.tasks)
              Padding(
                padding: const EdgeInsets.only(bottom: 4),
                child: Row(
                  children: [
                    StatusBadge(task.kind, tone: CumulusTone.warn, dense: true),
                    const SizedBox(width: 8),
                    Expanded(child: Text(task.title, style: CumulusText.body)),
                    const SizedBox(width: 8),
                    Text(task.queue, style: CumulusText.mono),
                  ],
                ),
              ),
          ],
          if (world.violations.isNotEmpty) ...[
            const SizedBox(height: 12),
            const FieldLabel('Ontology violations'),
            for (final violation in world.violations)
              Text(
                violation,
                style: CumulusText.meta.copyWith(color: CumulusColors.danger),
              ),
          ],
        ],
      ),
    );
  }
}

class _InstanceDetailCard extends StatelessWidget {
  final WorldObject object;

  const _InstanceDetailCard({required this.object});

  @override
  Widget build(BuildContext context) {
    final byField = <String, List<WorldClaim>>{};
    for (final claim in object.claims) {
      byField.putIfAbsent(claim.field, () => []).add(claim);
    }
    return CumulusCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SectionHeader(
            title: object.label,
            subtitle:
                '${object.type} · ${object.claims.length} claim(s)'
                '${object.conflictFields.isEmpty ? "" : " · sources disagree on ${object.conflictFields.join(", ")}"}',
            trailing: MetaPill(object.ref),
          ),
          const SizedBox(height: 12),
          // The pair every natural key raises: what it reads as, and what it
          // resolves to.
          Row(
            children: [
              SizedBox(
                width: 150,
                child: Text('NATURAL KEY', style: CumulusText.statLabel),
              ),
              Expanded(
                child: Text(object.naturalKey, style: CumulusText.body),
              ),
            ],
          ),
          const SizedBox(height: 4),
          Row(
            children: [
              SizedBox(
                width: 150,
                child: Text('RESOLVES TO', style: CumulusText.statLabel),
              ),
              Expanded(child: Text(object.id, style: CumulusText.mono)),
            ],
          ),
          const SizedBox(height: 14),
          const FieldLabel(
            'Fields, and the claims behind them',
            hint: 'a losing claim is kept, not overwritten',
          ),
          for (final entry in byField.entries)
            Padding(
              padding: const EdgeInsets.only(bottom: 6),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      SizedBox(
                        width: 150,
                        child: Text(entry.key, style: CumulusText.mono),
                      ),
                      Text(
                        '${object.props[entry.key] ?? "—"}',
                        style: CumulusText.body.copyWith(
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                      if (entry.value.length > 1) ...[
                        const SizedBox(width: 8),
                        StatusBadge(
                          '${entry.value.length} claims',
                          tone: CumulusTone.warn,
                          dense: true,
                        ),
                      ],
                    ],
                  ),
                  for (final claim in entry.value)
                    Padding(
                      padding: const EdgeInsets.only(left: 150, top: 2),
                      child: Row(
                        children: [
                          SizedBox(
                            width: 18,
                            child: claim.resolves
                                ? Icon(
                                    Icons.check,
                                    size: 13,
                                    color: CumulusTone.success.foreground,
                                  )
                                : const SizedBox.shrink(),
                          ),
                          SizedBox(
                            width: 96,
                            child: StatusBadge(
                              claim.authority,
                              tone: claim.authority == 'human'
                                  ? CumulusTone.violet
                                  : CumulusTone.neutral,
                              dense: true,
                            ),
                          ),
                          Expanded(
                            child: Text(
                              '${claim.value}  ·  ${claim.source}',
                              style: CumulusText.meta.copyWith(
                                color: claim.resolves
                                    ? CumulusColors.ink
                                    : CumulusColors.inkMuted,
                              ),
                              overflow: TextOverflow.ellipsis,
                            ),
                          ),
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
}

class _InstanceInventoryCard extends StatelessWidget {
  final MaterializedWorld world;
  final void Function(WorldObject object) onSelect;
  final String? selectedId;

  const _InstanceInventoryCard({
    required this.world,
    required this.onSelect,
    required this.selectedId,
  });

  @override
  Widget build(BuildContext context) {
    final byType = <String, List<WorldObject>>{};
    for (final object in world.objects) {
      byType.putIfAbsent(object.type, () => []).add(object);
    }
    final types = byType.keys.toList()..sort();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      spacing: 16,
      children: [
        ListCard(
          title: 'Instances',
          subtitle:
              '${world.objects.length} across ${types.length} type(s) · '
              'click one for its claims',
          children: [
            for (final type in types)
              for (final object in byType[type]!)
                CumulusListRow(
                  onTap: () => onSelect(object),
                  leading: Monogram(
                    type.substring(0, 1),
                    tone: object.conflictFields.isEmpty
                        ? CumulusTone.neutral
                        : CumulusTone.warn,
                  ),
                  title: Row(
                    children: [
                      Flexible(child: Text(object.label)),
                      const SizedBox(width: 10),
                      StatusBadge(type, dense: true),
                      if (object.conflictFields.isNotEmpty) ...[
                        const SizedBox(width: 6),
                        StatusBadge(
                          '${object.conflictFields.length} conflict(s)',
                          tone: CumulusTone.warn,
                          dense: true,
                        ),
                      ],
                      if (selectedId == object.id) ...[
                        const SizedBox(width: 6),
                        const StatusBadge(
                          'showing',
                          tone: CumulusTone.accent,
                          dense: true,
                        ),
                      ],
                    ],
                  ),
                  subtitle: Text(
                    object.props.entries
                        .take(4)
                        .map((e) => '${e.key}: ${e.value}')
                        .join(' · '),
                  ),
                  trailing: [
                    SizedBox(
                      width: 220,
                      child: Text(
                        object.naturalKey,
                        textAlign: TextAlign.end,
                        style: CumulusText.mono,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                  ],
                ),
          ],
        ),
        if (world.byLinkType.isNotEmpty)
          CumulusCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const SectionHeader(
                  title: 'Edges',
                  subtitle:
                      'counted by type — the map draws the shape, this names it',
                ),
                const SizedBox(height: 12),
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: [
                    for (final entry in world.byLinkType.entries)
                      Container(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 10,
                          vertical: 5,
                        ),
                        decoration: BoxDecoration(
                          color: CumulusColors.surfaceMuted,
                          borderRadius: BorderRadius.circular(7),
                        ),
                        child: Row(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Text(entry.key, style: CumulusText.mono),
                            const SizedBox(width: 7),
                            Text(
                              '${entry.value}',
                              style: const TextStyle(
                                fontSize: 11.5,
                                fontWeight: FontWeight.w700,
                                color: CumulusColors.accentInk,
                              ),
                            ),
                          ],
                        ),
                      ),
                  ],
                ),
              ],
            ),
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
