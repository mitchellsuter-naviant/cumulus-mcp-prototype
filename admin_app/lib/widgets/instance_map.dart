import 'package:flutter/material.dart';

import '../models/world.dart';
import '../theme/cumulus_theme.dart';
import 'graph_layout.dart';
import 'ui_kit.dart';

/// An ABox drawn with the TBox's geometry.
///
/// Every instance is placed in the column and row its *type* occupies in the
/// schema map, stacking within that cell. So a Vendor appears where Vendor
/// appears, an Invoice where Invoice appears, and the model diagram and the
/// instance diagram are the same picture at two levels. Giving them different
/// layouts would hide exactly the continuity worth showing.
///
/// Edges are drawn thin and unlabelled: at instance scale the labels become
/// noise, and the inventory beside the map names every edge type with its
/// count. The picture is for shape; the lists are for facts.
///
/// Cells cap the number of boxes they draw and say how many they dropped —
/// sixteen invoice lines stacked in one column is a scrollbar, not a diagram.
class InstanceMap extends StatelessWidget {
  final List<WorldObject> objects;
  final List<WorldLink> links;

  /// Called when a box is clicked, so the map can drive a detail pane.
  final void Function(WorldObject object)? onSelect;
  final String? selectedId;

  const InstanceMap({
    super.key,
    required this.objects,
    required this.links,
    this.onSelect,
    this.selectedId,
  });

  static const _boxWidth = 168.0;
  static const _boxHeight = 46.0;
  static const _gapX = 42.0;
  static const _cellGap = 8.0;
  static const _rowGap = 26.0;
  static const _perCell = 6;

  /// Instances grouped by their type's grid cell, capped.
  Map<(int, int), List<WorldObject>> get _cells {
    final out = <(int, int), List<WorldObject>>{};
    for (final object in objects) {
      out.putIfAbsent(cellFor(object.type), () => []).add(object);
    }
    for (final entry in out.entries) {
      entry.value.sort((a, b) => a.label.compareTo(b.label));
    }
    return out;
  }

  /// Y offset of each grid row, from the tallest cell in the rows above it.
  List<double> _rowOffsets(Map<(int, int), List<WorldObject>> cells) {
    final heights = List<double>.filled(ontologyRows + 1, 0);
    for (final entry in cells.entries) {
      final shown = entry.value.length.clamp(0, _perCell);
      final extra = entry.value.length > _perCell ? 1 : 0;
      final height = (shown + extra) * (_boxHeight + _cellGap);
      final row = entry.key.$2;
      if (height > heights[row]) heights[row] = height;
    }
    final offsets = <double>[0];
    for (var row = 0; row <= ontologyRows; row++) {
      offsets.add(offsets[row] + heights[row] + (heights[row] > 0 ? _rowGap : 0));
    }
    return offsets;
  }

  @override
  Widget build(BuildContext context) {
    if (objects.isEmpty) {
      return CumulusEmptyState(
        icon: Icons.scatter_plot_outlined,
        title: 'Nothing asserted',
        message:
            'This fragment declares no instances, so there is nothing to draw.',
      );
    }
    final cells = _cells;
    final offsets = _rowOffsets(cells);
    final positions = <String, Offset>{};
    for (final entry in cells.entries) {
      final (column, row) = entry.key;
      for (var i = 0; i < entry.value.length && i < _perCell; i++) {
        positions[entry.value[i].id] = Offset(
          column * (_boxWidth + _gapX),
          offsets[row] + i * (_boxHeight + _cellGap),
        );
      }
    }
    final width = ontologyColumns * _boxWidth + (ontologyColumns - 1) * _gapX;
    final height = offsets[ontologyRows] + 8;

    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      child: SizedBox(
        width: width,
        height: height,
        child: Stack(
          children: [
            Positioned.fill(
              child: CustomPaint(
                painter: _InstanceEdgePainter(
                  links: links,
                  positions: positions,
                  boxWidth: _boxWidth,
                  boxHeight: _boxHeight,
                ),
              ),
            ),
            for (final entry in cells.entries) ...[
              for (var i = 0; i < entry.value.length && i < _perCell; i++)
                Positioned(
                  left: positions[entry.value[i].id]!.dx,
                  top: positions[entry.value[i].id]!.dy,
                  width: _boxWidth,
                  height: _boxHeight,
                  child: _InstanceBox(
                    object: entry.value[i],
                    selected: selectedId == entry.value[i].id,
                    onTap: onSelect == null
                        ? null
                        : () => onSelect!(entry.value[i]),
                  ),
                ),
              if (entry.value.length > _perCell)
                Positioned(
                  left: entry.key.$1 * (_boxWidth + _gapX),
                  top: offsets[entry.key.$2] + _perCell * (_boxHeight + _cellGap),
                  width: _boxWidth,
                  height: _boxHeight,
                  child: Center(
                    child: Text(
                      '+${entry.value.length - _perCell} more '
                      '${entry.value.first.type}',
                      style: CumulusText.statCaption,
                    ),
                  ),
                ),
            ],
          ],
        ),
      ),
    );
  }
}

class _InstanceBox extends StatelessWidget {
  final WorldObject object;
  final bool selected;
  final VoidCallback? onTap;

  const _InstanceBox({
    required this.object,
    required this.selected,
    this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final conflicted = object.conflictFields.isNotEmpty;
    return Tooltip(
      message:
          '${object.type} · ${object.naturalKey}\n${object.ref}'
          '${conflicted ? "\nSources disagree on ${object.conflictFields.join(", ")}." : ""}',
      child: Material(
        color: selected ? CumulusColors.accentSoft : CumulusColors.surface,
        borderRadius: BorderRadius.circular(9),
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(9),
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 5),
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(9),
              border: Border.all(
                color: selected
                    ? CumulusColors.accent
                    : conflicted
                        ? CumulusColors.warn
                        : CumulusColors.border,
                width: selected ? 1.4 : 1,
              ),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Text(
                  object.label.isEmpty ? object.id : object.label,
                  style: const TextStyle(
                    fontSize: 11.5,
                    fontWeight: FontWeight.w600,
                    color: CumulusColors.ink,
                  ),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
                Text(
                  object.type,
                  style: CumulusText.statCaption,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

/// Thin curves between instance boxes. Unlabelled on purpose — at this density
/// the labels are noise, and the edge inventory carries the names.
class _InstanceEdgePainter extends CustomPainter {
  final List<WorldLink> links;
  final Map<String, Offset> positions;
  final double boxWidth;
  final double boxHeight;

  _InstanceEdgePainter({
    required this.links,
    required this.positions,
    required this.boxWidth,
    required this.boxHeight,
  });

  @override
  void paint(Canvas canvas, Size size) {
    for (final link in links) {
      final from = positions[link.fromId];
      final to = positions[link.toId];
      // A box the cell cap dropped has no position; its edges are dropped with
      // it rather than drawn into empty space.
      if (from == null || to == null) continue;
      final rightwards = to.dx >= from.dx;
      final start = Offset(
        from.dx + (rightwards ? boxWidth : 0),
        from.dy + boxHeight / 2,
      );
      final end = Offset(
        to.dx + (rightwards ? 0 : boxWidth),
        to.dy + boxHeight / 2,
      );
      final control = (end.dx - start.dx).abs() / 2;
      final path = Path()
        ..moveTo(start.dx, start.dy)
        ..cubicTo(
          start.dx + (rightwards ? control : -control), start.dy,
          end.dx - (rightwards ? control : -control), end.dy,
          end.dx, end.dy,
        );
      canvas.drawPath(
        path,
        Paint()
          ..color = link.live
              ? CumulusColors.borderStrong
              : CumulusColors.border
          ..strokeWidth = link.live ? 1.3 : 1
          ..style = PaintingStyle.stroke,
      );
      canvas.drawCircle(
        end,
        2.4,
        Paint()..color = link.live
            ? CumulusColors.inkMuted
            : CumulusColors.border,
      );
    }
  }

  @override
  bool shouldRepaint(_InstanceEdgePainter oldDelegate) =>
      oldDelegate.links != links || oldDelegate.positions != positions;
}
