import 'dart:async';

import 'package:flutter/material.dart';

import '../models/asset.dart' show AskAnswer;
import '../models/graph.dart';
import '../services/api_client.dart';
import '../theme/cumulus_theme.dart';
import 'graph_layout.dart';
import 'ui_kit.dart';

/// The Entities lens: Directory + Walk + Atlas as one navigation continuum.
///
/// The TBox always fits on a screen; the ABox never will. So the schema is
/// worn as *chrome* — a minimap and two lists on the left rail — and the ABox
/// only ever renders as the slice you asked for: a type's table, one entity's
/// dossier, a walk's frontier. Nothing here draws the whole graph, which is
/// exactly why it scales past the demo corpus.
///
/// Three ways in, one continuum: **search** by any handle a person actually
/// holds (labels, natural keys, external ids — identity Links make an ERP id
/// find the canonical entity); **browse** a type as a table whose columns are
/// its declared fields; **ask** a question and watch the planner's own tool
/// calls replayed as hops. Every view hands off to the dossier, and the
/// dossier hands off to the walk.
class EntityExplorer extends StatefulWidget {
  final ApiClient api;
  final Ontology? ontology;

  const EntityExplorer({super.key, required this.api, required this.ontology});

  @override
  State<EntityExplorer> createState() => _EntityExplorerState();
}

enum _View { table, links, dossier, walk }

/// One hop of a walk: where it landed and the Link that carried it there.
class _Hop {
  final GraphObjectDetail detail;
  final String via;

  const _Hop(this.detail, this.via);
}

class _EntityExplorerState extends State<EntityExplorer> {
  _View _view = _View.table;
  String? _type;
  String? _linkType;

  List<GraphObject>? _rows;
  List<LinkRow>? _links;
  GraphObjectDetail? _detail;
  final List<_Hop> _path = [];

  /// Which field's claim history is expanded in the dossier.
  String? _expandedField;

  final TextEditingController _searchCtl = TextEditingController();
  Timer? _debounce;
  List<EntityHit>? _hits;
  bool _searching = false;

  AskAnswer? _ask;
  String _askedQuestion = '';
  bool _asking = false;

  String? _error;
  bool _loading = false;

  @override
  void initState() {
    super.initState();
    final types = widget.ontology?.objects ?? const <OntologyObjectType>[];
    // Land on the vendor master when it has rows — labelled entities make a
    // better first screen than a table of content hashes — else wherever the
    // most instances are.
    final populated = types.where((t) => t.instances > 0).toList()
      ..sort((a, b) => b.instances.compareTo(a.instances));
    final vendor = populated.where((t) => t.name == 'Vendor').toList();
    _type = vendor.isNotEmpty
        ? 'Vendor'
        : populated.isNotEmpty
            ? populated.first.name
            : (types.isNotEmpty ? types.first.name : null);
    if (_type != null) _openType(_type!);
  }

  @override
  void dispose() {
    _debounce?.cancel();
    _searchCtl.dispose();
    super.dispose();
  }

  OntologyObjectType? _declared(String? name) {
    for (final t in widget.ontology?.objects ?? const <OntologyObjectType>[]) {
      if (t.name == name) return t;
    }
    return null;
  }

  // -- navigation -----------------------------------------------------------

  Future<void> _openType(String name) async {
    setState(() {
      _view = _View.table;
      _type = name;
      _rows = null;
      _error = null;
      _loading = true;
    });
    try {
      final rows = await widget.api.listObjects(type: name, limit: 200);
      if (mounted) {
        setState(() {
          _rows = rows;
          _loading = false;
        });
      }
    } on ApiException catch (e) {
      if (mounted) {
        setState(() {
          _error = e.message;
          _loading = false;
        });
      }
    }
  }

  Future<void> _openLinkType(String name) async {
    setState(() {
      _view = _View.links;
      _linkType = name;
      _links = null;
      _error = null;
      _loading = true;
    });
    try {
      final rows = await widget.api.listLinks(type: name);
      if (mounted) {
        setState(() {
          _links = rows;
          _loading = false;
        });
      }
    } on ApiException catch (e) {
      if (mounted) {
        setState(() {
          _error = e.message;
          _loading = false;
        });
      }
    }
  }

  Future<void> _openEntity(String objectId) async {
    setState(() {
      _hits = null;
      _error = null;
      _loading = true;
      _expandedField = null;
    });
    try {
      final detail = await widget.api.getObject(objectId);
      if (mounted) {
        setState(() {
          _detail = detail;
          _type = detail.object.type;
          _view = _View.dossier;
          _loading = false;
        });
      }
    } on ApiException catch (e) {
      if (mounted) {
        setState(() {
          _error = e.message;
          _loading = false;
        });
      }
    }
  }

  void _startWalk() {
    final detail = _detail;
    if (detail == null) return;
    setState(() {
      _path
        ..clear()
        ..add(_Hop(detail, ''));
      _view = _View.walk;
    });
  }

  Future<void> _hop(GraphObject other, String via) async {
    setState(() => _loading = true);
    try {
      final detail = await widget.api.getObject(other.id);
      if (mounted) {
        setState(() {
          _path.add(_Hop(detail, via));
          _loading = false;
        });
      }
    } on ApiException catch (e) {
      if (mounted) {
        setState(() {
          _error = e.message;
          _loading = false;
        });
      }
    }
  }

  // -- search and ask ---------------------------------------------------------

  void _onQueryChanged(String q) {
    _debounce?.cancel();
    if (q.trim().length < 2) {
      setState(() => _hits = null);
      return;
    }
    _debounce = Timer(const Duration(milliseconds: 250), () async {
      setState(() => _searching = true);
      try {
        final hits = await widget.api.searchEntities(q.trim());
        if (mounted) {
          setState(() {
            _hits = hits;
            _searching = false;
          });
        }
      } on ApiException {
        if (mounted) setState(() => _searching = false);
      }
    });
  }

  Future<void> _runAsk() async {
    final q = _searchCtl.text.trim();
    if (q.isEmpty) return;
    setState(() {
      _asking = true;
      _askedQuestion = q;
      _ask = null;
      _hits = null;
    });
    try {
      final answer = await widget.api.ask(q);
      if (mounted) {
        setState(() {
          _ask = answer;
          _asking = false;
        });
      }
    } on ApiException catch (e) {
      if (mounted) {
        setState(() {
          _error = e.message;
          _asking = false;
        });
      }
    }
  }

  /// `graph:Vendor/vendor_ab12cd@rev3` → `vendor_ab12cd`.
  static String? _idFromRef(String ref) {
    if (!ref.startsWith('graph:')) return null;
    final slash = ref.indexOf('/');
    if (slash < 0) return null;
    final at = ref.indexOf('@', slash);
    return at < 0 ? ref.substring(slash + 1) : ref.substring(slash + 1, at);
  }

  // -- build ------------------------------------------------------------------

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _searchBar(),
        if (_hits != null) _searchResults(),
        if (_ask != null || _asking) _askPanel(),
        const SizedBox(height: 16),
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            SizedBox(width: 268, child: _schemaRail()),
            const SizedBox(width: 16),
            Expanded(child: _content()),
          ],
        ),
      ],
    );
  }

  Widget _searchBar() {
    return Row(
      children: [
        Expanded(
          child: TextField(
            controller: _searchCtl,
            onChanged: _onQueryChanged,
            onSubmitted: (_) {
              // A question mark means a question; anything else searches.
              if (_searchCtl.text.trim().endsWith('?')) _runAsk();
            },
            decoration: InputDecoration(
              isDense: true,
              prefixIcon: const Icon(Icons.search, size: 18),
              suffixIcon: _searching
                  ? const Padding(
                      padding: EdgeInsets.all(12),
                      child: SizedBox(
                        width: 14,
                        height: 14,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      ),
                    )
                  : null,
              hintText:
                  'Find an entity by name, natural key, or an external '
                  "system's id — or end with ? to ask the graph",
              border: OutlineInputBorder(
                borderRadius: BorderRadius.circular(CumulusRadius.control),
                borderSide: const BorderSide(color: CumulusColors.border),
              ),
            ),
          ),
        ),
        const SizedBox(width: 10),
        FilledButton.icon(
          onPressed: _asking ? null : _runAsk,
          icon: const Icon(Icons.route_outlined, size: 16),
          label: const Text('Ask the graph'),
        ),
      ],
    );
  }

  Widget _searchResults() {
    final hits = _hits!;
    return Padding(
      padding: const EdgeInsets.only(top: 10),
      child: CumulusCard(
        padding: EdgeInsets.zero,
        child: hits.isEmpty
            ? const Padding(
                padding: EdgeInsets.all(18),
                child: Text('Nothing matches.', style: CumulusText.meta),
              )
            : Column(
                children: [
                  for (final hit in hits.take(10))
                    CumulusListRow(
                      onTap: () => _openEntity(hit.entity.id),
                      leading: _TypeDot(type: hit.entity.type),
                      title: Row(
                        children: [
                          Flexible(child: Text(hit.entity.label)),
                          const SizedBox(width: 8),
                          StatusBadge(hit.entity.type, dense: true),
                          const SizedBox(width: 6),
                          StatusBadge('matched ${hit.match}', dense: true),
                        ],
                      ),
                      subtitle: Text(
                        hit.via.isEmpty
                            ? hit.entity.naturalKey
                            : '${hit.entity.naturalKey} · found via '
                                'identity link from ${hit.via}',
                        style: CumulusText.meta,
                      ),
                    ),
                ],
              ),
      ),
    );
  }

  Widget _askPanel() {
    return Padding(
      padding: const EdgeInsets.only(top: 10),
      child: CumulusCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: SectionHeader(
                    title: _askedQuestion,
                    subtitle: _asking
                        ? 'walking the graph…'
                        : (_ask?.backend == 'vertex'
                            ? 'answered by the planner — its actual tool '
                                'calls are the citation'
                            : 'answered from retrieval (no planner in this '
                                'deployment); document citations below'),
                  ),
                ),
                IconButton(
                  icon: const Icon(Icons.close, size: 16),
                  onPressed: () => setState(() {
                    _ask = null;
                    _askedQuestion = '';
                  }),
                ),
              ],
            ),
            if (_asking)
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 18),
                child: Center(child: CircularProgressIndicator(strokeWidth: 2)),
              )
            else if (_ask != null) ...[
              const SizedBox(height: 8),
              Text(_ask!.answer, style: CumulusText.body),
              if (_ask!.steps.isNotEmpty) ...[
                const SizedBox(height: 14),
                const Text('THE WALK IT TOOK', style: CumulusText.statCaption),
                const SizedBox(height: 6),
                for (final (i, step) in _ask!.steps.indexed)
                  Padding(
                    padding: const EdgeInsets.symmetric(vertical: 3),
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text('${i + 1}.', style: CumulusText.meta),
                        const SizedBox(width: 8),
                        Text(step.tool, style: CumulusText.mono),
                        const SizedBox(width: 10),
                        Expanded(
                          child: Text(step.summary, style: CumulusText.meta),
                        ),
                        if (_idFromRef(step.ref) != null)
                          RowAction(
                            label: 'Open',
                            onTap: () => _openEntity(_idFromRef(step.ref)!),
                          ),
                      ],
                    ),
                  ),
              ],
            ],
          ],
        ),
      ),
    );
  }

  // -- the schema rail: the TBox worn as chrome -------------------------------

  Widget _schemaRail() {
    final ontology = widget.ontology;
    if (ontology == null) {
      return const CumulusCard(
        child: Text('The ontology did not answer.', style: CumulusText.meta),
      );
    }
    return CumulusCard(
      padding: const EdgeInsets.fromLTRB(14, 14, 14, 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _Minimap(
            ontology: ontology,
            selected: _type,
            onTapType: _openType,
          ),
          const SizedBox(height: 12),
          const Text('ENTITIES', style: CumulusText.statCaption),
          const SizedBox(height: 4),
          for (final t in ontology.objects)
            _railRow(
              label: t.label,
              count: t.instances,
              selected: _view == _View.table && _type == t.name,
              onTap: () => _openType(t.name),
            ),
          const SizedBox(height: 12),
          const Text('LINKS', style: CumulusText.statCaption),
          const SizedBox(height: 4),
          for (final l in ontology.links)
            _railRow(
              label: l.name,
              count: l.instances,
              selected: _view == _View.links && _linkType == l.name,
              onTap: () => _openLinkType(l.name),
              mono: true,
            ),
          const SizedBox(height: 6),
        ],
      ),
    );
  }

  Widget _railRow({
    required String label,
    required int count,
    required bool selected,
    required VoidCallback onTap,
    bool mono = false,
  }) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(8),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
        decoration: BoxDecoration(
          color: selected ? CumulusColors.accentSoft : Colors.transparent,
          borderRadius: BorderRadius.circular(8),
        ),
        child: Row(
          children: [
            Expanded(
              child: Text(
                label,
                overflow: TextOverflow.ellipsis,
                style: mono
                    ? CumulusText.mono.copyWith(
                        fontSize: 12,
                        color: selected
                            ? CumulusColors.accentInk
                            : CumulusColors.inkSecondary,
                      )
                    : TextStyle(
                        fontSize: 13,
                        fontWeight:
                            selected ? FontWeight.w600 : FontWeight.w500,
                        color: selected
                            ? CumulusColors.accentInk
                            : CumulusColors.ink,
                      ),
              ),
            ),
            Text('$count', style: CumulusText.statCaption),
          ],
        ),
      ),
    );
  }

  // -- content views -----------------------------------------------------------

  Widget _content() {
    if (_error != null) {
      return ErrorNotice(
        message: _error!,
        onRetry: () => setState(() => _error = null),
      );
    }
    return switch (_view) {
      _View.table => _tableView(),
      _View.links => _linksView(),
      _View.dossier => _dossierView(),
      _View.walk => _walkView(),
    };
  }

  Widget _tableView() {
    final declared = _declared(_type);
    final rows = _rows;
    final fieldNames = [
      for (final f in declared?.fields ?? const <Map<String, dynamic>>[])
        '${f['name']}',
    ].take(3).toList();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (declared != null)
          CumulusCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Expanded(
                      child: SectionHeader(
                        title: declared.label,
                        subtitle: declared.summary,
                      ),
                    ),
                    if (declared.authoritative)
                      const StatusBadge('authoritative',
                          tone: CumulusTone.success, dense: true),
                  ],
                ),
                const SizedBox(height: 8),
                Wrap(
                  spacing: 12,
                  runSpacing: 4,
                  children: [
                    Text('natural key: ${declared.naturalKey}',
                        style: CumulusText.meta),
                    if (declared.collection.isNotEmpty)
                      Text('· collection ${declared.collection}',
                          style: CumulusText.meta),
                    Text('· ${declared.fields.length} declared field(s)',
                        style: CumulusText.meta),
                  ],
                ),
              ],
            ),
          ),
        const SizedBox(height: 16),
        if (_loading && rows == null)
          const CumulusLoading()
        else
          ListCard(
            title: '${rows?.length ?? 0} in the graph',
            subtitle: 'click a row for the dossier — every claim, every edge',
            children: [
              if (rows != null && rows.isEmpty)
                const CumulusEmptyState(
                  icon: Icons.hub_outlined,
                  title: 'Nothing of this type yet',
                  message: 'Entities appear here when a flow writes them.',
                ),
              for (final row in rows ?? const <GraphObject>[])
                CumulusListRow(
                  onTap: () => _openEntity(row.id),
                  leading: _TypeDot(type: row.type),
                  title: Row(
                    children: [
                      Flexible(child: Text(row.label)),
                      const SizedBox(width: 8),
                      if (row.conflictFields.isNotEmpty)
                        StatusBadge(
                          '⚠ ${row.conflictFields.join(", ")}',
                          tone: CumulusTone.warn,
                          dense: true,
                        ),
                    ],
                  ),
                  subtitle: Wrap(
                    spacing: 12,
                    runSpacing: 4,
                    children: [
                      Text(row.naturalKey, style: CumulusText.mono),
                      for (final name in fieldNames)
                        if (row.props[name] != null)
                          Text('· $name ${row.props[name]}',
                              style: CumulusText.meta),
                    ],
                  ),
                  trailing: [
                    Text('rev ${row.revision} · ${row.claimCount} claims',
                        style: CumulusText.statCaption),
                  ],
                ),
            ],
          ),
      ],
    );
  }

  Widget _linksView() {
    final links = _links;
    OntologyLinkType? declared;
    for (final l in widget.ontology?.links ?? const <OntologyLinkType>[]) {
      if (l.name == _linkType) declared = l;
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (declared != null)
          CumulusCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Expanded(
                      child: SectionHeader(
                        title:
                            '${declared.fromType} — ${declared.name} → '
                            '${declared.toType}',
                        subtitle: declared.summary,
                      ),
                    ),
                    if (declared.identity)
                      const StatusBadge('identity link',
                          tone: CumulusTone.violet, dense: true),
                    const SizedBox(width: 6),
                    StatusBadge(declared.cardinality, dense: true),
                  ],
                ),
              ],
            ),
          ),
        const SizedBox(height: 16),
        if (_loading && links == null)
          const CumulusLoading()
        else
          ListCard(
            title: '${links?.length ?? 0} link(s)',
            subtitle: declared?.identity ?? false
                ? 'each row is an auditable identity assertion: confidence, '
                    'method, and the run that wrote it'
                : 'each row is one typed edge',
            children: [
              if (links != null && links.isEmpty)
                const CumulusEmptyState(
                  icon: Icons.link_off,
                  title: 'No links of this type yet',
                  message: 'Links appear here when a flow writes them.',
                ),
              for (final link in links ?? const <LinkRow>[])
                CumulusListRow(
                  leading: const Icon(Icons.link, size: 16,
                      color: CumulusColors.inkMuted),
                  title: Wrap(
                    crossAxisAlignment: WrapCrossAlignment.center,
                    spacing: 8,
                    children: [
                      RowAction(
                        label: link.fromEntity.label,
                        onTap: () => _openEntity(link.fromEntity.id),
                      ),
                      Text('→ ${link.type} →', style: CumulusText.meta),
                      RowAction(
                        label: link.toEntity.label,
                        onTap: () => _openEntity(link.toEntity.id),
                      ),
                    ],
                  ),
                  subtitle: Wrap(
                    spacing: 12,
                    runSpacing: 4,
                    children: [
                      Text('method ${link.method}', style: CumulusText.meta),
                      Text(
                        '· confidence ${link.confidence.toStringAsFixed(2)}',
                        style: CumulusText.meta,
                      ),
                      if (!link.live)
                        Text('· reversed: ${link.reversalReason}',
                            style: CumulusText.meta
                                .copyWith(color: CumulusColors.danger)),
                      if (link.runId.isNotEmpty)
                        Text('· ${link.runId}', style: CumulusText.mono),
                    ],
                  ),
                ),
            ],
          ),
      ],
    );
  }

  Widget _dossierView() {
    final detail = _detail;
    if (detail == null || (_loading && _view == _View.dossier)) {
      return const CumulusLoading();
    }
    final entity = detail.object;
    final groups = _groupEdges(detail.neighbors);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          children: [
            BackLink(
              label: 'All ${entity.type}s',
              onTap: () => _openType(entity.type),
            ),
            const Spacer(),
            OutlinedButton.icon(
              onPressed: _startWalk,
              icon: const Icon(Icons.route_outlined, size: 16),
              label: const Text('Walk from here'),
            ),
          ],
        ),
        const SizedBox(height: 10),
        CumulusCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Expanded(
                    child: SectionHeader(
                      title: entity.label,
                      subtitle: entity.naturalKey,
                    ),
                  ),
                  StatusBadge(entity.type, dense: true),
                  const SizedBox(width: 6),
                  StatusBadge(entity.status,
                      tone: entity.status == 'active'
                          ? CumulusTone.success
                          : CumulusTone.warn,
                      dense: true),
                  const SizedBox(width: 6),
                  MetaPill(entity.ref),
                ],
              ),
            ],
          ),
        ),
        const SizedBox(height: 16),
        ListCard(
          title: 'Fields',
          subtitle:
              'resolved under precedence — click one for the claims behind '
              'it, including the losers',
          children: [
            for (final entry in entity.props.entries)
              _fieldRow(detail, entry.key, entry.value),
          ],
        ),
        const SizedBox(height: 16),
        ListCard(
          title: 'Links',
          subtitle: 'typed and directional; click an entity to move to it',
          children: [
            if (groups.isEmpty)
              const CumulusEmptyState(
                icon: Icons.link_off,
                title: 'No links yet',
                message: 'This entity is not connected to anything.',
              ),
            for (final g in groups) _edgeGroupRow(g, onPick: _openEntity),
          ],
        ),
      ],
    );
  }

  Widget _fieldRow(GraphObjectDetail detail, String field, Object? value) {
    final conflicted = detail.conflicts.containsKey(field);
    final expanded = _expandedField == field;
    final claims = detail.claims.where((c) => c.field == field).toList()
      ..sort((a, b) => b.atIso.compareTo(a.atIso));
    return Column(
      children: [
        CumulusListRow(
          onTap: () =>
              setState(() => _expandedField = expanded ? null : field),
          title: Row(
            children: [
              SizedBox(
                width: 220,
                child: Text(field, style: CumulusText.mono),
              ),
              Expanded(child: Text('$value')),
              if (conflicted)
                const StatusBadge('sources disagree',
                    tone: CumulusTone.warn, dense: true),
              const SizedBox(width: 8),
              Icon(expanded ? Icons.expand_less : Icons.expand_more,
                  size: 16, color: CumulusColors.inkMuted),
            ],
          ),
        ),
        if (expanded)
          Container(
            width: double.infinity,
            color: CumulusColors.surfaceMuted.withValues(alpha: 0.5),
            padding: const EdgeInsets.fromLTRB(20, 10, 20, 12),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                for (final claim in claims)
                  Padding(
                    padding: const EdgeInsets.symmetric(vertical: 3),
                    child: Wrap(
                      spacing: 10,
                      runSpacing: 4,
                      crossAxisAlignment: WrapCrossAlignment.center,
                      children: [
                        Icon(
                          '${claim.value}' == '$value'
                              ? Icons.check_circle_outline
                              : Icons.remove_circle_outline,
                          size: 14,
                          color: '${claim.value}' == '$value'
                              ? CumulusColors.success
                              : CumulusColors.inkMuted,
                        ),
                        Text('${claim.value}',
                            style: CumulusText.body.copyWith(
                              fontWeight: '${claim.value}' == '$value'
                                  ? FontWeight.w600
                                  : FontWeight.w400,
                            )),
                        Text('said by ${claim.source}',
                            style: CumulusText.meta),
                        if (claim.runId.isNotEmpty)
                          Text('· in ${claim.runId}/${claim.stepId}',
                              style: CumulusText.mono),
                        if (claim.atIso.isNotEmpty)
                          Text('· ${claim.atIso.split("T").first}',
                              style: CumulusText.meta),
                      ],
                    ),
                  ),
                if (claims.isEmpty)
                  const Text('No claims recorded for this field.',
                      style: CumulusText.meta),
              ],
            ),
          ),
      ],
    );
  }

  Widget _walkView() {
    if (_path.isEmpty) return const CumulusLoading();
    final current = _path.last;
    final entity = current.detail.object;
    final groups = _groupEdges(current.detail.neighbors);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        // The path bar: the walk so far, and the provenance of wherever you
        // end up. Clicking an earlier stop rewinds to it.
        CumulusCard(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
          child: Wrap(
            spacing: 6,
            runSpacing: 6,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              const Text('PATH', style: CumulusText.statCaption),
              const SizedBox(width: 6),
              for (final (i, hop) in _path.indexed) ...[
                if (i > 0)
                  Text('—${hop.via}→',
                      style: CumulusText.mono.copyWith(fontSize: 11)),
                InkWell(
                  onTap: i == _path.length - 1
                      ? null
                      : () => setState(
                          () => _path.removeRange(i + 1, _path.length)),
                  borderRadius: BorderRadius.circular(CumulusRadius.pill),
                  child: StatusBadge(
                    hop.detail.object.label,
                    tone: i == _path.length - 1
                        ? CumulusTone.accent
                        : CumulusTone.neutral,
                    dense: true,
                  ),
                ),
              ],
            ],
          ),
        ),
        const SizedBox(height: 16),
        CumulusCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Expanded(
                    child: SectionHeader(
                      title: entity.label,
                      subtitle:
                          '${entity.type} · ${entity.naturalKey} · the '
                          'frontier — pick an edge to move',
                    ),
                  ),
                  RowAction(
                    label: 'Open dossier',
                    onTap: () => _openEntity(entity.id),
                  ),
                ],
              ),
            ],
          ),
        ),
        const SizedBox(height: 16),
        if (_loading)
          const CumulusLoading()
        else
          ListCard(
            title: 'Edges from here',
            subtitle: 'grouped by Link type; counts instead of fan-outs',
            children: [
              if (groups.isEmpty)
                const CumulusEmptyState(
                  icon: Icons.explore_off_outlined,
                  title: 'Nowhere to go',
                  message: 'This entity has no live links.',
                ),
              for (final g in groups)
                _edgeGroupRow(
                  g,
                  onPick: (id) {
                    final other =
                        g.edges.firstWhere((e) => e.other.id == id).other;
                    _hop(other, g.label);
                  },
                ),
            ],
          ),
      ],
    );
  }

  // -- shared edge grouping -----------------------------------------------------

  List<_EdgeGroup> _groupEdges(List<GraphEdge> edges) {
    final map = <String, _EdgeGroup>{};
    for (final e in edges.where((e) => e.live)) {
      final key = '${e.type}:${e.direction}';
      map.putIfAbsent(key, () => _EdgeGroup(e.type, e.direction)).edges.add(e);
    }
    final out = map.values.toList()
      ..sort((a, b) => b.edges.length.compareTo(a.edges.length));
    return out;
  }

  Widget _edgeGroupRow(_EdgeGroup g, {required void Function(String) onPick}) {
    final targetType = g.edges.first.other.type;
    return CumulusListRow(
      leading: Icon(
        g.direction == 'out' ? Icons.arrow_forward : Icons.arrow_back,
        size: 15,
        color: CumulusColors.inkMuted,
      ),
      title: Row(
        children: [
          Text(g.label, style: CumulusText.mono),
          const SizedBox(width: 10),
          Text(
            '${g.edges.length} $targetType'
            '${g.edges.length == 1 ? '' : 's'}'
            '${g.direction == 'in' ? ' (inbound)' : ''}',
            style: CumulusText.meta,
          ),
        ],
      ),
      subtitle: Padding(
        padding: const EdgeInsets.only(top: 6),
        child: Wrap(
          spacing: 6,
          runSpacing: 6,
          children: [
            for (final e in g.edges.take(12))
              InkWell(
                onTap: () => onPick(e.other.id),
                borderRadius: BorderRadius.circular(CumulusRadius.pill),
                child: StatusBadge(e.other.label, dense: true),
              ),
            if (g.edges.length > 12)
              Text('+${g.edges.length - 12} more', style: CumulusText.meta),
          ],
        ),
      ),
    );
  }
}

class _EdgeGroup {
  final String label;
  final String direction;
  final List<GraphEdge> edges = [];

  _EdgeGroup(this.label, this.direction);
}

class _TypeDot extends StatelessWidget {
  final String type;

  const _TypeDot({required this.type});

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 30,
      height: 30,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        color: CumulusColors.accentSoft,
        borderRadius: BorderRadius.circular(9),
      ),
      child: Text(
        type.isEmpty ? '?' : type[0],
        style: const TextStyle(
          fontSize: 12,
          fontWeight: FontWeight.w700,
          color: CumulusColors.accentInk,
        ),
      ),
    );
  }
}

// -----------------------------------------------------------------------------
// The minimap: the whole TBox in the corner of your eye
// -----------------------------------------------------------------------------

/// The schema at postage-stamp size, using the same [ontologyLayout] grid as
/// every other diagram so a type sits where it always sits. It is chrome, not
/// content: enough to keep the shape of the model in view and to click a
/// type, with the lists below doing the legible work.
class _Minimap extends StatelessWidget {
  final Ontology ontology;
  final String? selected;
  final ValueChanged<String> onTapType;

  const _Minimap({
    required this.ontology,
    required this.selected,
    required this.onTapType,
  });

  static const _cellW = 44.0;
  static const _cellH = 20.0;
  static const _gapX = 5.0;
  static const _gapY = 9.0;

  Offset _center(String type) {
    final (col, row) = cellFor(type);
    return Offset(
      col * (_cellW + _gapX) + _cellW / 2,
      row * (_cellH + _gapY) + _cellH / 2,
    );
  }

  /// `PurchaseOrderLine` → `POL`; `Vendor` → `Ven`.
  static String _abbr(String name) {
    final humps = name.split(RegExp('(?=[A-Z])')).where((s) => s.isNotEmpty);
    if (humps.length > 1) return humps.map((h) => h[0]).join();
    return name.length <= 3 ? name : name.substring(0, 3);
  }

  @override
  Widget build(BuildContext context) {
    const width = ontologyColumns * _cellW + (ontologyColumns - 1) * _gapX;
    const height = ontologyRows * _cellH + (ontologyRows - 1) * _gapY;
    return SizedBox(
      width: width,
      height: height,
      child: Stack(
        children: [
          CustomPaint(
            size: const Size(width, height),
            painter: _MinimapEdgePainter(ontology: ontology, center: _center),
          ),
          for (final t in ontology.objects)
            Positioned(
              left: _center(t.name).dx - _cellW / 2,
              top: _center(t.name).dy - _cellH / 2,
              child: Tooltip(
                message: '${t.label} · ${t.instances} in the graph',
                waitDuration: const Duration(milliseconds: 350),
                child: InkWell(
                  onTap: () => onTapType(t.name),
                  borderRadius: BorderRadius.circular(5),
                  child: Container(
                    width: _cellW,
                    height: _cellH,
                    alignment: Alignment.center,
                    decoration: BoxDecoration(
                      color: t.name == selected
                          ? CumulusColors.accentSoft
                          : CumulusColors.surface,
                      borderRadius: BorderRadius.circular(5),
                      border: Border.all(
                        color: t.name == selected
                            ? CumulusColors.accent
                            : CumulusColors.borderStrong,
                        width: t.name == selected ? 1.2 : 0.8,
                      ),
                    ),
                    child: Text(
                      _abbr(t.name),
                      style: TextStyle(
                        fontSize: 9,
                        fontWeight: FontWeight.w600,
                        color: t.name == selected
                            ? CumulusColors.accentInk
                            : CumulusColors.inkSecondary,
                      ),
                    ),
                  ),
                ),
              ),
            ),
        ],
      ),
    );
  }
}

class _MinimapEdgePainter extends CustomPainter {
  final Ontology ontology;
  final Offset Function(String) center;

  _MinimapEdgePainter({required this.ontology, required this.center});

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = CumulusColors.border
      ..strokeWidth = 1;
    for (final link in ontology.links) {
      canvas.drawLine(center(link.fromType), center(link.toType), paint);
    }
  }

  @override
  bool shouldRepaint(covariant _MinimapEdgePainter old) =>
      old.ontology != ontology;
}
