import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../models/session.dart';
import '../theme/cumulus_theme.dart';
import 'command_palette.dart';
import 'ui_kit.dart';

/// One stop on the left rail.
///
/// A stop either shows a section — [index] names it — or opens a dropdown of
/// [children], each of which shows one. The rail stopped being positional
/// when workspaces arrived: there are now more sections than stops, so a stop
/// carries the index it selects rather than being identified by its place in
/// the list.
class RailDestination {
  final IconData icon;
  final String label;
  final int badge;

  /// The section this stop shows. Null on a dropdown stop — its children
  /// carry the indices instead.
  final int? index;

  /// Non-empty when this stop opens a panel rather than selecting a section.
  final List<RailChild> children;

  /// Draws a hairline above this stop. The rail carries two kinds of
  /// destination — the personal ones (your day, your week, your queue, your
  /// workspaces) and the platform ones (the knowledge layer, the flows that
  /// write into it, who may touch either) — and the rule is worth one pixel:
  /// without it the two groups read as one undifferentiated column of icons.
  final bool startsGroup;

  const RailDestination({
    required this.icon,
    required this.label,
    this.index,
    this.children = const [],
    this.badge = 0,
    this.startsGroup = false,
  });

  bool get isMenu => children.isNotEmpty;
}

/// One row of a dropdown stop's panel — a workspace, in the one case the
/// console has today.
class RailChild {
  final IconData icon;
  final String label;
  final String? detail;
  final int index;

  const RailChild({
    required this.label,
    required this.index,
    this.icon = Icons.folder_outlined,
    this.detail,
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

  /// The acting persona and the roster behind the switcher, or null while the
  /// first session request is still in flight.
  final Session? session;

  /// Acts as another persona. The switcher is only offered when this is
  /// non-null *and* the session says the operator may switch.
  final ValueChanged<String>? onSwitchPersona;

  /// Opens the API reference from the help menu.
  final VoidCallback? onOpenApiDocs;

  /// Opens the Employee Hub from the account menu.
  final VoidCallback? onOpenEmployeeHub;

  /// True while the Employee Hub is the visible section. The hub owns no rail
  /// destination — it is personal, not a workstream — so the account button
  /// carries the active ring instead, and the rail correctly shows nothing
  /// selected.
  final bool employeeHubActive;

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
    this.session,
    this.onSwitchPersona,
    this.onOpenApiDocs,
    this.onOpenEmployeeHub,
    this.employeeHubActive = false,
    required this.child,
  });

  @override
  State<CumulusShell> createState() => _CumulusShellState();
}

/// Top bar height, and the offset the palette hangs from. Shared so the panel
/// stays welded to the bottom edge of the search pill.
const double _topBarHeight = 74;
const double _paletteTop = 66;
const double _searchMaxWidth = 680;

/// The two fixed end slots of the top bar. Equal on purpose: the search pill
/// centres inside what is left over, so unequal ends would drag the console's
/// command line off the middle of the window.
const double _endSlot = 344;

/// The persona control is a fixed width rather than sized to its contents,
/// because a persona with a longer title would otherwise widen the right slot
/// and shift the search pill every time you switched.
const double _personaWidth = 250;

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
                    session: widget.session,
                    onSwitchPersona: widget.onSwitchPersona,
                    onOpenApiDocs: widget.onOpenApiDocs,
                    onOpenEmployeeHub: widget.onOpenEmployeeHub,
                    employeeHubActive: widget.employeeHubActive,
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
  final Session? session;
  final ValueChanged<String>? onSwitchPersona;
  final VoidCallback? onOpenApiDocs;
  final VoidCallback? onOpenEmployeeHub;
  final bool employeeHubActive;

  const _TopBar({
    required this.palette,
    required this.hint,
    this.accountLabel,
    this.onSignOut,
    this.session,
    this.onSwitchPersona,
    this.onOpenApiDocs,
    this.onOpenEmployeeHub,
    this.employeeHubActive = false,
  });

  @override
  Widget build(BuildContext context) {
    // Impersonation colours the whole bar, not just the control that caused
    // it. A tinted chip is something you can look past for an hour; a chrome
    // that has changed colour is not, and the cost of forgetting whose session
    // you are in is a decision recorded against the wrong person.
    final acting = session?.impersonating ?? false;
    return Container(
      height: _topBarHeight,
      padding: const EdgeInsets.symmetric(horizontal: 18),
      decoration: BoxDecoration(
        color: acting ? CumulusColors.accentSoft : CumulusColors.canvas,
        border: acting
            ? const Border(
                bottom: BorderSide(color: CumulusColors.accent, width: 2),
              )
            : null,
      ),
      child: Row(
        children: [
          const SizedBox(width: _endSlot, child: _Wordmark()),
          Expanded(
            child: Center(
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: _searchMaxWidth),
                child: _CommandSearch(palette: palette, hint: hint),
              ),
            ),
          ),
          SizedBox(
            width: _endSlot,
            child: Row(
              mainAxisAlignment: MainAxisAlignment.end,
              children: [
                if (session != null) ...[
                  _PersonaButton(session: session!, onSwitch: onSwitchPersona),
                  const SizedBox(width: 10),
                ],
                _HelpButton(onOpenApiDocs: onOpenApiDocs),
                const SizedBox(width: 10),
                _AccountButton(
                  label: accountLabel,
                  onSignOut: onSignOut,
                  onOpenEmployeeHub: onOpenEmployeeHub,
                  hubActive: employeeHubActive,
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// Who the console is answering as, and the way to become somebody else.
///
/// Deliberately not folded into the account button beside it: the account is
/// who signed in and the persona is who the server is answering as, and while
/// impersonation is running those are two different people. One control for
/// both would be the fastest way to lose that distinction.
class _PersonaButton extends StatelessWidget {
  final Session session;
  final ValueChanged<String>? onSwitch;

  const _PersonaButton({required this.session, this.onSwitch});

  bool get _canSwitch => session.maySwitchPersona && onSwitch != null;

  String get _tooltip {
    final name = session.displayName;
    return [
      session.label == name ? name : '$name · ${session.label}',
      session.title,
      if (!session.inWorkforce)
        'No Employee record behind this persona, so the Hub, the calendar '
            'and the personal queues are empty by design.',
    ].join('\n');
  }

  @override
  Widget build(BuildContext context) {
    if (!_canSwitch) {
      return Tooltip(
        message: _tooltip,
        child: _PersonaFace(session: session, canSwitch: false),
      );
    }
    return PopupMenuButton<String>(
      tooltip: _tooltip,
      offset: const Offset(0, 62),
      constraints: const BoxConstraints(minWidth: 400, maxWidth: 420),
      onSelected: onSwitch,
      itemBuilder: (context) => _items(),
      child: _PersonaFace(session: session, canSwitch: true),
    );
  }

  List<PopupMenuEntry<String>> _items() {
    final entries = <PopupMenuEntry<String>>[];
    for (final group in session.groups) {
      if (entries.isNotEmpty) entries.add(const PopupMenuDivider());
      entries.add(
        PopupMenuItem<String>(
          enabled: false,
          height: 34,
          child: Text(group.label.toUpperCase(), style: CumulusText.statLabel),
        ),
      );
      for (final option in group.personas) {
        entries.add(
          PopupMenuItem<String>(
            value: option.id,
            child: _PersonaEntry(
              option: option,
              selected: option.id == session.personaId,
            ),
          ),
        );
      }
    }
    return entries;
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

/// The persona as it reads in the bar: role above, person, title below —
/// the same eyebrow/title/subtitle rhythm every page heading uses.
///
/// While an impersonation is running the eyebrow spends itself on the operator
/// instead of the role — "MITCHELL SUTER · ACTING AS" over "John Smith" — so
/// the bar answers *whose session is this* with two names rather than one and
/// an inference. The role is worth the line the rest of the time, except when
/// it is the person's own name, which is how the customer-user personas are
/// named on the server: "TOMAS BERG" above "Connie Alberts" says nothing.
class _PersonaFace extends StatelessWidget {
  final Session session;
  final bool canSwitch;

  const _PersonaFace({required this.session, required this.canSwitch});

  String get _role =>
      session.label == session.displayName ? '' : session.label;

  String get _eyebrow {
    if (!session.impersonating) return _role.toUpperCase();
    // An operator who is not on the roster cannot be named, so the eyebrow
    // falls back to saying only that this is an impersonation.
    final operator = session.operator;
    final parts = operator != null
        ? [operator.displayName, 'Acting as']
        : ['Acting as', if (_role.isNotEmpty) _role];
    return parts.join(' · ').toUpperCase();
  }

  /// The title, plus the reason a persona's personal surfaces will be empty.
  /// Naviant Support is not in the customer's org chart, and a hub with
  /// nothing in it should read as correct rather than as a failed request.
  String get _subtitle => session.inWorkforce
      ? session.title
      : '${session.title} · no employee record';

  @override
  Widget build(BuildContext context) {
    final acting = session.impersonating;
    final eyebrow = _eyebrow;
    return Container(
      width: _personaWidth,
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 7),
      decoration: BoxDecoration(
        color: CumulusColors.surface,
        borderRadius: BorderRadius.circular(CumulusRadius.control),
        border: Border.all(
          color: acting ? CumulusColors.accent : CumulusColors.border,
          width: acting ? 1.5 : 1,
        ),
        boxShadow: CumulusShadows.card,
      ),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                if (eyebrow.isNotEmpty)
                  Text(
                    eyebrow,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: CumulusText.statLabel.copyWith(
                      fontSize: 10,
                      height: 1.3,
                      color: acting
                          ? CumulusColors.accentInk
                          : CumulusColors.inkMuted,
                    ),
                  ),
                Text(
                  session.displayName,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: CumulusText.rowTitle.copyWith(
                    fontSize: 13,
                    height: 1.25,
                  ),
                ),
                Text(
                  _subtitle,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: CumulusText.sectionSubtitle.copyWith(
                    fontSize: 11,
                    height: 1.25,
                  ),
                ),
              ],
            ),
          ),
          if (canSwitch)
            const Padding(
              padding: EdgeInsets.only(left: 6),
              child: Icon(
                Icons.unfold_more,
                size: 16,
                color: CumulusColors.inkMuted,
              ),
            ),
        ],
      ),
    );
  }
}

/// One persona in the switcher. The blurb is the row's reason to exist: the
/// roster is a set of test subjects, and "owns no queue at all" is the only
/// thing that tells you which one answers the question you are asking.
class _PersonaEntry extends StatelessWidget {
  final PersonaOption option;
  final bool selected;

  const _PersonaEntry({required this.option, required this.selected});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 22,
            child: selected
                ? const Icon(
                    Icons.check,
                    size: 15,
                    color: CumulusColors.accent,
                  )
                : null,
          ),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                Row(
                  children: [
                    Flexible(
                      child: Text(
                        option.displayName,
                        overflow: TextOverflow.ellipsis,
                        style: CumulusText.rowTitle.copyWith(fontSize: 13),
                      ),
                    ),
                    if (option.label != option.displayName) ...[
                      const SizedBox(width: 8),
                      StatusBadge(option.label, dense: true),
                    ],
                  ],
                ),
                Text(option.title, style: CumulusText.sectionSubtitle),
                const SizedBox(height: 5),
                Text(option.blurb, style: CumulusText.meta),
              ],
            ),
          ),
        ],
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
  final VoidCallback? onOpenEmployeeHub;
  final bool hubActive;

  const _AccountButton({
    this.label,
    this.onSignOut,
    this.onOpenEmployeeHub,
    this.hubActive = false,
  });

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
      onSelected: (value) {
        if (value == 'signout') onSignOut?.call();
        if (value == 'employee-hub') onOpenEmployeeHub?.call();
      },
      itemBuilder: (context) => [
        PopupMenuItem<String>(
          enabled: false,
          height: 36,
          child: Text(label ?? 'Signed in', style: CumulusText.meta),
        ),
        const PopupMenuDivider(),
        PopupMenuItem<String>(
          value: 'employee-hub',
          height: 52,
          child: Row(
            children: [
              const Icon(
                Icons.badge_outlined,
                size: 16,
                color: CumulusColors.inkSecondary,
              ),
              const SizedBox(width: 10),
              Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  const Text('Employee Hub', style: TextStyle(fontSize: 13)),
                  Text(
                    'PTO, benefits, and the HR Agent',
                    style: CumulusText.sectionSubtitle,
                  ),
                ],
              ),
            ],
          ),
        ),
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
          color: hubActive ? CumulusColors.ink : CumulusColors.surface,
          shape: BoxShape.circle,
          border: Border.all(
            color: hubActive ? CumulusColors.ink : CumulusColors.border,
          ),
          boxShadow: CumulusShadows.card,
        ),
        child: Text(
          _initials.toUpperCase(),
          style: TextStyle(
            fontSize: 11.5,
            fontWeight: FontWeight.w700,
            letterSpacing: 0.4,
            color: hubActive ? Colors.white : CumulusColors.inkSecondary,
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
            for (final destination in destinations) ...[
              if (destination.startsGroup)
                const Padding(
                  padding: EdgeInsets.symmetric(horizontal: 12, vertical: 7),
                  child: Divider(height: 1),
                ),
              if (destination.isMenu)
                _RailMenuButton(
                  destination: destination,
                  selectedIndex: selectedIndex,
                  onSelected: onSelected,
                )
              else
                _RailButton(
                  destination: destination,
                  selected: destination.index == selectedIndex,
                  onTap: () => onSelected(destination.index!),
                ),
            ],
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
          color: Colors.transparent,
          borderRadius: BorderRadius.circular(12),
          child: InkWell(
            onTap: onTap,
            borderRadius: BorderRadius.circular(12),
            hoverColor: CumulusColors.surfaceMuted,
            child: _RailGlyph(destination: destination, selected: selected),
          ),
        ),
      ),
    );
  }
}

/// A dropdown stop: the icon opens a panel of workspaces hanging off the
/// right edge of the rail. The stop itself is never a destination — it carries
/// the active ring only while one of its children is the visible section, so
/// the rail still shows exactly one selection.
class _RailMenuButton extends StatelessWidget {
  final RailDestination destination;
  final int selectedIndex;
  final ValueChanged<int> onSelected;

  const _RailMenuButton({
    required this.destination,
    required this.selectedIndex,
    required this.onSelected,
  });

  bool get _active =>
      destination.children.any((child) => child.index == selectedIndex);

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 3),
      child: PopupMenuButton<int>(
        tooltip: destination.label,
        // Hangs to the right of the rail rather than below it, so the panel
        // reads as a drawer opening out of the stop it belongs to.
        offset: const Offset(46, -6),
        position: PopupMenuPosition.over,
        constraints: const BoxConstraints(minWidth: 236, maxWidth: 300),
        onSelected: onSelected,
        itemBuilder: (context) => [
          PopupMenuItem<int>(
            enabled: false,
            height: 34,
            child: Text(
              destination.label.toUpperCase(),
              style: CumulusText.statLabel,
            ),
          ),
          for (final child in destination.children)
            PopupMenuItem<int>(
              value: child.index,
              height: child.detail == null ? 46 : 56,
              padding: const EdgeInsets.symmetric(horizontal: 8),
              child: _RailChildRow(
                child: child,
                active: child.index == selectedIndex,
              ),
            ),
        ],
        child: _RailGlyph(destination: destination, selected: _active),
      ),
    );
  }
}

class _RailChildRow extends StatelessWidget {
  final RailChild child;
  final bool active;

  const _RailChildRow({required this.child, required this.active});

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 7),
      decoration: BoxDecoration(
        color: active ? CumulusColors.surfaceMuted : Colors.transparent,
        borderRadius: BorderRadius.circular(10),
      ),
      child: Row(
        children: [
          Icon(
            child.icon,
            size: 16,
            color: active ? CumulusColors.accent : CumulusColors.inkMuted,
          ),
          const SizedBox(width: 11),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(
                  child.label,
                  style: TextStyle(
                    fontSize: 13,
                    fontWeight: active ? FontWeight.w600 : FontWeight.w500,
                    color: CumulusColors.ink,
                  ),
                ),
                if (child.detail != null)
                  Text(child.detail!, style: CumulusText.sectionSubtitle),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// The 40×40 icon well shared by both kinds of stop: the active ring, the
/// glyph, and the badge.
class _RailGlyph extends StatelessWidget {
  final RailDestination destination;
  final bool selected;

  const _RailGlyph({required this.destination, required this.selected});

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 40,
      height: 40,
      decoration: BoxDecoration(
        color: selected ? CumulusColors.accentSoft : Colors.transparent,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Stack(
        alignment: Alignment.center,
        children: [
          Icon(
            destination.icon,
            size: 19,
            color: selected ? CumulusColors.accent : CumulusColors.inkMuted,
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
    );
  }
}

/// Scrolling page body: one measured column, capped width, generous gutters.
/// The horizontal geometry every page shares: one gutter, one max width.
///
/// [PageBody] bakes this into a scrolling column, which is right for a page
/// that scrolls as a whole. Anything that must sit *outside* the scroll — a
/// section title, a tab bar, a pane that scrolls internally — has to land on
/// the same left edge as the body or the page looks broken, and it will not
/// unless it is centred inside the same max width.
class PageGutter extends StatelessWidget {
  final Widget child;
  final double maxWidth;
  final EdgeInsetsGeometry padding;

  const PageGutter({
    super.key,
    required this.child,
    this.maxWidth = 1420,
    this.padding = EdgeInsets.zero,
  });

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: padding,
      child: Center(
        child: ConstrainedBox(
          constraints: BoxConstraints(maxWidth: maxWidth),
          // Fill the gutter rather than shrink to the child: a start-aligned
          // column would otherwise be centred as a block, which puts the
          // section title in the middle of the page instead of on the same
          // left edge as the body below it.
          child: SizedBox(width: double.infinity, child: child),
        ),
      ),
    );
  }
}

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
