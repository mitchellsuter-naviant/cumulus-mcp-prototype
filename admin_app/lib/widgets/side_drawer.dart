import 'package:flutter/material.dart';

import '../theme/cumulus_theme.dart';
import 'ui_kit.dart';

/// Slides a panel in from the right, over the console chrome.
///
/// A route rather than an in-page overlay: inspecting a run is a detour, so
/// Escape and the browser back gesture should both close it, and the page
/// underneath should keep its scroll position — all of which come free from
/// the navigator and none of which come free from a `Stack`.
Future<T?> showSideDrawer<T>({
  required BuildContext context,
  required String title,
  String? subtitle,
  List<Widget> titleBadges = const [],
  required WidgetBuilder builder,
  List<Widget> actions = const [],
  double width = 620,
}) {
  return showGeneralDialog<T>(
    context: context,
    barrierDismissible: true,
    barrierLabel: title,
    barrierColor: CumulusColors.ink.withValues(alpha: 0.18),
    transitionDuration: const Duration(milliseconds: 220),
    pageBuilder: (context, animation, secondary) {
      return Align(
        alignment: Alignment.centerRight,
        child: _DrawerShell(
          title: title,
          subtitle: subtitle,
          titleBadges: titleBadges,
          actions: actions,
          width: width,
          child: Builder(builder: builder),
        ),
      );
    },
    transitionBuilder: (context, animation, secondary, child) {
      final curved = CurvedAnimation(
        parent: animation,
        curve: Curves.easeOutCubic,
        reverseCurve: Curves.easeInCubic,
      );
      return SlideTransition(
        position: Tween<Offset>(
          begin: const Offset(1, 0),
          end: Offset.zero,
        ).animate(curved),
        child: child,
      );
    },
  );
}

class _DrawerShell extends StatelessWidget {
  final String title;
  final String? subtitle;
  final List<Widget> titleBadges;
  final List<Widget> actions;
  final double width;
  final Widget child;

  const _DrawerShell({
    required this.title,
    required this.subtitle,
    required this.titleBadges,
    required this.actions,
    required this.width,
    required this.child,
  });

  @override
  Widget build(BuildContext context) {
    final media = MediaQuery.of(context);
    return Material(
      color: CumulusColors.surface,
      child: SizedBox(
        width: width.clamp(320.0, media.size.width),
        height: media.size.height,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Container(
              padding: const EdgeInsets.fromLTRB(24, 20, 16, 16),
              decoration: const BoxDecoration(
                border: Border(
                  bottom: BorderSide(color: CumulusColors.border),
                ),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Expanded(
                        child: Wrap(
                          crossAxisAlignment: WrapCrossAlignment.center,
                          spacing: 10,
                          runSpacing: 6,
                          children: [
                            Text(
                              title,
                              style: CumulusText.mono.copyWith(
                                fontSize: 15,
                                fontWeight: FontWeight.w600,
                              ),
                            ),
                            ...titleBadges,
                          ],
                        ),
                      ),
                      IconButton(
                        tooltip: 'Close',
                        icon: const Icon(Icons.close, size: 18),
                        onPressed: () => Navigator.of(context).pop(),
                      ),
                    ],
                  ),
                  if (subtitle != null) ...[
                    const SizedBox(height: 6),
                    Text(subtitle!, style: CumulusText.meta),
                  ],
                ],
              ),
            ),
            Expanded(
              child: SingleChildScrollView(
                padding: const EdgeInsets.fromLTRB(24, 20, 24, 32),
                child: child,
              ),
            ),
            if (actions.isNotEmpty)
              Container(
                padding: const EdgeInsets.fromLTRB(24, 14, 24, 18),
                decoration: const BoxDecoration(
                  color: CumulusColors.surface,
                  border: Border(top: BorderSide(color: CumulusColors.border)),
                ),
                child: Row(
                  mainAxisAlignment: MainAxisAlignment.end,
                  children: [
                    for (final action in actions) ...[
                      action,
                      const SizedBox(width: 10),
                    ],
                  ],
                ),
              ),
          ],
        ),
      ),
    );
  }
}

/// A labelled monospace payload block — prompts, JSON inputs, raw responses.
/// Long payloads scroll inside their own box rather than stretching the
/// drawer, and every block is selectable because the point of showing a
/// payload is that someone can take it somewhere else.
class PayloadBlock extends StatelessWidget {
  final String label;
  final String content;
  final int maxLines;

  const PayloadBlock({
    super.key,
    required this.label,
    required this.content,
    this.maxLines = 14,
  });

  @override
  Widget build(BuildContext context) {
    if (content.trim().isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          FieldLabel(label),
          Container(
            width: double.infinity,
            constraints: BoxConstraints(maxHeight: maxLines * 18.0 + 24),
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              color: CumulusColors.surfaceMuted,
              borderRadius: BorderRadius.circular(CumulusRadius.control),
              border: Border.all(color: CumulusColors.border),
            ),
            child: SingleChildScrollView(
              child: SelectableText(
                content,
                style: CumulusText.mono.copyWith(fontSize: 11.5, height: 1.45),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
