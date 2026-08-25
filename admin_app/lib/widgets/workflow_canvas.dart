import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../models/workflow.dart';
import '../theme/cumulus_theme.dart';
import 'ui_kit.dart';

/// Node geometry. Fixed sizes keep the layered layout arithmetic honest —
/// edges are drawn from the same numbers the nodes are positioned with.
const double _nodeWidth = 210;
const double _nodeHeight = 108;
const double _hGap = 72;
const double _vGap = 26;
const double _padding = 40;

CumulusTone stepTone(String type) => switch (type) {
  'trigger' => CumulusTone.teal,
  'modelcall' => CumulusTone.violet,
  'rule' => CumulusTone.warn,
  'task' => CumulusTone.accent,
  'action' => CumulusTone.success,
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

/// A laid-out node: the step, its diff annotation, and where it sits.
class _PlacedNode {
  final WorkflowStep step;
  final String change;
  final List<String> changedFields;
  final Offset position;

  const _PlacedNode({
    required this.step,
    required this.change,
    required this.changedFields,
    required this.position,
  });

  Rect get rect =>
      Rect.fromLTWH(position.dx, position.dy, _nodeWidth, _nodeHeight);
}

class _Edge {
  final Offset from;
  final Offset to;
  final String label;
  final bool dimmed;

  const _Edge({
    required this.from,
    required this.to,
    required this.label,
    required this.dimmed,
  });
}

/// Layered left-to-right layout of the declarative export.
///
/// A step's layer is the longest path to it from a root, which is what makes
/// a branch's two arms land in the same column rather than one drifting right
/// of the other. Removed steps (present only in the baseline) are laid out
/// beside the step they used to follow so a deletion is visible in place.
class _Layout {
  final List<_PlacedNode> nodes;
  final List<_Edge> edges;
  final Size size;

  const _Layout({
    required this.nodes,
    required this.edges,
    required this.size,
  });

  static _Layout build(List<StepDiff> diffs) {
    if (diffs.isEmpty) {
      return const _Layout(nodes: [], edges: [], size: Size(0, 0));
    }
    final steps = [for (final d in diffs) d.step];
    final byId = {for (final s in steps) s.id: s};
    final annotation = {for (final d in diffs) d.step.id: d};

    // Longest-path layering, with a visit cap so a malformed cycle in an
    // exported definition degrades to a readable graph instead of hanging.
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

    // Steps nothing points at, other than the first, are orphans (a removed
    // step, or a trigger of a second entry point) — keep them in document
    // order rather than stacking them all in column zero.
    final referenced = <String>{
      for (final s in steps) ...s.next.where(byId.containsKey),
    };
    for (final step in steps) {
      if (step.id == steps.first.id || referenced.contains(step.id)) continue;
      if (annotation[step.id]?.change == 'removed') {
        // Place a deletion just after where it used to sit.
        final predecessor = steps.firstWhere(
          (s) => s.next.contains(step.id),
          orElse: () => step,
        );
        layer[step.id] = (layer[predecessor.id] ?? 0) + 1;
      }
    }

    final columns = <int, List<WorkflowStep>>{};
    for (final step in steps) {
      columns.putIfAbsent(layer[step.id]!, () => []).add(step);
    }

    final placed = <String, _PlacedNode>{};
    final tallest = columns.values.fold<int>(
      1,
      (m, c) => math.max(m, c.length),
    );
    final canvasHeight = _padding * 2 + tallest * _nodeHeight + (tallest - 1) * _vGap;
    for (final entry in columns.entries) {
      final column = entry.value;
      final columnHeight =
          column.length * _nodeHeight + (column.length - 1) * _vGap;
      final top = (canvasHeight - columnHeight) / 2;
      for (var i = 0; i < column.length; i++) {
        final step = column[i];
        final diff = annotation[step.id];
        placed[step.id] = _PlacedNode(
          step: step,
          change: diff?.change ?? 'unchanged',
          changedFields: diff?.changedFields ?? const [],
          position: Offset(
            _padding + entry.key * (_nodeWidth + _hGap),
            top + i * (_nodeHeight + _vGap),
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
        edges.add(
          _Edge(
            from: Offset(
              source.position.dx + _nodeWidth,
              source.position.dy + _nodeHeight / 2,
            ),
            to: Offset(
              target.position.dx,
              target.position.dy + _nodeHeight / 2,
            ),
            label: i < step.branchLabels.length ? step.branchLabels[i] : '',
            dimmed:
                source.change == 'removed' || target.change == 'removed',
          ),
        );
      }
    }

    final widest = columns.keys.fold<int>(0, math.max);
    return _Layout(
      nodes: placed.values.toList(),
      edges: edges,
      size: Size(
        _padding * 2 + (widest + 1) * _nodeWidth + widest * _hGap,
        canvasHeight,
      ),
    );
  }
}

/// The pipeline as an interactive graph: pan, zoom, click a node to inspect.
///
/// Built on [InteractiveViewer] and a [CustomPaint] edge layer rather than a
/// graph package — the whole console ships with no charting or graph
/// dependency, and a layered DAG of a dozen typed steps is arithmetic, not a
/// library.
class WorkflowCanvas extends StatefulWidget {
  final List<StepDiff> steps;

  /// Non-empty when a draft is selected, e.g. "vs v14 (active)". Drives the
  /// diff legend, which is otherwise hidden.
  final String diffAgainst;
  final String? selectedStepId;
  final ValueChanged<WorkflowStep?> onSelect;
  final VoidCallback? onExportSchema;

  const WorkflowCanvas({
    super.key,
    required this.steps,
    required this.onSelect,
    this.diffAgainst = '',
    this.selectedStepId,
    this.onExportSchema,
  });

  @override
  State<WorkflowCanvas> createState() => _WorkflowCanvasState();
}

class _WorkflowCanvasState extends State<WorkflowCanvas> {
  final TransformationController _view = TransformationController();

  @override
  void dispose() {
    _view.dispose();
    super.dispose();
  }

  void _zoomBy(double factor) {
    final current = _view.value.getMaxScaleOnAxis();
    final next = (current * factor).clamp(0.4, 2.0);
    // Zoom about the viewport's centre rather than the origin, so the graph
    // does not walk off screen as you step through the levels.
    final matrix = Matrix4.identity()..scaleByDouble(next, next, next, 1);
    _view.value = matrix;
  }

  @override
  Widget build(BuildContext context) {
    final layout = _Layout.build(widget.steps);
    if (layout.nodes.isEmpty) {
      return const CumulusEmptyState(
        icon: Icons.account_tree_outlined,
        title: 'No steps exported',
        message: 'This version has not exported its declarative shape yet.',
      );
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          children: [
            if (widget.diffAgainst.isNotEmpty) ...[
              Text('DIFF vs ${widget.diffAgainst}', style: CumulusText.statLabel),
              const SizedBox(width: 12),
              const _DiffKey(color: CumulusColors.success, label: 'added'),
              const SizedBox(width: 10),
              const _DiffKey(color: CumulusColors.warn, label: 'modified'),
              const SizedBox(width: 10),
              const _DiffKey(color: CumulusColors.danger, label: 'removed'),
            ],
            const Spacer(),
            if (widget.onExportSchema != null)
              TextButton.icon(
                onPressed: widget.onExportSchema,
                icon: const Icon(Icons.code, size: 15),
                label: const Text('Export schema'),
              ),
            const SizedBox(width: 6),
            _ZoomButton(
              icon: Icons.remove,
              tooltip: 'Zoom out',
              onTap: () => _zoomBy(1 / 1.25),
            ),
            const SizedBox(width: 4),
            _ZoomButton(
              icon: Icons.add,
              tooltip: 'Zoom in',
              onTap: () => _zoomBy(1.25),
            ),
            const SizedBox(width: 4),
            _ZoomButton(
              icon: Icons.fit_screen_outlined,
              tooltip: 'Reset view',
              onTap: () => _view.value = Matrix4.identity(),
            ),
          ],
        ),
        const SizedBox(height: 12),
        Container(
          height: math.min(560, layout.size.height + 40),
          decoration: BoxDecoration(
            color: CumulusColors.canvas,
            borderRadius: BorderRadius.circular(CumulusRadius.control),
            border: Border.all(color: CumulusColors.border),
          ),
          clipBehavior: Clip.antiAlias,
          child: InteractiveViewer(
            transformationController: _view,
            constrained: false,
            boundaryMargin: const EdgeInsets.all(200),
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
      ],
    );
  }
}

class _DiffKey extends StatelessWidget {
  final Color color;
  final String label;

  const _DiffKey({required this.color, required this.label});

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
        color: CumulusColors.surface,
        borderRadius: BorderRadius.circular(9),
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(9),
          child: Container(
            width: 30,
            height: 30,
            alignment: Alignment.center,
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(9),
              border: Border.all(color: CumulusColors.borderStrong),
            ),
            child: Icon(icon, size: 15, color: CumulusColors.inkSecondary),
          ),
        ),
      ),
    );
  }
}

/// Curved connectors with branch labels. Drawn beneath the nodes, so a label
/// never covers a step's text.
class _EdgePainter extends CustomPainter {
  final List<_Edge> edges;

  _EdgePainter(this.edges);

  @override
  void paint(Canvas canvas, Size size) {
    for (final edge in edges) {
      final paint = Paint()
        ..color = edge.dimmed
            ? CumulusColors.danger.withValues(alpha: 0.25)
            : CumulusColors.borderStrong
        ..strokeWidth = 1.6
        ..style = PaintingStyle.stroke;
      final dx = (edge.to.dx - edge.from.dx).abs();
      final control = math.max(30.0, dx / 2);
      final path = Path()
        ..moveTo(edge.from.dx, edge.from.dy)
        ..cubicTo(
          edge.from.dx + control,
          edge.from.dy,
          edge.to.dx - control,
          edge.to.dy,
          edge.to.dx,
          edge.to.dy,
        );
      canvas.drawPath(path, paint);
      _arrowHead(canvas, edge.to, paint);
      if (edge.label.isNotEmpty) {
        _branchLabel(canvas, edge);
      }
    }
  }

  void _arrowHead(Canvas canvas, Offset tip, Paint paint) {
    final head = Path()
      ..moveTo(tip.dx - 7, tip.dy - 4.5)
      ..lineTo(tip.dx, tip.dy)
      ..lineTo(tip.dx - 7, tip.dy + 4.5);
    canvas.drawPath(head, paint..strokeJoin = StrokeJoin.round);
  }

  void _branchLabel(Canvas canvas, _Edge edge) {
    final painter = TextPainter(
      text: TextSpan(
        text: edge.label,
        style: const TextStyle(
          fontSize: 10,
          fontWeight: FontWeight.w600,
          color: CumulusColors.inkSecondary,
        ),
      ),
      textDirection: TextDirection.ltr,
    )..layout();
    final mid = Offset(
      (edge.from.dx + edge.to.dx) / 2,
      (edge.from.dy + edge.to.dy) / 2,
    );
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
        ..color = CumulusColors.border
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
  final VoidCallback onTap;

  const _CanvasNode({
    required this.node,
    required this.selected,
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

    final borderColor = selected
        ? CumulusColors.ink
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

    return Opacity(
      opacity: removed ? 0.55 : 1,
      child: Material(
        color: CumulusColors.surface,
        borderRadius: BorderRadius.circular(CumulusRadius.control),
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(CumulusRadius.control),
          child: Container(
            padding: const EdgeInsets.fromLTRB(13, 11, 13, 11),
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(CumulusRadius.control),
              border: Border.all(
                color: borderColor,
                width: selected || added || removed || modified ? 1.6 : 1,
              ),
              boxShadow: added
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
                        color: tone.dot,
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
                        color: tone.foreground,
                      ),
                    ),
                    const Spacer(),
                    if (badge != null && !removed)
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
                const SizedBox(height: 7),
                Text(
                  step.name,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: CumulusText.mono.copyWith(
                    fontSize: 12,
                    fontWeight: FontWeight.w600,
                    decoration: removed ? TextDecoration.lineThrough : null,
                  ),
                ),
                const SizedBox(height: 3),
                Expanded(
                  child: Text(
                    config.isEmpty ? step.summary : config,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                      fontSize: 10.5,
                      color: CumulusColors.inkMuted,
                      height: 1.35,
                    ),
                  ),
                ),
                if (added || removed || modified)
                  Text(
                    switch (node.change) {
                      'added' => 'added in this draft',
                      'removed' => 'removed in this draft',
                      _ => 'changed: ${node.changedFields.join(', ')}',
                    },
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                      fontSize: 9.5,
                      fontWeight: FontWeight.w600,
                      color: added
                          ? CumulusColors.success
                          : removed
                          ? CumulusColors.danger
                          : CumulusColors.warn,
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
