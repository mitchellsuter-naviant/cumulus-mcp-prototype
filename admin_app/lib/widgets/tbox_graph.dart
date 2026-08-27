import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../theme/cumulus_theme.dart';

/// One link between the focused entity and a neighbour, as the graph draws it.
class SpokeEdge {
  final String name;

  /// True when the link runs *from* the focused entity.
  final bool outbound;

  /// association | composition | identity.
  final String nature;
  final String cardinality;

  const SpokeEdge({
    required this.name,
    required this.outbound,
    required this.nature,
    required this.cardinality,
  });
}

/// A neighbour of the focused entity, with every link that reaches it.
///
/// Grouped by neighbour rather than by link: two types joined by two links
/// (an Employee *submitted* an Expense request and *decided* one) are one
/// spoke carrying two labels, because drawing the same box twice reads as a
/// bug rather than as two relationships.
class GraphSpoke {
  final String type;
  final String label;
  final int instances;
  final bool isPart;
  final List<SpokeEdge> edges;

  const GraphSpoke({
    required this.type,
    required this.label,
    required this.instances,
    required this.isPart,
    required this.edges,
  });
}

/// The TBox as a hub and spokes: the focused entity in the middle, every
/// entity it links to around it, and the link's own name on the line.
///
/// A schema diagram earns its place only if the edges are readable — a graph
/// of unlabelled lines says "these things are connected" and stops there. So
/// the name rides on the line in a chip, with the direction arrow beside it,
/// and the line itself carries the nature: solid for an association, thicker
/// and tinted for composition (the target is a part), dashed for an identity
/// assertion.
class TBoxGraph extends StatelessWidget {
  final String focusLabel;
  final int focusInstances;
  final List<GraphSpoke> spokes;

  /// Walk a hop: tap a neighbour to make it the focus.
  final ValueChanged<String> onTapType;

  const TBoxGraph({
    super.key,
    required this.focusLabel,
    required this.focusInstances,
    required this.spokes,
    required this.onTapType,
  });

  static const _nodeW = 176.0;
  static const _nodeH = 46.0;
  static const _focusH = 50.0;

  @override
  Widget build(BuildContext context) {
    if (spokes.isEmpty) {
      return const Center(
        child: Padding(
          padding: EdgeInsets.all(32),
          child: Text(
            'This entity declares no links — its substance is its fields.',
            style: CumulusText.meta,
          ),
        ),
      );
    }
    return LayoutBuilder(
      builder: (context, constraints) {
        final w = constraints.maxWidth;
        final h = constraints.maxHeight.isFinite
            ? constraints.maxHeight
            : 520.0;
        final center = Offset(w / 2, h / 2);
        // An ellipse, not a circle: the pane is wider than it is tall, and a
        // circle would waste the width and crowd the vertical.
        final rx = math.max(150.0, (w - _nodeW) / 2 - 24);
        final ry = math.max(96.0, (h - _nodeH) / 2 - 34);
        final positions = _positions(center, rx, ry, spokes.length);

        return Stack(
          children: [
            // Edges first, so the nodes sit on top of their lines.
            Positioned.fill(
              child: CustomPaint(
                painter: _SpokePainter(
                  center: center,
                  positions: positions,
                  spokes: spokes,
                  nodeSize: const Size(_nodeW, _nodeH),
                  focusSize: const Size(_nodeW + 16, _focusH),
                ),
              ),
            ),
            for (final (i, spoke) in spokes.indexed) ...[
              _halo(positions[i]),
              _label(center, positions[i], spoke, i),
              _node(positions[i], spoke),
            ],
            _focus(center),
          ],
        );
      },
    );
  }

  /// Where the spokes sit. Two or fewer go left-and-right, which uses a wide
  /// pane; three or more start at the top and space evenly around.
  List<Offset> _positions(Offset center, double rx, double ry, int n) {
    final angles = <double>[];
    if (n == 1) {
      angles.add(0);
    } else if (n == 2) {
      angles.addAll([0, math.pi]);
    } else {
      for (var i = 0; i < n; i++) {
        angles.add(-math.pi / 2 + i * 2 * math.pi / n);
      }
    }
    return [
      for (final a in angles)
        Offset(center.dx + rx * math.cos(a), center.dy + ry * math.sin(a)),
    ];
  }

  Widget _halo(Offset at) {
    return Positioned(
      left: at.dx - _nodeW / 2,
      top: at.dy - _nodeH / 2,
      child: IgnorePointer(
        child: Container(
          width: _nodeW,
          height: _nodeH,
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(14),
            boxShadow: [
              BoxShadow(
                color: CumulusColors.accent.withValues(alpha: 0.07),
                blurRadius: 34,
                spreadRadius: 10,
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _focus(Offset at) {
    return Positioned(
      left: at.dx - (_nodeW + 16) / 2,
      top: at.dy - _focusH / 2,
      child: Container(
        width: _nodeW + 16,
        height: _focusH,
        alignment: Alignment.center,
        decoration: BoxDecoration(
          color: CumulusColors.surface,
          borderRadius: BorderRadius.circular(CumulusRadius.pill),
          border: Border.all(color: CumulusColors.accent, width: 1.4),
          boxShadow: CumulusShadows.card,
        ),
        child: Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Container(
              width: 7,
              height: 7,
              decoration: const BoxDecoration(
                color: CumulusColors.accent,
                shape: BoxShape.circle,
              ),
            ),
            const SizedBox(width: 8),
            Flexible(
              child: Text(
                focusLabel,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(
                  fontSize: 14,
                  fontWeight: FontWeight.w600,
                  color: CumulusColors.ink,
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _node(Offset at, GraphSpoke spoke) {
    return Positioned(
      left: at.dx - _nodeW / 2,
      top: at.dy - _nodeH / 2,
      child: SizedBox(
        width: _nodeW,
        height: _nodeH,
        child: Material(
          color: CumulusColors.surface,
          borderRadius: BorderRadius.circular(14),
          elevation: 0,
          child: InkWell(
            onTap: () => onTapType(spoke.type),
            borderRadius: BorderRadius.circular(14),
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 14),
              decoration: BoxDecoration(
                borderRadius: BorderRadius.circular(14),
                border: Border.all(
                  color: spoke.isPart
                      ? CumulusColors.accentSoft
                      : CumulusColors.borderStrong,
                ),
              ),
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    spoke.label,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                      fontSize: 13,
                      fontWeight: FontWeight.w600,
                      color: CumulusColors.ink,
                    ),
                  ),
                  Text(
                    spoke.isPart
                        ? 'part · ${spoke.instances} in the graph'
                        : '${spoke.instances} in the graph',
                    style: CumulusText.statCaption,
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }

  /// The link name, on the line. Staggered along adjacent spokes so two
  /// chips never land on top of each other.
  Widget _label(Offset center, Offset at, GraphSpoke spoke, int index) {
    final t = index.isEven ? 0.56 : 0.44;
    final point = Offset(
      center.dx + (at.dx - center.dx) * t,
      center.dy + (at.dy - center.dy) * t,
    );
    return Positioned(
      left: point.dx - 90,
      top: point.dy - 30,
      width: 180,
      height: 60,
      child: IgnorePointer(
        child: Center(
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 5),
            decoration: BoxDecoration(
              color: CumulusColors.surface,
              borderRadius: BorderRadius.circular(8),
              border: Border.all(color: CumulusColors.border),
            ),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                for (final edge in spoke.edges)
                  Padding(
                    padding: const EdgeInsets.symmetric(vertical: 1),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Icon(
                          edge.outbound
                              ? Icons.arrow_forward
                              : Icons.arrow_back,
                          size: 10,
                          color: CumulusColors.inkMuted,
                        ),
                        const SizedBox(width: 4),
                        Text(
                          edge.name,
                          style: CumulusText.mono.copyWith(
                            fontSize: 10.5,
                            color: switch (edge.nature) {
                              'composition' => CumulusColors.accentInk,
                              'identity' => CumulusColors.teal,
                              _ => CumulusColors.inkSecondary,
                            },
                          ),
                        ),
                      ],
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

class _SpokePainter extends CustomPainter {
  final Offset center;
  final List<Offset> positions;
  final List<GraphSpoke> spokes;
  final Size nodeSize;
  final Size focusSize;

  _SpokePainter({
    required this.center,
    required this.positions,
    required this.spokes,
    required this.nodeSize,
    required this.focusSize,
  });

  @override
  void paint(Canvas canvas, Size size) {
    for (final (i, spoke) in spokes.indexed) {
      final target = positions[i];
      final from = _edgeOf(center, target, focusSize);
      final to = _edgeOf(target, center, nodeSize);
      final nature = spoke.edges.first.nature;
      final paint = Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = nature == 'composition' ? 1.6 : 1.1
        ..color = switch (nature) {
          'composition' => CumulusColors.accent.withValues(alpha: 0.45),
          'identity' => CumulusColors.teal.withValues(alpha: 0.5),
          _ => CumulusColors.borderStrong,
        };
      if (nature == 'identity') {
        _dashed(canvas, from, to, paint);
      } else {
        canvas.drawLine(from, to, paint);
      }
      // An arrowhead at each end the links actually point to, so direction
      // survives without reading the chip.
      if (spoke.edges.any((e) => e.outbound)) {
        _arrow(canvas, from, to, paint.color);
      }
      if (spoke.edges.any((e) => !e.outbound)) {
        _arrow(canvas, to, from, paint.color);
      }
    }
  }

  /// Where a line from [a] to [b] leaves a's box.
  Offset _edgeOf(Offset a, Offset b, Size box) {
    final dx = b.dx - a.dx;
    final dy = b.dy - a.dy;
    if (dx == 0 && dy == 0) return a;
    final halfW = box.width / 2 + 6;
    final halfH = box.height / 2 + 6;
    final scale = math.min(
      dx == 0 ? double.infinity : halfW / dx.abs(),
      dy == 0 ? double.infinity : halfH / dy.abs(),
    );
    return Offset(a.dx + dx * scale, a.dy + dy * scale);
  }

  void _arrow(Canvas canvas, Offset from, Offset to, Color color) {
    final angle = math.atan2(to.dy - from.dy, to.dx - from.dx);
    const len = 7.0;
    const spread = 0.42;
    final tip = to;
    final path = Path()
      ..moveTo(tip.dx, tip.dy)
      ..lineTo(
        tip.dx - len * math.cos(angle - spread),
        tip.dy - len * math.sin(angle - spread),
      )
      ..lineTo(
        tip.dx - len * math.cos(angle + spread),
        tip.dy - len * math.sin(angle + spread),
      )
      ..close();
    canvas.drawPath(path, Paint()..color = color);
  }

  void _dashed(Canvas canvas, Offset from, Offset to, Paint paint) {
    const dash = 5.0;
    const gap = 4.0;
    final total = (to - from).distance;
    final dir = (to - from) / total;
    var travelled = 0.0;
    while (travelled < total) {
      final end = math.min(travelled + dash, total);
      canvas.drawLine(from + dir * travelled, from + dir * end, paint);
      travelled = end + gap;
    }
  }

  @override
  bool shouldRepaint(covariant _SpokePainter old) =>
      old.center != center ||
      old.positions != positions ||
      old.spokes != spokes;
}
