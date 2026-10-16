import 'package:flutter/material.dart';

import '../theme/cumulus_theme.dart';

/// Skeleton loading for the Cumulus console.
///
/// The rule: a page that is waiting on data renders its real chrome — heading,
/// tabs, toolbars, gutters — immediately, and swaps only the data regions for
/// grey geometry in the approximate shape of what will arrive. Everything here
/// mirrors a `ui_kit.dart` counterpart (StatTile, StatStrip, ListCard, the
/// sticky grids) so the swap from skeleton to content moves nothing.
///
/// Wrap the placeholder region — not the chrome — in one [SkeletonPulse]; it
/// owns the single animation for the whole region, so a page full of bars
/// costs one ticker and one opacity layer, not one per shape.
class SkeletonPulse extends StatefulWidget {
  final Widget child;

  const SkeletonPulse({super.key, required this.child});

  @override
  State<SkeletonPulse> createState() => _SkeletonPulseState();
}

class _SkeletonPulseState extends State<SkeletonPulse>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 1100),
  )..repeat(reverse: true);

  late final Animation<double> _opacity = Tween<double>(
    begin: 1.0,
    end: 0.45,
  ).animate(CurvedAnimation(parent: _controller, curve: Curves.easeInOut));

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return ExcludeSemantics(
      child: FadeTransition(opacity: _opacity, child: widget.child),
    );
  }
}

/// A rounded grey bar standing in for one run of text. Width `null` fills the
/// parent; pass a fraction of the real text's width where you know it.
class SkeletonBar extends StatelessWidget {
  final double? width;
  final double height;
  final double radius;
  final Color color;

  const SkeletonBar({
    super.key,
    this.width,
    this.height = 12,
    this.radius = 6,
    this.color = CumulusColors.border,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      width: width,
      height: height,
      decoration: BoxDecoration(
        color: color,
        borderRadius: BorderRadius.circular(radius),
      ),
    );
  }
}

class SkeletonCircle extends StatelessWidget {
  final double size;

  const SkeletonCircle({super.key, this.size = 28});

  @override
  Widget build(BuildContext context) {
    return Container(
      width: size,
      height: size,
      decoration: const BoxDecoration(
        color: CumulusColors.border,
        shape: BoxShape.circle,
      ),
    );
  }
}

/// Stands in for a MetaPill / StatusBadge / LensChip.
class SkeletonPill extends StatelessWidget {
  final double width;

  const SkeletonPill({super.key, this.width = 72});

  @override
  Widget build(BuildContext context) {
    return SkeletonBar(
      width: width,
      height: 22,
      radius: CumulusRadius.pill,
      color: CumulusColors.surfaceMuted,
    );
  }
}

/// The CumulusCard frame — real surface, border, radius and shadow — holding
/// skeleton geometry, so the card grid is in place before its contents are.
class SkeletonCard extends StatelessWidget {
  final Widget child;
  final EdgeInsetsGeometry padding;
  final double? height;

  const SkeletonCard({
    super.key,
    required this.child,
    this.padding = const EdgeInsets.all(20),
    this.height,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      height: height,
      decoration: BoxDecoration(
        color: CumulusColors.surface,
        borderRadius: BorderRadius.circular(CumulusRadius.card),
        border: Border.all(color: CumulusColors.border),
        boxShadow: CumulusShadows.card,
      ),
      child: Padding(padding: padding, child: child),
    );
  }
}

/// Mirrors [StatTile]'s internal rhythm: label / 10 / value / 8 / caption.
class SkeletonStatTile extends StatelessWidget {
  const SkeletonStatTile({super.key});

  @override
  Widget build(BuildContext context) {
    return const SkeletonCard(
      padding: EdgeInsets.fromLTRB(20, 18, 20, 18),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          SkeletonBar(width: 82, height: 10),
          SizedBox(height: 12),
          SkeletonBar(width: 116, height: 24),
          SizedBox(height: 10),
          SkeletonBar(width: 140, height: 10),
        ],
      ),
    );
  }
}

/// Mirrors [StatStrip]: equal-width tiles, same 14px gap, same 720/1080
/// reflow, so the strip does not jump when the real tiles land.
class SkeletonStatStrip extends StatelessWidget {
  final int count;

  const SkeletonStatStrip({super.key, this.count = 4});

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        const gap = 14.0;
        final perRow = constraints.maxWidth < 720
            ? 2
            : constraints.maxWidth < 1080
            ? 3
            : count;
        final rows = <int>[];
        for (var remaining = count; remaining > 0; remaining -= perRow) {
          rows.add(remaining.clamp(0, perRow));
        }
        return Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          spacing: gap,
          children: [
            for (final inRow in rows)
              Row(
                spacing: gap,
                children: [
                  for (var i = 0; i < inRow; i++)
                    const Expanded(child: SkeletonStatTile()),
                  for (var i = inRow; i < perRow; i++)
                    const Expanded(child: SizedBox.shrink()),
                ],
              ),
          ],
        );
      },
    );
  }
}

/// One list row: monogram circle, title over meta, a trailing pill — the
/// shape of [CumulusListRow].
class SkeletonListRow extends StatelessWidget {
  final bool leadingCircle;

  const SkeletonListRow({super.key, this.leadingCircle = true});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 11),
      child: Row(
        children: [
          if (leadingCircle) ...[
            const SkeletonCircle(),
            const SizedBox(width: 12),
          ],
          const Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                SkeletonBar(width: 180, height: 12),
                SizedBox(height: 7),
                SkeletonBar(width: 120, height: 9),
              ],
            ),
          ),
          const SizedBox(width: 12),
          const SkeletonPill(width: 64),
        ],
      ),
    );
  }
}

/// Mirrors [ListCard]: a section-header line, then N rows.
class SkeletonListCard extends StatelessWidget {
  final int rows;
  final bool leadingCircle;

  const SkeletonListCard({super.key, this.rows = 5, this.leadingCircle = true});

  @override
  Widget build(BuildContext context) {
    return SkeletonCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SkeletonBar(width: 140, height: 10),
          const SizedBox(height: 14),
          for (var i = 0; i < rows; i++)
            SkeletonListRow(leadingCircle: leadingCircle),
        ],
      ),
    );
  }
}

/// N table rows whose column widths approximate a data grid. Pass the flex
/// of each real column so the grey bars sit where the values will.
class SkeletonTableRows extends StatelessWidget {
  final int rows;
  final List<int> columnFlex;

  const SkeletonTableRows({
    super.key,
    this.rows = 10,
    this.columnFlex = const [3, 2, 2, 1],
  });

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        for (var i = 0; i < rows; i++)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 13, horizontal: 4),
            child: Row(
              children: [
                for (final flex in columnFlex)
                  Expanded(
                    flex: flex,
                    child: const Padding(
                      padding: EdgeInsets.only(right: 18),
                      child: SkeletonBar(height: 11),
                    ),
                  ),
              ],
            ),
          ),
      ],
    );
  }
}

/// The master–detail split while both sides wait: a fixed-width list of rows
/// beside a detail card. Matches the 440/320px splits used by Tasks, Desks
/// and the identity workbench.
class SkeletonMasterDetail extends StatelessWidget {
  final double listWidth;
  final int rows;

  const SkeletonMasterDetail({super.key, this.listWidth = 380, this.rows = 8});

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final list = SkeletonCard(
          child: Column(
            children: [
              for (var i = 0; i < rows; i++) const SkeletonListRow(),
            ],
          ),
        );
        final detail = SkeletonCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const SkeletonBar(width: 220, height: 16),
              const SizedBox(height: 10),
              const SkeletonBar(width: 150, height: 10),
              const SizedBox(height: 24),
              const SkeletonBar(height: 11),
              const SizedBox(height: 10),
              SkeletonBar(
                height: 11,
                width: constraints.maxWidth * 0.4,
              ),
              const SizedBox(height: 24),
              const SkeletonBar(height: 90, radius: CumulusRadius.control),
            ],
          ),
        );
        if (constraints.maxWidth < 900) {
          return Column(spacing: 16, children: [list, detail]);
        }
        return Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            SizedBox(width: listWidth, child: list),
            const SizedBox(width: 16),
            Expanded(child: detail),
          ],
        );
      },
    );
  }
}

/// A canvas surface waiting for its graph: faint node rectangles in a loose
/// grid with nothing joining them yet. Used by the ontology and flow canvases.
class SkeletonCanvas extends StatelessWidget {
  final double height;

  const SkeletonCanvas({super.key, this.height = 520});

  @override
  Widget build(BuildContext context) {
    return SkeletonCard(
      height: height,
      child: LayoutBuilder(
        builder: (context, constraints) {
          const nodeWidth = 148.0;
          const nodeHeight = 64.0;
          final columns =
              ((constraints.maxWidth + 60) / (nodeWidth + 60)).floor().clamp(
                    1,
                    5,
                  );
          final rows =
              ((constraints.maxHeight + 72) / (nodeHeight + 72)).floor().clamp(
                    1,
                    4,
                  );
          return Stack(
            children: [
              for (var r = 0; r < rows; r++)
                for (var c = 0; c < columns; c++)
                  Positioned(
                    left: c * (nodeWidth + 60) + (r.isOdd ? 34.0 : 0.0),
                    top: r * (nodeHeight + 72).toDouble(),
                    child: Container(
                      width: nodeWidth,
                      height: nodeHeight,
                      decoration: BoxDecoration(
                        color: CumulusColors.surfaceMuted,
                        borderRadius: BorderRadius.circular(
                          CumulusRadius.control,
                        ),
                        border: Border.all(color: CumulusColors.border),
                      ),
                    ),
                  ),
            ],
          );
        },
      ),
    );
  }
}
