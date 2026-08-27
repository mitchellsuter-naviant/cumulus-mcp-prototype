import 'dart:async';

import 'package:flutter/material.dart';

import '../models/asset.dart' show AskAnswer;
import '../models/data_layer.dart' show DataCollection;
import '../models/graph.dart';
import '../services/api_client.dart';
import '../theme/cumulus_theme.dart';
import 'ui_kit.dart';

/// The Entities lens: independent entities on the left, one entity's world on
/// the right, cut by link *nature*.
///
/// The hierarchy does the scaling. Only independent entities (types nothing
/// composes) appear in the left rail — parts fold into their owners, so a
/// growing ontology grows the rail slowly. Selecting an entity fills the
/// right two-thirds, where a floating pill switches between the three natures
/// an edge can have:
///
/// - **Contents** — what it is made of (compositional edges: an invoice's
///   lines, its retained match). Parts render with their substance, because
///   they have no page of their own.
/// - **Associations** — what it relates to (independent things: the vendor
///   that issued it, the PO it bills). Clicking one moves the selection.
/// - **Identity** — what other systems call it (identity edges, with the
///   confidence and method a steward can audit).
///
/// Search accepts any handle a person holds; ending with `?` asks the graph
/// and renders the planner's own walk.
class EntityExplorer extends StatefulWidget {
  final ApiClient api;
  final Ontology? ontology;

  /// The Collection registry, used to tell business entities from
  /// infrastructure (a landing Collection's types group below the fold).
  final List<DataCollection> collections;

  const EntityExplorer({
    super.key,
    required this.api,
    required this.ontology,
    this.collections = const [],
  });

  @override
  State<EntityExplorer> createState() => _EntityExplorerState();
}

enum _Nature { contents, associations, identity }

class _EntityExplorerState extends State<EntityExplorer> {
  /// Root instances by type name, for the left rail.
  final Map<String, List<GraphObject>> _roots = {};
  final Set<String> _expandedTypes = {};
  bool _loadingRail = true;

  GraphObjectDetail? _detail;
  bool _loadingDetail = false;
  _Nature _nature = _Nature.contents;
  String? _expandedField;
  bool _fieldsOpen = false;

  final TextEditingController _searchCtl = TextEditingController();
  Timer? _debounce;
  List<EntityHit>? _hits;
  bool _searching = false;

  AskAnswer? _ask;
  String _askedQuestion = '';
  bool _asking = false;

  String? _error;

  // -- derived ontology views -------------------------------------------------

  List<OntologyObjectType> get _rootTypes => [
    for (final t in widget.ontology?.objects ?? const <OntologyObjectType>[])
      if (!t.isPart) t,
  ];

  bool _isInfrastructure(OntologyObjectType t) {
    for (final c in widget.collections) {
      if (c.id == t.collection) return c.kind == 'landing';
    }
    return false;
  }

  String _natureOf(String linkType) {
    for (final l in widget.ontology?.links ?? const <OntologyLinkType>[]) {
      if (l.name == linkType) return l.nature;
    }
    return 'association';
  }

  OntologyObjectType? _declared(String name) {
    for (final t in widget.ontology?.objects ?? const <OntologyObjectType>[]) {
      if (t.name == name) return t;
    }
    return null;
  }

  @override
  void initState() {
    super.initState();
    _loadRail();
  }

  @override
  void dispose() {
    _debounce?.cancel();
    _searchCtl.dispose();
    super.dispose();
  }

  Future<void> _loadRail() async {
    setState(() => _loadingRail = true);
    Future<T?> attempt<T>(Future<T> call) =>
        call.then<T?>((v) => v).catchError((_) => null);
    final types = _rootTypes.where((t) => t.instances > 0).toList();
    final results = await Future.wait([
      for (final t in types) attempt(widget.api.listObjects(type: t.name, limit: 100)),
    ]);
    if (!mounted) return;
    setState(() {
      for (final (i, t) in types.indexed) {
        final rows = results[i];
        if (rows != null) _roots[t.name] = rows;
      }
      _loadingRail = false;
    });
    // Land on something with a story: the first business entity available.
    if (_detail == null) {
      for (final t in types) {
        if (!_isInfrastructure(t) && (_roots[t.name]?.isNotEmpty ?? false)) {
          if (t.name == 'Vendor' || t == types.last) {
            _select(_roots[t.name]!.first.id);
            break;
          }
        }
      }
    }
  }

  Future<void> _select(String objectId) async {
    setState(() {
      _hits = null;
      _loadingDetail = true;
      _expandedField = null;
      _fieldsOpen = false;
      _error = null;
    });
    try {
      final detail = await widget.api.getObject(objectId);
      if (!mounted) return;
      setState(() {
        _detail = detail;
        _loadingDetail = false;
        // Default to the nature that has something to show, in story order.
        final grouped = _grouped(detail);
        _nature = grouped[_Nature.contents]!.isNotEmpty
            ? _Nature.contents
            : _Nature.associations;
      });
    } on ApiException catch (e) {
      if (mounted) {
        setState(() {
          _error = e.message;
          _loadingDetail = false;
        });
      }
    }
  }

  /// Edges of the selected entity, cut by nature and grouped by link type.
  Map<_Nature, List<_EdgeGroup>> _grouped(GraphObjectDetail detail) {
    final byNature = {
      _Nature.contents: <String, _EdgeGroup>{},
      _Nature.associations: <String, _EdgeGroup>{},
      _Nature.identity: <String, _EdgeGroup>{},
    };
    for (final e in detail.neighbors.where((e) => e.live)) {
      final nature = switch (_natureOf(e.type)) {
        'composition' => _Nature.contents,
        'identity' => _Nature.identity,
        _ => _Nature.associations,
      };
      // Contents are what this entity is made of — its own parts, not the
      // owner seen from a part's side. The owner surfaces as a breadcrumb.
      if (nature == _Nature.contents && e.direction == 'in') continue;
      byNature[nature]!
          .putIfAbsent('${e.type}:${e.direction}',
              () => _EdgeGroup(e.type, e.direction))
          .edges
          .add(e);
    }
    return {
      for (final entry in byNature.entries)
        entry.key: entry.value.values.toList()
          ..sort((a, b) => b.edges.length.compareTo(a.edges.length)),
    };
  }

  GraphEdge? _ownerEdge(GraphObjectDetail detail) {
    for (final e in detail.neighbors.where((e) => e.live)) {
      if (e.direction == 'in' && _natureOf(e.type) == 'composition') return e;
    }
    return null;
  }

  // -- search and ask -----------------------------------------------------------

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

  static String? _idFromRef(String ref) {
    if (!ref.startsWith('graph:')) return null;
    final slash = ref.indexOf('/');
    if (slash < 0) return null;
    final at = ref.indexOf('@', slash);
    return at < 0 ? ref.substring(slash + 1) : ref.substring(slash + 1, at);
  }

  // -- build ---------------------------------------------------------------------

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _searchBar(),
        if (_hits != null) _searchResults(),
        if (_ask != null || _asking) _askPanel(),
        if (_error != null)
          Padding(
            padding: const EdgeInsets.only(top: 10),
            child: ErrorNotice(
              message: _error!,
              onRetry: () => setState(() => _error = null),
            ),
          ),
        const SizedBox(height: 16),
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(flex: 1, child: _rail()),
            const SizedBox(width: 16),
            Expanded(flex: 2, child: _entityPanel()),
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
                      onTap: () => _select(hit.entity.id),
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
                                'deployment)'),
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
                            onTap: () => _select(_idFromRef(step.ref)!),
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

  // -- the left rail: independent entities only -----------------------------------

  Widget _rail() {
    if (_loadingRail) return const CumulusCard(child: CumulusLoading());
    final business = _rootTypes.where((t) => !_isInfrastructure(t)).toList();
    final infra = _rootTypes.where(_isInfrastructure).toList();
    return CumulusCard(
      padding: const EdgeInsets.fromLTRB(6, 12, 6, 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Padding(
            padding: EdgeInsets.only(left: 14, bottom: 4),
            child: Text('ENTITIES', style: CumulusText.statCaption),
          ),
          for (final t in business) ..._typeSection(t),
          if (infra.any((t) => (_roots[t.name] ?? const []).isNotEmpty)) ...[
            const SizedBox(height: 14),
            const Padding(
              padding: EdgeInsets.only(left: 14, bottom: 4),
              child: Text('INFRASTRUCTURE', style: CumulusText.statCaption),
            ),
            for (final t in infra) ..._typeSection(t, cap: 4),
          ],
        ],
      ),
    );
  }

  List<Widget> _typeSection(OntologyObjectType t, {int cap = 8}) {
    final rows = _roots[t.name] ?? const <GraphObject>[];
    if (rows.isEmpty) return const [];
    final expanded = _expandedTypes.contains(t.name);
    final shown = expanded ? rows : rows.take(cap).toList();
    final parts = t.partTypes;
    return [
      Padding(
        padding: const EdgeInsets.fromLTRB(14, 12, 14, 4),
        child: Row(
          children: [
            Text(
              t.label.toUpperCase(),
              style: CumulusText.statCaption.copyWith(
                color: CumulusColors.ink,
                fontWeight: FontWeight.w700,
              ),
            ),
            const SizedBox(width: 6),
            Text('${rows.length}', style: CumulusText.statCaption),
            if (parts.isNotEmpty) ...[
              const SizedBox(width: 6),
              Tooltip(
                message:
                    'composes ${parts.join(", ")} — parts fold under their '
                    'owner instead of cluttering this list',
                child: Text('▸ ${parts.join(" · ")}',
                    style: CumulusText.statCaption),
              ),
            ],
          ],
        ),
      ),
      for (final row in shown) _railEntityRow(row),
      if (rows.length > cap)
        Padding(
          padding: const EdgeInsets.only(left: 14, top: 2),
          child: RowAction(
            label: expanded ? 'Show fewer' : 'All ${rows.length} →',
            onTap: () => setState(() {
              expanded
                  ? _expandedTypes.remove(t.name)
                  : _expandedTypes.add(t.name);
            }),
          ),
        ),
    ];
  }

  Widget _railEntityRow(GraphObject row) {
    final selected = _detail?.object.id == row.id;
    return InkWell(
      onTap: () => _select(row.id),
      borderRadius: BorderRadius.circular(8),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 7),
        margin: const EdgeInsets.symmetric(horizontal: 4),
        decoration: BoxDecoration(
          color: selected ? CumulusColors.accentSoft : Colors.transparent,
          borderRadius: BorderRadius.circular(8),
        ),
        child: Row(
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    row.label,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                      fontSize: 13,
                      fontWeight: selected ? FontWeight.w600 : FontWeight.w500,
                      color: selected
                          ? CumulusColors.accentInk
                          : CumulusColors.ink,
                    ),
                  ),
                  if (row.naturalKey != row.label)
                    Text(
                      row.naturalKey,
                      overflow: TextOverflow.ellipsis,
                      style: CumulusText.mono.copyWith(
                        fontSize: 10.5,
                        color: CumulusColors.inkMuted,
                      ),
                    ),
                ],
              ),
            ),
            if (row.conflictFields.isNotEmpty)
              const Padding(
                padding: EdgeInsets.only(left: 6),
                child: Icon(Icons.warning_amber_rounded,
                    size: 13, color: CumulusColors.warn),
              ),
          ],
        ),
      ),
    );
  }

  // -- the entity panel --------------------------------------------------------

  Widget _entityPanel() {
    if (_loadingDetail) return const CumulusCard(child: CumulusLoading());
    final detail = _detail;
    if (detail == null) {
      return const CumulusCard(
        child: CumulusEmptyState(
          icon: Icons.hub_outlined,
          title: 'Pick an entity',
          message:
              'Select one on the left, search above, or end a question '
              'with ? to ask the graph.',
        ),
      );
    }
    final grouped = _grouped(detail);
    final owner = _ownerEdge(detail);
    final counts = {
      for (final n in _Nature.values)
        n: grouped[n]!.fold<int>(0, (s, g) => s + g.edges.length),
    };
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _header(detail, owner),
        const SizedBox(height: 12),
        // The floating nature pill: one question at a time — what is it made
        // of, what does it relate to, what do other systems call it.
        Center(
          child: Container(
            padding: const EdgeInsets.all(4),
            decoration: BoxDecoration(
              color: CumulusColors.surface,
              borderRadius: BorderRadius.circular(CumulusRadius.pill),
              border: Border.all(color: CumulusColors.border),
              boxShadow: CumulusShadows.card,
            ),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                for (final n in _Nature.values)
                  _naturePill(n, counts[n] ?? 0),
              ],
            ),
          ),
        ),
        const SizedBox(height: 12),
        switch (_nature) {
          _Nature.contents => _contentsBody(grouped[_Nature.contents]!),
          _Nature.associations =>
            _associationsBody(grouped[_Nature.associations]!),
          _Nature.identity => _identityBody(grouped[_Nature.identity]!),
        },
      ],
    );
  }

  Widget _naturePill(_Nature n, int count) {
    final selected = _nature == n;
    final label = switch (n) {
      _Nature.contents => 'CONTENTS',
      _Nature.associations => 'ASSOCIATIONS',
      _Nature.identity => 'IDENTITY',
    };
    return InkWell(
      onTap: () => setState(() => _nature = n),
      borderRadius: BorderRadius.circular(CumulusRadius.pill),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
        decoration: BoxDecoration(
          color: selected ? CumulusColors.ink : Colors.transparent,
          borderRadius: BorderRadius.circular(CumulusRadius.pill),
        ),
        child: Text(
          '$label · $count',
          style: TextStyle(
            fontSize: 11.5,
            fontWeight: FontWeight.w700,
            letterSpacing: 0.4,
            color: selected ? Colors.white : CumulusColors.inkSecondary,
          ),
        ),
      ),
    );
  }

  Widget _header(GraphObjectDetail detail, GraphEdge? owner) {
    final entity = detail.object;
    final preview = entity.props.entries.take(4).toList();
    return CumulusCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (owner != null)
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Row(
                children: [
                  const Icon(Icons.subdirectory_arrow_right,
                      size: 14, color: CumulusColors.inkMuted),
                  const SizedBox(width: 6),
                  Text('part of ', style: CumulusText.meta),
                  RowAction(
                    label: '${owner.other.type} · ${owner.other.label}',
                    onTap: () => _select(owner.other.id),
                  ),
                ],
              ),
            ),
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
              StatusBadge(
                entity.status,
                tone: entity.status == 'active'
                    ? CumulusTone.success
                    : CumulusTone.warn,
                dense: true,
              ),
              const SizedBox(width: 6),
              MetaPill(entity.ref),
            ],
          ),
          const SizedBox(height: 10),
          Wrap(
            spacing: 14,
            runSpacing: 6,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              for (final e in preview)
                Text.rich(TextSpan(children: [
                  TextSpan(
                      text: '${e.key} ',
                      style: CumulusText.mono.copyWith(fontSize: 11.5)),
                  TextSpan(text: '${e.value}', style: CumulusText.meta),
                ])),
              RowAction(
                label: _fieldsOpen
                    ? 'Hide fields'
                    : 'All ${entity.props.length} fields & claims',
                onTap: () => setState(() => _fieldsOpen = !_fieldsOpen),
              ),
            ],
          ),
          if (_fieldsOpen) ...[
            const SizedBox(height: 10),
            const Divider(height: 1, color: CumulusColors.border),
            for (final e in detail.object.props.entries)
              _fieldRow(detail, e.key, e.value),
          ],
        ],
      ),
    );
  }

  Widget _fieldRow(GraphObjectDetail detail, String field, Object? value) {
    final conflicted = detail.conflicts.containsKey(field);
    final expanded = _expandedField == field;
    final claims = detail.claims.where((c) => c.field == field).toList()
      ..sort((a, b) => b.atIso.compareTo(a.atIso));
    return Column(
      children: [
        InkWell(
          onTap: () =>
              setState(() => _expandedField = expanded ? null : field),
          child: Padding(
            padding: const EdgeInsets.symmetric(vertical: 8),
            child: Row(
              children: [
                SizedBox(
                  width: 190,
                  child: Text(field,
                      style: CumulusText.mono.copyWith(fontSize: 12)),
                ),
                Expanded(child: Text('$value', style: CumulusText.body)),
                if (conflicted)
                  const StatusBadge('sources disagree',
                      tone: CumulusTone.warn, dense: true),
                const SizedBox(width: 8),
                Icon(expanded ? Icons.expand_less : Icons.expand_more,
                    size: 15, color: CumulusColors.inkMuted),
              ],
            ),
          ),
        ),
        if (expanded)
          Container(
            width: double.infinity,
            decoration: BoxDecoration(
              color: CumulusColors.surfaceMuted.withValues(alpha: 0.5),
              borderRadius: BorderRadius.circular(8),
            ),
            padding: const EdgeInsets.fromLTRB(14, 8, 14, 10),
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
                              style: CumulusText.mono
                                  .copyWith(fontSize: 11)),
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

  // -- nature bodies ----------------------------------------------------------

  /// Contents: the entity's parts, shown with their substance. A part has no
  /// page of its own — this is where it lives.
  Widget _contentsBody(List<_EdgeGroup> groups) {
    if (groups.isEmpty) {
      return const CumulusCard(
        child: CumulusEmptyState(
          icon: Icons.inventory_2_outlined,
          title: 'Nothing composed',
          message:
              'This entity has no parts — its substance is its fields.',
        ),
      );
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      spacing: 12,
      children: [
        for (final g in groups)
          ListCard(
            title:
                '${g.edges.length} × ${g.edges.first.other.type}',
            subtitle:
                'via ${g.label} — parts are identified through this owner '
                'and die with it',
            children: [
              for (final e in g.edges) _partRow(e.other),
            ],
          ),
      ],
    );
  }

  Widget _partRow(GraphObject part) {
    final declaredFields = [
      for (final f in _declared(part.type)?.fields ??
          const <Map<String, dynamic>>[])
        '${f['name']}',
    ];
    final shown = declaredFields
        .where((f) => part.props[f] != null)
        .take(5)
        .toList();
    return CumulusListRow(
      leading: _TypeDot(type: part.type),
      title: Row(
        children: [
          Flexible(child: Text(part.label)),
          if (part.conflictFields.isNotEmpty) ...[
            const SizedBox(width: 8),
            StatusBadge('⚠ ${part.conflictFields.join(", ")}',
                tone: CumulusTone.warn, dense: true),
          ],
        ],
      ),
      subtitle: Wrap(
        spacing: 12,
        runSpacing: 4,
        children: [
          for (final f in shown)
            Text('$f ${part.props[f]}', style: CumulusText.meta),
          if (shown.isEmpty)
            Text(part.naturalKey, style: CumulusText.mono),
        ],
      ),
      trailing: [
        Text('rev ${part.revision}', style: CumulusText.statCaption),
      ],
      onTap: () => _select(part.id),
    );
  }

  /// Associations: the independent things this entity relates to. Clicking
  /// one moves the selection — this is how you walk.
  Widget _associationsBody(List<_EdgeGroup> groups) {
    if (groups.isEmpty) {
      return const CumulusCard(
        child: CumulusEmptyState(
          icon: Icons.link_off,
          title: 'No associations',
          message: 'This entity is not related to anything independent yet.',
        ),
      );
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      spacing: 12,
      children: [
        for (final g in groups)
          ListCard(
            title: g.direction == 'out'
                ? '${g.label} → ${g.edges.length} '
                    '${g.edges.first.other.type}${g.edges.length == 1 ? '' : 's'}'
                : '${g.edges.length} '
                    '${g.edges.first.other.type}${g.edges.length == 1 ? '' : 's'} '
                    '→ ${g.label}',
            subtitle: _linkSummary(g.label),
            children: [
              for (final e in g.edges)
                CumulusListRow(
                  leading: _TypeDot(type: e.other.type),
                  title: Text(e.other.label),
                  subtitle: Text(e.other.naturalKey, style: CumulusText.mono),
                  trailing: [
                    if (e.props.isNotEmpty)
                      Text(
                        e.props.entries
                            .take(2)
                            .map((p) => '${p.key} ${p.value}')
                            .join(' · '),
                        style: CumulusText.statCaption,
                      ),
                  ],
                  onTap: () => _select(e.other.id),
                ),
            ],
          ),
      ],
    );
  }

  /// Identity: what other systems call this entity — each row auditable.
  Widget _identityBody(List<_EdgeGroup> groups) {
    if (groups.isEmpty) {
      return const CumulusCard(
        child: CumulusEmptyState(
          icon: Icons.fingerprint,
          title: 'No identity assertions',
          message:
              'No external system\'s record has been resolved to this '
              'entity. Identity links appear when a sync feed lands a row '
              'that matches.',
        ),
      );
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      spacing: 12,
      children: [
        for (final g in groups)
          ListCard(
            title: 'Known to other systems as',
            subtitle:
                'each row is an auditable assertion: the confidence and '
                'method that produced it, reversible by a steward',
            children: [
              for (final e in g.edges)
                CumulusListRow(
                  leading: const Icon(Icons.fingerprint,
                      size: 16, color: CumulusColors.inkMuted),
                  title: Text(e.other.label, style: CumulusText.mono),
                  subtitle: Wrap(
                    spacing: 12,
                    runSpacing: 4,
                    children: [
                      Text('method ${e.method}', style: CumulusText.meta),
                      Text('confidence ${e.confidence.toStringAsFixed(2)}',
                          style: CumulusText.meta),
                      for (final p in e.props.entries.take(2))
                        Text('· ${p.key} ${p.value}', style: CumulusText.meta),
                    ],
                  ),
                  onTap: () => _select(e.other.id),
                ),
            ],
          ),
      ],
    );
  }

  String _linkSummary(String linkType) {
    for (final l in widget.ontology?.links ?? const <OntologyLinkType>[]) {
      if (l.name == linkType) return l.summary;
    }
    return '';
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
