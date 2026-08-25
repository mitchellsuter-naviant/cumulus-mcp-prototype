import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../theme/cumulus_theme.dart';
import 'command_palette.dart';

/// One stop on the left rail.
class RailDestination {
  final IconData icon;
  final String label;
  final int badge;

  const RailDestination({
    required this.icon,
    required this.label,
    this.badge = 0,
  });
}

/// The console chrome: a full-width top bar with the wordmark and the command
/// palette, and a floating icon rail down the left edge. Every page renders
/// inside it, so navigation never repaints.
class CumulusShell extends StatefulWidget {
  final List<RailDestination> destinations;
  final int selectedIndex;
  final ValueChanged<int> onDestinationSelected;

  /// Owns the query, the lanes, and the keyboard selection. Held by the
  /// application rather than by this widget, because the palette outlives any
  /// one page and the pages need to publish scope into it.
  final CommandPaletteController palette;

  final String searchHint;

  final String? accountLabel;
  final VoidCallback? onSignOut;

  /// Opens the API reference from the help menu.
  final VoidCallback? onOpenApiDocs;

  final Widget child;

  const CumulusShell({
    super.key,
    required this.destinations,
    required this.selectedIndex,
    required this.onDestinationSelected,
    required this.palette,
    this.searchHint = 'Search, jump to, or ask Cumulus a question…',
    this.accountLabel,
    this.onSignOut,
    this.onOpenApiDocs,
    required this.child,
  });

  @override
  State<CumulusShell> createState() => _CumulusShellState();
}

/// Top bar height, and the offset the palette hangs from. Shared so the panel
/// stays welded to the bottom edge of the search pill.
const double _topBarHeight = 66;
const double _paletteTop = 62;
const double _searchMaxWidth = 680;

class _CumulusShellState extends State<CumulusShell> {
  @override
  void initState() {
    super.initState();
    // Registered on HardwareKeyboard rather than declared as a `Shortcuts`
    // binding inside the shell subtree. The subtree version only fires when
    // focus happens to live under it, which it does not when a route is pushed
    // over the console, when a popup menu is open, or when nothing is focused
    // at all — that is why the old binding appeared to do nothing. Handlers
    // registered here see every key event before the focus tree does, and
    // returning true marks the event consumed so the browser does not also act
    // on it (Chrome binds Ctrl+K to the address bar).
    HardwareKeyboard.instance.addHandler(_onGlobalKey);
  }

  @override
  void dispose() {
    HardwareKeyboard.instance.removeHandler(_onGlobalKey);
    super.dispose();
  }

  /// True when a text field has focus, so `/` types a slash instead of
  /// stealing focus mid-sentence.
  bool get _isTypingIntoAField {
    final context = FocusManager.instance.primaryFocus?.context;
    if (context == null) return false;
    return context.findAncestorWidgetOfExactType<EditableText>() != null;
  }

  bool _onGlobalKey(KeyEvent event) {
    if (event is! KeyDownEvent) return false;
    final keyboard = HardwareKeyboard.instance;
    final palette = widget.palette;

    if (event.logicalKey == LogicalKeyboardKey.keyK &&
        (keyboard.isMetaPressed || keyboard.isControlPressed)) {
      palette.isOpen ? palette.close() : palette.open();
      return true;
    }
    if (event.logicalKey == LogicalKeyboardKey.escape && palette.isOpen) {
      palette.close();
      return true;
    }
    // The second, browser-proof way in. Consumed, so the slash that opened the
    // palette is not also typed into it.
    if (event.logicalKey == LogicalKeyboardKey.slash &&
        !palette.isOpen &&
        !_isTypingIntoAField) {
      palette.open();
      return true;
    }
    return false;
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: CumulusColors.canvas,
      body: ListenableBuilder(
        listenable: widget.palette,
        builder: (context, _) {
          final open = widget.palette.isOpen;
          return Stack(
            children: [
              Column(
                children: [
                  _TopBar(
                    palette: widget.palette,
                    hint: widget.searchHint,
                    accountLabel: widget.accountLabel,
                    onSignOut: widget.onSignOut,
                    onOpenApiDocs: widget.onOpenApiDocs,
                  ),
                  Expanded(
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        _Rail(
                          destinations: widget.destinations,
                          selectedIndex: widget.selectedIndex,
                          onSelected: widget.onDestinationSelected,
                        ),
                        Expanded(child: widget.child),
                      ],
                    ),
                  ),
                ],
              ),
              // Starts below the top bar so the search field, the help menu and
              // the account menu stay live while the palette is open.
              if (open)
                Positioned(
                  top: _topBarHeight,
                  left: 0,
                  right: 0,
                  bottom: 0,
                  child: GestureDetector(
                    onTap: widget.palette.close,
                    behavior: HitTestBehavior.opaque,
                    child: ColoredBox(
                      color: CumulusColors.ink.withValues(alpha: 0.06),
                    ),
                  ),
                ),
              if (open)
                Positioned(
                  top: _paletteTop,
                  left: 0,
                  right: 0,
                  child: Center(
                    child: ConstrainedBox(
                      constraints: const BoxConstraints(
                        maxWidth: _searchMaxWidth,
                      ),
                      child: CommandPalettePanel(palette: widget.palette),
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

class _TopBar extends StatelessWidget {
  final CommandPaletteController palette;
  final String hint;
  final String? accountLabel;
  final VoidCallback? onSignOut;
  final VoidCallback? onOpenApiDocs;

  const _TopBar({
    required this.palette,
    required this.hint,
    this.accountLabel,
    this.onSignOut,
    this.onOpenApiDocs,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      height: _topBarHeight,
      padding: const EdgeInsets.symmetric(horizontal: 18),
      color: CumulusColors.canvas,
      child: Row(
        children: [
          const SizedBox(width: 220, child: _Wordmark()),
          Expanded(
            child: Center(
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: _searchMaxWidth),
                child: _CommandSearch(palette: palette, hint: hint),
              ),
            ),
          ),
          SizedBox(
            width: 220,
            child: Row(
              mainAxisAlignment: MainAxisAlignment.end,
              children: [
                _HelpButton(onOpenApiDocs: onOpenApiDocs),
                const SizedBox(width: 10),
                _AccountButton(label: accountLabel, onSignOut: onSignOut),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _Wordmark extends StatelessWidget {
  const _Wordmark();

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          width: 28,
          height: 28,
          decoration: BoxDecoration(
            color: CumulusColors.ink,
            borderRadius: BorderRadius.circular(9),
          ),
          child: const Icon(Icons.cloud, size: 16, color: Colors.white),
        ),
        const SizedBox(width: 10),
        const Text(
          'CUMULUS',
          style: TextStyle(
            fontSize: 13,
            fontWeight: FontWeight.w700,
            letterSpacing: 2.2,
            color: CumulusColors.ink,
          ),
        ),
      ],
    );
  }
}

/// The pill search bar. It is the console's command line, so it reads as one
/// affordance: icon, prompt, and the ⌘K hint that focuses it.
///
/// The field keeps focus while the palette is open — the panel below is not
/// focusable — so the arrow keys have to be intercepted here rather than by the
/// list they move through.
class _CommandSearch extends StatefulWidget {
  final CommandPaletteController palette;
  final String hint;

  const _CommandSearch({required this.palette, required this.hint});

  @override
  State<_CommandSearch> createState() => _CommandSearchState();
}

class _CommandSearchState extends State<_CommandSearch> {
  bool _focused = false;

  CommandPaletteController get _palette => widget.palette;

  @override
  void initState() {
    super.initState();
    _palette.focusNode.addListener(_onFocus);
  }

  @override
  void dispose() {
    _palette.focusNode.removeListener(_onFocus);
    super.dispose();
  }

  void _onFocus() {
    final focused = _palette.focusNode.hasFocus;
    setState(() => _focused = focused);
    // Clicking into the field is a request for the palette, the same as ⌘K.
    // The text is left alone, unlike ⌘K, because a click places a caret.
    if (focused && !_palette.isOpen) _palette.open(selectAll: false);
  }

  KeyEventResult _onKey(FocusNode node, KeyEvent event) {
    if (event is! KeyDownEvent) return KeyEventResult.ignored;
    final key = event.logicalKey;
    final keyboard = HardwareKeyboard.instance;

    if (key == LogicalKeyboardKey.arrowDown) {
      if (!_palette.isOpen) _palette.open(selectAll: false);
      _palette.moveSelection(1);
      return KeyEventResult.handled;
    }
    if (key == LogicalKeyboardKey.arrowUp) {
      _palette.moveSelection(-1);
      return KeyEventResult.handled;
    }
    if (key == LogicalKeyboardKey.enter ||
        key == LogicalKeyboardKey.numpadEnter) {
      // ⌘↵ asks regardless of which row is selected, so a question never
      // depends on scrolling to the Ask row first.
      if (keyboard.isMetaPressed || keyboard.isControlPressed) {
        _palette.runAsk();
        return KeyEventResult.handled;
      }
      // ⇧↵ leaves the dropdown for the full search screen, carrying the query
      // and filters across unchanged.
      if (keyboard.isShiftPressed) {
        _palette.showAllResults();
        return KeyEventResult.handled;
      }
      if (!_palette.isOpen) {
        _palette.open(selectAll: false);
        return KeyEventResult.handled;
      }
      _palette.activateSelection();
      return KeyEventResult.handled;
    }
    return KeyEventResult.ignored;
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedContainer(
      duration: const Duration(milliseconds: 140),
      height: 46,
      padding: const EdgeInsets.only(left: 18, right: 8),
      decoration: BoxDecoration(
        color: CumulusColors.surface,
        borderRadius: BorderRadius.circular(CumulusRadius.pill),
        border: Border.all(
          color: _focused ? CumulusColors.accent : CumulusColors.border,
          width: _focused ? 1.4 : 1,
        ),
        boxShadow: CumulusShadows.floating,
      ),
      child: Row(
        children: [
          const Icon(Icons.search, size: 18, color: CumulusColors.inkMuted),
          const SizedBox(width: 12),
          Expanded(
            child: Focus(
              onKeyEvent: _onKey,
              child: TextField(
                controller: _palette.input,
                focusNode: _palette.focusNode,
                textInputAction: TextInputAction.search,
                style: const TextStyle(
                  fontSize: 13.5,
                  color: CumulusColors.ink,
                ),
                decoration: InputDecoration(
                  isDense: true,
                  filled: false,
                  border: InputBorder.none,
                  enabledBorder: InputBorder.none,
                  focusedBorder: InputBorder.none,
                  contentPadding: EdgeInsets.zero,
                  hintText: widget.hint,
                  hintStyle: const TextStyle(
                    fontSize: 13.5,
                    color: CumulusColors.inkMuted,
                  ),
                ),
              ),
            ),
          ),
          const SizedBox(width: 8),
          ValueListenableBuilder<TextEditingValue>(
            valueListenable: _palette.input,
            builder: (context, value, _) => value.text.isEmpty
                ? const _KeyHint('⌘K')
                : IconButton(
                    icon: const Icon(Icons.close, size: 16),
                    tooltip: 'Clear',
                    visualDensity: VisualDensity.compact,
                    onPressed: _palette.clear,
                  ),
          ),
        ],
      ),
    );
  }
}

class _KeyHint extends StatelessWidget {
  final String label;

  const _KeyHint(this.label);

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.only(right: 6),
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      decoration: BoxDecoration(
        color: CumulusColors.surfaceMuted,
        borderRadius: BorderRadius.circular(7),
      ),
      child: Text(
        label,
        style: const TextStyle(
          fontSize: 11,
          fontWeight: FontWeight.w600,
          color: CumulusColors.inkMuted,
        ),
      ),
    );
  }
}

/// Help menu. Sits immediately left of the account button and mirrors its
/// shape, so the two read as one cluster of account-level affordances rather
/// than as a control that belongs to the page underneath.
class _HelpButton extends StatelessWidget {
  final VoidCallback? onOpenApiDocs;

  const _HelpButton({this.onOpenApiDocs});

  @override
  Widget build(BuildContext context) {
    return PopupMenuButton<String>(
      tooltip: 'Help',
      offset: const Offset(0, 44),
      onSelected: (value) {
        if (value == 'api-docs') onOpenApiDocs?.call();
      },
      itemBuilder: (context) => [
        const PopupMenuItem<String>(
          enabled: false,
          height: 36,
          child: Text('HELP', style: CumulusText.statLabel),
        ),
        const PopupMenuDivider(),
        PopupMenuItem<String>(
          value: 'api-docs',
          height: 52,
          child: Row(
            children: [
              const Icon(
                Icons.code,
                size: 16,
                color: CumulusColors.inkSecondary,
              ),
              const SizedBox(width: 10),
              Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  const Text(
                    'API documentation',
                    style: TextStyle(fontSize: 13),
                  ),
                  Text(
                    'REST, MCP, and A2A endpoints',
                    style: CumulusText.sectionSubtitle,
                  ),
                ],
              ),
            ],
          ),
        ),
      ],
      child: Container(
        width: 34,
        height: 34,
        alignment: Alignment.center,
        decoration: BoxDecoration(
          color: CumulusColors.surface,
          shape: BoxShape.circle,
          border: Border.all(color: CumulusColors.border),
          boxShadow: CumulusShadows.card,
        ),
        child: const Icon(
          Icons.help_outline,
          size: 17,
          color: CumulusColors.inkSecondary,
        ),
      ),
    );
  }
}

class _AccountButton extends StatelessWidget {
  final String? label;
  final VoidCallback? onSignOut;

  const _AccountButton({this.label, this.onSignOut});

  String get _initials {
    final source = (label ?? '').trim();
    if (source.isEmpty) return '—';
    final name = source.split('@').first;
    final parts = name.split(RegExp(r'[.\-_ ]')).where((p) => p.isNotEmpty);
    if (parts.isEmpty) return '—';
    if (parts.length == 1) {
      return parts.first.substring(0, 1).toUpperCase() +
          (parts.first.length > 1 ? parts.first.substring(1, 2) : '');
    }
    return parts.take(2).map((p) => p.substring(0, 1).toUpperCase()).join();
  }

  @override
  Widget build(BuildContext context) {
    return PopupMenuButton<String>(
      tooltip: label ?? 'Account',
      offset: const Offset(0, 44),
      onSelected: (_) => onSignOut?.call(),
      itemBuilder: (context) => [
        PopupMenuItem<String>(
          enabled: false,
          height: 36,
          child: Text(label ?? 'Signed in', style: CumulusText.meta),
        ),
        const PopupMenuDivider(),
        const PopupMenuItem<String>(
          value: 'signout',
          height: 40,
          child: Row(
            children: [
              Icon(Icons.logout, size: 16, color: CumulusColors.inkSecondary),
              SizedBox(width: 10),
              Text('Sign out', style: TextStyle(fontSize: 13)),
            ],
          ),
        ),
      ],
      child: Container(
        width: 34,
        height: 34,
        alignment: Alignment.center,
        decoration: BoxDecoration(
          color: CumulusColors.surface,
          shape: BoxShape.circle,
          border: Border.all(color: CumulusColors.border),
          boxShadow: CumulusShadows.card,
        ),
        child: Text(
          _initials.toUpperCase(),
          style: const TextStyle(
            fontSize: 11.5,
            fontWeight: FontWeight.w700,
            letterSpacing: 0.4,
            color: CumulusColors.inkSecondary,
          ),
        ),
      ),
    );
  }
}

class _Rail extends StatelessWidget {
  final List<RailDestination> destinations;
  final int selectedIndex;
  final ValueChanged<int> onSelected;

  const _Rail({
    required this.destinations,
    required this.selectedIndex,
    required this.onSelected,
  });

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(14, 4, 8, 24),
      child: Container(
        width: 52,
        padding: const EdgeInsets.symmetric(vertical: 8),
        decoration: BoxDecoration(
          color: CumulusColors.surface,
          borderRadius: BorderRadius.circular(CumulusRadius.rail),
          border: Border.all(color: CumulusColors.border),
          boxShadow: CumulusShadows.card,
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            for (var i = 0; i < destinations.length; i++)
              _RailButton(
                destination: destinations[i],
                selected: i == selectedIndex,
                onTap: () => onSelected(i),
              ),
          ],
        ),
      ),
    );
  }
}

class _RailButton extends StatelessWidget {
  final RailDestination destination;
  final bool selected;
  final VoidCallback onTap;

  const _RailButton({
    required this.destination,
    required this.selected,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 3),
      child: Tooltip(
        message: destination.label,
        preferBelow: false,
        verticalOffset: -2,
        margin: const EdgeInsets.only(left: 60),
        child: Material(
          color: selected ? CumulusColors.accentSoft : Colors.transparent,
          borderRadius: BorderRadius.circular(12),
          child: InkWell(
            onTap: onTap,
            borderRadius: BorderRadius.circular(12),
            hoverColor: CumulusColors.surfaceMuted,
            child: SizedBox(
              width: 40,
              height: 40,
              child: Stack(
                alignment: Alignment.center,
                children: [
                  Icon(
                    destination.icon,
                    size: 19,
                    color: selected
                        ? CumulusColors.accent
                        : CumulusColors.inkMuted,
                  ),
                  if (destination.badge > 0)
                    Positioned(
                      top: 5,
                      right: 4,
                      child: Container(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 4,
                          vertical: 1,
                        ),
                        decoration: BoxDecoration(
                          color: CumulusColors.ink,
                          borderRadius: BorderRadius.circular(999),
                        ),
                        child: Text(
                          '${destination.badge}',
                          style: const TextStyle(
                            fontSize: 8.5,
                            fontWeight: FontWeight.w700,
                            color: Colors.white,
                          ),
                        ),
                      ),
                    ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// Scrolling page body: one measured column, capped width, generous gutters.
class PageBody extends StatelessWidget {
  final List<Widget> children;
  final double maxWidth;
  final double spacing;
  final EdgeInsetsGeometry padding;

  const PageBody({
    super.key,
    required this.children,
    this.maxWidth = 1420,
    this.spacing = 16,
    this.padding = const EdgeInsets.fromLTRB(14, 12, 28, 48),
  });

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      padding: padding,
      child: Center(
        child: ConstrainedBox(
          constraints: BoxConstraints(maxWidth: maxWidth),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            spacing: spacing,
            children: children,
          ),
        ),
      ),
    );
  }
}
