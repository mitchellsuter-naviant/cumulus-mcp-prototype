/// Where every box and every line goes. Pure functions over the payload —
/// no widgets, no state, no measurement — so the layout can be argued with
/// in a test rather than by squinting at a screenshot.
///
/// **Determinism is the feature.** For a model people come back to weekly,
/// "Vendor is always in the same place" is worth more than a marginally
/// better crossing count, and a force simulation cannot honour the Collection
/// partition without constraint hacks that amount to this layout anyway.
///
/// The order of operations:
///
/// 1. **Cluster by Collection.** The Collections partition the types; a type
///    no Collection claims lands in [ungovernedCluster] rather than being
///    dropped, because a missing box is a lie and an ugly box is a bug report.
/// 2. **Order clusters** left→right by how many edges arrive from clusters
///    already placed, seeded and tie-broken by `(origin, id)`. Sources end up
///    left of the things they feed.
/// 3. **Rank inside a cluster** by composition depth. `owner_type` is derived
///    from the composition links and every part has exactly one owner, so the
///    ranks are a forest and the depth is unambiguous: owner 0, part 1.
/// 4. **Order inside a rank** by the barycentre of already-placed neighbours,
///    one pass, alphabetical where nothing pulls.
/// 5. **Wrap** the clusters into bands, so a seventeen-type model is a page
///    rather than a ten-column strip nobody can read without zooming out past
///    the point where the labels survive.
library;

import 'dart:math' as math;
import 'dart:ui' show Offset, Rect, Size;

import '../../models/ontology_screen.dart';

/// Where a type lands when no Collection claims it. The server emits this
/// cluster itself if its own partition ever breaks; this is the same escape
/// hatch for a payload that arrives inconsistent.
const ungovernedCluster = 'ungoverned';

const _nodeW = 158.0;
const _nodeH = 54.0;
const _rowGap = 26.0;

/// The rank gap doubles as the channel a link name is written in, so it is
/// sized for a label rather than for the line.
const _rankGap = 108.0;
const _clusterPadX = 16.0;
const _clusterPadY = 14.0;
const _clusterHeader = 30.0;
const _clusterGap = 48.0;
const _bandGap = 46.0;
const _scenePad = 28.0;

/// How wide a band may grow before the next cluster wraps. Chosen so the
/// whole model lands near 3:2 — the shape a pane is, and therefore the shape
/// that survives "fit to view" with the edge labels still legible.
const _bandBudget = 1320.0;

const _columnPitch = _nodeW + _rankGap;

/// One type, boxed and placed.
class OntologyNode {
  final OntologyType type;
  final Rect rect;
  final String clusterId;

  /// Composition depth inside the cluster: 0 for an owner, 1 for its parts.
  final int rank;
  final int band;

  const OntologyNode({
    required this.type,
    required this.rect,
    required this.clusterId,
    required this.rank,
    required this.band,
  });

  String get name => type.name;
  Offset get center => rect.center;
}

/// A Collection's frame — the box drawn behind its types.
class OntologyClusterBox {
  final OntologyCluster cluster;
  final Rect rect;
  final int band;

  const OntologyClusterBox({
    required this.cluster,
    required this.rect,
    required this.band,
  });
}

/// One link type, routed as one line.
///
/// One link, one edge — never one edge per neighbouring *pair*. Grouping by
/// neighbour is what made the old diagram draw two links as a single line and
/// then label it with both natures, so the line and its own label disagreed.
class OntologyEdge {
  final OntologyLink link;
  final Offset start;
  final Offset end;
  final Offset control;

  /// The target sits left of the source. Drawn where it actually goes, with
  /// a hollow arrowhead: at twenty-four edges you can afford honesty, and
  /// Sugiyama's cycle-breaking reverses an edge and then lies about it.
  final bool backEdge;

  /// Whether the name fits at the midpoint without landing on somebody's box.
  /// Where it does not, the canvas holds it back until the edge is hovered:
  /// a label printed over a type name costs more than it tells.
  final bool labelFits;

  const OntologyEdge({
    required this.link,
    required this.start,
    required this.end,
    required this.control,
    required this.backEdge,
    required this.labelFits,
  });

  /// Quadratic Bézier, which is all the curve this needs: one control point
  /// carries both the parallel-edge offset and the swing around a box.
  Offset pointAt(double t) {
    final u = 1 - t;
    return Offset(
      u * u * start.dx + 2 * u * t * control.dx + t * t * end.dx,
      u * u * start.dy + 2 * u * t * control.dy + t * t * end.dy,
    );
  }

  Offset get labelAt => pointAt(0.5);

  /// Distance from a point in **scene** coordinates. Hit-testing in screen
  /// coordinates is the bug that makes an edge feel unclickable, so the
  /// canvas puts its listener inside the transformed child and the numbers
  /// here never leave scene space.
  double distanceTo(Offset p) {
    var best = double.infinity;
    var previous = start;
    for (var i = 1; i <= 20; i++) {
      final next = pointAt(i / 20);
      best = math.min(best, _segmentDistance(p, previous, next));
      previous = next;
    }
    return best;
  }
}

/// `Employee reports_to Employee`. A line from a box back to itself is the
/// worst routing artifact a schema diagram produces and carries no
/// information a glyph on the box does not, so it never becomes an edge.
class OntologySelfLoop {
  final OntologyLink link;
  final OntologyNode node;

  const OntologySelfLoop({required this.link, required this.node});

  Offset get at => Offset(node.rect.right - 10, node.rect.top - 2);
}

/// The `+N more` handle on a node whose fan-out was capped.
class OntologyStub {
  final OntologyNode node;
  final List<OntologyLink> hidden;

  const OntologyStub({required this.node, required this.hidden});

  Offset get at => Offset(node.rect.right + 6, node.rect.center.dy);
}

class OntologyDiagramLayout {
  final List<OntologyNode> nodes;
  final List<OntologyClusterBox> clusters;
  final List<OntologyEdge> edges;
  final List<OntologySelfLoop> selfLoops;
  final List<OntologyStub> stubs;
  final Size size;

  const OntologyDiagramLayout({
    required this.nodes,
    required this.clusters,
    required this.edges,
    required this.selfLoops,
    required this.stubs,
    required this.size,
  });

  static const empty = OntologyDiagramLayout(
    nodes: [],
    clusters: [],
    edges: [],
    selfLoops: [],
    stubs: [],
    size: Size(1, 1),
  );

  OntologyNode? nodeNamed(String name) {
    for (final node in nodes) {
      if (node.name == name) return node;
    }
    return null;
  }

  /// The nearest edge to a scene point, or null past [tolerance].
  OntologyEdge? edgeAt(Offset scene, {double tolerance = 10}) {
    OntologyEdge? best;
    var bestDistance = tolerance;
    for (final edge in edges) {
      final d = edge.distanceTo(scene);
      if (d <= bestDistance) {
        bestDistance = d;
        best = edge;
      }
    }
    return best;
  }

  /// Every routed edge touching [name] — what a hover has to light up to
  /// answer "if I change this, what else moves".
  List<OntologyEdge> edgesTouching(String name) => [
    for (final e in edges)
      if (e.link.fromType == name || e.link.toType == name) e,
  ];
}

/// Lay the whole model out. [expanded] names the nodes whose fan-out cap has
/// been lifted by a click on their `+N more` stub.
OntologyDiagramLayout layoutOntology(
  OntologyScreen screen, {
  Set<String> expanded = const {},
  int maxFanOut = 8,
}) {
  final types = {for (final t in screen.types) t.name: t};
  if (types.isEmpty) return OntologyDiagramLayout.empty;

  final clusters = _clusterTypes(screen, types);
  final clusterOf = <String, String>{
    for (final c in clusters)
      for (final name in c.typeNames) name: c.id,
  };

  // Self-loops are excluded here, once, so nothing downstream has to remember
  // to skip them; they come back as glyphs at the end.
  final routable = [
    for (final link in screen.links)
      if (!link.isSelfLoop &&
          types.containsKey(link.fromType) &&
          types.containsKey(link.toType))
        link,
  ];

  final order = _placementOrder(clusters, clusterOf, routable);
  final ranks = {
    for (final name in types.keys) name: _rank(name, types, clusterOf),
  };

  // Cluster width is fixed by its rank count; band packing needs it before
  // anything is positioned, so it is measured first and reused below.
  final groups = <String, List<List<String>>>{};
  for (final cluster in order) {
    final byRank = <int, List<String>>{};
    for (final name in cluster.typeNames) {
      byRank.putIfAbsent(ranks[name]!, () => []).add(name);
    }
    final depth = byRank.keys.reduce(math.max);
    groups[cluster.id] = [
      for (var r = 0; r <= depth; r++) [...?byRank[r]]..sort(),
    ];
  }

  double widthOf(String id) {
    final columns = groups[id]!.length;
    return columns * _nodeW + (columns - 1) * _rankGap + 2 * _clusterPadX;
  }

  // Bands: the clusters keep their left→right order and wrap like text. A
  // single row would be ten columns wide and four rows tall, and "fit to
  // view" on that shape zooms past the point where any label survives.
  final bands = <List<OntologyCluster>>[];
  var bandWidth = 0.0;
  for (final cluster in order) {
    final w = widthOf(cluster.id);
    if (bands.isEmpty || (bandWidth + _clusterGap + w > _bandBudget)) {
      bands.add([cluster]);
      bandWidth = w;
    } else {
      bands.last.add(cluster);
      bandWidth += _clusterGap + w;
    }
  }

  final nodes = <OntologyNode>[];
  final boxes = <OntologyClusterBox>[];
  final placedY = <String, double>{};
  var bandTop = _scenePad;
  var sceneRight = _scenePad;

  for (final (bandIndex, band) in bands.indexed) {
    final rows = band
        .expand((c) => groups[c.id]!)
        .map((g) => g.length)
        .fold<int>(1, (a, b) => math.max(a, b));
    final contentH = rows * _nodeH + (rows - 1) * _rowGap;
    final bandH = _clusterHeader + 2 * _clusterPadY + contentH;
    var x = _scenePad;

    for (final cluster in band) {
      // A Collection holding one type gets a frame the size of one type. A
      // frame stretched to the tallest cluster in the band is mostly empty
      // space that reads as missing content.
      final own = groups[cluster.id]!.map((g) => g.length).fold<int>(
        1,
        (a, b) => math.max(a, b),
      );
      final ownH = _clusterHeader +
          2 * _clusterPadY +
          own * _nodeH +
          (own - 1) * _rowGap;
      final top = bandTop + (bandH - ownH) / 2;
      final rect = Rect.fromLTWH(x, top, widthOf(cluster.id), ownH);
      boxes.add(
        OntologyClusterBox(cluster: cluster, rect: rect, band: bandIndex),
      );
      for (final (rank, group) in groups[cluster.id]!.indexed) {
        if (group.isEmpty) continue;
        final left = x + _clusterPadX + rank * _columnPitch;
        // Short groups sit centred in the band rather than hanging off its
        // top, which keeps the edges between neighbouring clusters short.
        final groupTop = rect.top +
            _clusterHeader +
            _clusterPadY +
            (own - group.length) / 2 * (_nodeH + _rowGap);
        for (final (i, name) in _byBarycentre(
          group,
          routable,
          placedY,
          groupTop,
        ).indexed) {
          final y = groupTop + i * (_nodeH + _rowGap);
          placedY[name] = y + _nodeH / 2;
          nodes.add(
            OntologyNode(
              type: types[name]!,
              rect: Rect.fromLTWH(left, y, _nodeW, _nodeH),
              clusterId: cluster.id,
              rank: rank,
              band: bandIndex,
            ),
          );
        }
      }
      x = rect.right + _clusterGap;
      sceneRight = math.max(sceneRight, rect.right);
    }
    bandTop += bandH + _bandGap;
  }

  final sceneSize = Size(sceneRight + _scenePad, bandTop - _bandGap + _scenePad);
  // Edges may bow into the gutters between clusters but not off the page,
  // where a pan would be needed to see a line that has nowhere to go.
  final safeArea = Rect.fromLTWH(
    _scenePad / 2,
    _scenePad / 2,
    sceneSize.width - _scenePad,
    sceneSize.height - _scenePad,
  );
  final byName = {for (final n in nodes) n.name: n};
  final (routed, stubs) = _capFanOut(routable, byName, expanded, maxFanOut);

  return OntologyDiagramLayout(
    nodes: nodes,
    clusters: boxes,
    edges: _routeEdges(routed, byName, safeArea),
    selfLoops: [
      for (final link in screen.links)
        if (link.isSelfLoop && byName.containsKey(link.fromType))
          OntologySelfLoop(link: link, node: byName[link.fromType]!),
    ],
    stubs: stubs,
    size: sceneSize,
  );
}

/// The Collections, restricted to types that actually shipped, plus a
/// synthetic cluster for anything unclaimed.
List<OntologyCluster> _clusterTypes(
  OntologyScreen screen,
  Map<String, OntologyType> types,
) {
  final claimed = <String>{};
  final clusters = <OntologyCluster>[];
  for (final cluster in screen.clusters) {
    final members = [
      // `claimed.add` short-circuits a second claim on the same type: the
      // partition is asserted by a server test, and a type drawn twice would
      // be a worse way to find out it broke than an ungoverned box.
      for (final name in cluster.typeNames)
        if (types.containsKey(name) && claimed.add(name)) name,
    ];
    if (members.isEmpty) continue;
    clusters.add(
      OntologyCluster(
        id: cluster.id,
        label: cluster.label,
        role: cluster.role,
        origin: cluster.origin,
        typeNames: members,
      ),
    );
  }
  final orphans = [
    for (final name in types.keys)
      if (!claimed.contains(name)) name,
  ]..sort();
  if (orphans.isNotEmpty) {
    clusters.add(
      OntologyCluster(
        id: ungovernedCluster,
        label: 'Ungoverned — no Collection claims these',
        role: '',
        origin: '',
        typeNames: orphans,
      ),
    );
  }
  // The seed order, which is also every tiebreak below. Ungoverned sorts last
  // whatever its origin string happens to be: it is an exception report, not
  // a peer of the governed sets.
  clusters.sort((a, b) {
    final exception = (a.id == ungovernedCluster ? 1 : 0) -
        (b.id == ungovernedCluster ? 1 : 0);
    if (exception != 0) return exception;
    final origin = a.origin.compareTo(b.origin);
    return origin != 0 ? origin : a.id.compareTo(b.id);
  });
  return clusters;
}

/// Left→right: repeatedly take the cluster most fed by what is already
/// placed, so a source sits left of its consumers. Seed order breaks ties,
/// including the first pick where nothing is placed and every count is zero.
List<OntologyCluster> _placementOrder(
  List<OntologyCluster> seeded,
  Map<String, String> clusterOf,
  List<OntologyLink> links,
) {
  final order = <OntologyCluster>[];
  final placed = <String>{};
  final remaining = [...seeded];
  while (remaining.isNotEmpty) {
    var pick = 0;
    var bestScore = -1;
    for (var i = 0; i < remaining.length; i++) {
      final id = remaining[i].id;
      var score = 0;
      for (final link in links) {
        final from = clusterOf[link.fromType];
        final to = clusterOf[link.toType];
        if (to == id && from != id && placed.contains(from)) score++;
      }
      if (score > bestScore) {
        bestScore = score;
        pick = i;
      }
    }
    final chosen = remaining.removeAt(pick);
    placed.add(chosen.id);
    order.add(chosen);
  }
  return order;
}

/// Composition depth. Only owners inside the same cluster count: a part
/// belongs beside the thing that identifies it, and a cross-cluster owner
/// would drag it out of the Collection that governs it.
int _rank(
  String name,
  Map<String, OntologyType> types,
  Map<String, String> clusterOf,
) {
  var depth = 0;
  var current = types[name]!;
  final seen = <String>{name};
  while (current.ownerType.isNotEmpty) {
    final owner = types[current.ownerType];
    if (owner == null) break;
    if (clusterOf[owner.name] != clusterOf[name]) break;
    if (!seen.add(owner.name)) break;
    depth++;
    current = owner;
  }
  return depth;
}

/// Vertical order inside one rank: the mean height of the neighbours already
/// on screen, alphabetical where nothing pulls. One pass — a second would
/// reorder the clusters placed first, which is exactly the stability the
/// whole layout is buying.
List<String> _byBarycentre(
  List<String> group,
  List<OntologyLink> links,
  Map<String, double> placedY,
  double fallbackTop,
) {
  final centre = fallbackTop + (group.length - 1) / 2 * (_nodeH + _rowGap);
  final keys = <String, double>{};
  for (final name in group) {
    final ys = <double>[];
    for (final link in links) {
      if (link.fromType == name && placedY.containsKey(link.toType)) {
        ys.add(placedY[link.toType]!);
      }
      if (link.toType == name && placedY.containsKey(link.fromType)) {
        ys.add(placedY[link.fromType]!);
      }
    }
    keys[name] = ys.isEmpty
        ? centre
        : ys.reduce((a, b) => a + b) / ys.length;
  }
  return [...group]..sort((a, b) {
    final byKey = keys[a]!.compareTo(keys[b]!);
    return byKey != 0 ? byKey : a.compareTo(b);
  });
}

/// Drop the lowest-weight edges off a node that has too many, and hand back a
/// stub that can put them back. Weight is composition, then identity, then
/// association, then instance count: the edges that carry identity survive a
/// crowded node, the incidental many-to-manys are the ones worth folding.
///
/// Degree counts endpoints, so a self-loop counts twice even though it never
/// routes — a node with a self-loop really does carry that much notation.
(List<OntologyLink>, List<OntologyStub>) _capFanOut(
  List<OntologyLink> links,
  Map<String, OntologyNode> nodes,
  Set<String> expanded,
  int maxFanOut,
) {
  final degree = <String, int>{};
  for (final link in links) {
    degree[link.fromType] = (degree[link.fromType] ?? 0) + 1;
    degree[link.toType] = (degree[link.toType] ?? 0) + 1;
  }
  final hidden = <String, List<OntologyLink>>{};
  final dropped = <OntologyLink>{};
  for (final entry in degree.entries.toList()
    ..sort((a, b) => a.key.compareTo(b.key))) {
    if (entry.value <= maxFanOut || expanded.contains(entry.key)) continue;
    final incident = [
      for (final link in links)
        if (!dropped.contains(link) &&
            (link.fromType == entry.key || link.toType == entry.key))
          link,
    ]..sort(_byWeight);
    final excess = math.min(entry.value - maxFanOut, incident.length);
    final fold = incident.sublist(incident.length - excess);
    dropped.addAll(fold);
    hidden[entry.key] = fold;
  }
  return (
    [
      for (final link in links)
        if (!dropped.contains(link)) link,
    ],
    [
      for (final entry in hidden.entries)
        if (nodes.containsKey(entry.key))
          OntologyStub(node: nodes[entry.key]!, hidden: entry.value),
    ],
  );
}

int _natureWeight(String nature) => switch (nature) {
  'composition' => 2,
  'identity' => 1,
  _ => 0,
};

/// Heaviest first, so `sublist` from the end takes the ones worth folding.
int _byWeight(OntologyLink a, OntologyLink b) {
  final nature = _natureWeight(b.nature) - _natureWeight(a.nature);
  if (nature != 0) return nature;
  final instances = b.instances.compareTo(a.instances);
  return instances != 0 ? instances : a.name.compareTo(b.name);
}

List<OntologyEdge> _routeEdges(
  List<OntologyLink> links,
  Map<String, OntologyNode> nodes,
  Rect safeArea,
) {
  // Parallel edges share one frame, keyed on the unordered pair. Offsetting
  // each edge along its *own* direction would push a there-and-back pair —
  // Employee submitted / decided_by ExpenseRequest — onto the same side of
  // the line, which is the one place they must not both go.
  final siblings = <String, List<OntologyLink>>{};
  for (final link in links) {
    siblings.putIfAbsent(_pairKey(link), () => []).add(link);
  }
  for (final group in siblings.values) {
    group.sort((a, b) => a.name.compareTo(b.name));
  }

  // The label test uses every box, the two ends included: an owner-to-part
  // edge is short enough that its own endpoints are what the name lands on.
  final allBoxes = [for (final node in nodes.values) node.rect.inflate(2)];

  final edges = <OntologyEdge>[];
  for (final link in links) {
    final from = nodes[link.fromType]!;
    final to = nodes[link.toType]!;
    final group = siblings[_pairKey(link)]!;
    final index = group.indexOf(link);

    // The frame runs between the pair in name order, so every sibling agrees
    // on which side is which regardless of which way it points.
    final anchor = link.fromType.compareTo(link.toType) <= 0 ? from : to;
    final other = identical(anchor, from) ? to : from;
    final axis = other.center - anchor.center;
    final length = axis.distance;
    final perpendicular = length == 0
        ? const Offset(0, 1)
        : Offset(-axis.dy / length, axis.dx / length);

    final span = (to.center.dx - from.center.dx).abs();
    final drop = (to.center.dy - from.center.dy).abs();
    final double base;
    if (span < _nodeW) {
      // Same column: a straight line would run through every box between the
      // two ends, so it must swing clear of the column entirely.
      base = _nodeW * 0.62 + 0.12 * drop;
    } else if (span > _columnPitch * 1.6) {
      base = 22;
    } else {
      base = 0;
    }
    final spread = (index - (group.length - 1) / 2) * 26;
    final mid = (from.center + to.center) / 2;
    final blocking = [
      for (final node in nodes.values)
        if (node.name != link.fromType && node.name != link.toType)
          node.rect.inflate(7),
    ];

    // Try the smallest bow first and widen until the curve clears every other
    // box and stays on the canvas. Ten candidates is enough at this size, and
    // trying them in a fixed order keeps the result deterministic — which is
    // the whole reason this layout is not a force simulation.
    var control = mid + perpendicular * ((base + spread) * 2);
    var bestCost = double.infinity;
    for (final magnitude in [base, base + 30, base + 62, base + 96, base + 134]) {
      for (final sign in const [1.0, -1.0]) {
        final candidate = mid + perpendicular * ((sign * magnitude + spread) * 2);
        final cost = _routeCost(
          _exit(from.rect, candidate),
          candidate,
          _exit(to.rect, candidate),
          blocking,
          safeArea,
          magnitude,
        );
        if (cost < bestCost) {
          bestCost = cost;
          control = candidate;
        }
      }
    }

    final start = _exit(from.rect, control);
    final end = _exit(to.rect, control);
    edges.add(
      OntologyEdge(
        link: link,
        start: start,
        end: end,
        control: control,
        backEdge: to.center.dx < from.center.dx - _nodeW / 2,
        labelFits: _labelFits(
          Offset(
            0.25 * start.dx + 0.5 * control.dx + 0.25 * end.dx,
            0.25 * start.dy + 0.5 * control.dy + 0.25 * end.dy,
          ),
          link.name,
          allBoxes,
        ),
      ),
    );
  }
  return edges;
}

/// How bad a candidate route is: every sample of the curve that lands in
/// another type's box or off the canvas costs a hundred, and the bow itself
/// costs its own size, so the smallest route that collides with nothing wins.
double _routeCost(
  Offset start,
  Offset control,
  Offset end,
  List<Rect> blocking,
  Rect safeArea,
  double magnitude,
) {
  var penalty = 0.0;
  for (final t in const [0.12, 0.25, 0.4, 0.5, 0.6, 0.75, 0.88]) {
    final u = 1 - t;
    final p = Offset(
      u * u * start.dx + 2 * u * t * control.dx + t * t * end.dx,
      u * u * start.dy + 2 * u * t * control.dy + t * t * end.dy,
    );
    if (!safeArea.contains(p)) penalty += 100;
    for (final rect in blocking) {
      if (rect.contains(p)) penalty += 100;
    }
  }
  return penalty + magnitude;
}

/// Approximated rather than measured: the layout runs before any text is
/// laid out, and a monospaced name at ten points is regular enough that a
/// character width is a safe estimate.
bool _labelFits(Offset at, String name, List<Rect> blocking) {
  final box = Rect.fromCenter(
    center: at,
    width: 6.4 * name.length + 12,
    height: 18,
  );
  for (final rect in blocking) {
    if (rect.overlaps(box)) return false;
  }
  return true;
}

String _pairKey(OntologyLink link) {
  final a = link.fromType;
  final b = link.toType;
  return a.compareTo(b) <= 0 ? '$a $b' : '$b $a';
}

/// Where a ray from a box's centre towards [towards] leaves the box, plus a
/// margin. Aiming at the control point rather than the far centre is what
/// makes a curved edge leave its box along the curve's own tangent.
Offset _exit(Rect box, Offset towards, {double margin = 5}) {
  final d = towards - box.center;
  if (d.dx == 0 && d.dy == 0) return box.center;
  final halfW = box.width / 2 + margin;
  final halfH = box.height / 2 + margin;
  final scale = math.min(
    d.dx == 0 ? double.infinity : halfW / d.dx.abs(),
    d.dy == 0 ? double.infinity : halfH / d.dy.abs(),
  );
  return box.center + d * scale;
}

double _segmentDistance(Offset p, Offset a, Offset b) {
  final ab = b - a;
  final lengthSquared = ab.dx * ab.dx + ab.dy * ab.dy;
  if (lengthSquared == 0) return (p - a).distance;
  final ap = p - a;
  final t = ((ap.dx * ab.dx + ap.dy * ab.dy) / lengthSquared).clamp(0.0, 1.0);
  return (p - (a + ab * t)).distance;
}
