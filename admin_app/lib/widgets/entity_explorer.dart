import 'package:flutter/material.dart';

import '../models/asset.dart' show AskAnswer;
import '../models/data_layer.dart' show DataCollection;
import '../models/graph.dart';
import '../services/api_client.dart';
import '../theme/cumulus_theme.dart';
import 'ui_kit.dart';

/// The Entities & Links lens: the TBox, navigable as a table.
///
/// Deliberately schema-only — no instances. The left third lists the core
/// entity types (independent ones; parts fold under their owners and
/// infrastructure sits below the fold), with a filter box for when the
/// ontology outgrows a screen. Selecting one fills the right two-thirds with
/// a single table: every Link touching that type — the link's label, the
/// related entity, its nature, its cardinality. Clicking a related entity
/// opens an overview popup; from there you can make it the selection and
/// keep walking the schema.
///
/// The question box on top asks the graph (the planner's own tool calls
/// render as the citation); it does not search instances — the ABox has its
/// own screens.
class TBoxExplorer extends StatefulWidget {
  final ApiClient api;
  final Ontology? ontology;

  /// The Collection registry, used to tell business entities from
  /// infrastructure (a landing Collection's types group below the fold).
  final List<DataCollection> collections;

  const TBoxExplorer({
    super.key,
    required this.api,
    required this.ontology,
    this.collections = const [],
  });

  @override
  State<TBoxExplorer> createState() => _TBoxExplorerState();
}

class _TBoxExplorerState extends State<TBoxExplorer> {
  String? _selected;
  final TextEditingController _filterCtl = TextEditingController();
  String _filter = '';
  final Set<String> _activeTags = {};

  final TextEditingController _askCtl = TextEditingController();
  AskAnswer? _ask;
  String _askedQuestion = '';
  bool _asking = false;
  String? _error;

  // -- derived ontology views -------------------------------------------------

  List<OntologyObjectType> get _types =>
      widget.ontology?.objects ?? const <OntologyObjectType>[];

  List<OntologyLinkType> get _links =>
      widget.ontology?.links ?? const <OntologyLinkType>[];

  bool _isInfrastructure(OntologyObjectType t) {
    for (final c in widget.collections) {
      if (c.id == t.collection) return c.kind == 'landing';
    }
    return false;
  }

  OntologyObjectType? _declared(String name) {
    for (final t in _types) {
      if (t.name == name) return t;
    }
    return null;
  }

  bool _matchesFilter(OntologyObjectType t) {
    if (_activeTags.isNotEmpty &&
        !_activeTags.every((tag) => t.tags.contains(tag))) {
      return false;
    }
    if (_filter.isEmpty) return true;
    final q = _filter.toLowerCase();
    return t.label.toLowerCase().contains(q) ||
        t.name.toLowerCase().contains(q) ||
        t.tags.any((tag) => tag.contains(q));
  }

  @override
  void initState() {
    super.initState();
    // Land on the first core type with something declared against it.
    final core = _types.where((t) => !t.isPart && !_isInfrastructure(t));
    _selected = core.isNotEmpty ? core.first.name : null;
  }

  @override
  void dispose() {
    _filterCtl.dispose();
    _askCtl.dispose();
    super.dispose();
  }

  Future<void> _runAsk() async {
    final q = _askCtl.text.trim();
    if (q.isEmpty) return;
    setState(() {
      _asking = true;
      _askedQuestion = q;
      _ask = null;
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

  // -- build --------------------------------------------------------------------

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _askBar(),
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
            Expanded(flex: 2, child: _linkTable()),
          ],
        ),
      ],
    );
  }

  Widget _askBar() {
    return Row(
      children: [
        Expanded(
          child: TextField(
            controller: _askCtl,
            onSubmitted: (_) => _runAsk(),
            decoration: InputDecoration(
              isDense: true,
              prefixIcon: const Icon(Icons.route_outlined, size: 18),
              hintText:
                  'Ask the graph a question — the planner\'s traversal is '
                  'the citation',
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

  // -- left rail: core entity types ----------------------------------------------

  Widget _rail() {
    // Tags are facets, not sections: the list stays flat, and the chips
    // below the filter box narrow it. Selecting several tags means "all of
    // these"; a type carries many tags, so Employee answers to `people` and
    // to `expenses` without appearing twice.
    final core = _types
        .where((t) => !t.isPart && !_isInfrastructure(t) && _matchesFilter(t))
        .toList();
    final infra = _types
        .where((t) => !t.isPart && _isInfrastructure(t) && _matchesFilter(t))
        .toList();
    // The facet vocabulary, with how many core types answer to each.
    final tagCounts = <String, int>{};
    for (final t in _types.where((t) => !t.isPart && !_isInfrastructure(t))) {
      for (final tag in t.tags) {
        tagCounts[tag] = (tagCounts[tag] ?? 0) + 1;
      }
    }
    final tagOrder = tagCounts.keys.toList()
      ..sort((a, b) {
        final c = tagCounts[b]!.compareTo(tagCounts[a]!);
        return c != 0 ? c : a.compareTo(b);
      });
    return CumulusCard(
      padding: EdgeInsets.zero,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 12, 12, 6),
            child: TextField(
              controller: _filterCtl,
              onChanged: (v) => setState(() => _filter = v.trim()),
              decoration: InputDecoration(
                isDense: true,
                prefixIcon: const Icon(Icons.filter_list, size: 16),
                hintText: 'Filter by name or tag…',
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(CumulusRadius.control),
                  borderSide: const BorderSide(color: CumulusColors.border),
                ),
              ),
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 0, 12, 8),
            child: Wrap(
              spacing: 6,
              runSpacing: 6,
              children: [
                for (final tag in tagOrder)
                  InkWell(
                    onTap: () => setState(() {
                      _activeTags.contains(tag)
                          ? _activeTags.remove(tag)
                          : _activeTags.add(tag);
                    }),
                    borderRadius:
                        BorderRadius.circular(CumulusRadius.pill),
                    child: StatusBadge(
                      '$tag · ${tagCounts[tag]}',
                      tone: _activeTags.contains(tag)
                          ? CumulusTone.accent
                          : CumulusTone.neutral,
                      dense: true,
                    ),
                  ),
              ],
            ),
          ),
          const Padding(
            padding: EdgeInsets.fromLTRB(16, 4, 16, 4),
            child: Text('CORE ENTITIES', style: CumulusText.statCaption),
          ),
          for (final t in core) _railRow(t),
          if (infra.isNotEmpty) ...[
            const Padding(
              padding: EdgeInsets.fromLTRB(16, 12, 16, 4),
              child: Text('INFRASTRUCTURE', style: CumulusText.statCaption),
            ),
            for (final t in infra) _railRow(t),
          ],
          if (core.isEmpty && infra.isEmpty)
            const Padding(
              padding: EdgeInsets.all(16),
              child: Text('Nothing matches.', style: CumulusText.meta),
            ),
          const SizedBox(height: 8),
        ],
      ),
    );
  }

  Widget _railRow(OntologyObjectType t) {
    final selected = _selected == t.name;
    return InkWell(
      onTap: () => setState(() => _selected = t.name),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
        decoration: BoxDecoration(
          color: selected ? CumulusColors.accentSoft : Colors.transparent,
          border: const Border(
            bottom: BorderSide(color: CumulusColors.border, width: 0.5),
          ),
        ),
        child: Row(
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    t.label,
                    style: TextStyle(
                      fontSize: 13.5,
                      fontWeight: selected ? FontWeight.w700 : FontWeight.w500,
                      color: selected
                          ? CumulusColors.accentInk
                          : CumulusColors.ink,
                    ),
                  ),
                  if (t.partTypes.isNotEmpty)
                    Text(
                      '▸ ${t.partTypes.join(" · ")}',
                      style: CumulusText.statCaption,
                    ),
                ],
              ),
            ),
            if (t.authoritative)
              const Padding(
                padding: EdgeInsets.only(right: 8),
                child: Tooltip(
                  message: 'Cumulus is the system of record for this type',
                  child: Icon(Icons.verified_outlined,
                      size: 14, color: CumulusColors.success),
                ),
              ),
            Text('${t.instances}', style: CumulusText.statCaption),
          ],
        ),
      ),
    );
  }

  // -- the link table --------------------------------------------------------------

  /// Every Link touching the selected type, one row each — the schema as a
  /// table you read rather than a diagram you decode.
  Widget _linkTable() {
    final t = _declared(_selected ?? '');
    if (t == null) {
      return const CumulusCard(
        child: CumulusEmptyState(
          icon: Icons.hub_outlined,
          title: 'Pick an entity',
          message: 'Select a core entity on the left to see its links.',
        ),
      );
    }
    final rows = <_LinkRow>[
      for (final l in _links)
        if (l.fromType == t.name)
          _LinkRow(link: l, outbound: true, other: l.toType),
      for (final l in _links)
        if (l.toType == t.name && l.fromType != t.name)
          _LinkRow(link: l, outbound: false, other: l.fromType),
    ]..sort((a, b) {
        // Contents first, then associations, identity last — story order.
        int weight(_LinkRow r) => switch (r.link.nature) {
          'composition' => 0,
          'identity' => 2,
          _ => 1,
        };
        final w = weight(a).compareTo(weight(b));
        return w != 0 ? w : a.link.name.compareTo(b.link.name);
      });

    return CumulusCard(
      padding: EdgeInsets.zero,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // Header: the selected type's own declaration.
          Padding(
            padding: const EdgeInsets.fromLTRB(20, 18, 20, 14),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Expanded(
                      child: SectionHeader(
                        title: t.label,
                        subtitle: t.summary,
                      ),
                    ),
                    for (final tag in t.tags) ...[
                      StatusBadge(tag, dense: true),
                      const SizedBox(width: 6),
                    ],
                    if (t.authoritative)
                      const StatusBadge('authoritative',
                          tone: CumulusTone.success, dense: true),
                  ],
                ),
                const SizedBox(height: 6),
                Wrap(
                  spacing: 14,
                  runSpacing: 4,
                  children: [
                    if (t.collection.isNotEmpty)
                      Text('collection ${t.collection}',
                          style: CumulusText.meta),
                    Text('· ${t.instances} in the graph',
                        style: CumulusText.meta),
                  ],
                ),
              ],
            ),
          ),
          // FIELDS — a fixed part of the view, not a popup. The schema of a
          // type is small and central; hiding it was wrong.
          _sectionStrip('FIELDS · queryable metadata'),
          _fieldTableRow(
            leading: const Icon(Icons.key, size: 13,
                color: CumulusColors.warn),
            name: 'natural key',
            kind: Text(t.naturalKey, style: CumulusText.meta),
            note:
                'how records find this entity — identity is a minted uid, so '
                'the key can change without breaking references',
          ),
          for (final f in t.fields)
            _fieldTableRow(
              name: '${f['name']}',
              kind: _kindCell('${f['kind']}'),
              note: '${f['note'] ?? ''}',
            ),
          // LINKS — column headers.
          _sectionStrip('LINKS · how this entity relates'),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 9),
            decoration: const BoxDecoration(
              color: CumulusColors.surfaceMuted,
              border: Border(
                top: BorderSide(color: CumulusColors.border),
                bottom: BorderSide(color: CumulusColors.border),
              ),
            ),
            child: const Row(
              children: [
                SizedBox(
                    width: 190,
                    child: Text('LINK', style: CumulusText.statCaption)),
                SizedBox(
                    width: 180,
                    child: Text('RELATED ENTITY',
                        style: CumulusText.statCaption)),
                SizedBox(
                    width: 110,
                    child:
                        Text('NATURE', style: CumulusText.statCaption)),
                SizedBox(
                    width: 110,
                    child: Text('CARDINALITY',
                        style: CumulusText.statCaption)),
                Expanded(
                    child: Text('ABOUT', style: CumulusText.statCaption)),
              ],
            ),
          ),
          if (rows.isEmpty)
            const Padding(
              padding: EdgeInsets.all(20),
              child: Text('No links declared for this type.',
                  style: CumulusText.meta),
            ),
          for (final r in rows) _tableRow(t, r),
        ],
      ),
    );
  }

  Widget _sectionStrip(String label) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 7),
      decoration: const BoxDecoration(
        color: CumulusColors.surfaceMuted,
        border: Border(
          top: BorderSide(color: CumulusColors.border),
        ),
      ),
      child: Text(label,
          style: CumulusText.statCaption.copyWith(
            color: CumulusColors.ink,
            fontWeight: FontWeight.w700,
          )),
    );
  }

  /// One field row. A `ref:` kind renders as a typed chip — an observation
  /// awaiting promotion to a Link, clickable to preview the target type.
  Widget _fieldTableRow({
    required String name,
    required Widget kind,
    required String note,
    Widget? leading,
  }) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 8),
      decoration: const BoxDecoration(
        border: Border(
          bottom: BorderSide(color: CumulusColors.border, width: 0.5),
        ),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 190,
            child: Row(
              children: [
                if (leading != null) ...[leading, const SizedBox(width: 6)],
                Flexible(
                  child: Text(name,
                      style: CumulusText.mono.copyWith(fontSize: 12)),
                ),
              ],
            ),
          ),
          SizedBox(width: 180, child: kind),
          Expanded(child: Text(note, style: CumulusText.meta)),
        ],
      ),
    );
  }

  Widget _kindCell(String kind) {
    if (!kind.startsWith('ref:')) {
      return Text(kind, style: CumulusText.meta);
    }
    final target = kind.substring(4);
    return Align(
      alignment: Alignment.centerLeft,
      child: InkWell(
        onTap: () => _showOverview(target),
        borderRadius: BorderRadius.circular(CumulusRadius.pill),
        child: StatusBadge('ref → $target',
            tone: CumulusTone.violet, dense: true),
      ),
    );
  }

  Widget _tableRow(OntologyObjectType selected, _LinkRow r) {
    final other = _declared(r.other);
    final natureTone = switch (r.link.nature) {
      'composition' => CumulusTone.violet,
      'identity' => CumulusTone.teal,
      _ => CumulusTone.neutral,
    };
    final natureLabel = switch (r.link.nature) {
      'composition' => 'part',
      'identity' => 'identity',
      _ => 'association',
    };
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 11),
      decoration: const BoxDecoration(
        border: Border(
          bottom: BorderSide(color: CumulusColors.border, width: 0.5),
        ),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 190,
            child: Row(
              children: [
                Icon(
                  r.outbound ? Icons.arrow_forward : Icons.arrow_back,
                  size: 13,
                  color: CumulusColors.inkMuted,
                ),
                const SizedBox(width: 6),
                Flexible(
                  child: Text(r.link.name,
                      style: CumulusText.mono.copyWith(fontSize: 12)),
                ),
              ],
            ),
          ),
          SizedBox(
            width: 180,
            child: Align(
              alignment: Alignment.centerLeft,
              child: InkWell(
                onTap: () => _showOverview(r.other),
                borderRadius: BorderRadius.circular(6),
                child: Padding(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 4, vertical: 2),
                  child: Text(
                    other?.label ?? r.other,
                    style: const TextStyle(
                      fontSize: 13,
                      fontWeight: FontWeight.w600,
                      color: CumulusColors.accentInk,
                      decoration: TextDecoration.underline,
                      decorationColor: CumulusColors.accentSoft,
                    ),
                  ),
                ),
              ),
            ),
          ),
          SizedBox(
            width: 110,
            child: Align(
              alignment: Alignment.centerLeft,
              child: StatusBadge(natureLabel, tone: natureTone, dense: true),
            ),
          ),
          SizedBox(
            width: 110,
            child: Text(r.link.cardinality,
                style: CumulusText.mono.copyWith(fontSize: 11)),
          ),
          Expanded(
            child: Text(r.link.summary, style: CumulusText.meta),
          ),
        ],
      ),
    );
  }

  // -- the overview popup -----------------------------------------------------------

  void _showOverview(String typeName) {
    final t = _declared(typeName);
    if (t == null) return;
    showDialog<void>(
      context: context,
      builder: (context) => Dialog(
        backgroundColor: CumulusColors.surface,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(CumulusRadius.card),
        ),
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 620, maxHeight: 640),
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Expanded(
                      child: SectionHeader(
                        title: t.label,
                        subtitle: t.summary,
                      ),
                    ),
                    if (t.isPart)
                      StatusBadge('part of ${t.ownerType}',
                          tone: CumulusTone.violet, dense: true)
                    else if (t.authoritative)
                      const StatusBadge('authoritative',
                          tone: CumulusTone.success, dense: true),
                    IconButton(
                      icon: const Icon(Icons.close, size: 16),
                      onPressed: () => Navigator.of(context).pop(),
                    ),
                  ],
                ),
                const SizedBox(height: 8),
                Wrap(
                  spacing: 14,
                  runSpacing: 4,
                  children: [
                    Text('natural key: ${t.naturalKey}',
                        style: CumulusText.meta),
                    if (t.collection.isNotEmpty)
                      Text('· collection ${t.collection}',
                          style: CumulusText.meta),
                    Text('· ${t.instances} in the graph',
                        style: CumulusText.meta),
                    if (t.partTypes.isNotEmpty)
                      Text('· composes ${t.partTypes.join(", ")}',
                          style: CumulusText.meta),
                  ],
                ),
                const SizedBox(height: 14),
                const Text('DECLARED FIELDS', style: CumulusText.statCaption),
                const SizedBox(height: 6),
                Flexible(
                  child: SingleChildScrollView(
                    child: Column(
                      children: [
                        for (final f in t.fields)
                          Padding(
                            padding: const EdgeInsets.symmetric(vertical: 4),
                            child: Row(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                SizedBox(
                                  width: 170,
                                  child: Text('${f['name']}',
                                      style: CumulusText.mono
                                          .copyWith(fontSize: 12)),
                                ),
                                SizedBox(
                                  width: 110,
                                  child: _kindCell('${f['kind']}'),
                                ),
                                Expanded(
                                  child: Text('${f['note'] ?? ''}',
                                      style: CumulusText.meta),
                                ),
                              ],
                            ),
                          ),
                        if (t.fields.isEmpty)
                          const Align(
                            alignment: Alignment.centerLeft,
                            child: Text('No fields declared.',
                                style: CumulusText.meta),
                          ),
                      ],
                    ),
                  ),
                ),
                const SizedBox(height: 16),
                Row(
                  mainAxisAlignment: MainAxisAlignment.end,
                  children: [
                    // A part has no rail entry — it lives inside its owner —
                    // so exploration continues from the owner instead.
                    if (t.isPart)
                      OutlinedButton(
                        onPressed: () {
                          Navigator.of(context).pop();
                          setState(() => _selected = t.ownerType);
                        },
                        child: Text('Explore its owner (${t.ownerType})'),
                      )
                    else
                      FilledButton(
                        onPressed: () {
                          Navigator.of(context).pop();
                          setState(() => _selected = t.name);
                        },
                        child: const Text('Explore this entity'),
                      ),
                  ],
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _LinkRow {
  final OntologyLinkType link;
  final bool outbound;
  final String other;

  const _LinkRow({
    required this.link,
    required this.outbound,
    required this.other,
  });
}
