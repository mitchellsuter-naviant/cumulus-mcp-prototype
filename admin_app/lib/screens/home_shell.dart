import 'package:flutter/material.dart';

import '../models/asset.dart';
import '../services/api_client.dart';
import '../services/auth_service.dart';
import '../widgets/command_palette.dart';
import '../widgets/cumulus_shell.dart';
import 'api_docs_screen.dart';
import 'asset_list_screen.dart';
import 'identities_screen.dart';
import 'flows_screen.dart';

const _destinations = <RailDestination>[
  RailDestination(icon: Icons.hub_outlined, label: 'Knowledge layer'),
  RailDestination(icon: Icons.account_tree_outlined, label: 'Flows'),
  RailDestination(
    icon: Icons.groups_outlined,
    label: 'Identities, access & keys',
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

  /// Sections fetch on first build, so a section that has never been opened
  /// stays unbuilt rather than firing a request the user did not ask for.
  final Set<int> _visited = {0};

  @override
  void dispose() {
    _palette.dispose();
    _catalog.dispose();
    _openAsset.dispose();
    _pageResults.dispose();
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
        _pageResults.value = null;
        _openAsset.value = assetId;
      case CreateAssetIntent():
        _show(0);
        _pageResults.value = null;
        _openAsset.value = '';
      case ShowAllResultsIntent(:final query, :final filters):
        _show(0);
        _openAsset.value = null;
        _pageResults.value = (query: query, filters: filters);
      case OpenSectionIntent(:final index):
        _show(index);
      // Teams and identities both live on the identities page, which owns its
      // own drill-down. Bringing the section forward is as far as the palette
      // can honestly navigate without reaching into that page's state.
      case OpenTeamIntent():
      case OpenIdentityIntent():
        _show(2);
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
      onOpenApiDocs: () => Navigator.of(context).push(
        MaterialPageRoute(builder: (_) => const ApiDocsScreen()),
      ),
      child: IndexedStack(
        index: _index,
        children: [
          _lazy(
            0,
            () => AssetsSection(
              palette: _palette,
              openAsset: _openAsset,
              pageResults: _pageResults,
              onCorpusChanged: _catalog.refresh,
            ),
          ),
          _lazy(1, () => const FlowsSection()),
          _lazy(2, () => const IdentitiesSection()),
        ],
      ),
    );
  }
}
