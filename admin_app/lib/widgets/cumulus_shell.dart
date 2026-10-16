import 'package:flutter/foundation.dart';
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

  /// The count drawn over the glyph, or null for a stop that carries none.
  ///
  /// A listenable rather than a number, because the one stop that has a badge
  /// — the queue — moves on its own schedule: every decision, every persona
  /// switch. Passed as an `int` it was shell state, so changing one digit meant
  /// rebuilding the console and every section mounted inside it.
  final ValueListenable<int>? badge;

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
    this.badge,
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

  /// Acts as another persona. The switcher lives in the account menu and is
  /// only offered when this is non-null *and* the session says the operator
  /// may switch.
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
const double _endSlot = 306;

/// The acting-as chip is a fixed width rather than sized to its contents, for
/// the same reason the persona control it replaced was: a longer name would
/// otherwise widen the right slot and shift the search pill every time you
/// switched.
const double _actingChipWidth = 218;

/// The round buttons at the end of the bar — help and account — which are
/// also what their menus hang from.
const double _roundButtonSize = 34;

/// How far a menu hangs below its button: clear of the bar rather than over
/// its bottom edge, which during an impersonation is the accent rule that says
/// the session is not yours.
const double _menuDrop = (_topBarHeight - _roundButtonSize) / 2 + 6;

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
    //
    // Bare slash only. This handler runs on `HardwareKeyboard` — before any
    // widget in the tree gets a look — so without the modifier check it also
    // ate ⌘/ and sent an operator reaching for the filter on the page to the
    // global palette instead. A screen that wants its own find binds ⌘/ and
    // this leaves it alone.
    if (event.logicalKey == LogicalKeyboardKey.slash &&
        !keyboard.isMetaPressed &&
        !keyboard.isControlPressed &&
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
      body: Stack(
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
          // Only the overlay listens to the palette, and it is deliberately a
          // sibling of the Column rather than its parent. The controller
          // notifies on every keystroke and on every catalog load, and the
          // Column holds `widget.child` — which is the IndexedStack of every
          // section the console has ever opened. Listening above it meant a
          // letter typed into the search pill rebuilt the whole app.
          //
          // Nothing in the top bar reads palette *state*: the search pill is
          // its own StatefulWidget listening to the focus node, and its clear
          // button to the text controller.
          ListenableBuilder(
            listenable: widget.palette,
            builder: (context, _) {
              if (!widget.palette.isOpen) return const SizedBox.shrink();
              return Stack(
                children: [
                  // Starts below the top bar so the search field, the help menu
                  // and the account menu stay live while the palette is open.
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
        ],
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
                if (acting) ...[
                  _ActingChip(session: session!),
                  const SizedBox(width: 10),
                ],
                _HelpButton(onOpenApiDocs: onOpenApiDocs),
                const SizedBox(width: 10),
                _AccountButton(
                  label: accountLabel,
                  session: session,
                  onSignOut: onSignOut,
                  onSwitchPersona: onSwitchPersona,
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

/// The one thing the account button cannot say while its menu is shut: this
/// session is answering as somebody else.
///
/// Rendered only during an impersonation. The tinted chrome says that
/// something has changed; the chip says who, because the cost of forgetting
/// whose session you are in is a decision recorded against the wrong person.
/// Two names rather than one — "MITCHELL SUTER · ACTING AS" over "John Smith"
/// — so the bar answers *whose session is this* without an inference. An
/// operator who is not on the roster cannot be named, so the eyebrow then
/// falls back to saying only that an impersonation is running.
class _ActingChip extends StatelessWidget {
  final Session session;

  const _ActingChip({required this.session});

  @override
  Widget build(BuildContext context) {
    final operator = session.operator;
    final eyebrow = operator == null
        ? 'ACTING AS'
        : '${operator.displayName} · Acting as'.toUpperCase();
    return Tooltip(
      message: [
        operator == null
            ? 'Acting as ${session.displayName}'
            : '${operator.displayName} acting as ${session.displayName}',
        session.title,
      ].join('\n'),
      child: Container(
        width: _actingChipWidth,
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
        decoration: BoxDecoration(
          color: CumulusColors.surface,
          borderRadius: BorderRadius.circular(CumulusRadius.control),
          border: Border.all(color: CumulusColors.accent, width: 1.5),
          boxShadow: CumulusShadows.card,
        ),
        child: Row(
          children: [
            const Icon(
              Icons.visibility_outlined,
              size: 15,
              color: CumulusColors.accentInk,
            ),
            const SizedBox(width: 8),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(
                    eyebrow,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: CumulusText.statLabel.copyWith(
                      fontSize: 9.5,
                      height: 1.3,
                      color: CumulusColors.accentInk,
                    ),
                  ),
                  Text(
                    session.displayName,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: CumulusText.rowTitle.copyWith(
                      fontSize: 12.5,
                      height: 1.25,
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
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
                ? const Icon(Icons.check, size: 15, color: CumulusColors.accent)
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
/// than as a control that belongs to the page underneath. Same panel, same
/// drop, same right edge, for the same reason.
class _HelpButton extends StatelessWidget {
  final VoidCallback? onOpenApiDocs;

  const _HelpButton({this.onOpenApiDocs});

  @override
  Widget build(BuildContext context) {
    return MenuAnchor(
      style: _cumulusMenuStyle(_helpMenuWidth),
      alignmentOffset: const Offset(
        -(_helpMenuWidth - _roundButtonSize),
        _menuDrop,
      ),
      menuChildren: _menuRows(_helpMenuWidth, [
        const Padding(
          padding: EdgeInsets.fromLTRB(16, 6, 16, 2),
          child: Text('HELP', style: CumulusText.statLabel),
        ),
        MenuItemButton(
          style: _menuRowStyle(context),
          leadingIcon: const Icon(
            Icons.code,
            size: 16,
            color: CumulusColors.inkSecondary,
          ),
          onPressed: onOpenApiDocs,
          child: const _MenuRowLabel(
            title: 'API documentation',
            subtitle: 'REST, MCP, and A2A endpoints',
          ),
        ),
      ]),
      builder: (context, controller, _) => Tooltip(
        message: 'Help',
        child: InkWell(
          customBorder: const CircleBorder(),
          onTap: () =>
              controller.isOpen ? controller.close() : controller.open(),
          child: Container(
            width: _roundButtonSize,
            height: _roundButtonSize,
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
        ),
      ),
    );
  }
}

/// Menu geometry. The account menu is sized to its own rows; the roster is
/// sized to a persona's blurb, which is the only thing that tells one test
/// subject from another.
const double _accountMenuWidth = 300;
const double _helpMenuWidth = 268;
const double _rosterMenuWidth = 400;

/// Every menu in the bar's right cluster wears the popup menus' clothes —
/// surface, hairline border, no elevation and no tint — because `MenuAnchor`
/// reads none of `popupMenuTheme`, and the rail's menus, which are still
/// `PopupMenuButton`s, do.
MenuStyle _cumulusMenuStyle(double width) => MenuStyle(
  backgroundColor: const WidgetStatePropertyAll(CumulusColors.surface),
  surfaceTintColor: const WidgetStatePropertyAll(Colors.transparent),
  shadowColor: const WidgetStatePropertyAll(Colors.transparent),
  elevation: const WidgetStatePropertyAll(0),
  padding: const WidgetStatePropertyAll(EdgeInsets.symmetric(vertical: 6)),
  minimumSize: WidgetStatePropertyAll(Size(width, 0)),
  maximumSize: WidgetStatePropertyAll(Size(width, double.infinity)),
  shape: WidgetStatePropertyAll(
    RoundedRectangleBorder(
      borderRadius: BorderRadius.circular(CumulusRadius.control),
      side: const BorderSide(color: CumulusColors.border),
    ),
  ),
);

/// One row of either menu: the 13px type the popup menus use, and a hover
/// wash rather than the theme's accent ripple, which on a menu this dense
/// reads as a selection that has already happened.
///
/// The style is built from the text theme rather than written out, because a
/// `ButtonStyle.textStyle` *replaces* the ambient default rather than merging
/// with it: a bare `TextStyle(fontSize: 13)` drops the console's font and the
/// rows come back in the platform's.
ButtonStyle _menuRowStyle(BuildContext context, {EdgeInsetsGeometry? padding}) {
  final base = Theme.of(context).textTheme.bodyMedium ?? const TextStyle();
  return ButtonStyle(
    textStyle: WidgetStatePropertyAll(
      base.copyWith(fontSize: 13, color: CumulusColors.ink),
    ),
    padding: WidgetStatePropertyAll(
      padding ?? const EdgeInsets.symmetric(horizontal: 14),
    ),
    overlayColor: WidgetStateProperty.resolveWith(
      (states) => states.isEmpty ? null : CumulusColors.surfaceMuted,
    ),
  );
}

/// Gives every row of a menu the panel's width.
///
/// `MenuStyle.minimumSize` does not do this: it widens the box the panel is
/// laid out in, while the panel itself still hugs its widest row — so a menu
/// sized that way reserves 300px, paints 240, and sits 60px from where
/// anything anchored to it expects the edge to be.
List<Widget> _menuRows(double width, List<Widget> rows) => [
  for (final row in rows) SizedBox(width: width, child: row),
];

/// A hairline between regions of a menu.
class _MenuRule extends StatelessWidget {
  const _MenuRule();

  @override
  Widget build(BuildContext context) => const Padding(
    padding: EdgeInsets.symmetric(vertical: 6),
    child: Divider(height: 1),
  );
}

/// The console's one identity control: who signed in, who the server is
/// answering as, and — for an operator the session allows — the roster to
/// become somebody else.
///
/// The switcher used to be its own 250px control in the bar, on the argument
/// that the account is who signed in and the persona is who the server is
/// answering as, and that one control for both would lose the distinction.
/// They are still two different people during an impersonation, but this menu
/// names both without spending a quarter of the bar on it, and the acting-as
/// chip carries the distinction while the menu is shut.
///
/// The roster is a submenu rather than more rows in this menu. Inlined, a
/// dozen personas — each of which needs the width of a blurb to be worth
/// reading — would bury Sign out under a wall of test subjects and drag the
/// account menu to the width of the roster. It flies out to the left: this
/// button is the last thing on the bar, so there is never room to its right,
/// and the layout flips a submenu that would run off the edge.
class _AccountButton extends StatelessWidget {
  final String? label;

  /// The acting persona and the roster behind the switcher, or null while the
  /// first session request is still in flight.
  final Session? session;

  final VoidCallback? onSignOut;
  final ValueChanged<String>? onSwitchPersona;
  final VoidCallback? onOpenEmployeeHub;
  final bool hubActive;

  const _AccountButton({
    this.label,
    this.session,
    this.onSignOut,
    this.onSwitchPersona,
    this.onOpenEmployeeHub,
    this.hubActive = false,
  });

  bool get _canSwitch =>
      (session?.maySwitchPersona ?? false) && onSwitchPersona != null;

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
    final acting = session?.impersonating ?? false;
    return MenuAnchor(
      style: _cumulusMenuStyle(_accountMenuWidth),
      // Hangs from the button's right edge, not its left: the button is 18px
      // from the window edge, and the default alignment would push a 300px
      // menu off the screen and leave the overlay to clamp it flush.
      alignmentOffset: const Offset(
        -(_accountMenuWidth - _roundButtonSize),
        _menuDrop,
      ),
      menuChildren: _menuRows(_accountMenuWidth, [
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 14),
          child: _AccountHeader(label: label, session: session),
        ),
        if (_canSwitch) ...[
          const _MenuRule(),
          _PersonaSubmenu(session: session!, onSwitch: onSwitchPersona!),
        ],
        const _MenuRule(),
        MenuItemButton(
          style: _menuRowStyle(context),
          leadingIcon: const Icon(
            Icons.badge_outlined,
            size: 16,
            color: CumulusColors.inkSecondary,
          ),
          onPressed: onOpenEmployeeHub,
          child: const _MenuRowLabel(
            title: 'Employee Hub',
            subtitle: 'PTO, benefits, and the HR Agent',
          ),
        ),
        MenuItemButton(
          style: _menuRowStyle(context),
          leadingIcon: const Icon(
            Icons.logout,
            size: 16,
            color: CumulusColors.inkSecondary,
          ),
          onPressed: onSignOut,
          child: const Text('Sign out'),
        ),
      ]),
      builder: (context, controller, _) => Tooltip(
        message: label ?? 'Account',
        child: InkWell(
          customBorder: const CircleBorder(),
          onTap: () =>
              controller.isOpen ? controller.close() : controller.open(),
          child: Container(
            width: _roundButtonSize,
            height: _roundButtonSize,
            alignment: Alignment.center,
            decoration: BoxDecoration(
              color: hubActive ? CumulusColors.ink : CumulusColors.surface,
              shape: BoxShape.circle,
              border: Border.all(
                color: hubActive
                    ? CumulusColors.ink
                    : acting
                    ? CumulusColors.accent
                    : CumulusColors.border,
                width: acting && !hubActive ? 1.5 : 1,
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
        ),
      ),
    );
  }
}

/// The row that opens the roster, and the roster it opens.
///
/// The row names who you are acting as now, so the switcher answers "who am I"
/// on the way to "who else could I be" — and so the menu is worth opening for
/// an operator who only wanted to check.
class _PersonaSubmenu extends StatelessWidget {
  final Session session;
  final ValueChanged<String> onSwitch;

  const _PersonaSubmenu({required this.session, required this.onSwitch});

  int get _rosterSize =>
      session.groups.fold(0, (n, group) => n + group.personas.length);

  @override
  Widget build(BuildContext context) {
    return SubmenuButton(
      style: _menuRowStyle(context),
      menuStyle: _cumulusMenuStyle(_rosterMenuWidth),
      // Hugs the account menu's edge rather than the row's, so the two panels
      // read as one control opening sideways instead of two loose cards.
      alignmentOffset: const Offset(6, -6),
      leadingIcon: const Icon(
        Icons.switch_account_outlined,
        size: 16,
        color: CumulusColors.inkSecondary,
      ),
      submenuIcon: const WidgetStatePropertyAll(
        Icon(Icons.chevron_left, size: 18, color: CumulusColors.inkMuted),
      ),
      menuChildren: _menuRows(_rosterMenuWidth, _roster(context)),
      child: _MenuRowLabel(
        title: 'Switch persona',
        subtitle: _rosterSize == 1
            ? '1 persona on the roster'
            : '$_rosterSize personas on the roster',
      ),
    );
  }

  /// The roster, grouped as the server grouped it, with a rule between groups
  /// so "Naviant" and the customer's own people do not read as one list.
  List<Widget> _roster(BuildContext context) {
    final rows = <Widget>[];
    for (final group in session.groups) {
      if (rows.isNotEmpty) rows.add(const _MenuRule());
      rows.add(
        Padding(
          // Over the names it groups, not over the gutter the check sits in.
          padding: const EdgeInsets.fromLTRB(30, 6, 16, 2),
          child: Text(group.label.toUpperCase(), style: CumulusText.statLabel),
        ),
      );
      for (final option in group.personas) {
        rows.add(
          MenuItemButton(
            style: _menuRowStyle(
              context,
              padding: const EdgeInsets.only(left: 8, right: 16),
            ),
            onPressed: () => onSwitch(option.id),
            child: _PersonaEntry(
              option: option,
              selected: option.id == session.personaId,
            ),
          ),
        );
      }
    }
    return rows;
  }
}

/// A menu row's two lines: what it does, and the one fact you need to decide
/// whether to press it.
class _MenuRowLabel extends StatelessWidget {
  final String title;
  final String subtitle;

  const _MenuRowLabel({required this.title, required this.subtitle});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 7),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(title, style: const TextStyle(fontSize: 13)),
          Text(
            subtitle,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: CumulusText.sectionSubtitle,
          ),
        ],
      ),
    );
  }
}

/// The menu's first rows: the person actually signed in, then the persona the
/// server is answering as. Two blocks rather than one, because during an
/// impersonation they are two different people and this is the anchor that has
/// to keep saying which is which.
///
/// The role is worth its line except when it is the person's own name, which
/// is how the customer-user personas are named on the server: "TOMAS BERG"
/// above "Connie Alberts" says nothing.
class _AccountHeader extends StatelessWidget {
  final String? label;
  final Session? session;

  const _AccountHeader({this.label, this.session});

  /// The title, plus the reason a persona's personal surfaces will be empty.
  /// Naviant Support is not in the customer's org chart, and a hub with
  /// nothing in it should read as correct rather than as a failed request.
  String _subtitle(Session session) => session.inWorkforce
      ? session.title
      : '${session.title} · no employee record';

  String _eyebrow(Session session) {
    if (session.impersonating) return 'ACTING AS';
    final role = session.label == session.displayName ? '' : session.label;
    return role.isEmpty ? 'SIGNED IN AS' : role.toUpperCase();
  }

  @override
  Widget build(BuildContext context) {
    final session = this.session;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(label ?? 'Signed in', style: CumulusText.meta),
          if (session != null) ...[
            const SizedBox(height: 8),
            Text(
              _eyebrow(session),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: CumulusText.statLabel.copyWith(
                fontSize: 9.5,
                height: 1.3,
                color: session.impersonating
                    ? CumulusColors.accentInk
                    : CumulusColors.inkMuted,
              ),
            ),
            Text(
              session.displayName,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: CumulusText.rowTitle.copyWith(fontSize: 13, height: 1.25),
            ),
            Text(
              _subtitle(session),
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: CumulusText.sectionSubtitle.copyWith(height: 1.25),
            ),
          ],
        ],
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
          if (destination.badge != null)
            Positioned(
              top: 5,
              right: 4,
              // The only thing on the rail that redraws without a navigation:
              // listening here is what keeps the count from being a reason to
              // rebuild the page beside it.
              child: ValueListenableBuilder<int>(
                valueListenable: destination.badge!,
                builder: (context, count, _) => count == 0
                    ? const SizedBox.shrink()
                    : Container(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 4,
                          vertical: 1,
                        ),
                        decoration: BoxDecoration(
                          color: CumulusColors.ink,
                          borderRadius: BorderRadius.circular(999),
                        ),
                        child: Text(
                          '$count',
                          style: const TextStyle(
                            fontSize: 8.5,
                            fontWeight: FontWeight.w700,
                            color: Colors.white,
                          ),
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
