import 'package:flutter/material.dart';

import '../models/asset.dart';
import '../services/api_client.dart';
import '../services/auth_service.dart';
import '../theme/cumulus_theme.dart';
import '../widgets/command_palette.dart';
import '../widgets/cumulus_shell.dart';
import '../widgets/tasks_view.dart';
import 'api_docs_screen.dart';
import '../widgets/calendar_view.dart';
import '../widgets/data_plane.dart';
import '../widgets/home_view.dart';
import '../widgets/knowledge_overview.dart';
import '../widgets/learned_plane.dart';
import '../widgets/payables_view.dart';
import '../widgets/reimbursements_view.dart';
import '../widgets/ui_kit.dart';
import 'asset_list_screen.dart';
import 'employee_hub_screen.dart';
import 'identities_screen.dart';
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
const int _homeIndex = 0;
const int _calendarIndex = 1;
const int _tasksIndex = 2;
const int _expenseWorkspaceIndex = 3;
const int _payablesWorkspaceIndex = 4;
const int _knowledgeIndex = 5;
const int _flowsIndex = 6;
const int _identitiesIndex = 7;
const int _employeeHubIndex = 8;

/// The Library's tab index inside the Knowledge layer section. Named because
/// the palette navigates to it and a bare `1` would not survive a reorder.
const int _libraryPlane = 1;

/// The rail. `badge` on the Tasks stop is filled in per build from the open
/// count — the badge moved off the top bar with the queue itself.
List<RailDestination> _destinations(int openTasks) => [
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
  const RailDestination(
    icon: Icons.grid_view_outlined,
    label: 'Workspaces',
    children: [
      RailChild(
        label: 'Expense Reimbursement',
        detail: 'requests, decisions, spend',
        index: _expenseWorkspaceIndex,
      ),
      RailChild(
        label: 'Accounts Payable',
        detail: 'invoices, matching, posting',
        index: _payablesWorkspaceIndex,
      ),
    ],
  ),
  const RailDestination(
    icon: Icons.hub_outlined,
    label: 'Knowledge layer',
    index: _knowledgeIndex,
    startsGroup: true,
  ),
  const RailDestination(
    icon: Icons.account_tree_outlined,
    label: 'Flows',
    index: _flowsIndex,
  ),
  const RailDestination(
    icon: Icons.groups_outlined,
    label: 'Identities, access & keys',
    index: _identitiesIndex,
  ),
];

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

  /// Which plane of the Knowledge layer is showing — see the tab labels in
  /// [_KnowledgeSection]. Held here so opening an asset from the palette can
  /// flip back to the Library, wherever that tab sits.
  final ValueNotifier<int> _knowledgePlane = ValueNotifier(0);

  /// Sections fetch on first build, so a section that has never been opened
  /// stays unbuilt rather than firing a request the user did not ask for.
  final Set<int> _visited = {_homeIndex};

  @override
  void initState() {
    super.initState();
    _refreshTaskBadge();
  }

  @override
  void dispose() {
    _palette.dispose();
    _catalog.dispose();
    _openAsset.dispose();
    _pageResults.dispose();
    _knowledgePlane.dispose();
    _api.close();
    super.dispose();
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

  Widget _lazy(int index, Widget Function() build) =>
      _visited.contains(index) ? build() : const SizedBox.shrink();

  @override
  Widget build(BuildContext context) {
    return CumulusShell(
      destinations: _destinations(_openTasks),
      selectedIndex: _index,
      onDestinationSelected: _show,
      palette: _palette,
      accountLabel: AuthService.instance.email,
      onSignOut: AuthService.instance.signOut,
      onOpenApiDocs: () => Navigator.of(context).push(
        MaterialPageRoute(builder: (_) => const ApiDocsScreen()),
      ),
      onOpenEmployeeHub: () => _show(_employeeHubIndex),
      employeeHubActive: _index == _employeeHubIndex,
      // Ordered by section index, which is what IndexedStack selects on.
      child: IndexedStack(
        index: _index,
        children: [
          _lazy(
            _homeIndex,
            () => HomeSection(
              api: _api,
              onOpenCalendar: () => _show(_calendarIndex),
              onOpenPayables: () => _show(_payablesWorkspaceIndex),
              onOpenTasks: () => _show(_tasksIndex),
            ),
          ),
          _lazy(_calendarIndex, () => CalendarSection(api: _api)),
          _lazy(
            _tasksIndex,
            () => TasksSection(api: _api, onQueueChanged: _refreshTaskBadge),
          ),
          _lazy(_expenseWorkspaceIndex, () => ReimbursementsView(api: _api)),
          _lazy(_payablesWorkspaceIndex, () => PayablesView(api: _api)),
          _lazy(
            _knowledgeIndex,
            () => _KnowledgeSection(
              api: _api,
              plane: _knowledgePlane,
              onOpenWorkspace: _show,
              library: AssetsSection(
                palette: _palette,
                openAsset: _openAsset,
                pageResults: _pageResults,
                onCorpusChanged: _catalog.refresh,
              ),
            ),
          ),
          _lazy(_flowsIndex, () => const FlowsSection()),
          _lazy(_identitiesIndex, () => const IdentitiesSection()),
          _lazy(_employeeHubIndex, () => EmployeeHubSection(api: _api)),
        ],
      ),
    );
  }
}

/// The Knowledge layer section: an Overview plus four planes, cut by
/// *origin* — who or what put each thing here.
///
/// **Overview** is the landing screen and the only place metrics live: the
/// planes are about their subject, and reporting on the layer as a whole is
/// a different job from browsing any one plane of it.
///
/// **Library** is what people wrote: policies, standards, guidelines — the
/// documents that argue and carry authority. **Ontology** is what we encoded:
/// the TBox, the ABox, the Collection declarations, and the Mappings that
/// bind sources in. **Learned** is what the system figured out through use:
/// derived profiles, eval datasets — the compounding record of judgment. **Sources** is what arrives from outside: the feeds
/// against their contracts, and the artifact store.
///
/// The old Library/Data split put a derived vendor profile next to the
/// Expense Approval Policy as if they were the same kind of thing. They are
/// governed alike — versioned, audited, revision-pinned — but they answer
/// different questions, and origin is the axis that separates them.
class _KnowledgeSection extends StatefulWidget {
  final ApiClient api;
  final ValueNotifier<int> plane;
  final Widget library;

  /// Jumps to a Workspace section, so a dataset row can hand off to the
  /// business view of its rows.
  final ValueChanged<int> onOpenWorkspace;

  const _KnowledgeSection({
    required this.api,
    required this.plane,
    required this.library,
    required this.onOpenWorkspace,
  });

  @override
  State<_KnowledgeSection> createState() => _KnowledgeSectionState();
}

class _KnowledgeSectionState extends State<_KnowledgeSection> {
  @override
  Widget build(BuildContext context) {
    return ValueListenableBuilder<int>(
      valueListenable: widget.plane,
      builder: (context, selected, _) {
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // The section title sits above the tabs: the tabs are planes
            // *of* the knowledge layer, and without the title each plane
            // reads like a top-level destination of its own. PageGutter, not
            // Padding, because the planes below centre their content inside
            // a max width and the title has to land on the same left edge.
            PageGutter(
              padding: const EdgeInsets.fromLTRB(28, 18, 28, 0),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('Knowledge layer', style: CumulusText.pageTitle),
                  const SizedBox(height: 14),
                  SegmentedTabs(
                    labels: const [
                      'Overview',
                      'Library',
                      'Ontology',
                      'Learned',
                      'Sources',
                    ],
                    selectedIndex: selected,
                    onChanged: (i) => widget.plane.value = i,
                  ),
                ],
              ),
            ),
            Expanded(
              child: IndexedStack(
                index: selected,
                children: [
                  KnowledgeOverview(
                    api: widget.api,
                    onOpenPlane: (i) => widget.plane.value = i,
                  ),
                  widget.library,
                  DataPlaneView(
                    api: widget.api,
                    mode: DataPlaneMode.ontology,
                    onOpenExpenseWorkspace: () =>
                        widget.onOpenWorkspace(_expenseWorkspaceIndex),
                    onOpenPayablesWorkspace: () =>
                        widget.onOpenWorkspace(_payablesWorkspaceIndex),
                    onOpenFlows: () => widget.onOpenWorkspace(_flowsIndex),
                    onOpenLibrary: () => widget.plane.value = 1,
                  ),
                  LearnedPlaneView(
                    api: widget.api,
                    onOpenFlows: () => widget.onOpenWorkspace(_flowsIndex),
                  ),
                  DataPlaneView(
                    api: widget.api,
                    mode: DataPlaneMode.sources,
                    onOpenExpenseWorkspace: () =>
                        widget.onOpenWorkspace(_expenseWorkspaceIndex),
                    onOpenPayablesWorkspace: () =>
                        widget.onOpenWorkspace(_payablesWorkspaceIndex),
                    onOpenFlows: () => widget.onOpenWorkspace(_flowsIndex),
                    onOpenLibrary: () => widget.plane.value = 1,
                  ),
                ],
              ),
            ),
          ],
        );
      },
    );
  }
}
