import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../models/flow.dart';
import '../models/run.dart';
import '../theme/cumulus_theme.dart';
import 'ui_kit.dart';

/// Node geometry. Flow runs north to south: a layer is a row, and a branch
/// spreads its arms across that row.
const double _nodeWidth = 216;
const double _nodeHeight = 92;
const double _hGap = 40;
const double _vGap = 62;
const double _padding = 44;

CumulusTone stepTone(String type) => switch (type) {
  'trigger' => CumulusTone.teal,
  'modelcall' => CumulusTone.violet,
  'rule' || 'check' => CumulusTone.warn,
  'task' => CumulusTone.accent,
  'action' => CumulusTone.success,
  'resolve' || 'sync' => CumulusTone.teal,
  _ => CumulusTone.neutral,
};

/// The governed-operation badge a step type carries, if any. These are the
/// three places the doctrine puts a control point, so they are the three
/// places the canvas says so out loud.
String? governedBadge(String type) => switch (type) {
  'modelcall' => 'GOVERNED',
  'action' => 'GATEWAY',
  'task' => 'SUSPENDS',
  _ => null,
};

/// What one run did with the graph: which steps it executed, and which edges
/// it actually traversed.
///
/// The edge set matters because status alone is ambiguous. When a Rule
/// branches to both an Action and a Task and the Task ran, `rule → action` and
/// `task → action` are both edges between executed steps — but only the second
/// was traversed. [fromRun] resolves that by walking the journal in order.
///
/// Journal ids and node ids are not the same alphabet — see [canvasNode].
class RunPath {
  final String runId;
  final Map<String, String> statusByStep;
  final Set<String> takenEdges;

  const RunPath({
    required this.runId,
    required this.statusByStep,
    required this.takenEdges,
  });

  static String edgeKey(String from, String to) => '$from>$to';

  /// A journal holds only the steps that ran, so presence *is* execution.
  bool executed(String stepId) => statusByStep.containsKey(stepId);

  /// The canvas node a journal entry ran as.
  ///
  /// A loop mints one journal step per iteration of a declared step: the AP
  /// review cycle records `r2_1`, `k2_1`, `c1_1`, then `r2_2` when the
  /// correction sends it round again, while the definition declares `r2`,
  /// `k2` and `c1` exactly once. Trimming the round suffix is what lets a
  /// second pass light up the node it belongs to. A declared id always wins
  /// outright, so a definition that really does name a step `x_1` is never
  /// mistaken for a second visit to `x`; a journal step with no node at all
  /// (the engine runs steps the Tier-2 export does not show) keeps its own id
  /// and simply matches nothing.
  static String canvasNode(String journalStepId, Set<String> nodeIds) {
    if (nodeIds.contains(journalStepId)) return journalStepId;
    final cut = journalStepId.lastIndexOf('_');
    if (cut <= 0) return journalStepId;
    if (int.tryParse(journalStepId.substring(cut + 1)) == null) {
      return journalStepId;
    }
    final base = journalStepId.substring(0, cut);
    return nodeIds.contains(base) ? base : journalStepId;
  }

  factory RunPath.fromRun(FlowRun run, List<FlowStep> steps) {
    final byId = {for (final s in steps) s.id: s};
    final nodeIds = byId.keys.toSet();
    // Journal order is execution order, so the last visit to a looped node is
    // the one the canvas shows: a gate that suspended on its second round
    // reads as waiting, not as decided.
    final visited = [
      for (final e in run.steps) MapEntry(canvasNode(e.stepId, nodeIds), e.status),
    ].where((e) => byId.containsKey(e.key)).toList();
    // Consecutive entries are the traversal, so an edge is taken when the next
    // step is one this step actually points at.
    final taken = <String>{};
    for (var i = 0; i < visited.length - 1; i++) {
      final from = visited[i].key;
      final to = visited[i + 1].key;
      if (byId[from]!.next.contains(to)) taken.add(edgeKey(from, to));
    }
    return RunPath(
      runId: run.id,
      statusByStep: {for (final e in visited) e.key: e.value},
      takenEdges: taken,
    );
  }
}

class _PlacedNode {
  final FlowStep step;
  final String change;
  final List<String> changedFields;
  final Offset position;

  const _PlacedNode({
    required this.step,
    required this.change,
    required this.changedFields,
    required this.position,
  });
}

class _Edge {
  final String fromId;
  final String toId;
  final Offset from;
  final Offset to;
  final String label;
  final bool ghost;
  final bool taken;

  /// Set when this edge skips a row and has to route around whatever sits in
  /// it. Both cubic control points share this x, bowing the curve clear of
  /// the intervening node instead of drawing straight through it.
  final double? bypassX;

  const _Edge({
    required this.fromId,
    required this.toId,
    required this.from,
    required this.to,
    required this.label,
    required this.ghost,
    required this.taken,
    this.bypassX,
  });

  /// Where the curve actually passes at t=0.5 — the honest place for a label.
  /// For a cubic, x(0.5) = (p0 + 3c1 + 3c2 + p3) / 8.
  Offset get midpoint => Offset(
    bypassX == null
        ? (from.dx + to.dx) / 2
        : (from.dx + to.dx + 6 * bypassX!) / 8,
    (from.dy + to.dy) / 2,
  );
}

/// Layered top-to-bottom layout of the declarative export.
///
/// A step's layer is the longest path to it from a root, which is what keeps a
/// branch's two arms on the same row rather than letting one drift below the
/// other. Steps present only in the baseline (removed by a draft) are placed
/// just below where they used to sit, so a deletion is visible in position.
class _Layout {
  final List<_PlacedNode> nodes;
  final List<_Edge> edges;
  final Size size;

  const _Layout({
    required this.nodes,
    required this.edges,
    required this.size,
  });

  static _Layout build(List<StepDiff> diffs, RunPath? path) {
    if (diffs.isEmpty) {
      return const _Layout(nodes: [], edges: [], size: Size(0, 0));
    }
    final steps = [for (final d in diffs) d.step];
    final byId = {for (final s in steps) s.id: s};
    final annotation = {for (final d in diffs) d.step.id: d};

    final layer = <String, int>{for (final s in steps) s.id: 0};
    for (var pass = 0; pass < steps.length; pass++) {
      var moved = false;
      for (final step in steps) {
        for (final nextId in step.next) {
          if (!byId.containsKey(nextId)) continue;
          final candidate = layer[step.id]! + 1;
          if (candidate > layer[nextId]!) {
            layer[nextId] = candidate;
            moved = true;
          }
        }
      }
      if (!moved) break;
    }

    final referenced = <String>{
      for (final s in steps) ...s.next.where(byId.containsKey),
    };
    for (final step in steps) {
      if (step.id == steps.first.id || referenced.contains(step.id)) continue;
      if (annotation[step.id]?.change == 'removed') {
        final predecessor = steps.firstWhere(
          (s) => s.next.contains(step.id),
          orElse: () => step,
        );
        layer[step.id] = (layer[predecessor.id] ?? 0) + 1;
      }
    }

    final rows = <int, List<FlowStep>>{};
    for (final step in steps) {
      rows.putIfAbsent(layer[step.id]!, () => []).add(step);
    }

    final widest = rows.values.fold<int>(1, (m, r) => math.max(m, r.length));
    final canvasWidth =
        _padding * 2 + widest * _nodeWidth + (widest - 1) * _hGap;
    final placed = <String, _PlacedNode>{};
    for (final entry in rows.entries) {
      final row = entry.value;
      final rowWidth = row.length * _nodeWidth + (row.length - 1) * _hGap;
      final left = (canvasWidth - rowWidth) / 2;
      for (var i = 0; i < row.length; i++) {
        final step = row[i];
        final diff = annotation[step.id];
        placed[step.id] = _PlacedNode(
          step: step,
          change: diff?.change ?? 'unchanged',
          changedFields: diff?.changedFields ?? const [],
          position: Offset(
            left + i * (_nodeWidth + _hGap),
            _padding + entry.key * (_nodeHeight + _vGap),
          ),
        );
      }
    }

    final edges = <_Edge>[];
    for (final step in steps) {
      final source = placed[step.id];
      if (source == null) continue;
      for (var i = 0; i < step.next.length; i++) {
        final target = placed[step.next[i]];
        if (target == null) continue;
        final key = RunPath.edgeKey(step.id, step.next[i]);
        final taken = path?.takenEdges.contains(key) ?? false;
        final from = Offset(
          source.position.dx + _nodeWidth / 2,
          source.position.dy + _nodeHeight,
        );
        final to = Offset(
          target.position.dx + _nodeWidth / 2,
          target.position.dy,
        );

        // An edge that skips a row would otherwise be drawn straight through
        // whatever occupies it — which is exactly what a branch does when one
        // arm is longer than the other (rule → action, past the task). Route
        // it around the far side of every row it skips.
        double? bypassX;
        final fromLayer = layer[step.id]!;
        final toLayer = layer[step.next[i]]!;
        if ((toLayer - fromLayer).abs() > 1) {
          final lo = math.min(fromLayer, toLayer);
          final hi = math.max(fromLayer, toLayer);
          var clearRight = math.max(from.dx, to.dx);
          var clearLeft = math.min(from.dx, to.dx);
          for (var l = lo + 1; l < hi; l++) {
            for (final blocking in rows[l] ?? const <FlowStep>[]) {
              final node = placed[blocking.id];
              if (node == null) continue;
              clearRight = math.max(clearRight, node.position.dx + _nodeWidth);
              clearLeft = math.min(clearLeft, node.position.dx);
            }
          }
          // Leave on whichever side is nearer, so the detour stays short.
          // The margin has to clear the branch label's pill as well as the
          // curve, or the label lands back on the card it routed around.
          const margin = 46.0;
          final centre = (from.dx + to.dx) / 2;
          var lane = (clearRight - centre) <= (centre - clearLeft)
              ? clearRight + margin
              : clearLeft - margin;
          // A left-hand lane can fall off the near edge of the surface, which
          // the width calculation below cannot rescue; go right instead.
          if (lane < margin) lane = clearRight + margin;
          // Invert x(0.5) = (p0 + 6c + p3) / 8 so the curve reaches the lane.
          bypassX = (8 * lane - from.dx - to.dx) / 6;
        }

        edges.add(
          _Edge(
            fromId: step.id,
            toId: step.next[i],
            from: from,
            to: to,
            label: i < step.branchLabels.length ? step.branchLabels[i] : '',
            ghost: source.change == 'removed' ||
                target.change == 'removed' ||
                (path != null && !taken),
            taken: taken,
            bypassX: bypassX,
          ),
        );
      }
    }

    final deepest = rows.keys.fold<int>(0, math.max);
    // A bypass lane can swing wider than the node grid, and a branch label
    // rides on it — so the surface has to grow to hold them or the detour
    // gets clipped at the edge of the child.
    var width = canvasWidth;
    for (final edge in edges) {
      if (edge.bypassX == null) continue;
      width = math.max(width, edge.midpoint.dx + 90);
    }
    return _Layout(
      nodes: placed.values.toList(),
      edges: edges,
      size: Size(
        width,
        _padding * 2 + (deepest + 1) * _nodeHeight + deepest * _vGap,
      ),
    );
  }
}

/// The pipeline as an interactive graph: a dotted field you can pan and zoom,
/// flowing top to bottom, with click-to-inspect nodes.
///
/// Built on [InteractiveViewer] and a [CustomPaint] edge layer rather than a
/// graph package — the console ships with no charting or graph dependency, and
/// a layered DAG of a dozen typed steps is arithmetic, not a library.
class FlowCanvas extends StatefulWidget {
  final List<StepDiff> steps;

  /// Non-empty when a draft is selected, e.g. "v14 (active)". Drives the diff
  /// legend, which is otherwise hidden.
  final String diffAgainst;

  /// When set, the graph is drawn as one run took it: steps the run skipped
  /// fade to ghosts and the traversed edges are drawn in the accent.
  final RunPath? runPath;

  final String? selectedStepId;
  final ValueChanged<FlowStep?> onSelect;
  final VoidCallback? onExportSchema;

  const FlowCanvas({
    super.key,
    required this.steps,
    required this.onSelect,
    this.diffAgainst = '',
    this.runPath,
    this.selectedStepId,
    this.onExportSchema,
  });

  @override
  State<FlowCanvas> createState() => _FlowCanvasState();
}

class _FlowCanvasState extends State<FlowCanvas> {
  final TransformationController _view = TransformationController();

  @override
  void dispose() {
    _view.dispose();
    super.dispose();
  }

  void _zoomBy(double factor) {
    final current = _view.value.getMaxScaleOnAxis();
    final next = (current * factor).clamp(0.4, 2.0);
    _view.value = Matrix4.identity()..scaleByDouble(next, next, next, 1);
  }

  @override
  Widget build(BuildContext context) {
    final layout = _Layout.build(widget.steps, widget.runPath);
    if (layout.nodes.isEmpty) {
      return const CumulusEmptyState(
        icon: Icons.account_tree_outlined,
        title: 'No steps exported',
        message: 'This version has not exported its declarative shape yet.',
      );
    }
    return Stack(
      children: [
        // The dotted field is painted at VIEWPORT level, not inside the
        // transformed child: a pattern drawn on the child stops at the
        // graph's bounds and never repaints on pan or zoom, so the dots sat
        // still while the graph moved and ran out entirely at the edges.
        // Driving it from the controller instead gives an infinite field
        // that scrolls and scales with the content.
        Positioned.fill(
          // CustomPaint does not clip, and the field deliberately tiles from
          // one step *outside* each edge so no partial row is missing — which
          // without this spills a row of dots up over the header.
          child: ClipRect(
            child: ColoredBox(
              color: CumulusColors.canvas,
              child: ListenableBuilder(
                listenable: _view,
                builder: (context, _) => CustomPaint(
                  painter: _DotFieldPainter(_view.value),
                  size: Size.infinite,
                ),
              ),
            ),
          ),
        ),
        Positioned.fill(
          child: InteractiveViewer(
            transformationController: _view,
            constrained: false,
            boundaryMargin: const EdgeInsets.all(400),
            minScale: 0.4,
            maxScale: 2.0,
            child: SizedBox(
              width: layout.size.width,
              height: layout.size.height,
              child: Stack(
                children: [
                  Positioned.fill(
                    child: CustomPaint(painter: _EdgePainter(layout.edges)),
                  ),
                  for (final node in layout.nodes)
                    Positioned(
                      left: node.position.dx,
                      top: node.position.dy,
                      width: _nodeWidth,
                      height: _nodeHeight,
                      child: _CanvasNode(
                        node: node,
                        selected: node.step.id == widget.selectedStepId,
                        runStatus: widget.runPath?.statusByStep[node.step.id],
                        inRun: widget.runPath?.executed(node.step.id),
                        onTap: () => widget.onSelect(
                          node.step.id == widget.selectedStepId
                              ? null
                              : node.step,
                        ),
                      ),
                    ),
                ],
              ),
            ),
          ),
        ),
        // Floating chrome, Airflow-style: the field is the whole surface and
        // the controls sit on top of it rather than stealing a strip.
        Positioned(
          left: 12,
          top: 12,
          child: Row(
            children: [
              if (widget.diffAgainst.isNotEmpty)
                _FloatingBar(
                  children: [
                    Text(
                      'DIFF vs ${widget.diffAgainst}',
                      style: CumulusText.statLabel,
                    ),
                    const SizedBox(width: 10),
                    const _Key(color: CumulusColors.success, label: 'added'),
                    const SizedBox(width: 8),
                    const _Key(color: CumulusColors.warn, label: 'modified'),
                    const SizedBox(width: 8),
                    const _Key(color: CumulusColors.danger, label: 'removed'),
                  ],
                ),
              if (widget.runPath != null)
                _FloatingBar(
                  children: [
                    Text('PATH OF', style: CumulusText.statLabel),
                    const SizedBox(width: 8),
                    Text(
                      widget.runPath!.runId,
                      style: CumulusText.mono.copyWith(fontSize: 11.5),
                    ),
                    const SizedBox(width: 10),
                    const _Key(color: CumulusColors.accent, label: 'taken'),
                    const SizedBox(width: 8),
                    const _Key(
                      color: CumulusColors.inkMuted,
                      label: 'not taken',
                    ),
                  ],
                ),
            ],
          ),
        ),
        Positioned(
          right: 12,
          top: 12,
          child: Row(
            children: [
              if (widget.onExportSchema != null) ...[
                _FloatingBar(
                  padding: const EdgeInsets.symmetric(horizontal: 4),
                  children: [
                    TextButton.icon(
                      onPressed: widget.onExportSchema,
                      icon: const Icon(Icons.code, size: 14),
                      label: const Text('Export schema'),
                      style: TextButton.styleFrom(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 8,
                          vertical: 4,
                        ),
                      ),
                    ),
                  ],
                ),
                const SizedBox(width: 8),
              ],
              _FloatingBar(
                padding: const EdgeInsets.symmetric(horizontal: 3, vertical: 3),
                children: [
                  _ZoomButton(
                    icon: Icons.remove,
                    tooltip: 'Zoom out',
                    onTap: () => _zoomBy(1 / 1.25),
                  ),
                  _ZoomButton(
                    icon: Icons.add,
                    tooltip: 'Zoom in',
                    onTap: () => _zoomBy(1.25),
                  ),
                  _ZoomButton(
                    icon: Icons.fit_screen_outlined,
                    tooltip: 'Reset view',
                    onTap: () => _view.value = Matrix4.identity(),
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

class _FloatingBar extends StatelessWidget {
  final List<Widget> children;
  final EdgeInsets padding;

  const _FloatingBar({
    required this.children,
    this.padding = const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.only(right: 8),
      padding: padding,
      decoration: BoxDecoration(
        color: CumulusColors.surface,
        borderRadius: BorderRadius.circular(CumulusRadius.control),
        border: Border.all(color: CumulusColors.border),
        boxShadow: CumulusShadows.card,
      ),
      child: Row(mainAxisSize: MainAxisSize.min, children: children),
    );
  }
}

class _Key extends StatelessWidget {
  final Color color;
  final String label;

  const _Key({required this.color, required this.label});

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          width: 8,
          height: 8,
          decoration: BoxDecoration(color: color, shape: BoxShape.circle),
        ),
        const SizedBox(width: 5),
        Text(label, style: CumulusText.meta),
      ],
    );
  }
}

class _ZoomButton extends StatelessWidget {
  final IconData icon;
  final String tooltip;
  final VoidCallback onTap;

  const _ZoomButton({
    required this.icon,
    required this.tooltip,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return Tooltip(
      message: tooltip,
      child: Material(
        color: Colors.transparent,
        borderRadius: BorderRadius.circular(8),
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(8),
          child: SizedBox(
            width: 30,
            height: 28,
            child: Icon(icon, size: 15, color: CumulusColors.inkSecondary),
          ),
        ),
      ),
    );
  }
}

/// The infinite dotted background, drawn in viewport space from the viewer's
/// current transform.
///
/// It reads the translation and scale off the matrix and tiles from the
/// remainder, so the field always covers the whole viewport however far the
/// graph has been panned, and the dots travel and grow with the content
/// instead of being a static texture bolted to the graph's own bounds.
class _DotFieldPainter extends CustomPainter {
  static const double spacing = 22;

  final Matrix4 transform;

  _DotFieldPainter(this.transform);

  @override
  void paint(Canvas canvas, Size size) {
    final scale = transform.getMaxScaleOnAxis();
    final step = spacing * scale;
    // The viewer's minScale keeps this well above the floor today; the guard
    // is here so that lowering minScale later degrades to a plain background
    // rather than filling the viewport with a grey wash of touching dots.
    if (!step.isFinite || step < 7) return;
    final translation = transform.getTranslation();
    // Dart's % is non-negative for a positive divisor, so this lands the
    // first tile just off-screen in both directions without a sign dance.
    final originX = translation.x % step;
    final originY = translation.y % step;
    final radius = (scale * 1.0).clamp(0.6, 2.0);
    final dot = Paint()..color = CumulusColors.borderStrong;
    for (var x = originX - step; x <= size.width + step; x += step) {
      for (var y = originY - step; y <= size.height + step; y += step) {
        canvas.drawCircle(Offset(x, y), radius, dot);
      }
    }
  }

  @override
  bool shouldRepaint(_DotFieldPainter old) => old.transform != transform;
}

/// The connectors, painted beneath the nodes so a branch label never covers a
/// step's text.
class _EdgePainter extends CustomPainter {
  final List<_Edge> edges;

  _EdgePainter(this.edges);

  @override
  void paint(Canvas canvas, Size size) {
    for (final edge in edges) {
      _connector(canvas, edge);
    }
  }

  void _connector(Canvas canvas, _Edge edge) {
    final paint = Paint()
      ..color = edge.taken
          ? CumulusColors.accent
          : edge.ghost
          ? CumulusColors.borderStrong.withValues(alpha: 0.55)
          : CumulusColors.borderStrong
      ..strokeWidth = edge.taken ? 2.4 : 1.6
      ..style = PaintingStyle.stroke
      ..strokeJoin = StrokeJoin.round;
    final dy = edge.to.dy - edge.from.dy;
    final path = Path()..moveTo(edge.from.dx, edge.from.dy);
    if (edge.bypassX case final lane?) {
      // Both controls on the lane, at a third and two thirds of the drop, so
      // the curve leaves and rejoins vertically but bulges clear in between.
      path.cubicTo(
        lane,
        edge.from.dy + dy * 0.35,
        lane,
        edge.from.dy + dy * 0.65,
        edge.to.dx,
        edge.to.dy,
      );
    } else {
      final control = math.max(26.0, dy.abs() / 2);
      path.cubicTo(
        edge.from.dx,
        edge.from.dy + control,
        edge.to.dx,
        edge.to.dy - control,
        edge.to.dx,
        edge.to.dy,
      );
    }
    canvas.drawPath(path, paint);
    // Arrow into the top edge of the target.
    canvas.drawPath(
      Path()
        ..moveTo(edge.to.dx - 4.5, edge.to.dy - 7)
        ..lineTo(edge.to.dx, edge.to.dy)
        ..lineTo(edge.to.dx + 4.5, edge.to.dy - 7),
      paint,
    );
    if (edge.label.isNotEmpty) {
      _label(canvas, edge);
    }
  }

  void _label(Canvas canvas, _Edge edge) {
    final painter = TextPainter(
      text: TextSpan(
        text: edge.label,
        style: TextStyle(
          fontSize: 10,
          fontWeight: FontWeight.w600,
          color: edge.taken
              ? CumulusColors.accentInk
              : edge.ghost
              ? CumulusColors.inkMuted
              : CumulusColors.inkSecondary,
        ),
      ),
      textDirection: TextDirection.ltr,
    )..layout();
    // On the curve, not on the straight line between the endpoints — a
    // bypassing edge is nowhere near that line.
    final mid = edge.midpoint;
    final box = Rect.fromCenter(
      center: mid,
      width: painter.width + 14,
      height: painter.height + 8,
    );
    canvas.drawRRect(
      RRect.fromRectAndRadius(box, const Radius.circular(99)),
      Paint()..color = CumulusColors.surface,
    );
    canvas.drawRRect(
      RRect.fromRectAndRadius(box, const Radius.circular(99)),
      Paint()
        ..color = edge.taken ? CumulusColors.accent : CumulusColors.border
        ..style = PaintingStyle.stroke,
    );
    painter.paint(
      canvas,
      Offset(mid.dx - painter.width / 2, mid.dy - painter.height / 2),
    );
  }

  @override
  bool shouldRepaint(_EdgePainter old) => old.edges != edges;
}

class _CanvasNode extends StatelessWidget {
  final _PlacedNode node;
  final bool selected;

  /// Null when no run is selected; otherwise the journal's status for this
  /// step, and whether the run reached it at all.
  final String? runStatus;
  final bool? inRun;

  final VoidCallback onTap;

  const _CanvasNode({
    required this.node,
    required this.selected,
    required this.runStatus,
    required this.inRun,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final step = node.step;
    final tone = stepTone(step.type);
    final badge = governedBadge(step.type);
    final removed = node.change == 'removed';
    final added = node.change == 'added';
    final modified = node.change == 'modified';
    // A step the selected run never reached is drawn as a ghost: still in
    // position, so the shape of the definition survives, but visibly not part
    // of what happened.
    final excluded = inRun == false;

    final borderColor = selected
        ? CumulusColors.ink
        : runStatus == 'failed'
        ? CumulusColors.danger
        : runStatus == 'suspended'
        ? CumulusColors.accent
        : inRun == true
        ? CumulusColors.accent.withValues(alpha: 0.55)
        // Outside the run: the lightest border there is, so the card reads as
        // one flat muted block rather than an outlined empty one.
        : inRun == false
        ? CumulusColors.border
        : added
        ? CumulusColors.success
        : removed
        ? CumulusColors.danger
        : modified
        ? CumulusColors.warn
        : CumulusColors.border;

    final config = [
      if (step.config['model'] != null) step.config['model']!,
      if (step.config['queue'] != null) 'queue: ${step.config['queue']}',
      if (step.config['threshold'] != null) '≥ ${step.config['threshold']}',
      if (step.config['target'] != null) '→ ${step.config['target']}',
    ].join(' · ');

    final note = switch (node.change) {
            'added' => 'added in this draft',
            'removed' => 'removed in this draft',
            'modified' => 'changed: ${node.changedFields.join(', ')}',
      _ => switch (runStatus) {
        'failed' => 'failed in this run',
        'suspended' => 'waiting on a human',
        _ => inRun == false ? 'not taken in this run' : null,
      },
    };
    final noteColor = switch (node.change) {
      'added' => CumulusColors.success,
      'removed' => CumulusColors.danger,
      'modified' => CumulusColors.warn,
      _ => runStatus == 'failed'
          ? CumulusColors.danger
          : runStatus == 'suspended'
          ? CumulusColors.accent
          : CumulusColors.inkMuted,
    };

    // A step outside this run is muted, not made transparent. Transparency
    // reads as a rendering fault — half-drawn rather than deliberately off —
    // and it lets the dotted field show through the card. A flat muted fill
    // with greyed content recedes just as far and looks intended.
    final inert = removed || excluded;
    final surface = inert ? CumulusColors.surfaceMuted : CumulusColors.surface;

    return Material(
      color: surface,
      borderRadius: BorderRadius.circular(CumulusRadius.control),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(CumulusRadius.control),
        child: Container(
          padding: const EdgeInsets.fromLTRB(13, 10, 13, 10),
          decoration: BoxDecoration(
            color: surface,
            borderRadius: BorderRadius.circular(CumulusRadius.control),
            border: Border.all(
              color: borderColor,
              width: selected ? 2 : (added || removed || modified) ? 1.6 : 1,
            ),
            boxShadow: inert
                ? const []
                : added
                ? [
                    BoxShadow(
                      color: CumulusColors.success.withValues(alpha: 0.22),
                      blurRadius: 14,
                      spreadRadius: 1,
                    ),
                  ]
                : CumulusShadows.card,
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Container(
                    width: 7,
                    height: 7,
                    decoration: BoxDecoration(
                      color: inert ? CumulusColors.inkMuted : tone.dot,
                      shape: BoxShape.circle,
                    ),
                  ),
                  const SizedBox(width: 6),
                  Text(
                    step.type.toUpperCase(),
                    style: TextStyle(
                      fontSize: 9.5,
                      fontWeight: FontWeight.w700,
                      letterSpacing: 1,
                      color: inert
                          ? CumulusColors.inkMuted
                          : tone.foreground,
                    ),
                  ),
                  const Spacer(),
                  // The governed-operation badge is a claim about what this
                  // step does when it runs. On a step this run never reached
                  // it is noise, so it is withheld rather than greyed.
                  if (badge != null && !inert)
                    Container(
                      padding: const EdgeInsets.symmetric(
                        horizontal: 6,
                        vertical: 2,
                      ),
                      decoration: BoxDecoration(
                        color: tone.background,
                        borderRadius: BorderRadius.circular(99),
                      ),
                      child: Text(
                        badge,
                        style: TextStyle(
                          fontSize: 8,
                          fontWeight: FontWeight.w700,
                          letterSpacing: 0.6,
                          color: tone.foreground,
                        ),
                      ),
                    ),
                ],
              ),
              const SizedBox(height: 6),
              Text(
                step.name,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: CumulusText.mono.copyWith(
                  fontSize: 12,
                  fontWeight: FontWeight.w600,
                  color: inert ? CumulusColors.inkMuted : CumulusColors.ink,
                  decoration: removed ? TextDecoration.lineThrough : null,
                ),
              ),
              const SizedBox(height: 2),
              Expanded(
                child: Text(
                  config.isEmpty ? step.summary : config,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    fontSize: 10.5,
                    color: CumulusColors.inkMuted,
                  ),
                ),
              ),
              if (note != null)
                Text(
                  note,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    fontSize: 9.5,
                    fontWeight: FontWeight.w600,
                    color: noteColor,
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}
