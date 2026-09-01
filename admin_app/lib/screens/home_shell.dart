import 'package:flutter/material.dart';

import '../models/asset.dart';
import '../models/session.dart';
import '../services/api_client.dart';
import '../services/auth_service.dart';
import '../services/session_service.dart';
import '../theme/cumulus_theme.dart';
import '../widgets/command_palette.dart';
import '../widgets/cumulus_shell.dart';
import '../widgets/tasks_view.dart';
import 'api_docs_screen.dart';
import '../widgets/calendar_view.dart';
import '../widgets/abox_explorer.dart';
import '../widgets/home_view.dart';
import '../widgets/identity_workbench.dart';
import '../widgets/ontology/ontology_canvas.dart';
import '../widgets/payables_view.dart';
import '../widgets/reimbursements_view.dart';
import '../widgets/sources_plane.dart';
import '../widgets/system_state.dart';
import '../widgets/ui_kit.dart';
import 'asset_list_screen.dart';
import 'employee_hub_screen.dart';
import 'identities_screen.dart';
import '../widgets/queues_view.dart';
import 'flows_screen.dart';

/// Section indices. The rail is not a one-to-one map onto them: Workspaces is
/// a dropdown stop holding two of them, and the Employee Hub holds one with no
/// stop at all — it is personal rather than a workstream, so the account
/// button carries its active state and the rail correctly shows nothing
/// selected.
///
/// The rail reads in two groups, and the order is an argument about what a
/// console is for. The first four stops are *yours* — your day, your week,
/// your queue, the workspaces you work in. The rest are the platform: the
/// knowledge layer first, because everything else is either written into it
/// (Flows) or governs who may touch it (Identities).
/// Read from `command_palette.dart`, which is the one place they are declared.
/// They used to be declared here *and* copied there, and the copy drifted:
/// three of the palette's entries still pointed where those sections had been
/// before a reorder. The palette owns `OpenSectionIntent`, so the vocabulary
/// that intent speaks lives beside it.
const int _homeIndex = homeIndex;
const int _calendarIndex = calendarIndex;
const int _tasksIndex = tasksIndex;
const int _expenseWorkspaceIndex = expenseWorkspaceIndex;
const int _payablesWorkspaceIndex = payablesWorkspaceIndex;
const int _identityWorkspaceIndex = identityWorkspaceIndex;
const int _systemStateIndex = systemStateIndex;
const int _knowledgeIndex = knowledgeIndex;
const int _flowsIndex = flowsIndex;
const int _identitiesIndex = identitiesIndex;
const int _employeeHubIndex = employeeHubIndex;
const int _queuesIndex = queuesIndex;

/// The Library's tab index inside the Knowledge layer section. Named because
/// the palette navigates to it and a bare `1` would not survive a reorder.
const int _libraryPlane = 1;

/// Where the section lands. Ontology first because the diagram *is* the model
/// — the thing an Overview used to describe in cards, shown instead.
const int _ontologyPlane = 0;

/// The Sources tab inside the Knowledge layer. Named for the same reason the
/// Library's is: System state hands off to it, and a bare `2` would not
/// survive a reorder of the tabs.
const int _sourcesPlane = 2;

/// The rail. `badge` on the Tasks stop is filled in per build from the open
/// count — the badge moved off the top bar with the queue itself.
///
/// The governance stops are dropped for an own-scoped persona, because both of
/// them answer 403 to one: the knowledge layer is gated on scope and the
/// identity roster on team administration. A nav entry that can only ever fail
/// is worse than no entry — it was offering an AP clerk a destination whose
/// error came with a Retry button that could never succeed.
List<RailDestination> _destinations(int openTasks, Session? session) {
  final governs = session == null || session.scope != 'own';
  // Not `governs`. Identity curation is work with a queue behind it, and the
  // persona that owns it — the Controller — is own-scoped. The server answers
  // this on the session because the rule is its own (`may_open_identity`), and
  // deriving it here would mean a copy of the queue names in Dart.
  final curatesIdentity = session == null || session.mayOpenIdentity;
  final workspaces = <RailChild>[
    if (ownsWorkspace(session, expenseQueue))
      const RailChild(
        label: 'Expense Reimbursement',
        detail: 'requests, decisions, spend',
        index: _expenseWorkspaceIndex,
      ),
    if (ownsWorkspace(session, payablesQueue))
      const RailChild(
        label: 'Accounts Payable',
        detail: 'invoices, matching, posting',
        index: _payablesWorkspaceIndex,
      ),
    // Named for what it holds rather than "Identity", which this rail already
    // spends on the roster of people who may sign in. These are
    // counterparties: the companies, vendors and contacts the estate keeps
    // more than one row about.
    if (curatesIdentity)
      const RailChild(
        label: 'Counterparty identity',
        detail: 'proposed merges, evidence, decisions',
        index: _identityWorkspaceIndex,
      ),
  ];
  return [
    const RailDestination(
      icon: Icons.home_outlined,
      label: 'Home',
      index: _homeIndex,
    ),
    const RailDestination(
      icon: Icons.calendar_today_outlined,
      label: 'Calendar',
      index: _calendarIndex,
    ),
    RailDestination(
      icon: Icons.inbox_outlined,
      label: 'My Tasks',
      index: _tasksIndex,
      badge: openTasks,
    ),
    // A Workspace is a business view onto rows the flows write — scoped work,
    // not a plane of the map. They hung off the Knowledge layer's Data tab
    // once, which put a business surface inside the governance surface; the
    // dropdown is where they belong.
    //
    // A stop with no children left is dropped whole: an empty dropdown is a
    // destination that opens onto nothing, which is the same dead end as a
    // stop whose route can only 403.
    if (workspaces.isNotEmpty)
      RailDestination(
        icon: Icons.grid_view_outlined,
        label: 'Workspaces',
        children: workspaces,
      ),
    // The governance group opens on the reading of the whole deployment,
    // because every other stop in it is one of the surfaces this one counts:
    // it says which of them is worth opening this morning.
    if (governs)
      const RailDestination(
        icon: Icons.monitor_heart_outlined,
        label: 'System state',
        index: _systemStateIndex,
        startsGroup: true,
      ),
    if (governs)
      const RailDestination(
        icon: Icons.hub_outlined,
        label: 'Knowledge layer',
        index: _knowledgeIndex,
      ),
    const RailDestination(
      icon: Icons.account_tree_outlined,
      label: 'Flows',
      index: _flowsIndex,
      startsGroup: true,
    ),
    if (governs)
      const RailDestination(
        icon: Icons.groups_outlined,
        label: 'Identities, access & keys',
        index: _identitiesIndex,
      ),
    // Beside identities and not inside it, because they answer adjacent
    // questions that are not the same question: who someone *is* and what they
    // may reach, against where work *lands* and who is accountable for it. The
    // doctrine keeps them as two halves of one Role for now (point 230 leaves
    // the divergence open) and the console had a screen for only one of them.
    if (governs)
      const RailDestination(
        icon: Icons.workspaces_outline,
        label: 'Desks',
        index: _queuesIndex,
      ),
  ];
}

/// Holds the rail selection and the command palette.
///
/// The palette lives here rather than in [CumulusShell] because it outlives any
/// one page: the asset editor publishes its open document into it, and its
/// results must survive switching sections. Search is no longer routed into a
/// page — a query typed while an asset was open used to run, store its results
/// behind the editor, and appear only once you navigated back.
class HomeShell extends StatefulWidget {
  const HomeShell({super.key});

  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> {
  final ApiClient _api = ApiClient();
  late final CommandCatalog _catalog = CommandCatalog(_api);
  late final CommandPaletteController _palette = CommandPaletteController(
    api: _api,
    catalog: _catalog,
    onIntent: _handleIntent,
  );
  late final SessionController _session = SessionController(_api);

  /// A request to open a specific asset, consumed by [AssetsSection].
  final ValueNotifier<String?> _openAsset = ValueNotifier(null);

  /// The full-page result list, populated only from "See all results".
  final ValueNotifier<({String query, SearchFilters filters})?> _pageResults =
      ValueNotifier(null);

  int _index = _homeIndex;

  /// The open-task count behind the rail badge. Held here because the badge
  /// lives on a rail stop the shell draws, and refreshed whenever the queue
  /// might have moved: on first build, and after any decision made in the
  /// Tasks section. It does not poll — there is no push channel yet, and a
  /// silently wrong number is worse than a slightly stale one that corrects
  /// itself on every interaction.
  int _openTasks = 0;

  /// Which tab of the Knowledge layer is showing — see the labels in
  /// [_KnowledgeSection]. Held here so opening an asset from the palette can
  /// flip back to the Library, wherever that tab sits.
  final ValueNotifier<int> _knowledgePlane = ValueNotifier(_ontologyPlane);

  /// Sections fetch on first build, so a section that has never been opened
  /// stays unbuilt rather than firing a request the user did not ask for.
  final Set<int> _visited = {_homeIndex};

  /// A proposal to land the identity workbench on, by `link:` case ref — how
  /// a Company or Person detail sends you to the desk that judges it.
  String? _openProposal;

  @override
  void initState() {
    super.initState();
    _loadSession();
    _refreshTaskBadge();
  }

  @override
  void dispose() {
    _palette.dispose();
    _catalog.dispose();
    _openAsset.dispose();
    _pageResults.dispose();
    _knowledgePlane.dispose();
    _session.dispose();
    _api.close();
    super.dispose();
  }

  Future<void> _loadSession() async {
    try {
      await _session.load();
    } on ApiException {
      // The switcher simply does not appear. The console still works as
      // whoever the server already thinks you are, which is the honest
      // fallback: the alternative is a shell that will not draw.
    }
    _publishSessionToPalette();
  }

  /// The palette offers what the rail offers.
  ///
  /// It used to list every governance section unconditionally, so an
  /// own-scoped persona could jump to one the rail had correctly dropped and
  /// land on the blank widget the shell mounts in its place.
  ///
  /// Set here rather than inside `build`: the catalog is a ChangeNotifier the
  /// palette listens to, and notifying it mid-build would mark a widget dirty
  /// while the frame it belongs to is being assembled.
  void _publishSessionToPalette() {
    _catalog.session = _session.session.value;
  }

  /// Every section is rebuilt from scratch afterwards — see [SessionController]
  /// — but the task badge is the shell's own state, and the new persona owns
  /// different queues, so it has to be asked again here.
  Future<void> _switchPersona(String personaId) async {
    try {
      await _session.switchTo(personaId);
    } on ApiException {
      // A refused switch leaves the previous session on screen.
    }
    _publishSessionToPalette();
    await _refreshTaskBadge();
  }

  Future<void> _refreshTaskBadge() async {
    try {
      final stats = await _api.taskStats();
      if (mounted) setState(() => _openTasks = stats.open);
    } on ApiException {
      // The badge is a convenience; a failed count never blocks the shell.
    }
  }

  void _show(int index) {
    setState(() {
      _index = index;
      _visited.add(index);
    });
  }

  /// Open the identity workbench on one proposal. An entity's "also known as"
  /// is part of what the entity is, so the explorer names its proposals — and
  /// naming one is only useful if it goes somewhere.
  void _showProposal(String caseRef) {
    setState(() => _openProposal = caseRef);
    _show(_identityWorkspaceIndex);
  }

  /// The palette finds things; this decides what "open" means for each of them.
  void _handleIntent(PaletteIntent intent) {
    switch (intent) {
      case OpenAssetIntent(:final assetId):
        _show(_knowledgeIndex);
        _knowledgePlane.value = _libraryPlane;
        _pageResults.value = null;
        _openAsset.value = assetId;
      case CreateAssetIntent():
        _show(_knowledgeIndex);
        _knowledgePlane.value = _libraryPlane;
        _pageResults.value = null;
        _openAsset.value = '';
      case ShowAllResultsIntent(:final query, :final filters):
        _show(_knowledgeIndex);
        _knowledgePlane.value = _libraryPlane;
        _openAsset.value = null;
        _pageResults.value = (query: query, filters: filters);
      case OpenSectionIntent(:final index):
        _show(index);
      // Teams and identities both live on the identities page, which owns its
      // own drill-down. Bringing the section forward is as far as the palette
      // can honestly navigate without reaching into that page's state.
      case OpenTeamIntent():
      case OpenIdentityIntent():
        _show(_identitiesIndex);
    }
  }

  /// The governance sections, which an own-scoped persona cannot reach.
  static const _governance = {
    _systemStateIndex,
    _knowledgeIndex,
    _identitiesIndex,
    _queuesIndex,
  };

  Widget _lazy(int index, Widget Function() build, Session? session) {
    if (!_visited.contains(index)) return const SizedBox.shrink();
    // A section stays alive in the IndexedStack once visited, so switching to
    // a persona that may not see it left the old one mounted and refetching —
    // one 403 per governance section the *previous* persona had opened. The
    // rail already drops the stop; unbuilding the section is the other half.
    if (session != null &&
        session.scope == 'own' &&
        _governance.contains(index)) {
      return const SizedBox.shrink();
    }
    // The workbench answers to its own rule rather than to scope. Same halving
    // of the same problem: the rail drops the child, and this unmounts it.
    if (index == _identityWorkspaceIndex &&
        session != null &&
        !session.mayOpenIdentity) {
      return const SizedBox.shrink();
    }
    // And the same again for the two workspaces that belong to a desk. The
    // AP route behind this one now refuses an unbound persona outright, so
    // leaving the section mounted through a switch would fetch a 403 and draw
    // it as an outage.
    if (index == _expenseWorkspaceIndex &&
        !ownsWorkspace(session, expenseQueue)) {
      return const SizedBox.shrink();
    }
    if (index == _payablesWorkspaceIndex &&
        !ownsWorkspace(session, payablesQueue)) {
      return const SizedBox.shrink();
    }
    return build();
  }

  @override
  Widget build(BuildContext context) {
    return ValueListenableBuilder<Session?>(
      valueListenable: _session.session,
      builder: (context, session, _) => _shell(context, session),
    );
  }

  /// The selected section survives a persona switch on purpose: "can Kim post
  /// this invoice" is a question you ask by staying on the invoice and
  /// changing who is looking at it.
  Widget _shell(BuildContext context, Session? session) {
    return CumulusShell(
      destinations: _destinations(_openTasks, session),
      selectedIndex: _index,
      onDestinationSelected: _show,
      palette: _palette,
      accountLabel: session?.operatorEmail ?? AuthService.instance.email,
      onSignOut: AuthService.instance.signOut,
      session: session,
      onSwitchPersona: _switchPersona,
      onOpenApiDocs: () => Navigator.of(
        context,
      ).push(MaterialPageRoute(builder: (_) => const ApiDocsScreen())),
      onOpenEmployeeHub: () => _show(_employeeHubIndex),
      employeeHubActive: _index == _employeeHubIndex,
      // Ordered by section index, which is what IndexedStack selects on. The
      // generation key discards every section's state when the persona
      // changes, so each one refetches as the new persona instead of showing
      // what the last one was allowed to see.
      child: IndexedStack(
        key: ValueKey(_session.generation),
        index: _index,
        children: [
          _lazy(
            _homeIndex,
            () => HomeSection(
              api: _api,
              session: session,
              onOpenCalendar: () => _show(_calendarIndex),
              onOpenPayables: () => _show(_payablesWorkspaceIndex),
              onOpenTasks: () => _show(_tasksIndex),
            ),
            session,
          ),
          _lazy(_calendarIndex, () => CalendarSection(api: _api), session),
          _lazy(
            _tasksIndex,
            () => TasksSection(
              api: _api,
              session: session,
              onQueueChanged: _refreshTaskBadge,
            ),
            session,
          ),
          _lazy(
            _expenseWorkspaceIndex,
            () => ReimbursementsView(api: _api, session: session),
            session,
          ),
          _lazy(
            _payablesWorkspaceIndex,
            () => PayablesView(api: _api, session: session),
            session,
          ),
          _lazy(
            _identityWorkspaceIndex,
            // Keyed by the proposal so arriving from a second entity lands on
            // that one rather than reusing the workbench parked on the last.
            () => IdentityWorkbench(
              key: ValueKey(_openProposal ?? 'all'),
              api: _api,
              onQueueChanged: _refreshTaskBadge,
              initialCaseRef: _openProposal,
            ),
            session,
          ),
          // Every hand-off below is the surface that holds the rows the panel
          // counted. A tile whose number could not be gone and read would be a
          // figure this screen owned, which is the one thing it must not be.
          _lazy(
            _systemStateIndex,
            () => SystemStateSection(
              api: _api,
              session: session,
              onOpenOntology: () {
                _knowledgePlane.value = _ontologyPlane;
                _show(_knowledgeIndex);
              },
              onOpenSources: () {
                _knowledgePlane.value = _sourcesPlane;
                _show(_knowledgeIndex);
              },
              onOpenTasks: () => _show(_tasksIndex),
              onOpenIdentity: () => _show(_identityWorkspaceIndex),
              onOpenFlows: () => _show(_flowsIndex),
            ),
            session,
          ),
          _lazy(
            _knowledgeIndex,
            () => _KnowledgeSection(
              api: _api,
              plane: _knowledgePlane,
              session: session,
              onOpenProposal: _showProposal,
              library: AssetsSection(
                palette: _palette,
                openAsset: _openAsset,
                pageResults: _pageResults,
                onCorpusChanged: _catalog.refresh,
              ),
            ),
            session,
          ),
          _lazy(_flowsIndex, () => const FlowsSection(), session),
          _lazy(_identitiesIndex, () => const IdentitiesSection(), session),
          _lazy(
            _queuesIndex,
            // Reading is any authenticated principal's right — knowing the AP
            // desk exists and that three people hold it is how you know where
            // to send something. Editing is admin, because a binding grants
            // the authority to decide everything that routes there.
            () => QueuesView(
              api: _api,
              canAdminister:
                  session?.authority == 'admin' ||
                  session?.authority == 'owner',
            ),
            session,
          ),
          _lazy(
            _employeeHubIndex,
            () => EmployeeHubSection(api: _api),
            session,
          ),
        ],
      ),
    );
  }
}

/// The Knowledge layer: three questions, three tabs.
///
/// **Ontology** — what this system knows, how it relates, and which flows
/// touch it. The landing tab, because the diagram is the whole model at once;
/// the Overview it replaced was the tab bar redrawn as cards, offering the
/// same four destinations for the same click.
///
/// **Library** — what people wrote down that agents must obey.
///
/// **Sources** — did the outside world deliver, and was it clean.
///
/// Collection, TBox and ABox are gone from the navigation. None of the three
/// appears anywhere in DOCTRINE.md; they were implementation vocabulary
/// holding top-level stops. A Collection turns out to be a *property of a
/// type* — the seven of them exactly partition the seventeen object types —
/// so the diagram draws them as the frames around its clusters, which is
/// where a property belongs. The TBox and the ABox are one subject at two
/// zoom levels, so selecting a box and browsing what exists of it is a step
/// down rather than a second lens.
///
/// Every number lives beside the thing it measures, and nothing is stated
/// twice: conformance on the model, corpus traffic on the Library,
/// records-landed on Sources.
class _KnowledgeSection extends StatefulWidget {
  final ApiClient api;
  final ValueNotifier<int> plane;
  final Widget library;

  /// Null until the session has loaded. The three routes behind this section
  /// answer 403 to an own-scoped persona, so the section says so itself.
  final Session? session;

  /// Opens the identity workbench on one proposal — passed through to the ABox
  /// explorer, where an entity's standing merge proposals are part of what the
  /// entity is.
  final ValueChanged<String> onOpenProposal;

  const _KnowledgeSection({
    required this.api,
    required this.plane,
    required this.library,
    required this.session,
    required this.onOpenProposal,
  });

  @override
  State<_KnowledgeSection> createState() => _KnowledgeSectionState();
}

class _KnowledgeSectionState extends State<_KnowledgeSection> {
  /// The type whose instances are showing, or null for the model itself.
  /// Held here rather than inside the diagram because it is a zoom level of
  /// the Ontology tab, and the tab is what owns it.
  String? _instancesOf;

  @override
  Widget build(BuildContext context) {
    return ValueListenableBuilder<int>(
      valueListenable: widget.plane,
      builder: (context, selected, _) {
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // The section title sits above the tabs: the tabs are questions
            // *about* the knowledge layer, and without the title each one
            // reads like a top-level destination of its own. PageGutter, not
            // Padding, because the tabs below centre their content inside a
            // max width and the title has to land on the same left edge.
            PageGutter(
              padding: const EdgeInsets.fromLTRB(28, 18, 28, 0),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('Knowledge layer', style: CumulusText.pageTitle),
                  const SizedBox(height: 14),
                  if (_mayRead)
                    SegmentedTabs(
                      labels: const ['Ontology', 'Library', 'Sources'],
                      selectedIndex: selected,
                      onChanged: (i) => widget.plane.value = i,
                    ),
                ],
              ),
            ),
            Expanded(
              child: _mayRead
                  ? IndexedStack(
                      index: selected,
                      children: [
                        _ontology(),
                        widget.library,
                        SourcesPlane(api: widget.api),
                      ],
                    )
                  : const _NotYours(),
            ),
          ],
        );
      },
    );
  }

  /// Whether this persona reads past its own work. The rail already drops the
  /// stop for one that does not, so this only catches the case where the
  /// section was already open when the persona changed under it.
  bool get _mayRead => widget.session?.scope != 'own';

  /// The model, or what exists of one type of it — the same subject at two
  /// zoom levels rather than two lenses to choose between.
  Widget _ontology() {
    final type = _instancesOf;
    if (type == null) {
      return PageGutter(
        padding: const EdgeInsets.fromLTRB(28, 20, 28, 28),
        child: OntologyDiagram(
          api: widget.api,
          onBrowseInstances: (t) => setState(() => _instancesOf = t),
        ),
      );
    }
    return PageGutter(
      padding: const EdgeInsets.fromLTRB(28, 16, 28, 28),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Align(
            alignment: Alignment.centerLeft,
            child: BackLink(
              label: 'The model',
              onTap: () => setState(() => _instancesOf = null),
            ),
          ),
          const SizedBox(height: 12),
          Expanded(
            // Keyed by type so arriving from a different box reloads onto it
            // rather than reusing the explorer parked on the last one.
            child: ABoxExplorer(
              key: ValueKey(type),
              api: widget.api,
              initialType: type,
              onOpenProposal: widget.onOpenProposal,
            ),
          ),
        ],
      ),
    );
  }
}

/// What an own-scoped persona sees if the section is still open when they
/// become one. Not an error: the routes behind it refuse on purpose, and a
/// Retry button on a refusal that can never succeed is what made the old
/// version read like an outage.
class _NotYours extends StatelessWidget {
  const _NotYours();

  @override
  Widget build(BuildContext context) => PageBody(
    padding: const EdgeInsets.fromLTRB(28, 20, 28, 40),
    children: const [
      CumulusCard(
        child: CumulusEmptyState(
          icon: Icons.lock_outline,
          title: 'This is not scoped to you',
          // Second person, and never "this persona" — the persona is what you
          // are here, not a third party the screen is discussing.
          message:
              'The Knowledge layer is the whole map: every object type, the '
              'whole governed corpus, every inbound feed. Your access is to '
              'your own work, so there is nothing here for you rather than '
              'something withheld. Your queues are on Home and My Tasks.',
        ),
      ),
    ],
  );
}
