import 'package:flutter/material.dart';

import '../theme/cumulus_theme.dart';

/// Semantic colour roles. Every tinted chip, badge, and stat value in the
/// console picks its pair of colours from here so the palette stays closed.
enum CumulusTone { neutral, accent, success, warn, danger, teal, violet }

extension CumulusToneColors on CumulusTone {
  Color get foreground => switch (this) {
    CumulusTone.neutral => CumulusColors.inkSecondary,
    CumulusTone.accent => CumulusColors.accentInk,
    CumulusTone.success => CumulusColors.success,
    CumulusTone.warn => CumulusColors.warn,
    CumulusTone.danger => CumulusColors.danger,
    CumulusTone.teal => CumulusColors.teal,
    CumulusTone.violet => const Color(0xFF6D5AE6),
  };

  Color get background => switch (this) {
    CumulusTone.neutral => CumulusColors.surfaceMuted,
    CumulusTone.accent => CumulusColors.accentSoft,
    CumulusTone.success => CumulusColors.successSoft,
    CumulusTone.warn => CumulusColors.warnSoft,
    CumulusTone.danger => CumulusColors.dangerSoft,
    CumulusTone.teal => CumulusColors.tealSoft,
    CumulusTone.violet => const Color(0xFFEFEDFD),
  };

  Color get dot => switch (this) {
    CumulusTone.neutral => CumulusColors.inkMuted,
    _ => foreground,
  };
}

/// The one surface in the system: white, hairline-bordered, faintly lifted.
class CumulusCard extends StatelessWidget {
  final Widget child;
  final EdgeInsetsGeometry padding;
  final VoidCallback? onTap;
  final Color? borderColor;
  final double borderWidth;

  const CumulusCard({
    super.key,
    required this.child,
    this.padding = const EdgeInsets.all(20),
    this.onTap,
    this.borderColor,
    this.borderWidth = 1,
  });

  @override
  Widget build(BuildContext context) {
    final decorated = Container(
      decoration: BoxDecoration(
        color: CumulusColors.surface,
        borderRadius: BorderRadius.circular(CumulusRadius.card),
        border: Border.all(
          color: borderColor ?? CumulusColors.border,
          width: borderWidth,
        ),
        boxShadow: CumulusShadows.card,
      ),
      child: onTap == null
          ? Padding(padding: padding, child: child)
          : Material(
              color: Colors.transparent,
              borderRadius: BorderRadius.circular(CumulusRadius.card),
              child: InkWell(
                onTap: onTap,
                borderRadius: BorderRadius.circular(CumulusRadius.card),
                hoverColor: CumulusColors.surfaceMuted.withValues(alpha: 0.6),
                child: Padding(padding: padding, child: child),
              ),
            ),
    );
    return decorated;
  }
}

/// `SECTION TITLE   lowercase descriptor` — the header pattern used above
/// every card body.
class SectionHeader extends StatelessWidget {
  final String title;
  final String? subtitle;
  final Widget? trailing;

  const SectionHeader({
    super.key,
    required this.title,
    this.subtitle,
    this.trailing,
  });

  @override
  Widget build(BuildContext context) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.center,
      children: [
        Expanded(
          child: Wrap(
            crossAxisAlignment: WrapCrossAlignment.center,
            spacing: 10,
            runSpacing: 2,
            children: [
              Text(title.toUpperCase(), style: CumulusText.sectionTitle),
              if (subtitle != null)
                Text(subtitle!, style: CumulusText.sectionSubtitle),
            ],
          ),
        ),
        if (trailing != null) ...[const SizedBox(width: 12), trailing!],
      ],
    );
  }
}

/// A card whose body is introduced by a [SectionHeader].
class SectionCard extends StatelessWidget {
  final String title;
  final String? subtitle;
  final Widget? headerTrailing;
  final Widget child;
  final EdgeInsetsGeometry padding;
  final Color? borderColor;

  const SectionCard({
    super.key,
    required this.title,
    this.subtitle,
    this.headerTrailing,
    required this.child,
    this.padding = const EdgeInsets.all(20),
    this.borderColor,
  });

  @override
  Widget build(BuildContext context) {
    return CumulusCard(
      padding: padding,
      borderColor: borderColor,
      borderWidth: borderColor == null ? 1 : 1.5,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SectionHeader(
            title: title,
            subtitle: subtitle,
            trailing: headerTrailing,
          ),
          const SizedBox(height: 16),
          child,
        ],
      ),
    );
  }
}

/// Label / big number / caption, as in the metric strip across the top of a
/// policyholder or book page.
class StatTile extends StatelessWidget {
  final String label;
  final String value;
  final String? caption;
  final Color? valueColor;

  const StatTile({
    super.key,
    required this.label,
    required this.value,
    this.caption,
    this.valueColor,
  });

  @override
  Widget build(BuildContext context) {
    return CumulusCard(
      padding: const EdgeInsets.fromLTRB(20, 18, 20, 18),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label.toUpperCase(), style: CumulusText.statLabel),
          const SizedBox(height: 10),
          Text(
            value,
            style: CumulusText.statValue.copyWith(color: valueColor),
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
          const SizedBox(height: 8),
          Text(caption ?? '', style: CumulusText.statCaption),
          const Spacer(),
        ],
      ),
    );
  }
}

/// Equal-width metric strip that reflows to two rows on narrow windows.
class StatStrip extends StatelessWidget {
  final List<StatTile> tiles;

  const StatStrip({super.key, required this.tiles});

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        const gap = 14.0;
        final perRow = constraints.maxWidth < 720
            ? 2
            : constraints.maxWidth < 1080
            ? 3
            : tiles.length;
        // Chunk into rows so tiles in the same row share a height — a caption
        // that wraps must not leave its neighbours short.
        final rows = <List<StatTile>>[];
        for (var i = 0; i < tiles.length; i += perRow) {
          rows.add(
            tiles.sublist(i, (i + perRow).clamp(0, tiles.length)),
          );
        }
        return Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          spacing: gap,
          children: [
            for (final row in rows)
              IntrinsicHeight(
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  spacing: gap,
                  children: [
                    for (final tile in row) Expanded(child: tile),
                    // Keep a short final row aligned with the grid above it.
                    for (var i = row.length; i < perRow; i++)
                      const Expanded(child: SizedBox.shrink()),
                  ],
                ),
              ),
          ],
        );
      },
    );
  }
}

/// Eyebrow · title · trailing pills — the standing head of every page.
class PageHeading extends StatelessWidget {
  final String eyebrow;
  final String title;
  final String? subtitle;
  final List<Widget> pills;
  final Widget? trailing;

  const PageHeading({
    super.key,
    required this.eyebrow,
    required this.title,
    this.subtitle,
    this.pills = const [],
    this.trailing,
  });

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(eyebrow.toUpperCase(), style: CumulusText.eyebrow),
        const SizedBox(height: 8),
        Row(
          crossAxisAlignment: CrossAxisAlignment.center,
          children: [
            Expanded(
              child: Wrap(
                crossAxisAlignment: WrapCrossAlignment.center,
                spacing: 14,
                runSpacing: 8,
                children: [
                  Text(title, style: CumulusText.pageTitle),
                  ...pills,
                ],
              ),
            ),
            if (trailing != null) ...[const SizedBox(width: 16), trailing!],
          ],
        ),
        if (subtitle != null) ...[
          const SizedBox(height: 8),
          Text(subtitle!, style: CumulusText.pageSubtitle),
        ],
      ],
    );
  }
}

/// Outlined pill used for descriptors next to a page title
/// (`Commercial Package · $428K`).
class MetaPill extends StatelessWidget {
  final String label;
  final CumulusTone? tone;

  const MetaPill(this.label, {super.key, this.tone});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 13, vertical: 7),
      decoration: BoxDecoration(
        color: tone?.background ?? CumulusColors.surface,
        borderRadius: BorderRadius.circular(CumulusRadius.pill),
        border: Border.all(
          color: tone == null ? CumulusColors.border : Colors.transparent,
        ),
      ),
      child: Text(
        label,
        style: TextStyle(
          fontSize: 12.5,
          fontWeight: FontWeight.w500,
          color: tone?.foreground ?? CumulusColors.inkSecondary,
        ),
      ),
    );
  }
}

/// Small tinted status pill (`published`, `active`, `APPROVAL_REQUIRED`).
class StatusBadge extends StatelessWidget {
  final String label;
  final CumulusTone tone;
  final bool dense;
  final bool uppercase;

  const StatusBadge(
    this.label, {
    super.key,
    this.tone = CumulusTone.neutral,
    this.dense = false,
    this.uppercase = false,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: EdgeInsets.symmetric(
        horizontal: dense ? 8 : 10,
        vertical: dense ? 3 : 5,
      ),
      decoration: BoxDecoration(
        color: tone.background,
        borderRadius: BorderRadius.circular(CumulusRadius.pill),
      ),
      child: Text(
        uppercase ? label.toUpperCase() : label,
        style: TextStyle(
          fontSize: dense ? 10.5 : 11.5,
          fontWeight: FontWeight.w600,
          letterSpacing: uppercase ? 0.6 : 0,
          color: tone.foreground,
        ),
      ),
    );
  }
}

/// Dot-and-label pill used as a filter/lens toggle. Selected reads as solid
/// ink, unselected as a hairline outline.
class LensChip extends StatelessWidget {
  final String label;
  final CumulusTone tone;
  final bool selected;
  final VoidCallback? onTap;

  const LensChip({
    super.key,
    required this.label,
    this.tone = CumulusTone.neutral,
    this.selected = false,
    this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final fg = selected ? Colors.white : CumulusColors.inkSecondary;
    return Material(
      color: selected ? CumulusColors.ink : CumulusColors.surface,
      borderRadius: BorderRadius.circular(CumulusRadius.pill),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(CumulusRadius.pill),
        hoverColor: CumulusColors.surfaceMuted,
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 13, vertical: 7),
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(CumulusRadius.pill),
            border: Border.all(
              color: selected ? CumulusColors.ink : CumulusColors.borderStrong,
            ),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Container(
                width: 7,
                height: 7,
                decoration: BoxDecoration(
                  color: selected ? Colors.white : tone.dot,
                  shape: BoxShape.circle,
                ),
              ),
              const SizedBox(width: 8),
              Text(
                label.toUpperCase(),
                style: TextStyle(
                  fontSize: 11,
                  fontWeight: FontWeight.w600,
                  letterSpacing: 0.8,
                  color: fg,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// Segmented pill tabs; the selected segment is a lifted white pill.
class SegmentedTabs extends StatelessWidget {
  final List<String> labels;
  final int selectedIndex;
  final ValueChanged<int> onChanged;

  const SegmentedTabs({
    super.key,
    required this.labels,
    required this.selectedIndex,
    required this.onChanged,
  });

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        for (var i = 0; i < labels.length; i++)
          Padding(
            padding: const EdgeInsets.only(right: 6),
            child: _Tab(
              label: labels[i],
              selected: i == selectedIndex,
              onTap: () => onChanged(i),
            ),
          ),
      ],
    );
  }
}

class _Tab extends StatelessWidget {
  final String label;
  final bool selected;
  final VoidCallback onTap;

  const _Tab({
    required this.label,
    required this.selected,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(CumulusRadius.control),
        boxShadow: selected ? CumulusShadows.card : const [],
      ),
      child: Material(
        color: selected ? CumulusColors.surface : Colors.transparent,
        borderRadius: BorderRadius.circular(CumulusRadius.control),
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(CumulusRadius.control),
          hoverColor: CumulusColors.surfaceMuted,
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 9),
            child: Text(
              label,
              style: TextStyle(
                fontSize: 13,
                fontWeight: FontWeight.w600,
                color: selected
                    ? CumulusColors.ink
                    : CumulusColors.inkSecondary,
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// `← All assets` — the quiet way back from a detail page.
class BackLink extends StatelessWidget {
  final String label;
  final VoidCallback onTap;

  const BackLink({super.key, required this.label, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return Align(
      alignment: Alignment.centerLeft,
      child: TextButton.icon(
        onPressed: onTap,
        icon: const Icon(Icons.arrow_back, size: 16),
        label: Text(label),
        style: TextButton.styleFrom(
          foregroundColor: CumulusColors.inkSecondary,
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
        ),
      ),
    );
  }
}

/// Right-aligned `Open ↗` affordance on list rows.
class RowAction extends StatelessWidget {
  final String label;
  final IconData icon;
  final VoidCallback onTap;

  const RowAction({
    super.key,
    required this.label,
    this.icon = Icons.north_east,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return TextButton.icon(
      onPressed: onTap,
      icon: Icon(icon, size: 14),
      label: Text(label),
      style: TextButton.styleFrom(
        foregroundColor: CumulusColors.inkSecondary,
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      ),
    );
  }
}

/// Hairline-separated row inside a card: leading glyph, title + meta, then
/// trailing controls.
class CumulusListRow extends StatefulWidget {
  final Widget? leading;
  final Widget title;
  final Widget? subtitle;
  final List<Widget> trailing;
  final VoidCallback? onTap;

  const CumulusListRow({
    super.key,
    this.leading,
    required this.title,
    this.subtitle,
    this.trailing = const [],
    this.onTap,
  });

  @override
  State<CumulusListRow> createState() => _CumulusListRowState();
}

class _CumulusListRowState extends State<CumulusListRow> {
  bool _hovered = false;

  @override
  Widget build(BuildContext context) {
    return MouseRegion(
      cursor: widget.onTap == null
          ? MouseCursor.defer
          : SystemMouseCursors.click,
      onEnter: (_) => setState(() => _hovered = true),
      onExit: (_) => setState(() => _hovered = false),
      child: GestureDetector(
        onTap: widget.onTap,
        behavior: HitTestBehavior.opaque,
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 120),
          color: _hovered && widget.onTap != null
              ? CumulusColors.surfaceMuted.withValues(alpha: 0.7)
              : Colors.transparent,
          padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 14),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.center,
            children: [
              if (widget.leading != null) ...[
                widget.leading!,
                const SizedBox(width: 14),
              ],
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    DefaultTextStyle.merge(
                      style: CumulusText.rowTitle,
                      child: widget.title,
                    ),
                    if (widget.subtitle != null) ...[
                      const SizedBox(height: 4),
                      DefaultTextStyle.merge(
                        style: CumulusText.meta,
                        child: widget.subtitle!,
                      ),
                    ],
                  ],
                ),
              ),
              ...widget.trailing,
            ],
          ),
        ),
      ),
    );
  }
}

/// Card that holds hairline-separated [CumulusListRow]s edge to edge.
class ListCard extends StatelessWidget {
  final String title;
  final String? subtitle;
  final Widget? headerTrailing;
  final List<Widget> children;

  const ListCard({
    super.key,
    required this.title,
    this.subtitle,
    this.headerTrailing,
    required this.children,
  });

  @override
  Widget build(BuildContext context) {
    return CumulusCard(
      padding: EdgeInsets.zero,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(20, 18, 20, 14),
            child: SectionHeader(
              title: title,
              subtitle: subtitle,
              trailing: headerTrailing,
            ),
          ),
          for (var i = 0; i < children.length; i++) ...[
            if (i > 0) const Divider(height: 1),
            children[i],
          ],
          const SizedBox(height: 6),
        ],
      ),
    );
  }
}

/// Circular monogram used for people and for authority levels.
class Monogram extends StatelessWidget {
  final String text;
  final CumulusTone tone;
  final double size;

  const Monogram(
    this.text, {
    super.key,
    this.tone = CumulusTone.neutral,
    this.size = 32,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      width: size,
      height: size,
      alignment: Alignment.center,
      decoration: BoxDecoration(color: tone.background, shape: BoxShape.circle),
      child: Text(
        text,
        style: TextStyle(
          fontSize: size * 0.42,
          fontWeight: FontWeight.w600,
          color: tone.foreground,
        ),
      ),
    );
  }
}

class CumulusEmptyState extends StatelessWidget {
  final IconData icon;
  final String title;
  final String message;
  final Widget? action;

  const CumulusEmptyState({
    super.key,
    required this.icon,
    required this.title,
    required this.message,
    this.action,
  });

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 48, horizontal: 32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              width: 44,
              height: 44,
              alignment: Alignment.center,
              decoration: const BoxDecoration(
                color: CumulusColors.surfaceMuted,
                shape: BoxShape.circle,
              ),
              child: Icon(icon, size: 20, color: CumulusColors.inkMuted),
            ),
            const SizedBox(height: 14),
            Text(
              title,
              style: const TextStyle(
                fontSize: 14.5,
                fontWeight: FontWeight.w600,
                color: CumulusColors.ink,
              ),
            ),
            const SizedBox(height: 6),
            ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 380),
              child: Text(
                message,
                textAlign: TextAlign.center,
                style: CumulusText.meta,
              ),
            ),
            if (action != null) ...[const SizedBox(height: 18), action!],
          ],
        ),
      ),
    );
  }
}

/// Inline error strip — tinted, never a raw red string on the canvas.
class ErrorNotice extends StatelessWidget {
  final String message;
  final VoidCallback? onRetry;

  const ErrorNotice({super.key, required this.message, this.onRetry});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
      decoration: BoxDecoration(
        color: CumulusColors.dangerSoft,
        borderRadius: BorderRadius.circular(CumulusRadius.control),
      ),
      child: Row(
        children: [
          const Icon(
            Icons.error_outline,
            size: 18,
            color: CumulusColors.danger,
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
              message,
              style: const TextStyle(
                fontSize: 12.5,
                color: CumulusColors.danger,
                height: 1.4,
              ),
            ),
          ),
          if (onRetry != null)
            TextButton(
              onPressed: onRetry,
              style: TextButton.styleFrom(
                foregroundColor: CumulusColors.danger,
              ),
              child: const Text('Retry'),
            ),
        ],
      ),
    );
  }
}

/// Field label rendered above an input, in the same small-caps voice as the
/// section headers.
class FieldLabel extends StatelessWidget {
  final String text;
  final String? hint;

  const FieldLabel(this.text, {super.key, this.hint});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 7),
      child: Row(
        children: [
          Text(text.toUpperCase(), style: CumulusText.statLabel),
          if (hint != null) ...[
            const SizedBox(width: 8),
            Flexible(
              child: Text(
                hint!,
                style: CumulusText.sectionSubtitle,
                overflow: TextOverflow.ellipsis,
              ),
            ),
          ],
        ],
      ),
    );
  }
}

/// [FieldLabel] stacked over any input.
class LabeledField extends StatelessWidget {
  final String label;
  final String? hint;
  final Widget child;

  const LabeledField({
    super.key,
    required this.label,
    this.hint,
    required this.child,
  });

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [FieldLabel(label, hint: hint), child],
    );
  }
}

/// Centered loading state that keeps the canvas calm.
class CumulusLoading extends StatelessWidget {
  const CumulusLoading({super.key});

  @override
  Widget build(BuildContext context) {
    return const Padding(
      padding: EdgeInsets.symmetric(vertical: 64),
      child: Center(
        child: SizedBox(
          width: 22,
          height: 22,
          child: CircularProgressIndicator(strokeWidth: 2.4),
        ),
      ),
    );
  }
}

/// One labelled row of a usage breakdown, with a proportional bar behind the
/// count so the shape of the traffic is readable at a glance.
class UsageBar extends StatelessWidget {
  final String label;
  final int count;
  final int max;
  final bool mono;

  const UsageBar({
    super.key,
    required this.label,
    required this.count,
    required this.max,
    this.mono = false,
  });

  @override
  Widget build(BuildContext context) {
    final fraction = max == 0 ? 0.0 : count / max;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        children: [
          Expanded(
            child: Stack(
              children: [
                Container(
                  height: 26,
                  decoration: BoxDecoration(
                    color: CumulusColors.surfaceMuted,
                    borderRadius: BorderRadius.circular(7),
                  ),
                ),
                FractionallySizedBox(
                  widthFactor: fraction.clamp(0.02, 1.0),
                  child: Container(
                    height: 26,
                    decoration: BoxDecoration(
                      color: CumulusColors.accentSoft,
                      borderRadius: BorderRadius.circular(7),
                    ),
                  ),
                ),
                Positioned.fill(
                  child: Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 10),
                    child: Align(
                      alignment: Alignment.centerLeft,
                      child: Text(
                        label,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: mono
                            ? CumulusText.mono.copyWith(fontSize: 11.5)
                            : const TextStyle(
                                fontSize: 12.5,
                                color: CumulusColors.ink,
                              ),
                      ),
                    ),
                  ),
                ),
              ],
            ),
          ),
          SizedBox(
            width: 56,
            child: Text(
              '$count',
              textAlign: TextAlign.right,
              style: const TextStyle(
                fontSize: 12.5,
                fontWeight: FontWeight.w600,
                color: CumulusColors.ink,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

/// `1.2K` / `3.4M` — counts read as magnitudes rather than digits.
String compactCount(int n) => n >= 1000000
    ? '${(n / 1000000).toStringAsFixed(1)}M'
    : n >= 1000
    ? '${(n / 1000).toStringAsFixed(1)}K'
    : '$n';

/// `just now` / `12m ago` / `6h ago` / `3d ago` / `4mo ago`.
String agoLabel(DateTime when) {
  final diff = DateTime.now().difference(when);
  if (diff.inMinutes < 1) return 'just now';
  if (diff.inHours < 1) return '${diff.inMinutes}m ago';
  if (diff.inDays < 1) return '${diff.inHours}h ago';
  if (diff.inDays < 30) return '${diff.inDays}d ago';
  return '${(diff.inDays / 30).floor()}mo ago';
}
