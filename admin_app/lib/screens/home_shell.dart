import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';

import '../models/asset.dart';
import '../models/session.dart';
import '../services/api_client.dart';
import '../services/app_route.dart';
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
import '../widgets/data_plane.dart';
import '../tasks/accounts_payable/workspace.dart';
import '../tasks/compliance/workspace.dart';
import '../tasks/expense_reimbursement/workspace.dart';
import '../tasks/field_documents/workspace.dart';
import '../tasks/plant_schedule/workspace.dart';
import '../tasks/registry.dart';
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
// The three plant workspaces, appended. The rail's *order* is built explicitly
// in `_destinations` below and does not follow these numbers, so a new stop
// goes on the end of the constants and wherever it belongs in the rail — which
// is what makes appending safe.
const int _fieldDocumentsWorkspaceIndex = fieldDocumentsWorkspaceIndex;
const int _complianceWorkspaceIndex = complianceWorkspaceIndex;
const int _plantScheduleWorkspaceIndex = plantScheduleWorkspaceIndex;

/// The slug each section answers to in the address bar.
///
/// Slugs rather than the indices above: those move whenever a stop is added,
/// and a link that had encoded `8` would quietly point at a different section
/// the next time one was inserted. Home is the empty slug, which is how it
/// comes out as `/` rather than `/home`.
const Map<int, String> _sectionSlugs = {
  _homeIndex: '',
  _calendarIndex: 'calendar',
  _tasksIndex: 'tasks',
  _expenseWorkspaceIndex: 'expenses',
  _payablesWorkspaceIndex: 'payables',
  _identityWorkspaceIndex: 'identity-review',
  _systemStateIndex: 'system',
  _knowledgeIndex: 'knowledge',
  _flowsIndex: 'flows',
  _identitiesIndex: 'identities',
  _employeeHubIndex: 'people',
  _queuesIndex: 'queues',
  _fieldDocumentsWorkspaceIndex: 'field-documents',
  _complianceWorkspaceIndex: 'compliance',
  _plantScheduleWorkspaceIndex: 'plant-schedule',
};

/// The section a slug names. An unrecognised one lands on Home rather than on
/// an error: a URL is user input, and a stale link should still open the
/// console.
int sectionIndexForSlug(String slug) =>
    _sectionSlugs.entries
        .where((entry) => entry.value == slug)
        .map((entry) => entry.key)
        .firstOrNull ??
    _homeIndex;

/// The Library's tab index inside the Knowledge layer section. Named because
/// the palette navigates to it and a bare `1` would not survive a reorder.
const int _libraryPlane = 1;

/// Where the section lands. Ontology first because the diagram *is* the model
/// — the thing an Overview used to describe in cards, shown instead.
const int _ontologyPlane = 0;

/// The Data tab inside the Knowledge layer: what we hold, under what policy.
const int _dataPlane = 2;

/// The Systems tab inside the Knowledge layer. Named for the same reason the
/// Library's is: System state hands off to it, and a bare `3` would not
/// survive a reorder of the tabs.
const int _systemsPlane = 3;

/// The rail. The Tasks stop carries the open count as a listenable rather than
/// a number — the badge moved off the top bar with the queue itself, and the
/// rail is the only thing that has to redraw when it moves.
///
/// The governance stops are dropped for an own-scoped persona, because both of
/// them answer 403 to one: the knowledge layer is gated on scope and the
/// identity roster on team administration. A nav entry that can only ever fail
/// is worse than no entry — it was offering an AP clerk a destination whose
/// error came with a Retry button that could never succeed.
List<RailDestination> _destinations(
  ValueListenable<int> openTasks,
  Session? session,
) {
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
    // The three plant workspaces, gated on the desk whose work each is *and*
    // on whether this deployment has the surface at all — see [showsWorkspace].
    // A deployment whose estate does not run the app a workspace rests on has
    // no rows to draw, and a stop onto an empty page is the same dead end as a
    // stop whose route can only 403.
    if (showsWorkspace(session, fieldOpsQueue, 'field-documents'))
      const RailChild(
        label: 'Field Documents',
        detail: 'job packets, crews, what is missing',
        index: _fieldDocumentsWorkspaceIndex,
      ),
    if (showsWorkspace(session, hrSafetyQueue, 'compliance'))
      const RailChild(
        label: 'Compliance',
        detail: 'credentials, inspections, days to expiry',
        index: _complianceWorkspaceIndex,
      ),
    if (showsWorkspace(session, plantSupervisorQueue, 'plant-schedule'))
      const RailChild(
        label: 'Plant Schedule',
        detail: 'the week, department by day',
        index: _plantScheduleWorkspaceIndex,
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
  ///
  /// A notifier rather than a field: the count moves after every decision and
  /// every persona switch, and as shell state each of those was a `setState`
  /// that rebuilt every section mounted in the stack below to change one digit.
  final ValueNotifier<int> _openTasks = ValueNotifier(0);

  /// Bumped when the served task→screen wiring lands. Only the Tasks section
  /// reads the registry, so only the Tasks section is rebuilt for it.
  final ValueNotifier<int> _taskScreens = ValueNotifier(0);

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
    // A link may already name a section, so the location is read before the
    // first frame rather than corrected after it.
    _index = sectionIndexForSlug(AppRouteState.instance.value.section);
    _visited.add(_index);
    AppRouteState.instance.addListener(_onRoute);
    _loadSession();
    _loadTaskScreens();
    _refreshTaskBadge();
  }

  /// Which review screen answers which task, from the server rather than from
  /// a table compiled in here — see `tasks/registry.dart`.
  ///
  /// Here rather than in `main()` because the console door needs a signed-in
  /// session, and not awaited before the first frame because a cold Cloud Run
  /// start would hold the whole shell behind it. Until it lands the registry
  /// answers from what is compiled in, so the queue is never unopenable; the
  /// rebuild is what lets a queue already on screen pick up the served wiring.
  Future<void> _loadTaskScreens() async {
    await TaskScreens.load(_api);
    if (mounted) _taskScreens.value++;
  }

  /// The location moved under us — a back button, or a link opened into a
  /// session already running.
  void _onRoute() {
    if (!mounted) return;
    final index = sectionIndexForSlug(AppRouteState.instance.value.section);
    if (index == _index) return;
    setState(() {
      _index = index;
      _visited.add(index);
    });
  }

  @override
  void dispose() {
    AppRouteState.instance.removeListener(_onRoute);
    _palette.dispose();
    _catalog.dispose();
    _openAsset.dispose();
    _pageResults.dispose();
    _knowledgePlane.dispose();
    _openTasks.dispose();
    _taskScreens.dispose();
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
      if (mounted) _openTasks.value = stats.open;
    } on ApiException {
      // The badge is a convenience; a failed count never blocks the shell.
    }
  }

  void _show(int index) {
    // Through the location, which comes straight back through `_onRoute`.
    // Writing both here would make the address bar a copy of this state
    // rather than the same fact, and copies drift.
    AppRouteState.instance.openSection(_sectionSlugs[index] ?? '');
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
    // And once more for the three plant workspaces, on the same rule the rail
    // drops their children with. `showsWorkspace` rather than `ownsWorkspace`
    // here on purpose: a section this deployment does not have would otherwise
    // stay mounted through a persona switch and refetch a route that cannot
    // answer, which the console draws as an outage rather than as an absence.
    if (index == _fieldDocumentsWorkspaceIndex &&
        !showsWorkspace(session, fieldOpsQueue, 'field-documents')) {
      return const SizedBox.shrink();
    }
    if (index == _complianceWorkspaceIndex &&
        !showsWorkspace(session, hrSafetyQueue, 'compliance')) {
      return const SizedBox.shrink();
    }
    if (index == _plantScheduleWorkspaceIndex &&
        !showsWorkspace(session, plantSupervisorQueue, 'plant-schedule')) {
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
      // The generation key discards every section's state when the persona
      // changes, so each one refetches as the new persona instead of showing
      // what the last one was allowed to see.
      child: IndexedStack(
        key: ValueKey(_session.generation),
        index: _index,
        children: _slots(session),
      ),
    );
  }

  /// The stack's children, one per section, in the only order an IndexedStack
  /// understands: its own positions.
  ///
  /// The children are emitted from [_sections], where every slot carries the
  /// constant it answers to, and the assertion checks that each one landed in
  /// the position its constant names. Position and constant are the same fact
  /// said twice, and they said it differently once: Desks sat in slot 10 while
  /// `employeeHubIndex` pointed there, so opening the hub selected the desks
  /// slot — which nobody had visited, so it drew a blank pane rather than
  /// failing. A reorder that gets it wrong now stops in debug.
  ///
  /// The third clause is the one the plant workspaces added. Counting the
  /// slugs catches a section with no slug *only while the two lists are the
  /// same length*, which they stop being the moment somebody adds a slug and a
  /// section in one go and mismatches them — two errors that cancel in a
  /// count. Naming each index is the check that does not cancel: a section
  /// whose index has no slug opens fine and addresses as `/`, which is a link
  /// that silently points at Home.
  List<Widget> _slots(Session? session) {
    final sections = _sections(session);
    assert(() {
      for (var i = 0; i < sections.length; i++) {
        if (sections[i].$1 != i) return false;
        if (!_sectionSlugs.containsKey(sections[i].$1)) return false;
      }
      return sections.length == _sectionSlugs.length;
    }(), 'a section is not in the slot its index constant names, or has no slug');
    return [
      for (final (index, build) in sections) _lazy(index, build, session),
    ];
  }

  /// Every section, keyed by the constant that selects it. Written in constant
  /// order because the list is emitted in list order — see [_slots].
  List<(int, Widget Function())> _sections(Session? session) => [
    (
      _homeIndex,
      () => HomeSection(
        api: _api,
        session: session,
        onOpenCalendar: () => _show(_calendarIndex),
        onOpenPayables: () => _show(_payablesWorkspaceIndex),
        onOpenTasks: () => _show(_tasksIndex),
      ),
    ),
    (_calendarIndex, () => CalendarSection(api: _api)),
    (
      _tasksIndex,
      // The only section that reads the served task→screen registry, so it is
      // the only one rebuilt when that lands.
      () => ValueListenableBuilder<int>(
        valueListenable: _taskScreens,
        builder: (context, _, _) => TasksSection(
          api: _api,
          session: session,
          onQueueChanged: _refreshTaskBadge,
        ),
      ),
    ),
    (
      _expenseWorkspaceIndex,
      () => ReimbursementsView(api: _api, session: session),
    ),
    (_payablesWorkspaceIndex, () => PayablesView(api: _api, session: session)),
    (
      _identityWorkspaceIndex,
      // Keyed by the proposal so arriving from a second entity lands on that
      // one rather than reusing the workbench parked on the last.
      () => IdentityWorkbench(
        key: ValueKey(_openProposal ?? 'all'),
        api: _api,
        onQueueChanged: _refreshTaskBadge,
        initialCaseRef: _openProposal,
      ),
    ),
    // Every hand-off below is the surface that holds the rows the panel
    // counted. A tile whose number could not be gone and read would be a
    // figure this screen owned, which is the one thing it must not be.
    (
      _systemStateIndex,
      () => SystemStateSection(
        api: _api,
        session: session,
        onOpenOntology: () {
          _knowledgePlane.value = _ontologyPlane;
          _show(_knowledgeIndex);
        },
        onOpenSources: () {
          _knowledgePlane.value = _systemsPlane;
          _show(_knowledgeIndex);
        },
        onOpenTasks: () => _show(_tasksIndex),
        onOpenIdentity: () => _show(_identityWorkspaceIndex),
        onOpenFlows: () => _show(_flowsIndex),
      ),
    ),
    (
      _knowledgeIndex,
      () => _KnowledgeSection(
        api: _api,
        plane: _knowledgePlane,
        session: session,
        onOpenProposal: _showProposal,
        // The Ontology tab linking to its own export: land on the Library with
        // the asset open, the same path the palette takes.
        onOpenAsset: (assetId) {
          _knowledgePlane.value = _libraryPlane;
          _pageResults.value = null;
          _openAsset.value = assetId;
        },
        library: AssetsSection(
          palette: _palette,
          openAsset: _openAsset,
          pageResults: _pageResults,
          onCorpusChanged: _catalog.refresh,
        ),
      ),
    ),
    (_flowsIndex, () => const FlowsSection()),
    (_identitiesIndex, () => const IdentitiesSection()),
    (
      _employeeHubIndex,
      () => EmployeeHubSection(
        api: _api,
        // The same path the palette and the Ontology tab take: land on the
        // Library with the cited document open.
        onOpenAsset: (assetId) {
          _knowledgePlane.value = _libraryPlane;
          _pageResults.value = null;
          _openAsset.value = assetId;
          _show(_knowledgeIndex);
        },
      ),
    ),
    (
      _queuesIndex,
      // Reading is any authenticated principal's right — knowing the AP desk
      // exists and that three people hold it is how you know where to send
      // something. Editing is admin, because a binding grants the authority to
      // decide everything that routes there.
      () => QueuesView(
        api: _api,
        canAdminister:
            session?.authority == 'admin' || session?.authority == 'owner',
      ),
    ),
    // Appended, in constant order, after the last stop this file already had.
    // The assertion in [_slots] is what holds the list and the constants to
    // the same story; appending keeps both halves of it true, and inserting
    // would break the first half loudly and the second half not at all.
    (
      _fieldDocumentsWorkspaceIndex,
      () => FieldDocumentsView(api: _api, session: session),
    ),
    (_complianceWorkspaceIndex, () => ComplianceView(api: _api, session: session)),
    (
      _plantScheduleWorkspaceIndex,
      () => PlantScheduleView(api: _api, session: session),
    ),
  ];
}

/// The Knowledge layer: four questions, four tabs.
///
/// **Ontology** — what do we know. The landing tab, because the diagram is
/// the whole model at once; the Overview it replaced was the tab bar redrawn
/// as cards, offering the same destinations for the same click.
///
/// **Library** — what must be obeyed: what people wrote down that agents
/// read at run time.
///
/// **Data** — what do we hold, and under what policy. The Collections carry
/// the governance (retention, custody, authoritativeness — properties of a
/// set, never a row), and retention is read back against what is actually
/// held.
///
/// **Systems** — who do we talk to, and on what terms. Each inbound feed
/// against the contract it lands under, the connector's cursor, and the
/// outbound half: the closed table of permitted writes and the journal of
/// every one ever sent.
///
/// TBox and ABox stay out of the navigation: they are one subject at two
/// zoom levels of the Ontology tab, so selecting a box and browsing what
/// exists of it is a step down rather than a second lens. Collections came
/// *back* — not as implementation vocabulary this time but as the unit the
/// Data tab governs by, which is what they always were.
///
/// Every number lives beside the thing it measures, and nothing is stated
/// twice: conformance on the model, corpus traffic on the Library, custody
/// and retention on Data, records-landed on Systems.
class _KnowledgeSection extends StatefulWidget {
  final ApiClient api;
  final ValueNotifier<int> plane;
  final Widget library;

  /// Null until the session has loaded. The four routes behind this section
  /// answer 403 to an own-scoped persona, so the section says so itself.
  final Session? session;

  /// Opens the identity workbench on one proposal — passed through to the ABox
  /// explorer, where an entity's standing merge proposals are part of what the
  /// entity is.
  final ValueChanged<String> onOpenProposal;

  /// Opens one asset in the Library tab — how the Ontology tab reaches the
  /// `enterprise-ontology` export it is a rendering of.
  final ValueChanged<String> onOpenAsset;

  const _KnowledgeSection({
    required this.api,
    required this.plane,
    required this.library,
    required this.session,
    required this.onOpenProposal,
    required this.onOpenAsset,
  });

  @override
  State<_KnowledgeSection> createState() => _KnowledgeSectionState();
}

class _KnowledgeSectionState extends State<_KnowledgeSection> {
  /// The type whose instances are showing, or null for the model itself.
  /// Held here rather than inside the diagram because it is a zoom level of
  /// the Ontology tab, and the tab is what owns it.
  String? _instancesOf;

  /// Which planes have been shown at least once.
  ///
  /// The shell's own rule, one level down: each of the four planes opens with
  /// an aggregate the server answers by walking the whole graph, and mounting
  /// all four at once meant first entry to the section fired four of those
  /// against one client timeout. A plane is built when it is first selected and
  /// stays mounted afterwards, so switching tabs keeps its state.
  late final Set<int> _visitedPlanes = {widget.plane.value};

  @override
  void initState() {
    super.initState();
    widget.plane.addListener(_onPlane);
  }

  @override
  void dispose() {
    widget.plane.removeListener(_onPlane);
    super.dispose();
  }

  /// The tabs are not the only way the plane moves — the palette and System
  /// state both set it from outside — so first sight is recorded here rather
  /// than in the tab bar's callback.
  void _onPlane() {
    if (_visitedPlanes.add(widget.plane.value)) setState(() {});
  }

  Widget _lazyPlane(int plane, Widget Function() build) =>
      _visitedPlanes.contains(plane) ? build() : const SizedBox.shrink();

  @override
  Widget build(BuildContext context) {
    // The tab labels, the IndexedStack children and the named plane constants
    // are three tellings of one order; a reorder that moves one without the
    // others would send System state's hand-off to the wrong tab silently.
    assert(
      _ontologyPlane == 0 &&
          _libraryPlane == 1 &&
          _dataPlane == 2 &&
          _systemsPlane == 3,
      'the Knowledge tabs drifted from their named plane constants',
    );
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
                      labels: const ['Ontology', 'Library', 'Data', 'Systems'],
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
                        _lazyPlane(_ontologyPlane, _ontology),
                        _lazyPlane(_libraryPlane, () => widget.library),
                        _lazyPlane(
                          _dataPlane,
                          () => DataPlane(api: widget.api),
                        ),
                        _lazyPlane(
                          _systemsPlane,
                          () => SourcesPlane(api: widget.api),
                        ),
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
  ///
  /// A stack rather than one or the other, because stepping down a zoom level
  /// is not leaving the diagram. Returning the explorer in the diagram's place
  /// unmounted it, so browsing instances threw away the zoom, the pan and the
  /// selection you had arrived at, and coming back re-walked the whole ontology
  /// to draw a picture that had been on screen a moment earlier.
  Widget _ontology() {
    final type = _instancesOf;
    return IndexedStack(
      index: type == null ? 0 : 1,
      children: [
        PageGutter(
          padding: const EdgeInsets.fromLTRB(28, 20, 28, 28),
          child: OntologyDiagram(
            api: widget.api,
            onBrowseInstances: (t) => setState(() => _instancesOf = t),
            onOpenExport: () => widget.onOpenAsset('enterprise-ontology'),
          ),
        ),
        if (type == null)
          const SizedBox.shrink()
        else
          PageGutter(
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
                  // Keyed by type so arriving from a different box reloads onto
                  // it rather than reusing the explorer parked on the last one.
                  child: ABoxExplorer(
                    key: ValueKey(type),
                    api: widget.api,
                    initialType: type,
                    onOpenProposal: widget.onOpenProposal,
                  ),
                ),
              ],
            ),
          ),
      ],
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
