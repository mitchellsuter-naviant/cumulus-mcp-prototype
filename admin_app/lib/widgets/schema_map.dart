import 'package:flutter/material.dart';

import '../models/graph.dart';
import '../theme/cumulus_theme.dart';
import 'graph_layout.dart';

/// The TBox as a diagram: object types as boxes, link types as connectors,
/// with what is actually in the graph inside each box.
///
/// Deliberately **not** force-directed. At thirteen types a hand-assigned
/// layout beats any automatic one, and the layout itself carries meaning —
/// each row is a spine (the expense chain, the vendor/AP chain, procurement,
/// receiving), so reading left to right follows the direction facts actually
/// flow. The generic whole-graph picture is the thing to avoid: at this size
/// it is a hairball and at any larger size it is a screensaver.
///
/// A type with no instances draws hollow: a declared type nothing has written
/// yet is a promise, and worth being able to see as one.
class SchemaMap extends StatelessWidget {
  final Ontology ontology;

  /// Called when a type with instances is clicked, to browse them.
  final void Function(String type)? onBrowse;

  const SchemaMap({super.key, required this.ontology, this.onBrowse});

  static const _boxWidth = 152.0;
  static const _boxHeight = 66.0;
  // Wide enough for a link name to sit in the gutter without a box clipping
  // it — the labels are the diagram's whole vocabulary.
  static const _gapX = 106.0;
  static const _gapY = 38.0;
  static const _columns = ontologyColumns;
  static const _rows = ontologyRows;

  static Offset _origin(String type) {
    final cell = cellFor(type);
    return Offset(
      cell.$1 * (_boxWidth + _gapX),
      cell.$2 * (_boxHeight + _gapY),
    );
  }

  static Rect _rect(String type) =>
      Rect.fromLTWH(_origin(type).dx, _origin(type).dy, _boxWidth, _boxHeight);

  @override
  Widget build(BuildContext context) {
    final width = _columns * _boxWidth + (_columns - 1) * _gapX;
    final height = _rows * _boxHeight + (_rows - 1) * _gapY;
    final placed = ontology.objects;
    final edges = ontology.links.toList();

    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      child: SizedBox(
        width: width,
        height: height,
        child: Stack(
          children: [
            Positioned.fill(
              child: CustomPaint(painter: _EdgePainter(edges: edges)),
            ),
            for (final type in placed)
              Positioned(
                left: _origin(type.name).dx,
                top: _origin(type.name).dy,
                width: _boxWidth,
                height: _boxHeight,
                child: _TypeBox(
                  type: type,
                  onTap: type.instances == 0 || onBrowse == null
                      ? null
                      : () => onBrowse!(type.name),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

class _TypeBox extends StatelessWidget {
  final OntologyObjectType type;
  final VoidCallback? onTap;

  const _TypeBox({required this.type, this.onTap});

  @override
  Widget build(BuildContext context) {
    final empty = type.instances == 0;
    return Tooltip(
      message:
          '${type.summary}\n\nNatural key: ${type.naturalKey}\n'
          'Collection: ${type.collection}'
          '${type.authoritative ? '\nAuthoritative — a flow may read it as canonical.' : ''}',
      child: Material(
        color: empty ? CumulusColors.canvas : CumulusColors.surface,
        borderRadius: BorderRadius.circular(11),
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(11),
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 8),
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(11),
              border: Border.all(
                color: empty
                    ? CumulusColors.borderStrong
                    : type.authoritative
                        ? CumulusColors.accent
                        : CumulusColors.border,
                width: empty ? 1 : 1.3,
              ),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Text(
                  type.name,
                  style: TextStyle(
                    fontSize: 12.5,
                    fontWeight: FontWeight.w600,
                    color: empty ? CumulusColors.inkMuted : CumulusColors.ink,
                  ),
                  overflow: TextOverflow.ellipsis,
                ),
                const SizedBox(height: 3),
                Row(
                  children: [
                    Text(
                      empty ? 'declared only' : '${type.instances}',
                      style: TextStyle(
                        fontSize: empty ? 10.5 : 14,
                        fontWeight: FontWeight.w600,
                        color: empty
                            ? CumulusColors.inkMuted
                            : CumulusColors.accentInk,
                      ),
                    ),
                    if (!empty) ...[
                      const SizedBox(width: 5),
                      Text('in the graph', style: CumulusText.statCaption),
                    ],
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

/// Orthogonal connectors between the boxes.
///
/// Three cases, in order of how common the layout makes them: straight right
/// (the spine), straight up (a child booking against its parent), and an elbow
/// for anything that jumps a lane. Identity links draw dashed, because an
/// assertion that can be withdrawn is a different kind of edge from one that
/// cannot.
class _EdgePainter extends CustomPainter {
  final List<OntologyLinkType> edges;

  _EdgePainter({required this.edges});

  @override
  void paint(Canvas canvas, Size size) {
    for (final edge in edges) {
      final from = SchemaMap._rect(edge.fromType);
      final to = SchemaMap._rect(edge.toType);
      final live = edge.instances > 0;
      final paint = Paint()
        ..color = live
            ? (edge.identity
                ? CumulusColors.accent.withValues(alpha: 0.85)
                : CumulusColors.borderStrong)
            : CumulusColors.border
        ..strokeWidth = live ? 1.6 : 1.1
        ..style = PaintingStyle.stroke;

      final (path, labelAt) = _route(from, to);
      if (edge.identity) {
        _dashed(canvas, path, paint);
      } else {
        canvas.drawPath(path, paint);
      }
      _arrowHead(canvas, path, paint..style = PaintingStyle.fill);
      _label(canvas, edge, labelAt, live);
    }
  }

  /// The path, and where its label should sit.
  (Path, Offset) _route(Rect from, Rect to) {
    final path = Path();
    final sameRow = (from.top - to.top).abs() < 1;
    final sameColumn = (from.left - to.left).abs() < 1;
    // Adjacent means "nothing in between": a straight line across two columns
    // would run through whatever box sits between them.
    final adjacent =
        (from.left - to.left).abs() < SchemaMap._boxWidth + SchemaMap._gapX + 1;

    if (sameRow && adjacent) {
      final rightwards = to.left > from.left;
      final start = Offset(
        rightwards ? from.right : from.left,
        from.center.dy + (rightwards ? -6 : 8),
      );
      final end = Offset(
        rightwards ? to.left : to.right,
        to.center.dy + (rightwards ? -6 : 8),
      );
      path.moveTo(start.dx, start.dy);
      path.lineTo(end.dx, end.dy);
      // A back-edge's label goes under the line so it cannot collide with the
      // forward edge's between the same pair of boxes.
      return (
        path,
        Offset((start.dx + end.dx) / 2, start.dy + (rightwards ? -9 : 10)),
      );
    }
    if (sameRow) {
      // Same row, boxes in between: drop into the gutter below and come back
      // up, rather than drawing through them.
      final rightwards = to.left > from.left;
      final start = Offset(rightwards ? from.right : from.left, from.center.dy);
      final end = Offset(to.center.dx, to.bottom);
      final gutter = from.bottom + SchemaMap._gapY / 2;
      path.moveTo(start.dx, start.dy);
      path.lineTo(start.dx + (rightwards ? 12 : -12), start.dy);
      path.lineTo(start.dx + (rightwards ? 12 : -12), gutter);
      path.lineTo(end.dx, gutter);
      path.lineTo(end.dx, end.dy);
      return (path, Offset((start.dx + end.dx) / 2, gutter - 9));
    }
    if (sameColumn) {
      final upwards = to.top < from.top;
      final start = Offset(from.center.dx, upwards ? from.top : from.bottom);
      final end = Offset(to.center.dx, upwards ? to.bottom : to.top);
      path.moveTo(start.dx, start.dy);
      path.lineTo(end.dx, end.dy);
      return (path, Offset(start.dx + 8, (start.dy + end.dy) / 2));
    }
    // An elbow: out of the side, along the gutter between the rows, then in.
    final rightwards = to.center.dx > from.center.dx;
    final start = Offset(rightwards ? from.right : from.left, from.center.dy);
    final end = Offset(to.center.dx, to.top > from.top ? to.top : to.bottom);
    final gutter = to.top > from.top
        ? from.bottom + SchemaMap._gapY / 2
        : from.top - SchemaMap._gapY / 2;
    path.moveTo(start.dx, start.dy);
    path.lineTo(start.dx + (rightwards ? 14 : -14), start.dy);
    path.lineTo(start.dx + (rightwards ? 14 : -14), gutter);
    path.lineTo(end.dx, gutter);
    path.lineTo(end.dx, end.dy);
    return (path, Offset((start.dx + end.dx) / 2, gutter - 9));
  }

  void _dashed(Canvas canvas, Path path, Paint paint) {
    const dash = 5.0;
    const gap = 4.0;
    for (final metric in path.computeMetrics()) {
      var distance = 0.0;
      while (distance < metric.length) {
        final next = (distance + dash).clamp(0.0, metric.length);
        canvas.drawPath(metric.extractPath(distance, next), paint);
        distance = next + gap;
      }
    }
  }

  void _arrowHead(Canvas canvas, Path path, Paint paint) {
    final metrics = path.computeMetrics().toList();
    if (metrics.isEmpty) return;
    final metric = metrics.last;
    final tangent = metric.getTangentForOffset(metric.length);
    if (tangent == null) return;
    final tip = tangent.position;
    final direction = tangent.vector / tangent.vector.distance;
    final normal = Offset(-direction.dy, direction.dx);
    final base = tip - direction * 7.0;
    canvas.drawPath(
      Path()
        ..moveTo(tip.dx, tip.dy)
        ..lineTo(base.dx + normal.dx * 3.6, base.dy + normal.dy * 3.6)
        ..lineTo(base.dx - normal.dx * 3.6, base.dy - normal.dy * 3.6)
        ..close(),
      paint,
    );
  }

  void _label(Canvas canvas, OntologyLinkType edge, Offset at, bool live) {
    final painter = TextPainter(
      text: TextSpan(
        text: edge.name,
        style: TextStyle(
          fontSize: 9.5,
          fontWeight: FontWeight.w600,
          letterSpacing: 0.1,
          color: live ? CumulusColors.inkSecondary : CumulusColors.inkMuted,
        ),
      ),
      textDirection: TextDirection.ltr,
    )..layout();
    final origin = Offset(at.dx - painter.width / 2, at.dy - painter.height / 2);
    final box = Rect.fromLTWH(
      origin.dx - 3,
      origin.dy - 1,
      painter.width + 6,
      painter.height + 2,
    );
    canvas.drawRRect(
      RRect.fromRectAndRadius(box, const Radius.circular(4)),
      Paint()..color = CumulusColors.canvas,
    );
    painter.paint(canvas, origin);
  }

  @override
  bool shouldRepaint(_EdgePainter oldDelegate) => oldDelegate.edges != edges;
}
