import 'package:flutter/material.dart';

import '../models/asset.dart';
import '../services/api_client.dart';
import '../services/auth_service.dart';
import '../widgets/command_palette.dart';
import '../widgets/cumulus_shell.dart';
import '../widgets/task_queue.dart';
import 'api_docs_screen.dart';
import '../widgets/data_plane.dart';
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
const int _knowledgeIndex = 0;
const int _flowsIndex = 1;
const int _identitiesIndex = 2;
const int _employeeHubIndex = 3;
const int _expenseWorkspaceIndex = 4;
const int _payablesWorkspaceIndex = 5;

const _destinations = <RailDestination>[
  RailDestination(
    icon: Icons.hub_outlined,
    label: 'Knowledge layer',
    index: _knowledgeIndex,
  ),
  RailDestination(
    icon: Icons.account_tree_outlined,
    label: 'Flows',
    index: _flowsIndex,
  ),
  // A Workspace is a business view onto rows the flows write — scoped work,
  // not a plane of the map. They hung off the Knowledge layer's Data tab until
  // now, which put a business surface inside the governance surface; the
  // dropdown is where they belong.
  RailDestination(
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
        detail: 'invoices, exemplars, posting',
        index: _payablesWorkspaceIndex,
      ),
    ],
  ),
  RailDestination(
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

  int _index = 0;

  /// Which plane of the Knowledge layer is showing: 0 = Library (the governed
  /// corpus), 1 = Data (the dataset registry). Held here so opening an asset
  /// from the palette can flip back to the Library.
  final ValueNotifier<int> _knowledgePlane = ValueNotifier(0);

  /// Sections fetch on first build, so a section that has never been opened
  /// stays unbuilt rather than firing a request the user did not ask for.
  final Set<int> _visited = {0};

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
        _show(0);
        _knowledgePlane.value = 0;
        _pageResults.value = null;
        _openAsset.value = assetId;
      case CreateAssetIntent():
        _show(0);
        _knowledgePlane.value = 0;
        _pageResults.value = null;
        _openAsset.value = '';
      case ShowAllResultsIntent(:final query, :final filters):
        _show(0);
        _knowledgePlane.value = 0;
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
      destinations: _destinations,
      selectedIndex: _index,
      onDestinationSelected: _show,
      palette: _palette,
      accountLabel: AuthService.instance.email,
      onSignOut: AuthService.instance.signOut,
      taskButton: TaskQueueButton(api: _api),
      onOpenApiDocs: () => Navigator.of(context).push(
        MaterialPageRoute(builder: (_) => const ApiDocsScreen()),
      ),
      onOpenEmployeeHub: () => _show(_employeeHubIndex),
      employeeHubActive: _index == _employeeHubIndex,
      child: IndexedStack(
        index: _index,
        children: [
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
          _lazy(_expenseWorkspaceIndex, () => ReimbursementsView(api: _api)),
          _lazy(_payablesWorkspaceIndex, () => PayablesView(api: _api)),
        ],
      ),
    );
  }
}

/// The Knowledge layer section: two planes under one heading.
///
/// **Library** is the governed corpus (documents people author); **Data** is
/// the dataset registry — every container of content the map owns, what Role
/// it carries, where its rows come from, and which Flow reads or writes it.
/// The split mirrors the Dataset Role dial in the doctrine — delete knowledge
/// and the agents get dumber; delete data and history is lost — and gives the
/// section room to grow an Entities and an Ontology plane without another
/// rework.
///
/// The two business tables that used to sit in Data behind dataset chips are
/// now Workspaces on the rail. Data still names them, but as datasets — the
/// row count, the Flow that writes them, the Role they carry — and hands off
/// to the Workspace for the work itself.
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
            Padding(
              padding: const EdgeInsets.fromLTRB(28, 14, 28, 0),
              child: SegmentedTabs(
                labels: const ['Library', 'Data'],
                selectedIndex: selected,
                onChanged: (i) => widget.plane.value = i,
              ),
            ),
            Expanded(
              child: IndexedStack(
                index: selected,
                children: [
                  widget.library,
                  DataPlaneView(
                    api: widget.api,
                    onOpenExpenseWorkspace: () =>
                        widget.onOpenWorkspace(_expenseWorkspaceIndex),
                    onOpenPayablesWorkspace: () =>
                        widget.onOpenWorkspace(_payablesWorkspaceIndex),
                    onOpenFlows: () => widget.onOpenWorkspace(_flowsIndex),
                    onOpenLibrary: () => widget.plane.value = 0,
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
