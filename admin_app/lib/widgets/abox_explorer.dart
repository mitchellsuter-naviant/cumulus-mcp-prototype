import 'package:flutter/material.dart';

import '../models/asset.dart' show AskAnswer;
import '../models/graph.dart';
import '../models/world.dart';
import '../services/api_client.dart';
import '../theme/cumulus_theme.dart';
import 'tbox_graph.dart';
import 'ui_kit.dart';

/// The ABox explorer: one explorer over any world.
///
/// The world selector is the point — LIVE is just the first world in the
/// list. An eval fixture materializes into the same shape the live graph
/// projects into, so the world a test constructs is inspectable with exactly
/// the tooling production gets: same rail, same dossier, same graph view.
///
/// Natural language rides on top, deliberately not as a chat box. The primary
/// affordance is the selected type's declared competency questions with the
/// selected instance's label substituted in — one-tap asks that the planner
/// reliably answers — plus a free question bar. Every answer renders THE WALK
/// IT TOOK, and each step that landed on an entity is a click that selects it
/// here: the answer parks you at the relevant subgraph. Asking is only
/// offered on the live world, because that is the graph the planner walks —
/// a fragment is an isolated world it cannot see.
class ABoxExplorer extends StatefulWidget {
  final ApiClient api;
  final Ontology? ontology;

  const ABoxExplorer({super.key, required this.api, required this.ontology});

  @override
  State<ABoxExplorer> createState() => _ABoxExplorerState();
}

enum _View { table, graph }

class _ABoxExplorerState extends State<ABoxExplorer> {
  static const _live = 'live';

  List<AuthoredWorld> _worlds = const [];
  String _worldId = _live;
  MaterializedWorld? _world;
  bool _loadingWorld = true;

  String? _type;
  String? _instanceId;
  final TextEditingController _filterCtl = TextEditingController();
  String _filter = '';
  _View _view = _View.table;
  String? _expandedField;

  final TextEditingController _askCtl = TextEditingController();
  AskAnswer? _ask;
  String _askedQuestion = '';
  bool _asking = false;

  String? _error;

  // -- ontology helpers ---------------------------------------------------------

  OntologyObjectType? _declared(String name) {
    for (final t in widget.ontology?.objects ?? const <OntologyObjectType>[]) {
      if (t.name == name) return t;
    }
    return null;
  }

  String _natureOf(String linkType) {
    for (final l in widget.ontology?.links ?? const <OntologyLinkType>[]) {
      if (l.name == linkType) return l.nature;
    }
    return 'association';
  }

  List<OntologyObjectType> get _rootTypes {
    final types = [
      for (final t
          in widget.ontology?.objects ?? const <OntologyObjectType>[])
        if (!t.isPart) t,
    ]..sort((a, b) => a.label.toLowerCase().compareTo(b.label.toLowerCase()));
    return types;
  }

  // -- world loading --------------------------------------------------------------

  @override
  void initState() {
    super.initState();
    _loadWorlds();
  }

  @override
  void dispose() {
    _filterCtl.dispose();
    _askCtl.dispose();
    super.dispose();
  }

  Future<void> _loadWorlds() async {
    try {
      final worlds = await widget.api.listWorlds();
      if (mounted) setState(() => _worlds = worlds);
    } on ApiException {
      // The selector just shows LIVE; the fragments are a bonus.
    }
    await _loadWorld(_live);
  }

  Future<void> _loadWorld(String id) async {
    setState(() {
      _worldId = id;
      _loadingWorld = true;
      _error = null;
      _instanceId = null;
      _ask = null;
      _askedQuestion = '';
    });
    try {
      final world = id == _live
          ? await widget.api.graphInstances(limit: 400)
          : await widget.api.materializeWorld(id);
      if (!mounted) return;
      setState(() {
        _world = world;
        _loadingWorld = false;
        // Land on the busiest core type this world actually has.
        final counts = world.byType;
        final candidates = _rootTypes
            .where((t) => (counts[t.name] ?? 0) > 0)
            .toList()
          ..sort((a, b) =>
              (counts[b.name] ?? 0).compareTo(counts[a.name] ?? 0));
        _type = candidates.any((t) => t.name == 'Employee')
            ? 'Employee'
            : (candidates.isNotEmpty ? candidates.first.name : null);
      });
    } on ApiException catch (e) {
      if (mounted) {
        setState(() {
          _error = e.message;
          _loadingWorld = false;
        });
      }
    }
  }

  // -- world queries (all client-side: the payload is the world) -------------------

  List<WorldObject> _instancesOf(String type) {
    final world = _world;
    if (world == null) return const [];
    final q = _filter.toLowerCase();
    return [
      for (final o in world.objects)
        if (o.type == type &&
            (q.isEmpty ||
                o.label.toLowerCase().contains(q) ||
                o.naturalKey.toLowerCase().contains(q)))
          o,
    ];
  }

  WorldObject? _object(String id) {
    for (final o in _world?.objects ?? const <WorldObject>[]) {
      if (o.id == id) return o;
    }
    return null;
  }

  /// Live edges touching an instance, with the neighbour resolved.
  List<(WorldLink, WorldObject, bool outbound)> _edges(String id) {
    final out = <(WorldLink, WorldObject, bool)>[];
    for (final l in _world?.links ?? const <WorldLink>[]) {
      if (!l.live) continue;
      if (l.fromId == id) {
        final other = _object(l.toId);
        if (other != null) out.add((l, other, true));
      } else if (l.toId == id) {
        final other = _object(l.fromId);
        if (other != null) out.add((l, other, false));
      }
    }
    return out;
  }

  // -- ask ---------------------------------------------------------------------------

  Future<void> _runAsk(String question) async {
    if (question.trim().isEmpty) return;
    setState(() {
      _asking = true;
      _askedQuestion = question.trim();
      _ask = null;
    });
    try {
      final answer = await widget.api.ask(question.trim());
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

  /// `graph:Vendor/vendor_ab12cd@rev3` → select that entity here, if this
  /// world holds it.
  void _openRef(String ref) {
    if (!ref.startsWith('graph:')) return;
    final slash = ref.indexOf('/');
    if (slash < 0) return;
    final at = ref.indexOf('@', slash);
    final id = at < 0 ? ref.substring(slash + 1) : ref.substring(slash + 1, at);
    final obj = _object(id);
    if (obj == null) return;
    setState(() {
      _type = _declared(obj.type)?.isPart ?? false
          ? _declared(obj.type)!.ownerType
          : obj.type;
      _instanceId = obj.id;
      _expandedField = null;
    });
  }

  void _select(WorldObject obj) {
    setState(() {
      _instanceId = obj.id;
      _expandedField = null;
    });
  }

  // -- build --------------------------------------------------------------------------

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _worldBar(),
        const SizedBox(height: 12),
        if (_worldId == _live) ...[
          _askBar(),
          if (_ask != null || _asking) ...[
            const SizedBox(height: 12),
            _askPanel(),
          ],
          const SizedBox(height: 12),
        ] else ...[
          _fragmentBanner(),
          const SizedBox(height: 12),
        ],
        if (_error != null) ...[
          ErrorNotice(
            message: _error!,
            onRetry: () => _loadWorld(_worldId),
          ),
          const SizedBox(height: 12),
        ],
        Expanded(
          child: _loadingWorld
              ? const CumulusCard(child: CumulusLoading())
              : Row(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Expanded(flex: 1, child: _rail()),
                    const SizedBox(width: 16),
                    Expanded(flex: 2, child: _instancePane()),
                  ],
                ),
        ),
      ],
    );
  }

  // -- world bar ----------------------------------------------------------------------

  Widget _worldBar() {
    return Row(
      children: [
        const Text('WORLD', style: CumulusText.statCaption),
        const SizedBox(width: 10),
        Expanded(
          child: SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: Row(
              children: [
                LensChip(
                  label: 'LIVE',
                  selected: _worldId == _live,
                  onTap: () => _loadWorld(_live),
                ),
                for (final w in _worlds) ...[
                  const SizedBox(width: 8),
                  LensChip(
                    label: w.name.toUpperCase(),
                    selected: _worldId == w.id,
                    onTap: () => _loadWorld(w.id),
                  ),
                ],
              ],
            ),
          ),
        ),
      ],
    );
  }

  Widget _fragmentBanner() {
    final world = _world;
    if (world == null) return const SizedBox.shrink();
    final runs = world.runs.length;
    final tasks = world.tasks.length;
    return CumulusCard(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
      child: Row(
        children: [
          const Icon(Icons.science_outlined, size: 15,
              color: CumulusColors.inkMuted),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              '${world.world.summary}  ·  Materialized into a throwaway '
              'world: establishing it drove $runs sync run(s)'
              '${tasks > 0 ? ' and raised $tasks task(s)' : ''}. Asking is '
              'available on LIVE — the planner cannot see an isolated world.',
              style: CumulusText.meta,
            ),
          ),
          if (world.violations.isNotEmpty)
            StatusBadge('${world.violations.length} violation(s)',
                tone: CumulusTone.danger, dense: true)
          else
            const StatusBadge('conforms',
                tone: CumulusTone.success, dense: true),
        ],
      ),
    );
  }

  // -- ask ------------------------------------------------------------------------------

  Widget _askBar() {
    // The declared questions for the selected type, with the selected
    // instance's label substituted — the ontology's own admission test,
    // offered back as one-tap asks.
    final declared = _declared(_type ?? '');
    final instance = _instanceId == null ? null : _object(_instanceId!);
    final chips = <String>[
      for (final q in declared?.competencyQuestions ?? const <String>[])
        if (instance != null || !q.contains('{label}'))
          q.replaceAll('{label}', instance?.label ?? ''),
    ];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Expanded(
              child: TextField(
                controller: _askCtl,
                onSubmitted: _runAsk,
                decoration: InputDecoration(
                  isDense: true,
                  prefixIcon: const Icon(Icons.route_outlined, size: 18),
                  hintText:
                      'Ask the graph — the planner\'s traversal is the '
                      'citation',
                  border: OutlineInputBorder(
                    borderRadius:
                        BorderRadius.circular(CumulusRadius.control),
                    borderSide:
                        const BorderSide(color: CumulusColors.border),
                  ),
                ),
              ),
            ),
            const SizedBox(width: 10),
            FilledButton.icon(
              onPressed: _asking ? null : () => _runAsk(_askCtl.text),
              icon: const Icon(Icons.route_outlined, size: 16),
              label: const Text('Ask'),
            ),
          ],
        ),
        if (chips.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Wrap(
              spacing: 6,
              runSpacing: 6,
              children: [
                for (final q in chips)
                  InkWell(
                    onTap: _asking ? null : () => _runAsk(q),
                    borderRadius:
                        BorderRadius.circular(CumulusRadius.pill),
                    child: StatusBadge(q, dense: true),
                  ),
              ],
            ),
          ),
      ],
    );
  }

  Widget _askPanel() {
    return CumulusCard(
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
                          ? 'answered by the planner — its tool calls are '
                              'the citation, and each step is a click'
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
                      if (step.ref.startsWith('graph:'))
                        RowAction(
                          label: 'Open here',
                          onTap: () => _openRef(step.ref),
                        ),
                    ],
                  ),
                ),
            ],
          ],
        ],
      ),
    );
  }

  // -- rail: types, then instances -------------------------------------------------------

  Widget _rail() {
    final world = _world;
    if (world == null) return const CumulusCard(child: CumulusLoading());
    final counts = world.byType;
    return CumulusCard(
      padding: EdgeInsets.zero,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 12, 12, 6),
            child: TextField(
              controller: _filterCtl,
              onChanged: (v) => setState(() => _filter = v.trim()),
              decoration: InputDecoration(
                isDense: true,
                prefixIcon: const Icon(Icons.filter_list, size: 16),
                hintText: 'Filter instances…',
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(CumulusRadius.control),
                  borderSide: const BorderSide(color: CumulusColors.border),
                ),
              ),
            ),
          ),
          Expanded(
            child: SingleChildScrollView(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  for (final t in _rootTypes)
                    if ((counts[t.name] ?? 0) > 0) ...[
                      InkWell(
                        onTap: () => setState(() {
                          _type = t.name;
                          _instanceId = null;
                        }),
                        child: Container(
                          padding: const EdgeInsets.symmetric(
                              horizontal: 16, vertical: 8),
                          color: _type == t.name
                              ? CumulusColors.surfaceMuted
                              : Colors.transparent,
                          child: Row(
                            children: [
                              Expanded(
                                child: Text(
                                  t.label.toUpperCase(),
                                  style: CumulusText.statCaption.copyWith(
                                    color: _type == t.name
                                        ? CumulusColors.ink
                                        : CumulusColors.inkMuted,
                                    fontWeight: FontWeight.w700,
                                  ),
                                ),
                              ),
                              Text('${counts[t.name]}',
                                  style: CumulusText.statCaption),
                            ],
                          ),
                        ),
                      ),
                      if (_type == t.name)
                        for (final o in _instancesOf(t.name))
                          _railInstance(o),
                    ],
                  const SizedBox(height: 8),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _railInstance(WorldObject o) {
    final selected = _instanceId == o.id;
    return InkWell(
      onTap: () => _select(o),
      child: Container(
        padding: const EdgeInsets.fromLTRB(24, 7, 16, 7),
        color: selected ? CumulusColors.accentSoft : Colors.transparent,
        child: Row(
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    o.label,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                      fontSize: 13,
                      fontWeight:
                          selected ? FontWeight.w600 : FontWeight.w400,
                      color: selected
                          ? CumulusColors.accentInk
                          : CumulusColors.ink,
                    ),
                  ),
                  if (o.naturalKey != o.label)
                    Text(
                      o.naturalKey,
                      overflow: TextOverflow.ellipsis,
                      style: CumulusText.mono.copyWith(
                          fontSize: 10.5, color: CumulusColors.inkMuted),
                    ),
                ],
              ),
            ),
            if (o.conflictFields.isNotEmpty)
              const Icon(Icons.warning_amber_rounded,
                  size: 13, color: CumulusColors.warn),
          ],
        ),
      ),
    );
  }

  // -- the instance pane -------------------------------------------------------------------

  Widget _instancePane() {
    final instance = _instanceId == null ? null : _object(_instanceId!);
    if (instance == null) {
      return const CumulusCard(
        child: CumulusEmptyState(
          icon: Icons.hub_outlined,
          title: 'Pick an instance',
          message:
              'Select an entity on the left — or ask a question above and '
              'open a step of the walk.',
        ),
      );
    }
    final edges = _edges(instance.id);
    return CumulusCard(
      padding: EdgeInsets.zero,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(20, 16, 20, 12),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Expanded(
                      child: SectionHeader(
                        title: instance.label,
                        subtitle: instance.naturalKey,
                      ),
                    ),
                    StatusBadge(instance.type, dense: true),
                    const SizedBox(width: 6),
                    StatusBadge(
                      instance.status,
                      tone: instance.status == 'active'
                          ? CumulusTone.success
                          : CumulusTone.warn,
                      dense: true,
                    ),
                    const SizedBox(width: 10),
                    for (final v in _View.values) ...[
                      LensChip(
                        label: v == _View.table ? 'TABLE' : 'GRAPH',
                        selected: _view == v,
                        onTap: () => setState(() => _view = v),
                      ),
                      const SizedBox(width: 6),
                    ],
                  ],
                ),
              ],
            ),
          ),
          const Divider(height: 1, thickness: 1, color: CumulusColors.border),
          if (_view == _View.graph)
            Expanded(child: _instanceGraph(instance, edges))
          else
            Expanded(
              child: SingleChildScrollView(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    _strip('FIELDS · resolved under precedence — click one '
                        'for the claims behind it'),
                    for (final e in instance.props.entries)
                      _fieldRow(instance, e.key, e.value),
                    _strip('LINKS · everything attached to this instance'),
                    if (edges.isEmpty)
                      const Padding(
                        padding: EdgeInsets.all(20),
                        child: Text('No links yet.', style: CumulusText.meta),
                      ),
                    for (final (link, other, outbound) in edges)
                      _edgeRow(link, other, outbound),
                  ],
                ),
              ),
            ),
        ],
      ),
    );
  }

  Widget _strip(String label) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 7),
      decoration: const BoxDecoration(
        color: CumulusColors.surfaceMuted,
        border: Border(top: BorderSide(color: CumulusColors.border)),
      ),
      child: Text(label,
          style: CumulusText.statCaption
              .copyWith(color: CumulusColors.ink, fontWeight: FontWeight.w700)),
    );
  }

  Widget _fieldRow(WorldObject instance, String field, Object? value) {
    final conflicted = instance.conflictFields.contains(field);
    final expanded = _expandedField == field;
    final claims =
        instance.claims.where((c) => c.field == field).toList();
    return Column(
      children: [
        InkWell(
          onTap: () =>
              setState(() => _expandedField = expanded ? null : field),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 8),
            child: Row(
              children: [
                SizedBox(
                  width: 180,
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
            color: CumulusColors.surfaceMuted.withValues(alpha: 0.5),
            padding: const EdgeInsets.fromLTRB(20, 8, 20, 10),
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
                          claim.resolves
                              ? Icons.check_circle_outline
                              : Icons.remove_circle_outline,
                          size: 14,
                          color: claim.resolves
                              ? CumulusColors.success
                              : CumulusColors.inkMuted,
                        ),
                        Text('${claim.value}',
                            style: CumulusText.body.copyWith(
                              fontWeight: claim.resolves
                                  ? FontWeight.w600
                                  : FontWeight.w400,
                            )),
                        Text('said by ${claim.source}',
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

  Widget _edgeRow(WorldLink link, WorldObject other, bool outbound) {
    final nature = _natureOf(link.type);
    final tone = switch (nature) {
      'composition' => CumulusTone.violet,
      'identity' => CumulusTone.teal,
      _ => CumulusTone.neutral,
    };
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 9),
      decoration: const BoxDecoration(
        border: Border(
          bottom: BorderSide(color: CumulusColors.border, width: 0.5),
        ),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 180,
            child: Row(
              children: [
                Icon(outbound ? Icons.arrow_forward : Icons.arrow_back,
                    size: 13, color: CumulusColors.inkMuted),
                const SizedBox(width: 6),
                Flexible(
                  child: Text(link.type,
                      style: CumulusText.mono.copyWith(fontSize: 12)),
                ),
              ],
            ),
          ),
          SizedBox(
            width: 90,
            child: StatusBadge(
              nature == 'composition' ? 'part' : nature,
              tone: tone,
              dense: true,
            ),
          ),
          Expanded(
            child: InkWell(
              onTap: () => _select(other),
              child: Text(
                other.label,
                style: const TextStyle(
                  fontSize: 13,
                  fontWeight: FontWeight.w600,
                  color: CumulusColors.accentInk,
                ),
              ),
            ),
          ),
          if (link.props.isNotEmpty)
            Expanded(
              child: Text(
                link.props.entries
                    .map((e) => '${e.key} ${e.value}')
                    .join(' · '),
                style: CumulusText.meta,
              ),
            ),
        ],
      ),
    );
  }

  // -- the instance graph ----------------------------------------------------------------

  Widget _instanceGraph(
    WorldObject instance,
    List<(WorldLink, WorldObject, bool)> edges,
  ) {
    // Group by (link type, direction, neighbour type): ten expense requests
    // are one spoke saying "10 × Expense request", tapped open to pick one.
    final groups =
        <String, List<(WorldLink, WorldObject, bool)>>{};
    for (final e in edges) {
      final key = '${e.$1.type}:${e.$3}:${e.$2.type}';
      groups.putIfAbsent(key, () => []).add(e);
    }
    final spokes = <GraphSpoke>[];
    final byKey = <String, List<(WorldLink, WorldObject, bool)>>{};
    for (final entry in groups.entries) {
      final sample = entry.value.first;
      final many = entry.value.length > 1;
      final typeLabel = _declared(sample.$2.type)?.label ?? sample.$2.type;
      spokes.add(GraphSpoke(
        type: entry.key,
        label: many
            ? '${entry.value.length} × $typeLabel'
            : sample.$2.label,
        instances: entry.value.length,
        isPart: _natureOf(sample.$1.type) == 'composition',
        subtitle: many
            ? 'tap to pick one'
            : (sample.$2.naturalKey == sample.$2.label
                ? typeLabel
                : sample.$2.naturalKey),
        edges: [
          SpokeEdge(
            name: sample.$1.type,
            outbound: sample.$3,
            nature: _natureOf(sample.$1.type),
            cardinality: '',
            many: many,
          ),
        ],
      ));
      byKey[entry.key] = entry.value;
    }
    return TBoxGraph(
      focusLabel: instance.label,
      focusInstances: 1,
      spokes: spokes,
      onTapType: (key) {
        final members = byKey[key] ?? const [];
        if (members.length == 1) {
          _select(members.first.$2);
        } else if (members.length > 1) {
          _pickFrom(members);
        }
      },
    );
  }

  void _pickFrom(List<(WorldLink, WorldObject, bool)> members) {
    showDialog<void>(
      context: context,
      builder: (context) => Dialog(
        backgroundColor: CumulusColors.surface,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(CumulusRadius.card),
        ),
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 560, maxHeight: 620),
          child: Padding(
            padding: const EdgeInsets.all(20),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Expanded(
                      child: Text(
                        '${members.length} linked via ${members.first.$1.type}',
                        style: const TextStyle(
                            fontSize: 15, fontWeight: FontWeight.w600),
                      ),
                    ),
                    IconButton(
                      icon: const Icon(Icons.close, size: 16),
                      onPressed: () => Navigator.of(context).pop(),
                    ),
                  ],
                ),
                const SizedBox(height: 8),
                Flexible(
                  child: SingleChildScrollView(
                    child: Column(
                      children: [
                        for (final m in members)
                          CumulusListRow(
                            title: Text(m.$2.label),
                            subtitle: Text(m.$2.naturalKey,
                                style: CumulusText.mono),
                            trailing: [
                              if (m.$1.props.isNotEmpty)
                                Text(
                                  m.$1.props.entries
                                      .map((e) => '${e.key} ${e.value}')
                                      .join(' · '),
                                  style: CumulusText.statCaption,
                                ),
                            ],
                            onTap: () {
                              Navigator.of(context).pop();
                              _select(m.$2);
                            },
                          ),
                      ],
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
