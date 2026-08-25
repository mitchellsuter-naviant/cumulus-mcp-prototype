import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../theme/cumulus_theme.dart';

/// A point in a daily series: the label is shown in the hover tooltip and the
/// first/last labels anchor the x axis.
class ChartPoint {
  final String label;
  final double value;
  // Extra tooltip lines beyond the value, e.g. "7 tasks raised".
  final List<String> extra;

  const ChartPoint(this.label, this.value, {this.extra = const []});
}

/// Shared hover index across several charts of the same time axis.
///
/// Reading three stacked charts means asking "what happened on *this* day" —
/// which is only answerable if the crosshair moves in all of them at once.
/// Charts given the same [ChartSync] highlight the same day together.
class ChartSync extends ChangeNotifier {
  int? _index;

  int? get index => _index;

  set index(int? value) {
    if (_index == value) return;
    _index = value;
    notifyListeners();
  }
}

/// Single-hue daily bar chart in the console's voice: thin indigo bars with
/// rounded data ends, two recessive gridlines, a crosshair + tooltip on hover.
///
/// One measure per chart, one axis — two measures of different scale are two
/// charts side by side, never a dual axis.
class DailyBarChart extends StatefulWidget {
  final String title;
  final List<ChartPoint> points;
  final String Function(double) format;
  final double height;

  /// When supplied, hovering this chart moves every chart sharing the object.
  final ChartSync? sync;

  const DailyBarChart({
    super.key,
    required this.title,
    required this.points,
    required this.format,
    this.height = 150,
    this.sync,
  });

  @override
  State<DailyBarChart> createState() => _DailyBarChartState();
}

class _DailyBarChartState extends State<DailyBarChart> {
  int? _localHover;

  int? get _hover => widget.sync?.index ?? _localHover;

  void _setHover(int? index) {
    if (widget.sync != null) {
      widget.sync!.index = index;
    } else if (index != _localHover) {
      setState(() => _localHover = index);
    }
  }

  @override
  Widget build(BuildContext context) {
    final chart = LayoutBuilder(
      builder: (context, constraints) {
        final width = constraints.maxWidth;
        return MouseRegion(
          onHover: (event) => _setHover(_indexAt(event.localPosition.dx, width)),
          onExit: (_) => _setHover(null),
          child: CustomPaint(
            size: Size(width, widget.height),
            painter: _DailyBarPainter(
              points: widget.points,
              hover: _hover,
              format: widget.format,
            ),
          ),
        );
      },
    );
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(widget.title.toUpperCase(), style: CumulusText.statLabel),
        const SizedBox(height: 10),
        SizedBox(
          height: widget.height,
          child: widget.sync == null
              ? chart
              : ListenableBuilder(
                  listenable: widget.sync!,
                  builder: (context, _) => chart,
                ),
        ),
      ],
    );
  }

  int? _indexAt(double dx, double width) {
    if (widget.points.isEmpty) return null;
    final plotLeft = _DailyBarPainter.axisWidth;
    final plotWidth = width - plotLeft;
    if (dx < plotLeft || plotWidth <= 0) return null;
    final slot = plotWidth / widget.points.length;
    final index = ((dx - plotLeft) / slot).floor();
    return index.clamp(0, widget.points.length - 1);
  }
}

class _DailyBarPainter extends CustomPainter {
  static const double axisWidth = 40;
  static const double footHeight = 18;

  final List<ChartPoint> points;
  final int? hover;
  final String Function(double) format;

  _DailyBarPainter({
    required this.points,
    required this.hover,
    required this.format,
  });

  @override
  void paint(Canvas canvas, Size size) {
    final plot = Rect.fromLTRB(axisWidth, 4, size.width, size.height - footHeight);
    final grid = Paint()
      ..color = CumulusColors.border
      ..strokeWidth = 1;
    final baselinePaint = Paint()
      ..color = CumulusColors.borderStrong
      ..strokeWidth = 1;

    final maxValue = points.fold<double>(0, (m, p) => math.max(m, p.value));
    final top = maxValue <= 0 ? 1.0 : maxValue * 1.05;

    // Two gridlines plus the baseline keep the scale readable and recessive.
    canvas.drawLine(Offset(plot.left, plot.top), Offset(plot.right, plot.top), grid);
    final mid = plot.top + plot.height / 2;
    canvas.drawLine(Offset(plot.left, mid), Offset(plot.right, mid), grid);
    canvas.drawLine(
      Offset(plot.left, plot.bottom),
      Offset(plot.right, plot.bottom),
      baselinePaint,
    );
    _label(canvas, format(top), Offset(plot.left - 6, plot.top), alignRight: true);
    _label(canvas, format(top / 2), Offset(plot.left - 6, mid), alignRight: true);
    _label(canvas, '0', Offset(plot.left - 6, plot.bottom), alignRight: true);

    if (points.isEmpty) return;
    final slot = plot.width / points.length;
    final barWidth = math.max(2.0, math.min(13.0, slot * 0.68));
    final bars = Paint()..color = CumulusColors.accent;
    final barsHover = Paint()..color = CumulusColors.accentInk;

    for (var i = 0; i < points.length; i++) {
      final h = (points[i].value / top) * plot.height;
      if (h <= 0) continue;
      final x = plot.left + i * slot + (slot - barWidth) / 2;
      canvas.drawRRect(
        RRect.fromRectAndCorners(
          Rect.fromLTWH(x, plot.bottom - h, barWidth, h),
          topLeft: const Radius.circular(4),
          topRight: const Radius.circular(4),
        ),
        i == hover ? barsHover : bars,
      );
    }

    // X-axis extents only; every date is one hover away.
    _label(
      canvas,
      points.first.label,
      Offset(plot.left, plot.bottom + 4),
      anchorTop: true,
    );
    _label(
      canvas,
      points.last.label,
      Offset(plot.right, plot.bottom + 4),
      alignRight: true,
      anchorTop: true,
    );

    if (hover != null) {
      final i = hover!;
      final cx = plot.left + i * slot + slot / 2;
      final cross = Paint()
        ..color = CumulusColors.borderStrong
        ..strokeWidth = 1;
      const dash = 3.0;
      for (var y = plot.top; y < plot.bottom; y += dash * 2) {
        canvas.drawLine(
          Offset(cx, y),
          Offset(cx, math.min(y + dash, plot.bottom)),
          cross,
        );
      }
      _tooltip(canvas, plot, cx, points[i]);
    }
  }

  void _tooltip(Canvas canvas, Rect plot, double cx, ChartPoint point) {
    final lines = <TextPainter>[
      _painter(point.label, Colors.white, FontWeight.w600),
      _painter(format(point.value), const Color(0xFFC9CDD6), FontWeight.w400),
      for (final extra in point.extra)
        _painter(extra, const Color(0xFFC9CDD6), FontWeight.w400),
    ];
    final width = lines.fold<double>(0, (m, l) => math.max(m, l.width)) + 20;
    final height = lines.fold<double>(0, (m, l) => m + l.height + 2) + 12;
    // Flip sides so the tooltip never leaves the plot.
    final left = cx + 10 + width > plot.right ? cx - 10 - width : cx + 10;
    final rect = RRect.fromRectAndRadius(
      Rect.fromLTWH(left, plot.top + 6, width, height),
      const Radius.circular(8),
    );
    canvas.drawRRect(rect, Paint()..color = CumulusColors.ink);
    var y = plot.top + 13;
    for (final line in lines) {
      line.paint(canvas, Offset(left + 10, y));
      y += line.height + 2;
    }
  }

  TextPainter _painter(String text, Color color, FontWeight weight) {
    return TextPainter(
      text: TextSpan(
        text: text,
        style: TextStyle(fontSize: 10.5, color: color, fontWeight: weight),
      ),
      textDirection: TextDirection.ltr,
    )..layout();
  }

  void _label(
    Canvas canvas,
    String text,
    Offset at, {
    bool alignRight = false,
    bool anchorTop = false,
  }) {
    final painter = TextPainter(
      text: TextSpan(
        text: text,
        style: const TextStyle(fontSize: 10, color: CumulusColors.inkMuted),
      ),
      textDirection: TextDirection.ltr,
    )..layout();
    painter.paint(
      canvas,
      Offset(
        alignRight ? at.dx - painter.width : at.dx,
        anchorTop ? at.dy : at.dy - painter.height / 2,
      ),
    );
  }

  @override
  bool shouldRepaint(_DailyBarPainter old) =>
      old.points != points || old.hover != hover;
}

/// Tiny indigo trend line for list rows. Renders nothing meaningful for
/// fewer than two points, which is the honest answer for a new definition.
class Sparkline extends StatelessWidget {
  final List<int> values;
  final double width;
  final double height;

  const Sparkline({
    super.key,
    required this.values,
    this.width = 96,
    this.height = 26,
  });

  @override
  Widget build(BuildContext context) {
    return CustomPaint(
      size: Size(width, height),
      painter: _SparklinePainter(values),
    );
  }
}

class _SparklinePainter extends CustomPainter {
  final List<int> values;

  _SparklinePainter(this.values);

  @override
  void paint(Canvas canvas, Size size) {
    if (values.length < 2) return;
    final minV = values.reduce(math.min).toDouble();
    final maxV = values.reduce(math.max).toDouble();
    final span = (maxV - minV) == 0 ? 1.0 : (maxV - minV);
    final path = Path();
    for (var i = 0; i < values.length; i++) {
      final x = i * size.width / (values.length - 1);
      final y = 1 + (size.height - 2) * (1 - (values[i] - minV) / span);
      i == 0 ? path.moveTo(x, y) : path.lineTo(x, y);
    }
    canvas.drawPath(
      path,
      Paint()
        ..color = CumulusColors.accent
        ..style = PaintingStyle.stroke
        ..strokeWidth = 2
        ..strokeCap = StrokeCap.round
        ..strokeJoin = StrokeJoin.round,
    );
  }

  @override
  bool shouldRepaint(_SparklinePainter old) => old.values != values;
}
