import 'package:flutter/material.dart';

import '../models/asset.dart';
import '../services/api_client.dart';
import '../theme/cumulus_theme.dart';
import '../theme/domain_tones.dart';
import '../widgets/command_palette.dart';
import '../widgets/cumulus_shell.dart';
import '../widgets/ui_kit.dart';
import 'asset_editor_screen.dart';
import 'search_screen.dart';

/// Authority levels, in descending order of force — the same order the lens
/// chips are shown in.
const _authorityLevels = ['policy', 'standard', 'guideline', 'how-to', 'note'];

/// The knowledge layer: a metric strip over the corpus, authority lenses, and
/// the taxonomy grouped into cards. Editing happens in place rather than on a
/// pushed route, so the console chrome never flickers.
///
/// This page no longer owns search. The command palette does, and drives this
/// page through [openAsset] and [pageResults]. That split is what fixes the
/// old behaviour where a query typed with an asset open ran, stored its
/// results, and rendered nothing because the editor was drawn over them.
class AssetsSection extends StatefulWidget {
  /// Published to so ⌘K can search inside whatever document is open.
  final CommandPaletteController palette;

  /// An asset to open: an id, `''` for a new asset, null for none.
  final ValueNotifier<String?> openAsset;

  /// The full result list, set only by the palette's "See all results".
  final ValueNotifier<({String query, SearchFilters filters})?> pageResults;

  /// Called after a write so the palette's jump-to catalog stays current.
  final Future<void> Function() onCorpusChanged;

  const AssetsSection({
    super.key,
    required this.palette,
    required this.openAsset,
    required this.pageResults,
    required this.onCorpusChanged,
  });

  @override
  State<AssetsSection> createState() => _AssetsSectionState();
}

class _AssetsSectionState extends State<AssetsSection> {
  final ApiClient _api = ApiClient();

  List<AssetSummary> _assets = [];
  Map<String, AssetUsage> _usage = const {};

  /// The whole corpus, including what this screen filters out. Kept so the
  /// caption can name what is not here rather than leaving the reader to
  /// wonder why 9 documents sit under a "15 assets" corpus everywhere else.
  int _corpusTotal = 0;
  Map<String, int> _otherKinds = const {};

  /// The traffic window, echoed by the server. Held rather than written into
  /// the caption as `30d`, which is a number that keeps itself when the
  /// request changes its mind.
  int _windowDays = 30;

  /// When the newest document in the corpus was written. "No reads in 30d"
  /// over a corpus four minutes old is a fact about the corpus, not about the
  /// documents.
  DateTime? _newest;

  String? _lens;

  /// The dead-weight lenses. Two of them, because "nobody uses this" has two
  /// causes needing opposite fixes: `ignored` documents surface and get passed
  /// over (rewrite them), `undiscovered` ones never surface at all (retag
  /// them). Null means no usage lens is active.
  String? _usageLens;
  bool _loading = true;
  String? _error;

  /// Non-null while the full search view is showing.
  ({String query, SearchFilters filters})? _search;

  /// Non-null while the editor is open; the inner string is the asset id, or
  /// null for a new asset.
  ({String? id})? _editing;

  @override
  void initState() {
    super.initState();
    widget.openAsset.addListener(_onOpenAssetRequested);
    widget.pageResults.addListener(_onPageResultsRequested);
    _refresh();
  }

  @override
  void dispose() {
    widget.openAsset.removeListener(_onOpenAssetRequested);
    widget.pageResults.removeListener(_onPageResultsRequested);
    _api.close();
    super.dispose();
  }

  Future<void> _onOpenAssetRequested() async {
    final requested = widget.openAsset.value;
    if (requested == null) return;
    // Consumed immediately so opening the same asset twice in a row still
    // fires. Setting it back to null re-enters this listener, which the guard
    // above absorbs.
    widget.openAsset.value = null;

    if (!await _confirmLeavingEditor()) return;
    if (!mounted) return;
    setState(() => _editing = (id: requested.isEmpty ? null : requested));
  }

  /// Navigating away from a dirty editor asks first. Search should never be
  /// the reason someone loses a draft.
  Future<bool> _confirmLeavingEditor() async {
    final scope = widget.palette.scope;
    if (_editing == null || scope == null || !scope.isDirty()) return true;
    final leave = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Discard unsaved changes?'),
        content: Text(
          '"${scope.title}" has edits that have not been saved. Opening '
          'something else will discard them.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('Keep editing'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(context).pop(true),
            child: const Text('Discard'),
          ),
        ],
      ),
    );
    return leave ?? false;
  }

  Future<void> _onPageResultsRequested() async {
    final request = widget.pageResults.value;
    if (request == null) {
      setState(() => _search = null);
      return;
    }
    if (!await _confirmLeavingEditor()) {
      widget.pageResults.value = null;
      return;
    }
    if (!mounted) return;
    setState(() {
      _editing = null;
      _search = request;
    });
  }

  Future<void> _refresh() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final screen = await _api.libraryScreen();
      if (!mounted) return;
      setState(() {
        // The Library is documents people wrote. The route returns the whole
        // corpus because the Ontology's generated exports and the flows'
        // prompts are governed alongside them; this screen shows the half
        // that argues and carries authority.
        _assets = screen.assets.where((a) => a.kind == 'document').toList();
        // Usage folded over the *same* set. It used to fold over all fifteen
        // assets while the count beside it said nine, so one number described
        // the documents and its neighbour described the corpus.
        final shown = {for (final a in _assets) a.id};
        _usage = {
          for (final entry in screen.usage.entries)
            if (shown.contains(entry.key)) entry.key: entry.value,
        };
        _corpusTotal = screen.assets.length;
        _otherKinds = {};
        for (final asset in screen.assets) {
          if (asset.kind == 'document') continue;
          _otherKinds[asset.kind] = (_otherKinds[asset.kind] ?? 0) + 1;
        }
        _windowDays = screen.days;
        _newest = _assets.isEmpty
            ? null
            : _assets
                  .map((a) => a.updatedAt)
                  .reduce((a, b) => a.isAfter(b) ? a : b);
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = '$e';
        _loading = false;
      });
    }
  }

  /// Whether anything at all was recorded against the corpus in the window.
  ///
  /// The usage map is zero-filled, so its size says nothing. Without this the
  /// screen called every document undiscovered — in red — whenever search had
  /// simply not been run, which flags a healthy corpus as broken. No traffic
  /// is a fact about the window, not a verdict on the documents.
  bool get _hasTraffic => _usage.values.any(
    (u) => u.reads > 0 || u.citations > 0 || u.impressions > 0,
  );

  void _openEditor({String? assetId}) =>
      setState(() => _editing = (id: assetId));

  void _closeEditor({required bool changed}) {
    setState(() {
      _editing = null;
      if (changed) widget.pageResults.value = null;
    });
    if (changed) {
      _refresh();
      // Fire-and-forget: the palette's catalog going stale for a moment is not
      // worth blocking the editor closing.
      widget.onCorpusChanged();
    }
  }

  List<AssetSummary> get _visibleAssets => _assets
      .where((a) => _lens == null || a.authorityLevel == _lens)
      .where(_matchesUsageLens)
      .toList();

  bool _matchesUsageLens(AssetSummary asset) {
    if (_usageLens == null || !_hasTraffic) return true;
    final usage = _usage[asset.id];
    // No row at all means no recorded traffic of any kind — undiscovered.
    if (usage == null) return _usageLens == 'undiscovered';
    return switch (_usageLens) {
      'ignored' => usage.ignored,
      'undiscovered' => usage.undiscovered,
      _ => true,
    };
  }

  @override
  Widget build(BuildContext context) {
    if (_editing != null) {
      return AssetEditorView(
        // Keyed by asset so opening a different document from the palette
        // rebuilds the editor rather than reusing the previous one's fields.
        key: ValueKey(_editing!.id ?? '<new>'),
        assetId: _editing!.id,
        onClose: _closeEditor,
        onScopeChanged: (scope) => widget.palette.scope = scope,
      );
    }
    // Search is a mode of this page rather than a separate destination: the
    // taxonomy is one click behind it, and the palette that started the search
    // stays exactly where it was.
    if (_search != null) {
      return SearchView(
        query: _search!.query,
        filters: _search!.filters,
        onClose: () => widget.pageResults.value = null,
        onOpenAsset: (id) => _openEditor(assetId: id),
        onRefine: (query, filters) =>
            widget.pageResults.value = (query: query, filters: filters),
      );
    }
    return PageBody(
      // The knowledge-layer gutter: every plane and the section title above
      // the tabs share one left edge.
      padding: const EdgeInsets.fromLTRB(28, 20, 28, 40),
      children: [
        _heading(),
        if (_error != null) ErrorNotice(message: _error!, onRetry: _refresh),
        if (_loading)
          const CumulusCard(child: CumulusLoading())
        else
          ..._taxonomyCards(),
      ],
    );
  }

  /// "No reads" over a corpus younger than the window is a fact about the
  /// corpus. Four minutes of history cannot show thirty days of traffic, and
  /// the honest sentence says which of the two it is short of.
  String get _trafficPill {
    final reads = _usage.values.fold<int>(0, (sum, u) => sum + u.reads);
    if (_hasTraffic) return '$reads reads in ${_windowDays}d';
    final newest = _newest;
    if (newest != null &&
        DateTime.now().difference(newest) < Duration(days: _windowDays)) {
      return 'no reads yet · corpus is ${agoLabel(newest).replaceAll(' ago', '')} old';
    }
    return 'no reads recorded in ${_windowDays}d';
  }

  Widget _heading() {
    final published = _assets.where((a) => a.status == 'published').length;
    return PlaneHeader(
      // The corpus is bigger than this screen, and the summary now names what
      // it holds back rather than only the exports — the prompts a flow reads
      // are governed here too, and a reader counting 15 assets elsewhere
      // against 9 here was owed both halves.
      summary:
          'What people wrote down that agents must obey: policies, standards, '
          'guidelines and notes — the documents that argue and carry '
          'authority. The generated schema exports and the flows\' prompts are '
          'governed in the same corpus and are read under Ontology and Flows.',
      pills: [
        MetaPill(
          _otherKinds.isEmpty
              ? '${_assets.length} documents'
              : '${_assets.length} of $_corpusTotal assets are documents',
        ),
        MetaPill('$published published', tone: CumulusTone.success),
        // Traffic belongs here rather than on a survey screen, because the
        // lenses that act on it are two chips below. Folded over the same nine
        // documents the count names, so the two agree. Stated as a plain fact
        // when there is none: nothing has been read is not an alarm.
        if (!_loading) MetaPill(_trafficPill),
      ],
      trailing: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          OutlinedButton.icon(
            onPressed: _loading ? null : _refresh,
            icon: const Icon(Icons.refresh, size: 16),
            label: const Text('Refresh'),
          ),
          const SizedBox(width: 10),
          FilledButton.icon(
            onPressed: () => _openEditor(),
            icon: const Icon(Icons.add, size: 17),
            label: const Text('New asset'),
          ),
        ],
      ),
    );
  }

  Widget _lensBar() {
    return Wrap(
      spacing: 8,
      runSpacing: 8,
      children: [
        LensChip(
          label: 'All',
          selected: _lens == null,
          onTap: () => setState(() => _lens = null),
        ),
        for (final level in _authorityLevels)
          LensChip(
            label: level,
            tone: toneForAuthority(level),
            selected: _lens == level,
            onTap: () => setState(() => _lens = _lens == level ? null : level),
          ),
        if (_hasTraffic) ...[
          const SizedBox(width: 12),
          LensChip(
            label: 'ignored',
            tone: CumulusTone.warn,
            selected: _usageLens == 'ignored',
            onTap: () => setState(
              () => _usageLens = _usageLens == 'ignored' ? null : 'ignored',
            ),
          ),
          LensChip(
            label: 'undiscovered',
            tone: CumulusTone.danger,
            selected: _usageLens == 'undiscovered',
            onTap: () => setState(
              () => _usageLens =
                  _usageLens == 'undiscovered' ? null : 'undiscovered',
            ),
          ),
        ],
      ],
    );
  }

  List<Widget> _taxonomyCards() {
    final visible = _visibleAssets;
    final groups = <String, List<AssetSummary>>{};
    for (final asset in visible) {
      groups.putIfAbsent(asset.path.split('/').first, () => []).add(asset);
    }
    final branches = groups.keys.toList()..sort();

    return [
      CumulusCard(
        padding: const EdgeInsets.fromLTRB(20, 18, 20, 18),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            SectionHeader(
              title: 'Authority lens',
              subtitle: _hasTraffic
                  ? 'filter by how much force a document carries, or by how '
                        'it is failing to be used'
                  : 'filter by how much force a document carries',
              trailing: Text(
                '${visible.length} shown',
                style: CumulusText.sectionSubtitle,
              ),
            ),
            const SizedBox(height: 14),
            _lensBar(),
          ],
        ),
      ),
      if (branches.isEmpty)
        CumulusCard(
          child: CumulusEmptyState(
            icon: Icons.inventory_2_outlined,
            title: _assets.isEmpty ? 'No assets yet' : 'Nothing at this lens',
            message: _assets.isEmpty
                ? 'Author the first document in the knowledge layer and every '
                      'agent that reasons over Cumulus will see it.'
                : 'No documents carry this authority level. Clear the lens to '
                      'see the whole corpus.',
            action: _assets.isEmpty
                ? FilledButton.icon(
                    onPressed: () => _openEditor(),
                    icon: const Icon(Icons.add, size: 17),
                    label: const Text('New asset'),
                  )
                : OutlinedButton(
                    onPressed: () => setState(() => _lens = null),
                    child: const Text('Clear lens'),
                  ),
          ),
        )
      else
        for (final branch in branches)
          ListCard(
            title: branch,
            subtitle:
                '${groups[branch]!.length} '
                '${groups[branch]!.length == 1 ? "document" : "documents"}',
            children: [
              for (final asset in groups[branch]!..sort(_byPath))
                _assetRow(asset),
            ],
          ),
    ];
  }

  static int _byPath(AssetSummary a, AssetSummary b) =>
      a.path.compareTo(b.path);

  Widget _assetRow(AssetSummary asset) {
    return CumulusListRow(
      leading: Monogram(
        asset.authorityLevel.substring(0, 1).toUpperCase(),
        tone: toneForAuthority(asset.authorityLevel),
      ),
      title: Row(
        children: [
          Flexible(child: Text(asset.title, overflow: TextOverflow.ellipsis)),
          const SizedBox(width: 10),
          StatusBadge(
            asset.authorityLevel,
            tone: toneForAuthority(asset.authorityLevel),
            dense: true,
          ),
        ],
      ),
      // The revision beside the age, because a governed document is cited at
      // one — every ref in the console reads `knowledge:id@rev3` — and a row
      // that showed only "updated 4m ago" left the reader no way to tell a
      // freshly seeded first draft from a policy on its ninth edit.
      subtitle: Text(
        '${asset.path} · rev ${asset.revision} · '
        'updated ${agoLabel(asset.updatedAt)}'
        '${asset.owner.isEmpty ? "" : " · ${asset.owner}"}'
        '${asset.tags.isEmpty ? "" : " · ${asset.tags.join(", ")}"}',
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
      ),
      trailing: [
        _UsagePip(
          usage: _usage[asset.id],
          known: _hasTraffic,
          days: _windowDays,
        ),
        const SizedBox(width: 8),
        StatusBadge(asset.status, tone: toneForStatus(asset.status)),
        const SizedBox(width: 8),
        RowAction(label: 'Open', onTap: () => _openEditor(assetId: asset.id)),
      ],
      onTap: () => _openEditor(assetId: asset.id),
    );
  }

}


/// Compact usage figure for a taxonomy row.
///
/// Reads and citations are shown as separate numbers rather than summed. The
/// total hides the interesting case — opened repeatedly, never good enough to
/// cite — which is exactly the document worth rewriting.
class _UsagePip extends StatelessWidget {
  final AssetUsage? usage;

  /// False until the corpus usage call returns, so an asset is never labelled
  /// dead weight merely because the metrics have not loaded.
  final bool known;

  /// The window the server actually measured, not a literal — the tooltips
  /// state it in prose and would otherwise keep saying "30 days" whatever was
  /// asked for.
  final int days;

  const _UsagePip({
    required this.usage,
    required this.known,
    required this.days,
  });

  @override
  Widget build(BuildContext context) {
    if (!known) return const SizedBox.shrink();
    final reads = usage?.reads ?? 0;
    final citations = usage?.citations ?? 0;
    final impressions = usage?.impressions ?? 0;

    // The two ways a document fails read differently and need different
    // fixes, so the row says which one rather than a shared "unused".
    if (reads == 0 && citations == 0) {
      final ignored = impressions > 0;
      return Tooltip(
        message: ignored
            ? 'Surfaced in $impressions '
                  '${impressions == 1 ? "search" : "searches"} in $days days and '
                  'never opened — it ranks for questions it does not answer'
            : 'Never surfaced in a search in 30 days — nothing can read what '
                  'search does not return',
        child: Text(
          ignored ? 'ignored' : 'undiscovered',
          style: CumulusText.meta.copyWith(
            color: ignored ? CumulusColors.warn : CumulusColors.danger,
          ),
        ),
      );
    }

    return Tooltip(
      message:
          '$impressions surfaced · $reads '
          '${reads == 1 ? "read" : "reads"} · $citations '
          '${citations == 1 ? "citation" : "citations"} in $days days'
          '${usage?.lastUsedAt == null ? "" : " · last ${agoLabel(usage!.lastUsedAt!)}"}',
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          const Icon(
            Icons.visibility_outlined,
            size: 13,
            color: CumulusColors.inkMuted,
          ),
          const SizedBox(width: 4),
          Text('$reads', style: CumulusText.meta),
          if (citations > 0) ...[
            const SizedBox(width: 8),
            const Icon(
              Icons.format_quote,
              size: 13,
              color: CumulusColors.accent,
            ),
            const SizedBox(width: 2),
            Text(
              '$citations',
              style: CumulusText.meta.copyWith(color: CumulusColors.accentInk),
            ),
          ],
        ],
      ),
    );
  }
}
